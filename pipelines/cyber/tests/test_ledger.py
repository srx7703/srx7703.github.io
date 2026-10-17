"""The ring C ledger: three numbers per private company, what is never charted, deals, and the counts for facts.

The fixtures under ``fixtures/ledger_*.json`` are fictional companies with ``example.com`` sources, shaped like what
``reference.rows`` returns (and still carrying ``curator_notes``, to prove nothing of it travels). Every fixture row
passes the real curation rules; ``test_fixture_rows_are_valid_curated_rows`` keeps them honest. No test reads ``data/``.
"""

from __future__ import annotations

import copy
import json
import random
from datetime import date
from pathlib import Path

import pandera.errors
import polars as pl
import pytest

from pipelines.common.curation import row_problems
from pipelines.cyber import config as C
from pipelines.cyber import ledger as L
from pipelines.cyber import reference as R

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 17)
SENTINEL = "NOTE-SENTINEL"


def _rows(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))["rows"]


def _multiples() -> dict:
    return json.loads((FIX / "ledger_multiples.json").read_text(encoding="utf-8"))


_FIXTURE = object()  # "use the fixture file"; None must stay available as a real argument


def run(*, directory=_FIXTURE, metrics=_FIXTURE, events=_FIXTURE, multiples=_FIXTURE, today=TODAY,
        as_of="2026-10-16") -> L.Ledger:  # fmt: skip
    """Build on the fixtures, with any input replaced. The fixtures are copied so a test cannot dirty them."""
    return L.build(
        copy.deepcopy(_rows("ledger_directory.json") if directory is _FIXTURE else directory),
        copy.deepcopy(_rows("ledger_metrics.json") if metrics is _FIXTURE else metrics),
        copy.deepcopy(_rows("ledger_events.json") if events is _FIXTURE else events),
        _multiples()["multiples"] if multiples is _FIXTURE else multiples,
        today=today,
        multiples_as_of=as_of,
    )


@pytest.fixture(scope="module")
def built() -> L.Ledger:
    return run()


def company(built: L.Ledger, name: str) -> dict:
    hit = built.tables["private_intervals"].filter(pl.col("company") == name)
    assert hit.height == 1, f"{name}: {hit.height} rows"
    return hit.row(0, named=True)


# --- small row factories for the cases the fixtures do not cover -----------------------------------------------------
def mrow(company_: str = "Acme", what: str = "val", **kw) -> dict:
    base = {
        "id": f"{company_}:{what}", "company": company_, "layer": "network_edge", "market": "overseas",
        "metric": "post_money_valuation", "metric_kind": "primary_round_valuation", "period": "2026-04-30",
        "value": 1.0e9, "unit": "USD", "qualifier": "=", "basis": "disclosed", "status": "current",
        "definition": "Post-money valuation of the round as the company states it.", "company_confirmed": True,
        "vintage_year": 2026, "stale_mark": False, "as_of_date": "2026-04-30", "tag": "V", "chartable": True,
        "stale": False, "source_name": "Acme newsroom", "source_url": f"https://example.com/{company_}/{what}",
        "publish_date": "2026-04-30", "last_checked": "2026-10-17",
        "caveat": "Post-money valuation as the company states it; not a fair value.",
    }  # fmt: skip
    return {**base, **kw}


def arr(company_: str = "Acme", **kw) -> dict:
    base = {"metric": "arr", "metric_kind": "arr_company_stated", "value": 1.0e8, "period": "2026-06-30",
            "as_of_date": "2026-06-30", "vintage_year": None, "stale_mark": None}  # fmt: skip
    return mrow(company_, "arr", **{**base, **kw})


def drow(company_: str = "Acme", **kw) -> dict:
    base = {
        "id": f"{company_}:directory", "company": company_, "layer": "network_edge", "l2": "sase_sse",
        "market": "overseas", "country": "US", "status": "independent", "website": "https://example.com/acme",
        "summary": "Acme sells a product.", "source_name": "Acme site", "source_url": "https://example.com/acme/about",
        "publish_date": "2026-01-10", "as_of_date": "2026-10-17", "last_checked": "2026-10-17", "tag": "V",
        "chartable": False, "stale": False, "caveat": "Describes the company in its own terms.",
    }  # fmt: skip
    return {**base, **kw}


def erow(company_: str = "Acme", kind: str = "acquisition_announced", date_: str = "2026-03-01", **kw) -> dict:
    base = {
        "id": f"{company_}:{kind}:{date_}", "company": company_, "layer": "network_edge", "market": "overseas",
        "event_kind": kind, "event_date": date_, "as_of_date": date_, "company_confirmed": True,
        "counterparty": "Bigco", "buyer_type": "platform_security", "source_name": "Bigco newsroom",
        "source_url": f"https://example.com/{company_}/{kind}", "publish_date": date_, "last_checked": "2026-10-17",
        "tag": "V", "chartable": True, "stale": False, "caveat": "Announcement as the buyer states it.",
    }  # fmt: skip
    return {**base, **kw}


# --- the helpers are valid curated rows, and so are the fixtures -----------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "name"),
    [("private_directory", "ledger_directory.json"), ("private_metrics", "ledger_metrics.json"),
     ("events", "ledger_events.json")],
)  # fmt: skip
def test_fixture_rows_are_valid_curated_rows(kind: str, name: str) -> None:
    bad = {r["id"]: row_problems(R.SPEC, kind, r) for r in _rows(name) if row_problems(R.SPEC, kind, r)}
    assert bad == {}


def test_row_factories_are_valid_curated_rows() -> None:
    assert row_problems(R.SPEC, "private_metrics", mrow()) == []
    assert row_problems(R.SPEC, "private_metrics", arr()) == []
    assert row_problems(R.SPEC, "private_directory", drow()) == []
    assert row_problems(R.SPEC, "events", erow()) == []


# --- the registered assumptions come from config ---------------------------------------------------------------------
def test_discounts_and_band_are_parsed_from_config() -> None:
    assert L.DISCOUNTS == {2021: -0.60, 2022: -0.55, 2023: -0.35, 2024: -0.15, 2025: -0.05, 2026: 0.0}
    assert set(L.DISCOUNTS) == {int(y) for y in C.DISCOUNT_BY_ROUND_YEAR}
    assert L.BAND == pytest.approx(0.15)


@pytest.mark.parametrize("table", [{"2021": "minus sixty"}, {"year": "-5%"}, {"2021": "-60"}, {"2021": "-6o%"}])
def test_a_malformed_discount_table_is_refused(table: dict) -> None:
    with pytest.raises(ValueError, match="DISCOUNT_BY_ROUND_YEAR"):
        L.parse_discounts(table)


def test_a_band_without_a_number_is_refused() -> None:
    with pytest.raises(ValueError, match="DISCOUNT_BAND"):
        L.parse_band("wide")
    assert L.parse_band("plus or minus 15 percentage points") == pytest.approx(0.15)


# --- the three numbers -----------------------------------------------------------------------------------------------
def test_a_company_gets_a_headline_and_two_intervals_each_with_its_provenance(built: L.Ledger) -> None:
    a = company(built, "Alpha Edge")
    assert (a["headline_value"], a["headline_unit"], a["headline_qualifier"], a["headline_basis"]) == (
        6.0e9, "USD", "=", "disclosed")  # fmt: skip
    assert (a["headline_as_of"], a["vintage_year"], a["stale_mark"], a["headline_tag"], a["company_confirmed"]) == (
        "2025-06-30", 2025, False, "V", True)  # fmt: skip
    assert a["headline_source_url"] == "https://example.com/alpha-edge/val-2025"
    # 2025 discount -5%, band 15 points: 6.0e9 x 0.80 to 6.0e9 x 1.10
    assert (a["discount"], a["discount_band"]) == (pytest.approx(-0.05), pytest.approx(0.15))
    assert (a["discount_lower"], a["discount_upper"]) == (pytest.approx(4.8e9), pytest.approx(6.6e9))
    assert (a["discount_qualifier"], a["discount_basis"], a["discount_as_of"], a["discount_tag"]) == (
        "=", "disclosed", "2025-06-30", "C")  # fmt: skip
    assert a["discount_source_url"] == a["headline_source_url"] and a["discount_scored"] is True
    # network_edge comparables ZS 10.0, NTSK 7.3, FTNT 13.6 on ARR 3.0e8
    assert (a["arr_value"], a["arr_unit"], a["arr_qualifier"], a["arr_tag"]) == (3.0e8, "USD", "=", "V")
    assert (a["multiple_low"], a["multiple_low_ticker"]) == (7.3, "NTSK")
    assert (a["multiple_high"], a["multiple_high_ticker"]) == (13.6, "FTNT")
    assert (a["comparable_lower"], a["comparable_upper"]) == (pytest.approx(2.19e9), pytest.approx(4.08e9))
    assert (a["comparable_basis"], a["comparable_as_of"], a["comparable_tag"], a["multiples_as_of"]) == (
        "disclosed", "2026-06-30", "C", "2026-10-16")  # fmt: skip
    assert a["comparable_source_url"] == "https://example.com/alpha-edge/arr" and a["comparable_missing"] is None
    assert a["comparable_tickers"] == "ZS, NTSK, FTNT"
    assert a["above"] is True and a["q6_scored"] is True and a["above_reason"] is None and a["above_note"] is None


@pytest.mark.parametrize("year", sorted(L.DISCOUNTS))
def test_each_round_year_uses_its_own_discount(year: int) -> None:
    row = mrow(vintage_year=year, as_of_date=f"{year}-04-30", period=f"{year}-04-30", value=2.0e9)
    built = run(directory=[drow()], metrics=[row, arr()], events=[], multiples={"ZS": 10.0})
    got = company(built, "Acme")
    d = L.DISCOUNTS[year]
    assert got["discount"] == pytest.approx(d)
    assert got["discount_lower"] == pytest.approx(2.0e9 * (1 + d - 0.15))
    assert got["discount_upper"] == pytest.approx(2.0e9 * (1 + d + 0.15))


