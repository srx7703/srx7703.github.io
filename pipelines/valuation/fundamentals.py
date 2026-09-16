"""Quarterly attributable net income and revenue per listing, the denominator of trailing PE.

Usage:
    uv run python -m pipelines.valuation.fundamentals [--tickers 300308.SZ,COHR] [--source eastmoney]

Three sources, routed by ``Company.fundamentals_source``:

``eastmoney``  A-share quarterly report table (RPT_LICO_FN_CPD). East Money publishes *cumulative*
               year-to-date figures, so Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M. Differencing never
               crosses a fiscal year, and a missing interim period breaks the chain for that year
               rather than inventing a six-month "quarter".
``sec``        XBRL companyfacts, through the machinery already written for the SaaS benchmark
               (``pipelines.sec.transform.quarterly_series``): 80-100 day spans are used directly,
               longer spans sharing a start date are differenced.
``yahoo``      ``quarterly_income_stmt`` for HK/JP/KR/TW/UK. Yahoo states Japanese and Korean
               statements in the reporting currency in *units*, not thousands - nothing is rescaled
               here, the currency is recorded from ``info["financialCurrency"]`` and the
               calendarisation and FX layers deal with the rest.

Dual listings (6869.HK, 3750.HK, 1211.HK) are never fetched: the primary A-share line is fetched
once and its rows are copied onto the secondary ticker, so the two lines can never disagree.

Writes:
    data/snapshots/valuation/fundamentals/<date>.parquet         quarterly rows (FUNDAMENTALS_SCHEMA)
    data/snapshots/valuation/fundamentals_annual/<date>.parquet  Yahoo annual rows, see below
    data/snapshots/valuation/fundamentals_runs.jsonl             one summary line per run
    data/raw/valuation/{eastmoney_fin,sec,yahoo_fin}/<date>/<key>.json.gz

Why two tables: ``fundamentals`` is unique on (ticker, period_end) and carries no period-length
column, so an annual row would sit at a fiscal year end that is also a quarter end and be
indistinguishable from a quarter four times too large. Yahoo gives only 4-5 quarters, not enough for
a year-on-year TTM, so the annual statement is still fetched and parsed - it just lands in its own
table where nothing can mistake it for a quarter.

The two tables are read back through :func:`load_ttm`, which gives one trailing figure per ticker:
a real trailing twelve months (``basis="ttm"``) wherever four clean quarters exist, and the last
full fiscal year (``basis="last_fy"``) otherwise. On the 2026-09-16 snapshot that is *eleven*
listings, and not all for the same reason:

    no quarterly statement at all (nine)  3931.HK, 5019.T, 5801.T, 5802.T, 5803.T, 6752.T, 6762.T,
                                          6810.T, IKA.L. Yahoo publishes none for these.
    quarters on file, no clean window (two)
                                          3081.TWO has five quarterly rows, but 2025-09-30 is
                                          missing, so its last four span 456 days and fail the
                                          330-400 day test. 6981.T has four contiguous quarters
                                          whose newest ends 2025-09-30, older than TTM_MAX_AGE_DAYS.

Anything reporting why a listing fell back has to keep those apart: "no quarterly statement is
published for them" is false for the last two, and 3081.TWO's multiple is struck on a fiscal year
with four quarters sitting unusable beside it. A ``last_fy`` figure is not a TTM either way - it
ends at the company's fiscal year end and can be a year old - so anything that prints it should
print the basis with it.

Partial runs are normal (``--tickers`` / ``--source``), so a same-date write merges over whatever is
already on disk for that date - new rows win on (ticker, period_end) and nothing an earlier run
fetched is lost - and a run that produced no rows at all writes nothing rather than emptying a good
table. See :func:`_merge_existing`. Raw payloads are append-only evidence (``CLAUDE.md`` rule 2):
:func:`_raw_path` never returns a name already on disk, so a rerun cannot replace the payload the
earlier run's parquet was built from, not even one landing in the same minute.

One listing failing must not lose the others, and neither must one bad row. Each primary is fetched
inside its own guard, and each *listing's* rows then go through the frame build and pandera inside a
second one (:func:`validate_listing`), so a value polars or pandera rejects costs exactly that
listing and is named in the run record instead of taking the pool with it. The writes are guarded
too: if one fails, nothing lands, the reason is recorded in ``fundamentals_runs.jsonl`` with the rest
of the run, and the process exits non-zero.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from pipelines.common.checks import check
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import (
    RAW_DIR,
    SNAP_DIR,
    append_jsonl,
    run_stamp,
    utc_now,
    write_json_gz,
    write_parquet,
)
from pipelines.sec.transform import Q_MAX, Q_MIN, quarterly_series
from pipelines.valuation import read
from pipelines.valuation.config import BY_TICKER, COMPANIES, Company
from pipelines.valuation.schema import FUNDAMENTAL_KEY, FUNDAMENTALS_SCHEMA, fundamentals_frame

log = logging.getLogger("valuation.fundamentals")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://emweb.securities.eastmoney.com/",
}
EM_PAGE_SIZE = 40  # ~10 years of cumulative periods, far more than the 12 quarters we must keep
EM_MIN_INTERVAL = 0.35

# XBRL tag fallbacks, highest priority first.
SEC_NET_INCOME_TAGS = (
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)
SEC_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
# Amendments included, and deliberately the same tuple as pipelines/sec/ingest.py:90 - a 10-K/A is
# how a restatement arrives, and for a late-filed period it can be the only carrier of the span.
# Within a tag the latest ``filed`` wins, so the amendment supersedes the original.
SEC_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")

# Yahoo income-statement line items, highest priority first.
YAHOO_NET_INCOME_LINES = (
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
)
YAHOO_REVENUE_LINES = ("Total Revenue", "Operating Revenue")
YAHOO_MIN_INTERVAL = 0.6
YAHOO_RATE_LIMIT_SLEEP = 8.0
YAHOO_MAX_RETRIES = 2
# Only used when Yahoo's ``info`` call fails outright; HK is deliberately absent because an HK line
# may report in HKD or CNY and guessing would silently misprice it.
YAHOO_CURRENCY_FALLBACK = {"jp": "JPY", "kr": "KRW", "tw": "TWD", "uk": "GBP"}

MIN_QUARTERS = 12  # "keep at least the last 12 quarters where available"
TTM_MIN_SPAN, TTM_MAX_SPAN = 330, 400  # days covered by four consecutive quarters
FY_NOMINAL_SPAN = 365  # a fiscal year we could not measure against its predecessor
FY_SPAN_SLACK = 35  # how far a gap may sit from a whole number of years and still be annual
# How stale a trailing figure may be before it stops describing the company. A TTM window that ends
# more than two quarters back means the filings stopped arriving; a fiscal year legitimately ages
# most of a year before the next one is published, so it gets a much longer leash.
TTM_MAX_AGE_DAYS = 200
ANNUAL_MAX_AGE_DAYS = 550
RECENT_DROP_DAYS = 400  # a dropped period this recent breaks the listing's current TTM window
MAX_ARCHIVES = 64  # payloads archived for one key on one date before a run gives up (see `_raw_path`)


class SourceError(RuntimeError):
    """A source answered, but not with usable data."""


# --------------------------------------------------------------------------------------- helpers


def _f(v: Any) -> float | None:
    """Float or None; NaN, NA and unparseable values all become None."""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _period_end(v: Any) -> str | None:
    """Normalise a period end to YYYY-MM-DD, accepting dates, timestamps and '... HH:MM:SS'."""
    if v is None:
        return None
    if hasattr(v, "date"):
        try:
            return v.date().isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()[:10]
    return s if DATE_RE.match(s) else None


def _key(ticker: str) -> str:
    return ticker.replace("/", "_")


def _raw_path(source: str, day: str, key: str, hhmm: str) -> Path:
    """Raw archive path. Raw files are append-only evidence: a same-day rerun gets its own file
    rather than overwriting the payload already on disk.

    Every candidate is tried, not just ``__HHMM``: two runs inside the same minute (a cron run and a
    manual re-run colliding) used to land on the same name, so the second silently replaced the
    first run's payload and left a parquet no longer reproducible from its own raw layer. Same
    ladder as ``fx.archive_csv`` and the two estimates modules; unlike them this only picks the
    name, so an identical body is archived again rather than recognised — append-only is the
    property that matters, and a duplicate costs a few KB.
    """
    base = RAW_DIR / "valuation" / source / day
    for n in range(MAX_ARCHIVES):
        if n == 0:
            path = base / f"{key}.json.gz"
        elif n == 1:
            path = base / f"{key}__{hhmm}.json.gz"
        else:
            path = base / f"{key}__{hhmm}_{n}.json.gz"
        if not path.exists():
            return path
    raise RuntimeError(f"too many archives for {key} at {hhmm} in {base}")


def owner(c: Company) -> Company:
    """The listing whose filings this listing's fundamentals come from (itself, unless dual-listed)."""
    return BY_TICKER.get(c.primary, c)


