"""A-share analyst consensus from East Money's ProfitForecast page.

    uv run python -m pipelines.valuation.estimates_em

One GET per A-share listing (``estimates == "eastmoney"``, 31 of them) against
``/PC_HSF10/ProfitForecast/PageAjax``, which returns five blocks. Two of them matter:

``ycmx``  per-broker forecast detail — one row per report, with four (year, mark, EPS, net profit)
          slots on it. This is the evidence layer and the richest thing East Money publishes.
``jgyc``  the same grid with PE instead of net profit, plus one aggregate row whose
          ``ORG_NAME_ABBR`` is ``近六月平均`` — East Money's own six-month mean.

We write our OWN consensus rather than East Money's, because ours is reproducible from the broker
rows sitting next to it in the ``brokers`` table and carries a broker count. East Money's mean is
parsed too (``mean_row``) and used two ways: as a cross-check on ours, and as the fallback when a
listing has the aggregate but no broker detail.

Three coverage regimes exist in the pool today and all three must survive (brokers and mean are
independent): 28 codes have broker rows; ``SH600206`` (有研新材) has the mean but no brokers; and
``SH688567`` (孚能科技) and ``SH603200`` (上海洗霸) have neither, because East Money genuinely
publishes no consensus for them. Those two sit in ``EXPECTED_EMPTY``, so a clean run is green and
the coverage check keeps its one job: going red when the other 29 stop arriving.

``vintages`` is a COVERAGE series, not a revision history, and it is the only reading this module
emits. Every broker row is stamped with its publish date, so the mean can be rebuilt as of any past
date — but East Money publishes each broker's report exactly once (0 repeat publishers across the
live pool), so an earlier as-of point differs from today's only in WHICH brokers had published by
then, never in what any broker thinks. A series that runs 2 -> 20 brokers between April and
September is coverage growing, not analysts raising numbers, and every row carries the size of the
cohort behind it in ``n_analysts`` so that is visible in the data rather than only in this
docstring. ``schema.py`` states the same next to the column and ``evaluate.py`` excludes
``eastmoney_rebuilt`` from its revision measure.

There used to be a ``--cohort matched`` mode offering "the other reading": the panel frozen at the
first date that had a consensus, described as a revision series. It was removed because on this
source it cannot be one. With 0 repeat publishers every frozen panel member has exactly one
forecast, so the mean over that panel is the same number at every later as_of by construction — on
the 28 payloads archived for 2026-09-16 it produced 526 rows across 75 series and **all 75 were
perfectly flat**, against 3 of 75 for the coverage reading. It also had no separate ``origin``:
both modes wrote ``eastmoney_rebuilt``, so a matched run on a date that already had a default run
silently merged two different meanings into one table under one label. A real revision series for
the A-share pool has to come from our own weekly captures (``origin="snapshot"``), which is what
``evaluate.py`` is waiting for.

Writes, for run date ``<date>``:

    data/raw/valuation/eastmoney/<date>/<em_code>.json.gz          every payload, before parsing
    data/snapshots/valuation/brokers/<date>-eastmoney.parquet      per-broker detail
    data/snapshots/valuation/estimates/<date>-eastmoney.parquet    our consensus, one row per year
    data/snapshots/valuation/vintages/<date>-eastmoney.parquet     that consensus as of past dates
    data/snapshots/valuation/runs.jsonl                            one line per run, with checks

Raw payloads are append-only evidence (``CLAUDE.md`` rule 2): a second run on the same date never
overwrites the first payload for a code. An identical body is recognised and left alone, a
differing one lands beside it under the run's ``__HHMM``, then ``__HHMM_2``. See
:func:`archive_payload`, which mirrors ``fx.archive_csv``. A ``--limit`` smoke run must not be able
to delete the evidence behind the full run's parquet.

One listing failing must not lose the others, and neither must one bad row. Per-company work is
isolated, and each listing's rows are put through the frame build and pandera *inside* that guard,
so a value polars or pandera rejects costs exactly the listing it came from and is named in the run
record. The pool-level build, merge and validate are guarded too: all three frames are validated
before any of them is written, and if that fails nothing is written, the failure is recorded in
``runs.jsonl`` with the rest of the run, and the process exits non-zero. A run that produced no rows
writes nothing at all rather than laying an empty table over a good one. A partial run (``--only``)
merges with whatever is already on disk for the date, new rows winning on the table key, so it can
only add.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import statistics
import sys
import time
from collections import defaultdict
from datetime import date as date_cls
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
    write_parquet,
)
from pipelines.valuation.config import COMPANIES, THIN_COVERAGE_BELOW, Company
from pipelines.valuation.schema import (
    BROKER_KEY,
    BROKERS_SCHEMA,
    ESTIMATE_KEY,
    ESTIMATES_SCHEMA,
    VINTAGE_KEY,
    VINTAGES_SCHEMA,
    brokers_frame,
    estimates_frame,
    vintages_frame,
)

log = logging.getLogger("valuation.estimates_em")

BASE = "https://emweb.securities.eastmoney.com"
PATH = "/PC_HSF10/ProfitForecast/PageAjax"
SOURCE = "eastmoney"
CURRENCY = "CNY"  # A-share brokers state EPS and net profit in yuan

# The aggregate row East Money mixes into `jgyc` alongside the real brokers.
MEAN_ORG = "近六月平均"

SLOTS = (1, 2, 3, 4)  # YEAR1..YEAR4 forecast columns on each broker row
MARKS = ("A", "E")  # actual | estimate
YEAR_RANGE = (2015, 2035)  # mirrors BROKERS_SCHEMA's in_range: a junk year is dropped, not fatal
MIN_VINTAGE_BROKERS = 2  # one broker is not a consensus, so no as-of point is emitted
MAX_VINTAGE_DATES = 40  # most recent report dates kept per listing
MAX_MEAN_GAP = 0.05  # nearest-year gap against East Money's own mean; pool median is ~1.5%
THROTTLE = 0.25  # seconds between codes

# East Money publishes no consensus at all for these, and has not for as long as we have looked.
# They are the reason the coverage check needs an allowlist: without one it fails on every clean
# run, and a check that is always red cannot report the day East Money stops answering for the pool.
EXPECTED_EMPTY = frozenset({"688567.SS", "603200.SS"})  # 孚能科技, 上海洗霸

# What `rebuild_vintages` measures, recorded on every run record so a reader of runs.jsonl does not
# have to come here to find out. There is one reading and it is coverage; see the module docstring.
VINTAGE_SERIES = "coverage"

# Distinct payloads archived for one code on one date before the run gives up (see `archive_payload`).
MAX_ARCHIVES = 64

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://emweb.securities.eastmoney.com/",
}


# --- coercion ----------------------------------------------------------------------
def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x: Any) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _date(x: Any) -> str | None:
    """East Money stamps dates as "2026-09-09 00:00:00"; the table keeps the date part only.

    Parsed rather than shape-checked. A character test both under- and over-accepts: "2026-09-9
    00:00:00" would yield "2026-09-9 " (ten characters, trailing space) and fail the brokers schema
    at write time, taking the whole run's tables with it, while "9999-99-99" and "2026-02-31" would
    pass the schema and only blow up later, in whatever calls ``date.fromisoformat`` on the column.
    """
    if not isinstance(x, str):
        return None
    try:
        return date_cls.fromisoformat(x[:10]).isoformat()
    except ValueError:
        return None


def _year(x: Any) -> int | None:
    """A forecast year the brokers table will actually accept, or None."""
    y = _i(x)
    return y if y is not None and YEAR_RANGE[0] <= y <= YEAR_RANGE[1] else None


def _mark(x: Any) -> str | None:
    m = (x or "").strip().upper() if isinstance(x, str) else None
    return m if m in MARKS else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


# --- pure parsing ------------------------------------------------------------------
def parse_brokers(ticker: str, ycmx: list[dict] | None) -> list[dict]:
    """Unpivot `ycmx` into one row per (broker report, forecast year).

    Each source row carries up to four year slots. A slot with no year, with a year outside
    ``YEAR_RANGE``, or with a mark that is neither "A" nor "E", is dropped — the ``brokers`` schema
    forbids all three, and a single junk slot must not abort the write for every other listing. A
    null EPS is kept: the broker row is evidence even when the number is missing, and the consensus
    ignores it.
    """
    rows: list[dict] = []
    for r in ycmx or []:
        org = (r.get("ORG_NAME_ABBR") or "").strip()
        if not org or org == MEAN_ORG:
            continue  # the aggregate is not a broker; it is read by `mean_row`
        publish_date = _date(r.get("PUBLISH_DATE"))
        if publish_date is None:
            log.warning("%s: %s report with no usable publish date, skipped", ticker, org)
            continue
        researcher = r.get("RESEARCHER")
        rating = r.get("RATING")
        for i in SLOTS:
            raw_year = r.get(f"YEAR{i}")
            year = _year(raw_year)
            mark = _mark(r.get(f"YEAR_MARK{i}"))
            if year is None and _i(raw_year) is not None:
                log.warning(
                    "%s: %s forecast year %r is outside %d-%d, slot skipped", ticker, org, raw_year, *YEAR_RANGE
                )
            if year is None or mark is None:
                continue  # brokers who forecast only three years leave the last slot empty
            rows.append(
                {
                    "ticker": ticker,
                    "org": org,
                    "researcher": researcher,
                    "publish_date": publish_date,
                    "year": year,
                    "mark": mark,
                    "eps": _f(r.get(f"EPS{i}")),
                    "net_profit": _f(r.get(f"PARENT_NETPROFIT{i}")),
                    "rating": rating,
                }
            )
    return rows


def latest_reports(rows: list[dict]) -> list[dict]:
    """Keep only each broker's most recent report per ticker.

    A broker can publish several times in the six-month window. All of those rows belong in the
    ``brokers`` table — the key includes the publish date — but a consensus that averaged a
    broker's April and September views would count them twice and lag the revision.
    """
    latest: dict[tuple[str, str], str] = {}
    for r in rows:
        key = (r["ticker"], r["org"])
        if key not in latest or r["publish_date"] > latest[key]:
            latest[key] = r["publish_date"]
    return [r for r in rows if latest[(r["ticker"], r["org"])] == r["publish_date"]]


def _agg(group: list[dict]) -> dict:
    """Consensus fields for one (ticker, year) group of latest broker rows.

    Both means are keyed by broker, so a house that published twice on the same date contributes
    once to each. ``n_analysts`` is the count behind ``eps_avg`` alone: a broker can forecast net
    profit and leave EPS null, in which case ``net_profit_avg`` rests on the larger set and the
    table has no second column to say so. Where the two disagree, ``eps_avg`` is the described one.
    """
    eps = {r["org"]: r["eps"] for r in group if r["eps"] is not None}
    profit = {r["org"]: r["net_profit"] for r in group if r["net_profit"] is not None}
    values = list(eps.values())
    newest = max(group, key=lambda r: r["publish_date"])
    return {
        "mark": newest["mark"],
        "eps_avg": _mean(values),
        "eps_low": min(values) if values else None,
        "eps_high": max(values) if values else None,
        # the count behind eps_avg, so a thin or partly-missing year is visible downstream
        "n_analysts": len(eps),
        "net_profit_avg": _mean(list(profit.values())),
    }


def consensus_from_brokers(rows: list[dict]) -> list[dict]:
    """Our own consensus: the mean over each broker's latest report, one row per (ticker, year)."""
    by_key: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in latest_reports(rows):
        by_key[(r["ticker"], r["year"])].append(r)
    out: list[dict] = []
    for (ticker, year), group in sorted(by_key.items()):
        out.append(
            {
                "ticker": ticker,
                "source": SOURCE,
                "period_end": f"{year}-12-31",  # every A-share in the pool has a December year end
                "fy_label": f"FY{year}",
                "currency": CURRENCY,
                **_agg(group),
            }
        )
    return out


