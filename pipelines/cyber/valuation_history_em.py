"""East Money's daily valuation history for the A-share listings of the cybersecurity pool, and the China panel.

Usage:
    uv run python -m pipelines.cyber.valuation_history_em
    uv run python -m pipelines.cyber.valuation_history_em --only 002439.SZ --only 688561.SS

Writes:
    data/snapshots/cyber/valuation_history_em/<date>.parquet
        ticker, date, ps_ttm, pb, pe_ttm, total_market_cap
    data/snapshots/cyber/valuation_history_em/revenue/<date>.parquet
        ticker, period_end, revenue, yoy_reported, notice_date
    data/snapshots/cyber/valuation_history_em_runs.jsonl
        one line per run, with the error text behind every miss

The panel the page reads is built from those two tables by :func:`build`, which is pure. ``main`` only fetches and
stores.

Why this exists
---------------
A price-to-sales multiple is market value over trailing revenue, so it rises when the price rises and also when the
revenue falls. For the A-share cybersecurity names the second cause is common: several have shrunk for two years. The
panel therefore prints each listing's PS_TTM, where it sits in its own history since 2018, and the latest year-on-year
revenue growth beside it. A high percentile with falling revenue is a denominator story, not a re-rating, and the
``ps_percentile_while_revenue_falling`` flag marks exactly that.

What East Money serves (found by calling it, 2026-10-17)
--------------------------------------------------------
``RPT_VALUEANALYSIS_DET`` on ``datacenter-web.eastmoney.com/api/data/v1/get``, filtered by ``SECURITY_CODE`` (the bare
six digits, no exchange letters) and ``TRADE_DATE``, one row per trading day, newest first when sorted ``-1``:

* ``PS_TTM`` total market cap over trailing-twelve-month revenue; ``PB_MRQ`` price over book at the latest quarter;
  ``PE_TTM`` price over trailing earnings (negative when the company lost money, so it is not a valuation and nothing
  here ranks it); ``TOTAL_MARKET_CAP`` in yuan; ``CLOSE_PRICE``; ``TRADE_DATE`` as ``"2026-10-16 00:00:00"``.
* The implied denominator, ``TOTAL_MARKET_CAP / PS_TTM``, steps on the day a report is published (for Venustech
  2025-04-15, 04-28, 08-27 and 10-29, the annual, first-quarter, half-year and third-quarter notices), not on the
  period end, so the history is as it was known on each day and has no look-ahead.
* The history starts on 2018-01-02 for the oldest listings, whatever the filter says, and on the listing date for
  newer ones; the filter on 2018-01-01 is kept so the window is a stated choice and not an accident of the source.
* Paging: ``result.pages`` and ``result.count`` (rows matching the filter) are returned, ``pageSize`` 2000 is honoured
  (5000 is accepted), and a page past the end is not an empty list but ``{"success": false, "code": 9201, "result":
  null}``. The same answer on page 1 means East Money has no history for that code. ``SECUCODE`` carries ``.SH`` for
  Shanghai where the pool says ``.SS``; the filter does not use it.

Percentiles are ours. ``percentile`` is the share of the listing's own trading days since 2018 with a value at or below
the latest, ties and the latest day included, so the minimum is one day in n and the maximum is 1. East Money's own
percentile fields are never read.

Revenue growth comes from ``RPT_LICO_FN_CPD`` (the report list also used by ``pipelines.valuation.fundamentals``):
cumulative year-to-date revenue by report date. ``revenue_yoy`` is the latest report's cumulative revenue over the same
period a year earlier, minus one, as a fraction; ``revenue_period`` says which (``2026H1`` is January to June,
``2026Q1-Q3`` January to September). East Money's own ``YSTZ`` is read only when the prior-year row is missing, and
agreed with our division to two decimals for all 31 listings on 2026-10-17. Listings do not all report on the same day,
so after a quarter end the panel mixes periods and says so per row.
"""

from __future__ import annotations

import argparse
import logging
import math
import re
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, append_jsonl, run_stamp, utc_now, write_parquet
from pipelines.cyber.pool import POOL

log = logging.getLogger("cyber.valuation_history_em")

# --- where things live ------------------------------------------------------------------------------------
SNAP = SNAP_DIR / "cyber"
RUNS = SNAP / "valuation_history_em_runs.jsonl"
HISTORY_SUBDIR = "valuation_history_em"
REVENUE_SUBDIR = "revenue"

# --- the source ---------------------------------------------------------------------------------------------
EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"}
SOURCE_URL = "https://data.eastmoney.com/gzfx/"  # East Money's public valuation analysis pages
REPORT_URL = "https://data.eastmoney.com/bbsj/"  # East Money's public financial report pages
VALUATION_REPORT = "RPT_VALUEANALYSIS_DET"
REVENUE_REPORT = "RPT_LICO_FN_CPD"
MIN_INTERVAL = 0.3  # seconds between calls, the pace the other East Money modules use
PAGE_SIZE = 2000  # one listing's full history is about 2,100 rows, so one or two pages
MAX_PAGES = 20  # a stop for a paging loop that never ends; 20 pages is 40,000 trading days
REVENUE_ROWS = 12  # three years of report dates: the latest, its prior-year twin, and fallbacks for either
EMPTY_CODE = 9201  # East Money's "returned no data" answer, for a page past the end or a code it does not know