def test_the_other_four_companies_of_q6_read_as_hand_calculated(built: L.Ledger) -> None:
    beta = company(built, "Beta Identity")  # 2024: -15%; identity comparables OKTA 5.0, SAIL 8.8 on ARR 2.0e8
    assert (beta["discount_lower"], beta["discount_upper"]) == (pytest.approx(1.4e9), pytest.approx(2.0e9))
    assert (beta["comparable_lower"], beta["comparable_upper"]) == (pytest.approx(1.0e9), pytest.approx(1.76e9))
    assert beta["above"] is False and beta["q6_scored"] is True
    gamma = company(built, "Gamma Cloud")  # 2021: -60%; cloud_appsec comparables NET 29.8, ZS 10.0 (RBRK has none)
    assert (gamma["discount_lower"], gamma["discount_upper"]) == (pytest.approx(1.25e9), pytest.approx(2.75e9))
    assert (gamma["comparable_lower"], gamma["comparable_upper"]) == (pytest.approx(4.0e9), pytest.approx(1.192e10))
    assert gamma["above"] is False and gamma["stale_mark"] is True
    kappa = company(built, "Kappa Guard")  # 2026: 0%; endpoint comparables CRWD 32.8, S 5.4 on ARR 2.0e8
    assert (kappa["discount_lower"], kappa["discount_upper"]) == (pytest.approx(6.8e9), pytest.approx(9.2e9))
    assert (kappa["comparable_lower"], kappa["comparable_upper"]) == (pytest.approx(1.08e9), pytest.approx(6.56e9))
    assert kappa["above"] is True
    lam = company(built, "Lambda Net")  # yuan throughout: nothing is converted
    assert (lam["headline_unit"], lam["arr_unit"], lam["discount_unit"], lam["comparable_unit"]) == ("CNY",) * 4
    assert (lam["discount_lower"], lam["comparable_upper"]) == (pytest.approx(8.0e9), pytest.approx(6.8e9))
    assert lam["above"] is True and lam["market"] == "china" and lam["name_cn"] == "拉姆达网络"


def test_a_round_year_outside_the_table_is_not_scored_and_says_why(built: L.Ledger) -> None:
    delta = company(built, "Delta Endpoint")  # a 2019 round
    assert delta["discount_scored"] is False and delta["discount_lower"] is None and delta["discount_upper"] is None
    assert "2019" in delta["discount_reason"] and "2021" in delta["discount_reason"]
    assert delta["headline_value"] == 1.0e9, "the headline is still shown"
    assert delta["comparable_scored"] is True, "the other interval does not depend on the discount"
    assert delta["above"] is None and delta["q6_scored"] is False
    assert delta["above_reason"].startswith("discount interval not scored")


def test_a_valuation_row_without_a_vintage_year_is_not_scored() -> None:
    built = run(directory=[drow()], metrics=[mrow(vintage_year=None, stale_mark=None), arr()], events=[])
    got = company(built, "Acme")
    assert got["discount_scored"] is False and "no vintage year" in got["discount_reason"]


def test_a_layer_without_comparables_is_not_scored_and_says_why(built: L.Ledger) -> None:
    eps = company(built, "Epsilon Data")  # data_privacy has no registered comparables
    assert eps["comparable_scored"] is False and "data_privacy" in eps["comparable_reason"]
    assert eps["arr_value"] == 2.0e8, "the ARR is still shown"
    assert eps["discount_scored"] is True and eps["above"] is None
    assert eps["above_reason"].startswith("comparable interval not scored")


def test_no_company_stated_arr_means_no_comparable_interval(built: L.Ledger) -> None:
    eta = company(built, "Eta Access")
    assert eta["arr_value"] is None and eta["comparable_scored"] is False
    assert "no current chartable company-stated ARR" in eta["comparable_reason"]
    assert eta["discount_scored"] is True and eta["above"] is None


def test_a_company_with_nothing_chartable_has_no_headline_and_says_why(built: L.Ledger) -> None:
    theta = company(built, "Theta Ops")  # only reported talks, an estimate and a secondary mark
    assert theta["headline_value"] is None and theta["headline_tag"] is None
    assert theta["headline_reason"] == "no current chartable primary-round valuation"
    assert theta["discount_scored"] is False and theta["comparable_scored"] is False
    assert theta["above"] is None and theta["above_reason"] == theta["headline_reason"]


def test_a_comparable_without_a_multiple_narrows_the_range_and_is_named(built: L.Ledger) -> None:
    gamma = company(built, "Gamma Cloud")  # RBRK is null in the fixture run
    assert gamma["comparable_tickers"] == "NET, ZS" and gamma["comparable_missing"] == "RBRK"
    assert (gamma["multiple_low_ticker"], gamma["multiple_high_ticker"]) == ("ZS", "NET")
    check = next(c for c in built.checks if c["name"] == "Public comparables present")
    assert check["status"] == "warn" and "Gamma Cloud: RBRK" in check["detail"]


@pytest.mark.parametrize(
    "multiples",
    [
        {}, None, {"AAPL": 25.0},
        {"ZS": float("nan"), "NTSK": 0, "FTNT": -3.0},
        {"ZS": None, "NTSK": "7.3", "FTNT": float("inf")},
        {"ZS": True},
    ],
)  # fmt: skip
def test_no_usable_multiple_means_no_comparable_interval_and_nothing_breaks(multiples) -> None:
    built = run(multiples=multiples)
    a = company(built, "Alpha Edge")
    assert a["comparable_scored"] is False and "none of the comparables" in a["comparable_reason"]
    assert a["comparable_lower"] is None and a["above"] is None
    assert built.tables["private_intervals"]["comparable_scored"].sum() == 0
    assert built.summary["private"]["n_comparable_scored"] == 0


def test_the_snapshot_changes_only_the_comparable_reading() -> None:
    low = company(run(multiples={"ZS": 1.0, "NTSK": 2.0, "FTNT": 3.0}), "Alpha Edge")
    high = company(run(multiples={"ZS": 20.0, "NTSK": 30.0, "FTNT": 40.0}), "Alpha Edge")
    assert (low["comparable_lower"], low["comparable_upper"]) == (pytest.approx(3.0e8), pytest.approx(9.0e8))
    assert (high["comparable_lower"], high["comparable_upper"]) == (pytest.approx(6.0e9), pytest.approx(1.2e10))
    assert low["above"] is True and high["above"] is False, "4.8e9 clears 9e8 and does not clear 1.2e10"
    keep = ["headline_value", "discount_lower", "discount_upper", "arr_value"]
    assert [low[k] for k in keep] == [high[k] for k in keep]


def test_above_is_strict_and_uses_the_lower_end_against_the_upper_end() -> None:
    # 2026 headline 1.0e9, so the discount interval is 0.85e9 to 1.15e9; ARR 1.0e8 x a single multiple
    def above(mult: float) -> bool | None:
        built = run(directory=[drow()], metrics=[mrow(), arr()], events=[], multiples={"ZS": mult})
        return company(built, "Acme")["above"]

    assert above(8.4) is True, "lower end 0.85e9 is higher than 0.84e9"
    assert above(8.5) is False, "equal is not higher"
    assert above(11.0) is False, "the headline itself (1.0e9) is above 0.85e9 and still not above 1.1e9"


@pytest.mark.parametrize(
    ("headline", "arr_row", "fragment"),
    [
        ({"basis": "reported", "company_confirmed": False,
          "caveat": "Reported by Example News on 2026-04-30; the company has not confirmed it."}, {}, "basis is reported"),
        ({}, {"unit": "EUR"}, "a currency is never converted"),
        ({"vintage_year": 2019, "as_of_date": "2019-04-30"}, {}, "discount interval not scored"),
    ],
)  # fmt: skip
def test_above_is_withheld_unless_the_whole_rule_can_be_applied(headline: dict, arr_row: dict, fragment: str) -> None:
    built = run(directory=[drow()], metrics=[mrow(**headline), arr(**arr_row)], events=[], multiples={"ZS": 1.0})
    got = company(built, "Acme")
    assert got["above"] is None and got["q6_scored"] is False and fragment in got["above_reason"]
    assert got["discount_lower"] is not None or "discount" in got["above_reason"], "intervals are still shown"


def test_the_fixture_companies_withheld_from_q6(built: L.Ledger) -> None:
    zeta, iota = company(built, "Zeta Secure"), company(built, "Iota Shield")
    assert zeta["headline_basis"] == "reported" and zeta["above"] is None
    assert zeta["discount_lower"] is not None and zeta["comparable_lower"] is not None, "all three still exist"
    assert zeta["above_reason"] == "headline basis is reported, not disclosed"
    assert iota["above"] is None and "USD" in iota["above_reason"] and "EUR" in iota["above_reason"]


def test_the_qualifier_travels_and_a_note_says_when_it_cuts_against_reading_above_as_firm(built: L.Ledger) -> None:
    kappa = company(built, "Kappa Guard")  # more than $8B, ARR more than $200M
    assert (kappa["discount_qualifier"], kappa["comparable_qualifier"]) == (">", ">")
    assert kappa["above"] is True and "ARR is a lower bound" in kappa["above_note"]
    assert "headline" not in kappa["above_note"], "a '>' headline only strengthens the claim"

    def note(headline_q: str, arr_q: str) -> str | None:
        rows = [mrow(qualifier=headline_q), arr(qualifier=arr_q)]
        got = company(run(directory=[drow()], metrics=rows, events=[], multiples={"ZS": 1.0}), "Acme")
        assert got["above"] is True
        return got["above_note"]

    assert note("=", "=") is None and note(">", "<") is None
    assert "headline is an upper bound" in note("<", "=")
    assert "approximate figure" in note("~", "=") and "approximate figure" in note("=", "~")


def test_derived_intervals_are_tag_c_and_inputs_are_v_or_c(built: L.Ledger) -> None:
    iv = built.tables["private_intervals"]
    assert set(iv["discount_tag"].drop_nulls()) == {"C"} and set(iv["comparable_tag"].drop_nulls()) == {"C"}
    assert set(iv["headline_tag"].drop_nulls()) <= {"V", "C"} and set(iv["arr_tag"].drop_nulls()) <= {"V", "C"}


def test_one_row_per_company_in_the_directory_or_the_ledger(built: L.Ledger) -> None:
    iv = built.tables["private_intervals"]
    assert iv["company"].is_unique().all() and iv.height == 14
    assert set(iv["company"]) == {r["company"] for r in _rows("ledger_directory.json")} | {"Orphan Co"}
    orphan = company(built, "Orphan Co")
    assert orphan["in_directory"] is False and orphan["layer"] == "secops" and orphan["directory_status"] is None


def test_nothing_is_summed_or_averaged_across_companies(built: L.Ledger) -> None:
    iv = built.tables["private_intervals"]
    assert not [c for c in iv.columns if any(w in c for w in ("total", "sum", "mean", "average", "median"))]
    top = built.summary["private"]["latest_valuation_max"]
    usd = iv.filter(pl.col("headline_unit") == "USD")["headline_value"]
    assert top["value"] == usd.max(), "the maximum is one company's headline, not an aggregate"