def rebuild_vintages(rows: list[dict]) -> list[dict]:
    """Rebuild the published consensus as of each report date — a COVERAGE series.

    For date ``d``, every broker's latest report on or before ``d`` contributes. Only forecast
    years are emitted: a reported actual is the same number at every date, so a series on it would
    be a flat line the table has no ``mark`` column to filter out. Dates are capped to the most
    recent ``MAX_VINTAGE_DATES``, but reports older than the cap still feed the points kept.

    East Money publishes each broker once, so a point differs from today only in who had published
    by then. ``n_analysts`` carries that cohort size on every row, which is what tells a reader the
    mean climbed because brokers 3..20 arrived, not because anyone revised. That is the whole of
    what this source can say; the module docstring records why the frozen-panel reading was removed
    rather than offered as an alternative.
    """
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["mark"] == "E":
            by_ticker[r["ticker"]].append(r)

    out: list[dict] = []
    for ticker, trows in sorted(by_ticker.items()):
        dates = sorted({r["publish_date"] for r in trows})[-MAX_VINTAGE_DATES:]
        points: list[tuple[str, dict[int, dict[str, float]]]] = []
        for as_of in dates:
            known = latest_reports([r for r in trows if r["publish_date"] <= as_of])
            by_year: dict[int, dict[str, float]] = defaultdict(dict)
            for r in known:
                if r["eps"] is not None:
                    by_year[r["year"]][r["org"]] = r["eps"]
            points.append((as_of, by_year))

        for as_of, by_year in points:
            for year, eps in sorted(by_year.items()):
                if len(eps) < MIN_VINTAGE_BROKERS:
                    continue
                out.append(
                    {
                        "ticker": ticker,
                        "source": SOURCE,
                        "period_end": f"{year}-12-31",
                        "as_of": as_of,
                        "eps_avg": _mean(list(eps.values())),
                        # the cohort behind THIS point, not today's coverage (see the docstring)
                        "n_analysts": len(eps),
                        "currency": CURRENCY,
                        "origin": "eastmoney_rebuilt",
                    }
                )
    return out


