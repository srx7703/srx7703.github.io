"""Parse tests for the Yahoo earningsTrend consensus. No network: inline payload fixtures only.

Fixtures keep the real shape of the endpoint, including the ``{"raw": ..., "fmt": ...}`` wrappers
that survive ``formatted=false`` and the empty ``{}`` blocks Yahoo emits for missing fields.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import polars as pl
import pytest

from pipelines.common.storage import read_json_gz, run_stamp, utc_now
from pipelines.valuation import estimates_yf as yf
from pipelines.valuation.config import BY_TICKER
from pipelines.valuation.estimates_yf import (
    _minus_one_year,
    _parse_date,
    _raw,
    archive_payload,
    expected_fy_end,
    extract_result,
    fiscal_periods,
    fy_label,
    parse_trend,
    period_keys,
    price_of,
    resolve_currency,
    trend_of,
    yahoo_companies,
)
from pipelines.valuation.schema import (
    ESTIMATES_SCHEMA,
    VINTAGES_SCHEMA,
    estimates_frame,
    vintages_frame,
)

SNAP = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
STAMP = "2026-09-16T18:30:00+00:00"

# COHR, fiscal year ending 30 June: the shape verified against the live endpoint.
COHR_TREND = [
    {
        "period": "0q",
        "endDate": "2026-09-30",
        "earningsEstimate": {"avg": {"raw": 2.11}, "numberOfAnalysts": {"raw": 18}},
        "epsTrend": {"current": {"raw": 2.11}, "7daysAgo": {"raw": 2.09}},
    },
    {"period": "+1q", "endDate": "2026-12-31", "earningsEstimate": {"avg": {"raw": 2.35}}},
    {
        "period": "0y",
        "endDate": "2027-06-30",
        "earningsEstimate": {
            "avg": {"raw": 9.41634, "fmt": "9.42"},
            "low": {"raw": 8.26},
            "high": {"raw": 10.26},
            "numberOfAnalysts": {"raw": 22},
            "yearAgoEps": {"raw": 5.61},
            "growth": {"raw": 0.678},
        },
        "epsTrend": {
            "current": {"raw": 9.41634},
            "7daysAgo": {"raw": 9.41634},
            "30daysAgo": {"raw": 9.31178},
            "60daysAgo": {"raw": 8.28435},
            "90daysAgo": {"raw": 8.17378},
        },
        "revenueEstimate": {"avg": {"raw": 6.1e9}, "numberOfAnalysts": {"raw": 20}},
    },
    {
        "period": "+1y",
        "endDate": "2028-06-30",
        "earningsEstimate": {
            "avg": {"raw": 11.02},
            "low": {"raw": 9.1},
            "high": {"raw": 13.0},
            "numberOfAnalysts": {"raw": 21},
        },
        "epsTrend": {
            "current": {"raw": 11.02},
            "7daysAgo": {"raw": 11.0},
            "30daysAgo": {"raw": 10.8},
            "60daysAgo": {},  # Yahoo emits an empty block rather than omitting the key
            "90daysAgo": {"raw": 10.1},
        },
    },
]

# QuantumScape, plain December year end, pre-profit so the estimates are negative.
QS_TREND = [
    {
        "period": "0y",
        "endDate": "2026-12-31",
        "earningsEstimate": {
            "avg": {"raw": -0.83},
            "low": {"raw": -0.95},
            "high": {"raw": -0.71},
            "numberOfAnalysts": {"raw": 9},
            "yearAgoEps": {"raw": -0.94},
        },
        "epsTrend": {
            "current": {"raw": -0.83},
            "7daysAgo": {"raw": -0.83},
            "30daysAgo": {"raw": -0.85},
            "60daysAgo": {"raw": -0.86},
            "90daysAgo": {"raw": -0.88},
        },
    },
    {
        "period": "+1y",
        "endDate": "2027-12-31",
        "earningsEstimate": {"avg": {"raw": -0.72}, "numberOfAnalysts": {"raw": 8}},
        "epsTrend": {"current": {"raw": -0.72}},
    },
]

# CATL's H line: quoted in HKD, forecast in CNY, and with no yearAgoEps at all.
CATL_H_TREND = [
    {
        "period": "0y",
        "endDate": "2026-12-31",
        "earningsEstimate": {
            "avg": {"raw": 20.93},
            "low": {"raw": 18.4},
            "high": {"raw": 23.1},
            "numberOfAnalysts": {"raw": 31},
        },
        "epsTrend": {"current": {"raw": 20.93}, "30daysAgo": {"raw": 20.5}},
    },
]
CATL_H_PRICE = {"currency": "HKD", "financialCurrency": "CNY", "regularMarketPrice": {"raw": 412.0}}


def _rows(ticker: str, trend: list[dict], **kw):
    return parse_trend(ticker, BY_TICKER[ticker], trend, SNAP, **kw)


# --- _raw and the other payload helpers ----------------------------------------------
@pytest.mark.parametrize(
    ("node", "key", "want"),
    [
        ({"avg": {"raw": 9.41634, "fmt": "9.42"}}, "avg", 9.41634),
        ({"avg": {"raw": 0.0}}, "avg", 0.0),
        ({"avg": 9.5}, "avg", 9.5),  # unwrapped scalar
        ({"avg": {"raw": "9.5"}}, "avg", 9.5),  # numeric string
        ({"avg": {}}, "avg", None),  # empty block
        ({"avg": {"fmt": "9.42"}}, "avg", None),  # fmt without raw is not a number
        ({"avg": {"raw": None}}, "avg", None),
        ({}, "avg", None),  # missing key
        (None, "avg", None),  # missing block entirely
        ({"avg": "n/a"}, "avg", None),
        ({"avg": True}, "avg", None),  # a bool is not a figure
        ({"avg": {"raw": float("nan")}}, "avg", None),
    ],
)
def test_raw_tolerates_every_observed_shape(node, key, want):
    got = _raw(node, key)
    if want is None:
        assert got is None
    else:
        assert got == want


def test_parse_date_accepts_iso_and_epoch():
    assert _parse_date("2027-06-30") == date(2027, 6, 30)
    assert _parse_date({"raw": 1814313600}) == date(2027, 6, 30)  # epoch seconds fallback
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("not-a-date") is None


def test_minus_one_year_handles_leap_day():
    assert _minus_one_year(date(2027, 6, 30)) == date(2026, 6, 30)
    assert _minus_one_year(date(2028, 2, 29)) == date(2027, 2, 28)


# --- fiscal-year derivation ------------------------------------------------------------
def test_fy_label_uses_the_year_the_fiscal_year_ends_in():
    assert fy_label(date(2027, 6, 30)) == "FY2027"  # COHR's year in progress today
    assert fy_label(date(2026, 12, 31)) == "FY2026"
    assert fy_label(date(2027, 1, 31)) == "FY2027"  # Semtech's late-January year end


@pytest.mark.parametrize(
    ("month", "want"),
    [
        (6, date(2027, 6, 30)),  # COHR: June 2026 is already past
        (3, date(2027, 3, 31)),  # 5802.T
        (10, date(2026, 10, 31)),  # AVGO: still ahead of 16 Sep
        (9, date(2026, 9, 30)),  # MTSI: two weeks ahead
        (12, date(2026, 12, 31)),
        (1, date(2027, 1, 31)),  # SMTC
        (4, date(2027, 4, 30)),  # CRDO
        (2, date(2027, 2, 28)),  # month end, not day 30
    ],
)
def test_expected_fy_end_is_the_next_one_strictly_after_today(month, want):
    assert expected_fy_end(month, date(2026, 9, 16)) == want


def test_expected_fy_end_excludes_today_itself():
    assert expected_fy_end(9, date(2026, 9, 30)) == date(2027, 9, 30)


# --- COHR, the June year end ------------------------------------------------------------
def test_cohr_estimate_rows():
    est, _, checks = _rows("COHR", COHR_TREND)
    assert [(r["period_end"], r["mark"]) for r in est] == [
        ("2027-06-30", "E"),
        ("2026-06-30", "A"),
        ("2028-06-30", "E"),
    ]
    current = est[0]
    assert current == {
        "snapshot_ts": STAMP,
        "ticker": "COHR",
        "source": "yahoo",
        "period_end": "2027-06-30",
        "fy_label": "FY2027",
        "mark": "E",
        "eps_avg": 9.41634,
        "eps_low": 8.26,
        "eps_high": 10.26,
        "n_analysts": 22,
        "currency": "USD",
        "net_profit_avg": None,
    }
    assert isinstance(current["n_analysts"], int)
    assert [c for c in checks if c["status"] != "pass"] == []


def test_cohr_quarters_are_ignored():
    est, vin, _ = _rows("COHR", COHR_TREND)
    assert "2026-09-30" not in {r["period_end"] for r in est} | {r["period_end"] for r in vin}


def test_cohr_prior_year_actual():
    est, _, _ = _rows("COHR", COHR_TREND)
    actual = next(r for r in est if r["mark"] == "A")
    assert actual["period_end"] == "2026-06-30"  # 0y endDate minus one year
    assert actual["fy_label"] == "FY2026"
    assert actual["eps_avg"] == 5.61  # yearAgoEps
    assert actual["n_analysts"] is None
    assert actual["eps_low"] is None and actual["eps_high"] is None
    assert actual["currency"] == "USD"


def test_cohr_vintages():
    _, vin, _ = _rows("COHR", COHR_TREND)
    current_fy = [r for r in vin if r["period_end"] == "2027-06-30"]
    assert [(r["as_of"], r["eps_avg"]) for r in current_fy] == [
        ("2026-09-16", 9.41634),
        ("2026-09-09", 9.41634),
        ("2026-08-17", 9.31178),
        ("2026-07-18", 8.28435),
        ("2026-06-18", 8.17378),
    ]
    assert {r["origin"] for r in current_fy} == {"yahoo_trend"}
    assert {r["source"] for r in current_fy} == {"yahoo"}
    assert {r["currency"] for r in current_fy} == {"USD"}
    assert {r["n_analysts"] for r in current_fy} == {22}  # current count repeated; Yahoo has no history
    assert {r["snapshot_ts"] for r in current_fy} == {STAMP}


def test_vintage_as_of_follows_the_snapshot_not_a_hardcoded_date():
    _, vin, _ = parse_trend("COHR", BY_TICKER["COHR"], COHR_TREND, datetime(2027, 1, 4, 9, 0, tzinfo=UTC))
    current_fy = [r for r in vin if r["period_end"] == "2027-06-30"]
    assert [r["as_of"] for r in current_fy] == [
        "2027-01-04",
        "2026-12-28",
        "2026-12-05",
        "2026-11-05",
        "2026-10-06",
    ]


def test_empty_vintage_block_is_skipped_not_nulled():
    _, vin, _ = _rows("COHR", COHR_TREND)
    next_fy = [r for r in vin if r["period_end"] == "2028-06-30"]
    assert [r["as_of"] for r in next_fy] == ["2026-09-16", "2026-09-09", "2026-08-17", "2026-06-18"]
    assert all(r["eps_avg"] is not None for r in vin)


# --- December year end ------------------------------------------------------------------
def test_december_year_end():
    est, vin, checks = _rows("QS", QS_TREND)
    assert [(r["period_end"], r["fy_label"], r["mark"]) for r in est] == [
        ("2026-12-31", "FY2026", "E"),
        ("2025-12-31", "FY2025", "A"),
        ("2027-12-31", "FY2027", "E"),
    ]
    assert est[0]["eps_avg"] == -0.83
    assert est[1]["eps_avg"] == -0.94
    assert len([r for r in vin if r["period_end"] == "2026-12-31"]) == 5
    assert [c for c in checks if c["name"] == "Yahoo fiscal year end"] == []


# --- the currency trap --------------------------------------------------------------------
def test_dual_listing_takes_the_reporting_currency_not_the_trading_one():
    est, vin, checks = _rows("3750.HK", CATL_H_TREND, price=CATL_H_PRICE)
    assert {r["currency"] for r in est} == {"CNY"}  # not the HKD it trades in
    assert {r["currency"] for r in vin} == {"CNY"}
    assert [c for c in checks if c["name"] == "Yahoo EPS currency"] == []


def test_missing_year_ago_eps_produces_no_actual_row():
    est, _, _ = _rows("3750.HK", CATL_H_TREND, price=CATL_H_PRICE)
    assert [r["mark"] for r in est] == ["E"]
    assert est[0]["eps_avg"] == 20.93


def test_earnings_estimate_currency_beats_the_price_module():
    trend = [{**CATL_H_TREND[0], "earningsEstimate": {**CATL_H_TREND[0]["earningsEstimate"], "currency": "CNY"}}]
    assert resolve_currency(trend, {"currency": "HKD", "financialCurrency": "HKD"}, BY_TICKER["3750.HK"]) == (
        "CNY",
        "earningsEstimate",
    )


def test_dual_listing_without_a_payload_currency_infers_from_the_primary_and_warns():
    est, _, checks = _rows("3750.HK", CATL_H_TREND)  # no price module at all
    assert {r["currency"] for r in est} == {"CNY"}  # from 300750.SZ, the reporting entity
    warn = next(c for c in checks if c["name"] == "Yahoo EPS currency")
    assert warn["status"] == "warn"
    assert "3750.HK" in warn["detail"] and "300750.SZ" in warn["detail"]


def test_dual_listing_with_only_a_trading_currency_still_infers_the_primary_and_warns():
    # Yahoo nulls financialCurrency on plenty of non-US lines; the HKD left behind is the trading
    # currency, and taking it would put CATL's H-line PE out by the whole HKD/CNY rate
    hkd_only = {k: v for k, v in CATL_H_PRICE.items() if k != "financialCurrency"}
    est, vin, checks = _rows("3750.HK", CATL_H_TREND, price=hkd_only)
    assert {r["currency"] for r in est} == {"CNY"}
    assert {r["currency"] for r in vin} == {"CNY"}
    warn = next(c for c in checks if c["name"] == "Yahoo EPS currency")
    assert warn["status"] == "warn" and "300750.SZ" in warn["detail"]


def test_standalone_hk_listing_leaves_the_currency_null_rather_than_guessing():
    # the payload Yahoo really sends for CALB: a price module, and the only currency in it is HKD.
    # CALB is a mainland issuer reporting in CNY with no primary line to infer from, so the answer
    # has to be "unknown", not the currency it happens to trade in.
    est, vin, checks = _rows("3931.HK", CATL_H_TREND, price={"currency": "HKD"})
    assert {r["currency"] for r in est} == {None}
    assert {r["currency"] for r in vin} == {None}
    warn = next(c for c in checks if c["name"] == "Yahoo EPS currency")
    assert warn["status"] == "warn"
    assert "3931.HK" in warn["detail"]


@pytest.mark.parametrize(
    ("ticker", "price", "want"),
    [
        # the trap in one table: every one of these prices is the currency the line TRADES in
        ("3750.HK", {"currency": "HKD"}, ("CNY", "primary-listing")),  # CATL H reports in CNY
        ("6869.HK", {"currency": "HKD"}, ("CNY", "primary-listing")),  # YOFC H
        ("1211.HK", {"currency": "HKD"}, ("CNY", "primary-listing")),  # BYD H
        ("3931.HK", {"currency": "HKD"}, (None, "none")),  # CALB: unknown beats a wrong guess
        ("IKA.L", {"currency": "GBp"}, ("GBP", "market")),  # pence quote, accounts in pounds
        ("COHR", {"currency": "USD"}, ("USD", "market")),  # the easy case is unchanged
    ],
)
def test_price_currency_is_never_taken_as_the_reporting_currency(ticker, price, want):
    assert resolve_currency([], price, BY_TICKER[ticker]) == want


def test_a_pence_reporting_currency_from_the_payload_is_kept_verbatim():
    # GBp in financialCurrency is a statement about the accounts, and normalising it to GBP would
    # be a 100x error, so that one is trusted and passed through exactly as it arrived
    assert resolve_currency([], {"currency": "GBp", "financialCurrency": "GBp"}, BY_TICKER["IKA.L"]) == (
        "GBp",
        "price.financialCurrency",
    )


def test_domestic_listing_falls_back_to_its_market_without_a_warning():
    est, _, checks = _rows("COHR", COHR_TREND)  # no price module, us -> USD
    assert {r["currency"] for r in est} == {"USD"}
    assert [c for c in checks if c["name"] == "Yahoo EPS currency"] == []


# --- degenerate payloads --------------------------------------------------------------------
def test_empty_earnings_estimate_block():
    trend = [{"period": "0y", "endDate": "2026-12-31", "earningsEstimate": {}}]
    est, vin, checks = _rows("QS", trend)
    assert len(est) == 1
    assert est[0]["period_end"] == "2026-12-31"
    assert est[0]["fy_label"] == "FY2026"
    assert est[0]["mark"] == "E"
    assert est[0]["eps_avg"] is None
    assert est[0]["eps_low"] is None
    assert est[0]["eps_high"] is None
    assert est[0]["n_analysts"] is None
    assert vin == []
    assert [c["status"] for c in checks] == []  # currency still resolves from config


def test_trend_with_only_quarters_warns_and_yields_nothing():
    est, vin, checks = _rows("QS", COHR_TREND[:2])
    assert est == [] and vin == []
    assert any(c["name"] == "Yahoo consensus coverage" and c["status"] == "warn" for c in checks)


def test_empty_trend_list():
    est, vin, checks = _rows("QS", [])
    assert est == [] and vin == []
    assert any(c["name"] == "Yahoo consensus coverage" for c in checks)


def test_unparseable_end_date_skips_the_period():
    trend = [{"period": "0y", "endDate": None, "earningsEstimate": {"avg": {"raw": 1.0}}}]
    est, vin, checks = _rows("QS", trend)
    assert est == [] and vin == []
    assert any("endDate" in c["detail"] for c in checks)


# --- the fiscal-year cross-check -------------------------------------------------------------
def test_fy_end_cross_check_fires_when_yahoo_still_points_at_a_finished_year():
    stale = [{**COHR_TREND[2], "endDate": "2026-06-30"}]  # FY2026 ended, not yet reported
    est, _, checks = _rows("COHR", stale)
    warn = next(c for c in checks if c["name"] == "Yahoo fiscal year end")
    assert warn["status"] == "warn"
    assert "COHR" in warn["detail"]
    assert "2027-06-30" in warn["detail"]  # what the config implied
    assert est[0]["period_end"] == "2026-06-30"  # reported endDate still wins
    assert est[0]["fy_label"] == "FY2026"


def test_fy_end_cross_check_tolerates_a_short_drift():
    # AVGO's fiscal year ends in early November but is configured as month 10
    shifted = [{"period": "0y", "endDate": "2026-11-01", "earningsEstimate": {"avg": {"raw": 7.9}}}]
    _, _, checks = _rows("AVGO", shifted)
    assert [c for c in checks if c["name"] == "Yahoo fiscal year end"] == []


def test_fy_end_cross_check_only_looks_at_0y():
    _, _, checks = _rows("COHR", COHR_TREND)
    assert [c for c in checks if c["name"] == "Yahoo fiscal year end"] == []


@pytest.mark.parametrize(
    ("ticker", "month", "today", "reported"),
    [
        # AVGO's year really ends in early November but is configured as month 10, so on 31 October
        # the expected date has rolled a year ahead of a year end that is one day away
        ("AVGO", 10, date(2026, 10, 31), date(2026, 11, 1)),
        ("MTSI", 9, date(2026, 9, 30), date(2026, 10, 2)),
        ("CIEN", 10, date(2026, 10, 31), date(2026, 10, 31)),
    ],
)
def test_a_year_end_that_has_only_just_passed_is_not_a_years_drift(ticker, month, today, reported):
    assert BY_TICKER[ticker].fy_end_month == month  # the configuration this guards
    assert yf.fy_end_drift(reported, month, today)[1] <= yf.FY_END_TOLERANCE_DAYS
    trend = [{"period": "0y", "endDate": reported.isoformat(), "earningsEstimate": {"avg": {"raw": 7.9}}}]
    _, _, checks = parse_trend(ticker, BY_TICKER[ticker], trend, datetime(today.year, today.month, today.day, 9, tzinfo=UTC))
    assert [c for c in checks if c["name"] == "Yahoo fiscal year end"] == []


def test_a_year_end_that_passed_months_ago_still_fires():
    # the signal the tolerance must not swallow: Yahoo pointing at a finished, unreported year
    assert yf.fy_end_drift(date(2026, 6, 30), 6, date(2026, 9, 16)) == (date(2027, 6, 30), 365)


# --- the prior-year actual a non-December filer needs ------------------------------------------
def test_missing_year_ago_eps_warns_when_the_calendar_year_needs_it():
    # 5802.T files to March, so calendarize blends its estimate with the prior actual; without one
    # the coverage test fails and the company vanishes from the current calendar year on the page
    trend = [{"period": "0y", "endDate": "2027-03-31", "earningsEstimate": {"avg": {"raw": 304.5}}}]
    est, _, checks = _rows("5802.T", trend)
    assert [r["mark"] for r in est] == ["E"]  # still no invented actual row
    warn = next(c for c in checks if c["name"] == "Yahoo prior-year actual")
    assert warn["status"] == "warn"
    assert "5802.T" in warn["detail"]


def test_missing_year_ago_eps_is_silent_for_a_december_filer():
    # 3750.HK files to December, so the fiscal year already is the calendar year and nothing is lost
    _, _, checks = _rows("3750.HK", CATL_H_TREND, price=CATL_H_PRICE)
    assert [c for c in checks if c["name"] == "Yahoo prior-year actual"] == []


# --- key collisions the frame builders would hide ------------------------------------------------
def test_repeated_period_end_is_reported_rather_than_silently_deduped():
    # a thinly covered name that has not rolled its year: 0y and +1y carry the same endDate, and
    # estimates_frame would keep the first and drop next year's forecast without a word
    collided = [COHR_TREND[2], {**COHR_TREND[3], "endDate": "2027-06-30"}]
    est, vin, checks = _rows("COHR", collided)
    fail = next(c for c in checks if c["name"] == "Yahoo key collision" and "estimate" in c["detail"])
    assert fail["status"] == "fail"
    assert "COHR" in fail["detail"] and "2027-06-30" in fail["detail"]
    assert any(c["name"] == "Yahoo key collision" and "vintage" in c["detail"] for c in checks)
    assert estimates_frame(est).height < len(est)  # the drop the check exists to announce
    assert vintages_frame(vin).height < len(vin)


def test_an_analyst_count_wider_than_int64_is_dropped_not_carried():
    # 10^19 does not fit the Int64 the column is written as, and one nonsense count must not cost
    # the listing its rows; None reads as thin coverage, which is a warning rather than silence
    trend = [{**QS_TREND[0], "earningsEstimate": {**QS_TREND[0]["earningsEstimate"], "numberOfAnalysts": {"raw": 1e19}}}]
    est, _, _ = _rows("QS", trend)
    assert est[0]["n_analysts"] is None
    ESTIMATES_SCHEMA.validate(estimates_frame(est))


# --- quoteSummary envelope ---------------------------------------------------------------------
def test_extract_result_and_accessors():
    payload = {
        "quoteSummary": {
            "result": [{"earningsTrend": {"trend": COHR_TREND}, "price": {"currency": "USD"}}],
            "error": None,
        }
    }
    result = extract_result(payload)
    assert trend_of(result) is COHR_TREND
    assert price_of(result) == {"currency": "USD"}


@pytest.mark.parametrize(
    "payload",
    [
        {"quoteSummary": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}},
        {"quoteSummary": {"result": [], "error": None}},
        {},
    ],
)
def test_extract_result_raises_on_an_empty_envelope(payload):
    with pytest.raises(ValueError):
        extract_result(payload)


def test_result_without_a_price_module_still_parses():
    result = extract_result({"quoteSummary": {"result": [{"earningsTrend": {"trend": QS_TREND}}]}})
    est, _, _ = parse_trend("QS", BY_TICKER["QS"], trend_of(result), SNAP, price=price_of(result))
    assert est[0]["currency"] == "USD"


# --- the contract with schema.py ------------------------------------------------------------------
def test_parsed_rows_satisfy_the_pandera_schemas():
    est: list[dict] = []
    vin: list[dict] = []
    for ticker, trend, price in (
        ("COHR", COHR_TREND, None),
        ("QS", QS_TREND, None),
        ("3750.HK", CATL_H_TREND, CATL_H_PRICE),
        ("3931.HK", CATL_H_TREND, None),
    ):
        rows, vints, _ = _rows(ticker, trend, price=price)
        est += rows
        vin += vints
    est_frame = estimates_frame(est)
    vin_frame = vintages_frame(vin)
    ESTIMATES_SCHEMA.validate(est_frame)
    VINTAGES_SCHEMA.validate(vin_frame)
    assert est_frame.height == len(est)  # no key collisions between 0y, +1y and the actual
    assert vin_frame.height == len(vin)
    assert est_frame["n_analysts"].dtype.is_integer()
    assert est_frame["eps_avg"].dtype.is_float()


def test_the_pool_is_every_non_a_share_listing():
    pool = yahoo_companies()
    assert len(pool) == 37
    assert all(c.estimates == "yahoo" for c in pool)
    assert all(c.market != "cn" for c in pool)
    assert {"COHR", "5802.T", "006400.KS", "3081.TWO", "QS", "3750.HK", "IKA.L"} <= {c.ticker for c in pool}


def test_pool_can_be_narrowed_and_ignores_a_shares():
    assert [c.ticker for c in yahoo_companies(["COHR", "300308.SZ"])] == ["COHR"]


# --- the network layer, driven by a fake client and a mock transport --------------------------------
class _StubClient:
    """Stands in for HttpClient where the payload is injected past ``fetch`` entirely."""

    calls = 0


class _FakeClient:
    """An HttpClient shaped stand-in: replays canned payloads and records what was asked for."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.requests: list[tuple[str, dict]] = []

    def get_json(self, url, params=None):
        self.calls += 1
        params = dict(params or {})
        self.requests.append((url, params))
        return self._handler(url, params)

    @property
    def modules(self) -> list[str]:
        return [p.get("modules") for _, p in self.requests]


