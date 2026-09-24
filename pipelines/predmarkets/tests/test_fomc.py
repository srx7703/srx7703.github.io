import json

import polars as pl

from pipelines.predmarkets import fomc, read, snapshot


def test_bucket_and_meeting_mapping():
    assert fomc.kalshi_meeting("KXFEDDECISION-26SEP") == "2026-09"
    assert fomc.kalshi_meeting("KXFED-26SEP") is None
    assert [
        fomc.kalshi_bucket(x) for x in ("Cut >25bps", "Cut 25bps", "Fed maintains rate", "Hike 25bps", "Hike >25bps")
    ] == ["cut50", "cut25", "hold", "hike25", "hike50"]
    q = "Will the Fed decrease interest rates by 50+ bps after the December 2026 meeting?"
    assert fomc.polymarket_meeting(q) == "2026-12" and fomc.polymarket_bucket(q) == "cut50"
    assert (
        fomc.polymarket_bucket("Will there be no change in Fed interest rates after the October 2026 meeting?")
        == "hold"
    )


def test_resolutions_prefers_kalshi_and_maps_to_meeting(tmp_path, monkeypatch):
    monkeypatch.setattr(fomc, "SNAP_DIR", tmp_path)
    store = tmp_path / "predmarkets" / "fomc"
    store.mkdir(parents=True)
    (store / "resolutions.json").write_text(
        json.dumps(
            {
                "markets": {
                    "kalshi:KXFEDDECISION-26SEP-H25": {
                        "platform": "kalshi",
                        "market_id": "KXFEDDECISION-26SEP-H25",
                        "result": "yes",
                    },
                    "kalshi:KXFEDDECISION-26SEP-H0": {
                        "platform": "kalshi",
                        "market_id": "KXFEDDECISION-26SEP-H0",
                        "result": "no",
                    },
                    "polymarket:1": {"platform": "polymarket", "market_id": "1", "result": "yes"},
                }
            }
        )
    )
    grid = pl.DataFrame(
        {
            "platform": ["kalshi", "kalshi", "polymarket"],
            "market_id": ["KXFEDDECISION-26SEP-H25", "KXFEDDECISION-26SEP-H0", "1"],
            "meeting": ["2026-09", "2026-09", "2026-09"],
            "bucket": ["hike25", "hold", "hold"],
        }
    )
    assert fomc.resolutions(grid) == {"2026-09": "hike25"}  # Kalshi settlement wins over the Polymarket row


def test_full_run_timestamps_reads_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(read, "SNAP_DIR", tmp_path)
    d = tmp_path / "predmarkets" / "midterms"
    d.mkdir(parents=True)
    (d / "runs.jsonl").write_text(
        '{"snapshot_ts": "a", "scope": "full"}\n{"snapshot_ts": "b", "scope": "tier1"}\n{"snapshot_ts": "c"}\n'
    )
    assert read.full_run_timestamps("midterms") == {"a", "c"}


def test_safe_series_swallows_one_failure():
    class Boom:
        def events_for_series(self, s):
            if s == "BAD":
                raise RuntimeError("404")
            return [{"event_ticker": s + "-26"}]

    assert snapshot._safe_series(Boom(), "BAD") == []
    assert snapshot._safe_series(Boom(), "OK") == [{"event_ticker": "OK-26"}]


def test_kalshi_bucket_keys_on_the_ticker_suffix_not_the_reworded_label():
    # labels used on archived events: none of them says "maintain" or "hold", and one contains "cut"
    assert fomc.kalshi_bucket("No change", "KXFEDDECISION-25JAN-H0") == "hold"
    assert fomc.kalshi_bucket("No cut/hike", "KXFEDDECISION-24DEC-H0") == "hold"
    assert fomc.kalshi_bucket("Cut >25bps", "KXFEDDECISION-25MAR-C26") == "cut50"
    # without a ticker the text fallback still gets the old hold labels right
    assert [fomc.kalshi_bucket(x) for x in ("No change", "No cut/hike", "Hike 0bps")] == ["hold", "hold", "hold"]


def test_polymarket_mapping_reads_the_pre_2025_wording():
    q = "Fed raises interest rates by 25+ bps after 2024 May meeting?"
    assert fomc.polymarket_meeting(q) == "2024-05" and fomc.polymarket_bucket(q) == "hike25"
    assert fomc.polymarket_bucket("Fed decreases interest rates by 75+ bps after January 2025 meeting?") == "cut50"
    assert fomc.polymarket_bucket("Fed increases interest rates by 25+ bps after June 2024 meeting?") == "hike25"
    assert fomc.polymarket_bucket("Will the FED change rates to another level after December meeting?") is None


