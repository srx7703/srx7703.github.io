"""Tests for the curated layer's rules.

Each test here corresponds to a specific way this table can publish something false. They are written as the
error, not as the happy path, because the happy path is not what a validator is for.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.common.curation import CurationError
from pipelines.surgical import reference as ref

TODAY = date(2026, 9, 21)


def unit_row(**kw) -> dict:
    row = {
        "maker": "ISRG", "product": "da Vinci", "metric": "installed_base", "basis": "installed_base",
        "period": "2026-Q2", "value": 11710.0, "unit": "systems", "geography": "global",
        "placement_model": "mixed", "tier": "T1", "source_kind": "company",
        "source_name": "Intuitive Q2 2026 earnings release", "source_url": "https://www.sec.gov/x",
        "publish_date": "2026-07-22", "last_checked": "2026-09-21",
        "quote": "da Vinci installed base of 11,710 systems",
        "caveat": "Placed is not sold: Intuitive places systems under lease and usage terms and does not split them.",
    }
    row.update(kw)
    return row


def tender_row(**kw) -> dict:
    row = {
        "notice_id": "ccgp-2026-09-09-changzhi", "hospital": "长治医学院附属和平医院", "province": "山西",
        "brand": "精锋", "model": "MP2000", "maker": "2675.HK", "quantity": 1.0,
        "unit_price_cny": 10980000.0, "total_price_cny": 10980000.0, "award_date": "2026-09-09",
        "contract_kind": "purchase", "complete_system": True, "source_name": "中国政府采购网中标公告",
        "source_url": "https://www.ccgp.gov.cn/x", "publish_date": "2026-09-09", "last_checked": "2026-09-21",
        "caveat": "One award at one hospital; coverage of this portal is a floor, not a national count.",
    }
    row.update(kw)
    return row


def problems(kind: str, row: dict) -> list[str]:
    from pipelines.common.curation import row_problems
    return row_problems(ref.SPEC, kind, row)


# --- the generic rules ------------------------------------------------------------


def test_a_clean_row_has_no_problems():
    assert problems("units", unit_row()) == []
    assert problems("tenders", tender_row()) == []


def test_a_row_without_a_caveat_is_rejected():
    assert "no caveat" in problems("units", unit_row(caveat=""))


def test_a_row_whose_source_is_not_a_link_is_rejected():
    assert any("not a link" in p for p in problems("units", unit_row(source_url="Intuitive 10-Q")))


def test_a_working_note_anywhere_public_rejects_the_row():
    assert any("working note" in p for p in problems("units", unit_row(caveat="TODO check this before publishing")))


def test_a_working_note_in_curator_notes_is_fine():
    assert problems("units", unit_row(curator_notes="TODO: re-verify against the 10-K")) == []


def test_a_malformed_date_is_rejected():
    assert any("publish_date" in p for p in problems("units", unit_row(publish_date="July 2026")))


# --- units: the basis rule --------------------------------------------------------


def test_a_unit_figure_without_a_basis_is_rejected():
    """Four companies publish a number called 'units' while counting four different things."""
    assert "no basis" in problems("units", unit_row(basis=""))


def test_an_invented_basis_is_rejected_rather_than_bucketed():
    assert any("not one of" in p for p in problems("units", unit_row(basis="systems_shipped")))


def test_a_unit_figure_without_a_source_kind_is_rejected():
    """Whose number it is decides whether it may enter a ratio, so it cannot be left to a default."""
    assert "no source_kind" in problems("units", unit_row(source_kind=""))


def test_an_invented_source_kind_is_rejected():
    assert any("not one of" in p for p in problems("units", unit_row(source_kind="estimate")))


def test_a_unit_figure_without_a_geography_is_rejected():
    """The Procept trap: its quarterly figures are US-only and would otherwise be read as global."""
    assert any("no geography" in p for p in problems("units", unit_row(geography="")))


def test_a_zero_unit_count_is_a_parse_failure_not_a_count():
    assert any("positive" in p for p in problems("units", unit_row(value=0)))


def test_a_us_only_procept_row_is_accepted_when_it_says_so():
    assert problems("units", unit_row(
        maker="PRCT", product="HYDROS", metric="installed_base", basis="installed_base",
        value=816.0, geography="us", tier="T1",
        caveat="United States only; the release gives no global figure and a circulating one is not in it.",
    )) == []


# --- tenders: the lease trap ------------------------------------------------------


def test_a_lease_award_may_not_claim_a_brand():
    """The winner of a leasing award is the leasing company, not the manufacturer."""
    got = problems("tenders", tender_row(contract_kind="lease", brand="达芬奇",
                                         caveat="This is a three-year lease fee, not a machine price."))
    assert any("leasing company" in p for p in got)


def test_a_lease_award_must_say_in_its_caveat_that_the_price_is_a_lease_fee():
    got = problems("tenders", tender_row(contract_kind="lease", brand="",
                                         caveat="One award at one hospital."))
    assert any("lease fee" in p for p in got)


def test_a_properly_marked_lease_row_is_accepted():
    assert problems("tenders", tender_row(
        contract_kind="lease", brand="", model="", maker="",
        caveat="The winner is a leasing company and the figure is a three-year lease fee, not a machine price.",
    )) == []


def test_a_maintenance_contract_may_not_carry_a_machine_quantity():
    got = problems("tenders", tender_row(contract_kind="maintenance", quantity=1.0,
                                         caveat="A service contract, not a shipment."))
    assert any("must not carry a machine quantity" in p for p in got)


def test_an_unknown_contract_kind_is_rejected():
    assert any("not one of" in p for p in problems("tenders", tender_row(contract_kind="donation")))


def test_a_zero_price_is_rejected_but_an_undisclosed_one_is_allowed():
    assert any("positive" in p for p in problems("tenders", tender_row(unit_price_cny=0)))
    assert problems("tenders", tender_row(unit_price_cny=None)) == []


# --- quota ------------------------------------------------------------------------


def test_newly_added_cannot_exceed_the_total_permitted():
    row = {"province": "广东", "plan": "14th", "permitted_total": 60.0, "newly_added": 99.0,
           "source_name": "国卫财务发〔2023〕18号", "source_url": "https://www.nhc.gov.cn/x",
           "publish_date": "2023-06-29", "last_checked": "2026-09-21",
           "caveat": "A licence ceiling with no brand, model or price field; it can never state market share."}
    assert any("exceeds" in p for p in problems("quota", row))


# --- whole-file behaviour ---------------------------------------------------------


def test_the_shipped_files_all_parse_and_pass():
    bad = [c for c in ref.validate_all(TODAY) if c["status"] == "fail"]
    assert not bad, bad


def test_every_shipped_file_documents_itself():
    for kind in ref.KINDS:
        data = ref.load(kind)
        assert len(data.get("note", "")) > 200, f"{kind}: the file has to say what it is for"
        assert data.get("row_template"), f"{kind}: a curator needs a row to copy"


def test_one_bad_row_fails_the_whole_file_and_names_every_fault(tmp_path, monkeypatch):
    """A half-published table is worse than a missing one, and one error message should fix the file in one pass."""
    import json

    from pipelines.common import curation

    monkeypatch.setattr(curation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ref.SPEC.__class__, "directory",
                        property(lambda self: tmp_path / "reference" / self.track))
    d = tmp_path / "reference" / "surgical"
    d.mkdir(parents=True)
    (d / "units.json").write_text(json.dumps({
        "kind": "units", "rows": [unit_row(), unit_row(maker="PRCT", basis="", geography="", caveat="")],
    }), encoding="utf-8")
    with pytest.raises(CurationError) as exc:
        ref.rows("units", TODAY)
    msg = str(exc.value)
    assert "no basis" in msg and "no geography" in msg and "no caveat" in msg


def test_a_row_unchecked_for_too_long_is_flagged_stale_not_dropped():
    from pipelines.common.curation import is_stale
    assert is_stale("2026-01-01", ref.SPEC, TODAY) is True
    assert is_stale("2026-09-01", ref.SPEC, TODAY) is False
    assert is_stale(None, ref.SPEC, TODAY) is True


def test_an_award_that_is_not_a_complete_system_must_say_what_it_bought():
    """One award at CNY 1.09m bought a teaching arm, and unguarded it made the panel's spread look 3x wider."""
    got = problems("tenders", tender_row(complete_system=False, caveat="Cheap."))
    assert any("what it actually bought" in p for p in got)


def test_a_properly_explained_partial_award_is_accepted():
    assert problems("tenders", tender_row(
        complete_system=False, unit_price_cny=1_094_200,
        caveat="This buys one research and teaching robotic arm, not a complete clinical platform, so the "
               "figure is not what a hospital pays for a surgical robot.")) == []


def test_complete_system_is_required():
    row = tender_row()
    del row["complete_system"]
    assert "no complete_system" in problems("tenders", row)
