"""The single module that talks to Hindsight. Everything else goes through `MemoryBackend`.

`HindsightMemory` uses Hindsight Cloud (retain / recall / reflect, directives, mental models).
`LocalMemory` is an in-process stand-in with the same interface, used when HINDSIGHT_API_KEY is not set
and in tests. It exists so the app degrades gracefully; the product is designed around Hindsight.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Protocol

from app.models.schemas import MemoryItem

log = logging.getLogger(__name__)

def mission_for(company: str) -> str:
    return (f"I am a FinOps assistant for {company}'s engineering team. I explain cloud cost changes using the "
            "team's past history, feedback and deployments.")


MISSION = mission_for("Acme Logistics")
RETAIN_MISSION = ("Extract cloud cost events: which account/service/team, the date and day-of-month, the dollar "
                  "amounts (daily cost, usual baseline, excess), whether engineers judged it expected or waste and "
                  "why, who owns it, and any deploy (id, service, author, time) that caused or explains it.")
DIRECTIVES = [
    ("cite-before-expected", "Never mark a spike as EXPECTED without citing at least one matching past event.", 10),
    ("state-dollar-impact", "Always state the dollar impact.", 8),
    ("flag-unusual-magnitude",
     "If a known recurring event is much larger than its usual range, say EXPECTED_BUT_UNUSUAL.", 9),
    ("deploys-explain-not-excuse",
     "A deploy that provisions resources explains why they exist, not how long they may run. Only call it EXPECTED "
     "if the deploy states a planned duration or teardown and the spend stays inside it; resources that keep "
     "running past that, or non-prod environments left up over a weekend, are NEW_WASTE.", 9),
]
MENTAL_MODEL_ID = "known-recurring-cost-patterns"
MENTAL_MODEL_NAME = "Known recurring cost patterns"
MENTAL_MODEL_QUERY = ("Which cloud cost spikes at this company are known and recurring or planned? For each: "
                      "account, service, team, when it happens (day of month / quarter end), usual daily dollar "
                      "range, owner, and any known waste patterns to watch for.")


class MemoryBackend(Protocol):
    name: str

    def setup_bank(self, reset: bool = False) -> None: ...
    def retain(self, content: str, *, timestamp: datetime, context: str, document_id: str,
               tags: list[str], metadata: dict[str, str]) -> None: ...
    def retain_many(self, items: list[dict[str, Any]]) -> None: ...
    def recall(self, query: str, *, as_of: datetime | None = None, max_tokens: int = 2048) -> list[MemoryItem]: ...
    def reflect(self, query: str, *, context: str, response_schema: dict | None = None
                ) -> tuple[dict | None, str, list[MemoryItem]] | None: ...
    def refresh_mental_model(self) -> None: ...
    def mental_model(self) -> str | None: ...
    def ask(self, question: str) -> str | None: ...


def _items(resp: Any) -> list[Any]:
    return list(getattr(resp, "items", None) or [])


class _SingleThreadClient:
    """Runs every SDK call on one dedicated thread. The sync SDK binds its aiohttp session to the event loop of
    the thread that first uses it, so calls from FastAPI's worker threads and the replay thread must be funnelled
    through the same thread (and therefore the same loop)."""

    def __init__(self, factory):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight")
        self._client = self._pool.submit(factory).result()

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        return lambda *a, **k: self._pool.submit(attr, *a, **k).result()


class HindsightMemory:
    name = "hindsight-cloud"

    def __init__(self, api_key: str, base_url: str, bank_id: str, company: str = "Acme Logistics"):
        from hindsight_client import Hindsight

        self.client = _SingleThreadClient(lambda: Hindsight(base_url=base_url, api_key=api_key, timeout=180.0))
        self.bank_id = bank_id
        self.company = company

    def setup_bank(self, reset: bool = False) -> None:
        c, bank = self.client, self.bank_id
        if reset:
            try:
                c.delete_bank(bank)
            except Exception as e:  # bank may not exist yet
                log.info("delete_bank skipped: %s", e)
        c.create_bank(bank, name=f"{self.company} FinOps", mission=mission_for(self.company), retain_mission=RETAIN_MISSION,
                      disposition_skepticism=5, disposition_literalism=3, disposition_empathy=2)
        existing = {d.name for d in _items(c.list_directives(bank))}
        for name, content, priority in DIRECTIVES:
            if name not in existing:
                c.create_directive(bank, name=name, content=content, priority=priority)
        models = {m.id for m in _items(c.list_mental_models(bank))}
        if MENTAL_MODEL_ID not in models:
            c.create_mental_model(bank, name=MENTAL_MODEL_NAME, source_query=MENTAL_MODEL_QUERY,
                                  id=MENTAL_MODEL_ID, max_tokens=1200)

    def retain(self, content, *, timestamp, context, document_id, tags, metadata) -> None:
        # update_mode="replace": a corrected verdict replaces the stale memory for the same anomaly.
        self.client.retain(self.bank_id, content, timestamp=timestamp, context=context, document_id=document_id,
                           tags=tags, metadata=metadata, update_mode="replace")

    def retain_many(self, items) -> None:
        for i in range(0, len(items), 25):
            self.client.retain_batch(self.bank_id, items[i:i + 25])

    def recall(self, query, *, as_of=None, max_tokens=2048) -> list[MemoryItem]:
        resp = self.client.recall(self.bank_id, query, max_tokens=max_tokens, budget="mid",
                                  query_timestamp=as_of.isoformat() if as_of else None)
        return [MemoryItem(id=r.id, text=r.text, type=r.type, tags=r.tags or [],
                           occurred_at=str(r.occurred_start) if r.occurred_start else None,
                           metadata={k: str(v) for k, v in (r.metadata or {}).items()})
                for r in resp.results or []]

    def reflect(self, query, *, context, response_schema=None):
        resp = self.client.reflect(self.bank_id, query, budget="mid", context=context,
                                   response_schema=response_schema, include_facts=True, apply_all_directives=True)
        facts = []
        based_on = resp.based_on
        for f in (getattr(based_on, "memories", None) or [] if based_on is not None else []):
            facts.append(MemoryItem(id=getattr(f, "id", None), text=getattr(f, "text", str(f)),
                                    type=getattr(f, "type", None)))
        if resp.structured_output_error:
            log.warning("reflect structured_output_error: %s", resp.structured_output_error)
        return resp.structured_output, resp.text or "", facts

    def refresh_mental_model(self) -> None:
        try:
            self.client.refresh_mental_model(self.bank_id, MENTAL_MODEL_ID)
        except Exception as e:
            log.warning("mental model refresh failed: %s", e)

    def mental_model(self) -> str | None:
        try:
            m = self.client.get_mental_model(self.bank_id, MENTAL_MODEL_ID, detail="content")
            return m.content
        except Exception as e:
            log.warning("mental model fetch failed: %s", e)
            return None

    def ask(self, question: str) -> str | None:
        return self.client.reflect(self.bank_id, question, budget="mid").text


_WORD = re.compile(r"[a-z0-9][a-z0-9\-\.]+")
_STOP = {"the", "and", "for", "with", "was", "this", "that", "cost", "spend", "on", "of", "to", "in", "usd", "day"}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP}


class LocalMemory:
    """Keyword-overlap stand-in for Hindsight (no API key / tests). No reflect; the LLM does the reasoning."""

    name = "local-fallback"

    def __init__(self):
        self.docs: dict[str, dict[str, Any]] = {}

    def setup_bank(self, reset: bool = False) -> None:
        if reset:
            self.docs.clear()

    def retain(self, content, *, timestamp, context, document_id, tags, metadata) -> None:
        self.docs[document_id] = dict(content=content, timestamp=timestamp, context=context, tags=tags,
                                      metadata=metadata)

    def retain_many(self, items) -> None:
        for it in items:
            self.retain(it["content"], timestamp=it["timestamp"], context=it.get("context", ""),
                        document_id=it["document_id"], tags=it.get("tags", []), metadata=it.get("metadata", {}))

    def recall(self, query, *, as_of=None, max_tokens=2048) -> list[MemoryItem]:
        q = _tokens(query)
        scored = []
        for doc_id, d in self.docs.items():
            ts = d["timestamp"]
            if as_of and ts > as_of:
                continue
            score = len(q & _tokens(d["content"] + " " + " ".join(d["tags"])))
            if score >= 3:
                scored.append((score, doc_id, d))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [MemoryItem(id=doc_id, text=d["content"], occurred_at=d["timestamp"].isoformat(), type="world",
                           tags=d["tags"], metadata=d["metadata"]) for _, doc_id, d in scored[:10]]

    def reflect(self, query, *, context, response_schema=None):
        return None

    def refresh_mental_model(self) -> None:
        pass

    def mental_model(self) -> str | None:
        lines = []
        for d in sorted(self.docs.values(), key=lambda d: d["timestamp"]):
            m = d["metadata"]
            if m.get("kind") == "feedback" and m.get("response") == "expected":
                lines.append(f"- {m.get('account')} / {m.get('service')} / {m.get('team_tag')}: "
                             f"{m.get('pattern', 'expected spike')} (last seen {m.get('start_date')}, "
                             f"~${float(m.get('delta_usd', 0)):,.0f}/day above baseline)")
        return "\n".join(dict.fromkeys(lines)) or None

    def ask(self, question: str) -> str | None:
        return None


def build_memory(api_key: str, base_url: str, bank_id: str, company: str = "Acme Logistics") -> MemoryBackend:
    if api_key:
        return HindsightMemory(api_key, base_url or "https://api.hindsight.vectorize.io", bank_id, company)
    log.warning("HINDSIGHT_API_KEY not set: using LocalMemory fallback")
    return LocalMemory()


def utc(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
