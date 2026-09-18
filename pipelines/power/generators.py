"""EIA-860M: what the grid actually built, and what it decided not to shut down.

Usage:
    uv run python -m pipelines.power.generators
    uv run python -m pipelines.power.generators --vintage 2026-07
    uv run python -m pipelines.power.generators --skip-deferrals

Writes:
    data/snapshots/power/generators/<date>.parquet   one row per unit per vintage (GENERATORS_DTYPES)
    data/snapshots/power/retirements/<date>.parquet  retirements, actual and planned, on one timeline
    data/snapshots/power/deferrals/<date>.parquet    units whose retirement moved later or vanished
    data/snapshots/power/runs.jsonl                  one line per run, with the checks
    data/raw/power/eia860m/<date>/<name>.xlsx        the workbooks exactly as downloaded

This is the physical half of the data-center power question. The contract half ("who signed a PPA
with which reactor") lives in the curated `deals` table; a page that showed only one of them would be
wrong in a predictable direction, so both get built from here and from there.

Three traps this module deliberately does not paper over.

*An unpublished month is not a 404.* EIA serves the newest edition from ``/xls/`` and moves it to
``/archive/xls/`` a couple of months later, and a month it has not published yet answers **HTTP 200
with the section's HTML index page** -- 55,745 bytes of ``text/html`` under an ``.xlsx`` URL. Neither
the status code nor the content type separates that from a real workbook, and the size is not a test
either. The payload is: an xlsx is a zip and starts with ``PK``. Measured on 2026-09-18, august and
september 2026 answer with the index page while july 2026 is a live workbook under ``/xls/`` and july
2025 is a real one under ``/archive/xls/``. So the fetch walks back month by month up to
``EIA_860M_LOOKBACK_MONTHS``, tries both paths for each, and accepts only a zip.

*The same column is typed differently on every sheet, and an empty cell is often a space.*
``Net Summer Capacity (MW)`` reads Float64 on Operating and String on Retired; ``Planned Retirement
Year`` on Operating is a String column whose "nulls" are the literal ``' '`` while ``Retirement
Year`` on Retired is Int64; ids arrive as Int64 on one sheet and String on the next. Every cell
therefore goes through the small coercers below rather than a column cast, and a year that will not
parse stays null. ``fillna(0)`` would file every un-dated unit under the year 0 and the loss would
never surface; a null does surface, in the checks.

*A retirement pulled earlier is not a deferral.* The deferral table diffs two editions a year apart
on (plant id, generator id) and keeps only units whose retirement moved **later** or disappeared.
Seven units in the July 2025 -> July 2026 diff moved the other way; the schema's ``deferred_years >
0`` check would reject them, so they are filtered out and counted instead. A withdrawn date -- the
unit is still on the Operating sheet with no retirement year at all -- is a deferral of unknown
length and lands with a null ``deferred_years``, which is why that column is nullable.

An unmapped technology raises. A technology appearing in the inventory for the first time is the
news, not noise, so the run stops rather than bucketing it as "other". A **blank** technology cell is
different: the July 2026 workbook has two, and they land as family "other" and are counted in a check.

Failures are isolated per source. The year-ago edition failing costs the deferral table and nothing
else; a table that fails its pandera schema is not written while the others still are; either way the
reason lands in ``runs.jsonl`` and the process exits non-zero so CI shows red over real partial data.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import RAW_DIR, SNAP_DIR, append_jsonl, run_stamp, utc_now, write_parquet
from pipelines.power.config import (
    DEFERRAL_COMPARISON_MONTHS,
    EIA_860M_ARCHIVE,
    EIA_860M_CURRENT,
    EIA_860M_LOOKBACK_MONTHS,
    MAX_INVENTORY_AGE_DAYS,
    MONTHS,
    STATUS_STAGE,
    TECHNOLOGY_FAMILY,
)
from pipelines.power.schema import (
    DEFERRALS_SCHEMA,
    GENERATORS_SCHEMA,
    RETIREMENTS_SCHEMA,
    deferrals_frame,
    generators_frame,
    retirements_frame,
)

log = logging.getLogger("power.generators")

SOURCE = "eia860m"
#: EIA puts two title rows above the header on every sheet of every edition.
HEADER_ROW = 2
#: An xlsx is a zip. This is the only reliable test that a 200 carried a workbook and not the index page.
ZIP_MAGIC = b"PK"
UA = "portfolio-pipelines/0.1 (power/eia860m; +https://github.com/srx7703/srx7703.github.io)"

#: (workbook sheet, the `sheet` value it becomes, state to fall back to). The Puerto Rico sheets
#: already carry state "PR" on every populated row; the fallback is belt and braces, and they are
#: optional because an older edition may not ship them. There is no Canceled_PR sheet.
GENERATOR_SHEETS: tuple[tuple[str, str, str | None], ...] = (
    ("Planned", "planned", None),
    ("Operating", "operating", None),
    ("Canceled or Postponed", "canceled", None),
    ("Planned_PR", "planned", "PR"),
    ("Operating_PR", "operating", "PR"),
)

#: (operating sheet -> planned retirements, retired sheet -> actual ones, state fallback).
RETIREMENT_SHEETS: tuple[tuple[str, str, str | None], ...] = (
    ("Operating", "Retired", None),
    ("Operating_PR", "Retired_PR", "PR"),
)

_STATUS_RE = re.compile(r"^\s*\(([A-Za-z]{1,3})\)")
_VINTAGE_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

Fetcher = Callable[[str], bytes | None]


# --- cell coercion ----------------------------------------------------------------------------
def _text(v: object) -> str | None:
    """A trimmed cell, or None. EIA writes an empty cell as a single space on the string columns."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    s = str(v).strip()
    return s or None


