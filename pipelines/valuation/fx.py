"""Daily FX rates from FRED, the bridge between a listing's price currency and its reporting currency.

    uv run python -m pipelines.valuation.fx --days 400

FRED's graph CSV endpoint needs no API key: ``fredgraph.csv?id=DEXCHUS`` returns the full history of
one series. We keep the last ``--days`` of it rather than only the last row, so a mart can price a
past date (a revision chart is worthless if every historical EPS is translated at today's rate).

Two conventions live in ``config.FX_SERIES`` and mixing them up is the expensive mistake here:

``per_usd``   the series is already units of the foreign currency per 1 USD (DEXCHUS = 6.7080 CNY/USD).
``usd_per``   the series is USD per 1 unit of the foreign currency. **DEXUSUK is this one**: 1.3524 is
              USD per GBP, so ``per_usd = 1 / 1.3524 = 0.7394``. Forget the inversion and every UK
              number on the site is out by a factor of 1.83. ``SANITY_BANDS`` exists to catch exactly
              that: an un-inverted GBP lands at 1.3524, outside the 0.4-1.2 band, and the run warns.
``identity``  USD against itself, ``per_usd = 1.0``, emitted for every date the other series cover so
              a USD row is always joinable.

Pence are not a currency. London quotes ``IKA.L`` in GBp (pence) while the accounts are in GBP; the
price module records ``currency = "GBp"`` verbatim. The factor of 100 is owned by ``metrics.py`` —
``metrics.normalise_quote`` / ``metrics.convert`` are the one place in the pipeline that knows about
it, and this module deliberately keeps no second copy of them. There is no GBp row in the fx table:
a pence quote is normalised to its major unit before it ever meets a rate.

Each run writes:
    data/snapshots/valuation/fx/<date>.parquet          one row per (date, currency)
    data/raw/valuation/fred/<date>/<series>.csv.gz      the CSV exactly as FRED served it
    data/snapshots/valuation/runs.jsonl                 one line per run

One series failing must not lose the others: each currency is fetched, parsed *and date-checked*
independently, the table is written from whatever succeeded, and only then does the process exit
non-zero. A second run on the same date merges over the first rather than replacing it — new rows win
on (date, currency), so a run whose KRW series was down cannot delete the KRW rows the morning run
already wrote. A run that produced no rows at all writes nothing and still records itself in
runs.jsonl; an empty or shrunken table must never land on top of a good one.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import logging
import math
import re
import sys
import time
from collections.abc import Iterable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pipelines.common.checks import check
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import RAW_DIR, SNAP_DIR, append_jsonl, run_stamp, utc_now, write_parquet
from pipelines.valuation.config import FX_SERIES
from pipelines.valuation.schema import FX_KEY, FX_SCHEMA, fx_frame

log = logging.getLogger("valuation.fx")

BASE = "https://fred.stlouisfed.org"
CSV_PATH = "/graph/fredgraph.csv"
DEFAULT_DAYS = 400

# FRED has shipped both spellings of the date column over the years; accept either, reject anything else.
DATE_COLUMNS = {"date", "observation_date"}

# A missing observation is a literal "." in the classic export and an empty field in the current one.
MISSING_VALUES = {"", ".", "n/a", "na", "nan", "null"}

# The date column FRED exports and the format schema.FX_SCHEMA will accept. Checked here, per
# currency, so a payload whose date format changed is one currency's error and not a dead run.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Wide plausibility bands on per_usd, purely to catch an inverted or mis-scaled series. Warn, never fail:
# bands drift, and a stale band must not be able to block a run.
SANITY_BANDS: dict[str, tuple[float, float]] = {
    "CNY": (5.0, 9.0),
    "JPY": (80.0, 250.0),
    "KRW": (800.0, 2000.0),
    "TWD": (20.0, 45.0),
    "HKD": (7.0, 8.5),
    "GBP": (0.4, 1.2),
    "USD": (1.0, 1.0),
}

IDENTITY_SERIES = "identity"


# --- conventions ------------------------------------------------------------------
def to_per_usd(value: float, convention: str) -> float:
    """Apply a FX_SERIES convention to one raw observation. `usd_per` is the inversion.

    Non-finite values are rejected on both sides of the inversion. A CSV carrying ``inf`` (or
    ``1e400``, which ``float()`` rounds to it) would otherwise sail through: ``inf <= 0`` is False
    and pandera's ``gt(0.0)`` is True for infinity, so the rate would be written to the snapshot and
    ``metrics.convert`` would turn every amount in that currency into 0.0 with nothing flagged.
    A denormal observation is the same failure via the reciprocal: ``1 / 5e-324`` is ``inf``.
    """
    if convention == "identity":
        return 1.0
    if not math.isfinite(value):
        raise ValueError(f"non-finite FRED observation {value!r} cannot be a rate")
    if value <= 0:
        raise ValueError(f"non-positive FRED observation {value!r} cannot be a rate")
    if convention == "per_usd":
        rate = float(value)
    elif convention == "usd_per":
        rate = 1.0 / float(value)
    else:
        raise ValueError(f"unknown FX convention {convention!r}")
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"observation {value!r} under {convention!r} is not a usable rate ({rate!r})")
    return rate


# --- pure parsing -----------------------------------------------------------------
def parse_fred_csv(text: str, series_id: str | None = None) -> list[tuple[str, float]]:
    """Parse a fredgraph CSV into [(date, raw value)], newest last.

    Skips missing observations rather than reading them as zero, which would poison an average and,
    for an inverted series, divide by zero. Raises ValueError if the header is not a FRED export or
    carries a series other than `series_id`.
    """
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    head_at = next((i for i, r in enumerate(rows) if r), None)
    if head_at is None or len(rows[head_at]) < 2:
        raise ValueError("empty or headerless FRED CSV")
    header = rows[head_at]
    if header[0].strip().lower() not in DATE_COLUMNS:
        raise ValueError(f"unexpected FRED date column {header[0]!r}; expected one of {sorted(DATE_COLUMNS)}")
    got = header[1].strip()
    if series_id and got.lower() != series_id.lower():
        raise ValueError(f"FRED returned series {got!r}, expected {series_id!r}")

    out: list[tuple[str, float]] = []
    for row in rows[head_at + 1 :]:
        if len(row) < 2:
            continue
        day, raw = row[0].strip(), row[1].strip()
        if not day or raw.lower() in MISSING_VALUES:
            continue
        try:
            out.append((day, float(raw)))
        except ValueError:
            log.debug("skipping unparseable %s observation %s=%r", series_id, day, raw)
    return out


def fx_rows(
    currency: str,
    series_id: str,
    convention: str,
    text: str,
    *,
    snapshot_ts: str,
    since: str,
) -> list[dict]:
    """Rows for one currency, keeping observations on or after `since` (an ISO date).

    Raises ValueError if any observation date is not YYYY-MM-DD. FX_SCHEMA enforces that format at
    write time, long after every series has been fetched, so a payload whose dates changed shape
    (``2026-9-8``) would take the whole run down with it and throw away five healthy currencies.
    Checking here makes it one currency's error, handled exactly like a failed fetch.
    """
    rows: list[dict] = []
    for day, value in parse_fred_csv(text, series_id):
        if not DATE_RE.match(day):
            raise ValueError(f"{series_id} observation date {day!r} is not YYYY-MM-DD")
        if day < since:
            continue
        try:
            rate = to_per_usd(value, convention)
        except ValueError as exc:
            log.warning("%s %s: %s", currency, day, exc)
            continue
        rows.append(
            {
                "snapshot_ts": snapshot_ts,
                "date": day,
                "currency": currency,
                "per_usd": rate,
                "series": series_id,
            }
        )
    return rows


def identity_rows(dates: Iterable[str], *, snapshot_ts: str, currency: str = "USD") -> list[dict]:
    """USD against itself: per_usd = 1.0 on every date the other series cover, so a join on
    (date, currency) never drops a USD listing."""
    return [
        {
            "snapshot_ts": snapshot_ts,
            "date": day,
            "currency": currency,
            "per_usd": 1.0,
            "series": IDENTITY_SERIES,
        }
        for day in sorted(set(dates))
    ]


def duplicate_keys(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """(date, currency) pairs that appear more than once across the rows about to be framed.

    ``schema.fx_frame`` resolves duplicates with ``keep="first"``, which for a FRED export means the
    row printed first wins — and it does so before FX_SCHEMA's ``unique`` constraint can ever fire,
    so a revised observation would be dropped with nothing said. Surfacing them here keeps that
    silent tie-break visible in the log and in the run record.
    """
    seen: set[tuple[str, str]] = set()
    dupes: set[tuple[str, str]] = set()
    for r in rows:
        key = (str(r.get("date")), str(r.get("currency")))
        (dupes if key in seen else seen).add(key)
    return [f"{day}/{cur}" for day, cur in sorted(dupes)]


def latest_rates(df: pl.DataFrame) -> dict[str, float]:
    """Most recent non-null per_usd per currency."""
    if df.is_empty():
        return {}
    latest = (
        df.filter(pl.col("per_usd").is_not_null())
        .sort(["currency", "date"])
        .unique(subset=["currency"], keep="last", maintain_order=True)
    )
    return {str(c): float(v) for c, v in zip(latest["currency"], latest["per_usd"], strict=True)}


def band_warnings(rates: Mapping[str, float]) -> list[str]:
    """Currencies whose latest rate is outside its plausibility band — the inverted-series alarm."""
    out = []
    for cur, rate in sorted(rates.items()):
        lo, hi = SANITY_BANDS.get(cur, (0.0, float("inf")))
        if not lo <= rate <= hi:
            out.append(f"{cur}={rate:.4f} outside [{lo}, {hi}]")
    return out


# --- network ----------------------------------------------------------------------
class FredClient(HttpClient):
    """FRED serves CSV, and HttpClient only exposes get_json, so add a text GET that reuses the same
    throttle, retry and backoff policy instead of standing up a second HTTP stack."""

    def __init__(self, *, min_interval: float = 0.5) -> None:
        super().__init__(BASE, min_interval=min_interval, headers={"Accept": "text/csv, */*"})

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.get(path, params=params)
                self.calls += 1
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code} for {r.request.url}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r.text
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                if attempt == self.max_retries:
                    raise
                sleep = 5.0 if status == 429 else min(30.0, 2**attempt)
                log.warning("GET %s failed (%s); retry %d/%d in %.1fs", path, exc, attempt + 1, self.max_retries, sleep)
                time.sleep(sleep)
        raise RuntimeError(f"unreachable; last error: {last_exc!r}")

    def series_csv(self, series_id: str) -> str:
        return self.get_text(CSV_PATH, {"id": series_id})


def archive_csv(text: str, raw_dir: Path, series_id: str, hhmm: str) -> Path:
    """Archive the CSV exactly as served, before anything parses it.

    Raw files are evidence, so an existing archive for the same day is never overwritten: an
    identical body is left alone and a differing one lands beside it under the run's HHMM, then
    ``__HHMM_2``, ``__HHMM_3``... Every candidate is compared, not just the first — a third distinct
    payload in the same minute (a cron run and a manual re-run colliding) must not be dropped on the
    floor while the function returns a path holding somebody else's bytes. The parquet for a run has
    to be reproducible from the raw layer of that run.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = text.encode("utf-8")
    for n in range(64):
        if n == 0:
            path = raw_dir / f"{series_id}.csv.gz"
        elif n == 1:
            path = raw_dir / f"{series_id}__{hhmm}.csv.gz"
        else:
            path = raw_dir / f"{series_id}__{hhmm}_{n}.csv.gz"
        if not path.exists():
            with gzip.open(path, "wb") as f:
                f.write(body)
            return path
        try:
            if gzip.decompress(path.read_bytes()) == body:
                return path  # already archived, byte for byte
        except OSError:  # unreadable archive: keep it, write beside it
            continue
    raise RuntimeError(f"too many differing archives for {series_id} at {hhmm} in {raw_dir}")


