"""Unit tests for the openFDA clearance fetcher.

The tests that matter here are the 404 ones. openFDA answers 404 both for "your query matched nothing" and for
"that path does not exist", and the first version of this module treated them identically — which turned four
requests to a non-existent De Novo endpoint into four silent, successful, empty fetches. Nothing else in the
module can go wrong as expensively, so that is where the tests are.
"""

from __future__ import annotations

import httpx
import pytest

from pipelines.surgical import registry as reg


def _response(status: int, *, json_body=None, text: str = "") -> httpx.Response:
    req = httpx.Request("GET", "https://api.fda.gov/device/510k.json")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req)
    return httpx.Response(status, text=text, request=req)


# --- the two kinds of 404 ---------------------------------------------------------


def test_a_query_that_matched_nothing_is_an_empty_result(monkeypatch):
    monkeypatch.setattr(reg.httpx, "get", lambda *a, **k: _response(
        404, json_body={"error": {"code": "NOT_FOUND", "message": "No matches found!"}}))
    assert reg._get("/510k.json", {"search": "product_code:ZZZZ"}) == {
        "results": [], "meta": {"results": {"total": 0}}}


def test_a_path_that_does_not_exist_raises_instead_of_reporting_zero(monkeypatch):
    """The regression. openFDA serves HTML for an unknown path; that must never read as 'no records'."""
    monkeypatch.setattr(reg.httpx, "get", lambda *a, **k: _response(
        404, text="<!DOCTYPE html><html><body><pre>Cannot GET /device/denovo.json</pre></body></html>"))
    with pytest.raises(reg.UnknownEndpoint, match="does not exist"):
        reg._get("/denovo.json", {"limit": 1})


def test_an_unrecognised_404_body_also_raises(monkeypatch):
    monkeypatch.setattr(reg.httpx, "get", lambda *a, **k: _response(
        404, json_body={"error": {"code": "SOMETHING_ELSE"}}))
    with pytest.raises(reg.UnknownEndpoint):
        reg._get("/510k.json", {"limit": 1})


def test_openfda_has_no_denovo_endpoint_and_asking_for_one_raises():
    with pytest.raises(reg.UnknownEndpoint, match="no endpoint"):
        reg.fetch_code("SAQ", "fda_denovo")


def test_the_endpoint_map_is_the_single_source_of_truth():
    """If someone adds a registry here, they have to add a real path with it."""
    assert set(reg.REGISTRY_ENDPOINT) == {"fda_510k"}
    assert all(p.endswith(".json") for p in reg.REGISTRY_ENDPOINT.values())


# --- normalisation ----------------------------------------------------------------


def test_dates_are_normalised_from_both_shapes():
    rows = reg.normalise(
        [{"k_number": "K250725", "applicant": "Covidien LP", "decision_date": "20251203",
          "device_name": "Hugo RAS", "product_code": "SAQ"},
         {"k_number": "K260382", "applicant": "Auris Health, Inc.", "decision_date": "2026-03-11",
          "device_name": "Monarch", "product_code": "EOQ"}],
        "fda_510k", "SAQ", "ts")
    assert [r["decision_date"] for r in rows] == ["2025-12-03", "2026-03-11"]


def test_an_unparseable_date_becomes_null_rather_than_a_wrong_date():
    rows = reg.normalise([{"k_number": "K1", "decision_date": "N/A"}], "fda_510k", "SAQ", "ts")
    assert rows[0]["decision_date"] is None


def test_a_record_with_no_clearance_number_is_dropped():
    rows = reg.normalise([{"applicant": "Nobody"}, {"k_number": "K2"}], "fda_510k", "SAQ", "ts")
    assert [r["clearance_id"] for r in rows] == ["K2"]


def test_applicants_resolve_on_the_legal_entity_not_the_brand():
    """Hugo is filed by Covidien and Monarch by Auris. Matching 'Medtronic' or 'J&J' alone would lose both."""
    assert reg._maker_for("Covidien LP") == "MDT"
    assert reg._maker_for("Auris Health, Inc.") == "JNJ"
    assert reg._maker_for("Intuitive Surgical, Inc.") == "ISRG"
    assert reg._maker_for("TransEnterix Surgical, Inc.") == "private:karlstorz"
    assert reg._maker_for("Some Unrelated Endoscopy Co") is None
    assert reg._maker_for(None) is None


def test_an_applicant_mapped_to_an_empty_ticker_resolves_to_none():
    """Noah Medical is in the map so the match is recorded, but it is out of the pool, so it has no ticker."""
    assert reg._maker_for("Noah Medical Corporation") is None
