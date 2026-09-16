"""Tests for basis A (the computed pool) and the publication bar on basis B (the quoted figures).

The share module had no tests, and three of the acceptance findings lived in it: a segment figure that
was never consulted for the companies that most needed one, a dual-listed issuer counted twice among
the exclusions, and quoted rows the curator had marked "re-read the page before publishing" shipping to
a public page. Every fixture here is a hand-built pool, so the assertions are arithmetic a reader could
check with a calculator.
"""

from __future__ import annotations

import pytest

from pipelines.valuation import share as mod
from pipelines.valuation.config import Company
from pipelines.valuation.share import (
    BASIS_SEGMENT,
    BASIS_TOTAL,
    cited_share,
    computed_share,
    load_reference,
    quote_block,
    segment_revenue_index,
    shipments_and_capacity,
    withheld_quotes,
)

RATES = {"USD": 1.0, "CNY": 7.0, "HKD": 7.8}


def co(ticker: str, name: str, market: str = "us", **kw) -> Company:
    kw.setdefault("share_basis", "total")
    return Company(ticker, name, "optical", "high", market, 12, **kw)


def ttm(revenue: float | None, currency: str = "USD", period_end: str = "2026-06-30") -> dict:
    return {"ttm_revenue": revenue, "currency": currency, "period_end": period_end, "ttm_net_income": 1.0}


def ref(**kw) -> dict:
    base = {"track": "optical", "segment_revenue": [], "cited_share": [], "shipments": [], "capacity": []}
    base.update(kw)
    return base


def quote(entity: str, **kw) -> dict:
    row = {"entity": entity, "metric": "share", "value": "1", "unit": "%", "period": "2025",
           "source_name": "LightCounting", "source_url": "https://example.invalid/x",
           "publish_date": "2026-03-01", "confidence": "high"}
    row.update(kw)
    return row


def segment(ticker: str, value: float, **kw) -> dict:
    row = {"ticker": ticker, "value": value, "currency": "USD", "period": "FY2026",
           "basis": "10-K segment note", "source_name": "Form 10-K",
           "source_url": "https://example.invalid/10k", "publish_date": "2026-08-20"}
    row.update(kw)
    return row


# --- basis A: what the numerator is ----------------------------------------------------------------


def test_a_disclosed_segment_figure_beats_the_companys_consolidated_revenue():
    """The finding in one test: Coherent entered the optical pool on group revenue it does not earn here."""
    pool = [co("COHR", "Coherent"), co("AAOI", "Applied Optoelectronics")]
    ttms = {"COHR": ttm(7_118_181_000.0), "AAOI": ttm(1_000_000_000.0)}
    reference = ref(segment_revenue=[segment("COHR", 5_274_600_000.0)])

    got = computed_share(pool, ttms, reference, RATES)

    cohr = next(m for m in got["members"] if m["ticker"] == "COHR")
    assert cohr["revenue_usd"] == 5_274_600_000.0  # the segment, not the 7.118bn group figure
    assert cohr["basis"] == BASIS_SEGMENT
    assert cohr["note"] == "10-K segment note"
    assert cohr["period_end"] == "FY2026"
    assert got["basis_counts"] == {BASIS_SEGMENT: 1, BASIS_TOTAL: 1}


def test_a_specialist_with_no_segment_row_still_enters_on_total_revenue():
    got = computed_share([co("AAOI", "Applied Optoelectronics")], {"AAOI": ttm(1e9)}, ref(), RATES)

    member = got["members"][0]
    assert member["basis"] == BASIS_TOTAL
    assert member["note"] == "the company's whole revenue competes in this market"
    assert member["period_end"] == "2026-06-30"


