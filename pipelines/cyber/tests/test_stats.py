"""The registered estimators on cases with known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pipelines.cyber import stats


def test_ols_matches_a_known_line() -> None:
    x = np.arange(10, dtype=float)
    y = 3.0 + 2.0 * x
    beta, r2, adj = stats.ols(y, [x])
    assert beta == pytest.approx([3.0, 2.0]) and r2 == pytest.approx(1.0) and adj == pytest.approx(1.0)


def test_adjusted_r2_penalises_a_useless_regressor() -> None:
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=30), rng.normal(size=30)
    _, r2a, adj_a = stats.ols(y, [x])
    _, r2b, adj_b = stats.ols(y, [x, rng.normal(size=30)])
    assert r2b >= r2a, "an added regressor cannot lower R squared"
    assert adj_a < r2a and adj_b < r2b, "adjusted R squared sits below R squared"
    assert adj_b == pytest.approx(1 - (1 - r2b) * 29 / 27)


def test_small_layers_are_merged_into_other() -> None:
    labels = ["a"] * 4 + ["b"] * 3 + ["c", "d", "e"]
    assert stats.merge_small(labels) == ["a"] * 4 + ["b"] * 3 + ["other", "other", "other"]


def test_layer_uplift_is_large_for_a_real_layer_effect_and_near_zero_for_none() -> None:
    rng = np.random.default_rng(2)
    labels = ["a"] * 6 + ["b"] * 6 + ["c"] * 6
    growth = rng.normal(size=18)
    effect = np.array([{"a": 0.0, "b": 2.0, "c": -2.0}[g] for g in labels])
    assert stats.layer_uplift(growth + effect + rng.normal(scale=0.2, size=18), growth, labels) > 0.5
    assert abs(stats.layer_uplift(rng.normal(size=18), growth, labels)) < 0.3


def test_permutation_p_is_small_for_a_real_effect_and_seeded() -> None:
    rng = np.random.default_rng(3)
    labels = ["a"] * 6 + ["b"] * 6 + ["c"] * 6
    growth = rng.normal(size=18)
    effect = np.array([{"a": 0.0, "b": 2.0, "c": -2.0}[g] for g in labels])
    y = growth + effect + rng.normal(scale=0.3, size=18)
    p1 = stats.permutation_p(y, growth, labels, draws=500, seed=7)
    assert p1 < 0.05 and p1 == stats.permutation_p(y, growth, labels, draws=500, seed=7)


def test_bootstrap_skips_degenerate_resamples_and_is_deterministic() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    def stat(idx: np.ndarray) -> float:
        return float(x[idx].mean())

    a = stats.bootstrap(5, stat, draws=200, seed=1)
    assert len(a) == 200 and np.array_equal(a, stats.bootstrap(5, stat, draws=200, seed=1))
    lo, hi = stats.interval(a)
    assert 1.0 <= lo < 3.0 < hi <= 5.0


def test_growth_vs_fcf_recovers_coefficients() -> None:
    rng = np.random.default_rng(4)
    g, f = rng.normal(size=40), rng.normal(size=40)
    y = 2.0 * g + 0.2 * f + rng.normal(scale=0.05, size=40)
    fit = stats.growth_vs_fcf(y, g, f)
    assert fit["b"] == pytest.approx(2.0, abs=0.1) and fit["gap"] > 0.5 and fit["r2"] > 0.95


def test_china_dummy_recovers_a_level_shift() -> None:
    rng = np.random.default_rng(5)
    g = rng.normal(size=30)
    is_cn = np.array([False] * 20 + [True] * 10)
    y = g - 1.0 * is_cn + rng.normal(scale=0.05, size=30)
    assert stats.china_dummy(y, g, is_cn) == pytest.approx(-1.0, abs=0.1)


def test_spearman_with_ties_and_its_bounds() -> None:
    assert stats.spearman(np.array([1, 2, 3, 4.0]), np.array([10, 20, 30, 40.0])) == pytest.approx(1.0)
    assert stats.spearman(np.array([1, 2, 3, 4.0]), np.array([4, 3, 2, 1.0])) == pytest.approx(-1.0)
    assert stats.spearman(np.array([1, 1, 2, 2.0]), np.array([1, 2, 1, 2.0])) == pytest.approx(0.0)
    assert math.isnan(stats.spearman(np.array([1, 1, 1.0]), np.array([1, 2, 3.0])))
    assert stats.spearman_upper_bound(0.0, 34) == pytest.approx(0.2871, abs=0.001)  # tanh(1.6449 / sqrt(31))


def test_spearman_permutation_p_is_small_only_for_a_positive_relation() -> None:
    rng = np.random.default_rng(6)
    x = rng.normal(size=34)
    assert stats.spearman_p_positive(x, x + rng.normal(scale=0.3, size=34), draws=500, seed=1) < 0.01
    assert stats.spearman_p_positive(x, -x, draws=500, seed=1) > 0.9


def test_binomial_tail_is_exact() -> None:
    assert stats.binomial_p_at_least(0, 10) == 1.0
    assert stats.binomial_p_at_least(8, 10) == pytest.approx(56 / 1024)
    assert stats.binomial_p_at_least(10, 10) == pytest.approx(1 / 1024)


def test_ratio_interval_brackets_the_point_estimate() -> None:
    high, low = np.array([9.0, 10.0, 11.0, 12.0, 8.0]), np.array([3.0, 4.0, 5.0, 4.5, 3.5, 4.2])
    lo, hi = stats.two_group_ratio_interval(high, low, draws=500, seed=1)
    assert lo < stats.median_ratio(high, low) < hi


def test_upper_bound_survives_a_perfect_correlation_and_a_tiny_sample() -> None:
    assert stats.spearman_upper_bound(1.0, 20) == pytest.approx(1.0, abs=1e-5)
    assert stats.spearman_upper_bound(-1.0, 20) < -0.99
    assert stats.spearman_upper_bound(0.5, 3) == 1.0


def test_a_merged_group_of_one_or_two_joins_the_largest_layer() -> None:
    labels = ["a"] * 5 + ["b"] * 3 + ["c", "d"]
    assert stats.merge_small(labels) == ["a"] * 5 + ["b"] * 3 + ["a", "a"], "two leftovers are not a group"
    labels = ["a"] * 5 + ["b"] * 3 + ["c", "d", "e"]
    assert stats.merge_small(labels).count("other") == 3, "three leftovers are"


def test_the_registered_draw_count_and_seed_are_what_the_estimators_use() -> None:
    from pipelines.cyber import config as C

    assert (stats.DRAWS, str(stats.SEED)) == (10_000, C.SEED)
