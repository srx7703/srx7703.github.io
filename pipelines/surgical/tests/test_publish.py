"""Tests for the facts assembly.

Three invariants, each of which would otherwise fail silently and produce a page that looks right and is wrong:
bases must never be summed, companies that disclose nothing must never be drawn at zero, and a ratio must never
be computed across mismatched geographies.
"""

from __future__ import annotations

from pipelines.surgical import config as cfg
from pipelines.surgical import publish


def unit(maker, metric, value, period, *, basis=None, geography="global", tier="T1"):
    return {
        "maker": maker, "product": "x", "metric": metric, "basis": basis or metric, "period": period,
        "value": float(value), "unit": "systems", "geography": geography, "tier": tier,
        "placement_model": "mixed", "source_name": "s", "source_url": "https://example.org/x",
        "publish_date": "2026-07-22", "last_checked": "2026-09-21", "quote": "", "caveat": "c",
    }


# --- bases are never merged -------------------------------------------------------


def test_units_are_grouped_by_basis_and_never_merged():
    rows = [
        unit("ISRG", "installed_base", 11710, "2026-Q2"),
        unit("private:surgerii", "production", 23, "2025", basis="production", tier="T3"),
        unit("2675.HK", "installed_or_delivered", 158, "2026-H1", basis="installed_or_delivered", tier="T2"),
    ]
    by_basis = publish.units_by_basis(rows)
    assert set(by_basis) == {"installed_base", "production", "installed_or_delivered"}
    assert all(len(v) == 1 for v in by_basis.values())


def test_installed_base_ignores_other_bases_entirely():
    """Production is not an installed base. Adding it would inflate the denominator with unsold machines."""
    rows = [
        unit("ISRG", "installed_base", 11710, "2026-Q2"),
        unit("private:surgerii", "production", 9999, "2025", basis="production", tier="T3"),
    ]
    got = publish.latest_installed_base(rows)
    assert got["total_disclosed"] == 11710.0
    assert [r["maker"] for r in got["rows"]] == ["ISRG"]


def test_the_latest_period_wins_per_maker():
    rows = [unit("ISRG", "installed_base", 10488, "2025-Q2"), unit("ISRG", "installed_base", 11710, "2026-Q2")]
    got = publish.latest_installed_base(rows)
    assert len(got["rows"]) == 1 and got["rows"][0]["value"] == 11710.0


# --- the share is of the disclosed pool, never of a market ------------------------


def test_the_share_is_labelled_as_a_share_of_the_disclosed_pool_and_counts_the_silent():
    rows = [unit("ISRG", "installed_base", 750, "2026-Q2"), unit("PRCT", "installed_base", 250, "2026-Q2")]
    got = publish.latest_installed_base(rows)
    assert got["rows"][0]["share_of_disclosed"] == 0.75
    assert "share_of_disclosed" in got["rows"][0] and "share" not in got["rows"][0]
    # 21 makers in the pool, 2 disclosing, so 19 are silent and the page has to say so.
    assert got["n_silent"] == len(cfg.MAKERS) - 2


def test_mixed_geographies_are_flagged_because_the_pool_then_has_no_denominator():
    rows = [unit("ISRG", "installed_base", 11710, "2026-Q2", geography="global"),
            unit("PRCT", "installed_base", 816, "2026-Q2", geography="us")]
    got = publish.latest_installed_base(rows)
    assert got["mixed_geography"] is True
    assert got["geographies"] == ["global", "us"]


# --- utilisation ------------------------------------------------------------------


def test_utilisation_needs_both_figures_on_the_same_year_and_geography():
    rows = [
        unit("ISRG", "installed_base", 11106, "2025", geography="global"),
        unit("ISRG", "procedures", 3153000, "2025", basis="procedures", geography="global"),
    ]
    got = publish.utilisation(rows)
    assert len(got) == 1
    assert got[0]["procedures_per_system"] == round(3153000 / 11106, 1)


