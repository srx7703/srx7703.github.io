"""Pure-function tests for the FRED FX module. No network.

Fixtures are trimmed copies of real fredgraph.csv payloads fetched on 2026-09-16, including both
spellings of the date column (FRED migrated `DATE` -> `observation_date`) and both spellings of a
missing observation (a literal `.` in the classic export, an empty field in the current one).
"""

from __future__ import annotations

import gzip
import json
import math

import polars as pl
import pytest

from pipelines.valuation import fx as mod
from pipelines.valuation.config import FX_SERIES
from pipelines.valuation.fx import (
    SANITY_BANDS,
    archive_csv,
    band_warnings,
    duplicate_keys,
    fx_rows,
    identity_rows,
    latest_rates,
    parse_fred_csv,
    to_per_usd,
)

# The pence/major-unit factor of 100 and `convert` live in metrics.py and nowhere else; this module
# only asserts the consequence for the fx table, which is that a pence unit never gets a rate row.
from pipelines.valuation.metrics import PENCE_UNITS
from pipelines.valuation.schema import FX_SCHEMA, fx_frame

TS = "2026-09-16T18:30:00+00:00"
SINCE = "2026-09-01"

# current export: empty field for a missing observation (Labor Day 2026-09-07)
DEXCHUS_CSV = """observation_date,DEXCHUS
2026-09-04,6.7195
2026-09-07,
2026-09-08,6.7150
2026-09-09,6.7121
2026-09-10,6.7099
2026-09-11,6.7080
"""

# classic export: `DATE` header and a literal "." for a missing observation
DEXJPUS_CSV = """DATE,DEXJPUS
2026-09-04,154.02
2026-09-07,.
2026-09-08,153.88
2026-09-09,153.80
2026-09-10,153.77
2026-09-11,153.71
"""

# the inverted one: USD per GBP, not GBP per USD
DEXUSUK_CSV = """observation_date,DEXUSUK
2026-09-04,1.3510
2026-09-07,
2026-09-08,1.3542
2026-09-09,1.3549
2026-09-10,1.3532
2026-09-11,1.3524
"""


# --- the inversion ---------------------------------------------------------------
def test_dexusuk_is_inverted() -> None:
    """DEXUSUK quotes USD per GBP; per_usd must be its reciprocal, not the printed number."""
    assert to_per_usd(1.3524, "usd_per") == pytest.approx(1.0 / 1.3524)
    assert to_per_usd(1.3524, "usd_per") == pytest.approx(0.739426, abs=1e-6)


def test_gbp_rows_are_inverted_and_in_band() -> None:
    rows = fx_rows("GBP", "DEXUSUK", "usd_per", DEXUSUK_CSV, snapshot_ts=TS, since=SINCE)
    latest = rows[-1]
    assert latest["date"] == "2026-09-11"
    assert latest["per_usd"] == pytest.approx(1.0 / 1.3524)
    assert latest["per_usd"] < 1.0  # a pound is worth more than a dollar; per_usd must be below 1
    # the factor-1.83 bug: the un-inverted number would be flagged, the inverted one is not
    assert band_warnings({"GBP": latest["per_usd"]}) == []
    assert band_warnings({"GBP": 1.3524}) != []


def test_per_usd_convention_is_passed_through() -> None:
    assert to_per_usd(6.7080, "per_usd") == pytest.approx(6.7080)


def test_identity_convention_ignores_the_value() -> None:
    assert to_per_usd(0.0, "identity") == 1.0
    assert to_per_usd(999.0, "identity") == 1.0


def test_unknown_convention_and_non_positive_values_raise() -> None:
    with pytest.raises(ValueError, match="unknown FX convention"):
        to_per_usd(1.0, "sideways")
    with pytest.raises(ValueError, match="non-positive"):
        to_per_usd(0.0, "usd_per")  # would be a division by zero
    with pytest.raises(ValueError, match="non-positive"):
        to_per_usd(-1.0, "per_usd")


# --- a non-finite rate is the quietest way to get every number wrong ---------------
@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "1e400", "-1e400"])
def test_non_finite_observations_are_rejected(raw: str) -> None:
    """`inf` passes `value <= 0` and passes pandera's `gt(0.0)`, so nothing downstream would notice.

    metrics.convert(amount, 'CNY', 'USD', {'CNY': inf}) is 0.0: every A-share revenue becomes zero
    USD in the share table while "Share adds to one" still passes, because the surviving shares
    renormalise. It has to die here, at the parse.
    """
    with pytest.raises(ValueError, match="non-finite|non-positive"):
        to_per_usd(float(raw), "per_usd")
    with pytest.raises(ValueError, match="non-finite|non-positive"):
        to_per_usd(float(raw), "usd_per")


