"""A track that is still collecting is fetched and archived but never built by the default publish."""

from __future__ import annotations

from pipelines.valuation import evaluate, publish
from pipelines.valuation.config import TRACKS, published_tracks


def test_cyber_is_collecting_and_the_four_live_tracks_are_not() -> None:
    assert TRACKS["cyber"]["collecting"] is True
    assert published_tracks() == ["optical", "ssb", "surgical", "power"]


def test_default_publish_leaves_a_collecting_track_alone(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_build(tracks=None, now=None):
        seen.append(list(tracks or []))
        return {}

    monkeypatch.setattr(publish, "build", fake_build)
    monkeypatch.setattr(publish, "append_jsonl", lambda *a, **k: None)
    assert publish.main([]) == 0
    assert seen == [["optical", "ssb", "surgical", "power"]]
    seen.clear()
    assert publish.main(["--track", "cyber"]) == 0
    assert seen == [["cyber"]], "asking for a collecting track by name still builds it"


def test_evaluate_does_not_score_a_collecting_track(monkeypatch) -> None:
    monkeypatch.setattr(evaluate.read, "load_history", lambda *a, **k: {})
    monkeypatch.setattr(evaluate.read, "by_ticker", lambda rows: {})
    assert "cyber" not in evaluate.score_tracks()


def test_fundamentals_default_selection_skips_a_collecting_track() -> None:
    from pipelines.valuation import fundamentals

    assert {c.track for c in fundamentals.select("", "")} == {"optical", "ssb", "surgical", "power"}
    assert [c.ticker for c in fundamentals.select("CRWD", "")] == ["CRWD"], "naming a ticker still reaches it"
