"""Unit tests for the PJM load-forecast parser and its derived readings.

Fixtures are inline and built from the real shapes of the two published workbooks: workbook 1's
YEAR column arrives as a string because fastexcel cannot type it, and workbook 2 keeps its title in
the first two rows, its years as floats on the header row, and a non-breaking space in the column
that otherwise holds the zone label. Nothing here touches the network.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipelines.power import gridops
from pipelines.power.schema import REGION_LOAD_SCHEMA, region_load_frame

SNAPSHOT_TS = "2026-09-18T12:00:00+00:00"


def sheet(zone: str, rows: list[tuple[int, int, int, int]]) -> pl.DataFrame:
    """A load-workbook sheet. YEAR is a string, which is how fastexcel returns it."""
    return pl.DataFrame({
        "ZONE_NAME": [zone] * len(rows),
        "YEAR": [str(r[0]) for r in rows],
        "MONTH": [r[1] for r in rows],
        "PEAK_MW": [r[2] for r in rows],
        "ENERGY_GWH": [r[3] for r in rows],
    })


def load_rows(zone: str, rows: list[tuple[int, int, int, int]], sheet_name: str | None = None) -> list[dict]:
    return gridops.load_sheet_rows(
        sheet_name or zone, sheet(zone, rows), vintage="2026", snapshot_ts=SNAPSHOT_TS
    )


# Two years of June-September for one zone, plus a January to prove the winter months are excluded
# from the summer peak. (year, month, peak_mw, energy_gwh)
DOM_SHEET = [
    (2026, 1, 24779, 13413),
    (2026, 6, 24100, 12900),
    (2026, 7, 25193, 14800),
    (2026, 8, 25193, 14700),  # a tie with July: the earlier month wins
    (2026, 9, 22900, 12100),
    (2031, 7, 32000, 18000),
    (2031, 8, 31000, 17500),
]

ADJUSTMENTS_GRID = [
    ["Table B-9b", None, None, None, None],
    ["Total Adjustments to Summer Peak Load (MW) for Each PJM Zone and RTO (2026 - 2046)",
     None, None, None, None],
    [None, "ZONENAME", "AREANAME", 2026.0, 2031.0],
    ["DOM", None, None, 7066.0, 15829.0],
    [None, "DOM", "NVEC", 1995.4, 4887.5],
    [None, "DOM", "ODEC", 384.9, 682.6],
    ["\xa0", None, None, None, None],          # the separator between blocks
    ["DAYTON", "DAY", "DAY", 0.0, 1392.0],     # spelled DAYTON here, DAY as a tab in workbook 1
    ["ATSI", None, None, 132.0, 1302.0],
    [None, "ATSI", "PP ", 0.0, 30.9],          # AREANAME carries a trailing space
    ["PJM RTO", None, None, 11479.0, 46648.0],
]


def adjustment_rows() -> list[dict]:
    return gridops.adjustment_rows(ADJUSTMENTS_GRID, vintage="2026", snapshot_ts=SNAPSHOT_TS)


# --- workbook 1 -------------------------------------------------------------------
def test_load_sheet_reads_string_years_and_emits_both_metrics():
    rows = load_rows("DOM", DOM_SHEET)
    assert len(rows) == 2 * len(DOM_SHEET)
    assert {r["metric"] for r in rows} == {"peak_mw", "energy_gwh"}
    july_peak = next(r for r in rows if r["metric"] == "peak_mw" and r["year"] == 2026 and r["month"] == 7)
    assert july_peak == {
        "snapshot_ts": SNAPSHOT_TS, "iso": "PJM", "zone": "DOM", "year": 2026, "month": 7,
        "metric": "peak_mw", "value": 25193.0, "vintage": "2026",
    }
    assert isinstance(july_peak["year"], int) and isinstance(july_peak["value"], float)


def test_load_sheet_keys_on_the_tab_name_not_the_cell():
    """SOUTHERNMA's ZONE_NAME cell is truncated to "SouthernM" in the real workbook."""
    rows = load_rows("SouthernM", [(2026, 7, 100, 50)], sheet_name="SOUTHERNMA")
    assert {r["zone"] for r in rows} == {"SOUTHERNMA"}


