"""Analyst consensus EPS from Yahoo Finance's ``earningsTrend`` module.

Usage:
    uv run python -m pipelines.valuation.estimates_yf [--tickers COHR,5802.T]

Covers every listing with ``estimates == "yahoo"``: everything outside the A-share pool, 37 of the
68 listings today. East Money supplies the A-share rows through ``estimates_em`` and writes into
the same two tables, so this module writes ``<date>-yahoo.parquet`` and a later step merges the
halves.

Writes:
    data/snapshots/valuation/estimates/<date>-yahoo.parquet  current consensus (0y, +1y) + the prior actual
    data/snapshots/valuation/vintages/<date>-yahoo.parquet   the same means as of 7/30/60/90 days ago
    data/raw/valuation/yahoo/<date>/<ticker>.json.gz         the quoteSummary payload, archived before parsing
    data/snapshots/valuation/estimates_yf_runs.jsonl         one line per run, with the checks

Both snapshots are a *part* of the day's table -- ``read.py`` keys a file by ``stem[:10]`` and
concatenates every part for a date -- so a ``--tickers`` run covering a handful of listings merges
its rows over whatever a fuller run wrote earlier the same day instead of replacing it, and a run
that parsed nothing writes no file at all rather than truncating a good one.

Three things this module exists to get right:

*Year ends.* Yahoo's ``0y`` is the fiscal year in progress, not the calendar year: COHR's ``0y``
ends 2027-06-30, AVGO's 2026-10-31, Sumitomo Electric's 2027-03-31. Rows are labelled by the
calendar year the fiscal year ends in (a year ending 2027-06-30 is FY2027) and the reported
``endDate`` always wins. The configured ``fy_end_month`` only drives a warning when the two are
more than 45 days apart, which is how a fiscal year that has ended but not yet been reported
shows up. The quarters (``0q``, ``+1q``) are ignored.

*Currency.* ``earningsEstimate`` is stated in the issuer's reporting currency, which for a dual
listing is not the trading currency: CATL's H line 3750.HK is quoted in HKD and forecast in CNY, so
a PE computed without the conversion is ~17% wrong at today's rate. Only two payload fields are
trusted -- ``earningsEstimate.currency`` and ``price.financialCurrency`` -- and then the currency
implied by config. ``price.currency`` is deliberately **never** consulted: it is the currency the
line *trades* in, which is precisely the wrong answer for 3750.HK (HKD), 6869.HK, 1211.HK and
IKA.L, where it reads ``GBp`` against financials kept in pounds. A listing whose reporting currency
cannot be established this way is left null with a warning rather than stamped with its market's
currency. Whatever string does arrive is kept verbatim, because Yahoo distinguishes ``GBp`` (pence)
from ``GBP`` and confusing those is a 100x error, not a 17% one.

*Analyst counts on vintages.* Yahoo publishes historical means in ``epsTrend`` but no historical
analyst count, so every vintage row carries the *current* count for that period. Treat
``vintages.n_analysts`` as "coverage today", not "coverage on ``as_of``".

Even with ``formatted=false`` the numbers in this path arrive as ``{"raw": ..., "fmt": ...}`` and any
field can be ``{}`` or absent, so every numeric read goes through ``_raw``.
"""

from __future__ import annotations

import argparse
import calendar
import logging
import math
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
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
from pipelines.valuation.config import BY_TICKER, COMPANIES, THIN_COVERAGE_BELOW, Company
from pipelines.valuation.schema import (
    ESTIMATE_KEY,
    ESTIMATES_SCHEMA,
    VINTAGE_KEY,
    VINTAGES_SCHEMA,
    estimates_frame,
    vintages_frame,
)

log = logging.getLogger("valuation.estimates_yf")

QUOTE_SUMMARY = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

SOURCE = "yahoo"
ORIGIN = "yahoo_trend"
MODULES = "earningsTrend,price"
MIN_INTERVAL = 0.6

