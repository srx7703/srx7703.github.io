"""Unit tests for pipelines.power.demand, against inline fixtures shaped like the real workbooks.

The fixtures reproduce what actually makes these two sheets awkward: 861M's value columns sit in
four-wide blocks under a merged sector heading, so the only thing keeping commercial sales out of
the residential column is arithmetic on the header rows; and 7atab repeats the label "Commercial
sector" in three different blocks in three different units, so a label match that is not scoped to
the consumption block picks up cents/kWh and calls it TWh.

No test touches the network.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipelines.power import demand as d
from pipelines.power.schema import SALES_SCHEMA, STEO_SCHEMA, sales_frame, steo_frame

TS = "2026-09-18T12:00:00+00:00"

# --- fixtures ---------------------------------------------------------------------------------


def _sheet(rows: list[list[str | None]]) -> pl.DataFrame:
    """A header-less read: every cell a string, columns named column_1..column_N."""
    width = max(len(r) for r in rows)
    schema = {f"column_{i + 1}": pl.Utf8 for i in range(width)}
    padded = [[*r, *[None] * (width - len(r))] for r in rows]
    return pl.DataFrame([dict(zip(schema, r, strict=True)) for r in padded], schema=schema)


MEASURES = ["Revenue", "Sales", "Customers", "Price"]
UNITS = ["Thousand Dollars", "Megawatthours", "Count", "Cents/kWh"]
GROUPS = ["RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL", "TRANSPORTATION", "TOTAL"]
FOOTNOTE = "For October and November 2017, Puerto Rico Electric Power Authority supplied their data in the aggregate."


def _block(group: str) -> list[str | None]:
    return [group, None, None, None]


def _sales_sheet(body: list[list[str | None]], *, units: list[str] | None = None) -> pl.DataFrame:
    units = units or UNITS
    return _sheet(
        [
            ["", None, None, None, *[c for g in GROUPS for c in _block(g)]],
            ["", None, None, None, *(MEASURES * len(GROUPS))],
            ["Year", "Month", "State", "Data Status", *(units * len(GROUPS))],
            *body,
            [FOOTNOTE],
        ]
    )


def _values(res: float, com: float, ind: float, trn: float, tot: float) -> list[str]:
    """One row's twenty value cells: revenue, sales, customers, price for each of the five blocks.

    Revenue is set so that the derived price is exactly 10 cents/kWh (revenue in thousand dollars,
    sales in MWh: revenue * 100 / sales), which makes a misread column obvious.
    """
    out: list[str] = []
    for sales in (res, com, ind, trn, tot):
        out += [f"{sales / 10:.1f}", f"{sales:.1f}", "1000", "10.0"]
    return out


VA_JUN = ["2026", "6", "VA", "Preliminary", *_values(3000.0, 7000.0, 900.0, 10.0, 10910.0)]
MD_JUN = ["2026", "6", "MD", "Preliminary", *_values(2000.0, 1500.0, 300.0, 5.0, 3805.0)]
VA_MAY = ["2026", "5", "VA", "Final", *_values(2800.0, 6800.0, 880.0, 9.0, 10489.0)]


def _steo_sheet(
    *,
    commercial_id: str | None = "ELCCP_US",
    drop_month: bool = False,
    title: str | None = "U.S. Energy Information Administration | Short-Term Energy Outlook  - September 2026",
    forecast_date: str = "Thursday, September 3, 2026",
) -> pl.DataFrame:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def series(sid: str | None, label: str, a: float, b: float) -> list[str | None]:
        second = [f"{b}"] * 12
        if drop_month:
            second[7] = ""
        return [sid, label, *([f"{a}"] * 12), *second]

    return _sheet(
        [
            ["Table of Contents", "Table 7a.  U.S. Electricity Industry Overview"],
            [None, title],
            ["Forecast date:", None, "2025", *([None] * 11), "2026", *([None] * 11)],
            [forecast_date, None, *months, *months],
            [None, "Electricity supply (billion kilowatthours)"],
            series("CMEOTWH", "Commercial sector", 1.4, 1.5),  # generation, not consumption
            [None, "Electricity consumption (billion kilowatthours)"],
            series("ELCOTWH", "Total consumption ", 300.0, 310.0),
            series("ELTCTWH", "Sales to ultimate customers", 290.0, 300.0),
            series("ELRCP_US", "Residential sector", 120.0, 125.0),
            series(commercial_id, "Commercial sector", 100.0, 110.0),
            series("ELICP_US", "Industrial sector", 80.0, 82.0),
            series("ELACP_US", "Transportation sector", 0.5, 0.6),
            series("ELDUTWH", "Direct use (d)", 11.0, 11.5),
            [None, "Prices to ultimate customers (cents per kilowatthour)"],
            series("ESCMUUS", "Commercial sector", 12.4, 12.9),  # cents/kWh; the wrong row to grab
        ]
    )


# --- 861M -------------------------------------------------------------------------------------


def test_parse_sales_reads_each_sector_from_its_own_block():
    rows = d.parse_sales(_sales_sheet([VA_JUN]), TS)
    by_sector = {r["sector"]: r for r in rows}
    assert set(by_sector) == set(d.SECTORS)
    assert by_sector["residential"]["sales_mwh"] == 3000.0
    assert by_sector["commercial"]["sales_mwh"] == 7000.0
    assert by_sector["industrial"]["sales_mwh"] == 900.0
    assert by_sector["transportation"]["sales_mwh"] == 10.0
    assert by_sector["total"]["sales_mwh"] == 10910.0
    assert by_sector["commercial"]["revenue_kusd"] == 700.0
    assert by_sector["commercial"]["price_cents_kwh"] == 10.0
    assert by_sector["commercial"]["customers"] == 1000.0


def test_parse_sales_keys_and_status():
    row = d.parse_sales(_sales_sheet([VA_JUN, VA_MAY]), TS)[0]
    assert row["period"] == "2026-06"
    assert row["state"] == "VA"
    assert row["data_status"] == "Preliminary"
    assert row["snapshot_ts"] == TS
    assert {r["data_status"] for r in d.parse_sales(_sales_sheet([VA_MAY]), TS)} == {"Final"}


def test_parse_sales_drops_the_footnote_and_malformed_rows():
    body = [
        VA_JUN,
        ["2026", "13", "VA", "Preliminary", *_values(1, 1, 1, 1, 4)],  # impossible month
        ["", "6", "VA", "Preliminary", *_values(1, 1, 1, 1, 4)],  # no year
        ["2026", "6", "Total", "Preliminary", *_values(1, 1, 1, 1, 4)],  # not a state code
    ]
    rows = d.parse_sales(_sales_sheet(body), TS)
    assert {(r["period"], r["state"]) for r in rows} == {("2026-06", "VA")}
    assert len(rows) == len(d.SECTORS)


def test_parse_sales_tolerates_numeric_year_and_month_cells():
    """fastexcel types a column as float when nothing textual shares it; 2026.0 must still parse."""
    sheet = _sales_sheet([VA_JUN])
    sheet = sheet.with_columns(
        pl.when(pl.col("column_1") == "2026").then(pl.lit("2026.0")).otherwise(pl.col("column_1")).alias("column_1"),
        pl.when(pl.col("column_2") == "6").then(pl.lit("6.0")).otherwise(pl.col("column_2")).alias("column_2"),
    )
    assert {r["period"] for r in d.parse_sales(sheet, TS)} == {"2026-06"}


def test_parse_sales_refuses_a_changed_unit():
    changed = ["Thousand Dollars", "Gigawatthours", "Count", "Cents/kWh"]
    with pytest.raises(ValueError, match="gigawatthours"):
        d.parse_sales(_sales_sheet([VA_JUN], units=changed), TS)


def test_parse_sales_handles_the_us_ytd_shape():
    """US-YTD has no State column, spells the month header MONTH, and marks annual rows with '.'."""
    sheet = _sheet(
        [
            ["", None, None, *[c for g in GROUPS for c in _block(g)]],
            ["", None, None, *(MEASURES * len(GROUPS))],
            ["Year", "MONTH", "Data Status", *(UNITS * len(GROUPS))],
            ["2026", "6", "Preliminary", *_values(3000.0, 7000.0, 900.0, 10.0, 10910.0)],
            ["2026", ".", "Preliminary", *_values(9e4, 9e4, 9e4, 9e4, 9e4)],
            [FOOTNOTE],
        ]
    )
    rows = d.parse_sales(sheet, TS, state="US")
    assert {r["period"] for r in rows} == {"2026-06"}
    assert {r["state"] for r in rows} == {"US"}
    assert next(r for r in rows if r["sector"] == "commercial")["sales_mwh"] == 7000.0


def test_parse_sales_without_a_state_column_and_no_override_raises():
    sheet = _sheet(
        [
            ["", None, None, *[c for g in GROUPS for c in _block(g)]],
            ["", None, None, *(MEASURES * len(GROUPS))],
            ["Year", "MONTH", "Data Status", *(UNITS * len(GROUPS))],
            ["2026", "6", "Preliminary", *_values(1, 1, 1, 1, 4)],
        ]
    )
    with pytest.raises(ValueError, match="no State column"):
        d.parse_sales(sheet, TS)


def test_national_rows_sums_states_and_reweights_price():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN, VA_MAY]), TS)
    us = d.national_rows(states)
    by_key = {(r["period"], r["sector"]): r for r in us}
    assert by_key[("2026-06", "commercial")]["sales_mwh"] == 8500.0
    assert by_key[("2026-06", "commercial")]["revenue_kusd"] == 850.0
    assert by_key[("2026-06", "commercial")]["price_cents_kwh"] == pytest.approx(10.0)
    assert by_key[("2026-06", "commercial")]["customers"] == 2000.0
    assert by_key[("2026-05", "commercial")]["sales_mwh"] == 6800.0  # May has one state only
    assert {r["state"] for r in us} == {"US"}
    assert len(us) == 2 * len(d.SECTORS)


def test_national_rows_price_is_sales_weighted_not_averaged():
    """Two states at 10 and 30 cents with very different volumes must not average to 20."""
    cheap = ["2026-06", "VA", 9000.0, 900.0]
    dear = ["2026-06", "DC", 1000.0, 300.0]
    rows = [
        {"snapshot_ts": TS, "period": p, "state": s, "sector": "commercial", "sales_mwh": mwh,
         "revenue_kusd": rev, "customers": 1.0, "price_cents_kwh": rev * 100 / mwh, "data_status": "Final"}
        for p, s, mwh, rev in (cheap, dear)
    ]
    us = d.national_rows(rows)[0]
    assert us["price_cents_kwh"] == pytest.approx(120000.0 / 10000.0)  # 12.0, not (10 + 30) / 2


def test_national_rows_ignores_existing_us_rows_so_it_is_idempotent():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN]), TS)
    once = d.national_rows(states)
    twice = d.national_rows(states + once)
    assert once == twice


def test_national_rows_flags_a_mixed_data_status():
    states = d.parse_sales(_sales_sheet([VA_JUN]), TS) + d.parse_sales(_sales_sheet([MD_JUN]), TS)
    states[0]["data_status"] = "Final"
    assert d.national_rows(states)[0]["data_status"] in ("Mixed", "Preliminary")
    assert any(r["data_status"] == "Mixed" for r in d.national_rows(states))


def test_national_rows_keeps_null_when_nothing_reported():
    rows = [{"snapshot_ts": TS, "period": "2026-06", "state": "VA", "sector": "transportation",
             "sales_mwh": None, "revenue_kusd": None, "customers": None,
             "price_cents_kwh": None, "data_status": "Final"}]
    us = d.national_rows(rows)[0]
    assert us["sales_mwh"] is None and us["price_cents_kwh"] is None


def test_cross_check_passes_when_the_columns_line_up():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN]), TS)
    us = d.national_rows(states)
    n, worst, _ = d.cross_check_national(us, us)
    assert n == len(d.SECTORS) and worst == 0.0


def test_cross_check_catches_a_column_shifted_by_one_block():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN]), TS)
    us = d.national_rows(states)
    order = list(d.SECTORS)
    shifted = [dict(r, sector=order[(order.index(r["sector"]) + 1) % len(order)]) for r in us]
    n, worst, where = d.cross_check_national(shifted, us)
    assert n == len(d.SECTORS)
    assert worst > d.CROSS_CHECK_TOLERANCE
    assert where


def test_period_age_days_counts_from_the_end_of_the_month():
    from datetime import date

    assert d._period_age_days("2026-06", date(2026, 9, 18)) == 79
    assert d._period_age_days("2026-12", date(2027, 1, 1)) == 0


def test_sales_checks_report_freshness_coverage_and_the_cross_check():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN]), TS)
    us = d.national_rows(states)
    names = {c["name"]: c for c in d.sales_checks(states, us, (10, 1e-9, ""))}
    assert names["EIA-861M sector coverage"]["status"] == "pass"
    assert names["EIA-861M national cross-check"]["status"] == "pass"
    assert names["EIA-861M state coverage"]["status"] in ("warn", "fail")  # the fixture has two states
    failing = {c["name"]: c for c in d.sales_checks(states, us, (10, 0.2, "2026-06 commercial"))}
    assert failing["EIA-861M national cross-check"]["status"] == "fail"
    assert "2026-06 commercial" in failing["EIA-861M national cross-check"]["detail"]


def test_parsed_sales_satisfy_the_contract_schema():
    states = d.parse_sales(_sales_sheet([VA_JUN, MD_JUN, VA_MAY]), TS)
    df = sales_frame(states + d.national_rows(states))
    SALES_SCHEMA.validate(df)
    assert df.height == 3 * len(d.SECTORS) + 2 * len(d.SECTORS)


# --- STEO -------------------------------------------------------------------------------------


def test_steo_vintage_from_the_title_line():
    assert d.steo_vintage(_steo_sheet()) == "2026-09"


def test_steo_vintage_falls_back_to_the_forecast_date():
    assert d.steo_vintage(_steo_sheet(title="Table 7a")) == "2026-09"


def test_steo_vintage_raises_when_the_sheet_says_nothing():
    with pytest.raises(ValueError, match="no STEO vintage"):
        d.steo_vintage(_steo_sheet(title="Table 7a", forecast_date="Thursday"))


def test_parse_steo_finds_every_consumption_series_by_id():
    rows = d.parse_steo(_steo_sheet(), TS)
    assert {r["series_id"] for r in rows} == {sid for sid, _ in d.CONSUMPTION_SERIES}
    assert len(rows) == len(d.CONSUMPTION_SERIES) * 24
    assert {r["frequency"] for r in rows} == {"monthly"}
    assert {r["vintage"] for r in rows} == {"2026-09"}
    assert {r["unit"] for r in rows} == {d.STEO_UNIT}


def test_parse_steo_forward_fills_the_year_and_reads_the_right_cells():
    rows = {(r["series_id"], r["period"]): r for r in d.parse_steo(_steo_sheet(), TS)}
    assert rows[("ELCCP_US", "2025-01")]["value"] == 100.0
    assert rows[("ELCCP_US", "2025-12")]["value"] == 100.0
    assert rows[("ELCCP_US", "2026-01")]["value"] == 110.0
    assert rows[("ELCCP_US", "2026-06")]["label"] == "Commercial sector"
    assert rows[("ELDUTWH", "2026-06")]["label"] == "Direct use"  # footnote marker stripped


def test_parse_steo_never_reads_the_forecast_date_cell_as_a_month():
    """The cell left of Jan holds a date whose month name would match a prefix search."""
    rows = d.parse_steo(_steo_sheet(forecast_date="September 3, 2026"), TS)
    assert len(rows) == len(d.CONSUMPTION_SERIES) * 24
    assert all(r["value"] is not None for r in rows)


def test_parse_steo_label_fallback_stays_inside_the_consumption_block():
    """With the id gone, the label must match the consumption row, not the price row of the same
    name — 12.9 cents/kWh landing in a TWh column is exactly how the wrong number gets published."""
    rows = {(r["series_id"], r["period"]): r for r in d.parse_steo(_steo_sheet(commercial_id=None), TS)}
    assert rows[("ELCCP_US", "2026-01")]["value"] == 110.0
    assert rows[("ELCCP_US", "2025-01")]["value"] == 100.0


def test_parse_steo_skips_a_series_that_vanished_rather_than_losing_the_rest(caplog):
    sheet = _steo_sheet()
    sheet[13, "column_1"] = "RENAMED"  # ELDUTWH's id, and its label no longer matches either
    sheet[13, "column_2"] = "Behind-the-meter use"
    rows = d.parse_steo(sheet, TS)
    assert "ELDUTWH" not in {r["series_id"] for r in rows}
    assert len({r["series_id"] for r in rows}) == len(d.CONSUMPTION_SERIES) - 1
    checks = {c["name"]: c for c in d.steo_checks(rows, d.annualise(rows), "2026-09")}
    assert checks["STEO series coverage"]["status"] == "fail"
    assert "ELDUTWH" in checks["STEO series coverage"]["detail"]


def test_annualise_sums_complete_years():
    annual = {(r["series_id"], r["period"]): r for r in d.annualise(d.parse_steo(_steo_sheet(), TS))}
    assert annual[("ELCCP_US", "2025")]["value"] == pytest.approx(1200.0)
    assert annual[("ELCCP_US", "2026")]["value"] == pytest.approx(1320.0)
    assert annual[("ELCCP_US", "2026")]["frequency"] == "annual"
    assert annual[("ELCCP_US", "2026")]["unit"] == d.STEO_UNIT
    assert annual[("ELCCP_US", "2026")]["vintage"] == "2026-09"


def test_annualise_refuses_a_partial_year():
    rows = d.annualise(d.parse_steo(_steo_sheet(drop_month=True), TS))
    assert {r["period"] for r in rows} == {"2025"}


def test_annualise_ignores_rows_that_are_already_annual():
    monthly = d.parse_steo(_steo_sheet(), TS)
    assert d.annualise(monthly + d.annualise(monthly)) == d.annualise(monthly)


def test_steo_checks_flag_a_stale_vintage():
    checks = {c["name"]: c for c in d.steo_checks(d.parse_steo(_steo_sheet(), TS), [], "2019-01")}
    assert checks["STEO vintage"]["status"] == "fail"


def test_parsed_steo_satisfies_the_contract_schema():
    monthly = d.parse_steo(_steo_sheet(), TS)
    df = steo_frame(monthly + d.annualise(monthly))
    STEO_SCHEMA.validate(df)
    assert df.height == len(d.CONSUMPTION_SERIES) * (24 + 2)


# --- writing ----------------------------------------------------------------------------------


def test_merge_write_keeps_earlier_rows_and_lets_new_ones_win(tmp_path):
    monthly = d.parse_steo(_steo_sheet(), TS)
    path = tmp_path / "2026-09-18.parquet"

    first = [r for r in monthly if r["series_id"] == "ELCCP_US"]
    d.merge_write(steo_frame(first), path, ["vintage", "series_id", "period"], STEO_SCHEMA)

    rest = [r for r in monthly if r["series_id"] != "ELCCP_US"]
    revised = [dict(r, value=999.0) for r in first[:1]]
    _, new, total = d.merge_write(steo_frame(rest + revised), path, ["vintage", "series_id", "period"], STEO_SCHEMA)

    out = pl.read_parquet(path)
    assert new == len(rest) + 1
    assert total == len(monthly)  # the first write's rows are still there
    assert out.filter(pl.col("period") == revised[0]["period"]).filter(
        pl.col("series_id") == "ELCCP_US"
    )["value"].item() == 999.0
