import pandas as pd
import pytest

from app.llm.verdict import VerdictParseError, apply_guardrails, parse_verdict
from app.memory.hindsight_client import LocalMemory
from app.models.schemas import Anomaly, CitedEvent, VerdictLabel
from app.pipeline.agent import Agent

ANOMALY = Anomaly(id="acme-ml-platform|AmazonEC2|ml-research|2026-06-01", account="acme-ml-platform",
                  service="AmazonEC2", team_tag="ml-research", start_date="2026-06-01", end_date="2026-06-01",
                  peak_date="2026-06-01", peak_cost_usd=2384, baseline_usd=159, delta_usd=2225, z_score=169.5,
                  total_excess_usd=2225, days=1)

GOOD = ('{"verdict": "EXPECTED", "confidence": 0.9, "explanation": "Monthly retrain.", '
        '"cited_past_events": [{"date": "2026-05-01", "summary": "May retrain marked expected"}], '
        '"suggested_owner": "Priya Raman", "estimated_daily_impact_usd": 2225, "magnitude_vs_usual": "1.0x"}')


def test_parses_clean_json():
    v = parse_verdict(GOOD)
    assert v.verdict == VerdictLabel.EXPECTED and v.cited_past_events[0].date == "2026-05-01"


def test_parses_json_wrapped_in_think_and_fences():
    v = parse_verdict(f"<think>hmm, 1st of month...</think>\nSure:\n```json\n{GOOD}\n```")
    assert v.suggested_owner == "Priya Raman"


def test_normalises_loose_llm_output():
    v = parse_verdict({"verdict": "expected but unusual", "confidence": "85%", "explanation": "x",
                       "cited_past_events": ["May retrain"], "estimated_daily_impact_usd": "$5,146"})
    assert v.verdict == VerdictLabel.EXPECTED_BUT_UNUSUAL
    assert v.confidence == pytest.approx(0.85)
    assert v.estimated_daily_impact_usd == 5146
    assert v.cited_past_events[0].summary == "May retrain"


@pytest.mark.parametrize("bad", ["", "no json here", "{not json}", '{"verdict": "MAYBE", "confidence": 0.5, '
                                 '"explanation": "x", "estimated_daily_impact_usd": 1}', "[1, 2]"])
def test_rejects_invalid_output(bad):
    with pytest.raises(VerdictParseError):
        parse_verdict(bad)


def test_guardrail_downgrades_uncited_expected():
    v = parse_verdict(GOOD).model_copy(update={"cited_past_events": []})
    v2, notes = apply_guardrails(v, ANOMALY)
    assert v2.verdict == VerdictLabel.NEEDS_REVIEW and notes


@pytest.mark.parametrize("said, usual, expected", [
    ("EXPECTED_BUT_UNUSUAL", 2165, VerdictLabel.EXPECTED),       # 1.03x usual: the model over-called it
    ("EXPECTED", 2225 / 2.3, VerdictLabel.EXPECTED_BUT_UNUSUAL),  # 2.3x usual
    ("EXPECTED", None, VerdictLabel.EXPECTED),                    # no usual size recalled: keep the model's label
])
def test_code_decides_unusual_from_usual_excess(said, usual, expected):
    v = parse_verdict(GOOD).model_copy(update={"verdict": VerdictLabel(said), "usual_excess_usd": usual})
    assert apply_guardrails(v, ANOMALY)[0].verdict == expected


def test_calendar_flags_quarter_end():
    from datetime import date
    from app.pipeline.narrative import calendar
    assert "quarter end" in calendar(date(2026, 9, 29)) and "Q3" in calendar(date(2026, 9, 29))
    assert calendar(date(2026, 9, 17)) == ""
    assert "quarter" not in calendar(date(2026, 8, 30))


DEPLOYS = pd.DataFrame([
    dict(date="2026-07-13", time_utc="11:20", service="platform-infra", team="platform", deploy_id=399, author="K",
         summary="v1.17.1: k6 load-test harness (60x c6i.4xlarge, auto-teardown 20:00 UTC 14 Jul) CAB-2291"),
    dict(date="2026-08-07", time_utc="13:10", service="cart-service", team="checkout", deploy_id=449, author="R",
         summary="v1.9.4: Deploy cart-v3 perf environment to staging (16x m6i.4xlarge) for load profiling"),
])


@pytest.mark.parametrize("cited, expected", [
    (["Deploy #449 provisioned it"], VerdictLabel.NEEDS_REVIEW),  # created the env, says nothing about teardown
    (["Deploy #399 provisioned it"], VerdictLabel.EXPECTED),      # announced its own teardown
    (["Deploy #449 provisioned it", "2026-05-08 same perf env marked expected by Rohan"], VerdictLabel.EXPECTED),
])
def test_deploy_explains_but_does_not_excuse(cited, expected):
    ag = Agent(LocalMemory(), None, DEPLOYS)
    v = parse_verdict(GOOD).model_copy(update={"cited_past_events": [CitedEvent(summary=s) for s in cited]})
    assert ag._deploy_is_not_an_excuse(v, [])[0].verdict == expected


def test_cold_start_says_unknown_unless_a_deploy_backs_it():
    from app.pipeline.agent import cold_start_rule
    guess = parse_verdict(GOOD)  # EXPECTED citing a past-event memory
    v, notes = cold_start_rule(guess, ANOMALY, [])
    assert v.verdict == VerdictLabel.UNKNOWN and v.explanation.startswith("Unknown: no history yet, please classify")
    bug = guess.model_copy(update={"verdict": VerdictLabel.NEW_WASTE,
                                   "cited_past_events": [CitedEvent(summary="Deploy #421 switched to presigned URLs")]})
    assert cold_start_rule(bug, ANOMALY, [])[0].verdict == VerdictLabel.NEW_WASTE


class FlakyLLM:
    name = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user, json_mode=True):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, "fake-model"


def agent(llm):
    return Agent(LocalMemory(), llm, pd.DataFrame(columns=["date", "time_utc", "service", "team", "deploy_id",
                                                           "author", "summary"]))


def test_retry_once_then_succeed():
    llm = FlakyLLM(["garbage", GOOD])
    rec = agent(llm).analyze(ANOMALY, "no_memory")
    assert llm.calls == 2
    assert rec.verdict == VerdictLabel.EXPECTED  # cites an event, so guardrail keeps it


def test_two_failures_fall_back_to_needs_review():
    llm = FlakyLLM(["garbage", RuntimeError("429 rate limited")])
    rec = agent(llm).analyze(ANOMALY, "no_memory")
    assert rec.verdict == VerdictLabel.NEEDS_REVIEW and rec.engine == "fallback"
    # With memory ON and nothing ever classified, it says UNKNOWN instead of guessing.
    rec = agent(FlakyLLM(["garbage", "garbage"])).analyze(ANOMALY, "memory", history=0)
    assert rec.cold_start and rec.verdict == VerdictLabel.UNKNOWN


def test_memory_errors_never_crash_pipeline():
    class Broken(LocalMemory):
        def recall(self, *a, **k):
            raise ConnectionError("hindsight down")

    rec = Agent(Broken(), None, pd.DataFrame()).analyze(ANOMALY, "memory")
    assert rec.verdict == VerdictLabel.NEEDS_REVIEW
