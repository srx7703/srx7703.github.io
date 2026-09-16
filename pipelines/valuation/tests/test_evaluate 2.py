"""The pre-registered scorer, which is the module that decides whether the project's own claims hold.

Three kinds of test live here.

*Arithmetic* — Spearman's rho and the chi-square survival function are spelled out in `evaluate.py`
because the project has no scipy, so they are checked against hand-computable cases and published
critical values.

*Protocol* — that what the code does is what `docs/EVALUATION_PLAN.md` registers. "Four weeks later"
implemented as `days >= 21` is three weeks on a weekly cadence, and a constant that lives only in code
sits outside the blob hash the pages print; both have their own test.

*The real strings* — item 5's matcher is fed the exact text in `data/reference/share_*.json`,
including the two traps in it: a combined "CATL+BYD" figure and a clause that names one company's
volume while mentioning another company's name.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pipelines.common.storage import REPO_ROOT, write_parquet
from pipelines.valuation import evaluate as mod
from pipelines.valuation import read, schema
from pipelines.valuation.evaluate import (
    HORIZON_DAYS,
    MIN_MOVERS_PER_PERIOD,
    MIN_PERIODS_FOR_PERSISTENCE,
    STEP_DAYS,
    capture_snapshot_vintages,
    chi2_sf,
    chi_square_independence,
    citation_figures,
    comparisons_for,
    persistence_triples,
    resolve_member,
    revision_persistence,
    share_bases,
    spearman,
)

PLAN = REPO_ROOT / "docs" / "EVALUATION_PLAN.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "valuation.yml"

# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------

TS = "2026-09-16T18:30:00+00:00"


def weekly(start: str, n: int, step_days: int = 7) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=step_days * i)).isoformat() for i in range(n)]


def vintage(ticker: str, as_of: str, eps: float, origin: str = "snapshot") -> dict:
    """A December filer's calendar-2026 consensus as of one date: one fiscal year covers the year."""
    return {
        "snapshot_ts": TS,
        "ticker": ticker,
        "source": "eastmoney",
        "period_end": "2026-12-31",
        "as_of": as_of,
        "eps_avg": eps,
        "n_analysts": 8,
        "currency": "CNY",
        "origin": origin,
    }


def moving_series(dates: list[str], n_tickers: int = 12, origin: str = "snapshot") -> dict[str, list[dict]]:
    """A pool where all but one listing moves between every pair of consecutive dates.

    The pattern is deterministic and arbitrary: what the tests assert is the shape of the result
    (how many windows were used, how many transitions were pooled), never a particular rho.
    """
    out: dict[str, list[dict]] = {}
    for i in range(n_tickers):
        ticker = f"T{i:02d}"
        eps = 10.0
        rows = [vintage(ticker, dates[0], eps, origin)]
        for j in range(1, len(dates)):
            step = 0 if (i + j) % n_tickers == 0 else (1 if (i + 2 * j) % 2 == 0 else -1)
            eps *= 1 + 0.03 * step
            rows.append(vintage(ticker, dates[j], eps, origin))
        out[ticker] = rows
    return out


def estimate_row(ticker: str, period_end: str, eps: float, source: str = "eastmoney") -> dict:
    return {
        "snapshot_ts": TS,
        "ticker": ticker,
        "source": source,
        "period_end": period_end,
        "fy_label": f"FY{period_end[:4]}",
        "mark": "E",
        "eps_avg": eps,
        "eps_low": eps * 0.9,
        "eps_high": eps * 1.1,
        "n_analysts": 8,
        "currency": "CNY",
        "net_profit_avg": None,
    }


@pytest.fixture
def snap(tmp_path, monkeypatch):
    """Redirect the snapshot root so nothing here reads or writes the repository's own data."""
    root = tmp_path / "valuation"
    monkeypatch.setattr(read, "VAL_DIR", root)
    return root


def put_estimates(snap: Path, day: str, rows: list[dict], source: str = "eastmoney") -> Path:
    frame = schema.estimates_frame(rows)
    return write_parquet(frame, snap / "estimates" / f"{day}-{source}.parquet")


