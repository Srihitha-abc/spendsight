"""Turn anomalies, deploys and feedback into the natural-language text we retain and query with."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app.models.schemas import Anomaly


def short_service(service: str) -> str:
    for p in ("Amazon", "AWS"):
        if service.startswith(p) and len(service) > len(p):
            return service[len(p):]
    return service


def _d(s: str) -> date:
    return date.fromisoformat(s)


def calendar(day: date) -> str:
    """Calendar facts FinOps cares about: month end and quarter end."""
    month_end = (date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1))
    left = (month_end - day).days
    if left > 3:
        return ""
    when = "the last day" if left == 0 else f"{left} day{'s' if left > 1 else ''} before the end"
    quarter = f" and of Q{(day.month - 1) // 3 + 1} (quarter end)" if day.month % 3 == 0 else ""
    return f" Calendar: {day} is {when} of the month{quarter}."


def describe(a: Anomaly) -> str:
    start = _d(a.start_date)
    span = f"from {a.start_date} to {a.end_date} ({a.days} days)" if a.days > 1 else f"on {a.start_date}"
    items = "; ".join(f"{li.usage_type} x{li.resources} resources ${li.cost_usd:,.0f}" for li in a.top_line_items)
    ratio = f"{a.ratio:.1f}x" if a.baseline_usd > 0 else "new spend"
    return (f"Cost anomaly in AWS account {a.account}, service {a.service} ({short_service(a.service)}), "
            f"team {a.team_tag}, {span}. Started on a {start:%A}, day {start.day} of the month.{calendar(start)} "
            f"Peak daily cost ${a.peak_cost_usd:,.0f} on {a.peak_date} vs a 14-day baseline of "
            f"${a.baseline_usd:,.0f}/day (+${a.delta_usd:,.0f}/day, {ratio}, z={a.z_score}). "
            f"Total excess so far ${a.total_excess_usd:,.0f}. Top line items on the peak day: {items}.")


def recall_query(a: Anomaly) -> str:
    start = _d(a.start_date)
    items = ", ".join(li.usage_type for li in a.top_line_items)
    return (f"{short_service(a.service)} {a.service} cost spike for team {a.team_tag} in {a.account} on "
            f"{start:%A} {a.start_date} (day {start.day} of the month): +${a.delta_usd:,.0f}/day over the usual "
            f"${a.baseline_usd:,.0f}/day. Line items: {items}. Similar past anomalies, engineer feedback, recurring "
            f"jobs, and recent deploys by {a.team_tag}.")


def correlate_deploys(a: Anomaly, deploys: pd.DataFrame) -> list[dict]:
    """Deploys in the 2 days up to the spike start from the same team, or explicitly planned load/perf tests."""
    if deploys.empty:
        return []
    start = _d(a.start_date)
    lo = str(start - timedelta(days=2))
    near = deploys[(deploys["date"] >= lo) & (deploys["date"] <= a.start_date)]
    hits = []
    for r in near.itertuples():
        text = r.summary.lower()
        if r.team == a.team_tag or "load-test" in text or "load test" in text:
            hits.append(dict(deploy_id=int(r.deploy_id), date=r.date, time_utc=r.time_utc, service=r.service,
                             team=r.team, author=r.author, summary=r.summary,
                             relation=_relation(r.date, r.time_utc, a.start_date)))
    return hits


def _relation(d: str, t: str, spike_start: str) -> str:
    if d == spike_start:
        return f"deployed at {t} UTC on the day the spike started"
    days = (_d(spike_start) - _d(d)).days
    return f"deployed {days} day{'s' if days > 1 else ''} before the spike started"


def deploy_memory(r) -> dict:
    ts = datetime.fromisoformat(f"{r.date}T{r.time_utc}:00").replace(tzinfo=timezone.utc)
    return dict(
        content=(f"Deploy #{r.deploy_id} on {r.date} at {r.time_utc} UTC: service {r.service} (team {r.team}) "
                 f"deployed by {r.author}. Change: {r.summary}"),
        timestamp=ts, context="deploy log", document_id=f"deploy-{r.deploy_id}",
        tags=["kind:deploy", f"team:{r.team}", f"service:{r.service}"],
        metadata=dict(kind="deploy", deploy_id=str(r.deploy_id), service=r.service, team=r.team, date=r.date,
                      summary=r.summary))


def feedback_memory(a: Anomaly, thread: list[dict], agent_verdict: str | None, deploys: list[dict]) -> dict:
    """One memory document per anomaly, holding the whole feedback thread. Re-retained with the same
    document_id on every new response, so a correction replaces the stale conclusion."""
    final = thread[-1]
    lines = [f'- {f["submitted_at"][:16].replace("T", " ")} {f["author"]} marked it {f["response"].upper()}'
             + (f': "{f["note"]}"' if f["note"] else "") for f in thread]
    corrected = len({f["response"] for f in thread}) > 1
    dep = "".join(f" Related deploy #{d['deploy_id']} ({d['service']} by {d['author']}, {d['relation']}): "
                  f"{d['summary']}." for d in deploys)
    content = (f"{describe(a)}{dep}\nSpendSight verdict at the time: {agent_verdict or 'n/a'}.\n"
               f"Engineer feedback thread:\n" + "\n".join(lines) +
               f"\nFinal resolution: {final['response'].upper()} (by {final['author']})"
               + (" - this corrects earlier feedback on the same anomaly." if corrected else "."))
    ts = datetime.fromisoformat(final["submitted_at"].replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return dict(
        content=content, timestamp=ts, context="engineer feedback on a cost anomaly",
        document_id=f"anomaly-{a.id}",
        tags=["kind:feedback", f"account:{a.account}", f"service:{a.service}", f"team:{a.team_tag}",
              f"response:{final['response']}"],
        metadata=dict(kind="feedback", response=final["response"], account=a.account, service=a.service,
                      team_tag=a.team_tag, start_date=a.start_date, day_of_month=str(_d(a.start_date).day),
                      delta_usd=f"{a.delta_usd:.2f}", owner=final["author"],
                      pattern=(final["note"] or "")[:140]))
