"""Hygiene rules for the hand-curated reference files.

These are the rules a reader depends on and that no other check enforces: that the qualifier which
makes a quoted figure honest actually travels with it, that the working notes written for whoever
maintains the file never travel with it, that a column headed "capacity" holds capacity, and that
nothing still marked unverified is in a list the pages render.

`pipelines/valuation/share.py` passes these rows through untouched and `publish.py` writes them into
`data/facts/`, which is served at `site/public/data/facts/`. Every field in a rendered list is
therefore public, so the tests treat the whole row as public except `curator_notes`.
"""

from __future__ import annotations

import json

import pytest

from pipelines.valuation.config import TRACKS
from pipelines.valuation.share import reference_path

# Lists the pages render, per track. `readiness`, `segment_disclosure`, `financials` and
# `pool_verdicts` are working records that no page reads.
RENDERED = {
    "optical": ("cited_share", "segment_revenue"),
    "ssb": ("cited_share", "shipments", "capacity", "segment_revenue"),
    # the power track has no market-share layer: its supply paths are different products, so the page
    # renders none of these lists and its curated data lives under pipelines/power/reference
    "power": (),
}

# Phrases that address the pipeline's own authors rather than a reader. None of them belongs in a
# field that reaches data/facts.
WORKING_NOTE_MARKERS = (
    "before publishing",
    "re-read",
    "re-verify",
    "not re-verified",
    "adversarial",
    "verifier",
    "researcher",
    "carried from the",
    "do not publish",
    "must not be",
    "not checked yet",
)

# A capacity row has to state a rate of production. A target year, a licence ceiling or a maturity
# score does not, and each of those shipped in the capacity column once.
CAPACITY_UNITS = ("gwh", "mwh", "kwh", "cells", "metric ton", "tonne", "ton")
NOT_CAPACITY = ("target year", "trl", "licence ceiling", "license ceiling", "readiness")


def load(track: str) -> dict:
    return json.loads(reference_path(track).read_text(encoding="utf-8"))


def rendered_rows(track: str):
    data = load(track)
    for key in RENDERED[track]:
        for i, row in enumerate(data.get(key, [])):
            yield key, i, row


@pytest.mark.parametrize("track", sorted(TRACKS))
def test_rendered_rows_carry_a_reader_caveat(track: str) -> None:
    """The qualifier that makes a quoted figure honest has to reach the reader with it."""
    missing = [f"{key}[{i}]" for key, i, row in rendered_rows(track) if not (row.get("caveat") or "").strip()]
    assert not missing, f"{track}: rendered rows with no reader-facing caveat: {missing}"


@pytest.mark.parametrize("track", sorted(TRACKS))
def test_no_working_notes_reach_the_reader(track: str) -> None:
    """Working notes live in `curator_notes`, which no page prints and publish.py must not serve."""
    leaks = []
    for key, i, row in rendered_rows(track):
        for field, value in row.items():
            if field == "curator_notes" or not isinstance(value, str):
                continue
            low = value.lower()
            for marker in WORKING_NOTE_MARKERS:
                if marker in low:
                    leaks.append(f"{key}[{i}].{field}: {marker!r}")
    assert not leaks, f"{track}: working notes in reader-facing fields: {leaks}"


@pytest.mark.parametrize("track", sorted(TRACKS))
def test_notes_field_was_split_not_renamed_away(track: str) -> None:
    """The old single `notes` field is gone everywhere, not just in the rendered lists."""
    data = load(track)
    offenders = [
        f"{key}[{i}]"
        for key, rows in data.items()
        if isinstance(rows, list)
        for i, row in enumerate(rows)
        if isinstance(row, dict) and "notes" in row
    ]
    assert not offenders, f"{track}: rows still carrying the un-split `notes` field: {offenders}"


@pytest.mark.parametrize("track", sorted(TRACKS))
def test_rendered_rows_are_not_unverified(track: str) -> None:
    """Nothing still marked low confidence may sit in a list a page renders."""
    bad = [
        f"{key}[{i}] ({row.get('entity') or row.get('ticker')})"
        for key, i, row in rendered_rows(track)
        if row.get("confidence") == "low"
    ]
    assert not bad, f"{track}: low-confidence rows in the published set: {bad}"


@pytest.mark.parametrize("track", sorted(TRACKS))
def test_rendered_rows_are_sourced_and_dated(track: str) -> None:
    unsourced = [f"{key}[{i}]" for key, i, row in rendered_rows(track) if not row.get("source_url")]
    undated = [f"{key}[{i}]" for key, i, row in rendered_rows(track) if not row.get("publish_date")]
    assert not unsourced, f"{track}: rendered rows with no source URL: {unsourced}"
    assert not undated, f"{track}: rendered rows with no publish date: {undated}"


def test_capacity_column_contains_capacity() -> None:
    """A table captioned "Announced capacity" may not hold a target year or a licence ceiling."""
    rows = load("ssb")["capacity"]
    assert rows, "the capacity list is empty; the solid-state page shows shipments and capacity"
    for i, row in enumerate(rows):
        unit = (row.get("unit") or "").lower()
        assert any(u in unit for u in CAPACITY_UNITS), f"capacity[{i}] unit {row.get('unit')!r} states no capacity"
        assert not any(n in unit for n in NOT_CAPACITY), f"capacity[{i}] unit {row.get('unit')!r} is not capacity"
        assert (row.get("start_year") or "").strip(), f"capacity[{i}] has no stated start"


def test_segment_revenue_is_shaped_for_the_share_code() -> None:
    """`share.py` reads value, currency and period off these rows and converts the value."""
    for track in sorted(TRACKS):
        for i, row in enumerate(load(track).get("segment_revenue", [])):
            where = f"{track}.segment_revenue[{i}]"
            assert isinstance(row.get("value"), (int, float)), f"{where}: value must be a number"
            assert row.get("value") > 0, f"{where}: value must be positive"
            assert row.get("ticker"), f"{where}: no ticker"
            for field in ("currency", "period", "basis", "source_name", "source_url", "publish_date"):
                assert (row.get(field) or "").strip(), f"{where}: missing {field}"


def test_optical_segment_revenue_covers_the_diversified_names() -> None:
    """The diversified branch of the share code is dead while this list is empty."""
    tickers = {r["ticker"] for r in load("optical")["segment_revenue"]}
    assert "COHR" in tickers, "Coherent discloses a Datacenter & Communications segment; record it"
    assert len(tickers) >= 2, "at least one other diversified optical name discloses a track revenue line"
