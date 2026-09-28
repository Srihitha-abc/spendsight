"""Parse and validate verdict JSON, and apply the bank directives as hard guardrails in code."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.models.schemas import Anomaly, Verdict, VerdictLabel

UNUSUAL_RATIO = 1.6  # excess vs the usual excess of the same recurring event
_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class VerdictParseError(ValueError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of an LLM reply (handles <think> blocks, code fences, leading prose)."""
    text = _THINK.sub("", text or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VerdictParseError("no JSON object in reply")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise VerdictParseError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise VerdictParseError("JSON is not an object")
    return obj


def parse_verdict(raw: str | dict[str, Any]) -> Verdict:
    obj = raw if isinstance(raw, dict) else extract_json(raw)
    try:
        return Verdict.model_validate(obj)
    except ValidationError as e:
        raise VerdictParseError(str(e)) from e


def needs_review(anomaly: Anomaly, reason: str) -> Verdict:
    return Verdict(verdict=VerdictLabel.NEEDS_REVIEW, confidence=0.0,
                   explanation=f"Automatic analysis failed ({reason}). A human needs to review this "
                               f"${anomaly.delta_usd:,.0f}/day spike.",
                   estimated_daily_impact_usd=anomaly.delta_usd)


def apply_guardrails(v: Verdict, anomaly: Anomaly) -> tuple[Verdict, list[str]]:
    """Enforce the directives deterministically, whatever the model said."""
    notes: list[str] = []
    if v.verdict == VerdictLabel.EXPECTED and not v.cited_past_events:
        v = v.model_copy(update=dict(verdict=VerdictLabel.NEEDS_REVIEW, confidence=min(v.confidence, 0.4)))
        notes.append("Downgraded EXPECTED -> NEEDS_REVIEW: no matching past event was cited.")
    # The model recalls the usual size of the event; code does the arithmetic and picks the matching label.
    known = v.verdict in (VerdictLabel.EXPECTED, VerdictLabel.EXPECTED_BUT_UNUSUAL)
    if known and v.cited_past_events and v.usual_excess_usd and v.usual_excess_usd > 0:
        r = anomaly.delta_usd / v.usual_excess_usd
        label = VerdictLabel.EXPECTED_BUT_UNUSUAL if r >= UNUSUAL_RATIO else VerdictLabel.EXPECTED
        if label != v.verdict:
            notes.append(f"{v.verdict.value} -> {label.value}: ${anomaly.delta_usd:,.0f}/day is {r:.2f}x the usual "
                         f"${v.usual_excess_usd:,.0f}/day (threshold {UNUSUAL_RATIO}x).")
        v = v.model_copy(update=dict(verdict=label,
                                     magnitude_vs_usual=f"{r:.1f}x the usual ~${v.usual_excess_usd:,.0f}/day"))
    if v.estimated_daily_impact_usd <= 0:
        v = v.model_copy(update=dict(estimated_daily_impact_usd=anomaly.delta_usd))
        notes.append("Dollar impact missing; filled from detector.")
    return v, notes
