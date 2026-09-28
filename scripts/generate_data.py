#!/usr/bin/env python
"""Generate synthetic AWS billing data for the fictional company Acme Logistics (Apr-Sep 2026).

Outputs (written to data/ by default):
  billing.csv          daily cost line items (CUR-like: one row per resource per usage type per day)
  deploys.csv          deploy log for all services
  seed_feedback.json   engineer feedback that replay mode applies to detected anomalies
  planted_events.json  ground-truth labels for the planted anomalies (used only for evaluation)

Everything is deterministic for a given --seed.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

START = date(2026, 4, 1)
END = date(2026, 9, 30)

PROD, STAGING, ML = "acme-prod", "acme-staging", "acme-ml-platform"
ACCOUNT_IDS = {PROD: "481726350912", STAGING: "219384756102", ML: "736510294837"}

REGION_PREFIX = {"us-east-1": "USE1-", "ap-south-1": "APS3-", "eu-west-1": "EU-"}
REGION_PRICE = {"us-east-1": 1.0, "ap-south-1": 1.05, "eu-west-1": 1.114}

# On-demand Linux hourly prices (us-east-1).
EC2_HOURLY = {
    "m6i.xlarge": 0.192,
    "m6i.2xlarge": 0.384,
    "m6i.4xlarge": 0.768,
    "c6i.2xlarge": 0.34,
    "c6i.4xlarge": 0.68,
    "c6g.2xlarge": 0.272,
    "t3.xlarge": 0.1664,
    "g5.xlarge": 1.006,
    "g5.2xlarge": 1.212,
    "p3.8xlarge": 12.24,
    "p4d.24xlarge": 32.7726,
}

COLUMNS = ["date", "account", "service", "region", "usage_type", "resource_id", "team_tag", "cost_usd"]


@dataclass(frozen=True)
class Resource:
    account: str
    service: str
    region: str
    usage_type: str
    resource_id: str
    team: str
    base: float  # weekday daily cost at START
    weekend: float = 1.0  # multiplier on Sat/Sun (autoscaling, schedulers)
    growth: float = 0.0  # monthly compound growth
    noise: float = 0.03  # log-sd of day-to-day noise


class Catalog:
    """Builds the steady-state resource inventory with realistic IDs."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.items: list[Resource] = []

    def hexid(self, prefix: str) -> str:
        return prefix + self.rng.bytes(8).hex()

    def arn(self, service: str, account: str, region: str, rest: str) -> str:
        return f"arn:aws:{service}:{region}:{ACCOUNT_IDS[account]}:{rest}"

    def add(self, *args, **kwargs) -> Resource:
        r = Resource(*args, **kwargs)
        self.items.append(r)
        return r

    def fleet(self, account, region, itype, team, count, weekend=1.0, growth=0.0, noise=0.02) -> list[Resource]:
        """One row per EC2 instance; weekend < 1 models autoscaling down (fractional instance-hours)."""
        daily = EC2_HOURLY[itype] * REGION_PRICE[region] * 24
        return [
            self.add(account, "AmazonEC2", region, f"{REGION_PREFIX[region]}BoxUsage:{itype}",
                     self.hexid("i-0"), team, daily, weekend, growth, noise)
            for _ in range(count)
        ]


