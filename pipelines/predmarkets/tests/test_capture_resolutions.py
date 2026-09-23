import json

from pipelines.predmarkets import snapshot
from pipelines.predmarkets.config import MarketSet


def _market(ticker, result, outcome):
    return {
        "ticker": ticker,
        "result": result,
        "yes_sub_title": outcome,
        "title": f"{outcome}?",
        "settlement_ts": "2026-06-17T18:07:43.507539Z",
    }


def test_capture_resolutions_reads_archived_events_from_historical_markets(tmp_path, monkeypatch):
    fetched: list[str] = []

    class FakeKalshi:
        def iter_events(self, *, status, series_ticker):
            assert (status, series_ticker) == ("settled", "KXFEDDECISION")
            yield {
                "event_ticker": "KXFEDDECISION-26SEP",
                "markets": [_market("KXFEDDECISION-26SEP-H25", "yes", "Hike 25bps")],
            }
            yield {"event_ticker": "KXFEDDECISION-26JUN"}  # settled before the cutoff: listed without markets
            yield {"event_ticker": "KXFEDDECISION-25DEC"}  # same, but recorded by an earlier run

        def iter_historical_markets(self, *, event_ticker):
            fetched.append(event_ticker)
            return iter(
                [
                    _market("KXFEDDECISION-26JUN-H0", "yes", "Fed maintains rate"),
                    _market("KXFEDDECISION-26JUN-H25", "no", "Hike 25bps"),
                ]
            )

    monkeypatch.setattr(snapshot.kx, "KalshiClient", FakeKalshi)
    earlier = {
        "platform": "kalshi",
        "market_id": "KXFEDDECISION-25DEC-C25",
        "event_id": "KXFEDDECISION-25DEC",
        "result": "yes",
    }
    (tmp_path / "resolutions.json").write_text(json.dumps({"markets": {"kalshi:KXFEDDECISION-25DEC-C25": earlier}}))
    ms = MarketSet(name="fomc", polymarket_universe_tags=(), kalshi_tier1_series=("KXFEDDECISION",))

    stats = snapshot.capture_resolutions(ms, tmp_path, "2026-09-23T12:00:00+00:00")

    store = json.loads((tmp_path / "resolutions.json").read_text())["markets"]
    assert fetched == ["KXFEDDECISION-26JUN"]  # one lookup, only for the archived event not yet recorded
    assert stats["added"] == 3 and stats["resolved_total"] == 4
    assert store["kalshi:KXFEDDECISION-26JUN-H0"]["result"] == "yes"
    assert store["kalshi:KXFEDDECISION-26JUN-H0"]["event_id"] == "KXFEDDECISION-26JUN"
    assert store["kalshi:KXFEDDECISION-26JUN-H25"]["result"] == "no"