# --- what the headline is --------------------------------------------------------------------------------------------
def test_the_headline_is_the_latest_current_chartable_valuation() -> None:
    older = mrow(what="v1", value=1.0e9, vintage_year=2024, as_of_date="2024-05-31", period="2024-05-31")
    newer = mrow(what="v2", value=2.0e9, vintage_year=2025, as_of_date="2025-09-30", period="2025-09-30")
    for extra, expect in (
        (None, 2.0e9),
        (mrow(what="v3", value=3.0e9, status="superseded", as_of_date="2026-02-01", period="2026-02-01"), 2.0e9),
        (mrow(what="v3", value=3.0e9, status="disputed", as_of_date="2026-02-01", period="2026-02-01"), 2.0e9),
        (mrow(what="v3", value=3.0e9, as_of_date="2026-02-01", period="2026-02-01", tag="S", chartable=False), 2.0e9),
        (mrow(what="v3", value=3.0e9, as_of_date="2026-02-01", period="2026-02-01", chartable=False), 2.0e9),
        (mrow(what="v3", value=3.0e9, as_of_date="2026-02-01", period="2026-02-01"), 3.0e9),
    ):
        built = run(directory=[drow()], metrics=[older, newer, *([extra] if extra else [])], events=[])
        assert company(built, "Acme")["headline_value"] == expect
    # and with two current valuations the ledger says so
    assert next(c for c in built.checks if c["name"] == "One headline per company")["status"] == "warn"


def test_a_tie_on_the_date_prefers_the_exact_figure_and_is_counted() -> None:
    rows = [mrow(what="a", qualifier=">", value=5.0e9, period="FY2026"), mrow(what="b", qualifier="=", value=4.0e9)]
    built = run(directory=[drow()], metrics=rows, events=[])
    assert company(built, "Acme")["headline_value"] == 4.0e9
    latest = built.tables["ledger_latest"]
    assert latest.height == 1 and latest["qualifier"][0] == "="
    ties = next(c for c in built.checks if c["name"] == "Latest-value ties")
    assert ties["status"] == "warn" and ties["detail"].startswith("1 ")


def test_round_year_that_differs_from_the_date_is_a_warning() -> None:
    built = run(directory=[drow()], metrics=[mrow(vintage_year=2025)], events=[])
    check = next(c for c in built.checks if c["name"] == "Round year matches vintage year")
    assert check["status"] == "warn" and "Acme" in check["detail"]


# --- the chartable marts ---------------------------------------------------------------------------------------------
def test_the_timeline_holds_every_chartable_metric_row_and_keeps_history(built: L.Ledger) -> None:
    tl = built.tables["ledger_timeline"]
    assert tl.height == 26 and tl["chartable"].all()
    alpha = tl.filter((pl.col("company") == "Alpha Edge") & (pl.col("metric_kind") == "primary_round_valuation"))
    assert sorted(alpha["status"]) == ["current", "superseded"], "a superseded round is the history, not a gap"
    assert sorted(alpha["vintage_year"]) == [2023, 2025]
    assert set(tl["status"]) == {"current", "superseded"}
    assert {"stale", "stale_mark", "vintage_year", "basis", "company_confirmed", "qualifier", "source_url"} <= set(
        tl.columns
    )


@pytest.mark.parametrize(
    ("qualifier", "bounds"),
    [("=", (5.0, 5.0, None)), (">", (5.0, None, None)), ("<", (None, 5.0, None)), ("~", (None, None, 5.0))],
)  # fmt: skip
def test_implied_interval_follows_the_qualifier(qualifier: str, bounds: tuple) -> None:
    assert L.implied_interval(5.0, qualifier) == bounds
    built = run(directory=[drow()], metrics=[mrow(qualifier=qualifier, value=5.0)], events=[])
    row = built.tables["ledger_timeline"].row(0, named=True)
    assert (row["lower"], row["upper"], row["estimate"]) == bounds


def test_latest_is_the_newest_current_chartable_row_per_company_metric_and_kind(built: L.Ledger) -> None:
    latest = built.tables["ledger_latest"]
    assert latest.height == 25 and set(latest["status"]) == {"current"}
    assert latest.select(L.LATEST_KEY).is_duplicated().sum() == 0
    got = latest.filter((pl.col("company") == "Alpha Edge") & (pl.col("metric_kind") == "primary_round_valuation"))
    assert got["value"].to_list() == [6.0e9] and got["date"].to_list() == ["2025-06-30"]


def test_a_newer_row_that_cannot_be_charted_does_not_displace_an_older_one_that_can() -> None:
    rows = [
        mrow(what="v1", value=1.0e9, as_of_date="2026-01-31", period="2026-01-31"),
        mrow(what="v2", value=9.0e9, as_of_date="2026-09-30", period="2026-09-30", tag="S", chartable=False),
    ]
    built = run(directory=[drow()], metrics=rows, events=[])
    assert built.tables["ledger_latest"]["value"].to_list() == [1.0e9]
    assert built.tables["ledger_timeline"]["value"].to_list() == [1.0e9]
    assert built.tables["reported_unconfirmed"]["value"].to_list() == [9.0e9]


def test_the_acquisition_price_in_a_filing_and_the_press_price_are_both_kept(built: L.Ledger) -> None:
    latest = built.tables["ledger_latest"].filter(pl.col("company") == "Nu Cloud")
    got = {r["metric_kind"]: r for r in latest.iter_rows(named=True)}
    assert got["acquisition_price_announced"]["value"] == 3.2e10 and got["acquisition_price_sec"]["value"] == 3.05e10
    assert got["acquisition_price_sec"]["source_url"].startswith("https://www.sec.gov/")


# --- what is not chartable -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"metric_kind": "secondary_mark"}, "never_charted_kind"),
        ({"metric_kind": "reported_talks"}, "never_charted_kind"),
        ({"metric_kind": "third_party_estimate", "tag": "S"}, "never_charted_kind"),
        ({"tag": "S"}, "unverified_tag"),
        ({"tag": "U", "basis": "reported"}, "unverified_tag"),
        ({"basis": "undisclosed"}, "undisclosed_basis"),
        ({"value": 0}, "invalid_value"),
        ({"value": None}, "invalid_value"),
        ({"value": "4.8bn"}, "invalid_value"),
        ({"chartable": False, "basis": "reported"}, "reported_basis"),
        ({"chartable": False}, "unclassified"),
        ({"chartable": None}, "unclassified"),
        ({"chartable": False, "not_charted_code": "round_size_only"}, "round_size_only"),
        ({"chartable": False, "not_charted_code": "made_up"}, "unclassified"),
        ({"chartable": False, "not_charted_code": "mixed_amount", "basis": "reported"}, "reported_basis"),
    ],
)  # fmt: skip
def test_every_way_a_row_cannot_be_charted_has_a_reason(change: dict, reason: str) -> None:
    row = mrow(**change)
    assert L.reason_not_chartable(row) == reason
    assert reason in L.REASONS
    built = run(directory=[drow()], metrics=[row, mrow(what="ok", value=2.0e9, as_of_date="2026-05-30")], events=[])
    rep = built.tables["reported_unconfirmed"]
    assert rep["reason"].to_list() == [reason]
    assert built.tables["ledger_timeline"].height == 1 and built.tables["ledger_timeline"]["value"][0] == 2.0e9


def test_a_clean_row_is_chartable_and_events_need_no_value() -> None:
    assert L.reason_not_chartable(mrow()) is None
    assert L.reason_not_chartable(erow(), metric=False) is None
    assert L.reason_not_chartable(erow(tag="S"), metric=False) == "unverified_tag"
    assert L.reason_not_chartable(erow(metric_kind="reported_talks"), metric=False) == "never_charted_kind"


def test_reported_unconfirmed_lists_every_non_chartable_row_with_its_reason(built: L.Ledger) -> None:
    rep = built.tables["reported_unconfirmed"]
    got = {(r["source"], r["company"], r["metric_kind"]): (r["tag"], r["reason"]) for r in rep.iter_rows(named=True)}
    assert got == {
        ("private_metrics", "Gamma Cloud", "reported_talks"): ("S", "never_charted_kind"),
        ("private_metrics", "Theta Ops", "reported_talks"): ("S", "never_charted_kind"),
        ("private_metrics", "Theta Ops", "third_party_estimate"): ("V", "never_charted_kind"),
        ("private_metrics", "Theta Ops", "secondary_mark"): ("U", "never_charted_kind"),
        ("events", "Talk Co", "reported_talks"): ("S", "never_charted_kind"),
    }
    talk = rep.filter(pl.col("company") == "Talk Co").row(0, named=True)
    assert (talk["metric"], talk["date"], talk["value"], talk["qualifier"], talk["counterparty"]) == (
        "acquisition_announced", "2026-09-20", 5.0e9, "~", "Equity Partners")  # fmt: skip
    assert "reported" in talk["caveat"].lower() and talk["company_confirmed"] is False


def test_every_published_metric_row_is_either_charted_or_reported(built: L.Ledger) -> None:
    rep = built.tables["reported_unconfirmed"].filter(pl.col("source") == "private_metrics")
    assert built.tables["ledger_timeline"].height + rep.height == len(_rows("ledger_metrics.json"))
    assert next(c for c in built.checks if c["name"] == "Ledger rows accounted for")["status"] == "pass"


