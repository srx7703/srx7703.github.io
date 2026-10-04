"""Hiring signal: open-role counts by function and location from public Greenhouse and Ashby boards.

An auxiliary signal (PLAN §3 M7, EXECUTION W2e), never part of share. It answers "where is each company
adding people" (engineering against sales against risk and compliance) from boards the companies publish
themselves. The boards only show what is open now, so history starts with the first weekly snapshot.

What is kept, and what never is
-------------------------------
Rights rule (EXECUTION §1): job-description text is never committed. Ashby's posting API returns the full
description in every posting, and titles are free text too. So:

* titles (and Ashby's department/team names) are read **in memory** only, to classify the function;
* the raw responses are **not** archived to data/raw (unlike every other source in this project);
* the only persisted rows are ``(snapshot_date, ats, board, function, location_bucket, postings, openings)``.

``postings`` counts listings; ``openings`` counts distinct requisitions (Greenhouse ``internal_job_id``), since
one role is often posted once per country. Ashby has no requisition id, so there the two are equal.

Sources (keyless)
-----------------
::

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
    {"jobs": [{"id": 7903949003, "internal_job_id": 5842019003, "title": "Accountant",
               "location": {"name": "Mexico, Remote"}, "company_name": "Sezzle", ...}], "meta": {"total": 1}}

    GET https://api.ashbyhq.com/posting-api/job-board/{token}
    {"jobs": [{"id": "...", "title": "...", "department": "...", "team": "...", "location": "San Francisco HQ",
               "workplaceType": "Hybrid", "isListed": true,
               "address": {"postalAddress": {"addressCountry": "United States", ...}},
               "descriptionHtml": "...", ...}]}

Board tokens are pinned in ``config.ATS_BOARDS``; ``config.ATS_EXCLUDED_TOKENS`` (Greenhouse ``wise`` is a
different company) are refused. Greenhouse states the employer in ``company_name``, which gives an ownership
check per board; a mismatch is a warning for the orchestrator to turn into an amendment, not a silent drop.

Classification
--------------
A deterministic keyword classifier. A title is usually "role, team" ("Software Engineer, Risk"), and the role
decides the function, so the part before the first separator is classified first and the full title only when
that part says nothing. Ashby's department and team are a last fallback. Rules are tried in a fixed order;
the first match wins. Location buckets: ``remote`` (a remote workplace type or "remote" in the location),
``US`` (any US location), ``non-US``, and ``unknown`` when the board gives no location at all (kept separate
rather than guessed).

Failure isolation (CLAUDE.md rule 5)
------------------------------------
Every board fails on its own: an error is logged and recorded as a failed check, the other boards land.

Usage (runs in GitHub Actions; this container has no outbound network)::

    uv run python -m pipelines.payments.jobs [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, SNAP_DIR, run_stamp, utc_now, write_parquet
from pipelines.payments.config import ATS_BOARDS, ATS_EXCLUDED_TOKENS

log = logging.getLogger("payments.jobs")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

FUNCTIONS = ["engineering", "sales/gtm", "risk/compliance", "product/design", "operations/support",
             "finance/legal", "other"]
LOCATION_BUCKETS = ["US", "non-US", "remote", "unknown"]

#: Expected employer per Greenhouse token (regex on ``company_name``): the ownership check.
GREENHOUSE_EMPLOYER: dict[str, str] = {
    "stripe": r"stripe", "brex": r"brex", "mercury": r"mercury", "affirm": r"affirm", "adyen": r"adyen",
    "chime": r"chime", "block": r"block|square|cash app|afterpay|tidal", "toast": r"toast",
    "sezzle": r"sezzle", "billcom": r"\bbill\b|bill\.com|bill holdings",
}


def boards() -> list[tuple[str, str]]:
    """(ats, token) pairs from config, with excluded tokens refused."""
    out = []
    for ats, tokens in ATS_BOARDS.items():
        if ats not in ("greenhouse", "ashby"):
            raise ValueError(f"unknown ATS {ats!r}")
        for t in tokens:
            if t in ATS_EXCLUDED_TOKENS.get(ats, []):
                raise ValueError(f"{ats} token {t!r} is excluded (a different company)")
            out.append((ats, t))
    return out


# --- classifier ------------------------------------------------------------------------------

_W = r"(?<![a-z0-9])"   # word start
_E = r"(?![a-z0-9])"    # word end


def _rx(*words: str) -> re.Pattern[str]:
    return re.compile("|".join(f"{_W}{w}{_E}" for w in words))


#: Ordered (function, pattern). First match wins, so the specific rules sit above the broad ones:
#: "sales engineer" is go-to-market, "support engineer" is support, "counsel, compliance" is legal.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("sales/gtm", _rx(r"sales engineers?", r"solutions? (?:engineer|architect|consultant)s?", r"pre-?sales",
                      r"technical account manager", r"product marketing")),
    ("operations/support", _rx(r"support engineers?", r"technical support")),
    ("finance/legal", _rx(r"counsel", r"attorney", r"lawyer", r"paralegal", r"legal", r"accountant",
                          r"accounting", r"controller", r"tax", r"treasury", r"fp&a", r"financial planning",
                          r"payroll", r"audit(?:or)?", r"finance", r"financial analyst", r"revenue accountant",
                          r"procurement", r"investor relations")),
    ("risk/compliance", _rx(r"compliance", r"aml", r"bsa", r"kyc", r"kyb", r"financial crimes?", r"fincrime",
                            r"sanctions", r"fraud", r"risk", r"credit", r"underwrit(?:er|ing)", r"regulatory",
                            r"trust (?:&|and) safety", r"investigat(?:or|ions?)", r"model validation")),
    ("engineering", _rx(r"engineer(?:s|ing)?", r"developer", r"software", r"sre", r"devops", r"data scien(?:ce|tist)",
                        r"machine learning", r"ml", r"ai", r"research scientist", r"scientist", r"architect",
                        r"security", r"infrastructure", r"data analyst", r"analytics", r"programmer", r"qa",
                        r"technical program manager")),
    ("product/design", _rx(r"product (?:manager|management|owner|lead|director|operations)", r"head of product",
                           r"vp,? product", r"product", r"designer", r"design", r"ux", r"ui", r"user research(?:er)?",
                           r"content strateg(?:y|ist)")),
    ("sales/gtm", _rx(r"sales", r"account (?:executive|manager|director)", r"business development", r"bdr", r"sdr",
                      r"partnerships?", r"partner", r"marketing", r"growth", r"go-to-market", r"gtm",
                      r"customer success", r"revenue operations", r"demand generation", r"brand",
                      r"communications", r"public relations", r"events", r"enterprise", r"commercial",
                      r"merchant success", r"relationship manager")),
    ("operations/support", _rx(r"support", r"operations", r"ops", r"customer experience", r"cx", r"onboarding",
                               r"implementation", r"disputes?", r"chargebacks?", r"servicing", r"collections",
                               r"customer care", r"service", r"program manager", r"project manager",
                               r"chief of staff", r"strategy", r"bizops", r"workplace", r"facilities",
                               r"office manager", r"specialist", r"associate")),
]

_SEP = re.compile(r"\s*(?:,|\s[-–—|]\s|\(|:|/\s)\s*")


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace(".", "")).strip()


def _match(text: str) -> str | None:
    for fn, rx in RULES:
        if rx.search(text):
            return fn
    return None


def classify(title: str, department: str | None = None, team: str | None = None) -> str:
    """Function for a posting: role part of the title, then the full title, then department/team."""
    t = _norm(title)
    head = _SEP.split(t, maxsplit=1)[0] if t else ""
    for text in (head, t, _norm(team), _norm(department)):
        if text and (fn := _match(text)):
            return fn
    return "other"


_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
              "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_US_STATE_NAMES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
                   "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
                   "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
                   "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
                   "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
                   "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
                   "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming"]
_US_CITIES = ["san francisco", "new york", "nyc", "seattle", "chicago", "austin", "boston", "denver",
              "los angeles", "atlanta", "miami", "salt lake city", "nashville", "portland", "dallas",
              "philadelphia", "phoenix", "lehi", "palo alto", "menlo park", "oakland", "san jose", "san diego",
              "minneapolis", "pittsburgh", "raleigh", "charlotte", "houston", "detroit", "st louis", "jersey city"]
_RX_US_TEXT = re.compile(
    r"united states|(?<![a-z])usa?(?![a-z])|(?<![a-z])u\.s\.|"
    + "|".join(rf"(?<![a-z]){re.escape(x)}(?![a-z])" for x in _US_STATE_NAMES + _US_CITIES)
)
_RX_US_STATE_ABBR = re.compile(r",\s*(?:" + "|".join(_US_STATES) + r")(?![A-Za-z])")
_RX_NON_US_OVERRIDE = re.compile(r"canada|ontario|british columbia|quebec|alberta|mexico|new south wales|"
                                 r"western australia|united kingdom|\buk\b")
_RX_REMOTE = re.compile(r"(?<![a-z])remote(?![a-z])")
_US_COUNTRY = {"united states", "united states of america", "usa", "us"}


def _part_is_us(part: str) -> bool:
    low = part.lower()
    if _RX_US_STATE_ABBR.search(part) and not _RX_NON_US_OVERRIDE.search(low):
        return True
    if _RX_US_TEXT.search(low):
        # "Washington" / "Georgia" / "New Mexico" also name places outside the US
        return not _RX_NON_US_OVERRIDE.search(low) or "united states" in low
    return False


def location_bucket(location: str | None, *, workplace_type: str | None = None,
                    country: str | None = None) -> str:
    """US / non-US / remote / unknown. Remote wins; then a structured country; then any US part of the text."""
    text = (location or "").strip()
    if (workplace_type or "").lower() == "remote" or _RX_REMOTE.search(text.lower()):
        return "remote"
    if country:
        return "US" if country.strip().lower() in _US_COUNTRY else "non-US"
    if not text:
        return "unknown"
    parts = re.split(r";|\||\bor\b|\s/\s|\n", text)
    return "US" if any(_part_is_us(p) for p in parts if p.strip()) else "non-US"


# --- parse -----------------------------------------------------------------------------------

#: In-memory posting: no text survives past ``count``.
POSTING_FIELDS = ("req", "function", "location_bucket")


def parse_greenhouse(payload: Any) -> tuple[list[dict], dict]:
    """Postings (req, function, bucket) and metadata (total, employers). Titles do not leave this function."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"malformed Greenhouse payload: {str(payload)[:200]}")
    out = []
    employers: set[str] = set()
    for j in payload["jobs"]:
        if not isinstance(j, dict) or "title" not in j:
            raise ValueError("Greenhouse job without a title")
        loc = (j.get("location") or {}).get("name") if isinstance(j.get("location"), dict) else None
        out.append({"req": str(j.get("internal_job_id") or j.get("id")), "function": classify(str(j["title"])),
                    "location_bucket": location_bucket(loc)})
        if j.get("company_name"):
            employers.add(str(j["company_name"]))
    total = (payload.get("meta") or {}).get("total")
    return out, {"total": total, "employers": sorted(employers)}


