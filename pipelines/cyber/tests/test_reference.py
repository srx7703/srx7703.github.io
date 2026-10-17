"""The curated-layer rules: each one rejects the way that kind of private-company table can lie."""

from __future__ import annotations

import pytest

from pipelines.common.curation import row_problems
from pipelines.cyber import reference as R


def metric(**kw) -> dict:
    base = {
        "id": "cato:valuation:2025-06", "company": "Cato Networks", "metric": "post_money_valuation", "period": "2025-06-30",
        "value": 4.8e9, "unit": "USD", "qualifier": ">", "metric_kind": "primary_round_valuation", "basis": "disclosed",
        "status": "current", "definition": "Valuation after the Series G stated in the company's release.",
        "source_name": "Cato Networks newsroom", "source_url": "https://www.catonetworks.com/news/cato-raises-359-million/",
        "publish_date": "2025-06-30", "as_of_date": "2025-06-30", "last_checked": "2026-10-17", "tag": "V",
        "chartable": True, "company_confirmed": True, "caveat": "Post-money valuation of the preferred round, as the company states it.",
        "layer": "network_edge", "market": "overseas", "vintage_year": 2025, "stale_mark": False,
    }  # fmt: skip
    return {**base, **kw}


def problems(row: dict, kind: str = "private_metrics") -> list[str]:
    return row_problems(R.SPEC, kind, row)


def test_a_clean_row_passes() -> None:
    assert problems(metric()) == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"source_url": "https://pitchbook.com/profiles/company/123"}, "paid or restricted"),
        ({"source_url": "https://www.crunchbase.com/organization/cato"}, "paid or restricted"),
        ({"source_url": "https://www.bloomberg.com/news/articles/x"}, "paid or restricted"),
        ({"tag": "S"}, "only V or C"),
        ({"metric_kind": "secondary_mark"}, "never charted"),
        ({"metric_kind": "reported_talks"}, "never charted"),
        ({"basis": "undisclosed"}, "undisclosed valuation"),
        ({"basis": "reported"}, "does not say who reported it"),
        ({"qualifier": "about"}, "qualifier"),
        ({"value": 0}, "not positive"),
        ({"value": "4.8bn"}, "not a number"),
        ({"layer": "cloud"}, "twelve layers"),
        ({"market": "mars"}, "market"),
        ({"vintage_year": None}, "not a year"),
        ({"stale_mark": "no"}, "stale_mark"),
        ({"company_confirmed": "yes"}, "company_confirmed"),
        ({"caveat": ""}, "caveat"),
        ({"curator_notes": "", "definition": "todo: check before publishing"}, "working note"),
    ],
)
def test_each_way_the_table_can_lie_is_rejected(change: dict, fragment: str) -> None:
    got = problems(metric(**change))
    assert any(fragment in p for p in got), got


def test_a_reported_valuation_must_say_who_reported_it_and_may_not_be_charted_as_undisclosed() -> None:
    ok = metric(
        basis="reported",
        tag="S",
        chartable=False,
        caveat="Reported by Reuters on 2026-09-30; the company has not confirmed it.",
    )
    assert problems(ok) == []


def test_an_sec_price_must_link_to_the_sec() -> None:
    row = metric(metric_kind="acquisition_price_sec", vintage_year=None, stale_mark=None)
    assert any("SEC filing" in p for p in problems(row))
    assert not any(
        "SEC filing" in p for p in problems({**row, "source_url": "https://www.sec.gov/Archives/edgar/data/1/x.htm"})
    )


def test_a_china_round_amount_needs_a_qualifier_unless_disclosed_exactly() -> None:
    row = metric(
        market="china",
        metric_kind="round_amount",
        qualifier="=",
        basis="reported",
        caveat="Reported by Pedaily; hundreds of millions of yuan.",
    )
    assert any("~ or >" in p for p in problems(row))
    assert not any("~ or >" in p for p in problems({**row, "qualifier": "~"}))


def test_directory_and_event_rows() -> None:
    directory = {
        "company": "Cato Networks", "layer": "network_edge", "market": "overseas", "status": "independent", "country": "IL",
        "source_name": "Company site", "source_url": "https://www.catonetworks.com/about/", "publish_date": "2026-01-10",
        "caveat": "Describes the company in its own words.", "tag": "V", "as_of_date": "2026-10-17", "last_checked": "2026-10-17",
        "chartable": False,
    }  # fmt: skip
    assert problems(directory, "private_directory") == []
    assert any("status" in p for p in problems({**directory, "status": "unicorn"}, "private_directory"))
    event = {
        "company": "Wiz", "event_kind": "acquisition_closed", "event_date": "2026-03-11", "buyer_type": "platform_security",
        "layer": "cloud_appsec", "market": "overseas", "source_name": "Google", "source_url": "https://blog.google/x",
        "publish_date": "2026-03-11", "caveat": "Closing announced by the acquirer.", "tag": "V", "as_of_date": "2026-03-11",
        "last_checked": "2026-10-17", "chartable": True, "company_confirmed": True,
    }  # fmt: skip
    assert problems(event, "events") == []
    assert any("buyer_type" in p for p in problems({**event, "buyer_type": None}, "events"))
    assert any("event_kind" in p for p in problems({**event, "event_kind": "rumour"}, "events"))


def test_the_committed_files_validate() -> None:
    assert R.main([]) == 0