#: the two fiscal-year periods we keep; ``0q`` / ``+1q`` are quarters and are dropped
FY_PERIODS = ("0y", "+1y")
#: ``epsTrend`` keys and how many days back they were true
VINTAGE_KEYS = (("current", 0), ("7daysAgo", 7), ("30daysAgo", 30), ("60daysAgo", 60), ("90daysAgo", 90))
#: how far the reported fiscal-year end may sit from the configured one before it is worth a warning
FY_END_TOLERANCE_DAYS = 45

#: reporting currency implied by the market of the *reporting* entity. ``hk`` is deliberately
#: absent: an HK line is usually the secondary listing of a mainland issuer that reports in CNY, so
#: the market alone says nothing about the reporting currency. Dual listings resolve through
#: ``Company.primary`` instead; a standalone HK listing gets a null currency and a warning.
MARKET_CURRENCY = {"cn": "CNY", "us": "USD", "jp": "JPY", "kr": "KRW", "tw": "TWD", "uk": "GBP"}


# --- payload helpers -----------------------------------------------------------------
def _raw(node: Any, key: str) -> float | None:
    """A number out of Yahoo's ``{"raw": ..., "fmt": ...}`` wrapper, or None.

    Tolerates a missing key, a plain scalar instead of the wrapper, ``{}``, a string number and a
    null ``raw``. Bools are not numbers here, and non-finite values are treated as missing.
    """
    if not isinstance(node, dict):
        return None
    value = node.get(key)
    if isinstance(value, dict):
        value = value.get("raw")
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


#: ``n_analysts`` is written into a polars Int64; anything outside it is not a count of people
INT64_MAX = 2**63 - 1


def _int(node: Any, key: str) -> int | None:
    """An integer field, or None when it is missing or could not be an analyst count.

    An out-of-range value is dropped rather than carried: ``pl.DataFrame(schema=...)`` raises on a
    value wider than Int64, and one nonsense count must not cost the listing its whole row. None
    reads as thin coverage downstream, so it surfaces as a warning instead of vanishing.
    """
    value = _raw(node, key)
    if value is None:
        return None
    if abs(value) > INT64_MAX:
        log.warning("%s=%r is out of Int64 range; treated as missing", key, value)
        return None
    return int(value)


def _text(node: Any, key: str) -> str | None:
    """A string field, unwrapped the same way and kept verbatim (``GBp`` must stay ``GBp``)."""
    if not isinstance(node, dict):
        return None
    value = node.get(key)
    if isinstance(value, dict):
        value = value.get("raw") or value.get("fmt")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_date(value: Any) -> date | None:
    """``endDate`` is an ISO string with ``formatted=false``, an epoch second otherwise."""
    if isinstance(value, dict):
        value = value.get("raw") if value.get("raw") is not None else value.get("fmt")
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _minus_one_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return _month_end(day.year - 1, day.month)


# --- derivation rules ----------------------------------------------------------------
def expected_fy_end(fy_end_month: int, today: date) -> date:
    """The first fiscal-year end strictly after ``today``, from the configured month.

    This is what ``0y`` should be pointing at. It is a cross-check only: when it disagrees with
    Yahoo the reported ``endDate`` still wins, because the usual cause is a fiscal year that has
    ended but has not been reported yet, and Yahoo knows that before the config does.
    """
    end = _month_end(today.year, fy_end_month)
    if end <= today:
        end = _month_end(today.year + 1, fy_end_month)
    return end


def fy_end_drift(period_end: date, fy_end_month: int, today: date) -> tuple[date, int]:
    """(the fiscal-year end config can best justify, how many days the reported one sits from it).

    Comparing against ``expected_fy_end`` alone misfires on the three listings whose configured
    month is one short of their real year end (AVGO, ``fiscal year ends early November`` with
    ``fy_end_month=10``; MTSI; CIEN). On 31 October ``expected_fy_end`` has already rolled to the
    next year, so AVGO's real 2026-11-01 reads as 364 days of drift instead of one.

    So the year end that has *just* passed counts as a candidate too, but only while it is still
    inside the tolerance window. A year that ended months ago does not qualify, which is what keeps
    the real signal -- Yahoo still pointing at a finished, unreported year -- firing.
    """
    candidates = [expected_fy_end(fy_end_month, today)]
    previous = _minus_one_year(candidates[0])
    if previous >= today - timedelta(days=FY_END_TOLERANCE_DAYS):
        candidates.append(previous)
    return min(((c, abs((period_end - c).days)) for c in candidates), key=lambda pair: pair[1])