def _ident(v: object) -> str | None:
    """A plant or generator id as a string.

    The same column is Int64 on one sheet and String on the next, so 70424 and "70424" have to land
    on the same key -- and a float 70424.0 must not become "70424.0" or the year-ago diff misses it.
    """
    if isinstance(v, float):
        if v != v:
            return None
        if v.is_integer():
            v = int(v)
    return _text(v)


def _float(v: object) -> float | None:
    """A capacity cell. Float64 on some sheets, the string "16" or "1.5" on others."""
    s = _text(v)
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _int(v: object) -> int | None:
    """A year or month cell, or None when the source has no date.

    Never fall back to 0: an un-dated unit filed under the year 0 is a parse failure that looks like
    data. The null survives into the parquet and is what the required-field checks count.
    """
    f = _float(v)
    return None if f is None else int(f)


# --- pure parsing -----------------------------------------------------------------------------
def status_code(s: object) -> str | None:
    """The bracketed code out of an EIA status sentence.

    ``"(TS) Construction complete, but not yet in commercial operation"`` -> ``"TS"``. Ten codes
    appear in the July 2026 edition: P, L, T, U, V, TS, OP, SB, OA, OS. A value that does not open
    with a bracketed code -- a blank, or a sentence EIA reformats one day -- returns None rather than
    a guess, and the row still lands with a null stage.
    """
    t = _text(s)
    if t is None:
        return None
    m = _STATUS_RE.match(t)
    return m.group(1).upper() if m else None


def family_of(technology: object) -> str:
    """The chart family for an EIA technology string.

    A blank cell is "other" -- there are two in the July 2026 Operating sheet and they are counted in
    a check. An *unmapped* technology raises: a new technology entering the inventory is the finding,
    and silently bucketing it as "other" would bury it.
    """
    t = _text(technology)
    if t is None:
        return "other"
    try:
        return TECHNOLOGY_FAMILY[t]
    except KeyError:
        raise ValueError(
            f"unmapped EIA technology {t!r}: add it to pipelines.power.config.TECHNOLOGY_FAMILY. "
            "A technology appearing for the first time is news, so this stops the run."
        ) from None


