"""The simulations section I quotes: qualitative checks on small runs, so the claims in the text stay true."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.cyber import frozen as F
from pipelines.cyber import power as P
from pipelines.cyber import readings as R


@pytest.fixture(scope="module")
def base():
    return R.build_panel({date(2026, 10, 16): F.load_baseline()}, F.q1_population, min_snapshots=1)


def test_q1_with_no_layer_effect_mostly_ends_undecidable(base) -> None:
    out = P.q1(base, layer_share=0.0, runs=60, draws=200)
    assert out["undecidable"] > 0.7 and out["holds"] < 0.15 and out["falsified"] < 0.3


def test_q1_with_a_strong_layer_effect_mostly_holds(base) -> None:
    assert P.q1(base, layer_share=0.55, runs=60, draws=200)["holds"] > 0.8


def test_q3_holds_at_the_baseline_ratio_and_rarely_at_one_and_a_half(base) -> None:
    assert P.q3(base, ratio=3.15, runs=60, draws=200)["holds"] > 0.75
    assert P.q3(base, ratio=1.5, runs=60, draws=200)["holds"] < 0.4


def test_q4_boundaries_and_defaults() -> None:
    assert P.q4(34, 0.0, runs=100, draws=200)["holds"] > 0.5
    assert P.q4(34, 0.3, runs=100, draws=200)["falsified"] > 0.3


def test_shares_sum_to_one() -> None:
    assert sum(P.shares(["holds", "falsified", "undecidable", "undecidable"]).values()) == pytest.approx(1.0)