def test_load_sheet_skips_unparseable_rows_but_keeps_holes_in_the_grid():
    frame = sheet("DOM", [(2026, 7, 100, 50)])
    frame = pl.concat([
        frame,
        pl.DataFrame({"ZONE_NAME": ["DOM"], "YEAR": ["Source: PJM"], "MONTH": [None],
                      "PEAK_MW": [None], "ENERGY_GWH": [None]}),
        pl.DataFrame({"ZONE_NAME": ["DOM"], "YEAR": ["2026"], "MONTH": [8],
                      "PEAK_MW": [None], "ENERGY_GWH": [40]}),
    ], how="vertical_relaxed")
    rows = gridops.load_sheet_rows("DOM", frame, vintage="2026", snapshot_ts=SNAPSHOT_TS)
    assert len(rows) == 4  # the footer is gone, the August row survives with a null peak
    august = next(r for r in rows if r["month"] == 8 and r["metric"] == "peak_mw")
    assert august["value"] is None


def test_load_sheet_requires_its_columns():
    with pytest.raises(ValueError, match="PEAK_MW"):
        gridops.load_sheet_rows(
            "DOM", pl.DataFrame({"ZONE_NAME": ["DOM"], "YEAR": ["2026"], "MONTH": [7], "ENERGY_GWH": [1]}),
            vintage="2026", snapshot_ts=SNAPSHOT_TS,
        )


# --- workbook 2 -------------------------------------------------------------------
def test_adjustment_rows_find_the_header_and_the_published_values():
    rows = adjustment_rows()
    got = {(r["zone"], r["year"]): r["value"] for r in rows}
    assert got[("DOM", 2026)] == 7066.0
    assert got[("DOM", 2031)] == 15829.0
    assert got[("PJM_RTO", 2026)] == 11479.0
    assert all(r["month"] == 0 for r in rows)
    assert all(r["metric"] == "large_load_adjustment_mw" for r in rows)


def test_adjustment_rows_map_the_zone_spellings_of_workbook_2():
    zones = {r["zone"] for r in adjustment_rows()}
    assert "DAY" in zones and "DAYTON" not in zones      # DAYTON -> the DAY tab
    assert "PJM_RTO" in zones and "PJM RTO" not in zones  # a space is not a different zone
    assert gridops.normalise_adjustment_zone("PENLC") == "PN_FE_EAST"
    assert gridops.normalise_adjustment_zone("METED") == "METED_FE_EAST"


def test_adjustment_sub_areas_are_keyed_apart_from_their_zone():
    rows = adjustment_rows()
    zones = {r["zone"] for r in rows}
    assert {"DOM:NVEC", "DOM:ODEC", "ATSI:PP"} <= zones  # the trailing space in "PP " is stripped
    assert all(gridops.zone_role(z) == "subarea" for z in zones if ":" in z)
    # A sub-area must never be picked up by a lookup for its parent zone.
    dom_2026 = [r["value"] for r in rows if r["zone"] == "DOM" and r["year"] == 2026]
    assert dom_2026 == [7066.0]
    # A one-area zone (DAYTON/DAY/DAY on one line) is a zone total, not also a sub-area.
    assert not any(r["zone"].startswith("DAY:") for r in rows)


def test_adjustment_rows_reject_a_sheet_with_no_header():
    with pytest.raises(ValueError, match="ZONENAME"):
        gridops.adjustment_rows([["Table B-9b", None], [None, None]], vintage="2026", snapshot_ts=SNAPSHOT_TS)


