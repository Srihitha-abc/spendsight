"""Normalize uploaded billing and deploy exports into SpendSight's daily schema.

Supported billing formats (auto-detected from the header):
  spendsight          our own template (date, account, service, region, usage_type, resource_id, team_tag, cost_usd)
  aws_cur             AWS Cost & Usage Report, legacy (lineItem/UnblendedCost) and CUR 2.0 (line_item_unblended_cost)
  aws_cost_explorer   AWS Cost Explorer console CSV (wide: one column per service, "Service total" row)
  gcp                 GCP billing export (Service description, SKU description, Cost)
  azure               Azure cost details (MeterCategory, CostInBillingCurrency / PreTaxCost)
  focus               FinOps FOCUS (ChargePeriodStart, ServiceName, BilledCost)
  generic             anything else: the user maps columns by hand
Hourly and per-resource rows are aggregated to one row per day per (account, service, region, usage_type,
resource_id, team_tag). Bad rows are skipped and reported, never fatal.
"""
from __future__ import annotations

import io
import json
import re

import pandas as pd

TARGETS = ["date", "service", "cost_usd", "account", "region", "usage_type", "resource_id", "team_tag"]
REQUIRED = ["date", "service", "cost_usd"]
DEFAULTS = {"account": "default", "region": "unknown", "resource_id": "", "team_tag": "untagged"}
COLUMNS = ["date", "account", "service", "region", "usage_type", "resource_id", "team_tag", "cost_usd"]

FORMAT_LABELS = {
    "spendsight": "SpendSight template", "aws_cur": "AWS Cost & Usage Report (CUR)",
    "aws_cost_explorer": "AWS Cost Explorer export", "gcp": "Google Cloud billing export",
    "azure": "Azure cost details", "focus": "FinOps FOCUS", "generic": "Unrecognised format (map columns manually)",
}

# Candidate source columns per target, compared after lower-casing and removing non-alphanumerics.
CANDIDATES: dict[str, list[str]] = {
    "date": ["date", "usagedate", "lineitemusagestartdate", "usagestartdate", "usagestarttime", "chargeperiodstart",
             "timeperiodstart", "startdate", "day", "billingdate"],
    "service": ["service", "servicename", "productproductname", "productname", "lineitemproductcode", "productcode",
                "productservicecode", "servicedescription", "metercategory", "consumedservice"],
    "cost_usd": ["costusd", "cost", "lineitemunblendedcost", "unblendedcost", "lineitemnetunblendedcost", "billedcost",
                 "effectivecost", "costinbillingcurrency", "costinusd", "pretaxcost", "amount", "blendedcost"],
    "account": ["account", "accountid", "lineitemusageaccountid", "usageaccountid", "linkedaccount", "subaccountname",
                "subaccountid", "projectid", "projectname", "subscriptionname", "subscriptionid", "billingaccountid"],
    "region": ["region", "productregioncode", "productregion", "regioncode", "regionid", "regionname",
               "locationregion", "resourcelocation", "location"],
    "usage_type": ["usagetype", "lineitemusagetype", "skudescription", "metername", "meter", "metersubcategory",
                   "chargedescription", "lineitemoperation", "operation", "skuid"],
    "resource_id": ["resourceid", "lineitemresourceid", "resourcename", "instanceid", "resourceuri"],
    "team_tag": ["teamtag", "team", "resourcetagsuserteam", "resourcetagsteam", "tagteam", "labelsteam", "tagsteam",
                 "userteam", "owner"],
}
TAG_COLUMNS = ["tags", "resourcetags", "labels", "tag", "userlabels"]


def key(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).lower())


def read_csv(raw: bytes) -> pd.DataFrame:
    """Read an uploaded CSV as strings (so validation, not pandas, decides what's a bad value)."""
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise ValueError("The file is empty.")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=True,
                         on_bad_lines="skip")
    except pd.errors.ParserError as e:
        raise ValueError(f"Couldn't parse the CSV: {e}") from e
    if df.shape[1] < 2:
        raise ValueError("Expected a comma-separated file with a header row and at least 2 columns.")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def is_cost_explorer_wide(df: pd.DataFrame) -> bool:
    first = key(df.columns[0])
    money_cols = sum(1 for c in df.columns[1:] if str(c).strip().endswith("($)"))
    return first == "service" and money_cols >= 2