def test_decision_slugs_cover_both_families_only():
    assert fomc.is_decision_slug("fed-decision-in-march-885")
    assert fomc.is_decision_slug("fed-interest-rates-january-2025")
    assert not fomc.is_decision_slug("fed-rate-cuts-in-2026")
    assert not fomc.is_decision_slug("fed-interest-rates-year-end-2026")


def test_ny_date_dates_a_bar_by_the_new_york_day_it_closes():
    df = pl.DataFrame(
        {
            "ts": [
                1789617600,  # 2026-09-17 04:00 UTC: a Kalshi daily candle closing at midnight EDT
                1768435200,  # 2026-01-15 00:00 UTC: a Polymarket daily point, 19:00 EST the day before
                1768453200,  # 2026-01-15 05:00 UTC: midnight EST
                1768478400,  # 2026-01-15 12:00 UTC: morning in New York
            ]
        }
    )
    got = df.select(fomc.ny_date(pl.col("ts")).alias("d"))["d"].to_list()
    assert got == ["2026-09-16", "2026-01-14", "2026-01-14", "2026-01-15"]


def test_daily_prices_moves_backfill_labels_to_price_time(tmp_path, monkeypatch):
    monkeypatch.setattr(fomc, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(fomc, "ARCHIVE_DIR", tmp_path / "predmarkets" / "fomc" / "archive")
    hist = tmp_path / "predmarkets" / "fomc" / "history"
    hist.mkdir(parents=True)
    pl.DataFrame(
        {"platform": ["polymarket"], "market_id": ["1"], "date": ["2026-09-10"], "price": [0.4]}
    ).write_parquet(hist / "polymarket.parquet")
    fomc.ARCHIVE_DIR.mkdir(parents=True)
    pl.DataFrame(
        {"platform": ["kalshi"], "market_id": ["K"], "ts": [1768453200], "price": [0.2], "src": ["trade"]}
    ).write_parquet(fomc.ARCHIVE_DIR / "daily.parquet")
    snaps = pl.DataFrame(
        {
            "platform": ["polymarket", "polymarket"],
            "market_id": ["1", "1"],
            "snapshot_ts": ["2026-09-09T11:30:00+00:00", "2026-09-09T21:00:00+00:00"],
            "mid": [0.5, 0.6],
            "yes_price": [None, None],
        }
    )
    monkeypatch.setattr(read, "quotes", lambda name: snaps.lazy())
    grid = pl.DataFrame(
        {
            "platform": ["polymarket", "kalshi"],
            "market_id": ["1", "K"],
            "meeting": ["2026-10", "2026-01"],
            "bucket": ["hold", "hold"],
        }
    )
    got = {(r["market_id"], r["date"]): r["price"] for r in fomc.daily_prices(grid).iter_rows(named=True)}
    # the backfill point labelled 09-10 is the evening of 09-09, where the later snapshot wins
    assert got == {("1", "2026-09-09"): 0.6, ("K", "2026-01-14"): 0.2}


def test_complete_days_carries_thin_outcomes_and_breaks_at_real_gaps():
    grid = pl.DataFrame(
        {"platform": ["kalshi"] * 2, "market_id": ["H", "K"], "meeting": ["2026-12"] * 2, "bucket": ["hold", "hike25"]}
    )
    prices = pl.DataFrame(
        {
            "platform": ["kalshi"] * 5,
            "market_id": ["H", "H", "H", "K", "K"],
            "meeting": ["2026-12"] * 5,
            "bucket": ["hold", "hold", "hold", "hike25", "hike25"],
            # the hike outcome trades on day 1 and again 20 days later; hold trades throughout
            "date": ["2026-01-01", "2026-01-02", "2026-01-21", "2026-01-01", "2026-01-21"],
            "price": [0.9, 0.8, 0.7, 0.1, 0.3],
        }
    )
    got = fomc.complete_days(prices, grid)
    days = sorted(set(got["date"].to_list()))
    # hold carries 01-02 forward to 01-09 and hike 01-01 to 01-08: complete through 01-08, then a gap
    assert days[0] == "2026-01-01" and days[-2] == "2026-01-08" and days[-1] == "2026-01-21"
    segs = dict(zip(got["date"].to_list(), got["seg"].to_list(), strict=True))
    assert segs["2026-01-08"] != segs["2026-01-21"]
    assert got.filter((pl.col("date") == "2026-01-05") & (pl.col("market_id") == "K"))["price"].item() == 0.1