def build_catalog(rng: np.random.Generator) -> tuple[list[Resource], dict[str, Resource], Catalog]:
    c = Catalog(rng)
    key: dict[str, Resource] = {}
    use1, aps3, eu = "us-east-1", "ap-south-1", "eu-west-1"

    # ---------------- acme-prod ----------------
    key["prod_checkout_nodes"] = c.fleet(PROD, use1, "m6i.2xlarge", "checkout", 14, weekend=0.8, growth=0.015)[0]
    c.fleet(PROD, use1, "c6i.4xlarge", "checkout", 10, weekend=0.8, growth=0.015)
    c.fleet(PROD, use1, "m6i.4xlarge", "platform", 12, weekend=0.85, growth=0.012)
    c.fleet(PROD, use1, "c6g.2xlarge", "media", 8, weekend=0.85, growth=0.02)
    c.fleet(PROD, use1, "c6i.2xlarge", "tracking", 6, weekend=0.9, growth=0.018)
    c.fleet(PROD, eu, "m6i.xlarge", "tracking", 6, weekend=0.9, growth=0.01)
    c.fleet(PROD, aps3, "c6i.2xlarge", "routing", 8, weekend=0.7, growth=0.02)
    c.add(PROD, "AmazonEC2", use1, "USE1-EBS:VolumeUsage.gp3", c.hexid("vol-0"), "platform", 95, 1.0, 0.02, 0.005)
    c.add(PROD, "AmazonEC2", use1, "USE1-NatGateway-Bytes", c.hexid("nat-0"), "platform", 55, 0.85, 0.015, 0.06)
    c.add(PROD, "AmazonEC2", use1, "USE1-NatGateway-Hours", c.hexid("nat-0"), "platform", 3.24, 1.0, 0.0, 0.0)

    c.add(PROD, "AmazonRDS", use1, "USE1-InstanceUsage:db.r6g.2xlarge",
          c.arn("rds", PROD, use1, "cluster:checkout-aurora-pg"), "checkout", 49.8, 1.0, 0.0, 0.002)
    c.add(PROD, "AmazonRDS", use1, "USE1-Aurora:StorageIOUsage",
          c.arn("rds", PROD, use1, "cluster:checkout-aurora-pg"), "checkout", 18, 0.8, 0.02, 0.1)
    c.add(PROD, "AmazonRDS", aps3, "APS3-Aurora:ServerlessV2Usage",
          c.arn("rds", PROD, aps3, "cluster:route-planner-aurora"), "routing", 150, 0.75, 0.02, 0.05)

    key["media_egress"] = c.add(PROD, "AmazonS3", use1, "USE1-DataTransfer-Out-Bytes", "acme-prod-media-assets",
                                "media", 85, 0.85, 0.02, 0.07)
    c.add(PROD, "AmazonS3", use1, "USE1-TimedStorage-ByteHrs", "acme-prod-media-assets", "media", 140, 1.0, 0.03, 0.004)
    c.add(PROD, "AmazonS3", use1, "USE1-Requests-Tier1", "acme-prod-media-assets", "media", 22, 0.8, 0.02, 0.08)
    c.add(PROD, "AmazonS3", use1, "USE1-TimedStorage-ByteHrs", "acme-datalake-raw", "data-eng", 210, 1.0, 0.025, 0.003)
    key["datalake_requests"] = c.add(PROD, "AmazonS3", use1, "USE1-Requests-Tier2", "acme-datalake-raw",
                                     "data-eng", 14, 0.5, 0.02, 0.15)

    cf = f"arn:aws:cloudfront::{ACCOUNT_IDS[PROD]}:distribution/E2QWRUHAPOMQZL"
    key["cf_us"] = c.add(PROD, "AmazonCloudFront", "global", "US-DataTransfer-Out-Bytes", cf, "media", 120, 0.8, 0.02, 0.05)
    key["cf_in"] = c.add(PROD, "AmazonCloudFront", "global", "IN-DataTransfer-Out-Bytes", cf, "media", 48, 0.85, 0.03, 0.06)
    c.add(PROD, "AmazonCloudFront", "global", "US-Requests-Tier2-HTTPS", cf, "media", 18, 0.8, 0.02, 0.05)

    ddb = c.arn("dynamodb", PROD, use1, "table/shipment-events")
    c.add(PROD, "AmazonDynamoDB", use1, "USE1-WriteRequestUnits", ddb, "tracking", 96, 0.75, 0.02, 0.05)
    c.add(PROD, "AmazonDynamoDB", use1, "USE1-ReadRequestUnits", ddb, "tracking", 34, 0.75, 0.02, 0.06)
    c.add(PROD, "AmazonDynamoDB", use1, "USE1-TimedStorage-ByteHrs", ddb, "tracking", 21, 1.0, 0.03, 0.003)

    fn = c.arn("lambda", PROD, aps3, "function:route-recalc")
    c.add(PROD, "AWSLambda", aps3, "APS3-Lambda-GB-Second", fn, "routing", 38, 0.7, 0.02, 0.1)
    c.add(PROD, "AWSLambda", aps3, "APS3-Request", fn, "routing", 6, 0.7, 0.02, 0.1)

    for _ in range(3):
        c.add(PROD, "AmazonElastiCache", use1, "USE1-NodeUsage:cache.r6g.large",
              c.arn("elasticache", PROD, use1, f"cluster:checkout-sessions-00{_ + 1}"), "checkout", 4.94, 1.0, 0.0, 0.0)

    key["athena"] = c.add(PROD, "AmazonAthena", use1, "USE1-DataScannedInTB",
                          c.arn("athena", PROD, use1, "workgroup/finance-reporting"), "data-eng", 48, 0.4, 0.02, 0.22)
    rs = c.arn("redshift", PROD, use1, "cluster:acme-analytics")
    c.add(PROD, "AmazonRedshift", use1, "USE1-Node:ra3.4xlarge", rs, "data-eng", 156.5, 1.0, 0.0, 0.0)
    key["redshift_cs"] = c.add(PROD, "AmazonRedshift", use1, "USE1-CS:ra3.4xlarge", rs, "data-eng", 12, 0.3, 0.0, 0.45)
    c.add(PROD, "AmazonRedshift", use1, "USE1-RMS:ra3", rs, "data-eng", 22, 1.0, 0.03, 0.003)

    c.add(PROD, "AmazonCloudWatch", use1, "USE1-CW:MetricMonitorUsage", "", "sre", 45, 1.0, 0.01, 0.01)
    c.add(PROD, "AmazonCloudWatch", use1, "USE1-DataProcessing-Bytes", "", "sre", 70, 0.8, 0.02, 0.08)
    c.add(PROD, "AmazonCloudWatch", use1, "USE1-TimedStorage-ByteHrs", "", "sre", 18, 1.0, 0.04, 0.003)
    c.add(PROD, "awskms", use1, "USE1-KMS-Requests", "", "sre", 3.5, 0.8, 0.0, 0.1)

    alb = c.arn("elasticloadbalancing", PROD, use1, "loadbalancer/app/prod-edge-alb/3f9a1c07be52d4e8")
    key["alb_lcu"] = c.add(PROD, "AWSELB", use1, "USE1-LCUUsage", alb, "platform", 48, 0.8, 0.015, 0.06)
    c.add(PROD, "AWSELB", use1, "USE1-LoadBalancerUsage", alb, "platform", 0.54 * 4, 1.0, 0.0, 0.0)
    key["regional_dt"] = c.add(PROD, "AWSDataTransfer", use1, "USE1-DataTransfer-Regional-Bytes", "",
                               "platform", 35, 0.85, 0.015, 0.08)
    c.add(PROD, "AmazonEKS", use1, "USE1-AmazonEKS-Hours:perCluster",
          c.arn("eks", PROD, use1, "cluster/prod-main"), "platform", 2.4, 1.0, 0.0, 0.0)

    # ---------------- acme-staging (scheduler scales down on weekends) ----------------
    c.fleet(STAGING, use1, "m6i.xlarge", "platform", 10, weekend=0.3, noise=0.05)
    c.fleet(STAGING, use1, "t3.xlarge", "checkout", 6, weekend=0.25, noise=0.05)
    for i in range(2):
        c.add(STAGING, "AmazonRDS", use1, "USE1-InstanceUsage:db.t4g.large",
              c.arn("rds", STAGING, use1, f"db:staging-pg-0{i + 1}"), "platform", 3.1, 0.35, 0.0, 0.0)
    c.add(STAGING, "AmazonS3", use1, "USE1-TimedStorage-ByteHrs", "acme-staging-artifacts", "platform", 18, 1.0, 0.02, 0.004)
    c.add(STAGING, "AmazonEKS", use1, "USE1-AmazonEKS-Hours:perCluster",
          c.arn("eks", STAGING, use1, "cluster/staging-main"), "platform", 2.4, 1.0, 0.0, 0.0)
    c.add(STAGING, "AmazonCloudWatch", use1, "USE1-DataProcessing-Bytes", "", "sre", 12, 0.6, 0.0, 0.08)

    # ---------------- acme-ml-platform ----------------
    c.fleet(ML, use1, "g5.2xlarge", "ml-research", 3, noise=0.005)  # eta-model inference
    c.fleet(ML, use1, "g5.xlarge", "ml-research", 2, weekend=0.4, noise=0.1)  # dev notebooks
    c.add(ML, "AmazonEC2", use1, "USE1-EBS:VolumeUsage.gp3", c.hexid("vol-0"), "ml-research", 30, 1.0, 0.03, 0.004)
    c.add(ML, "AmazonS3", use1, "USE1-TimedStorage-ByteHrs", "acme-ml-feature-store", "ml-research", 75, 1.0, 0.04, 0.004)
    key["ml_s3_requests"] = c.add(ML, "AmazonS3", use1, "USE1-Requests-Tier2", "acme-ml-feature-store",
                                  "ml-research", 9, 0.8, 0.02, 0.18)
    c.add(ML, "AmazonCloudWatch", use1, "USE1-DataProcessing-Bytes", "", "ml-research", 8, 1.0, 0.0, 0.08)

    return c.items, key, c


