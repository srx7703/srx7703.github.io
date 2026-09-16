"""Pure-function tests for the fundamentals module. Nothing here touches the network.

The East Money fixture is the real RPT_LICO_FN_CPD payload shape for 300308 (Innolight), including
the ``"YYYY-MM-DD HH:MM:SS"`` REPORTDATE and the cumulative year-to-date values that make the
differencing necessary in the first place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipelines.valuation import fundamentals as fd
from pipelines.valuation.config import BY_TICKER


def _only(directory: Path) -> Path:
    """The single parquet part a run wrote, without depending on today's date."""
    parts = sorted(directory.glob("*.parquet"))
    assert len(parts) == 1, f"expected one part in {directory}, found {[p.name for p in parts]}"
    return parts[0]

# --------------------------------------------------------------------------------------- fixtures

EM_ROWS = [
    ("2026-06-30", 12.31, 13651149693.27, 41777861795.03),
    ("2026-03-31", 5.18, 5734501526.83, 19496398083.95),
    ("2025-12-31", 9.80, 10797254300.45, 38239935640.67),
    ("2025-09-30", 6.48, 7131932436.70, 25004800851.32),
    ("2025-06-30", 3.64, 3995115384.70, 14789074837.80),
    ("2025-03-31", 1.44, 1582876128.67, 6674176487.23),
]

EM_PAYLOAD = {
    "version": "b0f2c2a3",
    "result": {
        "pages": 1,
        "count": len(EM_ROWS),
        "data": [
            {
                "SECURITY_CODE": "300308",
                "SECURITY_NAME_ABBR": "中际旭创",
                "REPORTDATE": f"{d} 00:00:00",
                "BASIC_EPS": eps,
                "PARENT_NETPROFIT": ni,
                "TOTAL_OPERATE_INCOME": rev,
            }
            for d, eps, ni, rev in EM_ROWS
        ],
    },
    "success": True,
    "message": "ok",
    "code": 0,
}


def _em(period_end: str, net_income: float | None, revenue: float | None) -> dict:
    return {"period_end": period_end, "net_income": net_income, "revenue": revenue, "currency": "CNY"}


def _fact(start: str, end: str, val: float, *, form: str = "10-Q", filed: str = "2025-05-01") -> dict:
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed, "fy": 2025, "fp": "Q1"}


def _companyfacts(**gaap: dict) -> dict:
    return {"entityName": "TEST CO", "cik": 1234, "facts": {"us-gaap": gaap}}


def _usd(facts: list[dict]) -> dict:
    return {"units": {"USD": facts}}


# ------------------------------------------------------------------------------------- East Money


def test_parse_eastmoney_normalises_reportdate_and_orders_oldest_first():
    rows = fd.parse_eastmoney(EM_PAYLOAD, security_code="300308")
    assert [r["period_end"] for r in rows] == [
        "2025-03-31",
        "2025-06-30",
        "2025-09-30",
        "2025-12-31",
        "2026-03-31",
        "2026-06-30",
    ]
    assert rows[-1]["net_income"] == 13651149693.27
    assert rows[-1]["revenue"] == 41777861795.03
    assert rows[-1]["currency"] == "CNY"


def test_parse_eastmoney_raises_on_success_false_despite_http_200():
    with pytest.raises(fd.SourceError, match="success=false"):
        fd.parse_eastmoney({"success": False, "message": "查询失败", "result": None}, security_code="300308")


def test_difference_cumulative_on_verified_300308_rows():
    rows = fd.difference_cumulative(fd.parse_eastmoney(EM_PAYLOAD))
    got = {r["period_end"]: r for r in rows}
    assert list(got) == ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]

    # the headline case: Q2 2026 = H1 2026 - Q1 2026
    assert got["2026-06-30"]["net_income"] == pytest.approx(13651149693.27 - 5734501526.83)
    assert got["2026-06-30"]["revenue"] == pytest.approx(41777861795.03 - 19496398083.95)
    assert got["2026-06-30"]["derived"] is True

    # Q4 2025 = FY - 9M, Q3 2025 = 9M - H1
    assert got["2025-12-31"]["net_income"] == pytest.approx(10797254300.45 - 7131932436.70)
    assert got["2025-09-30"]["revenue"] == pytest.approx(25004800851.32 - 14789074837.80)

    # Q1 of each year is as reported, never differenced against the prior December
    assert got["2025-03-31"]["net_income"] == 1582876128.67
    assert got["2025-03-31"]["derived"] is False
    assert got["2026-03-31"]["net_income"] == 5734501526.83
    assert got["2026-03-31"]["derived"] is False
    assert got["2026-03-31"]["currency"] == "CNY"


def test_difference_cumulative_breaks_the_chain_when_the_9m_period_is_missing():
    cumulative = [
        _em("2024-03-31", 100.0, 1000.0),
        _em("2024-06-30", 250.0, 2200.0),
        # no 2024-09-30 filing
        _em("2024-12-31", 600.0, 5000.0),
    ]
    dropped: list[dict] = []
    rows = fd.difference_cumulative(cumulative, dropped=dropped)

    assert [r["period_end"] for r in rows] == ["2024-03-31", "2024-06-30"]
    # Q4 would have been FY - H1, a six-month "quarter"; it is dropped and recorded instead.
    assert [d["period_end"] for d in dropped] == ["2024-12-31"]
    assert "Q3 2024 missing" in dropped[0]["reason"]
    assert rows[1]["net_income"] == pytest.approx(150.0)


def test_difference_cumulative_never_crosses_a_year_boundary():
    cumulative = [_em("2024-12-31", 600.0, 5000.0), _em("2025-03-31", 90.0, 900.0)]
    rows = fd.difference_cumulative(cumulative)
    q1_2025 = next(r for r in rows if r["period_end"] == "2025-03-31")
    assert q1_2025["net_income"] == 90.0  # not 90 - 600
    assert q1_2025["derived"] is False


