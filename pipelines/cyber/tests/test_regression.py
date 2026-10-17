"""The page regression: weekly points agree with an independent fit, the series follows the registered Monday rule,
and every degenerate input (no runs, a throttled run, too few companies, no China, a collinear design) returns a
well-formed structure instead of a wrong number or an exception."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from datetime import date

import numpy as np
import pandera.errors
import polars as pl
import pytest

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import metrics as M
from pipelines.cyber import regression as G
from pipelines.cyber import stats as S

DRAWS = 300  # the registered 10,000 is for the page; the tests check the mechanics
BASELINE_DAY = date(2026, 10, 16)  # the market date of the baseline file


@pytest.fixture(scope="module")
def baseline() -> pl.DataFrame:
    return F.load_baseline()


def make_run(baseline: pl.DataFrame, ts: str, *, seed: int = 0, noise: float = 0.0, **edits) -> pl.DataFrame:
    """The baseline's 73 ring A rows restamped to ``ts``, enterprise values shifted by lognormal noise per company so
    that a week differs from the next. ``edits`` are keyword ``column=callable(frame)->Expr`` replacements."""
    rng = np.random.default_rng(seed)
    shift = np.exp(rng.normal(scale=noise, size=baseline.height)) if noise else np.ones(baseline.height)
    frame = baseline.with_columns(
        pl.lit(ts).alias("snapshot_ts"),
        (pl.col("enterprise_value") * pl.Series(shift)).alias("enterprise_value"),
    )
    for column, fn in edits.items():
        frame = frame.with_columns(fn(frame).alias(column))
    return frame


def null_for(column: str, tickers: Sequence[str]):
    return lambda f: pl.when(pl.col("ticker").is_in(list(tickers))).then(None).otherwise(pl.col(column))


def runs_of(*frames: pl.DataFrame) -> pl.DataFrame:
    return pl.concat(list(frames), how="vertical_relaxed")


def independent_design(frame: pl.DataFrame, as_of: date):
    """The page regression written out again from ``frozen`` alone (no ``readings.build_panel``, no module under
    test): returns tickers, y, growth, fcf, china indicator and the merged groups."""
    rows = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    pool = F.registered_pool()
    tickers, y, g, f, cn, layers = [], [], [], [], [], []
    for t in sorted({*F.q1_population(frame), *C.FROZEN_Q7_CN}):
        r = rows[t]
        mult, growth, fcf = F.ev_ntm(r, as_of), F.growth(r, as_of), F.fcf_margin(r)
        if mult is None or growth is None or fcf is None:
            continue
        tickers.append(t)
        y.append(np.log(mult))
        g.append(growth)
        f.append(fcf)
        cn.append(1.0 if F.is_china(t) else 0.0)
        layers.append(pool[t].layer)
    groups = S.merge_small(layers, 3)
    dummies = [np.array([1.0 if x == lab else 0.0 for x in groups]) for lab in sorted(set(groups))[1:]]
    return tickers, np.array(y), np.array(g), np.array(f), np.array(cn), groups, dummies


# --- one point, against an independent fit ------------------------------------------------------------------------
def test_baseline_point_agrees_with_an_independent_least_squares_fit(baseline) -> None:
    out = G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)
    point = out["latest"]
    tickers, y, g, f, cn, groups, dummies = independent_design(baseline, BASELINE_DAY)
    X = np.column_stack([np.ones(len(y)), g, f, cn, *dummies])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    r2 = 1 - (resid @ resid) / ((y - y.mean()) ** 2).sum()
    X0 = X[:, :4]
    b0, *_ = np.linalg.lstsq(X0, y, rcond=None)
    r0 = y - X0 @ b0
    r2_0 = 1 - (r0 @ r0) / ((y - y.mean()) ** 2).sum()

    assert point["fitted"] and point["reason"] is None and point["dropped"] == []
    assert (point["n"], point["n_us"], point["n_cn"], point["n_params"]) == (23, 17, 6, k)
    assert point["population"] == tickers == sorted(tickers)
    assert point["r2"] == pytest.approx(r2) and point["adj_r2"] == pytest.approx(1 - (1 - r2) * (n - 1) / (n - k))
    assert point["adj_r2_no_layer"] == pytest.approx(1 - (1 - r2_0) * (n - 1) / (n - 4))
    assert (point["slope"], point["fcf_coef"], point["china_dummy"]) == pytest.approx(tuple(beta[1:4]))
    # a negative China dummy and a positive growth slope, as the baseline reading of Q7 and Q2 have it
    assert point["china_dummy"] < 0 < point["slope"]

    layers = {c["layer"]: c for c in point["layer_coefs"]}
    ref = sorted(set(groups))[0]
    assert layers[ref]["reference"] and layers[ref]["coef"] == 0.0 and sum(c["reference"] for c in layers.values()) == 1
    for lab, coef in zip(sorted(set(groups))[1:], beta[4:], strict=True):
        assert layers[lab]["coef"] == pytest.approx(coef) and layers[lab]["n"] == groups.count(lab)
    assert sum(c["n"] for c in layers.values()) == n

    rows = {r["ticker"]: r for r in out["residuals"]}
    assert set(rows) == set(tickers)
    for t, e in zip(tickers, resid, strict=True):
        assert rows[t]["residual"] == pytest.approx(e)
        assert rows[t]["ev_ntm"] == pytest.approx(
            F.ev_ntm(next(baseline.filter(pl.col("ticker") == t).iter_rows(named=True)), BASELINE_DAY)
        )
        assert rows[t]["fitted"] == pytest.approx(rows[t]["ev_ntm"] * np.exp(-rows[t]["residual"]))


def test_residuals_are_largest_first_and_the_five_largest_are_flagged(baseline) -> None:
    out = G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)
    rows = out["residuals"]
    sizes = [abs(r["residual"]) for r in rows]
    assert sizes == sorted(sizes, reverse=True)
    assert [r["top5"] for r in rows] == [True] * 5 + [False] * (len(rows) - 5)
    assert {r["market"] for r in rows} == {"us", "china"}
    assert all(r["name"] and r["layer"] in C.LAYERS and r["as_of"] == "2026-10-16" for r in rows)


def test_intervals_are_ordered_deterministic_and_use_nearly_every_draw(baseline) -> None:
    a = G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)["latest"]
    b = G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)["latest"]
    assert a == b, "the registered seed makes a point reproducible"
    for key in ("slope_ci", "fcf_ci", "china_ci"):
        lo, hi = a[key]
        assert lo < hi
    assert a["slope_ci"][0] < a["slope"] < a["slope_ci"][1]
    assert 0.9 * DRAWS < a["ci_draws"] <= DRAWS


def test_bootstrap_draw_is_the_registered_style_fit_and_skips_what_it_cannot_identify(baseline) -> None:
    _, y, g, f, cn, groups, _ = independent_design(baseline, BASELINE_DAY)
    n, rng = len(y), np.random.default_rng(S.SEED)
    compared = skipped = 0
    for _ in range(400):
        idx = rng.integers(0, n, n)
        mine = G._draw(y, [g, f, cn], groups, idx)
        sample = [groups[i] for i in idx]
        cols = [g[idx], f[idx], cn[idx], *S.dummies(sample)]
        design = np.column_stack([np.ones(n), *cols])
        if np.linalg.matrix_rank(design) < design.shape[1]:
            assert mine is None
            skipped += 1
            continue
        beta, *_ = S.ols(y[idx], cols)
        assert mine is not None and mine[:4] == pytest.approx(beta[:4])
        compared += 1
    assert compared > 350, "almost every draw identifies the model"
    assert skipped == 400 - compared


def test_bootstrap_draw_skips_a_draw_without_china_or_with_only_china_and_matches_the_fit_on_all_rows(baseline) -> None:
    _, y, g, f, cn, groups, dummies = independent_design(baseline, BASELINE_DAY)
    n = len(y)
    us_only = np.array([i for i in range(n) if cn[i] == 0.0] * 2)  # no China company: the China column is zero
    china_only = np.array([i for i in range(n) if cn[i] == 1.0] * 4)  # only China companies: it equals the intercept
    assert G._draw(y, [g, f, cn], groups, us_only) is None
    assert G._draw(y, [g, f, cn], groups, china_only) is None
    everyone = G._draw(y, [g, f, cn], groups, np.arange(n))
    X = np.column_stack([np.ones(n), g, f, cn, *dummies])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    assert everyone is not None and everyone[:4] == pytest.approx(beta[:4])
    # four distinct companies (two China, two US, in two groups) cannot identify five or more parameters
    assert G._draw(y, [g, f, cn], groups, np.array([0, 2, 6, 8] * 3)) is None


# --- the series ----------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def many(baseline) -> pl.DataFrame:
    return runs_of(
        baseline,  # the baseline file: a run, never a series point
        make_run(baseline, "2026-10-19T14:00:00+00:00", seed=1, noise=0.05),  # a Monday before the formal series
        make_run(baseline, "2026-10-26T13:00:00+00:00", seed=2, noise=0.05),  # a Monday, but a later run exists
        make_run(baseline, "2026-10-26T16:00:00+00:00", seed=3, noise=0.05),  # the last good run of 2026-10-26
        make_run(baseline, "2026-11-02T14:00:00+00:00", seed=4, noise=0.05),
        make_run(baseline, "2026-11-09T14:00:00+00:00", seed=5, noise=0.05, price=lambda f: pl.lit(None, pl.Float64)),
        make_run(baseline, "2026-11-10T07:00:00+00:00", seed=6, noise=0.05),  # a Tuesday: latest, not a series point
    )


def test_series_is_the_usable_mondays_from_the_first_snapshot(many) -> None:
    out = G.build(many, date(2026, 11, 10), draws=DRAWS)
    assert [p["as_of"] for p in out["series"]] == ["2026-10-26", "2026-11-02"], (
        "the baseline, the Monday before 2026-10-26, the throttled Monday 2026-11-09 and the Tuesday are not points"
    )
    assert out["series"][0]["run_ts"] == "2026-10-26T16:00:00+00:00", "the last good run of a Monday is the snapshot"
    assert [q["as_of"] for q in out["premium_series"]] == [p["as_of"] for p in out["series"]]
    assert all(p["fitted"] and p["n"] == 23 for p in out["series"])
    assert out["series"][0]["slope"] != out["series"][1]["slope"], "different weeks, different fits"


def test_latest_is_the_newest_usable_run_of_any_weekday_and_the_baseline_is_returned_apart(many) -> None:
    out = G.build(many, date(2026, 11, 10), draws=DRAWS)
    assert out["latest"]["as_of"] == "2026-11-10" and out["latest"]["run_ts"] == "2026-11-10T07:00:00+00:00"
    assert out["latest_age_days"] == 0
    assert out["premium"]["as_of"] == "2026-11-10"
    base = out["baseline"]
    assert base["regression"]["as_of"] == "2026-10-16", "the baseline is read at its market date"
    assert base["regression"]["run_ts"].startswith("2026-10-17T")
    assert base["premium"]["ratio"] == pytest.approx(4.84, abs=0.01), "the Q9 already-seen premium"
    assert out["residuals"][0]["as_of"] == "2026-11-10"
    assert "2026-10-16" not in {p["as_of"] for p in out["series"]}


def test_today_cuts_off_later_runs(many) -> None:
    out = G.build(many, date(2026, 11, 3), draws=DRAWS)
    assert [p["as_of"] for p in out["series"]] == ["2026-10-26", "2026-11-02"]
    assert out["latest"]["as_of"] == "2026-11-02" and out["latest_age_days"] == 1
    early = G.build(many, date(2026, 10, 20), draws=DRAWS)
    assert early["series"] == [] and early["latest"]["as_of"] == "2026-10-19"
    assert early["baseline"]["regression"] is not None, "the baseline file predates the 20th"
    assert G.build(many, date(2026, 10, 16), draws=DRAWS)["latest"] is None


def test_one_snapshot_is_enough_for_a_point_and_a_series_of_one(baseline) -> None:
    runs = runs_of(baseline, make_run(baseline, "2026-10-26T14:00:00+00:00", seed=2, noise=0.05))
    out = G.build(runs, date(2026, 10, 26), draws=DRAWS)
    assert len(out["series"]) == 1 and out["latest"] == out["series"][0]
    assert out["premium"] == out["premium_series"][0]


def test_baseline_alone_has_no_series_but_a_latest_and_a_baseline(baseline) -> None:
    out = G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)
    assert out["series"] == [] and out["premium_series"] == []
    assert out["latest"] == out["baseline"]["regression"] and out["premium"] == out["baseline"]["premium"]
    assert out["latest_age_days"] == 0


def test_series_snapshots_keys_and_runs_without_the_columns(baseline, many) -> None:
    snaps = G.series_snapshots(many, date(2026, 12, 31))
    assert list(snaps) == [date(2026, 10, 26), date(2026, 11, 2)]
    assert (
        G.series_snapshots(None, date(2026, 12, 31)) == {}
        and G.series_snapshots(pl.DataFrame(), date(2026, 12, 31)) == {}
    )
    assert G.baseline_frame(many)["snapshot_ts"][0] == "2026-10-17T02:37:07+00:00"
    assert G.baseline_frame(many.filter(pl.col("snapshot_ts") > "2026-10-18")) is None


def test_market_date_maps_only_the_baseline_file_date() -> None:
    assert G.market_date(date(2026, 10, 17)) == date(2026, 10, 16)
    assert G.market_date(date(2026, 10, 26)) == date(2026, 10, 26)


# --- nothing usable --------------------------------------------------------------------------------------------------------
def throttled(baseline) -> pl.DataFrame:
    return make_run(
        baseline, "2026-10-26T14:00:00+00:00", price=lambda f: pl.when(pl.col("ticker") == "CRWD").then(pl.col("price"))
    )


@pytest.mark.parametrize("which", ["none", "empty", "throttled", "future"])
def test_no_usable_run_returns_the_empty_structure(baseline, which) -> None:
    runs = {
        "none": None,
        "empty": pl.DataFrame(),
        "throttled": throttled(baseline),  # one priced row of 73 is not a snapshot
        "future": runs_of(baseline),  # exists, but is after the day the page is built
    }[which]
    out = (
        G.build(runs, date(2026, 10, 1), draws=DRAWS)
        if which == "future"
        else G.build(runs, date(2026, 11, 1), draws=DRAWS)
    )
    assert out["latest"] is None and out["series"] == [] and out["residuals"] == []
    assert out["premium"] is None and out["premium_series"] == [] and out["latest_age_days"] is None
    assert out["baseline"] == {"regression": None, "premium": None}
    assert set(out) == {
        "today",
        "spec",
        "latest_age_days",
        "latest",
        "baseline",
        "series",
        "residuals",
        "premium",
        "premium_series",
    }
    json.dumps(out, allow_nan=False)


def test_everything_returned_is_json_ready(many) -> None:
    out = G.build(many, date(2026, 11, 10), draws=DRAWS)
    text = json.dumps(out, allow_nan=False)
    assert json.loads(text) == out
    assert out["spec"]["draws"] == DRAWS and out["spec"]["seed"] == S.SEED and out["spec"]["verdict"] is False
    assert out["spec"]["trio"] == C.Q9["trio"] and out["spec"]["definitions"]["growth"] == C.GROWTH


# --- degenerate weeks -----------------------------------------------------------------------------------------------------------
def test_too_few_companies_for_the_parameters_is_an_unfitted_point_with_a_reason(baseline) -> None:
    keep = ["CRWD", "NET", "002212.SZ", "002439.SZ"]  # four companies, and growth + FCF + China + intercept is four
    drop = [t for t in sorted({*F.q1_population(baseline), *C.FROZEN_Q7_CN}) if t not in keep]
    frame = make_run(baseline, "2026-10-26T14:00:00+00:00", ttm_free_cash_flow=null_for("ttm_free_cash_flow", drop))
    point, rows = G.fit_point(frame, date(2026, 10, 26), draws=DRAWS)
    assert not point["fitted"] and point["reason"] == "4 companies for 4 parameters" and rows == []
    assert point["n"] == 4 and point["r2"] is None and point["slope"] is None and point["layer_coefs"] == []
    assert point["dropped"] == sorted(drop) and point["population"] == sorted(keep)
    G.validate_points([point])
    out = G.build(runs_of(frame), date(2026, 10, 26), draws=DRAWS)
    assert out["latest"]["fitted"] is False and out["residuals"] == []
    assert out["series"][0] == out["latest"]


def test_no_china_in_the_week_drops_the_china_coefficient_but_not_the_fit(baseline) -> None:
    frame = make_run(baseline, "2026-10-26T14:00:00+00:00", em_rev1_mean=lambda f: pl.lit(None, pl.Float64))
    point, rows = G.fit_point(frame, date(2026, 10, 26), draws=DRAWS)
    assert point["fitted"] and point["n_cn"] == 0 and point["n_us"] == 17
    assert point["china_dummy"] is None and point["china_ci"] is None
    assert point["slope"] is not None and point["slope_ci"] is not None
    assert set(C.FROZEN_Q7_CN) <= set(point["dropped"]) and all(r["market"] == "us" for r in rows)
    G.validate_points([point])


def test_a_design_that_cannot_identify_the_china_dummy_is_unfitted(baseline) -> None:
    # every A-share company in one layer and every US company in another: the layer dummy is the China indicator
    pool = {
        t: dataclasses.replace(e, layer="network_edge" if F.is_china(t) else "identity")
        for t, e in F.registered_pool().items()
    }
    frame = make_run(baseline, "2026-10-26T14:00:00+00:00")
    point, rows = G.fit_point(frame, date(2026, 10, 26), draws=DRAWS, pool=pool)
    assert not point["fitted"] and "rank-deficient" in point["reason"] and rows == []
    assert point["n"] == 23 and point["china_dummy"] is None


def test_one_layer_group_means_no_layer_gain(baseline) -> None:
    pool = {t: dataclasses.replace(e, layer="identity") for t, e in F.registered_pool().items()}
    point, _ = G.fit_point(make_run(baseline, "2026-10-26T14:00:00+00:00"), date(2026, 10, 26), draws=DRAWS, pool=pool)
    assert point["fitted"] and len(point["layer_coefs"]) == 1 and point["layer_coefs"][0]["reference"]
    assert point["adj_r2"] == pytest.approx(point["adj_r2_no_layer"])


def test_dropped_lists_companies_without_a_multiple_growth_or_fcf_margin(baseline) -> None:
    frame = make_run(
        baseline,
        "2026-10-26T14:00:00+00:00",
        ttm_free_cash_flow=null_for("ttm_free_cash_flow", ["TLS"]),
        enterprise_value=null_for("enterprise_value", ["RPD", "688489.SS"]),
    )
    point, _ = G.fit_point(frame, date(2026, 10, 26), draws=DRAWS)
    assert point["dropped"] == ["688489.SS", "RPD", "TLS"] and point["n"] == 20
    assert "TLS" not in point["population"] and point["n_cn"] == 5


# --- the premium ---------------------------------------------------------------------------------------------------------------
def test_premium_is_the_trio_median_over_the_rest_median_and_agrees_with_readings(baseline) -> None:
    from pipelines.cyber import readings as R

    prem = G.premium_parts(baseline, BASELINE_DAY)
    assert prem["ratio"] == R.premium(baseline, BASELINE_DAY, F.q1_population(baseline))
    assert prem["ai_trio_median"] / prem["rest_median"] == pytest.approx(prem["ratio"])
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    trio = [F.ev_ntm(rows[t], BASELINE_DAY) for t in C.Q9["trio"]]
    rest = [F.ev_ntm(rows[t], BASELINE_DAY) for t in F.q1_population(baseline) if t not in C.Q9["trio"]]
    assert prem["ai_trio_median"] == pytest.approx(float(np.median(trio))) and prem["ai_trio_median"] == pytest.approx(
        29.76, abs=0.01
    )
    assert prem["rest_median"] == pytest.approx(float(np.median(rest))) and prem["rest_median"] == pytest.approx(
        6.15, abs=0.01
    )
    assert (prem["n_trio"], prem["n_rest"], prem["reason"]) == (3, 14, None)


def test_premium_without_the_whole_trio_or_enough_others_says_why(baseline) -> None:
    no_net = make_run(baseline, "2026-10-26T14:00:00+00:00", enterprise_value=null_for("enterprise_value", ["NET"]))
    prem = G.premium_parts(no_net, date(2026, 10, 26))
    assert prem["ratio"] is None and prem["ai_trio_median"] is None and prem["n_trio"] == 2
    assert "2 of 3" in prem["reason"] and prem["rest_median"] is not None

    others = [t for t in F.q1_population(baseline) if t not in C.Q9["trio"]][:7]
    thin = make_run(baseline, "2026-10-26T14:00:00+00:00", enterprise_value=null_for("enterprise_value", others))
    prem = G.premium_parts(thin, date(2026, 10, 26))
    assert prem["ratio"] is None and prem["rest_median"] is None and prem["n_rest"] == 7
    assert "fewer than 8" in prem["reason"] and prem["ai_trio_median"] is not None
    G.validate_premiums([prem])


def test_premium_series_follows_the_mondays(many) -> None:
    out = G.build(many, date(2026, 11, 10), draws=DRAWS)
    assert all(q["ratio"] and q["n_trio"] == 3 for q in out["premium_series"])
    assert out["premium_series"][0]["ratio"] != out["premium_series"][1]["ratio"]


# --- the schemas refuse what they should --------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def good(baseline) -> dict:
    return G.build(runs_of(baseline), date(2026, 10, 17), draws=DRAWS)


def test_point_schema_rejects_an_impossible_or_inconsistent_point(good) -> None:
    point = good["latest"]
    for bad in (
        {**point, "r2": 1.5},  # R squared above 1
        {**point, "slope_ci": [2.0, 1.0]},  # interval the wrong way round
        {**point, "slope_ci": [1.0]},  # an interval is two numbers
        {**point, "fitted": False, "reason": None},  # an unfitted point says why
        {**point, "n": point["n"] + 1},  # n is n_us + n_cn
        {**point, "china_dummy": None},  # an interval without its estimate
        {**point, "as_of": "26-10-2026"},
        {**point, "fitted": True, "slope": None},
    ):
        with pytest.raises(pandera.errors.SchemaError):
            G.validate_points([bad])
    with pytest.raises(pandera.errors.SchemaError):
        G.validate_points([point, point])  # one point per week


def test_residual_schema_rejects_a_wrong_flag_or_a_duplicate(good) -> None:
    rows = good["residuals"]
    flipped = [{**r, "top5": not r["top5"]} if i in (0, 9) else r for i, r in enumerate(rows)]
    for bad in (
        flipped,
        rows + [rows[0]],
        [{**rows[0], "ev_ntm": -1.0}, *rows[1:]],
        [{**rows[0], "layer": "plumbing"}, *rows[1:]],
    ):
        with pytest.raises(pandera.errors.SchemaError):
            G.validate_residuals(bad)
    assert G.validate_residuals(rows) == rows


def test_premium_schema_rejects_a_ratio_that_is_not_the_quotient(good) -> None:
    prem = good["premium"]
    for bad in (
        {**prem, "ratio": prem["ratio"] * 1.1},
        {**prem, "ratio": None, "reason": None},  # a missing premium says why
        {**prem, "ratio": -1.0},
        {**prem, "n_trio": 4},
    ):
        with pytest.raises(pandera.errors.SchemaError):
            G.validate_premiums([bad])


def test_load_runs_on_the_committed_snapshots_builds(baseline) -> None:
    out = G.build(M.load_runs(), date(2026, 10, 17), draws=DRAWS)
    assert out["latest"]["n"] == 23 and out["baseline"]["regression"]["as_of"] == "2026-10-16"
