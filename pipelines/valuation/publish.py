"""Turn the valuation snapshots into marts and the per-track facts files the pages are templated from.

Usage:
    uv run python -m pipelines.valuation.publish [--track optical|ssb|all]

Writes, per track:
    data/marts/valuation/<track>_companies.json   one row per listing, every derived metric
    data/marts/valuation/<track>_revisions.json   calendar-year consensus as of each vintage date
    data/marts/valuation/<track>_share.json       basis A members and basis B citations
    data/facts/valuation_<track>.json             the numbers the page prints

No number reaches a page except through the facts file, and every ratio here carries the reason it is
missing when it is missing, so a blank on the page can always be explained.

Three rules govern what may be written, because this is the last step before a public page:

**Nothing is published that has not been validated.** The marts and facts are the layer the site
actually reads, so they get a pandera contract of their own (``COMPANY_SCHEMA``, ``MEMBER_SCHEMA``,
``FACTS_SCHEMA``) exactly as ``pipelines/sec/transform.py`` does for its benchmark table. A violation
raises and that track writes nothing.

**A worse file never overwrites a better one.** Every fetch module already refuses to blank a good
snapshot; publish refuses to blank a good *page*. If the facts on disk describe a priced pool and this
run does not, the run is recorded, logged as an error and exited non-zero with the previous facts left
exactly where they were — so the page keeps its honest "data as of" stamp instead of going blank while
CI is still deciding how it feels about the job.

**One track never loses another's data.** A failure inside one track is caught, recorded and reported;
the other track still publishes.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import date

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR, append_jsonl, utc_now, write_json
from pipelines.valuation import read
from pipelines.valuation.config import COMPANIES, EXCLUDED, TRACKS, Company
from pipelines.valuation.fundamentals import ttm_by_ticker
from pipelines.valuation.metrics import (
    NM_LOSS,
    NM_NO_EARNINGS,
    company_row,
    forward_pe,
    sources_with_rows,
)
from pipelines.valuation.schema import ESTIMATE_KEY, FUNDAMENTAL_KEY, PRICE_KEY
from pipelines.valuation.share import (
    BASIS_SEGMENT,
    BASIS_TOTAL,
    EXCLUSION_CLAUSES,
    cited_share,
    computed_share,
    load_reference,
    shipments_and_capacity,
    withheld_quotes,
)

log = logging.getLogger("valuation.publish")

YEARS = (2026, 2027)
Y0, Y1 = YEARS
MART_DIR = MARTS_DIR / "valuation"

# How far back a union read may reach for a listing that this run did not refresh. Prices run daily
# and estimates weekly, so these are "about a week" and "about three weeks" respectively: long enough
# that one rate-limited run does not empty a column, short enough that nothing silently ancient is
# printed as if it were today's.
PRICE_MAX_AGE_DAYS = 8
ESTIMATE_MAX_AGE_DAYS = 24
STALE_PRICE_DAYS = 4  # a weekend plus a holiday; beyond this a price is worth flagging

# A secondary listing's quoted market cap has to imply the same share count as its primary line, or
# the two are not sizing the same issuer and a trailing multiple built from it is an artefact.
SHARE_BASE_TOLERANCE = 0.02

# The publish guard. A run may lose a few listings to a dead source; losing a fifth of the priced pool
# is a broken run, not news about the market.
MIN_PRICED_RATIO = 0.8

SOURCES = [
    {"name": "Yahoo Finance (prices and analyst consensus)", "url": "https://finance.yahoo.com/"},
    {"name": "East Money analyst forecasts (A-shares)", "url": "https://emweb.securities.eastmoney.com/"},
    {"name": "SEC EDGAR XBRL companyfacts", "url": "https://www.sec.gov/edgar/sec-api-documentation"},
    {"name": "FRED daily exchange rates", "url": "https://fred.stlouisfed.org/categories/94"},
]

# --- contracts on the published layer -------------------------------------------------------------

COMPANY_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "track": pl.Utf8,
    "purity": pl.Utf8,
    "path": pl.Utf8,
    "market": pl.Utf8,
    "fy_end_month": pl.Int64,
    "segment_line": pl.Utf8,
    "price": pl.Float64,
    "price_currency": pl.Utf8,
    "market_cap": pl.Float64,
    "reporting_currency": pl.Utf8,
    "ttm_net_income": pl.Float64,
    "ttm_revenue": pl.Float64,
    "ttm_basis": pl.Utf8,
    "trailing_pe": pl.Float64,
    "trailing_pe_nm": pl.Utf8,
    f"eps_{Y0}": pl.Float64,
    f"eps_{Y1}": pl.Float64,
    f"fwd_pe_{Y0}": pl.Float64,
    f"fwd_pe_{Y0}_nm": pl.Utf8,
    f"fwd_pe_{Y1}": pl.Float64,
    f"fwd_pe_{Y1}_nm": pl.Utf8,
    "growth": pl.Float64,
    "n_analysts": pl.Int64,
    "coverage": pl.Utf8,
    "dispersion": pl.Float64,
    "actual_weight": pl.Float64,
    "price_as_of": pl.Utf8,
    "price_age_days": pl.Int64,
}


def _is_finite(data) -> pl.LazyFrame:
    return data.lazyframe.select(pl.col(data.key).is_finite() | pl.col(data.key).is_null())


# Polars orders NaN above every number, so `Check.gt(0)` waves one through; and a NaN or an infinity
# reaches the browser as invalid JSON, which takes the whole page down rather than one cell. Every
# float column is checked for it explicitly.
FINITE = pa.Check(_is_finite, name="finite", error="NaN or infinity, which is not a number a page can print")

_pos = pa.Column(float, [pa.Check.gt(0.0), FINITE], nullable=True)


def _bounded(lo: float, hi: float, *, nullable: bool = True) -> pa.Column:
    return pa.Column(float, [pa.Check.in_range(lo, hi), FINITE], nullable=nullable)


def _at_least(lo: float) -> pa.Column:
    return pa.Column(float, [pa.Check.ge(lo), FINITE], nullable=True)


# Every multiple is strictly positive or null with a reason: the NM discipline, enforced rather than
# described.
COMPANY_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "name": pa.Column(str),
        "track": pa.Column(str, pa.Check.isin(list(TRACKS))),
        "purity": pa.Column(str, pa.Check.isin(["high", "main", "partial"])),
        "fy_end_month": pa.Column(int, pa.Check.in_range(1, 12)),
        "price": _pos,
        "market_cap": _pos,
        "trailing_pe": _pos,
        f"fwd_pe_{Y0}": _pos,
        f"fwd_pe_{Y1}": _pos,
        "growth": _bounded(-1.0, 50.0),
        "n_analysts": pa.Column(int, pa.Check.ge(0), nullable=True),
        "coverage": pa.Column(str, pa.Check.isin(["thin", "covered", "unknown"])),
        "dispersion": _at_least(0.0),
        "actual_weight": _bounded(0.0, 1.0),
        "price_age_days": pa.Column(int, pa.Check.ge(0), nullable=True),
    },
    unique=["ticker"],
    coerce=False,
)

MEMBER_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "purity": pl.Utf8,
    "revenue_usd": pl.Float64,
    "revenue_native": pl.Float64,
    "currency": pl.Utf8,
    "basis": pl.Utf8,
    "share": pl.Float64,
}

MEMBER_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "name": pa.Column(str),
        "revenue_usd": pa.Column(float, [pa.Check.gt(0.0), FINITE]),
        "currency": pa.Column(str),
        "basis": pa.Column(str, pa.Check.isin([BASIS_SEGMENT, BASIS_TOTAL])),
        "share": _bounded(0.0, 1.0, nullable=False),
    },
    unique=["ticker"],
    coerce=False,
)

FACTS_DTYPES: dict[str, pl.DataType] = {
    "generated_at": pl.Utf8,
    "track": pl.Utf8,
    "n_listings": pl.Int64,
    "n_issuers": pl.Int64,
    "n_priced": pl.Int64,
    "n_with_consensus": pl.Int64,
    "n_thin": pl.Int64,
    "n_nm": pl.Int64,
    "n_stale_prices": pl.Int64,
    "n_quoted": pl.Int64,
    "median_fwd_pe_this": pl.Float64,
    "median_fwd_pe_next": pl.Float64,
    "p25_fwd_pe_this": pl.Float64,
    "p75_fwd_pe_this": pl.Float64,
    "iqr_fwd_pe_this": pl.Float64,
    "median_growth": pl.Float64,
    "median_trailing_pe": pl.Float64,
    "n_members": pl.Int64,
    "n_excluded": pl.Int64,
    "pool_revenue_usd": pl.Float64,
    "top3_share": pl.Float64,
    "top5_share": pl.Float64,
    "hhi": pl.Float64,
}

FACTS_SCHEMA = pa.DataFrameSchema(
    {
        "generated_at": pa.Column(str),
        "track": pa.Column(str, pa.Check.isin(list(TRACKS))),
        "n_listings": pa.Column(int, pa.Check.gt(0)),
        "n_issuers": pa.Column(int, pa.Check.gt(0)),
        "n_priced": pa.Column(int, pa.Check.ge(0)),
        "n_with_consensus": pa.Column(int, pa.Check.ge(0)),
        "n_thin": pa.Column(int, pa.Check.ge(0)),
        "n_nm": pa.Column(int, pa.Check.ge(0)),
        "n_stale_prices": pa.Column(int, pa.Check.ge(0)),
        "n_quoted": pa.Column(int, pa.Check.ge(0)),
        "median_fwd_pe_this": _pos,
        "median_fwd_pe_next": _pos,
        "p25_fwd_pe_this": _pos,
        "p75_fwd_pe_this": _pos,
        "iqr_fwd_pe_this": _at_least(0.0),
        "median_growth": _bounded(-1.0, 50.0),
        "median_trailing_pe": _pos,
        "n_members": pa.Column(int, pa.Check.ge(0)),
        "n_excluded": pa.Column(int, pa.Check.ge(0)),
        "pool_revenue_usd": _at_least(0.0),
        "top3_share": _bounded(0.0, 1.0),
        "top5_share": _bounded(0.0, 1.0),
        "hhi": _bounded(0.0, 10000.1),
    },
    coerce=False,
)

# Keys the pages read that are not scalars, so they cannot be expressed as a frame column. Their
# absence is still a broken page, so their absence is still an error.
REQUIRED_FACTS_KEYS = (
    "label", "years", "as_of_price", "as_of_estimates", "as_of_fx", "cheapest", "priciest",
    "nm_reasons", "share", "cited_share", "dual_listings", "excluded_from_pool", "checks", "sources",
)


def _frame(rows: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    """A frame with exactly the contract's columns and dtypes, so an all-null column still types."""
    return pl.DataFrame([{c: r.get(c) for c in dtypes} for r in rows], schema=dtypes)


