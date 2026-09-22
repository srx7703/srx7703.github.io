"""Assemble the facts file for the soft-tissue surgical robot page.

Writes:
    data/marts/surgical/units.json        the curated unit rows, as published
    data/marts/surgical/tenders.json      the award price panel
    data/marts/surgical/clearances.json   the approval timeline
    data/facts/surgical.json              everything the page prints

Three rules govern every number that leaves this module.

**Never sum across bases.** Five companies publish a figure each calls "units" while counting five different
things. :func:`units_by_basis` groups before it aggregates and nothing here adds one basis to another. The page
gets one series per basis with the basis in the axis label.

**Never draw a company that discloses nothing.** A company at tier T4 sells a soft-tissue robot and publishes no
unit data; plotting it at zero would read as "sells none" rather than "tells nobody". Those companies appear in
the tables and in the disclosure scorecard, and the scorecard is the page's lead.

**Never divide by a denominator that does not exist.** There is no free source for total installed systems
worldwide, and none at all for China. So this module computes no market share. What it computes instead is a
share *of the disclosed pool*, labelled as such, with the count of companies that disclosed nothing printed
beside it — which is an honest statement about coverage rather than a guess about a market.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now, write_json
from pipelines.surgical import config as cfg
from pipelines.surgical import read, reference

log = logging.getLogger("surgical.publish")

MART_DIR = MARTS_DIR / "surgical"
FACTS_PATH = FACTS_DIR / "surgical.json"

SOURCES = [
    {"name": "openFDA device clearances", "url": "https://open.fda.gov/apis/device/"},
    {"name": "SEC EDGAR", "url": "https://www.sec.gov/edgar/search/"},
    {"name": "HKEXnews", "url": "https://www.hkexnews.hk/"},
    {"name": "上海证券交易所", "url": "https://www.sse.com.cn/"},
    {"name": "中国政府采购网", "url": "https://www.ccgp.gov.cn/"},
    {"name": "国家卫生健康委大型医用设备配置许可", "url": "https://www.nhc.gov.cn/"},
]


def _maker_index() -> dict[str, cfg.Maker]:
    return {m.ticker: m for m in cfg.MAKERS}


# ---------------------------------------------------------------------------------------------
# Disclosure: the page's lead
# ---------------------------------------------------------------------------------------------


def disclosure_scorecard(units: list[dict]) -> dict:
    """Who publishes what. This is the finding, not the preamble.

    Scored on the number of distinct auditable metrics a maker has published, because that is the thing that
    actually varies: one company publishes installed base, placements and procedures every quarter, and most
    publish nothing at all.
    """
    published: dict[str, set[str]] = defaultdict(set)
    latest: dict[str, str] = {}
    for r in units:
        published[r["maker"]].add(r["metric"])
        if r["period"] > latest.get(r["maker"], ""):
            latest[r["maker"]] = r["period"]

    rows = []
    for m in cfg.MAKERS:
        metrics = sorted(published.get(m.ticker, ()))
        rows.append({
            "maker": m.ticker, "name": m.name, "name_cn": m.name_cn or None,
            "tier": m.tier, "tier_label": cfg.DISCLOSURE_TIER[m.tier],
            "region": m.region, "listed": m.listed, "pure_play": m.pure_play,
            "status": m.status or None,
            "products": list(m.products),
            "metrics_published": metrics,
            "n_metrics": len(metrics),
            "latest_period": latest.get(m.ticker),
            "chartable": cfg.TIER_CHARTABLE[m.tier],
            "note": m.note or None,
        })
    rows.sort(key=lambda r: (cfg.TIER_ORDER.index(r["tier"]), -r["n_metrics"], r["name"]))

    by_tier = defaultdict(int)
    for r in rows:
        by_tier[r["tier"]] += 1
    disclosing = sum(1 for r in rows if r["n_metrics"])
    return {
        "rows": rows,
        "by_tier": {t: by_tier[t] for t in cfg.TIER_ORDER if by_tier[t]},
        "n_makers": len(rows),
        "n_disclosing": disclosing,
        "n_silent": len(rows) - disclosing,
        "tier_labels": cfg.DISCLOSURE_TIER,
    }


# ---------------------------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------------------------


def source_disagreements(units: list[dict]) -> list[dict]:
    """Where a company's own figure and a third party's estimate of that company differ.

    This is a finding, not a defect to resolve quietly. At 2024-12-31 Intuitive's own 8-K puts the da Vinci
    installed base at 9,902 systems; Frost & Sullivan, in the industry chapter of a competitor's IPO
    prospectus, puts it at 9,629 for the same date. Much of the Chinese market-sizing literature is built on
    the second number. The page shows both and the gap between them.
    """
    grouped: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in units:
        # An installed base is a level at an instant, so a source writing "2024" and one writing "2024-Q4"
        # are describing the same fact and must be compared. A procedure count is a flow, where they are not.
        instant = cfg.period_instant(r["metric"], r["period"])
        key = (r["maker"], r.get("product") or "", r["metric"], instant, r["geography"])
        kind = r.get("source_kind") or "company"
        # Within a stock collapsed to its year, the comparable reading is the year-end one. Taking whichever
        # row happened to come first compared a consultant's end-2024 figure against Intuitive's Q1.
        prev = grouped[key].get(kind)
        if prev is None or r["period"] > prev["period"]:
            grouped[key][kind] = r
    makers = _maker_index()
    out = []
    for (maker, product, metric, instant, geo), by_kind in sorted(grouped.items()):
        own, third = by_kind.get("company"), by_kind.get("third_party")
        if own is None or third is None or own["value"] == third["value"]:
            continue
        out.append({
            "maker": maker, "name": makers[maker].name if maker in makers else maker,
            "product": product, "metric": metric, "period": instant, "geography": geo,
            "company_period": own["period"], "third_party_period": third["period"],
            "company_value": own["value"], "company_source": own["source_name"],
            "third_party_value": third["value"], "third_party_source": third["source_name"],
            "gap": round(third["value"] - own["value"], 1),
            "gap_pct": round((third["value"] - own["value"]) / own["value"], 4) if own["value"] else None,
        })
    return out


def company_reported(units: list[dict]) -> list[dict]:
    """Only figures a company published about itself. Everything computed uses these.

    A third party's estimate is kept in the table and shown beside the company's own, but it never enters a
    ratio: an installed base from one source divided into a procedure count from another is a number neither
    source stands behind.
    """
    return [r for r in units if (r.get("source_kind") or "company") == "company"]


def units_by_basis(units: list[dict]) -> dict[str, list[dict]]:
    """One series per basis. Bases are never merged, so the page cannot add production to installed base."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in units:
        out[r["basis"]].append(r)
    for rows in out.values():
        rows.sort(key=lambda r: (r["maker"], r["period"]))
    return dict(out)


