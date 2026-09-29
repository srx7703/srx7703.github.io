"""Section G of docs/EVALUATION_PLAN.md says what the code does, and names the registration it translates.

A threshold that lives only in ``config.py`` sits outside the blob hash the page prints, so every registered
constant must appear in section G's constant list with the same value. Section G must also name the five
blob ids of the Chinese pre-registration, make no "blind" claim for Q3 or Q7, and the vendored file must be
the last registered blob.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from pipelines.common.storage import REPO_ROOT
from pipelines.memcycle import config as cfg

PLAN = REPO_ROOT / "docs" / "EVALUATION_PLAN.md"


@pytest.fixture(scope="module")
def section_g() -> str:
    text = PLAN.read_text(encoding="utf-8")
    m = re.search(r"^## Memory-chip price cycles \(project G\).*?(?=^## |\Z)", text, flags=re.S | re.M)
    assert m, "section G is missing from docs/EVALUATION_PLAN.md"
    return m.group(0)


@pytest.fixture(scope="module")
def constants(section_g) -> dict[str, str]:
    return dict(re.findall(r"^- `([A-Z0-9_]+) = (.+?)`$", section_g, flags=re.M))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.2f}" if v else "0.0"
    if isinstance(v, tuple):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    return str(v)


EXPECTED = {
    "HALF_WINDOW": _fmt(cfg.HALF_WINDOW),
    "MIN_PHASE": _fmt(cfg.MIN_PHASE),
    "MIN_CYCLE": _fmt(cfg.MIN_CYCLE),
    "AMP_MAIN": _fmt(cfg.AMP_MAIN),
    "AMP_SENS": _fmt(cfg.AMP_SENS),
    "SERIES_START": f"{{DRAM: {cfg.SERIES_START[cfg.DRAM_ITEM]}, flash: {cfg.SERIES_START[cfg.FLASH_ITEM]}}}",
    "LEAD_MIN": _fmt(cfg.LEAD_MIN),
    "Q3_HOLDS_SHARE": _fmt(cfg.Q3_HOLDS_SHARE),
    "Q3_MEDIAN_BAND": _fmt(cfg.Q3_MEDIAN_BAND),
    "Q3_FALSIFY_SHARE": _fmt(cfg.Q3_FALSIFY_SHARE),
    "INFINEON_LAST_MONTH": cfg.INFINEON_LAST_MONTH,
    "Q6_PERCENTILES": f"{cfg.Q6_PERCENTILES[0]}..{cfg.Q6_PERCENTILES[-1]} step "
                      f"{cfg.Q6_PERCENTILES[1] - cfg.Q6_PERCENTILES[0]}",
    "Q6_TRAIN_END": cfg.Q6_TRAIN_END,
    "Q6_TEST_START": cfg.Q6_TEST_START,
    "Q6_HORIZON_QUARTERS": _fmt(cfg.Q6_HORIZON_QUARTERS),
    "Q6_PRECISION_MARGIN": _fmt(cfg.Q6_PRECISION_MARGIN),
    "Q6_MIN_RECALL": _fmt(cfg.Q6_MIN_RECALL),
    "Q7_SPLIT_YEAR": _fmt(cfg.Q7_SPLIT_YEAR),
    "Q8_CONFIRM_MONTHS": _fmt(cfg.Q8_CONFIRM_MONTHS),
    "Q8_CONFIRM_FALL": _fmt(cfg.Q8_CONFIRM_FALL),
    "Q8_WINDOW_MONTHS": _fmt(cfg.Q8_WINDOW_MONTHS),
    "Q8_DEADLINE": cfg.Q8_DEADLINE,
}


def test_every_registered_constant_is_in_section_g(constants):
    missing = sorted(set(EXPECTED) - set(constants))
    assert not missing, f"constants in config.py but not in section G: {missing}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_constant_values_agree(constants, name):
    assert constants.get(name) == EXPECTED[name], f"{name}: plan says {constants.get(name)!r}, code {EXPECTED[name]!r}"


def test_section_g_lists_no_constant_the_code_lacks(constants):
    assert set(constants) <= set(EXPECTED)


def test_item_codes_and_table_are_named(section_g):
    for s in (cfg.ECOS_TABLE, cfg.DRAM_ITEM, cfg.FLASH_ITEM):
        assert f"`{s}`" in section_g


def test_section_g_names_the_five_blobs_in_order(section_g):
    blobs = re.findall(r"^\| `([0-9a-f]{8})` \|", section_g, flags=re.M)
    assert blobs == [b for b, _, _ in cfg.PREREG_BLOBS]


def test_no_blind_claim_for_q3_or_q7(section_g):
    """Q3 was computed, then its roster corrected, after results were seen; Q7's price data had been seen."""
    for sentence in re.split(r"(?<=[.;:])\s+", section_g.replace("\n", " ")):
        if "blind" in sentence.lower() and re.search(r"\bQ[37]\b", sentence):
            assert "not blind" in sentence.lower(), f"blind claim: {sentence!r}"
    assert re.search(r"Q7 is not blind", section_g)


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_vendored_prereg_is_the_last_registered_blob():
    blob = _git_blob(cfg.PREREG_PATH.read_bytes())
    assert blob.startswith(cfg.PREREG_BLOBS[-1][0]), "the Chinese pre-registration was edited in this repo"


def test_prereg_contains_the_registered_rule_values():
    """The Chinese original states the same thresholds (spot checks of the numbers in §1-§2)."""
    text = cfg.PREREG_PATH.read_text(encoding="utf-8")
    for needle in ("前后各 6 个月", "至少 6 个月", "至少 15 个月", "+20%", "30% 和 0%", "至少 60%",
                   "3 到 9 个月", "不到 50%", "1995 年 1 月", "2000 年 1 月", "2028 年 12 月 31 日"):
        assert needle in text, needle