def _payload(trend: list[dict], price: dict | None = None) -> dict:
    result: dict = {"earningsTrend": {"trend": trend}}
    if price is not None:
        result["price"] = price
    return {"quoteSummary": {"result": [result], "error": None}}


DELISTED = {
    "quoteSummary": {
        "result": None,
        "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
    }
}


def test_fetch_asks_for_both_modules_and_returns_the_payload():
    client = _FakeClient(lambda url, params: _payload(COHR_TREND, {"financialCurrency": "USD"}))
    got = yf.fetch(client, "COHR", crumb="abc")
    assert got["quoteSummary"]["result"][0]["price"] == {"financialCurrency": "USD"}
    assert client.modules == ["earningsTrend,price"]
    assert client.requests[0][1]["crumb"] == "abc"
    assert client.requests[0][0].endswith("/COHR")


def test_fetch_retries_without_the_price_module_and_keeps_the_first_payload():
    empty = {"quoteSummary": {"result": [], "error": None}}
    client = _FakeClient(lambda url, params: empty if "price" in params["modules"] else _payload(QS_TREND))
    discarded: list[dict] = []

    got = yf.fetch(client, "QS", on_discard=discarded.append)

    assert client.modules == ["earningsTrend,price", "earningsTrend"]
    assert got["quoteSummary"]["result"]  # the retry is what the parse sees
    assert discarded == [empty]  # ... and the superseded response is still evidence


