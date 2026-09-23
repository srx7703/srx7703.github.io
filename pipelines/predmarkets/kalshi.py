"""Kalshi read-only client (public market-data endpoints, no API key required)."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from pipelines.common.http import HttpClient

log = logging.getLogger(__name__)

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PLATFORM = "kalshi"
OPEN_STATUSES = {"open", "active"}


class KalshiClient:
    def __init__(self) -> None:
        self.http = HttpClient(BASE, min_interval=0.12)

    def iter_events(
        self,
        *,
        status: str = "open",
        series_ticker: str | None = None,
        with_nested_markets: bool = True,
        limit: int = 200,
        max_pages: int = 500,
    ) -> Iterator[dict]:
        cursor: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "status": status,
                "limit": limit,
                "with_nested_markets": str(with_nested_markets).lower(),
            }
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor
            resp = self.http.get_json("/events", params)
            events = resp.get("events") or []
            yield from events
            cursor = resp.get("cursor")
            if not cursor or not events:
                break

    def events_for_series(self, series_ticker: str) -> list[dict]:
        return list(self.iter_events(series_ticker=series_ticker))

    def orderbook(self, ticker: str, *, depth: int = 20) -> dict:
        return self.http.get_json(f"/markets/{ticker}/orderbook", {"depth": depth})

    def candlesticks(
        self, series_ticker: str, ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1440
    ) -> dict:
        return self.http.get_json(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    # Markets settled before the historical cutoff (it trails today by about two months) move to the
    # /historical endpoints: the live candlesticks 404 and /events stops nesting them.
    def historical_cutoff(self) -> dict:
        """`market_settled_ts`: markets settled before it are served by the /historical endpoints only."""
        return self.http.get_json("/historical/cutoff")

    def historical_candlesticks(self, ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1440) -> dict:
        """Candles of an archived market. Field names differ from the live endpoint: `price.close`,
        `volume`, `open_interest` instead of `price.close_dollars`, `volume_fp`, `open_interest_fp`."""
        return self.http.get_json(
            f"/historical/markets/{ticker}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    def iter_historical_markets(self, *, event_ticker: str, limit: int = 200, max_pages: int = 50) -> Iterator[dict]:
        """Markets (with `result`) of an event settled before the cutoff."""
        cursor: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"event_ticker": event_ticker, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            resp = self.http.get_json("/historical/markets", params)
            markets = resp.get("markets") or []
            yield from markets
            cursor = resp.get("cursor")
            if not cursor or not markets:
                break


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def flatten_markets(events: Iterable[dict], snapshot_ts: str) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        for m in ev.get("markets") or []:
            bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            status = m.get("status")
            rows.append(
                {
                    "snapshot_ts": snapshot_ts,
                    "platform": PLATFORM,
                    "market_id": m.get("ticker"),
                    "event_id": ev.get("event_ticker"),
                    "event_slug": ev.get("event_ticker"),
                    "event_title": ev.get("title"),
                    "series": ev.get("series_ticker"),
                    "market_slug": m.get("ticker"),
                    "question": m.get("title"),
                    "outcome_yes": m.get("yes_sub_title"),
                    "condition_id": None,
                    "yes_token": None,
                    "no_token": None,
                    "yes_price": _f(m.get("last_price_dollars")),
                    "best_bid": bid,
                    "best_ask": ask,
                    "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
                    "last_trade": _f(m.get("last_price_dollars")),
                    "volume": _f(m.get("volume_fp")),
                    "volume_24h": _f(m.get("volume_24h_fp")),
                    "liquidity": _f(m.get("liquidity_dollars")),
                    "open_interest": _f(m.get("open_interest_fp")),
                    "end_date": m.get("close_time"),
                    "closed": status not in OPEN_STATUSES,
                    "active": status in OPEN_STATUSES,
                    "tags": ev.get("category") or "",
                }
            )
    return rows


def summarize_orderbook(ob: dict, *, market_id: str, snapshot_ts: str) -> dict:
    """Normalize a Kalshi book to YES terms.

    Kalshi lists resting YES bids and resting NO bids. A NO bid at price p is an offer to
    sell YES at 1 - p, so best_ask(yes) = 1 - max(no bid). Quantities are contracts
    ($1 notional at settlement); `*_usd` is the cash at risk on that side.
    """
    fp = ob.get("orderbook_fp") or {}
    yes = [(float(p), float(q)) for p, q in fp.get("yes_dollars") or []]
    no = [(float(p), float(q)) for p, q in fp.get("no_dollars") or []]
    best_bid = max((p for p, _ in yes), default=None)
    best_no_bid = max((p for p, _ in no), default=None)
    best_ask = (1 - best_no_bid) if best_no_bid is not None else None

    row = {
        "snapshot_ts": snapshot_ts,
        "platform": PLATFORM,
        "market_id": market_id,
        "token_id": None,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": (best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
        "mid": ((best_ask + best_bid) / 2) if best_bid is not None and best_ask is not None else None,
        "n_bid_levels": len(yes),
        "n_ask_levels": len(no),
        "book_ts": None,
    }
    for cents in (5, 10):
        w = cents / 100
        bsel = [(p, q) for p, q in yes if best_bid is not None and best_bid - w <= p <= best_bid]
        asel = [(p, q) for p, q in no if best_no_bid is not None and best_no_bid - w <= p <= best_no_bid]
        row[f"bid_depth_{cents}c"] = sum(q for _, q in bsel)
        row[f"ask_depth_{cents}c"] = sum(q for _, q in asel)
        row[f"bid_depth_{cents}c_usd"] = sum(p * q for p, q in bsel)
        row[f"ask_depth_{cents}c_usd"] = sum(p * q for p, q in asel)
    return row
