"""The frozen lists are recomputed from the committed baseline snapshot; they cannot be edited to suit a result."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import stats
from pipelines.cyber.pool import BY_TICKER, POOL


@pytest.fixture(scope="module")
def baseline():
    return F.load_baseline()


def test_the_baseline_covers_every_ring_a_listing_once(baseline) -> None:
    assert sorted(baseline["ticker"].to_list()) == sorted(e.ticker for e in POOL if e.ring == "A")
    assert baseline["snapshot_ts"].n_unique() == 1
    assert baseline["snapshot_ts"][0].startswith(C.BASELINE_FILE_DATE)


def test_population_rules_reproduce_the_registered_lists(baseline) -> None:
    assert F.q1_population(baseline) == sorted(C.Q1_Q2_BASELINE)
    assert F.q4_q5_list(baseline) == sorted(C.FROZEN_Q4_Q5)
    assert F.china_qualifiers(baseline, min_brokers=C.CN_MEDIAN_MIN_BROKERS) == sorted(C.FROZEN_Q7_CN)
    cibr = F.load_cibr()
    assert F.q8_list(baseline, cibr, floor=1e9, cap=1e10) == sorted(C.FROZEN_Q8)


def test_the_cibr_reference_is_the_one_the_section_names() -> None:
    import json

    ref = json.loads(F.CIBR_FILE.read_text(encoding="utf-8"))
    assert ref["as_of"] == "2026-10-15" and F.CIBR_FILE.name == "cibr_holdings_2026-10-15.json"
    assert len(ref["holdings"]) == 45 and "SAIL" not in F.load_cibr()


def test_the_one_yen_rate_is_the_registered_one() -> None:
    assert F.BASELINE_PER_USD["JPY"] == float(C.Q8_JPY_PER_USD)


def test_borderline_enterprise_values_sit_where_the_section_says(baseline) -> None:
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    usd = {t: F.enterprise_value_usd(rows[t]) / 1e9 for t in ("RPD", "RDWR", "SAIL", "VRNS")}
    assert 1.0 <= usd["RPD"] < 1.2 and usd["RDWR"] < 1.0 and usd["SAIL"] > 10.0
    assert BY_TICKER["VRNS"].status == "pending_acquisition"


def test_the_bootstrap_constants_match_the_estimators() -> None:
    assert C.BOOTSTRAP_DRAWS == f"{stats.DRAWS:,}" and C.SEED == str(stats.SEED)
    assert str(stats.MIN_GROUP) == C.Q1["merge_below"]


# --- the next-twelve-month arithmetic, on cases that broke the first draft ------------------------------------
def _row(**kw):
    base = {
        "ticker": "X",
        "fy0_end": "2026-12-31",
        "rev_fy0_avg": 100.0,
        "rev_fy1_avg": 120.0,
        "enterprise_value": 300.0,
        "ttm_revenue": 90.0,
        "ttm_free_cash_flow": 18.0,
        "currency": "USD",
        "financial_currency": "USD",
    }
    return {**base, **kw}


def test_weight_is_days_to_the_end_of_fiscal_year_zero_over_365() -> None:
    assert F.ntm_weight("2026-12-31", date(2026, 7, 2)) == pytest.approx(182 / 365, abs=0.01)
    assert F.ntm_weight("2026-12-31", date(2025, 1, 1)) == 1.0, "never above 1"
    assert F.ntm_weight(None, date(2026, 10, 16)) is None


def test_a_fiscal_year_that_has_ended_but_is_not_yet_reported_gives_weight_zero() -> None:
    """A December company on 2027-01-05: Yahoo still labels 2026 as year 0, so the next twelve months are year +1."""
    row = _row(fy0_end="2026-12-31")
    assert F.ntm_weight(row["fy0_end"], date(2027, 1, 5)) == 0.0
    assert F.ntm_revenue(row, date(2027, 1, 5)) == 120.0
    mitk = _row(fy0_end="2026-09-30")  # Mitek: year ended 2026-09-30, unreported on 2026-10-16
    assert F.ntm_revenue(mitk, date(2026, 10, 16)) == 120.0


def test_midyear_ntm_blends_the_two_years() -> None:
    row = _row()
    w = 182 / 365
    assert F.ntm_revenue(row, date(2026, 7, 2)) == pytest.approx(w * 100 + (1 - w) * 120, rel=0.01)
    assert F.ev_ntm(row, date(2026, 7, 2)) == pytest.approx(300 / F.ntm_revenue(row, date(2026, 7, 2)))


def test_growth_and_fcf_margin_use_trailing_figures() -> None:
    row = _row(fy0_end="2026-10-31")
    assert F.growth(row, date(2026, 10, 16)) == pytest.approx(F.ntm_revenue(row, date(2026, 10, 16)) / 90 - 1)
    assert F.fcf_margin(row) == pytest.approx(0.2)
    assert F.fcf_margin(_row(ttm_free_cash_flow=None)) is None


def test_a_share_uses_east_money_by_calendar_year() -> None:
    row = {
        "ticker": "002439.SZ",
        "em_year0": 2026,
        "em_rev0_mean": 2731.0,
        "em_rev1_mean": 3163.0,
        "enterprise_value": 15000.0,
        "ttm_revenue": 2400.0,
    }
    assert F.ntm_revenue(row, date(2026, 10, 16)) == pytest.approx((76 / 365) * 2731 + (1 - 76 / 365) * 3163, rel=0.01)
    rolled = {**row, "em_year0": 2027, "em_rev0_mean": 3163.0, "em_rev1_mean": 3500.0}
    w = (date(2027, 12, 31) - date(2027, 1, 4)).days / 365
    assert F.ntm_revenue(rolled, date(2027, 1, 4)) == pytest.approx(w * 3163 + (1 - w) * 3500)


def test_a_multiple_is_not_computed_where_enterprise_value_and_revenue_are_in_different_currencies(baseline) -> None:
    """Yahoo states enterpriseValue in the quote currency and the revenue consensus in the financial currency."""
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    mismatched = sorted(t for t, r in rows.items() if not F.currencies_agree(r))
    assert mismatched == ["BTQ", "CNS.L", "KUD.SW"]
    assert all(F.ev_ntm(rows[t], date(2026, 10, 16)) is None for t in mismatched)
    assert F.currencies_agree({"currency": "GBp", "financial_currency": "GBP"}), "pence quoted against pounds reported"
    assert not F.currencies_agree({"currency": "USD", "financial_currency": None})


def test_populations_use_the_registered_pool_not_the_live_one(baseline, monkeypatch) -> None:
    """A company acquired after registration changes the live pool; it must not leave the list it was frozen into."""
    from pipelines.cyber import pool

    monkeypatch.setitem(pool.BY_TICKER, "S", pool.Entry(**{**pool.BY_TICKER["S"].__dict__, "status": "acquired"}))
    assert "S" in F.q1_population(baseline) and "S" in F.q8_list(baseline, F.load_cibr(), floor=1e9, cap=1e10)
