"""East Money valuation history and the China panel: parsers on saved responses, paging, percentiles, the panel,
its flag, and a run that loses one listing without losing the others.

The ``em_*.json`` fixtures in ``fixtures/`` are real responses fetched on 2026-10-17, trimmed: the two valuation pages
are the newest 12 trading days of Venustech (``002439``) with ``pages`` and ``count`` rewritten to match the 12 rows,
the empty answer is East Money's, byte for byte, and the two revenue pages are the newest six report dates of
Venustech and ABT Networks (``688168``). Nothing here touches the network or the real snapshot directory.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from pipelines.cyber import valuation_history_em as V
from pipelines.cyber.pool import POOL, Entry

FX = Path(__file__).parent / "fixtures"
FLAG = V.FLAG_PS_HIGH_REVENUE_FALLING


def fixture(name: str) -> Any:
    return json.loads((FX / name).read_text(encoding="utf-8"))


PAGE1, PAGE2 = fixture("em_valuation_page1.json"), fixture("em_valuation_page2.json")
EMPTY = fixture("em_valuation_empty.json")
REV_VENUSTECH, REV_ABT = fixture("em_revenue_002439.json"), fixture("em_revenue_688168.json")


# --- helpers -------------------------------------------------------------------------------------------------
def days(n: int, start: date = date(2020, 1, 1)) -> list[str]:
    return [(start + timedelta(i)).isoformat() for i in range(n)]


def hist(values: list[float | None], *, start: date = date(2020, 1, 1)) -> list[dict[str, Any]]:
    """Parsed-history rows, one per calendar day, with the same value for PS_TTM and PB."""
    return [
        {"date": d, "ps_ttm": v, "pb": v, "pe_ttm": None, "total_market_cap": 1e9}
        for d, v in zip(days(len(values), start), values, strict=True)
    ]


def rev(cur: float | None, prior: float | None, *, end: str = "2026-06-30", notice: str = "2026-08-26") -> list[dict]:
    """Two cumulative report rows a year apart, newest first as the caller may hold them (order must not matter)."""
    prior_end = f"{int(end[:4]) - 1}{end[4:]}"
    return [
        {"period_end": end, "revenue": cur, "yoy_reported": None, "notice_date": notice},
        {"period_end": prior_end, "revenue": prior, "yoy_reported": None, "notice_date": f"{prior_end[:4]}-08-26"},
    ]


def listing(ticker: str, ring: str = "A", name: str = "Co", name_cn: str | None = "公司") -> dict[str, Any]:
    return {"ticker": ticker, "name": name, "name_cn": name_cn, "ring": ring}


class FakeHttp:
    """Answers ``get_json`` from a list of payloads, one per call, and keeps the calls it was asked."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    @property
    def n(self) -> int:
        return len(self.calls)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append({"path": path, **(params or {})})
        return self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]


def page(rows: list[dict[str, Any]], *, pages: int, count: int, code: str = "002439") -> dict[str, Any]:
    data = [
        {"SECURITY_CODE": code, "TRADE_DATE": f"{r['date']} 00:00:00", "PS_TTM": r["ps_ttm"], "PB_MRQ": r["pb"]}
        for r in rows
    ]
    return {"success": True, "message": "ok", "code": 0, "result": {"pages": pages, "count": count, "data": data}}


# --- parse ---------------------------------------------------------------------------------------------------
def test_parse_keeps_the_five_fields_and_drops_every_other_column() -> None:
    rows = V.parse(PAGE1, code="002439")
    assert len(rows) == 6
    assert set(rows[0]) == {"date", "ps_ttm", "pb", "pe_ttm", "total_market_cap"}
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows), "oldest first"
    newest = V.parse(PAGE1)[-1]
    assert newest == {
        "date": "2026-10-16",
        "ps_ttm": 6.91699422,
        "pb": 1.55226953,
        "pe_ttm": -37.65151629,
        "total_market_cap": 16874804855.65,
    }, "TRADE_DATE loses its time of day, PB_MRQ becomes pb, and a negative PE (a loss) is kept as served"


def test_the_two_pages_of_the_fixture_are_disjoint_and_cover_twelve_days() -> None:
    both = V.parse(PAGE1) + V.parse(PAGE2)
    assert len({r["date"] for r in both}) == 12
    assert V.page_info(PAGE1) == (2, 12)


def test_parse_turns_junk_into_missing_values_rather_than_zeros() -> None:
    payload = {
        "success": True,
        "result": {
            "data": [
                {
                    "TRADE_DATE": "2026-01-02 00:00:00",
                    "PS_TTM": "7.5",
                    "PB_MRQ": "",
                    "PE_TTM": "-",
                    "TOTAL_MARKET_CAP": 2e9,
                },
                {
                    "TRADE_DATE": "2026-01-05",
                    "PS_TTM": float("nan"),
                    "PB_MRQ": True,
                    "PE_TTM": None,
                    "TOTAL_MARKET_CAP": 0,
                },
                {"TRADE_DATE": "2026-01-06", "PS_TTM": -3.0, "PB_MRQ": -1.2, "PE_TTM": -50.0, "TOTAL_MARKET_CAP": -5},
                {"TRADE_DATE": "2026-01-07", "PS_TTM": float("inf"), "PB_MRQ": "abc", "PE_TTM": 20.0},
            ]
        },
    }
    a, b, c, d = V.parse(payload)
    assert a["ps_ttm"] == 7.5, "a number served as a string is still a number"
    assert (a["pb"], a["pe_ttm"]) == (None, None)
    assert (b["ps_ttm"], b["pb"], b["total_market_cap"]) == (None, None, None), "NaN, a boolean and zero are not data"
    assert (c["ps_ttm"], c["pb"], c["total_market_cap"]) == (None, None, None), "a negative multiple ranks nothing"
    assert c["pe_ttm"] == -50.0, "a loss is a fact about the company and PE is not ranked here"
    assert (d["ps_ttm"], d["pb"], d["total_market_cap"]) == (None, None, None) and d["pe_ttm"] == 20.0


def test_parse_drops_undated_rows_and_keeps_the_last_of_a_repeated_day() -> None:
    payload = {
        "success": True,
        "result": {
            "data": [
                {"TRADE_DATE": None, "PS_TTM": 1.0},
                {"TRADE_DATE": "not a date", "PS_TTM": 2.0},
                {"TRADE_DATE": "2026-02-30 00:00:00", "PS_TTM": 3.0},
                {"TRADE_DATE": "2026-03-02 00:00:00", "PS_TTM": 4.0},
                {"TRADE_DATE": "2026-03-02 00:00:00", "PS_TTM": 5.0},
                "not a record",
            ]
        },
    }
    rows = V.parse(payload)
    assert [(r["date"], r["ps_ttm"]) for r in rows] == [("2026-03-02", 5.0)]


def test_an_empty_answer_is_no_rows_and_every_other_failure_raises() -> None:
    assert EMPTY["code"] == 9201 and EMPTY["success"] is False, "the fixture is East Money's own empty answer"
    assert V.parse(EMPTY) == [] and V.page_info(EMPTY) == (0, 0)
    for bad in ({"success": False, "code": 500, "message": "busy", "result": None}, "<html>blocked</html>", None, []):
        with pytest.raises(V.SourceError):
            V.parse(bad)


