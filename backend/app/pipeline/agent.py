"""Verdict pipeline: recall -> reflect (or LLM over recalled memories) -> validate -> guardrails.

Memory ON : Hindsight recall + reflect (bank mission, directives, disposition, mental model).
            Falls back to Groq with the recalled memories as context, then to an offline heuristic.
Memory OFF: the same LLM and the same guardrails, but only the anomaly's own numbers. No history.
Any failure ends in NEEDS_REVIEW; the pipeline never raises.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from app.llm.groq_client import GroqLLM
from app.llm.verdict import UNUSUAL_RATIO, VerdictParseError, apply_guardrails, needs_review, parse_verdict
from app.memory.hindsight_client import DIRECTIVES, MemoryBackend, mission_for
from app.models.schemas import VERDICT_JSON_SCHEMA, Anomaly, MemoryItem, Verdict, VerdictLabel, VerdictRecord
from app.pipeline.narrative import correlate_deploys, describe, recall_query, short_service

log = logging.getLogger(__name__)

RULES = """Decide one verdict:
- EXPECTED: matches a known recurring or planned event. You MUST cite at least one matching past event
  (a past anomaly with the same service/team and timing that engineers confirmed as expected, or a deploy/announcement
  that planned it, e.g. a load test). Same team is not enough: timing and resource type must match.
  A deploy that provisions resources explains why they exist, not how long they may run: it only justifies
  EXPECTED if it states a planned duration/teardown and the spend stays inside that window. Resources still
  running afterwards, or a non-prod environment left up over a weekend, are NEW_WASTE.
- EXPECTED_BUT_UNUSUAL: a known recurring event whose magnitude is far outside its usual range (roughly >1.6x the
  usual excess). Explain the likely reason (e.g. a related deploy) and state the ratio.
