"""Parse tests for the East Money consensus module. No network: fixtures are trimmed real payloads."""

from __future__ import annotations

import gzip
import json

import polars as pl
import pytest

from pipelines.valuation import estimates_em
from pipelines.valuation.estimates_em import (
    EXPECTED_EMPTY,
    archive_payload,
    collect,
    consensus_from_brokers,
    consensus_from_mean,
    latest_reports,
    mean_deviation,
    mean_row,
    parse_brokers,
    rebuild_vintages,
    snapshot,
)
from pipelines.valuation.schema import (
    BROKERS_SCHEMA,
    ESTIMATES_SCHEMA,
    VINTAGES_SCHEMA,
    brokers_frame,
    estimates_frame,
    vintages_frame,
)

TICKER = "300308.SZ"
TS = "2026-09-16T18:30:00+00:00"


def ycmx_row(org: str, publish: str, eps: tuple, **over) -> dict:
    """A `ycmx` row in East Money's real shape: four year slots hung off one report."""
    row = {
        "SECUCODE": "300308.SZ",
        "SECURITY_NAME_ABBR": "中际旭创",
        "PUBLISH_DATE": f"{publish} 00:00:00",
        "ORG_CODE": "10004822",
        "ORG_NAME_ABBR": org,
        "RESEARCHER": "许梓豪,代小笛",
        "YEAR1": 2025,
        "YEAR_MARK1": "A",
        "EPS1": 9.166453796306,
        "PARENT_NETPROFIT1": 10797254300.45,
        "YEAR2": 2026,
        "YEAR_MARK2": "E",
        "EPS2": eps[0],
        "PARENT_NETPROFIT2": 34723000000,
        "YEAR3": 2027,
        "YEAR_MARK3": "E",
        "EPS3": eps[1],
        "PARENT_NETPROFIT3": 63766000000,
        "YEAR4": 2028,
        "YEAR_MARK4": "E",
        "EPS4": eps[2],
        "PARENT_NETPROFIT4": 95933000000,
        "RATING": "增持",
    }
    row.update(over)
    return row


# the verified 300308 aggregate: 2026E 28.68 (PE 32.88), 2027E 51.81 (PE 18.08)
MEAN_JGYC = {
    "SECUCODE": "300308.SZ",
    "SECURITY_NAME_ABBR": "中际旭创",
    "PUBLISH_DATE": "2026-09-17 00:00:00",
    "ORG_CODE": "00000000",
    "ORG_NAME_ABBR": "近六月平均",
    "YEAR1": 2025,
    "YEAR_MARK1": "A",
    "EPS1": 9.166453796306,
    "PE1": 99.03502709,
    "YEAR2": 2026,
    "YEAR_MARK2": "E",
    "EPS2": 28.681714285714,
    "PE2": 32.883802822741,
    "YEAR3": 2027,
    "YEAR_MARK3": "E",
    "EPS3": 51.813678571429,
    "PE3": 18.075571552975,
    "YEAR4": 2028,
    "YEAR_MARK4": "E",
    "EPS4": 73.64725,
    "PE4": 12.686526088019,
}


# --- parse_brokers -----------------------------------------------------------------
def test_unpivots_four_year_slots_into_rows():
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44))])

    assert [(r["year"], r["mark"]) for r in rows] == [(2025, "A"), (2026, "E"), (2027, "E"), (2028, "E")]
    assert [r["eps"] for r in rows] == [9.166453796306, 29.48, 54.14, 81.44]
    assert rows[1]["net_profit"] == 34723000000
    assert {r["ticker"] for r in rows} == {TICKER}
    assert {r["org"] for r in rows} == {"兴业证券"}
    assert {r["rating"] for r in rows} == {"增持"}
    assert {r["researcher"] for r in rows} == {"许梓豪,代小笛"}


def test_publish_date_keeps_only_the_date_part():
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44))])
    assert {r["publish_date"] for r in rows} == {"2026-09-09"}


def test_slot_with_no_year_is_dropped():
    """14 rows in the live pool forecast only three years and leave YEAR4 null."""
    rows = parse_brokers(
        TICKER, [ycmx_row("华泰证券", "2026-08-23", (28.0, 50.0, None), YEAR4=None, YEAR_MARK4=None, EPS4=None)]
    )
    assert [r["year"] for r in rows] == [2025, 2026, 2027]


