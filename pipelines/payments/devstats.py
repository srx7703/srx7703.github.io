"""Developer downloads: npm and PyPI counts for the pinned payment SDKs, and the Q1 ratio.

This is the "developer downloads" lens of the share chapter (PLAN §3 M3d, EXECUTION W2e) and the input to
Q1 in section H of docs/EVALUATION_PLAN.md ("developer mindshare is more concentrated than money"). A
download is a package-manager fetch (CI runs, mirrors and bots included), not a developer and not a
dollar, so the page always calls it *downloads* and shows indexed trends next to the one registered ratio.

Package sets (pinned in config.py, registered in section H)
-----------------------------------------------------------
* ``NPM_CLIENT_SDKS``: company -> client-side packages, summed per company. Framework wrappers
  (``NPM_EXCLUDED_WRAPPERS``) install the core package, so adding them would double count; they are refused
  here even if someone adds one to the whitelist by mistake.
* ``NPM_SERVER_SDKS``: server libraries, descriptive only, kept in a separate table and never in Q1.
* PyPI server SDKs that belong to the companies (``stripe``, ``adyen``, ``braintree``). The PyPI packages in
  ``NOT_COMPANY_PACKAGES`` (``affirm``, ``airwallex``) are third-party and never fetched.

Sources (keyless)
-----------------
npm::

    GET https://api.npmjs.org/downloads/range/{start}:{end}/{package}
    {"start": "2026-09-01", "end": "2026-09-03", "package": "@stripe/stripe-js",
     "downloads": [{"downloads": 2338296, "day": "2026-09-01"}, ...]}

One query covers at most 18 months (the API's limit), so history is fetched in chunks. Scoped packages keep
the ``/`` between scope and name as a literal path separator on this endpoint: the registry docs ask for
``%2F`` only on ``/versions``, and the downloads route itself is shown with ``@scope/name``. Only the other
characters are percent-encoded. The most recent day can read 0 before npm's daily job has run, so trailing
zeros in the last few days of a response are treated as "not yet reported", not as zero downloads.

PyPI (pypistats.org, which keeps 180 days and rate-limits hard)::

    GET https://pypistats.org/api/packages/{package}/overall?mirrors=false
    {"data": [{"category": "without_mirrors", "date": "2026-04-01", "downloads": 123}, ...],
     "package": "stripe", "type": "overall_downloads"}

Aggregation
-----------
Weekly (ISO weeks, Monday start) and calendar-month totals, kept only when the period is complete up to the
as-of day (the earliest last-reported day across the series in the table), so a half month never looks like
a drop. A company total exists for a period only when every one of its packages reported that period; a
failed package removes its company from the run instead of leaving a partial sum. Monthly series carry an
index against ``BASE_MONTH`` (npm) or the first complete month in the 180-day window (PyPI).

Q1 (as registered)
------------------
For each complete month: Stripe's share of client-SDK npm downloads among the five companies, divided by the
*upper bound* of Stripe's share of disclosed volume (an interval, because volume definitions differ; the
bound comes from the share lens, ``share.py``). ``ratio >= 2.0`` holds; refuted when the ratio is below 1.5 in
3 consecutive monthly refreshes. Only months from the registration month on count as refreshes: earlier
months were "already seen" and are shown, not graded. The thresholds are parsed from ``config.Q1`` so the
code cannot drift from the registered text.

Failure isolation (CLAUDE.md rule 5)
------------------------------------
Every (package, chunk) request fails on its own and is recorded as a failed check; one failed chunk drops
that package (a gap inside a series would fake a dip), never the others.

Usage (runs in GitHub Actions; this container has no outbound network)::

    uv run python -m pipelines.payments.devstats [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, RAW_DIR, run_stamp, utc_now, write_json_gz, write_parquet
from pipelines.payments.config import (
    NOT_COMPANY_PACKAGES,
    NPM_CLIENT_SDKS,
    NPM_EXCLUDED_WRAPPERS,
    NPM_SERVER_SDKS,
    Q1,
)

log = logging.getLogger("payments.devstats")

# --- constants -------------------------------------------------------------------------------

NPM_BASE = "https://api.npmjs.org"
PYPI_BASE = "https://pypistats.org/api"

#: First day fetched from npm. Matches the web-coverage lens (webtech.START) so the lenses share a window.
NPM_START = date(2020, 1, 1)
#: npm allows at most 18 months per range query; 500 days stays inside it for every month length.
NPM_MAX_DAYS = 500
#: A response's trailing zero days inside this many days of its end are "not yet reported".
TRAILING_GRACE_DAYS = 3

#: Company -> PyPI server SDK. Only packages the companies publish; NOT_COMPANY_PACKAGES are refused below.
PYPI_SERVER_SDKS: dict[str, str] = {"Stripe": "stripe", "Adyen": "adyen", "PayPal": "braintree"}

#: npm server package -> company, for labelling the descriptive table.
NPM_SERVER_COMPANY: dict[str, str] = {"stripe": "Stripe", "@adyen/api-library": "Adyen", "braintree": "PayPal"}

#: Index base for npm monthly series (=100). Not a registered threshold: it only scales the trend chart.
BASE_MONTH = date(2025, 1, 1)

#: Q1 is a forward test from the month section H was registered (2026-10-02/03).
Q1_FIRST_GRADED_MONTH = date(2026, 10, 1)
Q1_COMPANY = "Stripe"


def _num(s: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        raise ValueError(f"no number in registered threshold {s!r}")
    return float(m.group())


#: Registered thresholds, parsed from the pinned strings ("2.0x", "1.5", "3").
Q1_HOLDS = _num(Q1["ratio_holds"])
Q1_REFUTED = _num(Q1["ratio_refuted"])
Q1_CONSECUTIVE = int(_num(Q1["consecutive_refreshes"]))


def _client_packages() -> dict[str, str]:
    """Client package -> company, refusing wrappers and not-company packages (they would double count)."""
    out: dict[str, str] = {}
    banned = set(NPM_EXCLUDED_WRAPPERS) | set(NOT_COMPANY_PACKAGES["npm"])
    for company, pkgs in NPM_CLIENT_SDKS.items():
        for p in pkgs:
            if p in banned:
                raise ValueError(f"{p} is an excluded wrapper / not-company package; never counted")
            out[p] = company
    return out


def _pypi_packages() -> dict[str, str]:
    banned = set(NOT_COMPANY_PACKAGES["pypi"])
    bad = [p for p in PYPI_SERVER_SDKS.values() if p in banned]
    if bad:
        raise ValueError(f"not-company PyPI packages in the whitelist: {bad}")
    return {p: c for c, p in PYPI_SERVER_SDKS.items()}


CLIENT_PACKAGES = _client_packages()
PYPI_PACKAGES = _pypi_packages()

# --- schemas ---------------------------------------------------------------------------------

DAILY_DTYPES: dict[str, pl.DataType] = {"registry": pl.Utf8, "package": pl.Utf8, "day": pl.Date,
                                        "downloads": pl.Int64}

PERIOD_KEY = ["registry", "side", "series", "period"]

WEEKLY_DTYPES: dict[str, pl.DataType] = {
    "registry": pl.Utf8,   # npm | pypi
    "side": pl.Utf8,       # client | server
    "series": pl.Utf8,     # company (client side) or package (server side)
    "company": pl.Utf8,
    "packages": pl.Utf8,   # packages summed into the row, comma-separated
    "period": pl.Date,     # Monday of the ISO week
    "downloads": pl.Int64,
}
MONTHLY_DTYPES: dict[str, pl.DataType] = {
    **{k: v for k, v in WEEKLY_DTYPES.items() if k != "period"},
    "period": pl.Date,     # first day of the month
    "base_month": pl.Date,
    "index": pl.Float64,   # downloads / downloads in base_month * 100; null when the base is 0 or missing
}


def _period_schema(index: bool) -> pa.DataFrameSchema:
    cols: dict[str, pa.Column] = {
        "registry": pa.Column(str, pa.Check.isin(["npm", "pypi"])),
        "side": pa.Column(str, pa.Check.isin(["client", "server"])),
        "series": pa.Column(str),
        "company": pa.Column(str),
        "packages": pa.Column(str),
        "period": pa.Column(pl.Date),
        "downloads": pa.Column(int, pa.Check.ge(0)),
    }
    if index:
        cols["base_month"] = pa.Column(pl.Date, nullable=True)
        cols["index"] = pa.Column(float, pa.Check.ge(0.0), nullable=True)
    return pa.DataFrameSchema(cols, unique=PERIOD_KEY, strict=True, coerce=False)


WEEKLY_SCHEMA = _period_schema(index=False)
MONTHLY_SCHEMA = _period_schema(index=True)

Q1_DTYPES: dict[str, pl.DataType] = {
    "month": pl.Date,
    "stripe_downloads": pl.Int64,
    "pool_downloads": pl.Int64,          # sum over the five companies' client SDKs
    "download_share": pl.Float64,
    "volume_share_upper": pl.Float64,    # upper bound of Stripe's disclosed-volume share; null if unknown
    "ratio": pl.Float64,
    "graded": pl.Boolean,                # month counts as a forward refresh
    "below_refute": pl.Boolean,
    "consecutive_below": pl.Int64,
    "status": pl.Utf8,                   # status as of this month
}
Q1_SCHEMA = pa.DataFrameSchema(
    {
        "month": pa.Column(pl.Date, unique=True),
        "stripe_downloads": pa.Column(int, pa.Check.ge(0)),
        "pool_downloads": pa.Column(int, pa.Check.gt(0)),
        "download_share": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
        "volume_share_upper": pa.Column(float, pa.Check.in_range(0.0, 1.0, include_min=False), nullable=True),
        "ratio": pa.Column(float, pa.Check.ge(0.0), nullable=True),
        "graded": pa.Column(bool),
        "below_refute": pa.Column(bool),
        "consecutive_below": pa.Column(int, pa.Check.ge(0)),
        "status": pa.Column(str, pa.Check.isin(["collecting", "holds", "falsified", "undecidable"])),
    },
    strict=True,
    coerce=False,
)


class JsonClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


# --- urls and chunks -------------------------------------------------------------------------


def npm_path(package: str) -> str:
    """URL path segment for a package on the downloads endpoint.

    ``@stripe/stripe-js`` -> ``@stripe/stripe-js``: the scope separator stays a literal ``/`` (the downloads
    route takes ``@scope/name``; ``%2F`` is only required on ``/versions``). Anything else unsafe is encoded,
    and a name that is not a valid npm package name is refused rather than sent.
    """
    if not re.fullmatch(r"(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*", package):
        raise ValueError(f"not an npm package name: {package!r}")
    return quote(package, safe="@/")


def npm_range_url(package: str, start: date, end: date) -> str:
    return f"{NPM_BASE}/downloads/range/{start.isoformat()}:{end.isoformat()}/{npm_path(package)}"


def date_chunks(start: date, end: date, max_days: int = NPM_MAX_DAYS) -> list[tuple[date, date]]:
    """Inclusive, contiguous, non-overlapping [start, end] chunks of at most ``max_days`` days."""
    if end < start:
        return []
    out = []
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=max_days - 1))
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def pypi_url(package: str) -> str:
    return f"{PYPI_BASE}/packages/{quote(package, safe='')}/overall"


# --- parse -----------------------------------------------------------------------------------


def parse_npm_range(payload: Any, package: str) -> pl.DataFrame:
    """One npm range response -> daily rows. Strict: error bodies, another package or bad rows raise."""
    if not isinstance(payload, dict):
        raise ValueError(f"expected an object, got {type(payload).__name__}")
    if "error" in payload:
        raise ValueError(f"npm error: {payload['error']}")
    if payload.get("package") != package:
        raise ValueError(f"response for {payload.get('package')!r}, asked for {package!r}")
    days = payload.get("downloads")
    if not isinstance(days, list):
        raise ValueError("no downloads list")
    rows = []
    for d in days:
        if not isinstance(d, dict) or "day" not in d or "downloads" not in d:
            raise ValueError(f"malformed day row: {d!r}")
        n = d["downloads"]
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"bad download count {n!r} on {d['day']}")
        rows.append({"registry": "npm", "package": package, "day": date.fromisoformat(d["day"]), "downloads": n})
    return pl.DataFrame(rows, schema=DAILY_DTYPES)


def parse_pypi_overall(payload: Any, package: str) -> pl.DataFrame:
    """pypistats ``overall`` -> daily rows, ``without_mirrors`` only (the call asks for mirrors=false)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"malformed pypistats payload: {str(payload)[:200]}")
    if payload.get("package", package) != package:
        raise ValueError(f"response for {payload.get('package')!r}, asked for {package!r}")
    rows = []
    for d in payload["data"]:
        if not isinstance(d, dict) or not {"category", "date", "downloads"} <= d.keys():
            raise ValueError(f"malformed row: {d!r}")
        if d["category"] != "without_mirrors":
            continue
        n = d["downloads"]
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"bad download count {n!r} on {d['date']}")
        rows.append({"registry": "pypi", "package": package, "day": date.fromisoformat(d["date"]), "downloads": n})
    df = pl.DataFrame(rows, schema=DAILY_DTYPES)
    if df.height != df.unique(subset=["day"]).height:
        raise ValueError(f"duplicate days for {package}")
    return df.sort("day")


