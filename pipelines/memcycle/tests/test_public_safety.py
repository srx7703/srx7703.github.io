"""Nothing in the public memcycle tables can be a stock or benchmark price.

The repo is public and the closes behind the stock side come from vendors whose terms forbid redistribution.
So every column of every memcycle mart and of the frozen table must be on an allow-list with a declared kind,
and the values must look like that kind. A column whose name says close, open, high, low, price or level is
allowed only where it holds a month or a lead, never a level. The one exception is ``price_index``, which is
the Bank of Korea's index and may be republished with attribution.
"""

from __future__ import annotations

import csv
import json
import re

import pytest

from pipelines.memcycle import config as cfg

MONTH = re.compile(r"^\d{4}-\d{2}$")
LEVELISH = re.compile(r"(close|open|high|low|price|level|rebased)", re.I)

# column -> kind. month: YYYY-MM; lead: signed whole months; ratio: a multiple or a return; flag; text; count
ALLOW: dict[str, str] = {
    # identifiers and text
    "product": "text", "company": "text", "label": "text", "group": "text", "market": "text", "currency": "text",
    "status": "text", "window": "text", "bench1": "text", "bench2": "text", "excluded_because": "text",
    # months
    "first_month": "month", "last_month": "month", "price_peak": "month", "stock_peak": "month",
    "stock_peak_high_month": "month", "stock_trough": "month", "upleg_low": "month", "upleg_start": "month",
    "upleg_low_hl_month": "month", "stock_peak_month": "month", "stock_high_month": "month",
    "stock_trough_month": "month", "upleg_low_month": "month",
    # leads and counts
    "lead_months": "lead", "lead_months_high": "lead", "trough_lead_months": "lead", "lead_months_intramonth": "lead",
    "months_low_to_peak": "lead",
    # ratios
    "drawdown_to_trough": "ratio", "upleg_multiple": "ratio", "upleg_multiple_hl": "ratio",
    "upleg_multiple_usd": "ratio", "upleg_multiple_usd_series": "ratio", "bench1_multiple": "ratio",
    "excess1": "ratio", "bench2_multiple": "ratio", "excess2": "ratio", "multiple": "ratio",
    "multiple_intramonth": "ratio", "multiple_usd": "ratio",
    # flags
    "dram_maker": "flag", "price_peak_confirmed": "flag", "complete": "flag", "upleg_truncated_by_listing": "flag",
    "delisted_or_out_of_scope": "flag", "partial_latest_month": "flag", "in_main": "flag", "in_dram_makers": "flag",
    "in_diversified": "flag", "in_nand_panel": "flag", "truncated_by_listing": "flag", "spans_2000_bubble": "flag",
}


def _ok(kind: str, v) -> bool:
    if v in (None, ""):
        return True
    if kind == "month":
        return bool(MONTH.match(str(v)))
    if kind == "lead":
        return re.fullmatch(r"-?\d+", str(v)) is not None and abs(int(v)) <= 240
    if kind == "ratio":
        return -1.0 <= float(v) <= 1000.0
    if kind == "flag":
        return str(v) in ("True", "False", "true", "false")
    return isinstance(v, str)


def _check_table(name: str, rows: list[dict]):
    assert rows, name
    for col in rows[0]:
        assert col in ALLOW, f"{name}: column {col!r} is not on the public allow-list"
        kind = ALLOW[col]
        if LEVELISH.search(col):
            assert kind in ("month", "lead", "ratio", "flag", "text"), f"{name}: {col} could hold a level"
            assert kind != "text" or col in ("bench1", "bench2"), f"{name}: {col}"
        bad = [r[col] for r in rows if not _ok(kind, r[col] if not isinstance(r[col], bool) else str(r[col]))]
        assert not bad, f"{name}: {col} ({kind}) has values that do not look like one: {bad[:3]}"


def test_no_close_column_in_the_frozen_table():
    with cfg.FROZEN_PATH.open() as f:
        header = next(csv.reader(f))
    assert not set(header) & set(cfg.CLOSE_COLUMNS)
    assert not [c for c in header if re.search(r"close|rebased|level", c)]


def test_frozen_table_columns_are_allow_listed():
    with cfg.FROZEN_PATH.open() as f:
        _check_table("frozen", list(csv.DictReader(f)))


@pytest.mark.parametrize("name", ["leads", "multiples"])
def test_stock_marts_are_allow_listed(name):
    _check_table(name, json.loads((cfg.MART_DIR / f"{name}.json").read_text()))


def test_price_index_is_ecos_only():
    rows = json.loads((cfg.MART_DIR / "price_index.json").read_text())
    assert set(rows[0]) == {"product", "month", "value", "preliminary", "nor_era"}
    assert {r["product"] for r in rows} == {"DRAM", "NAND"}


def test_no_other_public_file_carries_stock_levels():
    """turns, calendar, evaluation and facts hold no numeric field named like a level."""
    def walk(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                if LEVELISH.search(k) and isinstance(v, int | float) and not isinstance(v, bool):
                    yield f"{path}.{k}"
                yield from walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                yield from walk(v, f"{path}[{i}]")

    for name in ("turns", "calendar", "evaluation"):
        assert not list(walk(json.loads((cfg.MART_DIR / f"{name}.json").read_text()))), name
    assert not list(walk(json.loads(cfg.FACTS_PATH.read_text())))


def test_no_local_closes_anywhere_in_the_repo_tree():
    """No file under data/ for this project looks like a monthly close series."""
    for p in (cfg.CASE_DIR.parent.parent).rglob("*memcycle*/**/*"):
        if p.is_file() and p.suffix == ".csv":
            with p.open() as f:
                header = next(csv.reader(f), [])
            assert "close" not in header, p