def test_null_eps_keeps_the_broker_row_but_leaves_eps_null():
    rows = parse_brokers(TICKER, [ycmx_row("中信证券", "2026-09-01", (30.0, None, 80.0))])
    by_year = {r["year"]: r for r in rows}
    assert by_year[2027]["eps"] is None
    assert by_year[2027]["net_profit"] == 63766000000  # the slot still carries its other numbers


def test_aggregate_row_is_not_mistaken_for_a_broker():
    rows = parse_brokers(TICKER, [ycmx_row("近六月平均", "2026-09-17", (28.68, 51.81, 73.64))])
    assert rows == []


def test_row_without_a_usable_publish_date_is_skipped():
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44), PUBLISH_DATE=None)])
    assert rows == []


def test_unknown_mark_is_dropped_because_the_schema_forbids_it():
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44), YEAR_MARK2="?")])
    assert [r["year"] for r in rows] == [2025, 2027, 2028]


def test_empty_payload_parses_to_nothing():
    assert parse_brokers(TICKER, []) == []
    assert parse_brokers(TICKER, None) == []


# --- consensus ---------------------------------------------------------------------
def test_consensus_averages_brokers_and_reports_the_spread():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (30.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("B证券", "2026-09-06", (20.0, 40.0, 60.0))])

    out = {r["period_end"]: r for r in consensus_from_brokers(rows)}
    y26 = out["2026-12-31"]
    assert y26["eps_avg"] == 25.0
    assert (y26["eps_low"], y26["eps_high"]) == (20.0, 30.0)
    assert y26["n_analysts"] == 2
    assert y26["mark"] == "E"
    assert y26["fy_label"] == "FY2026"
    assert y26["currency"] == "CNY"
    assert y26["source"] == "eastmoney"
    assert y26["net_profit_avg"] == 34723000000
    assert out["2025-12-31"]["mark"] == "A"  # actuals come through marked as actuals


def test_only_each_brokers_latest_report_feeds_the_consensus():
    """The trap: a broker that published twice must count once, at its newest view."""
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-04-02", (10.0, 20.0, 30.0))])
    rows += parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (30.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("中信证券", "2026-09-06", (20.0, 40.0, 60.0))])

    assert len(latest_reports(rows)) == 8  # the stale report's four slots drop out

    y26 = next(r for r in consensus_from_brokers(rows) if r["period_end"] == "2026-12-31")
    assert y26["eps_avg"] == 25.0  # (30 + 20) / 2, not (10 + 30 + 20) / 3
    assert y26["n_analysts"] == 2
    assert y26["eps_low"] == 20.0  # the April 10.0 is not the low, it is superseded

    # ... but both reports survive in the brokers table, which keys on publish date
    both = [r for r in rows if r["org"] == "兴业证券" and r["year"] == 2026]
    assert sorted(r["publish_date"] for r in both) == ["2026-04-02", "2026-09-09"]
    assert brokers_frame(both).height == 2


def test_a_year_with_only_null_eps_yields_a_null_mean_and_zero_count():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (30.0, None, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("B证券", "2026-09-06", (20.0, None, 60.0))])

    out = {r["period_end"]: r for r in consensus_from_brokers(rows)}
    assert out["2027-12-31"]["eps_avg"] is None
    assert out["2027-12-31"]["n_analysts"] == 0
    assert out["2027-12-31"]["net_profit_avg"] == 63766000000
    assert out["2026-12-31"]["n_analysts"] == 2  # the other years are unaffected


def test_null_eps_is_excluded_from_the_mean_rather_than_counted_as_zero():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (30.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("B证券", "2026-09-06", (None, 40.0, 60.0))])

    y26 = next(r for r in consensus_from_brokers(rows) if r["period_end"] == "2026-12-31")
    assert y26["eps_avg"] == 30.0
    assert y26["n_analysts"] == 1


def test_consensus_covers_several_tickers_independently():
    rows = parse_brokers("300308.SZ", [ycmx_row("A证券", "2026-09-09", (30.0, 50.0, 80.0))])
    rows += parse_brokers("300750.SZ", [ycmx_row("A证券", "2026-09-08", (20.8, 25.94, 30.0))])

    out = {(r["ticker"], r["period_end"]): r["eps_avg"] for r in consensus_from_brokers(rows)}
    assert out[("300308.SZ", "2026-12-31")] == 30.0
    assert out[("300750.SZ", "2026-12-31")] == 20.8


