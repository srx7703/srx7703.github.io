"""Valuation maths, with the currency traps this pool actually contains.

Every rate below is the real FRED close for 2026-09-11, so a test that passes here is arithmetic the
page can stand behind.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.valuation.config import Company
from pipelines.valuation.metrics import (
    NM_LOSS,
    NM_NEG_FORECAST,
    NM_NO_CONSENSUS,
    NM_NO_EARNINGS,
    NM_NO_FX,
    NM_SOURCE_UNAVAILABLE,
    company_row,
    convert,
    coverage_flag,
    dispersion,
    expected_growth,
    forward_pe,
    major_currency,
    nearest_vintage,
    normalise_quote,
    revision,
    sources_with_rows,
    trailing_pe,
    vintage_estimates,
)

RATES = {"USD": 1.0, "CNY": 6.7080, "JPY": 153.71, "KRW": 1340.30, "TWD": 31.6400, "HKD": 7.8427, "GBP": 1 / 1.3524}


def test_pence_quotes_become_pounds():
    assert normalise_quote(23.5, "GBp") == (0.235, "GBP")
    assert major_currency("GBp") == "GBP"
    assert normalise_quote(289.64, "USD") == (289.64, "USD")
    assert normalise_quote(None, "GBp") == (None, "GBp")


def test_convert_round_trips_and_crosses():
    assert convert(100.0, "USD", "USD", RATES) == 100.0
    assert convert(6.708, "CNY", "USD", RATES) == pytest.approx(1.0)
    # 1 USD buys 6.708 CNY and 7.8427 HKD, so 1 CNY is about 1.169 HKD
    assert convert(1.0, "CNY", "HKD", RATES) == pytest.approx(7.8427 / 6.7080, rel=1e-9)
    assert convert(1.0, "GBP", "USD", RATES) == pytest.approx(1.3524, rel=1e-9)
    assert convert(1.0, "CNY", "XXX", RATES) is None


def test_trailing_pe_converts_market_cap_into_the_reporting_currency():
    """CATL's Hong Kong line: market cap in HKD, profit in CNY. Skipping the conversion is a 17% error."""
    cap_hkd = 2318032699392.0
    profit_cny = 80e9
    pe, nm = trailing_pe(
        market_cap=cap_hkd, market_cap_currency="HKD",
        ttm_net_income=profit_cny, reporting_currency="CNY", rates=RATES,
    )
    assert nm is None
    assert pe == pytest.approx(cap_hkd / 7.8427 * 6.7080 / profit_cny, rel=1e-9)
    naive = cap_hkd / profit_cny
    assert abs(pe - naive) / naive > 0.1  # the trap is worth guarding


def test_trailing_pe_refuses_a_negative_denominator():
    assert trailing_pe(market_cap=1e9, market_cap_currency="USD", ttm_net_income=-5e7,
                       reporting_currency="USD", rates=RATES) == (None, NM_LOSS)
    assert trailing_pe(market_cap=1e9, market_cap_currency="USD", ttm_net_income=None,
                       reporting_currency="USD", rates=RATES) == (None, NM_NO_EARNINGS)
    assert trailing_pe(market_cap=1e9, market_cap_currency="ZZZ", ttm_net_income=1e8,
                       reporting_currency="CNY", rates=RATES) == (None, NM_NO_FX)


def test_forward_pe_converts_the_consensus_into_the_price_currency():
    """3750.HK trades at HKD 501 while its consensus EPS of 20.93 is stated in CNY."""
    pe, nm = forward_pe(price=501.0, price_currency="HKD", eps=20.93, eps_currency="CNY", rates=RATES)
    assert nm is None
    eps_hkd = 20.93 / 6.7080 * 7.8427
    assert pe == pytest.approx(501.0 / eps_hkd, rel=1e-9)
    assert pe == pytest.approx(20.5, abs=0.3)
    assert abs(pe - 501.0 / 20.93) > 1.0  # the unconverted figure is materially different


def test_forward_pe_handles_pence_and_missing_pieces():
    pe, nm = forward_pe(price=23.5, price_currency="GBp", eps=0.05, eps_currency="GBP", rates=RATES)
    assert nm is None and pe == pytest.approx(0.235 / 0.05)
    assert forward_pe(price=10.0, price_currency="USD", eps=-0.68, eps_currency="USD", rates=RATES) == (None, NM_NEG_FORECAST)
    assert forward_pe(price=10.0, price_currency="USD", eps=None, eps_currency="USD", rates=RATES) == (None, NM_NO_CONSENSUS)


def test_a_share_forward_pe_matches_the_published_one():
    """East Money publishes 300308 at 32.88x on 2026E EPS 28.68; ours must agree."""
    pe, nm = forward_pe(price=943.0, price_currency="CNY", eps=28.68, eps_currency="CNY", rates=RATES)
    assert nm is None and pe == pytest.approx(32.88, rel=0.02)