def test_tag_s_tag_u_and_never_charted_rows_never_reach_a_chartable_mart() -> None:
    """The proof: every way a figure can be un-chartable, each newer and larger than the real one, none leaks."""
    nasty_values = {9.1e9, 9.2e9, 9.3e9, 9.4e9, 9.5e9, 9.6e9}
    bad_metrics = [
        # mislabelled chartable by the curator, but tag S / tag U / a never-charted kind
        mrow(what="s", value=9.1e9, tag="S", chartable=True, as_of_date="2026-10-01", period="2026-10-01"),
        mrow(what="u", value=9.2e9, tag="U", chartable=True, as_of_date="2026-10-02", period="2026-10-02"),
        mrow(what="mark", value=9.3e9, metric_kind="secondary_mark", chartable=True, as_of_date="2026-10-03",
             period="2026-10-03"),
        mrow(what="talks", value=9.4e9, metric_kind="reported_talks", chartable=True, as_of_date="2026-10-04",
             period="2026-10-04"),
        mrow(what="est", value=9.5e9, metric_kind="third_party_estimate", chartable=True, as_of_date="2026-10-05",
             period="2026-10-05"),
        arr(tag="S", chartable=True, value=9.6e9, as_of_date="2026-10-06", period="2026-10-06",
            source_url="https://example.com/Acme/arr-s"),
    ]  # fmt: skip
    good = [mrow(what="real", value=6.0e9, vintage_year=2025, as_of_date="2025-06-30", period="2025-06-30"),
            arr(value=3.0e8)]  # fmt: skip
    bad_events = [
        erow(kind="acquisition_announced", date_="2026-10-07", tag="S", chartable=True, value=9.1e9, unit="USD",
             qualifier="~", metric_kind="acquisition_price_announced"),
        erow(kind="acquisition_announced", date_="2026-10-08", chartable=True, value=9.2e9, unit="USD",
             qualifier="~", metric_kind="reported_talks"),
    ]  # fmt: skip
    built = run(directory=[drow()], metrics=[*good, *bad_metrics], events=bad_events, multiples={"ZS": 10.0})

    assert L.charted_problems(built.tables) == []
    for name in L.CHARTED_MARTS:
        df = built.tables[name]
        for column in ("tag", "headline_tag", "arr_tag"):
            if column in df.columns:
                assert set(df[column].drop_nulls()) <= {"V", "C"}, (name, column)
        if "metric_kind" in df.columns:
            assert not set(df["metric_kind"].drop_nulls()) & set(C.NEVER_CHARTED_KINDS), name
        numbers = {v for col in df.columns if df.schema[col] == pl.Float64 for v in df[col].drop_nulls()}
        assert not numbers & nasty_values, f"{name} carries a figure that may not be charted"
    got = company(built, "Acme")
    assert got["headline_value"] == 6.0e9 and got["arr_value"] == 3.0e8, "none of them displaced the real figures"
    assert built.summary["private"]["latest_valuation_max"]["value"] == 6.0e9
    assert built.tables["events"].is_empty() and built.summary["events"]["n"] == 0
    rep = built.tables["reported_unconfirmed"]
    assert {v for v in rep["value"]} == nasty_values, "every one of them is in the reported table instead"
    assert next(c for c in built.checks if c["name"] == "Never-charted kinds kept out")["status"] == "pass"


def test_charted_problems_finds_a_leak_in_any_chartable_mart(built: L.Ledger) -> None:
    assert L.charted_problems(built.tables) == []
    leaks = [  # (mart, column, the value that may not be there)
        ("ledger_latest", "tag", "S"),
        ("ledger_timeline", "tag", "U"),
        ("ledger_timeline", "metric_kind", "secondary_mark"),
        ("events", "chartable", False),
        ("events", "metric_kind", "reported_talks"),
        ("private_intervals", "headline_tag", "S"),
        ("private_intervals", "arr_tag", "U"),
    ]
    for mart, column, value in leaks:
        df = built.tables[mart]
        broken = df.with_columns(pl.when(pl.int_range(pl.len()) == 0).then(pl.lit(value)).otherwise(pl.col(column))
                                 .alias(column))  # fmt: skip
        problems = L.charted_problems({**built.tables, mart: broken})
        assert len(problems) == 1 and problems[0].startswith(f"{mart}: 1 row(s) with {column}"), (mart, column)
    assert "reported_unconfirmed" not in L.CHARTED_MARTS, "the reported table is the one place these rows live"


def test_curator_notes_and_unknown_fields_never_reach_any_output(built: L.Ledger) -> None:
    raw = json.dumps(_rows("ledger_metrics.json") + _rows("ledger_directory.json") + _rows("ledger_events.json"))
    assert SENTINEL in raw, "the fixtures do carry working notes"
    for name, df in built.tables.items():
        assert not [c for c in df.columns if "note" in c and c != "above_note"], name
        strings = [v for c in df.columns if df.schema[c] == pl.Utf8 for v in df[c].drop_nulls()]
        assert not [s for s in strings if SENTINEL in s], name
    assert SENTINEL not in json.dumps(built.summary, default=str)
    assert SENTINEL not in json.dumps(built.checks)
    assert next(c for c in built.checks if c["name"] == "No private notes in output")["status"] == "pass"
    extra = mrow() | {"curator_notes": "SECRET-A", "internal": "SECRET-B"}
    out = run(directory=[drow() | {"curator_notes": "SECRET-C"}], metrics=[extra], events=[erow() | extra])
    dumped = json.dumps({k: v.to_dicts() for k, v in out.tables.items()}, default=str) + json.dumps(out.summary)
    assert not any(s in dumped for s in ("SECRET-A", "SECRET-B", "SECRET-C"))


# --- events ----------------------------------------------------------------------------------------------------------
def test_events_are_chartable_only_with_announced_and_sec_prices_in_two_columns(built: L.Ledger) -> None:
    ev = built.tables["events"]
    assert ev.height == 7 and ev["chartable"].all() and "Talk Co" not in ev["company"].to_list()
    nu = ev.filter((pl.col("company") == "Nu Cloud") & (pl.col("event_kind") == "acquisition_announced")).row(
        0, named=True
    )
    assert (nu["announced_value"], nu["sec_value"], nu["sec_qualifier"]) == (3.2e10, 3.05e10, "=")
    assert (nu["chart_value"], nu["chart_basis"], nu["chart_qualifier"]) == (3.05e10, "sec_filing", "=")
    assert (nu["buyer_type"], nu["counterparty"], nu["unit"], nu["metric_kind"]) == (
        "platform_security", "Bigco", "USD", "acquisition_price_announced")  # fmt: skip
    old = ev.filter(pl.col("company") == "Old Acq Co").row(0, named=True)
    assert (old["announced_value"], old["qualifier"], old["sec_value"], old["chart_basis"]) == (
        1.0e9,
        "~",
        None,
        "announced",
    )
    assert (old["buyer_type"], old["status_after"]) == ("pe", "pending_acquisition")


def test_a_row_that_is_itself_the_filing_fills_the_sec_column_only(built: L.Ledger) -> None:
    filing = built.tables["events"].filter(pl.col("company") == "Filing Co").row(0, named=True)
    assert filing["announced_value"] is None and filing["sec_value"] == 4.1e8 and filing["chart_basis"] == "sec_filing"
    assert filing["sec_source_url"] == "https://www.sec.gov/Archives/edgar/data/0000001/filing-co-8k.htm"
    assert filing["metric_kind"] == "acquisition_price_sec"


def test_an_event_without_a_price_has_no_value_columns(built: L.Ledger) -> None:
    ev = built.tables["events"]
    assert ev.filter(pl.col("buyer_type").is_null()).height == 1  # the round
    plain = L.build([], [], [erow(value=None) | {"counterparty": "X"}], {}, today=TODAY).tables["events"]
    row = plain.row(0, named=True)
    assert (row["announced_value"], row["sec_value"], row["chart_value"], row["chart_basis"], row["unit"]) == (
        None,
    ) * 5


@pytest.mark.parametrize("bad", [0, -5.0, "n/a", True])
def test_an_unusable_sec_value_is_dropped_and_fails_a_check(bad) -> None:
    ev = erow(value=1.0e9, unit="USD", qualifier="=", metric_kind="acquisition_price_announced") | {"sec_value": bad}
    built = L.build([], [], [ev], {}, today=TODAY)
    row = built.tables["events"].row(0, named=True)
    assert row["sec_value"] is None and row["chart_basis"] == "announced" and row["announced_value"] == 1.0e9
    assert next(c for c in built.checks if c["name"] == "Event SEC prices")["status"] == "fail"


def test_a_sec_value_without_a_currency_is_refused_not_guessed() -> None:
    ev = erow() | {"sec_value": 3.0e9}
    built = L.build([], [], [ev], {}, today=TODAY)
    assert built.tables["events"]["sec_value"][0] is None
    assert next(c for c in built.checks if c["name"] == "Event SEC prices")["status"] == "fail"


def test_non_chartable_events_go_to_the_reported_table_and_are_not_counted(built: L.Ledger) -> None:
    rep = built.tables["reported_unconfirmed"].filter(pl.col("source") == "events")
    assert rep["company"].to_list() == ["Talk Co"]
    assert built.summary["events"]["n"] == 7 and built.summary["events"]["n_unconfirmed"] == 1
    assert built.summary["events"]["by_buyer_type"]["pe"] == 2, "Talk Co's buyer is not counted"


def test_an_acquisition_without_a_buyer_type_is_refused() -> None:
    with pytest.raises(L.LedgerError, match="buyer_type"):
        L.build([], [], [erow(buyer_type=None)], {}, today=TODAY)
    # the same row as a round is fine
    assert L.build([], [], [erow(kind="round", buyer_type=None)], {}, today=TODAY).tables["events"].height == 1


# --- deals: an announcement and its closing are one acquisition -------------------------------------------------------
def _ev(company_: str, kind: str, day: str, **kw) -> dict:
    return {
        "company": company_,
        "event_kind": kind,
        "event_date": day,
        "counterparty": kw.pop("counterparty", "B"),
        **kw,
    }


def test_an_announcement_and_its_closing_are_one_deal() -> None:
    got = L.deals([_ev("A", "acquisition_closed", "2026-06-10"), _ev("A", "acquisition_announced", "2026-03-01")])
    assert got == [{"company": "A", "start": "2026-03-01", "closed": "2026-06-10"}]


def test_a_closing_seen_alone_and_a_take_private_each_stand_alone() -> None:
    got = L.deals(
        [_ev("A", "acquisition_closed", "2026-06-10"), _ev("T", "take_private", "2026-08-01", closed_date="2026-09-30")]
    )
    assert sorted(got, key=lambda d: d["company"]) == [
        {"company": "A", "start": "2026-06-10", "closed": "2026-06-10"},
        {"company": "T", "start": "2026-08-01", "closed": "2026-09-30"},
    ]


def test_a_withdrawn_deal_and_a_later_one_for_the_same_company_are_two_deals() -> None:
    events = [_ev("A", "acquisition_announced", "2025-01-10"), _ev("A", "acquisition_announced", "2026-05-01"),
              _ev("A", "acquisition_closed", "2026-07-01")]  # fmt: skip
    got = L.deals(events)
    assert got == [{"company": "A", "start": "2025-01-10", "closed": None},
                   {"company": "A", "start": "2026-05-01", "closed": "2026-07-01"}]  # fmt: skip


def test_other_event_kinds_are_not_deals() -> None:
    assert (
        L.deals([_ev("A", "ipo", "2026-01-01"), _ev("A", "round", "2026-02-01"), _ev("A", "delisting", "2026-03-01")])
        == []
    )


