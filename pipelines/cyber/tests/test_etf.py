"""The CIBR cross-check, the excluded table and the metric dictionary.

The cross-check is tested on small synthetic pools for every placement and every failure path, and on the committed
files for the one fact that matters: no CIBR holding is missing from the pool. The dictionary is tested against the
config strings it must embed and against section I for the few definitions config does not hold.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

import pandera.errors as pe
import polars as pl
import pytest

from pipelines.cyber import config as C
from pipelines.cyber import etf
from pipelines.cyber import frozen as F
from pipelines.cyber.pool import BY_TICKER, POOL, Entry
from pipelines.valuation.config import EXCLUDED

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "docs" / "EVALUATION_PLAN.md"
SPEC = ROOT / "docs" / "cyber" / "PIPELINE_SPEC.md"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _plan_section() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## Cybersecurity (project I)")
    nxt = text.find("\n## ", start + 10)
    return _flat(text[start : nxt if nxt != -1 else None])


def _entry(ticker: str, ring: str = "A", **kw) -> Entry:
    base = {"name": f"{ticker} Inc", "country": "US", "layer": "network_edge", "purity": "high", "status": "listed"}
    return Entry(ticker=ticker, ring=ring, **(base | kw))


def _excluded(ticker: str, track: str = "cyber", reason: str = "a switching vendor") -> dict:
    return {"ticker": ticker, "name": f"{ticker} Corp", "track": track, "reason": reason}


@pytest.fixture
def pool() -> list[Entry]:
    return [
        _entry("AAA", "A", layer="identity", purity="main"),
        _entry("A2", "A"),
        _entry("A3", "A", status="pending_acquisition"),
        _entry("BBB", "B", layer="services", purity="partial"),
        _entry("FFF", "F", status="acquired", status_date="2026-02-11", note="Bought by AAA"),
    ]


@pytest.fixture
def excluded() -> list[dict]:
    return [_excluded("XXX"), _excluded("OPT", track="optical", reason="private")]


@pytest.fixture
def cibr() -> etf.CibrHoldings:
    names = {"AAA": "AAA Inc (Class A)", "BBB": "BBB Ltd", "FFF": "FFF Group", "XXX": "XXX Corp", "ZZZ": "ZZZ plc"}
    return etf.CibrHoldings(
        holdings=tuple(etf.Holding(t, n) for t, n in names.items()),
        fund="Test Cyber ETF",
        index="Test Index",
        as_of="2026-10-15",
        source_url="https://example.com/holdings",
    )


def _by(cc: etf.Crosscheck) -> dict[str, dict]:
    return {r["ticker"]: r for r in cc.table.iter_rows(named=True)}


# --- placement ------------------------------------------------------------------------------------------------
def test_every_holding_is_placed_in_one_of_five_ways(cibr, pool, excluded) -> None:
    got = _by(etf.build(cibr, pool, excluded))
    assert {t: r["status"] for t, r in got.items()} == {
        "AAA": "ring_a", "BBB": "ring_b", "FFF": "ring_f", "XXX": "excluded", "ZZZ": "missing",
    }  # fmt: skip
    a = got["AAA"]
    assert (a["in_pool"], a["ring"], a["layer"], a["purity"], a["excluded_reason"], a["company_status"]) == (
        True, "A", "identity", "main", None, "listed",
    )  # fmt: skip
    x = got["XXX"]
    assert (x["in_pool"], x["ring"], x["layer"], x["purity"]) == (False, None, None, None)
    assert x["excluded_reason"] == "a switching vendor" and x["company_status"] is None
    z = got["ZZZ"]
    assert (z["in_pool"], z["ring"], z["excluded_reason"], z["company_status"]) == (False, None, None, None)


def test_the_company_status_travels_with_a_pool_row(cibr, pool, excluded) -> None:
    holdings = [etf.Holding("A3", "A3 Inc")]
    got = _by(etf.build(holdings, pool, excluded))
    assert got["A3"]["status"] == "ring_a" and got["A3"]["company_status"] == "pending_acquisition"


def test_the_table_has_the_agreed_columns_in_order_and_follows_the_holdings_order(cibr, pool, excluded) -> None:
    cc = etf.build(cibr, pool, excluded)
    assert cc.table.columns == [
        "ticker", "name", "in_pool", "ring", "layer", "purity", "excluded_reason", "status", "company_status",
    ]  # fmt: skip
    assert cc.table["ticker"].to_list() == [h.ticker for h in cibr.holdings]
    assert cc.table.schema == pl.Schema(etf.TABLE_DTYPES)


def test_the_fund_name_is_kept_and_a_tickers_only_call_falls_back_to_the_pool_name(cibr, pool, excluded) -> None:
    assert _by(etf.build(cibr, pool, excluded))["AAA"]["name"] == "AAA Inc (Class A)"
    got = _by(etf.build(["AAA", "XXX", "ZZZ"], pool, excluded))
    assert got["AAA"]["name"] == "AAA Inc" and got["XXX"]["name"] == "XXX Corp" and got["ZZZ"]["name"] is None


def test_holdings_can_be_given_as_tickers_holdings_or_records(pool, excluded) -> None:
    as_tickers = etf.build(["AAA", "BBB"], pool, excluded)
    as_records = etf.build([{"ticker": "AAA", "name": "x", "identifier": "AAA US"}, {"ticker": "BBB"}], pool, excluded)
    as_holdings = etf.build([etf.Holding("AAA"), etf.Holding("BBB")], pool, excluded)
    assert as_tickers.table["status"].to_list() == as_records.table["status"].to_list() == ["ring_a", "ring_b"]
    assert as_holdings.table["status"].to_list() == ["ring_a", "ring_b"]
    assert as_tickers.counts["as_of"] is None and as_tickers.counts["source_url"] is None
    assert _by(as_records)["AAA"]["name"] == "x"


@pytest.mark.parametrize("bad", ["AAA", [1], [{"name": "no ticker"}], [None]])
def test_a_holdings_argument_that_is_not_holdings_is_a_type_error(bad, pool, excluded) -> None:
    with pytest.raises(TypeError):
        etf.build(bad, pool, excluded)


def test_a_blank_ticker_is_refused(pool, excluded) -> None:
    with pytest.raises(ValueError, match="blank ticker"):
        etf.build([" "], pool, excluded)


def test_the_pool_may_be_a_mapping_and_the_inputs_are_not_changed(cibr, pool, excluded) -> None:
    before = (copy.deepcopy(pool), copy.deepcopy(excluded))
    by_ticker = {e.ticker: e for e in pool}
    a = etf.build(cibr, pool, excluded)
    b = etf.build(cibr, by_ticker, excluded)
    assert a.table.equals(b.table) and a.counts == b.counts
    assert (pool, excluded) == before
    assert etf.build(cibr, pool, excluded).table.equals(a.table), "no hidden state between calls"


def test_only_the_cyber_rows_of_the_excluded_list_count(pool) -> None:
    got = _by(etf.build(["OPT"], pool, [_excluded("OPT", track="optical", reason="private")]))
    assert got["OPT"]["status"] == "missing", "an optical exclusion is not a reason a cyber holding is accounted for"


# --- counts and facts ---------------------------------------------------------------------------------------
def test_counts_add_up_and_carry_provenance(cibr, pool, excluded) -> None:
    n = etf.build(cibr, pool, excluded).counts
    assert (n["n_holdings"], n["n_in_ring_a"], n["n_in_ring_b"], n["n_in_ring_f"], n["n_excluded"], n["n_missing"]) == (
        5, 1, 1, 1, 1, 1,
    )  # fmt: skip
    assert n["n_holdings"] == n["n_in_ring_a"] + n["n_in_ring_b"] + n["n_in_ring_f"] + n["n_excluded"] + n["n_missing"]
    assert (n["fund"], n["as_of"], n["source_url"]) == ("Test Cyber ETF", "2026-10-15", "https://example.com/holdings")


def test_the_reverse_view_counts_ring_a_listings_the_fund_does_not_hold(cibr, pool, excluded) -> None:
    n = etf.build(cibr, pool, excluded).counts
    assert n["n_ring_a"] == 3 and n["n_ring_a_outside_cibr"] == 2, "A2 and A3 are ring A and not held"
    only_a2 = etf.build(["A2", "A3", "AAA"], pool, excluded).counts
    assert only_a2["n_ring_a_outside_cibr"] == 0
    assert not hasattr(etf.build(cibr, pool, excluded), "outside"), "the reverse view is a count and not a list"


def test_facts_values_have_the_facts_shape(cibr, pool, excluded) -> None:
    cc = etf.build(cibr, pool, excluded)
    assert set(cc.values) == set(etf._COUNT_UNITS) and "n_ring_a_outside_cibr" in cc.values
    for key, v in cc.values.items():
        assert set(v) == {"value", "qualifier", "unit", "period", "as_of", "source_url"}, key
        assert v["value"] == cc.counts[key] and v["qualifier"] == "="
        assert v["as_of"] == v["period"] == "2026-10-15" and v["source_url"] == "https://example.com/holdings"
    assert cc.values["n_holdings"]["unit"] == "holdings" and cc.values["n_ring_a_outside_cibr"]["unit"] == "listings"


# --- the failed check ---------------------------------------------------------------------------------------
def test_a_missing_holding_fails_the_check_and_names_the_ticker(cibr, pool, excluded) -> None:
    cc = etf.build(cibr, pool, excluded)
    assert cc.missing == ["ZZZ"]
    first = etf.quality_checks(cc)[0]
    assert first["name"] == "CIBR cross-check complete" == etf.CHECK_NAME
    assert first["status"] == "fail" and "ZZZ" in first["detail"] and "1 missing" in first["detail"]


def test_a_complete_cross_check_passes(cibr, pool, excluded) -> None:
    cc = etf.build(cibr, pool, [*excluded, _excluded("ZZZ")])
    assert cc.missing == [] and cc.counts["n_excluded"] == 2
    assert [c["status"] for c in etf.quality_checks(cc)] == ["pass", "pass"]


def test_no_holdings_is_an_empty_table_zero_counts_and_a_failed_check(pool, excluded) -> None:
    cc = etf.build([], pool, excluded)
    assert cc.table.is_empty() and cc.table.schema == pl.Schema(etf.TABLE_DTYPES)
    assert cc.counts["n_holdings"] == 0 and cc.counts["n_ring_a_outside_cibr"] == cc.counts["n_ring_a"] == 3
    assert etf.quality_checks(cc)[0]["status"] == "fail", "nothing to cross-check is not a complete cross-check"


def test_no_pool_and_no_exclusions_make_every_holding_missing(cibr) -> None:
    cc = etf.build(cibr, [], [])
    assert cc.counts["n_missing"] == 5 and cc.counts["n_ring_a"] == 0


def test_a_ticker_in_the_pool_and_on_the_excluded_list_stays_in_the_pool_and_fails_a_check(cibr, pool) -> None:
    cc = etf.build(cibr, pool, [_excluded("AAA", reason="left out")])
    a = _by(cc)["AAA"]
    assert a["status"] == "ring_a" and a["excluded_reason"] == "left out", "the contradiction is visible in the row"
    assert cc.counts["n_excluded_in_pool"] == 1
    second = etf.quality_checks(cc)[1]
    assert second["status"] == "fail" and "1 ticker" in second["detail"]


def test_the_overlap_counts_excluded_tickers_the_fund_does_not_hold(cibr, pool) -> None:
    cc = etf.build(cibr, pool, [_excluded("A2")])
    assert cc.counts["n_excluded_in_pool"] == 1 and "A2" not in cc.table["ticker"].to_list()


# --- bad inputs ---------------------------------------------------------------------------------------------
def test_duplicate_holdings_do_not_pass_the_schema(pool, excluded) -> None:
    with pytest.raises(pe.SchemaError):
        etf.build(["AAA", "AAA"], pool, excluded)


def test_a_pool_with_a_duplicate_ticker_or_a_non_listing_ring_is_refused(cibr, excluded) -> None:
    with pytest.raises(ValueError, match="duplicate ticker in the pool"):
        etf.build(cibr, [_entry("AAA"), _entry("AAA", "B")], excluded)
    with pytest.raises(ValueError, match="listing rings"):
        etf.build(cibr, [_entry("AAA", "C")], excluded)


def test_an_excluded_row_without_a_reason_or_a_duplicate_is_refused(cibr, pool) -> None:
    with pytest.raises(ValueError, match="needs a ticker and a reason"):
        etf.build(cibr, pool, [_excluded("XXX", reason=" ")])
    with pytest.raises(ValueError, match="duplicate ticker in the excluded list"):
        etf.build(cibr, pool, [_excluded("XXX"), _excluded("XXX", reason="again")])


# --- the schema rejects what the page must never show --------------------------------------------------------
def _row(**over) -> dict:
    base = {"ticker": "AAA", "name": "AAA Inc", "in_pool": True, "ring": "A", "layer": "identity", "purity": "main",
            "excluded_reason": None, "status": "ring_a", "company_status": "listed"}  # fmt: skip
    return base | over


def _validate(*rows: dict) -> pl.DataFrame:
    return etf.TABLE_SCHEMA.validate(pl.DataFrame(list(rows), schema=etf.TABLE_DTYPES))


def test_the_schema_accepts_each_consistent_row() -> None:
    _validate(
        _row(),
        _row(ticker="B", ring="B", status="ring_b"),
        _row(ticker="F", ring="F", status="ring_f"),
        _row(ticker="X", in_pool=False, ring=None, layer=None, purity=None, company_status=None,
             status="excluded", excluded_reason="why"),
        _row(ticker="Z", in_pool=False, ring=None, layer=None, purity=None, company_status=None, status="missing"),
    )  # fmt: skip


@pytest.mark.parametrize(
    "over",
    [
        {"ring": None},  # ring_a with no ring
        {"ring": "B"},  # ring_a with ring B
        {"in_pool": False},  # ring_a but not in the pool
        {"status": "excluded"},  # excluded with a pool ring and no reason
        {"status": "missing"},  # missing with a pool ring
        {"status": "ring_x"},  # unknown status
        {"layer": None},  # in the pool without a layer
        {"layer": "not_a_layer"},
        {"purity": "total"},
        {"ring": "C", "status": "ring_c"},  # ring C holds no listing
        {"company_status": "weird"},
        {"excluded_reason": ""},
        {"ticker": ""},
    ],
)
def test_the_schema_rejects_inconsistent_rows(over) -> None:
    with pytest.raises(pe.SchemaError):
        _validate(_row(**over))


def test_the_schema_rejects_pool_columns_on_a_row_outside_the_pool_and_a_reasonless_exclusion() -> None:
    gone = {"in_pool": False, "ring": None, "layer": None, "purity": None, "company_status": None}
    with pytest.raises(pe.SchemaError):
        _validate(_row(**(gone | {"layer": "identity"}), status="missing"))
    with pytest.raises(pe.SchemaError):
        _validate(_row(**gone, status="excluded", excluded_reason=None))
    with pytest.raises(pe.SchemaError):
        _validate(_row(**gone, status="missing", excluded_reason="why"))


def test_the_schema_rejects_a_duplicate_ticker_and_an_extra_column() -> None:
    with pytest.raises(pe.SchemaError):
        _validate(_row(), _row())
    with pytest.raises(pe.SchemaError):
        etf.TABLE_SCHEMA.validate(_validate(_row()).with_columns(extra=pl.lit(1)))


# --- the holdings file ---------------------------------------------------------------------------------------
def _write_cibr(tmp_path: Path, **over) -> Path:
    doc = {
        "fund": "Test ETF", "tracks": "Test Index", "as_of": "2026-10-15", "source": "https://example.com/h",
        "holdings": [{"identifier": "AAA", "ticker": "AAA", "name": "AAA Inc"}],
    }  # fmt: skip
    doc |= over
    path = tmp_path / "cibr.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_loader_reads_names_and_provenance(tmp_path) -> None:
    rec = etf.load_cibr_records(_write_cibr(tmp_path))
    assert rec.tickers == ["AAA"] and rec.holdings[0].name == "AAA Inc"
    assert (rec.fund, rec.index, rec.as_of, rec.source_url) == (
        "Test ETF",
        "Test Index",
        "2026-10-15",
        "https://example.com/h",
    )


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"as_of": None}, "as_of"),
        ({"as_of": "15/10/2026"}, "YYYY-MM-DD"),
        ({"source": "http://example.com/h"}, "https"),
        ({"source": ""}, "source"),
        ({"fund": " "}, "fund"),
        ({"tracks": None}, "tracks"),
        ({"holdings": "AAA"}, "not a list"),
        ({"holdings": [{"ticker": "AAA", "name": ""}]}, "name"),
        ({"holdings": [{"ticker": "", "name": "x"}]}, "ticker"),
    ],
)
def test_the_loader_refuses_a_file_the_page_cannot_print(tmp_path, over, message) -> None:
    with pytest.raises(ValueError, match=message):
        etf.load_cibr_records(_write_cibr(tmp_path, **over))


def test_the_loader_agrees_with_frozens_ticker_loader_on_the_committed_file() -> None:
    rec = etf.load_cibr_records()
    assert rec.tickers == F.load_cibr()
    assert rec.as_of == "2026-10-15" and rec.source_url.startswith("https://") and rec.fund.endswith("(CIBR)")
    assert all(h.name for h in rec.holdings)


# --- the committed files --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real() -> etf.Crosscheck:
    return etf.build(etf.load_cibr_records(), POOL, EXCLUDED)


def test_cibr_has_no_missing_holding_on_the_committed_files(real) -> None:
    assert real.missing == [], f"CIBR holds tickers the pool and the excluded list do not account for: {real.missing}"
    assert real.counts["n_missing"] == 0
    raw = json.loads(F.CIBR_FILE.read_text(encoding="utf-8"))
    assert real.counts["n_holdings"] == len(raw["holdings"]) > 0
    n = real.counts
    assert n["n_holdings"] == n["n_in_ring_a"] + n["n_in_ring_b"] + n["n_in_ring_f"] + n["n_excluded"]
    assert [c["status"] for c in etf.quality_checks(real)] == ["pass", "pass"]


def test_the_q8_list_is_held_by_cibr_and_sits_in_ring_a(real) -> None:
    got = _by(real)
    assert all(got[t]["status"] == "ring_a" for t in C.FROZEN_Q8)


def test_what_cibr_holds_that_the_pool_leaves_out_is_on_the_excluded_list(real) -> None:
    cyber_excluded = {r["ticker"] for r in EXCLUDED if r["track"] == "cyber"}
    excluded_rows = real.table.filter(pl.col("status") == "excluded")
    assert set(excluded_rows["ticker"]) <= cyber_excluded
    assert excluded_rows["excluded_reason"].null_count() == 0
    assert real.counts["n_excluded_in_pool"] == 0
    held = set(real.table["ticker"])
    assert real.counts["n_ring_a_outside_cibr"] == sum(1 for e in POOL if e.ring == "A" and e.ticker not in held)


def test_a_tickers_only_call_places_the_committed_holdings_the_same_way(real) -> None:
    again = etf.build(F.load_cibr(), BY_TICKER, EXCLUDED)
    assert again.table.drop("name")["status"].to_list() == real.table["status"].to_list()
    assert again.counts["n_missing"] == 0 and again.counts["as_of"] is None


def test_the_real_pool_has_no_ticker_that_is_both_in_it_and_excluded() -> None:
    assert not {r["ticker"] for r in EXCLUDED if r["track"] == "cyber"} & set(BY_TICKER)


# --- excluded with reasons -----------------------------------------------------------------------------------
def test_excluded_table_has_the_left_out_cyber_rows_and_the_ring_f_entries(pool, excluded) -> None:
    df = etf.excluded_table(excluded, pool)
    assert df.columns == ["ticker", "name", "reason", "kind"]
    assert df.rows() == [
        ("XXX", "XXX Corp", "a switching vendor", "left_out"),
        ("FFF", "FFF Inc", "acquired 2026-02-11: Bought by AAA", "ring_f"),
    ]  # an optical row is not here, ring A and B listings are not here


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"status": "delisted", "status_date": "2025-11", "note": "Taken private"}, "delisted 2025-11: Taken private"),
        ({"status": "delisted", "status_date": "2025-11"}, "delisted 2025-11"),
        ({"status": "acquired"}, "acquired"),
    ],
)
def test_a_ring_f_reason_says_the_status_the_date_and_the_note_it_has(kw, reason) -> None:
    df = etf.excluded_table([], [_entry("OLD", "F", **kw)])
    assert df["reason"].to_list() == [reason] and df["kind"].to_list() == ["ring_f"]


def test_the_excluded_table_is_sorted_by_kind_then_ticker_and_empty_is_valid() -> None:
    df = etf.excluded_table([_excluded("ZED"), _excluded("ABC")], [_entry("MID", "F", status="delisted")])
    assert df["ticker"].to_list() == ["ABC", "ZED", "MID"]
    empty = etf.excluded_table([], [])
    assert empty.is_empty() and empty.schema == pl.Schema(etf.EXCLUDED_DTYPES)


def test_the_excluded_table_refuses_a_ticker_that_is_left_out_and_in_ring_f() -> None:
    with pytest.raises(pe.SchemaError):
        etf.excluded_table([_excluded("OLD")], [_entry("OLD", "F", status="delisted")])


def test_the_excluded_table_on_the_committed_files_is_the_cyber_rows_plus_ring_f() -> None:
    df = etf.excluded_table()
    left_out = {r["ticker"] for r in EXCLUDED if r["track"] == "cyber"}
    ring_f = {e.ticker for e in POOL if e.ring == "F"}
    assert set(df.filter(pl.col("kind") == "left_out")["ticker"]) == left_out >= {"ANET", "NTAP", "DT"}
    assert set(df.filter(pl.col("kind") == "ring_f")["ticker"]) == ring_f and ring_f
    assert not {r["ticker"] for r in EXCLUDED if r["track"] != "cyber"} & set(df["ticker"])
    assert df["reason"].str.len_chars().min() > 0


# --- the metric dictionary -----------------------------------------------------------------------------------
METRICS = [
    "EV/NTM revenue", "NTM revenue", "growth", "FCF margin", "Rule of 40", "target upside", "dispersion",
    "PS_TTM (A-share)", "PS percentile", "headline valuation", "discount interval", "public-comparable interval",
    "cyber_segment_share", "purity", "layer",
]  # fmt: skip
#: Which row embeds which pinned config string.
CONFIG_IN_ROW = [
    ("EV/NTM revenue", "EV_SOURCE"),
    ("NTM revenue", "NTM_WEIGHT"),
    ("NTM revenue", "FY0_END"),
    ("growth", "GROWTH"),
    ("growth", "TRAILING_SOURCE"),
    ("FCF margin", "FCF_MARGIN"),
    ("FCF margin", "TRAILING_SOURCE"),
    ("Rule of 40", "RULE_OF_40"),
    ("target upside", "UPSIDE"),
    ("dispersion", "DISPERSION"),
    ("discount interval", "DISCOUNT_BAND"),
]


def _dictionary() -> dict[str, dict]:
    return {r["metric"]: r for r in etf.metric_dictionary()}


def test_the_dictionary_has_the_fifteen_metrics_in_order_with_the_six_fields() -> None:
    rows = etf.metric_dictionary()
    assert [r["metric"] for r in rows] == METRICS
    for r in rows:
        assert list(r) == ["metric", "formula", "unit", "comparable_across", "not_comparable_across", "source"]
        assert all(isinstance(v, str) and v.strip() for v in r.values()), r["metric"]
    assert etf.metric_dictionary() == rows, "a pure function of config"


@pytest.mark.parametrize(("metric", "name"), CONFIG_IN_ROW)
def test_every_config_definition_string_used_appears_in_its_row(metric, name) -> None:
    assert getattr(C, name) in _dictionary()[metric]["formula"]


def test_the_config_strings_the_module_says_it_embeds_are_the_ones_this_file_checks() -> None:
    assert set(etf.CONFIG_DEFINITION_NAMES) == {name for _, name in CONFIG_IN_ROW}
    formulas = "\n".join(r["formula"] for r in etf.metric_dictionary())
    assert all(getattr(C, name) in formulas for name in etf.CONFIG_DEFINITION_NAMES)


def test_definitions_follow_config_and_are_not_retyped(monkeypatch) -> None:
    for name in etf.CONFIG_DEFINITION_NAMES:
        monkeypatch.setattr(C, name, f"CHANGED {name}")
    formulas = "\n".join(r["formula"] for r in etf.metric_dictionary())
    assert all(f"CHANGED {name}" in formulas for name in etf.CONFIG_DEFINITION_NAMES)


def test_the_discount_interval_lists_every_registered_discount_and_the_band() -> None:
    f = _dictionary()["discount interval"]["formula"]
    assert all(f"{year} {pct}" in f for year, pct in C.DISCOUNT_BY_ROUND_YEAR.items())
    assert C.DISCOUNT_BAND in f and "not scored" in f


def test_the_public_comparable_interval_lists_every_layers_comparables() -> None:
    f = _dictionary()["public-comparable interval"]["formula"]
    for layer, tickers in C.COMPARABLES_BY_LAYER.items():
        assert f"{layer}: {', '.join(tickers)}" in f
    assert "company-stated ARR" in f


def test_layer_and_purity_rows_name_every_layer_and_class() -> None:
    layer, purity = _dictionary()["layer"], _dictionary()["purity"]
    assert all(v["name"] in layer["formula"] for v in C.LAYERS.values())
    assert f"{len(C.LAYERS)} layers" in layer["formula"]
    assert all(f"{cls}:" in purity["formula"] for cls in C.PURITY)
    assert purity["unit"] == f"class ({', '.join(C.PURITY)})"


def test_cyber_segment_share_says_it_is_null_today() -> None:
    row = _dictionary()["cyber_segment_share"]
    assert "null today" in row["formula"] and "null today" in row["unit"]
    assert "null" in row["comparable_across"], "nothing can be compared while every value is null"
    assert "cyber_segment_share" in _dictionary()["purity"]["not_comparable_across"], "purity is not a share"


def test_ps_rows_are_east_money_and_a_share_only() -> None:
    d = _dictionary()
    assert "East Money" in d["PS_TTM (A-share)"]["source"] and "East Money" in d["PS percentile"]["source"]
    assert "EV/NTM" in d["PS_TTM (A-share)"]["not_comparable_across"]
    assert "since 2018" in d["PS percentile"]["formula"]


def test_thin_coverage_in_the_upside_and_dispersion_rows_is_the_registered_cut() -> None:
    d = _dictionary()
    for metric in ("target upside", "dispersion"):
        assert f"fewer than {C.THIN_COVERAGE_BELOW} analysts" in d[metric]["not_comparable_across"]


def test_the_definitions_config_does_not_hold_are_verbatim_fragments_of_section_i() -> None:
    plan = _plan_section()
    for text in (*etf.PURITY_DEFINITIONS.values(), etf.LAYER_RULE, etf.HEADLINE_RULE):
        assert _flat(text) in plan, f"not in section I: {text!r}"
    assert set(etf.PURITY_DEFINITIONS) == set(C.PURITY)
    d = _dictionary()
    assert all(text in d["purity"]["formula"] for text in etf.PURITY_DEFINITIONS.values())
    assert etf.LAYER_RULE in d["layer"]["formula"] and etf.HEADLINE_RULE in d["headline valuation"]["formula"]


def test_the_ps_history_start_is_the_one_the_pipeline_spec_and_the_china_panel_use() -> None:
    assert "since 2018" in _flat(SPEC.read_text(encoding="utf-8"))
    vh = pytest.importorskip("pipelines.cyber.valuation_history_em")
    assert f"since {vh.HISTORY_START[:4]} " in _dictionary()["PS percentile"]["formula"]


def test_the_dictionary_schema_rejects_a_blank_field_a_duplicate_metric_and_a_missing_field() -> None:
    good = pl.DataFrame(etf.metric_dictionary(), schema=etf.DICTIONARY_DTYPES)
    etf.DICTIONARY_SCHEMA.validate(good)
    with pytest.raises(pe.SchemaError):
        etf.DICTIONARY_SCHEMA.validate(
            good.with_columns(
                formula=pl.when(pl.col("metric") == "growth").then(pl.lit("")).otherwise(pl.col("formula"))
            )
        )
    with pytest.raises(pe.SchemaError):
        etf.DICTIONARY_SCHEMA.validate(pl.concat([good, good.head(1)]))
    with pytest.raises(pe.SchemaError):
        etf.DICTIONARY_SCHEMA.validate(good.drop("source"))


# --- the runner ----------------------------------------------------------------------------------------------
def test_main_passes_on_the_committed_files(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="cyber.etf"):
        assert etf.main([]) == 0
    assert any("CIBR cross-check complete" in r.getMessage() for r in caplog.records)


def test_main_fails_and_names_a_holding_the_pool_does_not_account_for(tmp_path, caplog) -> None:
    path = _write_cibr(tmp_path, holdings=[{"ticker": "FTNT", "name": "Fortinet"}, {"ticker": "NOPE", "name": "Nope"}])
    with caplog.at_level(logging.INFO, logger="cyber.etf"):
        assert etf.main(["--cibr", str(path)]) == 1
    assert any("NOPE" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
