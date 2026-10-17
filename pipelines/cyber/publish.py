"""Assemble the cybersecurity page's marts and ``data/facts/cyber.json``.

Writes (every table validated by :mod:`pipelines.cyber.schema` before anything is written):

    data/marts/cyber/listings.json              one row per pool listing, every ring (metrics.listing_rows); the
                                                enterprise value is also given in dollars at the repository's FX table
    data/marts/cyber/layer_summary.json         per layer: listings by ring and market group, private companies (and
                                                how many are overseas or Chinese), median
    data/marts/cyber/regression.json            an object: latest point, baseline, weekly series, residuals, AI premium
    data/marts/cyber/targets.json               ring A outside China with a target: upside, dispersion, thin
    data/marts/cyber/recommendations.json       Yahoo's five buckets and East Money's six-month counts, never merged
    data/marts/cyber/china_panel.json           A-share PS_TTM, its percentile since 2018, revenue growth, flags
    data/marts/cyber/{private_intervals,ledger_timeline,ledger_latest,events,reported_unconfirmed}.json   ring C
    data/marts/cyber/{etf_crosscheck,excluded,metric_dictionary}.json
    data/marts/cyber/{scoreboard,scoreboard_evidence}.json   Q1..Q10 and the readings each rests on
    data/marts/cyber/kpis.json                  the three headline KPIs, each tag V or C
    data/facts/cyber.json                       everything the page prints

Rules applied here on top of each module's own
-----------------------------------------------
* **A number is a record.** Every figure in facts that the page prints is ``{value, qualifier, unit, period, as_of,
  source_url}``; a statistic computed exactly from the stored tables carries ``=``. Counts are plain integers under a
  section that has its own ``as_of``. ``schema.validate_facts`` refuses facts in which a number lacks its record.
* **Only chartable rows reach a chartable mart.** Tag ``V`` or ``C`` and never a secondary mark, reported talks or a
  third-party estimate (``ledger.charted_problems`` is asserted before the write); the rest is ``reported_unconfirmed``.
* **No Yahoo payload.** The listing table holds the parsed fields the capture table holds and the measures derived
  from them; a check traces each Yahoo-sourced column back to its snapshot column and names any column it cannot.
* **No ``curator_notes`` anywhere** (``reference.rows`` strips them; a check proves none reached facts or a mart).
* **An absent input is an empty section, not a crash.** No consensus run: listings carry their classification only.
  No weekly Monday snapshot: the series is empty. No East Money history: ``china_panel`` is empty. No ring C rows:
  the private tables are empty. Each is a ``warn`` check naming what is missing. A curated file that does not validate,
  an unreadable file or a ledger that breaks its own rules is a ``fail`` check, and the section it feeds is empty.
* **Exchange rates are read, not fetched.** The enterprise value in dollars uses the newest rate per currency of the
  repository's own FX snapshot (``data/snapshots/valuation/fx``). A table that is missing or unreadable is a ``warn``
  check and the dollar column is null for every listing; a listing whose currency has no rate gets null. The date of
  the rates is in ``facts.coverage`` and ``facts.sources``.
* **Regression guard.** If this run would publish fewer priced listings, or fewer listings, than half of what the
  existing facts file covers, nothing is overwritten: the old facts and marts stay, and a failed "Regression guard"
  check is recorded in the existing facts file's ``checks``.
* **Exit code.** 1 if any check failed (the file is still written), so the weekly workflow does not commit a page
  built from a broken run; ``warn`` never fails the run.

Usage::

    uv run python -m pipelines.cyber.publish [--dry-run] [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

import numpy as np
import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now
from pipelines.cyber import capture, etf, evaluate, frozen, ledger, metrics, readings, reference, regression, schema
from pipelines.cyber import config as C
from pipelines.cyber import stats as S
from pipelines.cyber import valuation_history_em as V
from pipelines.cyber.pool import POOL, market_of
from pipelines.valuation import fx as valuation_fx
from pipelines.valuation import metrics as valuation_metrics
from pipelines.valuation import read as valuation_read
from pipelines.valuation.config import EXCLUDED

log = logging.getLogger("cyber.publish")

MART_DIR = MARTS_DIR / "cyber"
FACTS_PATH = FACTS_DIR / "cyber.json"
SLUG = "cybersecurity-landscape"

YAHOO_URL = "https://finance.yahoo.com/"
FX_URL = valuation_fx.BASE + "/"
EASTMONEY_URL = capture.EM_BASE + "/"
EDGAR_URL = "https://www.sec.gov/edgar/search/"
CIBR_URL = "https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=CIBR"
#: Where the registered section sits; the source of a figure that is only the pool's own count.
REGISTRATION_URL = "https://github.com/srx7703/srx7703.github.io/blob/main/docs/EVALUATION_PLAN.md"

# --- the thresholds of the checks and of the presentation (the registered ones live in config / readings) -----------
MIN_SPREAD_N = 4  # fewer companies than this and a quartile is not printed: with one, p25 and p75 are the value
PRICE_STALE_DAYS = 5  # a quote older than this against the run that stored it: a suspended or illiquid listing
RUN_WARN_DAYS = 9  # the capture is weekly: a newest usable run older than this has missed its Monday
RUN_FAIL_DAYS = 18  # two missed Mondays
WEEK_BACK = (5, 10)  # the run compared with the newest one for the week-on-week checks: this many days earlier
TARGET_MOVE = 0.30  # a target mean that moved more than this in a week is named
#: A target more than ten times the price, or less than a tenth of it, is named as extreme (single-analyst microcaps).
TARGET_EXTREME = (0.1, 10.0)
#: A target near 100 times, or near a hundredth of, the price is a pence-against-pounds mix-up until proven otherwise.
TARGET_UNIT_SLIP = (50.0, 200.0)
SHORT_LIST = 8  # tickers named in a check's detail before "and n more"
#: The newest exchange rate is FRED's, published with a lag of days; a table whose newest rate is older than this
#: against the consensus run is named.
FX_STALE_DAYS = 14

#: The pool fields, the measures derived from the snapshot, and the columns copied from it. A listing column that is
#: none of these has no source, and the check that traces the table to the capture table names it.
POOL_FIELDS = frozenset(
    {
        "ticker",
        "name",
        "name_cn",
        "country",
        "ring",
        "layer",
        "purity",
        "status",
        "status_date",
        "platform",
        "placement_basis",
        "also_in",
        "market_group",
        "note",
    }
)
DERIVED_FIELDS = frozenset(
    {
        "priced",
        "enterprise_value_usd",
        "ntm_revenue",
        "ev_ntm",
        "ev_ntm_reason",
        "growth",
        "fcf_margin",
        "rule_of_40",
        "upside",
        "dispersion",
        "thin",
        "in_regression_population",
    }
)
#: listing column -> capture column, for the values copied as parsed.
COPIED_FROM_SNAPSHOT = {
    c: c
    for c in (
        "currency",
        "financial_currency",
        "price",
        "price_ts",
        "market_cap",
        "enterprise_value",
        "target_mean",
        "target_high",
        "target_low",
        "n_opinions",
        "recommendation_mean",
        "rev_fy0_n",
        "rev_fy1_n",
        "em_rev0_n",
        "fy0_end",
    )
}
#: struct column -> {field: capture column}
STRUCT_FROM_SNAPSHOT = {
    "yahoo_ratings": {k: f"rating_{k}" for k in schema.YAHOO_RATING_BUCKETS},
    "em_ratings_6m": {k: f"em_rating_{k}_6m" for k in schema.EM_RATING_FIELDS},
}
assert set(COPIED_FROM_SNAPSHOT.values()) | {v for m in STRUCT_FROM_SNAPSHOT.values() for v in m.values()} <= set(
    capture.COLUMNS
), "every listing column copied from the snapshot is a column the capture table holds"
assert set(schema.LISTINGS_DTYPES) == POOL_FIELDS | DERIVED_FIELDS | set(COPIED_FROM_SNAPSHOT) | set(
    STRUCT_FROM_SNAPSHOT
), "every listing column has a declared source"

SOURCES = [
    {
        "name": "Yahoo Finance: price, enterprise value, target prices, ratings, revenue consensus (parsed fields)",
        "url": YAHOO_URL,
    },
    {"name": "East Money profit forecast and six-month rating counts (A-share listings)", "url": EASTMONEY_URL},
    {"name": "East Money valuation analysis: daily PS_TTM and PB since 2018", "url": V.SOURCE_URL},
    {"name": "East Money financial reports: A-share revenue by report date", "url": V.REPORT_URL},
    {"name": "First Trust NASDAQ Cybersecurity ETF (CIBR) holdings", "url": CIBR_URL},
    {
        "name": "Company filings and announcements cited by the ring C ledger (SEC EDGAR and equivalents)",
        "url": EDGAR_URL,
    },
    {
        "name": "Section I of the evaluation plan: the registered definitions, populations and items",
        "url": REGISTRATION_URL,
    },
]


# --- inputs ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Inputs:
    """Everything ``build`` reads. ``build`` is a pure function of this, so a test supplies it without files."""

    today: date
    generated_at: str
    runs: pl.DataFrame | None = None  # every consensus run, the baseline included
    private_directory: list[dict] = field(default_factory=list)
    private_metrics: list[dict] = field(default_factory=list)
    private_events: list[dict] = field(default_factory=list)
    curation_checks: list[dict] = field(default_factory=list)  # reference.validate_all
    china: V.Loaded | None = None
    cibr: etf.CibrHoldings | None = None
    fx_rates: dict[str, float] = field(default_factory=dict)  # currency -> units of it per US dollar (USD is 1.0)
    fx_dates: dict[str, str] = field(default_factory=dict)  # currency -> the day of the rate in fx_rates
    errors: dict[str, str] = field(default_factory=dict)  # input name -> why it could not be read


class Built(NamedTuple):
    """``facts`` is the JSON-ready facts file; ``marts`` maps a mart name to a table (a record list) or, for
    ``regression``, an object."""

    facts: dict[str, Any]
    marts: dict[str, pl.DataFrame | dict[str, Any]]


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def load_consensus(directory: Path | None = None) -> tuple[pl.DataFrame | None, dict[str, str]]:
    """Every run on disk. One unreadable file costs that run, not the others (``CLAUDE.md``, rule 5)."""
    directory = metrics.CONSENSUS_DIR if directory is None else directory
    frames, errors = [], {}
    for path in sorted(directory.glob("*.parquet")):
        try:
            frames.append(pl.read_parquet(path))
        except (OSError, pl.exceptions.PolarsError) as exc:
            errors[f"consensus/{path.name}"] = _err(exc)
    if not frames:
        return None, errors
    try:
        return pl.concat(frames, how="vertical_relaxed"), errors
    except pl.exceptions.PolarsError as exc:
        return None, {**errors, "consensus": _err(exc)}


def load_fx() -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
    """``(rates, dates, errors)``: the newest rate per currency of the repository's FX snapshot (units of the currency
    per dollar, ``valuation.fx.latest_rates``) and the day of each. A table that is missing or unreadable gives no rates
    and says why under ``fx``; the page then has no dollar enterprise values, and nothing else is lost."""
    try:
        table = valuation_read.load("fx")
        if table.is_empty() or not {"date", "currency", "per_usd"} <= set(table.columns):
            return {}, {}, {"fx": "no exchange-rate snapshot is stored under data/snapshots/valuation/fx"}
        rates = {c: v for c, v in valuation_fx.latest_rates(table).items() if math.isfinite(v) and v > 0}
        newest = table.filter(pl.col("per_usd").is_not_null()).group_by("currency").agg(pl.col("date").max())
        dates = {str(c): str(d) for c, d in zip(newest["currency"], newest["date"], strict=True) if c in rates}
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        return {}, {}, {"fx": _err(exc)}
    return rates, dates, {}


def load_inputs(
    today: date | None = None,
    *,
    consensus_dir: Path | None = None,
    history_dir: Path | None = None,
    cibr_path: Path | None = None,
) -> Inputs:
    """Read the committed files. The only place that reads the clock or the disk; each source fails on its own."""
    now = utc_now()
    today = today or now.date()
    errors: dict[str, str] = {}
    runs, run_errors = load_consensus(consensus_dir)
    errors.update(run_errors)
    curated: dict[str, list[dict]] = {}
    for kind in reference.KINDS:
        try:
            curated[kind] = reference.rows(kind, today)
        except Exception as exc:  # noqa: BLE001 - one broken curated file must not lose the others (rule 5)
            log.error("%s unreadable: %s", kind, exc)
            curated[kind] = []
            errors[kind] = _err(exc)
    try:
        curation_checks = reference.validate_all(today)
    except Exception as exc:  # noqa: BLE001
        curation_checks = [check("Curated reference files", False, _err(exc))]
    try:
        china = V.load_latest(history_dir)
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        china, errors["china_history"] = None, _err(exc)
    try:
        cibr = etf.load_cibr_records(cibr_path or frozen.CIBR_FILE)
    except (OSError, ValueError) as exc:
        cibr, errors["cibr"] = None, _err(exc)
    fx_rates, fx_dates, fx_errors = load_fx()
    errors.update(fx_errors)
    return Inputs(
        today=today,
        generated_at=now.isoformat(timespec="seconds"),
        runs=runs,
        private_directory=curated["private_directory"],
        private_metrics=curated["private_metrics"],
        private_events=curated["events"],
        curation_checks=curation_checks,
        china=china,
        cibr=cibr,
        fx_rates=fx_rates,
        fx_dates=fx_dates,
        errors=errors,
    )


# --- small helpers --------------------------------------------------------------------------------------------------
def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd) + 0.0


def val(
    value: float | None,
    unit: str,
    period: str | None,
    as_of: str | None,
    source_url: str | None,
    *,
    qualifier: str = "=",
    **extra: Any,
) -> dict[str, Any]:
    """A published number. A statistic computed exactly from the stored tables carries ``=``. No value, no qualifier."""
    d = {
        "value": _r(value),
        "qualifier": qualifier if value is not None else None,
        "unit": unit,
        "period": period,
        "as_of": as_of,
        "source_url": source_url,
    }
    d.update({k: (_r(v) if isinstance(v, float) else v) for k, v in extra.items()})
    return d


def spread(values: Iterable[float]) -> dict[str, Any]:
    """Count, median and quartiles (linear interpolation between order statistics). Quartiles only from
    ``MIN_SPREAD_N`` companies; the median from one."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"n": 0, "median": None, "p25": None, "p75": None}
    p25, med, p75 = (float(q) for q in np.percentile(xs, [25, 50, 75]))
    enough = len(xs) >= MIN_SPREAD_N
    return {"n": len(xs), "median": med, "p25": p25 if enough else None, "p75": p75 if enough else None}


