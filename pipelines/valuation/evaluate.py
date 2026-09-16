"""Score the pre-registered evaluation items for the two valuation pages.

Usage:
    uv run python -m pipelines.valuation.evaluate

Writes ``data/marts/valuation/evaluation.json``.

The rules are fixed in ``docs/EVALUATION_PLAN.md`` and were committed before the first snapshot was
published; this module only applies them. Items that cannot be scored yet return
``{"status": "not_yet", "why": ...}`` rather than a placeholder number, and the pages print the reason.

Item 1 (consensus accuracy) cannot run until the 2026 annual reports land in 2027. Items 2 to 5 can be
computed from what is already on disk, but every one of them reports how many periods it is based on,
because four weeks of history is a description, not a finding.
"""

from __future__ import annotations

import logging
import statistics
import sys
from collections import Counter
from datetime import date

from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, utc_now, write_json
from pipelines.valuation import read
from pipelines.valuation.config import COMPANIES, TRACKS
from pipelines.valuation.metrics import vintage_estimates
from pipelines.valuation.share import cited_share, load_reference

log = logging.getLogger("valuation.evaluate")

YEARS = (2026, 2027)
MART_DIR = MARTS_DIR / "valuation"

# An estimate must move by more than this to count as a revision rather than as rounding.
REVISION_EPSILON = 0.005
MIN_MOVERS_PER_PERIOD = 10
MIN_PERIODS_FOR_PERSISTENCE = 3


def _not_yet(why: str) -> dict:
    return {"status": "not_yet", "why": why}


def consensus_accuracy() -> dict:
    """Item 1. Needs reported calendar-2026 EPS, which does not exist until the 2027 filing season."""
    return _not_yet(
        "the calendar-2026 consensus is scored against reported 2026 results; the last A-share and "
        "Japanese filers report in spring 2027, so this runs from May 2027"
    )


# Only these vintage origins are true revisions of a fixed analyst panel. "eastmoney_rebuilt" is a
# coverage series: its mean moves when a new broker starts covering the stock, which is not a revision.
REVISION_ORIGINS = ("yahoo_trend", "snapshot")


def revision_persistence(vintages_by_ticker: dict[str, list[dict]]) -> dict:
    """Item 2. Does a consensus that moved up keep moving up four weeks later?"""
    # calendar-year consensus per ticker per vintage date
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
    if len(all_dates) < MIN_PERIODS_FOR_PERSISTENCE + 1:
        return _not_yet(
            f"only {len(all_dates)} true-revision vintage dates on file across "
            f"{len(series)} listings; East Money's rebuilt series is excluded because its mean moves "
            "with broker coverage, not only with revisions, so the A-shares contribute nothing here "
            "until our own weekly snapshots accumulate"
        )

    def direction(prev: float, cur: float) -> str:
        if prev == 0:
            return "unchanged"
        move = cur / abs(prev) - (1.0 if prev > 0 else -1.0)
        if move > REVISION_EPSILON:
            return "up"
        if move < -REVISION_EPSILON:
            return "down"
        return "unchanged"

    # pair each transition with the transition roughly four weeks later
    transitions: Counter[tuple[str, str]] = Counter()
    used_periods = 0
    dropped_periods = 0
    for i in range(1, len(all_dates) - 1):
        d_prev, d_now = all_dates[i - 1], all_dates[i]
        later = [d for d in all_dates[i + 1 :] if (date.fromisoformat(d) - date.fromisoformat(d_now)).days >= 21]
        if not later:
            continue
        d_next = later[0]
        movers = 0
        pairs: list[tuple[str, str]] = []
        for s in series.values():
            if not {d_prev, d_now, d_next} <= set(s):
                continue
            first = direction(s[d_prev], s[d_now])
            second = direction(s[d_now], s[d_next])
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
            f"no period yet has {MIN_MOVERS_PER_PERIOD} listings whose consensus moved; "
            f"{dropped_periods} periods were dropped for being too quiet"
        )

    states = ("up", "unchanged", "down")
    matrix = {a: {b: transitions.get((a, b), 0) for b in states} for a in states}
    totals = {a: sum(matrix[a].values()) for a in states}
    return {
        "status": "partial",
        "periods_used": used_periods,
        "periods_dropped_as_quiet": dropped_periods,
        "n_transitions": sum(transitions.values()),
        "matrix": matrix,
        "row_shares": {a: {b: (matrix[a][b] / totals[a] if totals[a] else None) for b in states} for a in states},
        "note": "a short history; reported as a description of what has happened, not as evidence of persistence",
    }


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
    """Item 4. Same earnings, two prices — the control for item 3."""
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


def share_bases(track: str, track_facts: dict) -> dict:
    """Item 5. Computed pool share against published third-party share, where both exist."""
    cited = cited_share(load_reference(track))
    members = {m["name"].lower(): m for m in track_facts.get("share", {}).get("members", [])}
    matched = []
    for c in cited:
        if c.get("unit") not in (None, "", "%", "percent", "share"):
            continue
        key = str(c.get("entity", "")).lower()
        hit = next((m for name, m in members.items() if key and (key in name or name in key)), None)
        if not hit:
            continue
        try:
            cited_value = float(str(c["value"]).strip().rstrip("%")) / 100.0
        except (TypeError, ValueError):
            continue
        matched.append({
            "entity": c["entity"],
            "computed_share": hit["share"],
            "cited_share": cited_value,
            "difference": hit["share"] - cited_value,
            "source_name": c.get("source_name"),
            "source_url": c.get("source_url"),
            "period": c.get("period"),
        })
    if not matched:
        return _not_yet(
            f"{len(cited)} cited figures on file, none of them a percentage share that matches a company "
            "in the computed pool; most public optical sources publish rankings rather than shares"
        )
    return {
        "status": "partial",
        "n_matched": len(matched),
        "median_abs_difference": statistics.median(abs(m["difference"]) for m in matched),
        "pairs": matched,
        "unexplained": {
            "private_vendors": [e["name"] for e in track_facts.get("excluded_from_pool", [])],
            "no_segment_disclosure": [e["name"] for e in track_facts.get("share", {}).get("excluded", [])],
        },
    }


def build() -> dict:
    now = utc_now()
    vintages = read.by_ticker(read.load_history("vintages", limit_dates=60))
    out: dict = {"generated_at": now.isoformat(), "plan": "docs/EVALUATION_PLAN.md", "tracks": {}}
    for track in TRACKS:
        facts_path = MARTS_DIR.parent / "facts" / f"valuation_{track}.json"
        if not facts_path.exists():
            out["tracks"][track] = {"status": "not_yet", "why": "the track has not been published yet"}
            continue
        import json

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
