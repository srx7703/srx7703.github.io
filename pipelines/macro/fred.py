"""FRED daily market series and first-print jobless claims, keyless.

Daily series come from ``fredgraph.csv?id=<series>``. That endpoint ignores ``vintage_date`` and always
serves the latest revision, which is fine for prices and the policy target (never revised) and wrong for
anything that is: claims have been revised by thousands a week after they were first printed, and a
chart drawn from the revised series shows what the market could not have known on the day.

Claims therefore come from ALFRED's graph CSV, which does honour vintages and accepts several at once
(one column per vintage, ``IC4WSA_20260910``). The vintage dates themselves are the options of ALFRED's
download form. A week's first print is its value in the earliest vintage that contains it, and it is
dated by that vintage — the day it was published — not by the week it describes.
"""

from __future__ import annotations

import csv
import io
import logging
import re

import polars as pl

from pipelines.common.http import HttpClient
from pipelines.valuation.fx import FredClient, parse_fred_csv

log = logging.getLogger("macro.fred")

ALFRED = "https://alfred.stlouisfed.org"
#: ALFRED returns at most 12 vintage columns per request and silently drops the rest, which would make
#: a later vintage look like a week's first print. `parse_vintages` checks the columns it got.
VINTAGES_PER_CALL = 12

DAILY_COLS: dict[str, pl.DataType] = {"series": pl.Utf8, "date": pl.Utf8, "value": pl.Float64}
CLAIMS_COLS: dict[str, pl.DataType] = {
    "series": pl.Utf8,
    "week_end": pl.Utf8,
    "release_date": pl.Utf8,
    "value": pl.Float64,
}


class AlfredClient(FredClient):
    """FredClient's retrying text GET, pointed at ALFRED."""

    def __init__(self, *, min_interval: float = 1.0) -> None:
        HttpClient.__init__(self, ALFRED, min_interval=min_interval, headers={"Accept": "text/csv, text/html, */*"})


def daily(client: FredClient, series_ids: list[str], since: str) -> tuple[pl.DataFrame, list[str]]:
    """Long table (series, date, value) from `since`, plus the ids that failed. One failure does not
    lose the others."""
    rows: list[dict] = []
    failed: list[str] = []
    for sid in series_ids:
        try:
            obs = parse_fred_csv(client.series_csv(sid), sid)
        except Exception as exc:  # noqa: BLE001
            log.warning("FRED %s failed: %s", sid, exc)
            failed.append(sid)
            continue
        rows += [{"series": sid, "date": d, "value": v} for d, v in obs if d >= since]
    return pl.DataFrame(rows, schema=DAILY_COLS), failed


def vintage_dates(html: str, since: str) -> list[str]:
    """Vintage dates listed as options on ALFRED's download form."""
    return sorted({d for d in re.findall(r'<option[^>]*value="(\d{4}-\d{2}-\d{2})"', html) if d >= since})


def parse_vintages(text: str, series_id: str, requested: list[str]) -> list[tuple[str, str, float]]:
    """(observation date, vintage date, value) for every non-missing cell of a multi-vintage CSV.
    Raises if the columns are not exactly the vintages requested."""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    header = rows[0]
    vint = []
    for col in header[1:]:
        m = re.fullmatch(rf"{re.escape(series_id)}_(\d{{4}})(\d{{2}})(\d{{2}})", col.strip())
        if not m:
            raise ValueError(f"unexpected ALFRED column {col!r} for {series_id}")
        vint.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    if vint != requested:
        raise ValueError(f"ALFRED returned vintages {vint} for {series_id}, requested {requested}")
    out = []
    for r in rows[1:]:
        for v, cell in zip(vint, r[1:], strict=False):
            cell = cell.strip()
            if cell and cell != ".":
                out.append((r[0].strip(), v, float(cell)))
    return out


def first_prints(cells: list[tuple[str, str, float]], first_vintage: str) -> list[dict]:
    """Each observation's value in the earliest vintage that has it. Observations already present in
    the first vintage fetched are dropped: their first print came before the window."""
    seen: dict[str, tuple[str, float]] = {}
    for obs, vint, val in sorted(cells, key=lambda c: (c[0], c[1])):
        if obs not in seen:
            seen[obs] = (vint, val)
    return [
        {"week_end": obs, "release_date": vint, "value": val}
        for obs, (vint, val) in sorted(seen.items())
        if vint > first_vintage
    ]


def claims_first_print(client: AlfredClient, series_id: str, since: str) -> pl.DataFrame:
    html = client.get_text("/series/downloaddata", {"seid": series_id})
    vints = vintage_dates(html, since)
    if len(vints) < 2:
        raise ValueError(f"ALFRED lists {len(vints)} vintages of {series_id} since {since}")
    cells: list[tuple[str, str, float]] = []
    for i in range(0, len(vints), VINTAGES_PER_CALL):
        chunk = vints[i : i + VINTAGES_PER_CALL]
        text = client.get_text(
            "/graph/alfredgraph.csv", {"id": ",".join([series_id] * len(chunk)), "vintage_date": ",".join(chunk)}
        )
        cells += parse_vintages(text, series_id, chunk)
    rows = first_prints(cells, vints[0])
    return pl.DataFrame([{"series": series_id, **r} for r in rows], schema=CLAIMS_COLS)