def test_utilisation_refuses_to_mix_a_global_procedure_count_with_a_us_installed_base():
    """Procept's installed base is US-only. Dividing a worldwide procedure count by it would invent a number."""
    rows = [
        unit("PRCT", "installed_base", 816, "2026", geography="us"),
        unit("PRCT", "procedures", 50000, "2026", basis="procedures", geography="global"),
    ]
    assert publish.utilisation(rows) == []


def test_a_zero_installed_base_does_not_divide():
    rows = [unit("X", "installed_base", 0, "2025"), unit("X", "procedures", 10, "2025", basis="procedures")]
    assert publish.utilisation(rows) == []


# --- the disclosure scorecard -----------------------------------------------------


def test_silent_companies_are_present_but_not_chartable():
    """A company that publishes nothing must appear in the table and never on a chart."""
    card = publish.disclosure_scorecard([unit("ISRG", "installed_base", 11710, "2026-Q2")])
    by_maker = {r["maker"]: r for r in card["rows"]}
    assert by_maker["MDT"]["n_metrics"] == 0
    assert by_maker["MDT"]["chartable"] is False
    assert by_maker["ISRG"]["chartable"] is True
    assert card["n_makers"] == len(cfg.MAKERS)
    assert card["n_silent"] == len(cfg.MAKERS) - 1


def test_the_scorecard_covers_every_maker_even_with_no_data_at_all():
    card = publish.disclosure_scorecard([])
    assert card["n_makers"] == len(cfg.MAKERS)
    assert card["n_disclosing"] == 0
    assert all(r["latest_period"] is None for r in card["rows"])


def test_a_suspended_company_carries_its_status_into_the_scorecard():
    card = publish.disclosure_scorecard([])
    medbot = next(r for r in card["rows"] if r["maker"] == "2252.HK")
    assert medbot["status"] == "suspended"


# --- tenders ----------------------------------------------------------------------


def tender(kind, price, brand="精锋"):
    return {"notice_id": f"n{price}", "hospital": "h", "brand": brand, "contract_kind": kind,
            "unit_price_cny": price, "award_date": "2026-09-09", "quantity": 1.0,
            "source_url": "https://example.org", "caveat": "c"}


def test_only_purchases_enter_the_price_statistics():
    """A lease fee is not a machine price and a service contract is not either."""
    rows = [tender("purchase", 10_980_000), tender("purchase", 19_800_000, brand="达芬奇"),
            tender("lease", 3_500_100, brand=""), tender("maintenance", 800_000, brand="")]
    got = publish.tender_panel(rows)
    assert got["n_total"] == 4 and got["n_purchases"] == 2
    assert got["n_leases"] == 1 and got["n_maintenance"] == 1
    assert got["overall"]["n"] == 2
    assert got["overall"]["max"] == 19_800_000
    assert set(got["by_maker"]) == {"精锋", "达芬奇"}


def test_a_purchase_with_no_disclosed_price_is_kept_but_not_priced():
    rows = [tender("purchase", 10_980_000), {**tender("purchase", 1), "unit_price_cny": None}]
    got = publish.tender_panel(rows)
    assert got["n_total"] == 2 and got["overall"]["n"] == 1


def test_an_empty_panel_has_no_statistics_rather_than_zeros():
    got = publish.tender_panel([])
    assert got["overall"] is None and got["by_maker"] == {}


# --- quota ------------------------------------------------------------------------


def test_the_quota_reconciles_against_the_published_national_total():
    rows = [{"province": "A", "plan": "14th", "permitted_total": 800.0, "newly_added": 500.0},
            {"province": "B", "plan": "14th", "permitted_total": 19.0, "newly_added": 59.0}]
    got = publish.quota_view(rows)
    assert got["national_permitted"] == 819.0
    assert got["matches_published_total"] is True
    assert got["successor"] is None  # no 15th Five-Year Plan exists


