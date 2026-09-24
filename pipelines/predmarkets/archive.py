"""Recover the decision markets that closed before the snapshot pipeline first saw them.

Usage:
    uv run python -m pipelines.predmarkets.archive

The snapshotter started on 2026-09-16 and only ever discovers markets that are open, so every meeting
from January 2025 to July 2026 is missing from `dim_markets`, and therefore from the backfill, which
walks the dim. This module finds those meetings on both platforms directly:

- Polymarket: closed events under the "Fed Rates" tag in both slug families (`fed-decision-in-*` since
  March 2025, `fed-interest-rates-<month>-<yyyy>` before). Daily points come from the CLOB
  price-history endpoint; days it skips are refilled from hourly points fetched in 7-day windows (the
  endpoint rejects a 31-day window with HTTP 400).
- Kalshi: settled `KXFEDDECISION` events. Markets settled before the historical cutoff (about two
  months behind today) are served only by the `/historical` endpoints, whose candles name the close
  `price.close` rather than `price.close_dollars`. A day without a trade has no close; its bid/ask mid
  is used instead when the spread is at most 10 cents.

Writes data/snapshots/predmarkets/fomc/archive/markets.parquet (one row per market: meeting, bucket,
token, result) and daily.parquet (platform, market_id, ts, price, src). Timestamps stay raw epoch
seconds; `fomc.daily_prices` dates them in New York time. A closed market's history does not change,
so a market already archived is not fetched again and both files only grow.

Also writes kalshi_open_daily.parquet: the same trade-else-mid daily prices for the Kalshi decision
markets that are still open (see `refresh_live_kalshi`), refetched and merged each run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pandera.polars as pa
import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import write_json, write_parquet
from pipelines.predmarkets import fomc, read
from pipelines.predmarkets.kalshi import KalshiClient
from pipelines.predmarkets.polymarket import PolymarketClient, _loads

log = logging.getLogger("predmarkets.archive")

FED_TAG = 100196  # Polymarket "Fed Rates"
SERIES = "KXFEDDECISION"
ARCHIVE_FROM = "2025-01"  # first meeting kept; the 2024 events list a 50 and a 75+ cut market side by side
MAX_SPREAD = 0.10
FILL_WINDOW_DAYS = 7

MARKET_COLS: dict[str, pl.DataType] = {
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "meeting": pl.Utf8,
    "bucket": pl.Utf8,
    "event_id": pl.Utf8,
    "question": pl.Utf8,
    "yes_token": pl.Utf8,
    "result": pl.Utf8,
    "decision_ts": pl.Utf8,
    "source": pl.Utf8,
}
DAILY_COLS: dict[str, pl.DataType] = {
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "ts": pl.Int64,
    "price": pl.Float64,
    "src": pl.Utf8,
}
DAILY_SCHEMA = pa.DataFrameSchema(
    {
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "ts": pa.Column(int, pa.Check.gt(1_600_000_000)),
        # null only on a `no_price` row: a Kalshi candle whose book changed to one with no usable price
        "price": pa.Column(float, pa.Check.in_range(0.0, 1.0), nullable=True),
        "src": pa.Column(str, pa.Check.isin(["daily", "hourly_fill", "trade", "quote_mid", "no_price"])),
    },
    unique=["platform", "market_id", "ts"],
    checks=pa.Check(
        lambda data: data.lazyframe.select(pl.col("price").is_null() == (pl.col("src") == "no_price")),
        error="price must be null exactly on no_price rows",
    ),
)


def archived_meetings() -> set[str]:
    return {m for m in fomc.MEETINGS if m >= ARCHIVE_FROM}


# --- Polymarket -------------------------------------------------------------------------------------
def polymarket_markets(pc: PolymarketClient, meetings: set[str]) -> list[dict]:
    rows = []
    for ev in pc.events_by_tag(FED_TAG, closed=True):
        if not fomc.is_decision_slug(ev.get("slug")):
            continue
        for m in ev.get("markets") or []:
            q = m.get("question") or ""
            meeting, bucket = fomc.polymarket_meeting(q), fomc.polymarket_bucket(q)
            toks = _loads(m.get("clobTokenIds")) or []
            if meeting not in meetings or bucket is None or not toks:
                continue
            prices = _loads(m.get("outcomePrices")) or []
            won = str(prices[0]) if prices else None
            rows.append(
                {
                    "platform": "polymarket",
                    "market_id": str(m.get("id")),
                    "meeting": meeting,
                    "bucket": bucket,
                    "event_id": ev.get("slug"),
                    "question": q,
                    "yes_token": str(toks[0]),
                    "result": {"1": "yes", "0": "no"}.get(won) if m.get("closed") else None,
                    "decision_ts": None,
                    "source": "clob",
                }
            )
    return rows


def _midnight_utc(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def polymarket_daily(pc: PolymarketClient, token: str, market_id: str) -> list[dict]:
    """Daily points (00:00 UTC, i.e. 20:00 the previous evening in New York), then the same clock time
    recovered from hourly points for any day the daily series skips."""
    hist = pc.prices_history(token, interval="max", fidelity=1440).get("history") or []
    pts = {int(h["t"]): float(h["p"]) for h in hist}
    rows = [
        {"platform": "polymarket", "market_id": market_id, "ts": t, "price": p, "src": "daily"} for t, p in pts.items()
    ]
    if len(pts) < 2:
        return rows
    have = {datetime.fromtimestamp(t, UTC).date() for t in pts}
    first, last = min(have), max(have)
    missing = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    missing = [d for d in missing if d not in have]
    # windows span at most FILL_WINDOW_DAYS calendar days, however the missing days are spread out
    windows: list[list] = []
    for d in missing:
        if windows and (d - windows[-1][0]).days < FILL_WINDOW_DAYS:
            windows[-1].append(d)
        else:
            windows.append([d])
    for chunk in windows:
        start = _midnight_utc(chunk[0].isoformat()) - 86400
        end = _midnight_utc(chunk[-1].isoformat())
        try:
            hourly = (
                pc.clob.get_json(
                    "/prices-history", {"market": token, "startTs": start, "endTs": end, "fidelity": 60}
                ).get("history")
                or []
            )
        except Exception as exc:  # noqa: BLE001 - a failed refill leaves the gap, it does not drop the market
            log.warning("hourly refill failed %s %s: %s", market_id, chunk[0], exc)
            continue
        for d in chunk:
            cut = _midnight_utc(d.isoformat())
            near = [(int(h["t"]), float(h["p"])) for h in hourly if cut - 6 * 3600 < int(h["t"]) <= cut]
            if near:
                t, p = max(near)
                rows.append(
                    {"platform": "polymarket", "market_id": market_id, "ts": t, "price": p, "src": "hourly_fill"}
                )
    return rows


# --- Kalshi -----------------------------------------------------------------------------------------
def kalshi_markets(kc: KalshiClient, meetings: set[str]) -> list[dict]:
    rows = []
    for ev in kc.iter_events(status="settled", series_ticker=SERIES):
        et = ev.get("event_ticker")
        meeting = fomc.kalshi_meeting(et)
        if meeting not in meetings:
            continue
        markets, source = ev.get("markets") or [], "live"
        if not markets:
            markets, source = list(kc.iter_historical_markets(event_ticker=et)), "historical"
        for m in markets:
            bucket = fomc.kalshi_bucket(m.get("yes_sub_title"), m.get("ticker"))
            if bucket is None:
                continue
            rows.append(
                {
                    "platform": "kalshi",
                    "market_id": m["ticker"],
                    "meeting": meeting,
                    "bucket": bucket,
                    "event_id": et,
                    "question": m.get("title"),
                    "yes_token": None,
                    "result": m.get("result") or None,
                    "decision_ts": ev.get("strike_date"),
                    "source": source,
                }
            )
    return rows


def _num(x: object) -> float | None:
    try:
        return None if x is None or x == "" else float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def candle_price(k: dict) -> tuple[float | None, str | None]:
    """Close of a daily candle (live `close_dollars` or historical `close`), else the bid/ask mid."""
    p, yb, ya = k.get("price") or {}, k.get("yes_bid") or {}, k.get("yes_ask") or {}
    close = _num(p.get("close_dollars", p.get("close")))
    if close is not None:
        return close, "trade"
    b, a = _num(yb.get("close_dollars", yb.get("close"))), _num(ya.get("close_dollars", ya.get("close")))
    if b is not None and a is not None and 0 < a and a - b <= MAX_SPREAD:
        return (a + b) / 2, "quote_mid"
    return None, None


def kalshi_daily(
    kc: KalshiClient, market_id: str, source: str, start_ts: int, end_ts: int, *, keep_unpriced: bool = False
) -> list[dict]:
    """Daily rows for one market. With `keep_unpriced`, a candle with no usable price is kept as a
    `no_price` row: Kalshi emits a candle only when the book changes (across 860 multi-day gaps in the
    open markets, the book reopened exactly where it had closed every time), so these rows plus the
    priced ones are a complete record of when a market's price was known and when it stopped being."""
    if source == "historical":
        resp = kc.historical_candlesticks(market_id, start_ts=start_ts, end_ts=end_ts, period_interval=1440)
    else:
        resp = kc.candlesticks(SERIES, market_id, start_ts=start_ts, end_ts=end_ts, period_interval=1440)
    rows = []
    for k in resp.get("candlesticks") or []:
        price, src = candle_price(k)
        if price is None and keep_unpriced:
            price, src = None, "no_price"
        if src is not None:
            rows.append(
                {
                    "platform": "kalshi",
                    "market_id": market_id,
                    "ts": int(k["end_period_ts"]),
                    "price": price,
                    "src": src,
                }
            )
    return rows


