"""Read the latest valuation snapshots back off disk.

Each table is written once per run as ``data/snapshots/valuation/<table>/<date>[-<source>].parquet``.
The estimates and vintages tables are written twice per run, once by the Yahoo fetcher and once by the
East Money fetcher, so reading them means concatenating the two files for the newest date that has any.
Reading the newest date rather than the newest file matters: if one source failed this week, the other
source's fresh file must not be silently paired with a stale partner from a different week.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from pipelines.common.storage import SNAP_DIR

log = logging.getLogger("valuation.read")

VAL_DIR = SNAP_DIR / "valuation"
TABLES = ("prices", "estimates", "vintages", "brokers", "fundamentals", "fx")


def _date_of(path: Path) -> str:
    """File names are ``<YYYY-MM-DD>.parquet`` or ``<YYYY-MM-DD>-<source>.parquet``."""
    return path.stem[:10]


def files_for(table: str) -> list[Path]:
    d = VAL_DIR / table
    return sorted(d.glob("*.parquet")) if d.exists() else []


def latest_date(table: str) -> str | None:
    files = files_for(table)
    return max((_date_of(f) for f in files), default=None)


def load(table: str, date: str | None = None) -> pl.DataFrame:
    """All parquet parts for one table on one date, concatenated. Empty frame if there are none."""
    files = files_for(table)
    if not files:
        log.warning("no snapshots for table %s", table)
        return pl.DataFrame()
    want = date or max(_date_of(f) for f in files)
    parts = [pl.read_parquet(f) for f in files if _date_of(f) == want]
    if not parts:
        log.warning("no snapshot for table %s on %s", table, want)
        return pl.DataFrame()
    if len(parts) == 1:
        return parts[0]
    return pl.concat(parts, how="diagonal_relaxed")


def load_history(table: str, limit_dates: int = 60) -> pl.DataFrame:
    """The last N dates of a table, for series that accumulate across runs (vintages, prices)."""
    files = files_for(table)
    if not files:
        return pl.DataFrame()
    dates = sorted({_date_of(f) for f in files})[-limit_dates:]
    parts = [pl.read_parquet(f) for f in files if _date_of(f) in dates]
    return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()


def load_union(table: str, key: list[str], limit_dates: int = 8) -> pl.DataFrame:
    """The newest row per key across the last few dates of a table.

    ``load()`` returns one date, which is right for a table that is rewritten whole every run. The
    fundamentals tables are not: a ``--source`` or ``--tickers`` run writes only the listings it
    fetched, and it merges with the same date but not with last week's. Reading one date would then
    let a partial Tuesday hide a complete Monday. Taking the newest row per key across recent dates
    keeps the freshest figure for every listing and never loses one to a partial run.
    """
    hist = load_history(table, limit_dates=limit_dates)
    if not hist.height:
        return hist
    sort_by = [c for c in ("snapshot_ts", *key) if c in hist.columns]
    return hist.sort(sort_by).unique(subset=key, keep="last", maintain_order=True)


def rows(df: pl.DataFrame) -> list[dict]:
    return df.to_dicts() if df.height else []


def by_ticker(df: pl.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows(df):
        out.setdefault(r.get("ticker"), []).append(r)
    return out