def test_fetch_does_not_retry_a_delisted_symbol():
    # the retry exists for a rejected price module; an error envelope answers the same way twice,
    # and retrying costs a second call on a rate-limited endpoint and buries the explanation
    client = _FakeClient(lambda url, params: DELISTED)
    discarded: list[dict] = []

    got = yf.fetch(client, "0877.HK", on_discard=discarded.append)

    assert client.modules == ["earningsTrend,price"]
    assert got is DELISTED and discarded == []
    with pytest.raises(ValueError, match="quoteSummary error"):
        extract_result(got)


def _mock_client(handler) -> yf.HttpClient:
    """A real HttpClient whose transport is mocked, so retry and status handling are the real ones."""
    client = yf.HttpClient(min_interval=0.0, max_retries=0)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_fetch_surfaces_a_401_instead_of_retrying_it():
    # what a missing crumb produces; HttpClient re-raises a 4xx immediately, and main() turns that
    # into one failed ticker rather than an aborted run
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401, json={"finance": {"error": {"code": "Unauthorized"}}})

    with pytest.raises(httpx.HTTPStatusError):
        yf.fetch(_mock_client(handler), "COHR")
    assert len(seen) == 1  # not retried four times against a wall


def test_fetch_url_encodes_a_symbol_with_a_dot():
    captured: list[str] = []

    def handler(request):
        captured.append(str(request.url))
        return httpx.Response(200, json=_payload(CATL_H_TREND, CATL_H_PRICE))

    yf.fetch(_mock_client(handler), "3750.HK", crumb="c1")
    assert captured[0].startswith(yf.QUOTE_SUMMARY + "3750.HK?")
    assert "modules=earningsTrend%2Cprice" in captured[0]


