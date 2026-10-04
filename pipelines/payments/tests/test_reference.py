"""The curated payments files and the rules that guard them."""

from __future__ import annotations

import pytest

from pipelines.common.curation import row_problems
from pipelines.payments import reference
from pipelines.payments.reference import KINDS, SPEC


def _metric_row(**over) -> dict:
    row = {
        "company": "Affirm", "metric": "GMV", "period": "FY2026", "value": 1.0e9, "unit": "USD",
        "qualifier": "=", "metric_kind": "filed_volume", "definition": "Gross merchandise volume",
        "tag": "V", "chartable": True, "company_confirmed": True,
        "as_of_date": "2026-06-30", "last_checked": "2026-10-03",
        "source_name": "Affirm 10-K", "source_url": "https://investors.affirm.com/", "publish_date": "2026-08-28",
        "caveat": "Fiscal year ends 30 June.",
    }
    row.update(over)
    return row


def test_clean_row_passes():
    assert row_problems(SPEC, "kpi_disclosures_other", _metric_row()) == []


@pytest.mark.parametrize("tag", ["S", "U"])
def test_unverified_row_cannot_be_chartable(tag):
    problems = row_problems(SPEC, "kpi_disclosures_other", _metric_row(tag=tag))
    assert any("only V or C" in p for p in problems)


def test_reported_talks_never_charted():
    problems = row_problems(SPEC, "private_metrics",
                            _metric_row(metric_kind="reported_talks", status="current"))
    assert any("never charted" in p for p in problems)


def test_qualifier_is_closed():
    problems = row_problems(SPEC, "kpi_disclosures_other", _metric_row(qualifier="about"))
    assert any("qualifier" in p for p in problems)


def test_metric_kind_is_closed():
    problems = row_problems(SPEC, "kpi_disclosures_other", _metric_row(metric_kind="guess"))
    assert any("metric_kind" in p for p in problems)


@pytest.mark.parametrize("kind", KINDS)
def test_committed_files_validate(kind):
    if not SPEC.path(kind).exists():
        pytest.skip(f"{kind} not curated yet")
    reference.rows(kind)
