"""HTTP Archive Tech Report: how many websites carry each payment technology, month by month.

This is the "web coverage" lens of the share chapter (PLAN §4c, EXECUTION W2d) and the input to Q2
("the top of the web is changing hands": Stripe vs PayPal on US top-10k origins, mobile, 3-month
rolling mean). It is always called *coverage*, never share of payments: the unit is an origin whose
home page (or one interior page) loads a script or markup that Wappalyzer recognises, not a dollar.

Source
------
The Tech Report's public reports API, the same one https://httparchive.org/reports/techreport reads.
It is keyless and served from Cloud CDN::

    GET https://cdn.httparchive.org/v1/adoption?technology=Stripe&geo=ALL&rank=ALL&start=2020-01-01

    [{"technology": "Stripe", "date": "2023-06-01", "adoption": {"mobile": 19, "desktop": 11}}, ...]

``technology=ALL`` returns the total number of origins in the crawl for that geo and rank, which is the
denominator for ``share``. Geo values are CrUX country names ("United States of America"); rank values
are CrUX rank magnitudes ("Top 10k", "Top 100k", "ALL"). Both lists are served by ``/v1/geos`` and
``/v1/ranks``. The API is not a formally versioned public product (PLAN §9 risks), so the parser is
strict about shape and every slice fails on its own.

What the numbers can and cannot see
-----------------------------------
Detection is client-side fingerprinting. That has three blind spots the page must state, so they are
encoded next to the technology list rather than in prose somewhere else:

* **Adyen and Square are barely detected.** Adyen's web drop-in is mostly used inside enterprise
  checkouts behind logins or on payment-page subdomains the crawl never reaches; Square sellers mostly
  sit on Square Online / Weebly storefronts. Both show double- or triple-digit origin counts against
  Stripe's tens of thousands, which says nothing about their volume.
* **Shopify-hosted processing is invisible.** Shopify Payments is Stripe underneath and runs on
  Shopify's checkout domain; "Shop Pay" detects the wallet button, not who processes the card.
* **Wallets are buttons, not processors.** Apple Pay / Google Pay / Amazon Pay detections overlap with
  every processor above.

Hence the second output, the detection-coverage table: one row per technology with its first month,
months present and a ``detectable`` flag, so the page shows the blind spots instead of hiding them.

Failure isolation (CLAUDE.md rule 5)
------------------------------------
One request per (technology, geo, rank) slice. A slice that errors or returns a malformed payload is
logged, recorded as a failed check and skipped; every other slice still lands. A failed denominator
slice leaves ``total_origins``/``share`` null for that geo and rank rather than dropping the counts.

Usage (runs in GitHub Actions; this container has no outbound network)::

    uv run python -m pipelines.payments.webtech
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Protocol

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, RAW_DIR, run_stamp, utc_now, write_json_gz, write_parquet

log = logging.getLogger("payments.webtech")

# --- source ----------------------------------------------------------------------------------

BASE_URL = "https://cdn.httparchive.org/v1"
START = "2020-01-01"

#: Technology value that returns every origin in the crawl for a geo/rank: the share denominator.
TOTAL_TECHNOLOGY = "ALL"

#: Output label -> API value. Output keeps the API's own strings so a row can be traced to a request.
GEOS: dict[str, str] = {"ALL": "ALL", "United States of America": "United States of America"}
RANKS: list[str] = ["Top 10k", "Top 100k", "ALL"]
CLIENTS: list[str] = ["mobile", "desktop"]

#: Payment technologies exactly as Wappalyzer (HTTP Archive's fork) names them, category 41 "Payment
#: processors" or 91 "Buy now pay later". Note: HTTP Archive barely detects Adyen or Square, and
#: Shopify-hosted processing is invisible (Shop Pay is the wallet button, not the processor).
TECHNOLOGIES: dict[str, str] = {
    "Stripe": "",
    "PayPal": "Includes PayPal Checkout buttons; Venmo and Pay Later are not separate here.",
    "Braintree": "PayPal-owned gateway; detected separately from PayPal.",
    "Adyen": "Barely detected: enterprise checkouts sit behind logins or on payment subdomains.",
    "Square": "Barely detected: most sellers use Square Online storefronts, not a Square script.",
    "Shop Pay": "Wallet button only. Shopify Payments processing (Stripe underneath) is invisible.",
    "Klarna Checkout": "Wappalyzer's name for Klarna's on-site widgets and checkout.",
    "Afterpay": "",
    "Affirm": "",
    "Checkout.com": "",
    "Amazon Pay": "Wallet button; overlaps with the processors above.",
    "Apple Pay": "Wallet button; overlaps with the processors above.",
    "Google Pay": "Wallet button; overlaps with the processors above.",
}

#: A technology counts as detectable when its latest all-origins mobile count reaches this many
#: origins. PLAN §4c reads Adyen at 69 and Square at 51 global origins; anything in that range is a
#: rounding error against a crawl of ~15M origins and must not be charted as a trend.
MIN_DETECTABLE_ORIGINS = 1000

#: Month-over-month change in the crawl's total origins above this is flagged (crawl-size jumps
#: move every count at once; PLAN §8 asks for an alarm on HTTP Archive jumps).
MAX_TOTAL_JUMP = 0.5

#: The crawl is monthly and the Tech Report lands a few weeks after; older than this is stale.
MAX_AGE_DAYS = 75

ADOPTION_KEY = ["technology", "date", "geo", "rank", "client"]
COVERAGE_KEY = ["technology"]

# --- schemas ---------------------------------------------------------------------------------

ADOPTION_DTYPES: dict[str, pl.DataType] = {
    "technology": pl.Utf8,
    "date": pl.Date,          # first day of the crawl month
    "geo": pl.Utf8,
    "rank": pl.Utf8,
    "client": pl.Utf8,
    "origins": pl.Int64,
    "total_origins": pl.Int64,  # null when the denominator slice failed
    "share": pl.Float64,        # origins / total_origins, a fraction in [0, 1]
}

ADOPTION_SCHEMA = pa.DataFrameSchema(
    {
        "technology": pa.Column(str, pa.Check.isin(list(TECHNOLOGIES))),
        "date": pa.Column(pl.Date),
        "geo": pa.Column(str, pa.Check.isin(list(GEOS))),
        "rank": pa.Column(str, pa.Check.isin(RANKS)),
        "client": pa.Column(str, pa.Check.isin(CLIENTS)),
        "origins": pa.Column(int, pa.Check.ge(0)),
        "total_origins": pa.Column(int, pa.Check.gt(0), nullable=True),
        "share": pa.Column(float, pa.Check.in_range(0.0, 1.0), nullable=True),
    },
    unique=ADOPTION_KEY,
    strict=True,
    coerce=False,
)

COVERAGE_DTYPES: dict[str, pl.DataType] = {
    "technology": pl.Utf8,
    "first_month": pl.Date,     # first crawl with a non-zero count (ALL geo, ALL rank, mobile)
    "months_present": pl.Int64,
    "latest_month": pl.Date,
    "latest_origins": pl.Int64,
    "detectable": pl.Boolean,
    "note": pl.Utf8,
}

COVERAGE_SCHEMA = pa.DataFrameSchema(
    {
        "technology": pa.Column(str, pa.Check.isin(list(TECHNOLOGIES)), unique=True),
        "first_month": pa.Column(pl.Date, nullable=True),
        "months_present": pa.Column(int, pa.Check.ge(0)),
        "latest_month": pa.Column(pl.Date, nullable=True),
        "latest_origins": pa.Column(int, pa.Check.ge(0)),
        "detectable": pa.Column(bool),
        "note": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

_COUNTS_DTYPES = {k: ADOPTION_DTYPES[k] for k in ["technology", "date", "geo", "rank", "client", "origins"]}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JsonClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


# --- parse -----------------------------------------------------------------------------------


def parse_adoption(payload: Any, technology: str, geo: str, rank: str) -> pl.DataFrame:
    """One ``/v1/adoption`` response -> long rows (technology, date, geo, rank, client, origins).

    Strict: an error envelope, a non-list, a row for another technology, or a bad date raises
    ``ValueError`` so the caller records the slice as failed. A null client value (the crawl did not
    run that client that month) is skipped rather than read as zero. An empty list is valid: the
    technology was never detected in that slice.
    """
    if isinstance(payload, dict):
        raise ValueError(f"API error envelope: {payload.get('errors') or payload}")
    if not isinstance(payload, list):
        raise ValueError(f"expected a list, got {type(payload).__name__}")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or "date" not in item or "adoption" not in item:
            raise ValueError(f"malformed adoption row: {item!r}")
        if item.get("technology", technology) != technology:
            raise ValueError(f"row for {item.get('technology')!r} in a {technology!r} response")
        d = str(item["date"])
        if not _DATE_RE.match(d):
            raise ValueError(f"bad date {d!r}")
        adoption = item["adoption"] or {}
        if not isinstance(adoption, dict):
            raise ValueError(f"malformed adoption block: {adoption!r}")
        for client in CLIENTS:
            v = adoption.get(client)
            if v is None:
                continue
            n = int(v)
            if n < 0 or n != v:
                raise ValueError(f"bad origin count {v!r} for {technology} {d} {client}")
            rows.append({"technology": technology, "date": date_cls.fromisoformat(d), "geo": geo,
                         "rank": rank, "client": client, "origins": n})
    df = pl.DataFrame(rows, schema=_COUNTS_DTYPES)
    dup = df.height - df.unique(subset=ADOPTION_KEY).height
    if dup:
        raise ValueError(f"{dup} duplicate months for {technology} {geo} {rank}")
    return df.sort(ADOPTION_KEY)


# --- fetch -----------------------------------------------------------------------------------


@dataclass
class FetchResult:
    counts: pl.DataFrame                                         # payment technologies
    totals: pl.DataFrame                                         # technology == ALL, the denominator
    checks: list[dict] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)            # slice slug -> payload, for data/raw
    failed: list[tuple[str, str, str]] = field(default_factory=list)


def slice_slug(technology: str, geo: str, rank: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{technology}_{geo}_{rank}".lower()).strip("-")


def fetch_adoption(client: JsonClient, technology: str, geo: str, rank: str,
                   start: str = START, end: str | None = None) -> Any:
    params: dict[str, Any] = {"technology": technology, "geo": GEOS[geo], "rank": rank, "start": start}
    if end:
        params["end"] = end
    return client.get_json("/adoption", params=params)


def fetch_all(client: JsonClient | None = None, *, technologies: list[str] | None = None,
              geos: list[str] | None = None, ranks: list[str] | None = None,
              start: str = START, end: str | None = None) -> FetchResult:
    """Fetch every (technology, geo, rank) slice plus the ``ALL`` denominator; never raises per slice."""
    own = client is None
    client = client or HttpClient(BASE_URL, min_interval=0.5, max_retries=3)
    techs = technologies or list(TECHNOLOGIES)
    geos = geos or list(GEOS)
    ranks = ranks or RANKS
    counts: list[pl.DataFrame] = []
    totals: list[pl.DataFrame] = []
    res = FetchResult(counts=pl.DataFrame(schema=_COUNTS_DTYPES), totals=pl.DataFrame(schema=_COUNTS_DTYPES))
    try:
        for tech in [TOTAL_TECHNOLOGY, *techs]:
            for geo in geos:
                for rank in ranks:
                    slug = slice_slug(tech, geo, rank)
                    try:
                        payload = fetch_adoption(client, tech, geo, rank, start, end)
                        df = parse_adoption(payload, tech, geo, rank)
                    except Exception as exc:  # noqa: BLE001 - isolate every slice (rule 5)
                        log.error("adoption %s / %s / %s failed: %s", tech, geo, rank, exc)
                        res.failed.append((tech, geo, rank))
                        res.checks.append(check(f"Web coverage fetch: {tech} / {geo} / {rank}", False,
                                                f"{type(exc).__name__}: {exc}"[:300]))
                        continue
                    res.raw[slug] = payload
                    (totals if tech == TOTAL_TECHNOLOGY else counts).append(df)
                    log.info("adoption %s / %s / %s: %d rows", tech, geo, rank, df.height)
    finally:
        if own:
            client.close()  # type: ignore[union-attr]
    if counts:
        res.counts = pl.concat(counts)
    if totals:
        res.totals = pl.concat(totals)
    n = (1 + len(techs)) * len(geos) * len(ranks)
    res.checks.insert(0, check("Web coverage slices", not res.failed,
                               f"{n - len(res.failed)} of {n} technology/geo/rank slices fetched"
                               + (f"; failed: {', '.join('/'.join(f) for f in res.failed)}" if res.failed else ""),
                               warn=len(res.failed) < n))
    return res


# --- transform -------------------------------------------------------------------------------


def build_adoption(counts: pl.DataFrame, totals: pl.DataFrame) -> pl.DataFrame:
    """Join counts to the crawl total for the same month/geo/rank/client and validate.

    Months a technology was not detected are absent, not zero: the API omits them, and inventing
    zeros would turn "no row" (possibly a failed crawl) into "no adoption".

    A row whose count exceeds its crawl total (a partial or inconsistent ALL response for one
    geo/rank/client month) keeps its counts but gets a null share, so one bad month cannot fail the
    schema for every other slice (rule 5). ``quality_checks`` reports those rows as a failed
    "Web coverage range" check.
    """
    denom = totals.select("date", "geo", "rank", "client", pl.col("origins").alias("total_origins"))
    df = (
        counts.join(denom, on=["date", "geo", "rank", "client"], how="left")
        .with_columns(
            pl.when((pl.col("total_origins") > 0) & (pl.col("origins") <= pl.col("total_origins")))
            .then(pl.col("origins") / pl.col("total_origins"))
            .otherwise(None)
            .cast(pl.Float64)
            .alias("share")
        )
        .select(list(ADOPTION_DTYPES))
        .cast(ADOPTION_DTYPES)  # type: ignore[arg-type]
        .sort(ADOPTION_KEY)
    )
    return ADOPTION_SCHEMA.validate(df)


def build_coverage(adoption: pl.DataFrame, *, geo: str = "ALL", rank: str = "ALL",
                   client: str = "mobile") -> pl.DataFrame:
    """Detection-coverage table: one row per pinned technology, including ones never detected."""
    base = adoption.filter((pl.col("geo") == geo) & (pl.col("rank") == rank) & (pl.col("client") == client))
    present = base.filter(pl.col("origins") > 0)
    latest = base.sort("date").group_by("technology").agg(
        pl.col("date").last().alias("latest_month"), pl.col("origins").last().alias("latest_origins")
    )
    agg = present.group_by("technology").agg(
        pl.col("date").min().alias("first_month"), pl.col("date").n_unique().alias("months_present")
    )
    roster = pl.DataFrame({"technology": list(TECHNOLOGIES), "note": list(TECHNOLOGIES.values())})
    df = (
        roster.join(agg, on="technology", how="left")
        .join(latest, on="technology", how="left")
        .with_columns(
            pl.col("months_present").fill_null(0).cast(pl.Int64),
            pl.col("latest_origins").fill_null(0).cast(pl.Int64),
        )
        .with_columns((pl.col("latest_origins") >= MIN_DETECTABLE_ORIGINS).alias("detectable"))
        .with_columns(
            pl.when(~pl.col("detectable") & (pl.col("note") == ""))
            .then(pl.lit(f"Below {MIN_DETECTABLE_ORIGINS:,} origins in the latest crawl; not charted as a trend."))
            .otherwise(pl.col("note"))
            .alias("note")
        )
        .select(list(COVERAGE_DTYPES))
        .cast(COVERAGE_DTYPES)  # type: ignore[arg-type]
    )
    return COVERAGE_SCHEMA.validate(df)


# --- checks ----------------------------------------------------------------------------------


def total_jumps(totals: pl.DataFrame, max_jump: float = MAX_TOTAL_JUMP) -> pl.DataFrame:
    """Months where the crawl's total origins moved more than ``max_jump`` from the previous month."""
    return (
        totals.sort("date")
        .with_columns(pl.col("origins").shift(1).over(["geo", "rank", "client"]).alias("prev"))
        .filter(pl.col("prev").is_not_null() & (pl.col("prev") > 0))
        .with_columns(((pl.col("origins") - pl.col("prev")).abs() / pl.col("prev")).alias("change"))
        .filter(pl.col("change") > max_jump)
    )