def parse_ashby(payload: Any) -> tuple[list[dict], dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"malformed Ashby payload: {str(payload)[:200]}")
    out = []
    for j in payload["jobs"]:
        if not isinstance(j, dict) or "title" not in j:
            raise ValueError("Ashby job without a title")
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {}) if isinstance(j.get("address"), dict) else {}
        out.append({
            "req": str(j.get("id")),
            "function": classify(str(j["title"]), j.get("department"), j.get("team")),
            "location_bucket": location_bucket(j.get("location"), workplace_type=j.get("workplaceType"),
                                               country=addr.get("addressCountry")),
        })
    return out, {"total": None, "employers": []}


# --- schema ----------------------------------------------------------------------------------

COUNT_KEY = ["snapshot_date", "ats", "board", "function", "location_bucket"]
COUNT_DTYPES: dict[str, pl.DataType] = {
    "snapshot_date": pl.Date,
    "ats": pl.Utf8,
    "board": pl.Utf8,
    "function": pl.Utf8,
    "location_bucket": pl.Utf8,
    "postings": pl.Int64,
    "openings": pl.Int64,
}
COUNT_SCHEMA = pa.DataFrameSchema(
    {
        "snapshot_date": pa.Column(pl.Date),
        "ats": pa.Column(str, pa.Check.isin(["greenhouse", "ashby"])),
        "board": pa.Column(str),
        "function": pa.Column(str, pa.Check.isin(FUNCTIONS)),
        "location_bucket": pa.Column(str, pa.Check.isin(LOCATION_BUCKETS)),
        "postings": pa.Column(int, pa.Check.gt(0)),
        "openings": pa.Column(int, pa.Check.gt(0)),
    },
    unique=COUNT_KEY,
    strict=True,  # the guard that no text column can be added by accident
    coerce=False,
    checks=[pa.Check(lambda df: (df.lazyframe.select(pl.col("openings") <= pl.col("postings"))),
                     error="openings above postings")],
)