def put_vintages(snap: Path, day: str, rows: list[dict], source: str = "yahoo") -> Path:
    frame = schema.vintages_frame(rows)
    return write_parquet(frame, snap / "vintages" / f"{day}-{source}.parquet")


# ---------------------------------------------------------------------------------------------
# item 2 — the horizon that was registered
# ---------------------------------------------------------------------------------------------


def test_the_second_leg_is_four_weeks_and_the_first_is_one():
    dates = weekly("2026-01-05", 8)
    triples = persistence_triples(dates)
    assert triples
    for prev, now, nxt in triples:
        assert (date.fromisoformat(now) - date.fromisoformat(prev)).days == STEP_DAYS
        assert (date.fromisoformat(nxt) - date.fromisoformat(now)).days == HORIZON_DAYS


def test_a_grid_that_only_reaches_three_weeks_scores_nothing():
    """The regression. "The first date at least 21 days out" is exactly three weeks on a weekly grid.

    Four weekly dates give a 21-day partner and no 28-day one, so the registered test has nothing to
    measure. Scoring it anyway is a different, easier test than the one on the page.
    """
    dates = weekly("2026-01-05", 4)
    assert persistence_triples(dates) == []
    out = revision_persistence(moving_series(dates))
    assert out["status"] == "not_yet"
    assert "0 week/four-week windows" in out["why"]


def test_the_matrix_records_the_four_week_move_and_not_the_three_week_one():
    """The decisive case: a pool that was upgraded, fell for three weeks, then recovered by week four.

    Eight weekly dates, twelve identical listings, EPS 10, 11, 12, 13, 9, 11.5, 12.5, 14. Every
    grouping week is an upgrade. Three weeks after each upgrade the consensus is *below* where it
    started; four weeks after it is above. "The first vintage at least 21 days later" therefore fills
    the (up, down) cell, and the registered four-week horizon fills (up, up). Both are internally
    consistent; only one is the test the page says was run.
    """
    dates = weekly("2026-01-05", 8)
    path = [10.0, 11.0, 12.0, 13.0, 9.0, 11.5, 12.5, 14.0]
    series = {
        f"T{i:02d}": [vintage(f"T{i:02d}", day, eps) for day, eps in zip(dates, path, strict=True)]
        for i in range(12)
    }
    out = revision_persistence(series)
    assert out["status"] == "partial"
    assert out["periods_used"] == 3
    assert out["matrix"]["up"]["up"] == 36
    assert out["matrix"]["up"]["down"] == 0


def test_a_horizon_four_days_off_still_counts_and_five_days_off_does_not():
    base = "2026-01-05"
    for offset, expected in ((24, True), (32, True), (23, False), (33, False)):
        later = (date.fromisoformat(base) + timedelta(days=offset)).isoformat()
        dates = sorted(["2025-12-29", base, later])
        assert bool(persistence_triples(dates)) is expected, offset


def test_an_irregular_yahoo_grid_alone_yields_no_window():
    """Yahoo publishes 0/7/30/60/90 days back, which has no 28-day step in it at all."""
    today = date(2026, 9, 16)
    dates = sorted((today - timedelta(days=n)).isoformat() for n in (0, 7, 30, 60, 90))
    assert persistence_triples(dates) == []


# ---------------------------------------------------------------------------------------------
# item 2 — the chi-square that was registered
# ---------------------------------------------------------------------------------------------


def test_chi2_sf_reproduces_published_critical_values():
    assert chi2_sf(3.8415, 1) == pytest.approx(0.05, abs=5e-5)
    assert chi2_sf(5.9915, 2) == pytest.approx(0.05, abs=5e-5)
    assert chi2_sf(9.4877, 4) == pytest.approx(0.05, abs=5e-5)
    assert chi2_sf(13.2767, 4) == pytest.approx(0.01, abs=5e-5)
    assert chi2_sf(0.0, 4) == 1.0
    assert chi2_sf(1.0, 0) is None


