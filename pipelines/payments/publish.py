"""Assemble the payments-landscape marts and ``data/facts/payments.json``.

Writes (every table validated by :mod:`pipelines.payments.schema` before anything is written):

    data/marts/payments/waterfall_lines.json        per-$100 lines of the four books (economics)
    data/marts/payments/sensitivity.json            registered-band sensitivity rows (economics)
    data/marts/payments/ledger_timeline.json        private-company ledger, chartable rows (ledger)
    data/marts/payments/ledger_latest.json          latest current value per private company and metric
    data/marts/payments/private_intervals.json      derived bounds (Stripe net revenue band, Brex chain)
    data/marts/payments/private_take_rates_refused.json, take_rates.json, take_rates_refused.json
    data/marts/payments/share_lenses.json           the four share lenses, long (share)
    data/marts/payments/share_pool_hhi.json         concentration inside each closed pool
    data/marts/payments/products.json               company x product-line matrix (curated, chartable)
    data/marts/payments/events.json                 2025-26 deal timeline (curated, chartable)
    data/marts/payments/metric_dictionary.json      every metric: definition, kind, unit, source, comparability
    data/marts/payments/reported_unconfirmed.json   every non-chartable row, with the reason; never charted
    data/marts/payments/scoreboard.json             Q1-Q9 status; scoreboard_evidence.json the readings
    data/marts/payments/kpis.json                   the three headline KPIs
    data/marts/payments/{webtech_q2,devstats_q1,devstats_monthly,jobs_by_function,formd_filings,sec_latest}.json
                                                    small views of the CI-only sources, only when on disk
    data/facts/payments.json                        everything the page prints

Rules applied here on top of each module's own
-----------------------------------------------
* Only chartable rows (tag ``V``/``C``; never ``reported_talks``/``third_party_estimate``) reach a charted mart
  or a number in facts. Non-chartable rows go to ``reported_unconfirmed`` only, which the page shows as a table.
* Every number in facts carries its qualifier (``=``, ``>``, ``<``, ``~``); derived numbers inherit one through
  :mod:`pipelines.payments.economics`. Nothing is summed across layers, lenses, periods or waterfalls.
* ``curator_notes`` never leave the reference files (``reference.rows`` strips them; a check proves it).
* CI-only sources (npm, HTTP Archive, job boards, Form D, SEC) are read when present; absent ones are a warn
  check, unreadable ones a failed check, and the rest of the page still builds (CLAUDE.md rule 5).
* **Regression guard.** If this run would publish fewer than half the companies the existing facts file
  covers, nothing is overwritten: the old facts and marts stay, and a failed "Regression guard" check is
  recorded in the existing facts file's ``checks``.

Usage::

    uv run python -m pipelines.payments.publish [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now, write_json
from pipelines.payments import config, economics, evaluate, ledger, reference, schema, share

log = logging.getLogger("payments.publish")

MART_DIR = MARTS_DIR / "payments"
FACTS_PATH = FACTS_DIR / "payments.json"
SLUG = "payments-landscape"

#: CI-only source marts, written by their own modules in GitHub Actions.
CI_SOURCES: dict[str, tuple[str, ...]] = {
    "devstats": ("devstats_client_monthly.parquet", "devstats_server_monthly.parquet"),
    "webtech": ("webtech_adoption.parquet", "webtech_coverage.parquet"),
    "jobs": ("jobs_counts.parquet",),
    "formd": ("formd.parquet",),
    "sec": ("sec_latest.parquet",),
}
CI_LABEL = {"devstats": "npm downloads (devstats)", "webtech": "HTTP Archive coverage (webtech)",
            "jobs": "job-board counts (jobs)", "formd": "SEC Form D (formd)", "sec": "SEC financials (sec)"}
#: Freshness limits for the CI sources, in days since the latest period they report.
CI_MAX_AGE = {"devstats": 45, "webtech": 75, "jobs": 14, "formd": 120, "sec": 200}

SOURCES = [
    {"name": "Company filings and releases (SEC EDGAR, investor relations)", "url": "https://www.sec.gov/edgar/search/"},
    {"name": "Federal Reserve FEDS Notes: BNPL beyond 'pay in 4'",
     "url": "https://www.federalreserve.gov/econres/notes/feds-notes/buy-now-pay-later-beyond-pay-in-4-a-comprehensive-product-overview-20260605.html"},
    {"name": "Visa USA Interchange Reimbursement Fees",
     "url": "https://usa.visa.com/content/dam/VCOM/download/merchants/visa-usa-interchange-reimbursement-fees.pdf"},
    {"name": "FSB cross-border payments KPIs", "url": "https://www.fsb.org/"},
    {"name": "HTTP Archive Tech Report", "url": "https://httparchive.org/reports/techreport"},
    {"name": "npm download counts", "url": "https://api.npmjs.org/downloads/"},
    {"name": "Greenhouse and Ashby public job boards", "url": "https://boards-api.greenhouse.io/"},
    {"name": "SEC EDGAR XBRL APIs", "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"},
]


# --- loading ------------------------------------------------------------------------------------------


def _read_parquet(path: Path) -> tuple[pl.DataFrame | None, str | None]:
    if not path.exists():
        return None, None
    try:
        return pl.read_parquet(path), None
    except (OSError, pl.exceptions.PolarsError) as e:
        return None, f"{path.name}: {type(e).__name__}: {e}"[:300]


def load_ci(marts_dir: Path) -> tuple[dict[str, dict[str, pl.DataFrame | None]], dict[str, str]]:
    frames: dict[str, dict[str, pl.DataFrame | None]] = {}
    errors: dict[str, str] = {}
    for src, files in CI_SOURCES.items():
        frames[src] = {}
        for f in files:
            df, err = _read_parquet(marts_dir / f)
            frames[src][f.removesuffix(".parquet")] = df
            if err:
                errors[src] = err
    return frames, errors


def load_context(today: date | None = None, marts_dir: Path | None = None
                 ) -> tuple[evaluate.Context, dict[str, Any], list[dict]]:
    """Curated rows, the W2 module tables and the CI-only marts, each failing on its own."""
    today = today or utc_now().date()
    md = marts_dir or MART_DIR
    checks: list[dict] = []
    curated: dict[str, list[dict]] = {}
    for kind in reference.KINDS:
        try:
            curated[kind] = reference.rows(kind, today)
        except Exception as exc:  # noqa: BLE001 - one broken file must not lose the others (rule 5)
            log.error("%s unreadable: %s", kind, exc)
            curated[kind] = []
            checks.append(check(f"Reference {kind}", False, f"{type(exc).__name__}: {exc}"[:300]))
    eco, eco_checks = economics.build(today)
    led, led_checks = ledger.build(today)
    kpi = curated["kpi_disclosures_acceptance"] + curated["kpi_disclosures_other"]
    try:
        sh = share.build(kpi, curated["private_metrics"], curated["denominators"], marts_dir=md)
    except Exception as exc:  # noqa: BLE001 - a drifted CI mart must not lose the curated lenses (rule 5)
        log.error("share lenses failed with CI marts; rebuilding from curated rows only: %s", exc)
        checks.append(check("Share lenses with CI marts", False,
                            f"rebuilt without webtech/devstats marts: {type(exc).__name__}: {exc}"[:300]))
        sh = share.build(kpi, curated["private_metrics"], curated["denominators"], marts_dir=md, read_marts=False)
    ci, ci_errors = load_ci(md)
    ctx = evaluate.Context(
        today=today, kpi=kpi, private=curated["private_metrics"], denoms=curated["denominators"],
        events=curated["events"],
        waterfall_rows=curated["waterfall_inputs"], waterfall_lines=eco["waterfall_lines"],
        intervals=led.get("private_intervals"), client_monthly=ci["devstats"]["devstats_client_monthly"],
        adoption=ci["webtech"]["webtech_adoption"], ci_errors=ci_errors)
    built = {"curated": curated, "economics": eco, "ledger": led, "share": sh, "ci": ci}
    return ctx, built, checks + eco_checks + led_checks + sh.checks


# --- curated marts ------------------------------------------------------------------------------------


def _chartable(r: dict) -> bool:
    return r.get("chartable") is True and r.get("tag") in reference.CHARTABLE_TAGS \
        and r.get("metric_kind") not in config.NEVER_CHARTED_KINDS


def products_mart(rows: list[dict]) -> pl.DataFrame:
    recs = [{"company": r["company"], "segment": r.get("segment"), "product_line": r["product_line"],
             "product_status": r["product_status"], "launch_date": r.get("launch_date"),
             "exit_date": r.get("exit_date"), "as_of": r["as_of_date"], "last_checked": r["last_checked"],
             "stale": bool(r.get("stale")), "tag": r["tag"], "source_name": r.get("source_name") or "",
             "source_url": r["source_url"], "caveat": r.get("caveat")} for r in rows if _chartable(r)]
    df = pl.DataFrame(recs, schema=schema.PRODUCTS_DTYPES) if recs else pl.DataFrame(schema=schema.PRODUCTS_DTYPES)
    return schema.validate("products", df.sort(schema.PRODUCTS_KEY))


def events_mart(rows: list[dict]) -> pl.DataFrame:
    recs = []
    for r in rows:
        if not _chartable(r):
            continue
        v = r.get("value")
        recs.append({"event_date": r["event_date"], "company": r["company"], "segment": r.get("segment"),
                     "event_kind": r["event_kind"], "counterparty": r.get("counterparty") or "",
                     "target": evaluate.acquisition_target(r), "value": None if v is None else float(v),
                     "unit": r.get("unit") if v is not None else None,
                     "qualifier": r.get("qualifier") if v is not None else None,
                     "metric_kind": r.get("metric_kind") if v is not None else None, "tag": r["tag"],
                     "stale": bool(r.get("stale")), "source_name": r.get("source_name") or "",
                     "source_url": r["source_url"], "caveat": r.get("caveat")})
    df = pl.DataFrame(recs, schema=schema.EVENTS_DTYPES) if recs else pl.DataFrame(schema=schema.EVENTS_DTYPES)
    return schema.validate("events", df.sort(["event_date", "company", "event_kind", "counterparty"]))


def reported_mart(curated: dict[str, list[dict]], ledger_reported: pl.DataFrame | None) -> pl.DataFrame:
    """Every non-chartable curated row with its reason: the "reported, unconfirmed" table, never charted."""
    parts = [ledger_reported] if ledger_reported is not None and ledger_reported.height else []
    recs = []
    for kind in ("kpi_disclosures_acceptance", "kpi_disclosures_other", "waterfall_inputs", "denominators",
                 "products"):
        for r in curated.get(kind, []):
            if _chartable(r):
                continue
            v = r.get("value")
            recs.append({
                "source": kind, "company": r.get("company") or r.get("series") or r.get("waterfall") or "",
                "date": r["as_of_date"], "period": str(r.get("period") or r["as_of_date"]),
                "metric": r.get("metric") or r.get("line") or r.get("product_line") or "",
                "metric_kind": r.get("metric_kind"), "counterparty": "",
                "value": None if v is None else float(v), "unit": r.get("unit"), "currency": r.get("currency"),
                "qualifier": r.get("qualifier") if v is not None else None, "status": r.get("status"),
                "tag": r["tag"], "reason": ledger._reason(r), "company_confirmed": bool(r.get("company_confirmed")),
                "source_name": r.get("source_name"), "source_url": r["source_url"], "caveat": r.get("caveat")})
    if recs:
        parts.append(pl.DataFrame(recs, schema=schema.REPORTED_DTYPES))
    df = pl.concat(parts) if parts else pl.DataFrame(schema=schema.REPORTED_DTYPES)
    return schema.validate("reported_unconfirmed", df.sort(["source", "company", "date", "metric",
                                                             "counterparty"], nulls_last=True))


_COMPARABLE = {
    "filed_volume": "within one company over time; across companies only inside a disclosed pool with the same "
                    "layer, period and volume definition",
    "stated_volume": "within one company over time; never summed with filed volume or other companies' statements",
    "filed_revenue": "within one company over time; across companies only with the same revenue definition",
    "stated_run_rate": "annualised: never mixed with period or TTM totals",
    "primary_round_valuation": "within one company over time (post-money of a priced round)",
    "tender_valuation": "within one company over time; a secondary price, not a primary round",
    "acquisition_price": "deal value as announced; not a market valuation",
    "counterparty_filing": "as filed by the counterparty; comparable within that filing only",
    "statutory_accounts": "one legal entity's accounts, not group figures",
    "derived": "as its formula states; inherits its inputs' comparability",
}


def dictionary_mart(curated: dict[str, list[dict]], tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per metric the page can show: curated metrics (chartable rows only) and every derived metric."""
    recs: dict[tuple[str, str, str], dict] = {}

    def add(dataset: str, scope: str, metric: str, kind: str, unit: str, definition: str, src_name: str,
            src_url: str, mart: str, comparable: str | None = None) -> None:
        key = (dataset, scope, metric)
        if key in recs:
            recs[key]["n_rows"] += 1
            return
        recs[key] = {"dataset": dataset, "scope": scope, "metric": metric, "metric_kind": kind, "unit": unit,
                     "definition": definition, "comparable": comparable or _COMPARABLE.get(kind, ""),
                     "source_name": src_name, "source_url": src_url, "n_rows": 1, "mart": mart}

    for kind, mart in (("kpi_disclosures_acceptance", "take_rates, share_lenses"),
                       ("kpi_disclosures_other", "take_rates, share_lenses"),
                       ("private_metrics", "ledger_timeline"), ("denominators", "share_lenses")):
        for r in sorted((r for r in curated.get(kind, []) if _chartable(r)),
                        key=lambda r: (r.get("company") or r.get("series") or "", r["metric"], r["as_of_date"])):
            add(kind, r.get("company") or r.get("series") or "", r["metric"], r.get("metric_kind") or "derived",
                str(r.get("unit") or ""), r.get("definition") or "", r.get("source_name") or "", r["source_url"],
                mart)
    for r in sorted((r for r in curated.get("waterfall_inputs", []) if _chartable(r)),
                    key=lambda r: (r["waterfall"], r["line"], r["as_of_date"])):
        add("waterfall_inputs", r["waterfall"], r["line"], "derived" if r["tag"] == "C" else "filed_revenue"
            if "revenue" in r["line"] else "stated_volume" if "volume" in r["line"] or r["line"] == "gmv"
            else "derived", str(r.get("unit") or ""), r.get("definition") or "", r.get("source_name") or "",
            r["source_url"], "waterfall_lines",
            comparable="a rate or amount used as a waterfall input; see the waterfall line it feeds")
    wl = tables.get("waterfall_lines")
    if wl is not None:
        for r in wl.iter_rows(named=True):
            add("waterfall_lines", r["waterfall"], r["line"], "derived", "usd_per_100",
                f"{r['role']}: {r['formula']}" + (f" ({r['note']})" if r["note"] else ""), "computed",
                r["source_url"].split(" ")[0], "waterfall_lines",
                comparable=f"role '{r['role']}': additive only with take/cost lines of the same waterfall and period")
    tr = tables.get("take_rates")
    if tr is not None:
        for r in tr.iter_rows(named=True):
            add("take_rates", r["company"], r["ratio"], "derived", "percent",
                f"{r['numerator_metric']} / {r['denominator_metric']} x 100 ({r['scope']})", "computed",
                r["numerator_source_url"], "take_rates",
                comparable=f"only with other '{r['ratio_kind']}' ratios on the same basis ({r['basis']})")
    iv = tables.get("private_intervals")
    if iv is not None:
        for r in iv.iter_rows(named=True):
            add("private_intervals", r["company"], r["metric"], "derived", r["unit"],
                r["formula"] + (f"; assumption: {r['assumption']}" if r["assumption"] else ""), "computed",
                r["source_url"].split(" ")[0] or "computed", "private_intervals",
                comparable="an interval, not a point; never compared with a filed figure as if it were one")
    for lens, unit, definition in (
            ("disclosed_pool", share.UNIT_POOL, "company volume / sum of the volumes the same layer's companies "
             "disclose for the same calendar period and definition; share of disclosed pool, not market share"),
            ("official_denominator", share.UNIT_OFFICIAL, "company volume / an official total of the same scope, "
             "measure and period (Fed FEDS BNPL table); otherwise a refusal with the reason"),
            ("web_coverage", share.UNIT_ORIGINS, "origins where HTTP Archive detects the technology / all origins in "
             "the crawl slice; coverage, never share of payments"),
            ("developer_downloads", share.UNIT_DOWNLOADS, "npm downloads of the pinned SDK set per company / the "
             "set's total; downloads are not developers or dollars")):
        add("share_lenses", lens, unit, "derived", "fraction", definition, "computed", "computed", "share_lenses",
            comparable="only within one lens, pool and period; lenses are never added or averaged")
    add("share_pool_hhi", "pool", "hhi", "derived", "index (0-10,000)",
        "sum of squared pool shares in percent, inside one closed pool and period", "computed", "computed",
        "share_pool_hhi", comparable="only within the same pool over time")
    df = pl.DataFrame(list(recs.values()), schema=schema.DICT_DTYPES)
    return schema.validate("metric_dictionary", df.sort(schema.DICT_KEY))


