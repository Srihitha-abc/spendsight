"""End-to-end replay with the local memory stand-in and no LLM: memory must reduce false alarms."""
from app.config import ROOT, Settings
from app.memory.hindsight_client import LocalMemory
from app.pipeline.service import SpendSight


def test_replay_learns_and_corrections_replace(tmp_path):
    s = Settings(groq_api_key="", hindsight_api_key="", data_dir=ROOT / "data", database_path=tmp_path / "t.db")
    app = SpendSight(s, memory=LocalMemory(), llm=None)
    app.replay_reset()
    while app.replay_step():
        pass
    curve = app.learning_curve()
    with_mem = sum(r["memory_false_alarms"] for r in curve)
    without = sum(r["no_memory_false_alarms"] for r in curve)
    assert with_mem < without
    assert sum(r["memory_missed_waste"] for r in curve) == 0
    # Sep: known retrain at 2.3x cost is flagged as unusual, the forgotten GPU as new waste.
    sep = {a.start_date: app.store.verdicts(a.id)["memory"].verdict.value for a in app.store.anomalies("2026-09")}
    assert sep["2026-09-01"] == "EXPECTED_BUT_UNUSUAL"
    assert sep["2026-09-17"] == "NEW_WASTE"
    # Conflicting feedback on the load test: one document, final resolution is the correction.
    lt = next(d for k, d in app.memory.docs.items() if "2026-07-14" in k)
    assert lt["metadata"]["response"] == "expected" and "corrects earlier feedback" in lt["content"]
