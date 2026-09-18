"""EIA-861M retail sales and the EIA Short-Term Energy Outlook: the demand denominator.

Every number on the supply side of this project — megawatts planned, retirements deferred, a zone's
peak — only means something against how much electricity the country actually sells. That is what
this module fetches:

``sales``  EIA-861M, ``Monthly-States``: revenue, sales, customers and price per state, per month,
           per sector, back to 2010. Commercial sales per state are the single clearest fingerprint
           of data-center growth in public data (Virginia commercial: 58.7 TWh in 2021, 83.6 in
           2025), so the per-state grain is the point and must survive. The national aggregate is
           synthesised here by summing the states and stored in the same table as ``state = "US"``,
           so a chart can divide by the denominator without a second source.
``steo``   EIA's Short-Term Energy Outlook, sheet ``7atab``: the same quantity, forecast two years
           out, monthly plus an annual roll-up. This is EIA's own view of where demand is going, and
           the honest counterweight to a stack of press releases.

Two properties are worth stating because the code is shaped around them.

*The archive is the input.* Each workbook is written to ``data/raw/power/<source>/<date>/`` byte for
byte before anything parses it, and the parse reads that file. A rerun on the same date re-reads the
archived copy instead of re-downloading, so a run is reproducible and a parse bug can be debugged
against exactly the bytes that produced it.

*The two sources are isolated.* 861M failing must not cost us STEO. Each is wrapped, logged, and
whatever succeeded is written; the process exits 1 at the end so CI shows red without losing the
half that worked. A partial run merges into an existing same-date snapshot rather than replacing it.

The national sum is cross-checked against the workbook's own ``US-YTD`` sheet. That is a real audit
rather than a formality: the value columns are four-wide blocks under a merged sector heading, so a
column-alignment bug produces plausible-looking numbers in the wrong sector, and comparing a total
we computed against a total EIA published is the cheapest way to catch it.

Usage:
    uv run python -m pipelines.power.demand --source all
    uv run python -m pipelines.power.demand --source sales
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import re
import sys
import time
from datetime import date as date_cls
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import (
    RAW_DIR,
    SNAP_DIR,
    append_jsonl,
    run_stamp,
    utc_now,
    write_json,
    write_parquet,
)
from pipelines.power.config import EIA_861M_SALES, EIA_STEO, MONTHS, REFERENCE_STALE_DAYS
from pipelines.power.schema import (
    SALES_KEY,
    SALES_SCHEMA,
    STEO_KEY,
    STEO_SCHEMA,
    sales_frame,
    steo_frame,
)

log = logging.getLogger("power.demand")

# --- sources ---------------------------------------------------------------------------------

SALES_SHEET = "Monthly-States"
SALES_AUDIT_SHEET = "US-YTD"  # EIA's own national monthly totals, used only to check ours
STEO_SHEET = "7atab"

#: A workbook is an xlsx, which is a zip. EIA answers a request for a file it has not published with
#: 200 and an HTML index page, so the magic number is the only reliable "did we get the file".
XLSX_MAGIC = b"PK\x03\x04"
WORKBOOK_UA = "portfolio-pipelines/0.1 power.demand (+https://github.com/srx7703/srx7703.github.io)"

# --- 861M taxonomy ---------------------------------------------------------------------------

SECTOR_BY_GROUP = {
    "RESIDENTIAL": "residential",
    "COMMERCIAL": "commercial",
    "INDUSTRIAL": "industrial",
    "TRANSPORTATION": "transportation",
    "TOTAL": "total",
}
SECTORS = tuple(SECTOR_BY_GROUP.values())

MEASURE_BY_NAME = {
    "revenue": "revenue_kusd",
    "sales": "sales_mwh",
    "customers": "customers",
    "price": "price_cents_kwh",
}

#: The units row is read rather than assumed. EIA restating sales in gigawatthours would otherwise
#: land in the table as a silent factor-of-1000 error.
UNIT_EXPECTED = {
    "revenue_kusd": "thousand dollars",
    "sales_mwh": "megawatthours",
    "customers": "count",
    "price_cents_kwh": "cents/kwh",
}

STATE_RE = re.compile(r"[A-Z]{2}")

# --- STEO taxonomy ---------------------------------------------------------------------------

#: The electricity-consumption block of sheet 7atab, in sheet order. Rows are found by the series id
#: in column 0 — the ids are EIA's own and stable across editions — and only fall back to the label,
#: because the row numbers move: the block sat at rows 17-23 in the September 2026 edition.
CONSUMPTION_SERIES = (
    ("ELCOTWH", "Total consumption"),
    ("ELTCTWH", "Sales to ultimate customers"),
    ("ELRCP_US", "Residential sector"),
    ("ELCCP_US", "Commercial sector"),
    ("ELICP_US", "Industrial sector"),
    ("ELACP_US", "Transportation sector"),
    ("ELDUTWH", "Direct use"),
)
#: The label fallback is scoped to this block. "Commercial sector" also names a generation row and a
#: price row on the same sheet, and matching the price row would put cents/kWh in a TWh column.
CONSUMPTION_BLOCK = "electricity consumption"
STEO_UNIT = "billion kWh"

MONTH_BY_ABBR = {name[:3]: i + 1 for i, name in enumerate(MONTHS)}

TITLE_RE = re.compile(r"short-term energy outlook\s*[-–—]\s*([a-z]+)\s+((?:19|20)\d{2})", re.I)
FORECAST_DATE_RE = re.compile(r"([a-z]+)\s+\d{1,2},\s*((?:19|20)\d{2})", re.I)
FOOTNOTE_RE = re.compile(r"\s*\([a-z]\)\s*$", re.I)

#: The 861M cross-check is a comparison of two sums of the same numbers, so it should agree to float
#: noise; the observed worst deviation over 198 months is 1e-8. The threshold is loose enough not to
#: fire on a rounding change and tight enough that a single misplaced column trips it.
CROSS_CHECK_TOLERANCE = 5e-4


# --- small parsers ----------------------------------------------------------------------------


def _norm(x: Any) -> str:
    """Cell to a trimmed single-spaced string; None and NaN become ''."""
    if x is None:
        return ""
    if isinstance(x, float) and math.isnan(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def _f(x: Any) -> float | None:
    """Cell to a float, or None for anything EIA uses to mean "no value"."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int | float):
        v = float(x)
        return v if math.isfinite(v) else None
    s = str(x).strip().replace(",", "")
    if s in ("", ".", "-", "--", "NA", "NM", "W", "*"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _int(x: Any) -> int | None:
    """Cell to an int. fastexcel types a column as string or float depending on what else is in it,
    so a year arrives as "2026" on one sheet and 2026.0 on another; both must work."""
    v = _f(x)
    return int(v) if v is not None and float(v).is_integer() else None


def _clean_label(x: Any) -> str:
    """Row label without its footnote marker: 'Direct use (d)' -> 'Direct use'."""
    return FOOTNOTE_RE.sub("", _norm(x))


def read_sheet(path: Path, sheet: str) -> pl.DataFrame:
    """Read a sheet with no header row at all.

    Both workbooks stack two or three header rows above the data, so no single ``header_row`` yields
    usable names — they are assembled from those rows instead. Reading raw has a second benefit:
    with text in every column fastexcel types them all as strings, so values are parsed explicitly
    here and a cell that stopped being a number becomes None rather than silently becoming 0.0.
    """
    return pl.read_excel(path, sheet_name=sheet, read_options={"header_row": None})


def fetch_workbook(url: str, dest: Path, *, max_retries: int = 4, timeout: float = 120.0) -> Path:
    """Download a workbook and archive it at ``dest``, bytes unchanged, before anyone parses it.

    An existing same-date archive is reused rather than re-fetched: reruns then parse exactly the
    bytes the first run saw. ``HttpClient`` is JSON-only, hence the direct httpx use.
    """
    if dest.exists() and dest.stat().st_size > 0:
        log.info("reusing archived workbook %s (%s bytes)", dest, f"{dest.stat().st_size:,}")
        return dest
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": WORKBOOK_UA}) as client:
                r = client.get(url)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code} for {url}", request=r.request, response=r)
            r.raise_for_status()
            body = r.content
            if not body.startswith(XLSX_MAGIC):
                raise ValueError(f"{url} returned {len(body):,} bytes that are not an xlsx (an error page?)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            log.info("archived %s -> %s (%s bytes)", url, dest, f"{len(body):,}")
            return dest
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            if attempt == max_retries:
                raise
            sleep = min(30.0, (2**attempt) + random.uniform(0, 0.5))
            log.warning("GET %s failed (%s); retry %d/%d in %.1fs", url, exc, attempt + 1, max_retries, sleep)
            time.sleep(sleep)
    raise RuntimeError(f"unreachable; last error: {last!r}")


# --- EIA-861M ---------------------------------------------------------------------------------


def _sales_header(raw_df: pl.DataFrame) -> tuple[int, dict[str, int], dict[str, dict[str, int]]]:
    """Locate the stacked header and map each value column to (sector, measure).

    Three rows carry the header between them. The first names the sector group once per four-column
    block (RESIDENTIAL / COMMERCIAL / INDUSTRIAL / TRANSPORTATION / TOTAL) and leaves the other
    three cells of the block empty, because in the workbook it is a merged cell. The second names
    the measure (Revenue / Sales / Customers / Price). The third names the leading key columns
    (Year, Month, State, Data Status) and gives the units of every value column.

    Returns the index of that third row, the key columns by lowercased name, and sector -> measure
    -> column index. Both ``Monthly-States`` and ``US-YTD`` are shaped this way; the latter has no
    State column, which is why the caller may supply one.
    """
    head: int | None = None
    for i in range(min(10, raw_df.height)):
        cells = {_norm(c).lower() for c in raw_df.row(i)[:4]}
        if "year" in cells and "data status" in cells:
            head = i
            break
    if head is None or head < 2:
        raise ValueError("no 'Year ... Data Status' header row with two rows of sector headings above it")

    keys = {}
    for i, cell in enumerate(raw_df.row(head)):
        name = _norm(cell).lower()
        if name in ("year", "month", "state", "data status") and name not in keys:
            keys[name] = i
    if "year" not in keys or "month" not in keys:
        raise ValueError(f"header row {head} is missing Year or Month: {keys}")

    group_row, measure_row, unit_row = raw_df.row(head - 2), raw_df.row(head - 1), raw_df.row(head)
    sectors: dict[str, dict[str, int]] = {}
    group = ""
    for i in range(raw_df.width):
        if g := _norm(group_row[i]).upper():
            group = g  # merged heading: carry it across the block's remaining columns
        sector = SECTOR_BY_GROUP.get(group)
        measure = MEASURE_BY_NAME.get(_norm(measure_row[i]).lower())
        if sector is None or measure is None:
            continue
        unit = _norm(unit_row[i]).lower()
        if unit != UNIT_EXPECTED[measure]:
            raise ValueError(f"{sector}/{measure} in column {i} is in {unit!r}, expected {UNIT_EXPECTED[measure]!r}")
        sectors.setdefault(sector, {})[measure] = i
    missing = set(SECTORS) - set(sectors)
    if missing:
        raise ValueError(f"sector columns not found: {sorted(missing)}")
    return head, keys, sectors


def parse_sales(raw_df: pl.DataFrame, snapshot_ts: str, *, state: str | None = None) -> list[dict]:
    """861M sheet -> one row per (period, state, sector), all five sectors including ``total``.

    ``state`` overrides the State column for a sheet that is already aggregated (``US-YTD``), which
    is how the national cross-check reuses this parser instead of writing a second one.

    Rows that are not data are dropped by requiring a four-digit year and a month in 1-12: that
    removes the footnote paragraph EIA appends to every sheet, and ``US-YTD``'s annual rows, which
    carry "." as the month.
    """
    head, keys, sectors = _sales_header(raw_df)
    if state is None and "state" not in keys:
        raise ValueError(f"sheet has no State column; pass state= (sectors found: {sorted(sectors)})")

    rows: list[dict] = []
    for r in raw_df.slice(head + 1).iter_rows():
        year, month = _int(r[keys["year"]]), _int(r[keys["month"]])
        if year is None or not 1900 <= year <= 2100 or month is None or not 1 <= month <= 12:
            continue
        code = state if state is not None else _norm(r[keys["state"]]).upper()
        if not STATE_RE.fullmatch(code):
            continue
        status = (_norm(r[keys["data status"]]) if "data status" in keys else "") or None
        period = f"{year:04d}-{month:02d}"
        for sector, cols in sectors.items():
            rows.append(
                {
                    "snapshot_ts": snapshot_ts,
                    "period": period,
                    "state": code,
                    "sector": sector,
                    "sales_mwh": _f(r[cols["sales_mwh"]]),
                    "revenue_kusd": _f(r[cols["revenue_kusd"]]),
                    "customers": _f(r[cols["customers"]]),
                    "price_cents_kwh": _f(r[cols["price_cents_kwh"]]),
                    "data_status": status,
                }
            )
    return rows


def national_rows(state_rows: list[dict]) -> list[dict]:
    """Sum the states into a ``state = "US"`` row per (period, sector).

    Price is re-derived rather than averaged: revenue is in thousand dollars and sales in MWh, so
    ``revenue * 100 / sales`` is cents/kWh, and a sales-weighted price is the only correct one.
    Any "US" rows already in the input are ignored, so calling this twice is harmless.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for r in state_rows:
        if r.get("state") == "US":
            continue
        b = buckets.setdefault(
            (r["period"], r["sector"]),
            {
                "snapshot_ts": r.get("snapshot_ts"),
                "sums": dict.fromkeys(MEASURE_BY_NAME.values(), None),
                "status": set(),
            },
        )
        for measure in ("sales_mwh", "revenue_kusd", "customers"):
            v = r.get(measure)
            if v is not None:
                b["sums"][measure] = (b["sums"][measure] or 0.0) + v
        if r.get("data_status"):
            b["status"].add(r["data_status"])

    out: list[dict] = []
    for (period, sector), b in sorted(buckets.items()):
        sales, revenue = b["sums"]["sales_mwh"], b["sums"]["revenue_kusd"]
        price = (revenue * 100.0 / sales) if sales and revenue is not None else None
        status = b["status"].pop() if len(b["status"]) == 1 else ("Mixed" if b["status"] else None)
        out.append(
            {
                "snapshot_ts": b["snapshot_ts"],
                "period": period,
                "state": "US",
                "sector": sector,
                "sales_mwh": sales,
                "revenue_kusd": revenue,
                "customers": b["sums"]["customers"],
                "price_cents_kwh": price,
                "data_status": status,
            }
        )
    return out


def cross_check_national(ours: list[dict], theirs: list[dict]) -> tuple[int, float, str]:
    """Compare our summed national sales against EIA's published ones.

    Returns (months compared, worst relative deviation, the (period, sector) it came from). Only
    periods present on both sides are compared, so a sheet that starts later is not an error.
    """
    mine = {(r["period"], r["sector"]): r["sales_mwh"] for r in ours if r.get("sales_mwh") is not None}
    ref = {(r["period"], r["sector"]): r["sales_mwh"] for r in theirs if r.get("sales_mwh") is not None}
    worst, where, n = 0.0, "", 0
    for key, published in ref.items():
        computed = mine.get(key)
        if computed is None or not published:
            continue
        n += 1
        rel = abs(computed - published) / abs(published)
        if rel > worst:
            worst, where = rel, f"{key[0]} {key[1]}"
    return n, worst, where


def latest_period(rows: list[dict]) -> str | None:
    return max((r["period"] for r in rows if r.get("period")), default=None)


def _period_age_days(period: str, today: date_cls) -> int:
    """Days since the end of ``period``. 861M publishes a month roughly ten weeks after it ends."""
    year, month = int(period[:4]), int(period[5:7])
    after = date_cls(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return (today - after).days


def sales_checks(state_rows: list[dict], us_rows: list[dict], cross: tuple[int, float, str] | None) -> list[dict]:
    checks: list[dict] = []
    period = latest_period(state_rows)
    if period is None:
        return [check("EIA-861M freshness", False, "no monthly rows parsed")]

    age = _period_age_days(period, utc_now().date())
    checks.append(
        check(
            "EIA-861M freshness",
            age <= REFERENCE_STALE_DAYS,
            f"latest month {period}, {age} days past its end (limit {REFERENCE_STALE_DAYS})",
            warn=age <= 2 * REFERENCE_STALE_DAYS,
        )
    )

    seen = {r["sector"] for r in state_rows if r["period"] == period}
    missing = sorted(set(SECTORS) - seen)
    checks.append(
        check("EIA-861M sector coverage", not missing, f"{len(seen)}/{len(SECTORS)} sectors in {period}"
              + (f", missing {missing}" if missing else ""))
    )

    states = {r["state"] for r in state_rows if r["period"] == period}
    checks.append(check("EIA-861M state coverage", len(states) >= 50, f"{len(states)} states reporting in {period}",
                        warn=len(states) >= 45))

    if cross is None:
        checks.append(check("EIA-861M national cross-check", False, f"{SALES_AUDIT_SHEET} sheet could not be read"))
    else:
        n, worst, where = cross
        checks.append(
            check(
                "EIA-861M national cross-check",
                n > 0 and worst <= CROSS_CHECK_TOLERANCE,
                f"{len(us_rows)} synthesised US rows; worst deviation from {SALES_AUDIT_SHEET} {worst:.2e}"
                + (f" at {where}" if where else "")
                + f" over {n} month-sectors (tolerance {CROSS_CHECK_TOLERANCE:.0e})",
            )
        )
    return checks


# --- EIA STEO ---------------------------------------------------------------------------------


def steo_vintage(raw_df: pl.DataFrame) -> str:
    """The STEO edition as "YYYY-MM", read off the sheet rather than assumed from today's date.

    The title line carries it ("Short-Term Energy Outlook - September 2026"). The forecast date
    printed under the title is the fallback; it is usually in the same month but is a different
    fact, so it is only used when the title is gone.
    """
    header = [_norm(c) for i in range(min(8, raw_df.height)) for c in raw_df.row(i)]
    for cell in header:
        if m := TITLE_RE.search(cell):
            return f"{m.group(2)}-{MONTHS.index(m.group(1).lower()) + 1:02d}"
    for cell in header:
        m = FORECAST_DATE_RE.search(cell)
        if m and m.group(1).lower() in MONTHS:
            log.warning("no STEO title line; falling back to the forecast date %r", cell)
            return f"{m.group(2)}-{MONTHS.index(m.group(1).lower()) + 1:02d}"
    raise ValueError("no STEO vintage on the sheet (neither the title line nor a forecast date)")


def _steo_header(raw_df: pl.DataFrame) -> tuple[int, list[str | None], list[int | None]]:
    """Find the month row and read the calendar off the two header rows.

    The year appears only above January of each year — a merged cell again — so it is carried
    forward. The month row is found by content rather than position: the first row holding at least
    twelve bare month abbreviations. Matching the abbreviation exactly matters, because the cell to
    the left of Jan holds the forecast date ("Thursday, September 3, 2026") and a prefix match would
    read a thirteenth month out of it.
    """
    month_row: int | None = None
    for i in range(min(12, raw_df.height)):
        if sum(1 for c in raw_df.row(i) if _norm(c).lower() in MONTH_BY_ABBR) >= 12:
            month_row = i
            break
    if month_row is None or month_row < 1:
        raise ValueError("no month header row on the STEO sheet")

    years: list[str | None] = []
    current: str | None = None
    for cell in raw_df.row(month_row - 1):
        y = _int(cell)
        if y is not None and 1900 <= y <= 2100:
            current = f"{y:04d}"
        years.append(current)
    months = [MONTH_BY_ABBR.get(_norm(c).lower()) for c in raw_df.row(month_row)]
    return month_row, years, months


def _series_row_index(raw_df: pl.DataFrame, head: int) -> dict[str, int]:
    """Series id (column 0) -> row index, first occurrence wins."""
    out: dict[str, int] = {}
    for i in range(head + 1, raw_df.height):
        if sid := _norm(raw_df.row(i)[0]).upper():
            out.setdefault(sid, i)
    return out


def _is_block_heading(row: tuple) -> bool:
    """A block heading has a label, no series id, and — crucially — no numbers.

    "No series id" alone is not enough to tell a heading from a data row: the reason the label
    fallback exists at all is that a series can lose its id, and such a row would then end the very
    block it lives in, hiding itself from the search.
    """
    return not _norm(row[0]) and bool(_norm(row[1])) and all(_f(c) is None for c in row[2:])


def _consumption_block(raw_df: pl.DataFrame, head: int) -> range:
    """The rows between the "Electricity consumption" heading and the next heading."""
    start: int | None = None
    for i in range(head + 1, raw_df.height):
        row = raw_df.row(i)
        if not _is_block_heading(row):
            continue
        if start is None:
            if _norm(row[1]).lower().startswith(CONSUMPTION_BLOCK):
                start = i + 1
        else:
            return range(start, i)
    return range(start, raw_df.height) if start is not None else range(0)


def parse_steo(raw_df: pl.DataFrame, snapshot_ts: str) -> list[dict]:
    """STEO sheet 7atab -> one monthly row per consumption series per month of the horizon.

    A series that is on neither the id nor the label is logged and skipped rather than raised on, so
    one renamed row does not cost us the other six; the coverage check turns the run red.
    """
    vintage = steo_vintage(raw_df)
    head, years, months = _steo_header(raw_df)
    by_id = _series_row_index(raw_df, head)
    block = _consumption_block(raw_df, head)

    rows: list[dict] = []
    for sid, fallback in CONSUMPTION_SERIES:
        idx = by_id.get(sid)
        if idx is None:
            idx = next((i for i in block if _clean_label(raw_df.row(i)[1]).lower() == fallback.lower()), None)
            if idx is None:
                log.warning("STEO series %s (%s) is on neither the id nor the label; skipped", sid, fallback)
                continue
            log.warning("STEO series id %s missing; matched %r on the label at row %d", sid, fallback, idx)
        cells = raw_df.row(idx)
        label = _clean_label(cells[1]) or fallback
        for i, (year, month) in enumerate(zip(years, months, strict=True)):
            if year is None or month is None:
                continue
            rows.append(
                {
                    "snapshot_ts": snapshot_ts,
                    "vintage": vintage,
                    "series_id": sid,
                    "label": label,
                    "period": f"{year}-{month:02d}",
                    "frequency": "monthly",
                    "value": _f(cells[i]),
                    "unit": STEO_UNIT,
                }
            )
    return rows


def annualise(monthly_rows: list[dict]) -> list[dict]:
    """Sum each series' months into an annual row.

    Only a complete year is rolled up. A STEO horizon can end mid-year, and summing eight months
    into something labelled "2027" would read as a collapse in demand rather than a missing quarter.
    """
    buckets: dict[tuple[str, str, str], dict] = {}
    for r in monthly_rows:
        if r.get("frequency") != "monthly" or not r.get("period"):
            continue
        year = r["period"][:4]
        b = buckets.setdefault((r["vintage"], r["series_id"], year), {"row": r, "months": {}})
        b["months"][r["period"][5:7]] = r["value"]

    out: list[dict] = []
    for (vintage, sid, year), b in sorted(buckets.items()):
        values = b["months"]
        present = [v for v in values.values() if v is not None]
        if len(values) != 12 or len(present) != 12:
            log.info("STEO %s %s has %d of 12 months; no annual roll-up", sid, year, len(present))
            continue
        r = b["row"]
        out.append(
            {
                "snapshot_ts": r["snapshot_ts"],
                "vintage": vintage,
                "series_id": sid,
                "label": r["label"],
                "period": year,
                "frequency": "annual",
                "value": sum(present),
                "unit": r["unit"],
            }
        )
    return out


def steo_checks(monthly: list[dict], annual: list[dict], vintage: str | None) -> list[dict]:
    checks: list[dict] = []
    if not vintage:
        return [check("STEO vintage", False, "no vintage parsed from the sheet")]

    year, month = int(vintage[:4]), int(vintage[5:7])
    today = utc_now().date()
    age_months = (today.year - year) * 12 + today.month - month
    checks.append(
        check(
            "STEO vintage",
            0 <= age_months <= 1,
            f"edition {vintage}, {age_months} month(s) behind {today:%Y-%m}",
            warn=0 <= age_months <= 2,
        )
    )

    found = {r["series_id"] for r in monthly}
    missing = [sid for sid, _ in CONSUMPTION_SERIES if sid not in found]
    checks.append(
        check("STEO series coverage", not missing,
              f"{len(found)}/{len(CONSUMPTION_SERIES)} consumption series"
              + (f", missing {missing}" if missing else ""))
    )

    rolled = sorted({r["period"] for r in annual})
    n_years = len({r["period"][:4] for r in monthly})
    checks.append(
        check("STEO annual roll-up", len(rolled) > 0,
              f"{len(monthly)} monthly rows over {n_years} years -> {len(rolled)} complete years "
              f"({rolled[0]}-{rolled[-1]})" if rolled else "no complete year to roll up",
              warn=bool(rolled)),
    )
    return checks


# --- writing ----------------------------------------------------------------------------------


def merge_write(df: pl.DataFrame, path: Path, key: list[str], schema: Any) -> tuple[Path, int, int]:
    """Write ``df`` to ``path``, merging with an existing same-date snapshot instead of replacing it.

    A run of one source must not delete what the other source wrote earlier today, and a rerun of
    the same source must replace its own rows: new rows win on the key, old rows the run did not
    produce are kept. The merged frame is validated, not just the new part, so a stale row in
    yesterday's file cannot survive by being left alone.
    """
    new = df.height
    if path.exists():
        previous = pl.read_parquet(path)
        df = (
            pl.concat([df, previous], how="diagonal_relaxed")
            .select(list(df.columns))
            .cast(dict(zip(df.columns, df.dtypes, strict=True)))
            .unique(subset=key, keep="first", maintain_order=True)
        )
    schema.validate(df)
    write_parquet(df, path)
    return path, new, df.height


# --- orchestration -----------------------------------------------------------------------------


def run_sales(snapshot_ts: str, date: str) -> tuple[dict, list[dict]]:
    raw = fetch_workbook(EIA_861M_SALES, RAW_DIR / "power" / "eia861m" / date / "sales_revenue.xlsx")
    state_rows = parse_sales(read_sheet(raw, SALES_SHEET), snapshot_ts)
    if not state_rows:
        raise ValueError(f"no usable rows on {SALES_SHEET}")
    us_rows = national_rows(state_rows)

    cross: tuple[int, float, str] | None = None
    try:
        published = parse_sales(read_sheet(raw, SALES_AUDIT_SHEET), snapshot_ts, state="US")
        cross = cross_check_national(us_rows, published)
    except Exception:  # noqa: BLE001 - a broken audit sheet fails the check, not the fetch
        log.exception("%s cross-check could not run", SALES_AUDIT_SHEET)
        cross = None

    df = sales_frame(state_rows + us_rows)
    path, new, total = merge_write(df, SNAP_DIR / "power" / "sales" / f"{date}.parquet", SALES_KEY, SALES_SCHEMA)
    checks = sales_checks(state_rows, us_rows, cross)
    stats = {
        "workbook": str(raw),
        "state_rows": len(state_rows),
        "national_rows": len(us_rows),
        "rows_written": new,
        "rows_in_file": total,
        "latest_period": latest_period(state_rows),
        "path": str(path),
    }
    log.info("861M sales: %s", stats)
    return stats, checks


def run_steo(snapshot_ts: str, date: str) -> tuple[dict, list[dict]]:
    raw = fetch_workbook(EIA_STEO, RAW_DIR / "power" / "steo" / date / "STEO_m.xlsx")
    sheet = read_sheet(raw, STEO_SHEET)
    vintage = steo_vintage(sheet)
    monthly = parse_steo(sheet, snapshot_ts)
    if not monthly:
        raise ValueError(f"no consumption series found on {STEO_SHEET}")
    annual = annualise(monthly)

    df = steo_frame(monthly + annual)
    path, new, total = merge_write(df, SNAP_DIR / "power" / "steo" / f"{date}.parquet", STEO_KEY, STEO_SCHEMA)
    checks = steo_checks(monthly, annual, vintage)
    stats = {
        "workbook": str(raw),
        "vintage": vintage,
        "monthly_rows": len(monthly),
        "annual_rows": len(annual),
        "rows_written": new,
        "rows_in_file": total,
        "path": str(path),
    }
    log.info("STEO: %s", stats)
    return stats, checks


def run(source: str = "all") -> dict:
    t0 = time.monotonic()
    ts = utc_now()
    date, _hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    result: dict = {"module": "power.demand", "source": source, "snapshot_ts": snapshot_ts,
                    "errors": [], "checks": []}

    runners = [("sales", run_sales), ("steo", run_steo)]
    for name, fn in runners:
        if source not in (name, "all"):
            continue
        try:
            stats, checks = fn(snapshot_ts, date)
            result[name] = stats
            result["checks"] += checks
        except Exception as exc:  # noqa: BLE001 - one source failing must not lose the other
            log.exception("%s failed", name)
            result["errors"].append(f"{name}: {exc!r}")

    result["failed_checks"] = [c["name"] for c in result["checks"] if c["status"] == "fail"]
    result["seconds"] = round(time.monotonic() - t0, 1)
    write_json(
        {"snapshot_ts": snapshot_ts, "source": source, "checks": result["checks"], "errors": result["errors"]},
        SNAP_DIR / "power" / "checks" / f"demand-{date}.json",
    )
    append_jsonl(result, SNAP_DIR / "power" / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="all", choices=["sales", "steo", "all"])
    args = ap.parse_args(argv)
    setup_logging()
    result = run(args.source)
    for c in result["checks"]:
        log.info("check %-32s %-4s %s", c["name"], c["status"], c["detail"])
    log.info("done in %ss; errors=%s failed_checks=%s", result["seconds"], result["errors"], result["failed_checks"])
    return 1 if result["errors"] or result["failed_checks"] else 0


if __name__ == "__main__":
    sys.exit(main())