# --- vintages ----------------------------------------------------------------------
def test_as_of_rebuild_produces_a_rising_series():
    """Three brokers raising 2026 through the window: the mean as of each date climbs."""
    rows: list[dict] = []
    for org, date, eps in (
        ("A证券", "2026-04-02", 20.0),
        ("B证券", "2026-06-23", 26.0),
        ("C证券", "2026-09-09", 35.0),
    ):
        rows += parse_brokers(TICKER, [ycmx_row(org, date, (eps, 50.0, 80.0))])

    series = [(r["as_of"], r["eps_avg"], r["n_analysts"]) for r in rebuild_vintages(rows) if r["period_end"] == "2026-12-31"]

    # nothing at 2026-04-02: one broker is not a consensus
    assert series == [("2026-06-23", 23.0, 2), ("2026-09-09", 27.0, 3)]
    assert [p[1] for p in series] == sorted(p[1] for p in series)
    assert {r["origin"] for r in rebuild_vintages(rows)} == {"eastmoney_rebuilt"}
    assert {r["source"] for r in rebuild_vintages(rows)} == {"eastmoney"}
    assert {r["currency"] for r in rebuild_vintages(rows)} == {"CNY"}


def test_as_of_rebuild_uses_each_brokers_latest_view_at_that_date():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-04-02", (20.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("B证券", "2026-04-09", (24.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (40.0, 50.0, 80.0))])  # A revises up

    series = {r["as_of"]: (r["eps_avg"], r["n_analysts"]) for r in rebuild_vintages(rows) if r["period_end"] == "2026-12-31"}
    assert series["2026-04-09"] == (22.0, 2)  # A's April view + B
    assert series["2026-09-09"] == (32.0, 2)  # A's September view + B, still two brokers


def test_vintages_skip_actuals_and_thin_dates():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-04-02", (20.0, 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("B证券", "2026-09-09", (26.0, 50.0, 80.0))])

    out = rebuild_vintages(rows)
    assert {r["period_end"] for r in out} == {"2026-12-31", "2027-12-31", "2028-12-31"}  # no 2025 actual
    assert {r["as_of"] for r in out} == {"2026-09-09"}  # the single-broker April date is dropped


def test_vintage_dates_are_capped_but_older_reports_still_feed_the_points_kept():
    rows: list[dict] = []
    for day in range(1, 61):  # 60 brokers, one report each, on 60 distinct dates
        rows += parse_brokers(TICKER, [ycmx_row(f"券商{day:02d}", f"2026-07-{day % 30 + 1:02d}", (float(day), 50.0, 80.0))])
    rows += parse_brokers(TICKER, [ycmx_row("老券商", "2026-01-05", (1.0, 50.0, 80.0))])

    out = [r for r in rebuild_vintages(rows) if r["period_end"] == "2026-12-31"]
    assert len({r["as_of"] for r in out}) <= 40
    assert min(r["as_of"] for r in out) > "2026-01-05"  # the oldest date is outside the cap
    assert max(r["n_analysts"] for r in out) == 61  # yet its broker still counts toward the points kept


def test_no_brokers_means_no_vintages():
    assert rebuild_vintages([]) == []


# --- the East Money aggregate ------------------------------------------------------
def test_mean_row_reads_the_aggregate():
    mean = mean_row([MEAN_JGYC, ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44))])
    assert mean is not None
    assert mean["publish_date"] == "2026-09-17"
    by_year = {y["year"]: y for y in mean["years"]}
    assert by_year[2026]["eps"] == pytest.approx(28.68, abs=0.01)
    assert by_year[2026]["pe"] == pytest.approx(32.88, abs=0.01)
    assert by_year[2027]["eps"] == pytest.approx(51.81, abs=0.01)
    assert by_year[2027]["pe"] == pytest.approx(18.08, abs=0.01)
    assert by_year[2025]["mark"] == "A"


def test_mean_row_is_none_when_the_aggregate_is_absent():
    assert mean_row([ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44))]) is None
    assert mean_row([]) is None
    assert mean_row(None) is None


def test_consensus_from_mean_marks_the_count_unknown():
    mean = mean_row([MEAN_JGYC])
    rows = {r["period_end"]: r for r in consensus_from_mean("300308.SZ", mean)}

    assert rows["2026-12-31"]["eps_avg"] == pytest.approx(28.68, abs=0.01)
    assert rows["2026-12-31"]["mark"] == "E"
    assert rows["2025-12-31"]["mark"] == "A"
    assert all(r["n_analysts"] is None for r in rows.values())
    assert all(r["net_profit_avg"] is None for r in rows.values())  # jgyc carries PE, not net profit
    assert all(r["source"] == "eastmoney" and r["currency"] == "CNY" for r in rows.values())


