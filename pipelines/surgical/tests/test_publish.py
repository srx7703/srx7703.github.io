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
    assert set(got["by_brand"]) == {"精锋", "达芬奇"}


def test_a_purchase_with_no_disclosed_price_is_kept_but_not_priced():
    rows = [tender("purchase", 10_980_000), {**tender("purchase", 1), "unit_price_cny": None}]
    got = publish.tender_panel(rows)
    assert got["n_total"] == 2 and got["overall"]["n"] == 1


def test_an_empty_panel_has_no_statistics_rather_than_zeros():
    got = publish.tender_panel([])
    assert got["overall"] is None and got["by_brand"] == {}


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
