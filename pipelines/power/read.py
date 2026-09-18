"""Read the latest power snapshots back off disk.

Each table is written once per run as ``data/snapshots/power/<table>/<date>.parquet``.

Unlike the valuation pipeline, every table here is rewritten whole from a single upstream workbook, so
there is no partial-run problem to defend against and ``load`` returning one date is correct. The one
exception is ``deferrals``, which is a *diff* between two 860M editions: it only exists when two
editions a year apart were both downloadable, so a run that could not reach the archive writes no
deferrals file at all rather than an empty one. ``load`` therefore tolerates a missing table and the
page says the comparison is unavailable, instead of the page saying zero retirements were deferred.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from pipelines.common.storage import SNAP_DIR

log = logging.getLogger("power.read")

POWER_DIR = SNAP_DIR / "power"
TABLES = ("generators", "retirements", "deferrals", "sales", "steo", "region_load", "deals")


def files_for(table: str) -> list[Path]:
    d = POWER_DIR / table
    return sorted(d.glob("*.parquet")) if d.exists() else []


def latest_path(table: str) -> Path | None:
    files = files_for(table)
    return files[-1] if files else None


def load(table: str) -> pl.DataFrame:
    """The newest snapshot of one table, or an empty frame if it has never been written.

    Empty rather than raising, because the page is built from seven tables and losing one of them
    should cost the reader that one section with a failed check beside it, not the whole page.
    """
    path = latest_path(table)
    if path is None:
        log.warning("no %s snapshot under %s", table, POWER_DIR / table)
        return pl.DataFrame()
    df = pl.read_parquet(path)
    log.info("read %s: %d rows from %s", table, df.height, path.name)
    return df


def snapshot_dates(table: str) -> list[str]:
    """Every run date this table has, oldest first — used by the freshness check."""
    return [p.stem[:10] for p in files_for(table)]