def test_a_row_for_another_security_raises_because_the_filter_was_ignored() -> None:
    with pytest.raises(V.SourceError, match="002439"):
        V.parse(page(hist([1.0]), pages=1, count=1, code="600000"), code="002439")
    assert V.parse(PAGE1, code="002439"), "the right security passes"


def test_page_info_tolerates_missing_and_odd_values() -> None:
    assert V.page_info({"result": {"pages": "3", "count": 2130.0}}) == (3, 2130)
    assert V.page_info({"result": {"pages": None, "count": -4}}) == (0, 0)
    assert V.page_info({"result": None}) == (0, 0)
    assert V.page_info("nope") == (0, 0)


def test_em_code_is_the_bare_six_digits_for_both_exchanges_and_nothing_else() -> None:
    assert V.em_code("002439.SZ") == "002439" and V.em_code("688561.SS") == "688561"
    for bad in ("CRWD", "0700.HK", "SZ002439", "2439.SZ", "00243A.SZ", "002439.SH", ""):
        with pytest.raises(ValueError):
            V.em_code(bad)


# --- percentile ----------------------------------------------------------------------------------------------
def test_percentile_is_the_share_of_days_at_or_below_the_latest_with_the_latest_included() -> None:
    assert V.percentile(dict(zip(days(4), [3.0, 1.0, 2.0, 2.5], strict=True))) == 0.75
    assert V.percentile(dict(zip(days(4), [1.0, 2.0, 3.0, 4.0], strict=True))) == 1.0, "a new high is exactly 1"
    assert V.percentile(dict(zip(days(4), [4.0, 3.0, 2.0, 1.0], strict=True))) == 0.25, "the cheapest day is 1/n"
    assert V.percentile(dict(zip(days(1), [5.0], strict=True))) == 1.0


def test_ties_count_as_at_or_below() -> None:
    assert V.percentile(dict(zip(days(4), [2.0, 2.0, 2.0, 2.0], strict=True))) == 1.0
    assert V.percentile(dict(zip(days(5), [1.0, 2.0, 2.0, 3.0, 2.0], strict=True))) == 0.8


def test_percentile_on_the_real_fixture_rank_of_the_cheapest_day() -> None:
    series = {r["date"]: r["ps_ttm"] for r in V.parse(PAGE1) + V.parse(PAGE2)}
    assert max(series) == "2026-10-16"
    assert series["2026-10-16"] == min(series.values()), "the newest of the 12 days is the cheapest"
    assert V.percentile(series) == pytest.approx(1 / 12)


def test_as_of_cuts_the_history_there_and_reads_the_value_on_that_day() -> None:
    series = dict(zip(days(5), [1.0, 5.0, 3.0, 2.0, 9.0], strict=True))
    assert V.percentile(series, days(5)[2]) == pytest.approx(2 / 3), "3.0 against 1, 5, 3; later days are not seen"
    assert V.percentile(series, days(5)[4]) == 1.0
    assert V.percentile(series, date.fromisoformat(days(5)[0])) == 1.0, "a date object works as well as a string"


def test_a_missing_value_on_as_of_is_none_and_never_the_previous_days_percentile() -> None:
    series = {"2026-01-02": 1.0, "2026-01-03": 2.0, "2026-01-04": None}
    assert V.percentile(series) is None
    assert V.percentile(series, "2026-01-03") == 1.0
    assert V.percentile(series, "2026-01-09") is None, "a day the series does not have"
    assert V.percentile({}) is None
    assert V.percentile({"2026-01-02": float("nan")}) is None


def test_days_without_a_value_are_left_out_of_the_count() -> None:
    series = {"2026-01-02": 1.0, "2026-01-03": None, "2026-01-04": float("nan"), "2026-01-05": 2.0}
    assert V.percentile(series) == 1.0
    assert V.history_window(series) == [("2026-01-02", 1.0), ("2026-01-05", 2.0)]


def test_input_order_date_format_and_pairs_do_not_matter() -> None:
    pairs = [("2026-01-05 00:00:00", 3.0), ("2026-01-02", 1.0), (date(2026, 1, 3), 2.0)]
    assert V.percentile(pairs) == 1.0
    assert V.history_window(pairs)[0] == ("2026-01-02", 1.0)
    assert V.percentile(list(reversed(pairs))) == 1.0


def test_history_before_the_start_date_is_not_ranked() -> None:
    series = {"2017-12-29": 100.0, "2018-01-02": 1.0, "2018-01-03": 2.0}
    assert V.percentile(series) == 1.0, "the 2017 day is outside the window and cannot lower the rank"
    assert V.percentile(series, start="2017-01-01") == pytest.approx(2 / 3)
    assert V.HISTORY_START == "2018-01-01"


def test_min_days_withholds_a_percentile_on_a_short_history() -> None:
    series = dict(zip(days(10), range(10), strict=True))
    assert V.percentile(series, min_days=10) == 1.0
    assert V.percentile(series, min_days=11) is None
    assert V.percentile(series) == 1.0, "no minimum by default for the pure function"


# --- revenue -------------------------------------------------------------------------------------------------
def test_parse_revenue_reads_cumulative_revenue_reported_growth_as_a_fraction_and_the_notice_date() -> None:
    rows = V.parse_revenue(REV_VENUSTECH, code="002439")
    assert [r["period_end"] for r in rows] == sorted(r["period_end"] for r in rows)
    assert len(rows) == 6 and rows[-1] == {
        "period_end": "2026-06-30",
        "revenue": 1234618155.18,
        "yoy_reported": pytest.approx(0.0900916),
        "notice_date": "2026-08-22",
    }
    assert V.parse_revenue(REV_ABT)[-1]["yoy_reported"] == pytest.approx(-0.694578, abs=1e-6)


def test_parse_revenue_skips_rows_with_nothing_in_them_and_rejects_impossible_growth() -> None:
    payload = {
        "success": True,
        "result": {
            "data": [
                {"REPORTDATE": "2026-03-31 00:00:00", "TOTAL_OPERATE_INCOME": None, "YSTZ": None},
                {"REPORTDATE": "2026-02-28 00:00:00", "TOTAL_OPERATE_INCOME": 5.0, "YSTZ": 1.0},
                {"REPORTDATE": "2026-06-30 00:00:00", "TOTAL_OPERATE_INCOME": 7.0, "YSTZ": -250.0},
                {"REPORTDATE": None, "TOTAL_OPERATE_INCOME": 9.0},
            ]
        },
    }
    rows = V.parse_revenue(payload)
    assert [(r["period_end"], r["revenue"], r["yoy_reported"]) for r in rows] == [("2026-06-30", 7.0, None)]
    with pytest.raises(V.SourceError):
        V.parse_revenue({"success": False, "code": 1})
    assert V.parse_revenue(EMPTY) == []
    with pytest.raises(V.SourceError):
        V.parse_revenue(REV_ABT, code="002439")


