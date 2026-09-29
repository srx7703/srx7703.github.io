"""The registered turning-point rule: golden reproduction first, then the rule's edge cases.

The golden files are the research outputs at registration (152 turns across ten runs, 28 main-rule phases).
They were independently reproduced by a reviewer who rewrote the rule from the Chinese text; the port must
match them exactly before anything is built on it.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import pytest

from pipelines.memcycle import config as cfg
from pipelines.memcycle import ingest
from pipelines.memcycle import transform as T
from pipelines.memcycle.publish import main_run, runs, series

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture(scope="module")
def snap():
    return ingest.snapshot()


@pytest.fixture(scope="module")
def all_runs(snap):
    return runs(snap)


def _golden(name: str) -> list[list[str]]:
    with (GOLDEN / name).open() as f:
        return list(csv.reader(f))[1:]


# ---------------------------------------------------------------------------------------------
# golden reproduction
# ---------------------------------------------------------------------------------------------


def test_golden_turns(all_runs):
    mine = [[r["product"], r["basis"], str(r["amp"]), t.kind, t.month, str(t.confirmed)]
            for r in all_runs for t in r["turns"]]
    gold = _golden("price_turns.csv")
    assert len(all_runs) == 10
    assert len(gold) == 152
    assert mine == gold


def test_golden_phases(all_runs):
    mine = [[p, ph["from"], ph["from_kind"], ph["to"], ph["to_kind"], str(ph["months"]), str(round(ph["change"], 4)),
             str(ph["to_confirmed"])] for p in ("DRAM", "NAND") for ph in T.phases(main_run(all_runs, p)["turns"])]
    gold = _golden("price_phases_main.csv")
    assert len(gold) == 28
    assert mine == gold


def test_deletion_order_matches_correction_3(all_runs):
    """Prereg correction 3 lists the DRAM (C, 20%) deletions in order; steps 3 and 4 run sequentially."""
    log = main_run(all_runs, "DRAM")["deletions"]
    got = [(d.step, f"{d.a.kind}{d.a.month}/{d.b.kind}{d.b.month}") for d in log]
    assert got == [
        ("duration", "P2014-01/T2014-04"), ("duration", "T2020-01/P2020-05"), ("duration", "T1998-07/P1999-02"),
        ("duration", "T2012-01/P2012-06"), ("duration", "T2008-01/P2008-07"), ("duration", "T2000-03/P2000-08"),
        ("duration", "T1999-06/P1999-11"), ("duration", "T2001-10/P2002-03"), ("amplitude", "P2024-07/T2025-02"),
    ]


def test_durations_before_amplitudes(all_runs):
    for r in all_runs:
        steps = [d.step for d in r["deletions"]]
        assert steps == sorted(steps, key=lambda s: s != "duration"), (r["product"], r["basis"], r["amp"])


# ---------------------------------------------------------------------------------------------
# rule cases on the real series
# ---------------------------------------------------------------------------------------------


def test_p1995_07_exists_only_because_the_series_starts_in_1995_01(snap):
    months, values = series(snap, cfg.DRAM_ITEM, "C")
    assert T.date_turns(months, values, cfg.AMP_MAIN)[0].month == "1995-07"
    k = months.index("1995-04")  # the candidate now falls in the first six months and is dropped
    assert T.date_turns(months[k:], values[k:], cfg.AMP_MAIN)[0].month != "1995-07"


def test_knife_edge_at_2000_08(snap, all_runs):
    """Deletion 6 wins by a log gap of about 0.005; 2000-08 about 0.6% higher adds a 1999-06 -> 2000-08 cycle."""
    d6 = main_run(all_runs, "DRAM")["deletions"][5]
    assert d6.b.month == "2000-08" and d6.margin == pytest.approx(0.0049, abs=0.0006)
    months, values = series(snap, cfg.DRAM_ITEM, "C")
    i = months.index("2000-08")
    base = [t.month for t in T.date_turns(months, values, cfg.AMP_MAIN)]
    bumped = list(values)
    bumped[i] *= 1.006
    got = [t.month for t in T.date_turns(months, bumped, cfg.AMP_MAIN)]
    assert "2000-08" not in base and got[:3] == ["1995-07", "1999-06", "2000-08"]


def test_latest_peak_is_the_series_end_and_unconfirmed(all_runs):
    for p in ("DRAM", "NAND"):
        last = main_run(all_runs, p)["turns"][-1]
        assert last.kind == "P" and last.month == cfg.DATA_END and not last.confirmed


# ---------------------------------------------------------------------------------------------
# synthetic cases
# ---------------------------------------------------------------------------------------------


def _months(n: int, start: str = "2000-01") -> list[str]:
    return [T.add_months(start, i) for i in range(n)]


def test_tie_goes_to_the_earliest_month():
    v = [10, 10, 10, 10, 10, 10, 10, 11, 12, 20, 20, 12, 11, 10, 10, 10, 10, 10, 10, 10]
    c = T.candidates(_months(len(v)), [float(x) for x in v])
    assert [t.month for t in c if t.kind == "P"] == ["2000-10"]  # index 9, not the tied index 10


def test_flat_plateau_is_not_a_candidate():
    v = [5.0] * 30
    assert T.candidates(_months(30), v) == []


def test_exact_twenty_percent_move_passes():
    trough, peak = 100.0, 120.0
    tp = [T.Turn(0, "2000-01", "T", trough, True), T.Turn(8, "2000-09", "P", peak, True),
          T.Turn(16, "2001-05", "T", 96.0, True)]
    assert T.amplitude_violations(tp, 0.20) == set()
    tp2 = [tp[0], T.Turn(8, "2000-09", "P", 119.99, True), T.Turn(16, "2001-05", "T", 90.0, True)]
    assert T.amplitude_violations(tp2, 0.20) == {0}


def test_wrong_sign_phase_is_caught_only_by_the_amplitude_step():
    """A trough-to-peak phase that falls is an amplitude violation at any positive threshold; at 0% the rule is
    duration only and does not test signs. On the registered series no 0% run produces one."""
    tp = [T.Turn(0, "2000-01", "T", 100.0, True), T.Turn(8, "2000-09", "P", 90.0, True)]
    assert T.amplitude_violations(tp, 0.20) == {0}
    assert T.amplitude_violations(tp, 0.0) == set()


def test_no_wrong_sign_phase_in_the_registered_zero_threshold_runs(all_runs):
    for r in all_runs:
        for p in T.phases(r["turns"]):
            assert (p["change"] > 0) == (p["from_kind"] == "T"), (r["product"], r["amp"], p)


def test_short_phase_is_removed_with_both_endpoints():
    tp = [T.Turn(0, "2000-01", "P", 100.0, True), T.Turn(10, "2000-11", "T", 50.0, True),
          T.Turn(13, "2001-02", "P", 70.0, True), T.Turn(30, "2002-07", "T", 20.0, True)]
    assert 1 in T.duration_violations(tp)


def test_rejects_non_positive_values():
    with pytest.raises(ValueError):
        T.date_turns(_months(3), [1.0, 0.0, 2.0], 0.2)


@pytest.mark.parametrize("seed", range(25))
def test_random_walk_properties(seed):
    """Whatever the input, the output alternates and obeys every registered constraint."""
    rng = random.Random(seed)
    n = rng.randint(40, 300)
    x, vals = 0.0, []
    for _ in range(n):
        x += rng.gauss(0, 0.08)
        vals.append(math.exp(x))
    months = _months(n, "1990-01")
    for amp in (0.0, 0.2, 0.3):
        tp = T.date_turns(months, vals, amp)
        cands = {(t.month, t.kind) for t in T.candidates(months, vals)}
        assert all((t.month, t.kind) in cands for t in tp)
        assert all(a.kind != b.kind for a, b in zip(tp, tp[1:], strict=False))
        assert not T.duration_violations(tp)
        assert not T.amplitude_violations(tp, amp)
        for t in tp:
            assert t.i >= cfg.HALF_WINDOW
            assert t.confirmed == (t.i + cfg.HALF_WINDOW <= n - 1)