def detect_format(df: pd.DataFrame) -> str:
    keys = {key(c) for c in df.columns}
    if is_cost_explorer_wide(df) or {"timeperiodstart", "unblendedcost"} <= keys:
        return "aws_cost_explorer"
    if set(map(key, COLUMNS)) <= keys:
        return "spendsight"
    if any(k.startswith("lineitem") for k in keys):
        return "aws_cur"
    if {"chargeperiodstart", "billedcost"} <= keys or {"chargeperiodstart", "effectivecost"} <= keys:
        return "focus"
    if keys & {"metercategory", "costinbillingcurrency", "pretaxcost", "subscriptionid", "subscriptionname"}:
        return "azure"
    if {"servicedescription", "skudescription"} <= keys or ("projectid" in keys and "usagestartdate" in keys):
        return "gcp"
    return "generic"


def suggest_mapping(df: pd.DataFrame) -> dict[str, str | None]:
    by_key: dict[str, str] = {}
    for c in df.columns:
        by_key.setdefault(key(c), c)
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for target in TARGETS:
        mapping[target] = None
        for cand in CANDIDATES[target]:
            col = by_key.get(cand)
            if col and col not in used:
                mapping[target] = col
                used.add(col)
                break
    return mapping


def inspect(raw: bytes) -> dict:
    """First step of an upload: detect the format, propose a mapping, return a preview."""
    df = read_csv(raw)
    fmt = detect_format(df)
    wide = fmt == "aws_cost_explorer" and is_cost_explorer_wide(df)
    mapping = {t: None for t in TARGETS} if wide else suggest_mapping(df)
    return dict(format=fmt, format_label=FORMAT_LABELS[fmt], wide=wide, columns=list(df.columns), rows=len(df),
                mapping=mapping, required=REQUIRED, targets=TARGETS,
                missing_required=[] if wide else [t for t in REQUIRED if not mapping.get(t)],
                preview=df.head(10).to_dict("records"))


# ------------------------------------------------------------------------------------------ parsing
_MONEY = re.compile(r"[,$€£₹\s]|USD")


def parse_cost(s: pd.Series) -> pd.Series:
    txt = s.astype(str).str.strip()
    neg = txt.str.startswith("(") & txt.str.endswith(")")
    num = pd.to_numeric(txt.str.strip("()").str.replace(_MONEY, "", regex=True), errors="coerce")
    return num.where(~neg, -num)


def parse_date(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s.astype(str).str.strip(), errors="coerce", utc=True, format="mixed")
    return d.dt.strftime("%Y-%m-%d")


def team_from_tags(value: str) -> str | None:
    if not value:
        return None
    try:
        obj = json.loads(value)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if re.fullmatch(r"(user:)?(team|owner|squad)", str(k).lower()) and v:
                    return str(v)
    except (ValueError, TypeError):
        pass
    m = re.search(r"(?:team|owner|squad)[\"']?\s*[:=]\s*[\"']?([\w\-.]+)", value, re.I)
    return m.group(1) if m else None


def _unpivot_cost_explorer(df: pd.DataFrame) -> pd.DataFrame:
    first = df.columns[0]
    services = [c for c in df.columns[1:] if key(c) not in ("totalcosts", "total", "totalcost")]
    long = df.melt(id_vars=[first], value_vars=services, var_name="service", value_name="cost_usd")
    long = long.rename(columns={first: "date"})
    long = long[~long["date"].str.strip().str.lower().isin(["service total", "total", ""])]
    long["service"] = long["service"].str.replace(r"\s*\(\$\)\s*$", "", regex=True).str.strip()
    return long