class _Reply:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _Boot:
    """The throwaway httpx.Client the crumb handshake runs on."""

    def __init__(self, *, crumb_raises: bool = False, **kw) -> None:
        self._crumb_raises = crumb_raises
        self.cookies = {"A1": "d=abc", "A3": "d=def"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str):
        if url == yf.COOKIE_URL:
            return _Reply(404, "")  # 404s by design, but sets the consent cookies
        if self._crumb_raises:
            raise httpx.ConnectTimeout("getcrumb timed out")
        return _Reply(200, "crumb-xyz\n")


def _fake_httpx(monkeypatch, **kw):
    monkeypatch.setattr(
        yf, "httpx", SimpleNamespace(Client=lambda **client_kw: _Boot(**kw), HTTPError=httpx.HTTPError)
    )


def test_session_replays_the_cookies_and_the_crumb(monkeypatch):
    _fake_httpx(monkeypatch)
    client, crumb = yf._raw_session(min_interval=0.0)
    assert crumb == "crumb-xyz"
    assert client._client.headers["cookie"] == "A1=d=abc; A3=d=def"
    client.close()


def test_session_keeps_the_cookies_when_only_the_crumb_request_fails(monkeypatch):
    # one transient timeout on getcrumb used to throw the consent cookies away with it, and every
    # one of the 37 quoteSummary calls then answered 401 for want of a jar that had been collected
    _fake_httpx(monkeypatch, crumb_raises=True)
    client, crumb = yf._raw_session(min_interval=0.0)
    assert crumb is None
    assert client._client.headers["cookie"] == "A1=d=abc; A3=d=def"
    client.close()