def validate(facts: dict, rows: list[dict], share: dict) -> None:
    """The contract on everything this module is about to write. Raises rather than warning."""
    missing = [k for k in REQUIRED_FACTS_KEYS if k not in facts]
    if missing:
        raise ValueError(f"facts for {facts.get('track')} are missing keys the pages read: {', '.join(missing)}")
    COMPANY_SCHEMA.validate(_frame(rows, COMPANY_DTYPES))
    MEMBER_SCHEMA.validate(_frame(share["members"], MEMBER_DTYPES))
    flat = {k: facts.get(k) for k in FACTS_DTYPES}
    flat.update({k: (facts.get("share") or {}).get(k) for k in
                 ("n_members", "n_excluded", "pool_revenue_usd", "top3_share", "top5_share", "hhi")})
    FACTS_SCHEMA.validate(pl.DataFrame([flat], schema=FACTS_DTYPES))


# --- the publish guard ----------------------------------------------------------------------------


def previous_facts(track: str) -> dict | None:
    """The facts file currently on disk, which is what the live page is built from."""
    path = FACTS_DIR / f"valuation_{track}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cannot read the published facts at %s (%s); treating this as a first publish", path, exc)
        return None


def _counts(facts: dict | None) -> dict[str, int]:
    facts = facts or {}
    return {
        "n_priced": facts.get("n_priced") or 0,
        "n_with_consensus": facts.get("n_with_consensus") or 0,
        "n_members": (facts.get("share") or {}).get("n_members") or 0,
    }