# --- the choices of the panel ---------------------------------------------------------------------------------
HISTORY_START = "2018-01-01"  # the percentile is within each listing's own history since this date
TOP_QUARTILE = 0.75  # a percentile at or above this is "in the top quartile of its own history"
OVERLAP_DAYS = 30  # a weekly file repeats this many days of history, so a restated recent value replaces the old one
MIN_HISTORY_DAYS = 250  # about one trading year; fewer days and the percentile is not published, the count is
PANEL_RINGS = ("A", "B")  # ring F is delisted or acquired, ring C is private
MIN_COVERED_SHARE = 0.8  # a run in which fewer listings returned a history is reported as failed (exit 1)
MAX_AGE_DAYS = 10  # newest trading day older than this against the run date is a stale panel (Golden Week fits)
STALE_LAG_DAYS = 7  # a listing whose last day is this far behind the newest is named (suspended, delisting)
FLAG_PS_HIGH_REVENUE_FALLING = "ps_percentile_while_revenue_falling"
FLAGS = (FLAG_PS_HIGH_REVENUE_FALLING,)

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_PERIOD_SUFFIX = {3: "Q1", 6: "H1", 9: "Q1-Q3", 12: "FY"}  # cumulative year to date, A-shares all close in December


class SourceError(RuntimeError):
    """East Money answered, and the answer cannot be used (an error flag, the wrong security, an incomplete history)."""


# --- coercion ------------------------------------------------------------------------------------------------
def _num(value: Any, *, positive: bool = False) -> float | None:
    """A finite float, or None. Booleans and strings that are not numbers are missing values, not 0 or 1."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or (positive and out <= 0):
        return None
    return out


def _day(value: Any) -> str | None:
    """``"2026-10-16 00:00:00"``, ``"2026-10-16"`` or a date -> ``"2026-10-16"``; not a real day -> None."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    m = _ISO.match(value.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def _is_empty(payload: Any) -> bool:
    """East Money's answer for a page past the end or an unknown code: ``success`` false with code 9201."""
    return isinstance(payload, Mapping) and not payload.get("success") and payload.get("code") == EMPTY_CODE


def _records(payload: Any, what: str) -> list[Mapping[str, Any]]:
    """The row list of a datacenter payload. East Money answers HTTP 200 for failures, so ``success`` is read."""
    if not isinstance(payload, Mapping):
        raise SourceError(f"{what}: expected a JSON object, got {type(payload).__name__}")
    if _is_empty(payload):
        return []
    if not payload.get("success"):
        raise SourceError(f"{what}: success=false, code={payload.get('code')!r}, message={payload.get('message')!r}")
    data = (payload.get("result") or {}).get("data")
    return [r for r in data or [] if isinstance(r, Mapping)]


def em_code(ticker: str) -> str:
    """``'002439.SZ'`` or ``'688561.SS'`` -> ``'002439'``, the form East Money's ``SECURITY_CODE`` filter takes."""
    code, _, suffix = ticker.partition(".")
    if suffix not in ("SS", "SZ") or not (len(code) == 6 and code.isdigit()):
        raise ValueError(f"not an A-share ticker: {ticker!r}")
    return code


# --- parsers (pure) ------------------------------------------------------------------------------------------
def parse(payload: Any, *, code: str | None = None) -> list[dict[str, Any]]:
    """One ``RPT_VALUEANALYSIS_DET`` page -> ``[{date, ps_ttm, pb, pe_ttm, total_market_cap}]``, oldest first.

    Only these five fields are kept; the payload's names, board codes and prices are not stored. A day with no usable
    date is dropped, and a day repeated on the page keeps its last row. A non-positive PS_TTM, PB or market cap is a
    missing value (a negative PB means negative book value, and a ratio to it ranks nothing). PE_TTM is kept as served:
    a negative one means a loss. With ``code``, a row for a different security raises, because a filter East Money
    ignored would otherwise put another company's history under this ticker. An empty answer (code 9201) is ``[]``."""
    out: dict[str, dict[str, Any]] = {}
    for r in _records(payload, "valuation history"):
        if code is not None and str(r.get("SECURITY_CODE") or "").strip() != code:
            raise SourceError(f"valuation history for {code} contains a row for {r.get('SECURITY_CODE')!r}")
        day = _day(r.get("TRADE_DATE"))
        if day is None:
            continue
        out[day] = {
            "date": day,
            "ps_ttm": _num(r.get("PS_TTM"), positive=True),
            "pb": _num(r.get("PB_MRQ"), positive=True),
            "pe_ttm": _num(r.get("PE_TTM")),
            "total_market_cap": _num(r.get("TOTAL_MARKET_CAP"), positive=True),
        }
    return [out[d] for d in sorted(out)]


def page_info(payload: Any) -> tuple[int, int]:
    """``(pages, count)`` of a payload: the number of pages at the page size asked for and the matching rows in all.
    ``(0, 0)`` when the payload has neither (an empty answer)."""
    result = payload.get("result") if isinstance(payload, Mapping) else None
    result = result if isinstance(result, Mapping) else {}
    pages, count = _num(result.get("pages")), _num(result.get("count"))
    return int(pages) if pages and pages > 0 else 0, int(count) if count and count > 0 else 0


def period_label(period_end: str) -> str | None:
    """``'2026-06-30'`` -> ``'2026H1'``. Labels are cumulative year to date: ``Q1`` three months, ``H1`` six,
    ``Q1-Q3`` nine, ``FY`` twelve. None for a month that is not a quarter end."""
    day = _day(period_end)
    suffix = _PERIOD_SUFFIX.get(int(day[5:7])) if day else None
    return f"{day[:4]}{suffix}" if day and suffix else None