def count(postings: list[dict], ats: str, board: str, snapshot_date: date) -> pl.DataFrame:
    """In-memory postings -> persisted counts (the only shape that leaves memory)."""
    if not postings:
        return pl.DataFrame(schema=COUNT_DTYPES)
    df = pl.DataFrame(postings, schema={"req": pl.Utf8, "function": pl.Utf8, "location_bucket": pl.Utf8})
    out = df.group_by(["function", "location_bucket"]).agg(
        pl.len().cast(pl.Int64).alias("postings"), pl.col("req").n_unique().cast(pl.Int64).alias("openings"))
    out = out.with_columns(pl.lit(snapshot_date).alias("snapshot_date"), pl.lit(ats).alias("ats"),
                           pl.lit(board).alias("board"))
    return out.select(list(COUNT_DTYPES)).cast(COUNT_DTYPES).sort(COUNT_KEY)  # type: ignore[arg-type]


# --- fetch -----------------------------------------------------------------------------------


class JsonClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass
class FetchResult:
    counts: pl.DataFrame
    checks: list[dict] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def fetch_all(client: JsonClient | None = None, *, snapshot_date: date | None = None,
              only: list[tuple[str, str]] | None = None) -> FetchResult:
    """Fetch, classify and count every board; nothing but counts is returned."""
    own = client is None
    client = client or HttpClient(min_interval=0.5, max_retries=3)
    snapshot_date = snapshot_date or utc_now().date()
    todo = only or boards()
    frames: list[pl.DataFrame] = []
    res = FetchResult(counts=pl.DataFrame(schema=COUNT_DTYPES))
    try:
        for ats, token in todo:
            label = f"{ats}/{token}"
            try:
                if ats == "greenhouse":
                    postings, meta = parse_greenhouse(client.get_json(GREENHOUSE_URL.format(token=token)))
                else:
                    postings, meta = parse_ashby(client.get_json(ASHBY_URL.format(token=token)))
            except Exception as exc:  # noqa: BLE001 - isolate every board (rule 5)
                log.error("board %s failed: %s", label, exc)
                res.failed.append(label)
                res.checks.append(check(f"Job board fetch: {label}", False, f"{type(exc).__name__}: {exc}"[:300]))
                continue
            frames.append(count(postings, ats, token, snapshot_date))
            log.info("board %s: %d postings", label, len(postings))
            if not postings:
                res.checks.append(check(f"Job board size: {label}", False,
                                        "0 open postings; the token may be stale", warn=True))
            if meta["total"] is not None and meta["total"] != len(postings):
                res.checks.append(check(f"Job board total: {label}", False,
                                        f"meta.total {meta['total']} vs {len(postings)} postings", warn=True))
            if ats == "greenhouse" and token in GREENHOUSE_EMPLOYER and meta["employers"]:
                ok = all(re.search(GREENHOUSE_EMPLOYER[token], e, re.I) for e in meta["employers"])
                res.checks.append(check(f"Job board owner: {label}", ok,
                                        f"company_name: {', '.join(meta['employers'])}", warn=True))
    finally:
        if own:
            client.close()  # type: ignore[union-attr]
    if frames:
        res.counts = pl.concat(frames)
    n = len(todo)
    res.checks.insert(0, check("Job boards", not res.failed,
                               f"{n - len(res.failed)} of {n} boards fetched"
                               + (f"; failed: {', '.join(res.failed)}" if res.failed else ""),
                               warn=len(res.failed) < n))
    return res


