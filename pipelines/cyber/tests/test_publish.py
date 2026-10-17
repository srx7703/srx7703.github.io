"""publish.py and schema.py: the marts and facts contract, the checks, the empty-input behaviour, the weekly series, the
regression guard and the write.

Nothing here reads ``data/`` except the committed, immutable files (the baseline snapshot, the registered pool, the CIBR
holdings); the curated ring C rows come from the fictional fixtures of ``test_ledger`` (which still carry
``curator_notes``, to prove nothing of them travels), and the East Money history is synthetic. The bootstrap and
permutation draw count is lowered to ``DRAWS``: the registered 10,000 is for the page, these tests check the mechanics
and the numbers that do not depend on it.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandera.errors
import polars as pl
import pytest

from pipelines.common import curation
from pipelines.cyber import config as C
from pipelines.cyber import etf, evaluate, frozen, ledger, metrics, schema
from pipelines.cyber import publish as P
from pipelines.cyber import valuation_history_em as V
from pipelines.cyber.pool import POOL
from pipelines.cyber.tests.test_ledger import jamf_rows

FIX = Path(__file__).parent / "fixtures"
DRAWS = 200
TODAY = date(2026, 10, 17)
GENERATED_AT = "2026-10-17T08:00:00+00:00"
BASELINE_DAY = "2026-10-16"
SENTINEL = "NOTE-SENTINEL"
SPEC = Path(__file__).parents[3] / "docs" / "cyber" / "PIPELINE_SPEC.md"


# --- inputs ----------------------------------------------------------------------------------------------------
def fixture_rows(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def baseline() -> pl.DataFrame:
    return frozen.load_baseline()


def edit(frame: pl.DataFrame, column: str, ticker: str, new: Any) -> pl.DataFrame:
    """``frame`` with ``column`` of one ticker replaced; ``new`` is a value or a function of the old column's expression."""
    value = new(pl.col(column)) if callable(new) else pl.lit(new)
    return frame.with_columns(pl.when(pl.col("ticker") == ticker).then(value).otherwise(pl.col(column)).alias(column))


def monday_run(
    base: pl.DataFrame, day: str, *, seed: int = 0, noise: float = 0.05, edits: tuple[tuple[str, str, Any], ...] = ()
) -> pl.DataFrame:
    """The baseline's 73 ring A rows restamped to a run on ``day``, enterprise values shifted by lognormal noise so a
    week differs from the next. ``edits`` are ``(column, ticker, new)`` for ``edit``."""
    rng = np.random.default_rng(seed)
    shift = pl.Series(np.exp(rng.normal(scale=noise, size=base.height)))
    frame = base.with_columns(
        pl.lit(f"{day}T07:55:00+00:00").alias("snapshot_ts"),
        (pl.col("enterprise_value") * shift).alias("enterprise_value"),
    )
    for column, ticker, new in edits:
        frame = edit(frame, column, ticker, new)
    return frame


_BASELINE = object()  # "the baseline snapshot"; None and an empty frame must stay available as real arguments


def make_inputs(baseline: pl.DataFrame, *, runs: Any = _BASELINE, ledger_rows: bool = True, **over: Any) -> P.Inputs:
    """Inputs on the baseline, the ledger fixtures and the real CIBR holdings; no East Money history unless given."""
    base = P.Inputs(
        today=TODAY,
        generated_at=GENERATED_AT,
        runs=baseline if runs is _BASELINE else runs,
        private_directory=fixture_rows("ledger_directory.json") if ledger_rows else [],
        private_metrics=fixture_rows("ledger_metrics.json") if ledger_rows else [],
        private_events=fixture_rows("ledger_events.json") if ledger_rows else [],
        cibr=etf.load_cibr_records(),
    )
    return dataclasses.replace(base, **over)


def a_share_tickers() -> list[str]:
    return [e.ticker for e in POOL if e.ticker.endswith((".SS", ".SZ")) and e.ring in ("A", "B")]


def china_loaded(*, falling: tuple[str, ...] = ("002439.SZ",), days: int = 400) -> V.Loaded:
    """A synthetic East Money history for every A-share listing: PS_TTM rising to a new high on 2026-10-16, and
    year-on-year revenue falling for ``falling`` and rising for the rest."""
    last = date(2026, 10, 16)
    history, revenue = {}, {}
    for t in a_share_tickers():
        history[t] = [
            {
                "date": (last - timedelta(days=days - 1 - i)).isoformat(),
                "ps_ttm": 2.0 + i * 0.01,
                "pb": 1.0 + i * 0.005,
                "pe_ttm": None,
                "total_market_cap": 1e10,
            }
            for i in range(days)
        ]
        cur, prior = (80.0, 100.0) if t in falling else (120.0, 100.0)
        revenue[t] = [
            {"period_end": "2025-06-30", "revenue": prior, "yoy_reported": None, "notice_date": "2025-08-26"},
            {"period_end": "2026-06-30", "revenue": cur, "yoy_reported": None, "notice_date": "2026-08-26"},
        ]
    return V.Loaded(history, revenue, dict.fromkeys(history, "2026-10-17"))


@pytest.fixture(scope="module")
def built(baseline) -> P.Built:
    return P.build(make_inputs(baseline, china=china_loaded()), draws=DRAWS)


def check_by_name(facts: dict, name: str) -> dict:
    hits = [c for c in facts["checks"] if c["name"] == name]
    assert len(hits) == 1, f"{name}: {len(hits)} checks in {[c['name'] for c in facts['checks']]}"
    return hits[0]


def status(facts: dict, name: str) -> str:
    return check_by_name(facts, name)["status"]


def table(built: P.Built, name: str) -> pl.DataFrame:
    t = built.marts[name]
    assert isinstance(t, pl.DataFrame)
    return t


# --- the contract ---------------------------------------------------------------------------------------------
def test_facts_hold_every_key_the_page_reads_and_validate(built) -> None:
    facts, _ = built
    assert set(facts) == set(schema.FACT_KEYS)
    schema.validate_facts(facts)
    assert facts["slug"] == "cybersecurity-landscape" and facts["registered"] == C.REGISTERED
    assert facts["as_of"] == BASELINE_DAY, "the baseline file is named 2026-10-17 and read at its market date"
    assert {c["status"] for c in facts["checks"]} <= {"pass", "warn", "fail"}
    assert facts["marts"] == {name: f"cyber/{name}.json" for name in schema.ALL_MARTS}


def test_every_mart_in_the_pipeline_spec_is_published(built) -> None:
    text = SPEC.read_text(encoding="utf-8")
    block = text[text.index("## Marts") : text.index("## Facts")]
    named = set(re.findall(r"`([a-z_]+)`", block)) - {"json"}
    assert named, "the spec names its marts"
    assert named <= set(built.marts), sorted(named - set(built.marts))
    assert list(built.marts) == list(schema.ALL_MARTS)


def test_exactly_three_kpis_each_tagged_v_or_c(built) -> None:
    facts, marts = built
    kpis = facts["kpis"]
    assert len(kpis) == 3 and marts["kpis"].height == 3
    assert {k["tag"] for k in kpis} <= {"V", "C"}
    for k in kpis:
        assert k["qualifier"] in ("=", ">", "<", "~") and k["value"] is not None
        assert k["source_url"].startswith("http") and k["as_of"] and k["unit"] and k["period"]
    # the third is the highest private round of the ledger, with the qualifier the ledger gives it ('>': more than $8B)
    top = next(k for k in kpis if k["id"] == "highest_private_valuation")
    assert top["value"] == 8.0e9 and top["qualifier"] == ">" and "Kappa Guard" in top["label"]
    # the label says whose number it is, and the row's own caveat travels in the note
    assert top["label"] == "Highest company-stated valuation in the ledger (Kappa Guard)"
    assert top["note"].startswith("disclosed valuation; round of ") and "not a fair value" in top["note"]
    first = next(k for k in kpis if k["id"] == "pure_play_ev_ntm")
    n = facts["ev_sales"]["us_population_median"]["n"]
    assert f"the {n} US-listed pure-play companies in the registered group" in first["label"]
    assert f"at least {frozen.MIN_ANALYSTS} revenue analysts" in first["note"]


def test_every_published_number_is_a_record_with_a_qualifier(built) -> None:
    facts, _ = built
    assert schema.value_problems(facts) == []
    assert status(facts, "Qualifiers on every published number") == "pass"
    n = sum(1 for _, node in schema.walk(facts) if isinstance(node, dict) and "value" in node and "qualifier" in node)
    assert n > 100, "the facts carry their numbers as records"


def test_a_number_without_its_record_is_refused(built) -> None:
    facts = copy.deepcopy(built.facts)
    facts["ev_sales"]["median"]["qualifier"] = None  # a value with no qualifier
    del facts["targets"]["median_upside"]["source_url"]
    facts["rule_of_40"]["median_score"]["unit"] = ""
    probs = schema.value_problems(facts)
    assert any("ev_sales.median" in p and "qualifier" in p for p in probs)
    assert any("targets.median_upside" in p and "source_url" in p for p in probs)
    assert any("rule_of_40.median_score" in p and "unit" in p for p in probs)
    with pytest.raises(ValueError, match="not publishable"):
        schema.validate_facts(facts)


def test_facts_with_a_nan_or_the_wrong_kpi_count_are_refused(built) -> None:
    facts = copy.deepcopy(built.facts)
    facts["ev_sales"]["median"]["value"] = float("nan")
    with pytest.raises(ValueError, match="not finite"):
        schema.validate_facts(facts)
    facts = copy.deepcopy(built.facts)
    facts["kpis"] = facts["kpis"][:2]
    with pytest.raises(ValueError, match="2 KPIs, not three"):
        schema.validate_facts(facts)
    facts = copy.deepcopy(built.facts)
    facts["kpis"][0]["tag"] = "S"
    with pytest.raises(ValueError, match="not V or C"):
        schema.validate_facts(facts)


def test_build_is_deterministic_and_does_not_touch_its_inputs(baseline) -> None:
    inputs = make_inputs(baseline)
    before = copy.deepcopy((inputs.private_directory, inputs.private_metrics, inputs.private_events))
    a = P.build(inputs, draws=DRAWS)
    b = P.build(inputs, draws=DRAWS)
    assert json.dumps(a.facts, sort_keys=True) == json.dumps(b.facts, sort_keys=True)
    assert (inputs.private_directory, inputs.private_metrics, inputs.private_events) == before