def trim_unreported(df: pl.DataFrame, end: date | None, grace: int = TRAILING_GRACE_DAYS) -> pl.DataFrame:
    """Drop trailing zero days when the last non-zero day is within ``grace`` days of ``end``.

    npm reports the newest day as 0 until its daily job has run. A longer run of zeros is kept: the package
    really was idle, and dropping it would fake a gap.
    """
    if df.is_empty():
        return df
    df = df.sort("day")
    end = end or df["day"].max()
    last = df.filter(pl.col("downloads") > 0)["day"].max()
    if last is not None and last >= end - timedelta(days=grace):  # type: ignore[operator]
        return df.filter(pl.col("day") <= last)
    return df


# --- fetch -----------------------------------------------------------------------------------


@dataclass
class FetchResult:
    daily: pl.DataFrame
    checks: list[dict] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fetch_npm(client: JsonClient, packages: list[str], start: date, end: date) -> FetchResult:
    """Every package in chunks; a failed chunk drops its package (never a partial series), not the run."""
    frames: list[pl.DataFrame] = []
    res = FetchResult(daily=pl.DataFrame(schema=DAILY_DTYPES))
    chunks = date_chunks(start, end)
    for pkg in packages:
        parts: list[pl.DataFrame] = []
        raw: dict[str, Any] = {}
        try:
            for a, b in chunks:
                payload = client.get_json(npm_range_url(pkg, a, b))
                parts.append(parse_npm_range(payload, pkg))
                raw[f"npm_{_slug(pkg)}_{a}_{b}"] = payload
        except Exception as exc:  # noqa: BLE001 - isolate every package (rule 5)
            log.error("npm %s failed: %s", pkg, exc)
            res.failed.append(f"npm:{pkg}")
            res.checks.append(check(f"npm fetch: {pkg}", False, f"{type(exc).__name__}: {exc}"[:300]))
            continue
        df = pl.concat(parts).unique(subset=["day"], keep="last").sort("day")
        df = trim_unreported(df, end)  # only the series end can lag; chunk ends are real days
        frames.append(df)
        res.raw.update(raw)
        log.info("npm %s: %d days", pkg, df.height)
    if frames:
        res.daily = pl.concat(frames)
    res.checks.insert(0, check("npm packages", not res.failed,
                               f"{len(packages) - len(res.failed)} of {len(packages)} packages fetched"
                               + (f"; failed: {', '.join(res.failed)}" if res.failed else ""),
                               warn=len(res.failed) < len(packages)))
    return res