# --- mart ------------------------------------------------------------------------------------


def snapshot_dir(root: Path | None = None) -> Path:
    return (root or SNAP_DIR) / "payments" / "jobs"


def build_mart(snap_root: Path | None = None) -> pl.DataFrame:
    """All weekly snapshots -> one table; a board snapshotted twice on a day keeps the later run."""
    files = sorted(snapshot_dir(snap_root).glob("*/*.parquet"))
    if not files:
        return pl.DataFrame(schema=COUNT_DTYPES)
    parts = [pl.read_parquet(f).with_columns(pl.lit(f.stem).alias("_hhmm")) for f in files]
    df = pl.concat(parts, how="diagonal_relaxed")
    latest = df.group_by(["snapshot_date", "ats", "board"]).agg(pl.col("_hhmm").max())
    df = df.join(latest, on=["snapshot_date", "ats", "board", "_hhmm"]).drop("_hhmm")
    return COUNT_SCHEMA.validate(df.select(list(COUNT_DTYPES)).cast(COUNT_DTYPES).sort(COUNT_KEY))  # type: ignore[arg-type]


def quality_checks(counts: pl.DataFrame) -> list[dict]:
    out = [uniqueness(counts, COUNT_KEY, "board-function-location cells", name="Hiring key uniqueness")]
    if counts.height:
        other = counts.filter(pl.col("function") == "other")["postings"].sum()
        total = counts["postings"].sum()
        share = other / total if total else 0.0
        out.append(check("Hiring classifier coverage", share <= 0.25,
                         f"{share:.0%} of postings classified 'other' (limit 25%)", warn=share <= 0.4))
    return out


def run(client: JsonClient | None = None, *, snap_root: Path | None = None, out_dir: Path | None = None,
        write_outputs: bool = True) -> tuple[pl.DataFrame, list[dict]]:
    ts = utc_now()
    res = fetch_all(client, snapshot_date=ts.date())
    counts = COUNT_SCHEMA.validate(res.counts)
    checks = res.checks + quality_checks(counts)
    if write_outputs:
        if counts.height:
            day, hhmm = run_stamp(ts)
            write_parquet(counts, snapshot_dir(snap_root) / day / f"{hhmm}.parquet")
            write_parquet(build_mart(snap_root), (out_dir or MARTS_DIR / "payments") / "jobs_counts.parquet")
        else:
            log.error("no postings from any board; nothing written")
    return counts, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and classify, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    counts, checks = run(write_outputs=not args.dry_run)
    for c in checks:
        log.info("[%s] %s: %s", c["status"], c["name"], c["detail"])
    log.info("%d count rows across %d boards", counts.height, counts["board"].n_unique() if counts.height else 0)
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
