"""Workspaces: each one has its own data folder, SQLite database and Hindsight memory bank."""
from __future__ import annotations

import json
import re
import secrets
import threading
from pathlib import Path

from app.config import Settings
from app.db.store import now_iso
from app.pipeline.service import SpendSight

DEMO_ID = "acme"


class WorkspaceManager:
    def __init__(self, settings: Settings):
        self.s = settings
        self.root = settings.data_dir / "workspaces"
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / "registry.json"
        self._lock = threading.Lock()
        self._instances: dict[str, SpendSight] = {}
        self.registry = self._load()

    def _load(self) -> list[dict]:
        demo = dict(id=DEMO_ID, name="Acme Logistics", kind="demo", bank_id=self.s.hindsight_bank_id,
                    description="6 months of synthetic AWS billing with planted incidents", created_at=None)
        custom = json.loads(self.registry_path.read_text(encoding="utf-8")) if self.registry_path.exists() else []
        return [demo, *[w for w in custom if w["id"] != DEMO_ID]]

    def _save(self) -> None:
        self.registry_path.write_text(json.dumps([w for w in self.registry if w["kind"] != "demo"], indent=2),
                                      encoding="utf-8")

    def list(self) -> list[dict]:
        out = []
        for w in self.registry:
            inst = self._instances.get(w["id"])
            data_dir = self._data_dir(w["id"])
            out.append(w | dict(has_data=(data_dir / "billing.csv").exists(),
                                anomalies=len(inst.store.anomalies()) if inst else None))
        return out

    def create(self, name: str) -> dict:
        name = name.strip()[:60] or "New workspace"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "workspace"
        wid = f"{slug}-{secrets.token_hex(2)}"
        w = dict(id=wid, name=name, kind="custom", bank_id=f"spendsight-{wid}", created_at=now_iso(),
                 description="Uploaded billing data")
        with self._lock:
            self.registry.append(w)
            self._save()
        return w

    def _data_dir(self, wid: str) -> Path:
        return self.s.data_dir if wid == DEMO_ID else self.root / wid

    def get(self, wid: str | None) -> SpendSight | None:
        wid = wid or DEMO_ID
        w = next((x for x in self.registry if x["id"] == wid), None)
        if not w:
            return None
        with self._lock:
            if wid not in self._instances:
                d = self._data_dir(wid)
                d.mkdir(parents=True, exist_ok=True)
                s = self.s.model_copy(update=dict(
                    data_dir=d, hindsight_bank_id=w["bank_id"],
                    database_path=self.s.database_path if wid == DEMO_ID else d / "spendsight.db"))
                self._instances[wid] = SpendSight(s, workspace=w)
            return self._instances[wid]