def latest_installed_base(units: list[dict]) -> dict:
    """The most recent installed base per maker, plus the share OF THE DISCLOSED POOL.

    Not a market share, and the difference is the point. The denominator here is the sum of what the disclosing
    companies published, not the world; the number of companies that published nothing travels with it so a
    reader can see how much of the market the denominator leaves out.
    """
    best: dict[str, dict] = {}
    for r in units:
        if r["metric"] != "installed_base":
            continue
        cur = best.get(r["maker"])
        if cur is None or r["period"] > cur["period"]:
            best[r["maker"]] = r
    total = sum(r["value"] for r in best.values())
    makers = _maker_index()
    rows = [
        {
            "maker": t, "name": makers[t].name if t in makers else t,
            "value": r["value"], "period": r["period"], "geography": r["geography"],
            "basis": r["basis"], "placement_model": r.get("placement_model"),
            "share_of_disclosed": round(r["value"] / total, 4) if total else None,
            "caveat": r["caveat"],
        }
        for t, r in sorted(best.items(), key=lambda kv: -kv[1]["value"])
    ]
    geographies = sorted({r["geography"] for r in rows})
    return {
        "rows": rows,
        "total_disclosed": total,
        "n_disclosing": len(rows),
        "n_silent": len(cfg.MAKERS) - len(rows),
        # A pool whose members report on different geographies has no single denominator, and saying so is the
        # only honest thing to do with it.
        "mixed_geography": len(geographies) > 1,
        "geographies": geographies,
    }