def ar1_noise(rng: np.random.Generator, n: int, sigma: float, phi: float = 0.5) -> np.ndarray:
    """Autocorrelated log-noise so costs drift like real usage instead of pure white noise."""
    if sigma == 0:
        return np.zeros(n)
    eps = np.empty(n)
    eps[0] = rng.normal(0, sigma)
    shock = rng.normal(0, sigma * np.sqrt(1 - phi**2), n)
    for i in range(1, n):
        eps[i] = phi * eps[i - 1] + shock[i]
    return eps


def baseline_rows(rng: np.random.Generator, catalog: list[Resource]) -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    t = np.arange(len(days))
    weekend = days.dayofweek >= 5
    frames = []
    for r in catalog:
        eps = ar1_noise(rng, len(days), r.noise)
        cost = (r.base * (1 + r.growth) ** (t / 30.4) * np.where(weekend, r.weekend, 1.0)
                * np.exp(eps - r.noise**2 / 2))
        frames.append(pd.DataFrame({
            "date": days.date, "account": r.account, "service": r.service, "region": r.region,
            "usage_type": r.usage_type, "resource_id": r.resource_id, "team_tag": r.team, "cost_usd": cost,
        }))
    return pd.concat(frames, ignore_index=True)


class Billing:
    """Baseline billing plus helpers for planting events."""

    def __init__(self, df: pd.DataFrame, catalog: Catalog):
        self.df = df
        self.catalog = catalog
        self.extra: list[dict] = []

    def _mask(self, r: Resource, day: date):
        d = self.df
        return (d["date"] == day) & (d["resource_id"] == r.resource_id) & (d["usage_type"] == r.usage_type)

    def add_cost(self, r: Resource, day: date, usd: float):
        self.df.loc[self._mask(r, day), "cost_usd"] += usd

    def scale(self, r: Resource, day: date, factor: float):
        self.df.loc[self._mask(r, day), "cost_usd"] *= factor

    def new_row(self, day, account, service, region, usage_type, resource_id, team, cost):
        self.extra.append(dict(date=day, account=account, service=service, region=region, usage_type=usage_type,
                               resource_id=resource_id, team_tag=team, cost_usd=cost))

    def instances(self, day, account, itype, team, ids, hours, jitter=None):
        hourly = EC2_HOURLY[itype]
        for iid in ids:
            h = hours if jitter is None else max(0.5, hours + jitter.normal(0, 0.25))
            self.new_row(day, account, "AmazonEC2", "us-east-1", f"USE1-BoxUsage:{itype}", iid, team, hourly * h)

    def frame(self) -> pd.DataFrame:
        out = pd.concat([self.df, pd.DataFrame(self.extra)], ignore_index=True)
        out["cost_usd"] = out["cost_usd"].round(4)
        out = out[out["cost_usd"] > 0]
        return out.sort_values(["date", "account", "service", "usage_type", "resource_id"])[COLUMNS]