def test_expected_growth_needs_two_profitable_years():
    assert expected_growth(28.68, 51.81) == pytest.approx(51.81 / 28.68 - 1)
    assert expected_growth(-1.0, 2.0) is None
    assert expected_growth(2.0, -1.0) is None
    assert expected_growth(None, 2.0) is None


def test_dispersion_is_a_share_of_the_mean():
    assert dispersion(49.38, 84.33, 66.50) == pytest.approx((84.33 - 49.38) / 66.50)
    assert dispersion(None, 84.33, 66.50) is None
    assert dispersion(1.0, 2.0, 0.0) is None


def test_revision_handles_a_negative_base():
    assert revision(11.0, 10.0) == pytest.approx(0.1)
    # an estimate moving from -1.0 to -0.5 is an upgrade, so the sign must come out positive
    assert revision(-0.5, -1.0) == pytest.approx(0.5)
    assert revision(-1.5, -1.0) == pytest.approx(-0.5)
    assert revision(10.0, 0.0) is None


def test_coverage_flag_thresholds():
    assert coverage_flag(None) == "unknown"
    assert coverage_flag(1) == "thin"
    assert coverage_flag(2) == "thin"
    assert coverage_flag(3) == "covered"


def test_vintage_estimates_skip_dates_that_no_longer_cover_the_year():
    rows = [
        # a full pair on the older date, only one fiscal year on the newer one
        {"as_of": "2026-06-18", "period_end": "2026-06-30", "eps_avg": 5.0, "mark": "A", "currency": "USD"},
        {"as_of": "2026-06-18", "period_end": "2027-06-30", "eps_avg": 9.0, "mark": "E", "currency": "USD"},
        {"as_of": "2026-09-16", "period_end": "2027-06-30", "eps_avg": 9.4, "mark": "E", "currency": "USD"},
    ]
    got = vintage_estimates(rows, (2026, 2027))
    assert got["2026-06-18"][2026] == pytest.approx((5.0 + 9.0) / 2)
    # the newer vintage holds one fiscal year, which covers neither calendar year for a June filer,
    # so the date is dropped rather than reported as a collapse in the estimate
    assert "2026-09-16" not in got


def test_vintage_estimates_keep_a_december_filer_with_one_fiscal_year():
    rows = [{"as_of": "2026-09-16", "period_end": "2027-12-31", "eps_avg": 9.4, "mark": "E", "currency": "CNY"}]
    got = vintage_estimates(rows, (2026, 2027))
    assert got["2026-09-16"][2027] == pytest.approx(9.4)
    assert 2026 not in got["2026-09-16"]


def test_nearest_vintage_will_not_reach_past_a_month():
    vints = {"2026-08-17": {2026: 9.0}, "2026-06-18": {2026: 8.2}}
    hit = nearest_vintage(vints, date(2026, 8, 17), 2026)
    assert hit == ("2026-08-17", 9.0)
    assert nearest_vintage(vints, date(2026, 1, 1), 2026) is None
    assert nearest_vintage(vints, date(2026, 8, 17), 2027) is None


# --- company_row: the wiring, which is where the spread and the missing-number reasons are decided ---

AVGO = Company("AVGO", "Broadcom", "optical", "partial", "us", 10, note="fiscal year ends early November")
COHR = Company("COHR", "Coherent", "optical", "high", "us", 6, share_basis="total")
YEARS = (2026, 2027)
AS_OF = date(2026, 9, 16)


def est(period_end: str, eps: float, *, mark: str = "E", low=None, high=None, n=None, source="yahoo") -> dict:
    return {
        "ticker": "AVGO", "source": source, "period_end": period_end, "mark": mark,
        "eps_avg": eps, "eps_low": low, "eps_high": high, "n_analysts": n, "currency": "USD",
    }


# Yahoo publishes the current and the next fiscal year plus the prior actual, and nothing further out.
AVGO_ROWS = [
    est("2025-10-31", 6.82, mark="A"),
    est("2026-10-31", 11.66, low=10.50, high=12.90, n=28),
    est("2027-10-31", 19.38, low=16.00, high=23.00, n=26),
]
AVGO_PRICE = {"price": 350.0, "currency": "USD", "market_cap": 1.6e12, "financial_currency": "USD"}


def avgo_row(**kw) -> dict:
    args = dict(
        price_row=AVGO_PRICE, estimate_rows=AVGO_ROWS, vintage_rows=[], ttm=None,
        rates=RATES, years=YEARS, as_of=AS_OF, sources_ran=frozenset({"yahoo"}),
    )
    args.update(kw)
    return company_row(AVGO, **args)


