"""Snapshot prediction-market quotes and order books for a market set.

Usage:
    uv run python -m pipelines.predmarkets.snapshot --set midterms --scope full
    uv run python -m pipelines.predmarkets.snapshot --set all --scope tier1

Scopes:
    full   universe quotes (every market in the set) + tier-1 order books   (once a day)
    tier1  quotes + order books for tier-1 markets only                     (extra intraday runs)

Each run writes:
    data/snapshots/predmarkets/<set>/quotes/<date>/<HHMM>.parquet       narrow numeric rows
    data/snapshots/predmarkets/<set>/books/<date>/<HHMM>.parquet        tier-1 order-book summaries
    data/snapshots/predmarkets/<set>/dim_markets/<date>/<HHMM>.parquet  new/changed market attributes
    data/snapshots/predmarkets/<set>/kalshi_tier1_series.json           series discovered by the last full run
    data/snapshots/predmarkets/<set>/runs.jsonl                         one line per run
    data/raw/predmarkets/<set>/<date>/<HHMM>/*.json.gz                  raw tier-1 responses

Platform failures are isolated: if one platform errors, the other is still written and the
process exits non-zero at the end so CI shows red while keeping the partial data.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pipelines.common.log import setup_logging
from pipelines.common.storage import (
    RAW_DIR,
    SNAP_DIR,
    append_jsonl,
    run_stamp,
    utc_now,
    write_dim_delta,
    write_json,
    write_json_gz,
    write_parquet,
)
from pipelines.predmarkets import kalshi as kx
from pipelines.predmarkets import polymarket as pm
from pipelines.predmarkets.config import MARKET_SETS, MarketSet
from pipelines.predmarkets.schema import (
    BOOKS_SCHEMA,
    DIM_SCHEMA,
    KEY,
    QUOTES_SCHEMA,
    books_frame,
    dim_frame,
    quotes_frame,
)

log = logging.getLogger("predmarkets.snapshot")


def _in_bounds(price: float | None, bounds: tuple[float, float]) -> bool:
    return price is not None and bounds[0] <= price <= bounds[1]


# --- Polymarket ------------------------------------------------------------------
def _pm_tier1_events(client: pm.PolymarketClient, ms: MarketSet, universe: dict[str, dict]) -> dict[str, dict]:
    tier1: dict[str, dict] = {}
    for slug in ms.polymarket_tier1_slugs:
        ev = next((e for e in universe.values() if e.get("slug") == slug), None)
        if ev is None:
            try:
                ev = client.event_by_slug(slug)
            except Exception as exc:  # noqa: BLE001 - a missing headline event must not kill the run
                log.warning("polymarket headline event %s unavailable: %s", slug, exc)
                continue
        tier1[str(ev["id"])] = ev
    rx = re.compile(ms.polymarket_tier1_slug_regex) if ms.polymarket_tier1_slug_regex else None
    for tag in ms.polymarket_tier1_tags:
        tagged = [e for e in universe.values() if any(t.get("id") == tag for t in e.get("tags") or [])]
        if not tagged:  # not covered by the universe (or tier1 scope): fetch the tag directly
            tagged = client.events_by_tag(tag)
        for ev in tagged:
            if rx is None or rx.search(ev.get("slug") or ""):
                tier1[str(ev["id"])] = ev
    return tier1


def snapshot_polymarket(ms: MarketSet, snapshot_ts: str, raw_dir: Path, *, scope: str, books: bool):
    client = pm.PolymarketClient()
    universe: dict[str, dict] = {}
    if scope == "full":
        for tag in ms.polymarket_universe_tags:
            for ev in client.events_by_tag(tag):
                universe[str(ev["id"])] = ev
    tier1 = _pm_tier1_events(client, ms, universe)
    if scope == "full":  # the tier-1 event set barely changes between runs; archive it once a day
        write_json_gz([pm.trim_event(e) for e in tier1.values()], raw_dir / "polymarket_tier1_events.json.gz")

    if scope == "full":
        pfile = SNAP_DIR / "predmarkets" / ms.name / "polymarket_tier1_slugs.json"
        known = set(json.loads(pfile.read_text())["slugs"]) if pfile.exists() else set()
        known |= {e.get("slug") for e in tier1.values() if e.get("slug")}
        write_json({"updated_at": snapshot_ts, "slugs": sorted(known)}, pfile)
    quoted = universe if scope == "full" else tier1
    rows = pm.flatten_markets(quoted.values(), snapshot_ts)
    if scope == "full":  # tier-1 events fetched by slug may sit outside the universe tags
        seen = {r["market_id"] for r in rows}
        rows += [r for r in pm.flatten_markets(tier1.values(), snapshot_ts) if r["market_id"] not in seen]

    book_rows: list[dict] = []
    raw_books: list[dict] = []
    n_candidates = 0
    if books:
        tier1_rows = pm.flatten_markets(tier1.values(), snapshot_ts)
        for row in tier1_rows:
            if not _in_bounds(row["yes_price"], ms.book_price_bounds) or not row["yes_token"]:
                continue
            n_candidates += 1
            try:
                book = client.book(row["yes_token"])
            except Exception as exc:  # noqa: BLE001
                log.warning("book failed for %s: %s", row["market_id"], exc)
                continue
            raw_books.append({"market_id": row["market_id"], "token_id": row["yes_token"], "book": book})
            book_rows.append(
                pm.summarize_book(book, market_id=row["market_id"], token_id=row["yes_token"], snapshot_ts=snapshot_ts)
            )
        write_json_gz(raw_books, raw_dir / "polymarket_books.json.gz")
    stats = {
        "events": len(quoted),
        "markets": len(rows),
        "tier1_events": len(tier1),
        "book_candidates": n_candidates,
        "books": len(book_rows),
        "http_calls": client.gamma.calls + client.clob.calls,
    }
    return rows, book_rows, stats


def _safe_series(client: kx.KalshiClient, series: str) -> list[dict]:
    """One retired or renamed series must not abort the whole Kalshi side of a run."""
    try:
        return client.events_for_series(series)
    except Exception as exc:  # noqa: BLE001
        log.warning("kalshi series %s failed: %s", series, exc)
        return []


def _settled_markets(client: kx.KalshiClient, ev: dict, recorded: set[str]) -> list[dict]:
    """Markets of a settled Kalshi event. Events settled before Kalshi's historical cutoff come back
    from /events without their markets; fetch those from /historical/markets, once per event."""
    if ev.get("markets"):
        return ev["markets"]
    if ev.get("event_ticker") in recorded:
        return []
    try:
        return list(client.iter_historical_markets(event_ticker=ev["event_ticker"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("historical markets failed for %s: %s", ev.get("event_ticker"), exc)
        return []


def capture_resolutions(ms: MarketSet, set_dir: Path, snapshot_ts: str) -> dict:
    """Append-only record of settled tier-1 markets (both platforms), so results survive the
    markets dropping out of the open/active listings. Read by the scoring code."""
    path = set_dir / "resolutions.json"
    store: dict = json.loads(path.read_text()) if path.exists() else {"markets": {}}
    markets: dict = store["markets"]
    added = 0
    # Kalshi: settled events of every tier-1 series (explicit + discovered)
    series = set(ms.kalshi_tier1_series)
    sfile = set_dir / "kalshi_tier1_series.json"
    if sfile.exists():
        series |= set(json.loads(sfile.read_text())["series"])
    kc = kx.KalshiClient()
    recorded = {r.get("event_id") for r in markets.values() if r["platform"] == "kalshi"}
    for s in sorted(series):
        try:
            for ev in kc.iter_events(status="settled", series_ticker=s):
                for m in _settled_markets(kc, ev, recorded):
                    res = (m.get("result") or "").lower()
                    if res not in ("yes", "no"):
                        continue
                    key = f"kalshi:{m['ticker']}"
                    if key not in markets:
                        markets[key] = {
                            "platform": "kalshi",
                            "market_id": m["ticker"],
                            "event_id": ev.get("event_ticker"),
                            "event_slug": ev.get("event_ticker"),
                            "outcome_yes": m.get("yes_sub_title"),
                            "question": m.get("title"),
                            "result": res,
                            "settled_ts": m.get("settlement_ts") or m.get("close_time"),
                            "recorded_at": snapshot_ts,
                        }
                        added += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("settled sweep failed for %s: %s", s, exc)
    # Polymarket: re-fetch every tier-1 event slug ever seen; closed markets carry 1/0 prices
    pfile = set_dir / "polymarket_tier1_slugs.json"
    slugs = set(json.loads(pfile.read_text())["slugs"]) if pfile.exists() else set()
    pc = pm.PolymarketClient()
    for slug in sorted(slugs):
        try:
            ev = pc.event_by_slug(slug)
        except Exception as exc:  # noqa: BLE001
            log.warning("resolution fetch failed for %s: %s", slug, exc)
            continue
        for m in ev.get("markets") or []:
            if not m.get("closed"):
                continue
            prices = pm._loads(m.get("outcomePrices")) or []
            try:
                p0 = float(prices[0]) if prices else None
            except (TypeError, ValueError):
                p0 = None
            if p0 is None or 0.01 < p0 < 0.99:
                continue  # closed but not clearly resolved
            key = f"polymarket:{m.get('id')}"
            if key not in markets:
                markets[key] = {
                    "platform": "polymarket",
                    "market_id": str(m.get("id")),
                    "event_id": str(ev.get("id")),
                    "event_slug": ev.get("slug"),
                    "outcome_yes": None,
                    "question": m.get("question"),
                    "result": "yes" if p0 >= 0.99 else "no",
                    "settled_ts": m.get("closedTime") or m.get("endDate"),
                    "recorded_at": snapshot_ts,
                }
                added += 1
    store["updated_at"] = snapshot_ts
    write_json(store, path)
    return {
        "resolved_total": len(markets),
        "added": added,
        "kalshi_series": len(series),
        "polymarket_slugs": len(slugs),
    }


# --- Kalshi -------------------------------------------------------------------------
def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _kx_is_tier1(ev: dict, ms: MarketSet, rx: re.Pattern | None) -> bool:
    st = ev.get("series_ticker") or ""
    return st in ms.kalshi_tier1_series or bool(rx and rx.match(st))


def snapshot_kalshi(ms: MarketSet, snapshot_ts: str, raw_dir: Path, *, scope: str, books: bool, set_dir: Path):
    client = kx.KalshiClient()
    u = ms.kalshi_universe
    rx = re.compile(ms.kalshi_tier1_series_regex) if ms.kalshi_tier1_series_regex else None
    events: dict[str, dict] = {}
    series_file = set_dir / "kalshi_tier1_series.json"

    if scope == "full":
        for s in u.series:
            for ev in _safe_series(client, s):
                events[ev["event_ticker"]] = ev
        if u.categories:
            lo = datetime.fromisoformat(u.close_from).replace(tzinfo=UTC) if u.close_from else None
            hi = datetime.fromisoformat(u.close_to).replace(tzinfo=UTC) if u.close_to else None
            for ev in client.iter_events():
                if ev.get("category") not in u.categories:
                    continue
                closes = [c for c in (_parse_iso(m.get("close_time")) for m in ev.get("markets") or []) if c]
                if not closes or (lo and max(closes) < lo) or (hi and min(closes) >= hi):
                    continue
                events[ev["event_ticker"]] = ev
        # explicit tier-1 series may be missing from a category scan
        for s in ms.kalshi_tier1_series:
            if not any(e.get("series_ticker") == s for e in events.values()):
                for ev in _safe_series(client, s):
                    events[ev["event_ticker"]] = ev
        tier1_series = sorted({e["series_ticker"] for e in events.values() if _kx_is_tier1(e, ms, rx)})
        write_json({"snapshot_ts": snapshot_ts, "series": tier1_series}, series_file)
    else:
        tier1_series = list(ms.kalshi_tier1_series)
        if series_file.exists():
            tier1_series = sorted(set(tier1_series) | set(json.loads(series_file.read_text())["series"]))
        lo = datetime.fromisoformat(u.close_from).replace(tzinfo=UTC) if u.close_from else None
        hi = datetime.fromisoformat(u.close_to).replace(tzinfo=UTC) if u.close_to else None
        for s in tier1_series:
            for ev in _safe_series(client, s):
                closes = [c for c in (_parse_iso(m.get("close_time")) for m in ev.get("markets") or []) if c]
                if closes and ((lo and max(closes) < lo) or (hi and min(closes) >= hi)):
                    continue  # same window as the full scan (drops e.g. 2028 events of the same series)
                events[ev["event_ticker"]] = ev

    tier1 = [e for e in events.values() if _kx_is_tier1(e, ms, rx)]
    rows = kx.flatten_markets(events.values(), snapshot_ts)
    if scope == "full":
        write_json_gz(tier1, raw_dir / "kalshi_tier1_events.json.gz")

    book_rows: list[dict] = []
    raw_books: list[dict] = []
    n_candidates = 0
    if books:
        for ev in tier1:
            for m in ev.get("markets") or []:
                if m.get("status") not in kx.OPEN_STATUSES:
                    continue
                bid, ask = kx._f(m.get("yes_bid_dollars")), kx._f(m.get("yes_ask_dollars"))
                ref = (bid + ask) / 2 if bid is not None and ask is not None else kx._f(m.get("last_price_dollars"))
                if not _in_bounds(ref, ms.book_price_bounds):
                    continue
                n_candidates += 1
                try:
                    ob = client.orderbook(m["ticker"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("orderbook failed for %s: %s", m["ticker"], exc)
                    continue
                raw_books.append({"market_id": m["ticker"], "orderbook": ob})
                book_rows.append(kx.summarize_orderbook(ob, market_id=m["ticker"], snapshot_ts=snapshot_ts))
        write_json_gz(raw_books, raw_dir / "kalshi_orderbooks.json.gz")
    stats = {
        "events": len(events),
        "markets": len(rows),
        "tier1_events": len(tier1),
        "tier1_series": len(tier1_series),
        "book_candidates": n_candidates,
        "books": len(book_rows),
        "http_calls": client.http.calls,
    }
    return rows, book_rows, stats


# --- orchestration -------------------------------------------------------------------
def snapshot_set(ms: MarketSet, *, scope: str = "full", books: bool = True) -> dict:
    t0 = time.monotonic()
    ts = utc_now()
    date, hhmm = run_stamp(ts)
    snapshot_ts = ts.isoformat(timespec="seconds")
    set_dir = SNAP_DIR / "predmarkets" / ms.name
    raw_dir = RAW_DIR / "predmarkets" / ms.name / date / hhmm
    result: dict = {"set": ms.name, "scope": scope, "snapshot_ts": snapshot_ts, "errors": []}

    rows: list[dict] = []
    book_rows: list[dict] = []
    try:
        q, b, stats = snapshot_polymarket(ms, snapshot_ts, raw_dir, scope=scope, books=books)
        rows += q
        book_rows += b
        result["polymarket"] = stats
        log.info("%s/polymarket: %s", ms.name, stats)
    except Exception as exc:  # noqa: BLE001 - isolate platform failures
        log.exception("%s/polymarket failed", ms.name)
        result["errors"].append(f"polymarket: {exc!r}")
    try:
        q, b, stats = snapshot_kalshi(ms, snapshot_ts, raw_dir, scope=scope, books=books, set_dir=set_dir)
        rows += q
        book_rows += b
        result["kalshi"] = stats
        log.info("%s/kalshi: %s", ms.name, stats)
    except Exception as exc:  # noqa: BLE001
        log.exception("%s/kalshi failed", ms.name)
        result["errors"].append(f"kalshi: {exc!r}")

    if rows:
        qdf = quotes_frame(rows)
        QUOTES_SCHEMA.validate(qdf)
        write_parquet(qdf, set_dir / "quotes" / date / f"{hhmm}.parquet")
        ddf = dim_frame(rows)
        DIM_SCHEMA.validate(ddf)
        result["dim_delta_rows"] = write_dim_delta(ddf, set_dir / "dim_markets", KEY, snapshot_ts, date, hhmm)
    if book_rows:
        bdf = books_frame(book_rows)
        BOOKS_SCHEMA.validate(bdf)
        write_parquet(bdf, set_dir / "books" / date / f"{hhmm}.parquet")

    if scope == "full":
        try:
            result["resolutions"] = capture_resolutions(ms, set_dir, snapshot_ts)
            log.info("%s/resolutions: %s", ms.name, result["resolutions"])
        except Exception as exc:  # noqa: BLE001
            log.exception("%s/resolutions failed", ms.name)
            result["errors"].append(f"resolutions: {exc!r}")
    result["quotes"] = len(rows)
    result["books"] = len(book_rows)
    result["seconds"] = round(time.monotonic() - t0, 1)
    append_jsonl(result, set_dir / "runs.jsonl")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="all", choices=[*MARKET_SETS, "all"])
    ap.add_argument("--scope", default="full", choices=["full", "tier1"])
    ap.add_argument("--no-books", action="store_true", help="skip order-book snapshots")
    args = ap.parse_args(argv)
    setup_logging()
    names = list(MARKET_SETS) if args.set == "all" else [args.set]
    failed = False
    for name in names:
        res = snapshot_set(MARKET_SETS[name], scope=args.scope, books=not args.no_books)
        log.info(
            "done %s (%s): quotes=%s books=%s dim_delta=%s seconds=%s errors=%s",
            name,
            args.scope,
            res["quotes"],
            res["books"],
            res.get("dim_delta_rows"),
            res["seconds"],
            res["errors"],
        )
        failed |= bool(res["errors"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