def fy_label(period_end: date) -> str:
    """Label by the calendar year the fiscal year *ends* in: 2027-06-30 -> FY2027."""
    return f"FY{period_end.year}"


def implied_currency(company: Company) -> str | None:
    """Reporting currency implied by config alone, or None when config cannot say.

    A dual listing reports in the currency of its primary line's market (3750.HK -> 300750.SZ ->
    CNY), never its own trading currency. A standalone listing in a market where the two routinely
    differ returns None rather than a guess.
    """
    primary = BY_TICKER.get(company.primary)
    if primary is not None and primary.ticker != company.ticker:
        return MARKET_CURRENCY.get(primary.market)
    return MARKET_CURRENCY.get(company.market)


def resolve_currency(
    trend: list[dict], price: dict | None, company: Company
) -> tuple[str | None, str]:
    """(currency, where it came from) for the EPS figures of one listing.

    Order: the ``earningsEstimate`` block, then ``price.financialCurrency``, then config.

    ``price.currency`` is **not** in that list and must never be added to it. It is the currency the
    line trades in, and for every listing where this function has real work to do the two differ:
    3750.HK trades HKD and reports CNY, IKA.L quotes ``GBp`` against financials in pounds. Taking it
    would silently produce a plausible-looking number that is 17% (or 100x) wrong, and would also
    make the null branch below unreachable, because Yahoo populates it on essentially every line.

    ``source`` is reported so the caller can warn on the inferred ones.
    """
    for node in trend or []:
        found = _text((node or {}).get("earningsEstimate") or {}, "currency")
        if found:
            return found, "earningsEstimate"
    found = _text(price or {}, "financialCurrency")
    if found:
        return found, "price.financialCurrency"
    guess = implied_currency(company)
    if guess is None:
        return None, "none"
    return guess, "primary-listing" if company.fundamentals_from else "market"


def collision_check(ticker: str, rows: list[dict], key: list[str], label: str) -> list[dict]:
    """A fail check when two rows share a table key, because the frame builders hide that.

    ``estimates_frame`` / ``vintages_frame`` call ``.unique(subset=KEY, keep="first")`` before the
    frame ever reaches pandera, so the schemas' ``unique=`` guards structurally cannot fire: a
    collision is resolved in favour of whichever row parsed first and nothing records the loss.
    Yahoo does produce them -- a thinly covered name that has not rolled its year can return the
    same ``endDate`` for ``0y`` and ``+1y``, which would silently publish next year's consensus as
    a copy of this year's. Detecting it here is the only place it is still visible.
    """
    seen: set[tuple] = set()
    clashes: list[str] = []
    for row in rows:
        ident = tuple(row.get(column) for column in key)
        if ident in seen:
            clashes.append(" / ".join(str(part) for part in ident))
        seen.add(ident)
    if not clashes:
        return []
    return [
        check(
            "Yahoo key collision",
            False,
            f"{ticker}: {len(clashes)} {label} row(s) repeat a key ({', '.join(key)}): "
            f"{', '.join(sorted(set(clashes)))}; only the first survives the frame build",
        )
    ]


