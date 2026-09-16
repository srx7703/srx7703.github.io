"""Last close, market cap, share count and both currencies for every listing in the pool.

Usage:
    uv run python -m pipelines.valuation.prices
    uv run python -m pipelines.valuation.prices --tickers COHR,300308.SZ --min-interval 1.0

Writes:
    data/snapshots/valuation/prices/<date>.parquet          one row per listing (PRICES_DTYPES)
    data/raw/valuation/yahoo_info/<date>/<ticker>.json.gz   the `info` dict exactly as fetched
    data/snapshots/valuation/runs.jsonl                     one line per run, with the error text
                                                            behind every failed ticker

Source is the `yfinance` package: `Ticker(t).info`, one call per listing. Yahoo rate-limits hard, so
calls are paced `MIN_INTERVAL` seconds apart. What is retried, and what is not:

* a 429 (raised, or recognised by message) -> retried with exponential backoff up to `MAX_ATTEMPTS`;
* an **empty** payload (`info == {}`) -> also retried. Recent yfinance hides HTTP errors by default
  and returns nothing at all for a 403/500/502/503, so an empty dict is far more often a transient
  upstream failure than a dead symbol. Retrying it is what keeps a 30-second Yahoo outage from being
  recorded as a dozen delisted companies;
* a **stub** payload (non-empty but with no quote fields, e.g. `{"trailingPegRatio": None}`) -> *not*
  retried. That is Yahoo's answer for a symbol it does not know, and an unknown symbol stays unknown;
* a 404, or any error that is neither of the above -> not retried either.

One listing failing never costs the others: every listing is fetched inside its own try/except, the
parquet is written from whatever came back, and only then does `main()` return 1.

Partial runs never shrink a day's data. `--tickers` runs are normal, so a same-date snapshot is
merged with whatever is already on disk (new rows win on `ticker`) rather than replacing it, and a
run that produced no rows at all skips the write entirely instead of blanking a good file. Raw
archives are evidence and are never overwritten either: an identical payload is left alone, and a
payload that differs from the one already stored for the day lands beside it as
`<ticker>__<HHMM>.json.gz`.

Three traps this module deliberately does not paper over:

*Pence.* A London quote comes back as `currency == "GBp"`: the price is in pence while the financials
are in pounds (Ilika: price GBp, financialCurrency GBP). The price is recorded exactly as quoted and
the "GBp" label is preserved, because that label is the only evidence of the 100x. Converting here
would silently lose it -- `fx.normalise_quote` owns that step downstream.

*Stale quotes.* `info` happily serves a price from a market that closed days ago, and nothing in the
payload says so. `price_ts` records `regularMarketTime` (epoch seconds -> ISO UTC) so downstream can
tell a Friday close read on Monday from a live one; it is null when Yahoo omits the field. A row
without a timestamp is an *unknown* age, not a fresh one, so the freshness check warns on it rather
than reporting a pass it did not verify.

*`market_cap` and `shares_outstanding` are not on the same basis for a dual listing.* For an H line
Yahoo sizes the whole issuer at the H price while `sharesOutstanding` counts only the H float, so
`price * shares_outstanding` is nowhere near `market_cap` (CATL's H line is out by ~21x). Both
numbers are recorded as served -- the cap is the one to use for valuation; the share count is the H
float and must not be used to imply a price or a per-share figure for the whole company. The
"Cap/share consistency" check records every row where the two disagree, which is how the affected
listings stay visible instead of becoming a silent 21x error in the first consumer that divides one
by the other. GBp lines land in that check too, for the 100x in the other direction.

The price currency and the reporting currency are stored separately and neither is inferred from the
other: CATL's H line trades in HKD and reports in CNY, so a single currency column would be wrong for
one of the two uses.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yfinance as yf

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import (
    RAW_DIR,
    SNAP_DIR,
    append_jsonl,
    read_json_gz,
    run_stamp,
    utc_now,
    write_json_gz,
    write_parquet,
)
from pipelines.valuation.config import BY_TICKER, COMPANIES, Company
from pipelines.valuation.schema import PRICE_KEY, PRICES_SCHEMA, prices_frame

log = logging.getLogger("valuation.prices")

SOURCE = "yahoo_info"

# --- throttle (tune these; they are the whole defence against Yahoo's 429s) ---------------
MIN_INTERVAL = 0.6  # seconds between tickers; 68 listings -> ~40 s of pacing per run
MAX_ATTEMPTS = 4  # total tries per ticker when rate-limited (>= 3 required)
BACKOFF_BASE = 5.0  # first retry sleeps this long, then doubles
BACKOFF_CAP = 60.0
# A wall-clock ceiling for the whole run. Retrying each listing is right; retrying all of them through a
# rate limit is not. Yahoo answers a soft block with an empty payload for every ticker, so per-listing
# backoff alone can run for an hour and be killed by the job timeout mid-loop, losing even the listings
# that answered. Past the deadline the loop stops, records what is left as skipped, and lets the caller
# write the rows it already has; the next scheduled run picks up the rest.
RUN_DEADLINE_SECONDS = 15 * 60

STALE_AFTER_HOURS = 96.0  # a quote older than this (weekend + holiday) is worth flagging

# `price * shares_outstanding / market_cap` should sit near 1 when both are on the same basis. A dual
# listing's H float and a GBp quote both land far outside this band; the check records them.
CAP_RATIO_LO, CAP_RATIO_HI = 0.5, 2.0

# Yahoo's errors arrive as exception classes in recent yfinance and as plain messages in older ones,
# so both are matched by name/text rather than by importing classes that may not exist.
_RATE_LIMIT_TYPES = ("YFRateLimitError",)
_RATE_LIMIT_MARKERS = ("429", "too many requests", "rate limit", "rate-limit")
_MISSING_TYPES = ("YFTickerMissingError", "YFTzMissingError", "YFInvalidPeriodError")
_MISSING_MARKERS = ("404", "not found", "no data found", "delisted", "may be delisted")

# `info` keys that carry a price; the first one present wins.
_PRICE_KEYS = ("currentPrice", "regularMarketPrice")
# Presence of any of these is what separates a real listing from Yahoo's stub for a dead symbol.
_RESOLVED_KEYS = ("currentPrice", "regularMarketPrice", "marketCap")


# --- pure helpers -------------------------------------------------------------------------
def safe_name(ticker: str) -> str:
    """Filename-safe form of a Yahoo symbol: ``300308.SZ`` -> ``300308_SZ``."""
    return ticker.replace(".", "_").replace("/", "_")


def _pos_float(value: Any) -> float | None:
    """Positive finite float, else None.

    Price, market cap and share count are all ``Check.gt(0)`` in PRICES_SCHEMA, so a zero, a NaN or a
    negative placeholder has to become null here -- letting one through would fail validation and
    cost the whole frame, which is exactly what the isolation rule forbids.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _ccy(value: Any) -> str | None:
    """Currency code as given. Case is preserved because ``GBp`` != ``GBP``."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _iso_utc(value: Any) -> str | None:
    """`regularMarketTime` -> ISO UTC string. Accepts epoch seconds, epoch ms or a datetime."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat(timespec="seconds")
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    if epoch > 1e12:  # some payloads hand back milliseconds
        epoch /= 1000.0
    try:
        return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def is_resolved(info: Mapping[str, Any] | None) -> bool:
    """True if `info` describes a real listing.

    A symbol Yahoo does not know does not always raise: it can come back as a stub dict of a couple
    of nulls. Such a payload is a failure, not a row of nulls.
    """
    if not info:
        return False
    return any(info.get(k) is not None for k in _RESOLVED_KEYS)


