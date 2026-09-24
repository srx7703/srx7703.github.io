"""One-minute quotes for single markets, and the quote in force at a given instant.

Kalshi: one-minute candlesticks. A market settled before the historical cutoff is served only by
``/historical/markets/{ticker}/candlesticks`` (fields ``close``), a later one only by the series endpoint
(fields ``close_dollars``); the live endpoint is tried first and a 404 falls through to the archive. A
minute with no activity may have no candle at all, so a quote is carried forward from the last candle
at or before the instant asked for.

Polymarket: CLOB ``/prices-history`` at ``fidelity=1`` for an explicit ``startTs``/``endTs`` window.
"""

from __future__ import annotations

import logging
from bisect import bisect_right

import httpx
import polars as pl

from pipelines.predmarkets.kalshi import KalshiClient
from pipelines.predmarkets.polymarket import PolymarketClient

log = logging.getLogger("macro.intraday")

#: A bid/ask pair wider than this is not a price; the minute's last trade is used instead, if any.
MAX_SPREAD = 0.10

QUOTE_COLS: dict[str, pl.DataType] = {
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "ts": pl.Int64,
    "bid": pl.Float64,
    "ask": pl.Float64,
    "trade": pl.Float64,
    "price": pl.Float64,
}


def _num(x: object) -> float | None:
    try:
        return None if x is None or x == "" else float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def candle_quote(k: dict, max_spread: float = MAX_SPREAD) -> dict:
    """Bid, ask, last trade and the usable price of one candle, reading either Kalshi schema. The price
    is the mid when both sides are quoted within `max_spread`, else the trade, else None."""
    p, yb, ya = k.get("price") or {}, k.get("yes_bid") or {}, k.get("yes_ask") or {}
    bid = _num(yb.get("close_dollars", yb.get("close")))
    ask = _num(ya.get("close_dollars", ya.get("close")))
    trade = _num(p.get("close_dollars", p.get("close")))
    price = None
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1 and not (bid <= 0 and ask >= 1):
        if ask - bid <= max_spread:
            price = (bid + ask) / 2
    if price is None and trade is not None:
        price = trade
    return {"ts": int(k["end_period_ts"]), "bid": bid, "ask": ask, "trade": trade, "price": price}


def kalshi_minutes(
    kc: KalshiClient,
    series: str,
    ticker: str,
    start_ts: int,
    end_ts: int,
    *,
    max_spread: float = MAX_SPREAD,
    historical: bool = False,
) -> list[dict]:
    """`historical=True` skips the live endpoint for a market already known to be archived."""
    params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1}
    if historical:
        resp = kc.historical_candlesticks(ticker, **params)
    else:
        try:
            resp = kc.candlesticks(series, ticker, **params)
        except httpx.HTTPStatusError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            resp = kc.historical_candlesticks(ticker, **params)
    rows = []
    for k in resp.get("candlesticks") or []:
        q = candle_quote(k, max_spread)
        rows.append({"platform": "kalshi", "market_id": ticker, **q})
    return rows


def polymarket_minutes(pc: PolymarketClient, token: str, market_id: str, start_ts: int, end_ts: int) -> list[dict]:
    hist = (
        pc.clob.get_json("/prices-history", {"market": token, "startTs": start_ts, "endTs": end_ts, "fidelity": 1}).get(
            "history"
        )
        or []
    )
    return [
        {
            "platform": "polymarket",
            "market_id": market_id,
            "ts": int(h["t"]),
            "bid": None,
            "ask": None,
            "trade": None,
            "price": float(h["p"]),
        }
        for h in hist
    ]


def price_at(ts: list[int], price: list[float | None], at: int) -> float | None:
    """The last non-null price stamped at or before `at` (lists sorted by ts)."""
    i = bisect_right(ts, at)
    while i > 0:
        i -= 1
        if price[i] is not None:
            return price[i]
    return None


def prices_at(quotes: pl.DataFrame, at: int) -> dict[str, float]:
    """market_id -> price in force at `at`, for every market with a price at or before it."""
    out: dict[str, float] = {}
    for (mid,), g in quotes.sort("ts").group_by(["market_id"], maintain_order=True):
        p = price_at(g["ts"].to_list(), g["price"].to_list(), at)
        if p is not None:
            out[mid] = p
    return out