def blocking_regression(new: dict, old: dict | None) -> str | None:
    """Why this build must not overwrite the published one, or None if it may.

    The test is deliberately about *material loss*, not about change: a run that finds two fewer
    priced listings has found news, a run that finds none has found a broken source. Only the second
    kind is refused, because the first kind is what the page is for.
    """
    if old is None:
        return None
    was, now = _counts(old), _counts(new)
    if was["n_priced"] and not now["n_priced"]:
        return f"no listing is priced, where the published facts have {was['n_priced']}"
    if was["n_priced"] and now["n_priced"] < MIN_PRICED_RATIO * was["n_priced"]:
        return (f"only {now['n_priced']} of the {was['n_priced']} listings in the published facts are priced "
                f"({now['n_priced'] / was['n_priced']:.0%}, floor {MIN_PRICED_RATIO:.0%})")
    if was["n_with_consensus"] and not now["n_with_consensus"]:
        return f"no listing has a calendar-{Y0} consensus, where the published facts have {was['n_with_consensus']}"
    if was["n_members"] and not now["n_members"]:
        return f"the computed share pool is empty, where the published facts have {was['n_members']} members"
    return None


# --- derived rows ---------------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def _quantile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def latest_rates(fx_rows: list[dict]) -> dict[str, float]:
    """Newest non-null rate per currency."""
    best: dict[str, tuple[str, float]] = {}
    for r in fx_rows:
        ccy, d, v = r.get("currency"), r.get("date"), r.get("per_usd")
        if not ccy or not d or v is None:
            continue
        if ccy not in best or d > best[ccy][0]:
            best[ccy] = (d, float(v))
    rates = {c: v for c, (_, v) in best.items()}
    rates.setdefault("USD", 1.0)
    return rates