def test_period_labels_are_cumulative_year_to_date() -> None:
    assert [V.period_label(d) for d in ("2026-03-31", "2026-06-30", "2026-09-30", "2025-12-31")] == [
        "2026Q1",
        "2026H1",
        "2026Q1-Q3",
        "2025FY",
    ]
    assert V.period_label("2026-05-31") is None and V.period_label("garbage") is None


def test_revenue_growth_divides_cumulative_revenue_by_the_same_months_a_year_earlier() -> None:
    got = V.revenue_growth(V.parse_revenue(REV_VENUSTECH))
    assert got is not None
    assert got["revenue_period"] == "2026H1" and got["period_end"] == "2026-06-30" and got["basis"] == "computed"
    assert got["revenue_yoy"] == pytest.approx(1234618155.18 / 1132582026.18 - 1)
    assert got["revenue_yoy"] == pytest.approx(V.parse_revenue(REV_VENUSTECH)[-1]["yoy_reported"], abs=1e-9), (
        "our division agrees with East Money's own figure"
    )
    assert got["revenue"] == 1234618155.18 and got["prior_revenue"] == 1132582026.18


def test_revenue_growth_is_negative_when_revenue_fell() -> None:
    got = V.revenue_growth(V.parse_revenue(REV_ABT))
    assert got is not None and got["revenue_yoy"] == pytest.approx(130884844.68 / 428537935.15 - 1)
    assert got["revenue_yoy"] < -0.69


def test_revenue_growth_ignores_a_report_published_after_as_of() -> None:
    rows = V.parse_revenue(REV_ABT)
    assert V.revenue_growth(rows, "2026-08-25")["revenue_period"] == "2026Q1", "H1 was noticed on 2026-08-26"
    assert V.revenue_growth(rows, "2026-08-26")["revenue_period"] == "2026H1"
    assert V.revenue_growth(rows, "2026-04-20")["revenue_period"] == "2025FY"
    assert V.revenue_growth(rows, date(2026, 4, 21))["revenue_period"] == "2026Q1"


def test_revenue_growth_falls_back_to_east_moneys_figure_only_when_the_prior_year_row_is_missing() -> None:
    rows = V.parse_revenue(REV_ABT)
    got = V.revenue_growth(rows, "2025-12-31")
    assert got is not None and got["revenue_period"] == "2025Q1-Q3" and got["basis"] == "reported"
    assert got["revenue_yoy"] == pytest.approx(0.681699, abs=1e-6) and got["prior_revenue"] is None


def test_revenue_growth_uses_an_older_period_only_when_the_newer_has_nothing_to_offer() -> None:
    rows = [
        {"period_end": "2025-06-30", "revenue": 50.0, "yoy_reported": None, "notice_date": "2025-08-20"},
        {"period_end": "2026-06-30", "revenue": 60.0, "yoy_reported": None, "notice_date": "2026-08-20"},
        {"period_end": "2026-09-30", "revenue": 90.0, "yoy_reported": None, "notice_date": "2026-10-28"},
    ]
    got = V.revenue_growth(rows)
    assert got is not None and got["revenue_period"] == "2026H1" and got["revenue_yoy"] == pytest.approx(0.2)
    assert V.revenue_growth(rows[:1]) is None, "one report and no growth figure is nothing to publish"


def test_a_zero_or_missing_prior_revenue_never_divides() -> None:
    assert V.revenue_growth(rev(10.0, 0.0)) is None
    assert V.revenue_growth(rev(10.0, None)) is None
    assert V.revenue_growth(rev(None, 10.0)) is None
    assert V.revenue_growth(rev(-5.0, 10.0)) is None, "negative revenue is a data error, not a 150% fall"
    assert V.revenue_growth(rev(0.0, 10.0))["revenue_yoy"] == -1.0, "a company that sold nothing fell by all of it"
    assert V.revenue_growth([]) is None and V.revenue_growth(None) is None


# --- fetching ------------------------------------------------------------------------------------------------
def test_fetch_reads_every_page_and_returns_the_whole_history_oldest_first() -> None:
    http = FakeHttp(PAGE1, PAGE2)
    rows = V.fetch("002439.SZ", http)
    assert http.n == 2 and len(rows) == 12
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert rows[-1]["date"] == "2026-10-16" and rows[-1]["ps_ttm"] == 6.91699422
    first = http.calls[0]
    assert first["path"] == V.EM_URL and first["reportName"] == "RPT_VALUEANALYSIS_DET"
    assert first["filter"] == """(SECURITY_CODE="002439")(TRADE_DATE>='2018-01-01')"""
    assert (first["pageNumber"], http.calls[1]["pageNumber"]) == (1, 2)
    assert first["pageSize"] == V.PAGE_SIZE and first["sortColumns"] == "TRADE_DATE" and first["sortTypes"] == -1


def test_fetch_stops_at_the_last_page_it_was_told_about() -> None:
    single = page(hist([1.0, 2.0, 3.0]), pages=1, count=3)
    http = FakeHttp(single)
    assert len(V.fetch("002439.SZ", http)) == 3 and http.n == 1


def test_fetch_ends_on_east_moneys_empty_answer_when_no_page_count_is_given() -> None:
    no_pages = page(hist([1.0, 2.0]), pages=0, count=0)
    http = FakeHttp(no_pages, EMPTY)
    assert len(V.fetch("002439.SZ", http)) == 2 and http.n == 2, "the empty page 2 is the end, not a failure"


def test_fetch_raises_when_east_money_has_no_history_at_all() -> None:
    with pytest.raises(V.SourceError, match="no valuation history"):
        V.fetch("002439.SZ", FakeHttp(EMPTY))


def test_fetch_raises_on_a_truncated_history_rather_than_ranking_it() -> None:
    short = page(hist([1.0, 2.0]), pages=1, count=5)
    with pytest.raises(V.SourceError, match="2 of 5"):
        V.fetch("002439.SZ", FakeHttp(short))


def test_fetch_raises_when_a_page_belongs_to_another_security() -> None:
    with pytest.raises(V.SourceError):
        V.fetch("002439.SZ", FakeHttp(page(hist([1.0]), pages=1, count=1, code="600000")))


def test_a_day_repeated_across_pages_because_a_new_day_shifted_them_is_kept_once() -> None:
    rows = hist([1.0, 2.0, 3.0])
    one, two = page(rows[1:], pages=2, count=3), page(rows[:2], pages=2, count=3)
    got = V.fetch("002439.SZ", FakeHttp(one, two))
    assert [r["date"] for r in got] == [r["date"] for r in rows]


def test_fetch_drops_days_before_the_start_date_even_if_east_money_sent_them() -> None:
    rows = hist([9.0, 1.0, 2.0], start=date(2017, 12, 30))  # 2017-12-30, 12-31, 2018-01-01
    got = V.fetch("002439.SZ", FakeHttp(page(rows, pages=1, count=3)))
    assert [r["date"] for r in got] == ["2018-01-01"] and got[0]["ps_ttm"] == 2.0


def test_a_paging_loop_that_never_ends_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(V, "MAX_PAGES", 3)
    endless = page(hist([1.0]), pages=99, count=1)
    http = FakeHttp(endless)
    with pytest.raises(V.SourceError, match="did not end"):
        V.fetch("002439.SZ", http)
    assert http.n == 3


