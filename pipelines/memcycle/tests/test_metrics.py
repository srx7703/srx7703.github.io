"""Q3 verdict boundaries, sample membership and the stock-side window rules.

The stock-side functions run on synthetic series here, because the real closes never enter the repo. The
membership tests run on the frozen table, which is what the page is built from.
"""

from __future__ import annotations

import pytest

from pipelines.memcycle import config as cfg
from pipelines.memcycle import evaluate, freeze, metrics, publish
from pipelines.memcycle import transform as T

# ---------------------------------------------------------------------------------------------
# verdict boundaries
# ---------------------------------------------------------------------------------------------


def test_holds_at_exactly_sixty_percent_and_median_three():
    leads = [3, 3, 3, 0, 0]  # 60% lead, median 3
    assert evaluate.verdict(leads)["verdict"] == "holds"


def test_median_nine_is_inside_and_ten_is_not():
    assert evaluate.verdict([9, 9, 9, 0, 0])["verdict"] == "holds"
    assert evaluate.verdict([10, 10, 10, 0, 0])["verdict"] == "inconclusive"


def test_median_below_three_is_inconclusive_even_with_many_leaders():
    assert evaluate.verdict([2, 2, 2, 2, 0])["verdict"] == "inconclusive"


def test_exactly_fifty_percent_is_not_falsified():
    assert evaluate.verdict([1, 1, 0, 0])["verdict"] == "inconclusive"
    assert evaluate.verdict([1, 0, 0])["verdict"] == "falsified"


def test_a_lead_of_zero_does_not_count_as_leading():
    assert evaluate.verdict([0, 0, 0, 0])["share_lead_ge1"] == 0


def test_flips_to_inconclusive():
    assert evaluate.flips_to_inconclusive([1] * 14 + [0] * 19) == 3  # 14/33 -> 17/33 >= 50%
    assert evaluate.flips_to_inconclusive([1, 1, 0, 0]) is None  # not falsified to begin with
    assert evaluate.flips_to_inconclusive([]) is None


def test_not_yet_items_carry_a_reason():
    items = evaluate.not_yet({}, "2026-08", False)
    assert [i["id"] for i in items] == ["Q6", "Q7", "Q8"]
    assert all(i["status"] == "not_yet" and len(i["why"]) > 20 for i in items)


# ---------------------------------------------------------------------------------------------
# sample membership on the frozen table
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rows():
    return publish.read_frozen()


@pytest.fixture(scope="module")
def samples(rows):
    return evaluate.samples(rows)


def test_psc_2010_is_out_because_it_delisted_inside_the_window(rows, samples):
    psc = next(r for r in rows if r["company"] == "5346" and r["price_peak"] == "2010-05")
    assert psc["status"] == "ok" and not psc["complete"]
    assert psc not in samples["main_pure"]
    assert publish.exclusion_reason(psc) == "delisted or out of scope inside the window"


def test_infineon_stops_at_the_qimonda_carve_out(rows):
    ifx = [r for r in rows if r["company"] == "IFX"]
    assert max(r["last_month"] for r in ifx) == cfg.INFINEON_LAST_MONTH


def test_promos_is_shown_but_never_in_a_sample(rows, samples):
    assert any(r["company"] == "5387" for r in rows)
    assert not any(r["company"] == "5387" for s in samples.values() for r in s)


def test_wdc_is_out_of_the_nand_panel(rows):
    assert not any(r["company"] == "WDC" and r["product"] == "NAND" for r in rows)
    assert not next(c for c in cfg.COMPANIES if c.key == "WDC").nand_panel


def test_toshiba_is_registered_with_no_data(rows):
    toshiba = next(c for c in cfg.COMPANIES if c.key == "6502")
    assert toshiba.group == "diversified" and toshiba.nand_panel and "6502" in cfg.NO_DATA
    assert not any(r["company"] == "6502" for r in rows)


def test_running_cycle_and_first_peak_are_excluded(rows, samples):
    for s in samples.values():
        assert all(r["price_peak_confirmed"] for r in s)
        assert all(r["price_peak"] not in ("1995-07", "2000-10") for r in s)


def test_partial_flag_marks_only_the_month_in_progress(rows):
    flagged = [r for r in rows if publish.partial_latest_month(r)]
    assert flagged and all(max(filter(None, (r["stock_peak"], r["stock_trough"]))) > cfg.DATA_END for r in flagged)
    elpida = next(r for r in rows if r["company"] == "ELPIDA" and r["price_peak"] == "2010-05")
    assert elpida["partial_latest_month"] and not publish.partial_latest_month(elpida)  # the research quirk


# ---------------------------------------------------------------------------------------------
# stock-side windows on synthetic series
# ---------------------------------------------------------------------------------------------


def _turn(kind: str, month: str) -> T.Turn:
    return T.Turn(0, month, kind, 1.0, True)


def _cycle(prev_trough="2000-06", peak="2001-06", trough="2002-06", next_peak="2003-06", prev_peak="1999-06"):
    return {"prev_trough": _turn("T", prev_trough), "peak": _turn("P", peak), "trough": _turn("T", trough),
            "next_peak": _turn("P", next_peak), "prev_peak": _turn("P", prev_peak)}


def _px(start: str, closes: list[float], highs: list[float] | None = None) -> metrics.Px:
    out = {}
    for i, c in enumerate(closes):
        h = highs[i] if highs else c * 1.1
        out[T.add_months(start, i)] = {"close": c, "high": h, "low": c * 0.9, "partial": False}
    return out


