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
