"""Pins the registered rules (docs/prereg/ai-servers/2026-11-09_test_rules.md) on small hand-made inputs."""

from datetime import date

import pytest

from pipelines.servers import metrics as M


@pytest.mark.parametrize("end,q", [
    ("2026-05-01", "2026Q1"),  # Dell Feb-Apr
    ("2026-07-31", "2026Q2"),  # Dell May-Jul, HPE May-Jul
    ("2026-10-30", "2026Q3"),  # Dell Aug-Oct (a Friday)
    ("2024-11-01", "2024Q3"),  # Dell's 53-week drift must not jump a quarter
    ("2026-01-30", "2025Q4"),  # Dell Nov-Jan
    ("2026-09-30", "2026Q3"),  # calendar filers
    ("2026-08-28", "2026Q3"),  # Penguin Jun-Aug
])
def test_quarter_placement_is_most_of_the_days(end, q):
    assert M.quarter_of_end(date.fromisoformat(end)) == q


def test_two_fiscal_quarters_in_one_calendar_quarter_keep_the_recurring_one():
    vals = {"2019-08-30": 1.0, "2020-05-29": 2.0, "2020-08-28": 3.0, "2020-08-31": 99.0, "2021-08-27": 4.0}
    assert M.by_quarter(vals)["2020Q3"] == 3.0


def test_ttm_requires_four_consecutive_quarters():
    s = {"2025Q1": 1, "2025Q2": 2, "2025Q3": 3, "2025Q4": 4}
    assert M.ttm(s, "2025Q4") == 10
    assert M.ttm({k: v for k, v in s.items() if k != "2025Q2"}, "2025Q4") is None


def test_spearman_average_ranks_for_ties():
    assert M.ranks([3, 1, 3]) == [2.5, 1.0, 2.5]
    assert M.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert M.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


def _panel(gm0, om0, gm1, om1):
    qs = M.q_range("2022Q3", "2026Q3")
    rev = {q: 100.0 for q in qs}
    gp = {q: (gm0 if q <= "2023Q2" else gm1) * 100 for q in qs}
    oi = {q: (om0 if q <= "2023Q2" else om1) * 100 for q in qs}
    return {"revenue": rev, "gross_profit": gp, "operating_income": oi}


def test_q2_pattern_needs_half_a_point_of_squeeze_and_held_operating_margin():
    assert M.q2_member(_panel(0.10, 0.04, 0.094, 0.04))["pattern"] is True
    assert M.q2_member(_panel(0.10, 0.04, 0.096, 0.05))["pattern"] is False   # 0.4pp is not a squeeze
    assert M.q2_member(_panel(0.10, 0.04, 0.08, 0.039))["pattern"] is False   # operating margin fell


def test_q2_placebo_caps_the_verdict():
    shows = _panel(0.10, 0.04, 0.08, 0.05)
    panel = {k: shows for k in (*M.Q2_GRADED, *M.Q2_PLACEBO)}
    r = M.q2(panel)
    assert r["shows"] == r["n"] == len(M.Q2_GRADED)
    assert r["placebo_shows"] == 3 and r["verdict"] == "undecided"


def test_q5_negative_growth_correlation_with_positive_margin_correlation_is_refuted():
    rows = [{"ticker": t, "fwd_pe": 30 - i, "n_analysts": 5, "growth": i / 10, "operating_margin": (20 - i) / 100}
            for i, t in enumerate(M.Q5_MEMBERS)]
    r = M.q5(rows)
    assert r["n"] == len(M.Q5_MEMBERS) and r["verdict"] == "refuted"


def test_q5_thin_coverage_and_missing_pe_do_not_qualify():
    rows = [{"ticker": "DELL", "fwd_pe": None, "n_analysts": 9, "growth": 0.1, "operating_margin": 0.1},
            {"ticker": "SMCI", "fwd_pe": 10, "n_analysts": 2, "growth": 0.1, "operating_margin": 0.1}]
    r = M.q5(rows)
    assert r["n"] == 0 and r["verdict"] == "undecided"


def test_q7_direction_share_counts_a_zero_change_as_a_mismatch():
    pairs = [{"signal": 0.1, "target": 0.1}, {"signal": 0.2, "target": 0.2}, {"signal": 0.2, "target": 0.3},
             {"signal": 0.1, "target": 0.1}]
    m = M.q7_measures(pairs)
    assert m["n_changes"] == 3 and m["same_direction"] == pytest.approx(2 / 3)


def test_monthly_to_quarter_first_two_months_only():
    s = {"2026-01": 1.0, "2026-02": 2.0, "2026-03": 100.0, "2026-04": 5.0}
    assert M.monthly_to_quarter(s, 2) == {"2026Q1": 3.0}
    assert M.monthly_to_quarter(s, 3) == {"2026Q1": 103.0}


def test_a_balance_goes_with_the_quarter_it_closes():
    # Dell's 2026-07-31 balance closes fiscal Q2 (May-Jul), which is placed in 2026Q2: so must the inventory.
    assert M.by_quarter_instant({"2026-07-31": 1.0, "2026-09-30": 2.0}) == {"2026Q2": 1.0, "2026Q3": 2.0}
