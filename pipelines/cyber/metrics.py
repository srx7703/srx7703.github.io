"""One row per listing, from a consensus snapshot: the table every mart and the page's listings table read.

``listing_rows`` joins the live pool (layer, purity, ring, status) to a snapshot (price, enterprise value, consensus)
and computes the registered measures with ``frozen``: EV/NTM revenue, growth, FCF margin, Rule of 40, target upside
and dispersion. A multiple that cannot be computed carries the reason, because the page says why a company is missing
from a chart instead of dropping it silently.

Enterprise value is stated by Yahoo in the major unit of the quote currency (a pence quote is read as pounds), so
``enterprise_value_usd`` converts it at the rate table the caller passes (units of the currency per dollar, the
repository's FX snapshot). A listing whose currency has no rate gets no dollar value, and so does every listing when
no table is given; a currency is never guessed.

Ring B and ring F rows have no snapshot data; they appear with their classification only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from pipelines.common.storage import SNAP_DIR
from pipelines.cyber import frozen as F
from pipelines.cyber import readings as R
from pipelines.cyber.pool import POOL, Entry, ring
from pipelines.valuation import metrics as VM

CONSENSUS_DIR = SNAP_DIR / "cyber" / "consensus"
THIN = 3  # fewer target-price analysts than this and the company is drawn faded (config.THIN_COVERAGE_BELOW)


def load_runs(directory: Path = CONSENSUS_DIR) -> pl.DataFrame:
    """Every run ever captured, in one frame (the baseline included). Empty if nothing has been captured."""
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def n_ring_a() -> int:
    return len(ring("A"))


def usable_runs(frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Runs in which at least 80% of ring A was priced, keyed by ``snapshot_ts`` (a throttled run is not a snapshot)."""
    out = {}
    for part in frame.partition_by("snapshot_ts", maintain_order=True):
        if part["price"].is_not_null().sum() >= R.PRICED_SHARE * n_ring_a():
            out[part["snapshot_ts"][0]] = part
    return out


def latest(frame: pl.DataFrame) -> tuple[date, str, pl.DataFrame] | None:
    """The newest usable run: (market date, snapshot_ts, rows). The market date is the previous UTC day for a run
    before the US open on a weekday, so it is the run's own UTC date here and the caveat lives in ``price_ts``."""
    runs = usable_runs(frame)
    if not runs:
        return None
    ts = max(runs)
    return date.fromisoformat(ts[:10]), ts, runs[ts]


def market_group(ticker: str) -> str:
    if F.is_china(ticker):
        return "china"
    return "us" if F.is_us_listed(ticker) else "other_overseas"


def _reason(row: dict[str, Any] | None, as_of: date) -> str | None:
    """Why EV/NTM revenue is missing, or None when it exists."""
    if row is None:
        return "not_priced"
    if not F.currencies_agree(row):
        return "currency_mismatch"
    if not row.get("enterprise_value") or row["enterprise_value"] <= 0:
        return "no_enterprise_value"
    if F.ntm_revenue(row, as_of) in (None, 0):
        return "no_revenue_consensus"
    return None


def enterprise_value_usd(row: Mapping[str, Any], rates: Mapping[str, float] | None) -> float | None:
    """Enterprise value in US dollars: Yahoo's figure, in the major unit of the quote currency, over that currency's
    rate (units per dollar). None without a figure, without a currency, without any rate table, or when the table has
    no rate for the currency. The dollar is its own rate (1.0) once a table exists."""
    ev, currency = row.get("enterprise_value"), VM.major_currency(row.get("currency"))
    if ev is None or not currency or not rates:
        return None
    return VM.convert(float(ev), currency, "USD", {**rates, "USD": 1.0})


def static_fields(e: Entry) -> dict[str, Any]:
    return {
        "ticker": e.ticker, "name": e.name, "name_cn": e.name_cn or None, "country": e.country, "ring": e.ring,
        "layer": e.layer, "purity": e.purity, "status": e.status, "status_date": e.status_date or None,
        "platform": e.platform, "placement_basis": e.placement_basis, "also_in": list(e.also_in),
        "market_group": market_group(e.ticker), "note": e.note or None,
    }  # fmt: skip


def listing_rows(
    df: pl.DataFrame | None, as_of: date, rates: Mapping[str, float] | None = None
) -> list[dict[str, Any]]:
    """All rings. Priced rows get the measures; the rest keep their classification and a null measure block.
    ``rates`` (units of a currency per dollar) turns the enterprise value into dollars; without them it stays null."""
    rows = {r["ticker"]: r for r in df.iter_rows(named=True)} if df is not None and df.height else {}
    population = set(F.q1_population(df)) if df is not None and df.height else set()
    out = []
    for e in POOL:
        base = static_fields(e)
        r = rows.get(e.ticker)
        if r is None:
            out.append({**base, "priced": False, "ev_ntm_reason": "not_priced" if e.ring == "A" else None})
            continue
        ev_ntm = F.ev_ntm(r, as_of)
        growth, fcf = F.growth(r, as_of), F.fcf_margin(r)
        price, mean = r.get("price"), r.get("target_mean")
        high, low = r.get("target_high"), r.get("target_low")
        n = r.get("n_opinions")
        out.append(
            {
                **base,
                "priced": True,
                "currency": r.get("currency"),
                "financial_currency": r.get("financial_currency"),
                "price": price,
                "price_ts": r.get("price_ts"),
                "market_cap": r.get("market_cap"),
                "enterprise_value": r.get("enterprise_value"),
                "enterprise_value_usd": enterprise_value_usd(r, rates),
                "ntm_revenue": F.ntm_revenue(r, as_of),
                "ev_ntm": ev_ntm,
                "ev_ntm_reason": _reason(r, as_of),
                "growth": growth,
                "fcf_margin": fcf,
                "rule_of_40": 100.0 * (growth + fcf) if growth is not None and fcf is not None else None,
                "target_mean": mean,
                "target_high": high,
                "target_low": low,
                "n_opinions": n,
                "upside": mean / price - 1.0 if mean and price else None,
                "dispersion": (high - low) / mean if mean and high and low else None,
                "thin": n is None or n < THIN,
                "recommendation_mean": r.get("recommendation_mean"),
                "yahoo_ratings": {
                    "strong_buy": r.get("rating_strong_buy"),
                    "buy": r.get("rating_buy"),
                    "hold": r.get("rating_hold"),
                    "sell": r.get("rating_sell"),
                    "strong_sell": r.get("rating_strong_sell"),
                },  # fmt: skip
                "em_ratings_6m": {
                    "orgs": r.get("em_rating_orgs_6m"),
                    "buy": r.get("em_rating_buy_6m"),
                    "add": r.get("em_rating_add_6m"),
                    "neutral": r.get("em_rating_neutral_6m"),
                    "reduce": r.get("em_rating_reduce_6m"),
                    "sell": r.get("em_rating_sell_6m"),
                }
                if F.is_china(e.ticker)
                else None,  # fmt: skip
                "rev_fy0_n": r.get("rev_fy0_n"),
                "rev_fy1_n": r.get("rev_fy1_n"),
                "em_rev0_n": r.get("em_rev0_n"),
                "fy0_end": r.get("fy0_end"),
                "in_regression_population": e.ticker in population,
            }
        )
    return out
