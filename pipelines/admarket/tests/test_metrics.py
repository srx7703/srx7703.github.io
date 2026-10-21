"""Metric rules on small synthetic series: growth, the Q2/Q3 tests, Q4 gaps, rule A, scoring, prereg parsing."""

from __future__ import annotations

import math

import pytest

from pipelines.admarket import config as cfg
from pipelines.admarket import metrics as M
from pipelines.admarket import prereg
from pipelines.admarket.transform import shift


def geometric(start: str, n: int, v0: float, quarterly_growth: float) -> dict[str, float]:
    return {shift(start, i): v0 * (1 + quarterly_growth) ** i for i in range(n)}


def test_yoy_and_two_year_growth():
    s = geometric("2019Q1", 16, 100.0, 0.05)
    assert M.yoy(s, "2020Q1") == pytest.approx(1.05 ** 4 - 1)
    assert M.yoy(s, "2019Q1") is None
    assert M.g2(s, "2021Q1") == pytest.approx(1.05 ** 4 - 1)   # annualised over two years


def _q2_series(aff_rate_before, aff_rate_after, ctl_rate):
    """Affected members grow at one quarterly rate until 2021Q2 and another after; control steady."""
    def path(r1, r2):
        out, v = {}, 100.0
        for i in range(24):
            q = shift("2019Q1", i)
            out[q] = v
            v *= 1 + (r1 if q < "2021Q3" else r2)
        return out
    s = {k: path(aff_rate_before, aff_rate_after) for k in cfg.Q2_AFFECTED}
    s.update({k: path(ctl_rate, ctl_rate) for k in cfg.Q2_CONTROL})
    return s


def test_q2_holds_when_the_affected_group_slows_more():
    r = M.q2(_q2_series(0.08, 0.0, 0.04))
    assert r["control"]["drop"] == pytest.approx(0.0, abs=1e-12)
    assert r["affected"]["drop"] > cfg.Q2_THRESHOLD and r["verdict"] == "holds"


def test_q2_refuted_when_both_slow_alike():
    r = M.q2(_q2_series(0.04, 0.04, 0.04))
    assert r["gap"] == pytest.approx(0.0, abs=1e-12) and r["verdict"] == "refuted"


def _q3_series(open_rate, wall_rate):
    s = {k: geometric("2021Q1", 22, 10.0, open_rate) for k in cfg.Q3_OPEN}
    s.update({k: geometric("2021Q1", 22, 50.0, wall_rate) for k in cfg.Q3_WALLED})
    return s


@pytest.mark.parametrize("open_rate, wall_rate, verdict", [
    (0.0, 0.04, "holds"),           # open flat, walled ~17% a year
    (0.04, 0.04, "refuted"),        # equal growth
    (0.03, 0.04, "undecided"),      # walled faster by less than 5 points
])
def test_q3_verdicts(open_rate, wall_rate, verdict):
    r = M.q3(_q3_series(open_rate, wall_rate))
    assert r["verdict"] == verdict
    assert r["open"] == pytest.approx((1 + open_rate) ** 4 - 1)


def test_q3_trailing_sums_skip_missing_quarters():
    s = {"a": {"2023Q1": 1.0, "2023Q2": 2.0}, "b": {"2022Q3": 5.0}}
    assert M.ttm(s, ("a", "b"), "2023Q2") == 8.0


def test_q4_gap_is_rounded_points_and_the_rule_is_three():
    series = {"meta_ads": {"2024Q1": 100.0, "2025Q1": 121.0}, "google_search": {"2024Q1": 100.0, "2025Q1": 110.0},
              "google_network": {}}
    vp = [{"series": "meta_ads", "quarter": "2025Q1", "volume": 10, "price": 10},
          {"series": "google_search_clicks", "quarter": "2025Q1", "volume": 10, "price": 3}]
    r = M.q4(series, vp)
    meta, search = r["rows"]
    assert meta["implied"] == 21.0 and meta["reported"] == 21.0 and meta["gap_pp"] == 0.0
    assert search["implied"] == 13.3 and search["gap_pp"] == 3.3
    assert r["verdict"]["meta_ads"]["usable"] and not r["verdict"]["google_search_clicks"]["usable"]
    assert r["verdict"]["google_search_clicks"]["over_by_pp"] == 0.3