def utilisation(units: list[dict]) -> list[dict]:
    """Procedures per installed system per year: the bridge between an installed-base share and a procedure
    share, and the number that separates a franchise from a fleet of idle machines.

    Three conditions, and every one of them was added because dropping it produced a wrong number from real
    data on the first run:

    **Same product.** Intuitive sells two, and dividing Ion's procedures by da Vinci's installed base gave 2.6
    procedures per system per year for 2024 — a figure that would have read as catastrophic idleness and was
    purely a mismatch.

    **Annual over annual.** A quarterly procedure count over a year-end installed base understates utilisation
    by roughly four. Only a period that is a bare year counts as annual.

    **Same geography.** Procept reports the United States only; a worldwide procedure count over a US installed
    base would invent a number.

    The installed base used is the year-end reading, taken from the bare year where a company publishes one and
    otherwise from that year's Q4.
    """
    def year_of(period: str) -> str:
        return period[:4]

    annual_procedures = {
        (r["maker"], r.get("product") or "", year_of(r["period"]), r["geography"]): r
        for r in units if r["metric"] == "procedures" and len(r["period"]) == 4
    }
    # Year-end installed base: a bare year if the company gives one, else that year's Q4 reading.
    year_end: dict[tuple, dict] = {}
    for r in units:
        if r["metric"] != "installed_base":
            continue
        period = r["period"]
        is_year, is_q4 = len(period) == 4, period.endswith("-Q4")
        if not (is_year or is_q4):
            continue
        key = (r["maker"], r.get("product") or "", year_of(period), r["geography"])
        if is_year or key not in year_end:
            year_end[key] = r

    makers = _maker_index()
    out = []
    for key, proc in sorted(annual_procedures.items()):
        base = year_end.get(key)
        if base is None or not base["value"]:
            continue
        maker, product, year, geo = key
        out.append({
            "maker": maker, "name": makers[maker].name if maker in makers else maker,
            "product": product, "year": year, "geography": geo,
            "procedures": proc["value"], "installed_base": base["value"],
            "procedures_per_system": round(proc["value"] / base["value"], 1),
            "caveat": ("Annual procedures over the year-end installed base for the same product and the same "
                       "geography. The base grew during the year, so this understates the rate a system in "
                       "service all year actually achieved."),
        })
    return out


# ---------------------------------------------------------------------------------------------
# Tenders and quota
# ---------------------------------------------------------------------------------------------


def tender_panel(tenders: list[dict]) -> dict:
    """Award prices. Purchases only in the price statistics; leases and service contracts are kept and excluded.

    A leasing award prints a multi-year fee where a unit price belongs, so letting it into an average would drag
    the average toward a number that is not a price.
    """
    # A complete clinical system, bought outright, with a disclosed price. A lease fee, a service contract
    # and a teaching arm all have prices and none of them is what a robot costs.
    purchases = [t for t in tenders
                 if t.get("contract_kind") == "purchase" and t.get("unit_price_cny")
                 and t.get("complete_system") is not False]
    # Grouped by maker, not by the brand string the notice happened to use. Procurement officers write the
    # same manufacturer four ways — 图迈, 微创图迈, 精锋, 深圳精锋 — and grouping on the raw text splits one
    # vendor's price history into several thin, misleading samples.
    makers = _maker_index()
    by_maker: dict[str, list[float]] = defaultdict(list)
    for t in purchases:
        key = t.get("maker") or t.get("brand")
        if key:
            by_maker[key].append(float(t["unit_price_cny"]))

    def stats(values: list[float]) -> dict:
        s = sorted(values)
        n = len(s)
        return {
            "n": n,
            "min": s[0], "max": s[-1],
            "median": s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2,
        }

    return {
        "rows": sorted(tenders, key=lambda t: str(t.get("award_date")), reverse=True),
        "n_total": len(tenders),
        "n_purchases": len(purchases),
        "n_leases": sum(1 for t in tenders if t.get("contract_kind") == "lease"),
        "n_maintenance": sum(1 for t in tenders if t.get("contract_kind") == "maintenance"),
        "n_partial": sum(1 for t in tenders if t.get("complete_system") is False),
        "by_maker": {
            (makers[m].name if m in makers else m): {**stats(v), "brand_key": m}
            for m, v in sorted(by_maker.items(), key=lambda kv: -len(kv[1]))
        },
        "unattributed": sum(1 for t in purchases if not (t.get("maker") or t.get("brand"))),
        "overall": stats([float(t["unit_price_cny"]) for t in purchases]) if purchases else None,
        "caveat": ("Coverage of this portal is a floor and never a count: it searches titles only, omits private "
                   "and military hospitals, and misses purchases funded outside the government procurement "
                   "regime. These are individually cited prices, not a national shipment series."),
        # The warning that survived the adversarial re-read of all 65 notices, and the one most likely to be
        # ignored: the spread between the cheapest and dearest award is not a measure of pricing power.
        "configuration_caveat": ("Not one award notice discloses arm count, console count, instrument package "
                                 "or warranty term. The spread between the cheapest and the dearest system "
                                 "therefore cannot be read as vendor pricing power; a cheaper award may simply "
                                 "be a smaller configuration."),
    }


