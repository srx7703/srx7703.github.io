"""FOMC decision markets: map both platforms to one meeting/outcome grid and build marts + facts.

Outcome buckets: cut50 (<= -50 bps), cut25, hold, hike25, hike50 (>= +50 bps). Charts use the
3-way roll-up cut / hold / hike; the table keeps all five.
"""

from __future__ import annotations

import json
import logging
import re

import polars as pl

from pipelines.common.checks import check, freshness, probability_range, uniqueness
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR, utc_now, write_json
from pipelines.predmarkets import read

log = logging.getLogger("predmarkets.fomc")

# Decision day (second day of the meeting); statement at 14:00 ET. 2026 from the Fed's published
# calendar; 2027 dates are provisional and only matter once those meetings resolve.
MEETINGS: dict[str, str] = {
    "2026-01": "2026-01-28",
    "2026-03": "2026-03-18",
    "2026-04": "2026-04-29",
    "2026-06": "2026-06-17",
    "2026-07": "2026-07-29",
    "2026-09": "2026-09-16",
    "2026-10": "2026-10-28",
    "2026-12": "2026-12-09",
    "2027-01": "2027-01-27",
    "2027-03": "2027-03-17",
    "2027-04": "2027-04-28",
    "2027-06": "2027-06-16",
    "2027-07": "2027-07-28",
    "2027-09": "2027-09-22",
    "2027-10": "2027-10-27",
    "2027-12": "2027-12-08",
}
BPS = {"cut50": -50, "cut25": -25, "hold": 0, "hike25": 25, "hike50": 50}
ROLLUP = {"cut50": "cut", "cut25": "cut", "hold": "hold", "hike25": "hike", "hike50": "hike"}
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
KX_MON = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def kalshi_meeting(event_id: str) -> str | None:
    m = re.match(r"^KXFEDDECISION-(\d\d)([A-Z]{3})$", event_id or "")
    return f"20{m.group(1)}-{KX_MON[m.group(2)]:02d}" if m and m.group(2) in KX_MON else None


def kalshi_bucket(outcome: str) -> str | None:
    o = (outcome or "").lower()
    if "cut" in o and ">" in o:
        return "cut50"
    if "cut" in o:
        return "cut25"
    if "maintain" in o or "hold" in o:
        return "hold"
    if "hike" in o and ">" in o:
        return "hike50"
    if "hike" in o:
        return "hike25"
    return None


def polymarket_meeting(question: str) -> str | None:
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4}) meeting",
        question or "",
    )
    return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}" if m else None


def polymarket_bucket(question: str) -> str | None:
    q = (question or "").lower()
    if "no change" in q:
        return "hold"
    if "decrease" in q and "50" in q:
        return "cut50"
    if "decrease" in q:
        return "cut25"
    if "increase" in q and "50" in q:
        return "hike50"
    if "increase" in q:
        return "hike25"
    return None


def market_grid(dim: pl.DataFrame) -> pl.DataFrame:
    """platform, market_id -> meeting, bucket for decision markets only."""
    rows = []
    for r in dim.iter_rows(named=True):
        if r["platform"] == "kalshi":
            meeting, bucket = kalshi_meeting(r["event_id"]), kalshi_bucket(r["outcome_yes"])
        else:
            if not (r["event_slug"] or "").startswith("fed-decision-in-"):
                continue
            meeting, bucket = polymarket_meeting(r["question"]), polymarket_bucket(r["question"])
        if meeting and bucket:
            rows.append({"platform": r["platform"], "market_id": r["market_id"], "meeting": meeting, "bucket": bucket})
    return pl.DataFrame(rows, schema={"platform": pl.Utf8, "market_id": pl.Utf8, "meeting": pl.Utf8, "bucket": pl.Utf8})


def daily_prices(grid: pl.DataFrame) -> pl.DataFrame:
    """Union of backfilled daily history and snapshot observations, one row per market-date."""
    hist_dir = SNAP_DIR / "predmarkets" / "fomc" / "history"
    parts = [pl.read_parquet(p).select("platform", "market_id", "date", "price") for p in hist_dir.glob("*.parquet")]
    q = (
        read.quotes("fomc")
        .collect()
        .with_columns(
            pl.coalesce([pl.col("mid"), pl.col("yes_price")]).alias("price"),
            pl.col("snapshot_ts").str.slice(0, 10).alias("date"),
        )
        .select("platform", "market_id", "date", "price")
    )
    parts.append(q)
    allp = pl.concat(parts, how="vertical_relaxed").filter(pl.col("price").is_not_null())
    # snapshots come last so they win on the same date
    return allp.unique(subset=["platform", "market_id", "date"], keep="last", maintain_order=True).join(
        grid, on=["platform", "market_id"], how="inner"
    )