# --- the merge --------------------------------------------------------------------
def test_merge_puts_both_workbooks_in_one_valid_table():
    load = load_rows("DOM", DOM_SHEET) + load_rows("PJM_RTO", [(2026, 7, 156373, 82626)])
    merged = gridops.merge_region_load(load, adjustment_rows())
    assert len(merged) == len(load) + len(adjustment_rows())

    frame = region_load_frame(merged)
    REGION_LOAD_SCHEMA.validate(frame, lazy=True)
    assert frame.height == len(merged)
    assert set(frame["metric"].unique()) == {"peak_mw", "energy_gwh", "large_load_adjustment_mw"}
    # The monthly and the annual rows coexist because the adjustment sits at month 0.
    dom = frame.filter((pl.col("zone") == "DOM") & (pl.col("year") == 2026))
    assert sorted(dom.filter(pl.col("metric") == "large_load_adjustment_mw")["month"].to_list()) == [0]
    assert dom.filter(pl.col("metric") == "peak_mw")["month"].min() == 1


def test_merge_survives_an_empty_side():
    load = load_rows("DOM", DOM_SHEET)
    assert gridops.merge_region_load(load, []) == load
    assert gridops.merge_region_load([], adjustment_rows()) == adjustment_rows()


def test_merge_keeps_one_row_per_key():
    load = load_rows("DOM", DOM_SHEET)
    merged = gridops.merge_region_load(load, load)
    assert len(merged) == len(load)
    assert gridops.duplicate_keys(load + load)


# --- aggregate zones --------------------------------------------------------------
def test_zone_roles_flag_the_aggregates_without_dropping_them():
    rows = (
        load_rows("DOM", [(2026, 7, 25193, 14135)])
        + load_rows("PJM_RTO", [(2026, 7, 156373, 82626)])
        + load_rows("FE_EAST", [(2026, 7, 11632, 5525)])
        + load_rows("CENTRALMA", [(2026, 7, 20000, 11506)])
        + adjustment_rows()
    )
    roles = {r["zone"]: r for r in gridops.zone_roles(rows)}

    assert roles["DOM"]["role"] == "zone"
    assert roles["DOM"]["additive_to_rto"] is True and roles["DOM"]["aggregate"] is False
    assert roles["PJM_RTO"]["role"] == "rto"
    assert roles["FE_EAST"]["role"] == "aggregate"
    assert roles["CENTRALMA"]["role"] == "aggregate"
    assert roles["DOM:NVEC"]["role"] == "subarea"
    # Every non-zone carries the warning, and no aggregate is ever additive.
    for zone in ("PJM_RTO", "FE_EAST", "CENTRALMA"):
        assert roles[zone]["aggregate"] is True and roles[zone]["additive_to_rto"] is False
    assert roles["FE_EAST"]["note"]
    # But they are all still in the table.
    assert {"DOM", "PJM_RTO", "FE_EAST", "CENTRALMA"} <= {r["zone"] for r in rows}


def test_zone_roles_call_an_unknown_sheet_unclassified_and_fail_the_check():
    rows = load_rows("NEWZONE", [(2026, 7, 10, 5)]) + load_rows("DOM", [(2026, 7, 10, 5)])
    roles = gridops.zone_roles(rows)
    assert {r["zone"]: r["role"] for r in roles}["NEWZONE"] == "unclassified"
    result = gridops.sheet_coverage_check(roles)
    assert result["status"] == "fail" and "NEWZONE" in result["detail"]


def test_additivity_check_catches_a_misclassified_zone():
    zones = [load_rows(z, [(2026, m, 100, 10)]) for z in ("DOM", "AEP") for m in range(1, 13)]
    rows = [r for group in zones for r in group]
    rows += [r for m in range(1, 13) for r in load_rows("PJM_RTO", [(2026, m, 180, 20)])]
    assert gridops.additivity_check(rows, 2026)["status"] == "pass"   # 2*12*10 == 12*20
    rows += [r for m in range(1, 13) for r in load_rows("DPL", [(2026, m, 50, 5)])]
    assert gridops.additivity_check(rows, 2026)["status"] == "fail"


# --- summer peak ------------------------------------------------------------------
def test_summer_peak_takes_the_june_to_september_maximum():
    rows = load_rows("DOM", DOM_SHEET)
    peaks = {r["year"]: r for r in gridops.summer_peak(rows)}
    assert peaks[2026]["peak_mw"] == 25193.0
    assert peaks[2026]["month"] == 7        # January's 24779 is ignored; the July/August tie goes to July
    assert peaks[2031]["peak_mw"] == 32000.0
    assert peaks[2026]["iso"] == "PJM" and peaks[2026]["vintage"] == "2026"