# --- the numbers: section I's readings, and independent recomputations -----------------------------------------
def test_scope_counts_are_the_registered_pool(built) -> None:
    scope = built.facts["scope"]
    assert (scope["n_layers"], scope["n_subsectors"]) == (12, sum(len(v["l2"]) for v in C.LAYERS.values()))
    ring = scope["by_ring"]
    assert (ring["A"]["n"], ring["B"]["n"], ring["F"]["n"]) == (73, 107, 6)
    assert ring["A"]["us"] + ring["A"]["other_overseas"] == 58 and ring["A"]["china"] == 15
    assert scope["n_listings"] == len(POOL) == 186 and scope["n_listed"] == 180
    assert scope["n_private"] == ring["C"]["n"] == len(fixture_rows("ledger_directory.json"))
    assert sum(v["n_a"] for v in scope["by_layer"].values()) == 73


def test_layer_summary_adds_up_and_its_median_is_the_populations(built, baseline) -> None:
    t = table(built, "layer_summary")
    assert t["layer"].to_list() == list(C.LAYERS)
    assert (t["n_a"].sum(), t["n_b"].sum(), t["n_f"].sum()) == (73, 107, 6)
    assert t["n_population"].sum() == len(frozen.q1_population(baseline)) == 17
    as_of = date.fromisoformat(BASELINE_DAY)
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    for r in t.iter_rows(named=True):
        mine = [
            frozen.ev_ntm(rows[x], as_of)
            for x in frozen.q1_population(baseline)
            if frozen.registered_pool()[x].layer == r["layer"]
        ]
        assert r["n_population"] == len(mine)
        if mine:
            assert r["population_median_ev_ntm"] == pytest.approx(float(np.median(mine)))
        else:
            assert r["population_median_ev_ntm"] is None
    assert sum(x["n"] for r in t.iter_rows(named=True) for x in r["private_by_l2"]) == t["n_private"].sum()


def test_multiples_reproduce_the_registered_baseline_readings(built, baseline) -> None:
    ev = built.facts["ev_sales"]
    as_of = date.fromisoformat(BASELINE_DAY)
    values = [frozen.ev_ntm(r, as_of) for r in baseline.iter_rows(named=True)]
    values = [v for v in values if v is not None]
    assert ev["n_with"] == len(values) == 66 and ev["n_refused"] == 7 and ev["n_ring_a"] == 73
    assert ev["median"]["value"] == pytest.approx(float(np.median(values)), abs=1e-6)
    assert (
        ev["p25"]["value"] == ev["median"]["p25"]["value"] == pytest.approx(float(np.percentile(values, 25)), abs=1e-6)
    )
    assert ev["p75"]["value"] == pytest.approx(float(np.percentile(values, 75)), abs=1e-6)
    reasons = {r["reason"]: r["tickers"] for r in ev["refused_reasons"]}
    assert reasons["currency_mismatch"] == ["BTQ", "CNS.L", "KUD.SW"], (
        "section I: three differ and are shown unmultiplied"
    )
    assert sum(len(v) for v in reasons.values()) == 7
    # section I, Q7 and Q9: 17 companies, median 7.3x; six A-shares, median 4.4x; ratio 0.60; premium 4.84
    assert ev["us_population_median"]["n"] == 17 and round(ev["us_population_median"]["value"], 1) == 7.3
    assert ev["cn_median"]["n"] == 6 and round(ev["cn_median"]["value"], 1) == 4.4
    assert round(ev["cn_us_ratio"]["value"], 2) == 0.60
    reg = built.facts["regression"]
    assert (
        round(reg["premium"]["ratio"]["value"], 2) == 4.84
        and round(reg["premium"]["ai_trio_median"]["value"], 1) == 29.8
    )
    assert reg["premium"]["n_rest"] == 14
    assert reg["baseline"]["premium"]["ratio"]["value"] == reg["premium"]["ratio"]["value"]


def test_a_cn_median_below_five_companies_is_not_printed(baseline) -> None:
    low = baseline.with_columns(
        pl.when(pl.col("ticker").is_in(["002212.SZ", "300369.SZ", "688489.SS"]))
        .then(1)
        .otherwise(pl.col("em_rev0_n"))
        .alias("em_rev0_n")
    )
    facts = P.build(make_inputs(baseline, runs=low), draws=DRAWS).facts["ev_sales"]
    assert facts["cn_median"]["value"] is None and facts["cn_median"]["qualifier"] is None
    assert facts["cn_median"]["n"] == 3 and "fewer than 5" in facts["cn_median"]["reason"]
    assert facts["cn_us_ratio"]["value"] is None


def test_rule_of_40_counts_and_extremes(built, baseline) -> None:
    r40 = built.facts["rule_of_40"]
    rows = [r for r in metrics.listing_rows(baseline, date.fromisoformat(BASELINE_DAY)) if r["ring"] == "A"]
    scores = sorted(r["rule_of_40"] for r in rows if r["rule_of_40"] is not None)
    assert r40["n_scored"] == len(scores) and r40["n_pass"] == sum(s >= 40 for s in scores)
    assert r40["median_score"]["value"] == pytest.approx(float(np.median(scores)), abs=1e-6)
    assert [x["value"] for x in r40["leaders"]] == pytest.approx(scores[::-1][:3], abs=1e-5)
    assert [x["value"] for x in r40["laggards"]] == pytest.approx(scores[:3], abs=1e-5)
    assert all(x["growth"]["value"] is not None and x["ticker"] for x in r40["leaders"])


def test_targets_are_ring_a_outside_china_and_reproduce_section_i(built, baseline) -> None:
    t = table(built, "targets")
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    assert t.height == 58 and not any(x.endswith((".SS", ".SZ")) for x in t["ticker"]), (
        "A-shares are left out by design"
    )
    for r in t.iter_rows(named=True):
        src = rows[r["ticker"]]
        assert r["upside"] == pytest.approx(src["target_mean"] / src["price"] - 1.0)
        assert r["thin"] == (src["n_opinions"] < 3) and r["n_opinions"] == src["n_opinions"]
    tg = built.facts["targets"]
    assert (tg["n_with_target"], tg["n_without"], tg["n_thin"]) == (58, 0, t["thin"].sum())
    assert tg["n_gbp_pence_normalised"] == sum(1 for r in rows.values() if r["currency"] == "GBp") == 5
    # section I, Q4 "already seen": the 34 companies of the frozen list, median +13.5%, lowest 4704.T, highest IDN
    q4 = tg["q4_q5_list"]
    assert q4["n"] == 34 == q4["n_registered"] and round(100 * q4["median_upside"]["value"], 1) == 13.5
    assert (q4["least_upside"]["ticker"], round(100 * q4["least_upside"]["value"], 1)) == ("4704.T", -12.9)
    assert (q4["most_upside"]["ticker"], round(100 * q4["most_upside"]["value"], 1)) == ("IDN", 125.9)
    assert tg["most_upside"]["n_opinions"] >= 3, "an extreme is named among companies with three or more analysts"
    assert {x["ticker"] for x in tg["thin_extremes"]} == {"CYCU", "INTZ", "BKYI"}
    assert sum(v["n"] for v in tg["by_layer"].values()) == 58 == sum(v["n"] for v in tg["by_market"].values())
    assert t["in_q4_q5_list"].sum() == 34 and set(t.filter(pl.col("in_q4_q5_list"))["ticker"]) == set(C.FROZEN_Q4_Q5)


def test_a_listing_without_a_target_is_counted_not_dropped(baseline) -> None:
    built = P.build(make_inputs(baseline, runs=edit(baseline, "target_mean", "CRWD", None)), draws=DRAWS)
    tg = built.facts["targets"]
    assert (tg["n_with_target"], tg["n_without"]) == (57, 1)
    assert "CRWD" not in table(built, "targets")["ticker"].to_list()


def test_recommendations_keep_the_two_vocabularies_apart(built, baseline) -> None:
    rec = table(built, "recommendations")
    yahoo, em = rec.filter(pl.col("source") == "yahoo"), rec.filter(pl.col("source") == "eastmoney")
    assert yahoo.select(pl.any_horizontal([pl.col(c).is_not_null() for c in schema.EM_COLUMNS]).any()).item() is False
    assert em.select(pl.any_horizontal([pl.col(c).is_not_null() for c in schema.YAHOO_COLUMNS]).any()).item() is False
    assert set(yahoo.filter(pl.col("scope") == "listing")["market_group"]) <= {"us", "other_overseas"}
    assert set(em.filter(pl.col("scope") == "listing")["market_group"]) == {"china"}
    assert (
        yahoo.filter(pl.col("scope") == "listing").height == 58 and em.filter(pl.col("scope") == "listing").height == 15
    )
    pooled = yahoo.filter(pl.col("scope") == "pooled").row(0, named=True)
    listing = yahoo.filter(pl.col("scope") == "listing")
    for b in schema.YAHOO_RATING_BUCKETS:
        assert pooled[b] == listing[b].sum()
    assert pooled["n_ratings"] == sum(pooled[b] for b in schema.YAHOO_RATING_BUCKETS)
    # East Money printed no neutral, reduce or sell count for any covered company: null stays null, also pooled
    pooled_em = em.filter(pl.col("scope") == "pooled").row(0, named=True)
    assert pooled_em["em_neutral"] is None and pooled_em["em_reduce"] is None and pooled_em["em_sell"] is None
    assert pooled_em["em_buy"] + pooled_em["em_add"] == pooled_em["n_ratings"] == pooled_em["em_orgs"]
    f = built.facts["recommendations"]
    assert f["eastmoney"]["n_wholly_null"] == 3 and f["eastmoney"]["n_listings_printing"]["neutral"] == 0
    assert sum(v["value"] for v in f["yahoo"]["shares"].values()) == pytest.approx(1.0, abs=1e-5)


# --- the weekly series and the week-on-week checks --------------------------------------------------------------
@pytest.fixture(scope="module")
def weekly(baseline) -> P.Built:
    """Baseline plus three Monday runs; the third moves CRWD's target 40% and halves FTNT's analysts."""
    runs = pl.concat(
        [
            baseline,
            monday_run(baseline, "2026-10-26", seed=1, edits=(("n_opinions", "FTNT", 20),)),
            monday_run(baseline, "2026-11-02", seed=2, edits=(("n_opinions", "FTNT", 20),)),
            monday_run(
                baseline,
                "2026-11-09",
                seed=3,
                edits=(("target_mean", "CRWD", lambda c: c * 1.4), ("n_opinions", "FTNT", 8)),
            ),
        ],
        how="vertical_relaxed",
    )
    return P.build(make_inputs(baseline, runs=runs, today=date(2026, 11, 10), china=china_loaded()), draws=DRAWS)