def source_of(c: Company) -> str:
    """``Company.fundamentals_source`` reads the listing's *own* market, which is wrong for a dual
    listing: 6869.HK is an HK line but its filings are the A-share's, fetched from East Money."""
    return owner(c).fundamentals_source


# ----------------------------------------------------------------------------- East Money (pure)


def parse_eastmoney(payload: dict, *, security_code: str = "") -> list[dict]:
    """Cumulative year-to-date rows from an RPT_LICO_FN_CPD payload, oldest first.

    East Money returns HTTP 200 with ``{"success": false}`` on failure, so ``success`` is checked
    explicitly instead of trusting the status code.
    """
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = (payload or {}).get("message") if isinstance(payload, dict) else None
        raise SourceError(f"East Money returned success=false for {security_code or '?'}: {msg!r}")
    data = ((payload.get("result") or {}).get("data")) or []
    rows: dict[str, dict] = {}
    for r in data:
        pe = _period_end(r.get("REPORTDATE") or r.get("REPORT_DATE"))
        if pe is None:
            continue
        rows[pe] = {
            "period_end": pe,
            "net_income": _f(r.get("PARENT_NETPROFIT")),
            "revenue": _f(r.get("TOTAL_OPERATE_INCOME")),
            "currency": "CNY",
        }
    return [rows[pe] for pe in sorted(rows)]


def _q_index(period_end: str) -> int:
    """Quarter number within the fiscal year. A-shares all close in December, and East Money reports
    on 03-31 / 06-30 / 09-30 / 12-31, so the calendar month gives the quarter directly."""
    return (_d(period_end).month - 1) // 3 + 1


def difference_cumulative(rows: list[dict], *, dropped: list[dict] | None = None) -> list[dict]:
    """Cumulative year-to-date rows -> quarterly rows.

    Q1 is as reported (``derived=False``); Q2..Q4 are the difference against the previous cumulative
    period of the *same* fiscal year (``derived=True``). Differencing never crosses a year boundary.
    A quarter whose predecessor is missing (a year with no 9M filing, say) is dropped and recorded
    in ``dropped`` rather than turned into a six-month "quarter".
    """
    by_year: dict[str, dict[int, dict]] = {}
    for r in rows:
        pe = r.get("period_end")
        if not pe or not DATE_RE.match(str(pe)):
            continue
        by_year.setdefault(pe[:4], {})[_q_index(pe)] = r

    out: list[dict] = []
    for year in sorted(by_year):
        periods = by_year[year]
        for q in sorted(periods):
            row = periods[q]
            if q == 1:
                out.append(
                    {
                        "period_end": row["period_end"],
                        "net_income": row.get("net_income"),
                        "revenue": row.get("revenue"),
                        "currency": row.get("currency"),
                        "derived": False,
                    }
                )
                continue
            prev = periods.get(q - 1)
            if prev is None:
                rec = {
                    "period_end": row["period_end"],
                    "kind": "period",
                    "reason": f"cumulative Q{q - 1} {year} missing, chain broken",
                }
                log.warning("dropping %s: %s", rec["period_end"], rec["reason"])
                if dropped is not None:
                    dropped.append(rec)
                continue
            out.append(
                {
                    "period_end": row["period_end"],
                    "net_income": _sub(row.get("net_income"), prev.get("net_income")),
                    "revenue": _sub(row.get("revenue"), prev.get("revenue")),
                    "currency": row.get("currency") or prev.get("currency"),
                    "derived": True,
                }
            )
    return out


