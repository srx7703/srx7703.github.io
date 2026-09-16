"""Score the pre-registered evaluation items for the two valuation pages.

Usage:
    uv run python -m pipelines.valuation.evaluate

Writes ``data/marts/valuation/evaluation.json``.

The rules are fixed in ``docs/EVALUATION_PLAN.md``; this module only applies them. The plan's
valuation section and the first snapshot went into the repository in the *same* commit (7af7c68), so
the "registered before the data" guarantee runs from the second run onward, not from the first. The
plan records that in its own words and the pages must not claim more than it.

Every constant that changes what an item reports is registered in the plan, because a threshold that
lives only in code sits outside the blob hash the pages print and could be tuned to the answer without
leaving a trace.

Item 1 (consensus accuracy) cannot run until the 2026 annual reports land in 2027. Items 2 to 5 are
computed from what is on disk, and each one reports how many periods or pairs it rests on, because
four weeks of history is a description, not a finding. An item that cannot be scored yet returns
``{"status": "not_yet", "why": ...}`` rather than a placeholder number, and the reason is built from
the track's own data so that it is true on the page that prints it.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
import sys
from collections import Counter
from datetime import date

import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, utc_now, write_json, write_parquet
from pipelines.valuation import read, schema
from pipelines.valuation.config import COMPANIES, TRACKS
from pipelines.valuation.metrics import vintage_estimates
from pipelines.valuation.share import cited_share, load_reference

log = logging.getLogger("valuation.evaluate")

YEARS = (2026, 2027)
MART_DIR = MARTS_DIR / "valuation"

# ---------------------------------------------------------------------------------------------
# Registered constants. Each of these is written out in docs/EVALUATION_PLAN.md; changing one here
# without changing it there is a defect, and tests/test_evaluate.py checks that the plan names them.
# ---------------------------------------------------------------------------------------------

#: item 2 - an estimate must move by more than this to count as a revision rather than as rounding
REVISION_EPSILON = 0.005
#: item 2 - the grouping interval: "the week-on-week change", with the slack a real calendar allows
STEP_DAYS = 7
STEP_TOLERANCE_DAYS = 3
#: item 2 - "four weeks later". 21 days is three weeks and an easier test; it is not what was registered.
HORIZON_DAYS = 28
HORIZON_TOLERANCE_DAYS = 4
#: item 2 - a week in which fewer than this many listings moved at all is dropped, and counted
MIN_MOVERS_PER_PERIOD = 10
#: item 2 - below this many usable weeks the matrix is a description of noise, so nothing is reported
MIN_PERIODS_FOR_PERSISTENCE = 3
#: item 2 - a chi-square cell this thin makes the asymptotic p-value unreliable; it is flagged, not hidden
MIN_EXPECTED_CELL = 5.0

#: item 2 - only these vintage origins are true revisions of a fixed analyst panel. "eastmoney_rebuilt"
#: is a coverage series: its mean moves when a new broker starts covering the stock, which is not a
#: revision. Excluding it is what kept the A-shares out of this item until `snapshot` became real.
REVISION_ORIGINS = ("yahoo_trend", "snapshot")
#: our own capture of the current consensus, written by `capture_snapshot_vintages` below
SNAPSHOT_ORIGIN = "snapshot"
#: how many archived estimate dates are turned into snapshot vintages on a run
SNAPSHOT_LIMIT_DATES = 60

#: item 5 - a rank correlation on fewer pairs than this is arithmetic, not evidence
MIN_RANK_PAIRS = 3
#: item 5 - a company name in a citation is at most this many words; longer means it is a sentence
MAX_NAME_TOKENS = 5


def _not_yet(why: str) -> dict:
    return {"status": "not_yet", "why": why}


# ---------------------------------------------------------------------------------------------
# Item 1
# ---------------------------------------------------------------------------------------------


def consensus_accuracy() -> dict:
    """Item 1. Needs reported calendar-2026 EPS, which does not exist until the 2027 filing season."""
    return _not_yet(
        "the calendar-2026 consensus is scored against reported 2026 results; the last A-share and "
        "Japanese filers report in spring 2027, so this runs from May 2027"
    )


# ---------------------------------------------------------------------------------------------
# The snapshot vintage series, which item 2 is waiting for
# ---------------------------------------------------------------------------------------------


def snapshot_vintage_rows(estimate_rows: list[dict], as_of: str) -> list[dict]:
    """The consensus in one archived estimates file, restated as vintage rows.

    A vintage row is "what the consensus for this fiscal year was on this date". An archived estimates
    file is exactly that for the date it was written, so the snapshot series is a derivation of the
    archive rather than a separate capture: it can be rebuilt from scratch, it back-fills every
    estimates date already on disk, and a run that fails loses nothing permanently.
    """
    out: list[dict] = []
    for r in estimate_rows:
        if r.get("eps_avg") is None:
            continue
        if not (r.get("ticker") and r.get("source") and r.get("period_end")):
            continue
        out.append(
            {
                "snapshot_ts": r.get("snapshot_ts"),
                "ticker": r["ticker"],
                "source": r["source"],
                "period_end": r["period_end"],
                "as_of": as_of,
                "eps_avg": r["eps_avg"],
                "n_analysts": r.get("n_analysts"),
                "currency": r.get("currency"),
                "origin": SNAPSHOT_ORIGIN,
            }
        )
    return out


def capture_snapshot_vintages(limit_dates: int = SNAPSHOT_LIMIT_DATES) -> list[str]:
    """Write ``vintages/<date>-snapshot.parquet`` for every archived estimates date.

    Yahoo already publishes a same-day vintage (``epsTrend.current``, origin ``yahoo_trend``), so the
    listings that had no true revision series were the East Money ones: their only vintages are
    ``eastmoney_rebuilt``, a coverage series that item 2 must not difference. This is what makes the
    plan's escape hatch - "until our own weekly snapshots accumulate" - something that can actually
    happen.

    A (ticker, period_end, as_of) that any other origin already describes is left alone, source
    included. Two origins on one date would be two answers to "what was the consensus that day", and
    ``metrics.vintage_estimates`` groups a ticker's rows by date alone, so it would pick one of them by
    whatever order the parquet parts happened to concatenate in. Rows previously written by this
    function are ignored when working out what is already covered, which keeps the derivation
    idempotent: the same archive always produces the same files.

    One unreadable date never costs the others. Returns the paths actually written.
    """
    dates = sorted({f.stem[:10] for f in read.files_for("estimates")})[-limit_dates:]
    if not dates:
        log.info("no estimates snapshots on disk; nothing to capture as vintages")
        return []
    covered = {
        (r.get("ticker"), r.get("period_end"), r.get("as_of"))
        for r in read.rows(read.load_history("vintages", limit_dates=max(limit_dates, 60)))
        if r.get("origin") != SNAPSHOT_ORIGIN
    }
    written: list[str] = []
    for d in dates:
        try:
            rows = [
                row
                for row in snapshot_vintage_rows(read.rows(read.load("estimates", date=d)), d)
                if (row["ticker"], row["period_end"], d) not in covered
            ]
            if not rows:
                continue
            frame = schema.vintages_frame(rows)
            schema.VINTAGES_SCHEMA.validate(frame)
            path = read.VAL_DIR / "vintages" / f"{d}-snapshot.parquet"
            if path.exists() and pl.read_parquet(path).equals(frame):
                continue
            write_parquet(frame, path)
            written.append(str(path))
            log.info("captured %d snapshot vintages for %s", frame.height, d)
        except Exception:  # noqa: BLE001 - one bad date must not cost the other dates
            log.exception("could not capture snapshot vintages for %s", d)
    return written


# ---------------------------------------------------------------------------------------------
# Item 2
# ---------------------------------------------------------------------------------------------


def _direction(prev: float, cur: float) -> str:
    """Up, down or unchanged, where "unchanged" absorbs moves smaller than REVISION_EPSILON."""
    if prev == 0:
        return "unchanged"
    move = cur / abs(prev) - (1.0 if prev > 0 else -1.0)
    if move > REVISION_EPSILON:
        return "up"
    if move < -REVISION_EPSILON:
        return "down"
    return "unchanged"


def _nearest_within(dates: list[str], anchor: str, target_days: int, tolerance: int) -> str | None:
    """The date closest to ``anchor + target_days``, or None if nothing lands inside the tolerance.

    Taking the *first* date at least N days out is not the same test: on a weekly grid "at least 21
    days" always resolves to exactly 21, so a plan that says four weeks gets scored on three.
    """
    anchor_d = date.fromisoformat(anchor)
    candidates = [(abs((date.fromisoformat(d) - anchor_d).days - target_days), d) for d in dates]
    candidates = [(gap, d) for gap, d in candidates if gap <= tolerance]
    return min(candidates)[1] if candidates else None


def persistence_triples(all_dates: list[str]) -> list[tuple[str, str, str]]:
    """(week start, week end, four weeks later) date triples the registered protocol can use.

    The first leg is the week-on-week change that does the grouping; the second is the four weeks
    that follow it. A grid point that has no partner at either distance produces no triple, which is
    why an irregular vintage grid (Yahoo publishes 7/30/60/90 days back) yields nothing until weekly
    snapshots fill it in.
    """
    out: list[tuple[str, str, str]] = []
    for i, d_now in enumerate(all_dates):
        earlier = _nearest_within(all_dates[:i], d_now, -STEP_DAYS, STEP_TOLERANCE_DAYS)
        later = _nearest_within(all_dates[i + 1 :], d_now, HORIZON_DAYS, HORIZON_TOLERANCE_DAYS)
        if earlier and later:
            out.append((earlier, d_now, later))
    return out


def _gamma_p_series(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x) by its series expansion."""
    term = 1.0 / a
    total = term
    ap = a
    for _ in range(500):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_q_cf(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x) by its continued fraction."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, df: int) -> float | None:
    """P(chi-square with `df` degrees of freedom > x). No scipy in this project, so it is spelled out."""
    if df <= 0:
        return None
    if x <= 0:
        return 1.0
    a, xx = df / 2.0, x / 2.0
    return 1.0 - _gamma_p_series(a, xx) if xx < a + 1.0 else _gamma_q_cf(a, xx)