def normalize(raw: bytes, mapping: dict[str, str | None] | None = None) -> tuple[pd.DataFrame, dict]:
    df = read_csv(raw)
    fmt = detect_format(df)
    wide = fmt == "aws_cost_explorer" and is_cost_explorer_wide(df)
    if wide:
        src = _unpivot_cost_explorer(df)
        mapping = {"date": "date", "service": "service", "cost_usd": "cost_usd"}
    else:
        mapping = {t: c for t, c in (mapping or suggest_mapping(df)).items() if c}
        missing = [t for t in REQUIRED if t not in mapping]
        if missing:
            raise ValueError(f"Map the required column(s): {', '.join(missing)}.")
        unknown = [c for c in mapping.values() if c not in df.columns]
        if unknown:
            raise ValueError(f"Column(s) not in file: {', '.join(unknown)}.")
        src = df

    out = pd.DataFrame(index=src.index)
    out["date"] = parse_date(src[mapping["date"]])
    out["service"] = src[mapping["service"]].astype(str).str.strip()
    out["cost_usd"] = parse_cost(src[mapping["cost_usd"]])
    defaulted = []
    for t in ("account", "region", "usage_type", "resource_id", "team_tag"):
        if t in mapping:
            out[t] = src[mapping[t]].astype(str).str.strip()
        else:
            defaulted.append(t)
            out[t] = out["service"] if t == "usage_type" else DEFAULTS[t]
    if "team_tag" in defaulted:
        tag_col = next((c for c in src.columns if key(c) in TAG_COLUMNS), None)
        if tag_col is not None:
            teams = src[tag_col].astype(str).map(team_from_tags)
            if teams.notna().any():
                out["team_tag"] = teams.fillna("untagged")
                defaulted.remove("team_tag")
    for t, v in DEFAULTS.items():  # blank optional cells get the same default
        out[t] = out[t].replace("", v) if v else out[t]

    reasons = pd.Series("", index=out.index)
    reasons[out["service"].isin(["", "nan", "None"])] = "missing service"
    reasons[out["cost_usd"].isna()] = "missing or non-numeric cost"
    reasons[out["date"].isna()] = "unparseable date"
    bad = reasons != ""
    # Row numbers match the file (header = row 1); unpivoted Cost Explorer cells have no single row.
    examples = [dict(row=None if wide else int(i) + 2, reason=reasons[i],
                     values={k: str(v) for k, v in src.loc[i].items()})
                for i in reasons[bad].index[:10]]
    good = out[~bad]
    daily = (good.groupby(["date", "account", "service", "region", "usage_type", "resource_id", "team_tag"],
                          as_index=False)["cost_usd"].sum())
    daily["cost_usd"] = daily["cost_usd"].round(4)
    daily = daily[COLUMNS].sort_values(["date", "account", "service"]).reset_index(drop=True)

    days = daily["date"].nunique()
    warnings = []
    if days and days < 15:
        warnings.append(f"Only {days} day(s) of data. Anomaly detection needs 14 days of baseline before it "
                        "can flag anything.")
    if (daily["cost_usd"] < 0).any():
        warnings.append("Some rows have negative cost (credits/refunds); they are kept and netted per day.")
    top = daily.groupby("service")["cost_usd"].sum().sort_values(ascending=False)
    report = dict(
        format=fmt, format_label=FORMAT_LABELS[fmt], rows_total=int(len(out)), rows_valid=int((~bad).sum()),
        rows_skipped=int(bad.sum()), skipped_by_reason=reasons[bad].value_counts().to_dict(), errors=examples,
        rows_daily=int(len(daily)), aggregated=bool(len(daily) < (~bad).sum()),
        date_from=daily["date"].min() if len(daily) else None, date_to=daily["date"].max() if len(daily) else None,
        days=int(days), accounts=sorted(daily["account"].unique().tolist()),
        services=[dict(service=s, cost_usd=round(float(c), 2)) for s, c in top.head(12).items()],
        service_count=int(top.size), total_spend=round(float(daily["cost_usd"].sum()), 2),
        defaulted_columns=defaulted, warnings=warnings,
    )
    if daily.empty:
        raise ValueError(f"No valid rows: {report['skipped_by_reason'] or 'the file has no data rows'}.")
    return daily, report