# --- orchestration ----------------------------------------------------------------
def _merge_existing(df: pl.DataFrame, path: Path, key: list[str] | None = None) -> pl.DataFrame:
    """Keep rows an earlier run already wrote for this date. Same approach as `fundamentals.py`.

    A second run on a date is routine — a failed series being retried, or a short `--days` window
    after a long one. Writing only this run's rows would delete every currency the earlier run got
    and this one did not, so the new rows are merged over whatever is on disk for the same date,
    new rows winning on (date, currency).
    """
    key = key or FX_KEY
    if not path.exists():
        return df
    try:
        old = pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - a corrupt part must not block the fresh data
        log.warning("could not read %s to merge (%s); overwriting", path, exc)
        return df
    if not old.height:
        return df
    fresh = set(df.select(key).rows()) if df.height else set()
    keep = old.filter(~pl.struct(key).is_in([dict(zip(key, k, strict=False)) for k in fresh])) if fresh else old
    merged = pl.concat([keep, df], how="diagonal_relaxed") if df.height else keep
    log.info("merged %d existing rows from %s with %d new rows", keep.height, path.name, df.height)
    return merged.unique(subset=key, keep="last", maintain_order=True)


def snapshot_fx(days: int = DEFAULT_DAYS, *, client: FredClient | None = None) -> dict:
    t0 = time.monotonic()
    ts = utc_now()
    date, hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    since = (ts.date() - timedelta(days=days)).isoformat()
    raw_dir = RAW_DIR / "valuation" / "fred" / date
    client = client or FredClient()
    result: dict = {"module": "fx", "snapshot_ts": snapshot_ts, "days": days, "since": since, "errors": []}

    rows: list[dict] = []
    per_currency: dict[str, int] = {}
    identity: list[str] = []
    for currency, (series_id, convention) in sorted(FX_SERIES.items()):
        if series_id is None:  # USD: filled in from the other series' dates once they are all in
            identity.append(currency)
            continue
        try:
            text = client.series_csv(series_id)
            archive_csv(text, raw_dir, series_id, hhmm)
            got = fx_rows(currency, series_id, convention, text, snapshot_ts=snapshot_ts, since=since)
            if not got:
                raise ValueError(f"{series_id} returned no observations on or after {since}")
            rows += got
            per_currency[currency] = len(got)
            log.info("%s (%s, %s): %d observations since %s", currency, series_id, convention, len(got), since)
        except Exception as exc:  # noqa: BLE001 - one dead series must not lose the other five
            log.exception("FX series %s (%s) failed", currency, series_id)
            result["errors"].append(f"{currency}/{series_id}: {exc!r}")

    dates = {r["date"] for r in rows}
    for currency in identity:
        try:
            got = identity_rows(dates, snapshot_ts=snapshot_ts, currency=currency)
            if not got:
                raise ValueError(f"no dates to anchor the {currency} identity rows on")
            rows += got
            per_currency[currency] = len(got)
        except Exception as exc:  # noqa: BLE001
            log.exception("identity rows for %s failed", currency)
            result["errors"].append(f"{currency}/identity: {exc!r}")

    dupes = duplicate_keys(rows)
    if dupes:
        log.warning("%d duplicate (date, currency) keys, first row wins: %s", len(dupes), ", ".join(dupes[:10]))

    rates: dict[str, float] = {}
    result["new_rows"] = len(rows)
    result["rows"] = 0
    if rows:
        try:
            df = _merge_existing(fx_frame(rows), SNAP_DIR / "valuation" / "fx" / f"{date}.parquet")
            FX_SCHEMA.validate(df)
            path = write_parquet(df, SNAP_DIR / "valuation" / "fx" / f"{date}.parquet")
            rates = latest_rates(df)
            result["path"] = str(path)
            result["rows"] = df.height
            result["latest_date"] = str(df["date"].max())
            result["latest_rates"] = {c: round(v, 6) for c, v in sorted(rates.items())}
        except Exception as exc:  # noqa: BLE001 - a bad table is not written, but the run is recorded
            log.exception("building the fx table failed; nothing written")
            result["errors"].append(f"write: {exc!r}")
    else:
        log.error("no fx rows from any series; leaving any existing snapshot for %s untouched", date)

    missing = sorted(set(FX_SERIES) - set(per_currency))
    off_band = band_warnings(rates)
    result["currencies"] = per_currency
    result["checks"] = [
        check("FX series fetched", not result["errors"], f"{len(per_currency)}/{len(FX_SERIES)} currencies"),
        check("Currency coverage", not missing, "all present" if not missing else f"missing {', '.join(missing)}"),
        check(
            "Rate plausibility",
            not off_band,
            "all within band" if not off_band else "; ".join(off_band),
            warn=True,
        ),
        check(
            "Snapshot written",
            bool(result.get("path")),
            f"{result['rows']} rows in {date}.parquet" if result.get("path") else "no table written this run",
        ),
        check(
            "No duplicate rates",
            not dupes,
            "one rate per (date, currency)" if not dupes else f"{len(dupes)} duplicated: {', '.join(dupes[:5])}",
            warn=True,
        ),
    ]
    result["http_calls"] = client.calls
    result["seconds"] = round(time.monotonic() - t0, 1)
    append_jsonl(result, SNAP_DIR / "valuation" / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="days of history to keep (default: 400)")
    args = ap.parse_args(argv)
    setup_logging()
    res = snapshot_fx(days=args.days)
    log.info(
        "done fx: rows=%s currencies=%s latest=%s calls=%s seconds=%s errors=%s",
        res["rows"],
        len(res["currencies"]),
        res.get("latest_date"),
        res["http_calls"],
        res["seconds"],
        res["errors"],
    )
    for c in res["checks"]:
        if c["status"] != "pass":
            log.warning("check %s: %s (%s)", c["name"], c["status"], c["detail"])
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