def test_difference_cumulative_keeps_none_values_as_none():
    rows = fd.difference_cumulative([_em("2025-03-31", 10.0, None), _em("2025-06-30", 30.0, 500.0)])
    assert rows[0]["revenue"] is None
    assert rows[1]["revenue"] is None  # 500 - unknown is unknown, not 500
    assert rows[1]["net_income"] == pytest.approx(20.0)


def test_em_security_code_strips_the_exchange_prefix():
    assert fd.em_security_code(BY_TICKER["300308.SZ"]) == "300308"
    assert fd.em_security_code(BY_TICKER["601869.SS"]) == "601869"


# -------------------------------------------------------------------------------------------- SEC


def test_sec_spans_prefers_the_higher_priority_tag_and_the_latest_filing():
    cf = _companyfacts(
        NetIncomeLoss=_usd(
            [
                _fact("2025-01-01", "2025-03-31", 100.0, filed="2025-05-01"),
                _fact("2025-01-01", "2025-03-31", 110.0, filed="2025-08-01"),  # restatement wins
                _fact("2025-01-01", "2025-03-31", 999.0, form="8-K", filed="2025-09-01"),  # wrong form
            ]
        ),
        ProfitLoss=_usd([_fact("2025-01-01", "2025-03-31", 555.0, filed="2025-09-01")]),
    )
    spans, unit, used = fd.sec_spans(cf, fd.SEC_NET_INCOME_TAGS)
    assert spans == {("2025-01-01", "2025-03-31"): 110.0}
    assert unit == "USD"
    assert used == ["NetIncomeLoss"]


def test_sec_spans_falls_back_to_a_lower_priority_revenue_tag():
    cf = _companyfacts(Revenues=_usd([_fact("2025-01-01", "2025-03-31", 4000.0)]))
    spans, _unit, used = fd.sec_spans(cf, fd.SEC_REVENUE_TAGS)
    assert used == ["Revenues"]
    assert spans[("2025-01-01", "2025-03-31")] == 4000.0


def test_sec_quarters_marks_differenced_quarters_derived():
    cf = _companyfacts(
        NetIncomeLoss=_usd(
            [
                _fact("2025-01-01", "2025-03-31", 100.0),
                _fact("2025-01-01", "2025-06-30", 260.0, filed="2025-08-01"),  # H1, differenced to Q2
            ]
        ),
        Revenues=_usd(
            [
                _fact("2025-01-01", "2025-03-31", 1000.0),
                _fact("2025-01-01", "2025-06-30", 2200.0, filed="2025-08-01"),
            ]
        ),
    )
    rows, coverage = fd.sec_quarters(cf)
    got = {r["period_end"]: r for r in rows}
    assert got["2025-03-31"]["net_income"] == 100.0
    assert got["2025-03-31"]["derived"] is False
    assert got["2025-06-30"]["net_income"] == pytest.approx(160.0)
    assert got["2025-06-30"]["revenue"] == pytest.approx(1200.0)
    assert got["2025-06-30"]["derived"] is True
    assert got["2025-06-30"]["currency"] == "USD"
    assert coverage["net_income_tags"] == ["NetIncomeLoss"]
    assert coverage["revenue_tags"] == ["Revenues"]