- NEW_WASTE: no precedent, or it looks like idle/forgotten resources or a bug (e.g. spike right after a deploy).
- NEEDS_REVIEW: not enough information to decide.
usual_excess_usd: for a recurring event, the typical excess $/day of its PAST occurrences as recorded in memory.
Compare like with like: recurring jobs are always many times the daily baseline, so the baseline ratio (z, "15x")
does NOT make an event unusual. Deploys listed near the spike may be routine and unrelated; only treat one as
the cause if its change plausibly drives this service's cost.
Always state the dollar impact. Be skeptical. If you have no relevant history, say so plainly (cold start).
estimated_daily_impact_usd = extra dollars per day above baseline. magnitude_vs_usual: e.g. "2.3x the usual
retraining cost" or "14x baseline". suggested_owner: a person or team seen in the history (or the team tag).
Reply with ONLY a JSON object with keys: verdict, confidence (0-1), explanation (2-4 sentences),
cited_past_events (list of {date, summary}), suggested_owner, estimated_daily_impact_usd, magnitude_vs_usual,
usual_excess_usd (number, 0 if no past occurrence)."""

SYSTEM_MEMORY = (mission_for("the company") + " Directives: " + " ".join(d[1] for d in DIRECTIVES) +
                 " Disposition: highly skeptical; never assume a spike is fine without evidence.\n" + RULES)
SYSTEM_NO_MEMORY = ("You are a FinOps assistant analysing a single cloud cost anomaly. You have NO access to any "
                    "history, past incidents, feedback or deploy logs; judge only from the numbers given. "
                    "Directives: " + " ".join(d[1] for d in DIRECTIVES) + "\n" + RULES)


def as_of(a: Anomaly) -> datetime:
    """Replay clock: what the agent knows when it looks at this anomaly (end of its first day)."""
    return datetime.combine(date.fromisoformat(a.start_date), time(23, 59), tzinfo=timezone.utc)


def is_cold_start(a: Anomaly, recalled: list[MemoryItem]) -> bool:
    svc, team = short_service(a.service).lower(), a.team_tag.lower()
    return not any(svc in m.text.lower() and team in m.text.lower() for m in recalled)


class Agent:
    def __init__(self, memory: MemoryBackend, llm: GroqLLM | None, deploys: pd.DataFrame, anchor_time: bool = False):
        """anchor_time: recall only what was known on the anomaly's date. On for replay/backtests, off for live
        data, where feedback given today about an old spike is valid knowledge for later ones."""
        self.memory, self.llm, self.deploys, self.anchor_time = memory, llm, deploys, anchor_time

    # ------------------------------------------------------------------ public
    def analyze(self, a: Anomaly, mode: str, history: int | None = None) -> VerdictRecord:
        """history = how many earlier anomalies in the same (account, service, team) engineers have classified.
        0 means cold start. None falls back to guessing from the recalled text."""
        try:
            if mode == "memory":
                return self._with_memory(a, history)
            return self._without_memory(a)
        except Exception as e:  # never crash the pipeline
            log.exception("analysis failed for %s (%s)", a.id, mode)
            return VerdictRecord(**needs_review(a, type(e).__name__).model_dump(), mode=mode, engine="fallback")

    # ------------------------------------------------------------------ memory ON
    def _with_memory(self, a: Anomaly, history: int | None) -> VerdictRecord:
        recalled = self.memory.recall(recall_query(a), as_of=as_of(a) if self.anchor_time else None)
        cold = history == 0 if history is not None else is_cold_start(a, recalled)
        deploys = correlate_deploys(a, self.deploys)
        context = describe(a)
        if deploys:
            context += " Deploys by this team (or planned tests) in the 2 days before, possibly unrelated: " + "; ".join(
                f"#{d['deploy_id']} {d['service']} by {d['author']} ({d['relation']}): {d['summary']}" for d in deploys)
        if cold:
            context += (" NOTE: engineers have never classified an anomaly for this account/service/team "
                        "(cold start).")

        verdict, engine = None, ""
        reflected = None
        try:
            reflected = self.memory.reflect(
                "Classify this cloud cost anomaly using the team's past anomalies, engineer feedback and "
                "deploys. " + context + "\n" + RULES, context=context, response_schema=VERDICT_JSON_SCHEMA)
        except Exception as e:
            log.warning("reflect failed: %s", e)
        if reflected is not None:
            structured, text, _facts = reflected
            for candidate in (structured, text):
                if not candidate:
                    continue
                try:
                    verdict, engine = parse_verdict(candidate), "hindsight-reflect"
                    break
                except VerdictParseError as e:
                    log.warning("reflect output invalid: %s", e)

        if verdict is None and self.llm:
            memories = "\n".join(f"- [{m.occurred_at or '?'}] {m.text}" for m in recalled[:12]) or "(none)"
            user = f"ANOMALY:\n{context}\n\nRECALLED MEMORIES (from Hindsight):\n{memories}"
            verdict, engine = self._llm_verdict(SYSTEM_MEMORY, user, a)
        if verdict is None and not self.llm:
            verdict, engine = heuristic(a, recalled, deploys, self._recent_team_deploys(a), cold), "offline-heuristic"
        if verdict is None:
            verdict, engine = needs_review(a, "invalid model output after retry"), "fallback"
        verdict, notes = apply_guardrails(verdict, a)
        verdict, notes = self._deploy_is_not_an_excuse(verdict, notes)
        if cold:
            verdict, notes = cold_start_rule(verdict, a, notes)
        return VerdictRecord(**(verdict.model_dump() | {"suggested_owner": owner_or_none(verdict.suggested_owner)}), mode="memory", engine=engine, cold_start=cold,
                             recalled=recalled[:8], guardrail_notes=notes)

    # ------------------------------------------------------------------ memory OFF
    def _without_memory(self, a: Anomaly) -> VerdictRecord:
        if self.llm:
            verdict, engine = self._llm_verdict(SYSTEM_NO_MEMORY, f"ANOMALY:\n{describe(a)}", a)
        else:
            verdict, engine = heuristic(a, [], [], [], True, with_memory=False), "offline-heuristic"
        if verdict is None:
            verdict, engine = needs_review(a, "invalid model output after retry"), "fallback"
        verdict, notes = apply_guardrails(verdict, a)
        return VerdictRecord(**(verdict.model_dump() | {"suggested_owner": owner_or_none(verdict.suggested_owner)}), mode="no_memory", engine=engine, cold_start=True,
                             guardrail_notes=notes)

    # ------------------------------------------------------------------ helpers
    def _llm_verdict(self, system: str, user: str, a: Anomaly) -> tuple[Verdict | None, str]:
        """One try + one retry with the validation error fed back. Returns (None, engine) if both fail."""
        engine = self.llm.name
        prompt = user
        for attempt in range(2):
            try:
                text, model = self.llm.complete(system, prompt)
                engine = f"groq:{model}"
                return parse_verdict(text), engine
            except VerdictParseError as e:
                log.warning("verdict parse failed (attempt %d): %s", attempt + 1, e)
                prompt = f"{user}\n\nYour previous reply was invalid ({str(e)[:300]}). Reply with ONLY the JSON object."
            except Exception as e:
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
        return None, engine

    def _deploy_is_not_an_excuse(self, v: Verdict, notes: list[str]) -> tuple[Verdict, list[str]]:
        """Code version of the 'deploys-explain-not-excuse' directive. When a deploy is the only evidence, a deploy
        that merely created the resources doesn't make the spend EXPECTED; it has to state a schedule or teardown."""
        if v.verdict != VerdictLabel.EXPECTED or not v.cited_past_events:
            return v, notes
        if not all("deploy" in c.summary.lower() for c in v.cited_past_events):
            return v, notes  # also backed by a past anomaly/feedback memory
        ids = {int(x) for c in v.cited_past_events for x in re.findall(r"#\s?(\d+)", c.summary)}
        cited = self.deploys[self.deploys["deploy_id"].astype(int).isin(ids)] if ids and not self.deploys.empty else None
        text = " ".join(cited["summary"]).lower() if cited is not None else " ".join(c.summary.lower() for c in v.cited_past_events)
        if _BOUNDED.search(text):
            return v, notes
        v = v.model_copy(update=dict(verdict=VerdictLabel.NEEDS_REVIEW, confidence=min(v.confidence, 0.5)))
        return v, [*notes, "EXPECTED -> NEEDS_REVIEW: the cited deploy explains why these resources exist, but states "
                           "no schedule or teardown, so on its own it can't justify the spend."]

    def _recent_team_deploys(self, a: Anomaly) -> list[dict]:
        if self.deploys.empty:
            return []
        lo = str(date.fromisoformat(a.start_date) - timedelta(days=10))
        d = self.deploys[(self.deploys["date"] >= lo) & (self.deploys["date"] <= a.start_date)
                         & (self.deploys["team"] == a.team_tag)]
        return d.to_dict("records")


