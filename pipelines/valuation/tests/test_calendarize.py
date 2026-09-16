"""Calendar-year blending: the part of the pipeline most likely to be quietly wrong.

The cases below are the real fiscal calendars in the pool, with the period ends observed from Yahoo on
2026-09-16, so a regression here shows up as a failing test rather than as a company silently sitting in
the wrong place on the chart.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.valuation.calendarize import (
    FiscalEstimate,
    calendarize,
    fiscal_year_end_after,
    from_rows,
    fy_label,
    weights_for,
)


def fe(end: str, eps: float, mark: str = "E", n: int | None = 10, ccy: str = "USD") -> FiscalEstimate:
    return FiscalEstimate(period_end=date.fromisoformat(end), eps=eps, mark=mark, n_analysts=n, currency=ccy)


def test_december_year_end_passes_through():
    est = [fe("2026-12-31", 10.0), fe("2027-12-31", 12.0)]
    got = calendarize(est, 2026)
    assert got is not None
    assert got.eps == pytest.approx(10.0)
    assert got.coverage == pytest.approx(1.0)
    assert got.actual_weight == 0.0
    assert not got.is_blend


def test_june_year_end_is_half_and_half():
    """Coherent: FY2026 ended 2026-06-30 (reported), FY2027 and FY2028 are estimates."""
    est = [fe("2026-06-30", 5.61, mark="A", n=None), fe("2027-06-30", 9.42), fe("2028-06-30", 13.96)]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None
    assert cy26.eps == pytest.approx((5.61 + 9.42) / 2, rel=2e-3)
    assert cy26.actual_weight == pytest.approx(0.5, abs=5e-3)
    assert cy26.is_blend
    cy27 = calendarize(est, 2027)
    assert cy27 is not None
    assert cy27.eps == pytest.approx((9.42 + 13.96) / 2, rel=2e-3)
    assert cy27.actual_weight == pytest.approx(0.0, abs=5e-3)


def test_march_year_end_is_quarter_three_quarters():
    """Sumitomo Electric: Japanese fiscal years end 31 March, so calendar 2026 is 3/12 + 9/12."""
    est = [fe("2026-03-31", 118.445, mark="A", n=None, ccy="JPY"),
           fe("2027-03-31", 115.636, ccy="JPY"),
           fe("2028-03-31", 138.192, ccy="JPY")]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None
    assert cy26.eps == pytest.approx(0.25 * 118.445 + 0.75 * 115.636, rel=3e-3)
    assert cy26.actual_weight == pytest.approx(0.25, abs=6e-3)


def test_november_year_end_weights_ten_and_two():
    """Broadcom's fiscal year ends in early November."""
    est = [fe("2025-10-31", 6.82, mark="A", n=None), fe("2026-10-31", 11.66), fe("2027-10-31", 19.38)]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None
    w = {p: round(wt, 3) for p, wt, _ in cy26.parts}
    assert w["2026-10-31"] == pytest.approx(10 / 12, abs=0.01)
    assert w["2027-10-31"] == pytest.approx(2 / 12, abs=0.01)


def test_missing_prior_actual_yields_nothing_not_a_half_number():
    """A June filer with no prior actual only covers half of calendar 2026: report nothing."""
    est = [fe("2027-06-30", 9.42), fe("2028-06-30", 13.96)]
    assert calendarize(est, 2026) is None
    assert calendarize(est, 2027) is not None


def test_weights_never_exceed_the_year():
    est = [fe("2026-06-30", 1.0, mark="A"), fe("2027-06-30", 1.0), fe("2028-06-30", 1.0)]
    total = sum(w for _, w in weights_for(est, 2026))
    assert total == pytest.approx(1.0, abs=0.01)


def test_thinnest_estimate_governs_the_analyst_count():
    est = [fe("2026-06-30", 5.0, mark="A", n=None), fe("2027-06-30", 9.0, n=22), fe("2028-06-30", 13.0, n=4)]
    cy27 = calendarize(est, 2027)
    assert cy27 is not None and cy27.n_analysts == 4


def test_mixed_currencies_refuse_to_claim_one():
    est = [fe("2026-06-30", 5.0, mark="A", ccy="USD"), fe("2027-06-30", 9.0, ccy="CNY")]
    got = calendarize(est, 2026)
    assert got is not None and got.currency is None


def test_fiscal_year_end_after_handles_month_ends_and_rollover():
    assert fiscal_year_end_after(date(2026, 9, 16), 6) == date(2027, 6, 30)
    assert fiscal_year_end_after(date(2026, 9, 16), 12) == date(2026, 12, 31)
    assert fiscal_year_end_after(date(2026, 9, 16), 3) == date(2027, 3, 31)
    assert fiscal_year_end_after(date(2026, 9, 16), 10) == date(2026, 10, 31)
    assert fiscal_year_end_after(date(2026, 12, 31), 12) == date(2027, 12, 31)


def test_fy_label_uses_the_ending_year():
    assert fy_label(date(2027, 6, 30)) == "FY2027"
    assert fy_label(date(2026, 12, 31)) == "FY2026"


def test_from_rows_skips_rows_without_an_estimate():
    rows = [
        {"period_end": "2026-12-31", "eps_avg": 1.0, "mark": "E", "n_analysts": 5, "currency": "CNY"},
        {"period_end": "2027-12-31", "eps_avg": None, "mark": "E"},
        {"period_end": None, "eps_avg": 3.0, "mark": "E"},
    ]
    got = from_rows(rows)
    assert [e.period_end.isoformat() for e in got] == ["2026-12-31"]


def test_leap_day_period_end_does_not_crash():
    est = [fe("2028-02-29", 4.0, mark="A"), fe("2029-02-28", 6.0)]
    got = calendarize(est, 2028)
    assert got is not None and got.eps > 0