def parse_revenue(payload: Any, *, code: str | None = None) -> list[dict[str, Any]]:
    """One ``RPT_LICO_FN_CPD`` page -> ``[{period_end, revenue, yoy_reported, notice_date}]``, oldest first.

    ``revenue`` is ``TOTAL_OPERATE_INCOME``, cumulative from 1 January to the period end, in yuan. ``yoy_reported`` is
    East Money's ``YSTZ`` converted from percent to a fraction; it is kept to read when the prior-year row is missing
    and to compare with our own division. ``notice_date`` is the day the report was published, so a replay can ignore
    a report that was not yet public. A row with neither revenue nor a reported growth is dropped."""
    out: dict[str, dict[str, Any]] = {}
    for r in _records(payload, "financial report"):
        if code is not None and str(r.get("SECURITY_CODE") or "").strip() != code:
            raise SourceError(f"financial report for {code} contains a row for {r.get('SECURITY_CODE')!r}")
        period_end = _day(r.get("REPORTDATE") or r.get("REPORT_DATE"))
        if period_end is None or period_label(period_end) is None:
            continue
        revenue = _num(r.get("TOTAL_OPERATE_INCOME"))
        reported = _num(r.get("YSTZ"))
        if reported is not None and reported < -100.0:
            reported = None  # revenue cannot fall by more than all of it; the figure is junk, not a growth rate
        if revenue is None and reported is None:
            continue
        out[period_end] = {
            "period_end": period_end,
            "revenue": revenue,
            "yoy_reported": reported / 100.0 if reported is not None else None,
            "notice_date": _day(r.get("NOTICE_DATE")),
        }
    return [out[d] for d in sorted(out)]


# --- the percentile (pure) -----------------------------------------------------------------------------------
def _series(series: Mapping[Any, Any] | Iterable[tuple[Any, Any]]) -> dict[str, float | None]:
    """``{date: value}`` or ``[(date, value)]`` -> ``{'YYYY-MM-DD': float | None}``; a repeated day keeps the last."""
    pairs = series.items() if isinstance(series, Mapping) else series
    out: dict[str, float | None] = {}
    for key, value in pairs:
        day = _day(key)
        if day is not None:
            out[day] = _num(value)
    return out


def history_window(
    series: Mapping[Any, Any] | Iterable[tuple[Any, Any]],
    as_of: str | date | None = None,
    *,
    start: str = HISTORY_START,
) -> list[tuple[str, float]]:
    """The days with a value from ``start`` to ``as_of`` inclusive, oldest first. Without ``as_of`` the window ends
    on the last day in the series."""
    by_day = _series(series)
    if not by_day:
        return []
    end = _day(as_of) if as_of is not None else max(by_day)
    if end is None:
        return []
    return [(d, v) for d, v in sorted(by_day.items()) if start <= d <= end and v is not None]


def percentile(
    series: Mapping[Any, Any] | Iterable[tuple[Any, Any]],
    as_of: str | date | None = None,
    *,
    start: str = HISTORY_START,
    min_days: int = 1,
) -> float | None:
    """The share of the history days with a value at or below the value on ``as_of``, within ``[start, as_of]``.

    ``series`` is ``{date: value}`` or ``[(date, value)]``; dates may be ISO strings, East Money's
    ``'2026-10-16 00:00:00'`` or ``date`` objects, and a missing value (None, NaN) is a day without a value that is
    left out of the count. Without ``as_of`` it is the last day in the series. The value on ``as_of`` is the latest
    value; if that day is absent or has no value the answer is None, because the percentile of an older day is a
    different statement. Ties count as at or below, and the latest day is part of the history, so the result lies in
    ``[1/n, 1]``: the cheapest day in the window is ``1/n`` and a new high is exactly 1. None as well when the window
    holds fewer than ``min_days`` days. East Money's own percentile fields are never used."""
    by_day = _series(series)
    if not by_day:
        return None
    end = _day(as_of) if as_of is not None else max(by_day)
    latest = by_day.get(end) if end else None
    if latest is None:
        return None
    window = history_window(by_day.items(), end, start=start)
    if len(window) < max(min_days, 1) or end < start:
        return None
    return sum(1 for _, v in window if v <= latest) / len(window)


# --- revenue growth (pure) -----------------------------------------------------------------------------------
def revenue_growth(rows: Sequence[Mapping[str, Any]] | None, as_of: str | date | None = None) -> dict[str, Any] | None:
    """The latest report with a computable year-on-year revenue growth, from :func:`parse_revenue` rows.

    Returns ``{revenue_yoy, revenue_period, period_end, notice_date, revenue, prior_revenue, basis}`` or None.
    ``revenue_yoy`` is a fraction (``-0.34`` is a fall of 34%), cumulative year to date against the same months a year
    earlier. ``basis`` is ``computed`` when both cumulative revenues are present and the prior one is positive, and
    ``reported`` when East Money's own growth figure had to stand in for a missing prior-year row. Reports are tried
    newest first, so an older period is used only when the newer one has neither, and ``revenue_period`` always names
    the one used. With ``as_of`` a report published after that day is ignored, so a replay sees what was public."""
    bound = _day(as_of) if as_of is not None else None
    usable = [
        r
        for r in rows or []
        if _day(r.get("period_end"))
        and period_label(r["period_end"])
        and not (bound and (r.get("notice_date") or "") > bound)
    ]
    by_end = {_day(r["period_end"]): r for r in usable}
    for end in sorted(by_end, reverse=True):
        row = by_end[end]
        label = period_label(end)
        prior = by_end.get(f"{int(end[:4]) - 1}{end[4:]}")
        cur, old = _num(row.get("revenue")), _num((prior or {}).get("revenue"), positive=True)
        if cur is not None and cur >= 0 and old is not None:
            yoy, basis = cur / old - 1.0, "computed"
        elif _num(row.get("yoy_reported")) is not None:
            yoy, basis = float(row["yoy_reported"]), "reported"
        else:
            continue
        return {
            "revenue_yoy": yoy,
            "revenue_period": label,
            "period_end": end,
            "notice_date": row.get("notice_date"),
            "revenue": cur,
            "prior_revenue": old,
            "basis": basis,
        }
    return None


