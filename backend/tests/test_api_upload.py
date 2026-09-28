"""Upload -> mapping -> analyze -> cold start -> feedback -> learning, through the HTTP API (offline backends)."""
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.workspaces import WorkspaceManager

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path):
    s = Settings(groq_api_key="", hindsight_api_key="", data_dir=tmp_path, database_path=tmp_path / "demo.db")
    app.state.workspaces = WorkspaceManager(s)
    return TestClient(app)  # no lifespan: the manager above is used


@pytest.fixture(scope="module")
def nimbus(tmp_path_factory):
    out = tmp_path_factory.mktemp("nimbus")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_nimbus.py"), "--out", str(out)], check=True,
                   capture_output=True)
    return out


def wait(client, h, path="/api/analyze/status", key="running", timeout=60):
    for _ in range(timeout * 10):
        r = client.get(path, headers=h).json()
        if not r.get(key):
            return r
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_new_workspace_upload_cold_start_and_learning(client, nimbus):
    ws = client.post("/api/workspaces", json={"name": "Nimbus Retail"}).json()
    assert ws["bank_id"].startswith("spendsight-nimbus-retail")
    h = {"X-Workspace": ws["id"]}
    assert client.get("/api/status", headers=h).json()["has_data"] is False

    with open(nimbus / "nimbus_cost_explorer.csv", "rb") as f:
        info = client.post("/api/upload/billing", headers=h, files={"file": ("ce.csv", f, "text/csv")}).json()
    assert info["format"] == "aws_cost_explorer" and info["wide"] and len(info["preview"]) == 10

    rep = client.post("/api/upload/billing/commit", headers=h, json={"mapping": {}}).json()
    assert rep["rows_skipped"] == 2 and rep["days"] == 92 and rep["date_from"] == "2026-07-01"

    with open(nimbus / "nimbus_deploys.csv", "rb") as f:
        dep = client.post("/api/upload/deploys", headers=h, files={"file": ("d.csv", f, "text/csv")}).json()
    assert dep["rows_valid"] > 50 and dep["mapping"]["summary"] == "description"

    assert client.post("/api/analyze", headers=h).status_code == 200
    job = wait(client, h)
    assert job["done"] and [s["status"] for s in job["steps"]] == ["done"] * 4, job
    assert "cold start" in job["steps"][2]["detail"].lower()

    alerts = {a["anomaly"]["start_date"] + a["anomaly"]["service"]: a for a in client.get("/api/anomalies", headers=h).json()}
    jul, aug = alerts["2026-07-15OpenSearch Service"], alerts["2026-08-15OpenSearch Service"]
    assert jul["verdicts"]["memory"]["verdict"] == "UNKNOWN"
    assert jul["verdicts"]["memory"]["explanation"].startswith("Unknown: no history yet, please classify")
    assert aug["verdicts"]["memory"]["verdict"] == "UNKNOWN"  # nothing classified yet

    # A human classifies July; the agent re-checks August with that memory.
    fb = client.post(f"/api/anomalies/{jul['anomaly']['id']}/feedback", headers=h,
                     json={"response": "expected", "note": "monthly catalog reindex on the 15th", "author": "DevOps"}).json()
    assert fb["feedback"][0]["author"] == "DevOps"
    for _ in range(100):
        aug = client.get(f"/api/anomalies/{aug['anomaly']['id']}/view", headers=h).json()
        if not aug["reanalyzing"]:
            break
        time.sleep(0.1)
    assert aug["history"] == 1 and aug["verdicts"]["memory"]["verdict"] == "EXPECTED"

    # Learning curve for uploaded data is scored against human feedback.
    curve = client.get("/api/learning-curve", headers=h).json()
    assert [r["month"] for r in curve] == ["2026-07", "2026-08", "2026-09"] and curve[0]["incidents"] == 1
    assert client.post("/api/replay/reset", headers=h).status_code == 400  # replay is demo-only


def test_bad_uploads_are_rejected_clearly(client):
    h = {"X-Workspace": client.post("/api/workspaces", json={"name": "x"}).json()["id"]}
    r = client.post("/api/upload/billing", headers=h, files={"file": ("e.csv", b"", "text/csv")})
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    client.post("/api/upload/billing", headers=h, files={"file": ("m.csv", b"day,thing\n2026-07-01,EC2", "text/csv")})
    r = client.post("/api/upload/billing/commit", headers=h, json={"mapping": {"date": "day", "service": "thing"}})
    assert r.status_code == 400 and "cost_usd" in r.json()["detail"]
    assert client.get("/api/status", headers={"X-Workspace": "nope"}).status_code == 404
    assert client.get("/api/upload/template").text.startswith("date,account,service")