def quota_view(quota: list[dict]) -> dict:
    """China's provincial licence ceiling. A denominator, never a share: the regime records no brand or price."""
    if not quota:
        return {"status": "missing", "rows": []}
    total = sum(float(r["permitted_total"]) for r in quota)
    new = sum(float(r["newly_added"]) for r in quota)
    return {
        "status": "ok",
        "rows": sorted(quota, key=lambda r: -float(r["permitted_total"])),
        "national_permitted": total,
        "national_newly_added": new,
        "matches_published_total": abs(total - cfg.NHC_QUOTA_TOTAL) < 0.5,
        "published_total": cfg.NHC_QUOTA_TOTAL,
        "expires": cfg.NHC_QUOTA_EXPIRES,
        "successor": cfg.NHC_QUOTA_SUCCESSOR,
        "caveat": ("This licensing regime has no brand, model or price field, so it bounds the installed base and "
                   "can never state a vendor share. It covers laparoscopic systems only, and the plan it comes "
                   f"from expired {cfg.NHC_QUOTA_EXPIRES} with no successor published."),
    }


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------


def build() -> dict:
    now = utc_now()
    clearances = read.load("clearances")

    curated: dict[str, list[dict]] = {}
    curated_checks: list[dict] = []
    try:
        for kind in reference.KINDS:
            curated[kind] = reference.rows(kind)
        curated_checks = reference.validate_all()
    except Exception as exc:
        log.warning("curated layer unavailable: %s", exc)
        curated = {k: curated.get(k, []) for k in reference.KINDS}
        curated_checks = [check("Curated layer", False, str(exc))]

    units = curated.get("units", [])
    tenders = curated.get("tenders", [])
    quota = curated.get("quota", [])
    denovo = curated.get("denovo", [])

    scorecard = disclosure_scorecard(units)
    # Every computed figure uses only what a company said about itself; third-party estimates are displayed
    # beside them and never divided into them.
    own = company_reported(units)
    installed = latest_installed_base(own)
    util = utilisation(own)
    disagreements = source_disagreements(units)
    panel = tender_panel(tenders)
    quota_facts = quota_view(quota)

    clearance_rows = clearances.to_dicts() if clearances.height else []
    by_code: dict[str, int] = defaultdict(int)
    for r in clearance_rows:
        by_code[r.get("product_code") or "?"] += 1

    checks = [
        check("Clearance registry", clearances.height > 0,
              f"{clearances.height} 510(k) records across {len(by_code)} product codes"
              if clearances.height else "no clearance snapshot on disk"),
        check("Disclosed units", bool(units),
              f"{scorecard['n_disclosing']} of {scorecard['n_makers']} makers publish any unit figure; "
              f"{scorecard['n_silent']} publish none"
              if units else "the curated unit file is empty, so the page has no unit layer yet", warn=True),
        check("One denominator", not installed.get("mixed_geography"),
              "every disclosed installed base is on the same geography"
              if not installed.get("mixed_geography")
              else "disclosed installed bases mix geographies "
                   f"({', '.join(installed.get('geographies', []))}), so the pool has no single denominator and "
                   "the page shows a share of the disclosed pool rather than a market share",
              warn=True),
        check("Utilisation computable", bool(util),
              f"{len(util)} maker-years where the same company published both procedures and installed base"
              if util else "no maker published both a procedure count and an installed base for one year",
              warn=True),
        check("Tender panel", bool(tenders),
              f"{panel['n_purchases']} purchase awards priced, {panel['n_leases']} leases and "
              f"{panel['n_maintenance']} service contracts recorded and excluded from the price statistics"
              if tenders else "the tender price panel is empty", warn=True),
        check("Quota reconciles", quota_facts.get("matches_published_total", False),
              f"provincial rows sum to {quota_facts.get('national_permitted')} against the published "
              f"{cfg.NHC_QUOTA_TOTAL}" if quota else "no quota rows yet", warn=True),
        # Stated every run: this page has no market-share denominator and must not grow one by accident.
        check("No market share claimed", False,
              "no free source gives total installed systems worldwide, and none gives China at all, so every "
              "share here is a share of the disclosed pool with the silent companies counted beside it",
              warn=True),
        check("Procedure series is not continuous", False,
              f"Intuitive redefined its headline procedure metric at {cfg.PROCEDURE_DEFINITION_BREAK}: before, "
              "da Vinci alone; after, da Vinci and Ion combined. The page breaks the growth line there rather "
              "than drawing through two different populations", warn=True),
        check("Sources agree", not disagreements,
              f"{len(disagreements)} figure(s) where a consultant's estimate differs from the company's own "
              f"disclosure for the same product, period and geography; the page shows both"
              if disagreements else "no company figure is contradicted by a third-party estimate", warn=True),
        *curated_checks,
    ]

    facts = {
        "generated_at": now.isoformat(),
        "scope": {"includes": list(cfg.SCOPE_INCLUDES), "excludes": cfg.SCOPE_EXCLUDES,
                  "excluded_companies": list(cfg.EXCLUDED)},
        "disclosure": scorecard,
        "units": {"by_basis": units_by_basis(units), "installed_base": installed, "utilisation": util,
                  "definition_breaks": [{"maker": "ISRG", "metric": "procedures",
                                         "from_period": cfg.PROCEDURE_DEFINITION_BREAK,
                                         "note": cfg.PROCEDURE_BREAK_NOTE}],
                  "placements_note": cfg.PLACEMENTS_NOT_DELTA_NOTE,
                  "disagreements": disagreements, "n_third_party": len(units) - len(own),
                  "basis_definitions": cfg.UNIT_BASIS, "placement_models": cfg.PLACEMENT_MODEL},
        "tenders": panel,
        "quota": quota_facts,
        "clearances": {"rows": clearance_rows, "by_product_code": dict(by_code),
                       "product_codes": cfg.FDA_PRODUCT_CODES, "denovo": denovo},
        "checks": checks,
        "sources": SOURCES,
    }

    write_json(units, MART_DIR / "units.json")
    write_json(panel["rows"], MART_DIR / "tenders.json")
    write_json(clearance_rows, MART_DIR / "clearances.json")
    write_json(facts, FACTS_PATH)
    log.info("surgical: %d unit rows, %d tenders, %d clearances, %d makers (%d disclosing)",
             len(units), len(tenders), len(clearance_rows), scorecard["n_makers"], scorecard["n_disclosing"])
    return facts


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argparse.ArgumentParser(description="Build the surgical robot marts and facts").parse_args(argv)
    facts = build()
    for c in facts["checks"]:
        log.info("check %-28s %-5s %s", c["name"], c["status"], c["detail"])
    return 1 if any(c["status"] == "fail" for c in facts["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