# --- orchestration, still with no network ----------------------------------------------------------
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the module's write paths at a tmp dir and stub the Yahoo handshake out."""
    monkeypatch.setattr(yf, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(yf, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(yf, "session", lambda **kw: (_StubClient(), "crumb"))
    return tmp_path


def _paths(tmp_path):
    day, _ = run_stamp(utc_now())
    return (
        tmp_path / "snapshots" / "valuation" / "estimates" / f"{day}-yahoo.parquet",
        tmp_path / "snapshots" / "valuation" / "vintages" / f"{day}-yahoo.parquet",
        tmp_path / "raw" / "valuation" / "yahoo" / day,  # data/raw/valuation/<source>/<date>/
    )


def _run_log(tmp_path) -> dict:
    lines = (tmp_path / "snapshots" / "valuation" / "estimates_yf_runs.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


def test_main_writes_both_tables_under_a_yahoo_suffixed_name(sandbox, monkeypatch):
    feed = {"COHR": _payload(COHR_TREND), "QS": _payload(QS_TREND, {"currency": "USD"})}
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: feed[ticker])

    assert yf.main(["--tickers", "COHR,QS"]) == 0

    est_path, vin_path, raw_dir = _paths(sandbox)
    # the A-share half of these tables is written by estimates_em, so the name must stay suffixed
    assert est_path.exists() and vin_path.exists()
    assert sorted(p.name for p in raw_dir.iterdir()) == ["COHR.json.gz", "QS.json.gz"]
    assert read_json_gz(raw_dir / "COHR.json.gz") == feed["COHR"]  # archived verbatim, before parsing

    est = pl.read_parquet(est_path)
    assert est.height == 6  # (0y, +1y, prior actual) x 2 listings
    assert set(est["ticker"]) == {"COHR", "QS"}
    assert set(est["source"]) == {"yahoo"}
    assert sorted(est.filter(pl.col("ticker") == "COHR")["fy_label"]) == ["FY2026", "FY2027", "FY2028"]
    vin = pl.read_parquet(vin_path)
    assert vin.height == 9 + 6  # COHR 5 + 4, QS 5 + 1
    assert set(vin["origin"]) == {"yahoo_trend"}


def test_main_isolates_one_failing_company_and_still_writes_the_rest(sandbox, monkeypatch):
    def flaky(client, ticker, **kw):
        if ticker == "QS":
            raise RuntimeError("HTTP 429 for QS")
        return _payload(COHR_TREND)

    monkeypatch.setattr(yf, "fetch", flaky)

    assert yf.main(["--tickers", "COHR,QS"]) == 1  # red, but only after the good data landed

    est_path, _, raw_dir = _paths(sandbox)
    assert set(pl.read_parquet(est_path)["ticker"]) == {"COHR"}
    assert not (raw_dir / "QS.json.gz").exists()

    run = json.loads((sandbox / "snapshots" / "valuation" / "estimates_yf_runs.jsonl").read_text().splitlines()[-1])
    assert run["failed"] == ["QS"]
    assert "HTTP 429 for QS" in run["errors"][0]
    fetch_check = next(c for c in run["checks"] if c["name"] == "Yahoo consensus fetch")
    assert fetch_check["status"] == "fail" and "QS" in fetch_check["detail"]


def test_main_flags_thin_coverage(sandbox, monkeypatch):
    thin = [{**QS_TREND[0], "earningsEstimate": {**QS_TREND[0]["earningsEstimate"], "numberOfAnalysts": {"raw": 2}}}]
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(thin))

    assert yf.main(["--tickers", "QS"]) == 0

    run = json.loads((sandbox / "snapshots" / "valuation" / "estimates_yf_runs.jsonl").read_text().splitlines()[-1])
    coverage = next(c for c in run["checks"] if c["name"] == "Analyst coverage")
    assert coverage["status"] == "warn" and "QS" in coverage["detail"]


def test_main_stamps_catl_h_in_cny_when_yahoo_only_reports_the_trading_currency(sandbox, monkeypatch):
    """The headline regression, driven through main() and the real fetch().

    3750.HK trades in HKD and its consensus EPS is in CNY. A payload whose price module carries
    only ``currency`` (Yahoo nulls ``financialCurrency`` on plenty of non-US lines) must still come
    out as CNY, because the share layer divides by whatever this column says: stamping HKD on CNY
    figures puts the H-line PE out by the whole HKD/CNY rate, on a green run.
    """
    hkd_only = {k: v for k, v in CATL_H_PRICE.items() if k != "financialCurrency"}
    client = _FakeClient(lambda url, params: _payload(CATL_H_TREND, hkd_only))
    monkeypatch.setattr(yf, "session", lambda **kw: (client, "crumb"))

    assert yf.main(["--tickers", "3750.HK"]) == 0

    est_path, vin_path, _ = _paths(sandbox)
    assert set(pl.read_parquet(est_path)["currency"]) == {"CNY"}
    assert set(pl.read_parquet(vin_path)["currency"]) == {"CNY"}
    warn = next(c for c in _run_log(sandbox)["checks"] if c["name"] == "Yahoo EPS currency")
    assert warn["status"] == "warn" and "300750.SZ" in warn["detail"]


def test_main_leaves_a_standalone_hk_listing_null_and_says_so(sandbox, monkeypatch):
    # CALB reports in CNY, has no primary line in config, and its payload offers only HKD. The
    # branch that refuses to guess has to be reachable from a realistic payload, not just from a
    # parse_trend call with no price at all.
    client = _FakeClient(lambda url, params: _payload(CATL_H_TREND, {"currency": "HKD"}))
    monkeypatch.setattr(yf, "session", lambda **kw: (client, "crumb"))

    assert yf.main(["--tickers", "3931.HK"]) == 0

    est_path, _, _ = _paths(sandbox)
    assert pl.read_parquet(est_path)["currency"].null_count() == pl.read_parquet(est_path).height
    warn = next(c for c in _run_log(sandbox)["checks"] if c["name"] == "Yahoo EPS currency")
    assert warn["status"] == "warn" and "3931.HK" in warn["detail"]


def test_main_archives_the_payload_the_retry_superseded(sandbox, monkeypatch):
    def handler(url, params):
        if "price" in params["modules"]:
            return {"quoteSummary": {"result": [], "error": None}}
        return _payload(QS_TREND)

    monkeypatch.setattr(yf, "session", lambda **kw: (_FakeClient(handler), "crumb"))

    assert yf.main(["--tickers", "QS"]) == 0

    _, _, raw_dir = _paths(sandbox)
    assert sorted(p.name for p in raw_dir.iterdir()) == ["QS-first.json.gz", "QS.json.gz"]
    assert read_json_gz(raw_dir / "QS-first.json.gz")["quoteSummary"]["result"] == []


def test_main_isolates_a_company_whose_rows_the_schema_rejects(sandbox, monkeypatch):
    """A row polars or pandera refuses must cost one listing, not the whole run.

    The frame build and both validate() calls used to sit outside the per-company guard, so one bad
    value took down all 37 listings *and* the run record with them -- append_jsonl came after the
    write and never ran.
    """
    poisoned = [{**QS_TREND[0], "earningsEstimate": {**QS_TREND[0]["earningsEstimate"], "numberOfAnalysts": {"raw": -3}}}]
    feed = {"COHR": _payload(COHR_TREND), "QS": _payload(poisoned), "SLDP": _payload(QS_TREND)}
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: feed[ticker])

    assert yf.main(["--tickers", "COHR,QS,SLDP"]) == 1

    est_path, vin_path, raw_dir = _paths(sandbox)
    assert set(pl.read_parquet(est_path)["ticker"]) == {"COHR", "SLDP"}  # the good rows survive
    assert vin_path.exists()
    assert (raw_dir / "QS.json.gz").exists()  # the evidence for the bad one is still archived

    run = _run_log(sandbox)
    assert run["failed"] == ["QS"]
    assert "QS" in run["errors"][0]
    assert run["estimate_rows"] == 6