def _age_days(snapshot_ts: str | None, as_of: date) -> int | None:
    """How many days before this run the figure was captured. Reading unions means saying so."""
    if not snapshot_ts:
        return None
    try:
        return max((as_of - date.fromisoformat(str(snapshot_ts)[:10])).days, 0)
    except ValueError:
        return None


def build_rows(companies: list[Company], data: dict, as_of: date) -> list[dict]:
    # Which estimate sources produced anything at all this run. Without it, a throttled Yahoo makes the
    # page tell readers that analysts do not cover Broadcom or Cisco, which is a statement about
    # analysts rather than about our own fetch. With it, those listings say the source returned nothing.
    sources_ran = sources_with_rows(data["estimates"])
    rows: list[dict] = []
    for c in companies:
        est = [r for r in data["estimates"].get(c.ticker, []) if r.get("eps_avg") is not None]
        vint = data["vintages"].get(c.ticker, [])
        ttm = data["ttm"].get(c.primary)
        price_rows = data["prices"].get(c.ticker) or []
        price_row = price_rows[0] if price_rows else None
        row = company_row(
            c,
            price_row=price_row,
            estimate_rows=est,
            vintage_rows=vint,
            ttm=ttm,
            rates=data["rates"],
            years=YEARS,
            as_of=as_of,
            sources_ran=sources_ran,
        )
        # Reading a union of recent runs means a listing can carry a figure from an earlier one. That
        # is the point — a week-old close beats a hole — but it has to be visible, so every row says
        # when its price and its consensus were captured.
        row["price_as_of"] = (price_row or {}).get("snapshot_ts")
        row["price_age_days"] = _age_days(row["price_as_of"], as_of)
        row["estimates_as_of"] = max((r.get("snapshot_ts") for r in est if r.get("snapshot_ts")), default=None)
        rows.append(row)
    return rows


def _implied_shares(row: dict) -> float | None:
    """Share count implied by the quoted market cap and the quoted price, in that line's currency."""
    cap, price = row.get("market_cap"), row.get("price")
    if not cap or not price:
        return None
    return cap / price


def suppress_share_base_artefacts(rows: list[dict], companies: list[Company]) -> list[dict]:
    """Refuse to publish a secondary listing's trailing multiple when its market cap is not the issuer's.

    Trailing PE here is market cap over attributable profit, and a dual listing's two lines share the
    profit. So the gap between the two multiples is meant to be the gap between the two markets'
    prices — and it only is that if both quoted caps count the same shares. YOFC's H line quotes a cap
    implying 1,424m shares against 822m on the A line, so the 110.1x / 62.6x pair is not a 76% A/H
    premium at all; it is Yahoo sizing the two lines on different share bases. Publishing it would
    seed the page's own pre-registered "same earnings, two prices" comparison with a number that means
    nothing, so the multiple is suppressed and the cell says why.

    The implied count is cap divided by price on the same line, so it is currency-free: whatever the
    line is quoted in, both halves of the ratio are quoted in it.

    Returns the listings whose trailing multiple was suppressed, for the check.
    """
    by_ticker = {r["ticker"]: r for r in rows}
    flagged: list[dict] = []
    for c in companies:
        if not c.fundamentals_from:
            continue
        sec, pri = by_ticker.get(c.ticker), by_ticker.get(c.fundamentals_from)
        if sec is None or pri is None:
            continue
        sec_shares, pri_shares = _implied_shares(sec), _implied_shares(pri)
        if not sec_shares or not pri_shares:
            continue
        gap = sec_shares / pri_shares - 1.0
        if abs(gap) <= SHARE_BASE_TOLERANCE:
            continue
        reason = (
            f"the quoted market cap implies {sec_shares / 1e6:,.0f}m shares against {pri_shares / 1e6:,.0f}m on "
            f"{c.fundamentals_from}, so a trailing multiple from it would be a share-count artefact"
        )
        log.warning("%s: %s", c.ticker, reason)
        sec["trailing_pe"] = None
        sec["trailing_pe_nm"] = reason
        flagged.append({"ticker": c.ticker, "name": c.name, "primary": c.fundamentals_from,
                        "implied_shares": sec_shares, "primary_implied_shares": pri_shares, "gap": gap})
    return flagged