# --- fetching ------------------------------------------------------------------------------------------------
def client(min_interval: float = MIN_INTERVAL) -> HttpClient:
    return HttpClient(min_interval=min_interval, headers=EM_HEADERS)


def history_params(code: str, page: int, *, start: str = HISTORY_START, page_size: int = PAGE_SIZE) -> dict[str, Any]:
    return {
        "reportName": VALUATION_REPORT,
        "columns": "ALL",
        "filter": f"""(SECURITY_CODE="{code}")(TRADE_DATE>='{start}')""",
        "pageSize": page_size,
        "pageNumber": page,
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "source": "WEB",
        "client": "WEB",
    }


def fetch(
    ticker: str, http: HttpClient, *, start: str = HISTORY_START, page_size: int = PAGE_SIZE
) -> list[dict[str, Any]]:
    """One security's whole valuation history since ``start``: every page, parsed, deduplicated, oldest first.

    Pages are read until ``result.pages`` is reached or East Money answers "no data" for a page past the end. It
    raises :class:`SourceError` if page 1 has no data (East Money does not know the code), if a page names another
    security, if the loop does not end, or if fewer distinct days came back than ``result.count`` promised, because a
    percentile over a truncated history is wrong without looking wrong."""
    code = em_code(ticker)
    days: dict[str, dict[str, Any]] = {}
    count = 0
    for page in range(1, MAX_PAGES + 1):
        payload = http.get_json(EM_URL, history_params(code, page, start=start, page_size=page_size))
        rows = parse(payload, code=code)
        if not rows:
            if page == 1:
                raise SourceError(f"{ticker}: East Money returned no valuation history for {code}")
            break
        for r in rows:
            days[r["date"]] = r  # the same day on two pages (a day was added mid-run) keeps one row
        pages, total = page_info(payload)
        count = max(count, total)
        if pages and page >= pages:
            break  # with no page count the loop runs on until East Money says there is nothing more
    else:
        raise SourceError(f"{ticker}: valuation history did not end after {MAX_PAGES} pages")
    if len(days) < count:
        raise SourceError(f"{ticker}: valuation history incomplete, {len(days)} of {count} days")
    return [days[d] for d in sorted(days) if d >= start]


def fetch_revenue(ticker: str, http: HttpClient, *, rows: int = REVENUE_ROWS) -> list[dict[str, Any]]:
    """One security's latest ``rows`` financial report dates (cumulative revenue and its reported growth), parsed.
    Raises :class:`SourceError` when East Money has none."""
    code = em_code(ticker)
    payload = http.get_json(
        EM_URL,
        {
            "reportName": REVENUE_REPORT,
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": rows,
            "pageNumber": 1,
            "sortColumns": "REPORTDATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
    )
    parsed = parse_revenue(payload, code=code)
    if not parsed:
        raise SourceError(f"{ticker}: East Money returned no financial report rows for {code}")
    return parsed


# --- schemas -------------------------------------------------------------------------------------------------
_DAY = r"^\d{4}-\d{2}-\d{2}$"
_TICKER = r"^\d{6}\.(SS|SZ)$"

HISTORY_KEY = ["ticker", "date"]
HISTORY_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "date": pl.Utf8,
    "ps_ttm": pl.Float64,
    "pb": pl.Float64,
    "pe_ttm": pl.Float64,
    "total_market_cap": pl.Float64,
}
HISTORY_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_matches(_TICKER)),
        "date": pa.Column(str, pa.Check.str_matches(_DAY)),
        "ps_ttm": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
        "pb": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
        "pe_ttm": pa.Column(pl.Float64, nullable=True),
        "total_market_cap": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
    },
    unique=HISTORY_KEY,
    strict=True,
    coerce=False,
)

REVENUE_KEY = ["ticker", "period_end"]
REVENUE_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "period_end": pl.Utf8,
    "revenue": pl.Float64,
    "yoy_reported": pl.Float64,
    "notice_date": pl.Utf8,
}
REVENUE_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_matches(_TICKER)),
        "period_end": pa.Column(str, pa.Check.str_matches(_DAY)),
        "revenue": pa.Column(pl.Float64, nullable=True),
        "yoy_reported": pa.Column(pl.Float64, pa.Check.ge(-1.0), nullable=True),
        "notice_date": pa.Column(str, pa.Check.str_matches(_DAY), nullable=True),
    },
    unique=REVENUE_KEY,
    strict=True,
    coerce=False,
)

FLAGS_DTYPE = pl.Struct({FLAG_PS_HIGH_REVENUE_FALLING: pl.Boolean})
PANEL_KEY = ["ticker"]
PANEL_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "name_cn": pl.Utf8,
    "ring": pl.Utf8,
    "ps_ttm": pl.Float64,
    "ps_percentile": pl.Float64,
    "pb": pl.Float64,
    "pb_percentile": pl.Float64,
    "total_market_cap": pl.Float64,
    "n_history_days": pl.Int64,
    "history_start": pl.Utf8,
    "as_of": pl.Utf8,
    "revenue_yoy": pl.Float64,
    "revenue_period": pl.Utf8,
    "flags": FLAGS_DTYPE,
}