def mean_row(jgyc: list[dict] | None) -> dict | None:
    """East Money's own 近六月平均 aggregate, for cross-checking our mean and as a fallback.

    Returns ``{"org", "publish_date", "years": [{"year", "mark", "eps", "pe"}, ...]}``, or None
    when the payload has no aggregate row (or none with a usable year).
    """
    for r in jgyc or []:
        if (r.get("ORG_NAME_ABBR") or "").strip() != MEAN_ORG:
            continue
        years: list[dict] = []
        for i in SLOTS:
            year = _year(r.get(f"YEAR{i}"))
            mark = _mark(r.get(f"YEAR_MARK{i}"))
            if year is None or mark is None:
                continue
            years.append({"year": year, "mark": mark, "eps": _f(r.get(f"EPS{i}")), "pe": _f(r.get(f"PE{i}"))})
        return {"org": MEAN_ORG, "publish_date": _date(r.get("PUBLISH_DATE")), "years": years} if years else None
    return None


def consensus_from_mean(ticker: str, mean: dict) -> list[dict]:
    """Estimate rows built from East Money's aggregate, for listings with no broker detail.

    ``n_analysts`` is null rather than 0: the count is unknown, not zero.
    """
    return [
        {
            "ticker": ticker,
            "source": SOURCE,
            "period_end": f"{y['year']}-12-31",
            "fy_label": f"FY{y['year']}",
            "mark": y["mark"],
            "eps_avg": y["eps"],
            "eps_low": None,
            "eps_high": None,
            "n_analysts": None,
            "currency": CURRENCY,
            "net_profit_avg": None,
        }
        for y in mean["years"]
    ]