def d(month: int, day: int) -> date:
    return date(2026, month, day)


def plant_events(b: Billing, key: dict[str, Resource], rng: np.random.Generator) -> list[dict]:
    """Plant the seven story events. Returns ground-truth labels."""
    truth = []

    # 1 + 6. Monthly eta-model retraining on the 1st: 8x p4d.24xlarge. September's run is ~2.3x
    # because feature-pipeline added 18 months of GPS history to the training set (deploy on 28 Aug).
    trainers = [b.catalog.hexid("i-0") for _ in range(8)]
    run_hours = {4: 7.8, 5: 8.25, 6: 8.5, 7: 8.8, 8: 9.05, 9: 19.6}
    for month, hours in run_hours.items():
        b.instances(d(month, 1), ML, "p4d.24xlarge", "ml-research", trainers, hours, jitter=rng)
        b.add_cost(key["ml_s3_requests"], d(month, 1), 30 if month < 9 else 70)  # dataset reads
        if month > 4:  # April 1 has no 14-day baseline yet, so it is not a detectable anomaly
            unusual = month == 9
            truth.append(dict(
                key=f"ml_retrain_{month:02d}", label="EXPECTED_BUT_UNUSUAL" if unusual else "EXPECTED",
                account=ML, service="AmazonEC2", team_tag="ml-research",
                date_from=str(d(month, 1)), date_to=str(d(month, 1)),
                description=("Monthly eta-model retraining on 8x p4d.24xlarge"
                             + (" - ~2.3x usual cost after training set grew (feature-pipeline deploy 28 Aug)"
                                if unusual else ""))))

    # 2. Quarter-end finance reporting: Athena full-table scans + Redshift concurrency scaling.
    for month, qd in ((6, (29, 30)), (9, (29, 30))):
        scale = 1.0 if month == 6 else 1.07
        b.add_cost(key["athena"], d(month, qd[0]), 780 * scale)
        b.add_cost(key["athena"], d(month, qd[1]), 1610 * scale)
        b.add_cost(key["redshift_cs"], d(month, qd[1]), 395 * scale)
        b.add_cost(key["datalake_requests"], d(month, qd[1]), 25)
        for svc in ("AmazonAthena", "AmazonRedshift"):
            truth.append(dict(
                key=f"quarter_end_{month:02d}_{svc}", label="EXPECTED", account=PROD, service=svc,
                team_tag="data-eng", date_from=str(d(month, qd[0] if svc == "AmazonAthena" else qd[1])),
                date_to=str(d(month, qd[1])), description="Quarter-end finance reporting job"))

    # 3. One-off k6 load test for the Monsoon Mega Sale: 60x c6i.4xlarge in prod for ~10h on 14 Jul.
    lt_ids = [b.catalog.hexid("i-0") for _ in range(60)]
    b.instances(d(7, 14), PROD, "c6i.4xlarge", "checkout", lt_ids, 10.0, jitter=rng)
    b.add_cost(key["alb_lcu"], d(7, 14), 62)
    b.add_cost(key["regional_dt"], d(7, 14), 41)
    truth.append(dict(key="load_test_monsoon_sale", label="EXPECTED", account=PROD, service="AmazonEC2",
                      team_tag="checkout", date_from="2026-07-14", date_to="2026-07-14",
                      description="Planned k6 load test for the Monsoon Mega Sale (CAB-2291)"))
    # The sale itself (17-19 Jul) lifts traffic slightly, below alerting thresholds.
    for day in (d(7, 17), d(7, 18), d(7, 19)):
        b.df.loc[(b.df["date"] == day) & (b.df["account"] == PROD) & (b.df["team_tag"].isin(["checkout", "media"])),
                 "cost_usd"] *= 1.08

    # 4. Staging perf environment (16x m6i.4xlarge + RDS) left running over the 8-9 Aug weekend.
    perf_ids = [b.catalog.hexid("i-0") for _ in range(16)]
    perf_db = b.catalog.arn("rds", STAGING, "us-east-1", "db:cart-v3-perf")
    for day, hours in ((d(8, 7), 10.5), (d(8, 8), 24), (d(8, 9), 24), (d(8, 10), 4.5)):
        b.instances(day, STAGING, "m6i.4xlarge", "checkout", perf_ids, hours)
        b.new_row(day, STAGING, "AmazonRDS", "us-east-1", "USE1-InstanceUsage:db.r6g.xlarge", perf_db,
                  "checkout", 0.519 * hours)
    truth.append(dict(key="staging_weekend_perf_env", label="NEW_WASTE", account=STAGING, service="AmazonEC2",
                      team_tag="checkout", date_from="2026-08-07", date_to="2026-08-10",
                      description="cart-v3 perf environment left running over the weekend"))

    # 5. image-service deploy on 22 Jul 14:05 UTC serves thumbnails via S3 presigned URLs, bypassing
    #    CloudFront -> S3 egress jumps ~15x while CloudFront bytes drop. Rolled back 25 Jul 06:40 UTC.
    bypass = {d(7, 22): 9.92 / 24, d(7, 23): 1.0, d(7, 24): 1.0, d(7, 25): 6.67 / 24}
    for day, frac in bypass.items():
        wk = 0.85 if day.weekday() >= 5 else 1.0
        b.add_cost(key["media_egress"], day, 1265 * frac * wk * rng.uniform(0.95, 1.05))
        b.scale(key["cf_us"], day, 1 - 0.62 * frac)
        b.scale(key["cf_in"], day, 1 - 0.55 * frac)
    truth.append(dict(key="image_service_egress_bug", label="NEW_WASTE", account=PROD, service="AmazonS3",
                      team_tag="media", date_from="2026-07-22", date_to="2026-07-25",
                      description="S3 egress spike after image-service presigned-URL deploy (CloudFront bypassed)"))

    # 7. Forgotten p3.8xlarge from a route-embedding experiment, launched from the console on 17 Sep.
    #    Tagged ml-research like the retraining fleet, but on the wrong day and with no precedent.
    gpu = b.catalog.hexid("i-0")
    vol = b.catalog.hexid("vol-0")
    snap_gb = 0.0
    day = d(9, 17)
    while day <= END:
        hours = 10.0 if day == d(9, 17) else 5.5 if day == d(9, 30) else 24.0
        b.new_row(day, ML, "AmazonEC2", "us-east-1", "USE1-BoxUsage:p3.8xlarge", gpu, "ml-research",
                  EC2_HOURLY["p3.8xlarge"] * hours)
        b.new_row(day, ML, "AmazonEC2", "us-east-1", "USE1-EBS:VolumeUsage.io2", vol, "ml-research", 4.2)
        b.new_row(day, ML, "AmazonEC2", "us-east-1", "USE1-EBS:VolumeP-IOPS.io2", vol, "ml-research", 17.3)
        snap_gb += 1024 if day == d(9, 17) else rng.uniform(35, 60)  # nightly DLM snapshots keep growing
        b.new_row(day, ML, "AmazonEC2", "us-east-1", "USE1-EBS:SnapshotUsage", b.catalog.hexid("snap-0"),
                  "ml-research", snap_gb * 0.05 / 30)
        day += timedelta(days=1)
    truth.append(dict(key="forgotten_gpu_instance", label="NEW_WASTE", account=ML, service="AmazonEC2",
                      team_tag="ml-research", date_from="2026-09-17", date_to="2026-09-30",
                      description="Forgotten p3.8xlarge experiment box (console launch, no owner, no precedent)"))
    return truth