def test_sec_spans_lets_an_amendment_supersede_the_original_filing():
    """A restatement arrives on a 10-K/A. Excluding amendments kept the superseded number and, for
    a period only ever filed on an amendment, lost the span entirely - which breaks a TTM window.
    """
    cf = _companyfacts(
        NetIncomeLoss=_usd(
            [
                _fact("2025-01-01", "2025-03-31", 100.0, form="10-Q", filed="2025-05-01"),
                _fact("2025-01-01", "2025-03-31", 88.0, form="10-Q/A", filed="2025-11-01"),
                _fact("2025-04-01", "2025-06-30", 50.0, form="10-Q/A", filed="2025-11-01"),
            ]
        )
    )
    spans, _unit, _used = fd.sec_spans(cf, fd.SEC_NET_INCOME_TAGS)
    assert spans[("2025-01-01", "2025-03-31")] == 88.0  # the amendment wins
    assert spans[("2025-04-01", "2025-06-30")] == 50.0  # and carries a span nothing else has
    assert set(fd.SEC_FORMS) == {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def test_sec_spans_still_ignores_forms_that_are_not_periodic_reports():
    cf = _companyfacts(
        NetIncomeLoss=_usd([_fact("2025-01-01", "2025-03-31", 999.0, form="8-K", filed="2025-09-01")])
    )
    spans, _unit, used = fd.sec_spans(cf, fd.SEC_NET_INCOME_TAGS)
    assert spans == {}
    assert used == []


def test_sec_quarters_on_empty_facts():
    rows, coverage = fd.sec_quarters({"facts": {}})
    assert rows == []
    assert coverage["unit"] is None


def test_sec_user_agent_needs_an_email():
    assert fd.sec_user_agent_ok("Ruoxuan Song me@example.com")
    assert not fd.sec_user_agent_ok("portfolio-pipelines/0.1 (github.com/srx7703)")


# ------------------------------------------------------------------------------------------ Yahoo


def test_yahoo_line_item_fallbacks():
    stmt = {
        "Net Income Common Stockholders": {"2026-06-30": 5.0, "2026-03-31": 4.0},
        "Operating Revenue": {"2026-06-30": 50.0, "2026-03-31": 40.0},
    }
    rows, cov = fd.parse_yahoo_rows(stmt, currency="JPY")
    assert cov == {"net_income_line": "Net Income Common Stockholders", "revenue_line": "Operating Revenue"}
    assert [r["period_end"] for r in rows] == ["2026-03-31", "2026-06-30"]
    assert rows[-1]["net_income"] == 5.0
    assert rows[-1]["revenue"] == 50.0
    assert rows[-1]["currency"] == "JPY"
    assert rows[-1]["derived"] is False


def test_yahoo_prefers_net_income_and_total_revenue_when_present():
    stmt = {
        "Net Income": {"2026-06-30": 9.0},
        "Net Income Common Stockholders": {"2026-06-30": 7.0},
        "Net Income From Continuing Operation Net Minority Interest": {"2026-06-30": 8.0},
        "Total Revenue": {"2026-06-30": 90.0},
        "Operating Revenue": {"2026-06-30": 80.0},
    }
    rows, cov = fd.parse_yahoo_rows(stmt, currency="KRW")
    assert (cov["net_income_line"], cov["revenue_line"]) == ("Net Income", "Total Revenue")
    assert (rows[0]["net_income"], rows[0]["revenue"]) == (9.0, 90.0)


def test_yahoo_skips_an_all_null_line_and_takes_the_next_candidate():
    stmt = {
        "Net Income": {"2026-06-30": None, "2026-03-31": None},
        "Net Income From Continuing Operation Net Minority Interest": {"2026-06-30": 3.0, "2026-03-31": None},
        "Total Revenue": {"2026-06-30": 30.0, "2026-03-31": 20.0},
    }
    rows, cov = fd.parse_yahoo_rows(stmt, currency="TWD")
    assert cov["net_income_line"] == "Net Income From Continuing Operation Net Minority Interest"
    got = {r["period_end"]: r for r in rows}
    assert got["2026-06-30"]["net_income"] == 3.0
    assert got["2026-03-31"]["net_income"] is None  # revenue alone still keeps the row
    assert got["2026-03-31"]["revenue"] == 20.0


def test_yahoo_drops_a_period_with_no_values_at_all():
    stmt = {"Net Income": {"2026-06-30": 3.0, "2025-12-31": None}, "Total Revenue": {"2026-06-30": 30.0}}
    rows, _ = fd.parse_yahoo_rows(stmt, currency="HKD")
    assert [r["period_end"] for r in rows] == ["2026-06-30"]


def test_statement_to_dict_flattens_a_yfinance_frame():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        [[5.0, 4.0], [50.0, float("nan")]],
        index=["Net Income", "Total Revenue"],
        columns=[pd.Timestamp("2026-06-30"), pd.Timestamp("2026-03-31")],
    )
    assert fd.statement_to_dict(df) == {
        "Net Income": {"2026-06-30": 5.0, "2026-03-31": 4.0},
        "Total Revenue": {"2026-06-30": 50.0, "2026-03-31": None},
    }
    assert fd.statement_to_dict(None) == {}
    assert fd.statement_to_dict(pd.DataFrame()) == {}


# -------------------------------------------------------------------------------------------- TTM


def _q(ticker: str, period_end: str, ni: float, rev: float, currency: str = "USD") -> dict:
    return {
        "ticker": ticker,
        "period_end": period_end,
        "net_income": ni,
        "revenue": rev,
        "currency": currency,
    }


FOUR_CLEAN = [
    _q("COHR", "2025-09-30", 1.0, 10.0),
    _q("COHR", "2025-12-31", 2.0, 20.0),
    _q("COHR", "2026-03-31", 3.0, 30.0),
    _q("COHR", "2026-06-30", 4.0, 40.0),
]


def test_ttm_accepts_a_clean_four_quarter_window():
    out = fd.ttm_from_quarters(FOUR_CLEAN, "COHR")
    assert out == {
        "period_end": "2026-06-30",
        "ttm_net_income": 10.0,
        "ttm_revenue": 100.0,
        "n_quarters": 4,
        "span_days": 364,
        "currency": "USD",
        "basis": "ttm",
    }
    assert fd.TTM_MIN_SPAN <= out["span_days"] <= fd.TTM_MAX_SPAN


def test_ttm_carries_the_statements_own_currency():
    """metrics.company_row reads ttm["currency"] first and the prices table only as a fallback.

    Dropping it meant the FX leg of trailing PE always used the price feed's opinion of the
    reporting currency - and that opinion is missing exactly when a listing failed to price.
    """
    cny = [dict(r, currency="CNY", ticker="601869.SS") for r in FOUR_CLEAN]
    assert fd.ttm_from_quarters(cny, "601869.SS")["currency"] == "CNY"


def test_ttm_rejects_a_window_that_changes_reporting_currency():
    mixed = [dict(r) for r in FOUR_CLEAN]
    mixed[0]["currency"] = "HKD"  # three quarters in USD, one in HKD: the sum is in no currency
    assert fd.ttm_from_quarters(mixed, "COHR") is None


def test_ttm_tolerates_rows_with_no_currency_at_all():
    blank = [dict(r, currency=None) for r in FOUR_CLEAN]
    out = fd.ttm_from_quarters(blank, "COHR")
    assert out["currency"] is None  # unknown, not a disagreement: the window still totals
    assert out["ttm_net_income"] == 10.0


def test_ttm_rejects_a_window_that_is_too_old_when_an_age_limit_is_given():
    """Four quarters ending in 2019 are internally perfect; only the calendar catches them."""
    assert fd.ttm_from_quarters(FOUR_CLEAN, "COHR", "2026-09-16", max_age_days=200) is not None
    assert fd.ttm_from_quarters(FOUR_CLEAN, "COHR", "2027-09-16", max_age_days=200) is None
    # No as_of and no limit means no age gate, which is why load_ttm always supplies the run date.
    assert fd.ttm_from_quarters(FOUR_CLEAN, "COHR", max_age_days=200) is not None


def test_ttm_rejects_a_three_quarter_window():
    assert fd.ttm_from_quarters(FOUR_CLEAN[1:], "COHR") is None