def test_a_quota_that_does_not_reconcile_says_so():
    got = publish.quota_view([{"province": "A", "plan": "14th", "permitted_total": 10.0, "newly_added": 5.0}])
    assert got["matches_published_total"] is False


def test_a_missing_quota_is_missing_rather_than_zero():
    assert publish.quota_view([])["status"] == "missing"


# --- regressions found by real data on the first load -----------------------------
# Every test below corresponds to a wrong number this module actually produced before the fix.


def u2(maker, product, metric, value, period, *, geography="global", source_kind="company", tier="T1"):
    return {
        "maker": maker, "product": product, "metric": metric, "basis": metric, "period": period,
        "value": float(value), "unit": "systems", "geography": geography, "tier": tier,
        "source_kind": source_kind, "placement_model": "", "source_name": "s",
        "source_url": "https://example.org/x", "publish_date": "2026-01-01",
        "last_checked": "2026-09-21", "quote": "", "caveat": "c",
    }


def test_utilisation_never_crosses_products():
    """Ion procedures over the da Vinci installed base gave 2.6 procedures per system per year."""
    rows = [
        u2("ISRG", "da Vinci", "installed_base", 9902, "2024-Q4"),
        u2("ISRG", "Ion", "installed_base", 805, "2024-Q4"),
        u2("ISRG", "Ion", "procedures", 95500, "2024"),
    ]
    got = {(r["product"], r["year"]): r["procedures_per_system"] for r in publish.utilisation(rows)}
    assert got == {("Ion", "2024"): round(95500 / 805, 1)}


def test_utilisation_refuses_a_quarterly_procedure_count():
    """A quarter's procedures over a year-end base understates the rate by about four."""
    rows = [u2("ISRG", "Ion", "installed_base", 805, "2024-Q4"),
            u2("ISRG", "Ion", "procedures", 25000, "2024-Q3")]
    assert publish.utilisation(rows) == []


def test_utilisation_uses_the_year_end_base_not_the_first_quarter():
    rows = [u2("ISRG", "da Vinci", "installed_base", 8887, "2024-Q1"),
            u2("ISRG", "da Vinci", "installed_base", 9902, "2024-Q4"),
            u2("ISRG", "da Vinci", "procedures", 2683000, "2024")]
    assert publish.utilisation(rows)[0]["installed_base"] == 9902.0


def test_a_third_party_estimate_never_enters_a_ratio():
    """A consultant's installed base divided into a company's procedure count is a number nobody stands behind."""
    rows = [u2("ISRG", "da Vinci", "installed_base", 9629, "2024", source_kind="third_party"),
            u2("ISRG", "da Vinci", "procedures", 2683000, "2024")]
    assert publish.utilisation(publish.company_reported(rows)) == []


def test_a_third_party_estimate_never_enters_the_installed_base_total():
    rows = [u2("ISRG", "da Vinci", "installed_base", 11710, "2026-Q2"),
            u2("ISRG", "da Vinci", "installed_base", 9629, "2024", source_kind="third_party")]
    got = publish.latest_installed_base(publish.company_reported(rows))
    assert got["total_disclosed"] == 11710.0


# --- the disagreement, which is a finding rather than a defect --------------------


def test_a_stock_is_compared_across_differently_labelled_periods():
    """Intuitive writes 2024-Q4 and the consultant writes 2024; both mean end-2024 for an installed base."""
    rows = [u2("ISRG", "da Vinci", "installed_base", 9902, "2024-Q4"),
            u2("ISRG", "da Vinci", "installed_base", 9629, "2024", source_kind="third_party")]
    got = publish.source_disagreements(rows)
    assert len(got) == 1
    assert got[0]["company_value"] == 9902.0 and got[0]["third_party_value"] == 9629.0
    assert got[0]["gap"] == -273.0
    assert round(got[0]["gap_pct"], 4) == round(-273 / 9902, 4)