def dual_listings(rows: list[dict], companies: list[Company], rates: dict[str, float]) -> list[dict]:
    """The same earnings priced in two markets.

    Both multiples are built from the PRIMARY line's consensus, with only the price differing. Taking
    each line's own consensus would measure the two data sources disagreeing rather than the two
    markets: YOFC's A line carries East Money's twenty-broker mean while its H line carries a Yahoo
    figure resting on one analyst, and comparing them produced a 333% "premium" on a single issuer
    whose earnings are, by construction, identical. What is left after holding the forecast fixed is
    the thing the page is actually about.
    """
    by_ticker = {r["ticker"]: r for r in rows}
    out = []
    for c in companies:
        if not c.fundamentals_from:
            continue
        sec, pri = by_ticker.get(c.ticker), by_ticker.get(c.fundamentals_from)
        if not sec or not pri:
            continue
        eps = pri.get(f"eps_{Y0}")
        eps_ccy = pri.get("estimate_currency")
        a_pe, a_nm = forward_pe(
            price=pri.get("price"), price_currency=pri.get("price_currency"),
            eps=eps, eps_currency=eps_ccy, rates=rates,
        )
        h_pe, h_nm = forward_pe(
            price=sec.get("price"), price_currency=sec.get("price_currency"),
            eps=eps, eps_currency=eps_ccy, rates=rates,
        )
        out.append({
            "name": pri["name"],
            "primary": c.fundamentals_from,
            "secondary": c.ticker,
            "primary_pe": a_pe,
            "secondary_pe": h_pe,
            "basis": f"both lines on {pri['ticker']}'s consensus for calendar {Y0}",
            "nm": a_nm or h_nm,
            "premium": (a_pe / h_pe - 1.0) if (a_pe and h_pe) else None,
        })
    return out


def revision_series(companies: list[Company], data: dict) -> list[dict]:
    """Calendar-year consensus as of every vintage date we hold, for the revision chart."""
    from pipelines.valuation.metrics import vintage_estimates

    out: list[dict] = []
    for c in companies:
        vints = vintage_estimates(data["vintages"].get(c.ticker, []), YEARS)
        for as_of, by_year in sorted(vints.items()):
            for year, eps in by_year.items():
                out.append({"ticker": c.ticker, "name": c.name, "as_of": as_of, "year": year, "eps": eps})
    return out


def _share_basis_detail(share: dict) -> str:
    """Say how the pool was measured and, separately, why each excluded issuer is out."""
    entered = ", ".join(
        f"{share['basis_counts'][b]} on {b}" for b in (BASIS_SEGMENT, BASIS_TOTAL) if share["basis_counts"][b]
    )
    clauses = [f"{n} {EXCLUSION_CLAUSES[k]}" for k, n in share["excluded_counts"].items() if n]
    out = f"{share['n_members']} issuers enter the computed pool" + (f" ({entered})" if entered else "")
    if clauses:
        out += f"; {len(share['excluded'])} excluded, " + ", ".join(clauses)
    return out


def _quoted_detail(published: int, held: list[dict]) -> str:
    detail = f"{published} quoted figures on the page, every one with a publisher, a URL and a publication date"
    if held:
        detail += "; " + ", ".join(f"{h['entity']} held back ({h['reason']})" for h in held)
    return detail