def test_a_diversified_company_without_a_recorded_figure_is_excluded_with_a_true_reason():
    """`segment disclosure not checked yet` was a note to ourselves published as an explanation."""
    pool = [
        co("AAOI", "Applied Optoelectronics"),
        co("CSCO", "Cisco", share_basis="segment"),
        co("AVGO", "Broadcom", share_basis="segment", segment_line="no"),
        co("CIEN", "Ciena", share_basis="segment", segment_line="yes"),
        co("MRVL", "Marvell", share_basis="segment", segment_line="bundled"),
    ]
    got = computed_share(pool, {"AAOI": ttm(1e9)}, ref(), RATES)

    reasons = {e["ticker"]: e["reason"] for e in got["excluded"]}
    assert "not checked yet" not in " ".join(reasons.values())
    assert reasons["CSCO"] == "no sourced revenue figure for this business is recorded for this listing"
    assert reasons["AVGO"] == "does not publish revenue for this business separately"
    assert "no sourced figure has been recorded here yet" in reasons["CIEN"]
    assert "bundled into a wider segment" in reasons["MRVL"]
    assert got["excluded_counts"] == {"other_layer": 0, "no_disclosure": 2, "not_recorded": 2, "no_data": 0}


def test_a_supplier_one_layer_down_is_excluded_with_its_own_note():
    pool = [co("AAOI", "Applied Optoelectronics"),
            co("CRDO", "Credo", share_basis="none", share_note="sells DSPs, one layer down from modules")]

    got = computed_share(pool, {"AAOI": ttm(1e9)}, ref(), RATES)

    out = next(e for e in got["excluded"] if e["ticker"] == "CRDO")
    assert out["reason"] == "sells DSPs, one layer down from modules"
    assert out["reason_class"] == "other_layer"
    assert got["excluded_counts"]["other_layer"] == 1


@pytest.mark.parametrize("revenue,expected", [
    (None, "no trailing revenue on file"),
    (0.0, "reports no revenue over the trailing twelve months"),
])
def test_a_company_with_no_revenue_is_excluded_with_a_reason_rather_than_charted_at_zero(revenue, expected):
    pool = [co("AAOI", "Applied Optoelectronics"), co("QS", "QuantumScape")]

    got = computed_share(pool, {"AAOI": ttm(1e9), "QS": ttm(revenue)}, ref(), RATES)

    assert [m["ticker"] for m in got["members"]] == ["AAOI"]
    out = next(e for e in got["excluded"] if e["ticker"] == "QS")
    assert out["reason"] == expected and out["reason_class"] == "no_data"


def test_a_company_whose_currency_has_no_rate_is_excluded_with_a_reason_not_dropped():
    pool = [co("AAOI", "Applied Optoelectronics"),
            co("6981.T", "Murata", market="jp")]

    got = computed_share(pool, {"AAOI": ttm(1e9), "6981.T": ttm(1e12, "JPY")}, ref(), RATES)

    out = next(e for e in got["excluded"] if e["ticker"] == "6981.T")
    assert out["reason"] == "no exchange rate for JPY"
    assert out["reason_class"] == "no_data"
    assert len(got["members"]) + len(got["excluded"]) == 2  # nobody disappeared


def test_the_latest_period_wins_when_a_company_has_two_segment_rows():
    reference = ref(segment_revenue=[segment("COHR", 4e9, period="FY2025"), segment("COHR", 5e9, period="FY2026")])

    assert segment_revenue_index(reference)["COHR"]["value"] == 5e9


def test_a_segment_row_with_no_value_is_ignored_rather_than_crashing():
    reference = ref(segment_revenue=[segment("COHR", None), segment("LITE", 1e9)])

    assert list(segment_revenue_index(reference)) == ["LITE"]


# --- basis A: one issuer, one row, in or out -------------------------------------------------------


def test_a_dual_listed_issuer_is_excluded_once_not_once_per_listing():
    """`n_excluded` is printed in the lede and in Data quality; YOFC was inflating both."""
    pool = [
        co("AAOI", "Applied Optoelectronics"),
        Company("601869.SS", "YOFC", "optical", "partial", "cn", 12, share_basis="segment"),
        Company("6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
                fundamentals_from="601869.SS", share_basis="segment"),
    ]

    got = computed_share(pool, {"AAOI": ttm(1e9)}, ref(), RATES)

    assert [e["ticker"] for e in got["excluded"]] == ["601869.SS"]
    assert len(got["excluded"]) == 1