def fetch_pypi(client: JsonClient, packages: list[str]) -> FetchResult:
    frames: list[pl.DataFrame] = []
    res = FetchResult(daily=pl.DataFrame(schema=DAILY_DTYPES))
    for pkg in packages:
        try:
            payload = client.get_json(pypi_url(pkg), params={"mirrors": "false"})
            df = parse_pypi_overall(payload, pkg)
        except Exception as exc:  # noqa: BLE001 - isolate every package (rule 5)
            log.error("pypi %s failed: %s", pkg, exc)
            res.failed.append(f"pypi:{pkg}")
            res.checks.append(check(f"PyPI fetch: {pkg}", False, f"{type(exc).__name__}: {exc}"[:300]))
            continue
        frames.append(df)
        res.raw[f"pypi_{_slug(pkg)}"] = payload
        log.info("pypi %s: %d days", pkg, df.height)
    if frames:
        res.daily = pl.concat(frames)
    res.checks.insert(0, check("PyPI packages", not res.failed,
                               f"{len(packages) - len(res.failed)} of {len(packages)} packages fetched"
                               + (f"; failed: {', '.join(res.failed)}" if res.failed else ""),
                               warn=len(res.failed) < len(packages)))
    return res


# --- aggregate -------------------------------------------------------------------------------