def test_the_spread_is_blended_on_the_blend_weights_not_taken_across_fiscal_years():
    """Broadcom's calendar 2026 is 10/12 FY2026 + 2/12 FY2027, so the spread must use those weights.

    min(eps_low)/max(eps_high) across both fiscal years gives (23.00 - 10.50) / 12.95 = 96.5%, which is
    mostly a year of earnings growth. The weight-consistent figure is 24.5%, and the column is headed
    "Forecast spread".
    """
    row = avgo_row()
    mean = 10 / 12 * 11.66 + 2 / 12 * 19.38
    low = 10 / 12 * 10.50 + 2 / 12 * 16.00
    high = 10 / 12 * 12.90 + 2 / 12 * 23.00
    assert row["blend"] is True
    assert row["dispersion"] == pytest.approx((high - low) / mean, rel=1e-9)
    assert row["dispersion"] == pytest.approx(0.2446, abs=5e-4)
    naive = (23.00 - 10.50) / mean
    assert naive == pytest.approx(0.9655, abs=5e-4)  # what the column used to say
    assert row["dispersion"] < naive / 3


def test_a_calendar_year_that_is_part_actual_reports_no_spread_rather_than_a_narrower_one():
    """Coherent: half of calendar 2026 is a reported actual, which Yahoo gives no range for."""
    rows = [
        {"ticker": "COHR", "source": "yahoo", "period_end": "2026-06-30", "mark": "A",
         "eps_avg": 5.61, "eps_low": None, "eps_high": None, "n_analysts": None, "currency": "USD"},
        {"ticker": "COHR", "source": "yahoo", "period_end": "2027-06-30", "mark": "E",
         "eps_avg": 9.42, "eps_low": 8.26, "eps_high": 10.26, "n_analysts": 22, "currency": "USD"},
    ]
    row = company_row(
        COHR, price_row={"price": 120.0, "currency": "USD", "market_cap": 1.9e10, "financial_currency": "USD"},
        estimate_rows=rows, vintage_rows=[], ttm=None, rates=RATES, years=YEARS, as_of=AS_OF,
        sources_ran=frozenset({"yahoo"}),
    )
    assert row["fwd_pe_2026"] is not None  # the multiple itself is fine
    assert row["dispersion"] is None  # (10.26 - 8.26) / 7.515 = 26.6% would be one year's range over two


def test_a_fiscal_calendar_that_runs_off_the_end_of_the_panel_says_so():
    """AVGO/MTSI/CIEN cover calendar 2026 but not the last months of 2027, and Yahoo stops at +1y.

    Calling that "no consensus for this year" is false: Yahoo publishes a full FY2027 panel, and the
    same row prints n_analysts from it.
    """
    row = avgo_row()
    assert row["fwd_pe_2026"] is not None and row["fwd_pe_2026_nm"] is None
    assert row["fwd_pe_2027"] is None
    assert row["fwd_pe_2027_nm"] != NM_NO_CONSENSUS
    assert row["fwd_pe_2027_nm"] == (
        "the fiscal years on file (FY2025, FY2026, FY2027) cover 10 of the 12 months of calendar 2027"
    )


def test_an_unfetched_source_is_not_reported_as_analysts_not_covering_the_listing():
    """An upstream fetch failure is ours, not a statement about analyst behaviour."""
    blank = dict(
        price_row={"price": 120.0, "currency": "USD", "market_cap": 1.9e10, "financial_currency": "USD"},
        estimate_rows=[], vintage_rows=[], ttm=None, rates=RATES, years=YEARS, as_of=AS_OF,
    )
    # East Money landed, Yahoo did not: every Yahoo-routed listing is blank because of the run
    failed = company_row(COHR, **blank, sources_ran=frozenset({"eastmoney"}))
    assert failed["fwd_pe_2026_nm"] == NM_SOURCE_UNAVAILABLE
    assert failed["fwd_pe_2027_nm"] == NM_SOURCE_UNAVAILABLE

    # Yahoo did run and still returned nothing for this listing, so the claim about analysts holds
    ran = company_row(COHR, **blank, sources_ran=frozenset({"yahoo", "eastmoney"}))
    assert ran["fwd_pe_2026_nm"] == NM_NO_CONSENSUS

    # and with no knowledge of the run we do not assert either way
    assert company_row(COHR, **blank)["fwd_pe_2026_nm"] == NM_NO_CONSENSUS


def test_partial_cover_wins_over_the_source_reason():
    """A listing with rows on file is never blank because its source failed."""
    row = avgo_row(sources_ran=frozenset({"eastmoney"}))
    assert row["fwd_pe_2027_nm"].startswith("the fiscal years on file")


def test_sources_with_rows_reads_the_run_rather_than_the_config():
    by_ticker = {
        "300308.SZ": [{"source": "eastmoney", "eps_avg": 28.68}],
        "COHR": [],
        "AAOI": [{"source": None, "eps_avg": 1.0}],
    }
    assert sources_with_rows(by_ticker) == frozenset({"eastmoney"})
    assert sources_with_rows({}) == frozenset()

