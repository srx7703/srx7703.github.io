"""The page regression and the AI-trio premium, by week.

Section I registers its verdicts on a 12-week panel and says the weekly regressions "are drawn on the page as a
stability chart and do not count toward a verdict". This module is that chart's data. A weekly point is one usable
run read on its own: the registered population of that week (``frozen.q1_population`` plus the frozen A-share group)
goes through ``readings.build_panel`` with one snapshot, so a company enters a weekly point by exactly the rule that
admits it to the panel, and the estimators are ``stats.ols`` / ``stats.bootstrap`` / ``stats.interval`` and not a copy.

The model is the page's, not one of the registered ones::

    ln(EV/NTM revenue) = a + b * growth + c * FCF margin + d * China + layer groups

fitted on the US population and the A-share group together. It is wider than Q1 (growth and layers), Q2 (growth and
FCF margin) or Q7 (growth and a China indicator), so a point here is *not* a verdict reading and is never labelled as
one; ``adj_r2 - adj_r2_no_layer`` is the layer gain of this model, not Q1's uplift.

Three conventions the text leaves to the code, pinned here:

* **Dates.** A weekly point is keyed by the UTC date of its run, as ``readings.weekly_snapshots`` keys it, and days to
  the end of fiscal year 0 are counted from that date. The baseline file is named for its UTC date (2026-10-17) but
  was taken after the US close of 2026-10-16, and section I counts from 2026-10-16; ``market_date`` maps the one to
  the other and nothing else.
* **Series.** The series is the Monday runs from ``config.FIRST_SNAPSHOT`` on. The baseline is never a series point
  (it is a Saturday by its file date, and is also excluded by name); it is returned separately as ``baseline``. The
  ``latest`` point is the newest usable run of any weekday, so it can be a run that is not in the series.
* **Layer groups.** Merged on the pooled US + A-share companies of the week (a layer with fewer than
  ``config.Q1['merge_below']`` members becomes ``other``, an ``other`` of fewer than that joins the largest layer),
  and kept fixed inside every bootstrap draw, as section I does for its panel. The reference group is the first in
  sorted order; its coefficient is 0 by construction and is listed with ``reference = True``.

A week that cannot be fitted (fewer companies than parameters, or a rank-deficient design) is still returned, with
``fitted = False``, the reason, and null estimates: a gap in the chart has to be explainable.

Everything returned is JSON-ready (dates as ISO strings, no NaN) and has passed the pandera schemas below.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy as np
import pandera.polars as pa
import polars as pl

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import metrics as M
from pipelines.cyber import readings as R
from pipelines.cyber import stats as S
from pipelines.cyber.pool import Entry

TOP_N = 5  # the largest absolute residuals that are flagged


def _level(text: str) -> float:
    return float(text.rstrip("%")) / 100.0


SLOPE_LEVEL = _level(C.Q2["level"])  # also used for the FCF coefficient
CHINA_LEVEL = _level(C.Q7["level"])
MERGE_BELOW = int(C.Q1["merge_below"])
FIRST = date.fromisoformat(C.FIRST_SNAPSHOT)
BASELINE_FILE_DAY = date.fromisoformat(C.BASELINE_FILE_DATE)
BASELINE_MARKET_DAY = date.fromisoformat(C.BASELINE)


# --- dates and snapshots -----------------------------------------------------------------------------------------
def market_date(file_day: date) -> date:
    """The date a run is read at. The baseline file is named 2026-10-17 (UTC) but is the 2026-10-16 close, and section I
    counts days to fiscal year 0 from the market date; every other run is read at its own UTC date."""
    return BASELINE_MARKET_DAY if file_day == BASELINE_FILE_DAY else file_day


def _has_runs(runs: pl.DataFrame | None) -> bool:
    return (
        isinstance(runs, pl.DataFrame)
        and runs.height > 0
        and {"snapshot_ts", "ticker", "price"} <= set(runs.columns)
    )  # fmt: skip


def runs_until(runs: pl.DataFrame | None, today: date) -> pl.DataFrame | None:
    """The runs dated on or before ``today`` (UTC date of the run), or None if there are none. A run cannot be later
    than the day the page is built; the filter makes a back-dated build reproducible."""
    if not _has_runs(runs):
        return None
    frame = runs.filter(pl.col("snapshot_ts").str.slice(0, 10) <= today.isoformat())  # type: ignore[union-attr]
    return frame if frame.height else None


def series_snapshots(runs: pl.DataFrame | None, today: date) -> dict[date, pl.DataFrame]:
    """The Monday snapshots of the formal series: the last usable run of each Monday from ``config.FIRST_SNAPSHOT``,
    never the baseline file's date, none after ``today``. Empty if there are no usable runs."""
    frame = runs_until(runs, today)
    if frame is None:
        return {}
    snaps = R.weekly_snapshots(frame, n_ring_a=M.n_ring_a())
    return {d: f for d, f in snaps.items() if d >= FIRST and d != BASELINE_FILE_DAY}