def as_of(daily: pl.DataFrame) -> date | None:
    """Earliest last-reported day across packages: every series is complete up to this day."""
    if daily.is_empty():
        return None
    return daily.group_by("package").agg(pl.col("day").max())["day"].min()  # type: ignore[return-value]


def _label(daily: pl.DataFrame, mapping: dict[str, str], side: str, by_company: bool) -> pl.DataFrame:
    df = daily.filter(pl.col("package").is_in(list(mapping)))
    df = df.with_columns(pl.col("package").replace_strict(mapping).alias("company"), pl.lit(side).alias("side"))
    return df.with_columns((pl.col("company") if by_company else pl.col("package")).alias("series"))


def aggregate(daily: pl.DataFrame, mapping: dict[str, str], side: str, *, by_company: bool,
              freq: str, cutoff: date | None = None, expected: dict[str, list[str]] | None = None) -> pl.DataFrame:
    """Daily rows -> complete weekly (``freq='1w'``) or monthly (``'1mo'``) totals per series.

    ``by_company`` sums packages per company; a company row is kept only when every package in
    ``expected[company]`` has data in the period, so a failed package cannot show up as a drop.
    """
    cutoff = cutoff or as_of(daily)
    out_schema = {k: WEEKLY_DTYPES[k] for k in WEEKLY_DTYPES}
    if cutoff is None or daily.is_empty():
        return pl.DataFrame(schema=out_schema)
    df = _label(daily, mapping, side, by_company).filter(pl.col("day") <= cutoff)
    if df.is_empty():
        return pl.DataFrame(schema=out_schema)
    df = df.with_columns(pl.col("day").dt.truncate(freq).alias("period"))
    agg = df.group_by(["registry", "side", "series", "company", "period"]).agg(
        pl.col("downloads").sum().cast(pl.Int64),
        pl.col("package").unique().sort().str.join(",").alias("packages"),
        pl.col("package").n_unique().alias("_npk"),
    )
    if freq == "1w":
        period_end = pl.col("period") + pl.duration(days=6)
        need = pl.lit(7)
    else:
        period_end = pl.col("period").dt.month_end()
        need = pl.col("period").dt.month_end().dt.day().cast(pl.Int64)
    agg = agg.filter(period_end <= cutoff)
    # Every package must cover the whole period (a series starting or pausing mid-period is partial).
    per_pkg = df.group_by(["series", "package", "period"]).agg(pl.col("day").n_unique().alias("n"))
    per_pkg = per_pkg.with_columns(need.alias("need"))
    partial = per_pkg.filter(pl.col("n") < pl.col("need")).select("series", "period").unique()
    agg = agg.join(partial, on=["series", "period"], how="anti")
    if by_company and expected:
        need = pl.DataFrame({"series": list(expected), "_need": [len(v) for v in expected.values()]})
        agg = agg.join(need, on="series", how="left").filter(pl.col("_npk") == pl.col("_need")).drop("_need")
    return agg.select(list(out_schema)).cast(out_schema).sort(PERIOD_KEY)  # type: ignore[arg-type]