def _names(items: Iterable[str], limit: int = SHORT_LIST) -> str:
    items = list(items)
    head = ", ".join(items[:limit])
    return head + (f" and {len(items) - limit} more" if len(items) > limit else "")


def has_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


def _ring(rows: Sequence[Mapping[str, Any]], *rings: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r["ring"] in rings]


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days


# --- the listing table and the tables built from it -----------------------------------------------------------------
def listings_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """``metrics.listing_rows`` as the validated ``listings`` mart. An unpriced listing has no measure; its flag for the
    regression population is false, and ``priced`` means the run holds a row for it."""
    df = pl.DataFrame(rows, schema=schema.LISTINGS_DTYPES).with_columns(
        pl.col("in_regression_population").fill_null(False)
    )
    return schema.validate("listings", df)


def layer_summary_frame(listings: pl.DataFrame, directory: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    """One row per layer. The pool does not classify a listing below its layer, so sub-sectors (L2) appear only for the
    private companies, whose directory rows carry one. The private companies of a layer are also counted by the market
    of the directory row (overseas or China), and the two add up to ``n_private``. The median is that of the Q1 and Q2
    population (a rule, re-counted every week), in the layer."""
    rows = listings.to_dicts()
    private = Counter((d["layer"], d.get("l2") or "unspecified") for d in directory)
    by_market = Counter((d["layer"], d.get("market")) for d in directory)
    out = []
    for layer, spec in C.LAYERS.items():
        mine = [r for r in rows if r["layer"] == layer]
        pop = [r for r in mine if r["in_regression_population"]]
        priced_pop = [r["ev_ntm"] for r in pop if r["ev_ntm"] is not None]

        def count(ring: str, group: str | None = None, mine: list = mine) -> int:
            return sum(1 for r in mine if r["ring"] == ring and (group is None or r["market_group"] == group))

        order = {k: i for i, k in enumerate(spec["l2"])}
        by_l2 = sorted(
            ((l2, n) for (ly, l2), n in private.items() if ly == layer), key=lambda x: (order.get(x[0], 99), x[0])
        )
        out.append(
            {
                "layer": layer,
                "code": spec["code"],
                "name": spec["name"],
                "n_l2": len(spec["l2"]),
                "n_a": count("A"),
                "n_a_us": count("A", "us"),
                "n_a_china": count("A", "china"),
                "n_a_other_overseas": count("A", "other_overseas"),
                "n_b": count("B"),
                "n_b_us": count("B", "us"),
                "n_b_china": count("B", "china"),
                "n_b_other_overseas": count("B", "other_overseas"),
                "n_f": count("F"),
                "n_private": sum(n for _, n in by_l2),
                "n_private_overseas": by_market[(layer, "overseas")],
                "n_private_china": by_market[(layer, "china")],
                "private_by_l2": [{"l2": k, "n": n} for k, n in by_l2],
                "n_population": len(pop),
                "n_population_with_multiple": len(priced_pop),
                "population_median_ev_ntm": float(np.median(priced_pop)) if priced_pop else None,
            }
        )
    return schema.validate("layer_summary", pl.DataFrame(out, schema=schema.LAYER_SUMMARY_DTYPES).sort("code"))


def targets_frame(rows: Sequence[Mapping[str, Any]], as_of: str | None) -> pl.DataFrame:
    """Ring A outside China with a target mean and a price. A-shares are out by design: East Money's report-list target
    is empty for cybersecurity and Yahoo's rests on very few analysts, so the two are not comparable (section I)."""
    q4 = set(C.FROZEN_Q4_Q5)
    recs = [
        {
            "ticker": r["ticker"],
            "name": r["name"],
            "layer": r["layer"],
            "purity": r["purity"],
            "market_group": r["market_group"],
            "status": r["status"],
            "currency": r["currency"],
            "price": r["price"],
            "price_ts": r["price_ts"],
            "target_mean": r["target_mean"],
            "target_high": r["target_high"],
            "target_low": r["target_low"],
            "n_opinions": r["n_opinions"],
            "upside": r["upside"],
            "dispersion": r["dispersion"],
            "thin": r["thin"],
            "pence_quote": r["currency"] == "GBp",
            "in_q4_q5_list": r["ticker"] in q4,
            "as_of": as_of,
        }
        for r in _ring(rows, "A")
        if r["market_group"] != "china" and r.get("target_mean") and r.get("price") and r.get("upside") is not None
    ]
    df = pl.DataFrame(recs, schema=schema.TARGETS_DTYPES).sort("ticker")
    return schema.validate("targets", df)


def _rating_mean(x: float | None) -> float | None:
    """Yahoo's mean rating runs from 1 (strong buy) to 5 (strong sell) and is served as 0 when no analyst has rated the
    company; a zero is "no rating", not a rating, so it is left out of the table (the listing table keeps what was
    served)."""
    return x if x is not None and x >= 1.0 else None


def recommendations_frame(rows: Sequence[Mapping[str, Any]], as_of: str | None) -> pl.DataFrame:
    """Two vocabularies, two sets of rows. Yahoo's five buckets for ring A outside China; East Money's six-month counts
    for the A-share ring A. A listing never appears under both, and no count of one is added to a count of the other.
    A null East Money count means East Money printed none: it stays null on a listing row, and a pooled field is null
    when no listing printed it. A listing the run holds no row for has no counts to show and no row here."""
    yahoo_rows, em_rows = [], []
    for r in _ring(rows, "A"):
        if not r["priced"]:
            continue
        if r["market_group"] == "china":
            em_rows.append(r)
        else:
            yahoo_rows.append(r)
    base = {**dict.fromkeys(schema.RECOMMENDATIONS_DTYPES), "as_of": as_of}
    recs: list[dict[str, Any]] = []

    def listing(r: Mapping[str, Any], source: str, window: str) -> dict[str, Any]:
        return {
            **base,
            "source": source,
            "scope": "listing",
            "ticker": r["ticker"],
            "name": r["name"],
            "layer": r["layer"],
            "market_group": r["market_group"],
            "window": window,
            "n_listings": 1,
            "as_of": as_of,
        }

    def pooled(source: str, window: str, n: int, ratings: int, sums: dict[str, int | None]) -> dict[str, Any]:
        return {
            **base,
            "source": source,
            "scope": "pooled",
            "window": window,
            "n_listings": n,
            "n_ratings": ratings,
            **sums,
            "as_of": as_of,
        }

    window_y, window_e = "current distribution", C.EM_RATING_WINDOW
    for r in yahoo_rows:
        y = r.get("yahoo_ratings") or {}
        recs.append(
            {
                **listing(r, "yahoo", window_y),
                **{k: y.get(k) for k in schema.YAHOO_RATING_BUCKETS},
                "recommendation_mean": _rating_mean(r.get("recommendation_mean")),
                "n_ratings": sum(y.get(k) or 0 for k in schema.YAHOO_RATING_BUCKETS),
            }
        )
    for r in em_rows:
        e = r.get("em_ratings_6m") or {}
        counts = {f"em_{k}": e.get(k) for k in schema.EM_RATING_FIELDS}
        recs.append(
            {
                **listing(r, "eastmoney", window_e),
                **counts,
                "n_ratings": sum(v or 0 for k, v in counts.items() if k != "em_orgs"),
            }
        )

    def pool(source: str, fields: list[str], ratings_cols: list[str]) -> None:
        mine = [x for x in recs if x["source"] == source and x["scope"] == "listing"]
        if not mine:
            return
        sums = {f: (sum(x[f] or 0 for x in mine) if any(x[f] is not None for x in mine) else None) for f in fields}
        n = sum(1 for x in mine if any(x[f] is not None for f in ratings_cols))
        recs.append(
            pooled(
                source, window_y if source == "yahoo" else window_e, n, sum(sums[f] or 0 for f in ratings_cols), sums
            )
        )

    pool("yahoo", list(schema.YAHOO_RATING_BUCKETS), list(schema.YAHOO_RATING_BUCKETS))
    em_fields = list(schema.EM_COLUMNS)
    pool("eastmoney", em_fields, [f for f in em_fields if f != "em_orgs"])
    df = pl.DataFrame(recs, schema=schema.RECOMMENDATIONS_DTYPES).sort(["source", "scope", "ticker"], nulls_last=True)
    return schema.validate("recommendations", df)


# --- facts: scope, universe, multiples, Rule of 40 ------------------------------------------------------------------
def scope_facts(
    listings: pl.DataFrame, directory: Sequence[Mapping[str, Any]], summary: pl.DataFrame
) -> dict[str, Any]:
    """The size of what the page covers. Counts are exact tallies of the pool and of the private directory."""
    rows = listings.to_dicts()

    def ring(name: str) -> dict[str, int]:
        mine = _ring(rows, name)
        return {"n": len(mine), **{g: sum(1 for r in mine if r["market_group"] == g) for g in schema.MARKET_GROUPS}}

    by_layer = {
        r["layer"]: {
            "code": r["code"],
            "name": r["name"],
            "n_l2": r["n_l2"],
            "n_a": r["n_a"],
            "n_b": r["n_b"],
            "n_f": r["n_f"],
            "n_private": r["n_private"],
            "n_private_overseas": r["n_private_overseas"],
            "n_private_china": r["n_private_china"],
        }
        for r in summary.to_dicts()
    }
    return {
        "n_layers": len(C.LAYERS),
        "n_subsectors": sum(len(v["l2"]) for v in C.LAYERS.values()),
        "n_listed": len(_ring(rows, "A", "B")),
        "n_listings": len(rows),
        "n_private": len(directory),
        "n_markets": len({market_of(r["ticker"]) for r in rows}),
        "n_countries": len({r["country"] for r in rows}),
        "by_ring": {"A": ring("A"), "B": ring("B"), "F": ring("F"), "C": {"n": len(directory)}},
        "n_layers_without_ring_a": sum(1 for v in by_layer.values() if v["n_a"] == 0),
        "n_layers_private_outnumber_listed": sum(1 for v in by_layer.values() if v["n_private"] > v["n_a"] + v["n_b"]),
        "by_layer": by_layer,
        "by_market": dict(sorted(Counter(market_of(r["ticker"]) for r in rows).items())),
    }


def ev_sales_facts(rows: Sequence[Mapping[str, Any]], as_of: str | None) -> dict[str, Any]:
    """EV / next-twelve-month revenue over ring A. A listing with no multiple is counted and its reason named."""
    period = f"snapshot of {as_of}" if as_of else None
    a = _ring(rows, "A")
    have = [r for r in a if r["ev_ntm"] is not None]
    refused = [r for r in a if r["ev_ntm"] is None]
    reasons = Counter(r["ev_ntm_reason"] or "unstated" for r in refused)

    unit = "times next-twelve-month revenue"

    def stat(pick: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        s = spread(r["ev_ntm"] for r in pick)
        return val(
            s["median"],
            unit,
            period,
            as_of,
            YAHOO_URL,
            n=s["n"],
            p25=val(s["p25"], unit, period, as_of, YAHOO_URL),
            p75=val(s["p75"], unit, period, as_of, YAHOO_URL),
        )

    def layer_stat(layer: str) -> dict[str, Any]:
        s = spread(r["ev_ntm"] for r in have if r["layer"] == layer)
        return {
            "n": s["n"],
            "median": val(s["median"], unit, period, as_of, YAHOO_URL),
            "p25": val(s["p25"], unit, period, as_of, YAHOO_URL),
            "p75": val(s["p75"], unit, period, as_of, YAHOO_URL),
        }

    high = [r for r in have if r["purity"] == "high"]
    population = [r for r in have if r["in_regression_population"]]
    qualified = [r for r in high if r["market_group"] == "china" and (r["em_rev0_n"] or 0) >= C.CN_MEDIAN_MIN_BROKERS]
    cn_shown = len(qualified) >= C.CN_MEDIAN_MIN_N
    s_cn = spread(r["ev_ntm"] for r in qualified)
    s_pop = spread(r["ev_ntm"] for r in population)
    ratio = s_cn["median"] / s_pop["median"] if cn_shown and s_pop["median"] else None
    return {
        "as_of": as_of,
        "n_ring_a": len(a),
        "n_with": len(have),
        "n_refused": len(refused),
        "refused_reasons": [
            {
                "reason": k,
                "n": n,
                "tickers": sorted(r["ticker"] for r in refused if (r["ev_ntm_reason"] or "unstated") == k),
            }
            for k, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "median": stat(have),
        "p25": val(spread(r["ev_ntm"] for r in have)["p25"], unit, period, as_of, YAHOO_URL),
        "p75": val(spread(r["ev_ntm"] for r in have)["p75"], unit, period, as_of, YAHOO_URL),
        "by_layer": {layer: layer_stat(layer) for layer in C.LAYERS},
        "pure_play_median": stat(high),
        "us_pure_play_median": stat([r for r in high if r["market_group"] == "us"]),
        "us_population_median": stat(population),
        "cn_median": val(
            s_cn["median"] if cn_shown else None,
            unit,
            period,
            as_of,
            EASTMONEY_URL,
            n=s_cn["n"],
            n_needed=C.CN_MEDIAN_MIN_N,
            min_brokers=C.CN_MEDIAN_MIN_BROKERS,
            reason=None
            if cn_shown
            else f"fewer than {C.CN_MEDIAN_MIN_N} A-share companies of purity high with "
            f"{C.CN_MEDIAN_MIN_BROKERS} or more East Money brokers",
            note="enterprise value from Yahoo, revenue consensus from East Money",
        ),
        "cn_us_ratio": val(
            ratio, "ratio of the A-share median to the median of the Q1 and Q2 population", period, as_of, EASTMONEY_URL
        ),
        "ev_source": C.EV_SOURCE,
        "ntm_definition": C.NTM_WEIGHT,
        "definition": "enterprise value over next-twelve-month revenue; a multiple is computed only where the quote "
        "currency and the revenue currency agree",
    }


def rule_of_40_facts(rows: Sequence[Mapping[str, Any]], as_of: str | None) -> dict[str, Any]:
    """Rule of 40 = 100 x (growth + FCF margin) over ring A; the top and bottom three by score."""
    period = f"snapshot of {as_of}" if as_of else None
    unit = "points (growth plus FCF margin, in percent)"
    scored = [r for r in _ring(rows, "A") if r["rule_of_40"] is not None]
    cut = float(C.Q3["cut"])

    def entry(r: Mapping[str, Any]) -> dict[str, Any]:
        return val(
            r["rule_of_40"],
            unit,
            period,
            as_of,
            YAHOO_URL,
            ticker=r["ticker"],
            name=r["name"],
            layer=r["layer"],
            growth=val(r["growth"], "fraction", period, as_of, YAHOO_URL),
            fcf_margin=val(r["fcf_margin"], "fraction", period, as_of, YAHOO_URL),
        )

    ordered = sorted(scored, key=lambda r: (-r["rule_of_40"], r["ticker"]))
    return {
        "as_of": as_of,
        "n_ring_a": len(_ring(rows, "A")),
        "n_scored": len(scored),
        "n_pass": sum(1 for r in scored if r["rule_of_40"] >= cut),
        "cut": cut,
        "median_score": val(spread(r["rule_of_40"] for r in scored)["median"], unit, period, as_of, YAHOO_URL),
        "leaders": [entry(r) for r in ordered[:3]],
        "laggards": [entry(r) for r in sorted(scored, key=lambda r: (r["rule_of_40"], r["ticker"]))[:3]],
        "definition": C.RULE_OF_40,
    }


# --- facts: targets and recommendations -----------------------------------------------------------------------------
def targets_facts(rows: Sequence[Mapping[str, Any]], targets: pl.DataFrame, as_of: str | None) -> dict[str, Any]:
    """Target-price upside over ring A outside China. Median and quartiles are over every company with a target; the
    extremes are named among those with three or more analysts, because one analyst's target is not a view of a
    company. Upside is a ratio in the quote currency, so a price in pence needs no conversion; the count of listings
    quoted in GBp is given so the page can say so."""
    period = f"snapshot of {as_of}" if as_of else None
    unit = "fraction (0.10 is 10% above the price)"
    t = targets.to_dicts()
    a_out = [r for r in _ring(rows, "A") if r["market_group"] != "china"]
    covered = [r for r in t if not r["thin"]]
    listed = [r for r in t if r["in_q4_q5_list"]]

    def one(x: Mapping[str, Any]) -> dict[str, Any]:
        return val(
            x["upside"],
            unit,
            period,
            as_of,
            YAHOO_URL,
            ticker=x["ticker"],
            name=x["name"],
            n_opinions=x["n_opinions"],
            currency=x["currency"],
        )

    def block(pick: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        s = spread(x["upside"] for x in pick)
        ordered = sorted(pick, key=lambda x: (x["upside"], x["ticker"]))
        return {
            "n": s["n"],
            "median_upside": val(s["median"], unit, period, as_of, YAHOO_URL),
            "p25_upside": val(s["p25"], unit, period, as_of, YAHOO_URL),
            "p75_upside": val(s["p75"], unit, period, as_of, YAHOO_URL),
            "most_upside": one(ordered[-1]) if ordered else None,
            "least_upside": one(ordered[0]) if ordered else None,
        }

    def grouped(key: str, values: Iterable[str]) -> dict[str, Any]:
        out = {}
        for v in values:
            pick = [x for x in t if x[key] == v]
            s = spread(x["upside"] for x in pick)
            out[v] = {
                "n": s["n"],
                "n_thin": sum(1 for x in pick if x["thin"]),
                "median_upside": val(s["median"], unit, period, as_of, YAHOO_URL),
            }
        return out

    all_block = block(t)
    covered_block = block(covered)
    thin_extreme = sorted((x for x in t if x["thin"]), key=lambda x: -abs(x["upside"]))[:3]
    return {
        "as_of": as_of,
        "n_ring_a_outside_china": len(a_out),
        "n_with_target": len(t),
        "n_without": len(a_out) - len(t),
        "n_above_price": sum(1 for x in t if x["target_mean"] > x["price"]),
        "n_below_price": sum(1 for x in t if x["target_mean"] < x["price"]),
        "n_thin": sum(1 for x in t if x["thin"]),
        "thin_below": C.THIN_COVERAGE_BELOW,
        "median_upside": all_block["median_upside"],
        "p25_upside": all_block["p25_upside"],
        "p75_upside": all_block["p75_upside"],
        "by_layer": grouped("layer", list(C.LAYERS)),
        "by_market": grouped("market_group", ["us", "other_overseas"]),
        "most_upside": covered_block["most_upside"],
        "least_upside": covered_block["least_upside"],
        "extremes_among": f"companies with {C.THIN_COVERAGE_BELOW} or more analysts",
        "thin_extremes": [one(x) | {"thin": True} for x in thin_extreme],
        "covered": covered_block,
        "q4_q5_list": {"n_registered": len(C.FROZEN_Q4_Q5), **block(listed)},
        "median_dispersion": val(
            spread(x["dispersion"] for x in covered if x["dispersion"] is not None)["median"],
            "fraction of the target mean (high minus low over mean)",
            period,
            as_of,
            YAHOO_URL,
            n=len([x for x in covered if x["dispersion"] is not None]),
        ),
        "n_gbp_pence_normalised": sum(1 for x in t if x["pence_quote"]),
        "quote_currency_note": "upside is target mean over price minus one, both in the quote currency, so a price "
        "quoted in pence (GBp) cancels in the ratio and needs no conversion",
        "definition": C.UPSIDE,
        "dispersion_definition": C.DISPERSION,
        "a_shares": "left out by design: East Money's report-list target is empty for cybersecurity and Yahoo's rests "
        "on very few analysts (see cn.n_opinions_min and cn.n_opinions_max), so the two sources are not comparable",
    }


def recommendations_facts(rec: pl.DataFrame, as_of: str | None) -> dict[str, Any]:
    """The pooled counts of each vocabulary, side by side and never added. East Money's neutral, reduce and sell
    fields are empty for every covered cybersecurity company, which the page reads as none and says so."""
    period = f"snapshot of {as_of}" if as_of else None
    rows = rec.to_dicts()

    def pooled(source: str) -> dict[str, Any] | None:
        return next((r for r in rows if r["source"] == source and r["scope"] == "pooled"), None)

    def listing_rows(source: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["source"] == source and r["scope"] == "listing"]

    y, e = pooled("yahoo"), pooled("eastmoney")
    y_n = y["n_ratings"] if y else 0
    y_rows = listing_rows("yahoo")
    yahoo = {
        "window": "current distribution",
        "scope": "ring A outside China",
        # every priced listing of the scope; the pooled counts below cover those that printed any bucket, zeros
        # included (n_listings_printing), and n_listings_with_ratings those with at least one analyst rating
        "n_listings": len(y_rows),
        "n_listings_printing": y["n_listings"] if y else 0,
        "n_listings_with_ratings": sum(1 for r in y_rows if any((r[k] or 0) > 0 for k in schema.YAHOO_RATING_BUCKETS)),
        "n_ratings": y_n,
        "counts": {k: (y[k] if y else None) for k in schema.YAHOO_RATING_BUCKETS},
        "shares": {
            k: val(y[k] / y_n if y and y_n and y[k] is not None else None, "share of ratings", period, as_of, YAHOO_URL)
            for k in schema.YAHOO_RATING_BUCKETS
        },
        "excluded": "A-share listings: their Yahoo ratings rest on very few analysts and no registered item uses them",
        "vocabulary": "strong buy, buy, hold, sell, strong sell",
    }
    e_rows = listing_rows("eastmoney")
    fields = [f for f in schema.EM_COLUMNS if f != "em_orgs"]
    eastmoney = {
        "window": C.EM_RATING_WINDOW,
        "scope": "A-share ring A",
        "n_listings": len(e_rows),
        "n_covered": sum(1 for r in e_rows if r["em_orgs"] is not None),
        "n_wholly_null": sum(1 for r in e_rows if all(r[f] is None for f in schema.EM_COLUMNS)),
        "n_org_company_pairs": e["em_orgs"] if e else None,
        "n_org_company_pairs_note": "an institution that rates several companies is counted once for each",
        "n_ratings": e["n_ratings"] if e else 0,
        "counts": {f.removeprefix("em_"): (e[f] if e else None) for f in fields},
        "n_listings_printing": {f.removeprefix("em_"): sum(1 for r in e_rows if r[f] is not None) for f in fields},
        "null_means": "East Money printed none: a null count is read as zero only where the page says so",
        "vocabulary": "buy, add, neutral, reduce, sell",
        "source_url": EASTMONEY_URL,
        "as_of": as_of,
    }
    return {
        "as_of": as_of,
        "yahoo": yahoo,
        "eastmoney": eastmoney,
        "note": "the two vocabularies are shown side by side and never merged or added",
    }


# --- facts: the A-share panel, the regression, the ring C section ---------------------------------------------------
def cn_facts(panel: pl.DataFrame, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A-share valuation: the panel's own facts, the percentile of each listing, and East Money and Yahoo coverage."""

    a_shares = [r for r in rows if r["market_group"] == "china" and r["ring"] in ("A", "B")]
    ring_a = _ring(a_shares, "A")
    analysts = [r["n_opinions"] for r in ring_a if r["target_mean"] is not None and r["n_opinions"] is not None]
    panel_facts = V.facts(panel)
    percentile = {}
    for r in panel.to_dicts():
        if r["ps_percentile"] is None:
            continue
        percentile[r["ticker"]] = val(
            r["ps_percentile"],
            "share of days since 2018 at or below the latest",
            f"since {r['history_start']}",
            r["as_of"],
            V.SOURCE_URL,
            name=r["name"],
            ring=r["ring"],
            n_days=r["n_history_days"],
            ps_ttm=val(r["ps_ttm"], "times trailing-12-month revenue", "latest day", r["as_of"], V.SOURCE_URL),
            revenue_yoy=val(
                r["revenue_yoy"], "fraction, cumulative year on year", r["revenue_period"], r["as_of"], V.REPORT_URL
            ),
            flagged=r["flags"][V.FLAG_PS_HIGH_REVENUE_FALLING],
        )
    return {
        **panel_facts,
        "n_listed": len(a_shares),
        "n_ring_a": len(ring_a),
        "n_ring_b": len(_ring(a_shares, "B")),
        "n_panel_rows": panel.height,
        "ps_percentile": percentile,
        "n_with_em_consensus": sum(1 for r in ring_a if (r["em_rev0_n"] or 0) >= 1),
        "n_with_yahoo_target": sum(1 for r in ring_a if r["target_mean"] is not None),
        "n_opinions_min": min(analysts, default=None),
        "n_opinions_max": max(analysts, default=None),
        "yahoo_target_note": "stored and used in no item: it rests on very few analysts",
        "flag_definition": f"{V.FLAG_PS_HIGH_REVENUE_FALLING}: PS_TTM percentile at or above {V.TOP_QUARTILE} while "
        "year-on-year revenue is negative",
    }


def _est(
    value: float | None, unit: str, period: str, as_of: str, ci: Sequence[float] | None, level: str | None, **extra: Any
) -> dict[str, Any]:
    lo, hi = (ci[0], ci[1]) if ci else (None, None)
    return val(value, unit, period, as_of, YAHOO_URL, lo=lo, hi=hi, level=level if ci else None, **extra)


def _regression_block(
    point: Mapping[str, Any] | None, prem: Mapping[str, Any] | None, units: Mapping[str, str]
) -> dict[str, Any]:
    """One weekly point as published numbers. The qualifier is ``=``: an estimate computed exactly from the table; the
    interval is the 95% percentile bootstrap the point carries."""
    if point is None:
        return {"as_of": None, "fitted": False, "reason": "no usable consensus run"}
    d, period = point["as_of"], f"one run, read at {point['as_of']}"
    gain = (
        None
        if point["adj_r2"] is None or point["adj_r2_no_layer"] is None
        else point["adj_r2"] - point["adj_r2_no_layer"]
    )
    level = "95%"
    out = {
        "as_of": d,
        "run_ts": point["run_ts"],
        "fitted": point["fitted"],
        "reason": point["reason"],
        "n": point["n"],
        "n_us": point["n_us"],
        "n_cn": point["n_cn"],
        "n_params": point["n_params"],
        "ci_draws": point["ci_draws"],
        "r2": val(point["r2"], "share of variance", period, d, YAHOO_URL),
        "adj_r2": val(point["adj_r2"], "share of variance, adjusted", period, d, YAHOO_URL),
        "adj_r2_no_layer": val(
            point["adj_r2_no_layer"], "share of variance, adjusted, without the layer groups", period, d, YAHOO_URL
        ),
        "layer_gain": val(gain, "adjusted R squared gained by the layer groups in this model", period, d, YAHOO_URL),
        "slope": _est(point["slope"], units["slope"], period, d, point["slope_ci"], level),
        "fcf_coef": _est(point["fcf_coef"], units["fcf_coef"], period, d, point["fcf_ci"], level),
        "china_dummy": _est(point["china_dummy"], units["china_dummy"], period, d, point["china_ci"], level),
        "layer_coefs": [
            {
                "layer": c["layer"],
                "n": c["n"],
                "reference": c["reference"],
                "coef": val(c["coef"], units["layer_coefs"], period, d, YAHOO_URL),
            }
            for c in point["layer_coefs"]
        ],
        "population": point["population"],
        "dropped": point["dropped"],
    }
    if prem is not None:
        pp = f"one run, read at {prem['as_of']}"
        out["premium"] = {
            "ai_trio_median": val(prem["ai_trio_median"], units["ev_ntm"], pp, prem["as_of"], YAHOO_URL),
            "rest_median": val(prem["rest_median"], units["ev_ntm"], pp, prem["as_of"], YAHOO_URL),
            "ratio": val(prem["ratio"], units["premium"], pp, prem["as_of"], YAHOO_URL),
            "n_trio": prem["n_trio"],
            "n_rest": prem["n_rest"],
            "reason": prem["reason"],
        }
    return out


def regression_facts(reg: Mapping[str, Any]) -> dict[str, Any]:
    """The page regression as facts: the latest weekly point and its premium, the baseline reading and the series size.
    The model is the page's own and is labelled as no verdict."""
    spec = reg["spec"]
    block = _regression_block(reg["latest"], reg["premium"], spec["units"])
    base = reg["baseline"]
    return {
        **block,
        "verdict": spec["verdict"],
        "model": spec["model"],
        "fitted_on": spec["fitted_on"],
        "draws": spec["draws"],
        "seed": spec["seed"],
        "age_days": reg["latest_age_days"],
        "n_series": len(reg["series"]),
        "series_from": C.FIRST_SNAPSHOT,
        "baseline": _regression_block(base["regression"], base["premium"], spec["units"]),
        "units": spec["units"],
        "definitions": spec["definitions"],
        "note": "this is the page's own model (growth, FCF margin, China and layer groups); it is not one of the "
        "registered readings, and its layer gain is not Q1's uplift",
    }


def events_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """``ledger.summary['events']`` with its newest event's price as a published number."""
    out = dict(summary)
    latest = out.get("latest")
    if latest is not None:
        out["latest"] = {
            **latest,
            "price": val(
                latest["chart_value"],
                latest["unit"] or "unstated",
                latest["event_date"],
                latest["event_date"],
                latest["source_url"],
                qualifier=latest["chart_qualifier"] or "=",
                basis=latest["chart_basis"],
            ),
        }
    return out


# --- KPIs -----------------------------------------------------------------------------------------------------------
def kpi_frame(facts: Mapping[str, Any]) -> pl.DataFrame:
    """Exactly three headline KPIs: the first three available of a fixed priority list, each read from facts and each
    tagged ``V`` (stated by the source) or ``C`` (computed from stated figures). The list ends in pool counts, which
    exist even with no snapshot, so there are always three."""
    cands: list[dict[str, Any]] = []

    def add(kid: str, label: str, v: Mapping[str, Any] | None, tag: str, note: str) -> None:
        if v and v.get("value") is not None and v.get("source_url") and v.get("as_of"):
            cands.append(
                {
                    "id": kid,
                    "label": label,
                    "value": v["value"],
                    "qualifier": v["qualifier"],
                    "unit": v["unit"],
                    "period": v["period"] or "",
                    "source_url": v["source_url"],
                    "as_of": v["as_of"],
                    "tag": tag,
                    "note": note,
                }
            )

    ev, tg, pv = facts["ev_sales"], facts["targets"], facts["private"]
    n_group = ev["us_population_median"].get("n", 0)
    add(
        "pure_play_ev_ntm",
        f"Median EV / next-twelve-month revenue, the {n_group} US-listed pure-play companies in the registered group",
        ev["us_population_median"],
        "C",
        "US-listed companies whose whole business is security and that at least "
        f"{frozen.MIN_ANALYSTS} revenue analysts cover, the group the registered claims are about; enterprise value "
        "over next-twelve-month revenue. The median over every US-listed pure play with a multiple is lower and is "
        "given with the valuation figures",
    )
    cov = tg["covered"]
    add(
        "median_target_upside",
        "Median analyst target upside, companies outside China with three or more analysts",
        cov["median_upside"],
        "C",
        f"ring A outside China, {cov['n']} companies with {C.THIN_COVERAGE_BELOW} or more target-price analysts",
    )
    top = pv.get("latest_valuation_max")
    if top:
        stated = "company-stated" if top["basis"] == "disclosed" and top.get("company_confirmed") else "reported"
        cands.append(
            {
                "id": "highest_private_valuation",
                "label": f"Highest {stated} valuation in the ledger ({top['company']})",
                "value": top["value"],
                "qualifier": top["qualifier"],
                "unit": top["unit"],
                "period": top["period"] or "",
                "source_url": top["source_url"],
                "as_of": top["as_of"],
                "tag": top["tag"],
                "note": f"{top['basis']} valuation; round of {top['vintage_year']}"
                + ("; stale mark set" if top["stale_mark"] else "")
                + (f". {top['caveat']}" if top.get("caveat") else ""),
            }
        )
    add(
        "cn_us_multiple_ratio",
        "A-share median multiple as a share of the US median",
        ev["cn_us_ratio"],
        "C",
        "A-share ring A of purity high with two or more East Money brokers, over the Q1 and Q2 population",
    )
    cn_pct = facts["cn"]["median_ps_percentile"]
    add(
        "cn_median_ps_percentile",
        "Median A-share PS_TTM percentile within its own history since 2018",
        cn_pct,
        "C",
        f"{facts['cn']['n_with_percentile']} A-share listings with a percentile",
    )
    n_a = facts["scope"]["by_ring"]["A"]["n"]
    when = facts["as_of"] or facts["registered"]
    for kid, label, value, unit, note in (
        ("ring_a_listings", "Listed companies priced on the page (ring A)", n_a, "companies", "the core pricing pool"),
        (
            "listed_companies",
            "Listed companies classified on the page",
            facts["scope"]["n_listed"],
            "companies",
            "rings A and B",
        ),
        ("layers", "Layers of the one tree", facts["scope"]["n_layers"], "layers", "network edge to infrastructure"),
    ):
        cands.append(
            {
                "id": kid,
                "label": label,
                "value": float(value),
                "qualifier": "=",
                "unit": unit,
                "period": "the pool as registered",
                "source_url": REGISTRATION_URL,
                "as_of": when,
                "tag": "C",
                "note": note,
            }
        )
    return schema.frame("kpis", cands[:3])


# --- checks ---------------------------------------------------------------------------------------------------------
def check_ring_counts(listings: pl.DataFrame) -> dict[str, str]:
    got, want = Counter(listings["ring"].to_list()), Counter(e.ring for e in POOL)
    ok = got == want and listings.height == len(POOL)
    return check(
        "Listing rows by ring",
        ok,
        ", ".join(f"ring {r}: {got.get(r, 0)} of {want[r]}" for r in sorted(want))
        + f"; {listings.height} of {len(POOL)} pool listings",
    )


def check_priced(rows: Sequence[Mapping[str, Any]], has_run: bool) -> dict[str, str]:
    a = _ring(rows, "A")
    if not has_run:
        return check(
            "Ring A priced", False, f"no usable consensus run: 0 of {len(a)} ring A listings have a price", warn=True
        )
    missing = [r["ticker"] for r in a if r.get("price") is None]
    return check(
        "Ring A priced",
        not missing,
        f"{len(a) - len(missing)} of {len(a)} ring A listings have a price"
        + (f"; none for {_names(missing)}" if missing else ""),
        warn=True,
    )


def check_price_age(rows: Sequence[Mapping[str, Any]], run_ts: str | None) -> dict[str, str]:
    if run_ts is None:
        return check("Price age", False, "no usable consensus run", warn=True)
    stale = [
        f"{r['ticker']} ({_days(run_ts, r['price_ts'])} d)"
        for r in _ring(rows, "A")
        if r.get("price") is not None and r.get("price_ts") and _days(run_ts, r["price_ts"]) > PRICE_STALE_DAYS
    ]
    unstamped = [r["ticker"] for r in _ring(rows, "A") if r.get("price") is not None and not r.get("price_ts")]
    detail = f"quotes older than {PRICE_STALE_DAYS} days against the run: {_names(stale) if stale else 'none'}"
    if unstamped:
        detail += f"; no time stamp for {_names(unstamped)}"
    return check("Price age", not stale and not unstamped, detail, warn=True)


def check_run_age(run_ts: str | None, today: date) -> dict[str, str]:
    if run_ts is None:
        return check("Consensus freshness", False, "no usable consensus run is stored yet", warn=True)
    age = (today - date.fromisoformat(run_ts[:10])).days
    ok = age <= RUN_WARN_DAYS
    return check(
        "Consensus freshness",
        ok,
        f"newest usable run {run_ts[:10]}, {age} days before {today.isoformat()} "
        f"(weekly capture; limit {RUN_WARN_DAYS})",
        warn=age <= RUN_FAIL_DAYS,
    )


def check_coverage(rows: Sequence[Mapping[str, Any]], has_run: bool) -> dict[str, str]:
    a = _ring(rows, "A")
    if not has_run:
        return check("Consensus coverage", False, "no usable consensus run", warn=True)
    with_multiple = sum(1 for r in a if r["ev_ntm"] is not None)
    share = with_multiple / len(a) if a else 0.0
    with_target = sum(1 for r in a if r.get("target_mean") is not None)
    return check(
        "Consensus coverage",
        share >= readings.PRICED_SHARE,
        f"{with_multiple} of {len(a)} ring A listings have a multiple ({share:.0%}; the capture needs "
        f"{readings.PRICED_SHARE:.0%} priced to count a run); {with_target} have a target price",
        warn=True,
    )


def check_multiples_reasoned(rows: Sequence[Mapping[str, Any]], has_run: bool) -> dict[str, str]:
    if not has_run:
        return check("Missing multiples have a reason", True, "no usable consensus run, so no multiple is expected")
    silent = [r["ticker"] for r in _ring(rows, "A") if r["ev_ntm"] is None and not r["ev_ntm_reason"]]
    by = Counter(r["ev_ntm_reason"] for r in _ring(rows, "A") if r["ev_ntm"] is None and r["ev_ntm_reason"])
    stated = ", ".join(f"{k} {n}" for k, n in sorted(by.items())) or "none missing"
    return check(
        "Missing multiples have a reason",
        not silent,
        f"every ring A listing without a multiple says why ({stated})"
        if not silent
        else f"no multiple and no stated reason for {_names(silent)}",
    )


def check_target_units(targets: pl.DataFrame) -> dict[str, str]:
    """Pence against pounds: a target near 100 times, or near a hundredth of, the price. An extreme but plausible ratio
    (a single analyst's target on a microcap) is named and does not fail."""
    lo_s, hi_s = TARGET_UNIT_SLIP
    lo_e, hi_e = TARGET_EXTREME
    slips, extreme = [], []
    for r in targets.to_dicts():
        ratio = r["target_mean"] / r["price"]
        if lo_s <= ratio <= hi_s or lo_s <= 1.0 / ratio <= hi_s:
            slips.append(f"{r['ticker']} ({ratio:.3g}x)")
        elif not lo_e <= ratio <= hi_e:
            extreme.append(f"{r['ticker']} ({ratio:.3g}x)")
    n_pence = int(targets["pence_quote"].sum()) if targets.height else 0
    detail = (
        f"{targets.height} targets against prices; {n_pence} quoted in pence (GBp), where the ratio cancels the unit; "
        + (f"possible pence-against-pounds slip: {_names(slips)}" if slips else "no ratio near 100 or 1/100")
        + (f"; target more than {hi_e:g}x or under {lo_e:g}x the price: {_names(extreme)}" if extreme else "")
    )
    name = "Target price and quote currency agree"
    if slips:
        return check(name, False, detail)
    return check(name, not extreme, detail, warn=True)


def check_target_analysts(targets: pl.DataFrame, frame: pl.DataFrame | None) -> dict[str, str]:
    """``n_opinions`` is Yahoo's count of target-price analysts. It is never the count of analysts behind the revenue
    consensus (``rev_fy0_n``, ``rev_fy1_n``) nor East Money's: each row equals the snapshot's ``n_opinions``."""
    if frame is None:
        return check("Target analyst count", True, "no usable consensus run, so no target is published")
    snap = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    wrong = [r["ticker"] for r in targets.to_dicts() if r["n_opinions"] != snap[r["ticker"]]["n_opinions"]]
    differ = sum(1 for r in targets.to_dicts() if snap[r["ticker"]]["n_opinions"] != snap[r["ticker"]]["rev_fy1_n"])
    return check(
        "Target analyst count",
        not wrong,
        f"{targets.height} targets use Yahoo's target-analyst count (n_opinions), which differs from the "
        f"revenue-estimate analyst count for {differ} of them; the two are never mixed"
        + (f"; not the snapshot's count: {_names(wrong)}" if wrong else ""),
    )


def previous_run(
    runs: pl.DataFrame | None, today: date
) -> tuple[tuple[str, pl.DataFrame], tuple[str, pl.DataFrame]] | None:
    """(newest usable run, the usable run closest to a week before it), or None if there is no such pair. ``WEEK_BACK``
    bounds the gap in days; a run of the same week is not a week-on-week pair."""
    frame = regression.runs_until(runs, today)
    usable = metrics.usable_runs(frame) if frame is not None else {}
    if len(usable) < 2:
        return None
    newest = max(usable)
    gaps = {ts: _days(newest, ts) for ts in usable if ts != newest}
    near = [ts for ts, g in gaps.items() if WEEK_BACK[0] <= g <= WEEK_BACK[1]]
    if not near:
        return None
    prev = min(near, key=lambda ts: (abs(gaps[ts] - 7), ts))
    return (newest, usable[newest]), (prev, usable[prev])


def check_week_on_week(runs: pl.DataFrame | None, today: date) -> list[dict[str, str]]:
    """The target mean that moved more than 30% in a week and the analyst count that halved. Needs two runs about a week
    apart; with one run (the baseline alone) neither can be assessed and both say so."""
    names = ("Target mean week on week", "Analyst count week on week")
    pair = previous_run(runs, today)
    if pair is None:
        why = f"not assessable: needs two usable runs {WEEK_BACK[0]} to {WEEK_BACK[1]} days apart"
        return [check(n, False, why, warn=True) for n in names]
    (now_ts, now), (prev_ts, prev) = pair
    a = {r["ticker"]: r for r in prev.iter_rows(named=True)}
    moved, halved = [], []
    for r in now.iter_rows(named=True):
        p = a.get(r["ticker"])
        if p is None:
            continue
        if r["target_mean"] and p["target_mean"] and abs(r["target_mean"] / p["target_mean"] - 1.0) > TARGET_MOVE:
            moved.append(f"{r['ticker']} ({r['target_mean'] / p['target_mean'] - 1.0:+.0%})")
        if (p["n_opinions"] or 0) >= 2 and (r["n_opinions"] or 0) <= p["n_opinions"] / 2:
            halved.append(f"{r['ticker']} ({p['n_opinions']} to {r['n_opinions'] or 0})")
    span = f"{prev_ts[:10]} to {now_ts[:10]}"
    return [
        check(
            names[0],
            not moved,
            f"{span}: target mean moved more than {TARGET_MOVE:.0%} for " + (_names(moved) if moved else "no listing"),
            warn=True,
        ),
        check(
            names[1],
            not halved,
            f"{span}: analyst count at least halved for " + (_names(halved) if halved else "no listing"),
            warn=True,
        ),
    ]


def check_eastmoney_ratings(rows: Sequence[Mapping[str, Any]], has_run: bool) -> dict[str, str]:
    a_share = [r for r in _ring(rows, "A") if r["market_group"] == "china"]
    if not has_run:
        return check("East Money rating rows", False, "no usable consensus run", warn=True)
    empty = [
        r["ticker"]
        for r in a_share
        if not r.get("em_ratings_6m") or all(v is None for v in r["em_ratings_6m"].values())
    ]
    return check(
        "East Money rating rows",
        not empty,
        f"{len(a_share) - len(empty)} of {len(a_share)} A-share ring A listings have an East Money rating "
        f"count" + (f"; wholly null (East Money printed none): {_names(empty)}" if empty else ""),
        warn=True,
    )


def fx_used(listings: pl.DataFrame, dates: Mapping[str, str]) -> dict[str, str]:
    """The day of the rate behind each currency that has an enterprise value in dollars in ``listings``."""
    used = {
        valuation_metrics.major_currency(c)
        for c in listings.filter(pl.col("enterprise_value_usd").is_not_null())["currency"].drop_nulls().to_list()
    }
    return {c: dates[c] for c in sorted(used) if c in dates}


def check_fx(inputs: Inputs, listings: pl.DataFrame, as_of: str | None) -> dict[str, str]:
    """The exchange rates behind the enterprise value in dollars. No table, or one that cannot be read, is a warn and an
    empty column; so are a currency without a rate, and a newest rate older than ``FX_STALE_DAYS`` against the run."""
    name = "Exchange rates"
    if not inputs.fx_rates:
        why = inputs.errors.get("fx") or "no exchange-rate snapshot is stored"
        return check(name, False, f"{why}: enterprise value in dollars is empty for every listing", warn=True)
    have = listings.filter(pl.col("enterprise_value").is_not_null())
    if have.is_empty():
        return check(name, True, f"rates for {len(inputs.fx_rates)} currencies; no listing has an enterprise value")
    bare = have.filter(pl.col("enterprise_value_usd").is_null())
    missing = sorted({str(c) for c in bare["currency"].to_list()})
    used = fx_used(listings, inputs.fx_dates)
    newest, oldest = max(used.values(), default=None), min(used.values(), default=None)
    old = newest is not None and _days(as_of or inputs.today.isoformat(), newest) > FX_STALE_DAYS
    days = "no date" if newest is None else newest if newest == oldest else f"{oldest} to {newest}"
    detail = (
        f"{have.height - bare.height} of {have.height} listings with an enterprise value have it in dollars, at the "
        f"newest rate on file for each of {len(used)} currencies (rate day {days})"
    )
    if missing:
        detail += f"; no rate for {', '.join(missing)}: {_names(bare['ticker'].to_list())} left empty"
    if old:
        detail += f"; the newest rate is more than {FX_STALE_DAYS} days older than the run"
    return check(name, not missing and not old, detail, warn=True)


def check_deal_count(events: pl.DataFrame, summary: Mapping[str, Any]) -> dict[str, str]:
    """A deal counts once: the deals the events mart marks as opened inside the twelve-month window are the
    ``acquisitions_12m`` of the facts, so a reader who counts the mart gets the same number."""
    lo, hi = summary.get("window_from"), summary.get("window_to")
    if lo is None or hi is None:
        return check("Deal count matches the events mart", True, "no events window")
    n = events.filter(pl.col("deal_start") & (pl.col("event_date") > lo) & (pl.col("event_date") <= hi)).height
    return check(
        "Deal count matches the events mart",
        n == summary["acquisitions_12m"],
        f"{n} deal-opening events in the events mart between {lo} (not included) and {hi}; "
        f"acquisitions_12m is {summary['acquisitions_12m']}",
    )


def snapshot_trace_problems(listings: pl.DataFrame, frame: pl.DataFrame | None) -> list[str]:
    """Why a listing column is not a parsed capture field or a measure derived from one: a column with no declared
    source, or a value that is not the snapshot's."""
    problems = [
        f"column {c!r} has no declared source"
        for c in listings.columns
        if c not in schema.LISTINGS_DTYPES
        or c not in POOL_FIELDS | DERIVED_FIELDS | set(COPIED_FROM_SNAPSHOT) | set(STRUCT_FROM_SNAPSHOT)
    ]
    if frame is None:
        return problems
    snap = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    for r in listings.filter(pl.col("priced")).iter_rows(named=True):
        s = snap.get(r["ticker"])
        if s is None:
            problems.append(f"{r['ticker']}: priced but not in the run")
            continue
        problems += [
            f"{r['ticker']}.{c} differs from the snapshot" for c, sc in COPIED_FROM_SNAPSHOT.items() if r[c] != s[sc]
        ]
        for col, mapping in STRUCT_FROM_SNAPSHOT.items():
            if r[col] is not None:
                problems += [
                    f"{r['ticker']}.{col}.{k} differs from the snapshot"
                    for k, sc in mapping.items()
                    if r[col][k] != s[sc]
                ]
    return problems


def check_snapshot_trace(listings: pl.DataFrame, frame: pl.DataFrame | None) -> dict[str, str]:
    problems = snapshot_trace_problems(listings, frame)
    return check(
        "Listing values are parsed capture fields",
        not problems,
        "every Yahoo-sourced listing column is a capture-table column, copied as parsed, or a derived measure"
        if not problems
        else "; ".join(problems[:5]) + (f" and {len(problems) - 5} more" if len(problems) > 5 else ""),
    )


def blocked_host(url: str) -> str | None:
    """The blocked source a URL points at, matching the host exactly or as a parent domain. (``reference`` also tests
    ``d in host``, which makes ``ft.com`` match ``microsoft.com``; a host test should be on the host.)"""
    host = (urlparse(str(url)).hostname or "").lower()
    for d in reference.BLOCKED_SOURCES:
        if host == d or host.endswith("." + d) or ("." not in d and d in host):
            return d
    return None


def _url_keys(name: str) -> bool:
    return name in ("url", "website") or name.endswith("source_url")


def _leaf(path: str) -> str:
    """The key a node sits under: ``a.b[2].source_url`` is ``source_url``, ``a.b[2]`` is ``b``."""
    return path.rsplit(".", 1)[-1].split("[")[0]


def blocked_sources(facts: Mapping[str, Any], marts: Mapping[str, Any]) -> list[str]:
    """Where a published URL points at a blocked host, as ``place: host``."""
    hits: list[str] = []
    for name, m in marts.items():
        if isinstance(m, pl.DataFrame):
            for col in (c for c in m.columns if _url_keys(c)):
                hits += [
                    f"{name}.{col}: {blocked_host(u)}"
                    for u in m[col].drop_nulls().unique().to_list()
                    if blocked_host(u)
                ]
        else:
            hits += [
                f"{name}.{p}: {blocked_host(v)}"
                for p, v in schema.walk(m)
                if isinstance(v, str) and _url_keys(_leaf(p)) and blocked_host(v)
            ]
    hits += [
        f"facts.{p}: {blocked_host(v)}"
        for p, v in schema.walk(facts)
        if isinstance(v, str) and _url_keys(_leaf(p)) and blocked_host(v)
    ]
    return sorted(set(hits))


def check_regression_population(reg: Mapping[str, Any], frame: pl.DataFrame | None) -> dict[str, str]:
    """The US part of the regression's population is the registered Q1 and Q2 population of that run, less the companies
    the regression dropped for lack of a growth or FCF figure; the rest are the A-share group."""
    latest = reg["latest"]
    if latest is None or frame is None:
        return check(
            "Regression population equals the registered population",
            True,
            "no usable consensus run, so no regression population",
        )
    us = [t for t in latest["population"] if not frozen.is_china(t)]
    expected = sorted(set(frozen.q1_population(frame)) - set(latest["dropped"]))
    cn = [t for t in latest["population"] if frozen.is_china(t)]
    cn_ok = set(cn) <= set(C.FROZEN_Q7_CN)
    ok = us == expected and cn_ok
    return check(
        "Regression population equals the registered population",
        ok,
        f"{len(us)} US companies equal frozen.q1_population less {len(latest['dropped'])} dropped, and "
        f"{len(cn)} A-share companies are in the Q7 group"
        if ok
        else f"US part {us} differs from the registered population {expected}"
        + ("" if cn_ok else f"; A-share part {cn} is not inside the Q7 group"),
    )


def check_regression_point(reg: Mapping[str, Any]) -> dict[str, str]:
    latest = reg["latest"]
    if latest is None:
        return check("Regression point", False, "no usable consensus run: no regression", warn=True)
    if not latest["fitted"]:
        return check("Regression point", False, f"{latest['as_of']}: not fitted ({latest['reason']})", warn=True)
    return check(
        "Regression point",
        True,
        f"{latest['as_of']}: {latest['n']} companies ({latest['n_us']} US, "
        f"{latest['n_cn']} A-share), {latest['n_params']} parameters, {latest['ci_draws']} bootstrap draws",
    )


def check_weekly_series(reg: Mapping[str, Any]) -> dict[str, str]:
    n = len(reg["series"])
    return check(
        "Weekly series",
        n > 0,
        f"{n} Monday snapshot(s) since {C.FIRST_SNAPSHOT}, the start of the formal series"
        + ("" if n else "; the series charts and Q1 to Q10 wait for the first Monday run"),
        warn=True,
    )


def check_kpis(kpis: pl.DataFrame) -> dict[str, str]:
    tags = sorted(set(kpis["tag"].to_list()))
    return check(
        "Headline KPIs",
        kpis.height == 3 and set(tags) <= set(schema.CHARTABLE_TAGS),
        f"{kpis.height} KPIs ({', '.join(kpis['id'].to_list())}), tags {'/'.join(tags)}",
    )


def check_scoreboard(board: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    by = Counter(r["status"] for r in board)
    return check(
        "Scoreboard",
        [r["id"] for r in board] == evaluate.QIDS,
        f"{len(board)} items: " + ", ".join(f"{by.get(s, 0)} {s}" for s in C.QSTATUS),
    )


def check_china(
    panel: pl.DataFrame, rows: Sequence[Mapping[str, Any]], today: date, loaded: bool
) -> list[dict[str, str]]:
    """The module's own checks when East Money history is stored; one warn when none is."""
    if not loaded:
        n = sum(1 for r in rows if r["market_group"] == "china" and r["ring"] in ("A", "B"))
        return [
            check(
                "China panel",
                False,
                f"no East Money valuation history is stored yet for the {n} A-share "
                "listings of rings A and B (written by pipelines.cyber.valuation_history_em)",
                warn=True,
            )
        ]
    return V.checks(panel, rows, today)


def check_curator_notes(facts: Mapping[str, Any], marts: Mapping[str, Any]) -> dict[str, str]:
    leaks = [
        name
        for name, m in marts.items()
        if (has_key(m, "curator_notes") if isinstance(m, dict) else any("curator_notes" in c for c in m.columns))
    ]
    if has_key(facts, "curator_notes"):
        leaks.append("facts")
    return check(
        "Private notes never published",
        not leaks,
        "no private-notes field in facts or any mart"
        if not leaks
        else "a private-notes field reached " + ", ".join(leaks),
    )


def check_working_notes(facts: Mapping[str, Any], marts: Mapping[str, Any]) -> dict[str, str]:
    """No string, and no key, of the facts or of any mart carries a working-note word (``schema.WORKING_NOTE_WORDS``):
    a note to the maintainer in a table the reader can open is a published note."""
    hits = [f"facts {h}" for h in schema.working_note_strings(facts)]
    for name, m in marts.items():
        hits += [f"{name} {h}" for h in schema.working_note_strings(m)]
    return check(
        "No working notes in published text",
        not hits,
        "no working-note wording in any published string or key" if not hits else "; ".join(hits[:5]),
    )


def check_chartable(marts: Mapping[str, Any], kpis: pl.DataFrame) -> dict[str, str]:
    tables = {k: v for k, v in marts.items() if isinstance(v, pl.DataFrame)}
    problems = ledger.charted_problems(tables)
    bad_kpi = kpis.filter(~pl.col("tag").is_in(schema.CHARTABLE_TAGS)).height
    if bad_kpi:
        problems.append(f"kpis: {bad_kpi} row(s) with a tag other than V or C")
    return check(
        "Charted figures are tag V or C",
        not problems,
        "every figure in a chartable mart and every KPI is tag V or C; never a secondary mark, reported "
        "talks or a third-party estimate"
        if not problems
        else "; ".join(problems),
    )


def check_values(facts: Mapping[str, Any]) -> dict[str, str]:
    """Every published number is a record with a qualifier, unit, period, as-of date and source; nothing is NaN."""
    problems = schema.value_problems(facts) + [f"not finite: {p}" for p in schema.non_finite(facts)]
    n = sum(1 for _, node in schema.walk(facts) if isinstance(node, dict) and "value" in node and "qualifier" in node)
    return check(
        "Qualifiers on every published number",
        not problems,
        f"{n} published numbers, each with qualifier, unit, period, as_of and source_url"
        if not problems
        else "; ".join(problems[:5]),
    )


# --- build ----------------------------------------------------------------------------------------------------------
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def dated_rows(kind: str, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict], dict[str, Any] | None]:
    """The ledger needs ``as_of_date`` to be a day; the curation rules let it be "the date or period the fact describes"
    (``FY2025``, ``2025``, ``2020-04``). For a row whose ``as_of_date`` is not a day, the day its source was published
    (``publish_date``, always a day) stands in; its ``period`` column is unchanged and says what the figure covers.
    Returns the rows and a ``warn`` check naming what was replaced, or None if nothing was.

    This is a workaround for a mismatch between ``ledger.py``'s schemas and the curation rules; it belongs in the ledger
    (or in the curated files), and is reported in the hand-over."""
    out, replaced = [], []
    for r in rows:
        if _ISO_DAY.match(str(r.get("as_of_date"))):
            out.append(dict(r))
            continue
        replaced.append(f"{r.get('company')} {r.get('as_of_date')!r}")
        out.append({**r, "as_of_date": r["publish_date"]})
    if not replaced:
        return out, None
    return out, check(
        f"Ledger as-of dates: {kind}",
        False,
        f"{len(replaced)} row(s) give a period, not a day, in as_of_date ({_names(replaced, 4)}); the day their source "
        "was published stands in and their period column is unchanged",
        warn=True,
    )


def _ledger(inputs: Inputs, multiples: dict[str, float], as_of: str | None, checks: list[dict]) -> ledger.Ledger:
    """The ring C tables, or empty ones if the curated rows break the ledger's own rules (a ``fail`` check says so)."""
    rows = {}
    for kind, source in (
        ("private_directory", inputs.private_directory),
        ("private_metrics", inputs.private_metrics),
        ("events", inputs.private_events),
    ):
        rows[kind], note = dated_rows(kind, source)
        if note:
            checks.append(note)
    try:
        return ledger.build(
            rows["private_directory"],
            rows["private_metrics"],
            rows["events"],
            multiples,
            today=inputs.today,
            multiples_as_of=as_of,
        )
    except ledger.LedgerError as exc:
        log.error("ring C ledger refused: %s", exc)
        checks.append(check("Private ledger", False, f"the curated rows break the ledger's rules: {_err(exc)}"))
        empty = ledger.build([], [], [], {}, today=inputs.today, multiples_as_of=as_of)
        return empty._replace(checks=[c for c in empty.checks if c["name"] != "Private ledger"])


def _china_panel(inputs: Inputs, rows: list[dict[str, Any]]) -> tuple[pl.DataFrame, bool]:
    loaded = inputs.china
    if loaded is None or not (loaded.history or loaded.revenue):
        return schema.empty("china_panel"), False
    return V.build(loaded.history, rows, loaded.revenue), True


def _crosscheck(inputs: Inputs) -> etf.Crosscheck:
    """An unreadable holdings file is an empty cross-check, which ``etf.quality_checks`` fails ("no holdings")."""
    return etf.build(inputs.cibr if inputs.cibr is not None else [], POOL, EXCLUDED)


def build(inputs: Inputs, *, draws: int = S.DRAWS) -> Built:
    """Every mart and the facts, from the inputs alone. ``draws`` is the bootstrap and permutation draw count; the
    registered default is ``stats.DRAWS`` and only tests lower it."""
    today = inputs.today
    checks: list[dict[str, str]] = list(inputs.curation_checks)
    for name, why in sorted(inputs.errors.items()):
        if name.startswith("consensus") or name in ("china_history", "cibr"):
            checks.append(check(f"Input: {name}", False, why))

    # the newest usable run, read at its market date, and the one table every other table is built from
    runs = regression.runs_until(inputs.runs, today)
    newest = metrics.latest(runs) if runs is not None else None
    file_day, run_ts, frame = newest if newest else (None, None, None)
    as_of_day = regression.market_date(file_day) if file_day else None
    as_of = as_of_day.isoformat() if as_of_day else None
    listings = listings_frame(metrics.listing_rows(frame, as_of_day or today, inputs.fx_rates))
    rows = listings.to_dicts()

    multiples = {r["ticker"]: r["ev_ntm"] for r in rows if r["ev_ntm"] is not None}
    led = _ledger(inputs, multiples, as_of, checks)
    reg = regression.build(inputs.runs, today, draws=draws)
    board = evaluate.score(inputs.runs, led.tables, today, draws=draws)
    panel, china_loaded = _china_panel(inputs, rows)
    cc = _crosscheck(inputs)

    summary = layer_summary_frame(listings, inputs.private_directory)
    targets = targets_frame(rows, as_of)
    recs = recommendations_frame(rows, as_of)
    scoreboard = schema.validate("scoreboard", pl.DataFrame(board, schema=evaluate.SCORE_DTYPES).drop("evidence"))
    evidence = evaluate.evidence_frame(board)
    dictionary = pl.DataFrame(etf.metric_dictionary(), schema=etf.DICTIONARY_DTYPES)

    facts: dict[str, Any] = {
        "generated_at": inputs.generated_at,
        "slug": SLUG,
        "as_of": as_of,
        "registered": C.REGISTERED,
        "scope": scope_facts(listings, inputs.private_directory, summary),
        "universe": {
            "etfs": [
                {
                    "fund": cc.counts["fund"],
                    "as_of": cc.counts["as_of"],
                    "source_url": cc.counts["source_url"],
                    "counts": {k: v for k, v in cc.counts.items() if k.startswith("n_")},
                    "values": cc.values,
                }
            ]
            if cc.counts["n_holdings"]
            else [],
            "excluded": etf.excluded_table().to_dicts(),
        },
        "regression": regression_facts(reg),
        "ev_sales": ev_sales_facts(rows, as_of),
        "rule_of_40": rule_of_40_facts(rows, as_of),
        "targets": targets_facts(rows, targets, as_of),
        "recommendations": recommendations_facts(recs, as_of),
        "cn": cn_facts(panel, rows),
        "private": led.summary["private"],
        "events": events_summary(led.summary["events"]),
    }
    kpis = kpi_frame(facts)
    facts["kpis"] = kpis.to_dicts()
    facts["scoreboard"] = [
        {**r, "evidence": [{**e, "source_url": REGISTRATION_URL} for e in r["evidence"]]} for r in board
    ]

    marts: dict[str, pl.DataFrame | dict[str, Any]] = {
        "listings": listings,
        "layer_summary": summary,
        "regression": reg,
        "targets": targets,
        "recommendations": recs,
        "china_panel": panel,
        **{name: led.tables[name] for name in ledger.SCHEMAS},
        "etf_crosscheck": cc.table,
        "excluded": etf.excluded_table(),
        "scoreboard": scoreboard,
        "scoreboard_evidence": evidence,
        "metric_dictionary": dictionary,
        "kpis": kpis,
    }
    schema.validate_regression(reg)
    for name, m in marts.items():
        if isinstance(m, pl.DataFrame):
            marts[name] = schema.validate(name, m)
    marts = {name: marts[name] for name in schema.ALL_MARTS}

    facts["counts"] = counts(marts, board)
    facts["coverage"] = coverage(listings, inputs.fx_dates)
    facts["marts"] = {name: f"cyber/{name}.json" for name in marts}
    facts["sources"] = [
        {**s, "url": cc.counts["source_url"] or s["url"]} if s["url"] == CIBR_URL else s for s in SOURCES
    ]
    if facts["coverage"]["fx_as_of"]:  # last, so that the sources the page indexes keep their places
        facts["sources"].append(
            {
                "name": "FRED daily exchange rates (units of a currency per US dollar), the newest rate on file per "
                f"currency, newest {facts['coverage']['fx_as_of']}: enterprise value in dollars",
                "url": FX_URL,
            }
        )

    checks += [
        check_ring_counts(listings),
        check_priced(rows, frame is not None),
        check_price_age(rows, run_ts),
        check_run_age(run_ts, today),
        check_coverage(rows, frame is not None),
        check_multiples_reasoned(rows, frame is not None),
        check_target_units(targets),
        check_target_analysts(targets, frame),
        *check_week_on_week(inputs.runs, today),
        check_eastmoney_ratings(rows, frame is not None),
        check_fx(inputs, listings, as_of),
        check_snapshot_trace(listings, frame),
        check_weekly_series(reg),
        check_regression_point(reg),
        check_regression_population(reg, frame),
        *check_china(panel, rows, today, china_loaded),
        *led.checks,
        *etf.quality_checks(cc),
        check_scoreboard(board),
        check_kpis(kpis),
        check_chartable(marts, kpis),
        check_deal_count(marts["events"], facts["events"]),
    ]
    facts["checks"] = checks
    checks.append(check_values(facts))
    checks.append(check_curator_notes(facts, marts))
    checks.append(check_working_notes(facts, marts))
    blocked = blocked_sources(facts, marts)
    checks.append(
        check(
            "No row from a blocked source host",
            not blocked,
            "no URL in facts or any mart points at a blocked source" if not blocked else "; ".join(blocked[:5]),
        )
    )
    facts["counts"]["checks"] = checks_summary(checks)
    schema.validate_facts(facts)
    return Built(facts, marts)


#: The warnings that wait for time to pass (the first Monday run, a second run to compare with, the day the private
#: ledger is frozen) and say nothing about the data; any other warning flags the data itself.
WAITING_CHECKS = frozenset({"Target mean week on week", "Analyst count week on week", "Weekly series", "Q6 inputs"})


def checks_summary(checks: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """How many checks passed, warn and fail, and of the warnings how many are waiting for time (:data:`WAITING_CHECKS`)
    and how many flag data, so the page states both without counting by hand."""
    status = Counter(c["status"] for c in checks)
    warns = [c["name"] for c in checks if c["status"] == "warn"]
    waiting = [n for n in warns if n in WAITING_CHECKS]
    return {
        "n": len(checks),
        "n_pass": status.get("pass", 0),
        "n_warn": status.get("warn", 0),
        "n_fail": status.get("fail", 0),
        "n_warn_waiting": len(waiting),
        "n_warn_data": len(warns) - len(waiting),
        "warn_waiting": waiting,
        "warn_data": [n for n in warns if n not in WAITING_CHECKS],
    }


def counts(marts: Mapping[str, Any], board: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "marts": {name: m.height for name, m in marts.items() if isinstance(m, pl.DataFrame)},
        "regression_series_points": len(marts["regression"]["series"]),
        "scoreboard": {s: sum(1 for r in board if r["status"] == s) for s in C.QSTATUS},
    }


def coverage(listings: pl.DataFrame, fx_dates: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What this run covers: the regression guard compares it with the facts already published. ``fx_as_of`` is the
    day of the newest exchange rate behind a dollar enterprise value (null when there is none) and ``fx_rate_dates``
    the day of the rate of each currency used."""
    a = listings.filter(pl.col("ring") == "A")
    priced = listings.filter(pl.col("price").is_not_null())
    used = fx_used(listings, fx_dates or {})
    return {
        "n_listings": listings.height,
        "n_ring_a": a.height,
        "n_priced": priced.height,
        "n_with_multiple": listings.filter(pl.col("ev_ntm").is_not_null()).height,
        "n_with_enterprise_value_usd": listings.filter(pl.col("enterprise_value_usd").is_not_null()).height,
        "fx_as_of": max(used.values(), default=None),
        "fx_rate_dates": used,
        "priced": sorted(priced["ticker"].to_list()),
    }


# --- write ----------------------------------------------------------------------------------------------------------
def regression_guard(facts: Mapping[str, Any], facts_path: Path) -> dict[str, str] | None:
    """A failed check when this run would publish fewer than half the priced listings, or the listings, of the facts
    already published. A first run, or an unreadable old file, has nothing to guard."""
    if not facts_path.exists():
        return None
    try:
        prev = json.loads(facts_path.read_text(encoding="utf-8"))["coverage"]
        prev_priced, prev_listings = int(prev.get("n_priced") or 0), int(prev.get("n_listings") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = facts["coverage"]
    if (prev_priced and now["n_priced"] < prev_priced / 2) or (prev_listings and now["n_listings"] < prev_listings / 2):
        return check(
            "Regression guard",
            False,
            f"this run covers {now['n_priced']} priced of {now['n_listings']} "
            f"listings against {prev_priced} of {prev_listings} in the published facts (fewer than half); "
            "nothing was overwritten",
        )
    return None


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def write(
    facts: dict[str, Any],
    marts: Mapping[str, pl.DataFrame | dict[str, Any]],
    facts_path: Path | None = None,
    mart_dir: Path | None = None,
) -> list[Path]:
    """Validate everything, serialise everything, then write. A schema failure, a NaN or a refused guard writes nothing
    (the guard records its failed check in the facts already on disk and returns no paths)."""
    facts_path = facts_path or FACTS_PATH
    mart_dir = mart_dir or MART_DIR
    guard = regression_guard(facts, facts_path)
    if guard:
        log.error("%s", guard["detail"])
        prev = json.loads(facts_path.read_text(encoding="utf-8"))
        prev["checks"] = [c for c in prev.get("checks", []) if c.get("name") != "Regression guard"] + [guard]
        facts_path.write_text(_dump(prev), encoding="utf-8")
        return []
    facts["checks"] = [c for c in facts["checks"] if c["name"] != "Regression guard"] + [
        check(
            "Regression guard",
            True,
            f"{facts['coverage']['n_priced']} priced of {facts['coverage']['n_listings']} listings covered",
        )
    ]

    problems = ledger.charted_problems({k: v for k, v in marts.items() if isinstance(v, pl.DataFrame)})
    if problems:
        raise ValueError("a chartable mart holds a row that may not be charted: " + "; ".join(problems))
    payload: dict[Path, str] = {}
    for name, m in sorted(marts.items()):
        if isinstance(m, pl.DataFrame):
            payload[mart_dir / f"{name}.json"] = _dump(schema.validate(name, m).to_dicts())
        else:
            payload[mart_dir / f"{name}.json"] = _dump(schema.validate_regression(m))
    payload[facts_path] = _dump(schema.validate_facts(facts))
    for path, text in payload.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(payload)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="build and validate, write nothing")
    ap.add_argument(
        "--today", type=date.fromisoformat, default=None, help="build as of this date (default: today, UTC)"
    )
    args = ap.parse_args(argv)
    setup_logging()
    facts, marts = build(load_inputs(args.today))
    if not args.dry_run:
        paths = write(facts, marts)
        log.info("wrote %d file(s)", len(paths))
        if not paths:
            return 1
    for c in facts["checks"]:
        if c["status"] != "pass":
            log.info("check %-50s %-5s %s", c["name"], c["status"], c["detail"])
    for r in facts["scoreboard"]:
        log.info("%s %-11s %s", r["id"], r["status"], r["reason"][:110])
    return 1 if any(c["status"] == "fail" for c in facts["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