def test_ttm_rejects_a_window_with_a_hole_in_it():
    rows = [
        _q("COHR", "2025-03-31", 1.0, 10.0),
        _q("COHR", "2025-06-30", 2.0, 20.0),
        # 2025-09-30 and 2025-12-31 never filed
        _q("COHR", "2026-03-31", 3.0, 30.0),
        _q("COHR", "2026-06-30", 4.0, 40.0),
    ]
    assert fd.ttm_from_quarters(rows, "COHR") is None


def test_ttm_rejects_a_window_whose_oldest_row_is_over_a_year_early():
    rows = [
        _q("6752.T", "2024-03-31", 400.0, 4000.0),  # 15 months before the next row
        _q("6752.T", "2025-06-30", 2.0, 20.0),
        _q("6752.T", "2025-09-30", 3.0, 30.0),
        _q("6752.T", "2025-12-31", 4.0, 40.0),
    ]
    assert fd.ttm_from_quarters(rows, "6752.T") is None  # the span gate, same as a hole


def test_ttm_cannot_tell_a_quarter_aligned_annual_row_from_a_quarter():
    """Why the annual rows live in their own table, pinned as the failure it would otherwise be.

    A December filer's fiscal year ends on a quarter end, so an annual row concatenated into the
    quarterly table passes every gate ttm_from_quarters has: four rows, 364 days, one currency.
    The total is then 34x too large and the trailing PE 34x too small, silently. Nothing in this
    function can catch it - the separation of the two parquet directories is the only defence, so
    if this ever stops being true the annual table has been merged into the quarterly one.
    """
    smuggled = [
        _q("6752.T", "2025-12-31", 400.0, 4000.0),  # FY2025, not Q4
        _q("6752.T", "2026-03-31", 3.0, 30.0),
        _q("6752.T", "2026-06-30", 4.0, 40.0),
        _q("6752.T", "2026-09-30", 5.0, 50.0),
    ]
    out = fd.ttm_from_quarters(smuggled, "6752.T")
    assert out["ttm_net_income"] == 412.0  # the truth is ~12; the gates cannot see the difference
    assert out["span_days"] == 364


def test_ttm_uses_the_preceding_quarter_for_an_exact_span():
    rows = [_q("COHR", "2025-06-30", 0.5, 5.0), *FOUR_CLEAN]
    out = fd.ttm_from_quarters(rows, "COHR")
    assert out["span_days"] == 365  # 2025-06-30 -> 2026-06-30 exactly
    assert out["ttm_net_income"] == 10.0  # the extra quarter is not summed in


def test_ttm_respects_as_of_and_ignores_other_tickers():
    rows = [_q("COHR", "2025-06-30", 0.5, 5.0), *FOUR_CLEAN, _q("LITE", "2026-09-30", 99.0, 999.0)]
    out = fd.ttm_from_quarters(rows, "COHR", as_of="2026-03-31")
    assert out["period_end"] == "2026-03-31"
    assert out["ttm_net_income"] == pytest.approx(6.5)
    assert fd.ttm_from_quarters(rows, "LITE") is None


def test_ttm_never_returns_a_partial_total():
    rows = [dict(r) for r in FOUR_CLEAN]
    rows[1]["net_income"] = None
    out = fd.ttm_from_quarters(rows, "COHR")
    assert out["ttm_net_income"] is None  # an incomplete sum is None, never three quarters' worth
    assert out["ttm_revenue"] == 100.0  # revenue is complete, so it still totals

    rows[1]["revenue"] = None
    assert fd.ttm_from_quarters(rows, "COHR") is None  # nothing left to total


# ------------------------------------------------------------- the annual fallback (basis last_fy)

# 6752.T as Yahoo actually returns it: four fiscal years ending 03-31, no quarterly statement.
FOUR_FY = [
    _q("6752.T", "2023-03-31", 265.0, 8378.0, "JPY"),
    _q("6752.T", "2024-03-31", 444.0, 8496.0, "JPY"),
    _q("6752.T", "2025-03-31", 366.0, 8458.0, "JPY"),
    _q("6752.T", "2026-03-31", 400.0, 8600.0, "JPY"),
]


def test_ttm_from_annual_returns_the_last_fiscal_year_in_the_ttm_shape():
    out = fd.ttm_from_annual(FOUR_FY, "6752.T")
    assert out == {
        "period_end": "2026-03-31",
        "ttm_net_income": 400.0,
        "ttm_revenue": 8600.0,
        "n_quarters": 4,
        "span_days": 365,
        "currency": "JPY",
        "basis": "last_fy",
    }
    # Same keys as the quarterly path, so publish and metrics need no special case - only `basis`
    # distinguishes them, and it is what tells a reader the number is a fiscal year.
    assert set(out) == set(fd.ttm_from_quarters(FOUR_CLEAN, "COHR"))
    assert fd.ttm_from_quarters(FOUR_CLEAN, "COHR")["basis"] == "ttm"


def test_ttm_from_annual_ignores_other_tickers_and_respects_as_of():
    rows = [*FOUR_FY, _q("IKA.L", "2026-04-30", 9.0, 90.0, "GBP")]
    assert fd.ttm_from_annual(rows, "IKA.L")["period_end"] == "2026-04-30"
    assert fd.ttm_from_annual(rows, "6752.T", "2025-06-30")["period_end"] == "2025-03-31"
    assert fd.ttm_from_annual(rows, "NOT.A.TICKER") is None


def test_ttm_from_annual_accepts_a_lone_year_and_a_series_with_a_hole():
    lone = fd.ttm_from_annual([FOUR_FY[-1]], "6752.T")
    assert lone["span_days"] == fd.FY_NOMINAL_SPAN  # nothing to measure against; nominal
    holed = fd.ttm_from_annual([FOUR_FY[0], FOUR_FY[-1]], "6752.T")  # 2023 then 2026
    assert holed["span_days"] == fd.FY_NOMINAL_SPAN
    assert holed["ttm_net_income"] == 400.0