def quality_checks(adoption: pl.DataFrame, totals: pl.DataFrame, today: date_cls | None = None) -> list[dict]:
    today = today or utc_now().date()
    out = [uniqueness(adoption, ADOPTION_KEY, "technology-months", name="Web coverage key uniqueness")]
    over = adoption.filter(pl.col("total_origins").is_not_null() & (pl.col("origins") > pl.col("total_origins")))
    detail = f"{over.height} rows with origins above the crawl total (share left null)"
    if over.height:
        detail += " (" + ", ".join(f"{r['technology']} {r['date']} {r['geo']}/{r['rank']}/{r['client']}"
                                   for r in over.head(5).iter_rows(named=True)) + ")"
    out.append(check("Web coverage range", over.height == 0, detail))
    missing = adoption.filter(pl.col("total_origins").is_null()).height
    out.append(check("Web coverage denominator", missing == 0,
                     f"{missing} rows without a crawl total (share left null)", warn=True))
    jumps = total_jumps(totals)
    detail = f"{jumps.height} month-over-month crawl-size moves above {MAX_TOTAL_JUMP:.0%}"
    if jumps.height:
        detail += " (" + ", ".join(f"{r['date']} {r['geo']}/{r['rank']}/{r['client']}"
                                   for r in jumps.head(5).iter_rows(named=True)) + ")"
    out.append(check("Web coverage crawl size", jumps.height == 0, detail, warn=True))
    if adoption.height:
        latest = adoption["date"].max()
        age = (today - latest).days  # type: ignore[operator]
        out.append(check("Web coverage freshness", age <= MAX_AGE_DAYS,
                         f"latest crawl {latest} is {age} days old (limit {MAX_AGE_DAYS})",
                         warn=age <= 2 * MAX_AGE_DAYS))
    else:
        out.append(check("Web coverage freshness", False, "no adoption rows"))
    return out