def add_index(monthly: pl.DataFrame, base: date | None = None) -> pl.DataFrame:
    """Index each series against ``base`` (=100), or against its first month when ``base`` is None."""
    if monthly.is_empty():
        return monthly.with_columns(pl.lit(None, pl.Date).alias("base_month"),
                                    pl.lit(None, pl.Float64).alias("index"))
    if base is None:
        bases = monthly.group_by("series").agg(pl.col("period").min().alias("base_month"))
    else:
        bases = monthly.select("series").unique().with_columns(pl.lit(base).alias("base_month"))
    base_vals = monthly.join(bases, on="series").filter(pl.col("period") == pl.col("base_month")).select(
        "series", pl.col("downloads").alias("_base"))
    out = monthly.join(bases, on="series", how="left").join(base_vals, on="series", how="left")
    out = out.with_columns(
        pl.when(pl.col("_base") > 0).then(pl.col("downloads") / pl.col("_base") * 100.0)
        .otherwise(None).cast(pl.Float64).alias("index")
    )
    return out.select(list(MONTHLY_DTYPES)).cast(MONTHLY_DTYPES).sort(PERIOD_KEY)  # type: ignore[arg-type]


@dataclass
class Tables:
    client_weekly: pl.DataFrame
    client_monthly: pl.DataFrame
    server_weekly: pl.DataFrame
    server_monthly: pl.DataFrame