def parse_sheet(
    df: pl.DataFrame,
    sheet: str,
    vintage: str,
    snapshot_ts: str,
    *,
    default_state: str | None = None,
) -> list[dict]:
    """One generator sheet -> GENERATORS_DTYPES rows.

    `sheet` is the contract value ("planned", "operating" or "canceled"), not the workbook tab name,
    because the Puerto Rico tabs feed the same two buckets. The operation date comes from the planned
    columns on a Planned sheet and the operating columns on an Operating one; the Canceled sheet
    carries neither a date nor a Status column at all, so both stay null there.

    Rows with no plant or generator id are dropped: the Puerto Rico tabs are padded with blank rows.
    """
    rows: list[dict] = []
    for r in df.iter_rows(named=True):
        plant_id = _ident(r.get("Plant ID"))
        generator_id = _ident(r.get("Generator ID"))
        if plant_id is None or generator_id is None:
            continue
        if sheet == "planned":
            year, month = _int(r.get("Planned Operation Year")), _int(r.get("Planned Operation Month"))
        elif sheet == "operating":
            year, month = _int(r.get("Operating Year")), _int(r.get("Operating Month"))
        else:
            year, month = None, None
        code = status_code(r.get("Status"))
        technology = _text(r.get("Technology"))
        rows.append(
            {
                "snapshot_ts": snapshot_ts,
                "vintage": vintage,
                "sheet": sheet,
                "plant_id": plant_id,
                "generator_id": generator_id,
                "plant_name": _text(r.get("Plant Name")),
                "entity_name": _text(r.get("Entity Name")),
                "state": _text(r.get("Plant State")) or default_state,
                "county": _text(r.get("County")),
                "balancing_authority": _text(r.get("Balancing Authority Code")),
                "sector": _text(r.get("Sector")),
                # the schema forbids a null technology, and a blank cell is not the same fact as a
                # missing row: it is kept as "" and counted, while `family_of` files it under "other"
                "technology": technology or "",
                "family": family_of(technology),
                "energy_source": _text(r.get("Energy Source Code")),
                "prime_mover": _text(r.get("Prime Mover Code")),
                "nameplate_mw": _float(r.get("Nameplate Capacity (MW)")),
                "net_summer_mw": _float(r.get("Net Summer Capacity (MW)")),
                "status_code": code,
                "stage": STATUS_STAGE.get(code) if code else None,
                "operation_year": year,
                "operation_month": month,
            }
        )
    return rows


def _retirement_row(r: dict, kind: str, year: int | None, month: int | None, vintage: str, snapshot_ts: str,
                    default_state: str | None) -> dict:
    technology = _text(r.get("Technology"))
    return {
        "snapshot_ts": snapshot_ts,
        "vintage": vintage,
        "kind": kind,
        "plant_id": _ident(r.get("Plant ID")),
        "generator_id": _ident(r.get("Generator ID")),
        "plant_name": _text(r.get("Plant Name")),
        "state": _text(r.get("Plant State")) or default_state,
        "technology": technology or "",
        "family": family_of(technology),
        "nameplate_mw": _float(r.get("Nameplate Capacity (MW)")),
        "retirement_year": year,
        "retirement_month": month,
    }


def retirement_rows(
    operating_df: pl.DataFrame | None,
    retired_df: pl.DataFrame | None,
    vintage: str,
    snapshot_ts: str,
    *,
    default_state: str | None = None,
) -> list[dict]:
    """Both kinds of retirement on one timeline.

    ``planned`` rows are the Operating units that carry a planned retirement year -- 536 of 28,318 in
    the July 2026 edition -- and ``actual`` rows are the whole Retired sheet. An Operating row whose
    planned retirement year is blank is not a retirement and is skipped; a Retired row with no year
    is a retirement with an unknown date and is kept with a null year, because dropping it would
    quietly shrink the retired fleet.
    """
    rows: list[dict] = []
    for r in operating_df.iter_rows(named=True) if operating_df is not None else ():
        if _ident(r.get("Plant ID")) is None or _ident(r.get("Generator ID")) is None:
            continue
        year = _int(r.get("Planned Retirement Year"))
        if year is None:
            continue
        month = _int(r.get("Planned Retirement Month"))
        rows.append(_retirement_row(r, "planned", year, month, vintage, snapshot_ts, default_state))
    for r in retired_df.iter_rows(named=True) if retired_df is not None else ():
        if _ident(r.get("Plant ID")) is None or _ident(r.get("Generator ID")) is None:
            continue
        year, month = _int(r.get("Retirement Year")), _int(r.get("Retirement Month"))
        rows.append(_retirement_row(r, "actual", year, month, vintage, snapshot_ts, default_state))
    return rows