def kalshi_daily_any(kc: KalshiClient, market_id: str, start_ts: int, end_ts: int) -> list[dict]:
    """`kalshi_daily` (keeping unpriced candles) for a market whose endpoint is not known: live first,
    the archive on a 404."""
    try:
        return kalshi_daily(kc, market_id, "live", start_ts, end_ts, keep_unpriced=True)
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        return kalshi_daily(kc, market_id, "historical", start_ts, end_ts, keep_unpriced=True)


def refresh_live_kalshi(kc: KalshiClient, live: pl.DataFrame, days: int = 400) -> tuple[pl.DataFrame, list[str]]:
    """Daily trade-else-mid prices for the Kalshi decision markets the snapshot pipeline knows.

    The weekly backfill keeps last trades only, and an outcome priced at a cent or two can go weeks
    without one, which leaves most days without a complete outcome set for a thinly traded meeting. The
    daily candle carries the closing bid and ask either way; this applies the archive's rule (the mid
    when the spread is at most 10 cents) to the open markets too. These markets are still trading, so
    the window is refetched each run and merged, new rows winning.
    """
    now = int(datetime.now(UTC).timestamp())
    rows: list[dict] = []
    failed: list[str] = []
    for mid in live.filter(pl.col("platform") == "kalshi")["market_id"].to_list():
        try:
            rows += kalshi_daily_any(kc, mid, now - days * 86400, now)
        except Exception as exc:  # noqa: BLE001 - one market failing must not lose the others
            log.warning("daily candles failed %s: %s", mid, exc)
            failed.append(f"kalshi:{mid}")
    return pl.DataFrame(rows, schema=DAILY_COLS), failed