def mean_deviation(ours: list[dict], mean: dict | None) -> float | None:
    """Relative gap between our consensus and East Money's, on the nearest forecast year.

    The two will not match, and not only because of rounding: East Money's aggregate averages
    reports whose per-broker detail it does not publish in `ycmx`. 光库科技 is the clearest case —
    one broker in the detail at 0.98 for 2026, an aggregate of 1.25 that only reconciles against
    roughly three reports. That gap widens with every year further out, because fewer brokers cover
    them, so taking the worst year measures coverage thinness rather than our parsing. The nearest
    forecast year is the one nearly every broker states, so it is where a parse bug would show:
    across the 28 live payloads the nearest-year gap has median 1.5% and max 21%, against 3.4% and
    58% for the worst year, which is what lets `snapshot` run its tripwire at 5% instead of 10%.
    Actuals are excluded — both sides are quoting the same reported number there.

    Still a pool-wide tripwire for a parsing mistake (see `snapshot`), never an equality assertion
    on one listing.
    """
    if not mean:
        return None
    theirs = {y["year"]: y["eps"] for y in mean["years"] if y["eps"] and y["mark"] == "E"}
    mine = {
        int(r["period_end"][:4]): r["eps_avg"]
        for r in ours
        if r.get("eps_avg") is not None and r.get("mark", "E") == "E"
    }
    both = sorted(set(theirs) & set(mine))
    if not both:
        return None
    year = both[0]
    return abs(mine[year] - theirs[year]) / abs(theirs[year])


