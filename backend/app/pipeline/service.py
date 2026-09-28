"""Application service for one workspace: ingest, analyze, feedback -> retain, replay, learning curve, uploads."""
from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, time, timezone
from pathlib import Path

import pandas as pd

from app.config import Settings
from app.db.store import Store, now_iso
from app.detection.detector import detect_anomalies
from app.llm.groq_client import build_llm
from app.memory.hindsight_client import MemoryBackend, build_memory
from app.models.schemas import Anomaly, VerdictLabel
from app.pipeline.agent import Agent, as_of
from app.pipeline.narrative import correlate_deploys, deploy_memory, feedback_memory

log = logging.getLogger(__name__)
MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]  # demo replay window
MAX_ANALYZE = 40  # verdicts generated per analysis run (most recent first)
FEEDBACK_TRUTH = {"expected": "EXPECTED", "waste": "NEW_WASTE"}
DEPLOY_COLUMNS = ["date", "time_utc", "service", "team", "deploy_id", "author", "summary"]
ANALYSIS_STEPS = [("parse", "Parsing billing data"), ("detect", "Detecting anomalies"),
                  ("memory", "Checking memory"), ("verdicts", "Generating verdicts")]


class SpendSight:
    def __init__(self, settings: Settings, memory: MemoryBackend | None = None, llm=None, store: Store | None = None,
                 workspace: dict | None = None):
        self.s = settings
        self.ws = workspace or dict(id="acme", name="Acme Logistics", kind="demo")
        self.store = store or Store(settings.database_path)
        self.memory = memory or build_memory(settings.hindsight_api_key, settings.hindsight_base_url,
                                             settings.hindsight_bank_id, company=self.ws["name"])
        self.llm = llm if llm is not None else build_llm(settings.groq_api_key, settings.groq_model,
                                                          settings.groq_fallback_model)
        self.billing = pd.DataFrame()
        self.deploys = pd.DataFrame(columns=DEPLOY_COLUMNS)
        self.seed_feedback: list[dict] = []
        self.truth: list[dict] = []
        self.replay_lock = threading.Lock()
        self.replay_log: list[str] = []
        self.replay_running = False
        self.job: dict = dict(running=False, done=False, error=None, steps=[])
        self.reanalyzing: set[str] = set()
        self.ingest()

    # ------------------------------------------------------------------ ingest
    def ingest(self) -> int:
        d: Path = self.s.data_dir
        self.billing = pd.read_csv(d / "billing.csv", dtype={"resource_id": str}, keep_default_na=False) \
            if (d / "billing.csv").exists() else pd.DataFrame()
        if not self.billing.empty:
            self.billing["cost_usd"] = pd.to_numeric(self.billing["cost_usd"], errors="coerce").fillna(0.0)
        self.deploys = pd.read_csv(d / "deploys.csv", dtype={"date": str, "time_utc": str}, keep_default_na=False) \
            if (d / "deploys.csv").exists() else pd.DataFrame(columns=DEPLOY_COLUMNS)
        self.seed_feedback = json.loads((d / "seed_feedback.json").read_text(encoding="utf-8")) \
            if (d / "seed_feedback.json").exists() else []
        self.truth = json.loads((d / "planted_events.json").read_text(encoding="utf-8")) \
            if (d / "planted_events.json").exists() else []
        if not self.billing.empty:
            self.store.replace_table("billing", self.billing)
            self.store.replace_table("deploys", self.deploys)
        anomalies = detect_anomalies(self.billing, self.s.baseline_days, self.s.z_threshold, self.s.min_delta_usd)
        self.store.save_anomalies(anomalies)
        self.agent = Agent(self.memory, self.llm, self.deploys, anchor_time=self.has_replay)
        return len(anomalies)

    @property
    def has_replay(self) -> bool:
        return bool(self.seed_feedback and self.truth)

    def months(self) -> list[str]:
        if self.has_replay:
            return MONTHS
        if self.billing.empty:
            return []
        return sorted(self.billing["date"].astype(str).str[:7].unique().tolist())

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        b = self.billing
        return dict(workspace=self.ws, memory_backend=self.memory.name,
                    llm=self.llm.name if self.llm else "offline-heuristic", bank_id=self.s.hindsight_bank_id,
                    replay_month=self.store.get("replay_month", ""), replay_running=self.replay_running,
                    has_replay=self.has_replay, months=self.months(), billing_rows=len(b),
                    has_data=not b.empty, anomalies=len(self.store.anomalies()), deploys=len(self.deploys),
                    date_from=str(b["date"].min()) if len(b) else None, date_to=str(b["date"].max()) if len(b) else None,
                    feedback_count=self.store.feedback_count())

    # ------------------------------------------------------------------ analyze
    def history(self, a: Anomaly) -> int:
        """Earlier anomalies in the same (account, service, team) that someone has classified. 0 = cold start."""
        n = 0
        for other in self.store.feedback_anomaly_ids():
            parts = other.split("|")
            if len(parts) == 4 and tuple(parts[:3]) == (a.account, a.service, a.team_tag) and parts[3] < a.start_date:
                n += 1
        return n

    def _analyze(self, a: Anomaly, modes=("memory", "no_memory")) -> None:
        for mode in modes:
            self.store.save_verdict(a.id, self.agent.analyze(a, mode, history=self.history(a)))

    def analyze(self, anomaly_id: str, modes=("memory", "no_memory")) -> dict | None:
        a = self.store.anomaly(anomaly_id)
        if not a:
            return None
        self._analyze(a, modes)
        return self.view(a)

    def view(self, a: Anomaly) -> dict:
        t = self.truth_for(a)
        return dict(anomaly=a.model_dump() | {"ratio": round(a.ratio, 2) if a.baseline_usd else None},
                    verdicts={m: v.model_dump() for m, v in self.store.verdicts(a.id).items()},
                    feedback=self.store.feedback(a.id), deploys=correlate_deploys(a, self.deploys),
                    truth=t["label"] if t else None, truth_source=t.get("source") if t else None,
                    history=self.history(a), reanalyzing=a.id in self.reanalyzing)

    def list_view(self, month: str | None = None) -> list[dict]:
        until = self.store.get("replay_month") or None
        items = self.store.anomalies(month=month) if month else self.store.anomalies()
        if until and not month and self.store.get("replay_active") == "1":
            items = [a for a in items if a.month <= until]
        return [self.view(a) for a in reversed(items)]

    # ------------------------------------------------------------------ analysis job (processing screen)
    def start_analysis(self) -> bool:
        if self.job.get("running"):
            return False
        self.job = dict(running=True, done=False, error=None, redirect="dashboard",
                        steps=[dict(key=k, label=lbl, status="pending", detail="") for k, lbl in ANALYSIS_STEPS])
        threading.Thread(target=self._run_analysis, daemon=True).start()
        return True

    def _step(self, key: str, status: str, detail: str = "") -> None:
        for s in self.job["steps"]:
            if s["key"] == key:
                s["status"], s["detail"] = status, detail

    def _run_analysis(self) -> None:
        current = "parse"
        try:
            self._step("parse", "running")
            self.ingest()
            if self.billing.empty:
                raise ValueError("No billing data in this workspace yet. Load demo data or upload a CSV.")
            b = self.billing
            self._step("parse", "done", f"{len(b):,} daily rows, {b['date'].nunique()} days, "
                                        f"${b['cost_usd'].sum():,.0f} total")
            current = "detect"
            self._step("detect", "running")
            anomalies = self.store.anomalies()
            self._step("detect", "done", f"{len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} "
                                         "(z > 3 and >= $150 over the 14-day baseline)")
            current = "memory"
            self._step("memory", "running")
            try:
                self.memory.setup_bank(reset=False)
            except Exception as e:
                log.warning("bank setup failed: %s", e)
            n_fb = self.store.feedback_count()
            detail = (f"{n_fb} classified anomalies in memory" if n_fb else
                      f"Empty memory: new workspace, cold start (bank {self.s.hindsight_bank_id})")
            if not self.has_replay and len(self.deploys):
                try:
                    self.memory.retain_many([deploy_memory(r) for r in self.deploys.itertuples()])
                    detail += f"; retained {len(self.deploys)} deploys"
                except Exception as e:
                    log.warning("deploy retain failed: %s", e)
                    detail += f"; deploy retain failed ({type(e).__name__})"
            self._step("memory", "done", detail)
            current = "verdicts"
            if self.has_replay:
                self._step("verdicts", "done", "Demo workspace: open Replay to watch memory build month by month")
                self.job["redirect"] = "replay"
            else:
                todo = list(reversed(anomalies))[:MAX_ANALYZE]
                for i, a in enumerate(todo, 1):
                    self._step("verdicts", "running", f"{i}/{len(todo)}: {a.service} {a.start_date}")
                    self._analyze(a)
                extra = f" (most recent {MAX_ANALYZE} of {len(anomalies)})" if len(anomalies) > MAX_ANALYZE else ""
                self._step("verdicts", "done", f"{len(todo)} verdicts{extra}")
            self.job["done"] = True
        except Exception as e:
            log.exception("analysis failed")
            self._step(current, "error", str(e))
            self.job["error"] = str(e)
        finally:
            self.job["running"] = False

    # ------------------------------------------------------------------ data input
    def write_billing(self, df: pd.DataFrame) -> None:
        self.s.data_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.s.data_dir / "billing.csv", index=False)
        for f in ("seed_feedback.json", "planted_events.json"):  # uploaded data has no planted story
            (self.s.data_dir / f).unlink(missing_ok=True)
        self.store.reset_run_state()
        self.ingest()

    def write_deploys(self, df: pd.DataFrame) -> None:
        self.s.data_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.s.data_dir / "deploys.csv", index=False)
        self.ingest()

    def load_demo(self, demo_dir: Path) -> None:
        if not (demo_dir / "billing.csv").exists():
            import subprocess, sys  # noqa: E401 - only needed on first run
            script = Path(__file__).resolve().parents[3] / "scripts" / "generate_data.py"
            subprocess.run([sys.executable, str(script), "--out", str(demo_dir)], check=True, capture_output=True)
        if demo_dir.resolve() != self.s.data_dir.resolve():
            self.s.data_dir.mkdir(parents=True, exist_ok=True)
            for f in ("billing.csv", "deploys.csv", "seed_feedback.json", "planted_events.json"):
                shutil.copy(demo_dir / f, self.s.data_dir / f)
        self.ingest()

    # ------------------------------------------------------------------ feedback
    def submit_feedback(self, anomaly_id: str, response: str, note: str, author: str, source: str = "user",
                        submitted_at: str | None = None) -> dict | None:
        a = self.store.anomaly(anomaly_id)
        if not a:
            return None
        fid = self.store.add_feedback(a.id, response, author, note, source, submitted_at or now_iso())
        thread = self.store.feedback(a.id)
        v = self.store.verdicts(a.id).get("memory")
        mem = feedback_memory(a, thread, v.verdict.value if v else None, correlate_deploys(a, self.deploys))
        try:
            self.memory.retain(mem["content"], timestamp=mem["timestamp"], context=mem["context"],
                               document_id=mem["document_id"], tags=mem["tags"], metadata=mem["metadata"])
            self.store.mark_retained(fid)
        except Exception as e:
            log.exception("retain failed for %s: %s", a.id, e)
        if source == "user":
            self._reanalyze_related(a)
        return self.view(a)

    def _reanalyze_related(self, a: Anomaly) -> None:
        """A human just taught the agent something: re-check later alerts of the same pattern with that memory."""
        related = [x for x in self.store.anomalies() if (x.account, x.service, x.team_tag)
                   == (a.account, a.service, a.team_tag) and x.start_date > a.start_date
                   and self.store.verdicts(x.id) and not self.store.feedback(x.id)]
        if not related:
            return
        self.reanalyzing |= {x.id for x in related}

        def run():
            for x in related:
                try:
                    self._analyze(x, ("memory",))
                finally:
                    self.reanalyzing.discard(x.id)

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ replay (demo workspace)
    def replay_reset(self) -> None:
        with self.replay_lock:
            self.store.reset_run_state()
            self.memory.setup_bank(reset=True)
            self.store.set("replay_active", "1")
            self.store.set("replay_month", "")
            self.store.set_json("retained_deploys", [])
            self.replay_log = [f"Memory bank reset. Agent knows nothing about {self.ws['name']} yet."]

    def replay_step(self) -> str | None:
        with self.replay_lock:
            if self.replay_running:
                return None
            done = self.store.get("replay_month", "")
            nxt = next((m for m in MONTHS if m > done), None)
            if not nxt:
                return None
            self.replay_running = True
        try:
            self._run_month(nxt)
            self.store.set("replay_month", nxt)
        except Exception as e:
            log.exception("replay of %s failed", nxt)
            self.replay_log.append(f"ERROR while replaying {nxt}: {type(e).__name__}: {e}")
            return None
        finally:
            self.replay_running = False
        return nxt

    def _retain_deploys_until(self, until: datetime) -> None:
        retained = set(self.store.get_json("retained_deploys", []))
        ts = pd.to_datetime(self.deploys["date"] + "T" + self.deploys["time_utc"], utc=True)
        todo = self.deploys[(ts <= until) & ~self.deploys["deploy_id"].isin(retained)]
        if todo.empty:
            return
        self.memory.retain_many([deploy_memory(r) for r in todo.itertuples()])
        self.store.set_json("retained_deploys", sorted(retained | set(int(x) for x in todo["deploy_id"])))
        self.replay_log.append(f"Retained {len(todo)} deploy-log events up to {until:%Y-%m-%d}.")

    def _run_month(self, month: str) -> None:
        self.replay_log.append(f"--- {month} ---")
        for a in self.store.anomalies(month=month):
            self._retain_deploys_until(as_of(a))
            self._analyze(a)
            v = self.store.verdicts(a.id)
            self.replay_log.append(f"{a.start_date} {a.service} {a.team_tag} +${a.delta_usd:,.0f}/day -> "
                                   f"memory: {v['memory'].verdict.value} | no memory: {v['no_memory'].verdict.value}")
            for fb in self._seed_for(a):
                self.submit_feedback(a.id, fb["response"], fb["note"], fb["author"], "seed", fb["submitted_at"])
                self.replay_log.append(f"  {fb['author']}: {fb['response']} -> retained into memory")
        last_day = (pd.Timestamp(f"{month}-01") + pd.offsets.MonthEnd(0)).date()
        self._retain_deploys_until(datetime.combine(last_day, time(23, 59), tzinfo=timezone.utc))
        self.memory.refresh_mental_model()

    def _seed_for(self, a: Anomaly) -> list[dict]:
        out = []
        for fb in self.seed_feedback:
            m = fb["match"]
            if ((m["account"], m["service"], m["team_tag"]) == (a.account, a.service, a.team_tag)
                    and m["date_from"] <= a.start_date <= m["date_to"]):
                out.append(fb)
        return sorted(out, key=lambda f: f["submitted_at"])

    # ------------------------------------------------------------------ evaluation
    def truth_for(self, a: Anomaly) -> dict | None:
        """Planted ground truth for the demo; otherwise the final human classification."""
        for t in self.truth:
            if ((t["account"], t["service"], t["team_tag"]) == (a.account, a.service, a.team_tag)
                    and t["date_from"] <= a.end_date and a.start_date <= t["date_to"]):
                return t | {"source": "planted"}
        thread = self.store.feedback(a.id)
        final = thread[-1]["response"] if thread else None
        return dict(label=FEEDBACK_TRUTH[final], source="feedback") if final in FEEDBACK_TRUTH else None

    def learning_curve(self) -> list[dict]:
        verdicts = self.store.all_verdicts()
        rows = []
        for month in self.months():
            row = dict(month=month, incidents=0)
            for mode in ("memory", "no_memory"):
                row |= {f"{mode}_false_alarms": 0, f"{mode}_correct": 0, f"{mode}_missed_waste": 0}
            for a in self.store.anomalies(month=month):
                v, t = verdicts.get(a.id), self.truth_for(a)
                if not v or not t:
                    continue
                row["incidents"] += 1
                for mode in ("memory", "no_memory"):
                    if mode not in v:
                        continue
                    label = v[mode].verdict.value
                    row[f"{mode}_correct"] += label == t["label"]
                    row[f"{mode}_false_alarms"] += t["label"] == "EXPECTED" and label != "EXPECTED"
                    row[f"{mode}_missed_waste"] += t["label"] == "NEW_WASTE" and label == VerdictLabel.EXPECTED.value
            rows.append(row)
        return rows

    def insights(self) -> dict:
        """Spend by verdict (known vs new) and per week, for the Insights view."""
        verdicts = self.store.all_verdicts()
        by_label: dict[str, float] = {}
        weekly: dict[str, dict[str, float]] = {}
        for a in self.store.anomalies():
            v = verdicts.get(a.id, {}).get("memory")
            label = v.verdict.value if v else "NOT_ANALYSED"
            by_label[label] = by_label.get(label, 0.0) + a.total_excess_usd
            wk = pd.Timestamp(a.start_date).to_period("W-SUN").start_time.strftime("%Y-%m-%d")
            bucket = weekly.setdefault(wk, {"known": 0.0, "new": 0.0})
            bucket["known" if label in ("EXPECTED", "EXPECTED_BUT_UNUSUAL") else "new"] += a.total_excess_usd
        return dict(by_verdict={k: round(v, 2) for k, v in by_label.items()},
                    weekly=[dict(week=k, **{x: round(y, 2) for x, y in v.items()}) for k, v in sorted(weekly.items())])

    # ------------------------------------------------------------------ chat
    def chat(self, question: str) -> dict:
        if self.billing.empty:
            return dict(answer="No billing data in this workspace yet.", engine="none")
        b = self.billing.assign(month=self.billing["date"].astype(str).str[:7])
        by = b.groupby(["month", "account", "service", "team_tag"])["cost_usd"].sum().round(0).reset_index()
        piv = by.pivot_table(index=["account", "service", "team_tag"], columns="month", values="cost_usd").fillna(0)
        top = piv.assign(spread=piv.max(axis=1) - piv.min(axis=1)).sort_values("spread", ascending=False).head(12)
        totals = b.groupby("month")["cost_usd"].sum().round(0).to_dict()
        context = (f"Monthly totals: {totals}. Biggest-moving cost groups (monthly $): "
                   + top.drop(columns="spread").to_json(orient="index"))
        try:
            answer = self.memory.ask(f"{question}\n\nBilling context: {context}")
            if answer:
                return dict(answer=answer, engine=self.memory.name)
        except Exception as e:
            log.warning("reflect chat failed: %s", e)
        try:
            recalled = self.memory.recall(question)
        except Exception:
            recalled = []
        if self.llm:
            mem = "\n".join(f"- {m.text}" for m in recalled[:10]) or "(none)"
            try:
                text, model = self.llm.complete(
                    f"You are SpendSight, a FinOps assistant for {self.ws['name']}. Answer in 3-6 sentences with "
                    "dollar figures, using the billing context and recalled memories. Say when memory has no "
                    "explanation.",
                    f"Question: {question}\n\nBilling context: {context}\n\nMemories:\n{mem}", json_mode=False)
                return dict(answer=text, engine=f"groq:{model}")
            except Exception as e:
                log.warning("chat llm failed: %s", e)
        lines = [m.text.split("\n")[0][:220] for m in recalled[:4]]
        return dict(answer="Monthly totals: " + ", ".join(f"{k}: ${v:,.0f}" for k, v in totals.items())
                    + (". Related memories: " + " | ".join(lines) if lines else ". No related memories yet."),
                    engine="offline")
