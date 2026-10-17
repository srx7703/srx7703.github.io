"""The committed ring C files, against the rules written in ``docs/cyber/CURATION.md``.

Every other ledger test runs on fictional fixtures; these read ``data/reference/cyber/`` itself, so a row edited the
wrong way fails here and not on the page. They check rules, never counts: a new deal must not break them.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from pipelines.cyber import ledger as L
from pipelines.cyber import reference as R

TODAY = date(2026, 10, 17)
ACQ = ("acquisition_announced", "acquisition_closed", "take_private")


@pytest.fixture(scope="module")
def events() -> list[dict]:
    return R.rows("events")


@pytest.fixture(scope="module")
def metrics() -> list[dict]:
    return R.rows("private_metrics")


@pytest.fixture(scope="module")
def built(events, metrics) -> L.Ledger:
    return L.build(R.rows("private_directory"), metrics, events, {}, today=TODAY, multiples_as_of="2026-10-16")


def deal_rows(events: list[dict], company: str) -> list[dict]:
    return sorted(
        (e for e in events if e["company"] == company and e["event_kind"] in ACQ), key=lambda e: e["event_date"]
    )


# --- buyer type: one written rule ------------------------------------------------------------------------------------------
def test_buyers_whose_main_business_is_not_security_are_not_platform_security(events) -> None:
    outside = {"Cisco": "strategic", "Cribl": "non_security_software", "Veeam": "non_security_software",
               "Tailscale": "non_security_software", "Infoblox": "non_security_software"}  # fmt: skip
    seen = {e["counterparty"] for e in events if e["event_kind"] in ACQ}
    assert set(outside) <= seen, "the rule's examples are in the file"
    for e in events:
        if e["event_kind"] in ACQ and e["counterparty"] in outside:
            assert e["buyer_type"] == outside[e["counterparty"]], (e["id"], e["buyer_type"])


def test_a_take_private_by_a_sponsor_owned_buyer_is_pe_and_a_merger_with_no_acquirer_is_other(events) -> None:
    varonis = [e for e in events if e["company"] == "Varonis Systems" and e["event_kind"] == "take_private"]
    assert varonis and {e["buyer_type"] for e in varonis} == {"pe"}
    for company in ("IriusRisk", "Synack"):
        rows = deal_rows(events, company)
        assert rows and {e["buyer_type"] for e in rows} == {"other"}, company


def test_every_deal_row_has_a_buyer_type_of_the_closed_list(events) -> None:
    assert all(e["buyer_type"] in R.BUYER_TYPES for e in events if e["event_kind"] in ACQ)


# --- the like-for-like inputs of the announced-versus-filing comparison -------------------------------------------------------
def test_armis_is_dated_by_servicenows_announcement_and_priced_there(events) -> None:
    rows = deal_rows(events, "Armis")
    assert [e["event_kind"] for e in rows] == ["acquisition_announced", "acquisition_closed"]
    announced = rows[0]
    assert announced["event_date"] == "2025-12-23" and announced["counterparty"] == "ServiceNow"
    assert (announced["value"], announced["qualifier"], announced["metric_kind"]) == (
        7.75e9,
        "~",
        "acquisition_price_announced",
    )
    assert "newsroom.servicenow.com" in announced["source_url"]


def test_a_pending_deal_note_and_a_target_press_release_are_not_filing_prices(events) -> None:
    portkey = deal_rows(events, "Portkey")
    assert portkey[0]["event_kind"] == "acquisition_announced" and portkey[0].get("value") is None
    assert portkey[-1]["event_kind"] == "acquisition_closed" and portkey[-1]["metric_kind"] == "acquisition_price_sec"
    jamf_close = [e for e in deal_rows(events, "Jamf") if e["event_kind"] == "acquisition_closed"]
    assert jamf_close and all(e.get("value") is None and e.get("sec_value") is None for e in jamf_close)


def test_no_price_is_called_announced_when_it_first_appears_after_completion(events) -> None:
    prompt = deal_rows(events, "Prompt Security")
    assert [e["event_kind"] for e in prompt] == ["acquisition_announced", "acquisition_closed"]
    assert all(e.get("value") is None for e in prompt), "no price was announced at signing"
    assert prompt[-1]["sec_value"] > 0


def test_where_a_deal_has_a_filing_price_its_announced_price_comes_from_the_announcement(built) -> None:
    """The announced-versus-filing comparison is only like for like if the announced price was announced: a price that
    first appears in a completion release or in a filing after closing is not one."""
    bad = []
    for deal in L._deal_groups(built.tables["events"].to_dicts()):
        members = deal["members"]
        if any(m["sec_value"] for m in members):
            bad += [
                deal["company"] for m in members if m["announced_value"] and m["event_kind"] not in L.DEAL_START_KINDS
            ]
    assert bad == []


# --- why a row is not charted --------------------------------------------------------------------------------------------------
def test_every_row_kept_off_the_charts_has_a_closed_reason_code(built) -> None:
    rep = built.tables["reported_unconfirmed"]
    assert rep.filter(pl.col("reason") == L.UNCLASSIFIED).height == 0
    assert {c["name"]: c["status"] for c in built.checks}["Every unpublished row has a reason"] == "pass"
    assert set(rep["reason"]) <= set(L.REASONS)


def test_the_codes_on_the_rows_are_in_the_closed_list(events, metrics) -> None:
    coded = [r["not_charted_code"] for r in (*events, *metrics) if "not_charted_code" in r]
    assert coded and set(coded) <= set(L.NOT_CHARTED_CODES)
    assert all(not r["chartable"] for r in (*events, *metrics) if "not_charted_code" in r), (
        "a code is for a row off the charts"
    )


def test_a_target_outside_security_is_kept_visible_and_not_counted(events, built) -> None:
    out = {e["company"] for e in events if e.get("not_charted_code") == "outside_scope"}
    assert {"Console", "Galileo", "Kentik"} <= out
    counted = set(built.tables["events"]["company"])
    assert not out & counted
    assert out <= set(built.tables["reported_unconfirmed"]["company"])


def test_a_figure_both_files_hold_is_listed_once(built) -> None:
    rep = built.tables["reported_unconfirmed"].filter(pl.col("value").is_not_null())
    key = ["company", "date", "metric_kind", "value", "source_url"]
    assert rep.height == rep.unique(subset=key).height
    assert built.summary["private"]["n_reported_duplicates"] >= 0


# --- the caveats that must travel ---------------------------------------------------------------------------------------------
def test_the_headline_caveats_say_what_the_figure_is(metrics) -> None:
    by = {r["id"]: r for r in metrics}
    assert "combined Cohesity and Veritas" in by["cohesity:round_valuation:2024-12-10"]["caveat"]
    assert "not a standalone round" in by["cohesity:round_valuation:2024-12-10"]["caveat"]
    assert "primary and secondary" in by["cribl:post_money_valuation:2024-08-27"]["caveat"]
    assert "secondary" in by["ninjaone:valuation:2026-06-09"]["caveat"]
