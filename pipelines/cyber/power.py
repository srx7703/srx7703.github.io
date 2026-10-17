"""How often each rule reaches a verdict at the sample sizes the baseline gives.

Section I says per item what is expected, and these functions are where the numbers come from. Each simulates a
cross-section like the baseline's under a stated truth, applies the registered rule from ``readings``, and returns the
share of runs ending ``holds``, ``falsified`` and ``undecidable``. Runs and draws are small by default so a test can run
them; the numbers in the section use ``RUNS`` and ``DRAWS``.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from pipelines.cyber import config as C
from pipelines.cyber import readings as R
from pipelines.cyber import stats as S

RUNS = 300
DRAWS = 500


def shares(statuses: list[str]) -> dict[str, float]:
    count = Counter(statuses)
    return {k: count.get(k, 0) / len(statuses) for k in ("holds", "falsified", "undecidable")}


def _panel(
    base: R.Panel, log_mult: np.ndarray, growth: np.ndarray | None = None, fcf: np.ndarray | None = None
) -> R.Panel:
    return R.Panel(
        base.tickers, base.layers, log_mult, base.growth if growth is None else growth,
        base.fcf if fcf is None else fcf, base.n_snapshots, True,
    )  # fmt: skip


def q1(
    base: R.Panel, *, layer_share: float, runs: int = RUNS, draws: int = DRAWS, seed: int = S.SEED
) -> dict[str, float]:
    """Truth: log multiple = the baseline growth-only fit plus noise of the baseline's residual size, plus a layer
    effect carrying ``layer_share`` of the variance the growth-only fit leaves unexplained."""
    rng = np.random.default_rng(seed)
    beta, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(base)), base.growth]), base.log_mult, rcond=None)
    fitted = beta[0] + beta[1] * base.growth
    sd = float(np.std(base.log_mult - fitted, ddof=2))
    labels = sorted(set(base.layers))
    effects = {g: rng.normal() for g in labels}
    layer = np.array([effects[x] for x in base.layers])
    layer = (layer - layer.mean()) / (layer.std() or 1.0) * sd * np.sqrt(layer_share / max(1e-9, 1 - layer_share))
    out = []
    for _ in range(runs):
        y = fitted + layer + rng.normal(scale=sd, size=len(base))
        out.append(R.q1(_panel(base, y), draws=draws)["status"])
    return shares(out)


def q2(
    base: R.Panel, *, b: float, c: float, runs: int = RUNS, draws: int = DRAWS, seed: int = S.SEED
) -> dict[str, float]:
    """Truth: log multiple = a + b·growth + c·FCF margin + noise at the baseline's residual size."""
    rng = np.random.default_rng(seed)
    X = np.column_stack([np.ones(len(base)), base.growth, base.fcf])
    beta, *_ = np.linalg.lstsq(X, base.log_mult, rcond=None)
    sd = float(np.std(base.log_mult - X @ beta, ddof=3))
    out = []
    for _ in range(runs):
        y = beta[0] + b * base.growth + c * base.fcf + rng.normal(scale=sd, size=len(base))
        out.append(R.q2(_panel(base, y), draws=draws)["status"])
    return shares(out)


def q4(n: int, rho: float, *, runs: int = RUNS, draws: int = DRAWS, seed: int = S.SEED) -> dict[str, float]:
    """Truth: upside and return are bivariate normal with correlation ``rho``; n companies."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(runs):
        x = rng.normal(size=n)
        y = rho * x + np.sqrt(1 - rho**2) * rng.normal(size=n)
        out.append(R.q4(x, y, draws=draws)["status"])
    return shares(out)


def q9_noise(
    premium_start: float, trio: np.ndarray, rest: np.ndarray, *, sd: float, runs: int = 2000, seed: int = S.SEED
) -> float:
    """Share of runs in which the end premium beats the start by more than the registered margin although every
    multiple only moves at random (log changes with standard deviation ``sd``) and the premium has no drift."""
    rng = np.random.default_rng(seed)
    limit = 1.0 + float(C.Q9["widening"][:-1]) / 100.0
    hits = 0
    for _ in range(runs):
        end = np.median(trio * np.exp(rng.normal(scale=sd, size=len(trio)))) / np.median(
            rest * np.exp(rng.normal(scale=sd, size=len(rest)))
        )
        hits += end > limit * premium_start
    return hits / runs


def q3(base: R.Panel, *, ratio: float, runs: int = RUNS, draws: int = DRAWS, seed: int = S.SEED) -> dict[str, float]:
    """Truth: the two Rule of 40 groups have the baseline's sizes and within-group spread, and their median multiples
    differ by ``ratio``."""
    rng = np.random.default_rng(seed)
    high = base.rule_of_40 >= float(C.Q3["cut"])
    spread = float(np.sqrt(np.mean([np.var(base.log_mult[high], ddof=1), np.var(base.log_mult[~high], ddof=1)])))
    centre = float(np.log(np.median(base.mult[~high])))
    out = []
    for _ in range(runs):
        y = np.where(high, centre + np.log(ratio), centre) + rng.normal(scale=spread, size=len(base))
        out.append(R.q3(_panel(base, y), draws=draws)["status"])
    return shares(out)
