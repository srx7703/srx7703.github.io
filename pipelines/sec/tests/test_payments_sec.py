"""Payments SEC universe, offline: 20-F / ifrs-full extraction, frames fallback, failure isolation, LTM."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest

from pipelines.sec import ingest, transform_payments
from pipelines.sec.config import UNIVERSES
from pipelines.sec.ingest import (
    SecUserAgentMissing,
    calendar_quarter,
    expected_latest_quarter,
    extract_facts,
    frames_fallback,
    frames_url,
    payments_user_agent,
)

FIX = Path(__file__).parent / "fixtures"
PAY = UNIVERSES["payments"]
TODAY = date(2026, 10, 4)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def _frame_fn(taxonomy, tag, unit, period):
    p = FIX / f"frames_{taxonomy}_{tag}_{unit}_{period}.json"
    return json.loads(p.read_text()) if p.exists() else None  # None == SEC 404 (no such frame yet)


# ---------------------------------------------------------------- extraction


def test_ifrs_20f_facts_are_extracted_in_reporting_currency():
    rows = extract_facts(_load("companyfacts_ifrs_20f.json"), "KLAR", "0002003292", PAY)
    rev = [r for r in rows if r["metric"] == "revenue"]
    assert {r["unit"] for r in rows} == {"USD"}  # USD chosen over the SEK duplicate
    assert {r["tag"] for r in rev} == {"ifrs-full:Revenue"}
    assert {r["form"] for r in rev} == {"20-F", "6-K"}  # F-1 comparatives are not periodic reports
    assert len(rev) == 5
    h1 = next(r for r in rev if r["start"] == "2025-01-01" and r["end"] == "2025-06-30")
    assert h1["val"] == 1_520_000_000 and h1["days"] == 180
    assert {r["metric"] for r in rows} == {"revenue", "net_income", "ocf", "capex"}  # instants skipped


def test_saas_universe_still_rejects_foreign_filers():
    assert extract_facts(_load("companyfacts_ifrs_20f.json"), "KLAR", "0002003292") == []


def test_saas_extraction_unchanged_for_us_gaap_filer():
    rows = extract_facts(_load("companyfacts_gaap_10q.json"), "X", "0001234567")
    assert {r["tag"] for r in rows} == {"Revenues", "NetIncomeLoss", "NetCashProvidedByUsedInOperatingActivities"}
    assert all(r["unit"] == "USD" for r in rows)
    pay = extract_facts(_load("companyfacts_gaap_10q.json"), "X", "0001234567", PAY)
    assert len(pay) == len(rows) and {r["tag"] for r in pay} >= {"us-gaap:Revenues"}


def test_reporting_currency_falls_back_to_most_common_non_usd():
    cf = {"facts": {"ifrs-full": {"Revenue": {"units": {"BRL": [{}, {}], "EUR": [{}]}}}}}
    assert ingest.reporting_currency(cf, PAY) == "BRL"


# ---------------------------------------------------------------- frames


def test_frames_url_and_periods():
    assert (
        frames_url("ifrs-full", "Revenue", "USD", "CY2026Q2")
        == "https://data.sec.gov/api/xbrl/frames/ifrs-full/Revenue/USD/CY2026Q2.json"
    )
    assert frames_url("us-gaap", "Cash", "USD", "CY2026Q2I").endswith("/CY2026Q2I.json")
    with pytest.raises(ValueError):
        frames_url("us-gaap", "Revenues", "USD", "2026Q2")


def test_calendar_alignment():
    assert expected_latest_quarter(TODAY) == (2026, 2)  # Q3 10-Qs are not due until mid-November
    assert expected_latest_quarter(date(2026, 8, 20)) == (2026, 2)
    assert expected_latest_quarter(date(2026, 8, 1)) == (2026, 1)
    assert calendar_quarter(date(2026, 1, 31)) == (2025, 4)  # a January fiscal quarter-end
    assert calendar_quarter(date(2026, 6, 27)) == (2026, 2)  # 52/53-week year


def test_frames_fallback_fills_the_lagging_quarter_and_records_it():
    rows = extract_facts(_load("companyfacts_gaap_10q.json"), "MQ", "0001234567", PAY)
    added, checks = frames_fallback(rows, "MQ", "0001234567", PAY, _frame_fn, TODAY)
    assert len(added) == 1
    a = added[0]
    assert (a["metric"], a["start"], a["end"], a["val"]) == ("revenue", "2026-04-01", "2026-06-30", 150e6)
    assert a["form"] == "frames" and a["frame"] == "CY2026Q2" and a["tag"] == "us-gaap:Revenues"
    used = [c for c in checks if "frames fallback used for MQ revenue CY2026Q2" in c["detail"]]
    assert len(used) == 1 and used[0]["status"] == "warn"
    # net income lags too but its frame does not exist yet: recorded, not fatal
    miss = [c for c in checks if "net_income" in c["detail"]]
    assert miss and miss[0]["status"] == "warn" and "missing in companyfacts and in frames" in miss[0]["detail"]


def test_frames_fallback_skips_semiannual_filers():
    rows = extract_facts(_load("companyfacts_ifrs_20f.json"), "KLAR", "0002003292", PAY)
    assert frames_fallback(rows, "KLAR", "0002003292", PAY, _frame_fn, TODAY) == ([], [])


def test_a_failing_frame_is_a_failed_check_not_a_crash():
    def boom(*_):
        raise httpx.ConnectError("blocked")

    rows = extract_facts(_load("companyfacts_gaap_10q.json"), "MQ", "0001234567", PAY)
    added, checks = frames_fallback(rows, "MQ", "0001234567", PAY, boom, TODAY)
    assert added == [] and checks and all(c["status"] == "fail" for c in checks)


# ---------------------------------------------------------------- user agent


def test_payments_requires_sec_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecUserAgentMissing):
        payments_user_agent()

    def no_network(*a, **k):
        raise AssertionError("network client built without a User-Agent")

    monkeypatch.setattr(ingest, "SecClient", no_network)
    with pytest.raises(SecUserAgentMissing):
        ingest.main(["--universe", "payments"])
    monkeypatch.setenv("SEC_USER_AGENT", "  ")
    with pytest.raises(SecUserAgentMissing):
        payments_user_agent()


def test_default_user_agent_has_no_email():
    assert "@" not in ingest.SEC_UA or "SEC_USER_AGENT" in __import__("os").environ


# ---------------------------------------------------------------- end to end (offline)


class FakeClient:
    """Stands in for SecClient: fixture files, one filer whose companyfacts request fails."""

    def tickers(self):
        return {v["ticker"]: (f"{v['cik_str']:010d}", v["title"]) for v in _load("company_tickers.json").values()}

    def companyfacts(self, cik):
        if cik == "0001234567":
            return _load("companyfacts_gaap_10q.json")
        if cik == "0002003292":
            return _load("companyfacts_ifrs_20f.json")
        raise httpx.ConnectError(f"companyfacts CIK{cik} unreachable")

    def frame(self, taxonomy, tag, unit, period):
        return _frame_fn(taxonomy, tag, unit, period)


@pytest.fixture
def built(tmp_path):
    snap = ingest.run(PAY, FakeClient(), ["MQ", "KLAR", "AFRM", "PYPL"], today=TODAY, snap_dir=tmp_path / "snap")
    result = transform_payments.build(snap_dir=tmp_path / "snap", marts_dir=tmp_path / "marts")
    return snap, result, tmp_path


def test_one_failing_company_does_not_lose_the_others(built):
    snap, _, tmp = built
    assert sorted(snap["companies"]) == ["KLAR", "MQ"]
    assert any(f.startswith("AFRM:") for f in snap["failed"])  # companyfacts error
    assert any(f.startswith("PYPL:") and "not in SEC ticker file" in f for f in snap["failed"])
    assert sorted(p.name for p in (tmp / "snap" / "sec_payments" / "facts").iterdir()) == ["KLAR.parquet", "MQ.parquet"]
    assert snap["companies"]["KLAR"]["foreign_private_issuer"] is True
    assert snap["companies"]["KLAR"]["taxonomy"] == ["ifrs-full"]
    assert snap["companies"]["MQ"]["frames_rows"] == 1
    assert not (tmp / "snap" / "sec").exists()  # never touches the SaaS snapshot directory


def test_payments_marts(built):
    _, result, tmp = built
    out = tmp / "marts" / "payments"
    latest = pl.read_parquet(out / "sec_latest.parquet")
    by = {r["ticker"]: r for r in latest.iter_rows(named=True)}
    assert set(by) == {"KLAR", "MQ"}

    mq = by["MQ"]  # Q3'25 120 + Q4'25 (460-330) 130 + Q1'26 140 + Q2'26 150 from frames
    assert (mq["period_end"], mq["basis"], mq["revenue"]) == ("2026-06-30", "4Q", 540e6)
    assert mq["latest_from_frames"] is True and mq["company"] == "Marqeta"

    kl = by["KLAR"]  # FY25 3.50 + H1'26 2.00 - H1'25 1.52 ; prior LTM FY24 2.811 + 1.52 - 1.30
    assert (kl["period_end"], kl["basis"], kl["currency"]) == ("2026-06-30", "FY+YTD", "USD")
    assert kl["revenue"] == pytest.approx(3.98e9)
    assert kl["rev_growth"] == pytest.approx(3.98 / 3.031 - 1)
    assert kl["net_margin"] == pytest.approx((-250 - 90 + 150) / 3980)
    assert kl["fcf_margin"] is None  # no LTM cash flow at the half-year: not silently stale

    q = pl.read_parquet(out / "sec_quarterly.parquet")
    assert q.filter(pl.col("ticker") == "KLAR").height == 0  # halves never enter the quarterly table
    cov = json.loads((out / "sec_coverage.json").read_text())
    details = " | ".join(c["detail"] for c in result["checks"])
    assert "frames fallback used for MQ revenue CY2026Q2" in details
    assert "latest quarter taken from SEC frames for MQ" in details
    assert any(c["name"] == "Company fetch" and c["status"] == "fail" for c in cov["checks"])
    assert cov["companies"]["KLAR"]["ltm_basis"]["revenue"] == "FY+YTD"


def test_ltm_series_prefers_quarters_then_fy_then_ytd():
    spans = {
        ("2024-01-01", "2024-12-31"): 400.0,
        ("2025-01-01", "2025-12-31"): 500.0,
        ("2025-01-01", "2025-06-30"): 240.0,
        ("2026-01-01", "2026-06-30"): 300.0,
    }
    lt = transform_payments.ltm_series(spans)
    assert lt["2024-12-31"] == (400.0, "FY")
    assert lt["2025-12-31"] == (500.0, "FY")
    assert lt["2026-06-30"] == (560.0, "FY+YTD")
    assert "2025-06-30" not in lt  # no FY 2024 H1 to difference against


# ---------------------------------------------------------------- review fixes


def _row(metric, start, end, val=1.0, form="10-Q"):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return {
        "metric": metric,
        "tag": "us-gaap:Revenues",
        "unit": "USD",
        "start": start,
        "end": end,
        "days": (e - s).days,
        "val": val,
        "form": form,
        "filed": end,
    }


def test_fiscal_year_span_covers_its_last_quarter():
    """A June FY-end filer (BILL-like) whose 10-K for FY ending 2026-06-30 is on file has no gap at CY2026Q2."""
    rows = [
        _row("revenue", "2026-01-01", "2026-03-31"),
        _row("revenue", "2025-07-01", "2026-06-30", form="10-K"),
    ]
    gaps, latest = ingest.missing_quarters(rows, "revenue", TODAY)
    assert gaps == [] and latest["end"] == "2026-03-31"  # tag/unit still come from the 3-month fact

    def never(*_):
        raise AssertionError("frames requested for a quarter the FY span already covers")

    assert frames_fallback(rows, "BILL", "0001786352", PAY, never, TODAY) == ([], [])


class _LookupDown:
    def tickers(self):
        raise httpx.ConnectError("company_tickers.json unreachable")

    def companyfacts(self, cik):
        raise httpx.ConnectError("unreachable")

    def frame(self, *a):
        return None


def test_saas_lookup_failure_raises_before_writing(tmp_path):
    saas = UNIVERSES["saas"]
    with pytest.raises(httpx.ConnectError):
        ingest.run(saas, _LookupDown(), list(saas.tickers)[:2], today=TODAY, snap_dir=tmp_path)
    assert not any(tmp_path.rglob("*"))


def test_all_failed_run_keeps_last_good_snapshot(tmp_path):
    snap = tmp_path / PAY.snap_subdir
    snap.mkdir(parents=True)
    (snap / "companies.json").write_text('{"companies": {"MQ": {}}}')
    out = ingest.run(PAY, _LookupDown(), ["MQ", "KLAR"], today=TODAY, snap_dir=tmp_path)
    assert out["companies"] == {} and len(out["failed"]) == 2
    assert (snap / "companies.json").read_text() == '{"companies": {"MQ": {}}}'
    assert not (snap / "refresh_log.jsonl").exists()
