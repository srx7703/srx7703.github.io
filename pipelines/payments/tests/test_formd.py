"""Unit tests for pipelines.payments.formd, offline.

fixtures/formd/ copies the shapes of data.sec.gov/submissions (Stripe's real accession numbers and dates),
efts.sec.gov/LATEST/search-index (synthetic CIKs; one hit is a fund whose name merely contains "Brex", one a
co-issuer filing) and a Form D primary_doc.xml (Stripe's 2023 D/A amounts), plus an "Indefinite" offering.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from pipelines.payments import config
from pipelines.payments import formd as f

FIX = Path(__file__).parent / "fixtures" / "formd"


def _json(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _xml(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _404(url: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError("HTTP 404", request=req, response=httpx.Response(404, request=req))


class FakeSec:
    def __init__(self, json_routes: dict[str, Any], text_routes: dict[str, Any] | None = None) -> None:
        self.json_routes, self.text_routes = json_routes, text_routes or {}
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        key = (path, (params or {}).get("q")) if params else path
        v = self.json_routes.get(key)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise _404(path)
        return v

    def get_text(self, url: str) -> str:
        self.calls.append((url, None))
        v = self.text_routes.get(url)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise _404(url)
        return v


# --- config ---------------------------------------------------------------------------------


def test_issuers_are_universe_companies_and_include_the_named_privates():
    names = {c for layer in config.UNIVERSE.values() for g in layer.values() for c in g}
    assert set(f.ISSUERS) <= names
    assert {"Stripe", "Ramp", "Brex", "Airwallex", "Checkout.com", "Mercury", "Rapyd", "Nium"} <= set(f.ISSUERS)


def test_user_agent_required(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(f.SecUserAgentMissing, match="SEC_USER_AGENT is not set"):
        f.sec_user_agent()
    with pytest.raises(f.SecUserAgentMissing):
        f.run(write_outputs=False)  # raises before any network call
    monkeypatch.setenv("SEC_USER_AGENT", "portfolio-bot")
    with pytest.raises(f.SecUserAgentMissing, match="email"):
        f.sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "portfolio-bot ci@example.org")
    assert f.sec_user_agent() == "portfolio-bot ci@example.org"


def test_main_exits_cleanly_without_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert f.main(["--dry-run"]) == 2


# --- parse ----------------------------------------------------------------------------------


def test_filing_url_uses_raw_xml_not_rendered_view():
    url = f.filing_url("0001691342", "0001691342-23-000002", "xslFormDX01/primary_doc.xml")
    assert url == "https://www.sec.gov/Archives/edgar/data/1691342/000169134223000002/primary_doc.xml"


def test_parse_submissions_keeps_form_d_only():
    payload = _json("submissions_stripe.json")
    payload["filings"]["recent"]["form"][2] = "8-K"
    rows = f.parse_submissions(payload, "1691342")
    assert [(r["accession"], r["form"]) for r in rows] == [("0001691342-24-000001", "D"),
                                                           ("0001691342-23-000002", "D/A")]
    assert rows[0]["cik"] == "0001691342" and rows[0]["filing_date"] == date(2024, 4, 12)
    with pytest.raises(ValueError, match="asked for"):
        f.parse_submissions(payload, "0000000001")


def test_parse_efts_filters_by_legal_name():
    rows, total = f.parse_efts(_json("efts_brex.json"), f.ISSUERS["Brex"].name)
    assert total == 4
    assert [r["accession"] for r in rows] == ["0001712345-21-000001", "0001712345-22-000001",
                                              "0001999999-23-000001"]  # the "Brex Secondary Fund" is not Brex
    assert {r["cik"] for r in rows} == {"0001712345"}  # the co-issuer filing is credited to Brex's CIK
    assert rows[1]["form"] == "D/A"


def test_parse_formd_xml_amounts():
    got = f.parse_formd_xml(_xml("primary_doc_stripe_2023_da.xml"))
    assert got["total_offering_amount"] == 6_869_866_984.0
    assert got["total_amount_sold"] == 6_869_866_984.0
    assert got["date_of_first_sale"] == date(2023, 3, 15)
    assert got["issuer_name"] == "Stripe, Inc." and got["offering_indefinite"] is False


def test_parse_formd_xml_indefinite_and_namespaced():
    got = f.parse_formd_xml(_xml("primary_doc_indefinite.xml"))
    assert got["total_offering_amount"] is None and got["offering_indefinite"] is True
    assert got["total_amount_sold"] == 250_000_000.0
    ns = _xml("primary_doc_stripe_2023_da.xml").replace("<edgarSubmission>",
                                                        '<edgarSubmission xmlns="http://www.sec.gov/edgar/formd">')
    assert f.parse_formd_xml(ns)["total_amount_sold"] == 6_869_866_984.0
    with pytest.raises(ValueError, match="offeringSalesAmounts"):
        f.parse_formd_xml("<edgarSubmission/>")


# --- fetch ----------------------------------------------------------------------------------

STRIPE = {"Stripe": f.ISSUERS["Stripe"]}
BREX = {"Brex": f.ISSUERS["Brex"]}


def _stripe_routes() -> tuple[dict, dict]:
    js = {f.SUBMISSIONS_URL.format(cik="0001691342"): _json("submissions_stripe.json")}
    base = "https://www.sec.gov/Archives/edgar/data/1691342/{}/primary_doc.xml"
    text = {base.format("000169134223000002"): _xml("primary_doc_stripe_2023_da.xml"),
            base.format("000169134224000001"): _xml("primary_doc_indefinite.xml")}
    return js, text


def test_submissions_path_end_to_end():
    js, text = _stripe_routes()
    res = f.fetch_all(FakeSec(js, text), issuers=STRIPE)
    df = f.FORMD_SCHEMA.validate(res.filings)
    assert df.height == 3 and set(df["source"]) == {"submissions"}
    da = df.filter(pl.col("accession") == "0001691342-23-000002").row(0, named=True)
    assert da["total_offering_amount"] == 6_869_866_984.0 and da["form"] == "D/A"
    assert da["url"].endswith("/000169134223000002/primary_doc.xml")
    # 2023-000001 has no XML in the fake: row kept, amounts null, recorded
    missing = df.filter(pl.col("accession") == "0001691342-23-000001")
    assert missing["total_amount_sold"].item() is None
    docs = next(c for c in res.checks if c["name"] == "Form D documents")
    assert docs["status"] == "warn" and "2 of 3" in docs["detail"]


def test_submissions_failure_falls_back_to_search():
    js = {f.SUBMISSIONS_URL.format(cik="0001691342"): httpx.ConnectError("reset"),
          (f.EFTS_URL, '"Stripe, Inc."'): {"hits": {"total": {"value": 1}, "hits": [
              {"_id": "0001691342-24-000001:primary_doc.xml",
               "_source": {"ciks": ["0001691342"], "display_names": ["Stripe, Inc. (CIK 0001691342)"],
                           "file_date": "2024-04-12", "form": "D", "adsh": "0001691342-24-000001"}}]}}}
    res = f.fetch_all(FakeSec(js), issuers=STRIPE)
    assert res.filings["source"].to_list() == ["efts"]
    assert any(c["name"] == "Form D submissions: Stripe" and c["status"] == "warn" for c in res.checks)


def test_efts_pagination_and_params():
    page = _json("efts_brex.json")
    page["hits"]["total"]["value"] = 150
    sec = FakeSec({(f.EFTS_URL, '"Brex"'): page})
    f.fetch_all(sec, issuers=BREX)
    searches = [p for u, p in sec.calls if u == f.EFTS_URL]
    assert [s["from"] for s in searches] == [0, 100]
    assert searches[0]["forms"] == "D" and searches[0]["q"] == '"Brex"'


def test_one_failing_company_does_not_lose_the_others():
    js, text = _stripe_routes()
    js[(f.EFTS_URL, '"Brex"')] = httpx.ReadTimeout("slow")
    res = f.fetch_all(FakeSec(js, text), issuers={**STRIPE, **BREX})
    assert res.failed == ["Brex"]
    assert set(res.filings["company"]) == {"Stripe"}
    by = {c["name"]: c for c in res.checks}
    assert by["Form D companies"]["status"] == "warn" and "1 of 2" in by["Form D companies"]["detail"]
    assert by["Form D fetch: Brex"]["status"] == "fail"


def test_run_writes_validated_mart_and_raw(tmp_path, monkeypatch):
    js, text = _stripe_routes()
    monkeypatch.setattr(f, "ISSUERS", STRIPE)
    filings, checks = f.run(FakeSec(js, text), out_dir=tmp_path / "marts", raw_root=tmp_path / "raw")
    out = pl.read_parquet(tmp_path / "marts" / "formd.parquet")
    assert out.columns == list(f.FORMD_DTYPES) and out.height == 3
    assert list((tmp_path / "raw" / "payments" / "formd").glob("*/*/submissions_stripe.json.gz"))
    assert any(c["name"] == "Form D key uniqueness" and c["status"] == "pass" for c in checks)