def test_fetch_refuses_a_ticker_that_is_not_an_a_share_before_any_call() -> None:
    http = FakeHttp(PAGE1)
    with pytest.raises(ValueError):
        V.fetch("CRWD", http)
    assert http.n == 0


def test_fetch_revenue_asks_the_report_list_for_the_latest_dates_and_parses_them() -> None:
    http = FakeHttp(REV_VENUSTECH)
    rows = V.fetch_revenue("002439.SZ", http)
    call = http.calls[0]
    assert call["reportName"] == "RPT_LICO_FN_CPD" and call["filter"] == '(SECURITY_CODE="002439")'
    assert call["pageSize"] == V.REVENUE_ROWS and call["sortColumns"] == "REPORTDATE" and call["sortTypes"] == -1
    assert rows[-1]["period_end"] == "2026-06-30"
    with pytest.raises(V.SourceError, match="no financial report"):
        V.fetch_revenue("002439.SZ", FakeHttp(EMPTY))


def test_the_client_paces_calls_and_sends_the_referer() -> None:
    c = V.client()
    try:
        assert c.min_interval == V.MIN_INTERVAL == 0.3
        assert c._client.headers["Referer"].startswith("https://emweb.securities.eastmoney.com")
    finally:
        c.close()


# --- schemas -------------------------------------------------------------------------------------------------
def test_the_history_schema_rejects_a_non_positive_multiple_a_duplicate_day_and_a_foreign_ticker() -> None:
    good = V.history_frame({"002439.SZ": hist([1.0, 2.0])})
    V.HISTORY_SCHEMA.validate(good)
    cases = [
        (good.with_columns(pl.lit(-1.0).alias("ps_ttm")), "greater_than"),
        (good.with_columns(pl.lit(0.0).alias("total_market_cap")), "greater_than"),
        (good.with_columns(pl.lit("2020-01-01").alias("date")), "not unique"),
        (good.with_columns(pl.lit("CRWD").alias("ticker")), "str_matches"),
        (good.with_columns(pl.Series("date", ["01/02/2020", "01/03/2020"])), "str_matches"),
    ]
    for bad, reason in cases:
        with pytest.raises(Exception, match=reason):
            V.HISTORY_SCHEMA.validate(bad)


def test_the_frame_builders_validate_what_they_return() -> None:
    twice = [{"date": "2026-01-02", "ps_ttm": 1.0, "pb": 1.0, "pe_ttm": None, "total_market_cap": 1.0}] * 2
    with pytest.raises(Exception, match="not unique"):
        V.history_frame({"002439.SZ": twice})
    with pytest.raises(Exception, match="greater_than"):
        V.history_frame({"002439.SZ": [{**twice[0], "ps_ttm": -4.0}]})
    with pytest.raises(Exception, match="not unique"):
        V.revenue_frame({"002439.SZ": [{"period_end": "2026-06-30", "revenue": 1.0}] * 2})


def test_the_revenue_schema_rejects_a_duplicate_period_and_a_growth_below_minus_one() -> None:
    rows = V.parse_revenue(REV_VENUSTECH)
    good = V.revenue_frame({"002439.SZ": rows})
    V.REVENUE_SCHEMA.validate(good)
    with pytest.raises(Exception, match="not unique"):
        V.REVENUE_SCHEMA.validate(good.with_columns(pl.lit("2026-06-30").alias("period_end")))
    with pytest.raises(Exception, match="greater_than_or_equal_to"):
        V.REVENUE_SCHEMA.validate(good.with_columns(pl.lit(-1.5).alias("yoy_reported")))


def _panel_one(**changes: Any) -> pl.DataFrame:
    base = V.build(
        {"002439.SZ": hist([1.0, 2.0, 3.0])}, [listing("002439.SZ")], {"002439.SZ": rev(80.0, 100.0)}, min_days=1
    )
    return base.with_columns(*[pl.lit(v, dtype=V.PANEL_DTYPES[k]).alias(k) for k, v in changes.items()])


def test_the_panel_schema_rejects_what_the_panel_must_never_hold() -> None:
    V.CHINA_PANEL_SCHEMA.validate(_panel_one())
    cases = [
        ({"ps_percentile": 1.2}, "less_than_or_equal_to"),
        ({"ps_percentile": 0.0}, "greater_than"),
        ({"pb_percentile": 1.01}, "less_than_or_equal_to"),
        ({"ps_ttm": -2.0}, "greater_than"),
        ({"ps_ttm": None}, "a percentile needs the value"),
        ({"ticker": "0700.HK"}, "str_matches"),
        ({"ring": "C"}, "isin"),
        ({"revenue_yoy": -1.4}, "greater_than_or_equal_to"),
        ({"revenue_period": "2026M5"}, "str_matches"),
        ({"n_history_days": -1}, "greater_than_or_equal_to"),
        ({"as_of": "16/10/2026"}, "str_matches"),
        ({"total_market_cap": 0.0}, "greater_than"),
    ]
    for change, reason in cases:
        with pytest.raises(Exception, match=reason):
            V.CHINA_PANEL_SCHEMA.validate(_panel_one(**change))


def test_the_panel_schema_rejects_a_flag_that_disagrees_with_its_inputs() -> None:
    panel = _panel_one()  # percentile 1.0, revenue fell by 20%, so the flag is true
    assert panel["flags"].struct.field(FLAG).to_list() == [True]
    wrong = panel.with_columns(pl.struct(pl.lit(False).alias(FLAG)).alias("flags"))
    with pytest.raises(Exception, match="flag must follow"):
        V.CHINA_PANEL_SCHEMA.validate(wrong)
    unset = panel.with_columns(pl.struct(pl.lit(None, dtype=pl.Boolean).alias(FLAG)).alias("flags"))
    with pytest.raises(Exception, match="flag must follow"):
        V.CHINA_PANEL_SCHEMA.validate(unset)


def test_the_panel_schema_rejects_two_rows_for_one_ticker_and_half_a_revenue_pair() -> None:
    panel = _panel_one()
    with pytest.raises(Exception):  # noqa: B017
        V.CHINA_PANEL_SCHEMA.validate(pl.concat([panel, panel]))
    with pytest.raises(Exception, match="come together"):
        V.CHINA_PANEL_SCHEMA.validate(panel.with_columns(pl.lit(None, dtype=pl.Utf8).alias("revenue_period")))