def _by_unit(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(plant, generator) -> its retirement row, an actual retirement beating a planned one.

    A unit is on exactly one sheet per edition so the collision does not arise in practice, but if it
    ever does, what happened outranks what was planned.
    """
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["plant_id"], r["generator_id"])
        cur = out.get(key)
        if cur is None or (cur.get("kind") == "planned" and r.get("kind") == "actual"):
            out[key] = r
    return out


def deferrals(
    old_rows: list[dict],
    new_rows: list[dict],
    from_vintage: str,
    to_vintage: str,
    snapshot_ts: str,
) -> tuple[list[dict], int]:
    """Two editions differenced: the units whose retirement moved later, or stopped being scheduled.

    Takes `retirement_rows` output from each edition. A unit qualifies when the older edition gave it
    a **planned** retirement year and the newer edition either moved that year later or no longer
    carries a retirement date for it at all. Three outcomes are not deferrals and do not appear:

    * the same year -- nothing moved;
    * an **earlier** year -- the retirement was pulled forward. ``DEFERRALS_SCHEMA`` requires
      ``deferred_years > 0`` and would reject the row, so it is filtered here and returned as the
      second element of the tuple rather than dropped in silence;
    * a unit that already had an actual retirement in the older edition -- it was gone before the
      window opened.

    A withdrawal keeps a null ``year_after`` and a null ``deferred_years``: the plant is still on the
    Operating sheet with no date, which is a deferral of unknown length, not a deferral of zero. A
    handful of those (4 of 35 in the July 2025 -> July 2026 diff) are units that left the workbook
    entirely; `run` counts them separately against the new inventory rather than guessing here.

    Attributes come from the newer edition where it still has the unit, so a renamed plant reads as
    it does today.
    """
    newest = _by_unit(new_rows)
    rows: list[dict] = []
    accelerated = 0
    for old in old_rows:
        if old.get("kind") != "planned":
            continue
        year_before = old.get("retirement_year")
        if year_before is None:
            continue
        key = (old["plant_id"], old["generator_id"])
        new = newest.get(key)
        year_after = new.get("retirement_year") if new else None
        if year_after is not None:
            if year_after < year_before:
                accelerated += 1
                continue
            if year_after == year_before:
                continue
        src = new or old
        rows.append(
            {
                "snapshot_ts": snapshot_ts,
                "plant_id": key[0],
                "generator_id": key[1],
                "plant_name": src.get("plant_name") or old.get("plant_name"),
                "state": src.get("state") or old.get("state"),
                "technology": src.get("technology") or old.get("technology"),
                "family": src.get("family") or old.get("family"),
                "nameplate_mw": src.get("nameplate_mw") if src.get("nameplate_mw") is not None
                else old.get("nameplate_mw"),
                "from_vintage": from_vintage,
                "to_vintage": to_vintage,
                "year_before": year_before,
                "year_after": year_after,
                "deferred_years": None if year_after is None else float(year_after - year_before),
            }
        )
    return rows, accelerated


# --- vintages ---------------------------------------------------------------------------------
def shift_vintage(vintage: str, months: int) -> str:
    """"2026-07" shifted by whole months, e.g. -12 -> "2025-07"."""
    year, month = (int(x) for x in vintage.split("-"))
    i = year * 12 + (month - 1) + months
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def vintage_candidates(as_of: date, lookback: int = EIA_860M_LOOKBACK_MONTHS) -> list[str]:
    """The editions to try, newest first, starting at the month `as_of` falls in."""
    start = f"{as_of.year:04d}-{as_of.month:02d}"
    return [shift_vintage(start, -i) for i in range(lookback)]


def vintage_data_end(vintage: str) -> date:
    """The last day the edition covers -- the end of its own month, not its publication date."""
    nxt = shift_vintage(vintage, 1)
    year, month = (int(x) for x in nxt.split("-"))
    return date(year, month, 1) - timedelta(days=1)


def edition_urls(vintage: str) -> tuple[str, str]:
    """The current and archive URLs for one edition, in the order they should be tried."""
    year, month = (int(x) for x in vintage.split("-"))
    name = MONTHS[month - 1]
    return (
        EIA_860M_CURRENT.format(month=name, year=year),
        EIA_860M_ARCHIVE.format(month=name, year=year),
    )


# --- fetching ---------------------------------------------------------------------------------
def fetch_workbook(url: str, *, timeout: float = 180.0, max_retries: int = 3) -> bytes | None:
    """GET one workbook URL; return the body only if it really is an xlsx, else None.

    `HttpClient` is JSON-only, so this is the module's own binary fetch: same retry shape (exponential
    backoff on transport errors, 429 and 5xx), same descriptive User-Agent. A 404 or any other 4xx is
    "not here", not a failure to retry. A 200 whose body is not a zip is EIA's HTML index page for a
    month it has not published, and is also "not here" -- see the module docstring.
    """
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        except httpx.TransportError as exc:
            last = exc
        else:
            if r.status_code < 400:
                body = r.content
                if body[:2] != ZIP_MAGIC:
                    log.info(
                        "%s is not a workbook (%d bytes, %s): EIA has not published it yet",
                        url, len(body), r.headers.get("content-type"),
                    )
                    return None
                return body
            if r.status_code != 429 and r.status_code < 500:
                log.info("%s -> HTTP %d", url, r.status_code)
                return None
            last = httpx.HTTPStatusError(f"HTTP {r.status_code} for {url}", request=r.request, response=r)
        if attempt == max_retries:
            raise last
        sleep = min(30.0, 2**attempt + random.uniform(0, 0.5))
        log.warning("GET %s failed (%s); retry %d/%d in %.1fs", url, last, attempt + 1, max_retries, sleep)
        time.sleep(sleep)
    raise RuntimeError(f"unreachable; last error: {last!r}")


def fetch_edition(vintage: str, client: Fetcher = fetch_workbook) -> tuple[str, bytes]:
    """One edition's workbook, current path first then archive. Raises if neither answers with a zip."""
    for url in edition_urls(vintage):
        body = client(url)
        if body:
            return url, body
    raise FileNotFoundError(f"EIA-860M {vintage} is published under neither /xls/ nor /archive/xls/")


