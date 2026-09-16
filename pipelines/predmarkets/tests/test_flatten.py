from pipelines.predmarkets import kalshi as kx
from pipelines.predmarkets import polymarket as pm

TS = "2026-09-15T18:00:00+00:00"

PM_EVENT = {
    "id": 32228,
    "slug": "balance-of-power-2026-midterms",
    "title": "2026 Balance of Power",
    "tags": [{"id": 102289, "slug": "midterms"}, {"id": 1, "slug": "politics"}],
    "markets": [
        {
            "id": 562828,
            "question": "2026 Balance of Power: D Senate, D House",
            "slug": "bop-dd",
            "conditionId": "0xabc",
            "clobTokenIds": '["111", "222"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.565", "0.435"]',
            "volume": "12163362.5",
            "volume24hr": 53552.06,
            "liquidity": "100.5",
            "bestBid": 0.56,
            "bestAsk": 0.57,
            "lastTradePrice": 0.565,
            "endDate": "2026-11-03T00:00:00Z",
            "closed": False,
            "active": True,
            "description": "long text that should be trimmed",
        }
    ],
}


def test_polymarket_flatten_parses_json_string_fields():
    rows = pm.flatten_markets([PM_EVENT], TS)
    assert len(rows) == 1
    r = rows[0]
    assert r["platform"] == "polymarket"
    assert r["market_id"] == "562828" and r["event_id"] == "32228"
    assert r["yes_token"] == "111" and r["no_token"] == "222"
    assert r["yes_price"] == 0.565 and r["mid"] == 0.565
    assert r["volume"] == 12163362.5 and r["volume_24h"] == 53552.06
    assert r["tags"] == "midterms,politics"
    assert r["closed"] is False and r["active"] is True


def test_polymarket_trim_event_drops_heavy_fields():
    t = pm.trim_event(PM_EVENT)
    assert "description" not in t["markets"][0]
    assert t["markets"][0]["id"] == 562828


def test_polymarket_book_summary_uses_best_levels_and_depth():
    book = {
        "timestamp": "1789524430000",
        "bids": [
            {"price": "0.40", "size": "100"},
            {"price": "0.50", "size": "10"},
            {"price": "0.55", "size": "5"},
        ],
        "asks": [
            {"price": "0.90", "size": "100"},
            {"price": "0.60", "size": "20"},
            {"price": "0.58", "size": "7"},
        ],
    }
    s = pm.summarize_book(book, market_id="m", token_id="t", snapshot_ts=TS)
    assert s["best_bid"] == 0.55 and s["best_ask"] == 0.58
    assert abs(s["spread"] - 0.03) < 1e-9 and abs(s["mid"] - 0.565) < 1e-9
    assert s["bid_depth_5c"] == 15  # 0.55 and 0.50 are within 5c of best bid
    assert s["ask_depth_5c"] == 27  # 0.58 and 0.60 within 5c of best ask
    assert s["bid_depth_10c"] == 15 and s["ask_depth_10c"] == 27
    assert abs(s["bid_depth_5c_usd"] - (0.55 * 5 + 0.50 * 10)) < 1e-9


def test_polymarket_book_summary_handles_empty_book():
    s = pm.summarize_book({"bids": [], "asks": []}, market_id="m", token_id="t", snapshot_ts=TS)
    assert s["best_bid"] is None and s["spread"] is None and s["bid_depth_5c"] == 0.0


KX_EVENT = {
    "event_ticker": "CONTROLH-2026",
    "series_ticker": "CONTROLH",
    "title": "Which party will win the U.S. House?",
    "category": "Elections",
    "markets": [
        {
            "ticker": "CONTROLH-2026-D",
            "title": "Which party will win the U.S. House?",
            "yes_sub_title": "Democratic Party",
            "status": "active",
            "yes_bid_dollars": "0.6900",
            "yes_ask_dollars": "0.7100",
            "last_price_dollars": "0.7000",
            "volume_fp": "123456.00",
            "volume_24h_fp": "1000.50",
            "open_interest_fp": "3021.88",
            "liquidity_dollars": "0.0000",
            "close_time": "2027-02-01T15:00:00Z",
        }
    ],
}


def test_kalshi_flatten_reads_dollar_fields():
    rows = kx.flatten_markets([KX_EVENT], TS)
    r = rows[0]
    assert r["platform"] == "kalshi" and r["market_id"] == "CONTROLH-2026-D"
    assert r["series"] == "CONTROLH" and r["event_id"] == "CONTROLH-2026"
    assert r["best_bid"] == 0.69 and r["best_ask"] == 0.71 and abs(r["mid"] - 0.70) < 1e-9
    assert r["open_interest"] == 3021.88 and r["volume_24h"] == 1000.5
    assert r["active"] is True and r["closed"] is False


def test_kalshi_orderbook_normalizes_to_yes_terms():
    ob = {
        "orderbook_fp": {
            "yes_dollars": [["0.60", "10"], ["0.69", "5"]],
            "no_dollars": [["0.25", "100"], ["0.29", "50"]],
        }
    }
    s = kx.summarize_orderbook(ob, market_id="CONTROLH-2026-D", snapshot_ts=TS)
    assert s["best_bid"] == 0.69
    assert abs(s["best_ask"] - 0.71) < 1e-9  # 1 - best NO bid (0.29)
    assert abs(s["spread"] - 0.02) < 1e-9
    assert s["bid_depth_5c"] == 5 and s["ask_depth_5c"] == 150  # both NO levels within 5c of 0.29
    assert s["bid_depth_10c"] == 15