def test_tie_goes_to_the_earliest_stock_peak():
    closes = [10.0] * 60
    closes[20] = closes[25] = 50.0  # 2000-09 and 2001-02 tie
    px = _px("1999-01", closes)
    r = metrics.analyse_company(px, _cycle(), latest="2003-12", first="1999-01", delisted=False)
    assert r["stock_peak"] == "2000-09" and r["lead_months"] == 9


def test_complete_window_and_listing_inside_it():
    px = _px("1999-01", [10.0 + i for i in range(60)])
    assert metrics.analyse_company(px, _cycle(), "2003-12", "1999-01", False)["complete"]
    late = {m: v for m, v in px.items() if m >= "2001-01"}  # listed inside (2000-06, 2002-06]
    r = metrics.analyse_company(late, _cycle(), "2003-12", "2001-01", False)
    assert r["status"] == "ok" and not r["complete"]


def test_delisting_inside_the_window_makes_it_incomplete():
    px = {m: v for m, v in _px("1999-01", [10.0] * 60).items() if m <= "2002-03"}
    r = metrics.analyse_company(px, _cycle(), latest="2002-03", first="1999-01", delisted=True)
    assert not r["complete"]


def test_a_month_without_a_close_makes_the_window_incomplete():
    px = _px("1999-01", [10.0] * 60)
    px["2001-01"] = {**px["2001-01"], "close": None}
    assert not metrics.analyse_company(px, _cycle(), "2003-12", "1999-01", False)["complete"]


def test_first_ecos_peak_is_excluded():
    cyc = {**_cycle(), "prev_trough": None}
    r = metrics.analyse_company(_px("1999-01", [10.0] * 60), cyc, "2003-12", "1999-01", False)
    assert r["status"].startswith("excluded")


def test_listing_truncation_flag():
    """Listed after the previous price peak, and the up-leg low sits in the first three months of trading."""
    px = _px("2000-01", [5.0, 6.0, 7.0] + [8.0 + i for i in range(40)])
    r = metrics.analyse_company(px, _cycle(), "2003-07", "2000-01", False)
    assert r["upleg_low"] == "2000-01" and r["upleg_truncated_by_listing"]
    old = _px("1998-01", [20.0] * 18 + [5.0] + [8.0 + i for i in range(50)])  # a real low after a long history
    r2 = metrics.analyse_company(old, _cycle(), "2003-07", "1998-01", False)
    assert not r2["upleg_truncated_by_listing"]


def test_upleg_multiple_and_auxiliary_basis():
    closes = [10.0] * 60
    closes[15] = 4.0  # 2000-04, the low after the previous peak (1999-06)
    closes[26] = 40.0  # 2001-03, the stock peak
    r = metrics.analyse_company(_px("1999-01", closes), _cycle(), "2003-12", "1999-01", False)
    assert r["upleg_low"] == "2000-04" and r["upleg_multiple"] == pytest.approx(10.0)
    assert r["upleg_multiple_hl"] == pytest.approx(40.0 * 1.1 / (4.0 * 0.9))


def test_usd_and_benchmark_multiples():
    closes = [10.0] * 60
    closes[15], closes[26] = 4.0, 40.0
    px = _px("1999-01", closes)
    cyc = _cycle()
    r = metrics.analyse_company(px, cyc, "2003-12", "1999-01", False)
    fx = {"KRW": ({"2000-04": 1000.0, "2001-03": 2000.0}, False)}
    bench = [("KOSPI", _px("1999-01", [100.0] * 15 + [100.0] + [100.0] * 10 + [200.0] + [100.0] * 33))]
    metrics.add_usd_and_bench(r, px, cyc, "KRW", "2003-12", fx, bench)
    assert r["upleg_multiple_usd"] == pytest.approx(5.0)  # the won halved against the dollar
    assert r["bench1"] == "KOSPI" and r["bench1_multiple"] == pytest.approx(2.0) and r["excess1"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------------------------
# freeze.py's input fixes (synthetic local folder)
# ---------------------------------------------------------------------------------------------


def test_korea_official_close_replaces_after_market_from_2026_09(tmp_path):
    kr = tmp_path / "kr"
    kr.mkdir()
    (kr / "000660_monthly.csv").write_text("month,close,high,low,source,notes\n2026-08,100,110,90,daum,\n"
                                           "2026-09,200,210,190,daum,\n")
    (kr / "000660_yahoo_monthly.csv").write_text("month,close,high,low\n2026-08,101,111,91\n2026-09,190,205,185\n")
    out = metrics.parse_monthly(freeze.korea_official(tmp_path, "000660").decode())
    assert out["2026-08"]["close"] == 100.0 and out["2026-09"]["close"] == 190.0


def test_delisted_merge_keeps_high_only_months_without_a_close(tmp_path):
    d = tmp_path / "delisted"
    d.mkdir()
    (d / "x_monthend_close_partial.csv").write_text(
        "ticker,entity,month,close,month_end_date,currency,adj_basis,source,notes\n"
        "X,X AG,2007-01,10,2007-01-31,USD,split_adjusted,archive,\n")
    (d / "x_quarterly_hl.csv").write_text("period,period_type,high,low,source\n2007-01,month,12,9,424B2\n"
                                          "2007-02,month,11,8,424B2\n2007Q1,quarter,12,8,424B2\n")
    out = metrics.parse_monthly(freeze.merged_delisted(tmp_path, "x").decode())
    assert out["2007-01"]["close"] == 10.0 and out["2007-02"]["close"] is None and out["2007-02"]["high"] == 11.0
