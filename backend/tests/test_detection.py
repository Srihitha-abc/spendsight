import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.detection.detector import detect_anomalies

ROOT = Path(__file__).resolve().parents[2]


def series(costs, account="acme-prod", service="AmazonEC2", team="checkout", start="2026-04-01"):
    days = pd.date_range(start, periods=len(costs), freq="D")
    return pd.DataFrame(dict(date=days.strftime("%Y-%m-%d"), account=account, service=service, region="us-east-1",
                             usage_type="USE1-BoxUsage:m6i.xlarge", resource_id="i-0abc", team_tag=team,
                             cost_usd=costs))


def noisy(n=40, base=500.0, sd=10.0, seed=1):
    return list(np.random.default_rng(seed).normal(base, sd, n))


def test_empty_data_returns_nothing():
    assert detect_anomalies(series([])) == []


def test_single_spike_detected():
    costs = noisy()
    costs[30] += 2000
    [a] = detect_anomalies(series(costs))
    assert a.start_date == "2026-05-01" and a.days == 1
    assert a.delta_usd == pytest.approx(2000, abs=60)
    assert a.z_score > 3


def test_noise_is_not_flagged():
    assert detect_anomalies(series(noisy(n=120, sd=15))) == []


def test_min_dollar_threshold_suppresses_small_spikes():
    costs = [10.0 + (i % 3) * 0.1 for i in range(40)]
    costs[30] = 60  # huge z-score but only +$50
    assert detect_anomalies(series(costs), min_delta_usd=150) == []


def test_needs_full_baseline_window():
    costs = noisy(n=20)
    costs[5] += 5000  # only 5 days of history
    assert detect_anomalies(series(costs)) == []


def test_consecutive_days_merge_and_slow_burn_extends():
    costs = noisy(n=60)
    for i in range(30, 45):  # step change that the rolling baseline would absorb
        costs[i] += 400
    [a] = detect_anomalies(series(costs))
    assert a.days == 15
    assert a.total_excess_usd == pytest.approx(15 * 400, rel=0.1)


def test_groups_are_independent():
    a = noisy(seed=2)
    a[30] += 3000
    df = pd.concat([series(a, team="checkout"), series(noisy(seed=3), team="media")])
    [x] = detect_anomalies(df)
    assert x.team_tag == "checkout"


def test_generated_dataset_matches_planted_events(tmp_path):
    """All planted events are detected and nothing else is (on the real synthetic dataset)."""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_data.py"), "--out", str(tmp_path)], check=True,
                   capture_output=True)
    billing = pd.read_csv(tmp_path / "billing.csv")
    truth = json.loads((tmp_path / "planted_events.json").read_text())
    found = detect_anomalies(billing)
    assert len(found) == len(truth)
    for t in truth:
        assert any((a.account, a.service, a.team_tag) == (t["account"], t["service"], t["team_tag"])
                   and a.start_date <= t["date_to"] and t["date_from"] <= a.end_date for a in found), t["key"]