def test_mean_deviation_flags_a_parse_that_drifted_from_east_money():
    mean = mean_row([MEAN_JGYC])
    close = [{"period_end": "2026-12-31", "eps_avg": 28.7}]
    far = [{"period_end": "2026-12-31", "eps_avg": 14.0}]
    assert mean_deviation(close, mean) < 0.01
    assert mean_deviation(far, mean) > 0.4
    assert mean_deviation(close, None) is None


# --- degenerate coverage, end to end -----------------------------------------------
class FakeCompany:
    ticker = "600206.SS"
    em_code = "SH600206"


def test_mean_but_no_brokers_falls_back_to_the_aggregate():
    """有研新材: zero broker rows, but the 近六月平均 aggregate is there. Verified 2026E 0.53 / 2027E 0.72."""
    payload = {
        "ycmx": [],
        "jgyc": [
            {
                "ORG_NAME_ABBR": "近六月平均",
                "PUBLISH_DATE": "2026-09-17 00:00:00",
                "YEAR1": 2025,
                "YEAR_MARK1": "A",
                "EPS1": 0.312785470378,
                "PE1": 166.4,
                "YEAR2": 2026,
                "YEAR_MARK2": "E",
                "EPS2": 0.53,
                "PE2": 98.219210182382,
                "YEAR3": 2027,
                "YEAR_MARK3": "E",
                "EPS3": 0.72,
                "PE3": 71.417562959412,
                "YEAR4": 2028,
                "YEAR_MARK4": "E",
                "EPS4": 0.85,
                "PE4": 60.4,
            }
        ],
    }
    got = collect(FakeCompany(), payload)

    assert got["basis"] == "mean"
    assert got["brokers"] == []
    assert got["vintages"] == []  # no per-broker dates, so no revision history
    by_year = {r["period_end"]: r for r in got["estimates"]}
    assert by_year["2026-12-31"]["eps_avg"] == 0.53
    assert by_year["2027-12-31"]["eps_avg"] == 0.72
    assert by_year["2026-12-31"]["n_analysts"] is None  # unknown, not zero
    assert by_year["2026-12-31"]["eps_low"] is None
    assert by_year["2026-12-31"]["ticker"] == "600206.SS"


def test_neither_brokers_nor_mean_yields_nothing_to_write():
    """孚能科技 / 上海洗霸: the endpoint answers, with empty blocks."""
    got = collect(FakeCompany(), {"pjtj": [], "jgyc": [], "yctj_chart": [], "yctj_list": [], "ycmx": []})
    assert got["basis"] == "none"
    assert (got["estimates"], got["brokers"], got["vintages"]) == ([], [], [])


def test_broker_payload_produces_all_three_tables_and_they_validate():
    payload = {
        "ycmx": [
            ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44)),
            ycmx_row("长江证券", "2026-09-06", (27.9, 49.5, 70.0)),
            ycmx_row("交银国际证券", "2026-04-02", (22.0, 44.0, 60.0)),
        ],
        "jgyc": [MEAN_JGYC],
    }
    got = collect(FakeCompany(), payload)

    assert got["basis"] == "brokers"
    assert got["n_brokers"] == 3
    assert got["deviation"] < 0.10  # our mean sits near East Money's own

    ts = "2026-09-16T18:30:00+00:00"
    stamp = [{**r, "snapshot_ts": ts} for r in got["brokers"]]
    BROKERS_SCHEMA.validate(brokers_frame(stamp))
    ESTIMATES_SCHEMA.validate(estimates_frame([{**r, "snapshot_ts": ts} for r in got["estimates"]]))
    VINTAGES_SCHEMA.validate(vintages_frame([{**r, "snapshot_ts": ts} for r in got["vintages"]]))

    assert brokers_frame(stamp).height == 12  # 3 brokers x 4 year slots


def test_mean_fallback_rows_validate():
    payload = {"ycmx": [], "jgyc": [MEAN_JGYC]}
    got = collect(FakeCompany(), payload)
    rows = [{**r, "snapshot_ts": "2026-09-16T18:30:00+00:00"} for r in got["estimates"]]
    df = estimates_frame(rows)
    ESTIMATES_SCHEMA.validate(df)
    assert df["n_analysts"].null_count() == df.height