def owner_or_none(owner: str | None) -> str | None:
    """Placeholder tags aren't owners."""
    return None if not owner or owner.strip().lower() in ("untagged", "unknown", "default", "none") else owner


def cold_start_rule(v: Verdict, a: Anomaly, notes: list[str]) -> tuple[Verdict, list[str]]:
    """Nobody has classified anything like this yet: don't guess. The only verdicts allowed without history are
    ones backed by a concrete deploy (a bug it shipped, or a test it announced with a teardown)."""
    backed_by_deploy = any("deploy" in c.summary.lower() for c in v.cited_past_events)
    if v.verdict in (VerdictLabel.NEW_WASTE, VerdictLabel.EXPECTED) and backed_by_deploy:
        return v, notes
    ratio = f"{a.ratio:.1f}x baseline" if a.baseline_usd else "new spend"
    first_read = "" if v.verdict in (VerdictLabel.NEEDS_REVIEW, VerdictLabel.UNKNOWN) else \
        f" Without history the model's first read would be {v.verdict.value}, which isn't trustworthy yet."
    svc = short_service(a.service)
    scope = (f" for {a.team_tag}" if a.team_tag != "untagged" else "") + \
            (f" in {a.account}" if a.account != "default" else "")
    return v.model_copy(update=dict(
        verdict=VerdictLabel.UNKNOWN, confidence=0.0, cited_past_events=[], suggested_owner=owner_or_none(a.team_tag),
        explanation=(f"Unknown: no history yet, please classify. Nobody has labelled a{'n' if svc[:1].lower() in 'aeiou' else ''} "
                     f"{svc} spike{scope} before. It's +${a.delta_usd:,.0f}/day ({ratio}), "
                     f"${a.total_excess_usd:,.0f} so far.{first_read} Your answer becomes the first memory for "
                     "this pattern."))), [*notes, "Cold start: verdict withheld until someone classifies this pattern."]


# ---------------------------------------------------------------------- offline heuristic
_BOUNDED = re.compile(r"tear.?down|auto-?stop|ttl|expires?|until \d|one-off|\b\d+ ?h\b|\bmonthly\b|\bweekly\b|"
                      r"\bnightly\b|\bdaily\b|quarter")
_PROVISIONS = re.compile(r"\d+x [a-z0-9]+\.\d*x?large|environment|presigned")


def _same_timing(dom: int, past_dom: int) -> bool:
    return abs(dom - past_dom) <= 1 or (dom >= 28 and past_dom >= 28)