def test_summer_peak_ignores_the_other_metrics_and_null_values():
    rows = load_rows("DOM", [(2026, 7, 100, 999999)])
    rows.append({"snapshot_ts": SNAPSHOT_TS, "iso": "PJM", "zone": "DOM", "year": 2026, "month": 8,
                 "metric": "peak_mw", "value": None, "vintage": "2026"})
    rows += adjustment_rows()
    peaks = gridops.summer_peak(rows)
    assert [p["peak_mw"] for p in peaks] == [100.0]


def test_summer_peak_is_stable_and_one_row_per_zone_year():
    rows = load_rows("DOM", DOM_SHEET) + load_rows("AEP", DOM_SHEET)
    peaks = gridops.summer_peak(rows)
    assert [(p["zone"], p["year"]) for p in peaks] == [("AEP", 2026), ("AEP", 2031), ("DOM", 2026), ("DOM", 2031)]


# --- growth attribution -----------------------------------------------------------
def _attribution_rows(peaks: dict[int, int], adjustments: dict[int, float], zone: str = "DOM") -> list[dict]:
    rows = load_rows(zone, [(year, 7, peak, peak // 2) for year, peak in peaks.items()])
    rows += [{"snapshot_ts": SNAPSHOT_TS, "iso": "PJM", "zone": zone, "year": year, "month": 0,
              "metric": "large_load_adjustment_mw", "value": value, "vintage": "2026"}
             for year, value in adjustments.items()]
    return rows


def test_growth_attribution_splits_the_growth():
    rows = _attribution_rows({2026: 25193, 2031: 33000}, {2026: 7066.0, 2031: 15829.0})
    got = gridops.growth_attribution(rows, "DOM", 2026, 2031)
    assert got["peak_growth_mw"] == pytest.approx(7807.0)
    assert got["adjustment_growth_mw"] == pytest.approx(8763.0)
    assert got["large_load_share"] == pytest.approx(8763.0 / 7807.0)
    assert got["zone"] == "DOM" and got["role"] == "zone"


def test_growth_attribution_returns_a_share_above_one_unclipped():
    """PJM's own finding: large load is more than 100% of the growth because base load shrinks."""
    rows = _attribution_rows({2026: 20000, 2031: 21000}, {2026: 500.0, 2031: 2000.0})
    got = gridops.growth_attribution(rows, "DOM", 2026, 2031)
    assert got["peak_growth_mw"] == 1000.0
    assert got["adjustment_growth_mw"] == 1500.0
    assert got["large_load_share"] == pytest.approx(1.5)
    assert got["large_load_share"] > 1.0  # not clamped to 1.0


def test_growth_attribution_keeps_the_sign_when_the_peak_falls():
    rows = _attribution_rows({2026: 20000, 2031: 19000}, {2026: 500.0, 2031: 900.0})
    got = gridops.growth_attribution(rows, "DOM", 2026, 2031)
    assert got["peak_growth_mw"] == -1000.0
    assert got["large_load_share"] == pytest.approx(-0.4)


def test_growth_attribution_nulls_the_share_rather_than_dividing_by_zero():
    flat = gridops.growth_attribution(
        _attribution_rows({2026: 20000, 2031: 20000}, {2026: 500.0, 2031: 900.0}), "DOM", 2026, 2031)
    assert flat["peak_growth_mw"] == 0.0 and flat["large_load_share"] is None
    assert flat["adjustment_growth_mw"] == 400.0

    unadjusted = gridops.growth_attribution(_attribution_rows({2026: 20000, 2031: 21000}, {}), "DOM", 2026, 2031)
    assert unadjusted["adjustment_growth_mw"] is None and unadjusted["large_load_share"] is None
    assert unadjusted["peak_growth_mw"] == 1000.0


def test_growth_attribution_raises_on_a_zone_with_no_peak():
    rows = _attribution_rows({2026: 20000, 2031: 21000}, {})
    with pytest.raises(ValueError, match="no summer peak for AEP"):
        gridops.growth_attribution(rows, "AEP", 2026, 2031)


# --- isolation --------------------------------------------------------------------
def test_a_failing_adjustments_workbook_still_writes_the_zone_data(tmp_path, monkeypatch):
    monkeypatch.setattr(gridops, "SNAP_DIR", tmp_path)
    rows = load_rows("DOM", DOM_SHEET) + load_rows("PJM_RTO", [(2026, 7, 156373, 82626)])

    def boom() -> list[dict]:
        raise RuntimeError("404 for total-load-adjustments-breakdown.xlsx")

    result = gridops.run(2026, sources=[("load", lambda: rows), ("adjustments", boom)])

    assert result["rows"] == len(rows)
    assert result["path"] and pl.read_parquet(result["path"]).height == len(rows)
    assert result["per_source"] == {"load": len(rows)}
    assert len(result["errors"]) == 1 and "404" in result["errors"][0]

    checks = {c["name"]: c for c in result["checks"]}
    assert checks["Table written"]["status"] == "pass"
    assert checks["Both workbooks"]["status"] == "fail"
    assert checks["Adjustment zones match sheets"]["status"] == "warn"
    assert checks["ERCOT large load"]["status"] == "warn"
    assert list((tmp_path / "power" / "region_load").glob("*-zones.json"))
    assert (tmp_path / "power" / "runs.jsonl").exists()


def test_a_failing_load_workbook_still_writes_the_adjustments(tmp_path, monkeypatch):
    monkeypatch.setattr(gridops, "SNAP_DIR", tmp_path)

    def boom() -> list[dict]:
        raise RuntimeError("load workbook moved")

    adjustments = adjustment_rows()
    result = gridops.run(2026, sources=[("load", boom), ("adjustments", lambda: adjustments)])

    assert result["rows"] == len(adjustments)
    assert pl.read_parquet(result["path"])["metric"].unique().to_list() == ["large_load_adjustment_mw"]
    assert result["per_source"] == {"adjustments": len(adjustments)}


def test_both_sources_failing_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(gridops, "SNAP_DIR", tmp_path)

    def boom() -> list[dict]:
        raise RuntimeError("down")

    result = gridops.run(2026, sources=[("load", boom), ("adjustments", boom)])
    assert result["rows"] == 0 and result["path"] is None
    assert len(result["errors"]) == 2
    assert {c["name"]: c["status"] for c in result["checks"]}["Table written"] == "fail"
    assert not list((tmp_path / "power" / "region_load").glob("*.parquet"))


def test_a_source_whose_rows_fail_the_schema_costs_only_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(gridops, "SNAP_DIR", tmp_path)
    rows = load_rows("DOM", DOM_SHEET)
    bad = [dict(r, month=99) for r in adjustment_rows()]  # month must be 0..12

    result = gridops.run(2026, sources=[("load", lambda: rows), ("adjustments", lambda: bad)])
    assert result["rows"] == len(rows)
    assert result["per_source"] == {"load": len(rows)}
    assert "adjustments" in result["errors"][0]


def test_the_written_table_carries_the_zone_roles_beside_it(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(gridops, "SNAP_DIR", tmp_path)
    rows = load_rows("DOM", DOM_SHEET) + load_rows("FE_EAST", [(2026, 7, 11632, 5525)])
    result = gridops.run(2026, sources=[("load", lambda: rows)])

    sidecar = next((tmp_path / "power" / "region_load").glob("*-zones.json"))
    payload = json.loads(sidecar.read_text())
    roles = {z["zone"]: z for z in payload["zones"]}
    assert payload["iso"] == "PJM" and payload["vintage"] == "2026"
    assert roles["FE_EAST"]["aggregate"] is True
    assert roles["DOM"]["additive_to_rto"] is True
    assert roles["DOM"]["first_year"] == 2026 and roles["DOM"]["last_year"] == 2031
    assert result["zones"] == 2
