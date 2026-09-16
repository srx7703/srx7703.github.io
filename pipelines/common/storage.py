"""Paths and file helpers shared by all pipelines.

Data layers (see docs/DATA_MODEL.md):
  data/raw/        immutable API responses (gzipped JSON), partitioned by set/date/time
  data/snapshots/  normalized per-run tables (parquet) + dimension delta files
  data/marts/      derived tables (Polars) the site reads
  data/facts/      per-project KPI JSON the site narrative is templated from
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(os.environ.get("PORTFOLIO_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SNAP_DIR = DATA_DIR / "snapshots"
MARTS_DIR = DATA_DIR / "marts"
FACTS_DIR = DATA_DIR / "facts"


def utc_now() -> datetime:
    return datetime.now(UTC)


def run_stamp(ts: datetime) -> tuple[str, str]:
    """Partition parts for a run timestamp: ('2026-09-15', '1830')."""
    return ts.strftime("%Y-%m-%d"), ts.strftime("%H%M")


def write_parquet(df: pl.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    return path


def write_json(obj: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_json_gz(obj: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    return path


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(obj: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return path


def load_dim(dim_dir: Path, key: list[str]) -> pl.DataFrame | None:
    """Consolidate dimension delta files: latest `valid_from` per key wins."""
    files = sorted(dim_dir.glob("*/*.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    return df.sort("valid_from").unique(subset=key, keep="last", maintain_order=True)


def write_dim_delta(new: pl.DataFrame, dim_dir: Path, key: list[str], snapshot_ts: str, date: str, hhmm: str) -> int:
    """Append-only dimension: write only rows that are new or whose attributes changed.

    `new` must contain the key and attribute columns (no bookkeeping columns). New rows get
    `first_seen = valid_from = snapshot_ts`; changed rows keep their original `first_seen`.
    Returns the number of rows written. Consolidate with `load_dim`.
    """
    attrs = [c for c in new.columns if c not in key]
    cur = load_dim(dim_dir, key)
    if cur is None:
        delta = new.with_columns(pl.lit(snapshot_ts).alias("first_seen"), pl.lit(snapshot_ts).alias("valid_from"))
    else:
        cur_attrs = cur.select([*key, *[pl.col(c).alias(f"_cur_{c}") for c in attrs], "first_seen"])
        joined = new.join(cur_attrs, on=key, how="left")
        changed = (
            pl.any_horizontal([pl.col(c).ne_missing(pl.col(f"_cur_{c}")) for c in attrs]) if attrs else pl.lit(False)
        )
        is_new = pl.col("first_seen").is_null()
        delta = (
            joined.filter(is_new | changed)
            .with_columns(pl.coalesce([pl.col("first_seen"), pl.lit(snapshot_ts)]).alias("first_seen"))
            .with_columns(pl.lit(snapshot_ts).alias("valid_from"))
            .select([*key, *attrs, "first_seen", "valid_from"])
        )
    if delta.height:
        write_parquet(delta, dim_dir / date / f"{hhmm}.parquet")
    return delta.height