def build_track(track: str, data: dict, as_of: date, snapshot_ts: str) -> dict:
    """Build one track's marts and facts, validate them, and write them unless that would blank a page.

    Returns ``{"track", "facts", "written", "refused", "paths"}``.
    """
    companies = [c for c in COMPANIES if c.track == track]
    rows = build_rows(companies, data, as_of)
    share_base_artefacts = suppress_share_base_artefacts(rows, companies)
    reference = load_reference(track)
    # A track can opt out of the market-share layer (TRACKS[track]["share"] is False) when its companies
    # sell different products: the power track's turbines, fuel cells and transformers are not one market.
    has_share = TRACKS[track].get("share", True)
    # The empty stand-in carries every key the populated one does. A share dict missing a key does not
    # fail where it is skipped; it fails later, in the facts assembly that reads all of them.
    share = (computed_share(companies, data["ttm"], reference, data["rates"]) if has_share
             else {"members": [], "excluded": [], "pool_revenue_usd": 0.0, "n_members": 0,
                   "top3_share": None, "top5_share": None, "hhi": None,
                   "basis_counts": {}, "excluded_counts": {}})
    cited = cited_share(reference) if has_share else []

    # Shipments and capacity are rendered on the solid-state page only, so the optical page's count of
    # quoted figures must not include quoted rows it never shows.
    is_ssb = track == "ssb"
    quoted = shipments_and_capacity(reference) if is_ssb else {"shipments": [], "capacity": []}
    n_quoted = len(cited) + len(quoted["shipments"]) + len(quoted["capacity"])
    held_back = [h for h in withheld_quotes(reference) if is_ssb or h["kind"] == "cited_share"]

    key0, key1 = f"fwd_pe_{Y0}", f"fwd_pe_{Y1}"
    priced = [r for r in rows if r.get(key0) is not None]
    fwd0 = [r[key0] for r in priced]
    fwd1 = [r[key1] for r in rows if r.get(key1) is not None]
    growths = [r["growth"] for r in rows if r.get("growth") is not None]
    nm = [r for r in rows if r.get(key0) is None]
    thin = [r for r in rows if r.get("coverage") == "thin"]
    stale = [r for r in rows if (r.get("price_age_days") or 0) > STALE_PRICE_DAYS]

    cheapest = min(priced, key=lambda r: r[key0]) if priced else None
    priciest = max(priced, key=lambda r: r[key0]) if priced else None

    facts = {
        "generated_at": snapshot_ts,
        "track": track,
        "label": TRACKS[track]["label"],
        "years": list(YEARS),
        "as_of_price": data["as_of_price"],
        "as_of_estimates": data["as_of_estimates"],
        "as_of_fx": data["as_of_fx"],
        "n_listings": len(rows),
        "n_issuers": len({c.primary for c in companies}),
        "n_priced": len([r for r in rows if r.get("price")]),
        "n_with_consensus": len(priced),
        "n_thin": len(thin),
        "n_nm": len(nm),
        "n_stale_prices": len(stale),
        "median_fwd_pe_this": _median(fwd0),
        "median_fwd_pe_next": _median(fwd1),
        "p25_fwd_pe_this": _quantile(fwd0, 0.25),
        "p75_fwd_pe_this": _quantile(fwd0, 0.75),
        "iqr_fwd_pe_this": (_quantile(fwd0, 0.75) - _quantile(fwd0, 0.25)) if fwd0 else None,
        "median_growth": _median(growths),
        "median_trailing_pe": _median([r["trailing_pe"] for r in rows if r.get("trailing_pe") is not None]),
        "cheapest": {k: cheapest[k] for k in ("ticker", "name", key0, "growth", "coverage")} if cheapest else None,
        "priciest": {k: priciest[k] for k in ("ticker", "name", key0, "growth", "coverage")} if priciest else None,
        "nm_reasons": sorted({str(r.get(f"{key0}_nm")) for r in nm if r.get(f"{key0}_nm")}),
        "share": {
            "n_members": share["n_members"],
            "n_excluded": len(share["excluded"]),
            "pool_revenue_usd": share["pool_revenue_usd"],
            "basis_counts": share["basis_counts"],
            "excluded_counts": share["excluded_counts"],
            "top3_share": share["top3_share"],
            "top5_share": share["top5_share"],
            "hhi": share["hhi"],
            "leader": share["members"][0] if share["members"] else None,
            "members": share["members"],
            "excluded": share["excluded"],
        },
        "cited_share": cited,
        "n_quoted": n_quoted,
        "withheld_quotes": held_back,
        "dual_listings": dual_listings(rows, companies, data['rates']),
        "share_base_artefacts": share_base_artefacts,
        "excluded_from_pool": [e for e in EXCLUDED if e["track"] == track],
        "reference_updated": reference.get("updated"),
        "sources": SOURCES,
    }
    if is_ssb:
        facts.update(quoted)
        # "The track is the business" and "has no earnings" are two different statements, and the page
        # made one of them while selecting on the other: Microvast is a pure play *and* profitable, so
        # a set built on `purity == "high"` cannot be described as the listings with no PE. All three
        # sets are published, each meaning exactly what its name says. The counts are published too,
        # because `pure_plays` also holds any listing whose multiple is missing for some third reason
        # (no price, no rate), so the page must template off the counts rather than subtract them.
        pure_cols = ("ticker", "name", "purity", "price", "price_currency", "market_cap", "ttm_revenue",
                     "reporting_currency", f"eps_{Y0}", "trailing_pe", "trailing_pe_nm")
        pure = [{k: r[k] for k in pure_cols} for r in rows if r["purity"] == "high"]
        facts["pure_plays"] = pure
        facts["preprofit"] = [p for p in pure if p["trailing_pe_nm"] in (NM_NO_EARNINGS, NM_LOSS)]
        facts["pure_plays_with_earnings"] = [p for p in pure if p["trailing_pe"] is not None]
        facts["n_pure_plays"] = len(pure)
        facts["n_preprofit"] = len(facts["preprofit"])
        facts["n_pure_plays_with_earnings"] = len(facts["pure_plays_with_earnings"])

    published = previous_facts(track)
    refused = blocking_regression(facts, published)
    # Listings for which any trailing financials arrived, whatever they say. This separates a fetch
    # failure from a pool that is simply loss-making.
    with_ttm = [r for r in rows if r.get("ttm_revenue") is not None or r.get("ttm_net_income") is not None]

    facts["checks"] = [
        check("Publish guard", refused is None,
              f"{_counts(facts)['n_priced']} listings priced against "
              f"{_counts(published)['n_priced']} in the published facts"
              if refused is None else
              f"refusing to overwrite the published facts: {refused}"),
        check("Price coverage", len([r for r in rows if r.get("price")]) >= 0.9 * len(rows),
              f"{len([r for r in rows if r.get('price')])} of {len(rows)} listings priced"),
        check("Price freshness", not stale,
              f"every price is from the last {STALE_PRICE_DAYS} days" if not stale else
              f"{len(stale)} of {len(rows)} listings carry a price from an earlier run "
              f"({', '.join(sorted(r['ticker'] for r in stale))}); the table prints the date beside it",
              warn=True),
        check("Consensus coverage", len(priced) >= 0.5 * len(rows),
              f"{len(priced)} of {len(rows)} listings have a calendar-{Y0} consensus; "
              f"{len(nm)} do not ({', '.join(sorted({str(r.get(key0 + '_nm')) for r in nm}))})"),
        check("Analyst depth", len(thin) <= 0.5 * len(rows),
              f"{len(thin)} of {len(rows)} listings are covered by fewer than three analysts", warn=True),
        check("Trailing basis",
              all(r.get("ttm_basis") in (None, "ttm", "last_fy") for r in rows),
              f"{len([r for r in rows if r.get('ttm_basis') == 'last_fy'])} of {len(rows)} listings use the last "
              "full fiscal year instead of a trailing twelve months, because no quarterly statement is published "
              "for them; the table marks which",
              warn=any(r.get("ttm_basis") == "last_fy" for r in rows)),
        # Two situations produce "nobody has a trailing PE" and only one is a problem. If no listing has any
        # trailing financials at all, the fundamentals fetch failed and the track must not publish. If the
        # financials arrived and simply show losses, that is a fact about the market — and on the
        # surgical-robot track it is the page's central finding, not a fault.
        check("Trailing earnings", bool(with_ttm),
              (f"{len([r for r in rows if r.get('trailing_pe')])} of {len(rows)} listings have positive trailing "
               f"profit; {len(with_ttm)} have trailing financials at all"
               if with_ttm else
               f"no trailing financials for any of {len(rows)} listings, so the fundamentals layer is missing "
               "rather than the companies being unprofitable"),
              warn=bool(with_ttm) and not any(r.get("trailing_pe") for r in rows)),
        check("Dual-listing share base", not share_base_artefacts,
              f"{len(facts['dual_listings'])} dual-listed issuers; each secondary line's market cap implies the "
              "same share count as its primary" if not share_base_artefacts else
              f"{len(share_base_artefacts)} secondary listing(s) quote a market cap on a different share base "
              f"({', '.join(a['ticker'] for a in share_base_artefacts)}); their trailing multiple is suppressed "
              "rather than published", warn=True),
        check("Share basis A", (not has_share) or share["n_members"] >= 3,
              _share_basis_detail(share) if has_share else "no market-share layer for this track by design"),
        check("Share adds to one",
              share["n_members"] == 0 or abs(sum(m["share"] for m in share["members"]) - 1.0) < 1e-6,
              "computed shares sum to 1 by construction; this is a float tripwire, not a test of the pool"),
        check("Quoted figures sourced", not held_back, _quoted_detail(n_quoted, held_back), warn=True),
        check("Currency conversion", all(r.get("price") is None or r.get("price_currency") for r in rows),
              "every priced listing has a resolved price currency"),
    ]

    validate(facts, rows, share)

    if refused:
        log.error("%s: NOT PUBLISHING. %s", track, refused)
        log.error("%s: the facts and marts on disk are left exactly as they are, so the live page keeps its "
                  "own data and its own 'as of' stamp; fix the upstream source and re-run.", track)
        return {"track": track, "facts": facts, "written": False, "refused": refused, "paths": []}

    paths = [
        write_json(rows, MART_DIR / f"{track}_companies.json"),
        write_json(revision_series(companies, data), MART_DIR / f"{track}_revisions.json"),
        write_json({"computed": share, "cited": cited, "withheld": held_back}, MART_DIR / f"{track}_share.json"),
        write_json(facts, FACTS_DIR / f"valuation_{track}.json"),
    ]
    log.info("%s: %d listings, %d with consensus, %d in share pool", track, len(rows), len(priced), share["n_members"])
    return {"track": track, "facts": facts, "written": True, "refused": None, "paths": [str(p) for p in paths]}


