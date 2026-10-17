"""The cyber consensus capture: parsers on fixtures, isolation of failures, and the written table."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import polars as pl
import pytest

from pipelines.cyber import capture


def _epoch(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


INFO = {
    "currentPrice": 225.6,
    "regularMarketTime": 1_760_000_000,
    "currency": "USD",
    "financialCurrency": "USD",
    "marketCap": 231.0e9,
    "enterpriseValue": 228.7e9,
    "targetMeanPrice": 246.0,
    "targetMedianPrice": 250.0,
    "targetHighPrice": 325.0,
    "targetLowPrice": 132.0,
    "numberOfAnalystOpinions": 49,
    "recommendationMean": 1.9,
    "totalRevenue": 4.8e9,
    "freeCashflow": float("nan"),
    "lastFiscalYearEnd": _epoch(2026, 1, 31),
    "nextFiscalYearEnd": _epoch(2027, 1, 31),
    "mostRecentQuarter": _epoch(2026, 7, 31),
}
REVENUE = {
    "0y": {"avg": 5.9e9, "low": 5.8e9, "high": 6.0e9, "n": 40},
    "+1y": {"avg": 7.1e9, "low": 6.9e9, "high": 7.5e9, "n": 38},
}
RATINGS = {"strongBuy": 20, "buy": 25, "hold": 4, "sell": 0, "strongSell": 0}

EM = {
    "yctj_list": [
        {"YEAR": 2024, "YEAR_MARK": "A", "TOTAL_OPERATE_INCOME": 3.3e9, "TOTAL_OPERATE_INCOME_COUNT": None},
        {"YEAR": 2025, "YEAR_MARK": "A", "TOTAL_OPERATE_INCOME": 2.3e9, "TOTAL_OPERATE_INCOME_COUNT": None},
        {"YEAR": 2025, "YEAR_MARK": "E", "TOTAL_OPERATE_INCOME": 9.9e9, "TOTAL_OPERATE_INCOME_COUNT": 9},
        {"YEAR": 2026, "YEAR_MARK": "E", "TOTAL_OPERATE_INCOME": 2.731e9, "TOTAL_OPERATE_INCOME_COUNT": 4},
        {"YEAR": 2027, "YEAR_MARK": "E", "TOTAL_OPERATE_INCOME": 3.1e9, "TOTAL_OPERATE_INCOME_COUNT": 3},
        {"YEAR": 2028, "YEAR_MARK": "E", "TOTAL_OPERATE_INCOME": 3.5e9, "TOTAL_OPERATE_INCOME_COUNT": 2},
    ],
    "pjtj": [
        {"DATE_TYPE": "3月内", "RATING_ORG_NUM": 2},
        {
            "DATE_TYPE": "6月内",
            "RATING_ORG_NUM": 4,
            "RATING_BUY_NUM": 3,
            "RATING_ADD_NUM": 1,
            "RATING_NEUTRAL_NUM": None,
        },
    ],
}


def test_parse_yahoo_reads_every_field_the_questions_need() -> None:
    row = capture.parse_yahoo(INFO, REVENUE, RATINGS)
    assert row["price"] == 225.6 and row["enterprise_value"] == 228.7e9
    assert (row["target_mean"], row["target_high"], row["target_low"], row["n_opinions"]) == (246.0, 325.0, 132.0, 49)
    assert (row["rev_fy0_avg"], row["rev_fy0_n"], row["rev_fy1_avg"], row["rev_fy1_n"]) == (5.9e9, 40, 7.1e9, 38)
    assert row["rating_strong_buy"] == 20 and row["rating_sell"] == 0
    assert row["last_fiscal_year_end"] == "2026-01-31" and row["fy0_end"] == "2027-01-31"
    assert row["ttm_free_cash_flow"] is None, "a NaN is a missing value, not a number"


def test_every_column_is_either_parsed_or_set_elsewhere() -> None:
    parsed = set(capture.parse_yahoo(INFO, REVENUE, RATINGS)) | set(capture.parse_eastmoney(EM, this_year=2026))
    keys = {"snapshot_ts", "ticker", "source_errors"}
    assert set(capture.COLUMNS) - parsed - keys == set(), "a column nothing fills would be silently empty"


def test_pence_label_is_kept_and_fiscal_year_end_falls_back_to_last_plus_one_year() -> None:
    info = {"currentPrice": 154.4, "currency": "GBp", "financialCurrency": "GBP", "lastFiscalYearEnd": "2025-09-30"}
    row = capture.parse_yahoo(info, None, None)
    assert row["currency"] == "GBp" and row["financial_currency"] == "GBP"
    assert row["fy0_end"] == "2026-09-30"


def test_junk_values_become_null_rather_than_poison_the_frame() -> None:
    row = capture.parse_yahoo(
        {"currentPrice": -3, "targetMeanPrice": 0, "numberOfAnalystOpinions": -1, "marketCap": True}, {}, {}
    )
    assert (
        row["price"] is None and row["target_mean"] is None and row["n_opinions"] is None and row["market_cap"] is None
    )


def test_parse_eastmoney_picks_the_next_two_estimate_years_and_the_six_month_window() -> None:
    row = capture.parse_eastmoney(EM, this_year=2026)
    assert (row["em_year0"], row["em_rev0_mean"], row["em_rev0_n"]) == (2026, 2.731e9, 4)
    assert (row["em_year1"], row["em_rev1_n"]) == (2027, 3)
    assert (row["em_actual_year"], row["em_actual_revenue"]) == (2025, 2.3e9)
    assert (row["em_rating_orgs_6m"], row["em_rating_buy_6m"], row["em_rating_add_6m"]) == (4, 3, 1)
    assert row["em_rating_neutral_6m"] is None


def test_parse_eastmoney_tolerates_an_empty_payload() -> None:
    assert capture.parse_eastmoney({}, this_year=2026) == {}
    assert capture.parse_eastmoney(None, this_year=2026) == {}


def _fake_yahoo(ticker: str):
    if ticker == "BAD":
        raise RuntimeError("429 Too Many Requests")
    if ticker == "EMPTY":
        return {}, {}, {}, ["info: empty"]
    return INFO, REVENUE, RATINGS, []


def test_one_listing_failing_never_costs_the_others() -> None:
    rows, failed = capture.capture(
        ["CRWD", "BAD", "EMPTY", "002439.SZ"],
        snapshot_ts="2026-10-26T07:50:00+00:00",
        year=2026,
        yahoo=_fake_yahoo,
        eastmoney=lambda t: EM,
    )
    assert [r["ticker"] for r in rows] == ["CRWD", "BAD", "EMPTY", "002439.SZ"]
    assert set(failed) == {"BAD", "EMPTY"}
    by = {r["ticker"]: r for r in rows}
    assert by["CRWD"]["target_mean"] == 246.0 and "em_year0" not in by["CRWD"], "East Money is for A-shares only"
    assert by["002439.SZ"]["em_rev0_n"] == 4 and by["002439.SZ"]["rev_fy1_n"] == 38


def test_frame_validates_and_the_schema_rejects_a_negative_count() -> None:
    rows, _ = capture.capture(["CRWD"], snapshot_ts="t", year=2026, yahoo=_fake_yahoo, eastmoney=None)
    df = capture.consensus_frame(rows)
    capture.CONSENSUS_SCHEMA.validate(df)
    assert df.columns == list(capture.COLUMNS)
    bad = df.with_columns(pl.lit(-1).cast(pl.Int64).alias("n_opinions"))
    with pytest.raises(Exception):  # noqa: B017 - pandera's error class is not worth importing here
        capture.CONSENSUS_SCHEMA.validate(bad)


def test_main_writes_a_table_and_a_run_record_and_never_a_raw_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capture, "SNAP", tmp_path)
    monkeypatch.setattr(capture, "RUNS", tmp_path / "consensus_runs.jsonl")
    monkeypatch.setattr(
        capture, "ring", lambda name: [type("E", (), {"ticker": t}) for t in ("CRWD", "ZS", "002439.SZ")]
    )
    monkeypatch.setattr(capture, "fetch_yahoo", lambda t, pacer: _fake_yahoo(t))
    monkeypatch.setattr(capture, "fetch_eastmoney", lambda http, t: EM)
    assert capture.main(["--min-interval", "0"]) == 0
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert len(files) == 2 and files[0].startswith("consensus/") and files[1] == "consensus_runs.jsonl", files
    df = pl.read_parquet(next((tmp_path / "consensus").glob("*.parquet")))
    assert df.height == 3 and df["target_mean"].null_count() == 0
    record = json.loads((tmp_path / "consensus_runs.jsonl").read_text().splitlines()[-1])
    assert record["priced"] == 3 and record["failed"] == {} and not math.isnan(record["seconds"])


def test_a_throttled_run_is_reported_and_does_not_blank_the_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capture, "SNAP", tmp_path)
    monkeypatch.setattr(capture, "RUNS", tmp_path / "consensus_runs.jsonl")
    monkeypatch.setattr(capture, "ring", lambda name: [type("E", (), {"ticker": t}) for t in ("CRWD", "ZS")])
    monkeypatch.setattr(capture, "fetch_yahoo", lambda t, pacer: ({}, {}, {}, ["info: empty"]))
    assert capture.main(["--min-interval", "0"]) == 1
    assert not list((tmp_path / "consensus").glob("*.parquet")), "a run that priced nothing writes nothing"
    assert json.loads((tmp_path / "consensus_runs.jsonl").read_text().splitlines()[-1])["priced"] == 0


def test_an_estimate_row_with_no_analysts_is_a_missing_value_not_a_zero() -> None:
    row = capture.parse_yahoo(INFO, {"0y": {"avg": 0.0, "low": 0.0, "high": 0.0, "n": 0}, "+1y": REVENUE["+1y"]}, {})
    assert row["rev_fy0_avg"] is None and row["rev_fy0_n"] == 0 and row["rev_fy1_avg"] == 7.1e9


def test_an_empty_answer_is_retried_with_a_pause_and_a_good_answer_ends_the_loop() -> None:
    answers = iter([{}, None, {"currentPrice": 1.0}])
    pauses: list[float] = []
    got, problem = capture._attempt(lambda: next(answers), capture.Pacer(0), pauses.append)
    assert got == {"currentPrice": 1.0} and problem is None and pauses == [5.0, 15.0]


def test_a_call_that_keeps_failing_gives_up_after_three_attempts_and_says_why() -> None:
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("429 Too Many Requests")

    got, problem = capture._attempt(boom, capture.Pacer(0), lambda s: None)
    assert got is None and len(calls) == capture.ATTEMPTS and "429" in problem


def test_a_rerun_replaces_its_own_rows_and_keeps_other_runs_of_the_day(tmp_path) -> None:
    a = capture.consensus_frame([{"snapshot_ts": "t1", "ticker": "X", "price": 1.0}])
    b = capture.consensus_frame([{"snapshot_ts": "t2", "ticker": "X", "price": 2.0}])
    path = tmp_path / "d.parquet"
    capture.write_parquet(a, path)
    both = capture._merge_existing(b, path)
    assert sorted(both["snapshot_ts"].to_list()) == ["t1", "t2"]
    capture.write_parquet(both, path)
    again = capture._merge_existing(capture.consensus_frame([{"snapshot_ts": "t2", "ticker": "X", "price": 3.0}]), path)
    assert again.filter(pl.col("snapshot_ts") == "t2")["price"].to_list() == [3.0] and again.height == 2


def test_every_a_share_without_an_east_money_forecast_is_allow_listed() -> None:
    """estimates_em exits 1 on a listing with no forecast unless it is in EXPECTED_EMPTY, and a red job every Monday
    hides a real outage. The baseline says which ring A A-shares East Money does not cover."""
    from pipelines.cyber import frozen as F
    from pipelines.valuation.estimates_em import EXPECTED_EMPTY

    baseline = F.load_baseline()
    uncovered = {
        r["ticker"] for r in baseline.iter_rows(named=True) if F.is_china(r["ticker"]) and r["em_year0"] is None
    }
    assert uncovered <= EXPECTED_EMPTY, f"add to estimates_em.EXPECTED_EMPTY: {sorted(uncovered - EXPECTED_EMPTY)}"
    assert {"002912.SZ", "688023.SS", "688201.SS"} <= uncovered