def build_tables(npm_daily: pl.DataFrame, pypi_daily: pl.DataFrame) -> Tables:
    """Client (company sums, npm) and server (per package, npm + PyPI) weekly and monthly tables."""
    client_daily = npm_daily.filter(pl.col("package").is_in(list(CLIENT_PACKAGES)))
    server_npm = npm_daily.filter(pl.col("package").is_in(NPM_SERVER_SDKS))
    cw = aggregate(client_daily, CLIENT_PACKAGES, "client", by_company=True, freq="1w", expected=NPM_CLIENT_SDKS)
    cm = aggregate(client_daily, CLIENT_PACKAGES, "client", by_company=True, freq="1mo", expected=NPM_CLIENT_SDKS)
    sw = pl.concat([aggregate(server_npm, NPM_SERVER_COMPANY, "server", by_company=False, freq="1w"),
                    aggregate(pypi_daily, PYPI_PACKAGES, "server", by_company=False, freq="1w")])
    sm_npm = add_index(aggregate(server_npm, NPM_SERVER_COMPANY, "server", by_company=False, freq="1mo"), BASE_MONTH)
    sm_pypi = add_index(aggregate(pypi_daily, PYPI_PACKAGES, "server", by_company=False, freq="1mo"), None)
    return Tables(
        client_weekly=WEEKLY_SCHEMA.validate(cw),
        client_monthly=MONTHLY_SCHEMA.validate(add_index(cm, BASE_MONTH)),
        server_weekly=WEEKLY_SCHEMA.validate(sw.sort(PERIOD_KEY)),
        server_monthly=MONTHLY_SCHEMA.validate(pl.concat([sm_npm, sm_pypi]).sort(PERIOD_KEY)),
    )


# --- Q1 --------------------------------------------------------------------------------------