def test_acquisitions_in_the_last_twelve_months_count_deals_and_include_today_but_not_a_year_ago() -> None:
    def count(events: list[dict], today: date = TODAY) -> tuple[int, int]:
        out = L.build([], [], events, {}, today=today).summary["events"]
        return out["acquisitions_12m"], out["acquisitions_closed_12m"]

    pair = [erow("A", "acquisition_announced", "2026-03-01"), erow("A", "acquisition_closed", "2026-06-10")]
    assert count(pair) == (1, 1), "two events, one acquisition"
    assert count([erow("A", "acquisition_announced", "2025-10-17")]) == (0, 0), "exactly a year back is outside"
    assert count([erow("A", "acquisition_announced", "2025-10-18")]) == (1, 0)
    assert count([erow("A", "acquisition_announced", "2026-10-17")]) == (1, 0), "today is inside"
    assert count([erow("A", "acquisition_announced", "2026-10-18")]) == (0, 0), "the future is not"
    old_open = [erow("A", "acquisition_announced", "2025-06-01"), erow("A", "acquisition_closed", "2026-02-01")]
    assert count(old_open) == (0, 1), "announced before the window, closed inside it"
    assert count([erow("A", "take_private", "2026-08-01")]) == (1, 0)
    assert count([erow("A", "ipo", "2026-08-01", buyer_type=None)]) == (0, 0)
    assert count([erow("A", "acquisition_announced", "2027-02-28")], today=date(2028, 2, 29)) == (0, 0)
    assert count([erow("A", "acquisition_announced", "2027-03-01")], today=date(2028, 2, 29)) == (1, 0)


def test_the_event_counts_on_the_fixtures(built: L.Ledger) -> None:
    ev = built.summary["events"]
    assert ev["n"] == 7 and ev["acquisitions_12m"] == 3 and ev["acquisitions_closed_12m"] == 2
    assert ev["by_kind"] == {"ipo": 1, "acquisition_announced": 2, "acquisition_closed": 2, "take_private": 1,
                             "delisting": 0, "round": 1}  # fmt: skip
    assert ev["by_buyer_type"] == {"platform_security": 2, "non_security_software": 1, "pe": 2, "insurance": 0,
                                   "state": 0, "strategic": 0, "other": 0, "none": 1}  # fmt: skip
    assert ev["n_sec_priced"] == 3
    assert (ev["window_from"], ev["window_to"]) == ("2025-10-17", "2026-10-17")
    assert ev["latest"]["company"] == "Filing Co" and ev["latest"]["chart_basis"] == "sec_filing"
    assert set(ev["by_kind"]) == set(R.EVENT_KINDS) and set(ev["by_buyer_type"]) == set(R.BUYER_TYPES)


# --- the counts for facts --------------------------------------------------------------------------------------------
def test_private_counts_on_the_fixtures(built: L.Ledger) -> None:
    p = built.summary["private"]
    assert (p["n_directory"], p["n_companies"], p["n_ledger_rows"], p["n_chartable"], p["n_reported_unconfirmed"]) == (
        13, 14, 30, 26, 5)  # fmt: skip
    assert (p["n_undisclosed"], p["n_stale"], p["n_recheck_overdue"], p["n_with_arr"], p["n_with_headline"]) == (
        1, 2, 1, 9, 10)  # fmt: skip
    assert (p["n_discount_scored"], p["n_comparable_scored"], p["n_q6_scored"], p["n_q6_above"]) == (9, 8, 5, 3)
    assert p["multiples_as_of"] == "2026-10-16" and built.summary["as_of"] == "2026-10-17"


def test_by_layer_counts_the_directory_and_lists_every_layer(built: L.Ledger) -> None:
    by = built.summary["private"]["by_layer"]
    assert list(by) == list(C.LAYERS) and sum(by.values()) == 13
    assert (by["network_edge"], by["cloud_appsec"], by["email_human"]) == (4, 3, 0)


def test_the_highest_valuation_carries_what_a_reader_needs_and_is_in_dollars(built: L.Ledger) -> None:
    p = built.summary["private"]
    top = p["latest_valuation_max"]
    assert (top["company"], top["value"], top["unit"], top["qualifier"], top["basis"]) == (
        "Kappa Guard", 8.0e9, "USD", ">", "disclosed")  # fmt: skip
    assert (top["as_of"], top["period"], top["tag"], top["vintage_year"], top["stale_mark"]) == (
        "2026-05-31", "2026-05-31", "V", 2026, False)  # fmt: skip
    assert top["source_url"] == "https://example.com/kappa-guard/val-2026"
    by_unit = p["latest_valuation_max_by_unit"]
    assert set(by_unit) == {"USD", "CNY"} and by_unit["CNY"]["company"] == "Lambda Net"
    assert by_unit["CNY"]["value"] > top["value"], (
        "yuan 1.0e10 is bigger as a number and is never compared with dollars"
    )


def test_the_highest_valuation_prefers_an_exact_figure_on_a_tie() -> None:
    rows = [mrow("A", qualifier=">", value=5.0e9), mrow("B", qualifier="=", value=5.0e9), mrow("C", value=4.0e9)]
    built = run(directory=[drow("A"), drow("B"), drow("C")], metrics=rows, events=[])
    assert built.summary["private"]["latest_valuation_max"]["company"] == "B"


def test_the_summary_is_plain_json(built: L.Ledger) -> None:
    again = json.loads(json.dumps(built.summary))
    assert again == built.summary


# --- nothing, one snapshot, many ---------------------------------------------------------------------------------------
def test_empty_inputs_give_empty_validated_tables_and_a_zero_summary() -> None:
    out = L.build([], [], [], {})
    for name, df in out.tables.items():
        assert df.is_empty() and list(df.columns) == list(L.DTYPES[name]), name
        assert df.schema == pl.Schema(L.DTYPES[name]), name
        L.SCHEMAS[name].validate(df)
    p, e = out.summary["private"], out.summary["events"]
    assert out.summary["as_of"] is None
    assert (p["n_directory"], p["n_ledger_rows"], p["n_chartable"], p["n_reported_unconfirmed"], p["n_with_arr"]) == (
        0,
    ) * 5
    assert p["latest_valuation_max"] is None and p["latest_valuation_max_by_unit"] == {}
    assert sum(p["by_layer"].values()) == 0 and list(p["by_layer"]) == list(C.LAYERS)
    assert (e["n"], e["acquisitions_12m"], e["n_sec_priced"], e["latest"]) == (0, 0, 0, None)
    assert next(c for c in out.checks if c["name"] == "Private ledger")["status"] == "warn"
    assert L.build(None, None, None, None).summary == out.summary  # type: ignore[arg-type]


def test_a_directory_with_no_ledger_yet_lists_companies_with_a_reason_each() -> None:
    out = run(metrics=[], events=[])
    iv = out.tables["private_intervals"]
    assert iv.height == 13 and iv["headline_value"].null_count() == 13 and iv["above"].null_count() == 13
    assert set(iv["above_reason"]) == {"no current chartable primary-round valuation"}
    assert out.summary["private"]["n_directory"] == 13 and out.summary["private"]["n_ledger_rows"] == 0
    assert out.tables["ledger_timeline"].is_empty()


def test_today_defaults_to_the_newest_last_checked_and_never_the_clock() -> None:
    out = L.build(
        [drow(last_checked="2026-10-02")], [mrow(last_checked="2026-10-01")], [erow(last_checked="2026-10-09")], {}
    )
    assert out.summary["as_of"] == "2026-10-09" and out.summary["events"]["window_to"] == "2026-10-09"
    explicit = L.build([drow()], [mrow()], [], {}, today=date(2030, 1, 1))
    assert explicit.summary["as_of"] == "2030-01-01"


def test_build_is_pure_it_does_not_change_its_inputs_and_row_order_does_not_matter() -> None:
    directory, metrics, events = (copy.deepcopy(_rows(n)) for n in
                                  ("ledger_directory.json", "ledger_metrics.json", "ledger_events.json"))  # fmt: skip
    before = copy.deepcopy((directory, metrics, events))
    multiples = _multiples()["multiples"]
    first = L.build(directory, metrics, events, multiples, today=TODAY, multiples_as_of="2026-10-16")
    assert (directory, metrics, events) == before
    rng = random.Random(7)
    for rows in (directory, metrics, events):
        rng.shuffle(rows)
    second = L.build(directory, metrics, events, multiples, today=TODAY, multiples_as_of="2026-10-16")
    for name in first.tables:
        assert first.tables[name].equals(second.tables[name]), name
    assert first.summary == second.summary and first.checks == second.checks


def test_build_returns_a_named_tuple_of_tables_summary_and_checks(built: L.Ledger) -> None:
    tables, summary, checks = built
    assert set(tables) == {"private_intervals", "ledger_timeline", "ledger_latest", "reported_unconfirmed", "events"}
    assert set(summary) == {"as_of", "private", "events"}
    assert built.tables is tables and built.summary is summary and built.checks is checks


# --- schemas and failure paths -----------------------------------------------------------------------------------------
def test_every_table_satisfies_its_schema_and_has_the_declared_columns(built: L.Ledger) -> None:
    for name, df in built.tables.items():
        L.SCHEMAS[name].validate(df)
        assert df.schema == pl.Schema(L.DTYPES[name]), name


def test_a_duplicate_company_in_the_directory_is_an_error() -> None:
    with pytest.raises(L.LedgerError, match="duplicate company in private_directory: Acme"):
        L.build([drow(), drow()], [], [], {})


def test_a_duplicate_ledger_line_is_an_error_not_a_silent_pick() -> None:
    with pytest.raises(L.LedgerError, match="ledger_timeline"):
        L.build([drow()], [mrow(what="a"), mrow(what="b")], [], {})
    with pytest.raises(L.LedgerError, match="events"):
        L.build([], [], [erow(), erow()], {})


@pytest.mark.parametrize(
    ("kind", "row"),
    [
        ("metrics", mrow(qualifier="about")),
        ("metrics", mrow(layer="cloud")),
        ("metrics", mrow(market="mars")),
        ("metrics", mrow(unit=None)),
        ("metrics", mrow(as_of_date="June 2026")),
        ("metrics", mrow(source_url="ftp://example.com/x")),
        ("events", erow(event_date="March 2026")),
        ("events", erow(closed_date="soon")),
        ("events", erow(status_after="unicorn")),
    ],
)  # fmt: skip
def test_a_value_outside_its_vocabulary_is_an_error_that_names_the_table(kind: str, row: dict) -> None:
    metrics, events = ([row], []) if kind == "metrics" else ([], [row])
    with pytest.raises(L.LedgerError):
        L.build([drow()], metrics, events, {})


