"""ECOS 402Y016 parsing: the registration pull, and the ways a raw response can be wrong."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import polars as pl
import pytest

from pipelines.memcycle import config as cfg
from pipelines.memcycle import ingest
from pipelines.memcycle import transform as T

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture(scope="module")
def snap() -> pl.DataFrame:
    return ingest.snapshot()


def test_six_series_full_span(snap):
    per = snap.group_by("item_code", "basis").agg(pl.len().alias("n"), pl.col("month").min().alias("first"),
                                                  pl.col("month").max().alias("last"))
    assert per.height == 6
    first = {cfg.DRAM_ITEM: "1971-01", cfg.FLASH_ITEM: "2000-01"}  # the flash item starts in 2000 in ECOS
    for r in per.iter_rows(named=True):
        assert r["first"] == first[r["item_code"]] and r["last"] == cfg.DATA_END
        # no gaps: one row per month of the span
        assert r["n"] == T.months_between(r["first"], r["last"]) + 1


def test_no_duplicates_positive_values_one_unit(snap):
    assert snap.height == snap.unique(subset=["item_code", "basis", "month"]).height
    assert snap["value"].min() > 0
    assert snap["unit"].unique().to_list() == ["2020=100"]


def test_only_the_latest_month_is_preliminary(snap):
    prelim = snap.filter(pl.col("preliminary"))
    assert prelim.height == 6 and prelim["month"].unique().to_list() == [cfg.DATA_END]


def test_snapshot_equals_the_research_normalisation(snap):
    """Row for row the same as the normalised CSV the research code read (values, names, weights, timestamps)."""
    with (GOLDEN / "ecos_402Y016.csv").open() as f:
        gold = {(r["item_code"], r["basis"], r["month"]): r for r in csv.DictReader(f)}
    assert len(gold) == snap.height
    for r in snap.iter_rows(named=True):
        g = gold[(r["item_code"], r["basis"], r["month"])]
        assert float(g["value"]) == r["value"]
        assert g["item_name"] == r["item_name"] and float(g["weight"]) == r["weight"]
        assert g["retrieved_at"] == r["retrieved_at"]


def _line(url: str, rows=None, response=None) -> str:
    resp = response if response is not None else {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}
    return json.dumps({"url": url, "fetched_at": "2026-09-28T20:16:55Z", "response": resp})


def _row(month: str, value: str, item: str = cfg.DRAM_ITEM, basis: str = "C") -> dict:
    return {"STAT_CODE": "402Y016", "ITEM_CODE1": item, "ITEM_NAME1": "DRAM", "ITEM_CODE2": basis,
            "UNIT_NAME": "2020=100", "WGT": "103.4", "TIME": month.replace("-", ""), "DATA_VALUE": value}


URL = "https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/10/402Y016/M/202601/202608/30911201AA/C"
PROBE = "https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/1/402Y016/M/196001/202612/30911201AA/C"


def test_rejects_html():
    with pytest.raises(ingest.EcosError, match="HTML"):
        ingest.parse_raw([_line(URL, response="<!DOCTYPE html><html>blocked</html>")])


def test_rejects_ecos_error_payload():
    err = {"RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"}}
    with pytest.raises(ingest.EcosError, match="INFO-200"):
        ingest.parse_raw([_line(URL, response=err)])


def test_rejects_conflicting_duplicate():
    with pytest.raises(ingest.EcosError, match="conflicting"):
        ingest.parse_raw([_line(URL, [_row("2026-01", "100.0")]), _line(URL, [_row("2026-01", "101.0")])])


def test_rejects_non_monthly_time():
    with pytest.raises(ingest.EcosError, match="monthly"):
        ingest.parse_raw([_line(URL, [{**_row("2026-01", "1"), "TIME": "2026Q1"}])])


def test_probe_rows_are_collapsed_into_pages():
    rows = ingest.parse_raw([_line(PROBE, [_row("2026-01", "100.0")]),
                             _line(URL, [_row("2026-01", "100.0"), _row("2026-02", "110.0")])])
    assert [r["month"] for r in rows] == ["2026-01", "2026-02"]
    assert [r["preliminary"] for r in rows] == [False, True]


def test_unregistered_series_are_ignored():
    rows = ingest.parse_raw([_line(URL, [_row("2026-01", "1.0", item="99999999AA")])])
    assert rows == []
