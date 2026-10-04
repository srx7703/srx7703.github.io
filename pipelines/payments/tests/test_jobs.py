"""Unit tests for pipelines.payments.jobs, offline.

fixtures/jobs/ copies the response shapes of boards-api.greenhouse.io/v1/boards/{token}/jobs (one role posted
per country under one internal_job_id, a "Country, Remote" location) and api.ashbyhq.com/posting-api/job-board
(with a description field the pipeline must drop, and an unlisted posting). Descriptions there are placeholder
text, never real job copy.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pandera.errors
import polars as pl
import pytest

from pipelines.payments import config
from pipelines.payments import jobs as j

FIX = Path(__file__).parent / "fixtures" / "jobs"
DAY = date(2026, 10, 4)


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# --- classifier -----------------------------------------------------------------------------


@pytest.mark.parametrize("title,expected", [
    ("Senior Software Engineer, Fraud", "engineering"),
    ("Engineering Manager - Risk Platform", "engineering"),
    ("A.I. Engineering Intern ", "engineering"),
    ("Data Scientist, Credit", "engineering"),
    ("Staff Machine Learning Engineer", "engineering"),
    ("Security Engineer", "engineering"),
    ("Risk Analyst", "risk/compliance"),
    ("Financial Crimes Compliance Manager", "risk/compliance"),
    ("AML Investigator", "risk/compliance"),
    ("Credit Underwriter", "risk/compliance"),
    ("Fraud Operations Specialist", "risk/compliance"),
    ("Account Executive - Enterprise", "sales/gtm"),
    ("Solutions Engineer", "sales/gtm"),
    ("Sales Engineer, EMEA", "sales/gtm"),
    ("Product Marketing Manager", "sales/gtm"),
    ("Partnerships Lead", "sales/gtm"),
    ("Customer Success Manager", "sales/gtm"),
    ("Business Development Representative", "sales/gtm"),
    ("Product Manager, Payments", "product/design"),
    ("Product Designer", "product/design"),
    ("UX Researcher", "product/design"),
    ("Customer Support Specialist", "operations/support"),
    ("Support Engineer", "operations/support"),
    ("Disputes Analyst", "operations/support"),
    ("Chief of Staff to the CEO", "operations/support"),
    ("Accountant", "finance/legal"),
    ("Senior Counsel, Compliance", "finance/legal"),
    ("Tax Manager", "finance/legal"),
    ("FP&A Analyst", "finance/legal"),
    ("Revenue Accountant", "finance/legal"),
    ("Recruiter", "other"),
    ("Executive Assistant", "other"),
])
def test_classifier(title, expected):
    assert j.classify(title) == expected


def test_classifier_role_part_beats_team_part():
    # the team ("Compliance") would say risk, but the role decides
    assert j.classify("Software Engineer, Compliance") == "engineering"
    assert j.classify("Compliance Analyst, Engineering Org") == "risk/compliance"


def test_classifier_falls_back_to_team_then_department():
    assert j.classify("Strategic Initiatives", "All Departments", "Business Operations") == "operations/support"
    assert j.classify("Member of Technical Staff") == "other"
    assert j.classify("Member of Technical Staff", "Engineering") == "engineering"


def test_classifier_is_deterministic_and_total():
    titles = ["", "   ", "Ninja", "Software Engineer"]
    assert [j.classify(t) for t in titles] == [j.classify(t) for t in titles]
    assert all(j.classify(t) in j.FUNCTIONS for t in titles)


def test_account_executive_is_not_accounting():
    assert j.classify("Account Executive") == "sales/gtm"
    assert j.classify("Accounting Manager") == "finance/legal"


# --- location -------------------------------------------------------------------------------


@pytest.mark.parametrize("loc,kw,expected", [
    ("Mexico, Remote", {}, "remote"),
    ("Remote - US", {}, "remote"),
    ("New York", {"workplace_type": "Remote"}, "remote"),
    ("Minneapolis, MN", {}, "US"),
    ("San Francisco, CA; London, UK", {}, "US"),
    ("Washington, DC", {}, "US"),
    ("Minneapolis, Minnesota, United States", {}, "US"),
    ("San Francisco HQ", {}, "US"),
    ("Toronto, ON, Canada", {}, "non-US"),
    ("Vancouver, British Columbia, Canada", {}, "non-US"),
    ("London, UK", {}, "non-US"),
    ("Latin America", {}, "non-US"),
    ("Dublin", {}, "non-US"),
    ("Singapore", {}, "non-US"),
    ("London", {"country": "United Kingdom"}, "non-US"),
    ("New York", {"country": "United States"}, "US"),
    ("", {}, "unknown"),
    (None, {}, "unknown"),
])
def test_location_bucket(loc, kw, expected):
    assert j.location_bucket(loc, **kw) == expected


# --- parse and count ------------------------------------------------------------------------


def test_greenhouse_counts_and_openings():
    postings, meta = j.parse_greenhouse(_load("greenhouse_sezzle.json"))
    assert meta == {"total": 9, "employers": ["Sezzle"]}
    df = j.count(postings, "greenhouse", "sezzle", DAY)
    cell = df.filter(pl.col("function") == "finance/legal").sort("location_bucket")
    # one accountant requisition posted in three countries: 2 remote + 1 non-US
    assert cell.select("location_bucket", "postings", "openings").rows() == [("non-US", 1, 1), ("remote", 2, 1)]
    assert df["postings"].sum() == 9
    assert df.filter(pl.col("location_bucket") == "unknown")["function"].to_list() == ["operations/support"]
    assert df.filter(pl.col("function") == "product/design")["location_bucket"].to_list() == ["non-US"]


def test_ashby_drops_unlisted_and_uses_structured_fields():
    postings, _ = j.parse_ashby(_load("ashby_plaid.json"))
    assert len(postings) == 4
    df = j.count(postings, "ashby", "plaid", DAY)
    got = {(r["function"], r["location_bucket"]): r["postings"] for r in df.iter_rows(named=True)}
    assert got == {("operations/support", "US"): 1, ("engineering", "US"): 1,
                   ("risk/compliance", "non-US"): 1, ("sales/gtm", "remote"): 1}


def test_no_text_survives_to_the_persisted_table():
    postings, _ = j.parse_ashby(_load("ashby_plaid.json"))
    assert all(set(p) == set(j.POSTING_FIELDS) for p in postings)
    df = j.count(postings, "ashby", "plaid", DAY)
    assert df.columns == list(j.COUNT_DTYPES)
    dumped = df.write_csv()
    assert "FIXTURE DESCRIPTION" not in dumped and "Strategic Initiatives" not in dumped
    with pytest.raises(pandera.errors.SchemaError):
        j.COUNT_SCHEMA.validate(df.with_columns(pl.lit("Software Engineer").alias("title")))


def test_malformed_payloads_raise():
    with pytest.raises(ValueError):
        j.parse_greenhouse({"error": "not found"})
    with pytest.raises(ValueError):
        j.parse_ashby([])


def test_boards_follow_config_and_refuse_excluded_tokens(monkeypatch):
    got = j.boards()
    assert len(got) == sum(len(v) for v in config.ATS_BOARDS.values())
    assert ("greenhouse", "wise") not in got
    monkeypatch.setitem(j.ATS_BOARDS, "greenhouse", ["stripe", "wise"])
    with pytest.raises(ValueError, match="excluded"):
        j.boards()


# --- fetch (isolation) ----------------------------------------------------------------------


class FakeClient:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.urls.append(path)
        v = self.routes.get(path)
        if isinstance(v, Exception):
            raise v
        if v is None:
            req = httpx.Request("GET", path)
            raise httpx.HTTPStatusError("HTTP 404", request=req, response=httpx.Response(404, request=req))
        return v


def test_one_failing_board_does_not_lose_the_others():
    gh = j.GREENHOUSE_URL.format
    ash = j.ASHBY_URL.format
    client = FakeClient({
        gh(token="sezzle"): _load("greenhouse_sezzle.json"),
        ash(token="plaid"): _load("ashby_plaid.json"),
        ash(token="ramp"): httpx.ConnectError("reset"),
        gh(token="toast"): _load("greenhouse_other_owner.json"),
        gh(token="block"): {"jobs": [], "meta": {"total": 0}},
    })
    only = [("greenhouse", "sezzle"), ("ashby", "plaid"), ("ashby", "ramp"), ("greenhouse", "stripe"),
            ("greenhouse", "toast"), ("greenhouse", "block")]
    res = j.fetch_all(client, snapshot_date=DAY, only=only)
    assert res.failed == ["ashby/ramp", "greenhouse/stripe"]
    assert set(res.counts["board"].unique()) == {"sezzle", "plaid", "toast"}
    by = {c["name"]: c for c in res.checks}
    assert by["Job boards"]["status"] == "warn" and "4 of 6" in by["Job boards"]["detail"]
    assert by["Job board fetch: ashby/ramp"]["status"] == "fail"
    assert by["Job board owner: greenhouse/sezzle"]["status"] == "pass"
    assert by["Job board owner: greenhouse/toast"]["status"] == "warn"  # company_name says Wise Ltd
    assert by["Job board size: greenhouse/block"]["status"] == "warn"
    j.COUNT_SCHEMA.validate(res.counts)


def test_run_writes_snapshot_and_mart_only_counts(tmp_path, monkeypatch):
    routes = {j.GREENHOUSE_URL.format(token=t): _load("greenhouse_sezzle.json")
              for t in config.ATS_BOARDS["greenhouse"]}
    routes.update({j.ASHBY_URL.format(token=t): _load("ashby_plaid.json") for t in config.ATS_BOARDS["ashby"]})
    counts, checks = j.run(FakeClient(routes), snap_root=tmp_path / "snap", out_dir=tmp_path / "marts")
    snaps = list((tmp_path / "snap" / "payments" / "jobs").glob("*/*.parquet"))
    assert len(snaps) == 1
    mart = pl.read_parquet(tmp_path / "marts" / "jobs_counts.parquet")
    assert mart.columns == list(j.COUNT_DTYPES)
    assert mart.height == counts.height
    assert next(c for c in checks if c["name"] == "Job boards")["status"] == "pass"
    for f in [*snaps, tmp_path / "marts" / "jobs_counts.parquet"]:
        assert b"FIXTURE DESCRIPTION" not in f.read_bytes()


def test_build_mart_keeps_latest_run_per_board_and_day(tmp_path):
    base = j.snapshot_dir(tmp_path)
    early = j.count([{"req": "1", "function": "engineering", "location_bucket": "US"}], "ashby", "ramp", DAY)
    late = j.count([{"req": "1", "function": "engineering", "location_bucket": "US"},
                    {"req": "2", "function": "engineering", "location_bucket": "US"}], "ashby", "ramp", DAY)
    from pipelines.common.storage import write_parquet
    write_parquet(early, base / DAY.isoformat() / "0600.parquet")
    write_parquet(late, base / DAY.isoformat() / "1800.parquet")
    mart = j.build_mart(tmp_path)
    assert mart["postings"].to_list() == [2]
