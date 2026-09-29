"""The published tables reconcile with the research outputs at registration.

CI tier: the committed marts, facts and frozen table equal the golden fixtures, and the four Q3 groups are
the registered result.

Local tier (skipped unless MEMCYCLE_INPUTS points at the owner's local closes): ``freeze.py`` rebuilds the
frozen table byte for byte from those closes, and every manifest hash matches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipelines.memcycle import config as cfg
from pipelines.memcycle import publish

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
MEMCYCLE_INPUTS = os.environ.get("MEMCYCLE_INPUTS")


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads((GOLDEN / "summary.json").read_text())


@pytest.fixture(scope="module")
def built() -> dict:
    return publish.build()


def _load(name: str):
    return json.loads((cfg.MART_DIR / f"{name}.json").read_text())


def test_frozen_table_is_the_golden_export():
    assert cfg.FROZEN_PATH.read_bytes() == (GOLDEN / "company_cycles_derived.csv").read_bytes()


@pytest.mark.parametrize(("group", "n", "share", "median"), [
    ("main_pure", 33, 0.424, -2),
    ("sens_dram_makers_only", 30, 0.433, -1),
    ("diversified_dram", 19, 0.368, -5),
    ("nand_panel", 20, 0.15, -8),
])
def test_registered_q3_groups(built, group, n, share, median):
    g = built["facts"]["q3"][group]
    assert (g["n"], g["share_lead_ge1"], g["median_lead"], g["verdict"]) == (n, share, median, "falsified")


def test_q3_equals_golden_including_every_lead(built, golden):
    for k, v in golden["q3"].items():
        assert {kk: vv for kk, vv in built["facts"]["q3"][k].items() if kk != "label"} == v


def test_calendar_equals_golden(built, golden):
    for product, cal in golden["calendar"].items():
        turns = [[t["kind"], t["month"], t["confirmed"]] for t in built["turns"]
                 if t["product"] == product and t["basis"] == "C" and t["amp_threshold"] == cfg.AMP_MAIN]
        assert turns == cal["turns"]
    sens = {(s["product"], s["basis"], s["amp"]): s["turns"] for s in golden["sensitivity"]}
    for (product, basis, amp), turns in sens.items():
        mine = [[t["kind"], t["month"], t["confirmed"]] for t in built["turns"]
                if t["product"] == product and t["basis"] == basis and t["amp_threshold"] == amp]
        assert mine == turns


def test_committed_marts_are_current(built):
    """The committed marts are what publish.py builds now (rerun `python -m pipelines.memcycle.publish`)."""
    for name in ("price_index", "turns", "calendar", "leads", "multiples", "evaluation"):
        assert _load(name) == json.loads(json.dumps(built[name])), name
    facts = json.loads(cfg.FACTS_PATH.read_text())
    mine = json.loads(json.dumps(built["facts"]))
    for k in ("generated_at", "last_updated"):
        facts.pop(k), mine.pop(k)
    assert facts == mine


def test_regression_guard_refuses_a_moved_peak(built):
    rows = publish.read_frozen()
    moved = [{**r, "price_peak": "2018-06"} if r["price_peak"] == "2018-07" else r for r in rows]
    runs = publish.runs(built["snapshot"])
    versions = json.loads(cfg.Q3_VERSIONS_PATH.read_text())
    with pytest.raises(publish.RegressionError):
        publish.regression_guard(runs, moved, publish.evaluate.q3(rows), versions)


def test_regression_guard_refuses_a_changed_verdict(built):
    rows = publish.read_frozen()
    runs = publish.runs(built["snapshot"])
    versions = json.loads(cfg.Q3_VERSIONS_PATH.read_text())
    q3 = publish.evaluate.q3(rows)
    q3["main_pure"] = {**q3["main_pure"], "n": 34}
    with pytest.raises(publish.RegressionError):
        publish.regression_guard(runs, rows, q3, versions)


# ---------------------------------------------------------------------------------------------
# local tier
# ---------------------------------------------------------------------------------------------


@pytest.mark.skipif(not MEMCYCLE_INPUTS, reason="needs the owner's local closes (MEMCYCLE_INPUTS)")
def test_freeze_reproduces_the_frozen_table_byte_for_byte():
    from pipelines.memcycle import freeze

    rows, missing = freeze.company_cycles(freeze.Inputs(Path(MEMCYCLE_INPUTS)))
    assert freeze.frozen_bytes(rows) == cfg.FROZEN_PATH.read_bytes()
    assert missing == [f"6502 ({next(c.local_file for c in cfg.COMPANIES if c.key == '6502')})"]


@pytest.mark.skipif(not MEMCYCLE_INPUTS, reason="needs the owner's local closes (MEMCYCLE_INPUTS)")
def test_manifest_hashes_match_the_local_inputs():
    from pipelines.memcycle import freeze

    old = json.loads(cfg.MANIFEST_PATH.read_text())
    new = freeze.manifest(freeze.Inputs(Path(MEMCYCLE_INPUTS)), old["research_repo_head"],
                          old["research_scripts_sha256"])
    assert new["inputs"] == old["inputs"]


def test_local_verification_covers_the_committed_frozen_files():
    """The owner's recorded local run binds to these exact files; a changed frozen table makes it stale."""
    v = publish.local_verification()
    assert v is not None and v["result"] == "pass" and v["current"]
