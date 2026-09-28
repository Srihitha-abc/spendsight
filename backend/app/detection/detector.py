"""Statistical anomaly detection. Plain pandas, no LLM.

For each (account, service, team_tag) group we compare each day's cost to the mean/std of the
*previous* `baseline_days` days. A day is flagged when z > z_threshold AND the cost is at least
`min_delta_usd` above the baseline. Consecutive flagged days are merged into one incident, and an
incident is extended while cost stays above its (frozen) starting baseline, so slow-burn waste like
a forgotten instance is reported as one long incident instead of disappearing once the rolling
baseline catches up.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.models.schemas import Anomaly, LineItem

GROUP = ["account", "service", "team_tag"]


def daily_totals(billing: pd.DataFrame) -> pd.DataFrame:
    """Daily cost per group on a dense calendar (missing days = $0, so brand-new spend is visible)."""
    if billing.empty:
        return pd.DataFrame(columns=[*GROUP, "date", "cost_usd"])
    df = billing.assign(date=pd.to_datetime(billing["date"]))
    totals = df.groupby([*GROUP, "date"], as_index=False)["cost_usd"].sum()
    calendar = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    keys = totals[GROUP].drop_duplicates()
    dense = keys.merge(pd.DataFrame({"date": calendar}), how="cross")
    out = dense.merge(totals, on=[*GROUP, "date"], how="left").fillna({"cost_usd": 0.0})
    return out.sort_values([*GROUP, "date"]).reset_index(drop=True)


def detect_anomalies(
    billing: pd.DataFrame,
    baseline_days: int = 14,
    z_threshold: float = 3.0,
    min_delta_usd: float = 150.0,
) -> list[Anomaly]:
    totals = daily_totals(billing)
    if totals.empty:
        return []
    billing_dates = pd.to_datetime(billing["date"])
    anomalies: list[Anomaly] = []

    for key, g in totals.groupby(GROUP, sort=False):
        s = g.set_index("date")["cost_usd"]
        prior = s.shift(1).rolling(baseline_days, min_periods=baseline_days)
        mu, sd = prior.mean(), prior.std()
        # Floor the std so perfectly flat series (reserved capacity) don't produce infinite z-scores.
        z = (s - mu) / np.maximum(sd, mu * 0.02 + 1.0)
        flagged = (z > z_threshold) & ((s - mu) >= min_delta_usd)

        dates = list(s.index)
        i = 0
        while i < len(dates):
            if not flagged.iloc[i]:
                i += 1
                continue
            start = i
            base = float(mu.iloc[start])
            j = start
            # Merge consecutive flagged days, then keep extending while still elevated vs the frozen baseline.
            while j + 1 < len(dates) and (flagged.iloc[j + 1] or s.iloc[j + 1] - base >= min_delta_usd):
                j += 1
            window = s.iloc[start : j + 1]
            peak_date = window.idxmax()
            anomalies.append(_build(key, billing, billing_dates, s, window, base, float(z.iloc[start]), peak_date))
            i = j + 1

    return sorted(anomalies, key=lambda a: (a.start_date, a.account, a.service))


def _build(key, billing, billing_dates, s, window, base, z, peak_date) -> Anomaly:
    account, service, team = key
    start, end = window.index[0], window.index[-1]
    peak_rows = billing[(billing_dates == peak_date) & (billing["account"] == account)
                        & (billing["service"] == service) & (billing["team_tag"] == team)]
    items = (peak_rows.groupby("usage_type").agg(resources=("resource_id", "nunique"), cost_usd=("cost_usd", "sum"))
             .sort_values("cost_usd", ascending=False).head(3))
    return Anomaly(
        id=f"{account}|{service}|{team}|{start.date()}",
        account=account, service=service, team_tag=team,
        start_date=str(start.date()), end_date=str(end.date()), peak_date=str(peak_date.date()),
        peak_cost_usd=round(float(s[peak_date]), 2), baseline_usd=round(base, 2),
        delta_usd=round(float(s[peak_date]) - base, 2), z_score=round(z, 1),
        total_excess_usd=round(float((window - base).clip(lower=0).sum()), 2),
        days=len(window),
        top_line_items=[LineItem(usage_type=u, resources=int(r.resources), cost_usd=round(float(r.cost_usd), 2))
                        for u, r in items.iterrows()],
    )