def test_the_series_is_the_mondays_from_the_first_snapshot_and_never_the_baseline(weekly) -> None:
    series = weekly.marts["regression"]["series"]
    assert [p["as_of"] for p in series] == ["2026-10-26", "2026-11-02", "2026-11-09"]
    reg = weekly.facts["regression"]
    assert reg["n_series"] == 3 and reg["as_of"] == "2026-11-09" and reg["age_days"] == 1
    assert reg["baseline"]["as_of"] == BASELINE_DAY, "the baseline is shown beside the series, not in it"
    assert weekly.facts["as_of"] == "2026-11-09" and weekly.facts["counts"]["regression_series_points"] == 3
    assert status(weekly.facts, "Weekly series") == "pass"
    assert weekly.marts["regression"]["premium_series"][0]["as_of"] == "2026-10-26"


def test_listings_follow_the_newest_usable_run(weekly) -> None:
    listings = table(weekly, "listings")
    assert listings.filter(pl.col("ticker") == "CRWD")["target_mean"].item() == pytest.approx(
        frozen.load_baseline().filter(pl.col("ticker") == "CRWD")["target_mean"].item() * 1.4
    )


def test_a_target_that_moved_and_an_analyst_count_that_halved_are_named(weekly) -> None:
    moved = check_by_name(weekly.facts, "Target mean week on week")
    halved = check_by_name(weekly.facts, "Analyst count week on week")
    assert (
        moved["status"] == "warn" and "CRWD (+40%)" in moved["detail"] and "2026-11-02 to 2026-11-09" in moved["detail"]
    )
    assert halved["status"] == "warn" and "FTNT (20 to 8)" in halved["detail"]


def test_week_on_week_checks_say_when_they_cannot_be_assessed(built) -> None:
    for name in ("Target mean week on week", "Analyst count week on week"):
        c = check_by_name(built.facts, name)
        assert c["status"] == "warn" and "not assessable" in c["detail"]


def test_previous_run_is_the_one_nearest_a_week_back(baseline) -> None:
    runs = pl.concat(
        [
            monday_run(baseline, "2026-10-26"),  # 8 days before the newest: one day from a week
            monday_run(baseline, "2026-10-29"),  # 5 days before: inside the window, two days from a week
            monday_run(baseline, "2026-11-02"),  # 1 day before: the same week, not a week-on-week pair
            monday_run(baseline, "2026-11-03"),
        ]
    )
    (newest, _), (prev, _) = P.previous_run(runs, date(2026, 11, 10))
    assert newest[:10] == "2026-11-03" and prev[:10] == "2026-10-26"
    assert P.previous_run(monday_run(baseline, "2026-10-26"), date(2026, 11, 10)) is None
    assert P.previous_run(None, date(2026, 11, 10)) is None


def test_a_throttled_run_is_not_a_snapshot(baseline) -> None:
    keep = baseline["ticker"].to_list()[:10]
    throttled = monday_run(baseline, "2026-10-26").with_columns(
        pl.when(pl.col("ticker").is_in(keep)).then(pl.col("price")).otherwise(None).alias("price")
    )
    built = P.build(make_inputs(baseline, runs=pl.concat([baseline, throttled]), today=date(2026, 10, 27)), draws=DRAWS)
    assert built.facts["as_of"] == BASELINE_DAY and built.facts["regression"]["n_series"] == 0
    assert built.facts["coverage"]["n_priced"] == 73, "the listings stay on the last good run"


# --- an absent input is an empty section and a warn, never a crash -------------------------------------------------
def build_and_validate(inputs: P.Inputs) -> P.Built:
    built = P.build(inputs, draws=DRAWS)
    schema.validate_facts(built.facts)
    for name, m in built.marts.items():
        if isinstance(m, pl.DataFrame):
            schema.validate(name, m)
        else:
            schema.validate_regression(m)
    assert len(built.facts["kpis"]) == 3 and {k["tag"] for k in built.facts["kpis"]} <= {"V", "C"}
    assert [r["id"] for r in built.facts["scoreboard"]] == evaluate.QIDS
    assert not [
        c
        for c in built.facts["checks"]
        if c["name"] == "Qualifiers on every published number" and c["status"] != "pass"
    ]
    return built


@pytest.mark.parametrize("runs", [None, pl.DataFrame(), "throttled"], ids=["none", "empty frame", "no usable run"])
def test_no_usable_consensus_run(baseline, runs) -> None:
    if isinstance(runs, str):
        runs = baseline.head(10)
    built = build_and_validate(make_inputs(baseline, runs=runs))
    facts = built.facts
    listings = table(built, "listings")
    assert listings.height == 186 and not listings["priced"].any() and facts["as_of"] is None
    assert facts["coverage"]["n_priced"] == 0 and facts["ev_sales"]["n_with"] == 0
    assert facts["ev_sales"]["median"]["value"] is None and facts["ev_sales"]["median"]["qualifier"] is None
    assert table(built, "targets").height == 0 and table(built, "recommendations").height == 0
    assert facts["regression"]["fitted"] is False and built.marts["regression"]["latest"] is None
    for name in ("Ring A priced", "Consensus freshness", "Consensus coverage", "Regression point", "Weekly series"):
        assert status(facts, name) == "warn", name
    assert status(facts, "Listing rows by ring") == "pass"
    # the board is still ten items and all collecting; the ledger KPI and the pool counts fill the three KPIs
    assert {r["status"] for r in facts["scoreboard"]} == {"collecting"}
    assert [k["id"] for k in facts["kpis"]] == ["highest_private_valuation", "ring_a_listings", "listed_companies"]


def test_no_ledger_rows_yet(baseline) -> None:
    built = build_and_validate(make_inputs(baseline, ledger_rows=False))
    facts = built.facts
    for name in ("private_intervals", "ledger_timeline", "ledger_latest", "events", "reported_unconfirmed"):
        assert table(built, name).height == 0, name
    assert facts["private"]["n_directory"] == 0 and facts["private"]["latest_valuation_max"] is None
    assert facts["events"]["n"] == 0 and facts["events"]["latest"] is None
    assert status(facts, "Private ledger") == "warn"
    assert "highest_private_valuation" not in [k["id"] for k in facts["kpis"]]
    assert [k["id"] for k in facts["kpis"]][:2] == ["pure_play_ev_ntm", "median_target_upside"]
    assert all(v["n_private"] == 0 for v in facts["scope"]["by_layer"].values())


def test_no_east_money_history_yet(baseline) -> None:
    built = build_and_validate(make_inputs(baseline))
    assert table(built, "china_panel").height == 0
    c = check_by_name(built.facts, "China panel")
    assert c["status"] == "warn" and "31 A-share listings" in c["detail"]
    cn = built.facts["cn"]
    assert cn["n_listed"] == 31 and cn["n_panel_rows"] == 0 and cn["median_ps_ttm"]["value"] is None
    assert cn["n_with_em_consensus"] == 12 and cn["n_with_yahoo_target"] == 11


def test_an_unreadable_cibr_file_is_a_failed_check_and_an_empty_cross_check(baseline) -> None:
    built = build_and_validate(make_inputs(baseline, cibr=None, errors={"cibr": "FileNotFoundError: gone"}))
    assert table(built, "etf_crosscheck").height == 0 and built.facts["universe"]["etfs"] == []
    assert status(built.facts, "CIBR cross-check complete") == "fail"
    assert "gone" in check_by_name(built.facts, "Input: cibr")["detail"]


def test_no_baseline_among_the_runs_still_builds(baseline) -> None:
    later = monday_run(baseline, "2026-10-26")
    built = build_and_validate(make_inputs(baseline, runs=later, today=date(2026, 10, 27)))
    assert built.facts["regression"]["baseline"]["fitted"] is False
    assert built.facts["regression"]["n_series"] == 1 and built.facts["as_of"] == "2026-10-26"


def test_a_ledger_that_breaks_its_own_rules_is_a_failed_check_and_an_empty_ledger(baseline) -> None:
    directory = fixture_rows("ledger_directory.json")
    built = build_and_validate(make_inputs(baseline, private_directory=directory + [dict(directory[0])]))
    c = check_by_name(built.facts, "Private ledger")
    assert c["status"] == "fail" and "duplicate company" in c["detail"]
    assert table(built, "private_intervals").height == 0


# --- the ledger section ------------------------------------------------------------------------------------------
def test_the_ledger_section_is_the_ledgers_summary(built) -> None:
    facts = built.facts
    directory = fixture_rows("ledger_directory.json")
    assert facts["private"]["n_directory"] == len(directory) == table(built, "private_intervals")["in_directory"].sum()
    assert facts["private"]["multiples_as_of"] == BASELINE_DAY
    assert facts["private"]["n_chartable"] == table(built, "ledger_timeline").height
    assert facts["events"]["n"] == table(built, "events").height
    latest = facts["events"]["latest"]
    assert (
        latest["price"]["value"] == latest["chart_value"] and latest["price"]["qualifier"] == latest["chart_qualifier"]
    )
    assert latest["price"]["source_url"] == latest["source_url"]
    assert status(facts, "Ring C ledger matches directory") == "warn", "the fixture has an orphan company on purpose"


def test_charted_marts_hold_chartable_rows_only_and_the_rest_is_reported(built) -> None:
    for name in ledger.CHARTED_MARTS:
        t = table(built, name)
        tag = "headline_tag" if name == "private_intervals" else "tag"
        assert t.filter(pl.col(tag).is_not_null() & ~pl.col(tag).is_in(["V", "C"])).is_empty(), name
        if "metric_kind" in t.columns:
            assert t.filter(pl.col("metric_kind").is_in(C.NEVER_CHARTED_KINDS)).is_empty(), name
    rep = table(built, "reported_unconfirmed")
    assert rep.filter(pl.col("reason") == "never_charted_kind").height >= 1
    assert status(built.facts, "Charted figures are tag V or C") == "pass"


def test_dated_rows_replace_a_period_with_the_publish_day_and_say_so() -> None:
    rows = [
        {"company": "A", "as_of_date": "2026-05-31", "publish_date": "2026-06-02"},
        {"company": "B", "as_of_date": "FY2025", "publish_date": "2026-03-01"},
        {"company": "C", "as_of_date": "2020-04", "publish_date": "2020-05-09"},
    ]
    before = copy.deepcopy(rows)
    out, note = P.dated_rows("private_metrics", rows)
    assert rows == before, "the input rows are not modified"
    assert [r["as_of_date"] for r in out] == ["2026-05-31", "2026-03-01", "2020-05-09"]
    assert note["status"] == "warn" and "B 'FY2025'" in note["detail"] and "C '2020-04'" in note["detail"]
    assert P.dated_rows("events", rows[:1]) == ([rows[0]], None)