# --- dates and years the schema would reject ---------------------------------------
@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-9 00:00:00",  # single-digit day: ten characters, but not a date
        "9999-99-99 00:00:00",  # passes the schema's regex, then crashes date.fromisoformat
        "2026-02-31 00:00:00",  # plausible-looking, calendar-impossible
        "2026/09/09 00:00:00",
        "not a date",
    ],
)
def test_a_publish_date_that_is_not_a_date_is_dropped(stamp):
    """Shape-checking let "2026-09-9 " and "9999-99-99" through; both broke something downstream."""
    assert parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44), PUBLISH_DATE=stamp)]) == []


def test_a_forecast_year_outside_the_schema_range_is_dropped_rather_than_failing_the_write():
    """One junk year used to abort the write for all three tables and every other listing."""
    rows = parse_brokers(TICKER, [ycmx_row("兴业证券", "2026-09-09", (29.48, 54.14, 81.44), YEAR4=2050)])

    assert [r["year"] for r in rows] == [2025, 2026, 2027]
    BROKERS_SCHEMA.validate(brokers_frame([{**r, "snapshot_ts": TS} for r in rows]))


def test_the_aggregate_drops_an_out_of_range_year_too():
    mean = mean_row([{**MEAN_JGYC, "YEAR4": 91, "EPS4": 1.0}])
    assert [y["year"] for y in mean["years"]] == [2025, 2026, 2027]
    rows = consensus_from_mean(TICKER, mean)
    assert all(len(r["period_end"]) == 10 for r in rows)  # "91-12-31" would fail the estimates regex
    ESTIMATES_SCHEMA.validate(estimates_frame([{**r, "snapshot_ts": TS} for r in rows]))


# --- what the rebuilt vintage series actually measures ------------------------------
def test_the_rebuilt_series_is_coverage_and_n_analysts_says_so_on_every_row():
    """Nobody revises here. The mean still moves, and n_analysts is the reason, on every row."""
    rows: list[dict] = []
    for org, day, eps in (("A证券", "04-02", 20.0), ("B证券", "06-23", 26.0), ("C证券", "09-09", 35.0)):
        rows += parse_brokers(TICKER, [ycmx_row(org, f"2026-{day}", (eps, 50.0, 80.0))])

    cohort_at = {"2026-04-02": 1, "2026-06-23": 2, "2026-09-09": 3}
    out = rebuild_vintages(rows)
    assert out, "the coverage series is still emitted; the page wants the published consensus"
    for r in out:
        assert r["n_analysts"] == cohort_at[r["as_of"]]  # not today's count: the cohort behind THIS point
        assert r["n_analysts"] >= 2

    y26 = [(r["as_of"], r["eps_avg"], r["n_analysts"]) for r in out if r["period_end"] == "2026-12-31"]
    assert y26 == [("2026-06-23", 23.0, 2), ("2026-09-09", 27.0, 3)]


def test_there_is_one_reading_of_the_vintage_series_and_no_cohort_switch():
    """The frozen-panel "revision series" is gone: on one report per broker it could only be flat.

    It shared ``origin == "eastmoney_rebuilt"`` with the coverage reading, so a ``--cohort matched``
    run on a date that already had a default run merged two meanings into one table under one label.
    """
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-04-02", (20.0, 50.0, 80.0))])
    with pytest.raises(TypeError):
        rebuild_vintages(rows, cohort="matched")
    assert not hasattr(estimates_em, "COHORT_MATCHED")
    with pytest.raises(SystemExit):  # argparse refuses the flag rather than silently ignoring it
        estimates_em.main(["--cohort", "matched"])


def test_the_frozen_panel_reading_would_have_been_flat_by_construction():
    """Why it was removed, stated as a test rather than only in the docstring.

    Each broker publishes once, so freezing the panel at the first date with a consensus fixes both
    the members and their single forecasts: every later as_of reports the identical mean. The
    coverage reading of the same input moves, and ``n_analysts`` says why.
    """
    rows: list[dict] = []
    for org, day, eps in (("A证券", "04-02", 20.0), ("B证券", "06-23", 26.0), ("C证券", "09-09", 35.0)):
        rows += parse_brokers(TICKER, [ycmx_row(org, f"2026-{day}", (eps, 50.0, 80.0))])

    out = [r for r in rebuild_vintages(rows) if r["period_end"] == "2026-12-31"]
    assert [(r["as_of"], r["eps_avg"], r["n_analysts"]) for r in out] == [
        ("2026-06-23", 23.0, 2),
        ("2026-09-09", 27.0, 3),
    ]
    frozen = {"A证券", "B证券"}  # the panel the removed mode would have kept
    means = [
        sum(v for k, v in point.items() if k in frozen) / 2
        for point in ({"A证券": 20.0, "B证券": 26.0}, {"A证券": 20.0, "B证券": 26.0, "C证券": 35.0})
    ]
    assert means == [23.0, 23.0]  # a horizontal line, which is not a revision series


