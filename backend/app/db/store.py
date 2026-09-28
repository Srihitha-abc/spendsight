"""SQLite storage for billing data, anomalies, verdicts and feedback. Memory itself lives in Hindsight."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.models.schemas import Anomaly, VerdictRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS anomalies (
    id TEXT PRIMARY KEY, month TEXT, start_date TEXT, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    anomaly_id TEXT, mode TEXT, payload TEXT NOT NULL, created_at TEXT,
    PRIMARY KEY (anomaly_id, mode)
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, anomaly_id TEXT, response TEXT, author TEXT, note TEXT,
    source TEXT, submitted_at TEXT, retained INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _exec(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.fetchall()

    # ---- billing / deploys (bulk tables, replaced on ingest) ----
    def replace_table(self, name: str, df: pd.DataFrame) -> None:
        with self._lock:
            df.to_sql(name, self._conn, if_exists="replace", index=False)

    def read_table(self, name: str) -> pd.DataFrame:
        with self._lock:
            try:
                return pd.read_sql(f"SELECT * FROM {name}", self._conn)
            except Exception:
                return pd.DataFrame()

    # ---- anomalies ----
    def save_anomalies(self, anomalies: list[Anomaly]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM anomalies")
            self._conn.executemany(
                "INSERT INTO anomalies VALUES (?, ?, ?, ?)",
                [(a.id, a.month, a.start_date, a.model_dump_json()) for a in anomalies])
            self._conn.commit()

    def anomalies(self, month: str | None = None, until_month: str | None = None) -> list[Anomaly]:
        sql, params = "SELECT payload FROM anomalies", []
        if month:
            sql += " WHERE month = ?"
            params.append(month)
        elif until_month:
            sql += " WHERE month <= ?"
            params.append(until_month)
        rows = self._exec(sql + " ORDER BY start_date", tuple(params))
        return [Anomaly.model_validate_json(r["payload"]) for r in rows]

    def anomaly(self, anomaly_id: str) -> Anomaly | None:
        rows = self._exec("SELECT payload FROM anomalies WHERE id = ?", (anomaly_id,))
        return Anomaly.model_validate_json(rows[0]["payload"]) if rows else None

    # ---- verdicts ----
    def save_verdict(self, anomaly_id: str, record: VerdictRecord) -> None:
        self._exec("INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?)",
                   (anomaly_id, record.mode, record.model_dump_json(), now_iso()))

    def verdicts(self, anomaly_id: str) -> dict[str, VerdictRecord]:
        rows = self._exec("SELECT mode, payload FROM verdicts WHERE anomaly_id = ?", (anomaly_id,))
        return {r["mode"]: VerdictRecord.model_validate_json(r["payload"]) for r in rows}

    def all_verdicts(self) -> dict[str, dict[str, VerdictRecord]]:
        out: dict[str, dict[str, VerdictRecord]] = {}
        for r in self._exec("SELECT anomaly_id, mode, payload FROM verdicts"):
            out.setdefault(r["anomaly_id"], {})[r["mode"]] = VerdictRecord.model_validate_json(r["payload"])
        return out

    # ---- feedback ----
    def add_feedback(self, anomaly_id: str, response: str, author: str, note: str, source: str,
                     submitted_at: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO feedback (anomaly_id, response, author, note, source, submitted_at) VALUES (?,?,?,?,?,?)",
                (anomaly_id, response, author, note, source, submitted_at or now_iso()))
            self._conn.commit()
            return int(cur.lastrowid)

    def mark_retained(self, feedback_id: int) -> None:
        self._exec("UPDATE feedback SET retained = 1 WHERE id = ?", (feedback_id,))

    def feedback(self, anomaly_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM feedback WHERE anomaly_id = ? ORDER BY submitted_at, id", (anomaly_id,))
        return [dict(r) for r in rows]

    def feedback_anomaly_ids(self) -> list[str]:
        return [r["anomaly_id"] for r in self._exec("SELECT DISTINCT anomaly_id FROM feedback")]

    def feedback_count(self) -> int:
        return int(self._exec("SELECT COUNT(DISTINCT anomaly_id) AS n FROM feedback")[0]["n"])

    # ---- kv (replay state) ----
    def get(self, key: str, default: str | None = None) -> str | None:
        rows = self._exec("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))

    def get_json(self, key: str, default=None):
        raw = self.get(key)
        return json.loads(raw) if raw else default

    def set_json(self, key: str, value) -> None:
        self.set(key, json.dumps(value))

    def reset_run_state(self) -> None:
        with self._lock:
            self._conn.executescript("DELETE FROM verdicts; DELETE FROM feedback; DELETE FROM kv;")
            self._conn.commit()
