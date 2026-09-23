from types import SimpleNamespace

import httpx
import polars as pl

from pipelines.common.storage import write_parquet
from pipelines.predmarkets import backfill

CUTOFF = "2026-07-25T00:00:00Z"  # GET /historical/cutoff on 2026-09-23
T1, T2 = 1789617600, 1789617600 + 86400  # daily candle ends: 2026-09-17 and 2026-09-18 04:00 UTC


def _live(ts, close, volume="10.00", oi="100.00"):
    return {"end_period_ts": ts, "price": {"close_dollars": close}, "volume_fp": volume, "open_interest_fp": oi}


def _hist(ts, close, volume="10.00", oi="100.00"):
    return {"end_period_ts": ts, "price": {"close": close}, "volume": volume, "open_interest": oi}


def _fake_kalshi(live: dict, historical: dict, calls: list | None = None):
    """KalshiClient stand-in: serves candles by ticker and 404s any ticker it does not hold, like both
    real endpoints (live 404s archived markets, historical 404s markets settled after the cutoff)."""
    calls = [] if calls is None else calls

    def not_found(path):
        req = httpx.Request("GET", "https://api.elections.kalshi.com/trade-api/v2" + path)
        return httpx.HTTPStatusError("404 Not Found", request=req, response=httpx.Response(404, request=req))

    class Fake:
        def __init__(self):
            self.http = SimpleNamespace(min_interval=0.0)

        def historical_cutoff(self):
            return {"market_settled_ts": CUTOFF}

        def candlesticks(self, series_ticker, ticker, *, start_ts, end_ts, period_interval=1440):
            calls.append(("live", ticker))
            if ticker not in live:
                raise not_found(f"/series/{series_ticker}/markets/{ticker}/candlesticks")
            return {"ticker": ticker, "candlesticks": live[ticker]}

        def historical_candlesticks(self, ticker, *, start_ts, end_ts, period_interval=1440):
            calls.append(("historical", ticker))
            if ticker not in historical:
                raise not_found(f"/historical/markets/{ticker}/candlesticks")
            return {"ticker": ticker, "candlesticks": historical[ticker]}

    return Fake


def _dim(*markets):
    """(ticker, close_time) pairs as Kalshi rows of the market dimension."""
    return pl.DataFrame(
        {
            "platform": ["kalshi"] * len(markets),
            "market_id": [m for m, _ in markets],
            "series": ["KXFEDDECISION"] * len(markets),
            "end_date": [c for _, c in markets],
        }
    )


def test_kalshi_candle_row_reads_live_and_historical_names():
    live = _live(T1, "0.8800", volume="2084141.43", oi="10984000.31")
    hist = _hist(T1, "0.8800", volume="2084141.43", oi="10984000.31")
    expected = {
        "platform": "kalshi",
        "market_id": "KXFEDDECISION-26SEP-H25",
        "date": "2026-09-17",
        "price": 0.88,
        "volume": 2084141.43,
        "open_interest": 10984000.31,
    }
    assert backfill.kalshi_candle_row(live, "KXFEDDECISION-26SEP-H25") == expected
    assert backfill.kalshi_candle_row(hist, "KXFEDDECISION-26SEP-H25") == expected
    assert backfill.kalshi_candle_row(_hist(T1, None), "m") is None  # no trade that day
    assert backfill.kalshi_candle_row(_live(T1, None), "m") is None


def test_kalshi_routes_archived_markets_to_historical_endpoint(monkeypatch):
    archived, stale, live = "KXFEDDECISION-26JUN-H0", "KXFEDDECISION-26JUL-H0", "KXFEDDECISION-26OCT-H0"
    calls: list = []
    fake = _fake_kalshi(
        live={live: [_live(T1, "0.4700")]},
        historical={archived: [_hist(T1, "0.9900"), _hist(T2, None)], stale: [_hist(T1, "0.0100")]},
        calls=calls,
    )
    monkeypatch.setattr(backfill, "KalshiClient", fake)
    dim = _dim(
        (archived, "2026-06-17T17:59:00Z"),  # closed before the cutoff
        (stale, "2026-09-30T17:59:00Z"),  # the dim's close time is out of date: live 404s, historical has it
        (live, "2026-10-28T17:59:00Z"),
    )
    df = backfill.backfill_kalshi(dim)
    assert calls == [("historical", archived), ("live", stale), ("historical", stale), ("live", live)]
    assert dict(zip(df["market_id"], df["price"], strict=True)) == {archived: 0.99, stale: 0.01, live: 0.47}


def test_kalshi_404_keeps_stored_rows(tmp_path, monkeypatch):
    """The data-loss bug: a market whose candles 404 used to vanish from the rewritten file."""
    gone, fetched = "KXFEDDECISION-26SEP-H25", "KXFEDDECISION-26OCT-H0"
    path = tmp_path / "predmarkets" / "fomc" / "history" / "kalshi.parquet"
    stored = pl.DataFrame(
        {
            "platform": ["kalshi"] * 3,
            "market_id": [gone, gone, fetched],
            "date": ["2026-09-16", "2026-09-17", "2026-09-17"],
            "price": [0.87, 0.88, 0.40],
            "volume": [5.0, 6.0, 1.0],
            "open_interest": [50.0, 60.0, 10.0],
        },
        schema=backfill.HIST_DTYPES,
    )
    write_parquet(stored, path)
    monkeypatch.setattr(backfill, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(
        backfill.read, "dim", lambda name: _dim((gone, "2026-09-16T17:59:00Z"), (fetched, "2026-10-28T17:59:00Z"))
    )
    # `gone` 404s on both endpoints; `fetched` returns a revised close for 09-17 and a new day
    monkeypatch.setattr(
        backfill,
        "KalshiClient",
        _fake_kalshi(live={fetched: [_live(T1, "0.4600"), _live(T2, "0.4800")]}, historical={}),
    )

    assert backfill.main(["--set", "fomc", "--platform", "kalshi"]) == 0

    out = pl.read_parquet(path)
    assert out.filter(pl.col("market_id") == gone).sort("date").equals(stored.filter(pl.col("market_id") == gone))
    refreshed = out.filter(pl.col("market_id") == fetched).sort("date")
    assert refreshed["date"].to_list() == ["2026-09-17", "2026-09-18"]
    assert refreshed["price"].to_list() == [0.46, 0.48]  # this run wins the shared date
