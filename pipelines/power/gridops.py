"""PJM's load forecast: the one place a grid operator publishes its own data-center number.

Two workbooks, and the second is why this module exists.

**Workbook 1**, the annual load report data, gives ``peak_mw`` and ``energy_gwh`` per zone per month,
twenty years out. **Workbook 2**, the large-load adjustment breakdown, gives the megawatts PJM added
to its model *because of large loads* — overwhelmingly data centers. No other US ISO publishes an
equivalent series. It is the only figure in this project that is both a grid operator's own number
and specifically about this load; everything else is a company's claim or a sector proxy.

That adjustment is worth more than its size suggests, because PJM's underlying non-large load is
forecast to shrink slightly. The adjustment can therefore exceed total forecast growth: a zone can be
growing entirely on data centers while its other customers use less. A share above 100% is the real
result of that arithmetic and :func:`growth_attribution` returns it unclipped.

Three traps, each of which silently produces a wrong number rather than an error:

**Aggregates mixed in with zones.** The workbook has a tab per zone *and* tabs for PJM RTO, PJM WEST,
PJM MA, FE EAST and the MA sub-regions. Summing every tab double-counts most of the system. Rather
than dropping them — the RTO total is the headline — every zone is classified and only those marked
``additive_to_rto`` are ever summed. An unrecognised tab is ``unclassified`` and fails a check, so a
zone PJM adds later cannot quietly land in or out of a total.

**Two spellings of one zone.** Workbook 1 names tabs ``PJM_RTO`` and ``DAY``; workbook 2 writes
``PJM RTO`` and ``DAYTON``. Joining without :func:`normalise_adjustment_zone` drops the RTO row from
every comparison, which is the headline row.

**Sub-areas under a zone.** Workbook 2 breaks a zone into areas on rows whose zone cell is blank.
Treating those as zones double-counts; ignoring them loses detail. They are kept, keyed ``DOM:NVEC``,
and never match a lookup for their parent.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import date

import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, append_jsonl, run_stamp, utc_now, write_json, write_parquet
from pipelines.power import config as cfg
from pipelines.power.download import NotPublished, get_workbook
from pipelines.power.schema import REGION_LOAD_KEY, REGION_LOAD_SCHEMA, region_load_frame

log = logging.getLogger("power.gridops")

ISO = "PJM"
ADJ_SHEET = "Total LargeLoad Breakdown"
ZONE_COLUMNS = ("ZONE_NAME", "YEAR", "MONTH", "PEAK_MW", "ENERGY_GWH")

#: Month 0 marks an annual row, so the monthly peaks and the annual adjustment share one table.
ANNUAL = 0

#: Summer peak months. PJM is a summer-peaking system; a winter maximum is a different statistic.
SUMMER = (6, 7, 8, 9)

#: The RTO-wide total. Never summed with anything.
RTO_ZONE = "PJM_RTO"

#: Tabs that are sums of other tabs. Kept in the table, excluded from every total.
AGGREGATE_ZONES = {
    "PJM_RTO": "the whole RTO",
    "PJM_WEST": "the western half of the RTO",
    "PJM_MA": "the Mid-Atlantic region",
    "FE_EAST": "FirstEnergy's eastern zones (JCPL, METED, PENELEC) combined",
    "PLGRP": "the PPL group, which already contains PL",
    "CENTRALMA": "a Mid-Atlantic sub-region, inside PJM_MA",
    "WESTERNMA": "a Mid-Atlantic sub-region, inside PJM_MA",
    "EASTERNMA": "a Mid-Atlantic sub-region, inside PJM_MA",
    "SOUTHERNMA": "a Mid-Atlantic sub-region, inside PJM_MA",
}

#: The transmission zones that do add up to the RTO.
ADDITIVE_ZONES = {
    "AE", "AEP", "APS", "ATSI", "BGE", "COMED", "DAY", "DEOK", "DLCO", "DOM", "DPL", "EKPC",
    "JCPL_FE_EAST", "METED_FE_EAST", "OVEC", "PECO", "PEPCO", "PL", "PN_FE_EAST", "PS", "RECO", "UGI",
}

#: How far apart the additive zones may sit from the RTO total before the check complains. PJM's own
#: zonal figures do not reconcile to the penny, but a misclassified zone moves the sum by percent.
ADDITIVITY_TOLERANCE = 0.02

OUT_TABLE = "region_load"


class SheetLayoutChanged(ValueError):
    """A workbook no longer has the columns or header this parser was written against."""


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _i(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _clean(v) -> str:
    """A cell as a label: NBSP-aware, whitespace-stripped, empty for the blanks PJM leaves around."""
    if v is None:
        return ""
    s = str(v).replace("\xa0", " ").strip()
    return "" if s.lower() in ("none", "nan", "") else s


# ---------------------------------------------------------------------------------------------
# Workbook 1: per-zone monthly peak and energy
# ---------------------------------------------------------------------------------------------


def load_sheet_rows(sheet_name: str, frame: pl.DataFrame, *, vintage: str, snapshot_ts: str) -> list[dict]:
    """One zone tab into long rows, two per month.

    The zone is keyed on the **tab name**, not the ``ZONE_NAME`` cell: PJM truncates that cell to the
    column width, so the SOUTHERNMA tab says "SouthernM" and keying on it would invent a zone.

    A row whose year or month will not parse is a footer or a note and is dropped whole. A row that
    parses but has a null value is a real hole in the forecast and is kept as a null, because the
    difference between "PJM did not forecast this" and "we failed to read it" matters downstream.
    """
    missing = [c for c in ZONE_COLUMNS if c not in frame.columns]
    if missing:
        raise SheetLayoutChanged(
            f"PJM sheet {sheet_name}: missing {', '.join(missing)}; found {list(frame.columns)}"
        )
    rows: list[dict] = []
    for r in frame.iter_rows(named=True):
        year, month = _i(r.get("YEAR")), _i(r.get("MONTH"))
        if year is None or month is None or not (1 <= month <= 12):
            continue
        for metric, col in (("peak_mw", "PEAK_MW"), ("energy_gwh", "ENERGY_GWH")):
            rows.append({
                "snapshot_ts": snapshot_ts, "iso": ISO, "zone": sheet_name,
                "year": year, "month": month, "metric": metric,
                "value": _f(r.get(col)), "vintage": vintage,
            })
    return rows


# ---------------------------------------------------------------------------------------------
# Workbook 2: the large-load adjustment
# ---------------------------------------------------------------------------------------------


def normalise_adjustment_zone(name: str) -> str:
    """Workbook 2's spelling of a zone, in workbook 1's spelling."""
    return cfg.PJM_ZONE_ALIASES.get(name, name)


def adjustment_rows(grid: Sequence[Sequence], *, vintage: str, snapshot_ts: str) -> list[dict]:
    """The large-load adjustment per zone per year, sub-areas kept and keyed apart.

    The sheet is laid out for a human: a title, then a header row carrying ``ZONENAME``/``AREANAME``
    and the forecast years, then zone totals whose first cell is populated, each optionally followed
    by area rows whose first cell is blank. A row with a populated first cell is a zone total even
    when it also carries an area name — a single-area zone is written on one line, and reading it as
    both a zone and a sub-area would double it.
    """
    header_idx = year_cols = None
    for i, row in enumerate(grid):
        labels = {_clean(c).upper() for c in row}
        if "ZONENAME" in labels:
            cols = {j: _i(c) for j, c in enumerate(row) if _i(c) and 2000 <= _i(c) <= 2100}
            if cols:
                header_idx, year_cols = i, cols
                break
    if year_cols is None:
        raise SheetLayoutChanged(
            f"{ADJ_SHEET}: no header row carrying ZONENAME and forecast years was found in "
            f"{len(grid)} rows; the large-load adjustment cannot be read"
        )

    rows: list[dict] = []
    parent = ""
    for row in grid[header_idx + 1:]:
        zone_cell = _clean(row[0]) if len(row) else ""
        if zone_cell and zone_cell.lower() != "total":
            zone = normalise_adjustment_zone(zone_cell)
            parent = zone
        elif zone_cell:
            continue  # a grand total row, which would double the whole sheet
        else:
            area = _clean(row[2]) if len(row) > 2 else ""
            owner = normalise_adjustment_zone(_clean(row[1])) if len(row) > 1 else ""
            if not area or not (owner or parent):
                continue  # a separator row
            zone = f"{owner or parent}:{area}"
        for j, year in year_cols.items():
            v = _f(row[j]) if j < len(row) else None
            if v is None:
                continue
            rows.append({
                "snapshot_ts": snapshot_ts, "iso": ISO, "zone": zone, "year": year,
                "month": ANNUAL, "metric": "large_load_adjustment_mw", "value": v, "vintage": vintage,
            })
    return rows


# ---------------------------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------------------------


def _key(row: dict) -> tuple:
    return tuple(row.get(k) for k in REGION_LOAD_KEY)


def duplicate_keys(rows: Iterable[dict]) -> list[tuple]:
    seen: set[tuple] = set()
    dupes: list[tuple] = []
    for r in rows:
        k = _key(r)
        (dupes.append(k) if k in seen else seen.add(k))
    return dupes


def merge_region_load(*sources: Iterable[dict]) -> list[dict]:
    """Every source in one list, first writer of a key wins, order preserved."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for source in sources:
        for r in source:
            k = _key(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------------------------


def zone_role(zone: str) -> str:
    if ":" in zone:
        return "subarea"
    if zone == RTO_ZONE:
        return "rto"
    if zone in AGGREGATE_ZONES:
        return "aggregate"
    if zone in ADDITIVE_ZONES:
        return "zone"
    return "unclassified"


def zone_roles(rows: Iterable[dict]) -> list[dict]:
    """One classified entry per zone in the table, with the years it covers.

    Nothing is dropped. The classification is advice to whoever sums the table, and it travels with
    the snapshot as a sidecar so a later reader cannot lose it.
    """
    span: dict[str, list[int]] = {}
    for r in rows:
        z, y = r.get("zone"), r.get("year")
        if z is None or y is None:
            continue
        lo_hi = span.setdefault(z, [y, y])
        lo_hi[0], lo_hi[1] = min(lo_hi[0], y), max(lo_hi[1], y)

    out = []
    for zone in sorted(span):
        role = zone_role(zone)
        aggregate = role in ("rto", "aggregate")
        note = ""
        if role == "rto":
            note = "the RTO total; already contains every zone below it"
        elif role == "aggregate":
            note = f"an aggregate of other rows: {AGGREGATE_ZONES[zone]}"
        elif role == "subarea":
            note = f"a sub-area of {zone.split(':')[0]}, already inside that zone's total"
        elif role == "unclassified":
            note = ("PJM publishes this zone and this module has no rule for it; classify it in "
                    "ADDITIVE_ZONES or AGGREGATE_ZONES before trusting any total")
        out.append({
            "zone": zone, "role": role, "aggregate": aggregate,
            "additive_to_rto": role == "zone", "note": note,
            "first_year": span[zone][0], "last_year": span[zone][1],
        })
    return out


def sheet_coverage_check(roles: Sequence[dict]) -> dict:
    unknown = [r["zone"] for r in roles if r["role"] == "unclassified"]
    additive = sum(1 for r in roles if r["additive_to_rto"])
    return check(
        "Zone classification",
        not unknown,
        (f"{len(unknown)} unclassified zone(s): {', '.join(unknown)}" if unknown
         else f"{len(roles)} zones classified, {additive} additive to the RTO"),
    )


def additivity_check(rows: Iterable[dict], year: int) -> dict:
    """Do the zones marked additive actually add up to the RTO total?

    This is the guard on the classification above. Energy is used rather than peak because zonal
    peaks occur in different hours and genuinely do not sum to the system peak, while annual energy
    does. A zone in the wrong bucket moves this by percent and is caught; rounding does not.
    """
    rows = list(rows)
    zone_sum = 0.0
    rto = 0.0
    for r in rows:
        if r.get("metric") != "energy_gwh" or r.get("year") != year or r.get("value") is None:
            continue
        if r["zone"] == RTO_ZONE:
            rto += float(r["value"])
        elif zone_role(r["zone"]) == "zone":
            zone_sum += float(r["value"])
    if not rto:
        return check("Zones sum to the RTO", False,
                     f"no {RTO_ZONE} energy for {year}, so the sum cannot be checked", warn=True)
    gap = (zone_sum - rto) / rto
    return check(
        "Zones sum to the RTO",
        abs(gap) <= ADDITIVITY_TOLERANCE,
        f"{year}: additive zones {zone_sum:,.0f} GWh against the RTO's {rto:,.0f} GWh, "
        f"{gap:+.2%} (limit {ADDITIVITY_TOLERANCE:.0%})",
    )


# ---------------------------------------------------------------------------------------------
# Derived readings
# ---------------------------------------------------------------------------------------------


def summer_peak(rows: Iterable[dict]) -> list[dict]:
    """The June-to-September maximum per zone per year, earliest month winning a tie."""
    best: dict[tuple[str, int], dict] = {}
    for r in rows:
        if r.get("metric") != "peak_mw" or r.get("value") is None:
            continue
        if r.get("month") not in SUMMER:
            continue
        key = (r["zone"], r["year"])
        cur = best.get(key)
        v = float(r["value"])
        if cur is None or v > cur["peak_mw"] or (v == cur["peak_mw"] and r["month"] < cur["month"]):
            best[key] = {
                "iso": r.get("iso", ISO), "zone": r["zone"], "year": r["year"],
                "month": r["month"], "peak_mw": v, "vintage": r.get("vintage"),
            }
    return [best[k] for k in sorted(best)]


def growth_attribution(rows: Iterable[dict], zone: str, start: int, end: int) -> dict:
    """How much of a zone's forecast peak growth is the large-load adjustment.

    The share is returned exactly as the division comes out. Above 1 means large load grew by more
    than the peak did, which is PJM's own published finding and the most interesting number here —
    clamping it to 1 would erase the result. Below 0 means the peak is falling while large load
    grows. A flat peak returns a null share rather than dividing by zero.
    """
    rows = list(rows)
    peaks = {(p["zone"], p["year"]): p for p in summer_peak(rows)}
    p0, p1 = peaks.get((zone, start)), peaks.get((zone, end))
    if p0 is None or p1 is None:
        have = sorted(y for (z, y) in peaks if z == zone)
        raise ValueError(
            f"no summer peak for {zone} in {start if p0 is None else end}; "
            f"years available: {have or 'none'}"
        )
    adj = {
        r["year"]: float(r["value"])
        for r in rows
        if r.get("zone") == zone and r.get("metric") == "large_load_adjustment_mw"
        and r.get("value") is not None
    }
    a0, a1 = adj.get(start), adj.get(end)
    peak_growth = p1["peak_mw"] - p0["peak_mw"]
    adj_growth = None if (a0 is None or a1 is None) else a1 - a0
    share = None if (adj_growth is None or peak_growth == 0) else adj_growth / peak_growth
    return {
        "zone": zone, "role": zone_role(zone), "start_year": start, "end_year": end,
        "peak_mw_start": p0["peak_mw"], "peak_mw_end": p1["peak_mw"],
        "peak_growth_mw": peak_growth,
        "adjustment_mw_start": a0, "adjustment_mw_end": a1,
        "adjustment_growth_mw": adj_growth,
        "large_load_share": share,
    }


# ---------------------------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------------------------


def fetch_load(year: int, *, lookback: int = 2) -> tuple[str, bytes]:
    """The newest annual load workbook at or before `year`.

    PJM publishes in January, so for most of a year the current edition is named after it; early in
    the year it is still last year's. Walking back avoids a January outage.
    """
    tried = []
    for y in range(year, year - lookback - 1, -1):
        url = cfg.PJM_LOAD_WORKBOOK.format(year=y)
        tried.append(url)
        try:
            body = get_workbook(url)
        except NotPublished as exc:
            log.info("PJM %s not published: %s", y, exc)
            continue
        log.info("PJM load forecast %s from %s (%.1f MB)", y, url, len(body) / 1e6)
        return str(y), body
    raise NotPublished("no PJM load workbook found; tried:\n  " + "\n  ".join(tried))


def load_rows_from_workbook(body: bytes, vintage: str, snapshot_ts: str) -> list[dict]:
    import fastexcel

    rows: list[dict] = []
    for sheet in fastexcel.read_excel(body).sheet_names:
        frame = pl.read_excel(io.BytesIO(body), sheet_name=sheet, read_options={"header_row": 0})
        rows += load_sheet_rows(sheet, frame, vintage=vintage, snapshot_ts=snapshot_ts)
    log.info("PJM %s: %d rows across %d tabs", vintage, len(rows),
             len({r["zone"] for r in rows}))
    return rows


def adjustment_rows_from_workbook(body: bytes, vintage: str, snapshot_ts: str) -> list[dict]:
    frame = pl.read_excel(io.BytesIO(body), sheet_name=ADJ_SHEET, read_options={"header_row": 0})
    grid = [list(frame.columns)] + [list(r) for r in frame.iter_rows()]
    rows = adjustment_rows(grid, vintage=vintage, snapshot_ts=snapshot_ts)
    log.info("PJM large-load adjustment: %d rows across %d zones", rows and len(rows) or 0,
             len({r["zone"] for r in rows}))
    return rows


def default_sources(year: int, snapshot_ts: str) -> list[tuple[str, Callable[[], list[dict]]]]:
    state: dict[str, str] = {}

    def load() -> list[dict]:
        vintage, body = fetch_load(year)
        state["vintage"] = vintage
        return load_rows_from_workbook(body, vintage, snapshot_ts)

    def adjustments() -> list[dict]:
        body = get_workbook(cfg.PJM_LOAD_ADJUSTMENTS)
        return adjustment_rows_from_workbook(body, state.get("vintage", str(year)), snapshot_ts)

    return [("load", load), ("adjustments", adjustments)]


# ---------------------------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------------------------


def run(year: int, *, sources: Sequence[tuple[str, Callable[[], list[dict]]]] | None = None,
        snapshot_ts: str | None = None) -> dict:
    """Fetch, validate and write the region-load table.

    Each source is validated **on its own** before anything is merged. The two workbooks fail
    independently in practice — PJM moves the adjustment file more often than the load file — and one
    of them being unreachable should cost the page that half, not both. A source whose rows will not
    pass the schema is discarded with its name in the error, for the same reason.
    """
    ts = utc_now()
    snapshot_ts = snapshot_ts or ts.isoformat()
    run_date, _ = run_stamp(ts)
    sources = sources if sources is not None else default_sources(year, snapshot_ts)

    per_source: dict[str, int] = {}
    errors: list[str] = []
    collected: list[list[dict]] = []
    for name, fetch in sources:
        try:
            rows = list(fetch())
            if rows:
                REGION_LOAD_SCHEMA.validate(region_load_frame(rows))
            per_source[name] = len(rows)
            collected.append(rows)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            log.warning("source %s failed: %s", name, exc)

    merged = merge_region_load(*collected)
    roles = zone_roles(merged)
    vintage = next((r.get("vintage") for r in merged if r.get("vintage")), str(year))

    out_dir = SNAP_DIR / "power" / OUT_TABLE
    path = None
    if merged:
        frame = region_load_frame(merged)
        REGION_LOAD_SCHEMA.validate(frame)
        path = write_parquet(frame, out_dir / f"{run_date}.parquet")
        write_json({"iso": ISO, "vintage": vintage, "snapshot_ts": snapshot_ts, "zones": roles},
                   out_dir / f"{run_date}-zones.json")

    has_adjustment = any(r["metric"] == "large_load_adjustment_mw" for r in merged)
    adj_zones = {r["zone"] for r in merged if r["metric"] == "large_load_adjustment_mw" and ":" not in r["zone"]}
    load_zones = {r["zone"] for r in merged if r["metric"] != "large_load_adjustment_mw"}
    unmatched = sorted(adj_zones - load_zones) if (adj_zones and load_zones) else []

    checks = [
        check("Table written", bool(merged),
              f"{len(merged)} rows across {len(roles)} zones, vintage {vintage}" if merged
              else "no source produced any rows, so nothing was written"),
        check("Both workbooks", len(per_source) == len(sources),
              f"{len(per_source)} of {len(sources)} sources returned rows"
              + (f"; failed: {'; '.join(errors)}" if errors else "")),
        check("Adjustment zones match sheets", has_adjustment and not unmatched,
              (f"{len(unmatched)} adjustment zone(s) with no load tab: {', '.join(unmatched)}"
               if unmatched else
               f"{len(adj_zones)} adjustment zones all match a load tab" if has_adjustment else
               "the large-load adjustment workbook produced nothing, so the data-center share of "
               "PJM growth cannot be computed this run"),
              warn=True),
        sheet_coverage_check(roles),
        # Stated on every run rather than discovered later: PJM is the only ISO with this series.
        check("ERCOT large load", False,
              "ERCOT publishes no equivalent large-load adjustment, so the Texas share of growth on "
              "this page is inferred from retail sales rather than read from the operator",
              warn=True),
    ]
    if merged:
        years = sorted({r["year"] for r in merged if r["metric"] == "energy_gwh"})
        if years:
            checks.append(additivity_check(merged, years[0]))

    result = {
        "rows": len(merged), "path": path, "per_source": per_source, "errors": errors,
        "checks": checks, "zones": len(roles), "vintage": vintage, "snapshot_ts": snapshot_ts,
    }
    append_jsonl({k: (str(v) if k == "path" else v) for k, v in result.items() if k != "checks"},
                 SNAP_DIR / "power" / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Fetch the PJM load forecast and large-load adjustment")
    ap.add_argument("--year", type=int, default=date.today().year)
    a = ap.parse_args(argv)
    result = run(a.year)
    for c in result["checks"]:
        log.info("check %-28s %-5s %s", c["name"], c["status"], c["detail"])
    return 1 if any(c["status"] == "fail" for c in result["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
