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
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import date

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now, write_json
from pipelines.valuation import read
from pipelines.valuation.config import COMPANIES, EXCLUDED, TRACKS, Company
from pipelines.valuation.fundamentals import ttm_by_ticker
from pipelines.valuation.metrics import company_row
from pipelines.valuation.schema import FUNDAMENTAL_KEY
from pipelines.valuation.share import cited_share, computed_share, load_reference, shipments_and_capacity

log = logging.getLogger("valuation.publish")

YEARS = (2026, 2027)
MART_DIR = MARTS_DIR / "valuation"

SOURCES = [
    {"name": "Yahoo Finance (prices and analyst consensus)", "url": "https://finance.yahoo.com/"},
    {"name": "East Money analyst forecasts (A-shares)", "url": "https://emweb.securities.eastmoney.com/"},
    {"name": "SEC EDGAR XBRL companyfacts", "url": "https://www.sec.gov/edgar/sec-api-documentation"},
    {"name": "FRED daily exchange rates", "url": "https://fred.stlouisfed.org/categories/94"},
]


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


def build_rows(companies: list[Company], data: dict, as_of: date) -> list[dict]:
    rows: list[dict] = []
    for c in companies:
        est = [r for r in data["estimates"].get(c.ticker, []) if r.get("eps_avg") is not None]
        vint = data["vintages"].get(c.ticker, [])
        ttm = data["ttm"].get(c.primary)
        rows.append(
            company_row(
                c,
                price_row=data["prices"].get(c.ticker, [{}])[0] if data["prices"].get(c.ticker) else None,
                estimate_rows=est,
                vintage_rows=vint,
                ttm=ttm,
                rates=data["rates"],
                years=YEARS,
                as_of=as_of,
            )
        )
    return rows


def dual_listings(rows: list[dict], companies: list[Company]) -> list[dict]:
    """The same earnings priced in two markets: a clean control for the dispersion story."""
    by_ticker = {r["ticker"]: r for r in rows}
    out = []
    for c in companies:
        if not c.fundamentals_from:
            continue
        sec, pri = by_ticker.get(c.ticker), by_ticker.get(c.fundamentals_from)
        if not sec or not pri:
            continue
        key = f"fwd_pe_{YEARS[0]}"
        a_pe, h_pe = pri.get(key), sec.get(key)
        out.append({
            "name": pri["name"],
            "primary": c.fundamentals_from,
            "secondary": c.ticker,
            "primary_pe": a_pe,
            "secondary_pe": h_pe,
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


def build_track(track: str, data: dict, as_of: date, snapshot_ts: str) -> dict:
    companies = [c for c in COMPANIES if c.track == track]
    rows = build_rows(companies, data, as_of)
    reference = load_reference(track)
    share = computed_share(companies, data["ttm"], reference, data["rates"])
    cited = cited_share(reference)

    key0, key1 = f"fwd_pe_{YEARS[0]}", f"fwd_pe_{YEARS[1]}"
    priced = [r for r in rows if r.get(key0) is not None]
    fwd0 = [r[key0] for r in priced]
    fwd1 = [r[key1] for r in rows if r.get(key1) is not None]
    growths = [r["growth"] for r in rows if r.get("growth") is not None]
    nm = [r for r in rows if r.get(key0) is None]
    thin = [r for r in rows if r.get("coverage") == "thin"]

    cheapest = min(priced, key=lambda r: r[key0]) if priced else None
    priciest = max(priced, key=lambda r: r[key0]) if priced else None

    checks = [
        check("Price coverage", len([r for r in rows if r.get("price")]) >= 0.9 * len(rows),
              f"{len([r for r in rows if r.get('price')])} of {len(rows)} listings priced"),
        check("Consensus coverage", len(priced) >= 0.5 * len(rows),
              f"{len(priced)} of {len(rows)} listings have a calendar-{YEARS[0]} consensus; "
              f"{len(nm)} do not ({', '.join(sorted({str(r.get(key0 + '_nm')) for r in nm}))})"),
        check("Analyst depth", len(thin) <= 0.5 * len(rows),
              f"{len(thin)} of {len(rows)} listings are covered by fewer than three analysts", warn=True),
        check("Trailing basis",
              all(r.get("ttm_basis") in (None, "ttm", "last_fy") for r in rows),
              f"{len([r for r in rows if r.get('ttm_basis') == 'last_fy'])} of {len(rows)} listings use the last "
              "full fiscal year instead of a trailing twelve months, because no quarterly statement is published "
              "for them; the table marks which",
              warn=any(r.get("ttm_basis") == "last_fy" for r in rows)),
        check("Trailing earnings", any(r.get("trailing_pe") for r in rows),
              f"{len([r for r in rows if r.get('trailing_pe')])} of {len(rows)} listings "
              "have positive trailing profit"),
        check("Share basis A", share["n_members"] >= 3,
              f"{share['n_members']} listings enter the computed pool; {len(share['excluded'])} "
              "excluded for not disclosing revenue for this track"),
        check("Share adds to one",
              share["n_members"] == 0 or abs(sum(m["share"] for m in share["members"]) - 1.0) < 1e-6,
              "computed shares sum to 1 by construction"),
        check("Cited share sourced", all(c.get("source_url") for c in cited),
              f"{len(cited)} cited figures, every one with a publisher and a URL", warn=not cited),
        check("Currency conversion", all(r.get("price") is None or r.get("price_currency") for r in rows),
              "every priced listing has a resolved price currency"),
    ]

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
            "top3_share": share["top3_share"],
            "top5_share": share["top5_share"],
            "hhi": share["hhi"],
            "leader": share["members"][0] if share["members"] else None,
            "members": share["members"],
            "excluded": share["excluded"],
        },
        "cited_share": cited,
        "dual_listings": dual_listings(rows, companies),
        "excluded_from_pool": [e for e in EXCLUDED if e["track"] == track],
        "reference_updated": reference.get("updated"),
        "checks": checks,
        "sources": SOURCES,
    }
    if track == "ssb":
        facts.update(shipments_and_capacity(reference))
        preprofit_cols = ("ticker", "name", "price", "price_currency", "market_cap",
                          "ttm_revenue", "reporting_currency", f"eps_{YEARS[0]}")
        facts["preprofit"] = [{k: r[k] for k in preprofit_cols} for r in rows if r["purity"] == "high"]

    write_json(rows, MART_DIR / f"{track}_companies.json")
    write_json(revision_series(companies, data), MART_DIR / f"{track}_revisions.json")
    write_json({"computed": share, "cited": cited}, MART_DIR / f"{track}_share.json")
    write_json(facts, FACTS_DIR / f"valuation_{track}.json")
    log.info("%s: %d listings, %d with consensus, %d in share pool", track, len(rows), len(priced), share["n_members"])
    return facts


