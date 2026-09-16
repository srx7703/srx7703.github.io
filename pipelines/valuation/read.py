"""Read the latest valuation snapshots back off disk.

Each table is written once per run as ``data/snapshots/valuation/<table>/<date>[-<source>].parquet``.
The estimates and vintages tables are written twice per run, once by the Yahoo fetcher and once by the
East Money fetcher, so one date can be two part files.

Two readers, and the difference between them is the whole point:

``load`` returns **one date**, which is right for a table that is rewritten whole every run and wrong
for everything else. Reading one date makes the newest file the only file: if Yahoo is rate-limited on
a Tuesday and East Money is not, the East Money part becomes "the newest date" on its own and last
week's perfectly good Yahoo part is discarded — the A-share half of the pool keeps its consensus and
every US listing silently turns into "no consensus for this year".

``load_union`` returns the **newest row per key** across the last few dates, so a partial run can only
add to what is already known, never subtract from it. Prices, estimates and both fundamentals tables
are read this way. The cost is staleness rather than absence, which is the right trade for a page that
prints an "as of" beside every number: a week-old forward PE that says so beats a blank.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from pathlib import Path

import polars as pl

from pipelines.common.storage import SNAP_DIR

log = logging.getLogger("valuation.read")

VAL_DIR = SNAP_DIR / "valuation"
TABLES = ("prices", "estimates", "vintages", "brokers", "fundamentals", "fx")

# The run date a row was written on, taken from the file name and attached while a union is being
# resolved. It never leaves this module: `load_union` drops it before returning.
_RUN_DATE = "_run_date"


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


def _days_between(earlier: str, later: str) -> int:
    return (date_cls.fromisoformat(later) - date_cls.fromisoformat(earlier)).days


def load_union(table: str, key: list[str], limit_dates: int = 8, *, max_age_days: int | None = None) -> pl.DataFrame:
    """The newest row per key across the last few dates of a table.

    ``load()`` returns one date, which is right for a table that is rewritten whole every run. None of
    the tables read here are: a ``--source`` or ``--tickers`` run writes only the listings it fetched,
    and estimates are one part file per source, so a single failing source can leave a date holding
    half a pool. Reading one date would then let a partial Tuesday hide a complete Monday.

    Freshness is decided by the run that wrote the row, not by file order: rows are ordered by the
    date in the file name and then by ``snapshot_ts``, so a stale row can never win a key. The run
    date is always known, which matters because it is the only ordering left if a table is ever
    written without a ``snapshot_ts`` column — falling back to key order there would pick a winner at
    random. ``max_age_days`` bounds how old a surviving row may be, measured against the newest run on
    file, for callers that would rather have a hole than a figure from two months ago.
    """
    files = files_for(table)
    if not files:
        return pl.DataFrame()
    dates = sorted({_date_of(f) for f in files})[-limit_dates:]
    if max_age_days is not None and dates:
        newest = dates[-1]
        dates = [d for d in dates if _days_between(d, newest) <= max_age_days]

    parts = []
    for f in files:
        run_date = _date_of(f)
        if run_date not in dates:
            continue
        part = pl.read_parquet(f)
        if part.height:
            parts.append(part.with_columns(pl.lit(run_date).alias(_RUN_DATE)))
    if not parts:
        return pl.DataFrame()

    hist = pl.concat(parts, how="diagonal_relaxed")
    sort_by = [_RUN_DATE, *(c for c in ("snapshot_ts", *key) if c in hist.columns)]
    return (
        hist.sort(sort_by)
        .unique(subset=key, keep="last", maintain_order=True)
        .drop(_RUN_DATE)
    )


def rows(df: pl.DataFrame) -> list[dict]:
    return df.to_dicts() if df.height else []


def by_ticker(df: pl.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows(df):
        out.setdefault(r.get("ticker"), []).append(r)
    return out