def test_a_period_in_as_of_date_does_not_stop_the_ledger(baseline) -> None:
    metrics_rows = fixture_rows("ledger_metrics.json")
    metrics_rows[0] = {**metrics_rows[0], "as_of_date": "FY2025"}
    built = build_and_validate(make_inputs(baseline, private_metrics=metrics_rows))
    assert status(built.facts, "Ledger as-of dates: private_metrics") == "warn"
    assert table(built, "ledger_timeline").height > 0 and status(built.facts, "Never-charted kinds kept out") == "pass"


# --- the China panel ----------------------------------------------------------------------------------------------
def test_the_china_panel_has_every_a_share_listing_and_flags_a_high_percentile_with_falling_revenue(built) -> None:
    panel = table(built, "china_panel")
    assert panel.height == len(a_share_tickers()) == 31 and set(panel["ring"]) == {"A", "B"}
    flagged = panel.filter(pl.col("flags").struct.field(V.FLAG_PS_HIGH_REVENUE_FALLING))["ticker"].to_list()
    assert flagged == ["002439.SZ"] == built.facts["cn"]["flagged"]
    cn = built.facts["cn"]
    assert cn["n_listed"] == 31 == cn["n_panel_rows"] and cn["n_with_percentile"] == 31
    assert cn["ps_percentile"]["002439.SZ"]["flagged"] is True and cn["ps_percentile"]["300454.SZ"]["flagged"] is False
    assert cn["ps_percentile"]["002439.SZ"]["revenue_yoy"]["value"] == pytest.approx(-0.2)
    assert cn["median_ps_percentile"]["value"] == 1.0 and cn["median_ps_percentile"]["qualifier"] == "="
    assert not [c for c in built.facts["checks"] if c["name"] == "China panel"], "the module's own checks stand in"
    assert status(built.facts, "China panel rows") == "pass" and status(built.facts, "China panel freshness") == "pass"


def test_a_stale_china_history_is_flagged(baseline) -> None:
    loaded = china_loaded()
    old = {t: [r for r in rows if r["date"] < "2026-09-01"] for t, rows in loaded.history.items()}
    built = P.build(make_inputs(baseline, china=dataclasses.replace(loaded, history=old)), draws=DRAWS)
    assert status(built.facts, "China panel freshness") in ("warn", "fail")


# --- the checks ----------------------------------------------------------------------------------------------------
def test_the_baseline_checks_are_what_the_data_says(built) -> None:
    facts = built.facts
    expect = {
        "Listing rows by ring": "pass", "Ring A priced": "pass", "Price age": "pass", "Consensus freshness": "pass",
        "Consensus coverage": "pass", "Missing multiples have a reason": "pass", "Target analyst count": "pass",
        "Target price and quote currency agree": "warn",  # CYCU and INTZ: one analyst's target, 25x and 14x the price
        "East Money rating rows": "warn",  # three A-shares print no rating count
        "Listing values are parsed capture fields": "pass", "Regression point": "pass",
        "Regression population equals the registered population": "pass", "CIBR cross-check complete": "pass",
        "Excluded list disjoint from the pool": "pass", "Scoreboard": "pass", "Headline KPIs": "pass",
        "Private notes never published": "pass", "No working notes in published text": "pass",
        "No row from a blocked source host": "pass",
        "Charted figures are tag V or C": "pass", "Weekly series": "warn",
    }  # fmt: skip
    got = {c["name"]: c["status"] for c in facts["checks"]}
    for name, want in expect.items():
        assert got[name] == want, (name, got[name], check_by_name(facts, name)["detail"])
    assert "CYCU" in check_by_name(facts, "Target price and quote currency agree")["detail"]
    assert "002912.SZ" in check_by_name(facts, "East Money rating rows")["detail"]
    assert not [c for c in facts["checks"] if c["status"] == "fail"]
    assert len({c["name"] for c in facts["checks"]}) == len(facts["checks"]), "every check is named once"


def test_a_pence_against_pounds_slip_fails_the_quote_currency_check(baseline) -> None:
    slipped = edit(baseline, "target_mean", "NCC.L", lambda c: c * 100)
    facts = P.build(make_inputs(baseline, runs=slipped), draws=DRAWS).facts
    c = check_by_name(facts, "Target price and quote currency agree")
    assert c["status"] == "fail" and "NCC.L" in c["detail"] and "pence" in c["detail"]


def test_a_target_analyst_count_that_is_not_the_snapshots_fails(baseline) -> None:
    targets = P.targets_frame(metrics.listing_rows(baseline, date.fromisoformat(BASELINE_DAY)), BASELINE_DAY)
    assert P.check_target_analysts(targets, baseline)["status"] == "pass"
    mixed = targets.with_columns(
        pl.when(pl.col("ticker") == "CRWD").then(pl.lit(99)).otherwise(pl.col("n_opinions")).alias("n_opinions")
    )
    c = P.check_target_analysts(mixed, baseline)
    assert c["status"] == "fail" and "CRWD" in c["detail"]


def test_a_silent_missing_multiple_fails_and_a_stated_one_passes(baseline) -> None:
    silent = edit(baseline, "rev_fy1_avg", "ALLT", -1.0e12)
    rows = metrics.listing_rows(silent, date.fromisoformat(BASELINE_DAY))
    allt = next(r for r in rows if r["ticker"] == "ALLT")
    assert allt["ev_ntm"] is None and allt["ev_ntm_reason"] is None, (
        "negative revenue consensus: no multiple, no reason"
    )
    facts = P.build(make_inputs(baseline, runs=silent), draws=DRAWS).facts
    c = check_by_name(facts, "Missing multiples have a reason")
    assert c["status"] == "fail" and "ALLT" in c["detail"]


def test_a_stale_quote_is_named(baseline) -> None:
    old = edit(baseline, "price_ts", "ALLT", "2026-10-02T20:00:01+00:00")
    c = check_by_name(P.build(make_inputs(baseline, runs=old), draws=DRAWS).facts, "Price age")
    assert c["status"] == "warn" and "ALLT (15 d)" in c["detail"]


def test_an_old_run_is_a_warning_then_a_failure(baseline) -> None:
    for today, want in ((date(2026, 10, 30), "warn"), (date(2026, 12, 1), "fail")):
        c = check_by_name(P.build(make_inputs(baseline, today=today), draws=DRAWS).facts, "Consensus freshness")
        assert c["status"] == want, today


def test_the_regression_population_check_catches_a_drift(built, baseline) -> None:
    reg = copy.deepcopy(built.marts["regression"])
    assert P.check_regression_population(reg, baseline)["status"] == "pass"
    reg["latest"]["population"] = [t for t in reg["latest"]["population"] if t != "CRWD"]
    c = P.check_regression_population(reg, baseline)
    assert c["status"] == "fail" and "CRWD" in c["detail"]
    reg["latest"]["dropped"] = ["CRWD"]
    assert P.check_regression_population(reg, baseline)["status"] == "pass", "a dropped company is allowed to be absent"


def test_the_listing_table_traces_to_the_capture_table(built, baseline) -> None:
    listings = table(built, "listings")
    assert P.snapshot_trace_problems(listings, baseline) == []
    wrong = listings.with_columns(
        pl.when(pl.col("ticker") == "CRWD").then(1.0).otherwise(pl.col("price")).alias("price")
    )
    assert any("CRWD.price" in p for p in P.snapshot_trace_problems(wrong, baseline))
    extra = listings.with_columns(pl.lit("x").alias("long_business_summary"))
    assert any("long_business_summary" in p for p in P.snapshot_trace_problems(extra, baseline))
    assert P.check_snapshot_trace(extra, baseline)["status"] == "fail"


def test_no_curator_notes_reach_a_file(baseline, tmp_path) -> None:
    inputs = make_inputs(baseline)
    assert any(SENTINEL in json.dumps(r) for r in inputs.private_metrics), "the fixtures carry curator_notes"
    facts, marts = P.build(inputs, draws=DRAWS)
    assert status(facts, "Private notes never published") == "pass"
    paths = P.write(facts, marts, tmp_path / "cyber.json", tmp_path / "marts")
    assert len(paths) == len(schema.ALL_MARTS) + 1
    for p in paths:
        text = p.read_text(encoding="utf-8")
        assert SENTINEL not in text, p.name
        assert not P.has_key(json.loads(text), "curator_notes"), p.name
    leaky = copy.deepcopy(facts)
    leaky["private"]["curator_notes"] = "x"
    assert P.check_curator_notes(leaky, marts)["status"] == "fail"
    marts_leaky = {
        **marts,
        "kpis": table(P.Built(facts, marts), "kpis").with_columns(pl.lit("x").alias("curator_notes")),
    }
    assert P.check_curator_notes(facts, marts_leaky)["status"] == "fail"


def test_blocked_hosts_are_matched_on_the_host() -> None:
    assert P.blocked_host("https://www.pitchbook.com/profiles/x") == "pitchbook.com"
    assert P.blocked_host("https://app.sensortower.com/x") == "sensortower.com"
    assert P.blocked_host("https://sensor-tower.example/x") == "sensor-tower"
    assert P.blocked_host("https://news.microsoft.com/x") is None, "'ft.com' is a substring of 'microsoft.com'"
    assert P.blocked_host("https://draft.com/x") is None and P.blocked_host("https://www.ft.com/x") == "ft.com"
    assert P.blocked_host("https://www.sec.gov/Archives/x.htm") is None


def test_a_row_from_a_blocked_host_fails_the_check_even_if_it_got_past_curation(baseline) -> None:
    rows = fixture_rows("ledger_metrics.json")
    rows[0] = {**rows[0], "source_url": "https://www.crunchbase.com/organization/x"}
    facts = P.build(make_inputs(baseline, private_metrics=rows), draws=DRAWS).facts
    c = check_by_name(facts, "No row from a blocked source host")
    assert c["status"] == "fail" and "crunchbase.com" in c["detail"]


def test_the_universe_section_is_the_cross_check_and_the_excluded_list(built) -> None:
    uni = built.facts["universe"]
    (cibr,) = uni["etfs"]
    assert cibr["fund"].endswith("(CIBR)") and cibr["as_of"] == "2026-10-15"
    assert cibr["counts"]["n_holdings"] == 45 == table(built, "etf_crosscheck").height
    assert cibr["counts"]["n_holdings"] == sum(
        cibr["counts"][k] for k in ("n_in_ring_a", "n_in_ring_b", "n_in_ring_f", "n_excluded", "n_missing")
    )
    assert cibr["values"]["n_missing"]["value"] == 0 and cibr["values"]["n_missing"]["qualifier"] == "="
    assert {r["ticker"] for r in uni["excluded"] if r["kind"] == "left_out"} == {"ANET", "NTAP", "DT"}
    assert {r["ticker"] for r in uni["excluded"] if r["kind"] == "ring_f"} == {e.ticker for e in POOL if e.ring == "F"}
    assert [r["ticker"] for r in uni["excluded"]] == table(built, "excluded")["ticker"].to_list()