def chi_square_independence(matrix: dict[str, dict[str, int]]) -> dict:
    """Chi-square test of "next-period direction is independent of this-period direction".

    Rows and columns that are entirely empty carry no information and are dropped before the degrees
    of freedom are counted, so a pool where nothing was ever downgraded is not credited with a
    dimension it does not have.
    """
    states = list(matrix)
    row_tot = {a: sum(matrix[a].values()) for a in states}
    col_tot = {b: sum(matrix[a][b] for a in states) for b in states}
    rows = [a for a in states if row_tot[a]]
    cols = [b for b in states if col_tot[b]]
    n = sum(row_tot.values())
    df = (len(rows) - 1) * (len(cols) - 1)
    if not n or df <= 0:
        return {
            "statistic": None,
            "df": df,
            "p_value": None,
            "min_expected": None,
            "reliable": False,
            "why": "the pooled table has no spare degree of freedom: every transition sits in one row or one column",
        }
    stat = 0.0
    min_expected = None
    for a in rows:
        for b in cols:
            expected = row_tot[a] * col_tot[b] / n
            min_expected = expected if min_expected is None else min(min_expected, expected)
            stat += (matrix[a][b] - expected) ** 2 / expected
    reliable = min_expected is not None and min_expected >= MIN_EXPECTED_CELL
    return {
        "statistic": stat,
        "df": df,
        "p_value": chi2_sf(stat, df),
        "min_expected": min_expected,
        "reliable": reliable,
        "why": None
        if reliable
        else f"the thinnest expected cell is {min_expected:.2f}, below {MIN_EXPECTED_CELL:.0f}; "
        "the asymptotic p-value is not dependable at that count",
    }


