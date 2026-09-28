#!/usr/bin/env python
"""Generate a second synthetic company, Nimbus Retail (Jul-Sep 2026), to test upload, column mapping and cold start.

Unlike Acme it is exported the way the AWS Cost Explorer console does it: a WIDE CSV with one column per service
("EC2-Instances($)", ...), a "Service total" row and one row per day. There are no account/team columns, so the
upload path has to unpivot it and fill defaults. A few cells are deliberately broken to exercise validation.

Planted story (no labels are shipped; the user classifies these in the UI):
  - Monthly catalog reindex on OpenSearch on the 15th (recurring, expected): Jul 15 is a cold start,
    Aug 15 / Sep 15 should be recognised once someone labels July.
  - Back-to-school sale 20-22 Aug: CloudFront + EC2 up (planned, one-off).
  - Kinesis shard leak after the clickstream-ingest deploy on 3 Sep: Kinesis ~4x and stays there (waste).

Outputs (default data/samples/nimbus_retail/):
  nimbus_cost_explorer.csv   billing, AWS Cost Explorer console format
  nimbus_deploys.csv         deploy log with different column names (deployed_at, app, owner, description, id)
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

START, END = date(2026, 7, 1), date(2026, 9, 30)

# column -> (weekday base $/day, weekend factor, monthly growth, noise)
SERVICES = {
    "EC2-Instances": (640, 0.85, 0.02, 0.03),
    "EC2-Other": (95, 0.9, 0.01, 0.05),
    "Relational Database Service": (310, 1.0, 0.01, 0.01),
    "OpenSearch Service": (185, 1.0, 0.01, 0.02),
    "ElastiCache": (72, 1.0, 0.0, 0.005),
    "CloudFront": (140, 1.15, 0.02, 0.06),  # retail traffic peaks at weekends
    "S3": (88, 1.0, 0.03, 0.01),
    "Lambda": (34, 1.1, 0.02, 0.09),
    "DynamoDB": (61, 1.05, 0.02, 0.05),
    "Kinesis": (46, 1.0, 0.0, 0.03),
}


def billing(rng: np.random.Generator) -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    t = np.arange(len(days))
    wk = days.dayofweek >= 5
    cols = {}
    for name, (base, wkf, growth, noise) in SERVICES.items():
        eps = np.cumsum(rng.normal(0, noise * 0.4, len(days))) * 0.3 + rng.normal(0, noise, len(days))
        cols[name] = base * (1 + growth) ** (t / 30.4) * np.where(wk, wkf, 1.0) * np.exp(eps)
    df = pd.DataFrame(cols, index=days.date)

    for m in (7, 8, 9):  # monthly catalog reindex, bigger as the catalog grows
        df.loc[date(2026, m, 15), "OpenSearch Service"] += 690 * (1 + 0.04 * (m - 7)) * rng.uniform(0.95, 1.05)
    for d, f in ((date(2026, 8, 20), 1.35), (date(2026, 8, 21), 1.6), (date(2026, 8, 22), 1.5)):  # sale
        df.loc[d, "CloudFront"] *= f + 1.1
        df.loc[d, "EC2-Instances"] *= f
    d = date(2026, 9, 3)
    while d <= END:  # shard leak: per-tenant streams never cleaned up
        df.loc[d, "Kinesis"] += 185 + 4.0 * (d - date(2026, 9, 3)).days
        d += timedelta(days=1)
    return df.round(6)


def to_cost_explorer_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    out.columns = [f"{c}($)" for c in out.columns]
    out["Total costs($)"] = out.sum(axis=1)
    out = out.astype(object)
    out.loc[date(2026, 8, 9), "Lambda($)"] = ""        # blank cell
    out.loc[date(2026, 9, 12), "ElastiCache($)"] = "N/A"  # non-numeric cell
    rows = [["Service", *out.columns], ["Service total", *[f"{v:.6f}" for v in df.sum().tolist()],
                                        f"{df.values.sum():.6f}"]]
    for d, r in out.iterrows():
        rows.append([d.isoformat(), *[v if isinstance(v, str) else f"{v:.6f}" for v in r.tolist()]])
    pd.DataFrame(rows).to_csv(path, index=False, header=False, quoting=1)


def deploys(rng: np.random.Generator) -> pd.DataFrame:
    apps = {"storefront-web": "Aarav Shah", "checkout-svc": "Lena Hoffmann", "search-indexer": "Kavya Reddy",
            "clickstream-ingest": "Tom Becker", "recs-api": "Ishaan Malhotra", "infra-terraform": "Grace Liu"}
    msgs = {"storefront-web": ["Lazy-load hero images", "Upgrade Next.js", "Fix PDP layout shift"],
            "checkout-svc": ["Add UPI autopay", "Retry payment webhooks", "Fix coupon stacking"],
            "search-indexer": ["Tune analyzer for Hindi transliteration", "Bump OpenSearch client"],
            "clickstream-ingest": ["Batch PutRecords", "Drop bot traffic earlier"],
            "recs-api": ["New embeddings model", "Cache top-N per segment"],
            "infra-terraform": ["Rotate certs", "Enable S3 intelligent tiering"]}
    rows, i, d = [], 5001, START
    while d <= END:
        for _ in range(rng.poisson(0.2 if d.weekday() >= 5 else 1.6)):
            app = str(rng.choice(list(apps)))
            ts = datetime.combine(d, datetime.min.time()) + timedelta(minutes=int(rng.integers(240, 900)))
            rows.append(dict(deployed_at=ts.isoformat() + "Z", app=app, owner=apps[app],
                             description=str(rng.choice(msgs[app]))))
        d += timedelta(days=1)
    rows += [
        dict(deployed_at="2026-07-14T21:00:00Z", app="search-indexer", owner="Kavya Reddy",
             description="Schedule monthly full catalog reindex (15th, 02:00 UTC)"),
        dict(deployed_at="2026-08-19T10:30:00Z", app="infra-terraform", owner="Grace Liu",
             description="Scale storefront + CDN for Back-to-School sale 20-22 Aug, scale down 23 Aug"),
        dict(deployed_at="2026-09-03T11:15:00Z", app="clickstream-ingest", owner="Tom Becker",
             description="Per-tenant Kinesis streams for clickstream isolation"),
    ]
    df = pd.DataFrame(rows).sort_values("deployed_at").reset_index(drop=True)
    df.insert(0, "id", range(i, i + len(df)))
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "samples" / "nimbus_retail")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    df = billing(rng)
    to_cost_explorer_csv(df, args.out / "nimbus_cost_explorer.csv")
    deploys(rng).to_csv(args.out / "nimbus_deploys.csv", index=False)
    print(f"Nimbus Retail: {len(df)} days x {df.shape[1]} services, ${df.values.sum():,.0f} total -> {args.out}")


if __name__ == "__main__":
    main()
