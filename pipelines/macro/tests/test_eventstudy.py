import polars as pl
import pytest

from pipelines.macro import build, releases
from pipelines.macro import eventstudy as es
from pipelines.macro.intraday import QUOTE_COLS, candle_quote, price_at


def test_candle_quote_prefers_a_tight_mid_then_the_trade():
    k = {"end_period_ts": 1, "yes_bid": {"close": 0.40}, "yes_ask": {"close": 0.42}, "price": {"close": 0.45}}
    assert candle_quote(k)["price"] == pytest.approx(0.41)
    k["yes_ask"] = {"close": 0.9}
    assert candle_quote(k)["price"] == 0.45
    empty_book = {"end_period_ts": 1, "yes_bid": {"close_dollars": "0"}, "yes_ask": {"close_dollars": "1"}, "price": {}}
    assert candle_quote(empty_book)["price"] is None


def test_price_at_carries_the_last_quote_forward():
    assert price_at([10, 20, 30], [0.1, None, 0.3], 25) == 0.1
    assert price_at([10, 20, 30], [0.1, None, 0.3], 5) is None


def _quotes(rows):
    return pl.DataFrame(
        [
            {"platform": "kalshi", "market_id": m, "ts": t, "bid": None, "ask": None, "trade": None, "price": p}
            for m, t, p in rows
        ],
        schema=QUOTE_COLS,
    )


def test_delta_bps_sums_probability_times_bps_between_two_instants():
    markets = pl.DataFrame(
        {"platform": ["kalshi"] * 3, "market_id": ["C", "H", "K"], "bucket": ["cut25", "hold", "hike25"]}
    )
    q = _quotes([("C", 100, 0.20), ("H", 100, 0.70), ("K", 100, 0.10), ("K", 150, 0.30), ("C", 160, 0.10)])
    d = es.delta_bps(markets, q, 120, 200)["kalshi"]
    # hike25 +0.20 x 25 = +5, cut25 -0.10 x -25 = +2.5
    assert d["d_bps"] == pytest.approx(7.5) and d["coverage"] == "3/3"
    assert d["level_pre_bps"] == pytest.approx(-0.20 * 25 + 0.10 * 25)
    assert es.delta_bps(markets, q, 120, 200)["polymarket"] is None


def test_next_meetings_include_a_decision_later_the_same_day():
    assert es.next_meetings("2025-09-17")[0] == "2025-09"
    assert es.next_meetings("2025-09-18")[0] == "2025-10"


def test_placebo_days_skip_thursdays_releases_and_fomc_days():
    cal = releases.calendar()
    days = es.placebo_days(cal, "2025-09-08", "2025-09-19")
    assert "2025-09-11" not in days  # CPI, and a Thursday
    assert "2025-09-17" not in days and "2025-09-18" not in days  # decision day and the day after
    assert "2025-09-10" not in days  # PPI
    assert "2025-09-08" in days and "2025-09-09" in days


def test_rolling_uses_the_next_meeting_decided_after_the_evening_price():
    grid = pl.DataFrame(
        {
            "platform": ["polymarket"] * 4,
            "market_id": ["a", "b", "c", "d"],
            "meeting": ["2025-09", "2025-09", "2025-10", "2025-10"],
            "bucket": ["hold", "cut25", "hold", "cut25"],
        }
    )
    rows = [
        ("a", "2025-09", "hold", 0.2),
        ("b", "2025-09", "cut25", 0.8),
        ("c", "2025-10", "hold", 0.4),
        ("d", "2025-10", "cut25", 0.6),
    ]
    prices = pl.DataFrame(
        [
            {"platform": "polymarket", "market_id": m, "meeting": mt, "bucket": b, "price": p, "date": d}
            for d in ("2025-09-16", "2025-09-17", "2025-09-18")
            for m, mt, b, p in rows
        ]
    )
    roll = build.rolling(prices, grid, "2025-09-16", "2025-09-18").filter(pl.col("platform") == "polymarket")
    got = {r["date"]: (r["next_meeting"], r["next1_bps"]) for r in roll.iter_rows(named=True)}
    assert got["2025-09-16"] == ("2025-09", -20.0)  # the September decision is still ahead that evening
    assert got["2025-09-17"] == ("2025-10", -15.0)  # decided at 14:00; the evening price looks to October
    nxt2 = {r["date"]: r["next2_bps"] for r in roll.iter_rows(named=True)}
    assert nxt2["2025-09-16"] == -35.0  # September + October
    assert nxt2["2025-09-17"] is None  # October + December, and December is not priced


def test_ols_and_sign_agreement():
    fit = es.ols([1, 2, 3, 4, 5, 6], [2, 4, 6, 8, 10, 12.5])
    assert fit["slope"] == pytest.approx(2.07, abs=0.01) and fit["n"] == 6
    assert es.sign_agreement([(1, 2), (-1, -3), (1, -1), (0, 5)]) == {"n": 3, "same": 2, "share": 0.667}