def q1_series(client_monthly: pl.DataFrame, volume_share_upper: float | pl.DataFrame | None,
              first_graded: date = Q1_FIRST_GRADED_MONTH) -> pl.DataFrame:
    """Monthly Q1 readings with the registered consecutive-refresh rule.

    A month enters only when all five companies have a complete total (the share needs the whole pool).
    ``volume_share_upper`` is a fraction, or a frame (month, volume_share_upper) when it changes over time.
    Status per month: ``falsified`` once ``Q1_CONSECUTIVE`` graded months in a row sit below ``Q1_REFUTED``
    (sticky); else ``holds`` when the latest graded ratio is >= ``Q1_HOLDS``; ``undecidable`` while the
    volume bound is unknown; else ``collecting`` (including every pre-registration month).
    """
    companies = list(NPM_CLIENT_SDKS)
    m = client_monthly.filter(pl.col("side") == "client")
    full = m.group_by("period").agg(pl.col("series").n_unique().alias("n")).filter(pl.col("n") == len(companies))
    m = m.join(full.select("period"), on="period")
    if m.is_empty():
        return pl.DataFrame(schema=Q1_DTYPES)
    pool = m.group_by("period").agg(
        pl.col("downloads").filter(pl.col("series") == Q1_COMPANY).sum().alias("stripe_downloads"),
        pl.col("downloads").sum().alias("pool_downloads"),
    ).filter(pl.col("pool_downloads") > 0).rename({"period": "month"}).sort("month")
    if isinstance(volume_share_upper, pl.DataFrame):
        pool = pool.join(volume_share_upper.select("month", pl.col("volume_share_upper").cast(pl.Float64)),
                         on="month", how="left")
    else:
        pool = pool.with_columns(pl.lit(volume_share_upper, pl.Float64).alias("volume_share_upper"))
    pool = pool.with_columns((pl.col("stripe_downloads") / pl.col("pool_downloads")).alias("download_share"))
    pool = pool.with_columns(
        pl.when(pl.col("volume_share_upper") > 0)
        .then(pl.col("download_share") / pl.col("volume_share_upper")).otherwise(None).alias("ratio"),
        (pl.col("month") >= first_graded).alias("graded"),
    )
    rows = []
    run = 0
    falsified = False
    for r in pool.iter_rows(named=True):
        below = bool(r["graded"] and r["ratio"] is not None and r["ratio"] < Q1_REFUTED)
        if below:
            run += 1
        elif r["graded"] and r["ratio"] is not None:
            run = 0  # a graded month at or above the bar breaks the streak; an unknown month leaves it
        if run >= Q1_CONSECUTIVE:
            falsified = True
        if falsified:
            status = "falsified"
        elif not r["graded"]:
            status = "collecting"
        elif r["ratio"] is None:
            status = "undecidable"
        elif r["ratio"] >= Q1_HOLDS:
            status = "holds"
        else:
            status = "collecting"
        rows.append({**r, "below_refute": below, "consecutive_below": run, "status": status})
    out = pl.DataFrame(rows).select(list(Q1_DTYPES)).cast(Q1_DTYPES)  # type: ignore[arg-type]
    return Q1_SCHEMA.validate(out)


def q1_status(series: pl.DataFrame) -> dict:
    """Latest Q1 reading for facts.json: status plus the numbers it rests on."""
    if series.is_empty():
        return {"status": "collecting", "month": None, "download_share": None, "ratio": None,
                "consecutive_below": 0}
    last = series.sort("month").row(-1, named=True)
    return {"status": last["status"], "month": last["month"].isoformat(), "download_share": last["download_share"],
            "volume_share_upper": last["volume_share_upper"], "ratio": last["ratio"],
            "consecutive_below": last["consecutive_below"]}


# --- checks ----------------------------------------------------------------------------------


MAX_AGE_DAYS = 7


def quality_checks(t: Tables, npm_daily: pl.DataFrame, today: date | None = None) -> list[dict]:
    today = today or utc_now().date()
    out = [
        uniqueness(t.client_monthly, PERIOD_KEY, "series-months", name="Downloads key uniqueness"),
        uniqueness(npm_daily, ["package", "day"], "package-days", name="Downloads daily uniqueness"),
    ]
    latest = t.client_monthly["period"].max() if t.client_monthly.height else None
    present = set(t.client_monthly.filter(pl.col("period") == latest)["series"].to_list()) if latest else set()
    missing = sorted(set(NPM_CLIENT_SDKS) - present)
    out.append(check("Client SDK companies", not missing,
                     f"{len(present)} of {len(NPM_CLIENT_SDKS)} companies in the latest complete month"
                     + (f"; missing: {', '.join(missing)}" if missing else "")))
    leaked = sorted(set(npm_daily["package"].unique().to_list()) & (set(NPM_EXCLUDED_WRAPPERS)
                                                                     | set(NOT_COMPANY_PACKAGES["npm"])))
    out.append(check("Excluded packages", not leaked,
                     "no wrapper or not-company package counted" if not leaked else f"counted: {leaked}"))
    d = as_of(npm_daily)
    if d is None:
        out.append(check("Downloads freshness", False, "no npm rows"))
    else:
        age = (today - d).days
        out.append(check("Downloads freshness", age <= MAX_AGE_DAYS,
                         f"npm reported through {d}, {age} days ago (limit {MAX_AGE_DAYS})",
                         warn=age <= 2 * MAX_AGE_DAYS))
    return out