def revision_persistence(vintages_by_ticker: dict[str, list[dict]]) -> dict:
    """Item 2. A consensus that moved one way this week - does it move the same way four weeks on?"""
    series: dict[str, dict[str, float]] = {}
    for ticker, all_rows in vintages_by_ticker.items():
        rows = [r for r in all_rows if r.get("origin") in REVISION_ORIGINS]
        if not rows:
            continue
        cal = vintage_estimates(rows, YEARS)
        got = {as_of: by_year[YEARS[0]] for as_of, by_year in cal.items() if YEARS[0] in by_year}
        if len(got) >= 2:
            series[ticker] = got

    all_dates = sorted({d for s in series.values() for d in s})
    triples = persistence_triples(all_dates)
    if len(triples) < MIN_PERIODS_FOR_PERSISTENCE:
        # Counted off the rows rather than off `series`, which drops a listing until it has two dates:
        # "0 dates across 0 listings" would be true and would tell the reader nothing about why.
        revision_rows = [
            r for rows in vintages_by_ticker.values() for r in rows if r.get("origin") in REVISION_ORIGINS
        ]
        n_dates = len({r.get("as_of") for r in revision_rows if r.get("as_of")})
        n_listings = len({r.get("ticker") for r in revision_rows})
        n_snapshot = len({r.get("ticker") for r in revision_rows if r.get("origin") == SNAPSHOT_ORIGIN})
        return _not_yet(
            f"{n_dates} true-revision vintage {'date' if n_dates == 1 else 'dates'} on file across "
            f"{n_listings} listings, giving {len(triples)} week/four-week windows where "
            f"{MIN_PERIODS_FOR_PERSISTENCE} are needed. East Money's rebuilt series is excluded because "
            "its mean moves with broker coverage, not only with revisions; the weekly snapshot series "
            f"that replaces it for those listings now covers {n_snapshot} of them and grows by one date "
            "a week"
        )

    transitions: Counter[tuple[str, str]] = Counter()
    used_periods = 0
    dropped_periods = 0
    for d_prev, d_now, d_next in triples:
        movers = 0
        pairs: list[tuple[str, str]] = []
        for s in series.values():
            if not {d_prev, d_now, d_next} <= set(s):
                continue
            first = _direction(s[d_prev], s[d_now])
            second = _direction(s[d_now], s[d_next])
            if first != "unchanged":
                movers += 1
            pairs.append((first, second))
        if movers < MIN_MOVERS_PER_PERIOD:
            dropped_periods += 1
            continue
        transitions.update(pairs)
        used_periods += 1

    if not transitions:
        return _not_yet(
            f"no window yet has {MIN_MOVERS_PER_PERIOD} listings whose consensus moved; "
            f"{dropped_periods} windows were dropped for being too quiet"
        )

    states = ("up", "unchanged", "down")
    matrix = {a: {b: transitions.get((a, b), 0) for b in states} for a in states}
    totals = {a: sum(matrix[a].values()) for a in states}
    return {
        "status": "partial",
        "periods_used": used_periods,
        "periods_dropped_as_quiet": dropped_periods,
        "n_transitions": sum(transitions.values()),
        "step_days": STEP_DAYS,
        "horizon_days": HORIZON_DAYS,
        "matrix": matrix,
        "row_shares": {a: {b: (matrix[a][b] / totals[a] if totals[a] else None) for b in states} for a in states},
        "chi_square": chi_square_independence(matrix),
        "note": "a short history, and the same listing contributes to overlapping windows, so the "
        "transitions are not independent observations and the p-value is optimistic. Reported as a "
        "description of what has happened, not as evidence of persistence",
    }


