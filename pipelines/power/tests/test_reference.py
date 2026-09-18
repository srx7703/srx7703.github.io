"""The hygiene rules for the curated power layers, one test per rule.

Every fixture here is inline: these tests are about what the loader refuses, so they must not depend
on what happens to be in `data/reference/power/` today, and they must not touch the network. The last
few tests do read the files that ship with the repo, to assert the skeletons are loadable and empty.

`pipelines/power/publish.py` copies whatever `rows()` returns into `data/facts/`, which the site
serves, so a rule that leaks here leaks to the public page.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from pipelines.power import reference as ref
from pipelines.power.config import REFERENCE_STALE_DAYS

TODAY = date(2026, 9, 18)


def deal(**over) -> dict:
    """A deal row with nothing wrong with it. Each test breaks exactly one thing."""
    row = {
        "deal_id": "msft-constellation-tmi",
        "path": "C1",
        "buyer": "Example Cloud",
        "seller": "Example Power",
        "technology": "Nuclear",
        "mw": 835.0,
        "term_years": 20.0,
        "state": "PA",
        "announced_date": "2026-06-01",
        "stage": "announced",
        "company_confirmed": True,
        "source_name": "Example Power press release",
        "source_url": "https://example.com/press/1",
        "publish_date": "2026-06-01",
        "last_checked": "2026-09-10",
        "caveat": "The capacity is the plant's rated output, not a quantity delivered to one building.",
    }
    row.update(over)
    return row


def write(tmp_path, monkeypatch, kind: str, rows: list[dict], updated: str = "2026-09-18") -> None:
    monkeypatch.setattr(ref, "REFERENCE_DIR", tmp_path)
    (tmp_path / f"{kind}.json").write_text(
        json.dumps({"kind": kind, "updated": updated, "note": "fixture", "rows": rows}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------------------------
# Rule 1 — a link and the date behind it
# ---------------------------------------------------------------------------------------------


def test_a_sourced_and_dated_row_passes() -> None:
    assert ref.sourcing_problems(deal()) == []


@pytest.mark.parametrize(
    "over",
    [
        {"source_url": ""},
        {"source_url": "example.com/press/1"},  # a reader cannot open this
        {"source_url": None},
        {"source_url": 12},
    ],
)
def test_a_row_without_a_link_is_rejected(over: dict) -> None:
    assert any("source_url" in p for p in ref.sourcing_problems(deal(**over)))


@pytest.mark.parametrize("over", [{"publish_date": ""}, {"publish_date": None}])
def test_a_row_without_a_publication_date_is_rejected(over: dict) -> None:
    assert "no publish_date" in ref.sourcing_problems(deal(**over))


@pytest.mark.parametrize("bad", ["June 2026", "2026-06", "06/01/2026", "2026-13-01"])
def test_a_publication_date_that_is_not_a_day_is_rejected(bad: str) -> None:
    """"June 2026" is a month; the whole point of the field is to place the claim on a day."""
    assert any("publish_date" in p for p in ref.sourcing_problems(deal(publish_date=bad)))


# ---------------------------------------------------------------------------------------------
# Rule 2 — the caveat travels with the figure
# ---------------------------------------------------------------------------------------------


def test_a_row_with_a_caveat_passes() -> None:
    assert ref.caveat_problems(deal()) == []


@pytest.mark.parametrize("over", [{"caveat": ""}, {"caveat": "   "}, {"caveat": None}, {}])
def test_a_row_without_a_caveat_is_rejected(over: dict) -> None:
    row = deal(**over) if over else {k: v for k, v in deal().items() if k != "caveat"}
    assert ref.caveat_problems(row) == ["no caveat"]


# ---------------------------------------------------------------------------------------------
# Rule 3 — the shared vocabulary; an unknown value is an error
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["announced", "permitted", "under_construction", "commissioning", "in_service"])
def test_every_stage_word_the_generator_table_uses_is_accepted(stage: str) -> None:
    assert ref.vocabulary_problems(deal(stage=stage)) == []


@pytest.mark.parametrize("stage", ["signed", "Announced", "under construction", "retired", "operating"])
def test_a_stage_outside_the_vocabulary_is_rejected(stage: str) -> None:
    """Including "retired", which is a real EIA status and cannot be a stage of a contract."""
    assert any("stage" in p for p in ref.vocabulary_problems(deal(stage=stage)))


@pytest.mark.parametrize("path", ["A1", "A4", "C1", "U"])
def test_a_known_supply_path_is_accepted(path: str) -> None:
    assert ref.vocabulary_problems(deal(path=path)) == []


@pytest.mark.parametrize("path", ["A9", "c1", "nuclear", "A1 "])
def test_an_unknown_supply_path_is_rejected(path: str) -> None:
    assert any("path" in p for p in ref.vocabulary_problems(deal(path=path)))


def test_an_unknown_path_raises_rather_than_dropping_the_row(tmp_path, monkeypatch) -> None:
    """The row is not quietly skipped: a deal on a path the page has no column for is a fault."""
    write(tmp_path, monkeypatch, "deals", [deal(path="A9")])
    with pytest.raises(ref.CurationError) as exc:
        ref.rows("deals", TODAY)
    assert "A9" in str(exc.value)


def test_an_unknown_kind_raises() -> None:
    with pytest.raises(ref.CurationError):
        ref.reference_path("nonsense")


# ---------------------------------------------------------------------------------------------
# Rule 4 — stale rows are greyed, not dropped
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "stale"),
    [(0, False), (1, False), (REFERENCE_STALE_DAYS - 1, False), (REFERENCE_STALE_DAYS, False),
     (REFERENCE_STALE_DAYS + 1, True), (400, True)],
)
def test_the_stale_flag_turns_over_at_the_limit(age_days: int, stale: bool) -> None:
    assert ref.is_stale((TODAY - timedelta(days=age_days)).isoformat(), TODAY) is stale


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_a_row_nobody_has_checked_counts_as_stale(missing: str | None) -> None:
    """The absence of a check is not evidence of freshness."""
    assert ref.is_stale(missing, TODAY) is True


def test_an_unreadable_last_checked_date_raises() -> None:
    with pytest.raises(ref.CurationError):
        ref.is_stale("last spring", TODAY)


def test_a_stale_row_is_still_returned_and_is_flagged(tmp_path, monkeypatch) -> None:
    fresh = deal(deal_id="fresh", last_checked=(TODAY - timedelta(days=3)).isoformat())
    old = deal(deal_id="old", last_checked=(TODAY - timedelta(days=REFERENCE_STALE_DAYS + 5)).isoformat())
    write(tmp_path, monkeypatch, "deals", [fresh, old])
    got = {r["deal_id"]: r["stale"] for r in ref.rows("deals", TODAY)}
    assert got == {"fresh": False, "old": True}


# ---------------------------------------------------------------------------------------------
# Rule 5 — mw is null or a capacity
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mw", [None, 0.5, 1, 835.0, 2200])
def test_a_null_or_positive_capacity_is_accepted(mw) -> None:
    """Undisclosed capacity stays null; the one thing the column must never hold is a guess."""
    assert ref.capacity_problems(deal(mw=mw)) == []


def test_an_absent_mw_field_is_accepted() -> None:
    assert ref.capacity_problems({k: v for k, v in deal().items() if k != "mw"}) == []


@pytest.mark.parametrize("mw", [0, 0.0, -1, -835.0])
def test_zero_and_negative_are_not_capacities(mw) -> None:
    assert ref.capacity_problems(deal(mw=mw))


@pytest.mark.parametrize("mw", ["835", "~800 MW", True])
def test_a_capacity_that_is_not_a_number_is_rejected(mw) -> None:
    assert any("not a number" in p for p in ref.capacity_problems(deal(mw=mw)))


# ---------------------------------------------------------------------------------------------
# Rule 6 — who said it
# ---------------------------------------------------------------------------------------------


def test_a_company_confirmed_row_is_not_flagged() -> None:
    assert ref.is_unconfirmed(deal(company_confirmed=True)) is False


def test_a_reported_but_unconfirmed_row_is_flagged() -> None:
    assert ref.is_unconfirmed(deal(company_confirmed=False)) is True


def test_a_file_that_does_not_make_the_claim_is_not_flagged() -> None:
    """Only the deal rows carry `company_confirmed`; elsewhere the question does not arise."""
    assert ref.is_unconfirmed({"ticker": "AEP", "quarter": "2026Q2"}) is False


def test_an_unconfirmed_deal_is_published_with_its_flag(tmp_path, monkeypatch) -> None:
    write(tmp_path, monkeypatch, "deals", [deal(company_confirmed=False)])
    got = ref.rows("deals", TODAY)
    assert len(got) == 1 and got[0]["unconfirmed"] is True


# ---------------------------------------------------------------------------------------------
# Rule 7 — working notes never reach a reader
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        {"caveat": "Capacity is the plant rating. TODO confirm the term."},
        {"caveat": "Figure not verified against the filing."},
        {"seller": "Example Power (re-verify the entity name)"},
        {"technology": "Nuclear — check before publishing"},
        {"source_name": "adversarial pass, second researcher"},
    ],
)
def test_a_note_to_ourselves_in_a_published_field_is_rejected(over: dict) -> None:
    assert ref.working_note_problems(deal(**over))


def test_the_same_note_is_fine_in_the_private_field() -> None:
    """The curator needs somewhere to write "re-verify in October"; that somewhere is not the page."""
    assert ref.working_note_problems(deal(curator_notes="re-verify in October, TODO chase the filing")) == []


def test_the_private_field_is_stripped_before_anything_can_publish_it(tmp_path, monkeypatch) -> None:
    write(tmp_path, monkeypatch, "deals", [deal(curator_notes="dead URL, mirrored on archive.org")])
    got = ref.rows("deals", TODAY)
    assert "curator_notes" not in got[0]
    assert "curator_notes" not in json.dumps(got)


# ---------------------------------------------------------------------------------------------
# The other three files
# ---------------------------------------------------------------------------------------------


def utility_row(**over) -> dict:
    row = {
        "ticker": "AEP", "quarter": "2026Q2", "pipeline_gw_total": 24.0, "pipeline_gw_contracted": 0.0,
        "stage_breakdown": "signed electric service agreements only", "in_service_years": "2027-2031",
        "capex_plan_usd_bn": 54.0, "capex_plan_years": "2026-2030",
        "source_name": "Q2 2026 earnings call", "source_url": "https://example.com/q2", "publish_date": "2026-07-30",
        "last_checked": "2026-09-12", "quote": "Our pipeline stands at 24 gigawatts.",
        "caveat": "A pipeline is load requested, not load delivered; the company's definition changed in 2025.",
    }
    row.update(over)
    return row


def test_zero_contracted_gigawatts_is_a_disclosure_not_a_missing_value(tmp_path, monkeypatch) -> None:
    """Rule 5 is about `mw`. A utility saying nothing is contracted yet is news, and 0 records it."""
    write(tmp_path, monkeypatch, "utility_pipelines", [utility_row()])
    got = ref.rows("utility_pipelines", TODAY)
    assert got[0]["pipeline_gw_contracted"] == 0.0


def test_a_demand_forecast_must_say_whether_it_is_a_level_or_an_increment(tmp_path, monkeypatch) -> None:
    """176 TWh consumed in a year and "+240 TWh by 2030" cannot share a column unlabelled."""
    row = {
        "source": "Example Lab", "scenario": "reference", "year": 2030, "twh": 240.0, "share_of_us_pct": None,
        "source_name": "Example report", "source_url": "https://example.com/r", "publish_date": "2026-04-10",
        "page": "p. 12", "caveat": "Growth against a 2024 base, not a level.",
    }
    write(tmp_path, monkeypatch, "demand_forecasts", [row])
    with pytest.raises(ref.CurationError) as exc:
        ref.rows("demand_forecasts", TODAY)
    assert "basis" in str(exc.value)
    write(tmp_path, monkeypatch, "demand_forecasts", [row | {"basis": "increment"}])
    assert ref.rows("demand_forecasts", TODAY)[0]["basis"] == "increment"


# ---------------------------------------------------------------------------------------------
# Loading, the empty case, and the table
# ---------------------------------------------------------------------------------------------


def test_every_fault_in_the_file_is_reported_at_once(tmp_path, monkeypatch) -> None:
    """A curated file is short enough to fix in one pass, so the error names everything wrong."""
    write(tmp_path, monkeypatch, "deals", [deal(deal_id="a", caveat="", source_url="x"), deal(deal_id="b", mw=0)])
    with pytest.raises(ref.CurationError) as exc:
        ref.rows("deals", TODAY)
    message = str(exc.value)
    assert "deals[a]" in message and "deals[b]" in message
    assert "no caveat" in message and "source_url" in message and "mw 0" in message


def test_an_empty_file_is_valid(tmp_path, monkeypatch) -> None:
    for kind in ref.KINDS:
        write(tmp_path, monkeypatch, kind, [])
    assert ref.rows("deals", TODAY) == []
    statuses = {c["name"]: c["status"] for c in ref.validate_all(today=TODAY)}
    assert set(statuses.values()) == {"pass"}, statuses
    assert "the file is empty" in next(c["detail"] for c in ref.validate_all(today=TODAY) if c["name"] == "deals rows")


def test_a_missing_file_fails_only_its_own_checks(tmp_path, monkeypatch) -> None:
    """One broken curated layer must not take the other three off the page."""
    for kind in ref.KINDS:
        write(tmp_path, monkeypatch, kind, [])
    (tmp_path / "unit_economics.json").unlink()
    checks = ref.validate_all(today=TODAY)
    failed = [c["name"] for c in checks if c["status"] == "fail"]
    assert failed == ["unit_economics file"]
    assert any(c["name"] == "deals rows" and c["status"] == "pass" for c in checks)


def test_a_file_that_is_not_json_fails_as_a_check_not_as_a_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ref, "REFERENCE_DIR", tmp_path)
    for kind in ref.KINDS:
        write(tmp_path, monkeypatch, kind, [])
    (tmp_path / "deals.json").write_text("{ not json", encoding="utf-8")
    failed = [c["name"] for c in ref.validate_all(today=TODAY) if c["status"] == "fail"]
    assert failed == ["deals file", "deals table"]


def test_the_deal_table_holds_the_contract_columns_only(tmp_path, monkeypatch) -> None:
    write(tmp_path, monkeypatch, "deals", [deal(curator_notes="private")])
    df = ref.deals_table(TODAY)
    assert df.height == 1
    assert "stale" not in df.columns and "unconfirmed" not in df.columns and "curator_notes" not in df.columns
    assert df["mw"].to_list() == [835.0] and df["deal_id"].to_list() == ["msft-constellation-tmi"]


def test_the_deal_table_is_empty_and_valid_with_no_deals(tmp_path, monkeypatch) -> None:
    write(tmp_path, monkeypatch, "deals", [])
    df = ref.deals_table(TODAY)
    assert df.height == 0 and "deal_id" in df.columns


def test_the_deal_table_rejects_two_rows_with_one_id(tmp_path, monkeypatch) -> None:
    """`deals_frame` de-duplicates on `deal_id`, so a copy-pasted id would silently lose a deal."""
    write(tmp_path, monkeypatch, "deals", [deal(buyer="One"), deal(buyer="Two")])
    assert ref.deals_table(TODAY).height == 1
    assert len(ref.rows("deals", TODAY)) == 2  # the loader keeps both; the id collision is visible


# ---------------------------------------------------------------------------------------------
# The curated files that ship with the repo
# ---------------------------------------------------------------------------------------------
#
# These run against the real files rather than fixtures, because the rules above are only worth
# anything if the shipped rows actually obey them. A curator editing a JSON file by hand is the
# least-checked path into this site, and this is where that gets caught.


@pytest.mark.parametrize("kind", ref.KINDS)
def test_the_shipped_file_documents_itself(kind: str) -> None:
    data = ref.load(kind)
    assert len(data.get("note", "")) > 200, f"{kind}: the file has to say what it is for"
    assert data.get("row_template"), f"{kind}: a curator needs a row to copy"


@pytest.mark.parametrize("kind", ref.KINDS)
def test_every_shipped_row_is_publishable(kind: str) -> None:
    """`rows()` raises with every fault in the file, so a failure here names what to fix."""
    published = ref.rows(kind)
    assert len(published) == len(ref.load(kind)["rows"])


@pytest.mark.parametrize("kind", ref.KINDS)
def test_no_shipped_row_leaks_a_private_field(kind: str) -> None:
    for row in ref.rows(kind):
        assert not set(row) & set(ref.PRIVATE_FIELDS), f"{kind}: curator notes reached the page"


@pytest.mark.parametrize("kind", ref.KINDS)
def test_every_shipped_row_matches_the_template(kind: str) -> None:
    """A field nobody declared is a field the page will not render and nobody will notice."""
    data = ref.load(kind)
    allowed = set(data["row_template"]) | {"stale", "unconfirmed"}
    for row in data["rows"]:
        extra = set(row) - allowed
        assert not extra, f"{kind}: {sorted(extra)} is not in row_template"


def test_the_shipped_files_pass_every_check() -> None:
    bad = [c for c in ref.validate_all() if c["status"] == "fail"]
    assert not bad, bad


def test_main_succeeds_on_the_shipped_files() -> None:
    assert ref.main([]) == 0