# --- the net profit mean is keyed by broker too -------------------------------------
def test_a_broker_reporting_twice_on_one_date_counts_once_in_the_net_profit_mean():
    rows = parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (30.0, 50.0, 80.0), PARENT_NETPROFIT2=1e9)])
    rows += parse_brokers(TICKER, [ycmx_row("A证券", "2026-09-09", (30.0, 50.0, 80.0), PARENT_NETPROFIT2=3e9)])

    y26 = next(r for r in consensus_from_brokers(rows) if r["period_end"] == "2026-12-31")
    assert y26["n_analysts"] == 1  # one broker, as the EPS mean has always counted it
    assert y26["net_profit_avg"] == 3e9  # its latest row, not the 2e9 mean of both


# --- the cross-check measures the year every broker covers --------------------------
def test_mean_deviation_reads_the_nearest_forecast_year_not_the_thinnest():
    """2028 is covered by a handful of brokers; a wide gap there is coverage, not a parse bug."""
    ours = [
        {"period_end": "2026-12-31", "mark": "E", "eps_avg": 28.69},
        {"period_end": "2028-12-31", "mark": "E", "eps_avg": 110.0},  # 50% off East Money's 73.6
    ]
    assert mean_deviation(ours, mean_row([MEAN_JGYC])) < 0.01


def test_mean_deviation_ignores_reported_actuals():
    ours = [{"period_end": "2025-12-31", "mark": "A", "eps_avg": 9.166453796306}]
    assert mean_deviation(ours, mean_row([MEAN_JGYC])) is None


# --- snapshot(): isolation, merging, and never shrinking a day ----------------------
# `snapshot` is the module's central promise and nothing exercised it, so a refactor that moved the
# writes or the try/except could break isolation with the suite still green. These run offline:
# `fetch` is replaced with a dict lookup and the two storage roots are redirected into tmp_path.
class Listing:
    """Enough of config.Company for snapshot(): it only reads .ticker and .em_code."""

    def __init__(self, ticker: str, em_code: str) -> None:
        self.ticker, self.em_code = ticker, em_code


A = Listing("300308.SZ", "SZ300308")
B = Listing("300502.SZ", "SZ300502")
FARASIS = Listing("688567.SS", "SH688567")  # in EXPECTED_EMPTY
EMPTY_PAYLOAD = {"pjtj": [], "jgyc": [], "yctj_chart": [], "yctj_list": [], "ycmx": []}


def payload_for(eps: float, orgs=("兴业证券", "长江证券"), dates=("2026-09-09", "2026-09-06")) -> dict:
    return {"ycmx": [ycmx_row(o, d, (eps, 50.0, 80.0)) for o, d in zip(orgs, dates, strict=True)], "jgyc": []}


