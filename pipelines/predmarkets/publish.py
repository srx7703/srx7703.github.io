"""Build marts and facts for the prediction-market projects from the snapshot tables.

Usage:
    uv run python -m pipelines.predmarkets.publish

Writes:
    data/marts/predmarkets/midterms_headline.json     long time series of headline probabilities
    data/marts/predmarkets/midterms_coverage.json     per-run coverage (markets, live markets, volume)
    data/facts/midterms.json                          KPI numbers the site narrative is templated from
    data/facts/fomc.json                              coverage KPIs for the FOMC set (page comes later)

Headline probability = order-book mid when both sides are quoted, else the platform's displayed
YES price. All probabilities are in [0, 1].
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass

import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, utc_now, write_json
from pipelines.predmarkets import read

log = logging.getLogger("predmarkets.publish")

ELECTION_DAY = "2026-11-03"


@dataclass(frozen=True)
class Headline:
    key: str  # e.g. house_dem
    label: str  # human label
    platform: str
    market_id: str


# Discovered from the 2026-09-16 snapshots; see docs/DATA_MODEL.md
HEADLINES: tuple[Headline, ...] = (
    Headline("house_dem", "Democrats win the House", "polymarket", "562802"),
    Headline("house_dem", "Democrats win the House", "kalshi", "CONTROLH-2026-D"),
    Headline("house_rep", "Republicans win the House", "polymarket", "562803"),
    Headline("house_rep", "Republicans win the House", "kalshi", "CONTROLH-2026-R"),
    Headline("senate_dem", "Democrats win the Senate", "polymarket", "562793"),
    Headline("senate_dem", "Democrats win the Senate", "kalshi", "CONTROLS-2026-D"),
    Headline("senate_rep", "Republicans win the Senate", "polymarket", "562794"),
    Headline("senate_rep", "Republicans win the Senate", "kalshi", "CONTROLS-2026-R"),
    Headline("bop_dd", "D Senate + D House", "polymarket", "562828"),
    Headline("bop_rd", "R Senate + D House", "polymarket", "562830"),
    Headline("bop_rr", "R Senate + R House", "polymarket", "562831"),
    Headline("bop_dr", "D Senate + R House", "polymarket", "562829"),
)


def _prob(df: pl.LazyFrame) -> pl.LazyFrame:
    return df.with_columns(pl.coalesce([pl.col("mid"), pl.col("yes_price")]).alias("prob"))


def headline_series() -> pl.DataFrame:
    keys = pl.DataFrame(
        [(h.key, h.label, h.platform, h.market_id) for h in HEADLINES],
        schema=["key", "label", "platform", "market_id"],
        orient="row",
    )
    q = _prob(read.quotes("midterms")).collect()
    out = (
        q.join(keys, on=["platform", "market_id"], how="inner")
        .select(
            "snapshot_ts",
            "key",
            "label",
            "platform",
            "market_id",
            "prob",
            "best_bid",
            "best_ask",
            "volume",
            "volume_24h",
        )
        .sort("snapshot_ts", "key", "platform")
    )
    return out


def coverage() -> pl.DataFrame:
    q = _prob(read.quotes("midterms")).collect()
    return (
        q.group_by("snapshot_ts", "platform")
        .agg(
            pl.len().alias("markets"),
            (pl.col("prob").is_between(0.02, 0.98)).sum().alias("live_markets"),
            pl.col("volume").sum().alias("volume_usd"),
            pl.col("volume_24h").sum().alias("volume_24h_usd"),
        )
        .sort("snapshot_ts", "platform")
    )


def _latest_by(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    return df.sort("snapshot_ts").group_by(cols, maintain_order=True).last()


def build_midterms() -> dict:
    series = headline_series()
    cov = coverage()
    q = read.quotes("midterms").collect()
    snapshots = sorted(q["snapshot_ts"].unique().to_list())
    runs = [
        json.loads(line)
        for line in (read.set_dir("midterms") / "runs.jsonl").read_text().splitlines()
        if line
    ]
    books_total = sum(r.get("books") or 0 for r in runs)

    latest = _latest_by(series, ["key", "platform"])
    head: dict[str, dict] = {}
    for key in sorted({h.key for h in HEADLINES}):
        rows = latest.filter(pl.col("key") == key)
        by_platform = {
            r["platform"]: round(r["prob"], 4) for r in rows.iter_rows(named=True) if r["prob"] is not None
        }
        entry = {"label": rows["label"][0] if rows.height else key, **by_platform}
        if "polymarket" in by_platform and "kalshi" in by_platform:
            entry["spread"] = round(by_platform["polymarket"] - by_platform["kalshi"], 4)
        head[key] = entry

    latest_cov = _latest_by(cov, ["platform"])
    markets_tracked = {r["platform"]: r["markets"] for r in latest_cov.iter_rows(named=True)}
    live_tracked = {r["platform"]: r["live_markets"] for r in latest_cov.iter_rows(named=True)}

    # top state Senate/Governor races on Polymarket by cumulative volume (latest snapshot)
    dim = read.dim("midterms")
    latest_q = q.filter(pl.col("snapshot_ts") == snapshots[-1]).join(
        dim, on=["platform", "market_id"], how="left"
    )
    races = (
        latest_q.filter(
            pl.col("event_slug").str.contains(
                "senate-election-winner|governor-winner-2026|governor-election-winner"
            )
        )
        .group_by("event_slug", "event_title")
        .agg(pl.col("volume").sum().alias("volume_usd"), pl.len().alias("markets"))
        .sort("volume_usd", descending=True)
        .head(10)
    )

    facts = {
        "project": "midterms-2026",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "election_day": ELECTION_DAY,
        "first_snapshot_ts": snapshots[0],
        "last_snapshot_ts": snapshots[-1],
        "n_snapshots": len(snapshots),
        "n_days": len({s[:10] for s in snapshots}),
        "markets_tracked": markets_tracked,
        "live_markets_tracked": live_tracked,
        "order_books_captured": books_total,
        "headline": head,
        "top_races_polymarket": [
            {
                "event_slug": r["event_slug"],
                "title": r["event_title"],
                "volume_usd": round(r["volume_usd"] or 0),
                "markets": r["markets"],
            }
            for r in races.iter_rows(named=True)
        ],
        "sources": [
            {"name": "Polymarket Gamma + CLOB APIs", "url": "https://docs.polymarket.com/"},
            {"name": "Kalshi public trade API v2", "url": "https://docs.kalshi.com/"},
        ],
    }
    (MARTS_DIR / "predmarkets").mkdir(parents=True, exist_ok=True)
    series.write_parquet(MARTS_DIR / "predmarkets" / "midterms_headline.parquet")
    write_json(series.to_dicts(), MARTS_DIR / "predmarkets" / "midterms_headline.json")
    write_json(cov.to_dicts(), MARTS_DIR / "predmarkets" / "midterms_coverage.json")
    write_json(facts, FACTS_DIR / "midterms.json")
    return facts


def build_fomc() -> dict:
    q = _prob(read.quotes("fomc")).collect()
    snapshots = sorted(q["snapshot_ts"].unique().to_list())
    latest = q.filter(pl.col("snapshot_ts") == snapshots[-1])
    facts = {
        "project": "fomc-markets",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "first_snapshot_ts": snapshots[0],
        "last_snapshot_ts": snapshots[-1],
        "n_snapshots": len(snapshots),
        "markets_tracked": {
            r["platform"]: r["n"]
            for r in latest.group_by("platform").agg(pl.len().alias("n")).iter_rows(named=True)
        },
    }
    write_json(facts, FACTS_DIR / "fomc.json")
    return facts


def main() -> int:
    setup_logging()
    m = build_midterms()
    log.info(
        "midterms facts: snapshots=%s days=%s headline=%s",
        m["n_snapshots"],
        m["n_days"],
        m["headline"].get("house_dem"),
    )
    f = build_fomc()
    log.info("fomc facts: snapshots=%s markets=%s", f["n_snapshots"], f["markets_tracked"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