def _boom(*args, **kw):
    raise OSError("[Errno 28] no space left on device")


def test_main_still_records_the_run_when_the_write_itself_fails(sandbox, monkeypatch):
    # a full disk or a permission problem on the snapshot write is the one path that can lose every
    # parsed row; the audit line is then the only thing that explains where the day's data went
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(COHR_TREND))
    monkeypatch.setattr(yf, "write_parquet", _boom)

    assert yf.main(["--tickers", "COHR"]) == 1

    run = _run_log(sandbox)
    assert run["estimate_rows"] == 3  # parsed fine ...
    assert run["written"] == {}  # ... but nothing landed, and the record says so
    assert any("no space left" in e for e in run["errors"])
    assert any(c["name"] == "Estimates snapshot" and c["status"] == "fail" for c in run["checks"])


def test_a_partial_run_merges_into_the_days_file_instead_of_shrinking_it(sandbox, monkeypatch):
    """``--tickers`` is a normal way to run this, and it must not cost the day its other listings."""
    feed = {"COHR": _payload(COHR_TREND), "QS": _payload(QS_TREND)}
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: feed[ticker])
    assert yf.main(["--tickers", "COHR,QS"]) == 0

    est_path, vin_path, _ = _paths(sandbox)
    before = pl.read_parquet(est_path)
    assert set(before["ticker"]) == {"COHR", "QS"}

    # an hour later, one listing is re-run on its own with a revised mean
    revised = [{**COHR_TREND[2], "earningsEstimate": {**COHR_TREND[2]["earningsEstimate"], "avg": {"raw": 9.99}}}]
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(revised))
    assert yf.main(["--tickers", "COHR"]) == 0

    after = pl.read_parquet(est_path)
    assert set(after["ticker"]) == {"COHR", "QS"}  # QS was not fetched and was not dropped
    cohr = after.filter((pl.col("ticker") == "COHR") & (pl.col("period_end") == "2027-06-30"))
    assert cohr["eps_avg"].to_list() == [9.99]  # the new row wins on the key
    assert set(pl.read_parquet(vin_path)["ticker"]) == {"COHR", "QS"}
    ESTIMATES_SCHEMA.validate(after)