def test_private_companies_are_counted_by_sub_sector_in_the_layers_order(built) -> None:
    t = table(built, "layer_summary")
    by_layer = {r["layer"]: r["private_by_l2"] for r in t.iter_rows(named=True)}
    for layer, items in by_layer.items():
        order = C.LAYERS[layer]["l2"]
        assert [x["l2"] for x in items] == sorted((x["l2"] for x in items), key=order.index), layer
    fixture = fixture_rows("ledger_directory.json")
    edge = [d["l2"] for d in fixture if d["layer"] == "network_edge"]
    assert {x["l2"]: x["n"] for x in by_layer["network_edge"]} == {k: edge.count(k) for k in set(edge)}


def test_thin_multiple_coverage_is_a_warning_with_the_share(baseline) -> None:
    gone = baseline["ticker"].to_list()[:25]
    thin = baseline.with_columns(
        pl.when(pl.col("ticker").is_in(gone)).then(None).otherwise(pl.col("enterprise_value")).alias("enterprise_value")
    )
    facts = P.build(make_inputs(baseline, runs=thin), draws=DRAWS).facts
    c = check_by_name(facts, "Consensus coverage")
    kept = sum(
        1
        for r in baseline.iter_rows(named=True)
        if r["ticker"] not in gone and frozen.ev_ntm(r, date.fromisoformat(BASELINE_DAY)) is not None
    )
    assert c["status"] == "warn" and f"{kept} of 73 ring A listings have a multiple ({kept / 73:.0%}" in c["detail"]
    assert status(facts, "Missing multiples have a reason") == "pass", "each missing multiple says no_enterprise_value"
    assert facts["ev_sales"]["refused_reasons"][0]["reason"] == "no_enterprise_value"


def test_a_quote_with_no_time_stamp_is_named(baseline) -> None:
    c = P.check_price_age(
        metrics.listing_rows(edit(baseline, "price_ts", "CRWD", None), date(2026, 10, 16)), "2026-10-17"
    )
    assert c["status"] == "warn" and "no time stamp for CRWD" in c["detail"]


