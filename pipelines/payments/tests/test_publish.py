"""publish.py: marts and facts contract, chartable-only rule, CI-source handling, regression guard, idempotence."""
import json
from datetime import date

import polars as pl
import pytest

from pipelines.payments import config, publish, schema

TODAY = date(2026, 10, 4)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    md = tmp_path_factory.mktemp("marts")  # no CI-only marts here
    facts, tables = publish.build(TODAY, md)
    return facts, tables


def test_facts_contract(built):
    facts, _ = built
    assert facts["generated_at"]
    assert len(facts["kpis"]) == 3
    for k in facts["kpis"]:
        for f in ("value", "qualifier", "unit", "period", "source_url", "as_of"):
            assert k[f] is not None, f
        assert k["qualifier"] in ("=", ">", "<", "~")
    assert [r["id"] for r in facts["scoreboard"]] == [f"Q{i}" for i in range(1, 10)]
    assert {r["status"] for r in facts["scoreboard"]} <= set(config.STATUSES)
    assert all("graded" in r and "evidence" in r for r in facts["scoreboard"])
    assert {c["status"] for c in facts["checks"]} <= {"pass", "warn", "fail"}
    assert facts["coverage"]["n_companies"] == len(facts["coverage"]["companies"]) > 10


def test_every_value_carries_a_qualifier(built):
    facts, _ = built
    for key, v in facts["values"].items():
        assert v["qualifier"] in ("=", ">", "<", "~"), key
        assert "unit" in v and "period" in v, key


def test_no_curator_notes_anywhere(built):
    facts, tables = built
    assert not publish._has_key(facts, "curator_notes")
    for t in tables.values():
        assert "curator_notes" not in t.columns
    assert next(c for c in facts["checks"] if c["name"] == "Curator notes never published")["status"] == "pass"


def test_charted_marts_only_hold_chartable_rows(built):
    _, tables = built
    for name in schema.CHARTED_MARTS:
        t = tables[name]
        assert t.filter(~pl.col("tag").is_in(["V", "C"])).is_empty(), name
        if "metric_kind" in t.columns:
            assert t.filter(pl.col("metric_kind").is_in(config.NEVER_CHARTED_KINDS)).is_empty(), name


def test_reported_rows_only_in_reported_table(built):
    facts, tables = built
    rep = tables["reported_unconfirmed"]
    assert rep.filter(pl.col("reason") == "never_charted_kind").height >= 1
    reported_values = set(rep["value"].drop_nulls().to_list())
    # Brex's ~$700M annualised revenue is reported_talks: listed, never a templated number
    assert 700000000.0 in reported_values
    assert all(v.get("value") != 700000000.0 for v in facts["values"].values())
    assert "ledger.Brex.annualized_revenue" not in facts["values"]


def test_ci_sources_absent_are_warnings_and_questions_collect(built):
    facts, tables = built
    ci = [c for c in facts["checks"] if c["name"].startswith("CI source:")]
    assert len(ci) == len(publish.CI_SOURCES) and all(c["status"] == "warn" for c in ci)
    q = {r["id"]: r for r in facts["scoreboard"]}
    assert q["Q1"]["status"] == q["Q2"]["status"] == "collecting"
    assert not q["Q1"]["inputs_present"] and q["Q1"]["ci_only"]
    assert "webtech_q2" not in tables and "devstats_q1" not in tables


def test_unreadable_ci_mart_is_a_failed_check(tmp_path):
    (tmp_path / "jobs_counts.parquet").write_bytes(b"not a parquet file")
    facts, _ = publish.build(TODAY, tmp_path)
    c = next(c for c in facts["checks"] if c["name"] == "CI source: job-board counts (jobs)")
    assert c["status"] == "fail" and "unreadable" in c["detail"]


def test_drifted_ci_mart_skips_its_view_only(tmp_path):
    pl.DataFrame({"snapshot_date": [date(2026, 10, 1)], "ats": ["greenhouse"], "board": ["stripe"],
                  "func": ["engineering"], "postings": [10], "openings": [12]}).write_parquet(
        tmp_path / "jobs_counts.parquet")
    facts, tables = publish.build(TODAY, tmp_path)
    assert "jobs_by_function" not in tables
    assert {"products", "events", "share_lenses", "waterfall_lines", "scoreboard", "kpis"} <= set(tables)
    assert len(facts["kpis"]) == 3 and len(facts["scoreboard"]) == 9
    c = next(c for c in facts["checks"] if c["name"] == "CI source: job-board counts (jobs)")
    assert c["status"] == "fail" and "function" in c["detail"]
    others = [c for c in facts["checks"] if c["name"].startswith("CI source:") and "jobs" not in c["name"]]
    assert all(c["status"] == "warn" for c in others)