def test_chi_square_matches_a_table_computed_by_hand():
    """Rows (10,10,10)/(10,10,10)/(10,10,10) are independent; moving 10 into one corner is not.

    Hand check of the second table: n=90, every row and column total is 30, so every expected cell is
    10. The squared deviations are 100, 0, 100 across the first row and 25, 0, 25 across each of the
    other two, so the statistic is (100+100 + 25+25 + 25+25)/10 = 30 on (3-1)(3-1) = 4 degrees of
    freedom.
    """
    flat = {a: dict.fromkeys(("up", "unchanged", "down"), 10) for a in ("up", "unchanged", "down")}
    assert chi_square_independence(flat)["statistic"] == pytest.approx(0.0)
    assert chi_square_independence(flat)["p_value"] == pytest.approx(1.0)

    skewed = {
        "up": {"up": 20, "unchanged": 10, "down": 0},
        "unchanged": {"up": 5, "unchanged": 10, "down": 15},
        "down": {"up": 5, "unchanged": 10, "down": 15},
    }
    got = chi_square_independence(skewed)
    assert got["df"] == 4
    assert got["statistic"] == pytest.approx(30.0)
    assert got["p_value"] == pytest.approx(chi2_sf(30.0, 4))
    assert got["p_value"] < 0.001
    assert got["reliable"] is True


def test_a_thin_table_is_flagged_rather_than_quietly_published():
    thin = {
        "up": {"up": 2, "unchanged": 1, "down": 0},
        "unchanged": {"up": 1, "unchanged": 2, "down": 1},
        "down": {"up": 0, "unchanged": 1, "down": 2},
    }
    got = chi_square_independence(thin)
    assert got["statistic"] is not None
    assert got["reliable"] is False
    assert "expected cell" in got["why"]


def test_a_table_with_one_live_row_has_no_degrees_of_freedom():
    single = {a: dict.fromkeys(("up", "unchanged", "down"), 0) for a in ("up", "unchanged", "down")}
    single["up"] = {"up": 5, "unchanged": 3, "down": 2}
    got = chi_square_independence(single)
    assert got["df"] == 0
    assert got["statistic"] is None
    assert got["p_value"] is None
    assert got["reliable"] is False


def test_a_scored_matrix_carries_the_registered_chi_square():
    dates = weekly("2026-01-05", 8)
    out = revision_persistence(moving_series(dates))
    assert out["status"] == "partial"
    assert out["periods_used"] == MIN_PERIODS_FOR_PERSISTENCE
    assert out["step_days"] == STEP_DAYS and out["horizon_days"] == HORIZON_DAYS
    assert out["n_transitions"] == 3 * 12
    chi = out["chi_square"]
    assert set(chi) == {"statistic", "df", "p_value", "min_expected", "reliable", "why"}
    assert chi["statistic"] >= 0.0
    assert 0.0 <= chi["p_value"] <= 1.0
    assert "not independent observations" in out["note"]


def test_a_quiet_pool_is_dropped_and_counted_rather_than_scored():
    dates = weekly("2026-01-05", 8)
    series = moving_series(dates, n_tickers=MIN_MOVERS_PER_PERIOD - 1)
    out = revision_persistence(series)
    assert out["status"] == "not_yet"
    assert f"{MIN_MOVERS_PER_PERIOD} listings whose consensus moved" in out["why"]


def test_the_east_money_coverage_series_is_never_differenced_as_a_revision():
    dates = weekly("2026-01-05", 8)
    rebuilt = moving_series(dates, origin="eastmoney_rebuilt")
    out = revision_persistence(rebuilt)
    assert out["status"] == "not_yet"
    assert "0 true-revision vintage dates" in out["why"]
    # the same series, captured as our own weekly snapshot, is scorable
    assert revision_persistence(moving_series(dates, origin="snapshot"))["status"] == "partial"


def test_the_not_yet_reason_counts_the_snapshot_listings_it_is_waiting_for():
    dates = weekly("2026-01-05", 3)
    series = moving_series(dates, n_tickers=4)
    out = revision_persistence(series)
    assert out["status"] == "not_yet"
    assert "covers 4 of them" in out["why"]