@pytest.mark.parametrize("field", ["event_date", "closed_date"])
def test_an_impossible_calendar_date_is_a_ledger_error_not_a_crash(field: str) -> None:
    """2026-02-30 has the shape of a date and the schema alone would let it through to the window arithmetic."""
    with pytest.raises(L.LedgerError, match=f"{field} '2026-02-30' is not a date"):
        L.build([], [], [erow(**{field: "2026-02-30"})], {}, today=TODAY)


@pytest.mark.parametrize("which", ["directory", "metrics", "events"])
def test_a_row_without_a_company_is_a_ledger_error(which: str) -> None:
    rows = {"directory": [drow(company="")], "metrics": [mrow(company="")], "events": [erow(company=None)]}
    args = {"directory": [], "metrics": [], "events": [], **{which: rows[which]}}
    with pytest.raises(L.LedgerError, match="no company"):
        L.build(args["directory"], args["metrics"], args["events"], {}, today=TODAY)


def test_a_directory_row_without_a_layer_is_a_ledger_error() -> None:
    row = drow()
    del row["layer"]
    with pytest.raises(L.LedgerError, match="private_intervals"):
        L.build([row], [], [], {})


def test_the_fallback_date_skips_a_last_checked_that_is_not_a_date() -> None:
    out = L.build([drow(last_checked="soon")], [mrow(last_checked="2026-10-03")], [erow(last_checked=None)], {})
    assert out.summary["as_of"] == "2026-10-03"
    assert L.build([drow(last_checked="soon")], [], [], {}).summary["as_of"] is None


def test_the_schemas_refuse_what_the_builder_never_makes(built: L.Ledger) -> None:
    t = built.tables
    cases = [
        ("ledger_timeline", t["ledger_timeline"].with_columns(pl.lit("S").alias("tag"))),
        ("ledger_timeline", t["ledger_timeline"].with_columns(pl.lit(False).alias("chartable"))),
        ("ledger_timeline", t["ledger_timeline"].with_columns(pl.lit("secondary_mark").alias("metric_kind"))),
        ("ledger_timeline", t["ledger_timeline"].with_columns(pl.lit(None, pl.Float64).alias("lower"))),
        ("ledger_latest", t["ledger_timeline"].filter(pl.col("status") == "superseded")),
        ("reported_unconfirmed", t["reported_unconfirmed"].with_columns(pl.lit("because").alias("reason"))),
        ("reported_unconfirmed", t["reported_unconfirmed"].with_columns(pl.lit(None, pl.Utf8).alias("qualifier"))),
        ("events", t["events"].with_columns(pl.lit(1.0).alias("chart_value"))),
        ("events", t["events"].with_columns(pl.lit(None, pl.Utf8).alias("buyer_type"))),
        ("private_intervals", t["private_intervals"].with_columns(pl.lit(True).alias("above"))),
        ("private_intervals", t["private_intervals"].with_columns(pl.lit("S").alias("headline_tag"))),
        ("private_intervals", t["private_intervals"].with_columns(pl.lit("V").alias("discount_tag"))),
        ("private_intervals", t["private_intervals"].with_columns((pl.col("discount_lower") * 10).alias("discount_lower"))),
    ]  # fmt: skip
    for name, bad in cases:
        with pytest.raises(pandera.errors.SchemaError):
            L.SCHEMAS[name].validate(bad)


def test_q6_is_never_decided_on_a_reported_headline_even_if_a_table_says_so(built: L.Ledger) -> None:
    iv = built.tables["private_intervals"]
    forged = iv.with_columns(
        pl.when(pl.col("company") == "Zeta Secure").then(pl.lit(True)).otherwise(pl.col("above")).alias("above"),
        pl.when(pl.col("company") == "Zeta Secure").then(pl.lit(True)).otherwise(pl.col("q6_scored")).alias("q6_scored"),
    )  # fmt: skip
    with pytest.raises(pandera.errors.SchemaError):
        L.INTERVALS_SCHEMA.validate(forged)


def test_checks_have_the_house_shape_and_report_what_the_fixtures_contain(built: L.Ledger) -> None:
    names = [c["name"] for c in built.checks]
    assert len(names) == len(set(names))
    assert all(set(c) == {"name", "status", "detail"} and c["status"] in {"pass", "warn", "fail"} for c in built.checks)
    by = {c["name"]: c for c in built.checks}
    assert (
        by["Ring C ledger matches directory"]["status"] == "warn"
        and "Orphan Co" in by["Ring C ledger matches directory"]["detail"]
    )
    assert by["Private ledger freshness"]["status"] == "warn" and by["Private ledger freshness"]["detail"].startswith(
        "1 "
    )
    assert by["Q6 inputs"]["status"] == "warn" and "5 of 14" in by["Q6 inputs"]["detail"]
    assert not [c for c in built.checks if c["status"] == "fail"]


def test_a_ledger_row_in_a_different_layer_from_the_directory_fails_a_check_and_the_directory_wins() -> None:
    out = L.build([drow(layer="identity")], [mrow(layer="network_edge"), arr(layer="network_edge")], [], {"OKTA": 5.0})
    got = out.tables["private_intervals"].row(0, named=True)
    assert got["layer"] == "identity" and got["comparable_tickers"] == "OKTA"
    check = next(c for c in out.checks if c["name"] == "Ring C ledger matches directory")
    assert check["status"] == "fail" and "Acme" in check["detail"]


def test_the_q6_check_passes_once_the_registered_minimum_of_companies_can_be_scored() -> None:
    n = int(C.Q6["min_scored"])
    names = [f"Co{i}" for i in range(n)]
    metrics = [r for name in names for r in (mrow(name), arr(name))]
    out = L.build([drow(name) for name in names], metrics, [], {"ZS": 5.0, "NTSK": 6.0, "FTNT": 7.0}, today=TODAY)
    assert out.summary["private"]["n_q6_scored"] == n
    assert next(c for c in out.checks if c["name"] == "Q6 inputs")["status"] == "pass"


# --- a take-private that repeats an announcement is the same deal ----------------------------------------------------
JAMF_DAY, JAMF_CLOSE = "2025-10-29", "2026-01-30"
FP = "Francisco Partners"


def jamf_rows() -> list[dict]:
    """The shape the curated events have for Jamf: one deal entered as an announcement and as a take-private on the same
    day with the same buyer and the same price, then its closing, whose price is the filing's."""
    price = {"value": 2.2e9, "unit": "USD", "qualifier": "~", "metric_kind": "take_private_price"}
    common = {"counterparty": FP, "buyer_type": "pe"}
    return [
        erow("Jamf", "acquisition_announced", JAMF_DAY, **common, **price),
        erow("Jamf", "take_private", JAMF_DAY, **common, **price),
        erow("Jamf", "acquisition_closed", JAMF_CLOSE, **common, **(price | {"metric_kind": "acquisition_price_sec"})),
    ]


def test_the_jamf_rows_are_valid_curated_rows() -> None:
    for r in jamf_rows():
        assert row_problems(R.SPEC, "events", r) == [], r["event_kind"]


def test_a_take_private_that_repeats_the_announcement_folds_into_it() -> None:
    rows = jamf_rows()
    want = [{"company": "Jamf", "start": JAMF_DAY, "closed": JAMF_CLOSE}]
    assert L.deals(rows) == want
    assert L.deals(list(reversed(rows))) == want, "the order of the input does not matter"
    assert L.deals(rows[:2]) == [{"company": "Jamf", "start": JAMF_DAY, "closed": None}], "before the closing: one deal"


@pytest.mark.parametrize(
    ("take_private", "reason"),
    [
        ({"counterparty": "Rival Capital"}, "another buyer on the same day is another deal"),
        ({"event_date": "2025-10-30"}, "the next day is a later event, not the same fact"),
        ({"company": "Other Co"}, "another company is another deal"),
    ],
)
def test_a_take_private_that_does_not_repeat_the_announcement_stands_alone(take_private: dict, reason: str) -> None:
    announced = _ev("Jamf", "acquisition_announced", JAMF_DAY, counterparty=FP)
    tp = {**_ev("Jamf", "take_private", JAMF_DAY, counterparty=FP), **take_private}
    assert len(L.deals([announced, tp])) == 2, reason


def test_the_buyer_is_compared_without_case_or_stray_spaces() -> None:
    got = L.deals([_ev("A", "acquisition_announced", "2026-03-01", counterparty="Big Co"),
                   _ev("A", "take_private", "2026-03-01", counterparty=" big co ")])  # fmt: skip
    assert len(got) == 1
    none = L.deals([_ev("A", "acquisition_announced", "2026-03-01", counterparty=None),
                    _ev("A", "take_private", "2026-03-01", counterparty="")])  # fmt: skip
    assert len(none) == 1, "no buyer on both rows is still the same deal"


def test_a_take_private_alone_still_stands_alone_and_a_folded_one_lends_its_closing_date() -> None:
    assert L.deals([_ev("T", "take_private", "2026-08-01")]) == [
        {"company": "T", "start": "2026-08-01", "closed": None}
    ]
    got = L.deals([_ev("A", "acquisition_announced", "2026-03-01"),
                   _ev("A", "take_private", "2026-03-01", closed_date="2026-05-01")])  # fmt: skip
    assert got == [{"company": "A", "start": "2026-03-01", "closed": "2026-05-01"}]


def test_a_deal_entered_under_both_kinds_counts_once_in_the_summary_and_in_the_mart() -> None:
    built = L.build([], [], jamf_rows(), {}, today=TODAY)
    ev = built.summary["events"]
    assert (ev["n"], ev["acquisitions_12m"], ev["acquisitions_closed_12m"]) == (3, 1, 1), "three events, one deal"
    assert ev["by_kind"]["take_private"] == 1 and ev["by_kind"]["acquisition_announced"] == 1, "events stay events"
    table = built.tables["events"]
    assert table.height == 3, "no row is dropped from the mart"
    flags = {r["event_kind"]: r["deal_start"] for r in table.iter_rows(named=True)}
    assert flags == {"acquisition_announced": True, "take_private": False, "acquisition_closed": False}
    inside = (pl.col("event_date") > ev["window_from"]) & (pl.col("event_date") <= ev["window_to"])
    assert table.filter(pl.col("deal_start") & inside).height == ev["acquisitions_12m"]


