"""Backfill daily price history for a market set from each platform's history endpoints.

Usage:
    uv run python -m pipelines.predmarkets.backfill --set fomc

Merges into data/snapshots/predmarkets/<set>/history/<platform>.parquet on (platform, market_id,
date): this run's rows win a shared key and every other stored row is kept, so a fetch that fails
or 404s never deletes history. Stored rows outlive the fetch window, so a change to how rows are
derived (e.g. the date label) means migrating the file, not deleting it.
Rows: platform, market_id, date, price (YES, 0-1), volume, open_interest.
Polymarket: CLOB /prices-history (interval=max, fidelity=1440 minutes). Kalshi: daily candlesticks,
from /historical for markets settled before Kalshi's historical cutoff (the live endpoint 404s them).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import httpx
import pandera.polars as pa
import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, write_parquet
from pipelines.predmarkets import read
from pipelines.predmarkets.config import MARKET_SETS
from pipelines.predmarkets.kalshi import KalshiClient
from pipelines.predmarkets.polymarket import PolymarketClient

log = logging.getLogger("predmarkets.backfill")

HIST_KEY = ["platform", "market_id", "date"]

HIST_SCHEMA = pa.DataFrameSchema(
    {
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "date": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "price": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
    },
    unique=HIST_KEY,
)

HIST_DTYPES: dict[str, pl.DataType] = {
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "date": pl.Utf8,
    "price": pl.Float64,
    "volume": pl.Float64,
    "open_interest": pl.Float64,
}


def backfill_polymarket(dim: pl.DataFrame) -> pl.DataFrame:
    c = PolymarketClient()
    rows: list[dict] = []
    markets = dim.filter((pl.col("platform") == "polymarket") & pl.col("yes_token").is_not_null())
    for r in markets.iter_rows(named=True):
        try:
            hist = c.prices_history(r["yes_token"], interval="max", fidelity=1440).get("history") or []
        except Exception as exc:  # noqa: BLE001
            log.warning("prices-history failed %s: %s", r["market_id"], exc)
            continue
        for h in hist:
            rows.append(
                {
                    "platform": "polymarket",
                    "market_id": r["market_id"],
                    "date": datetime.fromtimestamp(int(h["t"]), UTC).strftime("%Y-%m-%d"),
                    "price": float(h["p"]),
                    "volume": None,
                    "open_interest": None,
                }
            )
    df = pl.DataFrame(rows, schema=HIST_DTYPES)
    # one row per market-date: last observation of the day
    return df.unique(subset=["market_id", "date"], keep="last", maintain_order=True)


def _utc(s: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s) if s else None
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt and dt.tzinfo is None else dt


def kalshi_candle_row(k: dict, market_id: str) -> dict | None:
    """One daily candle as a history row, or None if nothing traded that day. Reads both schemas:
    live `price.close_dollars` / `volume_fp` / `open_interest_fp`, historical `price.close` /
    `volume` / `open_interest`."""
    price = k.get("price") or {}
    close = price.get("close_dollars", price.get("close"))
    if close is None:
        return None
    return {
        "platform": "kalshi",
        "market_id": market_id,
        "date": datetime.fromtimestamp(int(k["end_period_ts"]), UTC).strftime("%Y-%m-%d"),
        "price": float(close),
        "volume": float(k.get("volume_fp", k.get("volume")) or 0),
        "open_interest": float(k.get("open_interest_fp", k.get("open_interest")) or 0),
    }


def _kalshi_candles(c: KalshiClient, r: dict, cutoff: datetime | None, window: dict) -> list[dict]:
    """Live candles, or historical ones for a market that closed before the cutoff. The dim knows the
    close time but the cutoff is on settlement time, so a 404 from the first endpoint tries the other."""
    live = partial(c.candlesticks, r["series"], r["market_id"], **window)
    archived = partial(c.historical_candlesticks, r["market_id"], **window)
    closed = _utc(r["end_date"])
    first, second = (archived, live) if cutoff and closed and closed < cutoff else (live, archived)
    try:
        return first().get("candlesticks") or []
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
    return second().get("candlesticks") or []


def backfill_kalshi(dim: pl.DataFrame, days: int = 400) -> pl.DataFrame:
    c = KalshiClient()
    c.http.min_interval = 0.3  # candlesticks are rate-limited harder than list endpoints
    now = int(datetime.now(UTC).timestamp())
    window = {"start_ts": now - days * 86400, "end_ts": now, "period_interval": 1440}
    try:
        cutoff = _utc(c.historical_cutoff().get("market_settled_ts"))
    except Exception as exc:  # noqa: BLE001 - without it every market tries the live endpoint first
        log.warning("historical cutoff unavailable: %s", exc)
        cutoff = None
    rows: list[dict] = []
    markets = dim.filter(pl.col("platform") == "kalshi")
    for r in markets.iter_rows(named=True):
        try:
            parsed = [kalshi_candle_row(k, r["market_id"]) for k in _kalshi_candles(c, r, cutoff, window)]
        except Exception as exc:  # noqa: BLE001
            log.warning("candlesticks failed %s: %s", r["market_id"], exc)
            continue
        rows += [row for row in parsed if row]
    return pl.DataFrame(rows, schema=HIST_DTYPES).unique(subset=["market_id", "date"], keep="last", maintain_order=True)


def merge_history(new: pl.DataFrame, path: Path) -> pl.DataFrame:
    """Stored rows plus this run's, one per (platform, market_id, date), this run winning a shared key.
    A market this run could not fetch keeps every row it already had."""
    if not path.exists():
        return new
    old = pl.read_parquet(path).select(list(HIST_DTYPES)).cast(HIST_DTYPES)
    return pl.concat([old, new]).unique(subset=HIST_KEY, keep="last", maintain_order=True).sort(HIST_KEY)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="fomc", choices=list(MARKET_SETS))
    ap.add_argument("--platform", default="all", choices=["all", "polymarket", "kalshi"])
    args = ap.parse_args(argv)
    setup_logging()
    dim = read.dim(args.set)
    if dim is None:
        log.error("no dimension for set %s; run the snapshot first", args.set)
        return 1
    out = SNAP_DIR / "predmarkets" / args.set / "history"
    for platform, fetch in (("polymarket", backfill_polymarket), ("kalshi", backfill_kalshi)):
        if args.platform not in ("all", platform):
            continue
        path = out / f"{platform}.parquet"
        new = fetch(dim)
        merged = merge_history(new, path)
        HIST_SCHEMA.validate(merged)
        write_parquet(merged, path)
        log.info(
            "%s history: fetched %d rows for %d markets; stored %d rows for %d markets",
            platform,
            new.height,
            new["market_id"].n_unique(),
            merged.height,
            merged["market_id"].n_unique(),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