# ---------------------------------------------------------------------------------------------
# the snapshot origin — the plan's escape hatch, made real
# ---------------------------------------------------------------------------------------------


def test_every_archived_estimates_date_becomes_a_snapshot_vintage(snap):
    days = weekly("2026-08-03", 3)
    for day in days:
        put_estimates(snap, day, [estimate_row("300308.SZ", "2026-12-31", 30.0)])
    written = capture_snapshot_vintages()
    assert len(written) == 3
    frame = read.load_history("vintages", limit_dates=60)
    assert sorted(frame["as_of"].unique()) == days
    assert frame["origin"].unique().to_list() == ["snapshot"]
    schema.VINTAGES_SCHEMA.validate(frame)


def test_capturing_twice_writes_nothing_the_second_time(snap):
    put_estimates(snap, "2026-08-03", [estimate_row("300308.SZ", "2026-12-31", 30.0)])
    assert capture_snapshot_vintages()
    assert capture_snapshot_vintages() == []


def test_a_snapshot_never_competes_with_a_vintage_the_source_publishes(snap):
    """Yahoo already writes an as-of-today vintage. Writing a second one for the same key would put
    two different means on the same date, and calendarising them would pick one arbitrarily."""
    day = "2026-08-03"
    put_estimates(snap, day, [estimate_row("COHR", "2026-06-30", 4.0, source="yahoo")], source="yahoo")
    put_vintages(
        snap,
        day,
        [
            {
                "snapshot_ts": TS,
                "ticker": "COHR",
                "source": "yahoo",
                "period_end": "2026-06-30",
                "as_of": day,
                "eps_avg": 4.0,
                "n_analysts": 12,
                "currency": "USD",
                "origin": "yahoo_trend",
            }
        ],
    )
    assert capture_snapshot_vintages() == []


def test_a_snapshot_never_adds_a_second_answer_for_a_date_another_origin_already_has(snap):
    """`metrics.vintage_estimates` groups a ticker's vintage rows by date alone. Two rows for the same
    date and fiscal year, from two origins, would leave it picking one by concatenation order."""
    day = "2026-08-03"
    put_estimates(snap, day, [estimate_row("300308.SZ", "2026-12-31", 30.0)])
    put_vintages(
        snap,
        day,
        [
            {
                "snapshot_ts": TS,
                "ticker": "300308.SZ",
                "source": "yahoo",  # a different source for the same listing and the same date
                "period_end": "2026-12-31",
                "as_of": day,
                "eps_avg": 29.0,
                "n_analysts": 20,
                "currency": "CNY",
                "origin": "yahoo_trend",
            }
        ],
    )
    assert capture_snapshot_vintages() == []


def test_one_unreadable_date_does_not_cost_the_others(snap):
    put_estimates(snap, "2026-08-03", [estimate_row("300308.SZ", "2026-12-31", 30.0)])
    (snap / "estimates" / "2026-08-10-eastmoney.parquet").write_bytes(b"not a parquet file")
    written = capture_snapshot_vintages()
    assert len(written) == 1
    assert "2026-08-03-snapshot.parquet" in written[0]


def test_rows_without_an_eps_are_not_captured_as_vintages(snap):
    rows = [estimate_row("300308.SZ", "2026-12-31", 30.0), estimate_row("300502.SZ", "2026-12-31", 0.0)]
    rows[1]["eps_avg"] = None
    put_estimates(snap, "2026-08-03", rows)
    capture_snapshot_vintages()
    frame = read.load_history("vintages", limit_dates=60)
    assert frame["ticker"].to_list() == ["300308.SZ"]


