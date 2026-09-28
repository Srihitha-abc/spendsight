import pytest

from app.ingest.normalize import TEMPLATE, detect_format, inspect, normalize, normalize_deploys, read_csv


def b(text: str) -> bytes:
    return text.strip().encode()


CUR_HOURLY = b("""
identity/LineItemId,lineItem/UsageStartDate,lineItem/UsageAccountId,lineItem/ProductCode,product/region,lineItem/UsageType,lineItem/ResourceId,lineItem/UnblendedCost,resourceTags/user:team
a,2026-07-01T00:00:00Z,111122223333,AmazonEC2,us-east-1,BoxUsage:m6i.xlarge,i-01,0.192,checkout
b,2026-07-01T01:00:00Z,111122223333,AmazonEC2,us-east-1,BoxUsage:m6i.xlarge,i-01,0.192,checkout
c,2026-07-01T02:00:00Z,111122223333,AmazonEC2,us-east-1,BoxUsage:m6i.xlarge,i-01,0.192,checkout
d,2026-07-02T00:00:00Z,111122223333,AmazonS3,us-east-1,TimedStorage-ByteHrs,bucket,1.5,
""")

CE_WIDE = b("""
"Service","EC2-Instances($)","S3($)","Total costs($)"
"Service total","30","6","36"
"2026-07-01","10","2","12"
"2026-07-02","10","2","12"
"2026-07-03","10","N/A","10"
""")


def test_detects_formats():
    assert detect_format(read_csv(CUR_HOURLY)) == "aws_cur"
    assert detect_format(read_csv(CE_WIDE)) == "aws_cost_explorer"
    assert detect_format(read_csv(b(TEMPLATE))) == "spendsight"
    assert detect_format(read_csv(b("Usage start date,Service description,SKU description,Project ID,Cost ($)\n"
                                    "2026-07-01,Compute Engine,N1 core,shop-prod,4.2"))) == "gcp"
    assert detect_format(read_csv(b("Date,SubscriptionName,MeterCategory,ResourceLocation,CostInBillingCurrency\n"
                                    "07/01/2026,prod,Virtual Machines,eastus,12.5"))) == "azure"
    assert detect_format(read_csv(b("ChargePeriodStart,ServiceName,SubAccountId,RegionId,BilledCost\n"
                                    "2026-07-01T00:00:00Z,Amazon EC2,123,us-east-1,5"))) == "focus"


def test_cur_hourly_rows_aggregate_to_daily_and_map_tags():
    daily, rep = normalize(CUR_HOURLY)
    ec2 = daily[daily["service"] == "AmazonEC2"].iloc[0]
    assert ec2["cost_usd"] == pytest.approx(0.576) and ec2["team_tag"] == "checkout"
    assert rep["aggregated"] and rep["rows_daily"] == 2
    assert daily[daily["service"] == "AmazonS3"].iloc[0]["team_tag"] == "untagged"  # blank tag -> default


def test_cost_explorer_wide_is_unpivoted_and_bad_cells_reported():
    daily, rep = normalize(CE_WIDE)
    assert set(daily["service"]) == {"EC2-Instances", "S3"}  # "Total costs" and "Service total" dropped
    assert rep["rows_skipped"] == 1 and rep["skipped_by_reason"] == {"missing or non-numeric cost": 1}
    assert rep["total_spend"] == pytest.approx(34)
    assert "account" in rep["defaulted_columns"] and daily["account"].eq("default").all()


def test_bad_rows_are_skipped_with_row_numbers():
    raw = b("""
date,service,cost_usd
2026-07-01,EC2,"$1,200.50"
not-a-date,EC2,5
2026-07-02,,5
2026-07-03,EC2,abc
2026-07-04,EC2,(3.00)
""")
    daily, rep = normalize(raw)
    assert rep["rows_valid"] == 2 and rep["rows_skipped"] == 3
    assert [e["row"] for e in rep["errors"]] == [3, 4, 5]
    assert daily["cost_usd"].tolist() == [pytest.approx(1200.5), pytest.approx(-3.0)]  # (3.00) is a credit
    assert any("negative" in w for w in rep["warnings"]) and any("14 days" in w for w in rep["warnings"])


def test_team_from_tags_column_when_unmapped():
    raw = b('Date,MeterCategory,Cost,Tags\n2026-07-01,Storage,3,"{""team"": ""payments"", ""env"": ""prod""}"')
    daily, _ = normalize(raw)
    assert daily.iloc[0]["team_tag"] == "payments"


def test_missing_required_mapping_is_a_clear_error():
    with pytest.raises(ValueError, match="cost_usd"):
        normalize(b("date,service,notes\n2026-07-01,EC2,hi"))
    info = inspect(b("date,service,notes\n2026-07-01,EC2,hi"))
    assert info["missing_required"] == ["cost_usd"] and len(info["preview"]) == 1


def test_manual_mapping_and_empty_file():
    daily, _ = normalize(b("day,thing,usd\n2026-07-01,EC2,4"), {"date": "day", "service": "thing", "cost_usd": "usd"})
    assert daily.iloc[0]["cost_usd"] == 4
    with pytest.raises(ValueError, match="empty"):
        normalize(b(""))


def test_deploys_with_other_column_names():
    raw = b("deployed_at,app,owner,description,id\n2026-07-22T14:05:00Z,image-service,Arjun,presigned urls,421\n"
            "garbage,app,x,y,1")
    d, rep = normalize_deploys(raw)
    assert rep["rows_valid"] == 1 and d.iloc[0]["time_utc"] == "14:05" and d.iloc[0]["deploy_id"] == 421
    with pytest.raises(ValueError, match="summary"):
        normalize_deploys(b("date,service\n2026-07-01,a"))