def baseline_frame(runs: pl.DataFrame | None) -> pl.DataFrame | None:
    """The last usable run stored under the baseline file's date, or None if the baseline is not among ``runs``."""
    if not _has_runs(runs):
        return None
    day = runs.filter(pl.col("snapshot_ts").str.slice(0, 10) == C.BASELINE_FILE_DATE)  # type: ignore[union-attr]
    if day.is_empty():
        return None
    return R.weekly_snapshots(day, n_ring_a=M.n_ring_a(), weekday=None).get(BASELINE_FILE_DAY)


# --- the premium ---------------------------------------------------------------------------------------------------
def premium_parts(frame: pl.DataFrame, as_of: date, population: list[str] | None = None) -> dict[str, Any]:
    """Median EV/NTM of ``config.Q9['trio']`` over the median of the rest of the Q1 and Q2 population, with both
    medians. The ratio is ``readings.premium``'s, so a weekly premium and a Q9 reading are one number; the medians are
    the two things it divides. Each median exists only when the premium's own condition holds (all three trio
    members, at least ``Q9['min_rest']`` others); otherwise it is None and ``reason`` says which."""
    population = F.q1_population(frame) if population is None else population
    rows = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    trio_tickers = list(C.Q9["trio"])
    trio = [v for t in trio_tickers if t in rows and (v := F.ev_ntm(rows[t], as_of))]
    rest = [v for t in population if t not in trio_tickers and t in rows and (v := F.ev_ntm(rows[t], as_of))]
    min_rest = int(C.Q9["min_rest"])
    reasons = []
    if len(trio) < len(trio_tickers):
        reasons.append(f"{len(trio)} of {len(trio_tickers)} members of the trio have a multiple")
    if len(rest) < min_rest:
        reasons.append(f"the rest has {len(rest)} companies, fewer than {min_rest}")
    ratio = R.premium(frame, as_of, population)
    return {
        "as_of": as_of.isoformat(),
        "ai_trio_median": float(np.median(trio)) if len(trio) == len(trio_tickers) else None,
        "rest_median": float(np.median(rest)) if len(rest) >= min_rest else None,
        "ratio": ratio,
        "n_trio": len(trio),
        "n_rest": len(rest),
        "reason": "; ".join(reasons) or None,
    }


# --- one weekly point ------------------------------------------------------------------------------------------------
def _china_group(frame: pl.DataFrame) -> list[str]:
    present = set(frame["ticker"].to_list())
    return [t for t in C.FROZEN_Q7_CN if t in present]


def _draw(y: np.ndarray, base: list[np.ndarray], groups: list[str], idx: np.ndarray) -> np.ndarray | None:
    """Coefficients (intercept, ``base`` columns, then the layer dummies) of one bootstrap draw, or None if the draw
    cannot identify them. A company keeps its group label and the dummies are rebuilt from the labels the draw
    contains (the first sorted group present is the reference), so a group that the draw lacks simply has no
    column. A draw with no China company or all of them, or with any other collinearity, is rank-deficient and is
    skipped (``stats.bootstrap`` skips a None)."""
    sample = [groups[i] for i in idx]
    X = np.column_stack([np.ones(len(idx)), *(c[idx] for c in base), *S.dummies(sample)])
    beta, _, rank, _ = np.linalg.lstsq(X, y[idx], rcond=None)
    if rank < X.shape[1] or not np.all(np.isfinite(beta)):
        return None
    return beta


