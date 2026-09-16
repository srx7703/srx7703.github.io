"""Backfill daily price history for a market set from each platform's history endpoints.

Usage:
    uv run python -m pipelines.predmarkets.backfill --set fomc

Writes data/snapshots/predmarkets/<set>/history/<platform>.parquet (overwritten each run; git keeps
prior versions). Rows: platform, market_id, date, price (YES, 0-1), volume, open_interest.
Polymarket: CLOB /prices-history (interval=max, fidelity=1440 minutes). Kalshi: daily candlesticks.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

import pandera.polars as pa
import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, write_parquet
from pipelines.predmarkets import read
from pipelines.predmarkets.config import MARKET_SETS
from pipelines.predmarkets.kalshi import KalshiClient
from pipelines.predmarkets.polymarket import PolymarketClient

log = logging.getLogger("predmarkets.backfill")

HIST_SCHEMA = pa.DataFrameSchema(
    {
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "date": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "price": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
    },
    unique=["platform", "market_id", "date"],
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


def backfill_kalshi(dim: pl.DataFrame, days: int = 400) -> pl.DataFrame:
    c = KalshiClient()
    c.http.min_interval = 0.3  # candlesticks are rate-limited harder than list endpoints
    now = int(datetime.now(UTC).timestamp())
    rows: list[dict] = []
    markets = dim.filter(pl.col("platform") == "kalshi")
    for r in markets.iter_rows(named=True):
        try:
            cs = (
                c.candlesticks(
                    r["series"], r["market_id"], start_ts=now - days * 86400, end_ts=now, period_interval=1440
                ).get("candlesticks")
                or []
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("candlesticks failed %s: %s", r["market_id"], exc)
            continue
        for k in cs:
            close = (k.get("price") or {}).get("close_dollars")
            if close is None:
                continue
            rows.append(
                {
                    "platform": "kalshi",
                    "market_id": r["market_id"],
                    "date": datetime.fromtimestamp(int(k["end_period_ts"]), UTC).strftime("%Y-%m-%d"),
                    "price": float(close),
                    "volume": float(k.get("volume_fp") or 0),
                    "open_interest": float(k.get("open_interest_fp") or 0),
                }
            )
    return pl.DataFrame(rows, schema=HIST_DTYPES).unique(subset=["market_id", "date"], keep="last", maintain_order=True)


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
    if args.platform in ("all", "polymarket"):
        pm = backfill_polymarket(dim)
        HIST_SCHEMA.validate(pm)
        write_parquet(pm, out / "polymarket.parquet")
        log.info("polymarket history: %d rows, %d markets", pm.height, pm["market_id"].n_unique())
    if args.platform in ("all", "kalshi"):
        kx = backfill_kalshi(dim)
        HIST_SCHEMA.validate(kx)
        write_parquet(kx, out / "kalshi.parquet")
        log.info("kalshi history: %d rows, %d markets", kx.height, kx["market_id"].n_unique())
    return 0


if __name__ == "__main__":
    sys.exit(main())