def _flag_matches_inputs(d: pa.PolarsData) -> pl.LazyFrame:
    """True exactly when the percentile is in the top quartile and revenue fell; null when either is missing."""
    want = (
        pl.when(pl.col("ps_percentile").is_null() | pl.col("revenue_yoy").is_null())
        .then(None)
        .otherwise((pl.col("ps_percentile") >= TOP_QUARTILE) & (pl.col("revenue_yoy") < 0))
    )
    return d.lazyframe.select(pl.col("flags").struct.field(FLAG_PS_HIGH_REVENUE_FALLING).eq_missing(want))


def _percentile_needs_value(d: pa.PolarsData) -> pl.LazyFrame:
    return d.lazyframe.select(
        (pl.col("ps_percentile").is_null() | pl.col("ps_ttm").is_not_null())
        & (pl.col("pb_percentile").is_null() | pl.col("pb").is_not_null())
    )


def _revenue_fields_together(d: pa.PolarsData) -> pl.LazyFrame:
    return d.lazyframe.select(pl.col("revenue_yoy").is_null() == pl.col("revenue_period").is_null())


def _history_is_dated(d: pa.PolarsData) -> pl.LazyFrame:
    """No days ranked means no start day; a listing with rows but no PS_TTM keeps its ``as_of``, so a blank is dated."""
    return d.lazyframe.select(
        (pl.col("history_start").is_null() == (pl.col("n_history_days") == 0))
        & (pl.col("as_of").is_not_null() | (pl.col("n_history_days") == 0))
        & (pl.col("history_start").is_null() | (pl.col("history_start") <= pl.col("as_of")))
    )


CHINA_PANEL_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_matches(_TICKER)),
        "name": pa.Column(str),
        "name_cn": pa.Column(str, nullable=True),
        "ring": pa.Column(str, pa.Check.isin(list(PANEL_RINGS))),
        "ps_ttm": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
        "ps_percentile": pa.Column(pl.Float64, [pa.Check.gt(0), pa.Check.le(1)], nullable=True),
        "pb": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
        "pb_percentile": pa.Column(pl.Float64, [pa.Check.gt(0), pa.Check.le(1)], nullable=True),
        "total_market_cap": pa.Column(pl.Float64, pa.Check.gt(0), nullable=True),
        "n_history_days": pa.Column(pl.Int64, pa.Check.ge(0)),
        "history_start": pa.Column(str, pa.Check.str_matches(_DAY), nullable=True),
        "as_of": pa.Column(str, pa.Check.str_matches(_DAY), nullable=True),
        "revenue_yoy": pa.Column(pl.Float64, pa.Check.ge(-1.0), nullable=True),
        "revenue_period": pa.Column(str, pa.Check.str_matches(r"^\d{4}(Q1|H1|Q1-Q3|FY)$"), nullable=True),
        "flags": pa.Column(FLAGS_DTYPE),
    },
    unique=PANEL_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_flag_matches_inputs, error="flag must follow the percentile and the revenue growth"),
        pa.Check(_percentile_needs_value, error="a percentile needs the value it ranks"),
        pa.Check(_revenue_fields_together, error="revenue_yoy and revenue_period come together"),
        pa.Check(_history_is_dated, error="history_start/as_of must agree with n_history_days"),
    ],
)


# --- the panel (pure) ----------------------------------------------------------------------------------------
def _field(obj: Any, key: str) -> Any:
    """A listing is a ``metrics.listing_rows`` dict or a ``pool.Entry``."""
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def a_share_listings(listings: Iterable[Any]) -> list[Any]:
    """The listings the panel covers: ring A or B, ticker ending ``.SS`` or ``.SZ``."""
    return [
        x
        for x in listings
        if _field(x, "ring") in PANEL_RINGS and str(_field(x, "ticker") or "").endswith((".SS", ".SZ"))
    ]


def _flags(value: bool | None) -> dict[str, bool | None]:
    return {FLAG_PS_HIGH_REVENUE_FALLING: value}


def _flag(ps_percentile: float | None, revenue_yoy: float | None) -> bool | None:
    """True when the percentile is in the top quartile of the listing's history and revenue fell year on year.
    None when either number is missing: not flagged and not cleared."""
    if ps_percentile is None or revenue_yoy is None:
        return None
    return ps_percentile >= TOP_QUARTILE and revenue_yoy < 0


def panel_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=PANEL_DTYPES) if rows else pl.DataFrame(schema=PANEL_DTYPES)