def test_the_makefile_has_the_publish_target() -> None:
    text = (Path(__file__).parents[3] / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^cyber-publish:.*\n\tuv run python -m pipelines\.cyber\.publish$", text, re.M)
    assert "cyber-publish" in text.splitlines()[0], "declared phony"


# --- schemas: the failure paths --------------------------------------------------------------------------------
def test_listing_schema_refuses_inconsistent_rows(built) -> None:
    listings = table(built, "listings")
    schema.validate("listings", listings)
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("listings", listings.with_columns(pl.lit("Z").alias("ring")))
    with pytest.raises(pandera.errors.SchemaError):  # a multiple that also carries a reason
        schema.validate("listings", listings.with_columns(pl.lit("currency_mismatch").alias("ev_ntm_reason")))
    with pytest.raises(pandera.errors.SchemaError):  # an unpriced listing with a measure
        schema.validate("listings", listings.with_columns(pl.col("priced").and_(pl.col("ring") == "Q").alias("priced")))
    with pytest.raises(pandera.errors.SchemaError):  # thin disagrees with n_opinions
        schema.validate("listings", listings.with_columns(pl.col("thin").not_().alias("thin")))
    with pytest.raises(pandera.errors.SchemaError):  # an infinite multiple
        schema.validate("listings", listings.with_columns(pl.col("growth") * float("inf")))


def test_targets_schema_refuses_a_wrong_upside_or_a_ring_that_is_not_outside_china(built) -> None:
    t = table(built, "targets")
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("targets", t.with_columns((pl.col("upside") + 0.5).alias("upside")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("targets", t.with_columns(pl.lit("china").alias("market_group")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("targets", t.with_columns(pl.col("pence_quote").not_().alias("pence_quote")))


def test_recommendation_schema_refuses_mixed_vocabularies_and_wrong_sums(built) -> None:
    rec = table(built, "recommendations")
    schema.validate("recommendations", rec)
    mixed = rec.with_columns(pl.when(pl.col("source") == "yahoo").then(3).otherwise(pl.col("em_buy")).alias("em_buy"))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("recommendations", mixed)
    wrong = rec.with_columns((pl.col("n_ratings") + 1).alias("n_ratings"))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("recommendations", wrong)


def test_layer_summary_and_kpi_schemas_refuse(built) -> None:
    t = table(built, "layer_summary")
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("layer_summary", t.with_columns((pl.col("n_a") + 1).alias("n_a")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("layer_summary", t.head(11))
    k = table(built, "kpis")
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("kpis", k.head(2))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("kpis", k.with_columns(pl.lit("U").alias("tag")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("kpis", k.with_columns(pl.lit("!").alias("qualifier")))
    with pytest.raises(KeyError):
        schema.validate("nope", k)


def test_the_regression_object_must_be_complete(built) -> None:
    reg = copy.deepcopy(built.marts["regression"])
    schema.validate_regression(reg)
    broken = {k: v for k, v in reg.items() if k != "premium_series"}
    with pytest.raises(ValueError, match="premium_series"):
        schema.validate_regression(broken)
    reg["baseline"] = {"regression": None}
    with pytest.raises(ValueError, match="baseline"):
        schema.validate_regression(reg)


def test_every_mart_has_a_schema_and_empty_form(built) -> None:
    assert set(schema.DTYPES) == set(schema.MARTS)
    fixed = {"kpis", "layer_summary", "scoreboard"}
    for name in set(schema.MARTS) - fixed:
        assert schema.validate(name, schema.empty(name)).height == 0, name
    for name in fixed:  # never empty: always three KPIs, one row per layer and the ten items
        with pytest.raises(pandera.errors.SchemaError):
            schema.validate(name, schema.empty(name))
    assert set(built.marts) - set(schema.MARTS) == set(schema.OBJECT_MARTS)


# --- the KPI priority list ------------------------------------------------------------------------------------------
def test_kpis_take_the_first_three_available(built) -> None:
    facts = copy.deepcopy(built.facts)
    facts["private"]["latest_valuation_max"] = None
    assert [k["id"] for k in P.kpi_frame(facts).to_dicts()] == [
        "pure_play_ev_ntm",
        "median_target_upside",
        "cn_us_multiple_ratio",
    ]
    facts["ev_sales"]["us_population_median"] = P.val(None, "x", None, None, None)
    facts["targets"]["covered"]["median_upside"] = P.val(None, "x", None, None, None)
    ids = [k["id"] for k in P.kpi_frame(facts).to_dicts()]
    assert ids == ["cn_us_multiple_ratio", "cn_median_ps_percentile", "ring_a_listings"]


# --- the write -----------------------------------------------------------------------------------------------------
def test_write_is_idempotent(built, tmp_path) -> None:
    fp, md = tmp_path / "cyber.json", tmp_path / "marts"
    P.write(copy.deepcopy(built.facts), built.marts, fp, md)
    first = {p.name: p.read_bytes() for p in [fp, *md.iterdir()]}
    assert len(first) == len(schema.ALL_MARTS) + 1
    P.write(copy.deepcopy(built.facts), built.marts, fp, md)
    assert {p.name: p.read_bytes() for p in [fp, *md.iterdir()]} == first
    on_disk = json.loads(fp.read_text(encoding="utf-8"))
    assert sum(1 for c in on_disk["checks"] if c["name"] == "Regression guard") == 1
    assert json.loads((md / "regression.json").read_text())["spec"]["verdict"] is False
    assert isinstance(json.loads((md / "listings.json").read_text()), list)


def test_a_write_that_cannot_be_serialised_writes_nothing(built, tmp_path) -> None:
    facts = copy.deepcopy(built.facts)
    facts["ev_sales"]["median"]["value"] = float("inf")
    with pytest.raises(ValueError, match="not finite"):
        P.write(facts, built.marts, tmp_path / "cyber.json", tmp_path / "marts")
    assert not list(tmp_path.iterdir()), "nothing is written when anything is refused"


def test_a_chartable_mart_with_a_row_that_may_not_be_charted_is_never_written(built, tmp_path) -> None:
    bad = table(built, "ledger_timeline").with_columns(
        pl.when(pl.col("company") == pl.col("company").first()).then(pl.lit("S")).otherwise(pl.col("tag")).alias("tag")
    )
    marts = {**built.marts, "ledger_timeline": bad}
    with pytest.raises(ValueError, match="may not be charted"):
        P.write(copy.deepcopy(built.facts), marts, tmp_path / "cyber.json", tmp_path / "marts")
    assert not list(tmp_path.iterdir())
    assert P.check_chartable(marts, table(built, "kpis"))["status"] == "fail"


def test_the_regression_residuals_and_the_listings_carry_the_same_multiple(built) -> None:
    """The baseline file is read at its market date in both, so a company's EV/NTM is one number on the page."""
    ev = {r["ticker"]: r["ev_ntm"] for r in table(built, "listings").iter_rows(named=True)}
    residuals = built.marts["regression"]["residuals"]
    assert len(residuals) == 23
    for r in residuals:
        assert r["ev_ntm"] == pytest.approx(ev[r["ticker"]], rel=1e-9), r["ticker"]
        assert r["residual"] == pytest.approx(np.log(r["ev_ntm"] / r["fitted"]))


def test_the_regression_guard_refuses_a_run_that_publishes_less_than_half(built, baseline, tmp_path) -> None:
    fp, md = tmp_path / "cyber.json", tmp_path / "marts"
    assert P.regression_guard(built.facts, fp) is None, "nothing published yet, nothing to guard"
    P.write(copy.deepcopy(built.facts), built.marts, fp, md)
    before = {p.name: p.read_bytes() for p in md.iterdir()}
    thin = P.build(make_inputs(baseline, runs=None), draws=DRAWS)
    assert thin.facts["coverage"]["n_priced"] == 0
    assert P.write(thin.facts, thin.marts, fp, md) == []
    after = json.loads(fp.read_text(encoding="utf-8"))
    guard = [c for c in after["checks"] if c["name"] == "Regression guard"]
    assert len(guard) == 1 and guard[0]["status"] == "fail" and "fewer than half" in guard[0]["detail"]
    assert after["coverage"]["n_priced"] == 73, "the old facts stand"
    assert {p.name: p.read_bytes() for p in md.iterdir()} == before, "the old marts stand"
    # and a good run afterwards replaces the failed guard with a passing one
    P.write(copy.deepcopy(built.facts), built.marts, fp, md)
    again = json.loads(fp.read_text(encoding="utf-8"))
    assert [c["status"] for c in again["checks"] if c["name"] == "Regression guard"] == ["pass"]


def test_the_guard_ignores_a_facts_file_it_cannot_read(built, tmp_path) -> None:
    fp = tmp_path / "cyber.json"
    fp.write_text("{not json", encoding="utf-8")
    assert P.regression_guard(built.facts, fp) is None
    fp.write_text(json.dumps({"coverage": {"n_priced": 73, "n_listings": 400}}), encoding="utf-8")
    c = P.regression_guard(built.facts, fp)
    assert c is not None and c["status"] == "fail", "186 listings against 400 is fewer than half"


# --- reading the disk -------------------------------------------------------------------------------------------------
def test_load_consensus_keeps_the_readable_runs_and_names_the_broken_one(baseline, tmp_path) -> None:
    baseline.write_parquet(tmp_path / "2026-10-17.parquet")
    (tmp_path / "2026-10-18.parquet").write_bytes(b"not a parquet file")
    runs, errors = P.load_consensus(tmp_path)
    assert runs is not None and runs.height == baseline.height
    assert list(errors) == ["consensus/2026-10-18.parquet"]
    assert P.load_consensus(tmp_path / "nowhere") == (None, {})


def test_load_inputs_isolates_each_source(baseline, tmp_path, monkeypatch) -> None:
    baseline.write_parquet(tmp_path / "2026-10-17.parquet")
    (tmp_path / "2026-10-18.parquet").write_bytes(b"junk")

    def fail(*a: Any, **k: Any) -> None:
        raise curation.CurationError("1 unpublishable row(s) in private_metrics.json")

    monkeypatch.setattr(P.reference, "rows", fail)
    monkeypatch.setattr(P.reference, "validate_all", lambda today=None: [{"name": "x", "status": "pass", "detail": ""}])
    inputs = P.load_inputs(
        TODAY, consensus_dir=tmp_path, history_dir=tmp_path / "none", cibr_path=tmp_path / "missing.json"
    )
    assert inputs.runs is not None and inputs.runs.height == 73 and inputs.china is None and inputs.cibr is None
    assert inputs.private_directory == inputs.private_metrics == inputs.private_events == []
    assert {"consensus/2026-10-18.parquet", "cibr", "private_metrics"} <= set(inputs.errors)
    facts = P.build(inputs, draws=DRAWS).facts
    assert status(facts, "Input: consensus/2026-10-18.parquet") == "fail" and status(facts, "Input: cibr") == "fail"


def test_main_writes_and_exits_by_the_checks(baseline, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(P, "FACTS_PATH", tmp_path / "cyber.json")
    monkeypatch.setattr(P, "MART_DIR", tmp_path / "marts")
    monkeypatch.setattr(P, "build", functools.partial(P.build, draws=DRAWS))
    monkeypatch.setattr(P, "load_inputs", lambda today=None: make_inputs(baseline))
    assert P.main(["--dry-run"]) == 0 and not (tmp_path / "cyber.json").exists()
    assert P.main([]) == 0
    assert json.loads((tmp_path / "cyber.json").read_text())["slug"] == "cybersecurity-landscape"
    monkeypatch.setattr(P, "load_inputs", lambda today=None: make_inputs(baseline, cibr=None))
    assert P.main(["--dry-run"]) == 1, "a failed check fails the run"
    monkeypatch.setattr(P, "load_inputs", lambda today=None: make_inputs(baseline, runs=None))
    assert P.main([]) == 1, "the guard refuses to replace a good page with an empty one"
    assert json.loads((tmp_path / "cyber.json").read_text())["coverage"]["n_priced"] == 73


# --- exchange rates and the enterprise value in dollars ---------------------------------------------------------------
#: A fake rate table, units of a currency per dollar: dollars, euros, pounds (a pence quote is read as pounds), yen, yuan.
FAKE_RATES = {"USD": 1.0, "EUR": 0.8, "GBP": 0.5, "JPY": 150.0, "CNY": 7.5}
FAKE_RATE_DAYS = {"USD": "2026-10-09", "EUR": "2026-10-09", "GBP": "2026-10-09", "JPY": "2026-10-08", "CNY": "2026-10-09"}


@pytest.fixture(scope="module")
def built_fx(baseline) -> P.Built:
    return P.build(make_inputs(baseline, fx_rates=FAKE_RATES, fx_dates=FAKE_RATE_DAYS), draws=DRAWS)


def test_listings_carry_the_enterprise_value_in_dollars_at_the_table_rates(built_fx, baseline) -> None:
    snap = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    major = {"GBp": "GBP"}
    got = 0
    for r in table(built_fx, "listings").iter_rows(named=True):
        s = snap.get(r["ticker"])
        currency = major.get(s["currency"], s["currency"]) if s else None
        if currency in FAKE_RATES:
            assert r["enterprise_value_usd"] == pytest.approx(s["enterprise_value"] / FAKE_RATES[currency]), r["ticker"]
            got += 1
        else:
            assert r["enterprise_value_usd"] is None, r["ticker"]
    assert got == 68, "dollars, euros, pounds, yen and yuan; krona, Swiss franc, Australian dollar and Taiwan dollar stay empty"
    by = {r["ticker"]: r for r in table(built_fx, "listings").iter_rows(named=True)}
    assert by["NCC.L"]["currency"] == "GBp" and by["NCC.L"]["enterprise_value_usd"] == pytest.approx(5.17904e8 / 0.5)
    assert by["YUBICO.ST"]["enterprise_value_usd"] is None and by["SEN.AX"]["enterprise_value_usd"] is None
    assert by["CRWD"]["enterprise_value_usd"] == by["CRWD"]["enterprise_value"], "dollars stay as they are"
    assert all(r["enterprise_value_usd"] is None for r in by.values() if r["ring"] != "A")


def test_the_dollar_column_has_a_declared_source_and_agrees_with_the_snapshot(built_fx, baseline) -> None:
    assert "enterprise_value_usd" in P.DERIVED_FIELDS and "enterprise_value_usd" in schema.LISTINGS_DTYPES
    assert P.snapshot_trace_problems(table(built_fx, "listings"), baseline) == []
    assert status(built_fx.facts, "Listing values are parsed capture fields") == "pass"
    # the multiple is not touched by the conversion: it is a ratio of two figures in one currency
    plain = {r["ticker"]: r["ev_ntm"] for r in table(P.build(make_inputs(baseline), draws=DRAWS), "listings").iter_rows(named=True)}
    assert {r["ticker"]: r["ev_ntm"] for r in table(built_fx, "listings").iter_rows(named=True)} == plain


def test_without_rates_the_dollar_column_is_empty_and_one_warn_says_why(built) -> None:
    listings = table(built, "listings")
    assert "enterprise_value_usd" in listings.columns and listings["enterprise_value_usd"].null_count() == listings.height
    c = check_by_name(built.facts, "Exchange rates")
    assert c["status"] == "warn" and "enterprise value in dollars is empty for every listing" in c["detail"]
    cov = built.facts["coverage"]
    assert (cov["fx_as_of"], cov["fx_rate_dates"], cov["n_with_enterprise_value_usd"]) == (None, {}, 0)
    assert len(built.facts["sources"]) == len(P.SOURCES), "no rates, no rate source"


def test_the_rate_date_is_in_coverage_and_the_rate_source_is_listed_last(built_fx, built) -> None:
    cov = built_fx.facts["coverage"]
    assert cov["fx_as_of"] == "2026-10-09" and cov["n_with_enterprise_value_usd"] == 68
    assert cov["fx_rate_dates"] == FAKE_RATE_DAYS, "the day of the rate of each currency a dollar value rests on"
    assert cov["priced"] == built.facts["coverage"]["priced"] and cov["n_priced"] == 73
    sources = built_fx.facts["sources"]
    assert sources[:-1] == built.facts["sources"], "the sources the page indexes keep their places"
    last = sources[-1]
    assert last["url"] == P.FX_URL and "2026-10-09" in last["name"] and "dollar" in last["name"]
    assert all(s["url"].startswith("http") for s in sources)


def test_the_rate_date_only_names_currencies_that_a_dollar_value_rests_on(baseline) -> None:
    only_euro = {"USD": 1.0, "EUR": 0.8}
    facts = P.build(make_inputs(baseline, fx_rates=only_euro, fx_dates=FAKE_RATE_DAYS), draws=DRAWS).facts
    assert facts["coverage"]["fx_rate_dates"] == {"EUR": "2026-10-09", "USD": "2026-10-09"}
    assert facts["coverage"]["n_with_enterprise_value_usd"] == 45


def test_the_exchange_rate_check_names_what_has_no_rate_and_what_is_old(built_fx, baseline) -> None:
    c = check_by_name(built_fx.facts, "Exchange rates")
    assert c["status"] == "warn" and "68 of 73 listings" in c["detail"] and "rate day 2026-10-08 to 2026-10-09" in c["detail"]
    for currency in ("SEK", "AUD", "TWD", "CHF"):
        assert currency in c["detail"], currency
    assert "YUBICO.ST" in c["detail"]
    everything = {**FAKE_RATES, "SEK": 10.0, "AUD": 1.5, "TWD": 32.0, "CHF": 0.9}
    days = dict.fromkeys(everything, "2026-10-09")
    clean = P.build(make_inputs(baseline, fx_rates=everything, fx_dates=days), draws=DRAWS).facts
    assert check_by_name(clean, "Exchange rates")["status"] == "pass"
    assert clean["coverage"]["n_with_enterprise_value_usd"] == 73
    old = {k: "2026-09-01" for k in days}
    c = check_by_name(P.build(make_inputs(baseline, fx_rates=everything, fx_dates=old), draws=DRAWS).facts, "Exchange rates")
    assert c["status"] == "warn" and f"more than {P.FX_STALE_DAYS} days older" in c["detail"]


def test_rates_with_no_run_to_convert_are_not_a_problem(baseline) -> None:
    facts = P.build(make_inputs(baseline, runs=None, fx_rates=FAKE_RATES, fx_dates=FAKE_RATE_DAYS), draws=DRAWS).facts
    c = check_by_name(facts, "Exchange rates")
    assert c["status"] == "pass" and "no listing has an enterprise value" in c["detail"]
    assert facts["coverage"]["fx_as_of"] is None and len(facts["sources"]) == len(P.SOURCES)


def test_a_missing_rate_table_is_a_warn_not_a_crash(baseline) -> None:
    built = build_and_validate(make_inputs(baseline, errors={"fx": "FileNotFoundError: gone"}))
    c = check_by_name(built.facts, "Exchange rates")
    assert c["status"] == "warn" and "gone" in c["detail"]
    assert not [x for x in built.facts["checks"] if x["name"] == "Input: fx"], "one check, and it is a warn"
    assert table(built, "listings")["enterprise_value_usd"].null_count() == 186
    assert status(built.facts, "Ring A priced") == "pass", "nothing else is lost"


def _fx_parquet(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"snapshot_ts": pl.Utf8, "date": pl.Utf8, "currency": pl.Utf8, "per_usd": pl.Float64, "series": pl.Utf8},
        orient="row",
    ).write_parquet(path)


def test_load_fx_reads_the_newest_rate_per_currency_from_the_repository_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(P.valuation_read, "VAL_DIR", tmp_path)
    ts = "2026-10-16T11:54:58+00:00"
    _fx_parquet(
        tmp_path / "fx" / "2026-10-16.parquet",
        [
            (ts, "2026-10-07", "EUR", 0.9, "x"), (ts, "2026-10-09", "EUR", 0.8, "x"),
            (ts, "2026-10-09", "GBP", 0.5, "x"), (ts, "2026-10-09", "USD", 1.0, "identity"),
            (ts, "2026-10-08", "JPY", 150.0, "x"), (ts, "2026-10-09", "JPY", None, "x"),
            (ts, "2026-10-09", "ZZZ", 0.0, "x"), (ts, "2026-10-09", "NAN", float("nan"), "x"),
        ],
    )  # fmt: skip
    rates, days, errors = P.load_fx()
    assert errors == {}
    assert rates == {"EUR": 0.8, "GBP": 0.5, "USD": 1.0, "JPY": 150.0}, "the newest non-null rate; zero and NaN are no rate"
    assert days == {"EUR": "2026-10-09", "GBP": "2026-10-09", "USD": "2026-10-09", "JPY": "2026-10-08"}
    # the older file is not read: the newest file is the whole table (valuation.read.load)
    _fx_parquet(tmp_path / "fx" / "2026-10-01.parquet", [(ts, "2026-09-30", "CHF", 0.9, "x")])
    assert "CHF" not in P.load_fx()[0]


@pytest.mark.parametrize("how", ["absent", "junk", "wrong columns", "empty"])
def test_load_fx_never_raises_and_says_why(tmp_path, monkeypatch, how) -> None:
    monkeypatch.setattr(P.valuation_read, "VAL_DIR", tmp_path)
    if how == "junk":
        (tmp_path / "fx").mkdir()
        (tmp_path / "fx" / "2026-10-16.parquet").write_bytes(b"not a parquet file")
    elif how == "wrong columns":
        (tmp_path / "fx").mkdir()
        pl.DataFrame({"a": [1]}).write_parquet(tmp_path / "fx" / "2026-10-16.parquet")
    elif how == "empty":
        _fx_parquet(tmp_path / "fx" / "2026-10-16.parquet", [])
    rates, days, errors = P.load_fx()
    assert (rates, days) == ({}, {}) and list(errors) == ["fx"] and errors["fx"]


def test_load_inputs_carries_the_rates_and_an_unreadable_table_costs_nothing_else(baseline, tmp_path, monkeypatch) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    baseline.write_parquet(runs / "2026-10-17.parquet")
    monkeypatch.setattr(P.reference, "rows", lambda kind, today=None: [])
    monkeypatch.setattr(P.reference, "validate_all", lambda today=None: [])
    monkeypatch.setattr(P.valuation_read, "VAL_DIR", tmp_path / "val")
    _fx_parquet(tmp_path / "val" / "fx" / "2026-10-16.parquet",
                [("t", "2026-10-09", "EUR", 0.8, "x"), ("t", "2026-10-09", "USD", 1.0, "identity")])  # fmt: skip
    inputs = P.load_inputs(TODAY, consensus_dir=runs, history_dir=tmp_path / "none")
    assert inputs.fx_rates == {"EUR": 0.8, "USD": 1.0} and inputs.fx_dates == {"EUR": "2026-10-09", "USD": "2026-10-09"}
    assert "fx" not in inputs.errors
    (tmp_path / "val" / "fx" / "2026-10-16.parquet").write_bytes(b"junk")
    broken = P.load_inputs(TODAY, consensus_dir=runs, history_dir=tmp_path / "none")
    assert broken.fx_rates == {} and "fx" in broken.errors
    assert broken.runs is not None and broken.runs.height == 73 and broken.cibr is not None
    facts = P.build(broken, draws=DRAWS).facts
    assert status(facts, "Exchange rates") == "warn" and facts["coverage"]["n_priced"] == 73


def test_the_listing_schema_refuses_a_dollar_value_with_nothing_behind_it(built_fx) -> None:
    t = table(built_fx, "listings")
    schema.validate("listings", t)
    ring_b = t.filter(pl.col("ring") == "B")["ticker"][0]
    with pytest.raises(pandera.errors.SchemaError):  # a dollar value for a listing with no enterprise value
        schema.validate(
            "listings",
            t.with_columns(pl.when(pl.col("ticker") == ring_b).then(1.0).otherwise(pl.col("enterprise_value_usd")).alias("enterprise_value_usd")),
        )
    with pytest.raises(pandera.errors.SchemaError):  # the wrong sign
        schema.validate("listings", t.with_columns((-pl.col("enterprise_value_usd")).alias("enterprise_value_usd")))
    with pytest.raises(pandera.errors.SchemaError):  # an infinite one
        schema.validate("listings", t.with_columns((pl.col("enterprise_value_usd") * float("inf")).alias("enterprise_value_usd")))


# --- the layer summary by market, and the scope counts that prose must not compute ------------------------------------------
def test_layer_summary_counts_private_companies_by_market_of_the_directory(built) -> None:
    t = table(built, "layer_summary")
    directory = fixture_rows("ledger_directory.json")
    for r in t.iter_rows(named=True):
        mine = [d for d in directory if d["layer"] == r["layer"]]
        assert r["n_private_overseas"] == sum(d["market"] == "overseas" for d in mine), r["layer"]
        assert r["n_private_china"] == sum(d["market"] == "china" for d in mine), r["layer"]
        assert r["n_private_overseas"] + r["n_private_china"] == r["n_private"], r["layer"]
    assert (t["n_private_china"].sum(), t["n_private_overseas"].sum()) == (1, len(directory) - 1)
    assert t.filter(pl.col("layer") == "network_edge")["n_private_china"].item() == 1
    assert t["n_private_overseas"].dtype == t["n_private_china"].dtype == pl.Int64
    by_layer = built.facts["scope"]["by_layer"]
    assert all(
        v["n_private_overseas"] + v["n_private_china"] == v["n_private"] for v in by_layer.values()
    ), "scope.by_layer repeats them"


def test_layer_summary_schema_refuses_market_counts_that_do_not_add_up(built) -> None:
    t = table(built, "layer_summary")
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("layer_summary", t.with_columns((pl.col("n_private_china") + 1).alias("n_private_china")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("layer_summary", t.with_columns((pl.col("n_private_overseas") - 1).alias("n_private_overseas")))
    with pytest.raises(pandera.errors.SchemaError):
        schema.validate("layer_summary", t.drop("n_private_china"))


def test_no_directory_means_zero_private_by_market(baseline) -> None:
    t = table(P.build(make_inputs(baseline, ledger_rows=False), draws=DRAWS), "layer_summary")
    assert t["n_private_overseas"].sum() == 0 and t["n_private_china"].sum() == 0


def test_scope_counts_the_layers_prose_would_otherwise_have_to_count(built) -> None:
    scope = built.facts["scope"]
    a_layers = {e.layer for e in POOL if e.ring == "A"}
    bare = [k for k in C.LAYERS if k not in a_layers]
    assert bare == ["ot_iot", "grc_insurance", "ai_security"], "the registered pool has no priced company there"
    assert scope["n_layers_without_ring_a"] == len(bare) == 3
    listed = {k: sum(1 for e in POOL if e.ring in ("A", "B") and e.layer == k) for k in C.LAYERS}
    private = Counter(d["layer"] for d in fixture_rows("ledger_directory.json"))
    outnumber = [k for k in C.LAYERS if private[k] > listed[k]]
    assert scope["n_layers_private_outnumber_listed"] == len(outnumber)
    assert isinstance(scope["n_layers_without_ring_a"], int) and isinstance(scope["n_layers_private_outnumber_listed"], int)


def test_private_outnumbering_is_strict_and_follows_the_directory(baseline) -> None:
    """Only a layer whose private companies are more than its ring A and ring B listings counts: add enough to one."""
    directory = fixture_rows("ledger_directory.json")
    base = P.build(make_inputs(baseline), draws=DRAWS).facts["scope"]["n_layers_private_outnumber_listed"]
    listed = sum(1 for e in POOL if e.ring in ("A", "B") and e.layer == "infra_hw")
    template = next(d for d in directory if d["layer"] == "network_edge")
    extra = [{**template, "id": f"x{i}", "company": f"Extra {i}", "layer": "infra_hw"} for i in range(listed)]
    equal = P.build(make_inputs(baseline, private_directory=directory + extra), draws=DRAWS).facts["scope"]
    assert equal["n_layers_private_outnumber_listed"] == base, "as many as the listed is not more"
    more = P.build(make_inputs(baseline, private_directory=directory + [*extra, extra[0] | {"company": "One more"}]), draws=DRAWS)
    assert more.facts["scope"]["n_layers_private_outnumber_listed"] == base + 1


# --- targets and recommendations: the counts prose would otherwise compute ------------------------------------------------
def test_targets_count_those_above_and_those_below_the_price(built, baseline) -> None:
    t = table(built, "targets")
    tg = built.facts["targets"]
    assert tg["n_above_price"] == t.filter(pl.col("target_mean") > pl.col("price")).height
    assert tg["n_below_price"] == t.filter(pl.col("target_mean") < pl.col("price")).height
    assert tg["n_above_price"] + tg["n_below_price"] == tg["n_with_target"] == 58
    assert tg["n_above_price"] == t.filter(pl.col("upside") > 0).height > tg["n_below_price"] > 0
    assert tg["n_with_target"] == t.height and tg["n_thin"] == int(t["thin"].sum()), "the existing counts stand"


def test_a_target_at_the_price_is_neither_above_nor_below(baseline) -> None:
    price = baseline.filter(pl.col("ticker") == "CRWD")["price"].item()
    flat = edit(baseline, "target_mean", "CRWD", price)
    base = P.build(make_inputs(baseline), draws=DRAWS).facts["targets"]
    got = P.build(make_inputs(baseline, runs=flat), draws=DRAWS).facts["targets"]
    assert got["n_with_target"] == 58 and got["n_above_price"] + got["n_below_price"] == 57
    assert (base["n_above_price"] - got["n_above_price"]) + (base["n_below_price"] - got["n_below_price"]) == 1


def test_a_pence_quote_is_compared_with_its_price_in_pence(built, baseline) -> None:
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    pence = [x for x in table(built, "targets").iter_rows(named=True) if x["pence_quote"]]
    assert len(pence) == 5 and all(x["target_mean"] / x["price"] < 10 for x in pence)
    above = sum(1 for x in pence if rows[x["ticker"]]["target_mean"] > rows[x["ticker"]]["price"])
    assert above == sum(1 for x in pence if x["upside"] > 0)


def test_yahoo_counts_the_listings_that_have_at_least_one_rating(built, baseline) -> None:
    y = built.facts["recommendations"]["yahoo"]
    buckets = ["rating_strong_buy", "rating_buy", "rating_hold", "rating_sell", "rating_strong_sell"]
    ring_a = {e.ticker for e in POOL if e.ring == "A" and not e.ticker.endswith((".SS", ".SZ"))}
    outside = [r for r in baseline.iter_rows(named=True) if r["ticker"] in ring_a]
    assert len(outside) == 58
    printed = sum(1 for r in outside if any(r[b] is not None for b in buckets))
    nonzero = sum(1 for r in outside if any((r[b] or 0) > 0 for b in buckets))
    assert y["n_listings"] == len(outside) == 58, "n_listings is every priced listing of the scope"
    assert y["n_listings_printing"] == printed == 57, "the pooled counts cover listings that printed any bucket"
    assert y["n_listings_with_ratings"] == nonzero == 54 and nonzero < printed < y["n_listings"]
    keys = list(y)
    assert keys.index("n_listings_printing") == keys.index("n_listings") + 1
    assert keys.index("n_listings_with_ratings") == keys.index("n_listings_printing") + 1


def test_a_listing_whose_buckets_are_all_zero_is_in_n_listings_and_not_in_n_listings_with_ratings(baseline) -> None:
    zeroed = baseline
    for b in ("rating_strong_buy", "rating_buy", "rating_hold", "rating_sell", "rating_strong_sell"):
        zeroed = edit(zeroed, b, "CRWD", 0)
    base = P.build(make_inputs(baseline), draws=DRAWS).facts["recommendations"]["yahoo"]
    got = P.build(make_inputs(baseline, runs=zeroed), draws=DRAWS).facts["recommendations"]["yahoo"]
    assert got["n_listings_printing"] == base["n_listings_printing"], "a printed zero is still a printed rating row"
    assert got["n_listings_with_ratings"] == base["n_listings_with_ratings"] - 1
    assert got["n_ratings"] < base["n_ratings"]


def test_a_yahoo_with_no_ratings_at_all_has_zero_listings_with_ratings(baseline) -> None:
    facts = P.build(make_inputs(baseline, runs=None), draws=DRAWS).facts["recommendations"]["yahoo"]
    assert (facts["n_listings"], facts["n_listings_printing"], facts["n_listings_with_ratings"]) == (0, 0, 0)


# --- the ledger section of the facts -----------------------------------------------------------------------------------------
def test_the_new_ledger_counts_reach_the_facts(built) -> None:
    p, ev = built.facts["private"], built.facts["events"]
    raw = fixture_rows("ledger_metrics.json")
    assert p["n_china_rows"] == sum(1 for r in raw if r["market"] == "china") == 3
    assert p["n_china_headline_rounds"] == 1 and p["n_comparable_layers"] == len(C.COMPARABLES_BY_LAYER)
    assert p["n_stale_ledger_rows"] == 3 and p["n_stale"] == 2
    assert sum(p["n_reported_by_group"].values()) == p["n_reported_unconfirmed"] == table(built, "reported_unconfirmed").height
    assert ev["window_end"] == TODAY.isoformat() == ev["window_to"] and ev["window_from"] == "2025-10-17"
    assert ev["latest"]["chart_metric_kind"] == "acquisition_price_sec"


def test_reported_unconfirmed_carries_the_group_beside_the_reason(built) -> None:
    rep = table(built, "reported_unconfirmed")
    assert "reason_group" in rep.columns and rep.columns.index("reason_group") == rep.columns.index("reason") + 1
    assert set(rep["reason_group"]) <= set(ledger.REASON_GROUPS)
    assert set(rep["reason"]) <= set(ledger.REASONS), "the reason is one of the closed codes"
    assert rep.columns.index("reason_label") == rep.columns.index("reason_group") + 1


def test_events_carry_the_metric_kind_of_the_drawn_price(built) -> None:
    ev = table(built, "events")
    assert {"chart_metric_kind", "deal_start"} <= set(ev.columns)
    assert ev.columns.index("chart_metric_kind") == ev.columns.index("chart_basis") + 1
    drawn = ev.filter(pl.col("chart_basis") == "sec_filing")
    assert drawn["chart_metric_kind"].to_list() == ["acquisition_price_sec"] * drawn.height
    assert ev.filter(pl.col("chart_basis").is_null())["chart_metric_kind"].null_count() == ev.filter(pl.col("chart_basis").is_null()).height


# --- one deal, one count: the Jamf shape through the whole publish ----------------------------------------------------------
def test_a_deal_entered_twice_counts_once_in_the_facts_and_the_events_mart_agrees(baseline) -> None:
    events = fixture_rows("ledger_events.json")
    plain = P.build(make_inputs(baseline, private_events=events), draws=DRAWS)
    twice = P.build(make_inputs(baseline, private_events=events + jamf_rows()), draws=DRAWS)
    assert plain.facts["events"]["acquisitions_12m"] == 3
    assert twice.facts["events"]["acquisitions_12m"] == 4, "Jamf adds one deal, not two"
    assert twice.facts["events"]["n"] == plain.facts["events"]["n"] + 3, "the three events stay in the mart"
    ev = table(twice, "events")
    lo, hi = twice.facts["events"]["window_from"], twice.facts["events"]["window_to"]
    inside = (pl.col("event_date") > lo) & (pl.col("event_date") <= hi)
    assert ev.filter(pl.col("deal_start") & inside).height == twice.facts["events"]["acquisitions_12m"]
    jamf = ev.filter(pl.col("company") == "Jamf")
    assert dict(zip(jamf["event_kind"], jamf["deal_start"], strict=True)) == {
        "acquisition_announced": True, "take_private": False, "acquisition_closed": False}  # fmt: skip
    assert status(twice.facts, "Deal count matches the events mart") == "pass"
    assert status(plain.facts, "Deal count matches the events mart") == "pass"


def test_the_deal_count_check_fails_when_the_mart_and_the_facts_disagree(built) -> None:
    ev = table(built, "events")
    summary = built.facts["events"]
    assert P.check_deal_count(ev, summary)["status"] == "pass"
    off = ev.with_columns(pl.when(pl.col("company") == "Take Co").then(False).otherwise(pl.col("deal_start")).alias("deal_start"))
    c = P.check_deal_count(off, summary)
    assert c["status"] == "fail" and "acquisitions_12m is 3" in c["detail"]
    assert P.check_deal_count(ev, {"window_from": None, "window_to": None})["status"] == "pass"


# --- nothing a reader can open carries a working note ---------------------------------------------------------------------
WORKING_NOTE_WORDS = re.compile(r"scouting|CROSSWALK|candidate (sheet|list)|lead file|curator|batch instruction", re.I)


def test_no_published_string_carries_a_working_note(built) -> None:
    facts, marts = built
    assert schema.WORKING_NOTE_WORDS.pattern == WORKING_NOTE_WORDS.pattern, "the check and this test read one rule"
    hits = list(schema.working_note_strings(facts))
    for name, mart in marts.items():
        hits += [f"{name} {h}" for h in schema.working_note_strings(mart)]
    assert hits == []
    assert status(facts, "No working notes in published text") == "pass"


def test_no_string_of_the_committed_marts_or_facts_carries_a_working_note() -> None:
    root = Path(P.FACTS_PATH).parent.parent
    files = sorted((root / "marts" / "cyber").glob("*.json")) + [P.FACTS_PATH]
    assert len(files) > 15 and all(f.exists() for f in files)
    for f in files:
        assert schema.working_note_strings(json.loads(f.read_text(encoding="utf-8"))) == [], f.name


def test_the_metric_dictionary_names_no_working_document() -> None:
    for row in etf.metric_dictionary():
        for field, text in row.items():
            assert not WORKING_NOTE_WORDS.search(str(text)), (row["metric"], field)


def test_the_check_names_a_working_note_and_a_key_that_gives_one_away(built) -> None:
    facts, marts = built
    dirty = copy.deepcopy(facts)
    dirty["private"]["note"] = "from the scouting notes"
    assert P.check_working_notes(dirty, marts)["status"] == "fail"
    assert schema.working_note_strings({"curator_notes": "x"}) == [".curator_notes"]
    kpis = table(P.Built(facts, marts), "kpis").with_columns(pl.lit("candidate list").alias("label"))
    assert P.check_working_notes(facts, {**marts, "kpis": kpis})["status"] == "fail"


# --- the checks, counted ----------------------------------------------------------------------------------------------
def test_the_checks_are_counted_and_the_warnings_split_into_waiting_and_data(built) -> None:
    facts = built.facts
    got = facts["counts"]["checks"]
    checks = facts["checks"]
    assert got["n"] == len(checks) == got["n_pass"] + got["n_warn"] + got["n_fail"]
    assert got["n_warn"] == got["n_warn_waiting"] + got["n_warn_data"]
    assert set(got["warn_waiting"]) <= P.WAITING_CHECKS and not set(got["warn_data"]) & P.WAITING_CHECKS
    assert set(got["warn_waiting"]) | set(got["warn_data"]) == {c["name"] for c in checks if c["status"] == "warn"}
    assert "Weekly series" in got["warn_waiting"] and "East Money rating rows" in got["warn_data"]
    assert P.checks_summary([])["n"] == 0


# --- deals, as the page counts them -------------------------------------------------------------------------------------
def test_the_facts_split_the_deals_the_page_counts(built) -> None:
    ev = built.facts["events"]
    assert ev["acquisitions_12m"] == ev["acquisitions_announced_12m"] + ev["acquisitions_first_recorded_at_completion_12m"]
    assert sum(ev["acquisitions_12m_by_buyer_type"].values()) == ev["acquisitions_12m"]
    assert ev["n_sec_priced_deals"] <= ev["n_sec_priced"] and ev["n_sec_priced_deals_12m"] <= ev["n_sec_priced_deals"]
    assert ev["latest"]["status_after"] in (None, *C.STATUS)
    q6 = built.facts["private"]["q6"]
    assert q6["thin_below"] == 3 and q6["old_arr_months"] == 12 and "companies" in q6