# --- write -----------------------------------------------------------------------------------


def write(adoption: pl.DataFrame, coverage: pl.DataFrame, out_dir: Path | None = None) -> list[Path]:
    """Validate, then write ``webtech_adoption.parquet`` and ``webtech_coverage.parquet``.

    A schema failure raises before anything is written (rule 3). The regression guard against a run
    that lost most slices lives in ``publish.py`` (W2f).
    """
    out_dir = out_dir or MARTS_DIR / "payments"
    adoption = ADOPTION_SCHEMA.validate(adoption)
    coverage = COVERAGE_SCHEMA.validate(coverage)
    return [write_parquet(adoption, out_dir / "webtech_adoption.parquet"),
            write_parquet(coverage, out_dir / "webtech_coverage.parquet")]


def archive_raw(raw: dict[str, Any], root: Path | None = None) -> Path:
    """Append-only copy of every successful response under data/raw/payments/webtech/<date>/<HHMM>/."""
    day, hhmm = run_stamp(utc_now())
    base = (root or RAW_DIR) / "payments" / "webtech" / day / hhmm
    for slug, payload in raw.items():
        write_json_gz(payload, base / f"{slug}.json.gz")
    return base


def run(client: JsonClient | None = None, *, out_dir: Path | None = None, raw_root: Path | None = None,
        write_outputs: bool = True) -> tuple[pl.DataFrame, pl.DataFrame, list[dict]]:
    res = fetch_all(client)
    if write_outputs and res.raw:
        # Archive before any transform, so raw responses survive a transform bug (rule 2).
        archive_raw(res.raw, raw_root)
    adoption = build_adoption(res.counts, res.totals)
    coverage = build_coverage(adoption)
    checks = res.checks + quality_checks(adoption, res.totals)
    if write_outputs:
        if adoption.height:
            write(adoption, coverage, out_dir)
        else:
            log.error("no adoption rows; nothing written")
    return adoption, coverage, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and validate, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    adoption, coverage, checks = run(write_outputs=not args.dry_run)
    for c in checks:
        log.info("[%s] %s: %s", c["status"], c["name"], c["detail"])
    log.info("%d adoption rows; detectable: %s", adoption.height,
             ", ".join(coverage.filter("detectable")["technology"].to_list()) or "none")
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
