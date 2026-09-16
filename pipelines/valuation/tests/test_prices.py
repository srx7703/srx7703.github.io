"""Pure-function tests for the Yahoo prices module. No network: every payload is inline.

The fixtures keep the shape (and, where they were measured, the values) of real `Ticker(t).info`
dicts: a plain US listing, a dual listing whose price and reporting currencies differ, a London
listing quoted in pence, a listing that omits `currentPrice`, and one that omits `marketCap`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import polars as pl
import pytest
from pandera.errors import SchemaError

from pipelines.common.storage import read_json_gz, run_stamp, utc_now
from pipelines.valuation import prices as mod
from pipelines.valuation.prices import (
    Throttle,
    archive_info,
    fetch_info,
    is_missing,
    is_rate_limited,
    is_resolved,
    jsonable,
    parse_info,
    run_checks,
    safe_name,
    select_companies,
    write_snapshot,
)
from pipelines.valuation.schema import PRICES_DTYPES, PRICES_SCHEMA, prices_frame

# COHR, measured 2026-09-16.
COHR = {
    "shortName": "Coherent Corp.",
    "currentPrice": 289.64,
    "regularMarketPrice": 289.64,
    "currency": "USD",
    "financialCurrency": "USD",
    "marketCap": 56720850944,
    "sharesOutstanding": 195832246,
    "regularMarketTime": 1789588799,
}

# CATL's H line, measured 2026-09-16: trades in HKD, reports in CNY, and the two currencies must not
# collapse into one column. `marketCap` sizes the whole issuer while `sharesOutstanding` counts only
# the H float, so price x shares is ~21x short of the cap -- the asymmetry the cap check records.
CATL_H = {
    "shortName": "CONTEMPORARY AMPEREX-H",
    "currentPrice": 501.0,
    "regularMarketPrice": 501.0,
    "currency": "HKD",
    "financialCurrency": "CNY",
    "marketCap": 2318032699392,
    "sharesOutstanding": 218300300,
    "regularMarketTime": 1789561800,
}

# Ilika: price in pence, financials in pounds.
IKA_L = {
    "shortName": "ILIKA PLC ORD 1P",
    "currentPrice": 18.5,
    "regularMarketPrice": 18.5,
    "currency": "GBp",
    "financialCurrency": "GBP",
    "marketCap": 33100000,
    "sharesOutstanding": 178900000,
    "regularMarketTime": 1789574400,
}

# Samsung SDI: no `currentPrice` key at all, only `regularMarketPrice`.
NO_CURRENT_PRICE = {
    "shortName": "SamsungSDI",
    "regularMarketPrice": 289500.0,
    "currency": "KRW",
    "financialCurrency": "KRW",
    "marketCap": 42809335742464,
    "sharesOutstanding": 68764530,
    "regularMarketTime": 1789527600,
}

# A thin listing Yahoo prices but does not size.
NO_MARKET_CAP = {
    "shortName": "LUXNET CORPORATION",
    "currentPrice": 121.5,
    "regularMarketPrice": 121.5,
    "currency": "TWD",
    "financialCurrency": "TWD",
    "sharesOutstanding": 96400000,
    "regularMarketTime": 1789530000,
}


def test_parse_plain_us_listing():
    row = parse_info("COHR", COHR, snapshot_ts="2026-09-16T18:30:00+00:00")
    assert row["ticker"] == "COHR"
    assert row["price"] == 289.64
    assert row["currency"] == "USD"
    assert row["financial_currency"] == "USD"
    assert row["market_cap"] == 56720850944.0
    assert row["shares_outstanding"] == 195832246.0
    assert row["snapshot_ts"] == "2026-09-16T18:30:00+00:00"
    assert row["price_ts"] == "2026-09-16T19:59:59+00:00"  # epoch seconds -> ISO UTC
    # Derived from the schema, not typed out again: a column renamed in schema.py but not here would
    # otherwise reach production as an all-null column with the whole suite still green.
    assert set(row) == set(PRICES_DTYPES)


def test_parse_keeps_price_and_reporting_currency_apart():
    row = parse_info("3750.HK", CATL_H)
    assert row["currency"] == "HKD"  # what the quote is in
    assert row["financial_currency"] == "CNY"  # what the filings are in
    assert row["price"] == 501.0
    # both numbers are recorded as served, on their different bases
    assert row["market_cap"] == 2318032699392.0
    assert row["shares_outstanding"] == 218300300.0


def test_parse_pence_quote_is_not_converted():
    """GBp is pence. The price stays as quoted and the label is preserved for fx.normalise_quote."""
    row = parse_info("IKA.L", IKA_L)
    assert row["price"] == 18.5  # not 0.185
    assert row["currency"] == "GBp"  # case matters: GBp != GBP
    assert row["financial_currency"] == "GBP"


def test_parse_falls_back_to_regular_market_price():
    row = parse_info("006400.KS", NO_CURRENT_PRICE)
    assert row["price"] == 289500.0
    assert row["currency"] == "KRW"
    assert row["market_cap"] == 42809335742464.0


def test_parse_missing_market_cap_is_null_not_zero():
    row = parse_info("4979.TWO", NO_MARKET_CAP)
    assert row["market_cap"] is None
    assert row["price"] == 121.5
    assert row["shares_outstanding"] == 96400000.0


@pytest.mark.parametrize(
    "info",
    [
        {"currentPrice": 0, "regularMarketPrice": 12.0},  # zero is not a price; fall through
        {"currentPrice": None, "regularMarketPrice": 12.0},
        {"currentPrice": float("nan"), "regularMarketPrice": 12.0},
        {"currentPrice": -3.0, "regularMarketPrice": 12.0},
    ],
)
def test_unusable_price_falls_through_to_the_backup_key(info):
    assert parse_info("X", info)["price"] == 12.0


def test_no_usable_price_anywhere_is_null():
    row = parse_info("X", {"marketCap": 1000.0, "currency": "USD"})
    assert row["price"] is None
    assert row["market_cap"] == 1000.0


def test_price_ts_accepts_datetime_and_milliseconds_and_junk():
    naive = datetime(2026, 9, 16, 3, 59, 59)
    assert parse_info("X", {"regularMarketTime": naive})["price_ts"] == "2026-09-16T03:59:59+00:00"
    aware = datetime(2026, 9, 16, 3, 59, 59, tzinfo=UTC)
    assert parse_info("X", {"regularMarketTime": aware})["price_ts"] == "2026-09-16T03:59:59+00:00"
    assert parse_info("X", {"regularMarketTime": 1789588799000})["price_ts"] == "2026-09-16T19:59:59+00:00"
    assert parse_info("X", {"regularMarketTime": 0})["price_ts"] is None
    assert parse_info("X", {"regularMarketTime": "later"})["price_ts"] is None
    assert parse_info("X", {})["price_ts"] is None


def test_rows_satisfy_the_prices_schema():
    rows = [
        parse_info(t, info, snapshot_ts="2026-09-16T18:30:00+00:00")
        for t, info in [
            ("COHR", COHR),
            ("3750.HK", CATL_H),
            ("IKA.L", IKA_L),
            ("006400.KS", NO_CURRENT_PRICE),
            ("4979.TWO", NO_MARKET_CAP),
        ]
    ]
    df = prices_frame(rows)
    PRICES_SCHEMA.validate(df)
    assert df.height == 5
    assert df["ticker"].to_list() == ["COHR", "3750.HK", "IKA.L", "006400.KS", "4979.TWO"]


def test_frame_is_one_row_per_listing():
    rows = [parse_info("COHR", COHR, snapshot_ts="t"), parse_info("COHR", COHR, snapshot_ts="t")]
    assert prices_frame(rows).height == 1


# --- unresolved symbols, archiving and throttling ------------------------------------------
def test_stub_payload_for_an_unknown_symbol_is_not_a_listing():
    assert is_resolved(COHR)
    assert not is_resolved({})
    assert not is_resolved(None)
    assert not is_resolved({"trailingPegRatio": None})  # what Yahoo hands back for a dead symbol
    assert is_resolved({"marketCap": 1000})  # priced pre-open: cap but no quote yet


def test_rate_limit_and_missing_errors_are_told_apart():
    class YFRateLimitError(Exception):
        pass

    assert is_rate_limited(YFRateLimitError("Too Many Requests"))
    assert is_rate_limited(RuntimeError("HTTP 429 from Yahoo"))
    assert not is_rate_limited(RuntimeError("connection reset"))
    assert is_missing(RuntimeError("404 Not Found: possibly delisted"))
    assert not is_missing(RuntimeError("HTTP 429 from Yahoo"))


def test_safe_name_strips_path_characters():
    assert safe_name("300308.SZ") == "300308_SZ"
    assert safe_name("4979.TWO") == "4979_TWO"
    assert safe_name("COHR") == "COHR"
    assert "/" not in safe_name("A/B.C")


def test_jsonable_drops_values_that_would_corrupt_the_archive():
    out = jsonable(
        {
            "shortName": "Coherent Corp.",
            "marketCap": 56720850944,
            "companyOfficers": [{"name": "x", "age": 51}],
            "lastSplitDate": datetime(2026, 1, 1),  # not JSON-serialisable
            "trailingPE": float("nan"),  # would archive as the invalid literal NaN
        }
    )
    assert set(out) == {"shortName", "marketCap", "companyOfficers"}


def test_select_companies_defaults_to_the_whole_pool_and_rejects_unknowns():
    from pipelines.valuation.config import COMPANIES

    assert len(select_companies("")) == len(COMPANIES)
    assert [c.ticker for c in select_companies("COHR, 300308.SZ")] == ["COHR", "300308.SZ"]
    with pytest.raises(ValueError, match="NOPE"):
        select_companies("COHR,NOPE")


def test_run_checks_report_coverage_missing_quotes_and_stale_ones():
    now = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
    rows = [
        {"ticker": "COHR", "price": 289.64, "price_ts": "2026-09-16T03:59:59+00:00"},
        {"ticker": "IKA.L", "price": None, "price_ts": "2026-09-16T03:59:59+00:00"},
        {"ticker": "5802.T", "price": 3100.0, "price_ts": "2026-09-01T06:00:00+00:00"},  # two weeks old
    ]
    by_name = {c["name"]: c for c in run_checks(rows, ["4977.TW"], 4, now=now)}
    assert by_name["Listing coverage"]["status"] == "fail"
    assert "4977.TW" in by_name["Listing coverage"]["detail"]
    assert by_name["Quote present"]["status"] == "fail"
    assert "IKA.L" in by_name["Quote present"]["detail"]
    assert by_name["Quote freshness"]["status"] == "warn"  # staleness never hard-fails a run
    assert "5802.T" in by_name["Quote freshness"]["detail"]


def test_run_checks_pass_on_a_clean_run():
    now = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
    rows = [parse_info("COHR", COHR, snapshot_ts="2026-09-16T18:30:00+00:00")]
    assert [c["status"] for c in run_checks(rows, [], 1, now=now)] == ["pass"] * 4


def test_freshness_does_not_report_a_pass_it_did_not_verify():
    """A row with no `regularMarketTime` is an unknown age, not a fresh quote."""
    now = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
    rows = [
        {"ticker": "COHR", "price": 289.64, "price_ts": "2026-09-16T03:59:59+00:00"},
        {"ticker": "600487.SS", "price": 31.0, "price_ts": None},  # suspended: month-old price, no ts
    ]
    freshness = next(c for c in run_checks(rows, [], 2, now=now) if c["name"] == "Quote freshness")
    assert freshness["status"] == "warn"  # not "pass"
    assert "600487.SS" in freshness["detail"]


def test_cap_share_consistency_records_the_rows_on_different_bases():
    """The H float against a whole-issuer cap (~21x) and a pence quote against a cap in pounds (100x)."""
    now = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
    rows = [parse_info(t, info, snapshot_ts="t") for t, info in [("COHR", COHR), ("3750.HK", CATL_H), ("IKA.L", IKA_L)]]
    cap = next(c for c in run_checks(rows, [], 3, now=now) if c["name"] == "Cap/share consistency")
    assert cap["status"] == "warn"  # known and expected, so it never hard-fails the run
    assert "3750.HK" in cap["detail"] and "IKA.L" in cap["detail"]
    assert "COHR" not in cap["detail"]  # a plain US line is on one basis and stays quiet


def test_select_companies_deduplicates_so_the_run_record_matches_the_file():
    assert [c.ticker for c in select_companies("COHR,COHR")] == ["COHR"]
    assert [c.ticker for c in select_companies("COHR,QS,COHR")] == ["COHR", "QS"]


# --- network layer: yfinance is mocked, nothing below reaches Yahoo ------------------------
QS = {
    "shortName": "QuantumScape Corporation",
    "currentPrice": 9.12,
    "regularMarketPrice": 9.12,
    "currency": "USD",
    "financialCurrency": "USD",
    "marketCap": 5460000000,
    "sharesOutstanding": 598684000,
    "regularMarketTime": 1789588799,
}

STUB = {"trailingPegRatio": None}  # Yahoo's answer for a symbol it does not know


class _Rate(Exception):
    """Stands in for yfinance's YFRateLimitError (name-matched, so the class need not be imported)."""