def collect(company: Company, payload: dict) -> dict:
    """Turn one payload into the three tables' rows, plus how the consensus was arrived at."""
    brokers = parse_brokers(company.ticker, payload.get("ycmx"))
    mean = mean_row(payload.get("jgyc"))
    if brokers:
        estimates = consensus_from_brokers(brokers)
        return {
            "brokers": brokers,
            "estimates": estimates,
            "vintages": rebuild_vintages(brokers),
            "basis": "brokers",
            "n_brokers": len({r["org"] for r in brokers}),
            "deviation": mean_deviation(estimates, mean),
        }
    if mean:
        return {
            "brokers": [],
            "estimates": consensus_from_mean(company.ticker, mean),
            "vintages": [],
            "basis": "mean",
            "n_brokers": 0,
            "deviation": None,
        }
    return {"brokers": [], "estimates": [], "vintages": [], "basis": "none", "n_brokers": 0, "deviation": None}


# --- network -----------------------------------------------------------------------
def fetch(http: HttpClient, em_code: str) -> dict:
    payload = http.get_json(PATH, {"code": em_code})
    if not isinstance(payload, dict):
        raise ValueError(f"{em_code}: expected a JSON object, got {type(payload).__name__}")
    return payload


def archive_payload(payload: Any, raw_dir: Path, key: str, hhmm: str) -> Path:
    """Archive one payload exactly as served, before anything parses it.

    Raw files are evidence, so an existing archive for the same day is never overwritten: an
    identical body is left alone and a differing one lands beside it under the run's HHMM, then
    ``__HHMM_2``, ``__HHMM_3``... Every candidate is compared, not just the first — a third distinct
    payload in the same minute (a cron run and a manual re-run colliding) must not be dropped on the
    floor while the function returns a path holding somebody else's bytes.

    This module used to write straight to ``<em_code>.json.gz`` with ``write_json_gz``, so a second
    run replaced the first run's payload in place. Measured on 2026-09-16, five runs left zero
    ``__HHMM`` files, two of them ``--limit`` partials that overwrote the evidence behind the full
    run's parquet — the parquet for a run has to be reproducible from the raw layer of that run, and
    ``CLAUDE.md`` rule 2 says raw snapshots are append-only. Same policy and same shape as
    ``fx.archive_csv``; the only difference is gzipped JSON instead of gzipped CSV.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    for n in range(MAX_ARCHIVES):
        if n == 0:
            path = raw_dir / f"{key}.json.gz"
        elif n == 1:
            path = raw_dir / f"{key}__{hhmm}.json.gz"
        else:
            path = raw_dir / f"{key}__{hhmm}_{n}.json.gz"
        if not path.exists():
            with gzip.open(path, "wb") as f:
                f.write(body)
            return path
        try:
            if gzip.decompress(path.read_bytes()) == body:
                return path  # already archived, byte for byte
        except OSError:  # unreadable archive: keep it, write beside it
            continue
    raise RuntimeError(f"too many differing archives for {key} at {hhmm} in {raw_dir}")


def _stamp(rows: list[dict], snapshot_ts: str) -> list[dict]:
    return [{**r, "snapshot_ts": snapshot_ts} for r in rows]


def validate_company(got: dict, snapshot_ts: str) -> None:
    """Prove one listing's rows survive the frame build and pandera, before they join the pool.

    Framing and validating only the combined tables would put this outside the per-company
    try/except: one listing carrying a value polars or pandera rejects would then take down all 29
    A-share listings *and* the ``runs.jsonl`` record that would have explained why. Doing it per
    company keeps a bad payload costing exactly the listing it came from, named in ``errors`` and in
    the Fetches check. Same reasoning as ``estimates_yf.validate_rows`` and the guard in ``fx``.
    """
    for rows, frame, schema in (
        (got["brokers"], brokers_frame, BROKERS_SCHEMA),
        (got["estimates"], estimates_frame, ESTIMATES_SCHEMA),
        (got["vintages"], vintages_frame, VINTAGES_SCHEMA),
    ):
        if rows:
            schema.validate(frame(_stamp(rows, snapshot_ts)))


def _merge_existing(df: pl.DataFrame, path: Path, key: list[str]) -> pl.DataFrame:
    """Keep rows an earlier partial run already wrote for this date.

    An ``--only`` or ``--limit`` run covers part of the pool by design. Writing only that part would
    throw away the listings a full run fetched an hour earlier, so the new rows are merged over
    whatever is already on disk for the same date, new rows winning on the key. Same approach as
    ``fundamentals._merge_existing``; the result can only grow, never shrink.
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