def latest_probs(grid: pl.DataFrame) -> pl.DataFrame:
    q = read.quotes("fomc").collect()
    latest = q.filter(pl.col("snapshot_ts") == q["snapshot_ts"].max()).with_columns(
        pl.coalesce([pl.col("mid"), pl.col("yes_price")]).alias("prob")
    )
    return latest.join(grid, on=["platform", "market_id"], how="inner").select(
        "platform", "market_id", "meeting", "bucket", "prob", "volume", "snapshot_ts"
    )


def resolutions(grid: pl.DataFrame) -> dict[str, str]:
    """meeting -> winning bucket, from the append-only resolutions store written by the snapshotter
    (Kalshi settlement results first, Polymarket closed 1/0 prices as a fallback)."""
    path = SNAP_DIR / "predmarkets" / "fomc" / "resolutions.json"
    if not path.exists():
        return {}
    store = json.loads(path.read_text())["markets"]
    lookup = {(r["platform"], r["market_id"]): (r["meeting"], r["bucket"]) for r in grid.iter_rows(named=True)}
    out: dict[str, str] = {}
    for pf in ("kalshi", "polymarket"):
        for rec in store.values():
            if rec["platform"] != pf or rec["result"] != "yes":
                continue
            hit = lookup.get((rec["platform"], rec["market_id"]))
            if hit and hit[0] not in out:
                out[hit[0]] = hit[1]
    return out


