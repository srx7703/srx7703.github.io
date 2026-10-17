"""The listing table: classification for every ring, measures only where a snapshot exists, reasons where it cannot."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.cyber import frozen as F
from pipelines.cyber import metrics as M
from pipelines.cyber.pool import POOL

AS_OF = date(2026, 10, 16)


@pytest.fixture(scope="module")
def rows():
    return M.listing_rows(F.load_baseline(), AS_OF)


def test_one_row_per_pool_listing_in_pool_order(rows) -> None:
    assert [r["ticker"] for r in rows] == [e.ticker for e in POOL]


def test_ring_a_is_priced_and_the_others_are_not(rows) -> None:
    assert all(r["priced"] for r in rows if r["ring"] == "A")
    assert not any(r["priced"] for r in rows if r["ring"] != "A")
    assert all(r.get("ev_ntm") is None for r in rows if r["ring"] != "A")


def test_a_missing_multiple_says_why(rows) -> None:
    by = {r["ticker"]: r for r in rows}
    assert by["CNS.L"]["ev_ntm"] is None and by["CNS.L"]["ev_ntm_reason"] == "currency_mismatch"
    assert by["CRWD"]["ev_ntm"] == pytest.approx(32.8, abs=0.1) and by["CRWD"]["ev_ntm_reason"] is None
    reasons = {r["ev_ntm_reason"] for r in rows if r["ring"] == "A" and r["ev_ntm"] is None}
    assert reasons <= {"currency_mismatch", "no_enterprise_value", "no_revenue_consensus"}


def test_regression_membership_matches_the_registered_population(rows) -> None:
    got = sorted(r["ticker"] for r in rows if r.get("in_regression_population"))
    assert got == sorted(F.q1_population(F.load_baseline()))


def test_upside_dispersion_and_thin_coverage(rows) -> None:
    by = {r["ticker"]: r for r in rows}
    crwd = by["CRWD"]
    assert crwd["upside"] == pytest.approx(crwd["target_mean"] / crwd["price"] - 1)
    assert crwd["dispersion"] == pytest.approx(0.78, abs=0.01) and crwd["thin"] is False
    assert by["688168.SS"]["thin"] is True, "no analysts means thin"


def test_china_rows_carry_east_money_ratings_and_others_do_not(rows) -> None:
    by = {r["ticker"]: r for r in rows}
    assert by["002439.SZ"]["em_ratings_6m"]["orgs"] == 4 and by["CRWD"]["em_ratings_6m"] is None
    assert {r["market_group"] for r in rows} == {"us", "other_overseas", "china"}


def test_load_runs_and_latest_on_the_committed_baseline() -> None:
    frame = M.load_runs()
    got = M.latest(frame)
    assert got is not None and got[0] == date(2026, 10, 17) and got[2].height == 73
    assert M.latest(frame.head(10)) is None, "ten priced rows are not a snapshot"


# --- the enterprise value in dollars --------------------------------------------------------------------------------
#: A fake rate table, units of a currency per dollar: dollars, euros, pounds (a pence quote is read as pounds), yen, yuan.
RATES = {"USD": 1.0, "EUR": 0.8, "GBP": 0.5, "JPY": 150.0, "CNY": 7.5}


def usd(ev, currency, rates=RATES):
    return M.enterprise_value_usd({"enterprise_value": ev, "currency": currency}, rates)


def test_enterprise_value_is_converted_at_the_rate_of_its_quote_currency() -> None:
    assert usd(1.0e9, "USD") == pytest.approx(1.0e9)
    assert usd(8.0e8, "EUR") == pytest.approx(1.0e9)
    assert usd(1.5e11, "JPY") == pytest.approx(1.0e9)
    assert usd(7.5e9, "CNY") == pytest.approx(1.0e9)
    assert usd(5.0e8, "GBP") == pytest.approx(1.0e9)


def test_a_pence_quote_is_read_as_pounds_and_not_divided_again() -> None:
    """Yahoo states the enterprise value in the major unit of the quote currency: 5e8 on a GBp quote is 500 million
    pounds. Treating it as pence would give a hundredth of the dollars."""
    assert usd(5.0e8, "GBp") == pytest.approx(1.0e9) == usd(5.0e8, "GBP")


def test_a_currency_without_a_rate_gets_no_dollar_value() -> None:
    for currency in ("SEK", "AUD", "TWD", "gbp", "XXX"):
        assert usd(1.0e9, currency) is None, currency
    assert usd(1.0e9, None) is None and usd(1.0e9, "") is None


def test_without_a_rate_table_nothing_is_converted_not_even_dollars() -> None:
    assert usd(1.0e9, "USD", None) is None and usd(1.0e9, "USD", {}) is None and usd(1.0e9, "EUR", {}) is None


def test_the_dollar_is_its_own_rate_once_a_table_exists() -> None:
    assert usd(1.0e9, "USD", {"EUR": 0.8}) == pytest.approx(1.0e9)
    assert usd(8.0e8, "EUR", {"EUR": 0.8}) == pytest.approx(1.0e9)


def test_no_enterprise_value_no_dollar_value_and_the_sign_is_kept() -> None:
    assert usd(None, "USD") is None
    assert usd(-8.0e8, "EUR") == pytest.approx(-1.0e9), "net cash above the market value: negative stays negative"
    assert usd(0.0, "EUR") == 0.0


def test_listing_rows_carry_the_dollar_value_for_the_currencies_the_table_knows() -> None:
    frame = F.load_baseline()
    by_ticker = {r["ticker"]: r for r in frame.iter_rows(named=True)}
    rows = {r["ticker"]: r for r in M.listing_rows(frame, AS_OF, RATES)}
    assert rows["CRWD"]["enterprise_value_usd"] == pytest.approx(by_ticker["CRWD"]["enterprise_value"])
    assert rows["NCC.L"]["currency"] == "GBp"
    assert rows["NCC.L"]["enterprise_value_usd"] == pytest.approx(by_ticker["NCC.L"]["enterprise_value"] / 0.5)
    assert rows["4704.T"]["enterprise_value_usd"] == pytest.approx(by_ticker["4704.T"]["enterprise_value"] / 150.0)
    known = {"USD", "EUR", "GBp", "JPY", "CNY"}
    want = sum(1 for r in by_ticker.values() if r["currency"] in known)
    assert sum(1 for r in rows.values() if r.get("enterprise_value_usd") is not None) == want == 68
    assert rows["YUBICO.ST"]["currency"] == "SEK" and rows["YUBICO.ST"]["enterprise_value_usd"] is None
    assert rows["SEN.AX"]["enterprise_value_usd"] is None
    assert all(r.get("enterprise_value_usd") is None for r in rows.values() if r["ring"] != "A"), "no snapshot"


def test_listing_rows_without_rates_have_the_column_and_it_is_empty() -> None:
    for rows in (M.listing_rows(F.load_baseline(), AS_OF), M.listing_rows(F.load_baseline(), AS_OF, {})):
        assert all("enterprise_value_usd" in r for r in rows if r["priced"])
        assert all(r.get("enterprise_value_usd") is None for r in rows)
    assert all(r.get("enterprise_value_usd") is None for r in M.listing_rows(None, AS_OF, RATES))


def test_rates_change_only_the_dollar_value() -> None:
    frame = F.load_baseline()
    with_rates = M.listing_rows(frame, AS_OF, RATES)
    without = M.listing_rows(frame, AS_OF)
    for a, b in zip(with_rates, without, strict=True):
        assert {k: v for k, v in a.items() if k != "enterprise_value_usd"} == {
            k: v for k, v in b.items() if k != "enterprise_value_usd"
        }
    assert any(a.get("enterprise_value_usd") is not None for a in with_rates)