def test_ttm_from_annual_rejects_rows_that_are_not_a_yearly_series():
    """A quarterly row in the annual table would otherwise be published as a whole year."""
    quarters = [
        _q("6752.T", "2026-03-31", 3.0, 30.0, "JPY"),
        _q("6752.T", "2026-06-30", 4.0, 40.0, "JPY"),
    ]
    assert fd.ttm_from_annual(quarters, "6752.T") is None


def test_ttm_from_annual_rejects_a_fiscal_year_that_has_gone_stale():
    assert fd.ttm_from_annual(FOUR_FY, "6752.T", "2026-09-16", max_age_days=550) is not None
    assert fd.ttm_from_annual(FOUR_FY, "6752.T", "2028-09-16", max_age_days=550) is None


def test_ttm_by_ticker_prefers_quarters_and_only_then_falls_back_to_the_year():
    both = [*FOUR_CLEAN, *[dict(r, ticker="6981.T") for r in FOUR_CLEAN]]
    annual = [*FOUR_FY, *[dict(r, ticker="6981.T") for r in FOUR_FY]]
    out = fd.ttm_by_ticker(both, annual, as_of="2026-09-16")

    assert out["6981.T"]["basis"] == "ttm"  # has both; the real TTM wins
    assert out["6981.T"]["ttm_net_income"] == 10.0
    assert out["6752.T"]["basis"] == "last_fy"  # annual only
    assert out["COHR"]["basis"] == "ttm"
    assert set(out) == {"COHR", "6981.T", "6752.T"}


def test_ttm_by_ticker_applies_the_age_gates():
    stale_q = [dict(r, ticker="DEAD") for r in FOUR_CLEAN]  # newest quarter 2026-06-30
    out = fd.ttm_by_ticker(stale_q, [], as_of="2028-01-01")
    assert out == {}  # stopped filing; no trailing PE rather than a 2026 one priced today


def test_load_ttm_reads_both_snapshot_tables(monkeypatch, tmp_path):
    """The wiring publish.load_all should use: one call, both tables, age gates on."""
    from pipelines.valuation import read
    from pipelines.valuation.schema import fundamentals_frame

    val = tmp_path / "valuation"
    quarterly, _ = fd.clean_rows(
        [{"period_end": r["period_end"], "net_income": r["net_income"], "revenue": r["revenue"],
          "currency": "USD", "derived": False} for r in FOUR_CLEAN],
        "COHR", "sec", "2026-09-16T12:00:00+00:00",
    )
    annual, _ = fd.clean_rows(
        [{"period_end": r["period_end"], "net_income": r["net_income"], "revenue": r["revenue"],
          "currency": "JPY", "derived": False} for r in FOUR_FY],
        "6752.T", "yahoo", "2026-09-16T12:00:00+00:00",
    )
    (val / "fundamentals").mkdir(parents=True)
    (val / "fundamentals_annual").mkdir(parents=True)
    fundamentals_frame(quarterly).write_parquet(val / "fundamentals" / "2026-09-16.parquet")
    fundamentals_frame(annual).write_parquet(val / "fundamentals_annual" / "2026-09-16.parquet")
    monkeypatch.setattr(read, "VAL_DIR", val)

    out = fd.load_ttm(as_of="2026-09-16")
    assert out["COHR"]["basis"] == "ttm"
    assert out["COHR"]["currency"] == "USD"
    assert out["6752.T"]["basis"] == "last_fy"  # the nine annual-only listings get a number at last
    assert out["6752.T"]["ttm_net_income"] == 400.0


# ------------------------------------------------------------------------- stamping and fan-out


def test_clean_rows_stamps_and_keeps_a_loss():
    rows = [
        {"period_end": "2025-03-31", "net_income": -5.0, "revenue": 100.0, "currency": "CNY", "derived": False},
        {"period_end": "nonsense", "net_income": 1.0, "revenue": 1.0, "currency": "CNY", "derived": True},
        {"period_end": "2025-09-30", "net_income": None, "revenue": None, "currency": "CNY", "derived": True},
    ]
    out, dropped = fd.clean_rows(rows, "300308.SZ", "eastmoney", "2026-09-16T12:00:00+00:00")
    assert [r["period_end"] for r in out] == ["2025-03-31"]
    assert out[0]["net_income"] == -5.0  # a loss is a legitimate quarter
    assert out[0]["ticker"] == "300308.SZ"
    assert out[0]["source"] == "eastmoney"
    assert out[0]["snapshot_ts"] == "2026-09-16T12:00:00+00:00"
    assert [d["reason"] for d in dropped] == ["unparseable period end"]
    assert [d["kind"] for d in dropped] == ["period"]


def test_clean_rows_voids_a_negative_revenue_but_keeps_the_net_income():
    """002074.SZ restated FY2019 revenue below its own nine-month figure, so the difference went
    negative. Dropping the whole row would cost the listing its TTM window - and its trailing PE,
    and its place in the dispersion stats - for four quarters, over a number trailing PE never uses.
    """
    rows = [{"period_end": "2025-06-30", "net_income": 5.0, "revenue": -20.0, "currency": "CNY", "derived": True}]
    out, dropped = fd.clean_rows(rows, "002074.SZ", "eastmoney", "2026-09-16T12:00:00+00:00")

    assert len(out) == 1
    assert out[0]["net_income"] == 5.0  # the quarter trailing PE needs survives
    assert out[0]["revenue"] is None  # the value the schema forbids does not
    assert dropped[0]["kind"] == "revenue"
    assert "voided" in dropped[0]["reason"]

    # A row with nothing left after voiding is still dropped rather than written empty.
    only_rev, _ = fd.clean_rows(
        [{"period_end": "2025-06-30", "net_income": None, "revenue": -20.0, "currency": "CNY"}],
        "002074.SZ", "eastmoney", "2026-09-16T12:00:00+00:00",
    )
    assert only_rev == []


