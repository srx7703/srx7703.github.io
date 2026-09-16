"""Polymarket read-only client (Gamma for discovery/quotes, CLOB for books & history).

No authentication is required for any endpoint used here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from pipelines.common.http import HttpClient

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
PLATFORM = "polymarket"

# Heavy / irrelevant fields stripped before archiving raw events
_EVENT_DROP = {"description", "image", "icon", "resolutionSource", "seriesSlug"}
_MARKET_DROP = {
    "description",
    "image",
    "icon",
    "resolutionSource",
    "clobRewards",
    "rewardsMinSize",
    "rewardsMaxSpread",
    "umaResolutionStatuses",
    "events",
}


class PolymarketClient:
    def __init__(self) -> None:
        self.gamma = HttpClient(GAMMA, min_interval=0.2)
        self.clob = HttpClient(CLOB, min_interval=0.15)

    # --- Gamma -----------------------------------------------------------------
    def events_by_tag(self, tag_id: int, *, closed: bool = False, page: int = 100) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            batch = self.gamma.get_json(
                "/events",
                {"tag_id": tag_id, "closed": str(closed).lower(), "limit": page, "offset": offset},
            )
            out.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return out

    def event_by_slug(self, slug: str) -> dict:
        return self.gamma.get_json(f"/events/slug/{slug}")

    # --- CLOB ------------------------------------------------------------------
    def book(self, token_id: str) -> dict:
        return self.clob.get_json("/book", {"token_id": token_id})

    def prices_history(self, token_id: str, *, interval: str = "max", fidelity: int = 1440) -> dict:
        return self.clob.get_json(
            "/prices-history", {"market": token_id, "interval": interval, "fidelity": fidelity}
        )


# --- normalization -------------------------------------------------------------
def _loads(s: Any) -> Any:
    """Gamma encodes list fields (outcomes, prices, token ids) as JSON strings."""
    if s is None or isinstance(s, list | dict):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def trim_event(ev: dict) -> dict:
    out = {k: v for k, v in ev.items() if k not in _EVENT_DROP}
    out["markets"] = [{k: v for k, v in m.items() if k not in _MARKET_DROP} for m in ev.get("markets") or []]
    return out


def flatten_markets(events: Iterable[dict], snapshot_ts: str) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        tags = [t.get("slug") for t in ev.get("tags") or [] if t.get("slug")]
        for m in ev.get("markets") or []:
            toks = _loads(m.get("clobTokenIds")) or []
            prices = _loads(m.get("outcomePrices")) or []
            outcomes = _loads(m.get("outcomes")) or []
            bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
            rows.append(
                {
                    "snapshot_ts": snapshot_ts,
                    "platform": PLATFORM,
                    "market_id": str(m.get("id")),
                    "event_id": str(ev.get("id")),
                    "event_slug": ev.get("slug"),
                    "event_title": ev.get("title"),
                    "series": None,
                    "market_slug": m.get("slug"),
                    "question": m.get("question"),
                    "outcome_yes": str(outcomes[0]) if outcomes else None,
                    "condition_id": m.get("conditionId"),
                    "yes_token": str(toks[0]) if toks else None,
                    "no_token": str(toks[1]) if len(toks) > 1 else None,
                    "yes_price": _f(prices[0]) if prices else None,
                    "best_bid": bid,
                    "best_ask": ask,
                    "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
                    "last_trade": _f(m.get("lastTradePrice")),
                    "volume": _f(m.get("volume")),
                    "volume_24h": _f(m.get("volume24hr")),
                    "liquidity": _f(m.get("liquidity")),
                    "open_interest": None,
                    "end_date": m.get("endDate"),
                    "closed": bool(m.get("closed")),
                    "active": bool(m.get("active")),
                    "tags": ",".join(tags),
                }
            )
    return rows


def summarize_book(book: dict, *, market_id: str, token_id: str, snapshot_ts: str) -> dict:
    """Top-of-book and near-touch depth for a YES token's order book.

    Polymarket returns bids ascending and asks descending, so best = max(bid) / min(ask).
    Sizes are in shares; `*_usd` multiplies by price to approximate dollar depth.
    """
    bids = [(float(b["price"]), float(b["size"])) for b in book.get("bids") or []]
    asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks") or []]
    best_bid = max((p for p, _ in bids), default=None)
    best_ask = min((p for p, _ in asks), default=None)

    def depth(levels: list[tuple[float, float]], lo: float, hi: float) -> tuple[float, float]:
        sel = [(p, s) for p, s in levels if lo <= p <= hi]
        return sum(s for _, s in sel), sum(p * s for p, s in sel)

    row = {
        "snapshot_ts": snapshot_ts,
        "platform": PLATFORM,
        "market_id": market_id,
        "token_id": token_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": (best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
        "mid": ((best_ask + best_bid) / 2) if best_bid is not None and best_ask is not None else None,
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
        "book_ts": str(book.get("timestamp")) if book.get("timestamp") is not None else None,
    }
    for cents in (5, 10):
        w = cents / 100
        bs, bu = depth(bids, (best_bid or 0) - w, best_bid or 0) if best_bid is not None else (0.0, 0.0)
        as_, au = depth(asks, best_ask or 1, (best_ask or 1) + w) if best_ask is not None else (0.0, 0.0)
        row[f"bid_depth_{cents}c"] = bs
        row[f"ask_depth_{cents}c"] = as_
        row[f"bid_depth_{cents}c_usd"] = bu
        row[f"ask_depth_{cents}c_usd"] = au
    return row