def resolve_vintage(as_of: date, client: Fetcher = fetch_workbook) -> tuple[str, str, bytes]:
    """The newest 860M edition actually published at `as_of`, as (vintage, url, bytes).

    `client` is the byte fetcher, injected by the tests; the shared `HttpClient` cannot stand in
    because it only returns JSON. Walks back from the month of `as_of` for up to
    `EIA_860M_LOOKBACK_MONTHS`, trying the current and then the archive path for each.
    """
    tried: list[str] = []
    for vintage in vintage_candidates(as_of):
        try:
            url, body = fetch_edition(vintage, client)
        except FileNotFoundError:
            tried.append(vintage)
            continue
        return vintage, url, body
    raise FileNotFoundError(
        f"no EIA-860M edition published in the last {EIA_860M_LOOKBACK_MONTHS} months (tried {', '.join(tried)})"
    )


def archive_workbook(body: bytes, raw_dir: Path, name: str, hhmm: str) -> Path:
    """Archive one workbook, never overwriting an earlier archive for the same day.

    Raw files are evidence: the snapshot committed this morning was parsed from this exact file, so a
    re-run must not replace it. Identical bytes are left alone; a body that differs (EIA re-issued the
    edition) lands beside it under the run's HHMM, the policy the valuation raw archives already use.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    if path.exists():
        if path.read_bytes() == body:
            return path
        path = raw_dir / f"{Path(name).stem}__{hhmm}{Path(name).suffix}"
        if path.exists():
            return path
    path.write_bytes(body)
    return path


def read_sheet(path: Path, sheet: str, *, required: bool = True) -> pl.DataFrame | None:
    """One sheet of an archived workbook.

    fastexcel logs "Could not determine dtype for column N, falling back to string" for the mixed
    columns (Unit Code, and the year columns on some editions). That is the honest answer for those
    columns and the coercers above expect it, so it is left alone rather than silenced.
    """
    try:
        return pl.read_excel(path, sheet_name=sheet, read_options={"header_row": HEADER_ROW})
    except Exception:
        if required:
            raise
        log.info("optional sheet %s is absent from %s", sheet, path.name)
        return None


def read_generator_rows(path: Path, vintage: str, snapshot_ts: str) -> list[dict]:
    rows: list[dict] = []
    for tab, sheet, state in GENERATOR_SHEETS:
        df = read_sheet(path, tab, required=state is None)
        if df is None:
            continue
        got = parse_sheet(df, sheet, vintage, snapshot_ts, default_state=state)
        log.info("%s %s: %d rows -> %d", vintage, tab, df.height, len(got))
        rows += got
    return rows


def read_retirement_rows(path: Path, vintage: str, snapshot_ts: str) -> list[dict]:
    rows: list[dict] = []
    for op_tab, ret_tab, state in RETIREMENT_SHEETS:
        op = read_sheet(path, op_tab, required=state is None)
        ret = read_sheet(path, ret_tab, required=state is None)
        if op is None and ret is None:
            continue
        rows += retirement_rows(op, ret, vintage, snapshot_ts, default_state=state)
    return rows


# --- checks -----------------------------------------------------------------------------------
def coal_retirement_gw(rows: list[dict], kind: str = "planned") -> dict[int, float]:
    """Nameplate GW of coal retiring per year -- the smoke test for the whole parse.

    The July 2026 edition should read ~4.1 GW in 2026, ~7.1 in 2027 and ~12.9 in 2028. A coercion bug
    in the year or capacity columns moves these numbers visibly, which a row count would not.
    """
    out: dict[int, float] = {}
    for r in rows:
        if r.get("kind") != kind or r.get("family") != "coal":
            continue
        year = r.get("retirement_year")
        if year is None:
            continue
        out[year] = out.get(year, 0.0) + (r.get("nameplate_mw") or 0.0)
    return {y: round(mw / 1000, 2) for y, mw in sorted(out.items())}


def run_checks(
    *,
    vintage: str | None,
    as_of: date,
    gen_rows: list[dict],
    ret_rows: list[dict],
    def_rows: list[dict],
    accelerated: int,
    untraceable: int,
    written: dict[str, int],
    errors: list[str],
) -> list[dict]:
    blank_tech = sum(1 for r in gen_rows if not r["technology"])
    no_stage = sum(1 for r in gen_rows if r["sheet"] != "canceled" and r["status_code"] is None)
    no_capacity = sum(1 for r in gen_rows if r["nameplate_mw"] is None)
    op_year_rows = sum(1 for r in gen_rows if r["sheet"] != "canceled")
    no_op_year = sum(1 for r in gen_rows if r["sheet"] != "canceled" and r["operation_year"] is None)
    keys = {(r["plant_id"], r["generator_id"]) for r in gen_rows}
    codes = sorted({r["status_code"] for r in gen_rows if r["status_code"]})
    withdrawn = sum(1 for r in def_rows if r["year_after"] is None)
    age = (as_of - vintage_data_end(vintage)).days if vintage else None
    by_sheet = {s: sum(1 for r in gen_rows if r["sheet"] == s) for s in ("planned", "operating", "canceled")}
    # a run that parsed nothing verified nothing: the inventory checks below must not read as passes
    parsed = bool(gen_rows)
    nothing = "no inventory parsed this run"
    return [
        check(
            "Inventory freshness",
            age is not None and age <= MAX_INVENTORY_AGE_DAYS,
            f"860M {vintage} covers through {vintage_data_end(vintage)}, {age} days old "
            f"(limit {MAX_INVENTORY_AGE_DAYS})" if age is not None else "no edition resolved",
            warn=age is not None and age <= 2 * MAX_INVENTORY_AGE_DAYS,
        ),
        check(
            "Sheet coverage",
            all(by_sheet.values()),
            ", ".join(f"{s}: {n:,}" for s, n in by_sheet.items()) + f"; {len(ret_rows):,} retirement rows",
        ),
        check(
            "Unit key uniqueness",
            parsed and len(keys) == len(gen_rows),
            f"{len(gen_rows) - len(keys)} duplicate (plant_id, generator_id) across the generator sheets"
            if parsed
            else nothing,
        ),
        check(
            "Technology mapped",
            parsed and blank_tech == 0,
            (
                f"{blank_tech} units with a blank technology cell, filed as family 'other'"
                if blank_tech
                else f"all {len(gen_rows):,} units mapped to a family"
            )
            if parsed
            else nothing,
            warn=parsed,
        ),
        check(
            "Status codes",
            parsed and no_stage == 0,
            f"codes seen: {', '.join(codes)}"
            + (f"; {no_stage} planned/operating rows with an unparsable status" if no_stage else "")
            if parsed
            else nothing,
        ),
        check(
            "Required numbers parsed",
            # Three outcomes, not two. A handful of missing operating years is normal and warns; a
            # missing capacity, or *every* operating year missing, is a parse failure and fails.
            # Losing the whole column empties the additions chart as completely as losing capacity
            # would, and the previous condition — which asked only about capacity — called it a pass.
            # `warn` is only consulted when ok is False, so it must not repeat the ok predicate.
            parsed and no_capacity == 0 and no_op_year == 0,
            f"nameplate_mw: {no_capacity} null; operation_year: {no_op_year} of {op_year_rows} null "
            "on planned/operating rows"
            if parsed
            else nothing,
            warn=parsed and no_capacity == 0 and not (op_year_rows and no_op_year == op_year_rows),
        ),
        check(
            "Deferrals",
            not def_rows or untraceable == 0,
            f"{len(def_rows)} units moved later or withdrawn ({withdrawn} withdrawn, of which "
            f"{untraceable} no longer in the inventory at all); {accelerated} pulled earlier and "
            "excluded, as the schema requires deferred_years > 0"
            if def_rows
            else "not computed this run",
            warn=True,
        ),
        check(
            "Snapshots written",
            not errors,
            ", ".join(f"{t}: {n:,}" for t, n in sorted(written.items())) or "nothing written",
        ),
    ]


# --- run --------------------------------------------------------------------------------------
def run(*, vintage: str | None = None, skip_deferrals: bool = False, as_of: date | None = None) -> dict:
    t0 = time.monotonic()
    ts = utc_now()
    day, hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    today = as_of or ts.date()
    raw_dir = RAW_DIR / "power" / SOURCE / day
    out_dir = SNAP_DIR / "power"
    errors: list[str] = []

    resolved: str | None = None
    gen_rows: list[dict] = []
    ret_rows: list[dict] = []
    try:
        if vintage:
            url, body = fetch_edition(vintage)
            resolved = vintage
        else:
            resolved, url, body = resolve_vintage(today)
        path = archive_workbook(body, raw_dir, url.rsplit("/", 1)[-1], hhmm)
        log.info("860M %s: %s -> %s (%.1f MB)", resolved, url, path, len(body) / 1e6)
        gen_rows = read_generator_rows(path, resolved, snapshot_ts)
        ret_rows = read_retirement_rows(path, resolved, snapshot_ts)
    except Exception as exc:  # the edition is the only source here; the tables below still try
        log.exception("EIA-860M current edition failed")
        errors.append(f"current: {exc!r}")

    from_vintage: str | None = None
    def_rows: list[dict] = []
    accelerated = 0
    untraceable = 0
    if ret_rows and not skip_deferrals and resolved:
        try:
            from_vintage = shift_vintage(resolved, -DEFERRAL_COMPARISON_MONTHS)
            old_url, old_body = fetch_edition(from_vintage)
            old_path = archive_workbook(old_body, raw_dir, old_url.rsplit("/", 1)[-1], hhmm)
            log.info("860M %s (comparison): %s -> %s", from_vintage, old_url, old_path)
            old_rows = read_retirement_rows(old_path, from_vintage, snapshot_ts)
            def_rows, accelerated = deferrals(old_rows, ret_rows, from_vintage, resolved, snapshot_ts)
            # a withdrawal is meant to be "still listed, no date"; these units left the file entirely
            live = {(r["plant_id"], r["generator_id"]) for r in gen_rows}
            untraceable = sum(
                1 for r in def_rows if r["year_after"] is None and (r["plant_id"], r["generator_id"]) not in live
            )
        except Exception as exc:  # isolated: the deferral table is the only thing this can cost
            log.exception("EIA-860M comparison edition %s failed", from_vintage)
            errors.append(f"deferrals: {exc!r}")

    written: dict[str, int] = {}
    tables = (
        ("generators", gen_rows, generators_frame, GENERATORS_SCHEMA),
        ("retirements", ret_rows, retirements_frame, RETIREMENTS_SCHEMA),
        ("deferrals", def_rows, deferrals_frame, DEFERRALS_SCHEMA),
    )
    for table, rows, builder, schema in tables:
        if not rows:
            continue
        try:
            df = builder(rows)
            schema.validate(df)  # before the write, and it raises: a bad table is not a warning
            write_parquet(df, out_dir / table / f"{day}.parquet")
            written[table] = df.height
        except Exception as exc:  # one bad table must not cost the others their snapshot
            log.exception("%s table failed", table)
            errors.append(f"{table}: {exc!r}")

    coal = coal_retirement_gw(ret_rows)
    result = {
        "module": "generators",
        "snapshot_ts": snapshot_ts,
        "vintage": resolved,
        "from_vintage": from_vintage,
        "rows": written,
        "accelerated_excluded": accelerated,
        "withdrawn_untraceable": untraceable,
        # the smoke test, on the record: a coercion bug moves these numbers where a row count would not
        "planned_coal_retirement_gw": {str(y): gw for y, gw in list(coal.items())[:6]},
        "errors": errors,
        "checks": run_checks(
            vintage=resolved,
            as_of=today,
            gen_rows=gen_rows,
            ret_rows=ret_rows,
            def_rows=def_rows,
            accelerated=accelerated,
            untraceable=untraceable,
            written=written,
            errors=errors,
        ),
        "seconds": round(time.monotonic() - t0, 1),
    }
    append_jsonl(result, out_dir / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vintage", metavar="YYYY-MM", help="pin an 860M edition instead of resolving the newest")
    ap.add_argument("--skip-deferrals", action="store_true", help="skip the year-ago edition and its diff")
    args = ap.parse_args(argv)
    setup_logging()
    if args.vintage and not _VINTAGE_RE.match(args.vintage):
        log.error("--vintage must look like 2026-07, got %r", args.vintage)
        return 1
    res = run(vintage=args.vintage, skip_deferrals=args.skip_deferrals)
    log.info(
        "done %s: rows=%s deferrals=%s (accelerated excluded %s) coal_gw=%s seconds=%s errors=%s",
        res["vintage"],
        res["rows"],
        res["rows"].get("deferrals", 0),
        res["accelerated_excluded"],
        res["planned_coal_retirement_gw"],
        res["seconds"],
        res["errors"],
    )
    for c in res["checks"]:
        log.info("check %-26s %-4s %s", c["name"], c["status"], c["detail"])
    # A failed check is a failed run. Gating only on `errors` lets a silent parse failure —
    # a renamed EIA column that leaves every capacity null but raises nothing — write a full
    # snapshot of zeroes and exit green. Matches gridops, demand, reference and publish.
    failed = [c["name"] for c in res["checks"] if c["status"] == "fail"]
    if failed:
        log.error("failed checks: %s", ", ".join(failed))
    return 1 if (res["errors"] or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
