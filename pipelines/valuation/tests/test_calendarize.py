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
    calendar_estimate_or_reason,
    calendarize,
    fiscal_year_end_after,
    from_rows,
    fy_label,
    weights_for,
)


def fe(
    end: str,
    eps: float,
    mark: str = "E",
    n: int | None = 10,
    ccy: str = "USD",
    low: float | None = None,
    high: float | None = None,
) -> FiscalEstimate:
    return FiscalEstimate(
        period_end=date.fromisoformat(end),
        eps=eps,
        mark=mark,
        n_analysts=n,
        currency=ccy,
        eps_low=low,
        eps_high=high,
    )


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


def test_the_forecast_range_is_blended_on_the_same_weights_as_the_mean():
    """Broadcom's calendar 2026 is 10/12 FY2026 and 2/12 FY2027, and the spread must follow the mean.

    Taking min(low) and max(high) across the two fiscal years instead gives 23.00 - 10.50 = 12.50 over a
    12.95 mean, i.e. 96.5% - a year of earnings growth reported as analyst disagreement.
    """
    est = [
        fe("2025-10-31", 6.82, mark="A", n=None),
        fe("2026-10-31", 11.66, low=10.50, high=12.90),
        fe("2027-10-31", 19.38, low=16.00, high=23.00),
    ]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None and cy26.is_blend
    assert cy26.eps_low == pytest.approx(10 / 12 * 10.50 + 2 / 12 * 16.00, rel=1e-9)
    assert cy26.eps_high == pytest.approx(10 / 12 * 12.90 + 2 / 12 * 23.00, rel=1e-9)
    # the blended range sits inside the naive one, which is the whole point
    assert cy26.eps_low > 10.50 and cy26.eps_high < 23.00


def test_a_contributing_year_without_a_range_leaves_no_range_at_all():
    """Coherent: calendar 2026 is half a reported actual, and a reported actual has no high or low.

    Using the estimate half's range alone would divide one fiscal year's spread by a mean blended from
    two, so the calendar year gets no spread rather than a narrower-looking wrong one.
    """
    est = [fe("2026-06-30", 5.61, mark="A", n=None), fe("2027-06-30", 9.42, low=8.26, high=10.26)]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None
    assert cy26.eps_low is None and cy26.eps_high is None
    # the following calendar year is all estimate, so it does get one
    est.append(fe("2028-06-30", 13.96, low=11.00, high=16.50))
    cy27 = calendarize(est, 2027)
    assert cy27 is not None and cy27.eps_low == pytest.approx(0.5 * 8.26 + 0.5 * 11.00, rel=1e-9)


def test_a_december_filer_passes_its_range_through_untouched():
    est = [fe("2026-12-31", 28.68, low=24.10, high=33.40)]
    cy26 = calendarize(est, 2026)
    assert cy26 is not None and not cy26.is_blend
    assert (cy26.eps_low, cy26.eps_high) == pytest.approx((24.10, 33.40))


def test_partial_cover_is_reported_as_coverage_not_as_absence():
    """Yahoo publishes the current and the next fiscal year only.

    For Broadcom's early-November year end that covers calendar 2026 but leaves November and December
    of 2027 uncovered - 0.833, below MIN_COVERAGE. It is not the same fact as having nothing on file,
    and a caller that has to explain the blank to a reader needs to tell the two apart.
    """
    est = [fe("2025-10-31", 6.82, mark="A", n=None), fe("2026-10-31", 11.66), fe("2027-10-31", 19.38)]
    res = calendar_estimate_or_reason(est, 2027)
    assert res.estimate is None
    assert res.coverage == pytest.approx(10 / 12)
    assert res.months_covered == 10
    assert res.fiscal_years == ("FY2025", "FY2026", "FY2027")

    nothing = calendar_estimate_or_reason([], 2027)
    assert nothing.estimate is None and nothing.coverage == 0.0 and nothing.fiscal_years == ()

    # rows on file that touch none of the calendar year read as zero coverage, not partial
    far = calendar_estimate_or_reason([fe("2026-12-31", 1.0)], 2028)
    assert far.estimate is None and far.coverage == 0.0 and far.fiscal_years == ("FY2026",)

    # and calendarize keeps its old contract for the callers that only want the blend
    assert calendarize(est, 2027) is None


def test_macom_loses_a_quarter_of_the_far_calendar_year():
    """MACOM's fiscal year ends in early October, so calendar 2027 is only nine months covered."""
    est = [fe("2025-10-04", 2.4, mark="A", n=None), fe("2026-10-03", 3.6), fe("2027-10-02", 4.9)]
    res = calendar_estimate_or_reason(est, 2027)
    assert res.estimate is None
    assert res.coverage == pytest.approx(9 / 12)
    assert res.months_covered == 9


def test_a_covered_year_reports_full_coverage_alongside_its_blend():
    est = [fe("2025-10-31", 6.82, mark="A", n=None), fe("2026-10-31", 11.66), fe("2027-10-31", 19.38)]
    res = calendar_estimate_or_reason(est, 2026)
    assert res.estimate is not None and res.months_covered == 12
    assert res.estimate.eps == pytest.approx(10 / 12 * 11.66 + 2 / 12 * 19.38, rel=1e-9)


def test_from_rows_carries_the_forecast_range():
    rows = [
        {"period_end": "2026-12-31", "eps_avg": 1.0, "mark": "E", "eps_low": 0.8, "eps_high": 1.3},
        {"period_end": "2027-12-31", "eps_avg": 2.0, "mark": "E"},
    ]
    got = from_rows(rows)
    assert (got[0].eps_low, got[0].eps_high) == (0.8, 1.3)
    assert (got[1].eps_low, got[1].eps_high) == (None, None)

