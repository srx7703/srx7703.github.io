"""The ETF cross-check, the "Excluded with reasons" table and the metric dictionary for the cybersecurity page.

Three small tables that let a reader audit the pool and read the page's numbers correctly:

``build``             where every holding of the CIBR ETF sits in the pool: ring A, ring B, ring F, on the excluded
                      list, or missing. A holding that is none of these is a hole in the pool, and the page says so:
                      it is a failed check (``quality_checks``), not a quiet gap. The ETF's published holdings are
                      the operational definition of Q8 membership, so the pool has to account for every line.
``excluded_table``    the companies the page considered and left out (``pipelines/valuation/config.EXCLUDED``, cyber
                      rows) and the ones that left the pool (ring F), each with its reason.
``metric_dictionary`` what each measure on the page is, what it can be compared across and what it must not be.
                      The formulas are the strings pinned in ``config.py`` (imported, never retyped), so a definition
                      cannot drift from the registered text; the few definitions config does not hold are listed in
                      ``tests/test_etf.py`` against section I.

Everything here is a pure function of its arguments (no clock, no network). Nothing depends on a consensus snapshot,
so the three tables are the same with no snapshot, one, or many. ``main`` is a thin runner on the committed files.

Usage::

    uv run python -m pipelines.cyber.etf [--cibr data/reference/cyber/cibr_holdings_2026-10-15.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.cyber import config as C
from pipelines.cyber import reference
from pipelines.cyber.frozen import CIBR_FILE
from pipelines.cyber.pool import POOL, Entry
from pipelines.valuation.config import EXCLUDED

log = logging.getLogger("cyber.etf")

TRACK = "cyber"  # the track key of the cyber rows in pipelines/valuation/config.EXCLUDED
#: Rings a listing can sit in. Ring C is the private directory and has no tickers, so it cannot hold a holding.
LISTING_RINGS = tuple(r for r in C.RINGS if r != "C")
#: How a holding was placed: one status per ring, then the two ways of not being in the pool.
STATUSES = (*(f"ring_{r.lower()}" for r in LISTING_RINGS), "excluded", "missing")
EXCLUDED_KINDS = ("left_out", "ring_f")  # never in the pool by decision | left the pool (delisted or acquired)
CHECK_NAME = "CIBR cross-check complete"

# --- the tables' contracts ---------------------------------------------------------------------------------------
TABLE_KEY = ["ticker"]
TABLE_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,  # Yahoo symbol, as in the pool
    "name": pl.Utf8,  # the name the fund publishes; the pool's name only if the caller gave tickers alone
    "in_pool": pl.Boolean,  # a pool entry exists, in any ring
    "ring": pl.Utf8,  # the pool ring (A, B or F); null if not in the pool
    "layer": pl.Utf8,  # key of config.LAYERS; null if not in the pool
    "purity": pl.Utf8,  # null if not in the pool
    "excluded_reason": pl.Utf8,  # the reason on the excluded list; null if not on it
    "status": pl.Utf8,  # STATUSES: where the holding sits. "missing" is the failed check
    "company_status": pl.Utf8,  # the pool's status of the company (config.STATUS), e.g. pending_acquisition
}


def _status_is_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    """The status has to agree with the columns it summarises, so a table cannot say ring_a with no ring."""
    status, ring, reason, in_pool = pl.col("status"), pl.col("ring"), pl.col("excluded_reason"), pl.col("in_pool")
    ok = (
        pl.when(status.str.starts_with("ring_"))
        .then(in_pool & (status == pl.lit("ring_") + ring.str.to_lowercase()))
        .when(status == "excluded")
        .then(~in_pool & ring.is_null() & reason.is_not_null())
        .otherwise(~in_pool & ring.is_null() & reason.is_null())  # missing
        .fill_null(False)
    )
    return d.lazyframe.select(ok)


def _pool_columns_iff_in_pool(d: pa.PolarsData) -> pl.LazyFrame:
    """Layer, purity and company status come from the pool entry, so they are set exactly when there is one."""
    in_pool = pl.col("in_pool")
    return d.lazyframe.select(
        (
            (in_pool == pl.col("layer").is_not_null())
            & (in_pool == pl.col("purity").is_not_null())
            & (in_pool == pl.col("company_status").is_not_null())
        ).fill_null(False)
    )


TABLE_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_length(min_value=1)),
        "name": pa.Column(str, nullable=True),
        "in_pool": pa.Column(pl.Boolean),
        "ring": pa.Column(str, pa.Check.isin(list(LISTING_RINGS)), nullable=True),
        "layer": pa.Column(str, pa.Check.isin(list(C.LAYERS)), nullable=True),
        "purity": pa.Column(str, pa.Check.isin(C.PURITY), nullable=True),
        "excluded_reason": pa.Column(str, pa.Check.str_length(min_value=1), nullable=True),
        "status": pa.Column(str, pa.Check.isin(list(STATUSES))),
        "company_status": pa.Column(str, pa.Check.isin(C.STATUS), nullable=True),
    },
    unique=TABLE_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_status_is_consistent, error="status disagrees with in_pool, ring and excluded_reason"),
        pa.Check(_pool_columns_iff_in_pool, error="layer, purity and company_status are set exactly for pool rows"),
    ],
)

EXCLUDED_KEY = ["ticker"]
EXCLUDED_DTYPES: dict[str, pl.DataType] = {"ticker": pl.Utf8, "name": pl.Utf8, "reason": pl.Utf8, "kind": pl.Utf8}
EXCLUDED_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_length(min_value=1)),
        "name": pa.Column(str, pa.Check.str_length(min_value=1)),
        "reason": pa.Column(str, pa.Check.str_length(min_value=1)),
        "kind": pa.Column(str, pa.Check.isin(list(EXCLUDED_KINDS))),
    },
    unique=EXCLUDED_KEY,
    strict=True,
    coerce=False,
)

DICTIONARY_FIELDS = ("metric", "formula", "unit", "comparable_across", "not_comparable_across", "source")
DICTIONARY_DTYPES: dict[str, pl.DataType] = {f: pl.Utf8 for f in DICTIONARY_FIELDS}
DICTIONARY_SCHEMA = pa.DataFrameSchema(
    {f: pa.Column(str, pa.Check.str_length(min_value=1)) for f in DICTIONARY_FIELDS},
    unique=["metric"],
    strict=True,
    coerce=False,
)


# --- the holdings file -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str | None = None


@dataclass(frozen=True)
class CibrHoldings:
    """The fund's published holdings with the provenance the page prints beside every count."""

    holdings: tuple[Holding, ...]
    fund: str
    index: str  # the index the fund tracks
    as_of: str  # YYYY-MM-DD, the date of the holdings list
    source_url: str

    @property
    def tickers(self) -> list[str]:
        return [h.ticker for h in self.holdings]


