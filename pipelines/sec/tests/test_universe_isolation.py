"""C4 golden test: the SaaS benchmark is unchanged by the payments adaptation.

Rebuilds the SaaS marts from the committed snapshots into a tmp dir and compares them with the committed
marts byte for byte. data/facts/saas.json is compared as JSON with exactly two fields excluded, because they
are functions of the wall clock, not of the data: `generated_at` (build time) and the detail text of the
"Freshness" check ("latest refresh N h ago"). Everything else in it must be equal.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import polars as pl
import pytest

from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR
from pipelines.payments.config import UNIVERSE
from pipelines.sec import transform
from pipelines.sec.config import PAYMENTS_FILERS, TICKERS, UNIVERSES

MART_FILES = ["saas_quarterly.parquet", "saas_ttm.json", "saas_latest.json", "saas_coverage.json"]

pytestmark = pytest.mark.skipif(
    not (SNAP_DIR / "sec" / "companies.json").exists(), reason="committed SEC snapshots not present"
)


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _facts_without_clock(path) -> dict:
    facts = json.loads(path.read_text())
    facts.pop("generated_at")
    for c in facts["checks"]:
        if c["name"] == "Freshness":
            c.pop("detail")
    return facts


def _assert_matches_committed(marts, facts):
    for name in MART_FILES:
        assert _sha(marts / "sec" / name) == _sha(MARTS_DIR / "sec" / name), f"{name} differs from committed"
    assert _facts_without_clock(facts / "saas.json") == _facts_without_clock(FACTS_DIR / "saas.json")


def test_saas_rebuild_is_byte_identical(tmp_path):
    marts, facts = tmp_path / "marts", tmp_path / "facts"
    transform.build(marts_dir=marts, facts_out=facts)
    _assert_matches_committed(marts, facts)


def test_saas_build_ignores_parquets_outside_its_universe(tmp_path):
    """A stray facts file (another universe, an ad-hoc --tickers run) must not change the benchmark."""
    snap = tmp_path / "snap"
    shutil.copytree(SNAP_DIR / "sec", snap / "sec")
    stray = pl.read_parquet(snap / "sec" / "facts" / "BILL.parquet").with_columns(pl.lit("KLAR").alias("ticker"))
    stray.write_parquet(snap / "sec" / "facts" / "KLAR.parquet")
    marts, facts = tmp_path / "marts", tmp_path / "facts"
    transform.build(snap_dir=snap, marts_dir=marts, facts_out=facts)
    _assert_matches_committed(marts, facts)


def test_universes_are_separate():
    saas, pay = UNIVERSES["saas"], UNIVERSES["payments"]
    assert saas.tickers == TICKERS
    assert saas.snap_subdir == "sec" and pay.snap_subdir == "sec_payments"
    assert "20-F" not in saas.forms and "20-F" in pay.forms and "6-K" in pay.forms
    assert not saas.frames_fallback and pay.frames_fallback
    assert not saas.require_user_agent and pay.require_user_agent
    assert len(set(pay.tickers)) == len(pay.tickers)


def test_payments_filers_belong_to_the_registered_universe():
    registered = {c for layer in UNIVERSE.values() for group in layer.values() for c in group}
    for f in PAYMENTS_FILERS:
        assert f["company"] in registered, f
    assert "Adyen" not in {f["company"] for f in PAYMENTS_FILERS}  # not an SEC registrant