def test_the_mart_and_the_summary_count_the_same_deals_on_the_fixtures(built: L.Ledger) -> None:
    ev = built.summary["events"]
    inside = (pl.col("event_date") > ev["window_from"]) & (pl.col("event_date") <= ev["window_to"])
    got = built.tables["events"].filter(pl.col("deal_start") & inside).height
    assert got == ev["acquisitions_12m"] == 3
    assert built.tables["events"].filter(pl.col("event_kind").is_in(["ipo", "round"]))["deal_start"].not_().all()


def test_a_closing_seen_alone_opens_a_deal_and_an_attached_closing_does_not() -> None:
    alone = L.build([], [], [erow("A", "acquisition_closed", "2026-06-10")], {}, today=TODAY).tables["events"]
    assert alone["deal_start"].to_list() == [True]
    pair = [erow("A", "acquisition_announced", "2026-03-01"), erow("A", "acquisition_closed", "2026-06-10")]
    table = L.build([], [], pair, {}, today=TODAY).tables["events"]
    assert dict(zip(table["event_kind"], table["deal_start"], strict=True)) == {
        "acquisition_announced": True, "acquisition_closed": False}  # fmt: skip


def test_the_events_schema_refuses_a_deal_counted_twice_or_not_at_all() -> None:
    table = L.build([], [], jamf_rows(), {}, today=TODAY).tables["events"]
    L.EVENTS_SCHEMA.validate(table)
    twice = table.with_columns((pl.col("event_kind") != "acquisition_closed").alias("deal_start"))
    with pytest.raises(pandera.errors.SchemaError, match="deal_start"):
        L.EVENTS_SCHEMA.validate(twice)
    never = table.with_columns(pl.lit(False).alias("deal_start"))
    with pytest.raises(pandera.errors.SchemaError, match="deal_start"):
        L.EVENTS_SCHEMA.validate(never)
    ipo = L.build([], [], [erow("A", "ipo", "2026-05-05", buyer_type=None)], {}, today=TODAY).tables["events"]
    with pytest.raises(pandera.errors.SchemaError, match="deal_start"):
        L.EVENTS_SCHEMA.validate(ipo.with_columns(pl.lit(True).alias("deal_start")))


# --- the metric kind of the price that is drawn ------------------------------------------------------------------------
def test_chart_metric_kind_is_the_kind_of_the_drawn_price(built: L.Ledger) -> None:
    got = {(r["company"], r["event_kind"]): (r["chart_basis"], r["chart_metric_kind"], r["metric_kind"])
           for r in built.tables["events"].iter_rows(named=True)}  # fmt: skip
    assert got[("Nu Cloud", "acquisition_announced")] == (
        "sec_filing",
        "acquisition_price_sec",
        "acquisition_price_announced",
    ), "the filing is drawn, not the press price"
    assert got[("Old Acq Co", "acquisition_announced")] == (
        "announced", "acquisition_price_announced", "acquisition_price_announced")  # fmt: skip
    assert got[("Filing Co", "acquisition_closed")] == ("sec_filing", "acquisition_price_sec", "acquisition_price_sec")
    assert got[("Take Co", "take_private")][1] == "take_private_price"
    assert got[("Alpha Edge", "round")] == ("announced", "round_amount", "round_amount")


def test_an_event_without_a_price_has_no_chart_metric_kind() -> None:
    row = (
        L.build([], [], [erow(value=None) | {"counterparty": "X"}], {}, today=TODAY).tables["events"].row(0, named=True)
    )
    assert (row["chart_basis"], row["chart_metric_kind"]) == (None, None)
    unkinded = erow(value=1.0e9, unit="USD", qualifier="=") | {"counterparty": "X"}
    row = L.build([], [], [unkinded], {}, today=TODAY).tables["events"].row(0, named=True)
    assert (row["chart_basis"], row["chart_metric_kind"], row["metric_kind"]) == ("announced", None, None)


def test_the_events_schema_refuses_a_chart_metric_kind_that_is_not_the_drawn_prices(built: L.Ledger) -> None:
    table = built.tables["events"]
    wrong = table.with_columns(
        pl.when(pl.col("company") == "Nu Cloud").then(pl.lit("acquisition_price_announced"))
        .otherwise(pl.col("chart_metric_kind")).alias("chart_metric_kind"))  # fmt: skip
    with pytest.raises(pandera.errors.SchemaError, match="chart_metric_kind"):
        L.EVENTS_SCHEMA.validate(wrong)
    with pytest.raises(pandera.errors.SchemaError, match="chart_metric_kind"):
        L.EVENTS_SCHEMA.validate(table.with_columns(pl.lit("round_amount").alias("chart_metric_kind")))


# --- the three groups of what is reported and never charted --------------------------------------------------------------
@pytest.mark.parametrize(
    ("reason", "confirmed", "group"),
    [
        ("never_charted_kind", False, "never_charted_kind"),
        ("never_charted_kind", True, "never_charted_kind"),
        ("round_size_only", True, "company_confirmed_not_charted"),
        ("reported_basis", True, "company_confirmed_not_charted"),
        ("undisclosed_basis", True, "company_confirmed_not_charted"),
        ("outside_scope", False, "reported_by_a_publication"),
        ("reported_basis", False, "reported_by_a_publication"),
        ("unverified_tag", False, "reported_by_a_publication"),
        ("invalid_value", False, "reported_by_a_publication"),
    ],
)
def test_reason_group_splits_the_reasons_three_ways(reason: str, confirmed: bool, group: str) -> None:
    assert L.reason_group(reason, confirmed) == group


def test_every_reason_falls_into_exactly_one_group() -> None:
    assert set(L.REASON_GROUPS) == {"company_confirmed_not_charted", "reported_by_a_publication", "never_charted_kind"}
    for reason in L.REASONS:
        for confirmed in (True, False):
            assert L.reason_group(reason, confirmed) in L.REASON_GROUPS


def test_reported_rows_carry_their_group_beside_the_reason() -> None:
    said = "Example News reported the figure on 3 August 2026."
    rows = [
        mrow("Conf", "a", chartable=False, not_charted_code="superseded_figure"),  # confirmed, left uncharted
        mrow("Press", "b", basis="reported", chartable=False, company_confirmed=False, caveat=said),
        mrow("Both", "c", basis="reported", chartable=False, company_confirmed=True, caveat=said),
        mrow("Mark", "d", metric_kind="secondary_mark", chartable=False, company_confirmed=True),
        mrow("Tag", "e", tag="S", chartable=False, company_confirmed=False),
        erow(
            "Ev",
            "acquisition_announced",
            "2026-03-01",
            chartable=False,
            company_confirmed=False,
            not_charted_code="no_figure_stated",
        ),
        erow(
            "Ev2",
            "acquisition_announced",
            "2026-03-02",
            chartable=False,
            company_confirmed=True,
            not_charted_code="ip_asset_purchase",
        ),
    ]
    metrics_in, events_in = rows[:5], rows[5:]
    assert all(row_problems(R.SPEC, "private_metrics", r) == [] for r in metrics_in)
    assert all(row_problems(R.SPEC, "events", r) == [] for r in events_in)
    out = L.build([], metrics_in, events_in, {}, today=TODAY)
    rep = out.tables["reported_unconfirmed"]
    got = {r["company"]: (r["reason"], r["reason_group"]) for r in rep.iter_rows(named=True)}
    assert got == {
        "Conf": ("superseded_figure", "company_confirmed_not_charted"),
        "Press": ("reported_basis", "reported_by_a_publication"),
        "Both": ("reported_basis", "company_confirmed_not_charted"),
        "Mark": ("never_charted_kind", "never_charted_kind"),
        "Tag": ("unverified_tag", "reported_by_a_publication"),
        "Ev": ("no_figure_stated", "reported_by_a_publication"),
        "Ev2": ("ip_asset_purchase", "company_confirmed_not_charted"),
    }
    assert out.summary["private"]["n_reported_by_group"] == {
        "company_confirmed_not_charted": 3, "reported_by_a_publication": 3, "never_charted_kind": 1}  # fmt: skip


def test_the_group_counts_add_up_to_the_reported_rows(built: L.Ledger) -> None:
    p = built.summary["private"]
    assert p["n_reported_by_group"] == {"company_confirmed_not_charted": 0, "reported_by_a_publication": 0,
                                        "never_charted_kind": 5}  # fmt: skip
    assert sum(p["n_reported_by_group"].values()) == p["n_reported_unconfirmed"]
    assert list(p["n_reported_by_group"]) == list(L.REASON_GROUPS)


def test_the_reported_schema_refuses_a_group_that_disagrees_with_the_rule(built: L.Ledger) -> None:
    rep = built.tables["reported_unconfirmed"]
    L.REPORTED_SCHEMA.validate(rep)
    with pytest.raises(pandera.errors.SchemaError, match="reason_group"):
        L.REPORTED_SCHEMA.validate(rep.with_columns(pl.lit("reported_by_a_publication").alias("reason_group")))
    with pytest.raises(pandera.errors.SchemaError):
        L.REPORTED_SCHEMA.validate(rep.with_columns(pl.lit("other").alias("reason_group")))


# --- counts the page prints and must not compute -------------------------------------------------------------------------
def test_china_counts_comparable_layers_and_stale_ledger_rows_on_the_fixtures(built: L.Ledger) -> None:
    p = built.summary["private"]
    raw = _rows("ledger_metrics.json")
    assert p["n_china_rows"] == sum(1 for r in raw if r["market"] == "china") == 3
    assert p["n_china_headline_rounds"] == 1, "Lambda Net, the one Chinese company with a headline round"
    assert p["n_comparable_layers"] == len(C.COMPARABLES_BY_LAYER) == 4
    # the page's own count of stale rows: a chartable row that is not superseded and carries a stale mark or is overdue
    stale = [r for r in raw if L.reason_not_chartable(r) is None and r["status"] != "superseded"
             and (r.get("stale") or r.get("stale_mark"))]  # fmt: skip
    assert p["n_stale_ledger_rows"] == len(stale) == 3
    assert (p["n_stale"], p["n_recheck_overdue"]) == (2, 1), "n_stale counts headline rounds only"


def test_a_superseded_stale_round_is_not_a_stale_ledger_row() -> None:
    rows = [
        mrow("A", "old", status="superseded", stale_mark=True, as_of_date="2025-04-30", period="2025-04-30",
             vintage_year=2025),
        mrow("A", "new", stale_mark=True),
        mrow("B", "fresh", stale_mark=False),
        arr("B", stale_mark=None),
    ]  # fmt: skip
    p = run(directory=[drow("A"), drow("B")], metrics=rows, events=[]).summary["private"]
    assert p["n_stale_ledger_rows"] == 1 and p["n_stale"] == 1