def load_all() -> dict:
    # Union rather than latest-date, for every table a run can write partially. Estimates are one part
    # file per source per date, so `load()` would let a week in which only East Money answered discard
    # last week's good Yahoo file and turn every US listing into "no consensus for this year"; prices
    # are one file per date, so a half-finished run would drop the rest of the pool. `load_union` keeps
    # the newest row per key across recent runs, bounded so nothing ancient is printed as if it were
    # today's. The two fundamentals tables are then combined by `ttm_by_ticker`, which prefers a real
    # trailing twelve months and only falls back to the last full fiscal year for the listings whose
    # quarterly statements are not published at all. `as_of` turns the staleness gates on.
    prices = read.load_union("prices", PRICE_KEY, max_age_days=PRICE_MAX_AGE_DAYS)
    estimates = read.load_union("estimates", ESTIMATE_KEY, max_age_days=ESTIMATE_MAX_AGE_DAYS)
    vintages = read.load_history("vintages", limit_dates=60)
    fx = read.load("fx")

    quarterly = read.rows(read.load_union("fundamentals", FUNDAMENTAL_KEY))
    annual = read.rows(read.load_union("fundamentals_annual", FUNDAMENTAL_KEY))
    ttm = ttm_by_ticker(quarterly, annual, as_of=utc_now().date().isoformat())

    def newest(df: pl.DataFrame) -> str | None:
        stamps = [r.get("snapshot_ts") for r in read.rows(df) if r.get("snapshot_ts")]
        return max(stamps) if stamps else None

    return {
        "prices": read.by_ticker(prices),
        "estimates": read.by_ticker(estimates),
        "vintages": read.by_ticker(vintages),
        "ttm": ttm,
        "rates": latest_rates(read.rows(fx)),
        # The union can hold rows from more than one run, so "as of" is the newest figure in it, not
        # whichever row happens to sort first.
        "as_of_price": newest(prices),
        "as_of_estimates": newest(estimates),
        "as_of_fx": max((r["date"] for r in read.rows(fx)), default=None),
    }