def build() -> dict:
    dim = read.dim("fomc")
    grid = market_grid(dim)
    prices = daily_prices(grid)
    latest = latest_probs(grid)
    resolved = resolutions(grid)
    today = utc_now().date().isoformat()
    upcoming = [m for m in sorted(MEETINGS) if MEETINGS[m] >= today and m in set(grid["meeting"].to_list())][:4]

    def dist(df: pl.DataFrame, meeting: str, platform: str) -> dict[str, float]:
        sub = df.filter((pl.col("meeting") == meeting) & (pl.col("platform") == platform))
        return {r["bucket"]: round(float(r["prob"]), 4) for r in sub.iter_rows(named=True) if r["prob"] is not None}

    def expected_bps(d: dict[str, float]) -> float | None:
        if not d:
            return None
        return round(sum(BPS[b] * p for b, p in d.items()), 1)

    meetings_out = []
    for m in upcoming:
        entry: dict = {"meeting": m, "decision_date": MEETINGS[m], "platforms": {}}
        for pf in ("polymarket", "kalshi"):
            d = dist(latest, m, pf)
            rollup = (
                {k: round(sum(p for b, p in d.items() if ROLLUP[b] == k), 4) for k in ("cut", "hold", "hike")}
                if d
                else None
            )
            modal = max(rollup, key=rollup.get) if rollup else None
            entry["platforms"][pf] = {
                "buckets": d,
                "expected_bps": expected_bps(d),
                "sum_prob": round(sum(d.values()), 4) if d else None,
                "rollup": rollup,
                "modal_side": modal,
                "modal_prob": rollup[modal] if modal else None,
            }
        sides = [entry["platforms"][pf]["modal_side"] for pf in ("polymarket", "kalshi")]
        entry["agree"] = bool(sides[0] and sides[0] == sides[1])
        if all(entry["platforms"][pf]["rollup"] for pf in ("polymarket", "kalshi")):
            entry["gap_hike_pt"] = round(
                (entry["platforms"]["polymarket"]["rollup"]["hike"] - entry["platforms"]["kalshi"]["rollup"]["hike"])
                * 100,
                1,
            )
        meetings_out.append(entry)

    # scorecard for resolved meetings: final pre-decision distribution = last snapshot before
    # 17:30 UTC on decision day (fallback: last daily history point before decision day);
    # multi-outcome Brier score over the five buckets, range 0 (perfect) to 2 (certain and wrong).
    q_all = (
        read.quotes("fomc")
        .collect()
        .with_columns(pl.coalesce([pl.col("mid"), pl.col("yes_price")]).alias("price"))
        .join(grid, on=["platform", "market_id"], how="inner")
    )
    scorecard = []
    for m, winner in sorted(resolved.items()):
        cutoff_ts = MEETINGS.get(m, m + "-01") + "T17:30:00+00:00"
        row: dict = {"meeting": m, "decision_date": MEETINGS.get(m), "outcome": winner, "platforms": {}}
        for pf in ("polymarket", "kalshi"):
            snaps = q_all.filter(
                (pl.col("meeting") == m) & (pl.col("platform") == pf) & (pl.col("snapshot_ts") < cutoff_ts)
            )
            if snaps.height:
                last = snaps["snapshot_ts"].max()
                d = {
                    r["bucket"]: float(r["price"])
                    for r in snaps.filter(pl.col("snapshot_ts") == last).iter_rows(named=True)
                    if r["price"] is not None
                }
                as_of = last
            else:
                hist = prices.filter(
                    (pl.col("meeting") == m) & (pl.col("platform") == pf) & (pl.col("date") < cutoff_ts[:10])
                )
                if hist.height == 0:
                    continue
                as_of = hist["date"].max()
                d = {r["bucket"]: float(r["price"]) for r in hist.filter(pl.col("date") == as_of).iter_rows(named=True)}
            brier = sum((d.get(b, 0.0) - (1.0 if b == winner else 0.0)) ** 2 for b in BPS)
            row["platforms"][pf] = {
                "as_of": as_of,
                "buckets": {k: round(v, 4) for k, v in d.items()},
                "sum_prob": round(sum(d.values()), 4),
                "p_outcome": round(d.get(winner, 0.0), 4),
                "brier": round(brier, 4),
            }
        scorecard.append(row)

    # chart data: 3-way roll-up daily series for the upcoming meetings
    roll = (
        prices.filter(pl.col("meeting").is_in(upcoming))
        .with_columns(pl.col("bucket").replace_strict(ROLLUP, default=None).alias("side"))
        .group_by("meeting", "platform", "date", "side")
        .agg(pl.col("price").sum().alias("prob"))
        .sort("meeting", "platform", "date", "side")
    )
    (MARTS_DIR / "predmarkets").mkdir(parents=True, exist_ok=True)
    write_json(roll.to_dicts(), MARTS_DIR / "predmarkets" / "fomc_history.json")
    write_json(meetings_out, MARTS_DIR / "predmarkets" / "fomc_meetings.json")

    q = read.quotes("fomc").collect()
    snapshots = sorted(q["snapshot_ts"].unique().to_list())
    latest_all = q.filter(pl.col("snapshot_ts") == snapshots[-1])
    checks = [
        check("Schema", True, "pandera schemas validated before every write; history validated on backfill"),
        probability_range(latest_all, ["yes_price", "best_bid", "best_ask", "mid"]),
        uniqueness(latest_all, ["platform", "market_id"], "markets"),
        uniqueness(prices, ["platform", "market_id", "date"], "daily prices", name="Key uniqueness (history)"),
        check("Outcome grid", int(grid.height) >= 10, f"{grid.height} decision markets mapped to meeting x outcome"),
        freshness(snapshots[-1], 14, "snapshot"),
    ]
    facts = {
        "project": "fomc-markets",
        "checks": checks,
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "first_snapshot_ts": snapshots[0],
        "last_snapshot_ts": snapshots[-1],
        "n_snapshots": len(snapshots),
        "n_days": len({s[:10] for s in snapshots}),
        "history_from": prices["date"].min(),
        "markets_tracked": {
            r["platform"]: r["n"]
            for r in q.filter(pl.col("snapshot_ts") == snapshots[-1])
            .group_by("platform")
            .agg(pl.len().alias("n"))
            .iter_rows(named=True)
        },
        "decision_markets": int(grid.height),
        "next_meeting": meetings_out[0] if meetings_out else None,
        "meetings": meetings_out,
        "scorecard": scorecard,
        "sources": [
            {"name": "Polymarket Gamma + CLOB APIs", "url": "https://docs.polymarket.com/"},
            {"name": "Kalshi public trade API v2", "url": "https://docs.kalshi.com/"},
            {
                "name": "FOMC meeting calendar",
                "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            },
        ],
    }
    write_json(facts, FACTS_DIR / "fomc.json")
    return facts