def test_china_headline_rounds_follow_the_directory_market() -> None:
    cn = [mrow("Cn1", market="china", unit="CNY"), mrow("Cn2", market="china", unit="CNY", qualifier="~"),
          mrow("Us1"), arr("Cn1", market="china", unit="CNY")]  # fmt: skip
    directory = [drow("Cn1", market="china"), drow("Cn2", market="china"), drow("Cn3", market="china"), drow("Us1")]
    p = run(directory=directory, metrics=cn, events=[]).summary["private"]
    assert (p["n_china_headline_rounds"], p["n_china_rows"]) == (2, 3), "Cn3 has no round and no row"


def test_the_window_end_is_the_day_the_twelve_months_end() -> None:
    ev = run().summary["events"]
    assert ev["window_end"] == ev["window_to"] == "2026-10-17" and ev["window_from"] == "2025-10-17"
    other = run(today=date(2027, 1, 5)).summary["events"]
    assert other["window_end"] == "2027-01-05"
    assert L.build([], [], [], {}).summary["events"]["window_end"] is None, "no clock, no events, no window"


# --- closed reason codes, one line per figure, and the facts the page needs for its sentences ----------------------------
def test_every_reason_has_a_sentence_and_the_mart_carries_it(built: L.Ledger) -> None:
    from pipelines.cyber import schema

    assert set(L.REASON_LABELS) == set(L.REASONS) and set(L.NOT_CHARTED_CODES) < set(L.REASONS)
    assert not [r for r, text in L.REASON_LABELS.items() if schema.WORKING_NOTE_WORDS.search(r + " " + text)]
    rep = built.tables["reported_unconfirmed"]
    assert rep.height and all(L.REASON_LABELS[r] == t for r, t in zip(rep["reason"], rep["reason_label"], strict=True))
    with pytest.raises(pandera.errors.SchemaError, match="reason_label"):
        L.REPORTED_SCHEMA.validate(rep.with_columns(pl.lit("Because").alias("reason_label")))


def test_a_row_that_is_not_charted_and_has_no_code_fails_a_check() -> None:
    coded = run(directory=[drow()], metrics=[mrow(chartable=False, not_charted_code="round_size_only")], events=[])
    bare = run(directory=[drow()], metrics=[mrow(chartable=False)], events=[])
    name = "Every unpublished row has a reason"
    assert next(c for c in coded.checks if c["name"] == name)["status"] == "pass"
    failed = next(c for c in bare.checks if c["name"] == name)
    assert failed["status"] == "fail" and "Acme" in failed["detail"]
    assert bare.tables["reported_unconfirmed"]["reason"].to_list() == ["unclassified"]


def test_a_figure_both_files_hold_is_listed_once_and_the_metric_row_is_kept() -> None:
    same = {"value": 3.0e7, "qualifier": "=", "metric_kind": "round_amount", "unit": "USD"}
    metric = mrow("Twin", "r", metric="round_amount", chartable=False, not_charted_code="round_size_only",
                  as_of_date="2026-05-01", source_url="https://example.com/twin", **same)  # fmt: skip
    event = erow("Twin", "round", "2026-05-01", chartable=False, not_charted_code="round_size_only",
                 source_url="https://example.com/twin", buyer_type="none", **same)  # fmt: skip
    other_day = erow("Twin", "round", "2026-06-01", chartable=False, not_charted_code="round_size_only",
                     source_url="https://example.com/twin", buyer_type="none", **same)  # fmt: skip
    no_value = [erow("Nov", "acquisition_announced", d, chartable=False, not_charted_code="no_figure_stated",
                     source_url="https://example.com/nov") for d in ("2026-05-01", "2026-05-01")]  # fmt: skip
    no_value[1]["counterparty"] = "Other buyer"
    out = run(directory=[drow("Twin"), drow("Nov")], metrics=[metric], events=[event, other_day, *no_value])
    rep = out.tables["reported_unconfirmed"]
    assert sorted((r["source"], r["date"]) for r in rep.filter(pl.col("company") == "Twin").iter_rows(named=True)) == [
        ("events", "2026-06-01"), ("private_metrics", "2026-05-01")]  # fmt: skip
    assert rep.filter(pl.col("company") == "Nov").height == 2, "events that state no figure are never merged"
    p = out.summary["private"]
    assert p["n_reported_duplicates"] == 1 and p["n_reported_unconfirmed"] == rep.height == 4
    assert out.summary["events"]["n_unconfirmed"] == 3


def test_reason_counts_follow_the_mart(built: L.Ledger) -> None:
    p, rep = built.summary["private"], built.tables["reported_unconfirmed"]
    assert list(p["n_reported_by_reason"]) == list(L.REASONS)
    assert sum(p["n_reported_by_reason"].values()) == p["n_reported_unconfirmed"] == rep.height
    for reason, n in p["n_reported_by_reason"].items():
        assert n == rep.filter(pl.col("reason") == reason).height


def test_deals_are_split_by_how_they_are_dated_and_by_who_bought() -> None:
    events = [
        erow("A", "acquisition_announced", "2026-03-01", buyer_type="platform_security"),
        erow("A", "acquisition_closed", "2026-04-01", buyer_type="platform_security"),
        erow("B", "acquisition_closed", "2026-05-01", buyer_type="pe", counterparty="Fund"),  # no announcement on file
        erow("C", "acquisition_announced", "2025-01-01", buyer_type="strategic"),
        erow("C", "acquisition_closed", "2026-02-01", buyer_type="strategic"),  # announced before the window
        erow("D", "take_private", "2026-06-01", buyer_type="pe", counterparty="Fund"),
        erow("D", "acquisition_announced", "2026-06-01", buyer_type="pe", counterparty="Fund"),  # same deal twice
        erow("E", "acquisition_announced", "2026-07-01", buyer_type="other", counterparty="Merger"),
    ]  # fmt: skip
    ev = run(directory=[], metrics=[], events=events).summary["events"]
    assert (
        ev["acquisitions_12m"]
        == 4
        == ev["acquisitions_announced_12m"] + ev["acquisitions_first_recorded_at_completion_12m"]
    )
    assert (ev["acquisitions_announced_12m"], ev["acquisitions_first_recorded_at_completion_12m"]) == (3, 1)
    assert ev["acquisitions_12m_by_buyer_type"] == {
        "platform_security": 1, "non_security_software": 0, "pe": 2, "insurance": 0, "state": 0, "strategic": 0,
        "other": 1, "none": 0}  # fmt: skip
    assert sum(ev["acquisitions_12m_by_buyer_type"].values()) == ev["acquisitions_12m"]
    assert ev["acquisitions_closed_12m"] == 3 and ev["acquisitions_closed_12m_announced_earlier"] == 1


def test_filing_priced_deals_are_counted_as_deals_not_events() -> None:
    events = [
        erow("A", "acquisition_announced", "2026-03-01", value=2.0e8, unit="USD", qualifier="=",
             metric_kind="acquisition_price_announced"),
        erow("A", "acquisition_closed", "2026-04-01", sec_value=1.8e8, unit="USD", sec_source_url="https://www.sec.gov/a"),
        erow("B", "acquisition_closed", "2024-04-01", sec_value=1.0e8, unit="USD", sec_source_url="https://www.sec.gov/b"),
    ]  # fmt: skip
    ev = run(directory=[], metrics=[], events=events).summary["events"]
    assert ev["n_sec_priced"] == 2 and ev["n_sec_priced_deals"] == 2 and ev["n_sec_priced_deals_12m"] == 1


def test_the_latest_valuation_carries_the_caveat_that_goes_with_it() -> None:
    out = run(directory=[drow("Top")], metrics=[mrow("Top", caveat="Valuation the company calls secondary funding.")],
              events=[])  # fmt: skip
    top = out.summary["private"]["latest_valuation_max"]
    assert top["company"] == "Top" and top["caveat"] == "Valuation the company calls secondary funding."


def test_the_q6_basis_names_thin_comparable_sets_and_old_arr() -> None:
    mult = {"OKTA": 5.0, "SAIL": 8.8}  # identity has two comparables in the registered table
    metrics = [mrow("Old", layer="identity"), arr("Old", layer="identity", as_of_date="2025-04-23", period="FY2025"),
               mrow("New", layer="identity"), arr("New", layer="identity", as_of_date="2026-09-30")]  # fmt: skip
    out = run(directory=[drow("Old", layer="identity", l2="workforce_iam"), drow("New", layer="identity", l2="workforce_iam")],
              metrics=metrics, events=[], multiples=mult)  # fmt: skip
    q6 = out.summary["private"]["q6"]
    by = {x["company"]: x for x in q6["companies"]}
    assert (q6["thin_below"], q6["old_arr_months"]) == (L.THIN_COMPARABLES, L.OLD_ARR_MONTHS) == (3, 12)
    assert (
        by["Old"]["arr_old"]
        and by["Old"]["arr_months_old"] == 17
        and by["Old"]["thin"]
        and by["Old"]["n_comparables"] == 2
    )
    assert not by["New"]["arr_old"] and by["New"]["arr_months_old"] == 0 and by["New"]["thin"]
    assert (q6["n_old_arr"], q6["n_thin"], q6["thin_layers"]) == (1, 2, ["identity"])
    assert by["Old"]["scored"] is True and by["Old"]["above"] is not None, "the reading itself is unchanged"


def test_acquisitions_kept_off_the_charts_are_counted_apart_from_the_deals() -> None:
    events = [
        erow("Ip", "acquisition_announced", "2026-05-01", chartable=False, not_charted_code="ip_asset_purchase"),
        erow("Far", "acquisition_announced", "2026-06-01", chartable=False, not_charted_code="outside_scope"),
        erow("Old", "acquisition_announced", "2024-06-01", chartable=False, not_charted_code="no_figure_stated"),
        erow("Mix", "acquisition_announced", "2026-05-01", chartable=False, not_charted_code="combined_value"),
        erow("Mix", "acquisition_closed", "2026-06-01"),  # the closing is charted, so Mix is a deal
        erow("Rnd", "round", "2026-05-01", chartable=False, not_charted_code="round_size_only", buyer_type="none"),
    ]  # fmt: skip
    ev = run(directory=[], metrics=[], events=events).summary["events"]
    assert ev["acquisitions_12m"] == 1 and ev["acquisitions_not_charted_12m"] == 1