# ---------------------------------------------------------------------------------------------
# Items 3 and 4
# ---------------------------------------------------------------------------------------------


def valuation_dispersion(track_facts: dict) -> dict:
    """Item 3. The interquartile range of forward PE, with the count that produced it."""
    return {
        "status": "partial",
        "as_of": track_facts.get("generated_at"),
        "iqr": track_facts.get("iqr_fwd_pe_this"),
        "p25": track_facts.get("p25_fwd_pe_this"),
        "p75": track_facts.get("p75_fwd_pe_this"),
        "median": track_facts.get("median_fwd_pe_this"),
        "n_priced": track_facts.get("n_with_consensus"),
        "n_listings": track_facts.get("n_listings"),
        "note": "one observation so far; the series needs quarter ends to answer whether the spread narrows",
    }


def dual_listing_premium(track_facts: dict) -> dict:
    """Item 4. Same earnings, two prices - the control for item 3."""
    rows = [d for d in track_facts.get("dual_listings", []) if d.get("premium") is not None]
    if not rows:
        return _not_yet("no dual-listed issuer in this track has a forward PE on both lines yet")
    return {
        "status": "partial",
        "as_of": track_facts.get("generated_at"),
        "pairs": rows,
        "median_premium": statistics.median(d["premium"] for d in rows),
        "note": "the premium is the A line over the H line on identical reported earnings",
    }


