from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class VerdictLabel(str, Enum):
    EXPECTED = "EXPECTED"
    NEW_WASTE = "NEW_WASTE"
    EXPECTED_BUT_UNUSUAL = "EXPECTED_BUT_UNUSUAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN = "UNKNOWN"  # cold start: nobody has classified anything like this yet; set by code, never by the LLM


class FeedbackResponse(str, Enum):
    expected = "expected"
    waste = "waste"
    investigating = "investigating"


class LineItem(BaseModel):
    usage_type: str
    resources: int
    cost_usd: float


class Anomaly(BaseModel):
    id: str
    account: str
    service: str
    team_tag: str
    start_date: str
    end_date: str
    peak_date: str
    peak_cost_usd: float
    baseline_usd: float
    delta_usd: float  # peak day cost above baseline
    z_score: float
    total_excess_usd: float
    days: int
    top_line_items: list[LineItem] = []

    @property
    def ratio(self) -> float:
        return self.peak_cost_usd / self.baseline_usd if self.baseline_usd > 0 else float("inf")

    @property
    def month(self) -> str:
        return self.start_date[:7]


class CitedEvent(BaseModel):
    date: str | None = None
    summary: str


class Verdict(BaseModel):
    """Structured verdict. Every LLM / reflect output is validated against this model."""

    verdict: VerdictLabel
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1)
    cited_past_events: list[CitedEvent] = []
    suggested_owner: str | None = None
    estimated_daily_impact_usd: float
    magnitude_vs_usual: str | None = None
    usual_excess_usd: float | None = None  # typical $/day excess of past occurrences of the same event (from memory)

    @field_validator("verdict", mode="before")
    @classmethod
    def _norm_verdict(cls, v: Any) -> Any:
        return v.strip().upper().replace(" ", "_") if isinstance(v, str) else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _norm_confidence(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = float(v.strip().rstrip("%"))
        return v / 100 if isinstance(v, (int, float)) and 1 < v <= 100 else v

    @field_validator("estimated_daily_impact_usd", "usual_excess_usd", mode="before")
    @classmethod
    def _norm_usd(cls, v: Any) -> Any:
        if isinstance(v, str):
            if not v.strip() or v.strip().lower() in ("null", "none", "n/a"):
                return None
            return float(v.replace("$", "").replace(",", "").strip())
        return v

    @field_validator("cited_past_events", mode="before")
    @classmethod
    def _norm_cited(cls, v: Any) -> Any:
        if v is None:
            return []
        return [{"summary": x} if isinstance(x, str) else x for x in v]


# JSON schema handed to Hindsight reflect (flat, no $refs) and described to the LLM.
VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["EXPECTED", "NEW_WASTE", "EXPECTED_BUT_UNUSUAL", "NEEDS_REVIEW"]},
        "confidence": {"type": "number", "description": "0.0-1.0"},
        "explanation": {"type": "string"},
        "cited_past_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"date": {"type": "string"}, "summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
        "suggested_owner": {"type": "string"},
        "estimated_daily_impact_usd": {"type": "number"},
        "magnitude_vs_usual": {"type": "string"},
        # Plain "number": Hindsight reflect returns HTTP 500 for union types like ["number", "null"].
        "usual_excess_usd": {
            "type": "number",
            "description": "Typical excess $/day of PAST occurrences of this same recurring event, taken from memory "
                           "(not the daily baseline). 0 if there is no past occurrence.",
        },
    },
    "required": ["verdict", "confidence", "explanation", "cited_past_events", "estimated_daily_impact_usd"],
}


class MemoryItem(BaseModel):
    id: str | None = None
    text: str
    occurred_at: str | None = None
    type: str | None = None
    tags: list[str] = []
    metadata: dict[str, str] = {}


class VerdictRecord(Verdict):
    """A verdict as stored/served: the validated verdict plus how it was produced."""

    mode: str  # "memory" | "no_memory"
    engine: str  # e.g. "hindsight-reflect", "groq:openai/gpt-oss-120b", "offline-heuristic", "fallback"
    cold_start: bool = False
    recalled: list[MemoryItem] = []
    guardrail_notes: list[str] = []


class FeedbackIn(BaseModel):
    response: FeedbackResponse
    note: str = ""
    author: str = "you"


class ChatIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