def test_clean_rows_emits_exactly_the_columns_the_contract_names():
    """schema._frame fills missing keys with null, so a misspelt key here becomes an all-null
    column that pandera happily accepts - `currency` is nullable and `derived` is not in the
    schema at all. Renaming either one silently blanked every trailing PE on the page.
    """
    from pipelines.valuation.schema import FUNDAMENTALS_DTYPES

    out, _ = fd.clean_rows(
        [{"period_end": "2025-03-31", "net_income": 1.0, "revenue": 2.0, "currency": "CNY", "derived": True}],
        "300308.SZ", "eastmoney", "2026-09-16T12:00:00+00:00",
    )
    assert set(out[0]) == set(FUNDAMENTALS_DTYPES)
    assert out[0]["currency"] == "CNY"
    assert out[0]["derived"] is True


def test_recent_drops_separates_a_live_break_from_an_old_one():
    dropped = [
        {"ticker": "300308.SZ", "period_end": "2016-12-31", "kind": "period", "reason": "chain broken"},
        {"ticker": "002074.SZ", "period_end": "2026-06-30", "kind": "period", "reason": "chain broken"},
        {"ticker": "002074.SZ", "period_end": "2026-03-31", "kind": "revenue", "reason": "voided"},
    ]
    recent = fd._recent_drops(dropped, "2026-09-16")
    assert [d["ticker"] for d in recent] == ["002074.SZ"]  # the 2016 break is history
    assert all(d["kind"] == "period" for d in recent)  # a voided field is not a lost quarter


def test_source_of_routes_a_dual_listing_to_its_primary():
    assert fd.source_of(BY_TICKER["6869.HK"]) == "eastmoney"
    assert fd.source_of(BY_TICKER["3750.HK"]) == "eastmoney"
    assert fd.source_of(BY_TICKER["1211.HK"]) == "eastmoney"
    assert fd.source_of(BY_TICKER["3931.HK"]) == "yahoo"  # a genuinely HK-reporting listing
    assert fd.source_of(BY_TICKER["COHR"]) == "sec"


def test_dual_listing_is_fetched_once_and_copied(monkeypatch):
    """601869.SS and 6869.HK are the same filings; fetching twice would be waste and could disagree."""
    calls: list[str] = []

    def fake_collect(company, client, archive):
        calls.append(company.ticker)
        return (
            [
                {"period_end": "2026-03-31", "net_income": 1.0, "revenue": 10.0, "currency": "CNY", "derived": False},
                {"period_end": "2026-06-30", "net_income": 2.0, "revenue": 20.0, "currency": "CNY", "derived": True},
            ],
            [],
            {"cumulative_periods": 2},
        )

    monkeypatch.setattr(fd, "collect_eastmoney", fake_collect)
    monkeypatch.setattr(fd, "eastmoney_client", lambda: object())

    targets = [BY_TICKER["6869.HK"], BY_TICKER["601869.SS"]]
    result = fd.build(targets, "2026-09-16T12:00:00+00:00", "2026-09-16", "1200")

    assert calls == ["601869.SS"]  # fetched once, for the primary only
    assert result["failed"] == {}
    a = [r for r in result["rows"] if r["ticker"] == "601869.SS"]
    h = [r for r in result["rows"] if r["ticker"] == "6869.HK"]
    assert len(a) == len(h) == 2
    assert [(r["period_end"], r["net_income"], r["revenue"]) for r in a] == [
        (r["period_end"], r["net_income"], r["revenue"]) for r in h
    ]
    assert {r["source"] for r in h} == {"eastmoney"}  # not "yahoo", even though 6869.HK is an HK line


class _FakeYahoo:
    """Stands in for YahooFetcher: 6752.T as Yahoo really answers, no quarterly statement at all."""

    payloads = {
        "6752.T": {
            "quarterly_income_stmt": {},
            "income_stmt": {
                "Net Income": {"2025-03-31": 366.0, "2026-03-31": 400.0},
                "Total Revenue": {"2025-03-31": 8458.0, "2026-03-31": 8600.0},
            },
            "info": {"financialCurrency": "JPY"},
        },
        "6981.T": {
            "quarterly_income_stmt": {
                "Net Income": {"2025-12-31": 1.0, "2026-03-31": 2.0, "2026-06-30": 3.0},
                "Total Revenue": {"2025-12-31": 10.0, "2026-03-31": 20.0, "2026-06-30": 30.0},
            },
            "income_stmt": {"Net Income": {"2026-03-31": 9.0}, "Total Revenue": {"2026-03-31": 90.0}},
            "info": {"financialCurrency": "JPY"},
        },
    }

    def __init__(self, *a, **kw) -> None:
        self.calls = 0

    def payload(self, ticker: str) -> dict:
        self.calls += 1
        return {"ticker": ticker, **self.payloads[ticker]}