def test_a_dual_listed_issuer_enters_the_pool_once_on_its_primary_line():
    pool = [
        Company("601869.SS", "YOFC", "optical", "partial", "cn", 12, share_basis="total"),
        Company("6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
                fundamentals_from="601869.SS", share_basis="total"),
        co("AAOI", "Applied Optoelectronics"),
    ]

    got = computed_share(pool, {"601869.SS": ttm(7e9, "CNY"), "AAOI": ttm(1e9)}, ref(), RATES)

    assert sorted(m["ticker"] for m in got["members"]) == ["601869.SS", "AAOI"]
    assert got["n_members"] == 2


def test_the_primary_line_represents_the_issuer_even_when_the_secondary_is_listed_first():
    pool = [
        Company("6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
                fundamentals_from="601869.SS", share_basis="total"),
        Company("601869.SS", "YOFC", "optical", "partial", "cn", 12, share_basis="total"),
    ]

    got = computed_share(pool, {"601869.SS": ttm(7e9, "CNY")}, ref(), RATES)

    assert [m["ticker"] for m in got["members"]] == ["601869.SS"]


def test_a_secondary_listing_represents_the_issuer_when_the_primary_is_not_in_the_pool():
    pool = [Company("6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
                    fundamentals_from="601869.SS", share_basis="total")]

    got = computed_share(pool, {"601869.SS": ttm(7e9, "CNY")}, ref(), RATES)

    assert [m["ticker"] for m in got["members"]] == ["6869.HK"]


# --- basis A: the arithmetic -----------------------------------------------------------------------


def test_shares_sum_to_one_and_the_concentration_measures_agree_with_them():
    pool = [co(t, t) for t in ("A", "B", "C", "D", "E", "F")]
    ttms = {t: ttm(v) for t, v in zip("ABCDEF", (50e9, 20e9, 10e9, 10e9, 5e9, 5e9), strict=True)}

    got = computed_share(pool, ttms, ref(), RATES)

    assert [m["ticker"] for m in got["members"]] == ["A", "B", "C", "D", "E", "F"]  # sorted by revenue
    assert sum(m["share"] for m in got["members"]) == pytest.approx(1.0)
    assert got["pool_revenue_usd"] == 100e9
    assert got["top3_share"] == pytest.approx(0.80)
    assert got["top5_share"] == pytest.approx(0.95)
    assert got["top3_share"] <= got["top5_share"]
    assert got["hhi"] == pytest.approx(50**2 + 20**2 + 10**2 + 10**2 + 5**2 + 5**2)


def test_revenue_is_converted_into_one_currency_before_the_pool_is_summed():
    pool = [co("CN", "A-share", market="cn"), co("US", "US listing")]

    got = computed_share(pool, {"CN": ttm(7e9, "CNY"), "US": ttm(1e9)}, ref(), RATES)

    assert {m["ticker"]: m["share"] for m in got["members"]} == {"CN": pytest.approx(0.5), "US": pytest.approx(0.5)}
    assert next(m for m in got["members"] if m["ticker"] == "CN")["revenue_native"] == 7e9


def test_an_empty_pool_reports_nothing_rather_than_dividing_by_zero():
    got = computed_share([co("CSCO", "Cisco", share_basis="segment")], {}, ref(), RATES)

    assert got["n_members"] == 0 and got["pool_revenue_usd"] == 0
    assert got["top3_share"] is None and got["hhi"] is None


# --- basis B: the publication bar ------------------------------------------------------------------


def test_a_low_confidence_row_is_held_back_with_the_reason():
    """Two rows saying "re-read the page before publishing" were live on the page with no gate."""
    reference = ref(cited_share=[quote("LightCounting Top 10"), quote("Dell'Oro transport", confidence="low")])

    published = cited_share(reference)
    held = withheld_quotes(reference)

    assert [r["entity"] for r in published] == ["LightCounting Top 10"]
    assert held == [{"kind": "cited_share", "entity": "Dell'Oro transport", "metric": "share",
                     "reason": "confidence recorded as low"}]


def test_a_medium_confidence_row_still_publishes_and_carries_its_confidence():
    reference = ref(cited_share=[quote("C114 rendering of the Top 10", confidence="medium")])

    published = cited_share(reference)

    assert [r["confidence"] for r in published] == ["medium"]
    assert withheld_quotes(reference) == []


