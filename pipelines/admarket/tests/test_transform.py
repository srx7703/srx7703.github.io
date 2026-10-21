"""Derivation rules: quarter mapping, Q4 from annual figures, restatements, dimension rules, first-reported values."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.admarket import config as cfg
from pipelines.admarket import transform as T

R = "RevenueFromContractWithCustomerExcludingAssessedTax"
TOTAL = cfg.Rule("t", "X", None, None, "total")
LINE = cfg.Rule("ads", "X", "ProductOrServiceAxis", ("AdvertisingMember",), "ads")


def fact(start, end, value, dims=None, concept=R):
    return {"concept": concept, "start": start, "end": end, "dims": dims or {}, "value": value}


def filing(filed, facts, acc=None, ticker="X"):
    return {"ticker": ticker, "filed": filed, "accession": acc or f"acc-{filed}", "facts": facts}


@pytest.mark.parametrize("end, q", [
    (date(2025, 3, 31), "2025Q1"), (date(2025, 6, 30), "2025Q2"), (date(2025, 12, 31), "2025Q4"),
    (date(2025, 1, 3), "2024Q4"),   # 52/53-week quarter ending a few days into January
    (date(2024, 9, 28), "2024Q3"),  # ending a few days before the calendar quarter end
    (date(2024, 7, 2), "2024Q2"),
])
def test_calendar_quarter_is_end_minus_fifteen_days(end, q):
    assert T.cal_q(end) == q


def test_shift_and_quarters_between():
    assert T.shift("2025Q4", 1) == "2026Q1"
    assert T.shift("2026Q1", -8) == "2024Q1"
    assert T.quarters_between("2021Q1", "2026Q2") == 21


def test_q4_is_fiscal_year_minus_nine_months():
    f = [filing("2025-10-30", [fact("2025-01-01", "2025-09-30", 300.0), fact("2025-07-01", "2025-09-30", 110.0)]),
         filing("2026-02-01", [fact("2025-01-01", "2025-12-31", 450.0)])]
    q = T.quarterly(f, TOTAL, with_method=True)
    assert q["2025Q3"] == (110.0, "3M")
    assert q["2025Q4"] == (150.0, "FY-9M")


def test_q4_falls_back_to_year_minus_three_quarters():
    facts = [fact("2025-01-01", "2025-03-31", 100.0), fact("2025-04-01", "2025-06-30", 110.0),
             fact("2025-07-01", "2025-09-30", 120.0), fact("2025-01-01", "2025-12-31", 470.0)]
    q = T.quarterly([filing("2026-02-01", facts)], TOTAL, with_method=True)
    assert q["2025Q4"] == (140.0, "FY-3Q")


def test_q4_not_derived_when_a_quarter_is_missing():
    facts = [fact("2025-01-01", "2025-03-31", 100.0), fact("2025-04-01", "2025-06-30", 110.0),
             fact("2025-01-01", "2025-12-31", 470.0)]
    assert "2025Q4" not in T.quarterly([filing("2026-02-01", facts)], TOTAL)


def test_a_reported_three_month_fourth_quarter_beats_the_derivation():
    facts = [fact("2025-01-01", "2025-09-30", 300.0), fact("2025-10-01", "2025-12-31", 149.0),
             fact("2025-01-01", "2025-12-31", 450.0)]
    assert T.quarterly([filing("2026-02-01", facts)], TOTAL)["2025Q4"] == 149.0


def test_latest_filing_wins_on_a_restatement():
    f = [filing("2025-05-01", [fact("2025-01-01", "2025-03-31", 100.0)]),
         filing("2026-05-01", [fact("2025-01-01", "2025-03-31", 97.0)])]
    assert T.quarterly(f, TOTAL)["2025Q1"] == 97.0


def test_fewest_dimensions_win_over_a_later_filing():
    """Meta reports advertising both alone and inside the Family of Apps segment; the plainer fact wins."""
    seg = {"ProductOrServiceAxis": "AdvertisingMember", "StatementBusinessSegmentsAxis": "FamilyOfAppsMember"}
    f = [filing("2025-05-01", [fact("2025-01-01", "2025-03-31", 100.0, {"ProductOrServiceAxis": "AdvertisingMember"})]),
         filing("2026-05-01", [fact("2025-01-01", "2025-03-31", 99.0, seg)])]
    assert T.quarterly(f, LINE)["2025Q1"] == 100.0


def test_dimension_rules():
    ok_seg = {"ProductOrServiceAxis": "AdvertisingMember", "ConsolidationItemsAxis": "OperatingSegmentsMember"}
    geo = {"ProductOrServiceAxis": "AdvertisingMember", "StatementGeographicalAxis": "US"}
    other = {"ProductOrServiceAxis": "SubscriptionMember"}
    f = [filing("2025-05-01", [fact("2025-01-01", "2025-03-31", 10.0, ok_seg), fact("2025-04-01", "2025-06-30", 11.0, geo),
                                fact("2025-07-01", "2025-09-30", 12.0, other)])]
    assert T.quarterly(f, LINE) == {"2025Q1": 10.0}
    # a total takes only dimensionless facts
    assert T.quarterly(f, TOTAL) == {}


def test_contract_revenue_concept_preferred_over_revenues():
    f = [filing("2025-05-01", [fact("2025-01-01", "2025-03-31", 101.0, concept="Revenues"),
                               fact("2025-01-01", "2025-03-31", 100.0)])]
    assert T.quarterly(f, TOTAL)["2025Q1"] == 100.0


def test_other_tickers_and_old_quarters_ignored():
    f = [filing("2019-05-01", [fact("2018-01-01", "2018-03-31", 1.0), fact("2019-01-01", "2019-03-31", 2.0)]),
         filing("2025-05-01", [fact("2025-01-01", "2025-03-31", 3.0)], ticker="Y")]
    assert T.quarterly(f, TOTAL) == {"2019Q1": 2.0}


def test_applovin_composite_switches_to_total_revenue():
    series = {"applovin_ads": {"2025Q1": 1.0, "2025Q2": 2.0}, "applovin_total": {"2025Q1": 9.0, "2025Q2": 3.0}}
    assert T.composite(series, "applovin") == {"2025Q1": 1.0, "2025Q2": 3.0}
    assert T.windowed(series, "applovin", "2025Q2") == {"2025Q2": 3.0}


def test_first_reported_ignores_a_later_restatement(monkeypatch):
    rule = cfg.Rule("ttd_total", "TTD", None, None, "ttd")
    monkeypatch.setattr(cfg, "RULES", (rule,))
    f = [filing("2026-11-05", [fact("2026-07-01", "2026-09-30", 760e6)], acc="q3", ticker="TTD"),
         filing("2027-11-05", [fact("2026-07-01", "2026-09-30", 750e6)], acc="restated", ticker="TTD")]
    fr = T.first_reported(f, "ttd_total", "2026Q3")
    assert fr == {"value": 760e6, "filed": "2026-11-05", "accession": "q3"}
    assert T.quarterly(f, rule)["2026Q3"] == 750e6   # the panel itself takes the restatement
    assert T.first_reported(f[:0], "ttd_total", "2026Q3") is None


def test_volume_price_rows_reparse_snippets():
    snip = ("Three Months Ended June 30, Six Months Ended June 30, 2022 2022 Paid clicks change 12% 14% "
            "Cost-per-click change —% 4% Paid clicks increased")
    f = [{"ticker": "GOOGL", "period": "2022-06-30", "accession": "a", "filed": "2022-07-27",
          "text_record": {"snippets": snip, "parsed": {"google_search_clicks": {"volume": 12, "price": 4}}}}]
    rows = T.volume_price_rows(f)
    assert rows == [{"series": "google_search_clicks", "quarter": "2022Q2", "volume": 12, "price": 0,
                     "period": "2022-06-30", "accession": "a"}]
