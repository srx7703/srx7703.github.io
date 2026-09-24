from datetime import UTC, datetime

import pytest

from pipelines.predmarkets import archive


def _ts(day: str, hour: int = 0) -> int:
    return int(datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC).timestamp())


def test_candle_price_reads_both_schemas_and_uses_a_tight_mid_on_no_trade_days():
    assert archive.candle_price({"price": {"close_dollars": "0.42"}}) == (0.42, "trade")
    hist = {"price": {"close": None}, "yes_bid": {"close": 0.40}, "yes_ask": {"close": 0.44}}
    price, src = archive.candle_price(hist)
    assert price == pytest.approx(0.42) and src == "quote_mid"
    wide = {"price": {}, "yes_bid": {"close_dollars": "0.10"}, "yes_ask": {"close_dollars": "0.60"}}
    assert archive.candle_price(wide) == (None, None)


class _Clob:
    def __init__(self):
        self.windows = []

    def get_json(self, path, params):
        self.windows.append((params["startTs"], params["endTs"]))
        hours = range(params["startTs"], params["endTs"] + 1, 3600)
        return {"history": [{"t": t, "p": 0.3} for t in hours]}


class _PC:
    def __init__(self, days):
        self.days = days
        self.clob = _Clob()

    def prices_history(self, token, interval, fidelity):
        return {"history": [{"t": _ts(d), "p": 0.5} for d in self.days]}


def test_polymarket_gaps_are_refilled_in_windows_of_at_most_seven_days():
    # daily points on Jan 1 and Jan 20 only, then one on Mar 1: 18 + 39 missing days
    pc = _PC(["2026-01-01", "2026-01-20", "2026-03-01"])
    rows = archive.polymarket_daily(pc, "tok", "m1")
    assert max(end - start for start, end in pc.clob.windows) <= 8 * 86400
    fills = [r for r in rows if r["src"] == "hourly_fill"]
    assert len(fills) == 18 + 39
    # each fill is the last hourly point at or before 00:00 UTC of the missing day
    assert all(r["ts"] % 86400 == 0 for r in fills)


class _KC:
    def candlesticks(self, series, ticker, **kw):
        return {
            "candlesticks": [
                {"end_period_ts": 1_767_000_000, "price": {"close_dollars": "0.30"}},
                {
                    "end_period_ts": 1_767_086_400,
                    "price": {},
                    "yes_bid": {"close_dollars": "0.10"},
                    "yes_ask": {"close_dollars": "0.40"},
                },
            ]
        }


def test_unpriced_candles_are_kept_as_no_price_rows_only_when_asked():
    rows = archive.kalshi_daily(_KC(), "K", "live", 0, 1, keep_unpriced=True)
    assert [(r["price"], r["src"]) for r in rows] == [(0.30, "trade"), (None, "no_price")]
    assert [r["src"] for r in archive.kalshi_daily(_KC(), "K", "live", 0, 1)] == ["trade"]
    archive.DAILY_SCHEMA.validate(
        __import__("polars").DataFrame(
            [{"platform": "kalshi", "market_id": "K", **{k: r[k] for k in ("ts", "price", "src")}} for r in rows],
            schema=archive.DAILY_COLS,
        )
    )