# ---------------------------------------------------------------------------------------------
# Deploy log
# ---------------------------------------------------------------------------------------------

SERVICES = {
    "checkout-api": ("checkout", ["Rohan Gupta", "Liam O'Brien", "Sofia Martínez"]),
    "cart-service": ("checkout", ["Rohan Gupta", "Sofia Martínez"]),
    "image-service": ("media", ["Arjun Mehta", "Yuki Tanaka"]),
    "route-optimizer": ("routing", ["Vikram Nair", "Chen Wei"]),
    "route-recalc-lambda": ("routing", ["Chen Wei"]),
    "tracking-ingest": ("tracking", ["Emma Schultz", "Aditya Kulkarni"]),
    "eta-model": ("ml-research", ["Priya Raman", "Sneha Iyer"]),
    "feature-pipeline": ("ml-research", ["Ananya Das", "Sneha Iyer"]),
    "datalake-etl": ("data-eng", ["Meera Pillai", "Mateo Rossi"]),
    "platform-infra": ("platform", ["Karthik Subramanian", "Noah Fischer"]),
    "observability": ("sre", ["Fatima Sheikh"]),
}

SUMMARIES = {
    "checkout-api": ["Add idempotency keys to POST /orders", "Fix COD surcharge rounding for Tier-2 pincodes",
                     "Bump stripe-python to 12.4", "Retry UPI callbacks with jittered backoff",
                     "Remove legacy /v1/quote endpoint", "Add GST breakdown to invoice payload"],
    "cart-service": ["Cache promo eligibility for 60s", "Fix race in cart merge on login",
                     "Feature flag: cart-v3 for 5% of users", "Upgrade redis-py, tune pool size"],
    "image-service": ["Resize worker: switch to libvips 8.15", "Add AVIF variants for product shots",
                      "Fix EXIF orientation on POD photos", "Tune thumbnail cache headers"],
    "route-optimizer": ["OR-tools 9.10 upgrade", "Respect driver break windows in VRP solver",
                        "Add Pune and Kochi depots", "Reduce solver timeout to 8s for same-day orders"],
    "route-recalc-lambda": ["Bump memory to 1024MB", "Skip recalcs for delivered shipments",
                            "Batch traffic API calls"],
    "tracking-ingest": ["Dedupe GPS pings within 5s", "Add Kafka consumer lag metric",
                        "Parse new courier partner webhook format", "Increase DynamoDB write batch size"],
    "eta-model": ["Promote retrained eta-model", "Add holiday calendar features", "Shadow-deploy eta-model v3"],
    "feature-pipeline": ["Backfill weather features", "Fix timezone bug in hourly aggregates",
                         "Add warehouse dwell-time features"],
    "datalake-etl": ["Partition shipments by ship_date", "Compact small parquet files nightly",
                     "Add courier_sla dbt model", "Upgrade Glue jobs to 4.0"],
    "platform-infra": ["Karpenter 0.37 upgrade", "Rotate ALB certs", "Enable gp3 for new EBS volumes",
                       "Terraform: tighten staging SG rules", "EKS 1.30 node AMI rollout"],
    "observability": ["Drop debug logs from tracking-ingest", "Add SLO burn-rate alerts",
                      "Grafana dashboard for checkout latency"],
}