def test_positive_and_negative_since():
    assert M._positive_since([("a", -1), ("b", 2), ("c", -1), ("d", 1), ("e", 3)]) == "d"
    assert M._negative_since([("a", -1), ("b", 2)]) is None


def test_rule_a_point_is_last_year_times_latest_growth():
    s = {"x": {"2025Q2": 100.0, "2025Q3": 110.0, "2026Q2": 120.0}}
    assert M._predict(s["x"], "2026Q2", 1) == pytest.approx(110.0 * 1.2)
    assert M._predict(s["x"], "2026Q2", 2) is None   # no 2025Q4


def test_quantile_matches_linear_interpolation():
    assert M._quantile([1, 2, 3, 4, 5], 0.1) == pytest.approx(1.4)
    assert M._quantile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)


FORECAST_MD = """# Pre-registered forecasts

Forecast for a quarter = ... pooled across companies: one quarter ahead -5.3% to +6.3% (n=191), two quarters ahead -6.3% to +11.2% (n=176).
Source data: (`x.csv`, SHA-256 `b01adbcbef8f214bb353df979039628fbaed5652bbf380846ddd9beab87a3450`).

| Series | 2026Q3 point ($m) | 2026Q3 80% interval ($m) | 2026Q4 point ($m) | 2026Q4 80% interval ($m) |
|---|---|---|---|---|
| Google | 84,881 | 80,368 to 90,214 | 94,151 | 88,194 to 104,657 |
| Google Network | 7,303 | 6,915 to 7,762 | 7,774 | 7,282 to 8,641 |

B1. Google's share falls.
B7. Network shrinks.

## Amendments

(none)
"""


def test_parse_registered_forecasts():
    p = prereg.parse_forecasts(FORECAST_MD)
    assert p["forecasts"]["Google Network"]["2026Q4"] == {"point": 7774, "low": 7282, "high": 8641}
    assert p["band"][1] == (pytest.approx(-0.053), pytest.approx(0.063), 191)
    assert p["band"][2][2] == 176
    assert [c["id"] for c in p["calls"]] == ["B1", "B7"]
    assert p["snapshot_sha256"].startswith("b01adbcb")
    assert prereg.amendments(FORECAST_MD) == []
    assert prereg.amendments(FORECAST_MD.replace("(none)", "2026-11-01: corrected a typo")) == [
        "2026-11-01: corrected a typo"]


def test_parse_refuses_a_file_without_the_table():
    with pytest.raises(ValueError):
        prereg.parse_forecasts("nothing registered here")


def test_recompute_matches_counts_dollar_million_cells():
    reg = prereg.parse_forecasts(FORECAST_MD)
    rc = {"forecasts": {"Google": {"2026Q3": {"point": 84881.2e6, "low": 80368e6, "high": 90214e6},
                                   "2026Q4": {"point": 94151e6, "low": 88194e6, "high": 104657e6}}},
          "band": {1: (math.log(1 - 0.053), math.log(1.063)), 2: (math.log(1 - 0.063), math.log(1.112))},
          "n": {1: 191, 2: 176}}
    out = M.recompute_matches(rc, reg)
    assert out["cells"] == 12 and out["mismatches"] == 6 and out["band_ok"]   # Google Network not recomputed here


def _ttd_filing(filed, start, end, value):
    return {"ticker": "TTD", "filed": filed, "accession": filed, "facts": [
        {"concept": cfg.REV[0], "start": start, "end": end, "dims": {}, "value": value}]}


def test_score_a_pending_hit_and_miss():
    reg = {"forecasts": {"TradeDesk": {"2026Q3": {"point": 762, "low": 721, "high": 810},
                                       "2026Q4": {"point": 872, "low": 817, "high": 970}}}}
    none = M.score_a([], reg)
    assert none["status"] == "pending" and none["n_scored"] == 0 and none["hit_rate"] is None
    one = M.score_a([_ttd_filing("2026-11-06", "2026-07-01", "2026-09-30", 700e6)], reg)
    assert one["status"] == "partial" and one["rows"][0]["status"] == "miss" and one["rows"][1]["status"] == "pending"
    both = M.score_a([_ttd_filing("2026-11-06", "2026-07-01", "2026-09-30", 780e6),
                      _ttd_filing("2027-02-20", "2026-10-01", "2026-12-31", 900e6)], reg)
    assert both["status"] == "resolved" and both["hits"] == 2 and both["verdict"] == "too wide"