def build(
    history_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
    listings: Iterable[Any],
    revenue_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    as_of: str | date | None = None,
    min_days: int = MIN_HISTORY_DAYS,
) -> pl.DataFrame:
    """The ``china_panel`` table: one row per A-share listing of ring A or B, validated by ``CHINA_PANEL_SCHEMA``.

    ``history_by_ticker`` maps a ticker to :func:`parse` rows (what :func:`fetch` returns or :func:`load_latest`
    reads); ``listings`` are ``metrics.listing_rows`` dicts or ``pool.Entry`` objects, and anything that is not an
    A-share of ring A or B is ignored; ``revenue_by_ticker`` maps a ticker to :func:`parse_revenue` rows and may be
    left out, in which case every ``revenue_yoy`` is null. ``as_of`` replays the panel at an earlier day: history after
    it and reports published after it are ignored. Without it each row ends on its own last trading day.

    Columns: ``ps_ttm`` and ``pb`` are the multiples (times) on ``as_of``, the listing's last day; ``ps_percentile``
    and ``pb_percentile`` are their shares of days since 2018 at or below that value, each null when the listing has
    fewer than ``min_days`` days or no value that day; ``total_market_cap`` is in yuan; ``n_history_days`` and
    ``history_start`` describe the PS_TTM history ranked; ``revenue_yoy`` is a fraction and ``revenue_period`` the
    cumulative period it covers (``2026H1``); ``flags.ps_percentile_while_revenue_falling`` is true when the PS_TTM
    percentile is at or above ``TOP_QUARTILE`` and ``revenue_yoy`` is negative, and null when either is missing.
    A listing with no history is kept with nulls and ``n_history_days`` 0, so the page can say why it is blank."""
    bound = _day(as_of) if as_of is not None else None
    rows = []
    for item in sorted(a_share_listings(listings), key=lambda x: (_field(x, "ring"), _field(x, "ticker"))):
        ticker = _field(item, "ticker")
        history = {_day(d.get("date")): d for d in history_by_ticker.get(ticker) or [] if _day(d.get("date"))}
        days = sorted(d for d in history if bound is None or d <= bound)
        end = days[-1] if days else None
        last = history[end] if end else {}
        ps_series = {d: history[d].get("ps_ttm") for d in days}
        ps_window = history_window(ps_series, end)
        ps_pct = percentile(ps_series, end, min_days=min_days) if end else None
        pb_pct = percentile({d: history[d].get("pb") for d in days}, end, min_days=min_days) if end else None
        growth = revenue_growth((revenue_by_ticker or {}).get(ticker), as_of)
        yoy = growth["revenue_yoy"] if growth else None
        rows.append(
            {
                "ticker": ticker,
                "name": _field(item, "name"),
                "name_cn": _field(item, "name_cn") or None,
                "ring": _field(item, "ring"),
                "ps_ttm": _num(last.get("ps_ttm"), positive=True),
                "ps_percentile": ps_pct,
                "pb": _num(last.get("pb"), positive=True),
                "pb_percentile": pb_pct,
                "total_market_cap": _num(last.get("total_market_cap"), positive=True),
                "n_history_days": len(ps_window),
                "history_start": ps_window[0][0] if ps_window else None,
                "as_of": end,
                "revenue_yoy": yoy,
                "revenue_period": growth["revenue_period"] if growth else None,
                "flags": _flags(_flag(ps_pct, yoy)),
            }
        )
    panel = panel_frame(rows)
    CHINA_PANEL_SCHEMA.validate(panel)
    return panel


# --- facts and checks (pure) ---------------------------------------------------------------------------------
def _newest(panel: pl.DataFrame) -> str | None:
    days = panel["as_of"].drop_nulls()
    return days.max() if days.len() else None


def _val(value: float | None, unit: str, period: str, as_of: str | None, url: str = SOURCE_URL) -> dict[str, Any]:
    """The shape of ``payments.publish.values``: a median or a count read off the panel is exact, so ``=``."""
    return {
        "value": None if value is None else round(float(value), 6) + 0.0,
        "qualifier": "=" if value is not None else None,
        "unit": unit,
        "period": period,
        "as_of": as_of,
        "source_url": url,
    }


def facts(panel: pl.DataFrame) -> dict[str, Any]:
    """The numbers the page prints about the panel, ready for ``facts.cn`` and shaped like the other facts values.

    ``n_*`` are counts of panel rows; ``flagged`` lists the tickers whose flag is true and ``n_flag_assessable``
    counts the rows for which the flag could be decided. ``as_of`` is the newest trading day in the panel. Medians
    are over the rows that have the number."""
    newest = _newest(panel)
    flag = panel["flags"].struct.field(FLAG_PS_HIGH_REVENUE_FALLING)
    ps, pct = panel["ps_ttm"].drop_nulls().to_list(), panel["ps_percentile"].drop_nulls().to_list()
    yoy = panel["revenue_yoy"].drop_nulls().to_list()
    return {
        "n_listed": panel.height,
        "n_with_ps": len(ps),
        "n_with_percentile": len(pct),
        "n_with_revenue_yoy": len(yoy),
        "n_flag_assessable": int(flag.is_not_null().sum()),
        "n_flagged": int(flag.fill_null(False).sum()),
        "n_top_quartile": sum(1 for p in pct if p >= TOP_QUARTILE),
        "n_revenue_falling": sum(1 for y in yoy if y < 0),
        "flagged": panel.filter(flag.fill_null(False))["ticker"].to_list() if panel.height else [],
        "median_ps_ttm": _val(
            statistics.median(ps) if ps else None, "times trailing-12-month revenue", "latest day", newest
        ),
        "median_ps_percentile": _val(
            statistics.median(pct) if pct else None,
            "share of days since 2018 at or below the latest",
            f"since {HISTORY_START}",
            newest,
        ),
        "median_revenue_yoy": _val(
            statistics.median(yoy) if yoy else None,
            "fraction, cumulative year on year",
            "latest report of each listing",
            newest,
            REPORT_URL,
        ),
        "as_of": newest,
    }