def build_deploys(rng: np.random.Generator) -> pd.DataFrame:
    names = list(SERVICES)
    weights = np.array([6, 4, 3, 4, 2, 4, 2, 2, 3, 3, 2], dtype=float)
    weights /= weights.sum()
    versions = {s: [1, int(rng.integers(3, 40)), 0] for s in names}
    rows = []

    def bump(svc):
        v = versions[svc]
        if rng.random() < 0.15:
            v[1] += 1
            v[2] = 0
        else:
            v[2] += 1
        return f"v{v[0]}.{v[1]}.{v[2]}"

    day = START
    while day <= END:
        n = rng.poisson(0.3 if day.weekday() >= 5 else 2.4)
        for _ in range(n):
            svc = rng.choice(names, p=weights)
            team, authors = SERVICES[svc]
            ts = datetime.combine(day, datetime.min.time()) + timedelta(minutes=int(rng.integers(3 * 60, 15 * 60)))
            rows.append(dict(ts=ts, service=svc, team=team, author=str(rng.choice(authors)),
                             summary=f"{bump(svc)}: {rng.choice(SUMMARIES[svc])}"))
        day += timedelta(days=1)

    # Planted deploys that the story depends on.
    planted = [
        (datetime(2026, 7, 13, 11, 20), "platform-infra", "Karthik Subramanian",
         "k6 load-test harness for Monsoon Mega Sale (60x c6i.4xlarge, auto-teardown 20:00 UTC 14 Jul) CAB-2291"),
        (datetime(2026, 7, 22, 14, 5), "image-service", "Arjun Mehta",
         "Serve thumbnails via S3 presigned URLs instead of CloudFront origin (fixes stale cache on re-upload)"),
        (datetime(2026, 7, 25, 6, 40), "image-service", "Arjun Mehta",
         "HOTFIX revert presigned-URL thumbnails, restore CloudFront origin"),
        (datetime(2026, 8, 7, 13, 10), "cart-service", "Rohan Gupta",
         "Deploy cart-v3 perf environment to staging (16x m6i.4xlarge) for load profiling"),
        (datetime(2026, 8, 28, 9, 45), "feature-pipeline", "Ananya Das",
         "Add 18 months of historical GPS traces + multi-region features to eta-model training set"),
    ]
    for month in range(4, 10):  # retrained model promoted the morning after the run
        planted.append((datetime(2026, month, 2, 4, 30), "eta-model", "Priya Raman",
                        f"Promote retrained eta-model 2026.{month:02d} (monthly retrain on 1st)"))
    for ts, svc, author, summary in planted:
        rows.append(dict(ts=ts, service=svc, team=SERVICES[svc][0], author=author,
                         summary=f"{bump(svc)}: {summary}"))

    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    df["deploy_id"] = np.arange(201, 201 + len(df))
    df["date"] = df["ts"].dt.date.astype(str)
    df["time_utc"] = df["ts"].dt.strftime("%H:%M")
    return df[["date", "time_utc", "service", "team", "deploy_id", "author", "summary"]]


