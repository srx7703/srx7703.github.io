"""Weekly consensus capture for the cybersecurity pool (ring A).

Usage:
    uv run python -m pipelines.cyber.capture
    uv run python -m pipelines.cyber.capture --only CRWD --only 688561.SS

Writes:
    data/snapshots/cyber/consensus/<date>.parquet     one row per ring A listing (CONSENSUS_COLUMNS)
    data/snapshots/cyber/consensus_runs.jsonl         one line per run, with the error text behind every miss

Reads Yahoo for every listing (price, enterprise value, target prices, rating distribution, revenue consensus for
fiscal year 0 and +1, trailing revenue and free cash flow, fiscal-year dates) and East Money for the A-share
listings (revenue consensus by calendar year and six-month rating counts). Yahoo's own target price and rating
buckets for the A-shares are stored as served; they rest on one to nine analysts and no registered item uses them.

**No payload is kept, but the fields are.** Neither source's payload is written to disk, so there is no raw archive to
re-read. The committed table holds the parsed fields the verdicts need, and most of them are Yahoo's values as served,
not derived ones. Yahoo's terms restrict building a database of its data; this table is a store of that kind, smaller
than a raw archive and not outside the terms. Section I says so. A verdict uses the tables as committed, and a parser
error is corrected by an amendment that names it.

Pure parsing is separated from fetching so the parsers are tested on fixtures. One listing failing never costs the
others; the parquet is written from whatever came back, and only then does ``main`` return 1.

``fy0_end`` is Yahoo's ``nextFiscalYearEnd``. Yahoo's ``0y`` revenue estimate is the first fiscal year whose annual
report is not yet processed, which is one year after ``lastFiscalYearEnd``, so a December company still carries 2026
as ``0y`` through January 2027 even though 2026 has ended. Section I's next-twelve-month weight is defined on that
date for exactly this reason.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl

from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, append_jsonl, run_stamp, utc_now, write_parquet
from pipelines.cyber.pool import ring

log = logging.getLogger("cyber.capture")

SNAP = SNAP_DIR / "cyber"
RUNS = SNAP / "consensus_runs.jsonl"
MIN_INTERVAL = 0.6  # seconds between Yahoo calls, same pacing as the valuation modules
EM_BASE = "https://emweb.securities.eastmoney.com"
EM_PATH = "/PC_HSF10/ProfitForecast/PageAjax"
EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://emweb.securities.eastmoney.com/",
}
EM_WINDOW_6M = "6月内"
# A run that prices fewer than this share of the pool is treated as a throttled run, not a result.
MIN_PRICED_SHARE = 0.8

# column -> polars dtype. Everything but the key is nullable: a missing field is a fact about the source.
COLUMNS: dict[str, Any] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "price": pl.Float64,
    "price_ts": pl.Utf8,
    "currency": pl.Utf8,
    "financial_currency": pl.Utf8,
    "market_cap": pl.Float64,
    "enterprise_value": pl.Float64,
    "target_mean": pl.Float64,
    "target_median": pl.Float64,
    "target_high": pl.Float64,
    "target_low": pl.Float64,
    "n_opinions": pl.Int64,
    "recommendation_mean": pl.Float64,
    "rating_strong_buy": pl.Int64,
    "rating_buy": pl.Int64,
    "rating_hold": pl.Int64,
    "rating_sell": pl.Int64,
    "rating_strong_sell": pl.Int64,
    "ttm_revenue": pl.Float64,
    "ttm_free_cash_flow": pl.Float64,
    "last_fiscal_year_end": pl.Utf8,
    "fy0_end": pl.Utf8,
    "most_recent_quarter": pl.Utf8,
    "rev_fy0_avg": pl.Float64,
    "rev_fy0_low": pl.Float64,
    "rev_fy0_high": pl.Float64,
    "rev_fy0_n": pl.Int64,
    "rev_fy1_avg": pl.Float64,
    "rev_fy1_low": pl.Float64,
    "rev_fy1_high": pl.Float64,
    "rev_fy1_n": pl.Int64,
    "em_actual_year": pl.Int64,
    "em_actual_revenue": pl.Float64,
    "em_year0": pl.Int64,
    "em_rev0_mean": pl.Float64,
    "em_rev0_n": pl.Int64,
    "em_year1": pl.Int64,
    "em_rev1_mean": pl.Float64,
    "em_rev1_n": pl.Int64,
    "em_rating_orgs_6m": pl.Int64,
    "em_rating_buy_6m": pl.Int64,
    "em_rating_add_6m": pl.Int64,
    "em_rating_neutral_6m": pl.Int64,
    "em_rating_reduce_6m": pl.Int64,
    "em_rating_sell_6m": pl.Int64,
    "source_errors": pl.Utf8,
}
KEY = ["snapshot_ts", "ticker"]

CONSENSUS_SCHEMA = pa.DataFrameSchema(
    {
        "snapshot_ts": pa.Column(str),
        "ticker": pa.Column(str),
        "price": pa.Column(float, pa.Check.gt(0), nullable=True),
        "market_cap": pa.Column(float, pa.Check.gt(0), nullable=True),
        "enterprise_value": pa.Column(float, nullable=True),
        "target_mean": pa.Column(float, pa.Check.gt(0), nullable=True),
        "target_high": pa.Column(float, pa.Check.gt(0), nullable=True),
        "target_low": pa.Column(float, pa.Check.gt(0), nullable=True),
        "n_opinions": pa.Column(int, pa.Check.ge(0), nullable=True),
        "rev_fy0_n": pa.Column(int, pa.Check.ge(0), nullable=True),
        "rev_fy1_n": pa.Column(int, pa.Check.ge(0), nullable=True),
        "em_rev0_n": pa.Column(int, pa.Check.ge(0), nullable=True),
        "em_rev1_n": pa.Column(int, pa.Check.ge(0), nullable=True),
    },
    unique=KEY,
    coerce=False,
)


# --- coercion ------------------------------------------------------------------------------------------
def _num(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or (positive and out <= 0):
        return None
    return out


def _count(value: Any) -> int | None:
    out = _num(value)
    return int(out) if out is not None and out >= 0 else None


def _date(value: Any) -> str | None:
    """Epoch seconds or an ISO date string -> ``YYYY-MM-DD``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 and text[4] == "-" else None