# ------------------------------------------------------------------------------------ SEC (pure)


def sec_spans(companyfacts: dict, tags: tuple[str, ...]) -> tuple[dict[tuple[str, str], float], str | None, list[str]]:
    """Duration spans (start, end) -> value for the first tags that carry data.

    Only 10-K and 10-Q forms count. Within a tag the latest ``filed`` date wins on a duplicate span,
    so restatements flow through; across tags the higher-priority tag is never overwritten, which
    keeps the history of a company that switched tags mid-life.
    """
    gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    spans: dict[tuple[str, str], float] = {}
    used: list[str] = []
    unit_seen: str | None = None
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        units = node.get("units") or {}
        unit = "USD" if "USD" in units else next(iter(units), None)
        if unit is None:
            continue
        best: dict[tuple[str, str], tuple[str, float]] = {}
        for f in units[unit]:
            if f.get("form") not in SEC_FORMS or not f.get("start") or not f.get("end"):
                continue
            val = _f(f.get("val"))
            if val is None:
                continue
            key = (str(f["start"]), str(f["end"]))
            filed = str(f.get("filed") or "")
            prev = best.get(key)
            if prev is None or filed >= prev[0]:
                best[key] = (filed, val)
        added = 0
        for key, (_filed, val) in best.items():
            if key not in spans:
                spans[key] = val
                added += 1
        if added:
            used.append(tag)
            unit_seen = unit_seen or unit
    return spans, unit_seen, used


def _direct_ends(spans: dict[tuple[str, str], float]) -> set[str]:
    """Period ends that were filed as a quarter outright, rather than differenced out of a YTD span."""
    return {e for (s, e) in spans if Q_MIN <= (_d(e) - _d(s)).days <= Q_MAX}


def sec_quarters(companyfacts: dict) -> tuple[list[dict], dict]:
    """Quarterly rows from a companyfacts payload, plus a small coverage record.

    ``derived`` is true when any populated value on the row came from differencing a year-to-date
    span, which is how Q4 is always recovered (there is no Q4 10-Q).
    """
    ni_spans, ni_unit, ni_tags = sec_spans(companyfacts, SEC_NET_INCOME_TAGS)
    rev_spans, rev_unit, rev_tags = sec_spans(companyfacts, SEC_REVENUE_TAGS)
    ni, ni_recon = quarterly_series(ni_spans) if ni_spans else ({}, {})
    rev, rev_recon = quarterly_series(rev_spans) if rev_spans else ({}, {})
    ni_direct, rev_direct = _direct_ends(ni_spans), _direct_ends(rev_spans)
    currency = ni_unit or rev_unit

    rows: list[dict] = []
    for e in sorted(set(ni) | set(rev)):
        if not DATE_RE.match(e):
            continue
        derived = (e in ni and e not in ni_direct) or (e in rev and e not in rev_direct)
        rows.append(
            {
                "period_end": e,
                "net_income": ni.get(e),
                "revenue": rev.get(e),
                "currency": currency,
                "derived": bool(derived),
            }
        )
    coverage = {
        "net_income_tags": ni_tags,
        "revenue_tags": rev_tags,
        "unit": currency,
        "reconciled": len(ni_recon) + len(rev_recon),
    }
    return rows, coverage


# ---------------------------------------------------------------------------------- Yahoo (pure)


def pick_line(stmt: dict[str, dict[str, float | None]], candidates: tuple[str, ...]) -> tuple[str | None, dict]:
    """First candidate line item that exists and carries at least one value."""
    for name in candidates:
        series = stmt.get(name)
        if series and any(v is not None for v in series.values()):
            return name, series
    return None, {}


def parse_yahoo_rows(stmt: dict[str, dict[str, float | None]], *, currency: str | None) -> tuple[list[dict], dict]:
    """Income-statement dict (line item -> period end -> value) into fundamentals rows.

    Yahoo statements are as-reported for the period shown, so nothing is differenced and every row
    is ``derived=False``. Numbers are recorded in whatever units Yahoo used - Japanese and Korean
    statements come through in units rather than thousands and are deliberately not rescaled.
    """
    ni_name, ni = pick_line(stmt, YAHOO_NET_INCOME_LINES)
    rev_name, rev = pick_line(stmt, YAHOO_REVENUE_LINES)
    rows: list[dict] = []
    for e in sorted(set(ni) | set(rev)):
        if not DATE_RE.match(str(e)):
            continue
        net_income, revenue = ni.get(e), rev.get(e)
        if net_income is None and revenue is None:
            continue
        rows.append(
            {
                "period_end": e,
                "net_income": net_income,
                "revenue": revenue,
                "currency": currency,
                "derived": False,
            }
        )
    return rows, {"net_income_line": ni_name, "revenue_line": rev_name}


def statement_to_dict(df: Any) -> dict[str, dict[str, float | None]]:
    """A yfinance income-statement DataFrame (line items x period-end columns) as plain JSON data.

    Done at the network boundary so everything downstream, including the tests, is pure Python.
    """
    if df is None or getattr(df, "empty", True):
        return {}
    cols = [(c, _period_end(c)) for c in df.columns]
    out: dict[str, dict[str, float | None]] = {}
    for item, series in df.iterrows():
        name = str(item)
        if name in out:
            continue  # duplicate line item: the first occurrence wins
        out[name] = {pe: _f(series[c]) for c, pe in cols if pe}
    return out


# --------------------------------------------------------------------------------- TTM (public)


