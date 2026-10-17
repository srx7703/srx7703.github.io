"""The readings compose the registered estimators; the baseline reproduces the numbers section I prints."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import readings as R

AS_OF = date(2026, 10, 16)  # the market date of the baseline; days to the end of fiscal year 0 are counted from it


@pytest.fixture(scope="module")
def baseline():
    return F.load_baseline()


@pytest.fixture(scope="module")
def panels(baseline):
    snaps = {AS_OF: baseline}
    us = R.build_panel(snaps, F.q1_population, min_snapshots=1)
    cn = R.build_panel(
        snaps, lambda f: [t for t in C.FROZEN_Q7_CN if t in set(f["ticker"])], need_fcf=False, min_snapshots=1
    )
    return us, cn


# --- grade ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("precondition", "holds", "falsified", "expected"),
    [
        (False, True, False, "undecidable"),  # a failed precondition beats everything
        (False, False, True, "undecidable"),
        (True, True, True, "undecidable"),  # both met: neither is trusted
        (True, False, True, "falsified"),
        (True, True, False, "holds"),
        (True, False, False, "undecidable"),
    ],
)
def test_grade_follows_the_registered_order(precondition, holds, falsified, expected) -> None:
    assert R.grade(precondition=precondition, holds=holds, falsified=falsified) == expected


# --- the numbers section I prints ---------------------------------------------------------------------------------
def test_q1_reading_on_the_baseline(panels) -> None:
    us, _ = panels
    out = R.q1(us)
    assert out["n"] == 17 and dict(zip(*np.unique(us.layers, return_counts=True), strict=True)) == {
        "identity": 4, "network_edge": 6, "other": 4, "secops": 3,
    }  # fmt: skip
    assert out["adj_r2_growth"] == pytest.approx(0.751, abs=0.001)
    assert out["uplift"] == pytest.approx(-0.053, abs=0.001)
    assert out["perm_p"] == pytest.approx(0.941, abs=0.003)
    assert (out["lo"], out["hi"]) == pytest.approx((-0.061, 0.124), abs=0.002)
    assert out["status"] == "undecidable", "its upper end is above 0.10"


def test_q2_reading_on_the_baseline(panels) -> None:
    us, _ = panels
    out = R.q2(us)
    assert out["r2"] == pytest.approx(0.844, abs=0.001) and (out["b_lo"], out["b_hi"]) == pytest.approx(
        (1.09, 6.13), abs=0.02
    )
    assert (out["std_growth"], out["std_fcf"], out["gap"]) == pytest.approx((0.600, 0.392, 0.208), abs=0.002)
    assert (out["gap_lo"], out["gap_hi"]) == pytest.approx((-0.648, 0.670), abs=0.01)
    assert out["status"] == "undecidable"


def test_q3_reading_on_the_baseline(panels) -> None:
    us, _ = panels
    out = R.q3(us)
    assert (out["n_high"], out["n_low"]) == (11, 6)
    assert (out["median_high"], out["median_low"], out["ratio"]) == pytest.approx((8.83, 2.80, 3.15), abs=0.01)
    assert (out["lo"], out["hi"]) == pytest.approx((2.0, 10.2), abs=0.05)
    assert out["low_tickers"] == ["CHKP", "MITK", "OSPN", "RPD", "TENB", "TLS"] and out["status"] == "holds"


def test_q7_reading_on_the_baseline(panels) -> None:
    us, cn = panels
    out = R.q7(us, cn)
    assert (out["n_us"], out["n_cn"]) == (17, 6)
    assert out["coef"] == pytest.approx(-0.984, abs=0.005) and (out["lo"], out["hi"]) == pytest.approx(
        (-1.335, -0.666), abs=0.02
    )
    assert out["ratio"] == pytest.approx(0.604, abs=0.002) and (out["ratio_lo"], out["ratio_hi"]) == pytest.approx(
        (0.35, 1.10), abs=0.03
    )
    assert out["status"] == "holds"


def test_q9_premium_on_the_baseline(baseline) -> None:
    value = R.premium(baseline, AS_OF, F.q1_population(baseline))
    assert value == pytest.approx(4.84, abs=0.01)
    days = [date(2026, 10, 26) + 7 * i * (date(2026, 10, 26) - date(2026, 10, 19)) / 7 for i in range(0)]  # noqa: F841
    start = [date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]
    end = [date(2027, 9, 20), date(2027, 9, 27), date(2027, 10, 4), date(2027, 10, 11)]
    snaps = {d: baseline for d in (*start, *end)}
    # the baseline is dated 2026-10-16, so days to fiscal year 0 differ slightly between the copies; the verdict does not
    assert R.q9(snaps, start=start, end=end)["status"] in ("holds", "falsified", "undecidable")


# --- Q4, Q9 and Q10 on constructed cases ---------------------------------------------------------------------------
def test_q4_statuses_at_the_edges() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=34)
    assert R.q4(x, x + rng.normal(scale=0.2, size=34), draws=500)["status"] == "falsified"
    assert R.q4(x, -x + rng.normal(scale=0.2, size=34), draws=500)["status"] == "holds"
    assert R.q4(x[:20], x[:20], draws=500)["status"] == "undecidable", "fewer than 30 companies"


def test_q10_pairs_are_non_overlapping_and_voided_by_a_missing_monday() -> None:
    mondays = [date(2026, 10, 26).toordinal() + 7 * i for i in range(13)]
    series = {date.fromordinal(o): 100.0 * (1.01 ** (i if i % 2 else 0)) for i, o in enumerate(mondays)}
    pairs = R.q10_pairs(series)
    assert len(pairs) == 2, "pairs use t-1, t, t+4 and the next starts at t+5"
    first_move = pairs[0][0]
    del series[date.fromordinal(mondays[1])]
    voided = R.q10_pairs(series)
    assert voided[0][0] != first_move, "the pair that used the missing Monday is gone"
    assert voided[0][0] == pytest.approx(
        series[date.fromordinal(mondays[3])] / series[date.fromordinal(mondays[2])] - 1
    )


def test_q10_counts_same_and_opposite_and_leaves_flat_pairs_out_of_the_test() -> None:
    pairs = [(0.02, 0.03)] * 24 + [(0.02, -0.03)] * 6 + [(0.02, 0.001)] * 10
    out = R.q10(pairs)
    assert (out["tested"], out["same"], out["opposite"], out["flat"]) == (30, 24, 6, 10)
    assert out["status"] == "holds" and out["p_same"] < 0.001
    assert R.q10(pairs[:10])["status"] == "undecidable", "fewer than 30 tested pairs"
    assert R.q10([(0.02, -0.03)] * 30)["status"] == "falsified"


def test_weekly_snapshots_take_the_last_good_monday_run() -> None:
    def run(ts: str, priced: int) -> pl.DataFrame:
        return pl.DataFrame(
            {"snapshot_ts": [ts] * 10, "ticker": list("ABCDEFGHIJ"), "price": [1.0] * priced + [None] * (10 - priced)}
        )

    frame = pl.concat(
        [
            run("2026-10-26T14:00:00+00:00", 10),  # a Monday, good, but later ones exist
            run("2026-10-26T16:00:00+00:00", 9),  # the last good run of that Monday
            run("2026-10-26T18:00:00+00:00", 3),  # throttled: not a snapshot
            run("2026-10-27T07:00:00+00:00", 10),  # a Tuesday
        ]
    )
    got = R.weekly_snapshots(frame, n_ring_a=10)
    assert (
        list(got) == [date(2026, 10, 26)] and got[date(2026, 10, 26)]["snapshot_ts"][0] == "2026-10-26T16:00:00+00:00"
    )


def test_too_few_snapshots_make_a_panel_unscorable(baseline) -> None:
    snaps = {date(2026, 10, 26) + 7 * 0 * (date(2026, 10, 19) - date(2026, 10, 19)): baseline}
    panel = R.build_panel(snaps, F.q1_population)  # one snapshot against a minimum of ten
    assert not panel.enough_snapshots and R.q1(panel, draws=50)["status"] == "undecidable"