def heuristic(a: Anomaly, recalled: list[MemoryItem], near_deploys: list[dict], team_deploys: list[dict],
              cold: bool, with_memory: bool = True) -> Verdict:
    """Deterministic reasoning over structured memory metadata. Used only when no LLM key is configured."""
    ratio = f"{a.ratio:.1f}x baseline" if a.baseline_usd else "new spend"
    impact = a.delta_usd
    if not with_memory or (cold and not near_deploys):
        return Verdict(verdict=VerdictLabel.NEEDS_REVIEW, confidence=0.3, estimated_daily_impact_usd=impact,
                       magnitude_vs_usual=ratio, suggested_owner=a.team_tag,
                       explanation=(f"{'Cold start - no' if with_memory else 'No'} history for this pattern. "
                                    f"{a.service} for {a.team_tag} is +${impact:,.0f}/day ({ratio}); "
                                    "can't tell if this is planned without context."))
    dom = date.fromisoformat(a.start_date).day
    group = [m for m in recalled if m.metadata.get("kind") == "feedback"
             and (m.metadata.get("account"), m.metadata.get("service"), m.metadata.get("team_tag"))
             == (a.account, a.service, a.team_tag)]
    timed = [m for m in group if _same_timing(dom, int(m.metadata.get("day_of_month", 0)))]
    expected = [m for m in timed if m.metadata.get("response") == "expected"]
    cite = lambda ms: [{"date": m.metadata.get("start_date"), "summary": (
        f"{m.metadata.get('service')} spike +${float(m.metadata.get('delta_usd', 0)):,.0f}/day marked "
        f"{m.metadata.get('response')} by {m.metadata.get('owner')}: {m.metadata.get('pattern', '')}")} for m in ms[:3]]

    if expected:
        usual = statistics.median(float(m.metadata["delta_usd"]) for m in expected)
        r = a.delta_usd / usual if usual else 1.0
        owner = expected[-1].metadata.get("owner")
        if r >= UNUSUAL_RATIO:
            why = next((d for d in team_deploys if any(k in d["summary"].lower() for k in ("training", "backfill"))), None)
            reason = f" Likely cause: deploy #{why['deploy_id']} on {why['date']} ({why['summary']})." if why else ""
            return Verdict(verdict=VerdictLabel.EXPECTED_BUT_UNUSUAL, confidence=0.75, cited_past_events=cite(expected), usual_excess_usd=usual,
                           suggested_owner=owner, estimated_daily_impact_usd=impact,
                           magnitude_vs_usual=f"{r:.1f}x the usual ~${usual:,.0f}/day",
                           explanation=(f"Matches a known recurring event ({len(expected)} past occurrence(s)) but "
                                        f"costs +${impact:,.0f}/day, {r:.1f}x its usual ~${usual:,.0f}.{reason}"))
        return Verdict(verdict=VerdictLabel.EXPECTED, confidence=0.85, cited_past_events=cite(expected), usual_excess_usd=usual,
                       suggested_owner=owner, estimated_daily_impact_usd=impact,
                       magnitude_vs_usual=f"{r:.1f}x the usual ~${usual:,.0f}/day",
                       explanation=(f"Same service, team and timing as {len(expected)} past event(s) engineers "
                                    f"confirmed as expected. +${impact:,.0f}/day is within the usual range."))
    planned = [d for d in near_deploys if "load-test" in d["summary"].lower() or "load test" in d["summary"].lower()]
    if planned:
        d = planned[0]
        return Verdict(verdict=VerdictLabel.EXPECTED, confidence=0.7, suggested_owner=d["author"],
                       cited_past_events=[{"date": d["date"], "summary": f"Deploy #{d['deploy_id']}: {d['summary']}"}],
                       estimated_daily_impact_usd=impact, magnitude_vs_usual=ratio,
                       explanation=f"Planned one-off: deploy #{d['deploy_id']} announced it. +${impact:,.0f}/day.")
    culprits = [d for d in near_deploys
                if d["date"] == a.start_date or _PROVISIONS.search(d["summary"].lower())]
    if cold and not culprits:
        leads = ", ".join(f"#{d['deploy_id']}" for d in near_deploys)
        return Verdict(verdict=VerdictLabel.NEEDS_REVIEW, confidence=0.3, estimated_daily_impact_usd=impact,
                       magnitude_vs_usual=ratio, suggested_owner=a.team_tag,
                       explanation=(f"Cold start - no history for this pattern. {a.service} for {a.team_tag} is "
                                    f"+${impact:,.0f}/day ({ratio}). Nearby deploys ({leads}) don't obviously "
                                    "explain it; needs a human."))
    if culprits:
        d = culprits[0]
        return Verdict(verdict=VerdictLabel.NEW_WASTE, confidence=0.7, suggested_owner=d["author"],
                       cited_past_events=[{"date": d["date"], "summary": f"Deploy #{d['deploy_id']}: {d['summary']}"}],
                       estimated_daily_impact_usd=impact, magnitude_vs_usual=ratio,
                       explanation=(f"No precedent for this spike; it lines up with deploy #{d['deploy_id']} "
                                    f"({d['relation']}). Costing +${impact:,.0f}/day ({ratio})."))
    known = ", ".join(sorted({f"day {m.metadata.get('day_of_month')}" for m in group}))
    return Verdict(verdict=VerdictLabel.NEW_WASTE, confidence=0.65, suggested_owner=a.team_tag,
                   estimated_daily_impact_usd=impact, magnitude_vs_usual=ratio,
                   explanation=(f"No precedent: this does not match the known patterns for {a.team_tag} "
                                f"{short_service(a.service)} ({known or 'none'}) and no deploy explains it. "
                                f"+${impact:,.0f}/day ({ratio}), ${a.total_excess_usd:,.0f} so far."))