# --- orchestration ----------------------------------------------------------------------------------
def _read(path, cols: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=cols)


def run(platforms: tuple[str, ...] = ("polymarket", "kalshi")) -> dict:
    meetings = archived_meetings()
    dim = read.dim("fomc")
    live = fomc.market_grid(dim) if dim is not None else pl.DataFrame(schema=fomc.GRID_SCHEMA)
    live_keys = set(zip(live["platform"].to_list(), live["market_id"].to_list(), strict=True))

    mk_path, daily_path = fomc.ARCHIVE_DIR / "markets.parquet", fomc.ARCHIVE_DIR / "daily.parquet"
    markets_old, daily_old = _read(mk_path, MARKET_COLS), _read(daily_path, DAILY_COLS)
    done = set(zip(daily_old["platform"].to_list(), daily_old["market_id"].to_list(), strict=True))

    pc, kc = PolymarketClient(), KalshiClient()
    kc.http.min_interval = 0.3  # candlesticks are rate-limited harder than list endpoints
    found: list[dict] = []
    if "polymarket" in platforms:
        found += polymarket_markets(pc, meetings)
    if "kalshi" in platforms:
        found += kalshi_markets(kc, meetings)
    found = [r for r in found if (r["platform"], r["market_id"]) not in live_keys]
    log.info("archive discovery: %d closed decision markets outside the dim", len(found))

    now = int(datetime.now(UTC).timestamp())
    new_rows: list[dict] = []
    failed: list[str] = []
    for r in found:
        key = (r["platform"], r["market_id"])
        if key in done:
            continue
        try:
            if r["platform"] == "polymarket":
                new_rows += polymarket_daily(pc, r["yes_token"], r["market_id"])
            else:
                decided = r["decision_ts"] or f"{fomc.MEETINGS[r['meeting']]}T18:00:00Z"
                end = int(datetime.fromisoformat(decided.replace("Z", "+00:00")).timestamp()) + 3 * 86400
                new_rows += kalshi_daily(kc, r["market_id"], r["source"], end - 400 * 86400, min(end, now))
        except Exception as exc:  # noqa: BLE001 - one market failing must not lose the others
            log.warning("history failed %s %s: %s", *key, exc)
            failed.append(f"{key[0]}:{key[1]}")

    markets = (
        pl.concat([markets_old, pl.DataFrame(found, schema=MARKET_COLS)], how="vertical_relaxed")
        .unique(subset=["platform", "market_id"], keep="last", maintain_order=True)
        .sort("platform", "meeting", "bucket")
    )
    daily = pl.concat([daily_old, pl.DataFrame(new_rows, schema=DAILY_COLS)], how="vertical_relaxed").unique(
        subset=["platform", "market_id", "ts"], keep="first", maintain_order=True
    )
    DAILY_SCHEMA.validate(daily)
    write_parquet(markets, mk_path)
    write_parquet(daily.sort("platform", "market_id", "ts"), daily_path)

    quotes_path = fomc.ARCHIVE_DIR / "kalshi_open_daily.parquet"
    fresh, q_failed = refresh_live_kalshi(kc, live) if "kalshi" in platforms else (pl.DataFrame(schema=DAILY_COLS), [])
    failed += q_failed
    quotes = pl.concat([_read(quotes_path, DAILY_COLS), fresh], how="vertical_relaxed").unique(
        subset=["platform", "market_id", "ts"], keep="last", maintain_order=True
    )
    DAILY_SCHEMA.validate(quotes)
    write_parquet(quotes.sort("platform", "market_id", "ts"), quotes_path)
    manifest = {
        "run_ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "meetings": sorted(markets["meeting"].unique().to_list()),
        "markets": {pf: int(markets.filter(pl.col("platform") == pf).height) for pf in ("polymarket", "kalshi")},
        "rows": {pf: int(daily.filter(pl.col("platform") == pf).height) for pf in ("polymarket", "kalshi")},
        "fetched_this_run": len({(r["platform"], r["market_id"]) for r in new_rows}),
        "open_kalshi_rows": int(quotes.height),
        "failed": failed,
    }
    write_json(manifest, fomc.ARCHIVE_DIR / "manifest.json")
    log.info("archive: %s", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", default="all", choices=["all", "polymarket", "kalshi"])
    args = ap.parse_args(argv)
    setup_logging()
    platforms = ("polymarket", "kalshi") if args.platform == "all" else (args.platform,)
    manifest = run(platforms)
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
