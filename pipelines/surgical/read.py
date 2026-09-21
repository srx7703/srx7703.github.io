"""Read the latest surgical snapshots back off disk.

Only one table is fetched, so this is short. It exists rather than being inlined because the page must degrade
per table: a missing clearance snapshot should cost the page its approval timeline and nothing else.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from pipelines.common.storage import SNAP_DIR

log = logging.getLogger("surgical.read")

DIR = SNAP_DIR / "surgical"
TABLES = ("clearances",)


def files_for(table: str) -> list[Path]:
    d = DIR / table
    return sorted(d.glob("*.parquet")) if d.exists() else []


def load(table: str) -> pl.DataFrame:
    """The newest snapshot, or an empty frame. Empty rather than raising: one missing table costs the page one
    section with a failed check beside it, not the whole page."""
    files = files_for(table)
    if not files:
        log.warning("no %s snapshot under %s", table, DIR / table)
        return pl.DataFrame()
    df = pl.read_parquet(files[-1])
    log.info("read %s: %d rows from %s", table, df.height, files[-1].name)
    return df