def _span_days(ends: list[str], before: str | None) -> int:
    """Days the window covers: the gaps between its period ends, plus the first quarter's own length.

    The first quarter's length is exact when the preceding quarter is known and plausible; otherwise
    it is the mean of the window's own gaps. Four ~91-day quarters therefore come out near 365, not
    the ~273 an end-to-end measurement would give.
    """
    gaps = [(_d(b) - _d(a)).days for a, b in zip(ends, ends[1:], strict=False)]
    first = None
    if before is not None:
        candidate = (_d(ends[0]) - _d(before)).days
        if Q_MIN <= candidate <= Q_MAX:
            first = candidate
    if first is None:
        first = round(sum(gaps) / len(gaps)) if gaps else 0
    return sum(gaps) + first


def _by_end(rows: list[dict], ticker: str, as_of: str | None) -> dict[str, dict]:
    """This ticker's rows keyed by period end, dated and at or before ``as_of``."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("ticker") != ticker:
            continue
        pe = r.get("period_end")
        if not pe or not DATE_RE.match(str(pe)):
            continue
        if as_of and pe > as_of:
            continue
        out[str(pe)] = r
    return out


def _window_currency(rows: list[dict]) -> tuple[str | None, bool]:
    """The currency a window is stated in, and whether it is stated in only one.

    Summing four quarters that changed reporting currency mid-window produces a number in no
    currency at all, so a disagreement is fatal rather than something to pick a winner from.
    """
    seen = {r.get("currency") for r in rows if r.get("currency")}
    if len(seen) > 1:
        return None, False
    return (next(iter(seen)) if seen else None), True


def _too_old(period_end: str, as_of: str | None, max_age_days: int | None) -> bool:
    """Whether a trailing figure ending at ``period_end`` is too stale to describe the company.

    Only measurable against an ``as_of``; callers that do not supply one get no age gate, which is
    why :func:`load_ttm` always supplies the run date.
    """
    if not as_of or not max_age_days:
        return False
    return (_d(as_of) - _d(period_end)).days > max_age_days


def ttm_from_quarters(
    rows: list[dict], ticker: str, as_of: str | None = None, *, max_age_days: int | None = None
) -> dict | None:
    """Trailing twelve months for ``ticker`` from quarterly rows, or None if the window is not clean.

    The four most recent consecutive quarters at or before ``as_of`` must all be present, must cover
    330-400 days and must agree on the reporting currency; anything else (a gap, a stub of three
    quarters, a currency change) returns None rather than a number that looks like a TTM and is not
    one. With ``max_age_days`` the window must also be recent, which is the only way to catch a
    company that stopped filing: four quarters ending in 2019 are internally perfect.

    The returned ``currency`` is the statements' own, and is what ``metrics.company_row`` and
    ``share.py`` read as the reporting currency - without it they fall back to the prices table's
    opinion, which is missing exactly when a listing failed to price.
    """
    by_end = _by_end(rows, ticker, as_of)
    ends = sorted(by_end)
    if len(ends) < 4:
        return None
    window = ends[-4:]
    before = ends[-5] if len(ends) >= 5 else None
    span = _span_days(window, before)
    if not (TTM_MIN_SPAN <= span <= TTM_MAX_SPAN):
        return None
    if _too_old(window[-1], as_of, max_age_days):
        log.warning("%s: newest quarter %s is older than %d days; no TTM", ticker, window[-1], max_age_days)
        return None
    currency, consistent = _window_currency([by_end[e] for e in window])
    if not consistent:
        log.warning("%s: the four quarters to %s disagree on currency; no TTM", ticker, window[-1])
        return None

    def total(col: str) -> float | None:
        vals = [by_end[e].get(col) for e in window]
        return None if any(v is None for v in vals) else float(sum(vals))

    ni, rev = total("net_income"), total("revenue")
    if ni is None and rev is None:
        return None
    return {
        "period_end": window[-1],
        "ttm_net_income": ni,
        "ttm_revenue": rev,
        "n_quarters": len(window),
        "span_days": span,
        "currency": currency,
        "basis": "ttm",
    }


def _annual_span(last: str, prev: str | None) -> int | None:
    """Days the fiscal year ending ``last`` covers, or None when the series is not annual.

    Measured against the previous fiscal year end. A gap of a whole number of years is an annual
    series with a missing year and falls back to the nominal 365; a gap that is not near a year
    boundary - a quarter, say - means these are not annual rows and nothing should be built on them.
    """
    if prev is None:
        return FY_NOMINAL_SPAN
    gap = (_d(last) - _d(prev)).days
    if TTM_MIN_SPAN <= gap <= TTM_MAX_SPAN:
        return gap
    years = round(gap / FY_NOMINAL_SPAN)
    if years >= 2 and abs(gap - years * FY_NOMINAL_SPAN) <= FY_SPAN_SLACK:
        return FY_NOMINAL_SPAN
    return None


def ttm_from_annual(
    annual_rows: list[dict], ticker: str, as_of: str | None = None, *, max_age_days: int | None = None
) -> dict | None:
    """The last full fiscal year for ``ticker``, in the shape :func:`ttm_from_quarters` returns.

    For nine listings (3931.HK, 5019.T, 5801.T, 5802.T, 5803.T, 6752.T, 6762.T, 6810.T, IKA.L)
    Yahoo publishes no quarterly income statement at all, so a real TTM cannot be built and the
    choice is between a fiscal year and a blank column. This returns the fiscal year, labelled
    ``basis="last_fy"`` so no consumer can mistake it for a trailing twelve months: it ends where
    the company's year ends, and by the time the next one is filed it is nearly a year old.

    ``n_quarters`` is 4 because a fiscal year is four quarters of earnings, even though it arrived
    as one row. ``span_days`` is measured against the previous fiscal year end where there is one.
    """
    by_end = _by_end(annual_rows, ticker, as_of)
    ends = sorted(by_end)
    if not ends:
        return None
    last = ends[-1]
    if _too_old(last, as_of, max_age_days):
        log.warning("%s: last fiscal year %s is older than %d days; no trailing figure", ticker, last, max_age_days)
        return None
    span = _annual_span(last, ends[-2] if len(ends) >= 2 else None)
    if span is None:
        log.warning("%s: annual rows to %s are not a yearly series; no trailing figure", ticker, last)
        return None
    row = by_end[last]
    ni, rev = _f(row.get("net_income")), _f(row.get("revenue"))
    if ni is None and rev is None:
        return None
    return {
        "period_end": last,
        "ttm_net_income": ni,
        "ttm_revenue": rev,
        "n_quarters": 4,
        "span_days": span,
        "currency": row.get("currency"),
        "basis": "last_fy",
    }


def ttm_by_ticker(
    quarterly_rows: list[dict],
    annual_rows: list[dict] | None = None,
    *,
    as_of: str | None = None,
    max_age_days: int | None = TTM_MAX_AGE_DAYS,
    annual_max_age_days: int | None = ANNUAL_MAX_AGE_DAYS,
) -> dict[str, dict]:
    """One trailing figure per ticker, keyed by ticker: the mapping ``publish`` builds its rows from.

    A real TTM always wins. The annual table only ever fills a ticker the quarterly table cannot
    produce a clean window for, so a listing that has both is unaffected by the fallback existing.
    Read ``basis`` before printing the number.
    """
    out: dict[str, dict] = {}
    for t in sorted({r.get("ticker") for r in quarterly_rows if r.get("ticker")}):
        got = ttm_from_quarters(quarterly_rows, str(t), as_of, max_age_days=max_age_days)
        if got:
            out[str(t)] = got
    for t in sorted({r.get("ticker") for r in annual_rows or [] if r.get("ticker")}):
        if str(t) in out:
            continue
        got = ttm_from_annual(annual_rows or [], str(t), as_of, max_age_days=annual_max_age_days)
        if got:
            out[str(t)] = got
    return out


def load_ttm(as_of: str | None = None, **kwargs: Any) -> dict[str, dict]:
    """:func:`ttm_by_ticker` over the newest snapshot of both fundamentals tables.

    This is the entry point ``publish.load_all`` should call: looping ``ttm_from_quarters`` over
    ``read.load("fundamentals")`` alone leaves the eleven ``last_fy`` listings with no trailing PE
    and applies no age gate. ``as_of`` defaults to today, which is what turns the age gates on.
    """
    as_of = as_of or utc_now().date().isoformat()
    return ttm_by_ticker(
        read.rows(read.load("fundamentals")),
        read.rows(read.load("fundamentals_annual")),
        as_of=as_of,
        **kwargs,
    )


# --------------------------------------------------------------------------------- network layer


def eastmoney_client() -> HttpClient:
    return HttpClient(min_interval=EM_MIN_INTERVAL, headers=EM_HEADERS)


def fetch_eastmoney(client: HttpClient, security_code: str) -> dict:
    return client.get_json(
        EM_URL,
        params={
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{security_code}")',
            "pageSize": EM_PAGE_SIZE,
            "sortColumns": "REPORTDATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
    )


def em_security_code(c: Company) -> str:
    """'SZ300308' -> '300308'; falls back to the ticker's numeric part."""
    code = (c.em_code or "").strip()
    if code[:2].isalpha():
        code = code[2:]
    return code or c.ticker.split(".")[0]