@pytest.mark.parametrize("field,reason", [
    ("source_url", "no URL"),
    ("source_name", "no publisher"),
    ("publish_date", "no publication date"),
])
def test_a_row_missing_its_provenance_is_held_back(field, reason):
    row = quote("Anonymous figure", **{field: ""})

    assert quote_block(row) == reason
    assert cited_share(ref(cited_share=[row])) == []


def test_a_row_with_no_confidence_recorded_is_held_back():
    row = quote("Unmarked figure")
    row.pop("confidence")

    assert quote_block(row) == "confidence recorded as unrecorded"


def test_the_gate_covers_shipments_and_capacity_not_only_cited_share():
    """The published check reported 2 quoted figures where the battery page shows eight."""
    reference = ref(
        cited_share=[quote("CATL share")],
        shipments=[quote("NIO 150 kWh pack"), quote("unverified shipment", confidence="low")],
        capacity=[quote("PowerCo licence", start_year="2027")],
    )

    got = shipments_and_capacity(reference)

    assert [r["entity"] for r in got["shipments"]] == ["NIO 150 kWh pack"]
    assert [r["entity"] for r in got["capacity"]] == ["PowerCo licence"]
    assert [(h["kind"], h["entity"]) for h in withheld_quotes(reference)] == [("shipments", "unverified shipment")]


def test_cited_rows_come_back_newest_first_and_otherwise_unchanged():
    reference = ref(cited_share=[quote("Older", period="2024"), quote("Newer", period="2025")])

    assert [r["entity"] for r in cited_share(reference)] == ["Newer", "Older"]
    assert cited_share(reference)[0] == quote("Newer", period="2025")


# --- reading the reference file --------------------------------------------------------------------


def test_a_missing_reference_file_leaves_basis_a_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REFERENCE_DIR", tmp_path)

    reference = load_reference("optical")

    assert reference["cited_share"] == [] and reference["segment_revenue"] == []
    assert computed_share([co("AAOI", "AAOI")], {"AAOI": ttm(1e9)}, reference, RATES)["n_members"] == 1


def test_a_reference_file_missing_a_block_gets_an_empty_one(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REFERENCE_DIR", tmp_path)
    (tmp_path / "share_optical.json").write_text('{"track": "optical", "updated": "2026-09-16"}', encoding="utf-8")

    reference = load_reference("optical")

    assert reference["shipments"] == [] and reference["capacity"] == [] and reference["cited_share"] == []


def test_curator_notes_never_reach_a_published_row():
    """`data/facts/*.json` is served verbatim from the site, so a note to the next curator is public.

    The acceptance review found working notes ("re-read the page before publishing", "the adversarial
    check found the two had been fused into one data point") served at
    site/public/data/facts/valuation_optical.json. The reader-facing half of that thought is `caveat`,
    which the page renders; the rest stays behind.
    """
    from pipelines.valuation.share import INTERNAL_FIELDS, cited_share, shipments_and_capacity

    row = {
        "entity": "Innolight", "metric": "rank", "value": "1", "period": "2025",
        "source_name": "LightCounting", "source_url": "https://example.invalid/x",
        "publish_date": "2026-05-28", "confidence": "high",
        "caveat": "transceiver sales only",
        "curator_notes": "re-read the page before publishing",
        "publish_date_raw": "2026-05 (month precision only)",
    }
    ref = {"track": "optical", "cited_share": [row], "shipments": [dict(row)], "capacity": [dict(row)]}

    published = cited_share(ref) + shipments_and_capacity(ref)["shipments"] + shipments_and_capacity(ref)["capacity"]
    assert published, "the fixture row was held back, so this test proves nothing"
    for r in published:
        assert not (INTERNAL_FIELDS & set(r)), f"internal field published: {sorted(INTERNAL_FIELDS & set(r))}"
        assert r["caveat"] == "transceiver sales only", "the reader-facing caveat was stripped too"
        assert r["source_url"], "attribution must survive the strip"
