import polars as pl

from pipelines.sec.transform import merge_metric_facts, quarterly_series, ttm, yoy


def test_quarterly_from_3m_and_ytd_chain():
    # Q1 3M direct, 6M and 9M YTD, FY annual -> Q2..Q4 derived by differencing
    spans = {
        ("2025-01-01", "2025-03-31"): 100.0,
        ("2025-01-01", "2025-06-30"): 230.0,
        ("2025-01-01", "2025-09-30"): 380.0,
        ("2025-01-01", "2025-12-31"): 560.0,
        ("2025-04-01", "2025-06-30"): 130.0,  # direct Q2 also reported -> reconciliation pair
    }
    q, recon = quarterly_series(spans)
    assert q["2025-03-31"] == 100.0
    assert q["2025-06-30"] == 130.0  # direct wins
    assert q["2025-09-30"] == 150.0
    assert q["2025-12-31"] == 180.0
    assert recon == {"2025-06-30": (130.0, 130.0)}


def test_quarterly_ignores_non_adjacent_spans():
    spans = {("2024-01-01", "2024-12-31"): 400.0, ("2025-01-01", "2025-12-31"): 500.0}
    q, _ = quarterly_series(spans)
    assert q == {}  # two annual spans with different starts cannot be differenced


def test_ttm_requires_four_consecutive_quarters():
    qs = {"2025-03-31": 1.0, "2025-06-30": 2.0, "2025-09-30": 3.0, "2025-12-31": 4.0, "2026-06-30": 6.0}
    t = ttm(qs)
    assert t == {"2025-12-31": 10.0}  # 2026-06-30 window spans a gap


def test_merge_prefers_priority_tag_but_keeps_history():
    df = pl.DataFrame(
        {
            "metric": ["revenue"] * 3,
            "tag": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
            "start": ["2018-01-01", "2019-01-01", "2019-01-01"],
            "end": ["2018-03-31", "2019-03-31", "2019-03-31"],
            "val": [10.0, 20.0, 21.0],
        }
    )
    spans, used = merge_metric_facts(df, "revenue")
    assert spans[("2019-01-01", "2019-03-31")] == 20.0  # priority tag wins on the shared span
    assert spans[("2018-01-01", "2018-03-31")] == 10.0  # older history from the fallback tag kept
    assert used == ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]


def test_yoy_matches_quarter_one_year_earlier():
    wide = pl.DataFrame(
        {
            "ticker": ["A"] * 3,
            "quarter_end": ["2024-06-30", "2025-03-31", "2025-06-30"],
            "revenue": [100.0, 110.0, 120.0],
        }
    )
    out = yoy(wide, "revenue").sort("quarter_end")
    assert out["revenue_prev"].to_list() == [None, None, 100.0]