# ------------------------------------------------------------------------------------------ deploys
DEPLOY_CANDIDATES = {
    "date": ["date", "deployedat", "deploydate", "timestamp", "time", "createdat", "datetime", "finishedat"],
    "service": ["service", "app", "application", "component", "repo", "repository", "project"],
    "summary": ["summary", "description", "message", "title", "changes", "commitmessage", "notes"],
    "team": ["team", "ownerteam", "squad", "group"],
    "deploy_id": ["deployid", "id", "deploymentid", "deploy", "buildid", "releaseid", "version"],
    "author": ["author", "deployedby", "owner", "user", "committer", "actor"],
}


def normalize_deploys(raw: bytes) -> tuple[pd.DataFrame, dict]:
    df = read_csv(raw)
    by_key = {key(c): c for c in df.columns}
    m = {t: next((by_key[c] for c in cands if c in by_key), None) for t, cands in DEPLOY_CANDIDATES.items()}
    missing = [t for t in ("date", "service", "summary") if not m[t]]
    if missing:
        raise ValueError(f"deploys.csv needs columns for: {', '.join(missing)} "
                         f"(found: {', '.join(df.columns)}).")
    ts = pd.to_datetime(df[m["date"]].astype(str).str.strip(), errors="coerce", utc=True, format="mixed")
    if "time_utc" in df.columns:
        extra = pd.to_datetime(df[m["date"]].astype(str) + " " + df["time_utc"].astype(str), errors="coerce", utc=True,
                               format="mixed")
        ts = extra.fillna(ts)
    out = pd.DataFrame({
        "date": ts.dt.strftime("%Y-%m-%d"), "time_utc": ts.dt.strftime("%H:%M"),
        "service": df[m["service"]].astype(str).str.strip(),
        "team": df[m["team"]].astype(str).str.strip() if m["team"] else "unknown",
        "author": df[m["author"]].astype(str).str.strip() if m["author"] else "unknown",
        "summary": df[m["summary"]].astype(str).str.strip(),
    })
    ids = pd.to_numeric(df[m["deploy_id"]], errors="coerce") if m["deploy_id"] else pd.Series(float("nan"), index=df.index)
    out["deploy_id"] = ids.where(ids.notna(), pd.Series(range(1, len(df) + 1), index=df.index) + 100000).astype(int)
    bad = ts.isna() | out["summary"].eq("") | out["service"].eq("")
    good = out[~bad].sort_values(["date", "time_utc"]).reset_index(drop=True)
    good = good[["date", "time_utc", "service", "team", "deploy_id", "author", "summary"]]
    return good, dict(rows_total=int(len(out)), rows_valid=int(len(good)), rows_skipped=int(bad.sum()),
                      mapping=m, date_from=good["date"].min() if len(good) else None,
                      date_to=good["date"].max() if len(good) else None)


TEMPLATE = """date,account,service,region,usage_type,resource_id,team_tag,cost_usd
2026-07-01,prod,AmazonEC2,us-east-1,USE1-BoxUsage:m6i.xlarge,i-0abc123def4567890,checkout,110.59
2026-07-01,prod,AmazonS3,us-east-1,USE1-TimedStorage-ByteHrs,my-assets-bucket,media,42.10
2026-07-01,prod,AmazonRDS,us-east-1,USE1-InstanceUsage:db.r6g.large,arn:aws:rds:us-east-1:123456789012:db:orders,checkout,24.91
2026-07-02,prod,AmazonEC2,us-east-1,USE1-BoxUsage:m6i.xlarge,i-0abc123def4567890,checkout,110.59
"""
DEPLOY_TEMPLATE = """date,time_utc,service,team,deploy_id,author,summary
2026-07-01,14:05,image-service,media,421,Arjun Mehta,Serve thumbnails via S3 presigned URLs
"""
