"""Private-company ledger, reported-unconfirmed table, derived intervals and take rates."""

from __future__ import annotations

from datetime import date

import pandera.errors
import polars as pl
import pytest

from pipelines.payments import ledger as L
from pipelines.payments import reference

TODAY = date(2026, 10, 4)


def _row(**over) -> dict:
    row = {"company": "Acme", "metric": "revenue", "period": "2026-Q2", "value": 100.0, "unit": "USD",
           "currency": "USD", "qualifier": "=", "metric_kind": "filed_revenue", "tag": "V", "chartable": True,
           "company_confirmed": True, "as_of_date": "2026-06-30", "source_url": "https://example.com/a",
           "segment": "A", "stale": False, "status": "current"}
    row.update(over)
    return row


PAIR = L.Pair("Acme", "revenue", "volume", "revenue_per_volume", "gross_take_rate", "Acme group")


def _pair(num: dict, den: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    return L.take_rates([num, den], (PAIR,))


# --- take-rate pairing -----------------------------------------------------------------------


def test_take_rate_same_period_currency_scope():
    ok, refused = _pair(_row(value=2.0e9), _row(metric="volume", value=1.0e11, metric_kind="filed_volume"))
    assert refused.is_empty()
    r = ok.row(0, named=True)
    assert r["take_rate_pct"] == pytest.approx(2.0)   # 2B / 100B x 100
    assert (r["qualifier"], r["tag"], r["chartable"]) == ("=", "C", True)


@pytest.mark.parametrize(("den_over", "reason"), [
    ({"period": "2026-Q1"}, "no volume for this period"),
    ({"currency": "EUR"}, "currency differs"),
    ({"as_of_date": "2026-03-31"}, "as-of date differs"),
    ({"chartable": False, "tag": "S"}, "not chartable"),
    ({"metric_kind": "reported_talks", "chartable": False}, "not chartable"),
])
def test_take_rate_refusals(den_over, reason):
    ok, refused = _pair(_row(), _row(metric="volume", value=1e4, **den_over))
    assert ok.is_empty()
    assert any(reason in r for r in refused["reason"])


def test_annualised_never_divided_by_period_figure():
    pair = L.Pair("Acme", "annualized_revenue", "volume", "x", "gross_take_rate", "s")
    ok, refused = L.take_rates([_row(metric="annualized_revenue"), _row(metric="volume", value=1e4)], (pair,))
    assert ok.is_empty() and "basis differs" in refused["reason"][0]


def test_take_rate_qualifiers():
    # ">1B" revenue over "=100B" volume -> ">1%"
    ok, _ = _pair(_row(value=1e9, qualifier=">"), _row(metric="volume", value=1e11))
    assert (ok["take_rate_pct"][0], ok["qualifier"][0]) == (pytest.approx(1.0), ">")
    # "=1B" over ">100B" -> "<1%"
    ok, _ = _pair(_row(value=1e9), _row(metric="volume", value=1e11, qualifier=">"))
    assert ok["qualifier"][0] == "<"
    # "~" anywhere -> "~"
    ok, _ = _pair(_row(value=1e9), _row(metric="volume", value=1e11, qualifier="~"))
    assert ok["qualifier"][0] == "~"
    # ">" over ">" bounds the ratio from both sides: refused, not charted as a guess
    ok, refused = _pair(_row(value=1e9, qualifier=">"), _row(metric="volume", value=1e11, qualifier=">"))
    assert ok.is_empty() and "opposite sides" in refused["reason"][0]


def test_ambiguous_rows_refused():
    ok, refused = L.take_rates([_row(), _row(value=200.0), _row(metric="volume", value=1e4)], (PAIR,))
    assert ok.is_empty() and "ambiguous" in refused["reason"][0]


@pytest.fixture(scope="module")
def kpi() -> list[dict]:
    return [r for k in ("kpi_disclosures_acceptance", "kpi_disclosures_other") for r in reference.rows(k, TODAY)]


def test_every_pinned_pair_produces_rates(kpi):
    ok, refused = L.take_rates(kpi)
    have = set(zip(ok["company"], ok["ratio"], strict=True))
    assert {(p.company, p.ratio) for p in L.TAKE_RATE_PAIRS} <= have
    # BILL's annual revenue has no annual TPV row, so it is refused, not mixed with quarters
    assert ("BILL", "revenue_per_tpv", "FY2026") in set(zip(refused["company"], refused["ratio"], refused["period"],
                                                             strict=True))


def test_golden_take_rates(kpi):
    ok, _ = L.take_rates(kpi)

    def get(company, ratio, period):
        hit = ok.filter((pl.col("company") == company) & (pl.col("ratio") == ratio) & (pl.col("period") == period))
        assert hit.height == 1
        return hit.row(0, named=True)

    # PayPal 2Q'26: net revenues / TPV; TPV = 486,448M (8-K Ex.99.1)
    pp = get("PayPal", "net_revenues_per_tpv", "2026-Q2")
    assert pp["denominator_value"] == 486_448_000_000
    assert pp["take_rate_pct"] == pytest.approx(pp["numerator_value"] / 486_448_000_000 * 100)
    assert pp["currency"] == "USD" and pp["ratio_kind"] == "gross_take_rate"
    # Shift4 volume is stated "approximately": the take rate inherits "~"
    s4 = get("Shift4", "net_revenue_per_volume", "2026-Q2")
    assert s4["denominator_qualifier"] == "~" and s4["qualifier"] == "~"
    # Adyen stays in EUR
    assert get("Adyen", "net_revenue_per_processed_volume", "H1-2026")["currency"] == "EUR"


def test_take_rate_never_from_unverified_row(kpi):
    ok, refused = L.take_rates(kpi)
    # Affirm FQ4'26 GMV was seen only in a search extract (tag S)
    assert ok.filter((pl.col("company") == "Affirm") & (pl.col("period") == "FY2026-Q4")).is_empty()
    assert refused.filter((pl.col("company") == "Affirm") & (pl.col("period") == "FY2026-Q4"))["reason"].str.contains(
        "not chartable").all()


# --- private ledger --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built():
    tables, checks = L.build(TODAY)
    assert not [c for c in checks if c["status"] == "fail"], checks
    return tables


def test_timeline_has_only_chartable_rows(built):
    tl = built["ledger_timeline"]
    assert tl["chartable"].all() and set(tl["tag"]) <= {"V", "C"}
    assert not tl["metric_kind"].is_in(["reported_talks", "third_party_estimate"]).any()


def test_implied_intervals(built):
    tl = built["ledger_timeline"]
    ramp = tl.filter((pl.col("company") == "Ramp") & (pl.col("metric") == "annualized_revenue")
                     & (pl.col("period") == "2026-06")).row(0, named=True)
    # "more than $1 billion" -> a lower bound only
    assert (ramp["qualifier"], ramp["lower"], ramp["upper"], ramp["estimate"]) == (">", 1e9, None, None)
    stripe = tl.filter((pl.col("company") == "Stripe") & (pl.col("metric") == "total_volume")
                       & (pl.col("period") == "FY2025")).row(0, named=True)
    assert stripe["lower"] == stripe["upper"] == 1.9e12
    bvnk_like = L.timeline([_row(company="X", metric="deal", value=1.8e9, qualifier="<")], [])
    assert bvnk_like.select("lower", "upper").row(0) == (None, 1.8e9)


def test_events_join_without_double_counting(built):
    tl = built["ledger_timeline"]
    ramp_44 = tl.filter((pl.col("company") == "Ramp") & (pl.col("value") == 44e9))
    assert ramp_44.height == 1 and ramp_44["source"][0] == "private_metrics"
    # Airwallex's May 2025 round is only in events.json, so it comes from there
    aw = tl.filter((pl.col("company") == "Airwallex") & (pl.col("date") == "2025-05-21"))
    assert aw.height == 1 and aw["source"][0] == "events" and aw["value"][0] == 6.2e9
    # public companies' events stay out of the private ledger
    assert "Global Payments" not in set(tl["company"])


def test_latest_current_value(built):
    lt = built["ledger_latest"]
    v = lt.filter((pl.col("company") == "Ramp") & (pl.col("metric") == "valuation")).row(0, named=True)
    assert v["value"] == 44e9 and v["date"] == "2026-06-04"  # not the superseded rounds, not the 60B talk
    assert (lt["status"] == "current").all()
    assert lt.select(["company", "metric"]).is_unique().all()


def test_reported_unconfirmed_table(built):
    rep = built["ledger_reported"]
    talk = rep.filter((pl.col("company") == "Ramp") & (pl.col("metric") == "valuation"))
    assert talk["value"][0] == 60e9 and talk["reason"][0] == "never_charted_kind" and talk["qualifier"][0] == "~"
    brex = rep.filter((pl.col("company") == "Brex") & (pl.col("metric") == "annualized_revenue"))
    assert brex["value"][0] == 700e6
    spihl = rep.filter(pl.col("metric") == "spihl_revenue")
    assert set(spihl["reason"]) == {"curator_not_chartable"}
    assert rep.filter(pl.col("tag") == "S")["reason"].is_in(["never_charted_kind", "unverified_tag"]).all()


def test_timeline_schema_rejects_reported_talks(built):
    tl = built["ledger_timeline"]
    bad = tl.head(1).with_columns(pl.lit("reported_talks").alias("metric_kind"))
    with pytest.raises(pandera.errors.SchemaError):
        L.TIMELINE_SCHEMA.validate(bad)


def test_golden_private_intervals(built):
    iv = built["private_intervals"]

    def get(company, metric, period=None):
        hit = iv.filter((pl.col("company") == company) & (pl.col("metric") == metric))
        if period:
            hit = hit.filter(pl.col("period") == period)
        assert hit.height == 1, hit
        return hit.row(0, named=True)

    # Q6 step 2: $815M loans x 365 / 45 days = $6.61B; x 365 / 20 days = $14.87B
    spend = get("Brex", "implied_annual_card_spend")
    assert spend["lower"] == pytest.approx(815e6 * 365 / 45)
    assert spend["upper"] == pytest.approx(815e6 * 365 / 20)
    # Q6 step 3: x 1.28% = $84.6M to $190.4M ("$85-190M" in section H)
    ic = get("Brex", "implied_net_interchange")
    assert ic["lower"] == pytest.approx(815e6 * 365 / 45 * 0.0128)
    assert ic["upper"] == pytest.approx(815e6 * 365 / 20 * 0.0128)
    assert round(ic["lower"] / 1e6) == 85 and round(ic["upper"] / 1e6) == 190
    assert "20-45 days" in ic["assumption"]
    # Stripe: $1.9T x [0.30%, 0.45%] = $5.7B to $8.55B
    st = get("Stripe", "implied_net_revenue")
    assert (st["lower"], st["upper"]) == (pytest.approx(5.7e9), pytest.approx(8.55e9))
    # Ramp 2026-06: ">$1B" / "$200B" = ">0.5%"
    rp = get("Ramp", "take_rate:revenue_per_purchase_volume", "2026-06")
    assert (rp["lower"], rp["upper"], rp["qualifier"]) == (pytest.approx(0.5), None, ">")
    # Airwallex 2026-03: $1.3B / $287B = 0.4530%
    assert get("Airwallex", "take_rate:revenue_per_transaction_volume", "2026-03")["estimate"] == pytest.approx(
        1.3e9 / 287e9 * 100, abs=1e-6)


def test_private_refusals(built):
    ref = built["private_take_rates_refused"]
    got = {(c, p): r for c, p, r in zip(ref["company"], ref["period"], ref["reason"], strict=True)}
    # Ramp Nov 2025: ">$1B" over ">$100B" has no bound -> refused
    assert "opposite sides" in got[("Ramp", "2025-11")]
    # Checkout.com's annualised net revenue (2026-09) and FY2025 volume never share a period
    assert ("Checkout.com", "2026-09") in got


def test_ledger_isolates_a_broken_file(monkeypatch):
    real = reference.rows

    def flaky(kind, today=None):
        if kind == "events":
            raise ValueError("bad events file")
        return real(kind, today)

    monkeypatch.setattr(reference, "rows", flaky)
    tables, checks = L.build(TODAY)
    assert not tables["ledger_timeline"].is_empty()
    assert not tables["take_rates"].is_empty()
    assert any(c["name"] == "Reference events" and c["status"] == "fail" for c in checks)


def test_write(tmp_path, built):
    paths = L.write(built, tmp_path)
    assert {p.stem for p in paths} == set(L.TABLES)
