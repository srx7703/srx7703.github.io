"""The port reproduces the local research (``广告行业研究``, report v0 and data/*.json of 2026-10-21).

Built from the committed per-filing extracts, so it runs in CI. The pinned numbers are the research outputs, so the
tests that pin them read the panel cut at the research's last quarter: the weekday refresh keeps adding filings (Meta's
2026Q3 10-Q added a 21st volume-price quarter) and must not move them. The one deliberate difference is the 2022Q2 Google cost-per-click cell, where the research parser read the six-month column
because Alphabet printed the quarter's zero as a dash (see test_parsers and docs/admarket_build_notes.md).
"""

from __future__ import annotations

import csv
import json

import pytest

from pipelines.admarket import config as cfg
from pipelines.admarket import metrics as M
from pipelines.admarket import prereg
from pipelines.admarket import transform as T
from pipelines.admarket.ingest import load_filings


@pytest.fixture(scope="module")
def filings():
    return load_filings()


@pytest.fixture(scope="module")
def series(filings):
    return T.all_series(filings)


RESEARCH_LAST = "2026Q2"  # the research data ends here


@pytest.fixture(scope="module")
def research_series(series):
    return {name: {q: v for q, v in s.items() if q <= RESEARCH_LAST} for name, s in series.items()}


def test_panel_matches_the_registered_snapshot_cell_for_cell(series):
    """Every cell of data/reference/ad_market (the CSV whose hash the forecast registration records)."""
    with cfg.REFERENCE_CSV.open() as f:
        rows = list(csv.reader(f))
    head = rows[0][1:]
    n = 0
    for r in rows[1:]:
        for q, v in zip(head, r[1:], strict=True):
            if v:
                assert f"{series[r[0]][q] / 1e9:.3f}" == v, (r[0], q)
                n += 1
    assert n == 384


def test_video_subset_shares_and_growth(research_series):
    p = M.video_panel(research_series)
    assert p["last_quarter"] == "2026Q2"
    last = {k: round(v * 100, 1) for k, v in p["share_last"].items()}
    assert (last["Google"], last["Meta"], last["Amazon"], last["AppLovin"]) == (47.8, 34.7, 11.6, 1.1)
    assert round(p["share_first"]["Google"] * 100, 1) == 55.6
    assert round(p["share_video_end"]["Google"] * 100, 1) == 49.2
    assert round(p["share_video_end"]["Meta"] * 100, 1) == 33.2
    assert round(p["cagr"] * 100, 1) == 15.4
    assert round(p["ttm_yoy"] * 100, 1) == 19.9
    assert round(p["video_window"]["cagr_correct"] * 100, 1) == 15.0
    assert round(p["video_window"]["cagr_four_years"] * 100, 1) == 17.1
    assert (round(p["total_first_bn"], 1), round(p["total_video_end_bn"], 1)) == (80.4, 150.9)
    assert p["meta_2022_low"]["quarter"] == "2022Q3" and round(p["meta_2022_low"]["share"] * 100, 1) == 28.2
    assert p["applovin_passes_snap"] == "2026Q1"
    assert round(p["yoy_last"]["TradeDesk"] * 100) == 3 and round(p["yoy_video_end"]["TradeDesk"] * 100) == 18


def test_q2_att_as_registered(series):
    r = M.q2(series)
    assert round(r["affected"]["before"] * 100, 1) == 47.3 and round(r["affected"]["after"] * 100, 1) == 30.2
    assert round(r["control"]["before"] * 100, 1) == 17.0 and round(r["control"]["after"] * 100, 1) == 21.6
    assert round(r["gap"] * 100, 1) == 21.7 and r["verdict"] == "holds"


def test_q3_open_web_as_registered(series):
    r = M.q3(series)
    assert round(r["open"] * 100, 1) == -0.8 and round(r["walled"] * 100, 1) == 18.0
    assert round(r["walled_ex_reddit"] * 100, 1) == 17.8
    assert round(r["gap"] * 100, 1) == 18.7 and r["verdict"] == "holds"
    d = r["descriptive"]
    assert round(d["open_ex_network"] * 100, 1) == 15.7
    assert round(r["members"]["google_network"] * 100, 1) == -2.4
    assert round(d["network_ttm_bn"]) == 29 and round(d["others_ttm_bn"]) == 4


RESEARCH_Q4 = {"meta_ads": (20, 0.8, 0.4), "google_search_clicks": (20, 3.1, 0.99), "google_network": (17, 3.3, 1.11)}


def test_q4_volume_price_check(filings, research_series):
    rows = [x for x in T.volume_price_rows(filings) if x["quarter"] <= RESEARCH_LAST]
    r = M.q4(research_series, rows)
    for s, (n, mx, mean) in RESEARCH_Q4.items():
        v = r["verdict"][s]
        assert (v["n"], v["max_abs_gap_pp"]) == (n, mx), s
        if s == "google_search_clicks":
            assert v["mean_abs_gap_pp"] == 0.92   # research 0.99: the 2022Q2 dash cell, now read as zero
        else:
            assert v["mean_abs_gap_pp"] == mean, s
    assert [v["usable"] for v in r["verdict"].values()] == [True, False, False]
    fixed = next(x for x in r["rows"] if x["series"] == "google_search_clicks" and x["quarter"] == "2022Q2")
    assert (fixed["price"], fixed["implied"], fixed["gap_pp"]) == (0, 12.0, -1.5)


def test_rule_a_recomputes_every_registered_number(series):
    rc = M.recompute_matches(M.rule_a(series), prereg.load())
    assert rc == {"cells": 90, "mismatches": 0, "bad": [], "band_ok": True}


def test_published_facts_agree_with_the_recomputation(series):
    """The committed facts file was built from the committed extracts (no stale publish)."""
    facts = json.loads(cfg.FACTS_PATH.read_text())
    assert facts["q2"]["gap"] == pytest.approx(M.q2(series)["gap"])
    assert facts["q3"]["open"] == pytest.approx(M.q3(series)["open"])
    assert facts["panel"]["share_last"] == pytest.approx(M.video_panel(series)["share_last"])
    assert all(c["status"] != "fail" for c in facts["checks"])