def _iso_utc(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, UTC).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


# --- parsers (pure) ------------------------------------------------------------------------------------
def _next_fiscal_year_end(info: Mapping[str, Any]) -> str | None:
    nxt = _date(info.get("nextFiscalYearEnd"))
    if nxt:
        return nxt
    last = _date(info.get("lastFiscalYearEnd"))
    return f"{int(last[:4]) + 1}{last[4:]}" if last else None


def parse_yahoo(
    info: Mapping[str, Any],
    revenue: Mapping[str, Mapping[str, Any]] | None,
    ratings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Yahoo ``info``, the revenue-estimate table and the latest rating distribution -> consensus columns."""
    rev = revenue or {}
    fy0, fy1 = rev.get("0y") or {}, rev.get("+1y") or {}
    price = None
    for key in ("currentPrice", "regularMarketPrice", "previousClose"):
        price = _num(info.get(key), positive=True)
        if price is not None:
            break
    rat = ratings or {}
    # An estimate row with no analysts behind it is served as 0.0; it is a missing value, not a revenue of zero.
    fy0 = fy0 if _count(fy0.get("n")) else {"n": fy0.get("n")}
    fy1 = fy1 if _count(fy1.get("n")) else {"n": fy1.get("n")}
    return {
        "price": price,
        "price_ts": _iso_utc(info.get("regularMarketTime")),
        "currency": _text(info.get("currency")),
        "financial_currency": _text(info.get("financialCurrency")),
        "market_cap": _num(info.get("marketCap"), positive=True),
        "enterprise_value": _num(info.get("enterpriseValue")),
        "target_mean": _num(info.get("targetMeanPrice"), positive=True),
        "target_median": _num(info.get("targetMedianPrice"), positive=True),
        "target_high": _num(info.get("targetHighPrice"), positive=True),
        "target_low": _num(info.get("targetLowPrice"), positive=True),
        "n_opinions": _count(info.get("numberOfAnalystOpinions")),
        "recommendation_mean": _num(info.get("recommendationMean")),
        "rating_strong_buy": _count(rat.get("strongBuy")),
        "rating_buy": _count(rat.get("buy")),
        "rating_hold": _count(rat.get("hold")),
        "rating_sell": _count(rat.get("sell")),
        "rating_strong_sell": _count(rat.get("strongSell")),
        "ttm_revenue": _num(info.get("totalRevenue")),
        "ttm_free_cash_flow": _num(info.get("freeCashflow")),
        "last_fiscal_year_end": _date(info.get("lastFiscalYearEnd")),
        "fy0_end": _next_fiscal_year_end(info),
        "most_recent_quarter": _date(info.get("mostRecentQuarter")),
        "rev_fy0_avg": _num(fy0.get("avg")),
        "rev_fy0_low": _num(fy0.get("low")),
        "rev_fy0_high": _num(fy0.get("high")),
        "rev_fy0_n": _count(fy0.get("n")),
        "rev_fy1_avg": _num(fy1.get("avg")),
        "rev_fy1_low": _num(fy1.get("low")),
        "rev_fy1_high": _num(fy1.get("high")),
        "rev_fy1_n": _count(fy1.get("n")),
    }


def parse_eastmoney(payload: Mapping[str, Any] | None, *, this_year: int) -> dict[str, Any]:
    """East Money ``PageAjax`` -> the A-share columns: revenue consensus for the next two estimate years and
    the six-month rating counts. Mean and broker count only: that is all East Money publishes for revenue."""
    out: dict[str, Any] = {}
    rows = [r for r in (payload or {}).get("yctj_list") or [] if isinstance(r, Mapping)]
    actual = [r for r in rows if r.get("YEAR_MARK") == "A" and _num(r.get("TOTAL_OPERATE_INCOME")) is not None]
    if actual:
        last = max(actual, key=lambda r: _count(r.get("YEAR")) or 0)
        out["em_actual_year"] = _count(last.get("YEAR"))
        out["em_actual_revenue"] = _num(last.get("TOTAL_OPERATE_INCOME"))
    est = sorted(
        (r for r in rows if r.get("YEAR_MARK") == "E" and (_count(r.get("YEAR")) or 0) >= this_year),
        key=lambda r: _count(r.get("YEAR")) or 0,
    )
    for slot, row in zip(("0", "1"), est[:2], strict=False):
        out[f"em_year{slot}"] = _count(row.get("YEAR"))
        out[f"em_rev{slot}_mean"] = _num(row.get("TOTAL_OPERATE_INCOME"))
        out[f"em_rev{slot}_n"] = _count(row.get("TOTAL_OPERATE_INCOME_COUNT"))
    for row in (payload or {}).get("pjtj") or []:
        if isinstance(row, Mapping) and row.get("DATE_TYPE") == EM_WINDOW_6M:
            out["em_rating_orgs_6m"] = _count(row.get("RATING_ORG_NUM"))
            out["em_rating_buy_6m"] = _count(row.get("RATING_BUY_NUM"))
            out["em_rating_add_6m"] = _count(row.get("RATING_ADD_NUM"))
            out["em_rating_neutral_6m"] = _count(row.get("RATING_NEUTRAL_NUM"))
            out["em_rating_reduce_6m"] = _count(row.get("RATING_REDUCE_NUM"))
            out["em_rating_sell_6m"] = _count(row.get("RATING_SALE_NUM"))
    return out


def consensus_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    full = [{name: row.get(name) for name in COLUMNS} for row in rows]
    return pl.DataFrame(full, schema=COLUMNS)


# --- fetching ------------------------------------------------------------------------------------------
class Pacer:
    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        self._last = 0.0

    def tick(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def _revenue_table(frame: Any) -> dict[str, dict[str, Any]]:
    """``Ticker.revenue_estimate`` (periods as the index) -> ``{period: {avg, low, high, n}}``."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for period, row in frame.iterrows():
        out[str(period)] = {
            "avg": row.get("avg"),
            "low": row.get("low"),
            "high": row.get("high"),
            "n": row.get("numberOfAnalysts"),
        }
    return out


def _rating_row(frame: Any) -> dict[str, Any]:
    """``Ticker.recommendations_summary``: the row for the current month (``0m``)."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    for _, row in frame.iterrows():
        if str(row.get("period")) == "0m":
            return {k: row.get(k) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    return {}


ATTEMPTS = 3
BACKOFF = (5.0, 15.0)  # seconds before the second and third attempt at a call


def _attempt(step: Callable[[], Any], pacer: Pacer, sleep: Callable[[float], None]) -> tuple[Any, str | None]:
    """Run one Yahoo call, retrying an empty answer or a raised error (a 429 arrives as either) with a pause."""
    last: str | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(BACKOFF[attempt - 1])
        pacer.tick()
        try:
            got = step()
        except Exception as exc:  # noqa: BLE001 - one failed call must not end the listing, let alone the run
            last = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        if got is not None and not (hasattr(got, "empty") and got.empty) and got != {}:
            return got, None
        last = "empty"
    return None, last


def fetch_yahoo(
    ticker: str, pacer: Pacer, sleep: Callable[[float], None] = time.sleep
) -> tuple[dict, dict, dict, list[str]]:
    """Info, revenue table and rating row for one listing; each call is its own try so a miss is partial."""
    import yfinance as yf  # imported here so the parsers and their tests do not need the network package

    errors: list[str] = []
    handle = yf.Ticker(ticker)
    info: dict = {}
    revenue: dict = {}
    ratings: dict = {}
    for name, step in (
        ("info", lambda: handle.info),
        ("revenue_estimate", lambda: handle.revenue_estimate),
        ("recommendations_summary", lambda: handle.recommendations_summary),
    ):
        got, problem = _attempt(step, pacer, sleep)
        if problem:
            errors.append(f"{name}: {problem}")
            continue
        if name == "info":
            info = got or {}
        elif name == "revenue_estimate":
            revenue = _revenue_table(got)
        else:
            ratings = _rating_row(got)
    return info, revenue, ratings, errors


def em_code(ticker: str) -> str:
    return ("SZ" if ticker.endswith(".SZ") else "SH") + ticker.split(".")[0]


def fetch_eastmoney(http: HttpClient, ticker: str) -> dict:
    payload = http.get_json(EM_PATH, {"code": em_code(ticker)})
    if not isinstance(payload, dict):
        raise ValueError(f"{ticker}: expected a JSON object, got {type(payload).__name__}")
    return payload


# --- orchestration -------------------------------------------------------------------------------------
def snapshot_path(day: str) -> Path:
    return SNAP / "consensus" / f"{day}.parquet"


def capture(
    tickers: list[str],
    *,
    snapshot_ts: str,
    year: int,
    yahoo: Callable[[str], tuple[dict, dict, dict, list[str]]],
    eastmoney: Callable[[str], dict] | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    for ticker in tickers:
        row: dict[str, Any] = {"snapshot_ts": snapshot_ts, "ticker": ticker}
        errors: list[str] = []
        try:
            info, revenue, ratings, errs = yahoo(ticker)
            errors += errs
            row.update(parse_yahoo(info, revenue, ratings))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"yahoo: {type(exc).__name__}: {str(exc)[:120]}")
        if eastmoney is not None and ticker.endswith((".SS", ".SZ")):
            try:
                row.update(parse_eastmoney(eastmoney(ticker), this_year=year))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"eastmoney: {type(exc).__name__}: {str(exc)[:120]}")
        if errors:
            row["source_errors"] = "; ".join(errors)
            failed[ticker] = row["source_errors"]
        rows.append(row)
    return rows, failed


def _merge_existing(df: pl.DataFrame, path: Path) -> pl.DataFrame:
    """Rows of the same run (same ``snapshot_ts``) are replaced; every other run of the day is kept beside them.

    A day's file can therefore hold several runs. The weekly snapshot is the last run of the Monday in which at least
    ``MIN_PRICED_SHARE`` of ring A was priced, chosen by ``readings.weekly_snapshots``."""
    if not path.exists():
        return df
    old = pl.read_parquet(path)
    old = old.join(df.select(KEY), on=KEY, how="anti")
    return pl.concat([old.select(df.columns), df], how="vertical_relaxed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[], metavar="TICKER", help="restrict to these tickers")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL)
    args = ap.parse_args(argv)
    setup_logging()

    tickers = [e.ticker for e in ring("A")]
    if args.only:
        tickers = [t for t in tickers if t in set(args.only)]
    if not tickers:
        log.error("no listings selected")
        return 1

    ts = utc_now()
    day, _ = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    pacer = Pacer(args.min_interval)
    em_http = HttpClient(base_url=EM_BASE, min_interval=0.25, headers=EM_HEADERS)
    started = time.monotonic()
    rows, failed = capture(
        tickers,
        snapshot_ts=snapshot_ts,
        year=ts.year,
        yahoo=lambda t: fetch_yahoo(t, pacer),
        eastmoney=lambda t: fetch_eastmoney(em_http, t),
    )
    priced = sum(1 for r in rows if r.get("price") is not None)
    written = None
    error = None
    try:
        df = consensus_frame(rows)
        CONSENSUS_SCHEMA.validate(df)
        if priced:
            written = write_parquet(_merge_existing(df, snapshot_path(day)), snapshot_path(day))
        else:
            log.error("no listing was priced; leaving the %s snapshot untouched", day)
    except Exception as exc:  # noqa: BLE001 - the run record below must survive a bad frame
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.exception("could not write the consensus snapshot")
    record = {
        "module": "cyber.capture",
        "snapshot_ts": snapshot_ts,
        "listings": len(tickers),
        "priced": priced,
        "with_target": sum(1 for r in rows if r.get("target_mean") is not None),
        "with_revenue_consensus": sum(1 for r in rows if (r.get("rev_fy1_n") or r.get("em_rev1_n"))),
        "failed": failed,
        "error": error,
        "path": str(written) if written else None,
        "seconds": round(time.monotonic() - started, 1),
    }
    append_jsonl(record, RUNS)
    log.info(
        "priced=%s/%s target=%s revenue=%s failed=%s",
        priced,
        len(tickers),
        record["with_target"],
        record["with_revenue_consensus"],
        len(failed),
    )
    if error or priced < MIN_PRICED_SHARE * len(tickers):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