def _text(obj: Mapping[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {key!r} is missing or blank")
    return value.strip()


def load_cibr_records(path: Path = CIBR_FILE) -> CibrHoldings:
    """Read the committed CIBR holdings with names and provenance (``frozen.load_cibr`` returns tickers only).

    Raises ``ValueError`` for a file that is not what the page can print: no date, a date that is not ISO, a source
    that is not https, a holding without a ticker or a name. The same file defines Q8 membership, so a malformed one
    must stop the build and not shrink the cross-check.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    where = path.name
    as_of = _text(raw, "as_of", where)
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"{where}: as_of {as_of!r} is not a YYYY-MM-DD date") from exc
    source_url = _text(raw, "source", where)
    if not source_url.startswith("https://"):
        raise ValueError(f"{where}: source {source_url!r} is not an https link")
    rows = raw.get("holdings")
    if not isinstance(rows, list):
        raise ValueError(f"{where}: 'holdings' is not a list")
    holdings = tuple(
        Holding(_text(r, "ticker", f"{where} holding {i}"), _text(r, "name", f"{where} holding {i}"))
        for i, r in enumerate(rows)
    )
    return CibrHoldings(
        holdings=holdings,
        fund=_text(raw, "fund", where),
        index=_text(raw, "tracks", where),
        as_of=as_of,
        source_url=source_url,
    )


# --- input normalisation -----------------------------------------------------------------------------------------
CibrInput = CibrHoldings | Sequence[str | Mapping[str, Any] | Holding]
PoolInput = Iterable[Entry] | Mapping[str, Entry]


def _holdings(cibr: CibrInput) -> tuple[list[Holding], CibrHoldings | None]:
    """The holdings as ``Holding`` rows, and the provenance record if the caller gave one."""
    if isinstance(cibr, CibrHoldings):
        return list(cibr.holdings), cibr
    if isinstance(cibr, str):
        raise TypeError("CIBR holdings are a list of tickers or records, not one string")
    out: list[Holding] = []
    for item in cibr:
        if isinstance(item, Holding):
            out.append(item)
        elif isinstance(item, str):
            out.append(Holding(item))
        elif isinstance(item, Mapping) and isinstance(item.get("ticker"), str):
            out.append(Holding(item["ticker"], item.get("name")))
        else:
            raise TypeError(f"a CIBR holding is a ticker, a Holding or a record with a ticker, not {item!r}")
    blank = [h for h in out if not h.ticker.strip()]
    if blank:
        raise ValueError("a CIBR holding has a blank ticker")
    return out, None


def _entries(pool: PoolInput) -> dict[str, Entry]:
    entries = list(pool.values()) if isinstance(pool, Mapping) else list(pool)
    by = {e.ticker: e for e in entries}
    if len(by) != len(entries):
        dupes = sorted(t for t, n in Counter(e.ticker for e in entries).items() if n > 1)
        raise ValueError(f"duplicate ticker in the pool: {dupes}")
    bad = sorted(f"{e.ticker} (ring {e.ring})" for e in entries if e.ring not in LISTING_RINGS)
    if bad:
        raise ValueError(f"pool entries outside the listing rings {LISTING_RINGS}: {bad}")
    return by


def _cyber_excluded(excluded: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The cyber rows of the excluded list by ticker. Other tracks' rows are not this page's business."""
    rows = [r for r in excluded if r.get("track") == TRACK]
    for r in rows:
        if not str(r.get("ticker") or "").strip() or not str(r.get("reason") or "").strip():
            raise ValueError(f"an excluded row needs a ticker and a reason: {dict(r)!r}")
    by = {r["ticker"]: r for r in rows}
    if len(by) != len(rows):
        dupes = sorted(t for t, n in Counter(r["ticker"] for r in rows).items() if n > 1)
        raise ValueError(f"duplicate ticker in the excluded list: {dupes}")
    return by


# --- the cross-check ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Crosscheck:
    """``table`` is the ``etf_crosscheck`` mart; ``counts`` the flat numbers and provenance for facts; ``values`` the
    same counts in the facts shape (``value``, ``qualifier``, ``unit``, ``period``, ``as_of``, ``source_url``)."""

    table: pl.DataFrame
    counts: dict[str, Any]
    values: dict[str, dict[str, Any]]

    @property
    def missing(self) -> list[str]:
        return self.table.filter(pl.col("status") == "missing")["ticker"].to_list()


_COUNT_UNITS = {
    "n_holdings": "holdings",
    "n_in_ring_a": "holdings",
    "n_in_ring_b": "holdings",
    "n_in_ring_f": "holdings",
    "n_excluded": "holdings",
    "n_missing": "holdings",
    "n_ring_a": "listings",
    "n_ring_a_outside_cibr": "listings",
    "n_excluded_in_pool": "tickers",
}


def _placement(h: Holding, entry: Entry | None, exc: Mapping[str, Any] | None) -> dict[str, Any]:
    name = h.name or (entry.name if entry else None) or (exc.get("name") if exc else None)
    if entry is not None:
        status = f"ring_{entry.ring.lower()}"
    else:
        status = "excluded" if exc is not None else "missing"
    return {
        "ticker": h.ticker,
        "name": name,
        "in_pool": entry is not None,
        "ring": entry.ring if entry else None,
        "layer": entry.layer if entry else None,
        "purity": entry.purity if entry else None,
        "excluded_reason": str(exc["reason"]).strip() if exc is not None else None,
        "status": status,
        "company_status": entry.status if entry else None,
    }


def build(cibr: CibrInput, pool: PoolInput, excluded: Iterable[Mapping[str, Any]]) -> Crosscheck:
    """Place every CIBR holding in the pool, on the excluded list, or nowhere.

    ``cibr`` is what ``load_cibr_records()`` returns (names and provenance travel with it), or just tickers such as
    ``frozen.load_cibr()`` (then ``as_of`` and ``source_url`` in the counts are None and a name comes from the pool).
    ``pool`` is ``pool.POOL`` or ``pool.BY_TICKER``; ``excluded`` is ``pipelines.valuation.config.EXCLUDED`` (its
    non-cyber rows are ignored).

    A ticker in both the pool and the excluded list stays in the pool (the pool is the live truth) and keeps its
    excluded reason in the table; ``n_excluded_in_pool`` counts such tickers so the contradiction is a failed check
    and not a silent choice. An empty holdings list gives an empty table, zero counts and a failed check.
    """
    holdings, record = _holdings(cibr)
    by_pool, by_excluded = _entries(pool), _cyber_excluded(excluded)
    rows = [_placement(h, by_pool.get(h.ticker), by_excluded.get(h.ticker)) for h in holdings]
    table = TABLE_SCHEMA.validate(pl.DataFrame(rows, schema=TABLE_DTYPES))

    held = {h.ticker for h in holdings}
    ring_a = [t for t, e in by_pool.items() if e.ring == "A"]
    by_status = Counter(r["status"] for r in rows)
    counts: dict[str, Any] = {
        "fund": record.fund if record else None,
        "as_of": record.as_of if record else None,
        "source_url": record.source_url if record else None,
        "n_holdings": len(rows),
        "n_in_ring_a": by_status["ring_a"],
        "n_in_ring_b": by_status["ring_b"],
        "n_in_ring_f": by_status["ring_f"],
        "n_excluded": by_status["excluded"],
        "n_missing": by_status["missing"],
        "n_ring_a": len(ring_a),
        "n_ring_a_outside_cibr": sum(t not in held for t in ring_a),  # the reverse view: a count, not a list
        "n_excluded_in_pool": len(set(by_excluded) & set(by_pool)),
    }
    values = {
        key: {
            "value": counts[key],
            "qualifier": "=",
            "unit": unit,
            "period": counts["as_of"],
            "as_of": counts["as_of"],
            "source_url": counts["source_url"],
        }
        for key, unit in _COUNT_UNITS.items()
    }
    return Crosscheck(table=table, counts=counts, values=values)


def quality_checks(cc: Crosscheck) -> list[dict[str, str]]:
    """The checks ``facts.checks`` records for this table, in ``pipelines.common.checks`` shape.

    The cross-check is complete when there is at least one holding and none is missing; the excluded list has to be
    disjoint from the pool, or the page would list a company as both in and out.
    """
    n = cc.counts
    split = (
        f"{n['n_holdings']} holdings: {n['n_in_ring_a']} in ring A, {n['n_in_ring_b']} in ring B, "
        f"{n['n_in_ring_f']} in ring F, {n['n_excluded']} excluded, {n['n_missing']} missing"
    )
    if cc.missing:
        split += f" ({', '.join(cc.missing)})"
    overlap = n["n_excluded_in_pool"]
    return [
        check(CHECK_NAME, n["n_holdings"] > 0 and n["n_missing"] == 0, split),
        check(
            "Excluded list disjoint from the pool",
            overlap == 0,
            f"{overlap} ticker(s) on both the excluded list and in the pool",
        ),
    ]


# --- excluded with reasons ---------------------------------------------------------------------------------------
def _ring_f_reason(e: Entry) -> str:
    head = " ".join(p for p in (e.status, e.status_date) if p)
    return f"{head}: {e.note}" if e.note else head


def excluded_table(excluded: Iterable[Mapping[str, Any]] | None = None, pool: PoolInput | None = None) -> pl.DataFrame:
    """Everything the page leaves out, with the reason: the considered-and-left-out cyber rows of the excluded list
    (kind ``left_out``) and the pool's ring F entries (kind ``ring_f``: delisted or acquired, in no test).

    Defaults to the live ``EXCLUDED`` and ``POOL``. Sorted by kind then ticker so a republish diffs cleanly.
    """
    left_out = _cyber_excluded(EXCLUDED if excluded is None else excluded)
    entries = _entries(POOL if pool is None else pool)
    rows = [
        {"ticker": t, "name": str(r.get("name") or "").strip(), "reason": str(r["reason"]).strip(), "kind": "left_out"}
        for t, r in left_out.items()
    ]
    rows += [
        {"ticker": e.ticker, "name": e.name, "reason": _ring_f_reason(e), "kind": "ring_f"}
        for e in entries.values()
        if e.ring == "F"
    ]
    df = pl.DataFrame(rows, schema=EXCLUDED_DTYPES).sort(["kind", "ticker"])
    return EXCLUDED_SCHEMA.validate(df)


# --- the metric dictionary ---------------------------------------------------------------------------------------
#: The names of the ``config`` constants whose text the dictionary embeds verbatim. ``tests/test_etf.py`` checks that
#: each one appears in a formula, so a definition cannot be retyped here and drift from the pinned one.
CONFIG_DEFINITION_NAMES = (
    "EV_SOURCE",
    "NTM_WEIGHT",
    "FY0_END",
    "GROWTH",
    "FCF_MARGIN",
    "RULE_OF_40",
    "TRAILING_SOURCE",
    "UPSIDE",
    "DISPERSION",
    "DISCOUNT_BAND",
)

# Definitions section I registers in prose and config.py holds no string for. Each is a verbatim fragment of the
# section, and tests/test_etf.py fails if the section stops containing it.
PURITY_DEFINITIONS = {
    "high": (
        "cybersecurity is the main business (at least 70% of revenue where disclosed; otherwise the company "
        "describes itself as a security company with no other reportable segment)"
    ),
    "main": "an identifiable security business of 20–70% of revenue, or one of several stated main lines",
    "partial": "a small exposure, kept because an ETF or a common classification lists the company",
}
LAYER_RULE = "A company sits in the layer of its largest cybersecurity revenue line and appears once on the layer map"
HEADLINE_RULE = "what a round or a deal reported"
assert set(PURITY_DEFINITIONS) == set(C.PURITY), "a purity class has no definition (or a definition has no class)"

_YAHOO = "Yahoo Finance (parsed fields in the weekly consensus snapshot)"
_EAST_MONEY = "East Money (A-share revenue consensus by calendar year; valuation history)"


def _discounts() -> str:
    return ", ".join(f"{year} {pct}" for year, pct in C.DISCOUNT_BY_ROUND_YEAR.items())


def _comparables() -> str:
    return "; ".join(f"{layer}: {', '.join(tickers)}" for layer, tickers in C.COMPARABLES_BY_LAYER.items())


def _layers() -> str:
    return "; ".join(f"{v['code']} {v['name']}" for v in C.LAYERS.values())


def _dictionary_rows() -> list[dict[str, str]]:
    codes = [v["code"] for v in C.LAYERS.values()]
    thin = f"fewer than {C.THIN_COVERAGE_BELOW} analysts"
    never_charted = ", ".join(C.NEVER_CHARTED_KINDS)
    rows = [
        (
            "EV/NTM revenue",
            f"enterprise value / NTM revenue; enterprise value is {C.EV_SOURCE}, stated in the quote currency, and the "
            "multiple is computed only where that currency and the revenue consensus's currency agree",
            "multiple (x), enterprise value per unit of next-twelve-month revenue",
            "listings in the same snapshot, whatever their currency (the ratio is unit-free); one listing across weeks",
            "a listing whose enterprise value and revenue are in different currencies (no multiple is shown); "
            "A-shares against the rest without saying their revenue consensus is East Money's and the others' "
            "Yahoo's; private valuations, which are read only through the two intervals",
            f"{_YAHOO}; {_EAST_MONEY}",
        ),
        (
            "NTM revenue",
            "w x fiscal-year-0 revenue consensus + (1 - w) x fiscal-year-+1 revenue consensus, where "
            f"{C.NTM_WEIGHT}; {C.FY0_END}; for A-shares East Money's consensus by calendar year; a year with no "
            "analysts behind it is a missing value",
            "revenue in the currency the company reports in",
            "one listing across weeks; across listings only as the denominator of a multiple or a growth rate",
            "currencies as an amount; a single fiscal year's consensus, because it blends two",
            f"{_YAHOO}; {_EAST_MONEY}",
        ),
        (
            "growth",
            f"{C.GROWTH}; forward over trailing, so it spans more than twelve months for a company a quarter past "
            f"its last report; {C.TRAILING_SOURCE}",
            "fraction of trailing-twelve-month revenue",
            "listings in the same snapshot, A-shares included",
            "a fiscal-year growth rate: the window runs from the trailing twelve months to the next twelve, and its "
            "length depends on how long ago the company last reported",
            f"{_YAHOO}; {_EAST_MONEY}",
        ),
        (
            "FCF margin",
            f"{C.FCF_MARGIN}; {C.TRAILING_SOURCE}",
            "fraction of trailing-twelve-month revenue",
            "listings in the same snapshot, A-shares included",
            "a forecast margin (it is trailing); a free cash flow figure the company defines for itself, because the "
            "figure is the provider's as served",
            _YAHOO,
        ),
        (
            "Rule of 40",
            f"{C.RULE_OF_40}; the registered cut in Q3 is {C.Q3['cut']}",
            "points (growth and FCF margin in per cent, summed)",
            "listings in the same snapshot with both growth and FCF margin",
            "a company missing either input (it is dropped from the panel and the drop is counted, never filled in); "
            "the mix of growth and cash flow, which the sum hides",
            _YAHOO,
        ),
        (
            "target upside",
            f"{C.UPSIDE}, in the quote currency (pence divided by 100 first)",
            "fraction of the price",
            "listings outside China priced in the same snapshot",
            f"a listing with {thin}, which is flagged thin and drawn faded; A-share targets, which rest on few "
            "analysts and which no registered item uses; a forecast of the return (Q4 tests whether upside predicts "
            "it)",
            _YAHOO,
        ),
        (
            "dispersion",
            C.DISPERSION,
            "fraction of the mean target price",
            "listings outside China priced in the same snapshot",
            f"a listing with {thin}, flagged thin, where the range comes from very few forecasts; the number of "
            "target analysts with the number of EPS or revenue analysts, which are counted separately",
            _YAHOO,
        ),
        (
            "PS_TTM (A-share)",
            "total market capitalisation / trailing-twelve-month revenue, as East Money's valuation history serves it "
            "by day",
            "multiple (x)",
            "A-share listings on the same date; one A-share listing across dates",
            "EV/NTM revenue (market capitalisation and not enterprise value, trailing revenue and not forward); "
            "listings outside China, which have no such series",
            "East Money (valuation history of the A-share ring A and ring B listings)",
        ),
        (
            "PS percentile",
            "share of the listing's own trading days since 2018 on which PS_TTM was at or below its latest value (ties "
            "and the latest day included), computed from the daily series and not read from East Money's own "
            "percentile field",
            "fraction above 0 and up to 1 (1 means the highest PS_TTM day of its own history)",
            "one listing across time; listings against each other only as where each one stands in its own past",
            "a verdict on cost against peers; a re-rating (a high percentile can come from falling revenue and not "
            "from a rising price, which is why revenue growth is printed beside it); a listing with a short history "
            "(no percentile is published below about one trading year of days)",
            "East Money (valuation history of the A-share ring A and ring B listings)",
        ),
        (
            "headline valuation",
            f"{HEADLINE_RULE}, as the source states it, with its qualifier ({', '.join(reference.QUALIFIERS)}), basis "
            "and as-of date; never adjusted",
            "currency the source states, in whole units",
            "one company over time (the year of the round travels with the figure)",
            "a listed company's EV/NTM revenue (a round price is not an enterprise value on forward revenue); "
            f"figures of kind {never_charted}, which are shown in a table and never charted; an old round read as "
            "current (the vintage year and the stale mark say how old it is)",
            "company announcements and regulatory filings curated in ring C (public sources only)",
        ),
        (
            "discount interval",
            f"headline x (1 + the discount for the year of the round), widened by {C.DISCOUNT_BAND}; discount by year "
            f"of the round: {_discounts()}; a round in a year not in the table is not scored",
            "the currency of the headline",
            "other private companies' discount intervals, and the public-comparable interval of the same company",
            "a market price or a fair value: the discounts are an assumption and not data, and the page says so",
            "the registered discount table (section I of the evaluation plan) applied to the headline valuation",
        ),
        (
            "public-comparable interval",
            "lowest to highest EV/NTM revenue among the public comparables of the company's layer, multiplied by "
            f"company-stated ARR as given; comparables by layer: {_comparables()}",
            "the currency of the ARR",
            "the discount interval of the same company",
            "a company in a layer with no comparables (shown, not scored); a like-for-like multiple: ARR is a "
            "trailing run-rate and EV/NTM revenue is forward, which favours 'above' in Q6",
            f"{_YAHOO} for the multiples; company-stated ARR from curated ring C rows",
        ),
        (
            "cyber_segment_share",
            "disclosed cybersecurity revenue / total revenue, from the company's segment table; null today for every "
            "listing because no segment table has been read and the pool holds no such field",
            "fraction of revenue (null today)",
            "nothing yet: it is null for every listing",
            "purity, which is a coarse class and not a measured share; any figure the page prints as a security "
            "share, until this field is filled",
            "company filings (annual report segment tables), not yet read",
        ),
        (
            "purity",
            "; ".join(f"{k}: {PURITY_DEFINITIONS[k]}" for k in C.PURITY),
            f"class ({', '.join(C.PURITY)})",
            "companies on the page's tables and filters; only 'high' enters the Q1, Q2, Q3, Q7 and Q9 populations",
            "a measured share of revenue (purity is stored separately from the disclosed security share, which is "
            "cyber_segment_share and is null today)",
            "the pool as registered in section I of the evaluation plan (pipelines/cyber/pool.py)",
        ),
        (
            "layer",
            f"{LAYER_RULE}; other lines go in also_in; one of {len(C.LAYERS)} layers: {_layers()}; the page labels a "
            "row unverified until its segment table or company description has been read",
            f"layer code {min(codes)} to {max(codes)} and name",
            "companies in the same snapshot grouped by layer; in Q1 a layer with fewer than "
            f"{C.Q1['merge_below']} panel members is merged into 'other'",
            "a platform company's other lines (kept in also_in and used in no test); the layer schemes of other "
            "classifications, which this tree does not reproduce",
            "the layer tree and pool registered in section I of the evaluation plan (pipelines/cyber/pool.py)",
        ),
    ]
    return [dict(zip(DICTIONARY_FIELDS, row, strict=True)) for row in rows]


def metric_dictionary() -> list[dict[str, str]]:
    """The page's metric dictionary: one record per measure, fixed order.

    Keys: ``metric``, ``formula``, ``unit``, ``comparable_across``, ``not_comparable_across``, ``source``. Every
    formula that has a pinned definition in ``config.py`` contains that string verbatim (``CONFIG_DEFINITION_NAMES``).
    ``cyber_segment_share`` is in the dictionary and says it is null today.
    """
    df = pl.DataFrame(_dictionary_rows(), schema=DICTIONARY_DTYPES)
    return DICTIONARY_SCHEMA.validate(df).to_dicts()


# --- runner ------------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Cross-check the committed CIBR holdings against the live pool and print the result; writes nothing."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cibr", type=Path, default=CIBR_FILE, help="CIBR holdings file (default: the committed one)")
    args = ap.parse_args(argv)
    setup_logging()
    cc = build(load_cibr_records(args.cibr), POOL, EXCLUDED)
    for ticker in cc.missing:
        log.error("CIBR holds %s and the pool and the excluded list do not", ticker)
    results = quality_checks(cc)
    for c in results:
        log.info("%-40s %-5s %s", c["name"], c["status"], c["detail"])
    log.info("ring A listings CIBR does not hold: %d of %d", cc.counts["n_ring_a_outside_cibr"], cc.counts["n_ring_a"])
    return 1 if any(c["status"] == "fail" for c in results) else 0


if __name__ == "__main__":
    sys.exit(main())
