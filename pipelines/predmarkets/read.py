"""Read helpers for the snapshot tables (used by transforms, tests and notebooks)."""

from __future__ import annotations

import polars as pl

from pipelines.common.storage import SNAP_DIR, load_dim
from pipelines.predmarkets.schema import KEY


def set_dir(name: str):
    return SNAP_DIR / "predmarkets" / name


def quotes(name: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(set_dir(name) / "quotes" / "*" / "*.parquet"))


def books(name: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(set_dir(name) / "books" / "*" / "*.parquet"))


def dim(name: str) -> pl.DataFrame | None:
    return load_dim(set_dir(name) / "dim_markets", KEY)