def test_a_denormal_observation_does_not_invert_to_infinity() -> None:
    """The other side of the same bug: 1 / 5e-324 is inf, and `value <= 0` is False for a denormal."""
    with pytest.raises(ValueError, match="not a usable rate"):
        to_per_usd(5e-324, "usd_per")


def test_an_infinite_observation_is_skipped_and_never_reaches_the_table() -> None:
    csv_text = "observation_date,DEXCHUS\n2026-09-10,Infinity\n2026-09-11,6.7080\n"
    rows = fx_rows("CNY", "DEXCHUS", "per_usd", csv_text, snapshot_ts=TS, since=SINCE)
    assert [r["date"] for r in rows] == ["2026-09-11"]  # the good day survives, the poison does not
    assert all(math.isfinite(r["per_usd"]) for r in rows)

    df = fx_frame(rows)
    FX_SCHEMA.validate(df)  # would also pass with inf in it; the point is that there is none
    assert df["per_usd"].is_finite().all()
    assert all(math.isfinite(v) for v in latest_rates(df).values())


# --- parsing ---------------------------------------------------------------------
def test_empty_observation_is_skipped_not_zero() -> None:
    obs = parse_fred_csv(DEXCHUS_CSV, "DEXCHUS")
    assert [d for d, _ in obs] == ["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    assert "2026-09-07" not in {d for d, _ in obs}
    assert 0.0 not in {v for _, v in obs}


def test_dot_observation_is_skipped_not_zero() -> None:
    obs = parse_fred_csv(DEXJPUS_CSV, "DEXJPUS")
    assert len(obs) == 5
    assert "2026-09-07" not in {d for d, _ in obs}
    assert obs[-1] == ("2026-09-11", 153.71)


def test_both_header_spellings_parse_identically() -> None:
    body = "2026-09-10,6.7099\n2026-09-11,6.7080\n"
    legacy = parse_fred_csv("DATE,DEXCHUS\n" + body, "DEXCHUS")
    current = parse_fred_csv("observation_date,DEXCHUS\n" + body, "DEXCHUS")
    assert legacy == current == [("2026-09-10", 6.7099), ("2026-09-11", 6.7080)]


def test_unexpected_header_raises() -> None:
    """Anything that is not a FRED export must fail loudly, not parse to zero rows."""
    with pytest.raises(ValueError, match="unexpected FRED date column"):
        parse_fred_csv("Day,DEXCHUS\n2026-09-11,6.7080\n", "DEXCHUS")
    with pytest.raises(ValueError, match="empty or headerless"):
        parse_fred_csv("<!doctype html>\n<html>rate limited</html>\n", "DEXCHUS")
    with pytest.raises(ValueError, match="empty or headerless"):
        parse_fred_csv("", "DEXCHUS")


def test_wrong_series_raises() -> None:
    """A silently substituted series would translate every CNY number at the yen rate."""
    with pytest.raises(ValueError, match="expected 'DEXCHUS'"):
        parse_fred_csv(DEXJPUS_CSV, "DEXCHUS")


def test_unparseable_value_is_skipped() -> None:
    obs = parse_fred_csv("observation_date,DEXCHUS\n2026-09-10,n/a\n2026-09-11,6.7080\n", "DEXCHUS")
    assert obs == [("2026-09-11", 6.7080)]


def test_since_cutoff_trims_history() -> None:
    rows = fx_rows("CNY", "DEXCHUS", "per_usd", DEXCHUS_CSV, snapshot_ts=TS, since="2026-09-10")
    assert [r["date"] for r in rows] == ["2026-09-10", "2026-09-11"]
    assert all(r["snapshot_ts"] == TS and r["series"] == "DEXCHUS" and r["currency"] == "CNY" for r in rows)


@pytest.mark.parametrize("day", ["2026-9-8", "09/08/2026", "2026-09-08T00:00:00", "Sep 8 2026"])
def test_a_date_that_fx_schema_would_reject_fails_in_fx_rows(day: str) -> None:
    """FX_SCHEMA's ^\\d{4}-\\d{2}-\\d{2}$ only runs at write time, after all six series are fetched.

    Catching the format break per currency is what turns it into one currency's error instead of a
    traceback that throws away five healthy series' data.
    """
    with pytest.raises(ValueError, match="is not YYYY-MM-DD"):
        fx_rows("CNY", "DEXCHUS", "per_usd", f"observation_date,DEXCHUS\n{day},6.7080\n", snapshot_ts=TS, since=SINCE)


# --- the USD identity row --------------------------------------------------------
def test_identity_rows_are_one_per_date_at_exactly_one() -> None:
    dates = ["2026-09-11", "2026-09-10", "2026-09-11"]  # unsorted, with a duplicate
    rows = identity_rows(dates, snapshot_ts=TS)
    assert [r["date"] for r in rows] == ["2026-09-10", "2026-09-11"]
    assert {r["currency"] for r in rows} == {"USD"}
    assert {r["per_usd"] for r in rows} == {1.0}
    assert {r["series"] for r in rows} == {"identity"}


def test_usd_is_the_identity_entry_in_config() -> None:
    assert FX_SERIES["USD"] == (None, "identity")
    assert FX_SERIES["GBP"] == ("DEXUSUK", "usd_per")


# --- pence are not a currency ----------------------------------------------------
def test_no_pence_unit_is_an_fx_currency() -> None:
    """GBp must never get an fx row: it is a quote unit, not a currency.

    `metrics.PENCE_UNITS` is the single owner of that mapping — fx.py keeps no copy of it, nor of
    `normalise_quote`/`convert`. Two `convert`s with different missing-rate contracts (raise vs
    return None) is how share.py's graceful "no rate, exclude the company" branch turns into a
    KeyError that blanks both project pages.
    """
    assert PENCE_UNITS.keys().isdisjoint(FX_SERIES)
    assert PENCE_UNITS.keys().isdisjoint(SANITY_BANDS)
    assert not hasattr(mod, "convert") and not hasattr(mod, "normalise_quote")
    assert not hasattr(mod, "major_unit") and not hasattr(mod, "PENCE_UNITS")


# --- frame -----------------------------------------------------------------------
def _all_rows() -> list[dict]:
    rows = fx_rows("CNY", "DEXCHUS", "per_usd", DEXCHUS_CSV, snapshot_ts=TS, since=SINCE)
    rows += fx_rows("JPY", "DEXJPUS", "per_usd", DEXJPUS_CSV, snapshot_ts=TS, since=SINCE)
    rows += fx_rows("GBP", "DEXUSUK", "usd_per", DEXUSUK_CSV, snapshot_ts=TS, since=SINCE)
    return rows + identity_rows({r["date"] for r in rows}, snapshot_ts=TS)


def test_frame_validates_against_the_contract() -> None:
    df = fx_frame(_all_rows())
    FX_SCHEMA.validate(df)
    assert df.height == 5 * 4  # 5 good dates x (CNY, JPY, GBP, USD)
    assert set(df.columns) == {"snapshot_ts", "date", "currency", "per_usd", "series"}
    assert df["per_usd"].min() > 0.0


def test_a_revised_observation_is_a_duplicate_key_and_is_reported() -> None:
    """fx_frame silently resolves a duplicate (date, currency) with keep="first", which also means
    FX_SCHEMA's `unique=FX_KEY` can never fire. The tie-break is fine; being silent about it is not."""
    revised = "observation_date,DEXCHUS\n2026-09-11,6.7080\n2026-09-11,6.7155\n"
    rows = fx_rows("CNY", "DEXCHUS", "per_usd", revised, snapshot_ts=TS, since=SINCE)
    assert len(rows) == 2
    assert duplicate_keys(rows) == ["2026-09-11/CNY"]

    df = fx_frame(rows)
    assert df.height == 1 and df["per_usd"][0] == pytest.approx(6.7080)  # first wins, as documented
    assert duplicate_keys(_all_rows()) == []  # a healthy payload has none


def test_latest_rates_takes_the_most_recent_per_currency() -> None:
    rates = latest_rates(fx_frame(_all_rows()))
    assert rates["CNY"] == pytest.approx(6.7080)
    assert rates["JPY"] == pytest.approx(153.71)
    assert rates["GBP"] == pytest.approx(1.0 / 1.3524)
    assert rates["USD"] == 1.0
    assert band_warnings(rates) == []


def test_latest_rates_on_an_empty_frame() -> None:
    assert latest_rates(fx_frame([])) == {}


def test_band_warnings_catch_a_mis_scaled_series() -> None:
    assert band_warnings({"JPY": 1.5371}) != []  # yen quoted in the wrong scale
    assert band_warnings({"ZZZ": 12345.0}) == []  # unknown currency: no band, no opinion


# --- a whole run, offline ----------------------------------------------------------
class _FakeClient:
    """Serves fixtures; one series is dead so the isolation rule can be exercised offline.

    `bodies=` overrides or removes fixtures: a series mapped to None is served as a 503, which is how
    the tests below stage a second run whose currencies are not the first run's.
    """

    BODIES = {
        "DEXCHUS": DEXCHUS_CSV,
        "DEXJPUS": DEXJPUS_CSV,
        "DEXUSUK": DEXUSUK_CSV,
        "DEXHKUS": "observation_date,DEXHKUS\n2026-09-11,7.8427\n",
        "DEXTAUS": "observation_date,DEXTAUS\n2026-09-11,31.6400\n",
        # EUR is the second series quoted as USD-per-unit, so it exercises the inversion alongside GBP
        "DEXUSEU": "observation_date,DEXUSEU\n2026-09-11,1.1604\n",
        "DEXSZUS": "observation_date,DEXSZUS\n2026-09-11,0.8159\n",
    }

    def __init__(self, bodies: dict[str, str | None] | None = None) -> None:
        self.calls = 0
        self.bodies = {**self.BODIES, **(bodies or {})}

    def series_csv(self, series_id: str) -> str:
        self.calls += 1
        body = self.bodies.get(series_id)  # DEXKOUS: the one that falls over
        if body is None:
            raise RuntimeError(f"HTTP 503 for {series_id}")
        return body


def test_one_dead_series_does_not_lose_the_others(tmp_path, monkeypatch) -> None:
    """The non-negotiable isolation rule: KRW dies, every other currency is still written, exit code is 1."""
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    res = mod.snapshot_fx(days=3650, client=_FakeClient())

    assert len(res["errors"]) == 1 and "KRW/DEXKOUS" in res["errors"][0]
    assert set(res["currencies"]) == {"CNY", "JPY", "GBP", "EUR", "CHF", "HKD", "TWD", "USD"}
    assert res["rows"] > 0
    df = pl.read_parquet(res["path"])  # the data we did get was written before the failure was reported
    assert set(df["currency"].unique()) == {"CNY", "JPY", "GBP", "EUR", "CHF", "HKD", "TWD", "USD"}
    assert "KRW" not in set(df["currency"].unique())
    assert res["latest_rates"]["GBP"] == pytest.approx(1.0 / 1.3524, abs=1e-6)

    statuses = {c["name"]: c["status"] for c in res["checks"]}
    assert statuses["FX series fetched"] == "fail"
    assert statuses["Currency coverage"] == "fail"
    assert statuses["Rate plausibility"] == "pass"

    # main() reports the partial success but still exits non-zero
    monkeypatch.setattr(mod, "FredClient", lambda: _FakeClient())
    assert mod.main(["--days", "3650"]) == 1


def test_a_bad_payload_costs_one_currency_not_the_whole_run(tmp_path, monkeypatch) -> None:
    """A date format FX_SCHEMA would reject used to be found only at validate time — after every
    series had been fetched and archived — so it took the parquet, the runs.jsonl line and main()'s
    exit code down with it. One bad payload, six currencies' data in the bin."""
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    res = mod.snapshot_fx(days=3650, client=_FakeClient({"DEXJPUS": "observation_date,DEXJPUS\n2026-9-8,153.88\n"}))

    assert any("JPY/DEXJPUS" in e and "YYYY-MM-DD" in e for e in res["errors"])
    df = pl.read_parquet(res["path"])
    assert set(df["currency"].unique()) == {"CNY", "GBP", "EUR", "CHF", "HKD", "TWD", "USD"}  # everyone else survived
    assert {c["name"]: c["status"] for c in res["checks"]}["Snapshot written"] == "pass"
    assert (tmp_path / "snap" / "valuation" / "runs.jsonl").exists()  # the run is on the record

    monkeypatch.setattr(mod, "FredClient", lambda: _FakeClient({"DEXJPUS": "observation_date,DEXJPUS\n2026-9-8,1.0\n"}))
    assert mod.main(["--days", "3650"]) == 1  # reported, not a traceback


def test_a_second_run_never_shrinks_the_day(tmp_path, monkeypatch) -> None:
    """A partial run is normal (a series was down and is being retried). Writing only this run's rows
    would delete the currencies the earlier run did get, so the day is merged, new rows winning."""
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    first = mod.snapshot_fx(days=3650, client=_FakeClient())
    before = pl.read_parquet(first["path"])
    assert set(before["currency"].unique()) == {"CNY", "JPY", "GBP", "EUR", "CHF", "HKD", "TWD", "USD"}

    # CNY and JPY are down this time; TWD has been revised
    second = mod.snapshot_fx(
        days=3650,
        client=_FakeClient(
            {
                "DEXCHUS": None,
                "DEXJPUS": None,
                "DEXTAUS": "observation_date,DEXTAUS\n2026-09-11,31.9000\n",
            }
        ),
    )
    assert second["path"] == first["path"]  # same date, same file
    df = pl.read_parquet(second["path"])
    assert set(df["currency"].unique()) == {"CNY", "JPY", "GBP", "EUR", "CHF", "HKD", "TWD", "USD"}
    assert df.height >= before.height  # a partial run may add rows, never remove them
    FX_SCHEMA.validate(df)  # still one row per (date, currency)

    rates = latest_rates(df)
    assert rates["CNY"] == pytest.approx(6.7080)  # kept from the first run
    assert rates["TWD"] == pytest.approx(31.9000)  # the new row won on the key


def test_a_run_with_no_rows_writes_nothing_and_still_records_itself(tmp_path, monkeypatch) -> None:
    """An empty table must never land on top of a good one."""
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    good = mod.snapshot_fx(days=3650, client=_FakeClient())
    before = pl.read_parquet(good["path"])

    dead = mod.snapshot_fx(days=3650, client=_FakeClient(dict.fromkeys(_FakeClient.BODIES)))
    assert dead["rows"] == 0 and "path" not in dead
    assert {c["name"]: c["status"] for c in dead["checks"]}["Snapshot written"] == "fail"
    assert pl.read_parquet(good["path"]).equals(before)  # yesterday's... today's good table is intact

    lines = (tmp_path / "snap" / "valuation" / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[-1])["rows"] == 0  # the dead run is still on the record


def test_duplicate_rates_surface_as_a_warning_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    revised = "observation_date,DEXHKUS\n2026-09-11,7.8427\n2026-09-11,7.8500\n"
    res = mod.snapshot_fx(days=3650, client=_FakeClient({"DEXHKUS": revised}))

    dup = next(c for c in res["checks"] if c["name"] == "No duplicate rates")
    assert dup["status"] == "warn" and "2026-09-11/HKD" in dup["detail"]


# --- raw archive ------------------------------------------------------------------
def test_archive_writes_gzip_and_never_overwrites(tmp_path) -> None:
    first = archive_csv(DEXCHUS_CSV, tmp_path, "DEXCHUS", "1830")
    assert gzip.decompress(first.read_bytes()).decode() == DEXCHUS_CSV

    same = archive_csv(DEXCHUS_CSV, tmp_path, "DEXCHUS", "2030")
    assert same == first  # identical body: left alone, no duplicate

    changed = archive_csv(DEXCHUS_CSV + "2026-09-14,6.7011\n", tmp_path, "DEXCHUS", "2030")
    assert changed != first
    assert gzip.decompress(first.read_bytes()).decode() == DEXCHUS_CSV  # the first run's evidence survives


def test_a_third_payload_in_the_same_minute_is_archived_too(tmp_path) -> None:
    """The cron run and a manual re-run can land in the same minute. Whatever the parquet was built
    from has to be in the raw layer, and the returned path has to hold the bytes we were handed."""
    bodies = ["A\n", "B\n", "C\n", "D\n"]
    paths = [archive_csv(b, tmp_path, "DEXCHUS", "1830") for b in bodies]

    assert len(set(paths)) == len(bodies)  # every distinct body got its own file
    for body, path in zip(bodies, paths, strict=True):
        assert gzip.decompress(path.read_bytes()).decode() == body
    assert archive_csv("B\n", tmp_path, "DEXCHUS", "1830") == paths[1]  # a repeat still de-duplicates