# --- build ---------------------------------------------------------------------------------------------------
def test_build_on_the_real_fixture_reads_the_multiple_its_rank_the_market_cap_and_the_growth() -> None:
    history = {"002439.SZ": V.parse(PAGE1) + V.parse(PAGE2)}
    panel = V.build(
        history,
        [listing("002439.SZ", name="Venustech", name_cn="启明星辰")],
        {"002439.SZ": V.parse_revenue(REV_VENUSTECH)},
        min_days=5,
    )
    row = panel.row(0, named=True)
    assert (
        row["ticker"] == "002439.SZ"
        and row["name"] == "Venustech"
        and row["name_cn"] == "启明星辰"
        and row["ring"] == "A"
    )
    assert row["ps_ttm"] == 6.91699422 and row["pb"] == 1.55226953 and row["total_market_cap"] == 16874804855.65
    assert row["ps_percentile"] == pytest.approx(1 / 12) and row["pb_percentile"] == pytest.approx(1 / 12)
    assert row["n_history_days"] == 12 and row["as_of"] == "2026-10-16"
    assert row["history_start"] == min(r["date"] for r in history["002439.SZ"])
    assert row["revenue_yoy"] == pytest.approx(0.0900916, abs=1e-6) and row["revenue_period"] == "2026H1"
    assert row["flags"] == {FLAG: False}, "a low percentile with growing revenue is not the pattern"


def test_the_flag_is_true_for_a_top_quartile_multiple_on_falling_revenue() -> None:
    rising = [1.0 + i / 100 for i in range(300)]  # the newest day is the dearest: percentile 1.0
    falling_back = [3.0 - i / 100 for i in range(300)]  # the newest day is the cheapest: percentile 1/300
    panel = V.build(
        {
            "600001.SS": hist(rising),
            "600002.SS": hist(rising),
            "600003.SS": hist(falling_back),
            "600004.SS": hist(rising),
        },
        [listing("600001.SS"), listing("600002.SS"), listing("600003.SS"), listing("600004.SS")],
        {"600001.SS": rev(80.0, 100.0), "600002.SS": rev(120.0, 100.0), "600003.SS": rev(80.0, 100.0)},
    )
    got = {r["ticker"]: (r["ps_percentile"], r["revenue_yoy"], r["flags"][FLAG]) for r in panel.iter_rows(named=True)}
    assert got["600001.SS"] == (1.0, pytest.approx(-0.2), True), "dear and shrinking"
    assert got["600002.SS"][2] is False, "dear and growing"
    assert got["600003.SS"][2] is False, "shrinking and cheap"
    assert got["600004.SS"] == (1.0, None, None), "no revenue figure: neither flagged nor cleared"


def test_the_top_quartile_starts_at_the_seventy_fifth_percentile_and_revenue_must_be_below_zero() -> None:
    assert V.TOP_QUARTILE == 0.75
    assert V._flag(0.75, -0.01) is True and V._flag(0.7499, -0.5) is False
    assert V._flag(0.9, 0.0) is False, "flat revenue is not falling"
    assert V._flag(None, -0.5) is None and V._flag(0.9, None) is None


def test_a_listing_without_history_stays_in_the_panel_as_a_blank_row() -> None:
    panel = V.build({}, [listing("600001.SS", name_cn=None)], {"600001.SS": rev(90.0, 100.0)})
    row = panel.row(0, named=True)
    assert (
        row["name_cn"] is None and row["n_history_days"] == 0 and row["as_of"] is None and row["history_start"] is None
    )
    assert row["ps_ttm"] is None and row["ps_percentile"] is None and row["total_market_cap"] is None
    assert row["revenue_yoy"] == pytest.approx(-0.1), "the report is public even when the price history failed"
    assert row["flags"] == {FLAG: None}


def test_a_short_history_gives_the_multiple_and_the_day_count_but_no_percentile() -> None:
    panel = V.build({"600001.SS": hist([1.0] * (V.MIN_HISTORY_DAYS - 1))}, [listing("600001.SS")])
    row = panel.row(0, named=True)
    assert row["ps_ttm"] == 1.0 and row["n_history_days"] == V.MIN_HISTORY_DAYS - 1 and row["ps_percentile"] is None
    enough = V.build({"600001.SS": hist([1.0] * V.MIN_HISTORY_DAYS)}, [listing("600001.SS")])
    assert enough["ps_percentile"][0] == 1.0, "all ties, all at or below"


def test_a_listing_whose_rows_carry_no_multiple_at_all_is_still_a_valid_dated_blank() -> None:
    rows = [{"date": d, "ps_ttm": None, "pb": None, "pe_ttm": None, "total_market_cap": None} for d in days(5)]
    row = V.build({"600001.SS": rows}, [listing("600001.SS")]).row(0, named=True)
    assert row["as_of"] == days(5)[-1] and row["n_history_days"] == 0 and row["history_start"] is None
    assert row["ps_ttm"] is None and row["ps_percentile"] is None


def test_the_multiple_is_the_value_on_the_listings_last_day_and_a_null_there_is_not_replaced() -> None:
    series = [1.0, 2.0, 3.0, None]
    panel = V.build({"600001.SS": hist(series)}, [listing("600001.SS")], min_days=1)
    row = panel.row(0, named=True)
    assert row["as_of"] == days(4)[-1] and row["ps_ttm"] is None and row["ps_percentile"] is None
    assert row["n_history_days"] == 3 and row["history_start"] == days(4)[0]


def test_pb_has_its_own_percentile_and_its_own_missing_values() -> None:
    rows = [
        {"date": d, "ps_ttm": p, "pb": b, "pe_ttm": None, "total_market_cap": 1e9}
        for d, p, b in zip(days(3), [1.0, 2.0, 3.0], [3.0, 2.0, None], strict=True)
    ]
    row = V.build({"600001.SS": rows}, [listing("600001.SS")], min_days=1).row(0, named=True)
    assert row["ps_percentile"] == 1.0 and row["pb"] is None and row["pb_percentile"] is None


def test_as_of_replays_the_panel_with_the_history_and_the_reports_that_existed_then() -> None:
    values = [1.0, 5.0, 3.0, 2.0, 9.0]
    revenue = rev(80.0, 100.0, end="2026-06-30", notice="2026-08-26") + rev(
        50.0, 100.0, end="2026-03-31", notice="2026-04-21"
    )
    now = V.build({"600001.SS": hist(values)}, [listing("600001.SS")], {"600001.SS": revenue}, min_days=1).row(
        0, named=True
    )
    then = V.build(
        {"600001.SS": hist(values)}, [listing("600001.SS")], {"600001.SS": revenue}, as_of=days(5)[2], min_days=1
    ).row(0, named=True)
    assert now["as_of"] == days(5)[4] and now["ps_percentile"] == 1.0 and now["revenue_period"] == "2026H1"
    assert then["as_of"] == days(5)[2] and then["ps_ttm"] == 3.0 and then["ps_percentile"] == pytest.approx(2 / 3)
    assert then["n_history_days"] == 3
    long_ago = V.build(
        {"600001.SS": hist(values)}, [listing("600001.SS")], {"600001.SS": revenue}, as_of="2026-04-30", min_days=1
    ).row(0, named=True)
    assert long_ago["revenue_period"] == "2026Q1" and long_ago["revenue_yoy"] == pytest.approx(-0.5), (
        "H1 was not public yet"
    )


