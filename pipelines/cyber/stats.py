"""The estimators section I registers, written once so the baseline reading and the verdicts use the same code.

Nothing here knows about companies. Inputs are arrays; every random procedure takes a seed and the registered
seed and draw count are the defaults, so a verdict is reproducible to the digit from the committed tables.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence

import numpy as np

DRAWS = 10_000
SEED = 20261026
MIN_GROUP = 3  # a layer with fewer members than this is merged into "other"
OTHER = "other"


def _design(columns: Sequence[np.ndarray]) -> np.ndarray:
    n = len(columns[0])
    return np.column_stack([np.ones(n), *columns])


def ols(y: np.ndarray, columns: Sequence[np.ndarray]) -> tuple[np.ndarray, float, float]:
    """Least squares with an intercept: coefficients (intercept first), R squared, adjusted R squared."""
    X = _design(columns)
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res, ss_tot = float(resid @ resid), float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    adj = 1.0 - (1.0 - r2) * (n - 1) / (n - k) if n > k else float("nan")
    return beta, r2, adj


def merge_small(labels: Sequence[str], minimum: int = MIN_GROUP) -> list[str]:
    """Layers of fewer than ``minimum`` members become one ``other`` group; if that group is itself smaller than
    ``minimum`` it joins the largest remaining layer, so no group in the design has one or two companies."""
    counts = {x: labels.count(x) for x in set(labels)}
    merged = [x if counts[x] >= minimum else OTHER for x in labels]
    if 0 < merged.count(OTHER) < minimum:
        kept = Counter(x for x in merged if x != OTHER)
        if kept:
            largest = max(sorted(kept), key=lambda g: kept[g])
            merged = [largest if x == OTHER else x for x in merged]
    return merged


def dummies(labels: Sequence[str]) -> list[np.ndarray]:
    """One 0/1 column per group except the first in sorted order (the reference group)."""
    groups = sorted(set(labels))
    return [np.array([1.0 if x == g else 0.0 for x in labels]) for g in groups[1:]]


def layer_uplift(y: np.ndarray, growth: np.ndarray, labels: Sequence[str]) -> float:
    """Adjusted R squared of log multiple on growth plus layer groups, minus that of growth alone."""
    _, _, with_layers = ols(y, [growth, *dummies(labels)])
    _, _, without = ols(y, [growth])
    return with_layers - without


def permutation_p(
    y: np.ndarray, growth: np.ndarray, labels: Sequence[str], *, draws: int = DRAWS, seed: int = SEED
) -> float:
    """Share of label permutations whose uplift is at least the observed one (add-one corrected, one-sided)."""
    rng = np.random.default_rng(seed)
    observed = layer_uplift(y, growth, labels)
    labels = list(labels)
    hits = 0
    for _ in range(draws):
        if layer_uplift(y, growth, [labels[i] for i in rng.permutation(len(labels))]) >= observed:
            hits += 1
    return (1 + hits) / (1 + draws)


def bootstrap(n: int, statistic: Callable[[np.ndarray], float], *, draws: int = DRAWS, seed: int = SEED) -> np.ndarray:
    """Statistic over ``draws`` resamples of ``n`` units with replacement; degenerate resamples are skipped."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(draws):
        idx = rng.integers(0, n, n)
        try:
            value = statistic(idx)
        except np.linalg.LinAlgError:
            continue
        if value is not None and math.isfinite(value):
            out.append(value)
    return np.array(out)


def interval(samples: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(samples, [tail, 1.0 - tail])
    return float(lo), float(hi)


def growth_vs_fcf(y: np.ndarray, growth: np.ndarray, fcf: np.ndarray) -> dict[str, float]:
    """log multiple on growth and FCF margin: R squared, the growth coefficient, standardised coefficients."""
    beta, r2, _ = ols(y, [growth, fcf])
    sd_y = float(np.std(y, ddof=1))
    std_g = float(beta[1] * np.std(growth, ddof=1) / sd_y)
    std_f = float(beta[2] * np.std(fcf, ddof=1) / sd_y)
    return {"r2": r2, "b": float(beta[1]), "std_growth": std_g, "std_fcf": std_f, "gap": abs(std_g) - abs(std_f)}


def china_dummy(y: np.ndarray, growth: np.ndarray, is_china: np.ndarray) -> float:
    """Coefficient on a China indicator in log multiple on growth plus the indicator."""
    beta, *_ = ols(y, [growth, is_china.astype(float)])
    return float(beta[2])


def median_ratio(high: np.ndarray, low: np.ndarray) -> float:
    return float(np.median(high) / np.median(low))


def two_group_ratio_interval(
    high: np.ndarray, low: np.ndarray, *, draws: int = DRAWS, seed: int = SEED, level: float = 0.95
) -> tuple[float, float]:
    """Bootstrap interval for the ratio of group medians, resampling each group on its own."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(draws):
        a = high[rng.integers(0, len(high), len(high))]
        b = low[rng.integers(0, len(low), len(low))]
        out.append(np.median(a) / np.median(b))
    return interval(np.array(out), level)


def _ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    for value in np.unique(x):  # average the ranks of ties
        mask = x == value
        ranks[mask] = ranks[mask].mean()
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = _ranks(np.asarray(x, float)), _ranks(np.asarray(y, float))
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def spearman_p_positive(x: np.ndarray, y: np.ndarray, *, draws: int = DRAWS, seed: int = SEED) -> float:
    """One-sided permutation p for a positive rank correlation (add-one corrected)."""
    rng = np.random.default_rng(seed)
    observed = spearman(x, y)
    hits = sum(spearman(x, rng.permutation(y)) >= observed for _ in range(draws))
    return (1 + hits) / (1 + draws)


def spearman_upper_bound(rho: float, n: int, *, z: float = 1.6449) -> float:
    """One-sided 95% upper confidence bound for a rank correlation, by the Fisher transform."""
    if n <= 3:
        return 1.0
    rho = max(-0.999999, min(0.999999, rho))  # a perfect rank correlation has no finite Fisher transform
    return math.tanh(math.atanh(rho) + z / math.sqrt(n - 3))


def binomial_p_at_least(k: int, n: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 1/2), exact."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2**n