def _install_ticker(monkeypatch, feed: dict):
    """Point `yf.Ticker` at `{ticker: payload | Exception | [one per attempt]}`; returns the call log."""
    calls: list[str] = []

    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            self._ticker = ticker
            calls.append(ticker)

        @property
        def info(self):
            answer = feed[self._ticker]
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(mod.yf, "Ticker", FakeTicker)
    return calls


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Every path the module writes to, inside tmp_path; no backoff sleeps."""
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    return tmp_path


def _paths(sandbox):
    day, _ = run_stamp(utc_now())
    return sandbox / "snap" / "valuation" / "prices" / f"{day}.parquet", sandbox / "raw" / "valuation" / "yahoo_info" / day


def _last_run(sandbox) -> dict:
    return json.loads((sandbox / "snap" / "valuation" / "runs.jsonl").read_text().splitlines()[-1])


def test_main_writes_the_rows_and_archives_every_payload(sandbox, monkeypatch):
    _install_ticker(monkeypatch, {"COHR": COHR, "QS": QS})

    assert mod.main(["--tickers", "COHR,QS", "--min-interval", "0"]) == 0

    snap, raw_dir = _paths(sandbox)
    df = pl.read_parquet(snap)
    assert df["ticker"].to_list() == ["COHR", "QS"]
    assert df["price"].to_list() == [289.64, 9.12]
    assert sorted(p.name for p in raw_dir.iterdir()) == ["COHR.json.gz", "QS.json.gz"]
    assert read_json_gz(raw_dir / "COHR.json.gz") == COHR  # archived as fetched, before parsing
    assert _last_run(sandbox)["rows"] == 2 and _last_run(sandbox)["failed"] == []


def test_main_isolates_one_failing_ticker_and_still_writes_the_rest(sandbox, monkeypatch):
    """The non-negotiable isolation rule: one dead listing must not cost the others."""
    _install_ticker(monkeypatch, {"COHR": COHR, "QS": _Rate("Too Many Requests"), "300308.SZ": STUB})

    assert mod.main(["--tickers", "COHR,QS,300308.SZ", "--min-interval", "0"]) == 1  # red, after the good data landed

    snap, raw_dir = _paths(sandbox)
    assert pl.read_parquet(snap)["ticker"].to_list() == ["COHR"]
    assert [p.name for p in raw_dir.iterdir()] == ["COHR.json.gz"]  # no archive for what never resolved

    run = _last_run(sandbox)
    assert run["failed"] == ["QS", "300308.SZ"]
    assert "Too Many Requests" in run["errors"][0] and run["errors"][0].startswith("QS: ")
    assert "stub payload" in run["errors"][1]  # the reason survives in the permanent record
    coverage = next(c for c in run["checks"] if c["name"] == "Listing coverage")
    assert coverage["status"] == "fail" and "QS" in coverage["detail"]


def test_a_partial_rerun_never_shrinks_the_days_snapshot(sandbox, monkeypatch):
    """The 06:47 run prices the pool; a hand-run at 20:00 covering one ticker must only add to it."""
    _install_ticker(monkeypatch, {"COHR": COHR, "QS": QS, "3750.HK": CATL_H})
    assert mod.main(["--tickers", "COHR,QS,3750.HK", "--min-interval", "0"]) == 0

    _install_ticker(monkeypatch, {"COHR": {**COHR, "currentPrice": 301.11, "regularMarketPrice": 301.11}})
    assert mod.main(["--tickers", "COHR", "--min-interval", "0"]) == 0

    snap, raw_dir = _paths(sandbox)
    df = pl.read_parquet(snap)
    assert sorted(df["ticker"].to_list()) == ["3750.HK", "COHR", "QS"]  # the other two survived
    assert df.filter(pl.col("ticker") == "COHR")["price"].item() == 301.11  # the fresh row won
    assert df.height == 3
    # the morning's evidence survives too: the differing payload lands beside it, not over it
    assert len(list(raw_dir.glob("COHR*.json.gz"))) == 2
    assert read_json_gz(raw_dir / "COHR.json.gz")["currentPrice"] == 289.64


def test_a_run_that_prices_nothing_leaves_the_good_file_alone(sandbox, monkeypatch):
    _install_ticker(monkeypatch, {"COHR": COHR, "QS": QS})
    assert mod.main(["--tickers", "COHR,QS", "--min-interval", "0"]) == 0
    snap, _ = _paths(sandbox)
    before = snap.read_bytes()

    _install_ticker(monkeypatch, {"300308.SZ": _Rate("Too Many Requests")})
    assert mod.main(["--tickers", "300308.SZ", "--min-interval", "0"]) == 1

    assert snap.read_bytes() == before  # not blanked, not shrunk
    assert _last_run(sandbox)["path"] is None and _last_run(sandbox)["rows"] == 0


def test_write_snapshot_validates_before_anything_reaches_disk(sandbox):
    bad = parse_info("COHR", COHR, snapshot_ts="t") | {"price": -1.0}  # Check.gt(0) in PRICES_SCHEMA
    with pytest.raises(SchemaError):
        write_snapshot([bad], "2026-09-16")
    assert not (sandbox / "snap" / "valuation" / "prices").exists()


def test_an_empty_payload_is_retried_but_a_stub_is_not(sandbox, monkeypatch):
    """A 403/500/503 arrives as `{}` in recent yfinance; a brief outage is not 16 delisted companies."""
    calls = _install_ticker(monkeypatch, {"COHR": [{}, {}, COHR], "ZZZZ.NOPE": STUB})

    assert fetch_info("COHR", throttle=Throttle(0), attempts=3) == COHR
    assert calls.count("COHR") == 3

    with pytest.raises(LookupError, match="stub payload"):
        fetch_info("ZZZZ.NOPE", throttle=Throttle(0), attempts=3)
    assert calls.count("ZZZZ.NOPE") == 1  # an unknown symbol stays unknown; retrying burns the budget


def test_an_empty_payload_that_never_fills_in_raises_after_the_last_attempt(sandbox, monkeypatch):
    calls = _install_ticker(monkeypatch, {"COHR": {}})
    with pytest.raises(LookupError, match="empty payload"):
        fetch_info("COHR", throttle=Throttle(0), attempts=3)
    assert len(calls) == 3


def test_a_rate_limit_is_retried_and_a_missing_symbol_is_not(sandbox, monkeypatch):
    calls = _install_ticker(
        monkeypatch,
        {"COHR": [_Rate("Too Many Requests"), COHR], "ZZZZ.NOPE": RuntimeError("404 Not Found: possibly delisted")},
    )

    assert fetch_info("COHR", throttle=Throttle(0), attempts=4)["currentPrice"] == 289.64
    assert calls.count("COHR") == 2

    with pytest.raises(RuntimeError, match="404"):
        fetch_info("ZZZZ.NOPE", throttle=Throttle(0), attempts=4)
    assert calls.count("ZZZZ.NOPE") == 1


def test_archive_keeps_the_first_payload_of_the_day(tmp_path):
    first = archive_info(COHR, tmp_path, "300308.SZ", "0647")
    assert first.name == "300308_SZ.json.gz"  # the symbol is made filename-safe

    same = archive_info(COHR, tmp_path, "300308.SZ", "2000")
    assert same == first  # identical payload: left alone, no duplicate

    changed = archive_info({**COHR, "currentPrice": 301.11}, tmp_path, "300308.SZ", "2000")
    assert changed.name == "300308_SZ__2000.json.gz"
    assert read_json_gz(first)["currentPrice"] == 289.64  # the morning's evidence survives


def test_throttle_paces_and_counts_the_calls():
    import time as _time

    pacer = Throttle(0.02)
    started = _time.monotonic()
    for _ in range(3):
        pacer.tick()
    assert pacer.calls == 3
    assert _time.monotonic() - started >= 0.03  # two gaps of 20 ms between three calls