def snapshot(companies: list[Company]) -> dict:
    t0 = time.monotonic()
    ts = utc_now()
    date, hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    raw_dir = RAW_DIR / "valuation" / SOURCE / date
    out_dir = SNAP_DIR / "valuation"

    http = HttpClient(BASE, min_interval=THROTTLE, headers=HEADERS)
    brokers: list[dict] = []
    estimates: list[dict] = []
    vintages: list[dict] = []
    errors: list[str] = []  # fetch, parse or per-listing validation blew up
    no_estimates: list[str] = []  # payload arrived but carried neither brokers nor a mean
    mean_only: list[str] = []
    thin: list[str] = []
    deviations: dict[str, float] = {}

    try:
        for c in companies:
            try:
                payload = fetch(http, c.em_code)
                # raw first: evidence lands even if the parse below is what breaks
                archive_payload(payload, raw_dir, c.em_code, hhmm)
                got = collect(c, payload)
                # inside the guard, so a row polars or pandera rejects costs one listing, not 29
                validate_company(got, snapshot_ts)
            except Exception as exc:  # noqa: BLE001 - one listing must never lose the others
                log.exception("%s (%s) failed", c.ticker, c.em_code)
                errors.append(f"{c.ticker}: {exc!r}")
                continue

            brokers += got["brokers"]
            estimates += got["estimates"]
            vintages += got["vintages"]
            if got["basis"] == "none":
                log.warning("%s (%s): no broker rows and no %s row", c.ticker, c.em_code, MEAN_ORG)
                no_estimates.append(c.ticker)
            elif got["basis"] == "mean":
                log.warning("%s (%s): no broker rows, falling back to the %s row", c.ticker, c.em_code, MEAN_ORG)
                mean_only.append(c.ticker)
            elif got["n_brokers"] < THIN_COVERAGE_BELOW:
                thin.append(f"{c.ticker} ({got['n_brokers']})")
            if got["deviation"] is not None:
                deviations[c.ticker] = got["deviation"]
            log.info(
                "%s (%s): %s brokers, %s estimate rows, %s vintage rows (%s)",
                c.ticker,
                c.em_code,
                got["n_brokers"],
                len(got["estimates"]),
                len(got["vintages"]),
                got["basis"],
            )
    finally:
        http.close()

    # Build, merge and validate all three tables before writing any of them: a schema failure on
    # `estimates` must not leave a `brokers` parquet on disk with no partner file. The whole block
    # is guarded because it used to sit outside every try: a failure here took the run record with
    # it, so the one artefact that could have explained the missing data was never written. Now
    # nothing is written, the reason is named in `errors` and in the Snapshot check, the run is
    # recorded like any other, and main() exits non-zero on it.
    pending: list[tuple[str, Path, pl.DataFrame]] = []
    write_error: str | None = None
    try:
        for table, rows, frame, schema, key in (
            ("brokers", brokers, brokers_frame, BROKERS_SCHEMA, BROKER_KEY),
            ("estimates", estimates, estimates_frame, ESTIMATES_SCHEMA, ESTIMATE_KEY),
            ("vintages", vintages, vintages_frame, VINTAGES_SCHEMA, VINTAGE_KEY),
        ):
            if not rows:
                # nothing to say is not the same as "there is nothing": leave the day's file alone
                log.warning("no %s rows this run; leaving any existing %s file for %s untouched", table, table, date)
                continue
            path = out_dir / table / f"{date}-{SOURCE}.parquet"
            df = frame(_stamp(rows, snapshot_ts))
            schema.validate(df)
            merged = _merge_existing(df, path, key)
            schema.validate(merged)
            pending.append((table, path, merged))
    except Exception as exc:  # noqa: BLE001 - nothing is written, but the run is still recorded
        log.exception("building the snapshot tables failed; nothing written for %s", date)
        write_error = repr(exc)
        pending = []

    written: dict[str, int] = {}
    for table, path, df in pending:
        write_parquet(df, path)
        written[table] = df.height

    # A parse bug would shift the whole pool, so the median decides; the tail is coverage noise.
    typical = statistics.median(deviations.values()) if deviations else None
    worst_ticker = max(deviations, key=lambda k: deviations[k]) if deviations else "-"
    # 688567/603200 have no East Money consensus to find, so they are not news; anything else is.
    unexpected_empty = [t for t in no_estimates if t not in EXPECTED_EMPTY]
    known_empty = [t for t in no_estimates if t in EXPECTED_EMPTY]
    checks = [
        check(
            "East Money coverage",
            not unexpected_empty,
            f"{len(companies) - len(errors) - len(no_estimates)}/{len(companies)} listings with a consensus"
            + (f"; unexpectedly none for {', '.join(unexpected_empty)}" if unexpected_empty else "")
            + (f"; {', '.join(known_empty)} have no East Money consensus by nature" if known_empty else ""),
        ),
        check(
            "Broker detail present",
            not mean_only,
            f"{', '.join(mean_only)} fell back to the {MEAN_ORG} row (no per-broker detail, n_analysts null)"
            if mean_only
            else "every consensus is backed by broker rows",
            warn=True,
        ),
        check(
            "Consensus depth",
            not thin,
            f"under {THIN_COVERAGE_BELOW} brokers: {', '.join(thin)}" if thin else f"all >= {THIN_COVERAGE_BELOW}",
            warn=True,
        ),
        check(
            "Agrees with East Money's own mean",
            typical is None or typical < MAX_MEAN_GAP,
            f"median nearest-year gap {typical:.1%} across {len(deviations)} listings (limit "
            f"{MAX_MEAN_GAP:.0%}), worst {deviations[worst_ticker]:.1%} ({worst_ticker}); the tail "
            "is East Money averaging reports it does not publish per-broker detail for, not a parse error"
            if typical is not None
            else "no aggregate row to compare against",
            warn=True,
        ),
        check("Fetches", not errors, f"{len(errors)} failed: {'; '.join(errors)}" if errors else f"{http.calls} calls"),
        check(
            "Snapshot written",
            write_error is None,
            f"{', '.join(f'{t}: {n}' for t, n in sorted(written.items())) or 'nothing to write'}"
            if write_error is None
            else f"building the tables failed, nothing written, the day's files left as they are: {write_error}",
        ),
    ]

    result = {
        "module": "estimates_em",
        "snapshot_ts": snapshot_ts,
        "listings": len(companies),
        "rows": written,
        "brokers": len({(r["ticker"], r["org"]) for r in brokers}),
        "vintage_series": VINTAGE_SERIES,  # what `vintages` measures; there is one reading
        "mean_only": mean_only,
        "no_estimates": no_estimates,
        "no_estimates_unexpected": unexpected_empty,
        # a listing that failed, plus the table build if that is what failed: both are run failures
        "errors": errors + ([f"write: {write_error}"] if write_error else []),
        "write_error": write_error,
        "http_calls": http.calls,
        "checks": checks,
        "seconds": round(time.monotonic() - t0, 1),
    }
    append_jsonl(result, out_dir / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[], metavar="TICKER", help="restrict to these tickers")
    ap.add_argument("--limit", type=int, default=0, help="stop after N listings (smoke runs)")
    args = ap.parse_args(argv)
    setup_logging()

    companies = [c for c in COMPANIES if c.estimates == SOURCE]
    if args.only:
        companies = [c for c in companies if c.ticker in set(args.only)]
    if args.limit:
        companies = companies[: args.limit]
    if not companies:
        log.error("no listings selected")
        return 1

    res = snapshot(companies)
    log.info("rows=%s brokers=%s seconds=%s", res["rows"], res["brokers"], res["seconds"])
    for c in res["checks"]:
        log.info("check %-38s %-4s %s", c["name"], c["status"], c["detail"])
    # A listing East Money has never covered is not a run failure — otherwise the exit code is 1 on
    # every clean day and says nothing. Everything else is, including a table build that blew up,
    # but only after the data we did get is written and the run is recorded.
    known = [t for t in res["no_estimates"] if t in EXPECTED_EMPTY]
    if known:
        log.info("%s: no East Money consensus, as expected", ", ".join(known))
    failed = res["errors"] + res["no_estimates_unexpected"]
    if failed:
        log.error("%s listing(s) produced no consensus: %s", len(failed), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