def checks(panel: pl.DataFrame, listings: Iterable[Any], today: str | date) -> list[dict[str, Any]]:
    """Checks for ``facts.checks``, each ``{name, status, detail}``. ``today`` is passed in; nothing reads the clock."""
    expected = {_field(x, "ticker") for x in a_share_listings(listings)}
    got = set(panel["ticker"].to_list()) if panel.height else set()
    out = [
        check(
            "China panel rows",
            got == expected and panel.height == len(got),
            f"{panel.height} rows for {len(expected)} A-share listings of rings A and B"
            + (f"; missing {sorted(expected - got)}" if expected - got else "")
            + (f"; unexpected {sorted(got - expected)}" if got - expected else ""),
        )
    ]
    bare = panel.filter(pl.col("ps_ttm").is_null()) if panel.height else panel
    out.append(
        check(
            "China panel history",
            bare.height == 0,
            f"{panel.height - bare.height} of {panel.height} listings have a PS_TTM on their last day"
            + (f"; none for {bare['ticker'].to_list()}" if bare.height else ""),
            warn=panel.height > 0 and bare.height <= max(1, panel.height // 5),
        )
    )
    short = panel.filter(pl.col("ps_percentile").is_null() & pl.col("ps_ttm").is_not_null()) if panel.height else panel
    out.append(
        check(
            "China panel percentile",
            short.height == 0,
            f"{short.height} listings have a PS_TTM but fewer than {MIN_HISTORY_DAYS} days to rank it"
            + (f": {short['ticker'].to_list()}" if short.height else ""),
            warn=True,
        )
    )
    newest = _newest(panel)
    if newest is None:
        out.append(check("China panel freshness", False, "no trading day in the panel"))
    else:
        age = (_to_date(today) - date.fromisoformat(newest)).days
        lagging = [
            t
            for t, a in zip(panel["ticker"], panel["as_of"], strict=True)
            if a is not None and (date.fromisoformat(newest) - date.fromisoformat(a)).days > STALE_LAG_DAYS
        ]
        out.append(
            check(
                "China panel freshness",
                age <= MAX_AGE_DAYS and not lagging,
                f"newest trading day {newest}, {age} days before {_to_date(today).isoformat()} (limit {MAX_AGE_DAYS})"
                + (f"; more than {STALE_LAG_DAYS} days behind it: {lagging}" if lagging else ""),
                warn=age <= 3 * MAX_AGE_DAYS,
            )
        )
    noy = panel.filter(pl.col("revenue_yoy").is_null()) if panel.height else panel
    out.append(
        check(
            "China panel revenue growth",
            noy.height == 0,
            f"{panel.height - noy.height} of {panel.height} listings have a year-on-year revenue figure"
            + (f"; none for {noy['ticker'].to_list()}" if noy.height else ""),
            warn=panel.height > 0 and noy.height < panel.height,
        )
    )
    return out


def _to_date(value: str | date) -> date:
    day = _day(value)
    if day is None:
        raise ValueError(f"not a date: {value!r}")
    return date.fromisoformat(day)


# --- stored tables -------------------------------------------------------------------------------------------
def history_path(day: str) -> Path:
    return SNAP / HISTORY_SUBDIR / f"{day}.parquet"


def revenue_path(day: str) -> Path:
    return SNAP / HISTORY_SUBDIR / REVENUE_SUBDIR / f"{day}.parquet"


def history_frame(by_ticker: Mapping[str, Sequence[Mapping[str, Any]]]) -> pl.DataFrame:
    """The stored history table, sorted by ticker and day and validated by ``HISTORY_SCHEMA``."""
    rows = [
        {"ticker": t, **{k: r.get(k) for k in HISTORY_DTYPES if k != "ticker"}}
        for t in sorted(by_ticker)
        for r in sorted(by_ticker[t], key=lambda r: r["date"])
    ]
    frame = pl.DataFrame(rows, schema=HISTORY_DTYPES) if rows else pl.DataFrame(schema=HISTORY_DTYPES)
    return HISTORY_SCHEMA.validate(frame)


def revenue_frame(by_ticker: Mapping[str, Sequence[Mapping[str, Any]]]) -> pl.DataFrame:
    """The stored revenue table, sorted by ticker and period and validated by ``REVENUE_SCHEMA``."""
    rows = [
        {"ticker": t, **{k: r.get(k) for k in REVENUE_DTYPES if k != "ticker"}}
        for t in sorted(by_ticker)
        for r in sorted(by_ticker[t], key=lambda r: r["period_end"])
    ]
    frame = pl.DataFrame(rows, schema=REVENUE_DTYPES) if rows else pl.DataFrame(schema=REVENUE_DTYPES)
    return REVENUE_SCHEMA.validate(frame)


def _merge_existing(new: pl.DataFrame, path: Path) -> pl.DataFrame:
    """A second run on the same day replaces the tickers it fetched and keeps the others from the earlier file, so a
    retry that fails on one listing cannot blank the listings the first run got."""
    if not path.exists() or new.height == 0:
        return new
    old = pl.read_parquet(path).join(new.select("ticker").unique(), on="ticker", how="anti")
    return pl.concat([old.select(new.columns), new], how="vertical_relaxed").sort(new.columns[:2])


@dataclass(frozen=True)
class Loaded:
    """What :func:`load_latest` read. ``source_dates`` is the snapshot each ticker's history came from."""

    history: dict[str, list[dict[str, Any]]]
    revenue: dict[str, list[dict[str, Any]]]
    source_dates: dict[str, str]


def load_latest(directory: Path | None = None) -> Loaded | None:
    """The stored history and revenue per ticker, or None if nothing has been stored.

    A weekly file holds only the days a ticker's history gained since the previous file, plus an overlap of
    ``OVERLAP_DAYS`` that lets East Money restate recent values, so a ticker's history is the union of all files,
    the newest file winning on a repeated day. A run in which East Money failed for one listing leaves that listing on
    last week's history, with its own ``as_of``, instead of dropping it from the panel. Revenue is small and is
    read from the newest file that holds the ticker."""
    root = directory if directory is not None else SNAP / HISTORY_SUBDIR
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    for f in sorted(root.glob("????-??-??.parquet")):  # oldest first, so a later file overwrites a repeated day
        for part in pl.read_parquet(f).partition_by("ticker", maintain_order=True):
            t = part["ticker"][0]
            days = by_date.setdefault(t, {})
            for row in part.drop("ticker").to_dicts():
                days[row["date"]] = row
            sources[t] = f.stem
    history = {t: [days[d] for d in sorted(days)] for t, days in by_date.items()}
    revenue: dict[str, list[dict[str, Any]]] = {}
    for f in sorted((root / REVENUE_SUBDIR).glob("????-??-??.parquet"), reverse=True):
        for part in pl.read_parquet(f).partition_by("ticker", maintain_order=True):
            t = part["ticker"][0]
            if t not in revenue:
                revenue[t] = part.sort("period_end").drop("ticker").to_dicts()
    if not history and not revenue:
        return None
    return Loaded(history, revenue, sources)


def new_days(
    fetched: Mapping[str, Sequence[Mapping[str, Any]]], stored: Mapping[str, Sequence[Mapping[str, Any]]] | None
) -> dict[str, list[Mapping[str, Any]]]:
    """What a weekly file needs to hold: for a ticker already stored, the days from ``OVERLAP_DAYS`` before its newest
    stored day; for a new ticker, everything. Keeps the repository from gaining the whole history every week."""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for t, rows in fetched.items():
        have = (stored or {}).get(t)
        if not have:
            out[t] = list(rows)
            continue
        cut = (date.fromisoformat(max(r["date"] for r in have)) - timedelta(days=OVERLAP_DAYS)).isoformat()
        out[t] = [r for r in rows if r["date"] >= cut]
    return out


# --- orchestration -------------------------------------------------------------------------------------------
def collect(
    tickers: Sequence[str],
    *,
    history: Callable[[str], list[dict[str, Any]]],
    revenue: Callable[[str], list[dict[str, Any]]] | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Fetch every ticker; one failing never costs the others. Returns ``(history, revenue, failed)`` where ``failed``
    maps a ticker to the text of what went wrong, history and revenue each reported."""
    got_history: dict[str, list[dict[str, Any]]] = {}
    got_revenue: dict[str, list[dict[str, Any]]] = {}
    failed: dict[str, str] = {}
    for ticker in tickers:
        errors = []
        try:
            got_history[ticker] = history(ticker)
        except Exception as exc:  # noqa: BLE001 - a failed call must not end the listing, let alone the run
            errors.append(f"history: {type(exc).__name__}: {str(exc)[:160]}")
        if revenue is not None:
            try:
                got_revenue[ticker] = revenue(ticker)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"revenue: {type(exc).__name__}: {str(exc)[:160]}")
        if errors:
            failed[ticker] = "; ".join(errors)
    return got_history, got_revenue, failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[], metavar="TICKER", help="restrict to these tickers")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL)
    args = ap.parse_args(argv)
    setup_logging()

    tickers = [_field(x, "ticker") for x in a_share_listings(POOL)]
    if args.only:
        tickers = [t for t in tickers if t in set(args.only)]
    if not tickers:
        log.error("no listings selected")
        return 1

    ts = utc_now()
    day, _ = run_stamp(ts)
    http = client(args.min_interval)
    started = time.monotonic()
    got_history, got_revenue, failed = collect(
        tickers, history=lambda t: fetch(t, http), revenue=lambda t: fetch_revenue(t, http)
    )
    written: Path | None = None
    written_revenue: Path | None = None
    error = None
    try:
        stored = load_latest()
        frame = history_frame(new_days(got_history, stored.history if stored else None))
        rev = revenue_frame(got_revenue)  # a schema failure blocks both writes
        if frame.height:
            merged = HISTORY_SCHEMA.validate(_merge_existing(frame, history_path(day)))
            written = write_parquet(merged, history_path(day))
            if rev.height:
                merged_rev = REVENUE_SCHEMA.validate(_merge_existing(rev, revenue_path(day)))
                written_revenue = write_parquet(merged_rev, revenue_path(day))
        else:
            log.error("no listing returned a history; leaving the %s snapshot untouched", day)
    except Exception as exc:  # noqa: BLE001 - the run record below must survive a bad frame
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.exception("could not write the valuation history snapshot")
    record = {
        "module": "cyber.valuation_history_em",
        "snapshot_ts": ts.isoformat(timespec="seconds"),
        "listings": len(tickers),
        "with_history": sum(1 for rows in got_history.values() if rows),
        "with_revenue": sum(1 for rows in got_revenue.values() if rows),
        "history_rows": sum(len(rows) for rows in got_history.values()),
        "newest_day": max((rows[-1]["date"] for rows in got_history.values() if rows), default=None),
        "failed": failed,
        "error": error,
        "path": str(written) if written else None,
        "revenue_path": str(written_revenue) if written_revenue else None,
        "calls": http.calls,
        "seconds": round(time.monotonic() - started, 1),
    }
    append_jsonl(record, RUNS)
    log.info(
        "history=%s/%s revenue=%s rows=%s newest=%s failed=%s",
        record["with_history"],
        len(tickers),
        record["with_revenue"],
        record["history_rows"],
        record["newest_day"],
        len(failed),
    )
    if error or record["with_history"] < MIN_COVERED_SHARE * len(tickers):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