def test_only_a_share_listings_of_rings_a_and_b_are_in_the_panel() -> None:
    items = [
        listing("600003.SS", "B"),
        listing("CRWD", "A"),
        listing("600002.SS", "F"),
        listing("0700.HK", "A"),
        listing("000001.SZ", "A"),
        listing("600001.SS", "C"),
        listing("600004.SS", "A"),
    ]
    panel = V.build({}, items)
    assert panel["ticker"].to_list() == ["000001.SZ", "600004.SS", "600003.SS"], "ring A before ring B, each by ticker"
    assert [x["ticker"] for x in V.a_share_listings(items)] == ["600003.SS", "000001.SZ", "600004.SS"]


def test_listings_may_be_pool_entries_or_listing_row_dicts() -> None:
    entry = Entry(
        ticker="600001.SS",
        name="Entry Co",
        country="China",
        ring="B",
        layer="endpoint",
        purity="high",
        status="listed",
        name_cn="某公司",
    )
    panel = V.build({}, [entry, listing("600002.SS")])
    assert panel.filter(pl.col("ticker") == "600001.SS").row(0, named=True)["name_cn"] == "某公司"
    assert panel["ring"].to_list() == ["A", "B"]


def test_the_real_pool_gives_31_rows_16_in_ring_b_and_15_in_ring_a() -> None:
    panel = V.build({}, POOL)
    assert panel.height == 31 and panel["ring"].value_counts().sort("ring")["count"].to_list() == [15, 16]
    assert panel["ticker"].n_unique() == 31 and panel["n_history_days"].sum() == 0


def test_an_empty_listing_set_is_an_empty_valid_table() -> None:
    panel = V.build({}, [])
    assert panel.height == 0 and panel.columns == list(V.PANEL_DTYPES)
    assert V.facts(panel)["n_listed"] == 0


def test_build_without_revenue_leaves_growth_and_flags_null() -> None:
    panel = V.build({"600001.SS": hist([1.0] * 300)}, [listing("600001.SS")])
    row = panel.row(0, named=True)
    assert row["revenue_yoy"] is None and row["revenue_period"] is None and row["flags"] == {FLAG: None}


def test_the_panel_has_exactly_the_documented_columns_in_order() -> None:
    assert list(V.build({}, [listing("600001.SS")]).columns) == [
        "ticker", "name", "name_cn", "ring", "ps_ttm", "ps_percentile", "pb", "pb_percentile", "total_market_cap",
        "n_history_days", "history_start", "as_of", "revenue_yoy", "revenue_period", "flags",
    ]  # fmt: skip


def test_build_is_pure_it_does_not_change_its_inputs() -> None:
    history = {"600001.SS": hist([3.0, 1.0, 2.0] * 100)}
    revenue = {"600001.SS": rev(80.0, 100.0)}
    before = json.dumps([history, revenue], sort_keys=True)
    a = V.build(history, [listing("600001.SS")], revenue)
    b = V.build(history, [listing("600001.SS")], revenue)
    assert json.dumps([history, revenue], sort_keys=True) == before and a.equals(b)


# --- facts and checks ----------------------------------------------------------------------------------------
def _healthy_panel() -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    up, down = [1.0 + i / 100 for i in range(300)], [3.0 - i / 100 for i in range(300)]
    items = [listing("600001.SS"), listing("600002.SS"), listing("600003.SS", "B")]
    history = {"600001.SS": hist(up), "600002.SS": hist(down), "600003.SS": hist(up)}
    revenue = {"600001.SS": rev(80.0, 100.0), "600002.SS": rev(110.0, 100.0), "600003.SS": rev(95.0, 100.0)}
    return V.build(history, items, revenue), items


def test_facts_count_the_panel_and_carry_the_qualifier_unit_and_as_of_of_every_value() -> None:
    panel, _ = _healthy_panel()
    f = V.facts(panel)
    assert (f["n_listed"], f["n_with_ps"], f["n_with_percentile"], f["n_with_revenue_yoy"]) == (3, 3, 3, 3)
    assert (f["n_flagged"], f["n_flag_assessable"], f["flagged"]) == (2, 3, ["600001.SS", "600003.SS"])
    assert f["n_top_quartile"] == 2 and f["n_revenue_falling"] == 2 and f["as_of"] == days(300)[-1]
    median = f["median_ps_ttm"]
    assert (
        median["value"] == pytest.approx(sorted(panel["ps_ttm"].to_list())[1], abs=1e-6) and median["qualifier"] == "="
    )
    for key in ("median_ps_ttm", "median_ps_percentile", "median_revenue_yoy"):
        v = f[key]
        assert set(v) == {"value", "qualifier", "unit", "period", "as_of", "source_url"}
        assert v["unit"] and v["period"] and v["as_of"] == f["as_of"] and v["source_url"].startswith("https://")
    assert f["median_revenue_yoy"]["value"] == pytest.approx(-0.05)


def test_facts_of_a_panel_with_nothing_in_it_are_zeros_and_nulls_not_errors() -> None:
    f = V.facts(V.build({}, [listing("600001.SS")]))
    assert f["n_listed"] == 1 and f["n_with_ps"] == 0 and f["n_flagged"] == 0 and f["flagged"] == []
    assert f["median_ps_ttm"]["value"] is None and f["median_ps_ttm"]["qualifier"] is None and f["as_of"] is None


def test_the_checks_pass_on_a_healthy_panel() -> None:
    panel, items = _healthy_panel()
    out = V.checks(panel, items, days(300)[-1])
    assert {c["status"] for c in out} == {"pass"}, out
    assert [c["name"] for c in out] == [
        "China panel rows",
        "China panel history",
        "China panel percentile",
        "China panel freshness",
        "China panel revenue growth",
    ]


def test_the_checks_fail_on_a_missing_row_a_stale_panel_and_a_blank_history() -> None:
    panel, items = _healthy_panel()

    def by_name(out: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for c in out}

    missing = by_name(V.checks(panel.filter(pl.col("ticker") != "600003.SS"), items, days(300)[-1]))
    assert missing["China panel rows"]["status"] == "fail" and "600003.SS" in missing["China panel rows"]["detail"]
    stale_day = (date.fromisoformat(days(300)[-1]) + timedelta(V.MAX_AGE_DAYS + 1)).isoformat()
    assert by_name(V.checks(panel, items, stale_day))["China panel freshness"]["status"] == "warn"
    way_stale = (date.fromisoformat(days(300)[-1]) + timedelta(10 * V.MAX_AGE_DAYS)).isoformat()
    assert by_name(V.checks(panel, items, way_stale))["China panel freshness"]["status"] == "fail"
    blank = V.build({"600001.SS": hist(list(range(1, 301)))}, items, {})
    out = by_name(V.checks(blank, items, days(300)[-1]))
    assert out["China panel history"]["status"] == "fail" and "600002.SS" in out["China panel history"]["detail"]
    assert (
        out["China panel revenue growth"]["status"] == "fail" or out["China panel revenue growth"]["status"] == "warn"
    )
    assert V.checks(V.build({}, []), [], "2026-10-17")[3]["status"] == "fail", "no trading day at all is a failure"


def test_a_listing_whose_last_day_lags_the_others_is_named() -> None:
    items = [listing("600001.SS"), listing("600002.SS")]
    fresh, old = hist(range(1, 301)), hist(range(1, 301))[:-20]
    panel = V.build({"600001.SS": fresh, "600002.SS": old}, items, {})
    freshness = {c["name"]: c for c in V.checks(panel, items, days(300)[-1])}["China panel freshness"]
    assert freshness["status"] == "warn" and "600002.SS" in freshness["detail"], "a suspended stock is named, not fatal"


