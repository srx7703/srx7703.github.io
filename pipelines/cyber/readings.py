"""The registered readings: each item's statistics and status, composed from ``frozen`` and ``stats``.

Section I fixes what every item measures and how it is judged; this module is that text as code.
``tests/test_readings.py`` reproduces the numerical "already seen" lines of section I from the baseline snapshot
through it, so the lines are the output of the registered procedure and not a separate calculation. Two conventions
the text leaves to the code are pinned here: a company keeps its (merged) layer label through every bootstrap draw and a
permutation shuffles those labels, and a bootstrap that resamples "companies" resamples the pooled list.

Q5 (needs Yahoo's daily history) and Q6 (needs ring C) are added with their inputs, by an amendment that registers the
code before their first reading. Q8 is a hand-checked count of announcements.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import stats as S

PRICED_SHARE = 0.8  # a Monday on which fewer ring A listings were priced is not a snapshot
MIN_SNAPSHOTS = 10  # fewer snapshots in the window and Q1, Q2, Q3 and Q7 are undecidable
PANEL_SHARE = 0.75  # a company is in the panel if it is computable in this share of the snapshots there are
MONDAY = 0
MOVE = 0.005  # Q10: a revision of 0.5% or more
Q10_FOLLOW = 4  # weeks between the second snapshot of a pair and its follow-up


# --- verdicts ------------------------------------------------------------------------------------------------------
def grade(*, precondition: bool, holds: bool, falsified: bool) -> str:
    """The order in 'How every item is judged': precondition, then both-met, then Falsified, then Holds."""
    if not precondition or (holds and falsified):
        return "undecidable"
    if falsified:
        return "falsified"
    if holds:
        return "holds"
    return "undecidable"


# --- snapshots and the panel ---------------------------------------------------------------------------------------
def weekly_snapshots(frame: pl.DataFrame, *, n_ring_a: int, weekday: int | None = MONDAY) -> dict[date, pl.DataFrame]:
    """Per UTC date, the last run in which at least ``PRICED_SHARE`` of ring A was priced; Mondays only by default."""
    chosen: dict[date, tuple[str, pl.DataFrame]] = {}
    for part in frame.partition_by("snapshot_ts", maintain_order=True):
        ts = part["snapshot_ts"][0]
        if part["price"].is_not_null().sum() < PRICED_SHARE * n_ring_a:
            continue
        day = date.fromisoformat(ts[:10])
        if weekday is not None and day.weekday() != weekday:
            continue
        if day not in chosen or ts > chosen[day][0]:
            chosen[day] = (ts, part)
    return {day: part for day, (_, part) in sorted(chosen.items())}


@dataclass(frozen=True)
class Panel:
    tickers: tuple[str, ...]
    layers: tuple[str, ...]  # layer groups after merging layers of fewer than three members into "other"
    log_mult: np.ndarray  # mean over the weeks present of log(EV/NTM revenue)
    growth: np.ndarray
    fcf: np.ndarray
    n_snapshots: int
    enough_snapshots: bool

    @property
    def mult(self) -> np.ndarray:
        """Geometric: the exponential of the mean log multiple, so a median of these is a median of the panel means."""
        return np.exp(self.log_mult)

    @property
    def rule_of_40(self) -> np.ndarray:
        return 100.0 * (self.growth + self.fcf)

    def __len__(self) -> int:
        return len(self.tickers)


def build_panel(
    snapshots: dict[date, pl.DataFrame],
    population: Callable[[pl.DataFrame], list[str]],
    *,
    need_fcf: bool = True,
    min_snapshots: int = MIN_SNAPSHOTS,
    merge_below: int = S.MIN_GROUP,
) -> Panel:
    """One row per company: the mean over the snapshots it is computable in. A company is in the panel if it is
    computable in at least ``PANEL_SHARE`` of the snapshots there are (9 of 12), using every week it has data."""
    n = len(snapshots)
    sums: dict[str, list[float]] = {}
    for as_of, frame in snapshots.items():
        rows = {r["ticker"]: r for r in frame.iter_rows(named=True)}
        for ticker in population(frame):
            row = rows[ticker]
            mult, growth, fcf = F.ev_ntm(row, as_of), F.growth(row, as_of), F.fcf_margin(row)
            if mult is None or growth is None or (need_fcf and fcf is None):
                continue
            acc = sums.setdefault(ticker, [0, 0.0, 0.0, 0.0])
            acc[0] += 1
            acc[1] += math.log(mult)
            acc[2] += growth
            acc[3] += fcf if fcf is not None else 0.0
    required = math.ceil(PANEL_SHARE * n) if n else 1
    members = sorted(t for t, acc in sums.items() if acc[0] >= required)
    pool = F.registered_pool()
    labels = S.merge_small([pool[t].layer for t in members], merge_below) if members else []
    return Panel(
        tickers=tuple(members),
        layers=tuple(labels),
        log_mult=np.array([sums[t][1] / sums[t][0] for t in members]),
        growth=np.array([sums[t][2] / sums[t][0] for t in members]),
        fcf=np.array([sums[t][3] / sums[t][0] for t in members]),
        n_snapshots=n,
        enough_snapshots=n >= min_snapshots,
    )


# --- Q1 -------------------------------------------------------------------------------------------------------------
def q1(panel: Panel, *, draws: int = S.DRAWS) -> dict[str, Any]:
    y, g, labels = panel.log_mult, panel.growth, list(panel.layers)
    uplift = S.layer_uplift(y, g, labels)
    p = S.permutation_p(y, g, labels, draws=draws)
    boot = S.bootstrap(len(y), lambda i: S.layer_uplift(y[i], g[i], [labels[k] for k in i]), draws=draws)
    lo, hi = S.interval(boot, 0.90)
    _, _, adj_growth = S.ols(y, [g])
    precondition = panel.enough_snapshots and len(panel) >= int(C.Q1["min_n"])
    status = grade(
        precondition=precondition,
        holds=uplift >= float(C.Q1["uplift_holds"]) and p <= float(C.Q1["p_holds"]),
        falsified=hi < float(C.Q1["uplift_holds"]),
    )
    return {
        "n": len(panel),
        "adj_r2_growth": adj_growth,
        "uplift": uplift,
        "perm_p": p,
        "lo": lo,
        "hi": hi,
        "status": status,
    }


# --- Q2 -------------------------------------------------------------------------------------------------------------
def q2(panel: Panel, *, draws: int = S.DRAWS) -> dict[str, Any]:
    y, g, f = panel.log_mult, panel.growth, panel.fcf
    fit = S.growth_vs_fcf(y, g, f)
    b = S.interval(S.bootstrap(len(y), lambda i: S.growth_vs_fcf(y[i], g[i], f[i])["b"], draws=draws))
    gap = S.interval(S.bootstrap(len(y), lambda i: S.growth_vs_fcf(y[i], g[i], f[i])["gap"], draws=draws))
    precondition = panel.enough_snapshots and len(panel) >= int(C.Q2["min_n"])
    status = grade(
        precondition=precondition,
        holds=fit["r2"] >= float(C.Q2["r2_holds"]) and b[0] > 0 and gap[0] > 0,
        falsified=b[1] <= 0 or gap[1] < 0,
    )
    return {"n": len(panel), **fit, "b_lo": b[0], "b_hi": b[1], "gap_lo": gap[0], "gap_hi": gap[1], "status": status}


# --- Q3 -------------------------------------------------------------------------------------------------------------
def q3(panel: Panel, *, draws: int = S.DRAWS) -> dict[str, Any]:
    r40, mult = panel.rule_of_40, panel.mult
    high, low = mult[r40 >= float(C.Q3["cut"])], mult[r40 < float(C.Q3["cut"])]
    minimum = int(C.Q3["min_group_n"])
    if len(high) == 0 or len(low) == 0:
        return {"n_high": len(high), "n_low": len(low), "ratio": float("nan"), "status": "undecidable"}
    ratio = S.median_ratio(high, low)
    lo, hi = S.two_group_ratio_interval(high, low, draws=draws)
    status = grade(
        precondition=panel.enough_snapshots and len(high) >= minimum and len(low) >= minimum,
        holds=ratio >= float(C.Q3["ratio_holds"]),
        falsified=hi < float(C.Q3["ratio_holds"]),
    )
    low_tickers = [t for t, v in zip(panel.tickers, r40, strict=True) if v < float(C.Q3["cut"])]
    return {
        "n_high": len(high),
        "n_low": len(low),
        "median_high": float(np.median(high)),
        "median_low": float(np.median(low)),
        "ratio": ratio,
        "lo": lo,
        "hi": hi,
        "low_tickers": low_tickers,
        "status": status,
    }


# --- Q7 -------------------------------------------------------------------------------------------------------------
def q7(us: Panel, cn: Panel, *, draws: int = S.DRAWS) -> dict[str, Any]:
    """The China indicator in log multiple on growth over the pooled companies; the ratio of medians is reported."""
    y = np.concatenate([us.log_mult, cn.log_mult])
    g = np.concatenate([us.growth, cn.growth])
    is_cn = np.concatenate([np.zeros(len(us)), np.ones(len(cn))])
    coef = S.china_dummy(y, g, is_cn)
    lo, hi = S.interval(
        S.bootstrap(len(y), lambda i: S.china_dummy(y[i], g[i], is_cn[i]), draws=draws), float(C.Q7["level"][:2]) / 100
    )
    ratio = S.median_ratio(cn.mult, us.mult) if len(cn) and len(us) else float("nan")
    r_lo, r_hi = (
        S.two_group_ratio_interval(cn.mult, us.mult, draws=draws) if len(cn) and len(us) else (float("nan"),) * 2
    )
    status = grade(
        precondition=us.enough_snapshots and cn.enough_snapshots and len(cn) >= int(C.Q7["min_n"]),
        holds=hi < 0,
        falsified=lo > 0,
    )
    return {
        "n_cn": len(cn),
        "n_us": len(us),
        "coef": coef,
        "lo": lo,
        "hi": hi,
        "ratio": ratio,
        "ratio_lo": r_lo,
        "ratio_hi": r_hi,
        "status": status,
    }


# --- Q9 -------------------------------------------------------------------------------------------------------------
def premium(frame: pl.DataFrame, as_of: date, population: list[str]) -> float | None:
    rows = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    trio = [F.ev_ntm(rows[t], as_of) for t in C.Q9["trio"] if t in rows]
    rest = [F.ev_ntm(rows[t], as_of) for t in population if t not in C.Q9["trio"]]
    trio, rest = [v for v in trio if v], [v for v in rest if v]
    if len(trio) < len(C.Q9["trio"]) or len(rest) < int(C.Q9["min_rest"]):
        return None
    return float(np.median(trio) / np.median(rest))


def q9(snapshots: dict[date, pl.DataFrame], *, start: list[date], end: list[date]) -> dict[str, Any]:
    def mean_premium(days: list[date]) -> float | None:
        values = [premium(snapshots[d], d, F.q1_population(snapshots[d])) for d in days if d in snapshots]
        return None if len(values) < len(days) or any(v is None for v in values) else float(np.mean(values))

    p0, p1 = mean_premium(start), mean_premium(end)
    ok = p0 is not None and p1 is not None
    widening = 1.0 + float(C.Q9["widening"][:-1]) / 100.0
    status = grade(precondition=ok, holds=ok and p1 <= widening * p0, falsified=ok and p1 > widening * p0)
    return {"start": p0, "end": p1, "status": status}


# --- Q4 -------------------------------------------------------------------------------------------------------------
def q4(upside: np.ndarray, total_return: np.ndarray, *, draws: int = S.DRAWS) -> dict[str, Any]:
    n = len(upside)
    rho = S.spearman(upside, total_return)
    if n < 4 or math.isnan(rho):
        return {"n": n, "rho": rho, "status": "undecidable"}
    p = S.spearman_p_positive(upside, total_return, draws=draws)
    upper = S.spearman_upper_bound(rho, n)
    status = grade(
        precondition=n >= int(C.Q4["min_n"]),
        holds=upper < float(C.Q4["upper_bound"]),
        falsified=rho > 0 and p < float(C.Q4["p"]),
    )
    return {"n": n, "rho": rho, "p": p, "upper": upper, "status": status}


# --- Q10 ------------------------------------------------------------------------------------------------------------
def tracked_series(snapshots: dict[date, pl.DataFrame], ticker: str) -> dict[date, float]:
    """Revenue consensus of the fiscal year that is FY+1 on the first snapshot, followed by its end year: it is
    ``rev_fy1_avg`` while Yahoo's fiscal year 0 is the year before it and ``rev_fy0_avg`` once Yahoo rolls to it."""
    days = sorted(snapshots)
    rows = {d: next((r for r in snapshots[d].iter_rows(named=True) if r["ticker"] == ticker), None) for d in days}
    first = next((rows[d] for d in days if rows[d] and rows[d]["fy0_end"] and rows[d]["rev_fy1_avg"]), None)
    if first is None:
        return {}
    target_year = int(first["fy0_end"][:4]) + 1
    out: dict[date, float] = {}
    for d in days:
        row = rows[d]
        if not row or not row["fy0_end"]:
            continue
        year = int(row["fy0_end"][:4])
        value = row["rev_fy1_avg"] if year == target_year - 1 else row["rev_fy0_avg"] if year == target_year else None
        if value:
            out[d] = float(value)
    return out