def test_the_company_side_of_a_disagreement_is_the_year_end_reading():
    rows = [u2("ISRG", "da Vinci", "installed_base", 8887, "2024-Q1"),
            u2("ISRG", "da Vinci", "installed_base", 9902, "2024-Q4"),
            u2("ISRG", "da Vinci", "installed_base", 9629, "2024", source_kind="third_party")]
    assert publish.source_disagreements(rows)[0]["company_value"] == 9902.0


def test_a_flow_is_not_collapsed_across_periods():
    """A year of procedures and a quarter of procedures are different quantities, never a disagreement."""
    rows = [u2("ISRG", "da Vinci", "procedures", 2683000, "2024"),
            u2("ISRG", "da Vinci", "procedures", 670000, "2024-Q3", source_kind="third_party")]
    assert publish.source_disagreements(rows) == []


def test_agreeing_sources_are_not_reported_as_a_disagreement():
    rows = [u2("ISRG", "da Vinci", "installed_base", 9902, "2024-Q4"),
            u2("ISRG", "da Vinci", "installed_base", 9902, "2024", source_kind="third_party")]
    assert publish.source_disagreements(rows) == []


def test_stock_and_flow_are_declared_and_disjoint():
    assert not set(cfg.STOCK_METRICS) & set(cfg.FLOW_METRICS)
    assert set(cfg.STOCK_METRICS) | set(cfg.FLOW_METRICS) == set(cfg.UNIT_BASIS)
    assert cfg.period_instant("installed_base", "2024-Q4") == "2024"
    assert cfg.period_instant("procedures", "2024-Q4") == "2024-Q4"


def test_prices_group_by_maker_not_by_the_brand_string():
    """Procurement officers write one manufacturer four ways; grouping on raw text splits its price history."""
    rows = [{**tender("purchase", 11_000_000, brand="图迈"), "maker": "2252.HK", "notice_id": "a"},
            {**tender("purchase", 13_660_000, brand="微创图迈"), "maker": "2252.HK", "notice_id": "b"},
            {**tender("purchase", 10_980_000, brand="精锋"), "maker": "2675.HK", "notice_id": "c"}]
    got = publish.tender_panel(rows)
    assert set(got["by_maker"]) == {"MicroPort MedBot", "Edge Medical"}
    assert got["by_maker"]["MicroPort MedBot"]["n"] == 2


def test_an_award_with_no_attributable_maker_is_counted_not_hidden():
    rows = [{**tender("purchase", 9_000_000, brand=""), "maker": "", "notice_id": "z"}]
    got = publish.tender_panel(rows)
    assert got["unattributed"] == 1


# --- what the adversarial re-read of every source turned up -----------------------


def test_the_procedure_definition_break_is_carried_into_the_facts():
    """Two different populations either side of 2025-Q3; a reader must not be able to miss it."""
    assert cfg.PROCEDURE_DEFINITION_BREAK == "2025-Q3"
    assert "da Vinci and Ion" in cfg.PROCEDURE_BREAK_NOTE


def test_placements_are_never_derivable_from_the_installed_base_delta():
    """In 2026-Q1 the base grew 289 while 431 systems were placed; the gap is undisclosed retirements."""
    rows = [u2("ISRG", "da Vinci", "installed_base", 11106, "2025-Q4"),
            u2("ISRG", "da Vinci", "installed_base", 11395, "2026-Q1"),
            u2("ISRG", "da Vinci", "placements", 431, "2026-Q1")]
    by_basis = publish.units_by_basis(rows)
    delta = 11395 - 11106
    placed = by_basis["placements"][0]["value"]
    assert delta != placed
    assert "never" in cfg.PLACEMENTS_NOT_DELTA_NOTE.lower()


def test_the_price_panel_carries_the_configuration_warning():
    """No notice discloses arm or console count, so the spread is not pricing power."""
    got = publish.tender_panel([tender("purchase", 10_980_000)])
    assert "configuration" in got["configuration_caveat"] or "arm count" in got["configuration_caveat"]
    assert "pricing power" in got["configuration_caveat"]