def test_a_short_history_is_a_warning_not_a_failure() -> None:
    items = [listing("600001.SS")]
    panel = V.build({"600001.SS": hist([1.0] * 10)}, items, {})
    got = {c["name"]: c for c in V.checks(panel, items, days(10)[-1])}["China panel percentile"]
    assert got["status"] == "warn" and "600001.SS" in got["detail"]


# --- stored tables -------------------------------------------------------------------------------------------
def test_the_stored_tables_round_trip_and_validate() -> None:
    history = {"002439.SZ": V.parse(PAGE1) + V.parse(PAGE2), "600001.SS": hist([1.0, 2.0])}
    frame = V.history_frame(history)
    V.HISTORY_SCHEMA.validate(frame)
    assert frame.columns == ["ticker", "date", "ps_ttm", "pb", "pe_ttm", "total_market_cap"]
    assert frame["ticker"].to_list() == sorted(frame["ticker"].to_list()), (
        "sorted for reproducible bytes and good compression"
    )
    assert V.history_frame({}).height == 0 and V.revenue_frame({}).height == 0
    rev_frame = V.revenue_frame({"002439.SZ": V.parse_revenue(REV_VENUSTECH)})
    V.REVENUE_SCHEMA.validate(rev_frame)
    assert rev_frame.columns == ["ticker", "period_end", "revenue", "yoy_reported", "notice_date"]


def test_a_rerun_on_the_same_day_replaces_its_tickers_and_keeps_the_others(tmp_path: Path) -> None:
    path = tmp_path / "d.parquet"
    V.write_parquet(V.history_frame({"600001.SS": hist([1.0, 2.0]), "600002.SS": hist([3.0])}), path)
    again = V.history_frame({"600001.SS": hist([7.0])})
    merged = V._merge_existing(again, path)
    assert merged.filter(pl.col("ticker") == "600001.SS")["ps_ttm"].to_list() == [7.0]
    assert merged.filter(pl.col("ticker") == "600002.SS")["ps_ttm"].to_list() == [3.0]
    assert V._merge_existing(again, tmp_path / "missing.parquet").equals(again)
    V.HISTORY_SCHEMA.validate(merged)


def test_load_latest_is_none_when_nothing_was_stored(tmp_path: Path) -> None:
    assert V.load_latest(tmp_path) is None
    assert V.load_latest(tmp_path / "nowhere") is None


def test_load_latest_takes_each_ticker_from_the_newest_snapshot_that_has_it(tmp_path: Path) -> None:
    V.write_parquet(
        V.history_frame({"600001.SS": hist([1.0]), "600002.SS": hist([2.0, 3.0])}), tmp_path / "2026-10-01.parquet"
    )
    V.write_parquet(V.history_frame({"600001.SS": hist([5.0, 6.0])}), tmp_path / "2026-10-08.parquet")
    V.write_parquet(V.revenue_frame({"600001.SS": rev(80.0, 100.0)}), tmp_path / "revenue" / "2026-10-08.parquet")
    loaded = V.load_latest(tmp_path)
    assert loaded is not None
    assert [r["ps_ttm"] for r in loaded.history["600001.SS"]] == [5.0, 6.0]
    assert [r["ps_ttm"] for r in loaded.history["600002.SS"]] == [2.0, 3.0], (
        "a listing the newest run lost stays on its last data"
    )
    assert loaded.source_dates == {"600001.SS": "2026-10-08", "600002.SS": "2026-10-01"}
    assert set(loaded.history["600001.SS"][0]) == {"date", "ps_ttm", "pb", "pe_ttm", "total_market_cap"}
    assert loaded.revenue["600001.SS"][-1]["revenue"] == 80.0
    panel = V.build(loaded.history, [listing("600001.SS"), listing("600002.SS")], loaded.revenue, min_days=1)
    assert panel["as_of"].to_list() == [days(2)[-1], days(2)[-1]], "each row says which day it rests on"


def test_load_latest_ignores_files_that_are_not_dated_snapshots(tmp_path: Path) -> None:
    V.write_parquet(V.history_frame({"600001.SS": hist([1.0])}), tmp_path / "2026-10-01.parquet")
    V.write_parquet(V.history_frame({"600001.SS": hist([9.0])}), tmp_path / "scratch.parquet")
    assert [r["ps_ttm"] for r in V.load_latest(tmp_path).history["600001.SS"]] == [1.0]