def q10_pairs(series: dict[date, float]) -> list[tuple[float, float]]:
    """Pairs (move t-1 -> t, move t -> t+4 weeks) on exact Monday dates; a company's next pair starts at t+5, and a
    missing Monday voids any pair that uses it."""
    pairs: list[tuple[float, float]] = []
    days = sorted(series)
    blocked_until: date | None = None
    for t in days:
        before, after = t - timedelta(weeks=1), t + timedelta(weeks=Q10_FOLLOW)
        if blocked_until and t < blocked_until + timedelta(weeks=1):
            continue
        if before in series and after in series and series[before] > 0 and series[t] > 0:
            pairs.append((series[t] / series[before] - 1.0, series[after] / series[t] - 1.0))
            blocked_until = after
    return pairs


def q10(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    tested = [(a, b) for a, b in pairs if abs(a) >= MOVE and abs(b) >= MOVE]
    flat = sum(1 for a, b in pairs if abs(a) >= MOVE and abs(b) < MOVE)
    same = sum(1 for a, b in tested if a * b > 0)
    opposite = len(tested) - same
    p_same = S.binomial_p_at_least(same, len(tested)) if tested else 1.0
    p_opposite = S.binomial_p_at_least(opposite, len(tested)) if tested else 1.0
    level = float(C.Q10["level"][:-1]) / 100.0
    status = grade(
        precondition=len(tested) >= int(C.Q10["min_pairs"]),
        holds=same > opposite and p_same <= level,
        falsified=opposite > same and p_opposite <= level,
    )
    return {"tested": len(tested), "same": same, "opposite": opposite, "flat": flat, "p_same": p_same, "status": status}