def build(tracks: list[str] | None = None, now: date | None = None) -> dict[str, dict]:
    """Build every track. A failure in one track is recorded and never costs the other its publish."""
    stamp = utc_now()
    data = load_all()
    as_of = now or stamp.date()
    out: dict[str, dict] = {}
    for track in tracks or list(TRACKS):
        try:
            out[track] = build_track(track, data, as_of, stamp.isoformat())
        except Exception as exc:  # one track's failure must not cost the other its publish
            log.exception("%s: build failed, nothing written for this track", track)
            out[track] = {"track": track, "facts": None, "written": False,
                          "refused": None, "paths": [], "error": f"{type(exc).__name__}: {exc}"}
    return out


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Build valuation marts and facts")
    ap.add_argument("--track", default="all", choices=[*TRACKS, "all"])
    args = ap.parse_args(argv)
    tracks = list(TRACKS) if args.track == "all" else [args.track]

    started = time.monotonic()
    ts = utc_now()
    results = build(tracks)

    def broken(result: dict) -> bool:
        checks = (result["facts"] or {}).get("checks", [])
        return bool(result.get("error") or result["refused"] or any(c["status"] == "fail" for c in checks))

    failed = [t for t, r in results.items() if broken(r)]
    record = {
        "module": "publish",
        "snapshot_ts": ts.isoformat(timespec="seconds"),
        "tracks": {
            t: {
                "written": r["written"],
                "refused": r["refused"],
                "error": r.get("error"),
                "paths": r["paths"],
                "counts": _counts(r["facts"]),
                "checks": (r["facts"] or {}).get("checks", []),
            }
            for t, r in results.items()
        },
        "failed": failed,
        "seconds": round(time.monotonic() - started, 1),
    }
    append_jsonl(record, SNAP_DIR / "valuation" / "runs.jsonl")

    for t, r in results.items():
        for c in (r["facts"] or {}).get("checks", []):
            log.info("%s check %s: %s (%s)", t, c["name"], c["status"], c["detail"])
    if failed:
        log.error("publish failed for: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