def load_all() -> dict:
    prices = read.load("prices")
    estimates = read.load("estimates")
    vintages = read.load_history("vintages", limit_dates=60)
    fx = read.load("fx")

    # Union rather than latest-date: a partial fetch must not hide a complete earlier one. The two
    # fundamentals tables are combined by `ttm_by_ticker`, which prefers a real trailing twelve
    # months and only falls back to the last full fiscal year for the listings whose quarterly
    # statements are not published at all. `as_of` turns the staleness gates on.
    quarterly = read.rows(read.load_union("fundamentals", FUNDAMENTAL_KEY))
    annual = read.rows(read.load_union("fundamentals_annual", FUNDAMENTAL_KEY))
    ttm = ttm_by_ticker(quarterly, annual, as_of=utc_now().date().isoformat())

    return {
        "prices": read.by_ticker(prices),
        "estimates": read.by_ticker(estimates),
        "vintages": read.by_ticker(vintages),
        "ttm": ttm,
        "rates": latest_rates(read.rows(fx)),
        "as_of_price": read.rows(prices)[0]["snapshot_ts"] if prices.height else None,
        "as_of_estimates": read.rows(estimates)[0]["snapshot_ts"] if estimates.height else None,
        "as_of_fx": max((r["date"] for r in read.rows(fx)), default=None),
    }


def build(tracks: list[str] | None = None) -> dict[str, dict]:
    now = utc_now()
    data = load_all()
    as_of = now.date()
    out: dict[str, dict] = {}
    for track in tracks or list(TRACKS):
        out[track] = build_track(track, data, as_of, now.isoformat())
    return out


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Build valuation marts and facts")
    ap.add_argument("--track", default="all", choices=[*TRACKS, "all"])
    args = ap.parse_args(argv)
    tracks = list(TRACKS) if args.track == "all" else [args.track]
    facts = build(tracks)
    failed = [t for t, f in facts.items() if any(c["status"] == "fail" for c in f["checks"])]
    if failed:
        log.error("checks failed for: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
