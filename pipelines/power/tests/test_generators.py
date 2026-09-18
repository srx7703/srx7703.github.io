"""Pure-function tests for the EIA-860M module. No network: every workbook is an inline frame.

The fixtures keep the *shapes* of the real July 2026 edition, which is the part that bites. fastexcel
types the same column differently on different sheets, so the Planned fixture carries Int64 years
while the Operating one carries the String column whose empty cells are a single space, and the
Retired fixture carries capacities as strings. The Puerto Rico fixture carries the trailing blank
rows those tabs are padded with, and the Canceled fixture has no Status column at all, because the
real one does not.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from pandera.errors import SchemaError

from pipelines.power.config import EIA_860M_LOOKBACK_MONTHS
from pipelines.power.generators import (
    archive_workbook,
    coal_retirement_gw,
    deferrals,
    edition_urls,
    family_of,
    fetch_edition,
    parse_sheet,
    resolve_vintage,
    retirement_rows,
    shift_vintage,
    status_code,
    vintage_candidates,
    vintage_data_end,
)
from pipelines.power.schema import (
    DEFERRALS_SCHEMA,
    GENERATORS_SCHEMA,
    RETIREMENTS_SCHEMA,
    deferrals_frame,
    generators_frame,
    retirements_frame,
)

TS = "2026-09-18T12:00:00+00:00"
VINTAGE = "2026-07"

UNDER_CONSTRUCTION = "(U) Under construction, less than or equal to 50 percent complete"
HALF_BUILT = "(V) Under construction, more than 50 percent complete"
COMMISSIONING = "(TS) Construction complete, but not yet in commercial operation"
OPERATING = "(OP) Operating"
PENDING = "(L) Regulatory approvals pending. Not under construction"


def planned_sheet() -> pl.DataFrame:
    """Planned: years are Int64 here, and Status is always populated."""
    return pl.DataFrame(
        {
            "Entity Name": ["Homer City Redevelopment", "Fermi America", "TBE Montgomery LLC"],
            "Plant ID": [66872, 68410, 57472],
            "Plant Name": ["Homer City Energy Campus", "Matador Nuclear", "TBE Montgomery"],
            "Plant State": ["PA", "TX", "AL"],
            "County": ["Indiana", "Hutchinson", "Montgomery"],
            "Balancing Authority Code": ["PJM", "SWPP", "SOCO"],
            "Sector": ["IPP Non-CHP", "IPP Non-CHP", "Industrial Non-CHP"],
            "Generator ID": ["CT1", "U1", "CTG"],
            "Nameplate Capacity (MW)": [450.0, 1100.0, 23.5],
            "Net Summer Capacity (MW)": [430.0, 1080.0, 22.0],
            "Technology": [
                "Natural Gas Fired Combined Cycle",
                "Nuclear",
                "Other Waste Biomass",
            ],
            "Energy Source Code": ["NG", "NUC", "OBL"],
            "Prime Mover Code": ["CT", "ST", "CT"],
            "Planned Operation Month": [6, 12, 9],
            "Planned Operation Year": [2027, 2032, 2026],
            "Status": [UNDER_CONSTRUCTION, PENDING, HALF_BUILT],
        },
        schema_overrides={"Plant ID": pl.Int64, "Planned Operation Year": pl.Int64},
    )


def operating_sheet() -> pl.DataFrame:
    """Operating: the planned-retirement columns are String, and an empty cell is a single space.

    Row 2 is a coal unit with a date, row 3 a coal unit whose date EIA has since withdrawn, row 4 a
    unit with no date at all, and row 5 has a blank technology cell -- two of those exist in the real
    July 2026 workbook.
    """
    return pl.DataFrame(
        {
            "Entity Name": ["Duke Energy Indiana", "Georgia Power Co", "Dominion", "Old Muni"],
            "Plant ID": [1008, 709, 3803, 1326],
            "Plant Name": ["Gibson", "Bowen", "Chesterfield", "Unnamed Steam"],
            "Plant State": ["IN", "GA", "VA", "OH"],
            "County": ["Gibson", "Bartow", "Chesterfield", "Lucas"],
            "Balancing Authority Code": ["MISO", "SOCO", "PJM", "PJM"],
            "Sector": ["Electric Utility"] * 4,
            "Generator ID": ["1", "3", "7", "3"],
            "Nameplate Capacity (MW)": [668.0, 880.0, 180.0, 3.0],
            "Net Summer Capacity (MW)": [622.0, 810.0, 172.0, 2.8],
            "Technology": [
                "Conventional Steam Coal",
                "Conventional Steam Coal",
                "Natural Gas Fired Combustion Turbine",
                "",  # the blank cell: a family, not a crash
            ],
            "Energy Source Code": ["BIT", "BIT", "NG", "NG"],
            "Prime Mover Code": ["ST", "ST", "GT", "ST"],
            "Operating Month": [2, 5, 11, 1],
            "Operating Year": [1976, 1971, 1990, 1954],
            "Planned Retirement Month": ["12", "6", " ", " "],
            "Planned Retirement Year": ["2028", "2027", " ", " "],
            "Status": [OPERATING, OPERATING, OPERATING, "(SB) Standby/Backup: available for service"],
        },
        schema_overrides={"Plant ID": pl.Int64, "Planned Retirement Year": pl.Utf8},
    )


def retired_sheet() -> pl.DataFrame:
    """Retired: no Status column, and Net Summer arrives as strings on this tab."""
    return pl.DataFrame(
        {
            "Entity Name": ["Zapco Energy Tactics Corp", "Cargill Inc"],
            "Plant ID": [50348, 54965],
            "Plant Name": ["Zapco Sunnyvale", "Cargill Dayton"],
            "Plant State": ["CA", "OH"],
            "Generator ID": ["GEN6", "DCT"],
            "Nameplate Capacity (MW)": [1.5, 16.0],
            "Net Summer Capacity (MW)": ["1.5", "16"],
            "Technology": ["Landfill Gas", "Conventional Steam Coal"],
            "Operating Month": [6, 1],
            "Operating Year": [1989, 1954],
            "Retirement Month": [3, 11],
            "Retirement Year": [2025, 2026],
        },
        schema_overrides={"Plant ID": pl.Int64, "Retirement Year": pl.Int64},
    )


def canceled_sheet() -> pl.DataFrame:
    """Canceled or Postponed: no Status, no operation date, capacities as strings."""
    return pl.DataFrame(
        {
            "Entity Name": ["Echols Grove LLC", "SV CSG Prophetstown 1, LLC"],
            "Plant ID": [68902, 69162],
            "Plant Name": ["Echols Grove Solar", "SV CSG Prophetstown 1"],
            "Plant State": ["GA", "IL"],
            "Generator ID": ["ECHBA", "PROPT"],
            "Nameplate Capacity (MW)": [100.0, 4.9],
            "Net Summer Capacity (MW)": ["100", "4.9"],
            "Technology": ["Batteries", "Solar Photovoltaic"],
            "Energy Source Code": ["MWH", "SUN"],
            "Prime Mover Code": ["BA", "PV"],
        },
        schema_overrides={"Plant ID": pl.Int64},
    )


def planned_pr_sheet() -> pl.DataFrame:
    """Planned_PR: five real rows and two blank padding rows, as the real tab is shaped."""
    return pl.DataFrame(
        {
            "Entity Name": ["Genera PR LLC", None],
            "Plant ID": [62410, None],
            "Plant Name": ["Palo Seco", None],
            "Plant State": ["PR", None],
            "Generator ID": ["MG1", None],
            "Nameplate Capacity (MW)": [21.0, None],
            "Net Summer Capacity (MW)": [20.0, None],
            "Technology": ["Natural Gas Internal Combustion Engine", None],
            "Planned Operation Month": [3, None],
            "Planned Operation Year": [2027, None],
            "Status": [COMMISSIONING, None],
        },
        schema_overrides={"Plant ID": pl.Int64, "Planned Operation Year": pl.Int64},
    )


# --- status ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (UNDER_CONSTRUCTION, "U"),
        (COMMISSIONING, "TS"),
        (OPERATING, "OP"),
        ("(OS) Out of service and NOT expected to return to service in next calendar year", "OS"),
        ("  (P) Planned for installation, but regulatory approvals not initiated", "P"),
        ("(u) under construction", "U"),
    ],
)
def test_status_code_parses_the_bracketed_code(raw, expected):
    assert status_code(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "Operating", "U) Under construction", "[U] Under construction"])
def test_status_code_returns_none_for_a_malformed_status(raw):
    """A status EIA reformats must not become a guessed stage; the row lands with a null one."""
    assert status_code(raw) is None


# --- technology --------------------------------------------------------------------------------
def test_family_of_maps_a_known_technology():
    assert family_of("Natural Gas Fired Combined Cycle") == "gas_cc"
    assert family_of("Offshore Wind Turbine") == "wind"
    assert family_of("Hydroelectric Pumped Storage") == "storage"


def test_family_of_raises_on_an_unmapped_technology():
    """A technology entering the inventory for the first time is the news, so the run stops."""
    with pytest.raises(ValueError, match="unmapped EIA technology"):
        family_of("Small Modular Reactor")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_family_of_files_a_blank_technology_as_other(blank):
    assert family_of(blank) == "other"


def test_blank_technology_row_survives_parsing_and_the_schema():
    rows = parse_sheet(operating_sheet(), "operating", VINTAGE, TS)
    blanks = [r for r in rows if r["family"] == "other"]
    assert len(blanks) == 1
    # the schema forbids a null technology, so the blank is kept as "" rather than dropped to None
    assert blanks[0]["technology"] == ""
    assert blanks[0]["plant_id"] == "1326"
    GENERATORS_SCHEMA.validate(generators_frame(rows))


# --- parse_sheet -------------------------------------------------------------------------------
def test_parse_sheet_planned_reads_the_planned_operation_date():
    rows = parse_sheet(planned_sheet(), "planned", VINTAGE, TS)
    assert len(rows) == 3
    homer = rows[0]
    assert homer["sheet"] == "planned"
    assert homer["vintage"] == VINTAGE
    assert homer["plant_id"] == "66872"  # an Int64 id becomes a plain string key, not "66872.0"
    assert homer["generator_id"] == "CT1"
    assert homer["family"] == "gas_cc"
    assert homer["status_code"] == "U"
    assert homer["stage"] == "under_construction"
    assert (homer["operation_year"], homer["operation_month"]) == (2027, 6)
    assert rows[1]["stage"] == "announced"
    GENERATORS_SCHEMA.validate(generators_frame(rows))


def test_parse_sheet_operating_reads_the_operating_date_not_the_retirement_one():
    rows = parse_sheet(operating_sheet(), "operating", VINTAGE, TS)
    gibson = rows[0]
    assert gibson["operation_year"] == 1976
    assert gibson["stage"] == "in_service"
    assert "retirement_year" not in gibson


def test_parse_sheet_canceled_has_no_status_and_no_date():
    rows = parse_sheet(canceled_sheet(), "canceled", VINTAGE, TS)
    assert [r["sheet"] for r in rows] == ["canceled", "canceled"]
    assert all(r["status_code"] is None and r["stage"] is None for r in rows)
    assert all(r["operation_year"] is None for r in rows)
    # capacities arrive as strings on this tab and must still land as floats
    assert rows[0]["net_summer_mw"] == 100.0
    GENERATORS_SCHEMA.validate(generators_frame(rows))


def test_parse_sheet_drops_the_blank_padding_rows_on_a_puerto_rico_tab():
    rows = parse_sheet(planned_pr_sheet(), "planned", VINTAGE, TS, default_state="PR")
    assert len(rows) == 1
    assert rows[0]["state"] == "PR"
    assert rows[0]["stage"] == "commissioning"


def test_parse_sheet_defaults_the_state_when_a_puerto_rico_row_omits_it():
    df = planned_pr_sheet().head(1).with_columns(pl.lit(None, dtype=pl.Utf8).alias("Plant State"))
    assert parse_sheet(df, "planned", VINTAGE, TS, default_state="PR")[0]["state"] == "PR"


def test_float_years_with_nulls_never_become_zero():
    """fastexcel hands back a float column when a year column is mixed. 2028.0 is 2028, and a null
    stays null -- a fillna(0) here would file un-dated units under the year 0."""
    df = pl.DataFrame(
        {
            "Plant ID": [1.0, 2.0, 3.0],
            "Generator ID": ["A", "B", "C"],
            "Technology": ["Solar Photovoltaic"] * 3,
            "Nameplate Capacity (MW)": [10.0, None, 30.0],
            "Planned Operation Year": [2027.0, None, 2028.0],
            "Planned Operation Month": [6.0, None, None],
            "Status": [UNDER_CONSTRUCTION] * 3,
        },
        schema_overrides={"Planned Operation Year": pl.Float64, "Plant ID": pl.Float64},
    )
    rows = parse_sheet(df, "planned", VINTAGE, TS)
    assert [r["operation_year"] for r in rows] == [2027, None, 2028]
    assert [r["operation_month"] for r in rows] == [6, None, None]
    assert [r["plant_id"] for r in rows] == ["1", "2", "3"]  # not "1.0"
    assert [r["nameplate_mw"] for r in rows] == [10.0, None, 30.0]
    GENERATORS_SCHEMA.validate(generators_frame(rows))


# --- retirements -------------------------------------------------------------------------------
def test_retirement_rows_splits_planned_from_actual():
    rows = retirement_rows(operating_sheet(), retired_sheet(), VINTAGE, TS)
    planned = [r for r in rows if r["kind"] == "planned"]
    actual = [r for r in rows if r["kind"] == "actual"]
    # only the two Operating units that carry a year; the ' ' cells are not retirements
    assert {(r["plant_id"], r["retirement_year"]) for r in planned} == {("1008", 2028), ("709", 2027)}
    assert {(r["plant_id"], r["retirement_year"]) for r in actual} == {("50348", 2025), ("54965", 2026)}
    assert planned[0]["retirement_month"] == 12
    RETIREMENTS_SCHEMA.validate(retirements_frame(rows))


def test_retirement_rows_accepts_missing_sheets():
    assert retirement_rows(None, retired_sheet(), VINTAGE, TS, default_state="PR")[0]["kind"] == "actual"
    assert [r["kind"] for r in retirement_rows(operating_sheet(), None, VINTAGE, TS)] == ["planned", "planned"]


def test_coal_retirement_gw_sums_nameplate_by_year():
    rows = retirement_rows(operating_sheet(), retired_sheet(), VINTAGE, TS)
    assert coal_retirement_gw(rows) == {2027: 0.88, 2028: 0.67}
    assert coal_retirement_gw(rows, "actual") == {2026: 0.02}


# --- deferrals ---------------------------------------------------------------------------------
def _old(plant: str, year: int | None, *, kind: str = "planned") -> dict:
    return {
        "snapshot_ts": TS, "vintage": "2025-07", "kind": kind, "plant_id": plant, "generator_id": "1",
        "plant_name": f"Plant {plant}", "state": "OH", "technology": "Conventional Steam Coal",
        "family": "coal", "nameplate_mw": 500.0, "retirement_year": year, "retirement_month": 6,
    }


def _new(plant: str, year: int | None, *, kind: str = "planned") -> dict:
    row = _old(plant, year, kind=kind)
    row["vintage"] = VINTAGE
    return row


def test_deferrals_finds_a_retirement_pushed_later():
    rows, accelerated = deferrals([_old("1008", 2026)], [_new("1008", 2029)], "2025-07", VINTAGE, TS)
    assert accelerated == 0
    assert len(rows) == 1
    row = rows[0]
    assert (row["year_before"], row["year_after"], row["deferred_years"]) == (2026, 2029, 3.0)
    assert (row["from_vintage"], row["to_vintage"]) == ("2025-07", VINTAGE)
    DEFERRALS_SCHEMA.validate(deferrals_frame(rows))


def test_deferrals_treats_a_withdrawn_date_as_a_deferral_of_unknown_length():
    """The unit is still on the Operating sheet with no retirement year, so it emits no retirement
    row at all in the new edition -- which is "none at all", not "zero years"."""
    rows, accelerated = deferrals([_old("709", 2027)], [], "2025-07", VINTAGE, TS)
    assert accelerated == 0
    assert (rows[0]["year_after"], rows[0]["deferred_years"]) == (None, None)
    DEFERRALS_SCHEMA.validate(deferrals_frame(rows))


def test_deferrals_excludes_a_retirement_pulled_earlier_and_counts_it():
    rows, accelerated = deferrals([_old("3803", 2030)], [_new("3803", 2027)], "2025-07", VINTAGE, TS)
    assert (rows, accelerated) == ([], 1)


def test_deferrals_ignores_an_unchanged_date_and_an_already_retired_unit():
    old = [_old("1008", 2028), _old("50348", 2025, kind="actual")]
    new = [_new("1008", 2028), _new("50348", 2025, kind="actual")]
    assert deferrals(old, new, "2025-07", VINTAGE, TS) == ([], 0)


def test_deferrals_counts_an_executed_retirement_that_slipped_later():
    """The unit did retire, but a year after it was scheduled to: it ran the extra year, which is
    what the table is for. An actual retirement outranks a planned one when both are present."""
    new = [_new("709", 2028, kind="actual"), _new("709", 2030)]
    rows, accelerated = deferrals([_old("709", 2027)], new, "2025-07", VINTAGE, TS)
    assert (rows[0]["year_after"], rows[0]["deferred_years"], accelerated) == (2028, 1.0, 0)


def test_deferrals_takes_attributes_from_the_newer_edition():
    new = _new("1008", 2029)
    new["plant_name"] = "Gibson (renamed)"
    rows, _ = deferrals([_old("1008", 2026)], [new], "2025-07", VINTAGE, TS)
    assert rows[0]["plant_name"] == "Gibson (renamed)"


def test_the_schema_is_why_an_acceleration_has_to_be_filtered():
    """Proof the filter is load-bearing: the row an acceleration would produce is rejected."""
    bad = [{
        "snapshot_ts": TS, "plant_id": "3803", "generator_id": "1", "plant_name": "Chesterfield",
        "state": "VA", "technology": "Conventional Steam Coal", "family": "coal", "nameplate_mw": 500.0,
        "from_vintage": "2025-07", "to_vintage": VINTAGE, "year_before": 2030, "year_after": 2027,
        "deferred_years": -3.0,
    }]
    with pytest.raises(SchemaError):
        DEFERRALS_SCHEMA.validate(deferrals_frame(bad))


# --- vintages and fetching -----------------------------------------------------------------------
def test_shift_vintage_crosses_the_year_boundary():
    assert shift_vintage("2026-07", -12) == "2025-07"
    assert shift_vintage("2026-01", -1) == "2025-12"
    assert shift_vintage("2025-12", 1) == "2026-01"


def test_vintage_candidates_walk_back_from_the_month_of_as_of():
    got = vintage_candidates(date(2026, 9, 18))
    assert got[:3] == ["2026-09", "2026-08", "2026-07"]
    assert len(got) == EIA_860M_LOOKBACK_MONTHS


def test_vintage_data_end_is_the_end_of_the_edition_month():
    assert vintage_data_end("2026-07") == date(2026, 7, 31)
    assert vintage_data_end("2026-02") == date(2026, 2, 28)


def test_edition_urls_name_the_month():
    current, archive = edition_urls("2026-07")
    assert current.endswith("/xls/july_generator2026.xlsx")
    assert archive.endswith("/archive/xls/july_generator2026.xlsx")


#: What EIA serves for a month it has not published: HTTP 200, text/html, the section index page.
INDEX_PAGE = b"<!DOCTYPE html>" + b"x" * 55_730
WORKBOOK = b"PK\x03\x04" + b"y" * 100


def test_resolve_vintage_walks_past_the_html_index_page():
    """Measured on 2026-09-18: september and august 2026 answer 200 with the index page under /xls/,
    july 2026 is a real workbook there. Size and status are useless; the "PK" is the test."""
    served = {"https://www.eia.gov/electricity/data/eia860m/xls/july_generator2026.xlsx": WORKBOOK}
    seen: list[str] = []

    def client(url: str) -> bytes | None:
        seen.append(url)
        body = served.get(url, INDEX_PAGE)
        return body if body[:2] == b"PK" else None

    vintage, url, body = resolve_vintage(date(2026, 9, 18), client)
    assert vintage == "2026-07"
    assert url.endswith("/xls/july_generator2026.xlsx")
    assert body == WORKBOOK
    assert len(seen) == 5  # sept current+archive, aug current+archive, july current


def test_resolve_vintage_falls_back_to_the_archive_path():
    served = {"https://www.eia.gov/electricity/data/eia860m/archive/xls/july_generator2025.xlsx": WORKBOOK}

    def client(url: str) -> bytes | None:
        return served.get(url)

    vintage, url, _ = resolve_vintage(date(2025, 7, 9), client)
    assert (vintage, "/archive/" in url) == ("2025-07", True)


def test_resolve_vintage_raises_when_nothing_is_published():
    with pytest.raises(FileNotFoundError, match="no EIA-860M edition"):
        resolve_vintage(date(2026, 9, 18), lambda url: None)


def test_fetch_edition_raises_for_an_unpublished_month():
    with pytest.raises(FileNotFoundError, match="2026-09"):
        fetch_edition("2026-09", lambda url: None)


# --- raw archive -------------------------------------------------------------------------------
def test_archive_workbook_never_overwrites_the_days_evidence(tmp_path):
    first = archive_workbook(WORKBOOK, tmp_path, "july_generator2026.xlsx", "0900")
    again = archive_workbook(WORKBOOK, tmp_path, "july_generator2026.xlsx", "1400")
    assert again == first  # identical bytes: the morning's archive is left exactly as it was
    reissued = archive_workbook(WORKBOOK + b"z", tmp_path, "july_generator2026.xlsx", "1400")
    assert reissued.name == "july_generator2026__1400.xlsx"
    assert first.read_bytes() == WORKBOOK


# --- the quality gate itself ------------------------------------------------------
# Both of these were found by an adversarial review on 2026-09-18: a renamed EIA column produced a
# snapshot of nulls, every check read green, and the run exited 0. They are here so that cannot
# happen quietly again.

from datetime import date as _date  # noqa: E402

from pipelines.power import generators as _gen  # noqa: E402


def _gen_row(**kw) -> dict:
    row = {
        "snapshot_ts": "2026-09-18T00:00:00+00:00", "vintage": "2026-07", "sheet": "planned",
        "plant_id": "1", "generator_id": "A", "plant_name": "P", "entity_name": "E",
        "state": "VA", "county": "C", "balancing_authority": "PJM", "sector": "IPP Non-CHP",
        "technology": "Natural Gas Fired Combined Cycle", "family": "gas_cc",
        "energy_source": "NG", "prime_mover": "CA", "nameplate_mw": 100.0, "net_summer_mw": 95.0,
        "status_code": "U", "stage": "under_construction",
        "operation_year": 2027, "operation_month": 6,
    }
    row.update(kw)
    return row


def _checks(gen_rows: list[dict]) -> dict[str, dict]:
    got = _gen.run_checks(
        vintage="2026-07", as_of=_date(2026, 9, 18), gen_rows=gen_rows, ret_rows=[], def_rows=[],
        accelerated=0, untraceable=0, written={"generators": len(gen_rows)}, errors=[],
    )
    return {c["name"]: c for c in got}


def test_losing_every_operation_year_is_not_a_pass():
    """A renamed date column empties the additions chart; it must not report green.

    The old predicate asked only about nameplate capacity, so a run in which every single
    operation_year failed to parse still said "pass" while the published page showed no planned
    additions at all.
    """
    rows = [_gen_row(plant_id=str(i), operation_year=None) for i in range(5)]
    assert _checks(rows)["Required numbers parsed"]["status"] == "fail"


def test_some_missing_operation_years_are_a_warning_not_a_failure():
    """Real inventories always have a few. The check has to separate "some" from "all"."""
    rows = [_gen_row(plant_id=str(i)) for i in range(5)] + [_gen_row(plant_id="9", operation_year=None)]
    got = _checks(rows)["Required numbers parsed"]
    assert got["status"] == "warn"
    assert "1 of 6 null" in got["detail"]


def test_all_numbers_parsed_is_a_pass():
    assert _checks([_gen_row(plant_id=str(i)) for i in range(5)])["Required numbers parsed"]["status"] == "pass"


def test_main_exits_nonzero_when_a_check_fails(monkeypatch, caplog):
    """The exit code is the CI gate. A failed check with no exception must still fail the run."""
    monkeypatch.setattr(_gen, "run", lambda **kw: {
        "vintage": "2026-07", "rows": {"generators": 0}, "accelerated_excluded": 0,
        "planned_coal_retirement_gw": {}, "seconds": 0.1, "errors": [],
        "checks": [{"name": "Sheet coverage", "status": "fail", "detail": "nothing parsed"}],
    })
    assert _gen.main([]) == 1


def test_main_exits_zero_when_every_check_passes(monkeypatch):
    monkeypatch.setattr(_gen, "run", lambda **kw: {
        "vintage": "2026-07", "rows": {"generators": 1}, "accelerated_excluded": 0,
        "planned_coal_retirement_gw": {}, "seconds": 0.1, "errors": [],
        "checks": [{"name": "Sheet coverage", "status": "pass", "detail": "fine"},
                   {"name": "Deferrals", "status": "warn", "detail": "a warning is not a failure"}],
    })
    assert _gen.main([]) == 0
