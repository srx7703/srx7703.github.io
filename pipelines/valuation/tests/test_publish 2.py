"""Tests for the last step before a public page: the marts, the facts, and what may overwrite them.

Publish had no tests, and it is the one module in the valuation pipeline whose output is read directly
by a page. Four acceptance findings lived here — a run with no data could blank both pages and the
blank version auto-deployed; the estimates and prices were read one date at a time, so a single
rate-limited source discarded the other's good file; the solid-state "pure plays with no earnings" set
was selected on an exposure label rather than on earnings; and a dual listing's trailing multiple could
be a share-count artefact.

Every fixture is a hand-built pool of three to five listings with figures that can be checked by hand.
The real company list is deliberately not used: it is edited often, and a test that breaks when a
company moves between exposure buckets is testing the config, not the code.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pandera.errors import SchemaError

from pipelines.valuation import publish as mod
from pipelines.valuation import read
from pipelines.valuation.config import Company

AS_OF = date(2026, 9, 16)
STAMP = "2026-09-16T21:00:00+00:00"
RATES = {"USD": 1.0, "CNY": 7.0, "HKD": 7.8}

# --- a pool small enough to check by hand -----------------------------------------------------------

ALPHA = Company("AAA", "Alpha", "optical", "high", "us", 12, share_basis="total")
BETA = Company("BBB", "Beta", "optical", "high", "us", 12, share_basis="total")
GAMMA = Company("CCC", "Gamma", "optical", "high", "us", 12, share_basis="total")
DELTA = Company("DDD", "Delta", "optical", "high", "us", 12, share_basis="total")
EPSILON = Company("EEE", "Epsilon", "optical", "high", "us", 12, share_basis="total")
SUPPLIER = Company("SUP", "Supplier", "optical", "partial", "us", 12, share_basis="none",
                   share_note="sells chips into modules, one layer down")
DIVERSIFIED = Company("DIV", "Diversified", "optical", "partial", "us", 12, share_basis="segment")

# YOFC's real numbers: the A line's cap over its price implies 821.9m shares and the H line's implies
# 1,423.7m, against one set of accounts.
YOFC_A = Company("601869.SS", "YOFC", "optical", "partial", "cn", 12, share_basis="total")
YOFC_H = Company("6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
                 fundamentals_from="601869.SS", share_basis="total")

# Pure plays for the solid-state track: one profitable, four not. Microvast is the real case.
MVST = Company("MVST", "Microvast", "ssb", "high", "us", 12, share_basis="total")
QS = Company("QS", "QuantumScape", "ssb", "high", "us", 12, share_basis="total")
CATL = Company("300750.SZ", "CATL", "ssb", "main", "cn", 12, share_basis="total")

SIMPLE = [ALPHA, BETA, GAMMA, DELTA, EPSILON]


def price(ticker: str, px: float, *, ccy: str = "USD", cap: float | None = None,
          ts: str = "2026-09-16T20:00:00+00:00") -> dict:
    return {"snapshot_ts": ts, "ticker": ticker, "price": px, "currency": ccy,
            "financial_currency": ccy, "market_cap": cap if cap is not None else px * 1e8}


def est(ticker: str, eps: float, *, year: int = 2026, ts: str = "2026-09-16T20:18:00+00:00",
        n: int = 8, ccy: str = "USD") -> dict:
    return {"snapshot_ts": ts, "ticker": ticker, "source": "yahoo", "period_end": f"{year}-12-31",
            "fy_label": f"FY{year}", "mark": "E", "eps_avg": eps, "eps_low": eps * 0.9,
            "eps_high": eps * 1.1, "n_analysts": n, "currency": ccy}


def ttm(ni: float | None, *, rev: float = 1e9, ccy: str = "USD") -> dict:
    return {"ttm_net_income": ni, "ttm_revenue": rev, "currency": ccy,
            "period_end": "2026-06-30", "basis": "ttm", "n_quarters": 4}


def make_data(prices: list[dict], estimates: list[dict], ttms: dict[str, dict]) -> dict:
    by_price: dict[str, list[dict]] = {}
    for p in prices:
        by_price.setdefault(p["ticker"], []).append(p)
    by_est: dict[str, list[dict]] = {}
    for e in estimates:
        by_est.setdefault(e["ticker"], []).append(e)
    return {
        "prices": by_price, "estimates": by_est, "vintages": {}, "ttm": ttms, "rates": RATES,
        "as_of_price": "2026-09-16T20:00:00+00:00",
        "as_of_estimates": "2026-09-16T20:18:00+00:00",
        "as_of_fx": "2026-09-11",
    }


def simple_data(tickers: list[str] = None, *, with_consensus: bool = True) -> dict:
    """Every listing priced at 100 with a 2026 and 2027 consensus, so nothing is NM by accident."""
    tickers = tickers if tickers is not None else [c.ticker for c in SIMPLE]
    prices = [price(t, 100.0) for t in tickers]
    estimates = []
    if with_consensus:
        for t in tickers:
            estimates += [est(t, 5.0), est(t, 6.0, year=2027)]
    ttms = {c.ticker: ttm(2e8, rev=1e9) for c in SIMPLE}
    return make_data(prices, estimates, ttms)


REFERENCE = {"track": "optical", "updated": "2026-09-16", "segment_revenue": [],
             "cited_share": [], "shipments": [], "capacity": []}


def quote(entity: str, **kw) -> dict:
    row = {"entity": entity, "metric": "share", "value": "1", "unit": "%", "period": "2025",
           "source_name": "LightCounting", "source_url": "https://example.invalid/x",
           "publish_date": "2026-03-01", "confidence": "high"}
    row.update(kw)
    return row


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Every path publish writes to, inside tmp_path, with a pool and a reference file of our own."""
    monkeypatch.setattr(mod, "MART_DIR", tmp_path / "marts")
    monkeypatch.setattr(mod, "FACTS_DIR", tmp_path / "facts")
    monkeypatch.setattr(mod, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(mod, "COMPANIES", list(SIMPLE))
    monkeypatch.setattr(mod, "EXCLUDED", [])
    monkeypatch.setattr(mod, "load_reference", lambda track: dict(REFERENCE))
    return tmp_path


def use(monkeypatch, companies: list[Company], reference: dict | None = None) -> None:
    monkeypatch.setattr(mod, "COMPANIES", list(companies))
    if reference is not None:
        monkeypatch.setattr(mod, "load_reference", lambda track: dict(reference))


def facts_path(sandbox, track: str = "optical"):
    return sandbox / "facts" / f"valuation_{track}.json"


def named(result: dict, name: str) -> dict:
    return next(c for c in result["facts"]["checks"] if c["name"] == name)


def build(track: str = "optical", data: dict | None = None) -> dict:
    return mod.build_track(track, data if data is not None else simple_data(), AS_OF, STAMP)


# --- S1: a worse file never overwrites a better one -------------------------------------------------


def test_a_first_publish_writes_the_marts_and_the_facts(sandbox):
    result = build()

    assert result["written"] is True and result["refused"] is None
    assert facts_path(sandbox).exists()
    for name in ("optical_companies.json", "optical_revisions.json", "optical_share.json"):
        assert (sandbox / "marts" / name).exists()
    assert named(result, "Publish guard")["status"] == "pass"


def test_a_run_that_prices_nothing_leaves_the_published_page_exactly_as_it_was(sandbox):
    """The finding in one test: empty snapshot directories rewrote n_priced 34 to 0 and shipped it."""
    build()
    before_facts = facts_path(sandbox).read_bytes()
    before_mart = (sandbox / "marts" / "optical_companies.json").read_bytes()

    result = build(data=make_data([], [], {c.ticker: ttm(2e8) for c in SIMPLE}))

    assert result["written"] is False
    assert result["refused"] == "no listing is priced, where the published facts have 5"
    assert facts_path(sandbox).read_bytes() == before_facts
    assert (sandbox / "marts" / "optical_companies.json").read_bytes() == before_mart
    guard = named(result, "Publish guard")
    assert guard["status"] == "fail" and "refusing to overwrite" in guard["detail"]


def test_losing_most_of_the_priced_pool_is_refused(sandbox):
    build()

    result = build(data=simple_data(["AAA", "BBB"]))  # 2 of 5 = 40%, under the 80% floor

    assert result["written"] is False
    assert "only 2 of the 5 listings" in result["refused"]


def test_losing_one_listing_is_news_and_still_publishes(sandbox):
    build()

    result = build(data=simple_data(["AAA", "BBB", "CCC", "DDD"]))  # 4 of 5 = 80%, on the floor

    assert result["written"] is True and result["refused"] is None
    assert json.loads(facts_path(sandbox).read_text())["n_priced"] == 4


def test_the_consensus_collapsing_to_nothing_is_refused(sandbox):
    build()

    result = build(data=simple_data(with_consensus=False))

    assert result["written"] is False
    assert result["refused"] == "no listing has a calendar-2026 consensus, where the published facts have 5"


def test_an_emptied_share_pool_is_refused(sandbox):
    build()

    result = build(data=make_data([price(c.ticker, 100.0) for c in SIMPLE],
                                  [est(c.ticker, 5.0) for c in SIMPLE], {}))

    assert result["written"] is False
    assert result["refused"] == "the computed share pool is empty, where the published facts have 5 members"


def test_a_first_publish_is_never_refused_however_thin_it_is(sandbox):
    """No published facts means no page to protect; something is better than nothing on day one."""
    result = build(data=simple_data(["AAA"]))

    assert result["written"] is True and result["refused"] is None


def test_unreadable_published_facts_do_not_block_a_good_publish(sandbox):
    build()
    facts_path(sandbox).write_text("{not json", encoding="utf-8")

    result = build()

    assert result["written"] is True
    assert json.loads(facts_path(sandbox).read_text())["n_priced"] == 5


def test_main_records_the_refusal_and_exits_non_zero(sandbox, monkeypatch):
    build()
    monkeypatch.setattr(mod, "load_all", lambda: make_data([], [], {c.ticker: ttm(2e8) for c in SIMPLE}))
    monkeypatch.setattr(mod, "TRACKS", {"optical": {"label": "Optical modules"}})

    assert mod.main(["--track", "optical"]) == 1

    record = json.loads((sandbox / "snap" / "valuation" / "runs.jsonl").read_text().splitlines()[-1])
    assert record["module"] == "publish" and record["failed"] == ["optical"]
    assert record["tracks"]["optical"]["written"] is False
    assert "no listing is priced" in record["tracks"]["optical"]["refused"]
    assert record["tracks"]["optical"]["counts"]["n_priced"] == 0


def test_main_exits_zero_and_records_the_paths_on_a_good_run(sandbox, monkeypatch):
    monkeypatch.setattr(mod, "load_all", simple_data)
    monkeypatch.setattr(mod, "TRACKS", {"optical": {"label": "Optical modules"}})

    assert mod.main(["--track", "optical"]) == 0

    record = json.loads((sandbox / "snap" / "valuation" / "runs.jsonl").read_text().splitlines()[-1])
    assert record["failed"] == []
    assert len(record["tracks"]["optical"]["paths"]) == 4


def test_one_tracks_failure_never_costs_the_other_its_publish(sandbox, monkeypatch):
    monkeypatch.setattr(mod, "COMPANIES", [*SIMPLE, MVST, QS])
    monkeypatch.setattr(mod, "load_all", lambda: make_data(
        [price(t, 100.0) for t in ("AAA", "BBB", "CCC", "DDD", "EEE", "MVST", "QS")],
        [est(t, 5.0) for t in ("AAA", "BBB", "CCC", "DDD", "EEE", "MVST", "QS")],
        {t: ttm(2e8) for t in ("AAA", "BBB", "CCC", "DDD", "EEE", "MVST", "QS")},
    ))
    real = mod.build_track

    def explode(track, *a, **kw):
        if track == "optical":
            raise RuntimeError("boom")
        return real(track, *a, **kw)

    monkeypatch.setattr(mod, "build_track", explode)

    results = mod.build(["optical", "ssb"])

    assert results["optical"]["error"] == "RuntimeError: boom"
    assert results["optical"]["written"] is False
    assert results["ssb"]["written"] is True
    assert facts_path(sandbox, "ssb").exists() and not facts_path(sandbox, "optical").exists()


# --- S2: the union reaches publish ------------------------------------------------------------------


@pytest.fixture
def snaps(tmp_path, monkeypatch):
    monkeypatch.setattr(read, "VAL_DIR", tmp_path / "snapshots")
    return tmp_path / "snapshots"


def write_snapshot(snaps, table: str, name: str, rows: list[dict]) -> None:
    import polars as pl

    d = snaps / table
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(d / f"{name}.parquet")


def test_load_all_keeps_last_weeks_estimates_when_a_source_fails_this_week(snaps):
    """`read.load` returned one date, so an East-Money-only week discarded every Yahoo forecast."""
    write_snapshot(snaps, "estimates", "2026-09-09-yahoo",
                   [est(t, 5.0, ts="2026-09-09T20:00:00+00:00") for t in ("AAA", "BBB")])
    write_snapshot(snaps, "estimates", "2026-09-16-eastmoney",
                   [{**est("300308.SZ", 9.0), "source": "eastmoney"}])
    write_snapshot(snaps, "prices", "2026-09-15", [price(t, 100.0, ts="2026-09-15T20:00:00+00:00")
                                                   for t in ("AAA", "BBB", "300308.SZ")])
    write_snapshot(snaps, "prices", "2026-09-16", [price("AAA", 101.0)])

    data = mod.load_all()

    assert sorted(data["estimates"]) == ["300308.SZ", "AAA", "BBB"]
    assert sorted(data["prices"]) == ["300308.SZ", "AAA", "BBB"]
    assert data["prices"]["AAA"][0]["price"] == 101.0


def test_as_of_is_the_newest_figure_in_the_union_not_whichever_row_sorts_first(snaps):
    write_snapshot(snaps, "estimates", "2026-09-09-yahoo", [est("AAA", 5.0, ts="2026-09-09T20:00:00+00:00")])
    write_snapshot(snaps, "estimates", "2026-09-16-eastmoney",
                   [{**est("300308.SZ", 9.0, ts="2026-09-16T20:18:00+00:00"), "source": "eastmoney"}])
    write_snapshot(snaps, "prices", "2026-09-16", [price("AAA", 100.0)])

    data = mod.load_all()

    assert data["as_of_estimates"] == "2026-09-16T20:18:00+00:00"
    assert data["as_of_price"] == "2026-09-16T20:00:00+00:00"


def test_every_row_says_when_its_price_was_captured(sandbox):
    data = simple_data()
    data["prices"]["BBB"] = [price("BBB", 100.0, ts="2026-09-08T20:00:00+00:00")]

    result = build(data=data)

    rows = {r["ticker"]: r for r in json.loads((sandbox / "marts" / "optical_companies.json").read_text())}
    assert rows["AAA"]["price_age_days"] == 0
    assert rows["BBB"]["price_age_days"] == 8
    assert rows["BBB"]["price_as_of"] == "2026-09-08T20:00:00+00:00"
    assert result["facts"]["n_stale_prices"] == 1
    stale = named(result, "Price freshness")
    assert stale["status"] == "warn" and "BBB" in stale["detail"]


# --- P6: a dual listing's trailing multiple must not be a share-count artefact -----------------------


def _yofc_data() -> dict:
    return make_data(
        [price("601869.SS", 461.0, ccy="CNY", cap=378_898_251_776.0),
         price("6869.HK", 177.0, ccy="HKD", cap=251_987_591_168.0)],
        [],
        {"601869.SS": ttm(3_442_534_928.0, rev=1e10, ccy="CNY")},
    )


def test_a_secondary_line_quoting_a_different_share_base_loses_its_trailing_multiple(sandbox, monkeypatch):
    """110.1x against 62.6x on one set of accounts reads as a 76% A/H premium that does not exist."""
    use(monkeypatch, [ALPHA, YOFC_A, YOFC_H])
    data = _yofc_data()
    data["prices"]["AAA"] = [price("AAA", 100.0)]
    data["ttm"]["AAA"] = ttm(2e8)

    result = build(data=data)

    rows = {r["ticker"]: r for r in json.loads((sandbox / "marts" / "optical_companies.json").read_text())}
    assert rows["601869.SS"]["trailing_pe"] == pytest.approx(110.06, abs=0.01)
    assert rows["6869.HK"]["trailing_pe"] is None
    assert rows["6869.HK"]["trailing_pe_nm"] == (
        "the quoted market cap implies 1,424m shares against 822m on 601869.SS, "
        "so a trailing multiple from it would be a share-count artefact"
    )
    flagged = result["facts"]["share_base_artefacts"]
    assert [f["ticker"] for f in flagged] == ["6869.HK"]
    assert flagged[0]["gap"] == pytest.approx(0.732, abs=0.001)
    assert named(result, "Dual-listing share base")["status"] == "warn"


def test_a_dual_listing_whose_two_caps_agree_keeps_both_multiples(sandbox, monkeypatch):
    """CATL's two lines imply the same 4.627bn shares, so the difference really is the price."""
    shares = 4.627e9
    catl_h = Company("3750.HK", "CATL (H)", "ssb", "main", "hk", 12,
                     fundamentals_from="300750.SZ", share_basis="total")
    use(monkeypatch, [CATL, catl_h, MVST])
    data = make_data(
        [price("300750.SZ", 393.0, ccy="CNY", cap=shares * 393.0),
         price("3750.HK", 501.0, ccy="HKD", cap=shares * 501.0),
         price("MVST", 5.0)],
        [],
        {"300750.SZ": ttm(6e10, rev=4e11, ccy="CNY"), "MVST": ttm(5.1e7, rev=3.7e8)},
    )

    result = build("ssb", data)

    rows = {r["ticker"]: r for r in json.loads((sandbox / "marts" / "ssb_companies.json").read_text())}
    assert rows["300750.SZ"]["trailing_pe"] is not None
    assert rows["3750.HK"]["trailing_pe"] is not None
    assert result["facts"]["share_base_artefacts"] == []
    assert named(result, "Dual-listing share base")["status"] == "pass"


def test_a_secondary_line_with_no_price_is_left_alone_rather_than_guessed_at(sandbox, monkeypatch):
    use(monkeypatch, [ALPHA, YOFC_A, YOFC_H])
    data = _yofc_data()
    data["prices"].pop("6869.HK")
    data["prices"]["AAA"] = [price("AAA", 100.0)]
    data["ttm"]["AAA"] = ttm(2e8)

    result = build(data=data)

    assert result["facts"]["share_base_artefacts"] == []


# --- P1: "the track is the business" is not "has no earnings" ---------------------------------------


def test_a_profitable_pure_play_is_not_published_as_having_no_earnings(sandbox, monkeypatch):
    """Microvast is purity 'high' and earns money; the page said the whole set has no PE."""
    use(monkeypatch, [MVST, QS, CATL])
    data = make_data(
        [price("MVST", 5.0, cap=2.25e8), price("QS", 9.0, cap=5.5e9), price("300750.SZ", 393.0, ccy="CNY")],
        [],
        {"MVST": ttm(5.1e7, rev=3.675e8), "QS": ttm(-4e8, rev=None), "300750.SZ": ttm(6e10, rev=4e11, ccy="CNY")},
    )

    facts = build("ssb", data)["facts"]

    assert [p["ticker"] for p in facts["pure_plays"]] == ["MVST", "QS"]
    assert [p["ticker"] for p in facts["preprofit"]] == ["QS"]
    assert [p["ticker"] for p in facts["pure_plays_with_earnings"]] == ["MVST"]
    assert facts["n_pure_plays"] == 2 and facts["n_preprofit"] == 1 and facts["n_pure_plays_with_earnings"] == 1
    assert facts["pure_plays_with_earnings"][0]["trailing_pe"] == pytest.approx(4.41, abs=0.01)
    assert facts["preprofit"][0]["trailing_pe_nm"] == "loss-making over the trailing twelve months"


def test_a_pure_play_with_no_price_is_in_neither_earnings_bucket(sandbox, monkeypatch):
    """`pure_plays` minus `preprofit` is not `with earnings`, which is why all three counts ship."""
    use(monkeypatch, [MVST, QS, CATL])
    data = make_data(
        [price("MVST", 5.0, cap=2.25e8), price("300750.SZ", 393.0, ccy="CNY")],
        [],
        {"MVST": ttm(5.1e7), "QS": ttm(-4e8), "300750.SZ": ttm(6e10, ccy="CNY")},
    )

    facts = build("ssb", data)["facts"]

    assert facts["n_pure_plays"] == 2
    assert [p["ticker"] for p in facts["preprofit"]] == []
    assert [p["ticker"] for p in facts["pure_plays_with_earnings"]] == ["MVST"]
    assert facts["pure_plays"][1]["trailing_pe_nm"] == "no price"


def test_the_optical_track_publishes_no_pure_play_block_at_all(sandbox):
    facts = build()["facts"]

    assert "preprofit" not in facts and "pure_plays" not in facts


# --- P4: the pool says how it was measured ----------------------------------------------------------


def test_the_share_check_splits_the_exclusion_reasons_instead_of_calling_them_all_non_disclosure(
    sandbox, monkeypatch
):
    use(monkeypatch, [ALPHA, BETA, GAMMA, SUPPLIER, DIVERSIFIED])
    data = make_data([price(c.ticker, 100.0) for c in (ALPHA, BETA, GAMMA, SUPPLIER, DIVERSIFIED)],
                     [est(c.ticker, 5.0) for c in (ALPHA, BETA, GAMMA, SUPPLIER, DIVERSIFIED)],
                     {t: ttm(2e8) for t in ("AAA", "BBB", "CCC", "SUP", "DIV")})

    result = build(data=data)

    detail = named(result, "Share basis A")["detail"]
    assert detail == ("3 issuers enter the computed pool (3 on total revenue); 2 excluded, "
                      "1 as suppliers or contract manufacturers at another layer, "
                      "1 for want of a sourced revenue figure for this track")
    assert result["facts"]["share"]["basis_counts"] == {"disclosed segment": 0, "total revenue": 3}


def test_a_segment_figure_shows_up_in_the_basis_counts_and_beside_the_member(sandbox, monkeypatch):
    reference = {**REFERENCE, "segment_revenue": [
        {"ticker": "DIV", "value": 5e8, "currency": "USD", "period": "FY2026",
         "basis": "10-K segment note", "source_name": "10-K",
         "source_url": "https://example.invalid/10k", "publish_date": "2026-08-20"}]}
    use(monkeypatch, [ALPHA, BETA, GAMMA, DIVERSIFIED], reference)
    data = make_data([price(c.ticker, 100.0) for c in (ALPHA, BETA, GAMMA, DIVERSIFIED)],
                     [est(c.ticker, 5.0) for c in (ALPHA, BETA, GAMMA, DIVERSIFIED)],
                     {t: ttm(2e8) for t in ("AAA", "BBB", "CCC", "DIV")})

    result = build(data=data)

    share = result["facts"]["share"]
    assert share["basis_counts"] == {"disclosed segment": 1, "total revenue": 3}
    div = next(m for m in share["members"] if m["ticker"] == "DIV")
    assert div["basis"] == "disclosed segment" and div["revenue_usd"] == 5e8 and div["period_end"] == "FY2026"
    assert "1 on disclosed segment, 3 on total revenue" in named(result, "Share basis A")["detail"]


def test_a_dual_listed_issuer_is_counted_once_in_n_excluded(sandbox, monkeypatch):
    use(monkeypatch, [ALPHA, BETA, GAMMA, YOFC_A, YOFC_H])
    data = make_data(
        [price(t, 100.0) for t in ("AAA", "BBB", "CCC")]
        + [price("601869.SS", 461.0, ccy="CNY", cap=378_898_251_776.0),
           price("6869.HK", 177.0, ccy="HKD", cap=251_987_591_168.0)],
        [est(t, 5.0) for t in ("AAA", "BBB", "CCC")],
        {t: ttm(2e8) for t in ("AAA", "BBB", "CCC")},  # YOFC has no TTM, so it is excluded
    )

    share = build(data=data)["facts"]["share"]

    assert [e["ticker"] for e in share["excluded"]] == ["601869.SS"]
    assert share["n_excluded"] == 1


# --- S16: the citation check verifies what it claims to -------------------------------------------


def test_the_quoted_check_counts_the_shipments_and_capacity_rows_the_battery_page_shows(sandbox, monkeypatch):
    reference = {"track": "ssb", "updated": "2026-09-16", "segment_revenue": [],
                 "cited_share": [quote("CATL share")],
                 "shipments": [quote(f"shipment {i}") for i in range(3)],
                 "capacity": [quote(f"capacity {i}", start_year="2027") for i in range(3)]}
    use(monkeypatch, [MVST, QS, CATL], reference)
    data = make_data([price(t, 100.0) for t in ("MVST", "QS", "300750.SZ")], [],
                     {t: ttm(2e8) for t in ("MVST", "QS", "300750.SZ")})

    result = build("ssb", data)

    assert result["facts"]["n_quoted"] == 7
    assert named(result, "Quoted figures sourced")["detail"].startswith("7 quoted figures on the page")
    assert named(result, "Quoted figures sourced")["status"] == "pass"


def test_a_row_held_back_is_named_in_the_check_rather_than_silently_missing(sandbox, monkeypatch):
    reference = {**REFERENCE, "cited_share": [quote("Solid figure"),
                                              quote("Unverified figure", confidence="low")]}
    use(monkeypatch, SIMPLE, reference)

    result = build()

    assert result["facts"]["n_quoted"] == 1
    assert [w["entity"] for w in result["facts"]["withheld_quotes"]] == ["Unverified figure"]
    quoted = named(result, "Quoted figures sourced")
    assert quoted["status"] == "warn"
    assert "Unverified figure held back (confidence recorded as low)" in quoted["detail"]


def test_the_optical_page_does_not_count_quoted_rows_it_never_renders(sandbox, monkeypatch):
    reference = {**REFERENCE, "cited_share": [quote("Top 10")], "shipments": [quote("400G units")]}
    use(monkeypatch, SIMPLE, reference)

    result = build()

    assert result["facts"]["n_quoted"] == 1
    assert "shipments" not in result["facts"]


def test_the_withheld_rows_reach_the_share_mart_so_the_omission_is_auditable(sandbox, monkeypatch):
    reference = {**REFERENCE, "cited_share": [quote("Unverified", confidence="low")]}
    use(monkeypatch, SIMPLE, reference)

    build()

    mart = json.loads((sandbox / "marts" / "optical_share.json").read_text())
    assert [w["entity"] for w in mart["withheld"]] == ["Unverified"]


# --- S8: the published layer has a contract --------------------------------------------------------


def test_a_negative_multiple_never_reaches_the_mart(sandbox, monkeypatch):
    real = mod.company_row
    monkeypatch.setattr(mod, "company_row", lambda c, **kw: {**real(c, **kw), "trailing_pe": -5.0})

    with pytest.raises(SchemaError):
        build()

    assert not facts_path(sandbox).exists()
    assert not (sandbox / "marts").exists()


def test_a_nan_fails_the_contract_the_way_a_null_does_not(sandbox, monkeypatch):
    real = mod.company_row
    monkeypatch.setattr(mod, "company_row", lambda c, **kw: {**real(c, **kw), "fwd_pe_2026": float("nan")})

    with pytest.raises(SchemaError):
        build()


def test_two_rows_for_one_ticker_fail_the_contract(sandbox, monkeypatch):
    use(monkeypatch, [ALPHA, ALPHA, BETA])

    with pytest.raises(SchemaError):
        build(data=simple_data(["AAA", "BBB"]))


def test_an_impossible_share_fails_the_member_contract(sandbox):
    data = simple_data()
    facts = build(data=data)["facts"]
    share = {"members": [{**facts["share"]["members"][0], "share": 1.4}]}

    with pytest.raises(SchemaError):
        mod.validate(facts, [], share)


def test_a_facts_key_the_pages_read_going_missing_is_an_error(sandbox):
    result = build()
    facts = {k: v for k, v in result["facts"].items() if k != "cited_share"}

    with pytest.raises(ValueError, match="missing keys the pages read: cited_share"):
        mod.validate(facts, [], {"members": []})


def test_a_valid_build_passes_its_own_contract(sandbox):
    result = build()

    mod.validate(result["facts"], json.loads((sandbox / "marts" / "optical_companies.json").read_text()),
                 {"members": result["facts"]["share"]["members"]})