def test_build_keeps_yahoo_annual_rows_out_of_the_quarterly_rows(monkeypatch, tmp_path):
    """The module's biggest design decision: a fiscal year end is also a quarter end, so an annual
    row in the quarterly table is a quarter four times too large that nothing downstream can spot.
    """
    monkeypatch.setattr(fd, "YahooFetcher", _FakeYahoo)
    monkeypatch.setattr(fd, "RAW_DIR", tmp_path / "raw")

    targets = [BY_TICKER["6752.T"], BY_TICKER["6981.T"]]
    result = fd.build(targets, "2026-09-16T12:00:00+00:00", "2026-09-16", "1200")

    assert result["failed"] == {}
    quarterly = {(r["ticker"], r["period_end"]) for r in result["rows"]}
    annual = {(r["ticker"], r["period_end"]) for r in result["annual"]}
    assert ("6752.T", "2026-03-31") in annual
    assert ("6752.T", "2026-03-31") not in quarterly  # the fiscal year never enters the quarters
    assert not any(t == "6752.T" for t, _ in quarterly)  # Yahoo gives it no quarterly statement
    assert ("6981.T", "2026-03-31") in quarterly  # a real Q1, same date, different table
    assert ("6981.T", "2026-03-31") in annual
    assert {r["currency"] for r in result["rows"] + result["annual"]} == {"JPY"}
    assert {r["source"] for r in result["annual"]} == {"yahoo"}


def test_main_writes_the_two_tables_to_two_directories(monkeypatch, tmp_path):
    import polars as pl

    monkeypatch.setattr(fd, "YahooFetcher", _FakeYahoo)
    monkeypatch.setattr(fd, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(fd, "SNAP_DIR", tmp_path)

    assert fd.main(["--tickers", "6752.T,6981.T", "--source", "yahoo"]) == 0
    q = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    a = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals_annual"))
    assert sorted(q["ticker"].unique().to_list()) == ["6981.T"]
    assert sorted(a["ticker"].unique().to_list()) == ["6752.T", "6981.T"]
    # 6752.T's fiscal year is nowhere near the quarterly table, which is the whole point.
    assert q.filter(pl.col("ticker") == "6752.T").height == 0


def test_sec_lookup_failure_is_reported_as_itself_for_every_us_listing(monkeypatch, tmp_path):
    """The company_tickers file 403s when SEC_USER_AGENT carries no email. Leaving a half-built
    client behind made every later US listing report "not in the SEC company_tickers file" instead,
    so the run record blamed the pool config for an environment problem.
    """
    import pipelines.sec.ingest as ingest

    class _Boom:
        def __init__(self) -> None:
            pass

        def tickers(self) -> dict:
            raise RuntimeError("HTTPStatusError 403 for company_tickers.json")

    monkeypatch.setattr(ingest, "SecClient", _Boom)
    monkeypatch.setattr(fd, "RAW_DIR", tmp_path / "raw")

    targets = [BY_TICKER["COHR"], BY_TICKER["LITE"], BY_TICKER["AAOI"]]
    result = fd.build(targets, "2026-09-16T12:00:00+00:00", "2026-09-16", "1200")

    assert sorted(result["failed"]) == ["AAOI", "COHR", "LITE"]
    for ticker, why in result["failed"].items():
        assert "company_tickers lookup failed" in why, ticker
        assert "not in the SEC company_tickers file" not in why, ticker
        assert "403" in why, ticker


def test_one_failing_primary_does_not_lose_the_others(monkeypatch):
    def fake_collect(company, client, archive):
        if company.ticker == "300308.SZ":
            raise fd.SourceError("boom")
        return ([{"period_end": "2026-03-31", "net_income": 1.0, "revenue": 10.0, "currency": "CNY"}], [], {})

    monkeypatch.setattr(fd, "collect_eastmoney", fake_collect)
    monkeypatch.setattr(fd, "eastmoney_client", lambda: object())

    targets = [BY_TICKER["300308.SZ"], BY_TICKER["300502.SZ"]]
    result = fd.build(targets, "2026-09-16T12:00:00+00:00", "2026-09-16", "1200")
    assert list(result["failed"]) == ["300308.SZ"]
    assert {r["ticker"] for r in result["rows"]} == {"300502.SZ"}


def test_select_filters_by_ticker_and_by_owning_source():
    assert [c.ticker for c in fd.select("COHR,300308.SZ", "")] == ["300308.SZ", "COHR"]
    em = fd.select("", "eastmoney")
    assert "6869.HK" in {c.ticker for c in em}  # routed by its primary, not by its own market
    assert "3931.HK" not in {c.ticker for c in em}
    assert {c.ticker for c in fd.select("COHR", "eastmoney")} == set()
    assert fd.select("NOT.A.TICKER", "") == []


def test_raw_archive_never_overwrites_an_existing_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "RAW_DIR", tmp_path)
    first = fd._raw_path("eastmoney_fin", "2026-09-16", "300308.SZ", "1200")
    assert first.name == "300308.SZ.json.gz"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"evidence")

    second = fd._raw_path("eastmoney_fin", "2026-09-16", "300308.SZ", "1830")
    assert second.name == "300308.SZ__1830.json.gz"  # the earlier payload stays where it is
    assert first.read_bytes() == b"evidence"