def test_a_run_that_parsed_nothing_leaves_the_existing_file_alone(sandbox, monkeypatch):
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(COHR_TREND))
    assert yf.main(["--tickers", "COHR"]) == 0
    est_path, _, _ = _paths(sandbox)
    good = pl.read_parquet(est_path)

    # a later run whose only listing comes back with nothing parseable in it
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload([]))
    assert yf.main(["--tickers", "QS"]) == 0

    assert pl.read_parquet(est_path).equals(good)  # not truncated to an empty table
    run = _run_log(sandbox)
    assert run["estimate_rows"] == 0 and run["written"] == {}
    skipped = next(c for c in run["checks"] if c["name"] == "Estimates snapshot")
    assert skipped["status"] == "warn" and "left as it stands" in skipped["detail"]


# --- how many fiscal years Yahoo offers (T2) ------------------------------------------------------
# Yahoo's earningsTrend carries 0q, +1q, 0y, +1y plus the +5y / -5y long-term growth rows. There is
# no +2y, which is why an October or November year end cannot cover the whole of the calendar year
# after next: FY+2 does not exist to blend. Nothing here fetches; the point of these tests is that
# the module reads the payload rather than a hardcoded pair, so the day a third panel appears it is
# emitted and the run record says so.
FIVE_YEAR_GROWTH = [
    {"period": "+5y", "endDate": {}, "growth": {"raw": 0.213}},
    {"period": "-5y", "endDate": {}, "growth": {"raw": 0.164}},
]


def test_todays_payload_shape_offers_exactly_two_fiscal_years():
    assert fiscal_periods([n["period"] for n in COHR_TREND]) == ["0y", "+1y"]
    assert period_keys(COHR_TREND) == ["0q", "+1q", "0y", "+1y"]


def test_long_term_growth_rows_are_not_fiscal_years():
    """They carry a growth rate, an empty endDate and no earningsEstimate. Admitting them would
    manufacture a 'no usable endDate' warning on every listing in the pool."""
    periods = [n["period"] for n in COHR_TREND + FIVE_YEAR_GROWTH]
    assert fiscal_periods(periods) == ["0y", "+1y"]


def test_a_growth_row_does_not_reach_the_parser_or_its_checks():
    est, vin, checks = parse_trend("COHR", BY_TICKER["COHR"], COHR_TREND + FIVE_YEAR_GROWTH, SNAP)

    assert sorted({r["fy_label"] for r in est}) == ["FY2026", "FY2027", "FY2028"]
    assert not [c for c in checks if "endDate" in c["detail"]]
    assert vin