def test_ci_marts_present_feed_views_and_questions(tmp_path):
    months = [date(2026, m, 1) for m in range(6, 11)]
    adoption = pl.DataFrame([
        {"technology": t, "date": d, "geo": "United States of America", "rank": r, "client": "mobile",
         "origins": n, "total_origins": 10000, "share": n / 10000}
        for d in months for t, n in (("Stripe", 522), ("PayPal", 478)) for r in ("Top 10k", "ALL")],
        schema={"technology": pl.Utf8, "date": pl.Date, "geo": pl.Utf8, "rank": pl.Utf8, "client": pl.Utf8,
                "origins": pl.Int64, "total_origins": pl.Int64, "share": pl.Float64})
    adoption.write_parquet(tmp_path / "webtech_adoption.parquet")
    monthly = pl.DataFrame([
        {"registry": "npm", "side": "client", "series": c, "company": c, "packages": "p", "period": d,
         "downloads": n, "base_month": None, "index": None}
        for d in months for c, n in (("Stripe", 880), ("Adyen", 30), ("PayPal", 50), ("Checkout.com", 20),
                                     ("Airwallex", 20))],
        schema={"registry": pl.Utf8, "side": pl.Utf8, "series": pl.Utf8, "company": pl.Utf8, "packages": pl.Utf8,
                "period": pl.Date, "downloads": pl.Int64, "base_month": pl.Date, "index": pl.Float64})
    monthly.write_parquet(tmp_path / "devstats_client_monthly.parquet")
    facts, tables = publish.build(TODAY, tmp_path)
    assert {"webtech_q2", "devstats_q1", "devstats_monthly"} <= set(tables)
    q = {r["id"]: r for r in facts["scoreboard"]}
    assert q["Q1"]["inputs_present"] and q["Q2"]["inputs_present"]
    assert q["Q2"]["status"] == "collecting" and "2 of 6" in q["Q2"]["reason"]
    # the volume bound rests on members with no comparable calendar-year volume: no ratio, Q1 undecidable
    assert tables["devstats_q1"]["ratio"].null_count() == tables["devstats_q1"].height
    assert q["Q1"]["status"] == "undecidable"
    assert "q1.stripe_volume_share_upper" not in facts["values"]
    assert facts["ci_sources"]["webtech"] and facts["ci_sources"]["devstats"]


def test_write_is_idempotent_except_generated_at(built, tmp_path):
    facts, tables = built
    fp, md = tmp_path / "payments.json", tmp_path / "marts"
    publish.write(json.loads(json.dumps(facts)), tables, fp, md)
    first = {p.name: p.read_bytes() for p in md.iterdir()}
    f1 = json.loads(fp.read_text())
    facts2, tables2 = publish.build(TODAY, tmp_path / "empty")
    publish.write(facts2, tables2, fp, md)
    assert {p.name: p.read_bytes() for p in md.iterdir()} == first
    f2 = json.loads(fp.read_text())
    f1.pop("generated_at"), f2.pop("generated_at")
    assert f1 == f2


def test_regression_guard_keeps_existing_files(built, tmp_path):
    facts, tables = built
    fp, md = tmp_path / "payments.json", tmp_path / "marts"
    old = {"generated_at": "2026-10-01T00:00:00+00:00", "coverage": {"n_companies": 3 * facts["coverage"]["n_companies"]},
           "checks": [{"name": "x", "status": "pass", "detail": "y"}]}
    fp.write_text(json.dumps(old))
    assert publish.write(json.loads(json.dumps(facts)), tables, fp, md) == []
    kept = json.loads(fp.read_text())
    assert kept["generated_at"] == old["generated_at"] and kept["coverage"] == old["coverage"]
    guard = kept["checks"][-1]
    assert guard["name"] == "Regression guard" and guard["status"] == "fail"
    assert not md.exists()


def test_regression_guard_passes_on_a_comparable_run(built, tmp_path):
    facts, tables = built
    fp, md = tmp_path / "payments.json", tmp_path / "marts"
    fp.write_text(json.dumps({"coverage": {"n_companies": facts["coverage"]["n_companies"]}}))
    assert publish.write(json.loads(json.dumps(facts)), tables, fp, md)
    out = json.loads(fp.read_text())
    assert any(c["name"] == "Regression guard" and c["status"] == "pass" for c in out["checks"])
    assert (md / "scoreboard.json").exists() and (md / "kpis.json").exists()