# --- parsing -------------------------------------------------------------------------
def parse_trend(
    ticker: str,
    company: Company,
    trend: list[dict],
    snapshot_ts: datetime,
    *,
    price: dict | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Turn one ``earningsTrend.trend`` list into (estimate rows, vintage rows, checks).

    Pure: no network, no clock, no filesystem. ``snapshot_ts`` is the run's aware UTC timestamp and
    is the only source of "today", both for the fiscal-year cross-check and for the vintage
    ``as_of`` dates. ``price`` is the quoteSummary ``price`` module when it was fetched, used only
    to resolve the currency.

    Estimate rows: ``0y`` and ``+1y`` as mark ``E``, plus the year before ``0y`` as mark ``A`` built
    from ``yearAgoEps`` -- that actual is what the calendarisation layer blends with the estimate
    for a non-December year end. A missing ``yearAgoEps`` (observed on 3750.HK) produces no actual
    row rather than an invented one.
    """
    stamp = snapshot_ts.isoformat(timespec="seconds")
    today = snapshot_ts.date()
    estimates: list[dict] = []
    vintages: list[dict] = []
    checks: list[dict] = []

    by_period: dict[str, dict] = {}
    for node in trend or []:
        key = str((node or {}).get("period") or "")
        if key and key not in by_period:
            by_period[key] = node or {}

    currency, currency_source = resolve_currency(trend, price, company)
    if currency is None:
        checks.append(
            check(
                "Yahoo EPS currency",
                False,
                f"{ticker}: no currency in earningsEstimate or price.financialCurrency and config "
                f"cannot imply one; left null rather than assuming the {company.market} trading "
                f"currency",
                warn=True,
            )
        )
    elif currency_source == "primary-listing":
        checks.append(
            check(
                "Yahoo EPS currency",
                False,
                f"{ticker}: no currency in the payload; assumed {currency} from the primary "
                f"listing {company.primary}",
                warn=True,
            )
        )
    else:
        log.debug("%s: EPS currency %s from %s", ticker, currency, currency_source)

    if FY_PERIODS[0] not in by_period:
        checks.append(
            check(
                "Yahoo consensus coverage",
                False,
                f"{ticker}: earningsTrend has no 0y period (got {sorted(by_period) or 'nothing'})",
                warn=True,
            )
        )

    for period in FY_PERIODS:
        node = by_period.get(period)
        if node is None:
            continue
        period_end = _parse_date(node.get("endDate"))
        if period_end is None:
            checks.append(
                check(
                    "Yahoo consensus coverage",
                    False,
                    f"{ticker}: {period} has no usable endDate ({node.get('endDate')!r}); period skipped",
                    warn=True,
                )
            )
            continue
        est = node.get("earningsEstimate") or {}
        n_analysts = _int(est, "numberOfAnalysts")

        if period == "0y":
            want, drift = fy_end_drift(period_end, company.fy_end_month, today)
            if drift > FY_END_TOLERANCE_DAYS:
                checks.append(
                    check(
                        "Yahoo fiscal year end",
                        False,
                        f"{ticker}: Yahoo 0y ends {period_end.isoformat()} but fy_end_month="
                        f"{company.fy_end_month} implies {want.isoformat()} ({drift} days apart); "
                        f"using the reported {period_end.isoformat()}",
                        warn=True,
                    )
                )

        estimates.append(
            {
                "snapshot_ts": stamp,
                "ticker": ticker,
                "source": SOURCE,
                "period_end": period_end.isoformat(),
                "fy_label": fy_label(period_end),
                "mark": "E",
                "eps_avg": _raw(est, "avg"),
                "eps_low": _raw(est, "low"),
                "eps_high": _raw(est, "high"),
                "n_analysts": n_analysts,
                "currency": currency,
                "net_profit_avg": None,  # Yahoo forecasts EPS only; East Money also forecasts net profit
            }
        )

        if period == "0y":
            year_ago = _raw(est, "yearAgoEps")
            if year_ago is None and company.fy_end_month != 12:
                # calendarize blends the estimate with the prior actual for a non-December filer,
                # so without that actual the listing drops out of the current calendar year entirely
                checks.append(
                    check(
                        "Yahoo prior-year actual",
                        False,
                        f"{ticker}: 0y ending {period_end.isoformat()} has no yearAgoEps and the "
                        f"fiscal year ends in month {company.fy_end_month}, so the calendar-year "
                        f"blend for the year in progress will be dropped for want of coverage",
                        warn=True,
                    )
                )
            if year_ago is not None:
                prior_end = _minus_one_year(period_end)
                estimates.append(
                    {
                        "snapshot_ts": stamp,
                        "ticker": ticker,
                        "source": SOURCE,
                        "period_end": prior_end.isoformat(),
                        "fy_label": fy_label(prior_end),
                        "mark": "A",
                        "eps_avg": year_ago,
                        "eps_low": None,
                        "eps_high": None,
                        "n_analysts": None,  # a reported actual has no analyst count
                        "currency": currency,
                        "net_profit_avg": None,
                    }
                )

        eps_trend = node.get("epsTrend") or {}
        for key, days_back in VINTAGE_KEYS:
            value = _raw(eps_trend, key)
            if value is None:
                continue  # a null vintage is a hole in the revision series, not a data point
            vintages.append(
                {
                    "snapshot_ts": stamp,
                    "ticker": ticker,
                    "source": SOURCE,
                    "period_end": period_end.isoformat(),
                    "as_of": (today - timedelta(days=days_back)).isoformat(),
                    "eps_avg": value,
                    "n_analysts": n_analysts,  # current count; Yahoo publishes no historical one
                    "currency": currency,
                    "origin": ORIGIN,
                }
            )

    checks += collision_check(ticker, estimates, ESTIMATE_KEY, "estimate")
    checks += collision_check(ticker, vintages, VINTAGE_KEY, "vintage")
    return estimates, vintages, checks


def extract_result(payload: dict) -> dict:
    """The single ``quoteSummary.result`` entry, or an error explaining why there is none."""
    summary = (payload or {}).get("quoteSummary") or {}
    if summary.get("error"):
        raise ValueError(f"quoteSummary error: {summary['error']}")
    result = summary.get("result") or []
    if not result:
        raise ValueError("quoteSummary returned no result")
    return result[0] or {}


def trend_of(result: dict) -> list[dict]:
    return ((result or {}).get("earningsTrend") or {}).get("trend") or []


def price_of(result: dict) -> dict:
    return (result or {}).get("price") or {}


# --- network -------------------------------------------------------------------------
def session(*, min_interval: float = MIN_INTERVAL) -> tuple[HttpClient, str | None]:
    """An HttpClient for quoteSummary plus the crumb it needs, if the handshake succeeds.

    Yahoo gates quoteSummary on a consent cookie and a matching crumb. HttpClient has no cookie jar
    of its own, so the handshake runs once on a throwaway httpx client and its cookies are replayed
    as a header. A failed handshake is not fatal: the fetch is still attempted, and the 401 that
    follows surfaces per ticker like any other failure instead of aborting the run.
    """
    headers = {"User-Agent": BROWSER_UA}
    crumb: str | None = None
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as boot:
            try:
                boot.get(COOKIE_URL)  # 404s, but sets the A1/A3 consent cookies
            except httpx.HTTPError as exc:
                log.warning("yahoo cookie fetch failed: %s", exc)
            try:
                reply = boot.get(CRUMB_URL)
                if reply.status_code == 200 and reply.text.strip():
                    crumb = reply.text.strip()
            except httpx.HTTPError as exc:
                # guarded separately from the cookie GET: a transient failure on this one request
                # must not also discard the jar below, which on its own often carries the fetch
                log.warning("yahoo crumb fetch failed (%s); keeping the cookies anyway", exc)
            jar = "; ".join(f"{k}={v}" for k, v in boot.cookies.items())
            if jar:
                headers["Cookie"] = jar
    except httpx.HTTPError as exc:
        log.warning("yahoo crumb handshake failed (%s); continuing without a crumb", exc)
    if crumb is None:
        log.warning("no yahoo crumb; quoteSummary may answer 401")
    return HttpClient(min_interval=min_interval, headers=headers), crumb


def fetch(
    client: HttpClient,
    ticker: str,
    *,
    crumb: str | None = None,
    on_discard: Callable[[dict], None] | None = None,
) -> dict:
    """The raw quoteSummary payload for one ticker, unparsed, exactly as archived.

    The retry exists for one narrow case: a listing that rejects the ``price`` module still answers
    for ``earningsTrend`` alone. A payload carrying an ``error`` is *not* that case -- a delisted or
    renamed symbol answers the same way whatever modules are asked for -- so it is returned as it
    came, which spends one call instead of two on an endpoint that rate-limits hard and keeps the
    error itself in the archive, where it is the only thing that explains the failure.

    ``on_discard`` is handed the first payload when a retry does supersede it, so that response is
    archived too rather than disappearing from the evidence layer.
    """
    params: dict[str, Any] = {
        "modules": MODULES,
        "corsDomain": "finance.yahoo.com",
        "formatted": "false",
    }
    if crumb:
        params["crumb"] = crumb
    url = QUOTE_SUMMARY + quote(ticker, safe="")
    payload = client.get_json(url, params=params)
    summary = (payload or {}).get("quoteSummary") or {}
    if summary.get("result") or summary.get("error"):
        return payload
    log.warning("%s: no result for modules=%s; retrying with earningsTrend only", ticker, MODULES)
    if on_discard is not None:
        on_discard(payload)
    return client.get_json(url, params={**params, "modules": "earningsTrend"})


# --- orchestration -------------------------------------------------------------------
def validate_rows(rows: list[dict], vints: list[dict]) -> None:
    """Prove one company's rows survive the frame build and pandera, before they join the pool.

    Framing and validating only the combined table would put this outside the per-company
    try/except: one listing with a value polars or pandera rejects would then take down all 37 and
    the run record with them, even though the other 36 parsed perfectly. Doing it per company keeps
    a bad payload costing exactly the listing it came from.
    """
    if rows:
        ESTIMATES_SCHEMA.validate(estimates_frame(rows))
    if vints:
        VINTAGES_SCHEMA.validate(vintages_frame(vints))


def merge_existing(df: pl.DataFrame, path: Path, key: list[str]) -> pl.DataFrame:
    """Keep rows an earlier run already wrote to this same part file; new rows win on the key.

    A ``--tickers`` run covers part of the pool by design, and both tables are keyed by ticker, so
    writing only that part would throw away every listing a fuller run fetched earlier the same day.
    Same approach as ``fundamentals._merge_existing``.
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
    log.info("kept %d existing row(s) from %s alongside %d new one(s)", keep.height, path.name, df.height)
    return merged.unique(subset=key, keep="last", maintain_order=True)


def write_table(
    rows: list[dict],
    path: Path,
    frame_fn: Callable[[list[dict]], pl.DataFrame],
    schema: Any,
    key: list[str],
    label: str,
) -> tuple[Path | None, int]:
    """Merge this run's rows into the day's part file and write it. (path, row count) or (None, 0).

    Nothing parsed means nothing is written: an empty frame over a good file is a worse outcome
    than a missing one, and the run record still says so.
    """
    if not rows:
        log.warning("no %s rows parsed; %s left untouched", label, path.name)
        return None, 0
    df = frame_fn(rows)
    schema.validate(df)
    df = merge_existing(df, path, key)
    schema.validate(df)
    return write_parquet(df, path), df.height


def yahoo_companies(tickers: list[str] | None = None) -> list[Company]:
    pool = [c for c in COMPANIES if c.estimates == SOURCE]
    if tickers:
        want = {t.strip() for t in tickers if t.strip()}
        pool = [c for c in pool if c.ticker in want]
        missing = sorted(want - {c.ticker for c in pool})
        if missing:
            log.warning("not in the yahoo-estimates pool, ignored: %s", ", ".join(missing))
    return pool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="", help="comma-separated subset of the yahoo-estimates pool")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL, help="seconds between requests")
    args = ap.parse_args(argv)
    setup_logging()

    ts = utc_now()
    day, hhmm = run_stamp(ts)
    companies = yahoo_companies(args.tickers.split(",") if args.tickers else None)
    client, crumb = session(min_interval=args.min_interval)
    raw_dir = RAW_DIR / "valuation" / SOURCE / day

    estimates: list[dict] = []
    vintages: list[dict] = []
    checks: list[dict] = []
    errors: list[str] = []
    failed: list[str] = []

    def keeper(ticker: str) -> Callable[[dict], None]:
        """Archive a payload the modules retry is about to supersede, under its own name."""

        def keep(payload: dict) -> None:
            write_json_gz(payload, raw_dir / f"{ticker}-first.json.gz")

        return keep

    for company in companies:
        try:
            payload = fetch(client, company.ticker, crumb=crumb, on_discard=keeper(company.ticker))
            write_json_gz(payload, raw_dir / f"{company.ticker}.json.gz")  # evidence first, parse second
            result = extract_result(payload)
            rows, vints, company_checks = parse_trend(
                company.ticker, company, trend_of(result), ts, price=price_of(result)
            )
            validate_rows(rows, vints)  # inside the guard, so a rejected row costs one listing
        except Exception as exc:  # noqa: BLE001 - one company must never lose the others
            log.exception("%s: earningsTrend failed", company.ticker)
            failed.append(company.ticker)
            errors.append(f"{company.ticker}: {exc!r}")
            continue
        estimates += rows
        vintages += vints
        checks += company_checks
        log.info("%s: %d estimate rows, %d vintage rows", company.ticker, len(rows), len(vints))

    thin = sorted({r["ticker"] for r in estimates if r["mark"] == "E" and (r["n_analysts"] or 0) < THIN_COVERAGE_BELOW})
    checks.append(
        check(
            "Analyst coverage",
            not thin,
            f"{len(thin)} listing(s) below {THIN_COVERAGE_BELOW} analysts on a forecast year"
            + (f": {', '.join(thin)}" if thin else ""),
            warn=True,
        )
    )
    checks.append(
        check(
            "Yahoo consensus fetch",
            not failed,
            f"{len(companies) - len(failed)}/{len(companies)} listings fetched"
            + (f"; failed: {', '.join(failed)}" if failed else ""),
        )
    )

    if not estimates:
        checks.append(
            check(
                "Estimates snapshot",
                False,
                f"no estimate rows parsed from {len(companies)} listing(s); today's part file was "
                f"left as it stands rather than overwritten with an empty table",
                warn=True,
            )
        )

    # write what we got before reporting the failure, so a partial run still keeps its data
    written: dict[str, int] = {}
    write_error: str | None = None
    try:
        est_path, est_rows = write_table(
            estimates,
            SNAP_DIR / "valuation" / "estimates" / f"{day}-yahoo.parquet",
            estimates_frame,
            ESTIMATES_SCHEMA,
            ESTIMATE_KEY,
            "estimate",
        )
        if est_path:
            written[str(est_path)] = est_rows
        vin_path, vin_rows = write_table(
            vintages,
            SNAP_DIR / "valuation" / "vintages" / f"{day}-yahoo.parquet",
            vintages_frame,
            VINTAGES_SCHEMA,
            VINTAGE_KEY,
            "vintage",
        )
        if vin_path:
            written[str(vin_path)] = vin_rows
    except Exception as exc:  # noqa: BLE001 - the run record is written either way, and says why
        log.exception("writing the snapshots failed")
        write_error = repr(exc)
        errors.append(f"write: {exc!r}")
        checks.append(check("Estimates snapshot", False, f"snapshot write failed: {exc!r}"))

    summary = {
        "ts": ts.isoformat(timespec="seconds"),
        "date": day,
        "hhmm": hhmm,
        "source": SOURCE,
        "companies": len(companies),
        "estimate_rows": len(estimates),
        "vintage_rows": len(vintages),
        "written": written,
        "failed": failed,
        "errors": errors,
        "http_calls": client.calls,
        "checks": checks,
    }
    # last, but never conditional on the write: the audit line is what explains a run that lost data
    append_jsonl(summary, SNAP_DIR / "valuation" / "estimates_yf_runs.jsonl")
    log.info(
        "done: %d/%d listings, %d estimate rows, %d vintage rows, %d warn/fail checks",
        len(companies) - len(failed),
        len(companies),
        len(estimates),
        len(vintages),
        sum(1 for c in checks if c["status"] != "pass"),
    )
    return 1 if failed or write_error else 0


if __name__ == "__main__":
    sys.exit(main())