def sec_user_agent_ok(ua: str) -> bool:
    return "@" in ua


class YahooFetcher:
    """yfinance behind a throttle. yfinance does its own HTTP, so HttpClient's pacing does not apply."""

    def __init__(self, min_interval: float = YAHOO_MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0

    def _throttle(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _call(self, fn, label: str):
        last: Exception | None = None
        for attempt in range(YAHOO_MAX_RETRIES + 1):
            self._throttle()
            try:
                self.calls += 1
                return fn()
            except Exception as exc:  # noqa: BLE001 - yfinance raises a wide and unstable set
                last = exc
                rate_limited = "429" in repr(exc) or "rate limit" in repr(exc).lower()
                if attempt == YAHOO_MAX_RETRIES:
                    break
                sleep = YAHOO_RATE_LIMIT_SLEEP if rate_limited else 2.0 * (attempt + 1)
                log.warning("yahoo %s failed (%s); retry %d in %.1fs", label, exc, attempt + 1, sleep)
                time.sleep(sleep)
        raise SourceError(f"yahoo {label} failed: {last!r}") from last

    def payload(self, ticker: str) -> dict:
        import yfinance as yf  # imported lazily so the pure parse path needs no network stack

        t = yf.Ticker(ticker)
        quarterly = statement_to_dict(self._call(lambda: t.quarterly_income_stmt, f"{ticker} quarterly_income_stmt"))
        annual = statement_to_dict(self._call(lambda: t.income_stmt, f"{ticker} income_stmt"))
        try:
            info = self._call(lambda: t.info, f"{ticker} info") or {}
        except SourceError as exc:
            log.warning("%s: info unavailable (%s); currency falls back to the market default", ticker, exc)
            info = {}
        return {
            "ticker": ticker,
            "quarterly_income_stmt": quarterly,
            "income_stmt": annual,
            "info": {k: info.get(k) for k in ("financialCurrency", "currency", "quoteType", "longName")},
        }


# ------------------------------------------------------------------------------- per-primary work


def collect_eastmoney(c: Company, client: HttpClient, archive) -> tuple[list[dict], list[dict], dict]:
    payload = fetch_eastmoney(client, em_security_code(c))
    archive("eastmoney_fin", _key(c.ticker), payload)
    cumulative = parse_eastmoney(payload, security_code=em_security_code(c))
    dropped: list[dict] = []
    rows = difference_cumulative(cumulative, dropped=dropped)
    return rows, dropped, {"cumulative_periods": len(cumulative)}


def collect_sec(c: Company, client, lookup: dict[str, tuple[str, str]], archive) -> tuple[list[dict], list[dict], dict]:
    t = c.ticker.upper()
    if t not in lookup:
        raise SourceError(f"{t} is not in the SEC company_tickers file")
    cik, name = lookup[t]
    cf = client.companyfacts(cik)
    archive("sec", _key(c.ticker), cf)
    rows, coverage = sec_quarters(cf)
    coverage.update({"cik": cik, "entity": cf.get("entityName") or name})
    return rows, [], coverage


def collect_yahoo(c: Company, fetcher: YahooFetcher, archive) -> tuple[list[dict], list[dict], dict]:
    payload = fetcher.payload(c.ticker)
    archive("yahoo_fin", _key(c.ticker), payload)
    currency = payload["info"].get("financialCurrency") or YAHOO_CURRENCY_FALLBACK.get(c.market)
    if not currency:
        log.warning("%s: no financialCurrency from Yahoo and no safe default for market %r", c.ticker, c.market)
    rows, cov = parse_yahoo_rows(payload["quarterly_income_stmt"], currency=currency)
    annual, ann_cov = parse_yahoo_rows(payload["income_stmt"], currency=currency)
    cov.update({"annual_rows": len(annual), "annual_lines": ann_cov, "currency": currency})
    return rows, [], {"annual": annual, **cov}


def clean_rows(rows: list[dict], ticker: str, source: str, snapshot_ts: str) -> tuple[list[dict], list[dict]]:
    """Stamp rows for the table and deal with the values the contract cannot hold.

    A negative quarterly revenue means the differencing hit a restatement - an A-share filing its
    full year below the nine-month figure it is differenced against, which is exactly what 002074.SZ
    did for 2019. The schema forbids the value, but the net income on the same row is normally fine
    and is the only thing trailing PE needs, so the revenue is voided and the row is kept. Dropping
    the row instead would cost the listing its TTM window, and its place on the chart, for a year.

    Records are tagged ``kind``: ``"period"`` when the whole quarter is gone, ``"revenue"`` when
    only the one field was voided.
    """
    out: list[dict] = []
    dropped: list[dict] = []
    for r in rows:
        pe = str(r.get("period_end") or "")
        if not DATE_RE.match(pe):
            dropped.append({"ticker": ticker, "period_end": pe, "kind": "period", "reason": "unparseable period end"})
            continue
        net_income, revenue = _f(r.get("net_income")), _f(r.get("revenue"))
        if revenue is not None and revenue < 0:
            dropped.append(
                {
                    "ticker": ticker,
                    "period_end": pe,
                    "kind": "revenue",
                    "reason": f"negative revenue {revenue:,.0f} voided; net income kept",
                }
            )
            revenue = None
        if net_income is None and revenue is None:
            continue
        out.append(
            {
                "snapshot_ts": snapshot_ts,
                "ticker": ticker,
                "period_end": pe,
                "net_income": net_income,
                "revenue": revenue,
                "currency": r.get("currency"),
                "source": source,
                "derived": bool(r.get("derived")),
            }
        )
    out.sort(key=lambda r: r["period_end"])
    return out, dropped


# ------------------------------------------------------------------------------------------ main


def select(tickers: str | None, sources: str | None) -> list[Company]:
    chosen = COMPANIES
    if tickers:
        want = {t.strip().upper() for t in tickers.split(",") if t.strip()}
        chosen = [c for c in chosen if c.ticker.upper() in want]
        missing = want - {c.ticker.upper() for c in chosen}
        for t in sorted(missing):
            log.warning("%s is not in the valuation pool; ignored", t)
    if sources:
        want_src = {s.strip().lower() for s in sources.split(",") if s.strip()}
        chosen = [c for c in chosen if source_of(c) in want_src]
    return chosen


def validate_listing(rows: list[dict], annual: list[dict]) -> None:
    """Prove one listing's rows survive the frame build and pandera, before they join the pool.

    Framing and validating only the combined table would put both calls outside the per-listing
    guard, which is the one shape the isolation rule exists to forbid: a single row polars or
    pandera rejects would take every other listing's data with it, and the run record after it.
    Same reasoning and same placement as ``estimates_yf.validate_rows`` and ``estimates_em``.
    """
    for part in (rows, annual):
        if part:
            FUNDAMENTALS_SCHEMA.validate(fundamentals_frame(part))


def build(targets: list[Company], snapshot_ts: str, day: str, hhmm: str) -> dict:
    """Fetch every distinct primary once, then fan the rows back out to the listings that use them."""

    def archive(source: str, key: str, payload: Any) -> None:
        write_json_gz(payload, _raw_path(source, day, key, hhmm))

    primaries: list[str] = []
    for c in targets:
        if c.primary not in primaries:
            primaries.append(c.primary)

    em: HttpClient | None = None
    sec_client = None
    sec_lookup: dict[str, tuple[str, str]] = {}
    sec_lookup_error: Exception | None = None
    yahoo: YahooFetcher | None = None

    rows_by_primary: dict[str, list[dict]] = {}
    annual_by_primary: dict[str, list[dict]] = {}
    coverage: dict[str, dict] = {}
    dropped: list[dict] = []
    failed: dict[str, str] = {}

    for p in primaries:
        c = BY_TICKER.get(p)
        if c is None:
            failed[p] = "primary ticker is not in the pool"
            log.error("%s: %s", p, failed[p])
            continue
        src = c.fundamentals_source
        try:
            if src == "eastmoney":
                if em is None:
                    em = eastmoney_client()
                rows, drops, cov = collect_eastmoney(c, em, archive)
            elif src == "sec":
                # The ticker->CIK lookup is the whole SEC leg. If it failed once it will fail for
                # every other US listing too, and the real cause must be what they all report:
                # leaving a half-built client behind made them all say "not in the company_tickers
                # file" when the truth was a 403 on the file itself.
                if sec_lookup_error is not None:
                    raise sec_lookup_error
                if sec_client is None:
                    from pipelines.sec.ingest import SEC_UA, SecClient

                    if not os.environ.get("SEC_USER_AGENT"):
                        log.warning("SEC_USER_AGENT is unset; falling back to %r", SEC_UA)
                    if not sec_user_agent_ok(SEC_UA):
                        log.warning(
                            "SEC_USER_AGENT %r has no email address in it; SEC answers 403 to "
                            "anonymous clients - export SEC_USER_AGENT='name you@example.com'",
                            SEC_UA,
                        )
                    try:
                        client = SecClient()
                        lookup = client.tickers()
                    except Exception as exc:
                        sec_lookup_error = SourceError(f"SEC company_tickers lookup failed: {exc!r}")
                        raise sec_lookup_error from exc
                    sec_client, sec_lookup = client, lookup
                    archive("sec", "_company_tickers", sec_lookup)
                rows, drops, cov = collect_sec(c, sec_client, sec_lookup, archive)
            else:
                if yahoo is None:
                    yahoo = YahooFetcher()
                rows, drops, cov = collect_yahoo(c, yahoo, archive)
                annual_by_primary[p] = cov.pop("annual", [])
        except Exception as exc:  # noqa: BLE001 - isolation: one listing must never sink the rest
            failed[p] = repr(exc)
            log.exception("%s (%s) failed", p, src)
            continue
        rows_by_primary[p] = rows
        dropped += [{"ticker": p, **d} for d in drops]
        coverage[p] = {"source": src, "quarters": len(rows), **cov}
        log.info("%s: %d quarters from %s", p, len(rows), src)

    out: list[dict] = []
    annual_out: list[dict] = []
    invalid: dict[str, str] = {}
    for c in targets:
        base = rows_by_primary.get(c.primary)
        if base is None:
            continue
        src = source_of(c)
        try:
            clean, drops = clean_rows(base, c.ticker, src, snapshot_ts)
            ann, ann_drops = clean_rows(annual_by_primary.get(c.primary, []), c.ticker, src, snapshot_ts)
            # Inside the guard, so a value polars or pandera rejects costs this listing and no other.
            # The frame build and the schema call used to happen once, over the whole pool, outside
            # every try: one bad row lost all 68 listings *and* the fundamentals_runs.jsonl line that
            # would have explained where the day's data went.
            validate_listing(clean, ann)
        except Exception as exc:  # noqa: BLE001 - isolation: one listing must never sink the rest
            invalid[c.ticker] = repr(exc)
            log.exception("%s: rows rejected before the write; dropped, the rest of the pool stands", c.ticker)
            continue
        out += clean
        dropped += drops
        if c.primary != c.ticker:
            log.info("%s: %d quarters copied from %s", c.ticker, len(clean), c.primary)
        annual_out += ann
        dropped += ann_drops

    out.sort(key=lambda r: (r["ticker"], r["period_end"]))
    annual_out.sort(key=lambda r: (r["ticker"], r["period_end"]))
    return {
        "rows": out,
        "annual": annual_out,
        "coverage": coverage,
        "dropped": dropped,
        "failed": failed,
        "invalid": invalid,
        "primaries": primaries,
    }


def _affected(targets: list[Company], failed: dict[str, str]) -> list[str]:
    """Listings left without fundamentals because the primary they depend on failed."""
    return sorted(c.ticker for c in targets if c.primary in failed)


def _recent_drops(dropped: list[dict], day: str, within_days: int = RECENT_DROP_DAYS) -> list[dict]:
    """Dropped periods recent enough to break a listing's current TTM window.

    A chain break in 2016 is history; the same break on the newest quarter costs the listing its
    trailing PE this week. Counting them together hides the second among the first.
    """
    out: list[dict] = []
    for d in dropped:
        pe = str(d.get("period_end") or "")
        if d.get("kind") != "period" or not DATE_RE.match(pe):
            continue
        if 0 <= (_d(day) - _d(pe)).days <= within_days:
            out.append(d)
    return out


def _merge_existing(df, path, key: list[str]):
    """Keep rows an earlier partial run already wrote for this date.

    A `--source sec` or `--tickers` run covers part of the pool by design. Writing only that part
    would throw away the listings a full run fetched an hour earlier, so the new rows are merged over
    whatever is already on disk for the same date, new rows winning on the key.
    """
    if not path.exists():
        return df
    try:
        old = pl.read_parquet(path)
    except Exception as exc:  # a corrupt part must not block the fresh data
        log.warning("could not read %s to merge (%s); overwriting", path, exc)
        return df
    if not old.height:
        return df
    fresh = set(df.select(key).rows()) if df.height else set()
    keep = old.filter(~pl.struct(key).is_in([dict(zip(key, k, strict=False)) for k in fresh])) if fresh else old
    merged = pl.concat([keep, df], how="diagonal_relaxed") if df.height else keep
    log.info("merged %d existing rows from %s with %d new rows", keep.height, path.name, df.height)
    return merged.unique(subset=key, keep="last", maintain_order=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="", help="comma-separated tickers; default is the whole pool")
    ap.add_argument("--source", default="", help="comma-separated: eastmoney, sec, yahoo")
    args = ap.parse_args(argv)
    setup_logging()

    targets = select(args.tickers, args.source)
    if not targets:
        log.error("no listings selected (--tickers=%r --source=%r)", args.tickers, args.source)
        return 1

    ts = utc_now()
    day, hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    result = build(targets, snapshot_ts, day, hhmm)
    rows, annual, failed, dropped = result["rows"], result["annual"], result["failed"], result["dropped"]
    invalid: dict[str, str] = result["invalid"]

    # Write whatever we did get, before reporting failure.
    per_ticker: dict[str, int] = {}
    for r in rows:
        per_ticker[r["ticker"]] = per_ticker.get(r["ticker"], 0) + 1
    annual_tickers = {r["ticker"] for r in annual}
    thin = sorted(t for t, n in per_ticker.items() if n < MIN_QUARTERS)
    # Yahoo publishes no quarterly income statement at all for several HK and JP listings; those
    # listings are carried by the annual table rather than being a failure. A listing dropped for a
    # rejected row is not one of them and must not be reported as "no quarterly statement".
    empty = sorted(
        c.ticker
        for c in targets
        if c.ticker not in per_ticker and c.primary not in result["failed"] and c.ticker not in invalid
    )
    annual_only = [t for t in empty if t in annual_tickers]

    # Both writes are guarded: the frame build and the schema call used to sit outside every try, so
    # a failure here took the fundamentals_runs.jsonl line with it and the run that lost the day's
    # data left nothing that said so. Now nothing is written, the reason is named, and the run is
    # recorded and exits non-zero like any other failure.
    path = None
    annual_path = None
    table_rows: int | None = None
    annual_table_rows: int | None = None
    write_error: str | None = None
    try:
        if rows:
            target = SNAP_DIR / "valuation" / "fundamentals" / f"{day}.parquet"
            df = _merge_existing(fundamentals_frame(rows), target, FUNDAMENTAL_KEY)
            FUNDAMENTALS_SCHEMA.validate(df)
            path = write_parquet(df, target)
            table_rows = df.height
            log.info("wrote %s (%d rows, %d listings this run)", path, df.height, len(per_ticker))
        else:
            # Every primary failed, or every source answered with nothing. Writing the empty frame
            # would replace a good table with a hole and, on a new date, make that hole the newest
            # snapshot read.load() sees - so the run is recorded and the table is left as it is.
            log.error("no rows fetched; leaving the %s snapshot untouched rather than emptying it", day)

        if annual:
            annual_target = SNAP_DIR / "valuation" / "fundamentals_annual" / f"{day}.parquet"
            adf = _merge_existing(fundamentals_frame(annual), annual_target, FUNDAMENTAL_KEY)
            FUNDAMENTALS_SCHEMA.validate(adf)
            annual_path = write_parquet(adf, annual_target)
            annual_table_rows = adf.height
            log.info("wrote %s (%d annual rows)", annual_path, adf.height)
    except Exception as exc:  # noqa: BLE001 - the run record is written either way, and says why
        log.exception("writing the %s snapshots failed", day)
        write_error = repr(exc)

    recent = _recent_drops(dropped, day)
    recent_names = sorted({f"{d.get('ticker')} {d.get('period_end')}" for d in recent})
    voided = [d for d in dropped if d.get("kind") == "revenue"]
    periods_dropped = [d for d in dropped if d.get("kind") != "revenue"]
    checks = [
        check(
            "Snapshot written",
            bool(rows or annual) and write_error is None,
            f"the write failed, nothing landed, the snapshots on disk were left as they are: {write_error}"
            if write_error
            else (
                f"{len(rows)} quarterly rows for {len(per_ticker)} listings"
                + (f", {len(annual)} annual rows" if annual else "")
            )
            if (rows or annual)
            else "no rows fetched from any source; the snapshots on disk were left as they are",
        ),
        check(
            "Rows validated",
            not invalid,
            f"{len(invalid)} listing(s) dropped for a row the schema rejected: "
            + "; ".join(f"{t}: {why}" for t, why in sorted(invalid.items()))
            if invalid
            else f"every one of {len(per_ticker)} listings passed the schema before the write",
        ),
        check(
            "Listings fetched",
            not failed,
            f"{len(targets) - len(_affected(targets, failed))} of {len(targets)} listings fetched"
            + (f"; failed: {', '.join(sorted(failed))}" if failed else ""),
        ),
        check(
            "Quarterly coverage",
            not empty,
            f"{len(empty)} listings with no quarterly statement"
            + (
                f" ({', '.join(empty)}; {len(annual_only)} fall back to the last full fiscal year, "
                "which publish reports as basis=last_fy, not a trailing twelve months)"
                if empty
                else ""
            ),
            warn=bool(empty) and empty == annual_only,
        ),
        check(
            "Quarters per listing",
            not thin,
            f"{len(thin)} listings with fewer than {MIN_QUARTERS} quarters"
            + (f" ({', '.join(thin)})" if thin else ""),
            warn=True,  # Yahoo only ever publishes 4-5 quarters; the annual table covers the rest
        ),
        check(
            "Dropped periods",
            not dropped,
            f"{len(periods_dropped)} periods dropped (broken chains, bad dates), "
            f"{len(voided)} negative revenues voided with the row kept",
            warn=True,
        ),
        check(
            "Recent periods intact",
            not recent,
            f"{len(recent)} periods dropped inside the last {RECENT_DROP_DAYS} days"
            + (
                f" ({', '.join(recent_names)}) - those listings lose their trailing PE this run"
                if recent
                else "; every listing's current TTM window is intact"
            ),
        ),
    ]
    for c in checks:
        log.info("check %s: %s - %s", c["status"], c["name"], c["detail"])

    append_jsonl(
        {
            "ts": snapshot_ts,
            "listings": len(targets),
            "primaries": len(result["primaries"]),
            "rows": len(rows),  # rows this run produced
            "table_rows": table_rows,  # rows in the table after merging the date; None if not written
            "snapshot": str(path) if path else None,  # None when the run wrote nothing
            "annual_rows": len(annual),
            "annual_table_rows": annual_table_rows,
            "annual_snapshot": str(annual_path) if annual_path else None,
            "failed": failed,
            "invalid": invalid,  # listings dropped because their own rows failed the schema
            "write_error": write_error,
            "affected": _affected(targets, failed),
            "dropped": dropped,
            "thin": thin,
            "no_quarterly": empty,
            "annual_only": annual_only,
            "coverage": result["coverage"],
            "checks": checks,
        },
        SNAP_DIR / "valuation" / "fundamentals_runs.jsonl",
    )
    if failed:
        log.error("%d of %d primaries failed: %s", len(failed), len(result["primaries"]), ", ".join(sorted(failed)))
        return 1
    if invalid:
        log.error("%d listing(s) dropped for a rejected row: %s", len(invalid), ", ".join(sorted(invalid)))
        return 1
    if write_error is not None:
        log.error("the snapshot write failed: %s", write_error)
        return 1
    if not rows and not annual:
        log.error("no rows fetched from any source; nothing was written")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