@pytest.mark.parametrize(
    ("periods", "want"),
    [
        (["+1y", "0y"], ["0y", "+1y"]),  # nearest first whatever order Yahoo sent
        (["0q", "+1q", "-1q"], []),  # quarters are not fiscal years
        (["-1y", "0y"], ["0y"]),  # a year already reported is not a forecast
        (["0y", "+1y", "+2y"], ["0y", "+1y", "+2y"]),  # the third year, if it ever arrives
        (["0y", "+9y"], ["0y"]),  # beyond MAX_FY_AHEAD it is not an EPS panel
        (["0y", "0y"], ["0y"]),
    ],
)
def test_fiscal_periods_reads_what_the_payload_carries(periods, want):
    assert fiscal_periods(periods) == want


def test_a_third_fiscal_year_would_be_emitted_without_a_code_change():
    """AVGO, MTSI and CIEN need FY+2 to cover the calendar year after next. Yahoo does not publish
    it; if it ever does, the row lands rather than being silently discarded by a hardcoded pair."""
    third = {
        "period": "+2y",
        "endDate": "2029-06-30",
        "earningsEstimate": {"avg": {"raw": 12.8}, "low": {"raw": 10.0}, "high": {"raw": 15.5},
                             "numberOfAnalysts": {"raw": 11}},
        "epsTrend": {"current": {"raw": 12.8}, "30daysAgo": {"raw": 12.4}},
    }
    est, vin, _ = parse_trend("COHR", BY_TICKER["COHR"], [*COHR_TREND, third], SNAP)

    row = next(r for r in est if r["period_end"] == "2029-06-30")
    assert (row["fy_label"], row["mark"], row["eps_avg"], row["n_analysts"]) == ("FY2029", "E", 12.8, 11)
    assert row["eps_low"] == 10.0 and row["eps_high"] == 15.5
    assert len([r for r in vin if r["period_end"] == "2029-06-30"]) == 2
    ESTIMATES_SCHEMA.validate(estimates_frame(est))
    VINTAGES_SCHEMA.validate(vintages_frame(vin))


def test_the_run_record_carries_the_periods_the_payloads_actually_had(sandbox, monkeypatch):
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(COHR_TREND + FIVE_YEAR_GROWTH))

    assert yf.main(["--tickers", "COHR"]) == 0

    run = _run_log(sandbox)
    assert run["periods"] == {"+1q": 1, "+1y": 1, "+5y": 1, "-5y": 1, "0q": 1, "0y": 1}
    assert run["fiscal_periods"] == ["0y", "+1y"]
    panels = next(c for c in run["checks"] if c["name"] == "Yahoo fiscal-year panels")
    assert panels["status"] == "pass" and "no further fiscal year" in panels["detail"]


def test_a_third_fiscal_year_arriving_is_reported_rather_than_absorbed(sandbox, monkeypatch):
    third = {"period": "+2y", "endDate": "2029-06-30", "earningsEstimate": {"avg": {"raw": 12.8}}}
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload([*COHR_TREND, third]))

    assert yf.main(["--tickers", "COHR"]) == 0

    run = _run_log(sandbox)
    assert run["fiscal_periods"] == ["0y", "+1y", "+2y"]
    panels = next(c for c in run["checks"] if c["name"] == "Yahoo fiscal-year panels")
    assert panels["status"] == "warn" and "+2y" in panels["detail"]
    assert "2029-06-30" in pl.read_parquet(_paths(sandbox)[0])["period_end"].to_list()


# --- the raw archive is append-only (S6, CLAUDE.md rule 2) ----------------------------------------
def test_an_identical_payload_is_recognised_rather_than_archived_twice(tmp_path):
    first = archive_payload({"quoteSummary": 1}, tmp_path, "COHR", "0900")
    again = archive_payload({"quoteSummary": 1}, tmp_path, "COHR", "1400")

    assert again == first
    assert sorted(p.name for p in tmp_path.iterdir()) == ["COHR.json.gz"]


def test_a_differing_payload_lands_beside_the_first_instead_of_replacing_it(tmp_path):
    first = archive_payload({"quoteSummary": 1}, tmp_path, "COHR", "0900")
    second = archive_payload({"quoteSummary": 2}, tmp_path, "COHR", "1400")
    third = archive_payload({"quoteSummary": 3}, tmp_path, "COHR", "1400")  # same minute, third body

    assert [p.name for p in (first, second, third)] == [
        "COHR.json.gz", "COHR__1400.json.gz", "COHR__1400_2.json.gz",
    ]
    assert [json.loads(gzip.decompress(p.read_bytes())) for p in (first, second, third)] == [
        {"quoteSummary": 1}, {"quoteSummary": 2}, {"quoteSummary": 3},
    ]


def test_an_unreadable_archive_is_kept_and_the_payload_written_beside_it(tmp_path):
    (tmp_path / "COHR.json.gz").write_bytes(b"not gzip at all")
    path = archive_payload({"quoteSummary": 1}, tmp_path, "COHR", "1400")

    assert path.name == "COHR__1400.json.gz"
    assert (tmp_path / "COHR.json.gz").read_bytes() == b"not gzip at all"


def test_a_tickers_rerun_keeps_the_earlier_runs_payload_on_disk(sandbox, monkeypatch):
    """The parquet for a run has to stay reproducible from the raw layer of that run."""
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(COHR_TREND))
    assert yf.main(["--tickers", "COHR"]) == 0

    moved = [{**COHR_TREND[2], "earningsEstimate": {**COHR_TREND[2]["earningsEstimate"], "avg": {"raw": 9.9}}}]
    monkeypatch.setattr(yf, "fetch", lambda client, ticker, **kw: _payload(moved))
    assert yf.main(["--tickers", "COHR"]) == 0

    _, _, raw_dir = _paths(sandbox)
    archived = sorted(raw_dir.glob("COHR*.json.gz"))
    assert len(archived) == 2, [p.name for p in archived]
    avgs = sorted(
        json.loads(gzip.decompress(p.read_bytes()))["quoteSummary"]["result"][0]["earningsTrend"]["trend"][-1][
            "earningsEstimate"
        ]["avg"]["raw"]
        for p in archived
    )
    assert avgs == [9.9, 11.02]