def jsonable(info: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys whose value will not survive a strict JSON round-trip.

    `info` mixes in timestamps and numpy scalars, and NaN would archive as the non-standard literal
    `NaN`. Dropping those keeps the raw file valid JSON for anything that reads it later.
    """
    out: dict[str, Any] = {}
    for key, value in info.items():
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            continue
        out[str(key)] = value
    return out


def parse_info(ticker: str, info: Mapping[str, Any], *, snapshot_ts: str = "") -> dict:
    """One `info` dict -> one prices row. Pure: no network, no clock, never raises."""
    price = None
    for key in _PRICE_KEYS:
        price = _pos_float(info.get(key))
        if price is not None:
            break
    return {
        "snapshot_ts": snapshot_ts,
        "ticker": ticker,
        "price": price,
        "currency": _ccy(info.get("currency")),
        "financial_currency": _ccy(info.get("financialCurrency")),
        "market_cap": _pos_float(info.get("marketCap")),
        "shares_outstanding": _pos_float(info.get("sharesOutstanding")),
        "price_ts": _iso_utc(info.get("regularMarketTime")),
    }


def _named(exc: BaseException, types: Sequence[str], markers: Sequence[str]) -> bool:
    if any(cls.__name__ in types for cls in type(exc).__mro__):
        return True
    text = str(exc).lower()
    return any(m in text for m in markers)


def is_rate_limited(exc: BaseException) -> bool:
    return _named(exc, _RATE_LIMIT_TYPES, _RATE_LIMIT_MARKERS)


def is_missing(exc: BaseException) -> bool:
    """A symbol Yahoo cannot resolve. Retrying one of these only burns the rate-limit budget."""
    return _named(exc, _MISSING_TYPES, _MISSING_MARKERS)


def select_companies(spec: str) -> list[Company]:
    """`--tickers` value -> companies, in the order given, deduplicated.

    Deduplicating matters for the run record rather than for the fetch: `prices_frame` is one row per
    ticker, so `--tickers COHR,COHR` would otherwise report two rows for a file holding one.
    """
    wanted = list(dict.fromkeys(t.strip() for t in spec.split(",") if t.strip()))
    if not wanted:
        return list(COMPANIES)
    unknown = [t for t in wanted if t not in BY_TICKER]
    if unknown:
        raise ValueError(f"unknown ticker(s): {', '.join(unknown)}")
    return [BY_TICKER[t] for t in wanted]


def _age_hours(price_ts: str | None, now: datetime) -> float | None:
    if not price_ts:
        return None
    try:
        then = datetime.fromisoformat(price_ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds() / 3600


def _cap_ratio(row: Mapping[str, Any]) -> float | None:
    """`price * shares_outstanding / market_cap`, or None when a piece is missing."""
    price, shares, cap = row.get("price"), row.get("shares_outstanding"), row.get("market_cap")
    if not price or not shares or not cap:
        return None
    return price * shares / cap


def run_checks(rows: Sequence[dict], failed: Sequence[str], requested: int, *, now: datetime) -> list[dict]:
    no_price = [r["ticker"] for r in rows if r["price"] is None]
    no_ts = [r["ticker"] for r in rows if not r["price_ts"]]
    stale = []
    for r in rows:
        age = _age_hours(r["price_ts"], now)
        if age is not None and age > STALE_AFTER_HOURS:
            stale.append(f"{r['ticker']} {age / 24:.1f}d")
    off_basis = []
    for r in rows:
        ratio = _cap_ratio(r)
        if ratio is not None and not (CAP_RATIO_LO <= ratio <= CAP_RATIO_HI):
            off_basis.append(f"{r['ticker']} {ratio:.3g}x")
    return [
        check(
            "Listing coverage",
            not failed,
            f"{len(rows)}/{requested} listings priced" + (f"; failed: {', '.join(failed)}" if failed else ""),
        ),
        check(
            "Quote present",
            not no_price,
            f"{len(no_price)} of {len(rows)} rows without a quote"
            + (f" ({', '.join(no_price)})" if no_price else ""),
        ),
        check(
            # A row without a timestamp is an age we could not verify, not a fresh quote, so it has
            # to move the status too -- otherwise a payload that drops `regularMarketTime` turns this
            # check from "freshness verified" into "nothing verified" while still reporting a pass.
            "Quote freshness",
            not stale and not no_ts,
            f"{len(stale)} quotes older than {STALE_AFTER_HOURS:.0f} h"
            + (f": {', '.join(stale)}" if stale else "")
            + f"; {len(no_ts)} without a timestamp"
            + (f" ({', '.join(no_ts)})" if no_ts else ""),
            warn=True,
        ),
        check(
            # Expected to warn on dual listings (Yahoo sizes the whole issuer but counts only the H
            # float) and on GBp quotes (pence against a cap in pounds). Recording them is the point:
            # the rows where the two numbers are on different bases stay visible.
            "Cap/share consistency",
            not off_basis,
            f"{len(off_basis)} of {len(rows)} rows where price x shares is outside "
            f"{CAP_RATIO_LO:g}-{CAP_RATIO_HI:g}x market cap"
            + (f": {', '.join(off_basis)}" if off_basis else ""),
            warn=True,
        ),
    ]


# --- network layer ------------------------------------------------------------------------
class Throttle:
    """Paces outbound Yahoo calls and counts them, like `HttpClient.calls`.

    yfinance owns the socket, so this wraps the call site instead of the request: `calls` counts
    attempts we made, which may be fewer than the HTTP requests yfinance issued per attempt.
    """

    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    def tick(self) -> None:
        if self.min_interval:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1


def fetch_info(ticker: str, *, throttle: Throttle | None = None, attempts: int = MAX_ATTEMPTS) -> dict:
    """`Ticker(t).info` for one symbol, paced and retried. Raises on a symbol that never resolves.

    Retried: a 429, and an **empty** payload. Recent yfinance hides HTTP errors (its own retries are
    off and its quote scraper swallows `HTTPError`), so a 403/500/502/503 arrives as `info == {}`
    rather than as an exception -- treating that as a dead symbol would record a brief Yahoo outage
    as a dozen delisted companies. Not retried: a 404, a stub payload (non-empty but with no quote
    fields, which is Yahoo's answer for a symbol it does not know), and any other error.
    """
    pacer = throttle or Throttle()
    last: Exception | None = None
    for attempt in range(attempts):
        pacer.tick()
        try:
            info = dict(yf.Ticker(ticker).info or {})
        except Exception as exc:  # noqa: BLE001 - yfinance raises a wide and version-dependent set
            last = exc
            if is_missing(exc) or not is_rate_limited(exc):
                raise
        else:
            if is_resolved(info):
                return info
            if info:  # a stub: Yahoo answered, and its answer is "no such symbol"
                raise LookupError(f"{ticker}: Yahoo returned a stub payload (unknown or delisted symbol)")
            last = LookupError(f"{ticker}: Yahoo returned an empty payload (transient upstream failure?)")
        if attempt == attempts - 1:
            raise last
        sleep = min(BACKOFF_CAP, BACKOFF_BASE * (2**attempt))
        log.warning("%s failed (%s); retry %d/%d in %.0fs", ticker, last, attempt + 1, attempts - 1, sleep)
        time.sleep(sleep)
    raise RuntimeError(f"unreachable; last error: {last!r}")


def archive_info(info: Mapping[str, Any], raw_dir: Path, ticker: str, hhmm: str) -> Path:
    """Archive one payload, never overwriting an earlier one for the same day.

    Raw files are evidence: the morning run's archive is the payload its committed row was parsed
    from, so an afternoon re-fetch must not replace it. An identical payload is left alone and a
    differing one lands beside it under the run's HHMM (the policy `fx.archive_csv` already uses).
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = jsonable(info)
    path = raw_dir / f"{safe_name(ticker)}.json.gz"
    if path.exists():
        try:
            if read_json_gz(path) == payload:
                return path
        except (OSError, ValueError):  # unreadable archive: keep it, write beside it
            pass
        path = raw_dir / f"{safe_name(ticker)}__{hhmm}.json.gz"
        if path.exists():
            return path
    return write_json_gz(payload, path)


def fetch_prices(
    companies: Iterable[Company],
    *,
    ts: datetime | None = None,
    min_interval: float = MIN_INTERVAL,
    attempts: int = MAX_ATTEMPTS,
    deadline_seconds: float | None = RUN_DEADLINE_SECONDS,
) -> tuple[list[dict], list[str], list[str]]:
    """Fetch every listing.

    Returns (rows, failed tickers, error strings); a failure never stops the others. The error text
    goes into the run record because runs.jsonl is permanent while the Actions log that holds the
    reason expires -- "failed: [15 tickers]" months later cannot tell a rate limit from a delisting.

    `deadline_seconds` bounds the whole loop, not each listing: past it the remaining listings are
    recorded as skipped rather than attempted, so a rate-limited run ends with the rows it did get
    instead of being killed by the job timeout with nothing written.
    """
    stamp = ts or utc_now()
    date, hhmm = run_stamp(stamp)
    snapshot_ts = stamp.isoformat(timespec="seconds")
    raw_dir = RAW_DIR / "valuation" / SOURCE / date
    throttle = Throttle(min_interval)

    rows: list[dict] = []
    failed: list[str] = []
    errors: list[str] = []
    todo = list(companies)
    started = time.monotonic()
    for i, company in enumerate(todo, start=1):
        try:
            info = fetch_info(company.ticker, throttle=throttle, attempts=attempts)
            # Archive first: the raw payload is evidence and must survive a parsing bug.
            archive_info(info, raw_dir, company.ticker, hhmm)
            row = parse_info(company.ticker, info, snapshot_ts=snapshot_ts)
            rows.append(row)
            log.info(
                "[%d/%d] %s %s price=%s %s mcap=%s",
                i,
                len(todo),
                company.ticker,
                info.get("shortName") or company.name,
                row["price"],
                row["currency"],
                row["market_cap"],
            )
        except Exception as exc:  # noqa: BLE001 - one dead listing must not cost the other 67
            failed.append(company.ticker)
            errors.append(f"{company.ticker}: {exc!r}")
            log.warning("[%d/%d] %s failed: %s", i, len(todo), company.ticker, exc)
        if deadline_seconds is not None and time.monotonic() - started > deadline_seconds:
            skipped = [c.ticker for c in todo[i:]]
            if skipped:
                failed.extend(skipped)
                errors.append(
                    f"deadline of {deadline_seconds:.0f}s reached after {i} of {len(todo)} listings; "
                    f"skipped {len(skipped)}: {', '.join(skipped[:8])}"
                    + (" ..." if len(skipped) > 8 else "")
                )
                log.error(
                    "deadline reached after %d of %d listings; %d skipped and left for the next run",
                    i, len(todo), len(skipped),
                )
            break
    log.info(
        "fetched %d/%d listings in %.1fs (%d calls, %d failed)",
        len(rows),
        len(todo),
        time.monotonic() - started,
        throttle.calls,
        len(failed),
    )
    return rows, failed, errors


def _merge_existing(df: pl.DataFrame, path: Path, key: list[str]) -> pl.DataFrame:
    """Keep the rows an earlier run already wrote for this date.

    A `--tickers` run covers part of the pool by design, and a run that Yahoo soft-blocks halfway
    covers part of it by accident. Writing only that part would throw away the listings a full run
    priced an hour earlier -- `publish.py` would then render the missing companies with no P/E at
    all -- so the new rows are merged over whatever is on disk for the same date, new rows winning
    on `ticker`.
    """
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


def write_snapshot(rows: list[dict], date: str) -> Path:
    """Validate against PRICES_SCHEMA (raising on failure) and write the day's parquet.

    Merges with a same-date file rather than replacing it, so a partial re-run can only add
    listings, never remove them.
    """
    df = prices_frame(rows)
    PRICES_SCHEMA.validate(df)
    target = SNAP_DIR / "valuation" / "prices" / f"{date}.parquet"
    df = _merge_existing(df, target, PRICE_KEY)
    PRICES_SCHEMA.validate(df)
    return write_parquet(df, target)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="", help="comma-separated Yahoo symbols (default: the whole pool)")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL, help="seconds between tickers")
    args = ap.parse_args(argv)
    setup_logging()
    try:
        companies = select_companies(args.tickers)
    except ValueError as exc:
        ap.error(str(exc))

    started = time.monotonic()
    ts = utc_now()
    date, _ = run_stamp(ts)
    rows, failed, errors = fetch_prices(companies, ts=ts, min_interval=args.min_interval)

    if rows:  # write what we got before reporting failure
        path = write_snapshot(rows, date)
    else:  # a run that got nothing must not blank a good file
        path = None
        log.error("no listings priced; leaving any existing %s snapshot untouched", date)
    checks = run_checks(rows, failed, len(companies), now=ts)
    record = {
        "table": "prices",
        "snapshot_ts": ts.isoformat(timespec="seconds"),
        "requested": len(companies),
        "rows": len(rows),
        "failed": failed,
        "errors": errors,
        "path": str(path) if path else None,
        "seconds": round(time.monotonic() - started, 1),
        "checks": checks,
    }
    append_jsonl(record, SNAP_DIR / "valuation" / "runs.jsonl")
    log.info("prices -> %s (%d rows, %d failed, %.1fs)", path, len(rows), len(failed), record["seconds"])
    for c in checks:
        log.info("check %s: %s (%s)", c["name"], c["status"], c["detail"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