def _patch_em(monkeypatch, tmp_path, fail: set[str] = frozenset(), net_income: float | None = None):
    def fake_collect(company, client, archive):
        if company.ticker in fail:
            raise fd.SourceError("boom")
        return (
            [
                {"period_end": pe, "net_income": net_income if net_income is not None else ni,
                 "revenue": rev, "currency": "CNY", "derived": derived}
                for pe, ni, rev, derived in (
                    ("2025-09-30", 1.0, 10.0, True),
                    ("2025-12-31", 2.0, 20.0, True),
                    ("2026-03-31", 3.0, 30.0, False),
                    ("2026-06-30", 4.0, 40.0, True),
                )
            ],
            [],
            {},
        )

    monkeypatch.setattr(fd, "collect_eastmoney", fake_collect)
    monkeypatch.setattr(fd, "eastmoney_client", lambda: object())
    monkeypatch.setattr(fd, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(fd, "RAW_DIR", tmp_path / "raw")


def test_main_writes_the_snapshot_and_exits_zero(monkeypatch, tmp_path):
    import polars as pl

    _patch_em(monkeypatch, tmp_path)
    rc = fd.main(["--tickers", "601869.SS,6869.HK", "--source", "eastmoney"])
    assert rc == 0

    written = sorted((tmp_path / "valuation" / "fundamentals").glob("*.parquet"))
    assert len(written) == 1
    df = pl.read_parquet(written[0])
    assert sorted(df["ticker"].unique().to_list()) == ["601869.SS", "6869.HK"]
    assert df.height == 8
    assert set(df["source"].unique().to_list()) == {"eastmoney"}
    assert (tmp_path / "valuation" / "fundamentals_runs.jsonl").exists()


def test_main_still_writes_the_survivors_then_exits_one(monkeypatch, tmp_path):
    import polars as pl

    _patch_em(monkeypatch, tmp_path, fail={"300308.SZ"})
    rc = fd.main(["--tickers", "300308.SZ,300502.SZ", "--source", "eastmoney"])
    assert rc == 1  # a failure is reported...

    df = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    assert df["ticker"].unique().to_list() == ["300502.SZ"]  # ...but only after the rest is on disk


def test_main_writes_no_nulls_in_the_columns_the_page_needs(monkeypatch, tmp_path):
    import polars as pl

    _patch_em(monkeypatch, tmp_path)
    assert fd.main(["--tickers", "601869.SS", "--source", "eastmoney"]) == 0

    df = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    for col in ("ticker", "period_end", "net_income", "revenue", "currency", "source", "derived"):
        assert df[col].null_count() == 0, col  # an all-null currency column blanks every trailing PE
    assert df["currency"].unique().to_list() == ["CNY"]


def test_a_partial_run_merges_into_the_days_snapshot_instead_of_replacing_it(monkeypatch, tmp_path):
    """--tickers and --source runs are the normal way to use this module. Writing only the part
    they fetched threw away everything an earlier run that day had put on disk: the working tree
    once held a 16-listing SEC-only file where a 68-listing run had been an hour before.
    """
    import polars as pl

    _patch_em(monkeypatch, tmp_path)
    assert fd.main(["--tickers", "300308.SZ,300502.SZ", "--source", "eastmoney"]) == 0
    first = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    assert sorted(first["ticker"].unique().to_list()) == ["300308.SZ", "300502.SZ"]

    assert fd.main(["--tickers", "601869.SS", "--source", "eastmoney"]) == 0
    merged = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    assert sorted(merged["ticker"].unique().to_list()) == ["300308.SZ", "300502.SZ", "601869.SS"]
    assert merged.height == first.height + 4


def test_a_rerun_of_the_same_listing_updates_its_rows_rather_than_duplicating_them(monkeypatch, tmp_path):
    import polars as pl

    _patch_em(monkeypatch, tmp_path)
    assert fd.main(["--tickers", "300308.SZ", "--source", "eastmoney"]) == 0
    before = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))

    _patch_em(monkeypatch, tmp_path, net_income=99.0)
    assert fd.main(["--tickers", "300308.SZ", "--source", "eastmoney"]) == 0
    after = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))

    assert after.height == before.height  # same key, replaced not appended
    assert after["net_income"].to_list() == [99.0] * before.height  # the fresh run wins


def test_main_leaves_a_good_snapshot_alone_when_a_run_fetches_nothing(monkeypatch, tmp_path):
    """An empty frame passes FUNDAMENTALS_SCHEMA, so nothing but this guard stops a rate-limited
    rerun from replacing the day's table with zero rows - and read.load() only reads the newest
    date, so the previous good file would sit there being ignored.
    """
    import polars as pl

    _patch_em(monkeypatch, tmp_path)
    assert fd.main(["--tickers", "300308.SZ,300502.SZ", "--source", "eastmoney"]) == 0
    good = _only(tmp_path / "valuation" / "fundamentals")
    before = pl.read_parquet(good)
    assert before.height == 8

    _patch_em(monkeypatch, tmp_path, fail={"300308.SZ", "300502.SZ"})
    assert fd.main(["--tickers", "300308.SZ,300502.SZ", "--source", "eastmoney"]) == 1

    after = pl.read_parquet(_only(tmp_path / "valuation" / "fundamentals"))
    assert after.to_dicts() == before.to_dicts()  # untouched, not emptied

    record = json.loads((tmp_path / "valuation" / "fundamentals_runs.jsonl").read_text().splitlines()[-1])
    assert record["rows"] == 0
    assert record["snapshot"] is None  # the run is still recorded, saying plainly it wrote nothing
    assert [c for c in record["checks"] if c["name"] == "Snapshot written"][0]["status"] == "fail"


def test_main_reports_a_run_that_fetched_nothing_at_all(monkeypatch, tmp_path):
    _patch_em(monkeypatch, tmp_path, fail={"300308.SZ"})
    assert fd.main(["--tickers", "300308.SZ", "--source", "eastmoney"]) == 1
    assert not (tmp_path / "valuation" / "fundamentals").exists()  # no empty part left behind


def test_affected_names_the_listings_a_failed_primary_takes_down():
    targets = [BY_TICKER["6869.HK"], BY_TICKER["601869.SS"], BY_TICKER["300308.SZ"]]
    assert fd._affected(targets, {"601869.SS": "boom"}) == ["601869.SS", "6869.HK"]


def test_rows_validate_against_the_contract_schema():
    from pipelines.valuation.schema import FUNDAMENTALS_DTYPES, FUNDAMENTALS_SCHEMA, fundamentals_frame

    rows, _ = fd.clean_rows(
        fd.difference_cumulative(fd.parse_eastmoney(EM_PAYLOAD)),
        "300308.SZ",
        "eastmoney",
        "2026-09-16T12:00:00+00:00",
    )
    df = fundamentals_frame(rows)
    FUNDAMENTALS_SCHEMA.validate(df)
    assert df.height == 6
    assert df.columns == list(FUNDAMENTALS_DTYPES)
