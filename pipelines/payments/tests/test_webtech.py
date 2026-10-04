"""Unit tests for pipelines.payments.webtech, offline, against fixtures shaped like /v1/adoption.

The fixtures under fixtures/webtech/ copy the response shape of https://cdn.httparchive.org/v1/adoption
(a list of {technology, date, adoption: {mobile, desktop}}), including a null client value and an
error envelope. A fake client serves them and scales counts per geo/rank so slices differ. No test
touches the network.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandera.errors
import polars as pl
import pytest

from pipelines.payments import webtech as w

FIX = Path(__file__).parent / "fixtures" / "webtech"
SCALE = {("ALL", "ALL"): 1, ("ALL", "Top 100k"): 100, ("ALL", "Top 10k"): 1000,
         ("United States of America", "ALL"): 4, ("United States of America", "Top 100k"): 300,
         ("United States of America", "Top 10k"): 3000}


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeClient:
    """Serves fixtures by technology; ``fail`` maps a technology (or (tech, geo, rank)) to an exception."""

    def __init__(self, fail: dict | None = None, override: dict | None = None) -> None:
        self.fail = fail or {}
        self.override = override or {}
        self.calls: list[dict] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/adoption"
        p = params or {}
        self.calls.append(p)
        geo = "United States of America" if p["geo"] != "ALL" else "ALL"
        key = (p["technology"], geo, p["rank"])
        for k in (key, p["technology"]):
            if k in self.fail:
                raise self.fail[k]
        if p["technology"] in self.override:
            return self.override[p["technology"]]
        f = FIX / f"adoption_{p['technology']}.json"
        if not f.exists():
            return []
        rows = copy.deepcopy(_load(f.name))
        div = SCALE[(geo, p["rank"])]
        for r in rows:
            r["adoption"] = {c: (None if v is None else v // div) for c, v in r["adoption"].items()}
        return rows


def _run(client: FakeClient) -> tuple[w.FetchResult, pl.DataFrame, pl.DataFrame]:
    res = w.fetch_all(client)
    adoption = w.build_adoption(res.counts, res.totals)
    return res, adoption, w.build_coverage(adoption)


# --- parse ------------------------------------------------------------------------------------


def test_parse_adoption_long_rows_and_null_client_skipped():
    df = w.parse_adoption(_load("adoption_Stripe.json"), "Stripe", "ALL", "ALL")
    assert df.columns == ["technology", "date", "geo", "rank", "client", "origins"]
    assert df.height == 7  # 4 months x 2 clients, minus the null desktop in 2026-07
    assert df.filter((pl.col("date") == date(2026, 7, 1)) & (pl.col("client") == "desktop")).height == 0
    assert df.filter((pl.col("date") == date(2026, 8, 1)) & (pl.col("client") == "mobile"))["origins"].item() == 101905


def test_parse_empty_list_is_valid():
    assert w.parse_adoption([], "Square", "ALL", "ALL").height == 0


@pytest.mark.parametrize(
    "payload",
    [
        _load("error_400.json"),
        "not json",
        [{"technology": "Stripe", "date": "2026-08"}],  # no adoption block
        [{"technology": "PayPal", "date": "2026-08-01", "adoption": {"mobile": 1}}],  # wrong tech
        [{"technology": "Stripe", "date": "Aug 2026", "adoption": {"mobile": 1}}],  # bad date
        [{"technology": "Stripe", "date": "2026-08-01", "adoption": {"mobile": -3}}],  # negative
        [{"technology": "Stripe", "date": "2026-08-01", "adoption": {"mobile": 1}}] * 2,  # duplicate month
    ],
)
def test_parse_rejects_malformed(payload):
    with pytest.raises(ValueError):
        w.parse_adoption(payload, "Stripe", "ALL", "ALL")


# --- fetch / isolation --------------------------------------------------------------------------


def test_fetch_all_requests_every_slice_from_2020():
    client = FakeClient()
    res = w.fetch_all(client)
    n = (1 + len(w.TECHNOLOGIES)) * len(w.GEOS) * len(w.RANKS)
    assert len(client.calls) == n
    assert {c["start"] for c in client.calls} == {"2020-01-01"}
    assert {c["geo"] for c in client.calls} == {"ALL", "United States of America"}
    assert {c["rank"] for c in client.calls} == {"Top 10k", "Top 100k", "ALL"}
    assert not res.failed
    assert res.checks[0]["status"] == "pass"
    assert len(res.raw) == n


def test_one_failing_technology_does_not_lose_the_others():
    client = FakeClient(fail={"PayPal": RuntimeError("HTTP 503"),
                              ("Stripe", "United States of America", "Top 10k"): ValueError("boom")})
    res, adoption, _ = _run(client)
    failed_names = [c["name"] for c in res.checks if c["status"] == "fail"]
    assert len([f for f in res.failed if f[0] == "PayPal"]) == 6
    assert ("Stripe", "United States of America", "Top 10k") in res.failed
    assert "Web coverage fetch: Stripe / United States of America / Top 10k" in failed_names
    assert res.checks[0]["status"] == "warn"  # partial, not total, failure
    assert "PayPal" not in adoption["technology"].to_list()
    stripe = adoption.filter(pl.col("technology") == "Stripe")
    assert stripe.select("geo", "rank").unique().height == 5  # 6 slices minus the failed one
    assert adoption.filter(pl.col("technology") == "Adyen").height > 0


def test_malformed_payload_is_a_failed_slice_not_a_crash():
    res, adoption, _ = _run(FakeClient(override={"Stripe": _load("error_400.json")}))
    assert {f[0] for f in res.failed} == {"Stripe"}
    assert "Stripe" not in adoption["technology"].to_list()


def test_failed_denominator_leaves_share_null_but_keeps_counts():
    res, adoption, _ = _run(FakeClient(fail={("ALL", "ALL", "Top 10k"): RuntimeError("timeout")}))
    top10k = adoption.filter((pl.col("geo") == "ALL") & (pl.col("rank") == "Top 10k"))
    assert top10k.height > 0
    assert top10k["share"].null_count() == top10k.height
    assert top10k["total_origins"].null_count() == top10k.height
    checks = {c["name"]: c for c in w.quality_checks(adoption, res.totals, today=date(2026, 9, 15))}
    assert checks["Web coverage denominator"]["status"] == "warn"


def test_everything_failing_still_returns_valid_empty_frames():
    res, adoption, coverage = _run(FakeClient(fail={t: RuntimeError("down") for t in ["ALL", *w.TECHNOLOGIES]}))
    assert res.checks[0]["status"] == "fail"
    assert adoption.height == 0
    assert coverage.height == len(w.TECHNOLOGIES)
    assert not coverage["detectable"].any()


# --- transform --------------------------------------------------------------------------------


def test_build_adoption_tidy_schema_and_share():
    _, adoption, _ = _run(FakeClient())
    assert adoption.columns == list(w.ADOPTION_DTYPES)
    assert dict(adoption.schema) == w.ADOPTION_DTYPES
    row = adoption.filter((pl.col("technology") == "Stripe") & (pl.col("geo") == "ALL") & (pl.col("rank") == "ALL")
                          & (pl.col("client") == "mobile") & (pl.col("date") == date(2026, 8, 1))).row(0, named=True)
    assert row["origins"] == 101905 and row["total_origins"] == 15950871
    assert row["share"] == pytest.approx(101905 / 15950871)
    assert set(adoption["client"]) == {"mobile", "desktop"}
    assert adoption["date"].min() == date(2026, 5, 1)


def test_schema_rejects_share_above_one():
    _, adoption, _ = _run(FakeClient())
    bad = adoption.with_columns(pl.lit(1.5).alias("share"))
    with pytest.raises(pandera.errors.SchemaError):
        w.ADOPTION_SCHEMA.validate(bad)


def test_coverage_flags_blind_spots():
    _, _, coverage = _run(FakeClient())
    assert coverage.columns == list(w.COVERAGE_DTYPES)
    assert coverage.height == len(w.TECHNOLOGIES)
    by = {r["technology"]: r for r in coverage.iter_rows(named=True)}
    assert by["Stripe"]["detectable"] and by["Stripe"]["months_present"] == 4
    assert by["Stripe"]["first_month"] == date(2026, 5, 1)
    adyen = by["Adyen"]
    assert not adyen["detectable"]
    assert adyen["latest_origins"] == 69
    assert adyen["months_present"] == 2  # the 0-origin month does not count as present
    assert adyen["first_month"] == date(2026, 7, 1)
    assert "Barely detected" in adyen["note"]
    sq = by["Square"]  # never returned: present in the table, not detectable
    assert sq["months_present"] == 0 and sq["first_month"] is None and not sq["detectable"]
    assert "invisible" in by["Shop Pay"]["note"]


def test_technology_list_carries_the_known_traps():
    assert {"Stripe", "PayPal", "Braintree", "Adyen", "Square", "Shop Pay", "Klarna Checkout", "Afterpay", "Affirm",
            "Checkout.com", "Amazon Pay", "Apple Pay", "Google Pay"} == set(w.TECHNOLOGIES)
    assert w.TECHNOLOGIES["Adyen"] and w.TECHNOLOGIES["Square"] and w.TECHNOLOGIES["Shop Pay"]


# --- checks -----------------------------------------------------------------------------------


def test_quality_checks_pass_on_clean_fixture():
    res, adoption, _ = _run(FakeClient())
    checks = {c["name"]: c["status"] for c in w.quality_checks(adoption, res.totals, today=date(2026, 9, 15))}
    assert checks == {"Web coverage key uniqueness": "pass", "Web coverage range": "pass",
                      "Web coverage denominator": "pass", "Web coverage crawl size": "pass",
                      "Web coverage freshness": "pass"}


def test_crawl_size_jump_and_staleness_are_flagged():
    totals = _load("adoption_ALL.json")
    totals[-1]["adoption"]["mobile"] = 30_000_000  # crawl doubled
    res, adoption, _ = _run(FakeClient(override={"ALL": totals}))
    checks = {c["name"]: c for c in w.quality_checks(adoption, res.totals, today=date(2027, 3, 1))}
    assert checks["Web coverage crawl size"]["status"] == "warn"
    assert "2026-08-01" in checks["Web coverage crawl size"]["detail"]
    assert checks["Web coverage freshness"]["status"] == "fail"


def test_inconsistent_crawl_total_nulls_share_for_that_month_only():
    totals = _load("adoption_ALL.json")
    totals[-1]["adoption"]["mobile"] = 50_000  # below Stripe's 2026-08 mobile count
    res, adoption, _ = _run(FakeClient(override={"ALL": totals}))
    stripe = adoption.filter((pl.col("technology") == "Stripe") & (pl.col("geo") == "ALL")
                             & (pl.col("rank") == "ALL") & (pl.col("client") == "mobile"))
    bad = stripe.filter(pl.col("date") == date(2026, 8, 1))
    assert bad["share"].is_null().all() and bad["origins"].item() > 0
    assert stripe.filter(pl.col("date") != date(2026, 8, 1))["share"].is_not_null().all()
    # other clients and slices are untouched
    assert adoption.filter(pl.col("client") == "desktop")["share"].is_not_null().all()
    checks = {c["name"]: c for c in w.quality_checks(adoption, res.totals, today=date(2026, 9, 15))}
    assert checks["Web coverage range"]["status"] == "fail"
    assert "Stripe 2026-08-01" in checks["Web coverage range"]["detail"]


def test_run_archives_raw_even_if_transform_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("transform bug")
    monkeypatch.setattr(w, "build_adoption", boom)
    with pytest.raises(RuntimeError):
        w.run(FakeClient(), out_dir=tmp_path / "m", raw_root=tmp_path / "r")
    assert list((tmp_path / "r").rglob("*.json.gz"))
    assert not (tmp_path / "m").exists()


# --- write ------------------------------------------------------------------------------------


def test_write_round_trip_and_raw_archive(tmp_path):
    res, adoption, coverage = _run(FakeClient())
    paths = w.write(adoption, coverage, tmp_path / "marts")
    assert [p.name for p in paths] == ["webtech_adoption.parquet", "webtech_coverage.parquet"]
    assert pl.read_parquet(paths[0]).equals(adoption)
    assert pl.read_parquet(paths[1]).equals(coverage)
    base = w.archive_raw(res.raw, tmp_path / "raw")
    assert len(list(base.glob("*.json.gz"))) == len(res.raw)


def test_write_refuses_invalid_frame(tmp_path):
    _, adoption, coverage = _run(FakeClient())
    with pytest.raises(pandera.errors.SchemaError):
        w.write(adoption.with_columns(pl.lit("Venmo").alias("technology")), coverage, tmp_path)
    assert not list(tmp_path.glob("*.parquet"))


def test_run_end_to_end_offline(tmp_path):
    adoption, coverage, checks = w.run(FakeClient(fail={"Braintree": RuntimeError("x")}), out_dir=tmp_path / "m",
                                       raw_root=tmp_path / "r")
    assert (tmp_path / "m" / "webtech_adoption.parquet").exists()
    assert any(c["status"] == "fail" and "Braintree" in c["name"] for c in checks)
    assert adoption.height and coverage.height == len(w.TECHNOLOGIES)
