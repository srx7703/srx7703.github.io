import polars as pl
import pytest

from pipelines.macro import config
from pipelines.macro import releases as rel


def test_strikes_are_rounded_to_the_print_resolution_and_read_from_old_tickers():
    assert rel.strike_of({"floor_strike": 4.099999, "strike_type": "greater"}, 0.1) == 4.1
    assert (
        rel.strike_of({"ticker": "KXPAYROLLS-25JAN-T256000", "floor_strike": None, "strike_type": None}, 1000) == 256000
    )
    assert rel.strike_of({"floor_strike": 99999, "strike_type": "greater"}, 1000) == 100000
    assert rel.strike_of({"floor_strike": 0.3, "strike_type": "between"}, 0.1) is None


def test_ladder_mean_puts_mass_on_the_only_print_between_adjacent_strikes():
    # P(>0.0)=1, P(>0.1)=1, P(>0.2)=0.8, P(>0.3)=0.2 -> 0.2 w.p. 0.2, 0.3 w.p. 0.6, 0.4 w.p. 0.2
    assert rel.ladder_mean([0.0, 0.1, 0.2, 0.3], [1.0, 1.0, 0.8, 0.2], 0.1) == pytest.approx(0.30)


def test_ladder_mean_uses_midpoints_on_a_coarse_ladder_and_repairs_a_rising_curve():
    # payroll ladder in 50k steps: mass between strikes sits at the midpoint
    got = rel.ladder_mean([0, 50000, 100000], [1.0, 0.5, 0.0], 1000)
    assert got == pytest.approx(0.5 * 25000 + 0.5 * 75000)
    # quotes that rise with the strike are pooled before the mean is taken
    assert rel.pav_decreasing([0.9, 0.95, 0.5, 0.6, 0.1]) == pytest.approx([0.925, 0.925, 0.55, 0.55, 0.1])


def test_settled_print_prefers_the_value_only_when_it_agrees_with_the_results():
    ladder = [
        {"floor_strike": 0.2, "strike_type": "greater", "result": "yes", "expiration_value": "Above 0.2%"},
        {"floor_strike": 0.3, "strike_type": "greater", "result": "no", "expiration_value": "Above 0.2%"},
    ]
    assert rel.settled_print(ladder, 0.1) == (0.3, "results")
    for m in ladder:
        m["expiration_value"] = "0.3%"
    assert rel.settled_print(ladder, 0.1) == (0.3, "expiration_value")
    for m in ladder:
        m["expiration_value"] = "0.5"  # contradicts the "no" above 0.3
    assert rel.settled_print(ladder, 0.1) == (0.3, "results")
    assert rel.parse_value("-92,000") == -92000 and rel.parse_value("") is None


def test_a_ladder_is_matched_only_if_it_was_trading_ten_minutes_before_the_release():
    t0 = rel.et_epoch("2025-10-24", "08:30")
    cal = pl.DataFrame([{"release": "cpi", "t0": t0, "date_et": "2025-10-24", "title": "Consumer Price Index"}])
    on_time = {"event": "KXCPI-X", "close_ts": t0 - 5 * 60}
    stale = {"event": "KXCPI-25SEP", "close_ts": t0 - 9 * 86400}  # closed at the pre-shutdown date
    matched, missing = rel.match(cal, [on_time], "cpi")
    assert matched and matched[0]["ladder"]["event"] == "KXCPI-X" and not missing
    matched, missing = rel.match(cal, [stale], "cpi")
    assert not matched and "KXCPI-25SEP closed 9 days earlier" in missing[0]["reason"]


def test_calendar_is_well_formed():
    cal = rel.calendar()
    assert cal.height > 100 and cal["release"].is_in(["cpi", "jobs", "pce", "ppi", "gdp", "eci"]).all()
    assert cal.unique(subset=["release", "release_et"]).height == cal.height
    # the shutdown moved September 2025 CPI to October 24, and there was no November 13 release
    cpi = set(cal.filter(pl.col("release") == "cpi")["date_et"].to_list())
    assert "2025-10-24" in cpi and "2025-11-13" not in cpi
    assert set(config.PRIMARY) == set(config.RELEASES)