def deploy_id(deploys: pd.DataFrame, date_str: str, service: str, contains: str) -> int:
    m = deploys[(deploys["date"] == date_str) & (deploys["service"] == service)
                & deploys["summary"].str.contains(contains, regex=False)]
    return int(m["deploy_id"].iloc[0])


# ---------------------------------------------------------------------------------------------
# Seed feedback (casual Slack tone). Replay mode matches these to detected anomalies by
# account/service/team and a date window, then retains them into Hindsight.
# ---------------------------------------------------------------------------------------------

def build_feedback(deploys: pd.DataFrame) -> list[dict]:
    egress = deploy_id(deploys, "2026-07-22", "image-service", "presigned")
    revert = deploy_id(deploys, "2026-07-25", "image-service", "revert")
    lt = deploy_id(deploys, "2026-07-13", "platform-infra", "k6")
    perf = deploy_id(deploys, "2026-08-07", "cart-service", "perf environment")
    feat = deploy_id(deploys, "2026-08-28", "feature-pipeline", "18 months")

    def fb(event, account, service, team, date_from, date_to, response, author, note, at):
        return dict(event=event, match=dict(account=account, service=service, team_tag=team,
                                            date_from=date_from, date_to=date_to),
                    response=response, author=author, note=note, submitted_at=at)

    retrain = lambda m: (ML, "AmazonEC2", "ml-research", f"2026-{m:02d}-01", f"2026-{m:02d}-02")
    items = [
        # May: cold start. FinOps flags it, the owner corrects it -> conflicting feedback resolved by a correction.
        fb("ml_retrain_05", *retrain(5), "investigating", "Daniel Okafor",
           "whoa ~2.2k on ml-platform EC2 overnight?? 8 p4d boxes spun up. @priya is this you?",
           "2026-05-01T09:12:00Z"),
        fb("ml_retrain_05", *retrain(5), "expected", "Priya Raman",
           "yep that's the monthly eta-model retrain, kicks off on the 1st on 8x p4d.24xlarge and tears down "
           "itself. ~2k is normal for us, same as April. pls don't page ml-research for this lol",
           "2026-05-01T10:40:00Z"),
        fb("ml_retrain_06", *retrain(6), "expected", "Priya Raman",
           "june retrain, all good. model promoted this morning", "2026-06-01T11:05:00Z"),
        fb("ml_retrain_07", *retrain(7), "expected", "Sneha Iyer", "yup monthly retrain ✅", "2026-07-01T08:30:00Z"),
        fb("ml_retrain_08", *retrain(8), "expected", "Sneha Iyer", "retrain, ~2.4k, in line", "2026-08-01T07:55:00Z"),
        fb("ml_retrain_09", *retrain(9), "expected", "Ananya Das",
           f"expected-ish. we added 18 months of GPS history to the training set (deploy #{feat} on 28 Aug) so the "
           "run took ~20h instead of ~9h. new normal is prob ~5k/month, I'll raise the budget with Daniel",
           "2026-09-01T12:20:00Z"),

        fb("quarter_end_06_AmazonAthena", PROD, "AmazonAthena", "data-eng", "2026-06-29", "2026-06-30", "expected",
           "Meera Pillai",
           "quarter-end finance reporting. the QE job scans the full shipments table in athena, happens every "
           "quarter (march was about the same). nothing to do here", "2026-06-30T13:15:00Z"),
        fb("quarter_end_06_AmazonRedshift", PROD, "AmazonRedshift", "data-eng", "2026-06-30", "2026-07-01",
           "expected", "Mateo Rossi",
           "same QE thing, finance dashboards hammer redshift and concurrency scaling kicks in. goes away by the 2nd",
           "2026-06-30T15:02:00Z"),
        fb("quarter_end_09_AmazonAthena", PROD, "AmazonAthena", "data-eng", "2026-09-29", "2026-09-30", "expected",
           "Meera Pillai", "Q3 close, same as June", "2026-09-30T10:10:00Z"),
        fb("quarter_end_09_AmazonRedshift", PROD, "AmazonRedshift", "data-eng", "2026-09-30", "2026-10-01",
           "expected", "Mateo Rossi", "QE concurrency scaling again, expected", "2026-09-30T10:25:00Z"),

        # Load test: someone calls it waste first, then the owner corrects it.
        fb("load_test_monsoon_sale", PROD, "AmazonEC2", "checkout", "2026-07-14", "2026-07-15", "waste", "Liam O'Brien",
           "who spun up 60 c6i.4xlarge in prod?? marking waste until someone claims it", "2026-07-14T12:30:00Z"),
        fb("load_test_monsoon_sale", PROD, "AmazonEC2", "checkout", "2026-07-14", "2026-07-15", "expected",
           "Karthik Subramanian",
           f"that's us — planned k6 load test for the Monsoon Mega Sale (CAB-2291, deploy #{lt}). auto-teardown at "
           "20:00 UTC, one-off. not waste", "2026-07-14T13:05:00Z"),

        fb("staging_weekend_perf_env", STAGING, "AmazonEC2", "checkout", "2026-08-07", "2026-08-10", "waste",
           "Rohan Gupta",
           f"my bad 🙈 spun up the cart-v3 perf env friday evening (deploy #{perf}) and forgot to tear it down. "
           "killed it monday 10am IST. ~$700 down the drain", "2026-08-10T05:20:00Z"),
        fb("staging_weekend_perf_env", STAGING, "AmazonEC2", "checkout", "2026-08-07", "2026-08-10", "waste",
           "Daniel Okafor",
           "adding a ttl tag + nightly reaper for staging so perf envs can't outlive the weekend",
           "2026-08-10T07:45:00Z"),

        fb("image_service_egress_bug", PROD, "AmazonS3", "media", "2026-07-22", "2026-07-25", "investigating",
           "Fatima Sheikh",
           "S3 egress on acme-prod-media-assets is ~15x since wed afternoon and cloudfront bytes dropped at the "
           "same time. smells like the image-service deploy. @arjun can you look", "2026-07-23T06:10:00Z"),
        fb("image_service_egress_bug", PROD, "AmazonS3", "media", "2026-07-22", "2026-07-25", "waste", "Arjun Mehta",
           f"yeah it's my presigned URL change (#{egress}) — thumbnails were going straight from S3 and skipping "
           f"CloudFront. rolled back in #{revert} on sat morning. real bug, ~$3.5k. sorry folks",
           "2026-07-25T07:30:00Z"),

        fb("forgotten_gpu_instance", ML, "AmazonEC2", "ml-research", "2026-09-17", "2026-09-30", "investigating",
           "Priya Raman", "not the retrain, that's on the 1st. no idea whose p3.8xlarge this is, it has no Name tag",
           "2026-09-18T09:00:00Z"),
        fb("forgotten_gpu_instance", ML, "AmazonEC2", "ml-research", "2026-09-17", "2026-09-30", "waste",
           "Vikram Nair",
           "ugh that's mine, route-embedding experiment from the 17th, thought I terminated it 🤦 killing it now "
           "+ deleting the io2 volume and snapshots", "2026-09-30T05:30:00Z"),
    ]
    for i, item in enumerate(items, start=1):
        item["id"] = f"fb-{i:03d}"
    return [{"id": x.pop("id"), **x} for x in items]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    catalog, key, cat = build_catalog(rng)
    billing = Billing(baseline_rows(rng, catalog), cat)
    truth = plant_events(billing, key, rng)
    df = billing.frame()
    deploys = build_deploys(rng)
    feedback = build_feedback(deploys)

    args.out.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out / "billing.csv", index=False)
    deploys.to_csv(args.out / "deploys.csv", index=False)
    (args.out / "seed_feedback.json").write_text(json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "planted_events.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")

    monthly = df.assign(month=pd.to_datetime(df["date"]).dt.strftime("%Y-%m")).groupby("month")["cost_usd"].sum()
    print(f"billing.csv: {len(df):,} rows, ${df['cost_usd'].sum():,.0f} total")
    print("monthly spend:", ", ".join(f"{m} ${v:,.0f}" for m, v in monthly.items()))
    print(f"deploys.csv: {len(deploys)} deploys | seed_feedback.json: {len(feedback)} notes | "
          f"planted_events.json: {len(truth)} labelled anomalies")


if __name__ == "__main__":
    main()