# ---------------------------------------------------------------------------------------------
# Item 5
# ---------------------------------------------------------------------------------------------

#: a name containing one of these is an aggregate, a total or a sentence, never a single company
_AGGREGATE_CHARS = "+&,/;、／"
_AGGREGATE_WORDS = (
    "combined",
    "total",
    "industry",
    "market",
    "top",
    "share",
    "suppliers",
    "makers",
    "vendors",
    "average",
    "chinese",
    "korean",
    "japanese",
)
#: a name the curator worked out rather than the publisher printed. Scoring it would turn our own
#: inference into a third-party citation, which is the one thing basis B exists to avoid.
_NOT_PUBLISHED = ("inferred", "not stated")
_NUM = r"\d+(?:\.\d+)?"
#: a signed percentage is a growth rate or a prior-period share, never the share being cited
_PCT_RE = re.compile(rf"([+\-−→]?)\s*({_NUM})\s*%")
#: the first figure in a clause, with the unit token that follows it ("289.6 GWh", "5 bn", "39.9 %")
_FIGURE_RE = re.compile(rf"([+\-−→]?)\s*({_NUM})\s*(%|[A-Za-z$]{{1,6}})?")
#: "1 Innolight", "rank 4 Accelink" - an ordered list, which is the form most optical sources publish in
_RANK_RE = re.compile(r"(?:^|[;:])\s*(?:rank\s*)?(\d{1,2})[.)]?\s+([^;]+)")
#: one company per clause. A full stop before a capital ends a clause as surely as a semicolon does,
#: and a decimal point never does, because a digit is not a capital letter.
_CLAUSE_RE = re.compile(r"[;；]|\.\s+(?=[A-Z一-鿿])")


def _clean_name(raw: str) -> str:
    """Drop parentheticals and trailing prose, normalise whitespace, lowercase."""
    name = re.sub(r"[(（].*?[)）]", " ", raw)
    name = re.split(r"[—–]|--", name)[0]  # an em dash starts a comment, not a name
    return re.sub(r"\s+", " ", name).strip(" .·:-").lower()


