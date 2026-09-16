"""Data-quality checks rendered on every project page ("Data quality" section).

Each check is {name, status: pass|warn|fail, detail}. They are computed at publish time from the
tables the page is built from, so the page shows what was actually verified for this build.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl


def check(name: str, ok: bool, detail: str, *, warn: bool = False) -> dict:
    return {"name": name, "status": "pass" if ok else ("warn" if warn else "fail"), "detail": detail}


def freshness(last_ts: str | None, max_age_hours: float, label: str) -> dict:
    if not last_ts:
        return check("Freshness", False, f"no {label} yet")
    age = (datetime.now(UTC) - datetime.fromisoformat(last_ts.replace("Z", "+00:00"))).total_seconds() / 3600
    return check(
        "Freshness",
        age <= max_age_hours,
        f"latest {label} {age:.1f} h ago (limit {max_age_hours:.0f} h)",
        warn=age <= 2 * max_age_hours,
    )


def probability_range(df: pl.DataFrame, cols: list[str]) -> dict:
    bad = 0
    for c in cols:
        if c in df.columns:
            bad += int(df.filter(pl.col(c).is_not_null() & ((pl.col(c) < 0) | (pl.col(c) > 1))).height)
    return check("Probability range", bad == 0, f"{bad} values outside [0, 1] across {', '.join(cols)}")


def uniqueness(df: pl.DataFrame, key: list[str], label: str, name: str = "Key uniqueness") -> dict:
    dup = int(df.height - df.unique(subset=key).height)
    return check(name, dup == 0, f"{dup} duplicate {label} on ({', '.join(key)})")


def non_null(df: pl.DataFrame, cols: list[str], id_col: str | None = None, warn_up_to: int = 0) -> dict:
    """Missing values in required columns. With `id_col`, names the rows; `warn_up_to` rows missing
    something is a warning (a known coverage gap) rather than a failure."""
    missing = {c: int(df[c].null_count()) for c in cols if c in df.columns}
    bad = sum(missing.values())
    detail = ", ".join(f"{c}: {n} null" for c, n in missing.items() if n) or "all present"
    if id_col and bad:
        rows = df.filter(pl.any_horizontal([pl.col(c).is_null() for c in cols if c in df.columns]))
        detail += " (" + ", ".join(str(x) for x in rows[id_col].to_list()) + ")"
        return check("Required fields", bad == 0, detail, warn=rows.height <= warn_up_to)
    return check("Required fields", bad == 0, detail)


def file_fingerprint(path: Path, label: str) -> dict:
    import hashlib

    h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return check("Frozen input", True, f"{label} sha256 {h}, {path.stat().st_size:,} bytes")
