from __future__ import annotations

import threading

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.ingest.normalize import DEPLOY_TEMPLATE, TEMPLATE, inspect, normalize, normalize_deploys
from app.models.schemas import ChatIn, FeedbackIn
from app.pipeline.service import SpendSight
from app.workspaces import WorkspaceManager

router = APIRouter(prefix="/api")
MAX_UPLOAD = 50 * 1024 * 1024


def manager(request: Request) -> WorkspaceManager:
    return request.app.state.workspaces


def svc(request: Request) -> SpendSight:
    """The workspace for this request, from the X-Workspace header (default: the Acme demo)."""
    s = manager(request).get(request.headers.get("x-workspace"))
    if s is None:
        raise HTTPException(404, "unknown workspace")
    return s


# ---------------------------------------------------------------- workspaces
class WorkspaceIn(BaseModel):
    name: str


@router.get("/workspaces")
def list_workspaces(request: Request):
    return manager(request).list()


@router.post("/workspaces")
def create_workspace(request: Request, body: WorkspaceIn):
    return manager(request).create(body.name)


# ---------------------------------------------------------------- data input
async def _read(file: UploadFile) -> bytes:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 50 MB.")
    if not raw.strip():
        raise HTTPException(400, "The file is empty.")
    return raw


@router.post("/upload/billing")
async def upload_billing(request: Request, file: UploadFile = File(...)):
    """Step 1: store the raw file, detect its format, suggest a column mapping, return a 10-row preview."""
    s = svc(request)
    raw = await _read(file)
    try:
        info = inspect(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    up = s.s.data_dir / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    (up / "billing_raw.csv").write_bytes(raw)
    return info | {"filename": file.filename}


class MappingIn(BaseModel):
    mapping: dict[str, str | None] = {}


@router.post("/upload/billing/commit")
def commit_billing(request: Request, body: MappingIn):
    """Step 2: apply the mapping, validate, aggregate to daily rows and save as this workspace's billing data."""
    s = svc(request)
    raw_path = s.s.data_dir / "uploads" / "billing_raw.csv"
    if not raw_path.exists():
        raise HTTPException(400, "Upload a billing file first.")
    try:
        daily, report = normalize(raw_path.read_bytes(), body.mapping or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    s.write_billing(daily)
    return report | {"anomalies_preview": len(s.store.anomalies())}


@router.post("/upload/deploys")
async def upload_deploys(request: Request, file: UploadFile = File(...)):
    s = svc(request)
    try:
        df, report = normalize_deploys(await _read(file))
    except ValueError as e:
        raise HTTPException(400, str(e))
    s.write_deploys(df)
    return report


@router.get("/upload/template")
def template(kind: str = "billing"):
    body, name = (DEPLOY_TEMPLATE, "deploys_template.csv") if kind == "deploys" else (TEMPLATE, "billing_template.csv")
    return PlainTextResponse(body, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/demo/load")
def load_demo(request: Request):
    s = svc(request)
    s.load_demo(manager(request).s.data_dir)
    return s.status()


@router.post("/analyze")
def start_analysis(request: Request):
    s = svc(request)
    if not s.start_analysis():
        raise HTTPException(409, "analysis already running")
    return s.job


@router.get("/analyze/status")
def analysis_status(request: Request):
    return svc(request).job


@router.get("/insights")
def insights(request: Request):
    return svc(request).insights()


@router.get("/status")
def status(request: Request):
    return svc(request).status()


@router.get("/costs/summary")
def costs_summary(request: Request, account: str | None = None):
    """Daily cost by service (top 6 + Other), with anomaly markers."""
    s = svc(request)
    if s.billing.empty:
        return dict(series=[], services=[], anomalies=[], accounts=[])
    accounts = sorted(s.billing["account"].astype(str).unique().tolist())
    b = s.billing if not account else s.billing[s.billing["account"] == account]
    if b.empty:
        return dict(series=[], services=[], anomalies=[], accounts=accounts)
    top = b.groupby("service")["cost_usd"].sum().nlargest(6).index.tolist()
    b = b.assign(service=b["service"].where(b["service"].isin(top), "Other"))
    piv = b.pivot_table(index="date", columns="service", values="cost_usd", aggfunc="sum").fillna(0).round(2)
    services = [*top, *(["Other"] if "Other" in piv.columns else [])]
    series = [dict(date=d, total=round(float(r.sum()), 2), **{k: float(r[k]) for k in services})
              for d, r in piv.iterrows()]
    totals = {row["date"]: row["total"] for row in series}
    anomalies = [dict(id=a.id, date=a.peak_date, total=totals.get(a.peak_date), service=a.service,
                      delta_usd=a.delta_usd) for a in s.store.anomalies()
                 if not account or a.account == account]
    return dict(series=series, services=services, anomalies=anomalies, accounts=accounts)


@router.get("/costs/group")
def costs_group(request: Request, account: str, service: str, team_tag: str):
    """Daily cost for one (account, service, team) group plus its 14-day baseline."""
    s = svc(request)
    b = s.billing
    g = b if b.empty else b[(b["account"] == account) & (b["service"] == service) & (b["team_tag"] == team_tag)]
    if g.empty:
        raise HTTPException(404, "unknown group")
    daily = g.groupby("date")["cost_usd"].sum()
    daily.index = pd.to_datetime(daily.index)
    daily = daily.asfreq("D", fill_value=0)
    base = daily.shift(1).rolling(s.s.baseline_days, min_periods=s.s.baseline_days).mean()
    return [dict(date=str(d.date()), cost=round(float(c), 2),
                 baseline=None if pd.isna(base[d]) else round(float(base[d]), 2)) for d, c in daily.items()]


@router.get("/anomalies")
def anomalies(request: Request, month: str | None = None):
    return svc(request).list_view(month)


@router.get("/anomalies/{anomaly_id:path}/view")
def anomaly(request: Request, anomaly_id: str):
    s = svc(request)
    a = s.store.anomaly(anomaly_id)
    if not a:
        raise HTTPException(404, "anomaly not found")
    return s.view(a)


@router.post("/anomalies/{anomaly_id:path}/analyze")
def analyze(request: Request, anomaly_id: str, mode: str = "both"):
    modes = ("memory", "no_memory") if mode == "both" else (mode,)
    if any(m not in ("memory", "no_memory") for m in modes):
        raise HTTPException(400, "mode must be memory, no_memory or both")
    out = svc(request).analyze(anomaly_id, modes)
    if out is None:
        raise HTTPException(404, "anomaly not found")
    return out


@router.post("/anomalies/{anomaly_id:path}/feedback")
def feedback(request: Request, anomaly_id: str, body: FeedbackIn):
    out = svc(request).submit_feedback(anomaly_id, body.response.value, body.note.strip(), body.author.strip() or "you")
    if out is None:
        raise HTTPException(404, "anomaly not found")
    return out


@router.get("/learning-curve")
def learning_curve(request: Request):
    return svc(request).learning_curve()


@router.get("/replay/state")
def replay_state(request: Request):
    s = svc(request)
    return dict(month=s.store.get("replay_month", ""), running=s.replay_running, log=s.replay_log[-60:],
                active=s.store.get("replay_active") == "1", months=s.months(), available=s.has_replay)


@router.post("/replay/reset")
def replay_reset(request: Request):
    s = svc(request)
    if not s.has_replay:
        raise HTTPException(400, "Replay needs the demo's labelled history; load demo data in this workspace.")
    if s.replay_running:
        raise HTTPException(409, "replay is running")
    s.replay_reset()
    return replay_state(request)


@router.post("/replay/next")
def replay_next(request: Request, all: bool = False):
    s = svc(request)
    if s.replay_running:
        raise HTTPException(409, "replay is already running")

    def run():
        while s.replay_step() and all:
            pass

    threading.Thread(target=run, daemon=True).start()
    return dict(started=True)


@router.get("/memory/model")
def mental_model(request: Request):
    s = svc(request)
    return dict(name="Known recurring cost patterns", content=s.memory.mental_model(), backend=s.memory.name)


@router.post("/chat")
def chat(request: Request, body: ChatIn):
    return svc(request).chat(body.question)


@router.post("/ingest")
def ingest(request: Request):
    return dict(anomalies=svc(request).ingest())
