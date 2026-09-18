"""Turn the power snapshots into the marts and the facts file the page is templated from.

Writes:
    data/marts/power/additions.json        planned capacity by family, year and stage
    data/marts/power/retirements.json      retirements by family and year, and the deferred ones
    data/marts/power/sales_by_state.json   commercial-sector sales per state per year
    data/marts/power/region_growth.json    peak growth against large-load growth, per PJM zone
    data/marts/power/deals.json            the curated contract table, as published
    data/facts/power_demand.json           everything the page prints

The page has one claim at its centre — that data centers are most of the new electricity demand, and
that the plant being built to serve them is not the plant named in the press releases — so this
module is careful about three things.

**Two answers, never merged.** The physical answer (what EIA says is being built) and the contract
answer (what buyers say they signed) are computed separately and reported side by side. A reader
asking "is this nuclear-powered" gets both, and the gap between them.

**A denominator that is stated.** Every share carries its base. "Data centers are 60% of growth" is
meaningless without saying growth in what, over which period, in which region, so each share here
travels with the numerator and denominator it came from.

**Nothing about data centers inferred from load.** EIA does not label a megawatt-hour as a
data-center megawatt-hour. Commercial-sector sales in Virginia are a fingerprint, not a measurement,
and the page says so. The only data-center-specific figures are ones a company or a grid operator
published, and those live in the curated layer where each has a source beside it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now, write_json
from pipelines.power import config as cfg
from pipelines.power import gridops, read, reference

log = logging.getLogger("power.publish")

MART_DIR = MARTS_DIR / "power"
FACTS_PATH = FACTS_DIR / "power_demand.json"

SOURCES = [
    {"name": "EIA-860M preliminary monthly generator inventory",
     "url": "https://www.eia.gov/electricity/data/eia860m/"},
    {"name": "EIA-861M monthly retail sales and revenue",
     "url": "https://www.eia.gov/electricity/data/eia861m/"},
    {"name": "EIA Short-Term Energy Outlook", "url": "https://www.eia.gov/outlooks/steo/"},
    {"name": "PJM annual load forecast",
     "url": "https://www.pjm.com/planning/resource-adequacy-planning/load-forecast-dev-process"},
]

#: The window for planned additions. Three years out, because beyond that most of the inventory is
#: units with no regulatory approval and the mix says more about intentions than about construction.
ADDITION_YEARS = (2026, 2027, 2028, 2029)

#: Zones the page reports individually: the RTO total first, then the four largest data-center zones.
HEADLINE_ZONES = ("PJM_RTO", "DOM", "AEP", "COMED", "PL", "PS", "BGE", "PECO", "APS", "ATSI", "DAY", "DEOK")

#: States whose commercial-sector series the page plots. Virginia is the example everyone cites; the
#: others are where the build is newer, which is the point of showing them together.
FINGERPRINT_STATES = ("VA", "TX", "GA", "OH", "LA", "AZ", "IL")

STEO_HEADLINE = ("ELTCP_US", "ELCCP_US", "ELRCP_US", "ELICP_US")


def _rows(df: pl.DataFrame) -> list[dict]:
    return df.to_dicts() if df.height else []


# ---------------------------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------------------------


def national_sales(sales: pl.DataFrame) -> dict:
    """Trailing-twelve-month retail sales by sector, and the change in each over the prior year.

    The growth split is the page's denominator. EIA publishes no data-center line, but it does
    publish the commercial sector, and essentially all new data-center load is billed there. The
    commercial share of growth is therefore an upper bound on the data-center share, not a measure of
    it, and the page prints it that way.
    """
    if not sales.height:
        return {"status": "missing"}
    us = sales.filter((pl.col("state") == cfg.NATIONAL) & pl.col("sales_mwh").is_not_null())
    if not us.height:
        return {"status": "missing"}
    periods = sorted(us["period"].unique().to_list())
    if len(periods) < 24:
        return {"status": "not_enough_history", "periods": len(periods)}

    def ttm(end: int) -> dict[str, float]:
        window = set(periods[end - 11 : end + 1])
        f = us.filter(pl.col("period").is_in(window))
        return {r["sector"]: r["sales_mwh"] for r in f.group_by("sector").agg(pl.col("sales_mwh").sum()).to_dicts()}

    latest, prior = ttm(len(periods) - 1), ttm(len(periods) - 13)
    sectors = sorted(set(latest) | set(prior))
    growth = {s: latest.get(s, 0.0) - prior.get(s, 0.0) for s in sectors}
    total_growth = growth.get("total")
    return {
        "status": "ok",
        "window_end": periods[-1],
        "window_start": periods[len(periods) - 12],
        "prior_window_end": periods[-13],
        "ttm_twh": {s: round(latest.get(s, 0.0) / 1e6, 1) for s in sectors},
        "ttm_growth_twh": {s: round(growth[s] / 1e6, 1) for s in sectors},
        "ttm_growth_pct": {s: (round(growth[s] / prior[s], 4) if prior.get(s) else None) for s in sectors},
        "share_of_growth": {
            s: (round(growth[s] / total_growth, 4) if total_growth else None)
            for s in sectors if s != "total"
        },
        "total_growth_twh": round(growth.get("total", 0.0) / 1e6, 1),
    }


def commercial_by_year(sales: pl.DataFrame, state: str = cfg.NATIONAL) -> list[dict]:
    """Commercial-sector sales per calendar year for one state, or nationally.

    The national series is the control the state chart needs. Every state plotted there was chosen
    because it has data centers, so comparing Virginia with the median of the others compares one
    affected state with several others and understates the divergence. The country as a whole is the
    honest baseline.
    """
    if not sales.height:
        return []
    f = (
        sales.filter(
            (pl.col("state") == state) & (pl.col("sector") == "commercial") & pl.col("sales_mwh").is_not_null()
        )
        .with_columns(pl.col("period").str.slice(0, 4).cast(pl.Int64).alias("year"))
        .group_by("year")
        .agg((pl.col("sales_mwh").sum() / 1e6).round(2).alias("twh"), pl.len().alias("months"))
        .sort("year")
    )
    return [{**r, "complete_year": r["months"] == 12} for r in f.to_dicts()]


def sales_by_state(sales: pl.DataFrame, states: tuple[str, ...] = FINGERPRINT_STATES) -> list[dict]:
    """Commercial-sector sales per state per calendar year: the clearest public trace of the build.

    A part-year is marked and never annualised. Scaling six months by two would put a fabricated
    number on the most-read chart on the page.
    """
    if not sales.height:
        return []
    f = (
        sales.filter(
            (pl.col("sector") == "commercial")
            & pl.col("state").is_in(list(states))
            & pl.col("sales_mwh").is_not_null()
        )
        .with_columns(pl.col("period").str.slice(0, 4).cast(pl.Int64).alias("year"))
        .group_by("state", "year")
        .agg((pl.col("sales_mwh").sum() / 1e6).round(2).alias("twh"), pl.len().alias("months"))
        .sort("state", "year")
    )
    return [{**r, "complete_year": r["months"] == 12} for r in f.to_dicts()]


def last_complete_year(sales: pl.DataFrame) -> int | None:
    """The most recent calendar year for which the retail sales file has all twelve months."""
    if not sales.height:
        return None
    us = sales.filter((pl.col("state") == cfg.NATIONAL) & (pl.col("sector") == "total"))
    if not us.height:
        return None
    counts = (
        us.with_columns(pl.col("period").str.slice(0, 4).cast(pl.Int64).alias("year"))
        .group_by("year").agg(pl.len().alias("months"))
        .filter(pl.col("months") == 12).sort("year")
    )
    return int(counts["year"][-1]) if counts.height else None


def steo_view(steo: pl.DataFrame, base_year: int | None = None) -> dict:
    """EIA's own forecast: annual totals per sector, and the growth it implies.

    The growth is measured from `base_year` — the last calendar year that is entirely history — to
    the end of the forecast, not from the first year the table happens to start at. The STEO
    workbook carries four years of history before the forecast begins, and quoting the whole span as
    "the forecast" would attribute several years of past growth to a projection.
    """
    if not steo.height:
        return {"status": "missing", "annual": [], "growth": {}}
    annual = (
        steo.filter((pl.col("frequency") == "annual") & pl.col("series_id").is_in(list(STEO_HEADLINE)))
        .select("series_id", "label", "period", "value", "unit")
        .sort("series_id", "period")
    )
    rows = [{**r, "value": round(r["value"], 1)} for r in annual.to_dicts()]
    by_series: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        by_series[r["series_id"]][r["period"]] = r["value"]
    growth = {}
    for sid, series in by_series.items():
        years = sorted(series)
        if len(years) < 2:
            continue
        base = str(base_year) if base_year is not None and str(base_year) in series else years[0]
        last = years[-1]
        if base == last:
            continue
        growth[sid] = {
            "from_year": base, "to_year": last,
            "from": series[base], "to": series[last],
            "change_twh": round(series[last] - series[base], 1),
        }
    total = growth.get("ELTCP_US", {}).get("change_twh")
    commercial = growth.get("ELCCP_US", {}).get("change_twh")
    return {
        "status": "ok",
        "vintage": steo["vintage"].drop_nulls().max(),
        "history_through": str(base_year) if base_year is not None else None,
        "annual": rows,
        "growth": growth,
        "commercial_share_of_growth": (round(commercial / total, 4) if total and commercial else None),
    }


# ---------------------------------------------------------------------------------------------
# Supply, physical
# ---------------------------------------------------------------------------------------------


def additions(generators: pl.DataFrame, years: tuple[int, ...] = ADDITION_YEARS) -> list[dict]:
    if not generators.height:
        return []
    f = (
        generators.filter(
            (pl.col("sheet") == "planned")
            & pl.col("operation_year").is_in(list(years))
            & pl.col("nameplate_mw").is_not_null()
        )
        .group_by("operation_year", "family", "stage")
        .agg(pl.col("nameplate_mw").sum().round(1).alias("mw"), pl.len().alias("units"))
        .sort("operation_year", "family", "stage")
    )
    return [
        {"year": r["operation_year"], "family": r["family"], "stage": r["stage"],
         "family_label": cfg.FAMILY_LABEL.get(r["family"], r["family"]),
         "mw": r["mw"], "units": r["units"]}
        for r in f.to_dicts()
    ]


def addition_totals(rows: list[dict]) -> dict:
    """The headline split of planned additions, and the honest qualifier next to it.

    Most planned capacity has no regulatory approval yet. The mix of what is *under construction* is
    a far better guide to the next three years than the mix of what is *planned*, so both are here
    and the page shows them together rather than choosing.
    """
    by_family: dict[str, float] = defaultdict(float)
    firm: dict[str, float] = defaultdict(float)
    by_stage: dict[str, float] = defaultdict(float)
    for r in rows:
        by_family[r["family"]] += r["mw"]
        by_stage[r["stage"]] += r["mw"]
        if r["stage"] in ("under_construction", "commissioning"):
            firm[r["family"]] += r["mw"]
    total = sum(by_family.values())
    firm_total = sum(firm.values())
    gas = sum(v for k, v in by_family.items() if k.startswith("gas_"))
    gas_firm = sum(v for k, v in firm.items() if k.startswith("gas_"))
    return {
        "years": [min(r["year"] for r in rows), max(r["year"] for r in rows)] if rows else None,
        "total_mw": round(total, 1),
        "units": sum(r["units"] for r in rows),
        "by_family_mw": {k: round(v, 1) for k, v in sorted(by_family.items(), key=lambda kv: -kv[1])},
        "share_by_family": {k: round(v / total, 4) for k, v in by_family.items()} if total else {},
        "by_stage_mw": {k: round(by_stage[k], 1) for k in cfg.STAGE_ORDER if k in by_stage},
        "gas_mw": round(gas, 1),
        "gas_share": round(gas / total, 4) if total else None,
        "under_construction_mw": round(firm_total, 1),
        "under_construction_share": round(firm_total / total, 4) if total else None,
        "under_construction_by_family_mw": {k: round(v, 1) for k, v in sorted(firm.items(), key=lambda kv: -kv[1])},
        "gas_share_under_construction": round(gas_firm / firm_total, 4) if firm_total else None,
    }


def retirement_view(retirements: pl.DataFrame, deferrals: pl.DataFrame) -> dict:
    """Retirements scheduled, and the ones that stopped being scheduled."""
    out: dict = {"planned_by_year": [], "deferred": {}, "deferred_rows": []}
    if retirements.height:
        f = (
            retirements.filter(
                (pl.col("kind") == "planned")
                & pl.col("retirement_year").is_not_null()
                & pl.col("nameplate_mw").is_not_null()
            )
            .group_by("retirement_year", "family")
            .agg(pl.col("nameplate_mw").sum().round(1).alias("mw"), pl.len().alias("units"))
            .sort("retirement_year", "family")
        )
        out["planned_by_year"] = [
            {"year": r["retirement_year"], "family": r["family"],
             "family_label": cfg.FAMILY_LABEL.get(r["family"], r["family"]),
             "mw": r["mw"], "units": r["units"]}
            for r in f.to_dicts()
        ]
    if deferrals.height:
        by_family = (
            deferrals.group_by("family")
            .agg(pl.col("nameplate_mw").sum().round(1).alias("mw"), pl.len().alias("units"))
            .sort("mw", descending=True)
        )
        withdrawn = deferrals.filter(pl.col("year_after").is_null())
        out["deferred"] = {
            "units": deferrals.height,
            "mw": round(float(deferrals["nameplate_mw"].sum() or 0.0), 1),
            "withdrawn_units": withdrawn.height,
            "withdrawn_mw": round(float(withdrawn["nameplate_mw"].sum() or 0.0), 1),
            "median_years": (round(float(deferrals["deferred_years"].median()), 2)
                             if deferrals["deferred_years"].drop_nulls().len() else None),
            "by_family": [
                {**r, "family_label": cfg.FAMILY_LABEL.get(r["family"], r["family"])}
                for r in by_family.to_dicts()
            ],
            "from_vintage": deferrals["from_vintage"][0],
            "to_vintage": deferrals["to_vintage"][0],
        }
        out["deferred_rows"] = (
            deferrals.sort("nameplate_mw", descending=True)
            .select("plant_name", "state", "technology", "family", "nameplate_mw",
                    "year_before", "year_after", "deferred_years")
            .head(40).to_dicts()
        )
    return out


# ---------------------------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------------------------


def region_growth(region_load: pl.DataFrame, zones: tuple[str, ...] = HEADLINE_ZONES) -> dict:
    """Peak growth against large-load growth, per zone, over the forecast's own window.

    A share above 1 is PJM's own finding — large load grows by more than the system peak because
    everything else shrinks — and :func:`gridops.growth_attribution` returns it unclipped.
    """
    if not region_load.height:
        return {"status": "missing", "zones": [], "window": None}
    rows = _rows(region_load)
    peaks = gridops.summer_peak(rows)
    if not peaks:
        return {"status": "missing", "zones": [], "window": None}
    adj_years = sorted({r["year"] for r in rows if r["metric"] == "large_load_adjustment_mw"})
    peak_years = sorted({p["year"] for p in peaks})
    common = sorted(set(adj_years) & set(peak_years)) or peak_years
    start, end = common[0], min(common[0] + 5, common[-1])

    out = []
    for zone in zones:
        try:
            a = gridops.growth_attribution(rows, zone, start, end)
        except ValueError:
            continue
        out.append(a)
    return {
        "status": "ok",
        "window": [start, end],
        "vintage": region_load["vintage"].drop_nulls().max(),
        "zones": out,
        "roles": gridops.zone_roles(rows),
    }


# ---------------------------------------------------------------------------------------------
# Supply, contract
# ---------------------------------------------------------------------------------------------


def contract_view() -> dict:
    """The curated layer. Missing or broken, the page loses this half and says which."""
    try:
        deals = reference.rows("deals")
        pipelines = reference.rows("utility_pipelines")
        economics = reference.rows("unit_economics")
        forecasts = reference.rows("demand_forecasts")
        checks = reference.validate_all()
    except Exception as exc:
        log.warning("curated layer unavailable: %s", exc)
        return {"status": "error", "error": str(exc), "deals": [], "utility_pipelines": [],
                "unit_economics": [], "demand_forecasts": [], "checks": [
                    check("Curated layer", False, str(exc))]}

    by_path: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    undisclosed: dict[str, int] = defaultdict(int)
    for d in deals:
        path = d.get("path") or "?"
        if d.get("mw") is None:
            undisclosed[path] += 1
        else:
            by_path[path][d.get("stage") or "announced"] += float(d["mw"])
    return {
        "status": "ok",
        "deals": deals,
        "utility_pipelines": pipelines,
        "unit_economics": economics,
        "demand_forecasts": forecasts,
        "mw_by_path_stage": {p: {k: round(v, 1) for k, v in s.items()} for p, s in by_path.items()},
        "deals_without_capacity": dict(undisclosed),
        "confirmed_deals": sum(1 for d in deals if d.get("company_confirmed")),
        "stale_deals": sum(1 for d in deals if d.get("stale")),
        "checks": checks,
    }


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------


def build() -> dict:
    now = utc_now()
    gens = read.load("generators")
    rets = read.load("retirements")
    defs = read.load("deferrals")
    sales = read.load("sales")
    steo = read.load("steo")
    region = read.load("region_load")

    add_rows = additions(gens)
    totals = addition_totals(add_rows)
    national = national_sales(sales)
    state_rows = sales_by_state(sales)
    steo_facts = steo_view(steo, last_complete_year(sales))
    ret_facts = retirement_view(rets, defs)
    regions = region_growth(region)
    contract = contract_view()

    vintage = gens["vintage"].drop_nulls().max() if gens.height else None
    rto = next((z for z in regions["zones"] if z["zone"] == "PJM_RTO"), None)

    checks = [
        check("Generator inventory", gens.height > 0,
              f"{gens.height:,} units in the {vintage or 'missing'} EIA-860M edition"),
        check("Retail sales", national.get("status") == "ok",
              f"{sales.height:,} state-month-sector rows through {national.get('window_end', 'nothing')}"),
        check("EIA forecast", steo_facts.get("status") == "ok",
              f"STEO {steo_facts.get('vintage')}, {len(steo_facts.get('annual', []))} annual points"
              if steo_facts.get("status") == "ok" else "no STEO snapshot"),
        check("Regional attribution", bool(regions["zones"]),
              f"{len(regions['zones'])} PJM zones with both a summer peak and a large-load adjustment, "
              f"{regions['window'][0]}-{regions['window'][1]}" if regions["zones"]
              else "no zone has both a peak and an adjustment, so no growth can be attributed"),
        check("Retirement deferrals", bool(defs.height),
              f"{ret_facts['deferred'].get('units', 0)} units, "
              f"{ret_facts['deferred'].get('mw', 0):,.0f} MW moved later between "
              f"{ret_facts['deferred'].get('from_vintage')} and {ret_facts['deferred'].get('to_vintage')}"
              if defs.height else "only one 860M edition was readable, so nothing was compared",
              warn=True),
        check("Contracted deals", bool(contract["deals"]),
              f"{len(contract['deals'])} deals across {len(contract.get('mw_by_path_stage', {}))} paths, "
              f"{contract.get('confirmed_deals', 0)} confirmed by a party to them"
              if contract["deals"] else
              "the curated contract table is empty, so the page shows the physical build only",
              warn=True),
        *contract.get("checks", []),
    ]

    facts = {
        "generated_at": now.isoformat(),
        "inventory_vintage": vintage,
        "headline": {
            # The three numbers the page opens with, each with its base attached.
            "commercial_share_of_us_growth": national.get("share_of_growth", {}).get("commercial"),
            "us_growth_twh": national.get("total_growth_twh"),
            "gas_share_of_planned_additions": totals.get("gas_share"),
            "planned_additions_gw": round(totals.get("total_mw", 0) / 1000, 1),
            "pjm_large_load_share_of_growth": (rto or {}).get("large_load_share"),
            "pjm_window": regions.get("window"),
            "deferred_gw": round(ret_facts["deferred"].get("mw", 0) / 1000, 1),
        },
        "demand": {
            "national": national,
            "by_state": state_rows,
            "national_commercial": commercial_by_year(sales),
            "states": list(FINGERPRINT_STATES),
            "steo": steo_facts,
            "forecasts": contract.get("demand_forecasts", []),
        },
        "supply_physical": {
            "totals": totals,
            "additions": add_rows,
            "retirements": ret_facts,
        },
        "supply_contract": {
            "deals": contract.get("deals", []),
            "mw_by_path_stage": contract.get("mw_by_path_stage", {}),
            "deals_without_capacity": contract.get("deals_without_capacity", {}),
            "utility_pipelines": contract.get("utility_pipelines", []),
            "status": contract.get("status"),
        },
        "path_economics": contract.get("unit_economics", []),
        "regions": regions,
        "paths": cfg.PATHS,
        "family_order": list(cfg.FAMILY_ORDER),
        "family_label": cfg.FAMILY_LABEL,
        "stage_order": list(cfg.STAGE_ORDER),
        "region_definitions": [
            {"key": r.key, "label": r.label, "iso": r.iso, "states": list(r.states), "note": r.note}
            for r in cfg.REGIONS
        ],
        "checks": checks,
        "sources": SOURCES,
    }

    write_json(add_rows, MART_DIR / "additions.json")
    write_json(ret_facts, MART_DIR / "retirements.json")
    write_json(state_rows, MART_DIR / "sales_by_state.json")
    write_json(regions, MART_DIR / "region_growth.json")
    write_json(contract.get("deals", []), MART_DIR / "deals.json")
    write_json(facts, FACTS_PATH)
    log.info("power: %d addition rows, %d deals, %d zones, %d state-years",
             len(add_rows), len(contract.get("deals", [])), len(regions["zones"]), len(state_rows))
    return facts


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argparse.ArgumentParser(description="Build the data-center power marts and facts").parse_args(argv)
    facts = build()
    for c in facts["checks"]:
        log.info("check %-28s %-5s %s", c["name"], c["status"], c["detail"])
    return 1 if any(c["status"] == "fail" for c in facts["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