def _intervals(
    y: np.ndarray,
    base: list[np.ndarray],
    groups: list[str],
    at: dict[str, int],
    levels: dict[str, float],
    *,
    draws: int,
) -> tuple[dict[str, list[float] | None], int]:
    """Percentile bootstrap intervals, resampling companies, for the coefficients in ``at`` (name -> column).

    One pass of ``stats.bootstrap`` serves every coefficient: the statistic returns the first coefficient and
    records the others from the same fit, so the draws are shared and the cost is one regression per draw. Returns
    the intervals and the number of draws that identified every coefficient."""
    names = list(at)
    samples: dict[str, list[float]] = {k: [] for k in names}

    def statistic(idx: np.ndarray) -> float | None:
        beta = _draw(y, base, groups, idx)
        if beta is None:
            return None
        for name in names:
            samples[name].append(float(beta[at[name]]))
        return samples[names[0]][-1]

    S.bootstrap(len(y), statistic, draws=draws)
    used = len(samples[names[0]])
    if used == 0:
        return {k: None for k in names}, 0
    return {k: list(S.interval(np.array(samples[k]), levels[k])) for k in names}, used


def _num(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def _empty_point(as_of: date, run_ts: str, *, n_us: int, n_cn: int, population: list[str], dropped: list[str]) -> dict:
    return {
        "as_of": as_of.isoformat(), "run_ts": run_ts, "n": n_us + n_cn, "n_us": n_us, "n_cn": n_cn,
        "n_params": None, "fitted": False, "reason": None,
        "r2": None, "adj_r2": None, "adj_r2_no_layer": None,
        "slope": None, "slope_ci": None, "fcf_coef": None, "fcf_ci": None, "china_dummy": None, "china_ci": None,
        "ci_draws": None, "layer_coefs": [], "population": population, "dropped": dropped,
    }  # fmt: skip


def fit_point(
    frame: pl.DataFrame,
    as_of: date,
    *,
    draws: int = S.DRAWS,
    pool: Mapping[str, Entry] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One weekly regression point and its residuals (largest absolute residual first, the top five flagged).

    ``frame`` is one usable run; ``as_of`` is the date its days to fiscal year 0 are counted from. The point is
    unvalidated here; ``build`` validates every table it returns."""
    pool = F.registered_pool() if pool is None else pool
    run_ts = str(frame["snapshot_ts"][0])
    snaps = {as_of: frame}
    us = R.build_panel(snaps, F.q1_population, min_snapshots=1)
    cn = R.build_panel(snaps, _china_group, min_snapshots=1)
    tickers = [*us.tickers, *cn.tickers]
    intended = set(F.q1_population(frame)) | set(C.FROZEN_Q7_CN)
    point = _empty_point(
        as_of,
        run_ts,
        n_us=len(us),
        n_cn=len(cn),
        population=sorted(tickers),
        dropped=sorted(intended - set(tickers)),
    )
    n = len(tickers)
    if n == 0:
        point["reason"] = "no company has a multiple, growth and FCF margin"
        return point, []

    y = np.concatenate([us.log_mult, cn.log_mult])
    growth = np.concatenate([us.growth, cn.growth])
    fcf = np.concatenate([us.fcf, cn.fcf])
    is_cn = np.array([1.0 if F.is_china(t) else 0.0 for t in tickers])
    raw_layers = [pool[t].layer for t in tickers]
    groups = S.merge_small(raw_layers, MERGE_BELOW)
    has_cn = 0 < len(cn) < n  # a China coefficient needs companies on both sides
    base = [growth, fcf, *([is_cn] if has_cn else [])]
    cols = [*base, *S.dummies(groups)]
    X = np.column_stack([np.ones(n), *cols])
    k = X.shape[1]
    point["n_params"] = k
    if n <= k:
        point["reason"] = f"{n} companies for {k} parameters"
        return point, []
    if int(np.linalg.matrix_rank(X)) < k:
        point["reason"] = "the design matrix is rank-deficient"
        return point, []

    beta, r2, adj = S.ols(y, cols)
    _, _, adj_no_layer = S.ols(y, base)
    at = {"slope": 1, "fcf": 2, **({"china": 3} if has_cn else {})}
    levels = {"slope": SLOPE_LEVEL, "fcf": SLOPE_LEVEL, "china": CHINA_LEVEL}
    ci, used = _intervals(y, base, groups, at, levels, draws=draws)
    ordered = sorted(set(groups))
    first_dummy = 1 + len(base)  # columns: intercept, growth, FCF, (China), then the dummies of ordered[1:]
    point.update(
        fitted=True,
        r2=_num(r2),
        adj_r2=_num(adj),
        adj_r2_no_layer=_num(adj_no_layer),
        slope=float(beta[1]),
        slope_ci=ci["slope"],
        fcf_coef=float(beta[2]),
        fcf_ci=ci["fcf"],
        china_dummy=float(beta[3]) if has_cn else None,
        china_ci=ci.get("china") if has_cn else None,
        ci_draws=used,
        layer_coefs=[
            {
                "layer": g,
                "n": groups.count(g),
                "coef": 0.0 if i == 0 else float(beta[first_dummy + i - 1]),
                "reference": i == 0,
            }
            for i, g in enumerate(ordered)
        ],
    )
    fitted_log = X @ beta
    resid = y - fitted_log
    order = sorted(range(n), key=lambda i: (-abs(float(resid[i])), tickers[i]))
    top = set(order[:TOP_N])
    rows = [
        {
            "as_of": as_of.isoformat(),
            "ticker": tickers[i],
            "name": pool[tickers[i]].name,
            "layer": raw_layers[i],
            "group": groups[i],
            "market": "china" if is_cn[i] else "us",
            "ev_ntm": float(np.exp(y[i])),
            "fitted": float(np.exp(fitted_log[i])),
            "residual": float(resid[i]),
            "top5": i in top,
        }
        for i in order
    ]
    return point, rows


# --- schemas -------------------------------------------------------------------------------------------------------
_DATE = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"))
_CI = pa.Column(pl.List(pl.Float64), nullable=True)
_LAYER = pl.Struct({"layer": pl.Utf8, "n": pl.Int64, "coef": pl.Float64, "reference": pl.Boolean})

POINT_DTYPES: dict[str, Any] = {
    "as_of": pl.Utf8, "run_ts": pl.Utf8, "n": pl.Int64, "n_us": pl.Int64, "n_cn": pl.Int64, "n_params": pl.Int64,
    "fitted": pl.Boolean, "reason": pl.Utf8, "r2": pl.Float64, "adj_r2": pl.Float64, "adj_r2_no_layer": pl.Float64,
    "slope": pl.Float64, "slope_ci": pl.List(pl.Float64), "fcf_coef": pl.Float64, "fcf_ci": pl.List(pl.Float64),
    "china_dummy": pl.Float64, "china_ci": pl.List(pl.Float64), "ci_draws": pl.Int64,
    "layer_coefs": pl.List(_LAYER), "population": pl.List(pl.Utf8), "dropped": pl.List(pl.Utf8),
}  # fmt: skip


def _ci_ordered(col: str) -> pl.Expr:
    """A null interval, or two numbers with the lower end first."""
    ends = pl.col(col).list
    return pl.col(col).is_null() | (
        (ends.len() == 2) & (ends.get(0, null_on_oob=True) <= ends.get(1, null_on_oob=True))
    )


def _point_checks(d: pa.PolarsData) -> pl.LazyFrame:
    fitted = pl.col("fitted")
    ok = (
        (pl.col("n") == pl.col("n_us") + pl.col("n_cn"))
        # a fitted point has its estimates and an unfitted one has none and says why
        & pl.when(fitted)
        .then(pl.col("slope").is_not_null() & pl.col("fcf_coef").is_not_null() & (pl.col("layer_coefs").list.len() > 0))
        .otherwise(pl.col("slope").is_null() & pl.col("reason").is_not_null())
        # an interval needs its estimate (the estimate can stand without one if no bootstrap draw was usable)
        & (pl.col("china_ci").is_null() | pl.col("china_dummy").is_not_null())
        & (pl.col("slope_ci").is_null() | pl.col("slope").is_not_null())
        & (pl.col("fcf_ci").is_null() | pl.col("fcf_coef").is_not_null())
        & _ci_ordered("slope_ci")
        & _ci_ordered("fcf_ci")
        & _ci_ordered("china_ci")
    )
    return d.lazyframe.select(ok)


POINT_SCHEMA = pa.DataFrameSchema(
    {
        "as_of": _DATE,
        "run_ts": pa.Column(str),
        "n": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_us": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_cn": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_params": pa.Column(pl.Int64, pa.Check.ge(1), nullable=True),
        "fitted": pa.Column(pl.Boolean),
        "reason": pa.Column(str, nullable=True),
        "r2": pa.Column(pl.Float64, pa.Check.le(1.0 + 1e-9), nullable=True),
        "adj_r2": pa.Column(pl.Float64, pa.Check.le(1.0 + 1e-9), nullable=True),
        "adj_r2_no_layer": pa.Column(pl.Float64, pa.Check.le(1.0 + 1e-9), nullable=True),
        "slope": pa.Column(pl.Float64, nullable=True),
        "slope_ci": _CI,
        "fcf_coef": pa.Column(pl.Float64, nullable=True),
        "fcf_ci": _CI,
        "china_dummy": pa.Column(pl.Float64, nullable=True),
        "china_ci": _CI,
        "ci_draws": pa.Column(pl.Int64, pa.Check.ge(0), nullable=True),
        "layer_coefs": pa.Column(pl.List(_LAYER)),
        "population": pa.Column(pl.List(pl.Utf8)),
        "dropped": pa.Column(pl.List(pl.Utf8)),
    },
    unique=["as_of"], strict=True, coerce=False,
    checks=[pa.Check(_point_checks, error="a fitted point has estimates, an unfitted one a reason; intervals ordered")],
)  # fmt: skip

RESIDUAL_DTYPES: dict[str, Any] = {
    "as_of": pl.Utf8, "ticker": pl.Utf8, "name": pl.Utf8, "layer": pl.Utf8, "group": pl.Utf8, "market": pl.Utf8,
    "ev_ntm": pl.Float64, "fitted": pl.Float64, "residual": pl.Float64, "top5": pl.Boolean,
}  # fmt: skip


def _top5_is_the_largest(d: pa.PolarsData) -> pl.LazyFrame:
    """Exactly ``min(5, n)`` rows are flagged and no unflagged row has a larger absolute residual than a flagged one."""
    lf = d.lazyframe
    flagged_min = pl.col("residual").abs().filter(pl.col("top5")).min()
    unflagged_max = pl.col("residual").abs().filter(~pl.col("top5")).max()
    return lf.select(
        (pl.col("top5").sum() == pl.min_horizontal(pl.len(), pl.lit(TOP_N)))
        & (unflagged_max.is_null() | flagged_min.is_null() | (flagged_min >= unflagged_max))
    )


RESIDUAL_SCHEMA = pa.DataFrameSchema(
    {
        "as_of": _DATE, "ticker": pa.Column(str), "name": pa.Column(str),
        "layer": pa.Column(str, pa.Check.isin(list(C.LAYERS))), "group": pa.Column(str),
        "market": pa.Column(str, pa.Check.isin(["us", "china"])),
        "ev_ntm": pa.Column(pl.Float64, pa.Check.gt(0.0)), "fitted": pa.Column(pl.Float64, pa.Check.gt(0.0)),
        "residual": pa.Column(pl.Float64), "top5": pa.Column(pl.Boolean),
    },
    unique=["as_of", "ticker"], strict=True, coerce=False,
    checks=[pa.Check(_top5_is_the_largest, error="the five largest absolute residuals, and only those, are flagged")],
)  # fmt: skip

PREMIUM_DTYPES: dict[str, Any] = {
    "as_of": pl.Utf8, "ai_trio_median": pl.Float64, "rest_median": pl.Float64, "ratio": pl.Float64,
    "n_trio": pl.Int64, "n_rest": pl.Int64, "reason": pl.Utf8,
}  # fmt: skip


def _premium_is_the_quotient(d: pa.PolarsData) -> pl.LazyFrame:
    trio, rest, ratio = pl.col("ai_trio_median"), pl.col("rest_median"), pl.col("ratio")
    close = ((trio / rest) - ratio).abs() <= 1e-9 * ratio.abs()
    return d.lazyframe.select(
        pl.when(ratio.is_null()).then(pl.lit(True)).otherwise(trio.is_not_null() & rest.is_not_null() & close)
        & (ratio.is_not_null() | pl.col("reason").is_not_null())
    )


PREMIUM_SCHEMA = pa.DataFrameSchema(
    {
        "as_of": _DATE,
        "ai_trio_median": pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True),
        "rest_median": pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True),
        "ratio": pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True),
        "n_trio": pa.Column(pl.Int64, pa.Check.in_range(0, len(C.Q9["trio"]))),
        "n_rest": pa.Column(pl.Int64, pa.Check.ge(0)),
        "reason": pa.Column(str, nullable=True),
    },
    unique=["as_of"], strict=True, coerce=False,
    checks=[pa.Check(_premium_is_the_quotient, error="the premium is the trio over the rest median, or says why not")],
)  # fmt: skip


def _validated(
    records: list[dict[str, Any]], dtypes: dict[str, Any], schema: pa.DataFrameSchema
) -> list[dict[str, Any]]:
    """Records through their schema and back to records: what is returned is what was validated."""
    frame = pl.DataFrame(records, schema=dtypes)
    return schema.validate(frame).to_dicts()  # type: ignore[union-attr]


def validate_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _validated(points, POINT_DTYPES, POINT_SCHEMA)


def validate_residuals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _validated(rows, RESIDUAL_DTYPES, RESIDUAL_SCHEMA)


def validate_premiums(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _validated(rows, PREMIUM_DTYPES, PREMIUM_SCHEMA)


# --- the whole structure ----------------------------------------------------------------------------------------------
UNITS = {
    "slope": "change in ln(EV/NTM revenue) per 1.00 of growth (1.00 = 100% growth)",
    "fcf_coef": "change in ln(EV/NTM revenue) per 1.00 of FCF margin",
    "china_dummy": "ln(EV/NTM revenue) of an A-share company relative to a US one, same growth, FCF margin and layer",
    "layer_coefs": "ln(EV/NTM revenue) relative to the reference group, same growth, FCF margin and listing market",
    "residual": "natural log of EV/NTM revenue over the fitted EV/NTM revenue; 0.10 is about 10% above the fit",
    "ev_ntm": "EV/NTM revenue, a multiple (x)",
    "premium": "median EV/NTM revenue of the trio over the median of the rest of the population, a ratio",
}


def _spec(draws: int) -> dict[str, Any]:
    return {
        "model": "ln(EV/NTM revenue) = a + b*growth + c*FCF margin + d*China + layer groups",
        "fitted_on": "the Q1 and Q2 population of the week plus the Q7 A-share group, one run",
        "ci_level": {"slope": SLOPE_LEVEL, "fcf": SLOPE_LEVEL, "china": CHINA_LEVEL},
        "draws": draws,
        "seed": S.SEED,
        "layer_merge_below": MERGE_BELOW,
        "trio": list(C.Q9["trio"]),
        "definitions": {
            "ev_ntm": f"{C.EV_SOURCE} over next-twelve-month revenue; {C.NTM_WEIGHT}",
            "growth": C.GROWTH,
            "fcf_margin": C.FCF_MARGIN,
        },
        "units": UNITS,
        "verdict": False,
    }


def build(runs: pl.DataFrame | None, today: date, *, draws: int = S.DRAWS) -> dict[str, Any]:
    """The regression mart: ``{today, spec, latest_age_days, latest, baseline, series, residuals, premium,
    premium_series}``.

    * ``latest``: the point fitted on the newest usable run of any weekday (``metrics.latest``), or None.
    * ``baseline``: ``{"regression": point | None, "premium": record | None}`` for the registered baseline run,
      read at its market date 2026-10-16. Never a series point.
    * ``series``: one point per usable Monday run from 2026-10-26, oldest first.
    * ``residuals``: the latest point's companies, largest absolute residual first, ``top5`` flagged.
    * ``premium`` / ``premium_series``: the AI-trio premium of the latest run and of each series Monday.

    With no usable run every key is present and empty (None, or an empty list). ``draws`` is the bootstrap draw
    count; the registered default is ``stats.DRAWS``."""
    out: dict[str, Any] = {
        "today": today.isoformat(),
        "spec": _spec(draws),
        "latest_age_days": None,
        "latest": None,
        "baseline": {"regression": None, "premium": None},
        "series": [],
        "residuals": [],
        "premium": None,
        "premium_series": [],
    }
    frame = runs_until(runs, today)
    if frame is None:
        return out
    pool = F.registered_pool()
    memo: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = {}

    def at(part: pl.DataFrame, as_of: date) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        key = f"{part['snapshot_ts'][0]}|{as_of}"
        if key not in memo:
            point, rows = fit_point(part, as_of, draws=draws, pool=pool)
            memo[key] = (point, rows, premium_parts(part, as_of))
        return memo[key]

    base = baseline_frame(frame)
    if base is not None:
        point, _, prem = at(base, BASELINE_MARKET_DAY)
        out["baseline"] = {"regression": validate_points([point])[0], "premium": validate_premiums([prem])[0]}

    newest = M.latest(frame)
    if newest is not None:
        file_day, _, part = newest
        point, rows, prem = at(part, market_date(file_day))
        out["latest"] = validate_points([point])[0]
        out["residuals"] = validate_residuals(rows) if rows else []
        out["premium"] = validate_premiums([prem])[0]
        out["latest_age_days"] = (today - file_day).days

    snaps = series_snapshots(frame, today)
    if snaps:
        fits = [at(part, day) for day, part in snaps.items()]
        out["series"] = validate_points([p for p, _, _ in fits])
        out["premium_series"] = validate_premiums([q for _, _, q in fits])
    return out