# --- write and run ---------------------------------------------------------------------------


def write(t: Tables, q1: pl.DataFrame, out_dir: Path | None = None) -> list[Path]:
    """Validate, then write the five devstats marts (a schema failure raises before any write)."""
    out_dir = out_dir or MARTS_DIR / "payments"
    frames = {
        "devstats_client_weekly": WEEKLY_SCHEMA.validate(t.client_weekly),
        "devstats_client_monthly": MONTHLY_SCHEMA.validate(t.client_monthly),
        "devstats_server_weekly": WEEKLY_SCHEMA.validate(t.server_weekly),
        "devstats_server_monthly": MONTHLY_SCHEMA.validate(t.server_monthly),
        "devstats_q1": Q1_SCHEMA.validate(q1),
    }
    return [write_parquet(df, out_dir / f"{name}.parquet") for name, df in frames.items()]


def archive_raw(raw: dict[str, Any], root: Path | None = None) -> Path:
    day, hhmm = run_stamp(utc_now())
    base = (root or RAW_DIR) / "payments" / "devstats" / day / hhmm
    for slug, payload in raw.items():
        write_json_gz(payload, base / f"{slug}.json.gz")
    return base


def run(npm_client: JsonClient | None = None, pypi_client: JsonClient | None = None, *,
        volume_share_upper: float | pl.DataFrame | None = None, end: date | None = None,
        out_dir: Path | None = None, raw_root: Path | None = None,
        write_outputs: bool = True) -> tuple[Tables, pl.DataFrame, list[dict]]:
    end = end or utc_now().date() - timedelta(days=1)
    own_npm, own_pypi = npm_client is None, pypi_client is None
    npm_client = npm_client or HttpClient(min_interval=0.3, max_retries=3)
    pypi_client = pypi_client or HttpClient(min_interval=3.0, max_retries=3)
    try:
        npm = fetch_npm(npm_client, [*CLIENT_PACKAGES, *NPM_SERVER_SDKS], NPM_START, end)
        pypi = fetch_pypi(pypi_client, list(PYPI_PACKAGES))
    finally:
        for own, c in ((own_npm, npm_client), (own_pypi, pypi_client)):
            if own:
                c.close()  # type: ignore[union-attr]
    tables = build_tables(npm.daily, pypi.daily)
    q1 = q1_series(tables.client_monthly, volume_share_upper)
    checks = npm.checks + pypi.checks + quality_checks(tables, npm.daily)
    if write_outputs:
        if npm.raw or pypi.raw:
            archive_raw({**npm.raw, **pypi.raw}, raw_root)
        if tables.client_monthly.height:
            write(tables, q1, out_dir)
        else:
            log.error("no client-SDK months; nothing written")
    return tables, q1, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and validate, write nothing")
    ap.add_argument("--volume-share-upper", type=float, default=None,
                    help="upper bound of Stripe's disclosed-volume share (fraction); normally from share.py")
    args = ap.parse_args(argv)
    setup_logging()
    _, q1, checks = run(volume_share_upper=args.volume_share_upper, write_outputs=not args.dry_run)
    for c in checks:
        log.info("[%s] %s: %s", c["status"], c["name"], c["detail"])
    log.info("Q1: %s", q1_status(q1))
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
