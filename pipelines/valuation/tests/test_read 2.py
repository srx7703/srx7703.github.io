"""Tests for the snapshot readers, and specifically for the difference between the two of them.

``load`` returns one date and ``load_union`` returns the newest row per key across recent dates. The
estimates table is written as one part file per source per date, so reading it with ``load`` means a
week in which only one source answered silently discards the other source's last good file. That is
the defect these tests pin: every fixture below is a directory of parquet parts named the way the
fetchers name them.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from pipelines.valuation import read
from pipelines.valuation.schema import ESTIMATE_KEY, PRICE_KEY


@pytest.fixture
def snaps(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(read, "VAL_DIR", tmp_path)
    return tmp_path


def write(snaps: Path, table: str, name: str, rows: list[dict]) -> Path:
    d = snaps / table
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def est(ticker: str, source: str, ts: str, *, eps: float, period_end: str = "2026-12-31") -> dict:
    return {"snapshot_ts": ts, "ticker": ticker, "source": source, "period_end": period_end, "eps_avg": eps}


def price(ticker: str, ts: str, *, px: float) -> dict:
    return {"snapshot_ts": ts, "ticker": ticker, "price": px, "currency": "USD"}


# --- what `load` does, so the contrast is on the record ---------------------------------------------


def test_load_returns_every_part_of_the_newest_date(snaps):
    write(snaps, "estimates", "2026-09-09-yahoo", [est("COHR", "yahoo", "2026-09-09T20:00:00+00:00", eps=3.0)])
    write(snaps, "estimates", "2026-09-16-yahoo", [est("COHR", "yahoo", "2026-09-16T20:00:00+00:00", eps=3.2)])
    write(snaps, "estimates", "2026-09-16-eastmoney", [est("300308.SZ", "eastmoney", "2026-09-16T20:18:00+00:00", eps=9.0)])

    got = read.load("estimates")

    assert sorted(got["ticker"].to_list()) == ["300308.SZ", "COHR"]
    assert got.filter(pl.col("ticker") == "COHR")["eps_avg"].item() == 3.2


def test_load_of_a_missing_table_is_an_empty_frame_not_an_error(snaps):
    assert read.load("estimates").height == 0
    assert read.load_union("estimates", ESTIMATE_KEY).height == 0
    assert read.latest_date("estimates") is None


# --- the S2 regression: one failing source must not discard the other's good file --------------------


def test_load_union_keeps_a_sources_last_good_file_when_it_fails_this_week(snaps):
    """The defect in one test: Yahoo is rate-limited on the 16th, East Money is not."""
    yahoo = [est(t, "yahoo", "2026-09-09T20:00:00+00:00", eps=3.0) for t in ("COHR", "LITE", "AVGO")]
    em = [est(t, "eastmoney", "2026-09-09T20:18:00+00:00", eps=9.0) for t in ("300308.SZ", "300502.SZ")]
    write(snaps, "estimates", "2026-09-09-yahoo", yahoo)
    write(snaps, "estimates", "2026-09-09-eastmoney", em)
    # a week later only East Money answers
    write(snaps, "estimates", "2026-09-16-eastmoney",
          [est(t, "eastmoney", "2026-09-16T20:18:00+00:00", eps=9.5) for t in ("300308.SZ", "300502.SZ")])

    one_date = read.load("estimates")
    union = read.load_union("estimates", ESTIMATE_KEY)

    assert sorted(one_date["source"].unique().to_list()) == ["eastmoney"]
    assert one_date["ticker"].n_unique() == 2  # every US listing has silently vanished
    assert sorted(union["source"].unique().to_list()) == ["eastmoney", "yahoo"]
    assert sorted(union["ticker"].to_list()) == ["300308.SZ", "300502.SZ", "AVGO", "COHR", "LITE"]
    # and the source that did answer is the fresh one, not last week's copy of itself
    assert union.filter(pl.col("ticker") == "300308.SZ")["eps_avg"].item() == 9.5


def test_load_union_keeps_the_listings_a_partial_price_run_missed(snaps):
    write(snaps, "prices", "2026-09-15", [price(t, "2026-09-15T20:00:00+00:00", px=10.0) for t in ("AAA", "BBB", "CCC")])
    write(snaps, "prices", "2026-09-16", [price("AAA", "2026-09-16T20:00:00+00:00", px=11.0)])

    assert read.load("prices")["ticker"].to_list() == ["AAA"]

    union = read.load_union("prices", PRICE_KEY)
    assert sorted(union["ticker"].to_list()) == ["AAA", "BBB", "CCC"]
    assert union.filter(pl.col("ticker") == "AAA")["price"].item() == 11.0


# --- a stale row must never beat a fresh one -------------------------------------------------------


@pytest.mark.parametrize("names", [("2026-09-09", "2026-09-16"), ("2026-09-16", "2026-09-09")])
def test_the_newest_run_wins_a_key_whatever_order_the_files_are_written_in(snaps, names):
    first, second = names
    write(snaps, "prices", first, [price("AAA", f"{first}T20:00:00+00:00", px=1.0)])
    write(snaps, "prices", second, [price("AAA", f"{second}T20:00:00+00:00", px=2.0)])

    got = read.load_union("prices", PRICE_KEY)

    assert got.height == 1
    assert got["price"].item() == (2.0 if second == "2026-09-16" else 1.0)


def test_freshness_falls_back_to_the_run_date_when_a_table_carries_no_snapshot_ts(snaps):
    """Every table written today carries a timestamp, so this pins the invariant rather than a bug.

    It matters because the ordering is now what keeps a published price honest: with no timestamp
    column the only sort key left used to be the ticker itself, and the winner of a key would then be
    whichever row the concat happened to put last.
    """
    write(snaps, "prices", "2026-09-16", [{"ticker": "AAA", "price": 2.0}])
    write(snaps, "prices", "2026-09-09", [{"ticker": "AAA", "price": 1.0}])

    got = read.load_union("prices", PRICE_KEY)

    assert got.height == 1 and got["price"].item() == 2.0


def test_the_run_date_helper_column_never_reaches_the_caller(snaps):
    write(snaps, "prices", "2026-09-16", [price("AAA", "2026-09-16T20:00:00+00:00", px=2.0)])

    assert read.load_union("prices", PRICE_KEY).columns == ["snapshot_ts", "ticker", "price", "currency"]


# --- staleness has a bound -------------------------------------------------------------------------


def test_max_age_days_drops_a_run_older_than_the_bound_and_keeps_one_inside_it(snaps):
    write(snaps, "prices", "2026-09-01", [price("OLD", "2026-09-01T20:00:00+00:00", px=1.0)])
    write(snaps, "prices", "2026-09-14", [price("RECENT", "2026-09-14T20:00:00+00:00", px=2.0)])
    write(snaps, "prices", "2026-09-16", [price("FRESH", "2026-09-16T20:00:00+00:00", px=3.0)])

    inside = read.load_union("prices", PRICE_KEY, max_age_days=8)
    unbounded = read.load_union("prices", PRICE_KEY)

    assert sorted(inside["ticker"].to_list()) == ["FRESH", "RECENT"]
    assert sorted(unbounded["ticker"].to_list()) == ["FRESH", "OLD", "RECENT"]


def test_limit_dates_bounds_how_far_back_a_union_reaches(snaps):
    for day in range(10, 20):
        write(snaps, "prices", f"2026-09-{day}", [price(f"T{day}", f"2026-09-{day}T20:00:00+00:00", px=1.0)])

    assert read.load_union("prices", PRICE_KEY, limit_dates=3)["ticker"].to_list() == ["T17", "T18", "T19"]


def test_an_empty_part_file_does_not_break_the_union(snaps):
    write(snaps, "prices", "2026-09-15", [price("AAA", "2026-09-15T20:00:00+00:00", px=1.0)])
    pl.DataFrame(schema={"snapshot_ts": pl.Utf8, "ticker": pl.Utf8, "price": pl.Float64}).write_parquet(
        snaps / "prices" / "2026-09-16.parquet"
    )

    assert read.load_union("prices", PRICE_KEY)["ticker"].to_list() == ["AAA"]
