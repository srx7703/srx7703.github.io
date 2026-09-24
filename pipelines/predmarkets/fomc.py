"""FOMC decision markets: map both platforms to one meeting/outcome grid and build marts + facts.

Outcome buckets: cut50 (<= -50 bps), cut25, hold, hike25, hike50 (>= +50 bps). Charts use the
3-way roll-up cut / hold / hike; the table keeps all five.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

import polars as pl

from pipelines.common.checks import check, freshness, probability_range, uniqueness
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR, utc_now, write_json
from pipelines.predmarkets import read

log = logging.getLogger("predmarkets.fomc")

# Decision day (second day of the meeting); statement at 14:00 ET. 2025-2026 from the Fed's published
# calendar; 2027 dates are provisional and only matter once those meetings resolve. The 2025 meetings
# closed before the snapshot pipeline started and are recovered by `archive.py`.
MEETINGS: dict[str, str] = {
    "2025-01": "2025-01-29",
    "2025-03": "2025-03-19",
    "2025-05": "2025-05-07",
    "2025-06": "2025-06-18",
    "2025-07": "2025-07-30",
    "2025-09": "2025-09-17",
    "2025-10": "2025-10-29",
    "2025-12": "2025-12-10",
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
    "2027-06": "2027-06-09",
    "2027-07": "2027-07-28",
    "2027-09": "2027-09-15",
    "2027-10": "2027-10-27",
    "2027-12": "2027-12-08",
}
BPS = {"cut50": -50, "cut25": -25, "hold": 0, "hike25": 25, "hike50": 50}
GRID_SCHEMA = {"platform": pl.Utf8, "market_id": pl.Utf8, "meeting": pl.Utf8, "bucket": pl.Utf8}
ARCHIVE_DIR = SNAP_DIR / "predmarkets" / "fomc" / "archive"
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


# The ticker suffix is the stable key. The outcome label has been reworded twice ("No cut/hike" in
# late 2024, "No change" through June 2025, "Fed maintains rate" since), and none of those old labels
# contains "maintain" or "hold"; one even contains "cut".
KX_SUFFIX = {"C26": "cut50", "C25": "cut25", "H0": "hold", "H25": "hike25", "H26": "hike50"}


def kalshi_bucket(outcome: str, ticker: str | None = None) -> str | None:
    if ticker and "-" in ticker:
        suffix = ticker.rsplit("-", 1)[1]
        if suffix in KX_SUFFIX:
            return KX_SUFFIX[suffix]
    o = (outcome or "").lower()
    if "maintain" in o or "hold" in o or "no change" in o or "no cut" in o or o.startswith("hike 0"):
        return "hold"
    if "cut" in o and ">" in o:
        return "cut50"
    if "cut" in o:
        return "cut25"
    if "hike" in o and ">" in o:
        return "hike50"
    if "hike" in o:
        return "hike25"
    return None


_MONTH_RE = "(January|February|March|April|May|June|July|August|September|October|November|December)"


def polymarket_meeting(question: str) -> str | None:
    """ "after the March 2026 meeting", and the older "after 2024 May meeting" word order."""
    q = question or ""
    m = re.search(_MONTH_RE + r" (\d{4}) meeting", q)
    if m:
        return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    m = re.search(r"(\d{4}) " + _MONTH_RE + " meeting", q)
    return f"{m.group(1)}-{MONTHS[m.group(2).lower()]:02d}" if m else None


def polymarket_bucket(question: str) -> str | None:
    q = (question or "").lower()
    if "no change" in q:
        return "hold"
    if "decrease" in q and ("50" in q or "75" in q):
        return "cut50"
    if "decrease" in q:
        return "cut25"
    up = "increase" in q or "raise" in q
    if up and "50" in q:
        return "hike50"
    if up:
        return "hike25"
    return None


def is_decision_slug(slug: str | None) -> bool:
    """Polymarket's per-meeting decision events: "fed-decision-in-<month>[-<n>]" since March 2025,
    "fed-interest-rates-<month>-<yyyy>" before that."""
    s = slug or ""
    return s.startswith("fed-decision-in-") or bool(re.match(r"^fed-interest-rates-[a-z]+-\d{4}$", s))


def market_grid(dim: pl.DataFrame) -> pl.DataFrame:
    """platform, market_id -> meeting, bucket for decision markets only."""
    rows = []
    for r in dim.iter_rows(named=True):
        if r["platform"] == "kalshi":
            meeting, bucket = kalshi_meeting(r["event_id"]), kalshi_bucket(r["outcome_yes"], r["market_id"])
        else:
            if not is_decision_slug(r["event_slug"]):
                continue
            meeting, bucket = polymarket_meeting(r["question"]), polymarket_bucket(r["question"])
        if meeting and bucket:
            rows.append({"platform": r["platform"], "market_id": r["market_id"], "meeting": meeting, "bucket": bucket})
    return pl.DataFrame(rows, schema=GRID_SCHEMA)


def archive_grid() -> pl.DataFrame:
    """Decision markets that closed before the snapshot pipeline first saw them (see `archive.py`)."""
    path = ARCHIVE_DIR / "markets.parquet"
    if not path.exists():
        return pl.DataFrame(schema=GRID_SCHEMA)
    return pl.read_parquet(path).select(list(GRID_SCHEMA))


def full_grid(dim: pl.DataFrame) -> pl.DataFrame:
    live = market_grid(dim)
    old = archive_grid().join(live.select("platform", "market_id"), on=["platform", "market_id"], how="anti")
    return pl.concat([old, live], how="vertical_relaxed")


def ny_date(ts: pl.Expr) -> pl.Expr:
    """New York calendar date of a UTC epoch-seconds column, minus one second so that a bar stamped at
    its closing instant (00:00 UTC, or midnight in New York) is dated by the day it covers."""
    return (
        pl.from_epoch(ts - 1, time_unit="s")
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone("America/New_York")
        .dt.date()
        .cast(pl.Utf8)
    )


def daily_prices(grid: pl.DataFrame) -> pl.DataFrame:
    """One price per market and New York trading date, from three sources.

    Every row is dated by when the price was observed in New York ("price time"), not by the label its
    source gives it. That difference is not cosmetic: on 2025-26 data the daily change in the implied
    rate correlates +0.58 with the 2-year Treasury yield in price time and -0.08 by source label.

    - backfill (`history/*.parquet`): `date` is the UTC date of the bar's timestamp, the convention set
      in `backfill.backfill_polymarket` and `backfill.kalshi_candle_row`. Polymarket's daily point sits
      at 00:00 UTC, which is 20:00 the evening before in New York, and a Kalshi daily candle closes at
      midnight New York time, so both labels are one day late and are shifted back a day here.
    - archive (`archive/daily.parquet`, closed meetings; `archive/kalshi_open_daily.parquet`, open Kalshi
      markets priced trade-else-mid): raw timestamps, dated with `ny_date`.
    - snapshots: the New York date of `snapshot_ts`.

    On the same market and date a snapshot wins, then the archive files, then the backfill.
    """
    hist_dir = SNAP_DIR / "predmarkets" / "fomc" / "history"
    parts = [
        pl.read_parquet(p)
        .select("platform", "market_id", "date", "price")
        .with_columns((pl.col("date").str.to_date() - pl.duration(days=1)).cast(pl.Utf8).alias("date"))
        for p in sorted(hist_dir.glob("*.parquet"))
    ]
    # the archive's closed markets, and its trade-else-mid prices for open Kalshi markets, which win over
    # the backfill's trade-only rows on the same day
    for arch in (ARCHIVE_DIR / "daily.parquet", ARCHIVE_DIR / "kalshi_open_daily.parquet"):
        if arch.exists():
            parts.append(
                pl.read_parquet(arch).select("platform", "market_id", ny_date(pl.col("ts")).alias("date"), "price")
            )
    q = (
        read.quotes("fomc")
        .collect()
        .with_columns(
            pl.coalesce([pl.col("mid"), pl.col("yes_price")]).alias("price"),
            pl.col("snapshot_ts")
            .str.to_datetime(time_zone="UTC")
            .dt.convert_time_zone("America/New_York")
            .dt.date()
            .cast(pl.Utf8)
            .alias("date"),
        )
        .sort("snapshot_ts")
        .select("platform", "market_id", "date", "price")
    )
    parts.append(q)
    allp = pl.concat(parts, how="vertical_relaxed").filter(pl.col("price").is_not_null())
    # later parts win on the same date, and within the snapshots the latest run of the day
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


PLATFORMS = ("polymarket", "kalshi")


#: A market's last price stands in for up to this many days without one. Kalshi's daily history is the
#: last trade, and an outcome priced at a cent or two can go a week without trading.
CARRY_DAYS = 7
#: A charted day's outcome probabilities must add up to within this band.
SUM_BAND = (0.85, 1.15)


def complete_days(prices: pl.DataFrame, grid: pl.DataFrame, carry: int = CARRY_DAYS) -> pl.DataFrame:
    """Daily prices carried forward per market (at most `carry` days), kept only on days when every
    outcome of the meeting has a price and the prices sum to within `SUM_BAND`, plus `seg`: a counter
    that moves on at each gap, so a chart can break its line instead of drawing straight across weeks
    with no usable day. Prices are left as quoted, not renormalised."""
    if prices.height == 0:
        return prices.with_columns(pl.lit(0).alias("seg"))
    filled = (
        prices.with_columns(pl.col("date").str.to_date().alias("d"))
        .sort("platform", "market_id", "d")
        .group_by("platform", "market_id", "meeting", "bucket", maintain_order=True)
        .agg(pl.col("d"), pl.col("price"))
        .with_columns(
            pl.struct("d", "price")
            .map_elements(
                lambda x: _carry(x["d"], x["price"], carry),
                return_dtype=pl.List(pl.Struct({"d": pl.Date, "price": pl.Float64})),
            )
            .alias("rows")
        )
        .select("platform", "market_id", "meeting", "bucket", "rows")
        .explode("rows", empty_as_null=True)
        .unnest("rows")
    )
    total = grid.group_by("platform", "meeting").agg(pl.len().alias("n_total"))
    days = (
        filled.group_by("platform", "meeting", "d")
        .agg(pl.len().alias("n"), pl.col("price").sum().alias("s"))
        .join(total, on=["platform", "meeting"])
        # every outcome priced, and the prices add up to a distribution: carried-forward last trades
        # from different days can sum well past one on a thin market, and such a day is not drawn
        .filter((pl.col("n") == pl.col("n_total")) & pl.col("s").is_between(SUM_BAND[0], SUM_BAND[1]))
        .sort("platform", "meeting", "d")
        .with_columns(
            (pl.col("d").diff().dt.total_days().fill_null(1) > 1).cum_sum().over("platform", "meeting").alias("seg")
        )
        .select("platform", "meeting", "d", "seg")
    )
    return (
        filled.join(days, on=["platform", "meeting", "d"])
        .with_columns(pl.col("d").cast(pl.Utf8).alias("date"))
        .drop("d")
    )


def _carry(days: list, prices: list, carry: int) -> list[dict]:
    out: list[dict] = []
    for i, (d, p) in enumerate(zip(days, prices, strict=True)):
        out.append({"d": d, "price": p})
        nxt = days[i + 1] if i + 1 < len(days) else None
        k = 1
        while k <= carry and (nxt is None or d + timedelta(days=k) < nxt):
            if nxt is None:
                break  # never carry past a market's last observation
            out.append({"d": d + timedelta(days=k), "price": p})
            k += 1
    return out


def bucket_sum_check(prices: pl.DataFrame, full: pl.DataFrame) -> dict:
    """On a day with every outcome of a meeting priced, the probabilities should sum to about one. A mapping
    error that drops or double-counts an outcome shows here first. An outcome split across two markets (the
    January 2025 Polymarket event lists a 50 bps and a 75+ bps cut) is summed, which is what the bucket means."""
    total = full.group_by("platform", "meeting").agg(pl.len().alias("n_total"))
    sums = (
        prices.group_by("platform", "meeting", "date")
        .agg(pl.col("price").sum().alias("s"), pl.len().alias("n"))
        .join(total, on=["platform", "meeting"])
        .filter(pl.col("n") == pl.col("n_total"))
    )
    if sums.height == 0:
        return check("Outcome probabilities sum to one", False, "no meeting-day has every outcome priced")
    ok = sums.filter(pl.col("s").is_between(0.9, 1.1)).height / sums.height
    split = full.group_by("platform", "meeting", "bucket").agg(pl.len().alias("k")).filter(pl.col("k") > 1)
    detail = (
        f"{ok:.1%} of {sums.height:,} fully priced meeting-days sum to between 0.9 and 1.1 "
        f"(median {sums['s'].median():.3f})"
    )
    if split.height:
        detail += "; summed within one outcome: " + ", ".join(
            f"{r['platform']} {r['meeting']} {r['bucket']} ({r['k']} markets)" for r in split.iter_rows(named=True)
        )
    return check("Outcome probabilities sum to one", ok >= 0.95, detail, warn=ok >= 0.9)


def latest_rollup(roll: pl.DataFrame, meeting: str) -> dict:
    """platform -> {date, cut, hold, hike} on the last day in the history mart for this meeting."""
    out = {}
    for pf in PLATFORMS:
        sub = roll.filter((pl.col("meeting") == meeting) & (pl.col("platform") == pf))
        if sub.height:
            day = sub["date"].max()
            probs = {
                r["side"]: round(float(r["prob"]), 4) for r in sub.filter(pl.col("date") == day).iter_rows(named=True)
            }
            out[pf] = {"date": day, **{k: probs.get(k) for k in ("cut", "hold", "hike")}}
    return out


def archive_check(full: pl.DataFrame, prices: pl.DataFrame) -> dict:
    """Every meeting from the archive start to the first one the snapshotter saw must have a complete
    outcome set with prices on both platforms, or the rolling expectation series has a hole."""
    from pipelines.predmarkets.archive import ARCHIVE_FROM  # noqa: PLC0415 - archive imports this module

    first_live = min((m for m in MEETINGS if MEETINGS[m] >= "2026-09-16"), default=None)
    want = [m for m in sorted(MEETINGS) if ARCHIVE_FROM <= m and (first_live is None or m < first_live)]
    priced = prices.group_by("platform", "meeting").agg(pl.col("market_id").n_unique().alias("n"))
    have = {(r["platform"], r["meeting"]): r["n"] for r in priced.iter_rows(named=True)}
    short = [f"{pf} {m}" for m in want for pf in PLATFORMS if have.get((pf, m), 0) < (5 if pf == "kalshi" else 4)]
    return check(
        "Closed-meeting archive",
        not short,
        f"{len(want)} meetings closed before the first snapshot; "
        + (f"incomplete: {', '.join(short)}" if short else "all have a priced outcome set on both platforms"),
        warn=len(short) <= 2,
    )


def build() -> dict:
    dim = read.dim("fomc")
    # `grid` (what the snapshot pipeline has seen) drives the upcoming meetings and the scorecard;
    # `full` adds the meetings that closed before it started, which only feed the price history
    grid = market_grid(dim)
    full = full_grid(dim)
    prices = daily_prices(full)
    latest = latest_probs(grid)
    resolved = resolutions(grid)
    now_iso = utc_now().isoformat(timespec="seconds")
    listed = set(grid["meeting"].to_list())
    # a meeting stays "upcoming" until 19:30 UTC on decision day (statement at 18:00/19:00 UTC) and
    # drops out as soon as a settlement is recorded, whichever comes first
    open_meetings = [
        m for m in sorted(MEETINGS) if m in listed and m not in resolved and f"{MEETINGS[m]}T19:30:00+00:00" > now_iso
    ]
    upcoming = open_meetings[:4]

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

    # chart data: 3-way roll-up daily series for every meeting still open on either platform, on
    # complete days only (see `complete_days`), with `seg` numbering the runs between gaps
    roll = (
        complete_days(prices.filter(pl.col("meeting").is_in(open_meetings)), grid)
        .with_columns(pl.col("bucket").replace_strict(ROLLUP, default=None).alias("side"))
        .group_by("meeting", "platform", "date", "seg", "side")
        .agg(pl.col("price").sum().round(4).alias("prob"))
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
        bucket_sum_check(prices, full),
        archive_check(full, prices),
        freshness(snapshots[-1], 14, "snapshot"),
    ]
    live_prices = prices.join(grid.select("platform", "market_id"), on=["platform", "market_id"], how="semi")
    arch = prices.join(grid.select("platform", "market_id"), on=["platform", "market_id"], how="anti")
    facts = {
        "project": "fomc-markets",
        "checks": checks,
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "first_snapshot_ts": snapshots[0],
        "last_snapshot_ts": snapshots[-1],
        "n_snapshots": len(snapshots),
        "n_days": len({s[:10] for s in snapshots}),
        "history_from": live_prices["date"].min(),
        "chart_from": roll.filter(pl.col("meeting") == upcoming[0])["date"].min() if upcoming and roll.height else None,
        "chart_rules": {"carry_days": CARRY_DAYS, "sum_band": list(SUM_BAND)},
        "archive": {
            "meetings": sorted(arch["meeting"].unique().to_list()),
            "from": arch["date"].min(),
            "markets": {pf: int(arch.filter(pl.col("platform") == pf)["market_id"].n_unique()) for pf in PLATFORMS},
        },
        "open_meetings": [
            {
                "meeting": m,
                "decision_date": MEETINGS[m],
                "platforms": [
                    pf for pf in PLATFORMS if grid.filter((pl.col("meeting") == m) & (pl.col("platform") == pf)).height
                ],
                "latest": latest_rollup(roll, m),
            }
            for m in open_meetings
        ],
        "markets_tracked": {
            r["platform"]: r["n"]
            for r in q.filter(
                pl.col("snapshot_ts") == max(read.full_run_timestamps("fomc") & set(snapshots), default=snapshots[-1])
            )
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