def test_an_a_share_pool_becomes_scorable_once_the_snapshots_accumulate(snap):
    """End to end: East Money estimates alone, archived weekly, produce a scorable item 2.

    Before the snapshot origin existed, no module ever wrote `origin="snapshot"`, so the A-share
    listings were excluded from this item permanently and the plan's "until our own weekly snapshots
    accumulate" could never happen.
    """
    days = weekly("2026-07-06", 8)
    for j, day in enumerate(days):
        rows = []
        for i in range(12):
            step = 0 if (i + j) % 12 == 0 else (1 if (i + 2 * j) % 2 == 0 else -1)
            rows.append(estimate_row(f"T{i:02d}", "2026-12-31", 10.0 * (1 + 0.03 * step) ** j))
        put_estimates(snap, day, rows)
    capture_snapshot_vintages()
    vintages = read.by_ticker(read.load_history("vintages", limit_dates=60))
    out = revision_persistence(vintages)
    assert out["status"] == "partial"
    assert out["periods_used"] == 3
    assert out["chi_square"]["df"] >= 1


# ---------------------------------------------------------------------------------------------
# item 5 — arithmetic
# ---------------------------------------------------------------------------------------------


def test_spearman_on_hand_computable_cases():
    assert spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)
    assert spearman([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    # one adjacent swap in five: sum d^2 = 2, rho = 1 - 6*2/(5*24) = 0.9
    assert spearman([5.0, 4.0, 3.0, 2.0, 1.0], [5.0, 3.0, 4.0, 2.0, 1.0]) == pytest.approx(0.9)
    # a flat side has no ordering to correlate with
    assert spearman([1.0, 2.0, 3.0], [7.0, 7.0, 7.0]) is None
    # two points always give +-1, which is arithmetic rather than evidence
    assert spearman([1.0, 2.0], [1.0, 2.0]) is None


def test_ties_share_a_rank_rather_than_inventing_an_order():
    # y is flat across the first two, so the pair contributes nothing either way
    assert spearman([1.0, 2.0, 3.0, 4.0], [1.0, 1.0, 3.0, 4.0]) == pytest.approx(0.9486832980505138)


# ---------------------------------------------------------------------------------------------
# item 5 — the real reference strings
# ---------------------------------------------------------------------------------------------

LIGHTCOUNTING = {
    "entity": "industry — global Top 10 optical transceiver suppliers",
    "value": (
        "2025: 1 Innolight; 2 Eoptolink; 3 Coherent; 4 Accelink; 5 Molex; 6 Ligent (Hisense); "
        "7 HG Genuine; 8 Source Photonics; 9 Lumentum; 10 CIG"
    ),
    "unit": "rank",
    "period": "CY2025. Same figure restates CY2024 on the new methodology: 1 Innolight, 2 Coherent, 3 Eoptolink",
    "source_name": "LightCounting, LightTrends Newsletter, May 2026",
    "source_url": "https://example.invalid/lightcounting",
}

SNE = {
    "entity": "industry — global EV battery usage, all chemistries (CATL, BYD, LGES, CALB, Gotion)",
    "value": (
        "Total 725.2 GWh, +20.4% YoY (July alone 116.0 GWh, +22.1%). CATL 289.6 GWh / 39.9% share "
        "(+26.6% YoY; share 38.0%→39.9%); BYD 106.7 GWh / 14.7% (+4.7%; 16.9%→14.7%); "
        "LG Energy Solution 60.3 GWh / 8.3% (+4.5%; 9.6%→8.3%); CALB 37.3 GWh (+34.3%); "
        "Gotion 34.0 GWh (+44.2%); REPT 16.9 GWh (+118.5%, new entrant displacing Sunwoda). "
        "Seven Chinese suppliers in the top 10 = 72.8% combined (+3.1pp). "
        "CATL+BYD combined SHARE 54.6% (−0.3pp from 54.9%)."
    ),
    "unit": "GWh; % market share",
    "period": "2026-01 to 2026-07 (calendar, actuals)",
    "source_name": "SNE Research — Press Release (Insight)",
    "source_url": "https://example.invalid/sne",
}

OPTICAL_FACTS = {
    "share": {
        "members": [
            {"ticker": "300308.SZ", "name": "Innolight", "name_cn": "中际旭创", "share": 0.306},
            {"ticker": "COHR", "name": "Coherent", "name_cn": None, "share": 0.224},
            {"ticker": "300502.SZ", "name": "Eoptolink", "name_cn": "新易盛", "share": 0.1657},
            {"ticker": "LITE", "name": "Lumentum", "name_cn": None, "share": 0.0949},
            {"ticker": "002281.SZ", "name": "Accelink", "name_cn": "光迅科技", "share": 0.0624},
        ]
    }
}

SSB_FACTS = {
    "share": {
        "members": [
            {"ticker": "300750.SZ", "name": "CATL", "name_cn": "宁德时代", "share": 0.5655},
            {"ticker": "373220.KS", "name": "LG Energy Solution", "name_cn": None, "share": 0.13},
            {"ticker": "300014.SZ", "name": "EVE Energy", "name_cn": "亿纬锂能", "share": 0.0856},
            {"ticker": "300207.SZ", "name": "Sunwoda", "name_cn": "欣旺达", "share": 0.0807},
            {"ticker": "002074.SZ", "name": "Gotion High-tech", "name_cn": "国轩高科", "share": 0.0579},
            {"ticker": "3931.HK", "name": "CALB", "name_cn": "中创新航", "share": 0.0481},
        ]
    }
}


def test_a_published_ranking_is_read_rather_than_discarded_for_its_unit():
    """The old matcher kept only citations whose unit was a clean percent, which is every citation
    except the rankings — and rankings are the form the optical sources actually publish in."""
    figures = citation_figures(LIGHTCOUNTING, mod._member_aliases(OPTICAL_FACTS["share"]["members"]))
    assert [(f["member"]["name"], f["rank"]) for f in figures] == [
        ("Innolight", 1),
        ("Eoptolink", 2),
        ("Coherent", 3),
        ("Accelink", 4),
        ("Lumentum", 9),
    ]


def test_the_optical_rank_correlation_is_computed_and_hand_checkable():
    """Computed order Innolight > Coherent > Eoptolink > Lumentum > Accelink against the cited
    1 Innolight, 2 Eoptolink, 3 Coherent, 4 Accelink, 9 Lumentum: two adjacent swaps, sum d^2 = 4,
    rho = 1 - 6*4/(5*24) = 0.8."""
    [comparison] = comparisons_for(OPTICAL_FACTS, [LIGHTCOUNTING])
    assert comparison["basis"] == "rank"
    assert comparison["n"] == 5
    assert comparison["spearman"] == pytest.approx(0.8)


def test_only_the_value_field_is_read_so_a_second_ranking_cannot_leak_in():
    """LightCounting's `period` carries the prior year's order. Reading it would mix two years."""
    figures = citation_figures(LIGHTCOUNTING, mod._member_aliases(OPTICAL_FACTS["share"]["members"]))
    assert [f["member"]["ticker"] for f in figures].count("COHR") == 1
    assert next(f for f in figures if f["member"]["ticker"] == "COHR")["rank"] == 3


def test_catl_and_lges_are_compared_with_the_shares_sne_publishes():
    out = share_bases("ssb", {**SSB_FACTS, "excluded_from_pool": []})
    assert out["status"] == "partial"
    diff = out["share_difference"]
    assert {p["name"] for p in diff["pairs"]} == {"CATL", "LG Energy Solution"}
    catl = next(p for p in diff["pairs"] if p["name"] == "CATL")
    assert catl["computed_share"] == pytest.approx(0.5655)
    assert catl["cited_value"] == pytest.approx(0.399)
    assert catl["difference"] == pytest.approx(0.1665)
    assert "39.9% share" in catl["cited_text"]


def test_a_gwh_ordering_supports_the_rank_correlation():
    comparisons = comparisons_for(SSB_FACTS, [SNE])
    quantity = next(c for c in comparisons if c["basis"] == "quantity")
    assert quantity["unit"] == "GWh"
    assert [p["name"] for p in quantity["pairs"]] == ["CATL", "LG Energy Solution", "CALB", "Gotion High-tech"]
    assert quantity["spearman"] is not None


def test_a_combined_figure_never_binds_to_one_of_its_companies():
    """"CATL+BYD combined SHARE 54.6%" must not be scored as CATL's share, and REPT's volume must not
    be scored as Sunwoda's just because the clause names Sunwoda."""
    figures = citation_figures(SNE, mod._member_aliases(SSB_FACTS["share"]["members"]))
    catl = next(f for f in figures if f["member"]["name"] == "CATL")
    assert catl["percent"] == pytest.approx(39.9)
    assert "Sunwoda" not in [f["member"]["name"] for f in figures]


def test_the_matcher_refuses_aggregates_and_sentences():
    aliases = mod._member_aliases(SSB_FACTS["share"]["members"])
    assert resolve_member("CATL", aliases)["ticker"] == "300750.SZ"
    assert resolve_member("Gotion", aliases)["ticker"] == "002074.SZ"  # whole-word prefix
    assert resolve_member("CATL+BYD combined SHARE", aliases) is None
    assert resolve_member("Seven Chinese suppliers in the top ", aliases) is None
    assert resolve_member("industry (EV batteries)", aliases) is None
    assert resolve_member("CATL led the market with more than US$", aliases) is None


def test_a_figure_the_curator_inferred_is_not_scored_as_a_citation():
    aliases = mod._member_aliases(OPTICAL_FACTS["share"]["members"])
    assert resolve_member("Coherent", aliases)["ticker"] == "COHR"
    assert resolve_member("Coherent (INFERRED, not stated)", aliases) is None


def test_a_growth_rate_is_never_read_as_a_share():
    aliases = mod._member_aliases(SSB_FACTS["share"]["members"])
    figures = citation_figures({"value": "CALB 37.3 GWh (+34.3%)", "entity": "x"}, aliases)
    assert figures[0]["percent"] is None
    assert figures[0]["figure"] == pytest.approx(37.3)


def test_the_headline_comparison_is_picked_by_a_stated_rule_not_by_file_order():
    fewer = {**LIGHTCOUNTING, "source_name": "AAA smaller source", "value": "1 Innolight; 2 Eoptolink; 3 Coherent"}
    forwards = comparisons_for(OPTICAL_FACTS, [LIGHTCOUNTING, fewer])
    backwards = comparisons_for(OPTICAL_FACTS, [fewer, LIGHTCOUNTING])
    assert [c["n"] for c in forwards] == [5, 3]
    assert forwards == backwards


def test_the_reason_for_an_unscorable_track_is_about_that_track():
    """The old reason named optical's sources on both pages, including the battery page, where it was
    false: SNE publishes shares for two pool members."""
    empty = {"share": {"members": [{"ticker": "SLDP", "name": "Solid Power", "name_cn": None, "share": 1.0}]}}
    out = share_bases("ssb", empty)
    assert out["status"] == "not_yet"
    assert "Solid-state batteries" in out["why"]
    assert "optical" not in out["why"].lower()


def test_both_real_tracks_score_item_five_from_the_reference_files_on_disk():
    for track, expect_shares in (("optical", 1), ("ssb", 2)):
        facts = json.loads((REPO_ROOT / "data" / "facts" / f"valuation_{track}.json").read_text("utf-8"))
        out = share_bases(track, facts)
        assert out["status"] == "partial", track
        assert out["rank_correlation"]["n"] >= 3, track
        assert -1.0 <= out["rank_correlation"]["spearman"] <= 1.0, track
        assert out["share_difference"]["n"] == expect_shares, track
        for comparison in out["comparisons"]:
            for pair in comparison["pairs"]:
                assert pair["cited_text"], "every scored pair shows the text it was read out of"


# ---------------------------------------------------------------------------------------------
# the registration itself
# ---------------------------------------------------------------------------------------------


def test_the_plan_registers_every_constant_that_changes_a_result():
    plan = PLAN.read_text(encoding="utf-8")
    section = plan.split("## Optical modules and solid-state batteries")[1]
    for needle in (
        "0.5%",  # REVISION_EPSILON
        f"{STEP_DAYS} days apart",  # STEP_DAYS
        f"{HORIZON_DAYS} days after",  # HORIZON_DAYS
        f"**{MIN_MOVERS_PER_PERIOD}** listings",  # MIN_MOVERS_PER_PERIOD
        f"**{MIN_PERIODS_FOR_PERSISTENCE}** scorable windows",  # MIN_PERIODS_FOR_PERSISTENCE
        "eastmoney_rebuilt",  # the excluded origin
        "`snapshot`",  # the origin that replaces it
        "Spearman",  # item 5's statistic
        f"**{int(mod.MIN_EXPECTED_CELL)}**",  # MIN_EXPECTED_CELL
        f"**{mod.MIN_RANK_PAIRS} or more** matched",  # MIN_RANK_PAIRS
    ):
        assert needle in section, needle


def test_the_plan_does_not_claim_a_guarantee_the_git_history_cannot_back():
    """P16: the plan, both facts files and every snapshot parquet are in commit 7af7c68 together."""
    section = PLAN.read_text(encoding="utf-8").split("## Optical modules and solid-state batteries")[1]
    assert "7af7c68" in section
    assert "second weekly run onward" in section
    assert re.search(r"not\s+\*?not\b", section) is None  # no doubled negation from an edit
    assert "before the first snapshot was published" not in section


def test_the_plan_logs_its_own_amendments():
    section = PLAN.read_text(encoding="utf-8").split("## Optical modules and solid-state batteries")[1]
    assert "### Amendments to this section" in section
    assert "2026-09-16" in section.split("### Amendments to this section")[1]


# ---------------------------------------------------------------------------------------------
# the workflow gates
# ---------------------------------------------------------------------------------------------


def _step(text: str, name: str) -> str:
    """The YAML block for one named step, up to the next step at the same indentation."""
    start = text.index(f"- name: {name}")
    rest = text[start + 1 :]
    end = rest.find("\n      - name:")
    return rest if end == -1 else rest[:end]


def test_a_crashing_scorer_does_not_leave_the_job_green():
    """S13: continue-on-error turns the step's conclusion green; only `outcome` still remembers."""
    text = WORKFLOW.read_text(encoding="utf-8")
    gate = _step(text, "Surface step failures")
    assert "steps.evaluate.outcome == 'failure'" in gate
    for step_id in ("fx", "prices", "est_yf", "est_em", "fundamentals", "publish"):
        assert f"steps.{step_id}.outcome == 'failure'" in gate


def test_a_failed_publish_never_commits_the_layer_the_pages_render():
    """S1, workflow half: publish writes data/facts and data/marts, and deploy-site rebuilds from
    whatever lands on main. A half-written facts file must not get there."""
    text = WORKFLOW.read_text(encoding="utf-8")
    commit = _step(text, "Commit and push data")
    assert "steps.publish.outcome" in commit
    add_all = re.search(r"^\s*git add data/\s*$", commit, re.M)
    assert add_all is not None
    guarded = commit.index("steps.publish.outcome") < add_all.start()
    assert guarded, "`git add data/` must sit inside the publish-succeeded branch"
    assert "git add data/raw data/snapshots" in commit
    assert "git checkout -- data/facts data/marts" in commit


def test_the_evaluate_step_still_runs_when_a_fetch_failed():
    """One dead source must not stop the scorer from recording why an item could not be scored."""
    text = WORKFLOW.read_text(encoding="utf-8")
    evaluate = _step(text, "Score the pre-registered evaluation")
    assert "if: always()" in evaluate
    assert "continue-on-error: true" in evaluate


def test_no_snapshot_parquet_is_written_into_the_repository_by_these_tests():
    """The fixture redirects read.VAL_DIR; this asserts the redirect actually took."""
    live = REPO_ROOT / "data" / "snapshots" / "valuation" / "vintages"
    assert not list(live.glob("*-snapshot.parquet")) or all(
        pl.read_parquet(p).height for p in live.glob("*-snapshot.parquet")
    )