@pytest.fixture
def em(tmp_path, monkeypatch):
    """snapshot() wired to a fake endpoint and a temp data root. Returns a runner."""
    monkeypatch.setattr(estimates_em, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(estimates_em, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(estimates_em, "THROTTLE", 0.0)
    monkeypatch.setattr(estimates_em.HttpClient, "__init__", lambda self, *a, **k: setattr(self, "calls", 0))
    monkeypatch.setattr(estimates_em.HttpClient, "close", lambda self: None)

    def run(listings, payloads, **kw):
        def fake_fetch(http, em_code):
            got = payloads[em_code]
            if isinstance(got, Exception):
                raise got
            return got

        monkeypatch.setattr(estimates_em, "fetch", fake_fetch)
        return snapshot(listings, **kw)

    run.snap = tmp_path / "snap" / "valuation"
    return run


def table(em, name: str) -> pl.DataFrame:
    files = sorted((em.snap / name).glob("*.parquet"))
    return pl.read_parquet(files[0]) if files else pl.DataFrame()


def test_one_listing_blowing_up_does_not_lose_the_others(em):
    res = em([A, B], {"SZ300308": RuntimeError("502 from East Money"), "SZ300502": payload_for(18.0)})

    assert res["errors"] and "300308.SZ" in res["errors"][0]
    assert table(em, "estimates")["ticker"].to_list() == ["300502.SZ"] * 4
    assert table(em, "brokers").height == 8
    assert table(em, "vintages").height  # B's data is on disk, not lost with A's failure
    assert (em.snap / "runs.jsonl").read_text(encoding="utf-8").strip().count("\n") == 0  # exactly one run line


def test_a_validation_failure_never_leaves_a_half_written_snapshot(em, monkeypatch):
    """brokers used to be on disk before estimates was even validated: an orphan with no partner.

    The build is also inside a guard now, so the failure is recorded rather than raised: a run that
    lost its data has to leave the line in runs.jsonl that explains why.
    """

    def boom_on_estimates(df, path, key):
        if path.parent.name == "estimates":
            raise ValueError("out of contract")
        return df

    monkeypatch.setattr(estimates_em, "_merge_existing", boom_on_estimates)
    res = em([A], {"SZ300308": payload_for(29.0)})

    assert table(em, "brokers").is_empty()  # no orphan: brokers is never written without estimates
    assert table(em, "estimates").is_empty()
    assert table(em, "vintages").is_empty()
    assert res["rows"] == {}
    assert "out of contract" in res["write_error"]
    assert any("write:" in e for e in res["errors"])  # so main() exits non-zero
    written = next(c for c in res["checks"] if c["name"] == "Snapshot written")
    assert written["status"] == "fail" and "nothing written" in written["detail"]
    assert (em.snap / "runs.jsonl").read_text(encoding="utf-8").strip().count("\n") == 0  # recorded anyway


def test_one_listings_rows_failing_pandera_does_not_lose_the_other_listings(em, monkeypatch):
    """The isolation rule has to cover the frame build, not stop at the fetch.

    Framing and validating only the combined table put both calls outside the per-company guard, so
    a value pandera rejects on one A-share listing took down all 29 *and* the run record with them.
    """
    real = estimates_em.ESTIMATES_SCHEMA

    class RejectsA:
        def validate(self, df):
            if "300308.SZ" in df["ticker"].to_list():
                raise ValueError("eps_avg is out of contract for 300308.SZ")
            return real.validate(df)

    monkeypatch.setattr(estimates_em, "ESTIMATES_SCHEMA", RejectsA())
    res = em([A, B], {"SZ300308": payload_for(29.0), "SZ300502": payload_for(18.0)})

    assert sorted(set(table(em, "estimates")["ticker"].to_list())) == ["300502.SZ"]
    assert sorted(set(table(em, "brokers")["ticker"].to_list())) == ["300502.SZ"]
    assert table(em, "vintages").height  # B is whole, not partly written
    assert len(res["errors"]) == 1 and "300308.SZ" in res["errors"][0]
    assert res["write_error"] is None
    assert (em.snap / "runs.jsonl").read_text(encoding="utf-8").strip().count("\n") == 0


def test_a_partial_run_merges_with_the_days_file_instead_of_replacing_it(em):
    """`--only 300502.SZ` after a full run must not delete the rest of the day."""
    em([A], {"SZ300308": payload_for(29.0)})
    res = em([B], {"SZ300502": payload_for(18.0)})

    assert sorted(set(table(em, "estimates")["ticker"].to_list())) == ["300308.SZ", "300502.SZ"]
    assert sorted(set(table(em, "brokers")["ticker"].to_list())) == ["300308.SZ", "300502.SZ"]
    assert sorted(set(table(em, "vintages")["ticker"].to_list())) == ["300308.SZ", "300502.SZ"]
    assert res["rows"]["estimates"] == 8  # 2 listings x 4 years, not just this run's 4


def test_a_rerun_of_the_same_listing_wins_on_the_key(em):
    em([A], {"SZ300308": payload_for(29.0)})
    em([A], {"SZ300308": payload_for(31.0)})

    df = table(em, "estimates").filter(pl.col("period_end") == "2026-12-31")
    assert df.height == 1  # merged on (ticker, source, period_end), not duplicated
    assert df["eps_avg"].to_list() == [31.0]


def test_an_empty_run_leaves_the_days_good_data_alone(em):
    """An East Money outage answers 200 with empty blocks. That must not blank the table."""
    em([A], {"SZ300308": payload_for(29.0)})
    res = em([A, B], {"SZ300308": EMPTY_PAYLOAD, "SZ300502": EMPTY_PAYLOAD})

    assert res["rows"] == {}  # nothing written rather than an empty table over a good one
    assert table(em, "estimates")["eps_avg"].to_list() == [9.166453796306, 29.0, 50.0, 80.0]
    assert (em.snap / "runs.jsonl").read_text(encoding="utf-8").strip().count("\n") == 1  # both runs recorded


def test_coverage_is_green_when_only_the_known_gaps_are_empty(em):
    res = em([A, FARASIS], {"SZ300308": payload_for(29.0), "SH688567": EMPTY_PAYLOAD})

    coverage = next(c for c in res["checks"] if c["name"] == "East Money coverage")
    assert coverage["status"] == "pass"  # used to fail on every clean run, so it reported nothing
    assert res["no_estimates"] == ["688567.SS"]
    assert res["no_estimates_unexpected"] == []
    assert "688567.SS" in EXPECTED_EMPTY


def test_coverage_goes_red_when_a_covered_listing_stops_arriving(em):
    """The outage shape: HTTP 200, empty blocks, no error. The check has to be the one to see it."""
    res = em([A, FARASIS], {"SZ300308": EMPTY_PAYLOAD, "SH688567": EMPTY_PAYLOAD})

    coverage = next(c for c in res["checks"] if c["name"] == "East Money coverage")
    assert coverage["status"] == "fail"
    assert res["no_estimates_unexpected"] == ["300308.SZ"]
    assert "300308.SZ" in coverage["detail"]


def test_the_run_record_says_what_the_vintage_series_measures(em):
    assert em([A], {"SZ300308": payload_for(29.0)})["vintage_series"] == "coverage"


# --- the raw archive is append-only (CLAUDE.md rule 2) ------------------------------
def test_an_identical_payload_is_recognised_rather_than_archived_twice(tmp_path):
    first = archive_payload({"ycmx": [1]}, tmp_path, "SZ300308", "0900")
    again = archive_payload({"ycmx": [1]}, tmp_path, "SZ300308", "1400")

    assert again == first
    assert sorted(p.name for p in tmp_path.iterdir()) == ["SZ300308.json.gz"]


def test_a_differing_payload_lands_beside_the_first_instead_of_replacing_it(tmp_path):
    """The S6 failure: `--limit` smoke runs overwrote the evidence behind the full run's parquet."""
    first = archive_payload({"ycmx": [1]}, tmp_path, "SZ300308", "0900")
    second = archive_payload({"ycmx": [2]}, tmp_path, "SZ300308", "1400")
    third = archive_payload({"ycmx": [3]}, tmp_path, "SZ300308", "1400")  # same minute, third body

    assert len({first, second, third}) == 3
    assert second.name == "SZ300308__1400.json.gz"
    assert third.name == "SZ300308__1400_2.json.gz"
    bodies = [json.loads(gzip.decompress(p.read_bytes())) for p in (first, second, third)]
    assert bodies == [{"ycmx": [1]}, {"ycmx": [2]}, {"ycmx": [3]}]


def test_an_unreadable_archive_is_kept_and_the_payload_written_beside_it(tmp_path):
    (tmp_path / "SZ300308.json.gz").write_bytes(b"not gzip at all")
    path = archive_payload({"ycmx": [1]}, tmp_path, "SZ300308", "1400")

    assert path.name == "SZ300308__1400.json.gz"
    assert (tmp_path / "SZ300308.json.gz").read_bytes() == b"not gzip at all"


def test_a_rerun_keeps_the_earlier_runs_payload_on_disk(em, tmp_path):
    """Through snapshot(): the parquet for a run has to stay reproducible from that run's raw layer."""
    em([A], {"SZ300308": payload_for(29.0)})
    em([A], {"SZ300308": payload_for(31.0)})

    raw = sorted((tmp_path / "raw" / "valuation" / "eastmoney").glob("*/SZ300308*.json.gz"))
    assert len(raw) == 2, [p.name for p in raw]
    eps = sorted(
        json.loads(gzip.decompress(p.read_bytes()))["ycmx"][0]["EPS2"] for p in raw
    )
    assert eps == [29.0, 31.0]


def test_main_exits_zero_when_the_only_gap_is_an_expected_one(monkeypatch):
    canned = {
        "rows": {}, "brokers": 0, "seconds": 0.1, "checks": [],
        "errors": [], "no_estimates": ["688567.SS"], "no_estimates_unexpected": [],
    }
    monkeypatch.setattr(estimates_em, "snapshot", lambda companies, **kw: canned)
    assert estimates_em.main(["--only", "688567.SS"]) == 0

    canned["no_estimates"] = ["688567.SS", "300308.SZ"]
    canned["no_estimates_unexpected"] = ["300308.SZ"]
    assert estimates_em.main(["--only", "688567.SS"]) == 1