# --- CI-source views ----------------------------------------------------------------------------------


def _view_webtech_q2(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    if ctx.adoption is None or ctx.adoption.is_empty():
        return None
    v = evaluate.q2_series(ctx.adoption).with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))
    return schema.validate("webtech_q2", v.select(list(schema.WEB_Q2_DTYPES))
                           .cast(schema.WEB_Q2_DTYPES).sort(schema.WEB_Q2_KEY))  # type: ignore[arg-type]


def _view_devstats_q1(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    q1 = evaluate.q1_series(ctx)
    if q1 is None or not q1.height:
        return None
    v = q1.with_columns(pl.col("month").dt.strftime("%Y-%m-%d"),
                        pl.when(pl.col("ratio").is_not_null()).then(pl.lit(">")).otherwise(None)
                        .alias("ratio_qualifier"))
    return schema.validate("devstats_q1", v.select(list(schema.Q1_VIEW_DTYPES))
                           .cast(schema.Q1_VIEW_DTYPES))  # type: ignore[arg-type]


def _view_devstats_monthly(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    mon = [d for d in (ci["devstats"].get("devstats_client_monthly"), ci["devstats"].get("devstats_server_monthly"))
           if d is not None and d.height]
    if not mon:
        return None
    v = (pl.concat(mon, how="diagonal_relaxed").filter(pl.col("registry") == "npm")
         .with_columns(pl.col("period").dt.strftime("%Y-%m-%d")))
    return schema.validate("devstats_monthly", v.select(list(schema.DOWNLOADS_DTYPES))
                           .cast(schema.DOWNLOADS_DTYPES).sort(schema.DOWNLOADS_KEY))  # type: ignore[arg-type]


def _view_jobs(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    jobs = ci["jobs"].get("jobs_counts")
    if jobs is None or not jobs.height:
        return None
    v = (jobs.group_by(["snapshot_date", "ats", "board", "function"])
         .agg(pl.col("postings").sum(), pl.col("openings").sum())
         .with_columns(pl.col("snapshot_date").dt.strftime("%Y-%m-%d")))
    return schema.validate("jobs_by_function", v.select(list(schema.JOBS_DTYPES))
                           .cast(schema.JOBS_DTYPES).sort(schema.JOBS_KEY))  # type: ignore[arg-type]


def _view_formd(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    fd = ci["formd"].get("formd")
    if fd is None or not fd.height:
        return None
    v = fd.with_columns(pl.col("filing_date").dt.strftime("%Y-%m-%d"),
                        pl.col("date_of_first_sale").dt.strftime("%Y-%m-%d"))
    return schema.validate("formd_filings", v.select(list(schema.FORMD_VIEW_DTYPES))
                           .cast(schema.FORMD_VIEW_DTYPES).sort(schema.FORMD_VIEW_KEY))  # type: ignore[arg-type]


def _view_sec(ctx: evaluate.Context, ci: dict) -> pl.DataFrame | None:
    sec = ci["sec"].get("sec_latest")
    if sec is None or not sec.height:
        return None
    return schema.validate("sec_latest", sec.select(list(schema.SEC_VIEW_DTYPES))
                           .cast(schema.SEC_VIEW_DTYPES).sort("ticker"))  # type: ignore[arg-type]


#: (view name, CI source it reads, builder). Each view is built on its own: a CI mart that reads but has drifted
#: (renamed column, wrong type) skips that view and records a failed "CI source" check (rule 5).
CI_VIEWS = (("webtech_q2", "webtech", _view_webtech_q2), ("devstats_q1", "devstats", _view_devstats_q1),
            ("devstats_monthly", "devstats", _view_devstats_monthly), ("jobs_by_function", "jobs", _view_jobs),
            ("formd_filings", "formd", _view_formd), ("sec_latest", "sec", _view_sec))


def ci_views(ctx: evaluate.Context, ci: dict[str, dict[str, pl.DataFrame | None]]) -> dict[str, pl.DataFrame]:
    out: dict[str, pl.DataFrame] = {}
    for name, src, fn in CI_VIEWS:
        try:
            v = fn(ctx, ci)
        except Exception as e:  # noqa: BLE001 - one drifted CI mart must not lose the others (rule 5)
            log.error("CI view %s (%s) failed: %s", name, src, e)
            ctx.ci_errors.setdefault(src, f"{name}: {type(e).__name__}: {e}"[:300])
            continue
        if v is not None:
            out[name] = v
    return out


# --- facts --------------------------------------------------------------------------------------------


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd) + 0.0


def _val(value: float | None, qualifier: str | None, unit: str, period: str | None, as_of: str | None,
         source_url: str | None, note: str | None = None, **extra: Any) -> dict:
    d = {"value": _r(value), "qualifier": qualifier, "unit": unit, "period": period, "as_of": as_of,
         "source_url": source_url}
    d.update({k: (_r(v) if isinstance(v, float) else v) for k, v in extra.items()})
    if note:
        d["note"] = note
    return d


def _first_url(s: str | None) -> str | None:
    return s.split(" ")[0] if s else None


def values(tables: dict[str, pl.DataFrame], curated: dict[str, list[dict]], q1_vol: dict | None) -> dict[str, dict]:
    """Every number the page templates, each with its qualifier, unit, period, as-of date and source."""
    out: dict[str, dict] = {}
    wl = tables["waterfall_lines"]
    per_wf = wl.group_by("waterfall").agg(pl.col("period").n_unique().alias("n"))
    multi = set(per_wf.filter(pl.col("n") > 1)["waterfall"].to_list())
    for r in wl.filter(pl.col("usd_per_100").is_not_null()).iter_rows(named=True):
        key = (f"waterfall.{r['waterfall']}.{r['period']}.{r['line']}" if r["waterfall"] in multi
               else f"waterfall.{r['waterfall']}.{r['line']}")
        out[key] = _val(r["usd_per_100"], r["qualifier"], "usd_per_100", r["period"], r["as_of"],
                        _first_url(r["source_url"]), role=r["role"], company=r["company"])
    tr = tables["take_rates"]
    if tr.height:
        latest = tr.sort("as_of").group_by(["company", "ratio"], maintain_order=True).last()
        for r in latest.sort(["company", "ratio"]).iter_rows(named=True):
            out[f"take_rate.{r['company']}.{r['ratio']}"] = _val(
                r["take_rate_pct"], r["qualifier"], "percent", r["period"], r["as_of"], r["numerator_source_url"],
                ratio_kind=r["ratio_kind"])
    lt = tables["ledger_latest"]
    for r in lt.iter_rows(named=True):
        out[f"ledger.{r['company']}.{r['metric']}"] = _val(r["value"], r["qualifier"], r["unit"] or "", r["period"],
                                                           r["date"], r["source_url"], metric_kind=r["metric_kind"])
    for r in tables["private_intervals"].iter_rows(named=True):
        out[f"interval.{r['company']}.{r['metric']}"] = _val(
            r["estimate"], r["qualifier"], r["unit"], r["period"], r["as_of"], _first_url(r["source_url"]),
            lower=r["lower"], upper=r["upper"], note=r["assumption"])
    sl, hhi = tables["share_lenses"], tables["share_pool_hhi"]
    feds_url = next((d["source_url"] for d in curated.get("denominators", [])
                     if d["series"] == share.FEDS_SERIES and d["metric"] == share.FEDS_TOTAL), None)
    feds_asof = next((d["as_of_date"] for d in curated.get("denominators", [])
                      if d["series"] == share.FEDS_SERIES and d["metric"] == share.FEDS_TOTAL), None)
    for r in sl.filter((pl.col("lens") == "official_denominator") & (pl.col("role") == "member")
                       & pl.col("value").is_not_null()).sort(["pool", "company"]).iter_rows(named=True):
        out[f"share.official.{r['pool']}.{r['company']}"] = _val(
            r["value"], r["qualifier"], r["unit"], r["period"], feds_asof if r["pool"] == share.FEDS_SERIES else None,
            feds_url if r["pool"] == share.FEDS_SERIES else None, note=r["basis"])
    for pool in sorted(set(hhi["pool"].to_list())):
        p = (share.latest_common_period(hhi, pool) or share.latest_common_period(hhi, pool, quarterly=False))
        if p is None:
            continue
        row = hhi.filter((pl.col("pool") == pool) & (pl.col("period") == p)).row(0, named=True)
        if row["hhi"] is not None:
            out[f"hhi.{row['lens']}.{pool}"] = _val(row["hhi"], row["qualifier"], "index (0-10,000)", p, None, None,
                                                    members=row["members"], n_members=row["n_members"])
        for m in sl.filter((pl.col("pool") == pool) & (pl.col("period") == p) & (pl.col("role") == "member")
                           & pl.col("value").is_not_null()).sort("company").iter_rows(named=True):
            if m["lens"] == "disclosed_pool":
                out[f"share.pool.{pool}.{m['company']}"] = _val(m["value"], m["qualifier"], m["unit"], p, None, None)
    # the Q1 bound is templated only while it is informative: a member entered at [0, inf) for lack of a
    # comparable calendar-year volume inflates it mechanically (see evaluate.q1_uninformative)
    if q1_vol is not None and q1_vol["upper"] is not None and not evaluate.q1_uninformative(q1_vol):
        out["q1.stripe_volume_share_upper"] = _val(q1_vol["upper"], "<", "fraction", f"FY{q1_vol['year']}", None,
                                                   None, note="upper bound of Stripe's share of the five "
                                                              "registered Q1 companies' calendar-year volume")
    return dict(sorted(out.items()))


def kpis(tables: dict[str, pl.DataFrame], vals: dict[str, dict]) -> pl.DataFrame:
    """Exactly three headline KPIs: the first three available of a fixed priority list, all from facts values."""
    cands: list[dict] = []
    v = vals.get("waterfall.bnpl_affirm.revenue_less_transaction_costs")
    if v:
        cands.append({**v, "id": "affirm_kept_per_100", "label": "What Affirm keeps from every $100 of GMV",
                      "note": "revenue less transaction costs per $100 of GMV, computed from filed FY2026 lines"})
    feds = {k: x for k, x in vals.items() if k.startswith(f"share.official.{share.FEDS_SERIES}.")}
    if feds:
        k, x = max(feds.items(), key=lambda kv: kv[1]["value"])
        company = k.rsplit(".", 1)[1]
        cands.append({**x, "id": "bnpl_leader_share",
                      "label": "Largest provider's share of 2025 US BNPL issuance among the six largest providers "
                               "(Fed estimate)",
                      "note": f"{company}; Fed FEDS Note table of six providers, 2025 issuance, not the whole market"})
    v = vals.get("ledger.Stripe.total_volume")
    if v:
        cands.append({**v, "id": "stripe_volume", "label": "Volume on Stripe, as Stripe states it",
                      "note": "a company statement of gross volume, not revenue; Stripe files no group accounts"})
    v = vals.get("waterfall.corporate_card_bill.net_interchange_after_rewards")
    if v:
        cands.append({**v, "id": "bill_net_interchange", "label": "BILL net interchange after rewards",
                      "note": "per $100 of card volume, quarter ending 2026-06-30"})
    rows = [{k: c.get(k) for k in schema.KPI_DTYPES} for c in cands if c.get("source_url") and c.get("as_of")][:3]
    return schema.validate("kpis", pl.DataFrame(rows, schema=schema.KPI_DTYPES))


#: Curated KPI files name some companies by their parent (``Block``); the page and the roster use the unit.
ROSTER_NAME = {m.company: m.label for p in share.POOLS for m in p.members}


def coverage(tables: dict[str, pl.DataFrame]) -> list[str]:
    """Companies with at least one charted number on the page, in roster names (the regression guard's count)."""
    names: set[str] = set()
    for name in ("take_rates", "ledger_timeline", "products"):
        if tables.get(name) is not None and tables[name].height:
            names |= set(tables[name]["company"].to_list())
    sl = tables["share_lenses"]
    names |= set(sl.filter(pl.col("value").is_not_null() & pl.col("role").is_in(["member", "marker", "measured"]))
                 ["company"].drop_nulls().to_list())
    return sorted({ROSTER_NAME.get(n, n) for n in names})


def counts(tables: dict[str, pl.DataFrame], results: list[evaluate.Result]) -> dict:
    prod = tables["products"]
    live = prod.filter(pl.col("product_status") == "live")
    per_co = live.group_by("company").len().sort(["len", "company"], descending=[True, False])
    ev = tables["events"]
    rep = tables["reported_unconfirmed"]
    roster = {seg: {"core": len(d["core"]), "table": len(d["table"])} for seg, d in config.UNIVERSE.items()}
    return {
        "roster_core": sum(v["core"] for v in roster.values()),
        "roster_table": sum(v["table"] for v in roster.values()),
        "roster_by_segment": roster,
        "waterfalls": tables["waterfall_lines"]["waterfall"].n_unique(),
        "waterfall_lines": tables["waterfall_lines"].height,
        "take_rates": tables["take_rates"].height,
        "take_rate_companies": tables["take_rates"]["company"].n_unique(),
        "take_rates_refused": tables["take_rates_refused"].height,
        "private_companies": tables["ledger_timeline"]["company"].n_unique(),
        "private_intervals": tables["private_intervals"].height,
        "reported_unconfirmed": rep.height,
        "reported_never_charted_kind": rep.filter(pl.col("reason") == "never_charted_kind").height,
        "products": prod.height,
        "products_live": live.height,
        "product_companies": prod["company"].n_unique(),
        "product_lines_live_by_company": {r["company"]: r["len"] for r in per_co.iter_rows(named=True)},
        "product_lines_live_max": int(per_co["len"].max()) if per_co.height else 0,
        "products_with_launch_date": prod.filter(pl.col("launch_date").is_not_null()).height,
        "events": ev.height,
        "events_by_kind": {k: n for k, n in sorted(ev.group_by("event_kind").len().iter_rows())},
        "metrics_in_dictionary": tables["metric_dictionary"].height,
        "share_rows": tables["share_lenses"].height,
        "share_refusals": tables["share_lenses"].filter(pl.col("role").is_in(["refused", "excluded"])).height,
        "scoreboard": {s: sum(r.status == s for r in results) for s in config.STATUSES},
    }


# --- checks -------------------------------------------------------------------------------------------


def _has_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def publish_checks(tables: dict[str, pl.DataFrame], ctx: evaluate.Context, ci: dict, today: date,
                   kpi_df: pl.DataFrame, vals: dict) -> list[dict]:
    out: list[dict] = []
    # chartable rows only in charted marts
    leaked = []
    for name in schema.CHARTED_MARTS:
        t = tables[name]
        bad = t.filter(~pl.col("tag").is_in(list(reference.CHARTABLE_TAGS)))
        if "metric_kind" in t.columns:
            bad = pl.concat([bad, t.filter(pl.col("metric_kind").is_in(config.NEVER_CHARTED_KINDS))])
        if bad.height:
            leaked.append(f"{name}: {bad.height}")
    out.append(check("Charted marts hold chartable rows only", not leaked,
                     "every charted row is tag V or C and never reported_talks / third_party_estimate"
                     if not leaked else "; ".join(leaked)))
    rep = tables["reported_unconfirmed"]
    tl = tables["ledger_timeline"]
    overlap = (rep.join(tl, on=["company", "metric", "period", "value"], how="inner")
               if rep.height and tl.height else rep.head(0))
    out.append(check("Reported rows kept out of charts", overlap.height == 0,
                     f"{rep.height} reported, unconfirmed row(s); {overlap.height} also in a charted table"))
    # ranges
    sl = tables["share_lenses"]
    bad_share = sl.filter(pl.col("unit").is_in(share.SHARE_UNITS) & pl.col("value").is_not_null()
                          & ((pl.col("value") < 0) | (pl.col("value") > 1))).height
    wl = tables["waterfall_lines"]
    bad_wf = wl.filter(pl.col("usd_per_100").is_not_null() & (pl.col("usd_per_100").abs() > 100)).height
    tr = tables["take_rates"]
    bad_tr = tr.filter((pl.col("take_rate_pct") <= 0) | (pl.col("take_rate_pct") >= 100)).height
    out.append(check("Value ranges", bad_share + bad_wf + bad_tr == 0,
                     f"shares in [0, 1]: {bad_share} outside; per-$100 lines within +/-100: {bad_wf} outside; "
                     f"take rates in (0, 100)%: {bad_tr} outside"))
    no_q = [k for k, v in vals.items() if v.get("value") is not None and v.get("qualifier") not in reference.QUALIFIERS]
    out.append(check("Qualifiers on every published number", not no_q,
                     f"{len(vals)} templated values, {len(no_q)} without a qualifier"
                     + (f": {', '.join(no_q[:5])}" if no_q else "")))
    out.append(check("Headline KPIs", kpi_df.height == 3,
                     f"{kpi_df.height} KPIs: {', '.join(kpi_df['id'].to_list())}"))
    # uniqueness of the marts publish assembles
    for name, key in (("products", schema.PRODUCTS_KEY), ("events", schema.EVENTS_KEY),
                      ("metric_dictionary", schema.DICT_KEY), ("reported_unconfirmed", schema.REPORTED_KEY)):
        out.append(uniqueness(tables[name], key, f"{name} rows", name=f"Uniqueness: {name}"))
    # CI-source presence and freshness
    for src, frames in ci.items():
        err = ctx.ci_errors.get(src)
        present = [k for k, v in frames.items() if v is not None and v.height]
        if err:
            out.append(check(f"CI source: {CI_LABEL[src]}", False, f"present but unreadable: {err}"))
            continue
        if not present:
            out.append(check(f"CI source: {CI_LABEL[src]}", False,
                             "absent: written only by the scheduled CI run; dependent views and questions wait",
                             warn=True))
            continue
        try:
            latest = _ci_latest(src, frames)
        except Exception as e:  # noqa: BLE001 - a drifted date column fails this check only (rule 5)
            out.append(check(f"CI source: {CI_LABEL[src]}", False,
                             f"present but unreadable: freshness: {type(e).__name__}: {e}"[:300]))
            continue
        if latest is None:
            out.append(check(f"CI source: {CI_LABEL[src]}", True, f"{', '.join(present)} read"))
            continue
        age = (today - latest).days
        out.append(check(f"CI source: {CI_LABEL[src]}", age <= CI_MAX_AGE[src],
                         f"{', '.join(present)} read; latest period {latest}, {age} days old "
                         f"(limit {CI_MAX_AGE[src]})", warn=age <= 2 * CI_MAX_AGE[src]))
    return out


def _ci_latest(src: str, frames: dict[str, pl.DataFrame | None]) -> date | None:
    col = {"devstats": ("devstats_client_monthly", "period"), "webtech": ("webtech_adoption", "date"),
           "jobs": ("jobs_counts", "snapshot_date"), "formd": ("formd", "filing_date")}.get(src)
    if col is None:
        df = frames.get("sec_latest")
        if df is None or not df.height:
            return None
        return date.fromisoformat(max(df["period_end"].to_list()))
    df = frames.get(col[0])
    if df is None or not df.height:
        return None
    return df[col[1]].max()  # type: ignore[return-value]


# --- build and write ----------------------------------------------------------------------------------


def build(today: date | None = None, marts_dir: Path | None = None) -> tuple[dict, dict[str, pl.DataFrame]]:
    today = today or utc_now().date()
    ctx, built, checks = load_context(today, marts_dir)
    curated, eco, led, sh, ci = (built[k] for k in ("curated", "economics", "ledger", "share", "ci"))
    empty = {"ledger_timeline": ledger.TIMELINE_DTYPES, "ledger_latest": ledger.TIMELINE_DTYPES,
             "ledger_reported": ledger.REPORTED_DTYPES, "private_intervals": ledger.INTERVAL_DTYPES,
             "private_take_rates_refused": ledger.REFUSED_DTYPES, "take_rates": ledger.TAKE_DTYPES,
             "take_rates_refused": ledger.REFUSED_DTYPES}
    led = {k: led.get(k, pl.DataFrame(schema=d)) for k, d in empty.items()}
    tables: dict[str, pl.DataFrame] = {
        "waterfall_lines": eco["waterfall_lines"], "sensitivity": eco["sensitivity"],
        **{k: v for k, v in led.items() if k != "ledger_reported"},
        "share_lenses": sh.long, "share_pool_hhi": sh.hhi,
        "products": products_mart(curated["products"]),
        "events": events_mart(curated["events"]),
        "reported_unconfirmed": reported_mart(curated, led["ledger_reported"]),
    }
    tables["metric_dictionary"] = dictionary_mart(curated, tables)
    results = evaluate.scoreboard(ctx)
    tables["scoreboard"], tables["scoreboard_evidence"] = evaluate.tables(results)
    tables.update(ci_views(ctx, ci))
    vals = values(tables, curated, evaluate.q1_volume(ctx))
    tables["kpis"] = kpis(tables, vals)
    for name in list(tables):
        tables[name] = schema.validate(name, tables[name])

    checks = reference.validate_all(today) + checks + publish_checks(tables, ctx, ci, today, tables["kpis"], vals)
    cov = coverage(tables)
    facts = {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "slug": SLUG,
        "registered": evaluate.REGISTERED.isoformat(),
        "kpis": tables["kpis"].to_dicts(),
        "values": vals,
        "counts": counts(tables, results),
        "coverage": {"companies": cov, "n_companies": len(cov)},
        "scoreboard": [r.row() | {"evidence": r.evidence} for r in results],
        "ci_sources": {src: any(v is not None and v.height for v in frames.values()) for src, frames in ci.items()},
        "marts": {name: f"payments/{name}.json" for name in sorted(tables)},
        "checks": checks,
        "sources": SOURCES,
    }
    leak = _has_key(facts, "curator_notes") or any(_has_key(t.to_dicts(), "curator_notes") for t in tables.values())
    facts["checks"].append(check("Curator notes never published", not leak,
                                 "no curator_notes field in facts or any mart" if not leak
                                 else "a curator_notes field reached a published file"))
    return facts, tables


def regression_guard(facts: dict, facts_path: Path) -> dict | None:
    """A failed check when this run would publish fewer than half the companies of the existing facts."""
    if not facts_path.exists():
        return None
    try:
        prev = json.loads(facts_path.read_text(encoding="utf-8"))
        prev_n = int(prev.get("coverage", {}).get("n_companies") or 0)
    except (OSError, ValueError, TypeError):
        return None
    n = facts["coverage"]["n_companies"]
    if prev_n and n < prev_n / 2:
        return check("Regression guard", False, f"this run covers {n} companies against {prev_n} in the published "
                                                "facts (fewer than half); nothing was overwritten")
    return None


def write(facts: dict, tables: dict[str, pl.DataFrame], facts_path: Path | None = None,
          mart_dir: Path | None = None) -> list[Path]:
    facts_path = facts_path or FACTS_PATH
    mart_dir = mart_dir or MART_DIR
    guard = regression_guard(facts, facts_path)
    if guard:
        log.error("%s", guard["detail"])
        prev = json.loads(facts_path.read_text(encoding="utf-8"))
        prev["checks"] = [c for c in prev.get("checks", []) if c.get("name") != "Regression guard"] + [guard]
        write_json(prev, facts_path)
        return []
    facts["checks"].append(check("Regression guard", True, f"{facts['coverage']['n_companies']} companies covered"))
    paths = [write_json(schema.validate(name, df).to_dicts(), mart_dir / f"{name}.json")
             for name, df in sorted(tables.items())]
    paths.append(write_json(facts, facts_path))
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="build and validate, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    facts, tables = build()
    if not args.dry_run:
        paths = write(facts, tables)
        log.info("wrote %d file(s)", len(paths))
        if not paths:
            return 1
    for c in facts["checks"]:
        if c["status"] != "pass":
            log.info("check %-45s %-5s %s", c["name"], c["status"], c["detail"])
    for r in facts["scoreboard"]:
        log.info("%s %-11s %s", r["id"], r["status"], r["reason"])
    return 1 if any(c["status"] == "fail" for c in facts["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