def _member_aliases(members: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in members:
        for alias in (m.get("name"), m.get("name_cn")):
            if alias:
                out[str(alias).strip().lower()] = m
    return out


def _is_token_prefix(short: str, long: str) -> bool:
    return long == short or long.startswith(short + " ")


def resolve_member(raw: str, aliases: dict[str, dict]) -> dict | None:
    """The one pool member a citation's name refers to, or None.

    Deliberately strict. A bare substring match is what lets "CATL+BYD combined SHARE 54.6%" bind its
    combined figure to CATL, and "REPT 16.9 GWh (+118.5%, new entrant displacing Sunwoda)" bind REPT's
    volume to Sunwoda. So: an exact alias wins outright; otherwise anything carrying a conjunction or
    an aggregate word is refused before matching is attempted; what is left has to be a whole-word
    prefix of exactly one member, in one direction or the other.
    """
    if any(marker in raw.lower() for marker in _NOT_PUBLISHED):
        return None
    name = _clean_name(raw)
    if not name:
        return None
    if name in aliases:
        return aliases[name]
    tokens = re.split(r"[\s+&,/;、／]+", name)
    if len(tokens) > MAX_NAME_TOKENS:
        return None
    if any(ch in name for ch in _AGGREGATE_CHARS) or any(w in tokens for w in _AGGREGATE_WORDS):
        return None
    hits = {
        id(m): m
        for alias, m in aliases.items()
        if _is_token_prefix(name, alias) or _is_token_prefix(alias, name)
    }
    return next(iter(hits.values())) if len(hits) == 1 else None


def _unsigned_percent(text: str) -> float | None:
    """The first percentage that is not a change and not an arrow's right-hand side."""
    for m in _PCT_RE.finditer(text):
        if not m.group(1):
            return float(m.group(2))
    return None


def _first_figure(text: str) -> tuple[float, str] | None:
    """The first unsigned number in the text with the unit token that follows it."""
    for m in _FIGURE_RE.finditer(text):
        if m.group(1):
            continue
        return float(m.group(2)), (m.group(3) or "").strip()
    return None


def citation_figures(citation: dict, aliases: dict[str, dict]) -> list[dict]:
    """Every (pool member, figure) pair a single citation states, with the text it came from.

    Three forms are read, and nothing else is guessed at:

    * an ordered list - ``1 Innolight; 2 Eoptolink; ...`` - which yields a rank per company;
    * a clause per company - ``CATL 289.6 GWh / 39.9% share`` - which yields a figure and, when the
      citation states one, a percentage share;
    * a citation whose ``entity`` is itself one pool member, where the first unsigned percentage in
      the value is that company's cited share.

    Only ``value`` is parsed. ``period`` on the LightCounting row carries a second, older ranking, and
    reading it would silently mix two years into one comparison.
    """
    value = str(citation.get("value") or "")
    out: list[dict] = []
    seen: set[str] = set()

    for m in _RANK_RE.finditer(value):
        member = resolve_member(m.group(2), aliases)
        if member and member["ticker"] not in seen:
            seen.add(member["ticker"])
            out.append({"member": member, "rank": int(m.group(1)), "text": m.group(0).strip(" ;:")})
    if out:
        return out

    for clause in _CLAUSE_RE.split(value):
        head = re.split(rf"(?={_NUM})", clause, maxsplit=1)
        if len(head) < 2:
            continue
        member = resolve_member(head[0], aliases)
        if not member or member["ticker"] in seen:
            continue
        figure = _first_figure(clause[len(head[0]) :])
        if figure is None:
            continue
        seen.add(member["ticker"])
        out.append(
            {
                "member": member,
                "figure": figure[0],
                "unit": figure[1],
                "percent": _unsigned_percent(clause),
                "text": clause.strip(),
            }
        )
    if out:
        return out

    member = resolve_member(str(citation.get("entity") or ""), aliases)
    percent = _unsigned_percent(value)
    if member and percent is not None:
        out.append({"member": member, "percent": percent, "unit": "%", "figure": percent, "text": value.strip()})
    return out


def _midranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged, so a tie cannot invent an ordering."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rho: Pearson on midranks. None when there are too few pairs or one side is flat."""
    if len(xs) != len(ys) or len(xs) < MIN_RANK_PAIRS:
        return None
    rx, ry = _midranks(xs), _midranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _comparison(citation: dict, figures: list[dict], basis: str) -> dict | None:
    """One citation, one basis, as a scored comparison against the computed pool shares."""
    pairs = []
    units: set[str] = set()
    for f in figures:
        member = f["member"]
        if member.get("share") is None:
            continue
        if basis == "rank":
            cited_value, cited_rank, difference = -float(f["rank"]), f["rank"], None
        elif basis == "share":
            if f.get("percent") is None:
                continue
            cited_value, cited_rank = f["percent"] / 100.0, None
            difference = member["share"] - cited_value
        else:
            if f.get("figure") is None:
                continue
            cited_value, cited_rank, difference = f["figure"], None, None
            units.add(str(f.get("unit") or ""))
        pairs.append(
            {
                "name": member["name"],
                "ticker": member["ticker"],
                "computed_share": member["share"],
                "cited_value": cited_value,
                "cited_rank": cited_rank,
                "difference": difference,
                "cited_text": f["text"],
            }
        )
    if not pairs:
        return None
    if basis == "quantity" and len({u.lower() for u in units}) != 1:
        return None  # GWh against USD bn is not an ordering, it is a mix
    rho = spearman([p["computed_share"] for p in pairs], [p["cited_value"] for p in pairs])
    diffs = [abs(p["difference"]) for p in pairs if p["difference"] is not None]
    return {
        "basis": basis,
        "unit": "rank" if basis == "rank" else ("%" if basis == "share" else next(iter(units), "")),
        "source_name": citation.get("source_name"),
        "source_url": citation.get("source_url"),
        "period": citation.get("period"),
        "n": len(pairs),
        "spearman": rho,
        "median_abs_difference": statistics.median(diffs) if diffs else None,
        "pairs": pairs,
    }


def comparisons_for(track_facts: dict, cited: list[dict]) -> list[dict]:
    """Every usable comparison between the computed pool and the cited sources, most pairs first."""
    aliases = _member_aliases(track_facts.get("share", {}).get("members", []))
    out: list[dict] = []
    for citation in cited:
        figures = citation_figures(citation, aliases)
        if not figures:
            continue
        for basis in ("rank", "share", "quantity"):
            if basis == "rank" and not any("rank" in f for f in figures):
                continue
            if basis != "rank" and any("rank" in f for f in figures):
                continue
            comparison = _comparison(citation, figures, basis)
            if not comparison:
                continue
            # a lone figure with no share attached compares nothing
            if comparison["basis"] != "share" and comparison["n"] < MIN_RANK_PAIRS:
                continue
            out.append(comparison)
    # deterministic: most pairs first, then by source name, so which comparison is scored never
    # depends on dictionary order or on the order the reference file happens to be written in
    return sorted(out, key=lambda c: (-c["n"], str(c["source_name"] or "")))


def share_bases(track: str, track_facts: dict) -> dict:
    """Item 5. Computed pool share against published third-party share or ranking, where both exist."""
    label = str(TRACKS.get(track, {}).get("label", track))
    cited = cited_share(load_reference(track))
    comparisons = comparisons_for(track_facts, cited)
    ranked = next((c for c in comparisons if c["spearman"] is not None), None)
    shares = next((c for c in comparisons if c["basis"] == "share"), None)
    if not ranked and not shares:
        named = sum(1 for c in cited if citation_figures(c, _member_aliases(
            track_facts.get("share", {}).get("members", []))))
        return _not_yet(
            f"{len(cited)} cited figures on file for {label}; {named} of them name a company in the "
            f"computed pool, and none states a share or an ordering for {MIN_RANK_PAIRS} of them at "
            "once, which is what a rank correlation needs"
        )
    return {
        "status": "partial",
        "rank_correlation": None
        if not ranked
        else {
            "spearman": ranked["spearman"],
            "n": ranked["n"],
            "basis": ranked["basis"],
            "unit": ranked["unit"],
            "source_name": ranked["source_name"],
            "source_url": ranked["source_url"],
            "period": ranked["period"],
        },
        "rank_correlation_why": None
        if ranked
        else f"no single citation orders {MIN_RANK_PAIRS} pool members on one basis",
        "share_difference": None
        if not shares
        else {
            "median_abs_difference": shares["median_abs_difference"],
            "n": shares["n"],
            "source_name": shares["source_name"],
            "source_url": shares["source_url"],
            "period": shares["period"],
            "pairs": shares["pairs"],
        },
        "share_difference_why": None
        if shares
        else f"no cited source states a percentage share for a {label} pool member",
        "comparisons": comparisons,
        "unexplained": {
            "private_vendors": [e["name"] for e in track_facts.get("excluded_from_pool", [])],
            "no_segment_disclosure": [e["name"] for e in track_facts.get("share", {}).get("excluded", [])],
        },
    }


# ---------------------------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------------------------


def build() -> dict:
    now = utc_now()
    captured = capture_snapshot_vintages()
    vintages = read.by_ticker(read.load_history("vintages", limit_dates=60))
    out: dict = {
        "generated_at": now.isoformat(),
        "plan": "docs/EVALUATION_PLAN.md",
        "snapshot_vintages_written": captured,
        "tracks": {},
    }
    for track in TRACKS:
        facts_path = MARTS_DIR.parent / "facts" / f"valuation_{track}.json"
        if not facts_path.exists():
            out["tracks"][track] = {"status": "not_yet", "why": "the track has not been published yet"}
            continue
        track_facts = json.loads(facts_path.read_text(encoding="utf-8"))
        tickers = {c.ticker for c in COMPANIES if c.track == track}
        out["tracks"][track] = {
            "consensus_accuracy": consensus_accuracy(),
            "revision_persistence": revision_persistence({t: v for t, v in vintages.items() if t in tickers}),
            "valuation_dispersion": valuation_dispersion(track_facts),
            "dual_listing_premium": dual_listing_premium(track_facts),
            "share_bases": share_bases(track, track_facts),
        }
    write_json(out, MART_DIR / "evaluation.json")
    return out


def main() -> int:
    setup_logging()
    out = build()
    for track, items in out["tracks"].items():
        if isinstance(items, dict) and "status" in items:
            log.info("%s: %s", track, items.get("why"))
            continue
        for name, res in items.items():
            log.info("%s / %s: %s", track, name, res.get("status"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