# --- collect and main ----------------------------------------------------------------------------------------
def test_one_listing_failing_never_costs_the_others() -> None:
    def history(t: str) -> list[dict]:
        if t == "600002.SS":
            raise V.SourceError("no history")
        return hist([1.0, 2.0])

    def revenue(t: str) -> list[dict]:
        if t == "600003.SS":
            raise RuntimeError("503")
        return rev(80.0, 100.0)

    got_h, got_r, failed = V.collect(["600001.SS", "600002.SS", "600003.SS"], history=history, revenue=revenue)
    assert set(got_h) == {"600001.SS", "600003.SS"} and set(got_r) == {"600001.SS", "600002.SS"}
    assert failed["600002.SS"].startswith("history: SourceError") and failed["600003.SS"].startswith(
        "revenue: RuntimeError"
    )
    assert "600001.SS" not in failed


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every path ``main`` writes at a temp directory and give it a four-listing pool with one US name."""
    monkeypatch.setattr(V, "SNAP", tmp_path)
    monkeypatch.setattr(V, "RUNS", tmp_path / "valuation_history_em_runs.jsonl")
    pool = [
        Entry(ticker=t, name=t, country="China" if t[0].isdigit() or t[0] == "6" else "United States", ring="A",
              layer="endpoint", purity="high", status="listed")
        for t in ("600001.SS", "600002.SS", "000003.SZ", "CRWD")
    ]  # fmt: skip
    monkeypatch.setattr(V, "POOL", pool)
    monkeypatch.setattr(V, "client", lambda interval: SimpleNamespace(calls=7, min_interval=interval))
    return tmp_path


def _fetch_all(
    monkeypatch: pytest.MonkeyPatch, *, history_fails: set[str] = frozenset(), revenue_fails: set[str] = frozenset()
) -> list[str]:
    asked: list[str] = []

    def fake_fetch(ticker: str, http: Any) -> list[dict]:
        asked.append(ticker)
        if ticker in history_fails:
            raise V.SourceError(f"{ticker}: nothing")
        return hist([1.0, 2.0, 3.0])

    def fake_revenue(ticker: str, http: Any) -> list[dict]:
        if ticker in revenue_fails:
            raise V.SourceError("no report")
        return V.parse_revenue(REV_VENUSTECH)

    monkeypatch.setattr(V, "fetch", fake_fetch)
    monkeypatch.setattr(V, "fetch_revenue", fake_revenue)
    return asked


def test_main_writes_both_tables_and_a_run_record_for_the_a_shares_only(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _fetch_all(monkeypatch)
    assert V.main(["--min-interval", "0"]) == 0
    assert asked == ["600001.SS", "600002.SS", "000003.SZ"], "the US listing is never asked of East Money"
    files = sorted(p.relative_to(sandbox).as_posix() for p in sandbox.rglob("*") if p.is_file())
    assert (
        len(files) == 3
        and files[0].startswith("valuation_history_em/")
        and files[1].startswith("valuation_history_em/revenue/")
    )
    assert files[2] == "valuation_history_em_runs.jsonl", files
    frame = pl.read_parquet(next((sandbox / "valuation_history_em").glob("*.parquet")))
    assert frame["ticker"].n_unique() == 3 and frame.height == 9
    assert (
        pl.read_parquet(next((sandbox / "valuation_history_em" / "revenue").glob("*.parquet")))["ticker"].n_unique()
        == 3
    )
    record = json.loads((sandbox / "valuation_history_em_runs.jsonl").read_text().splitlines()[-1])
    assert record["listings"] == 3 and record["with_history"] == 3 and record["with_revenue"] == 3
    assert record["failed"] == {} and record["error"] is None and record["history_rows"] == 9 and record["calls"] == 7
    assert record["newest_day"] == days(3)[-1] and not math.isnan(record["seconds"])


def test_main_keeps_what_came_back_when_one_listing_fails_and_says_which(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fetch_all(monkeypatch, history_fails={"600002.SS"}, revenue_fails={"000003.SZ"})
    assert V.main(["--min-interval", "0"]) == 1, "two of three is under the coverage the job requires"
    frame = pl.read_parquet(next((sandbox / "valuation_history_em").glob("*.parquet")))
    assert sorted(frame["ticker"].unique().to_list()) == ["000003.SZ", "600001.SS"], "the other listings were written"
    record = json.loads((sandbox / "valuation_history_em_runs.jsonl").read_text().splitlines()[-1])
    assert set(record["failed"]) == {"600002.SS", "000003.SZ"}
    assert "history" in record["failed"]["600002.SS"] and "revenue" in record["failed"]["000003.SZ"]
    assert record["with_history"] == 2 and record["with_revenue"] == 2


def test_main_exits_zero_when_coverage_stays_above_the_limit(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(V, "MIN_COVERED_SHARE", 0.6)
    _fetch_all(monkeypatch, history_fails={"600002.SS"})
    assert V.main(["--min-interval", "0"]) == 0
    assert json.loads((sandbox / "valuation_history_em_runs.jsonl").read_text().splitlines()[-1])["failed"]


def test_main_with_every_listing_failing_writes_no_table_but_still_records_the_run(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fetch_all(monkeypatch, history_fails={"600001.SS", "600002.SS", "000003.SZ"})
    assert V.main(["--min-interval", "0"]) == 1
    assert not list(sandbox.rglob("*.parquet")), "a run that fetched nothing writes nothing"
    record = json.loads((sandbox / "valuation_history_em_runs.jsonl").read_text().splitlines()[-1])
    assert record["with_history"] == 0 and len(record["failed"]) == 3 and record["path"] is None


def test_main_does_not_blank_the_day_when_a_retry_loses_a_listing(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fetch_all(monkeypatch)
    assert V.main(["--min-interval", "0"]) == 0
    _fetch_all(monkeypatch, history_fails={"600002.SS"})
    monkeypatch.setattr(V, "MIN_COVERED_SHARE", 0.5)
    assert V.main(["--min-interval", "0"]) == 0
    frame = pl.read_parquet(next((sandbox / "valuation_history_em").glob("*.parquet")))
    assert sorted(frame["ticker"].unique().to_list()) == ["000003.SZ", "600001.SS", "600002.SS"]
    assert len(sandbox.joinpath("valuation_history_em_runs.jsonl").read_text().splitlines()) == 2


def test_main_only_restricts_the_listings_and_an_unknown_ticker_selects_nothing(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _fetch_all(monkeypatch)
    assert V.main(["--min-interval", "0", "--only", "600002.SS"]) == 0 and asked == ["600002.SS"]
    assert V.main(["--min-interval", "0", "--only", "NOPE"]) == 1
    assert V.main(["--min-interval", "0", "--only", "CRWD"]) == 1, "a US listing is not in the East Money panel"


def test_a_schema_failure_blocks_the_write_and_is_recorded(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_fetch(ticker: str, http: Any) -> list[dict]:
        return [
            {"date": "2026-01-02", "ps_ttm": 1.0, "pb": 1.0, "pe_ttm": None, "total_market_cap": 1.0}
        ] * 2  # a duplicate day

    monkeypatch.setattr(V, "fetch", bad_fetch)
    monkeypatch.setattr(V, "fetch_revenue", lambda t, http: V.parse_revenue(REV_VENUSTECH))
    assert V.main(["--min-interval", "0"]) == 1
    assert not list(sandbox.rglob("*.parquet"))
    record = json.loads((sandbox / "valuation_history_em_runs.jsonl").read_text().splitlines()[-1])
    assert record["error"] and record["path"] is None


def test_the_pool_has_the_31_a_share_listings_main_would_fetch() -> None:
    tickers = [e.ticker for e in V.a_share_listings(POOL)]
    assert len(tickers) == 31 and all(t.endswith((".SS", ".SZ")) for t in tickers)
    assert {e.ring for e in POOL if e.ticker in tickers} == {"A", "B"}


def test_a_weekly_file_stores_only_the_new_days_and_load_latest_rebuilds_the_whole_history(tmp_path) -> None:
    from pipelines.cyber import valuation_history_em as V

    def rows(days):
        return [
            {"date": d, "ps_ttm": 4.0 + i / 10, "pb": 2.0, "pe_ttm": None, "total_market_cap": 1e10}
            for i, d in enumerate(days)
        ]

    first_days = [f"2026-0{m}-{d:02d}" for m in (1, 2, 3) for d in (5, 12, 19, 26)]
    stored = {"688561.SS": rows(first_days)}
    later = first_days + ["2026-04-06", "2026-04-13"]
    got = V.new_days({"688561.SS": rows(later), "002439.SZ": rows(later)}, stored)
    assert len(got["002439.SZ"]) == len(later), "a ticker not stored yet gets its whole history"
    assert [r["date"] for r in got["688561.SS"]][0] >= "2026-02-24", "an overlap of OVERLAP_DAYS and no more"
    assert {"2026-04-06", "2026-04-13"} <= {r["date"] for r in got["688561.SS"]}
    assert len(got["688561.SS"]) < len(later)

    (tmp_path / "2026-03-30.parquet").parent.mkdir(exist_ok=True)
    V.write_parquet(V.history_frame(stored), tmp_path / "2026-03-30.parquet")
    V.write_parquet(V.history_frame({"688561.SS": got["688561.SS"]}), tmp_path / "2026-04-13.parquet")
    loaded = V.load_latest(tmp_path)
    assert [r["date"] for r in loaded.history["688561.SS"]] == sorted(set(later)), (
        "union of both files, no gaps, no repeats"
    )
    assert loaded.source_dates["688561.SS"] == "2026-04-13"
