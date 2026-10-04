"""schema.py: every mart has a contract, and the contracts reject what the page must never show."""
import pandera.errors as pe
import polars as pl
import pytest

from pipelines.payments import schema


def _product(**kw):
    row = {"company": "Stripe", "segment": "A", "product_line": "bnpl", "product_status": "live",
           "launch_date": "2023-05", "exit_date": None, "as_of": "2026-10-03", "last_checked": "2026-10-03",
           "stale": False, "tag": "V", "source_name": "s", "source_url": "https://example.org", "caveat": None}
    return pl.DataFrame([row | kw], schema=schema.PRODUCTS_DTYPES)


def test_registry_covers_publish_marts():
    assert set(schema.CHARTED_MARTS) <= set(schema.MARTS)
    with pytest.raises(KeyError):
        schema.validate("nope", pl.DataFrame())


def test_products_reject_unverified_tag():
    schema.validate("products", _product())
    with pytest.raises(pe.SchemaError):
        schema.validate("products", _product(tag="S"))


def test_events_reject_never_charted_kind():
    row = {"event_date": "2026-01-01", "company": "X", "segment": "A", "event_kind": "acquisition",
           "counterparty": "Y", "target": None, "value": 1e9, "unit": "USD", "qualifier": "~",
           "metric_kind": "reported_talks", "tag": "V", "stale": False, "source_name": "s",
           "source_url": "https://example.org", "caveat": None}
    with pytest.raises(pe.SchemaError):
        schema.validate("events", pl.DataFrame([row], schema=schema.EVENTS_DTYPES))
    ok = row | {"metric_kind": "acquisition_price"}
    schema.validate("events", pl.DataFrame([ok], schema=schema.EVENTS_DTYPES))
    with pytest.raises(pe.SchemaError):  # a value without a qualifier
        schema.validate("events", pl.DataFrame([ok | {"qualifier": None}], schema=schema.EVENTS_DTYPES))


def test_kpis_must_be_exactly_three():
    row = {"id": "a", "label": "l", "value": 1.0, "qualifier": "=", "unit": "u", "period": "p",
           "source_url": "https://example.org", "as_of": "2026-01-01", "note": "n"}
    rows = [row | {"id": str(i)} for i in range(3)]
    schema.validate("kpis", pl.DataFrame(rows, schema=schema.KPI_DTYPES))
    with pytest.raises(pe.SchemaError):
        schema.validate("kpis", pl.DataFrame(rows[:2], schema=schema.KPI_DTYPES))
    with pytest.raises(pe.SchemaError):
        schema.validate("kpis", pl.DataFrame([rows[0] | {"qualifier": "about"}] + rows[1:], schema=schema.KPI_DTYPES))


def test_scoreboard_rejects_unregistered_status():
    row = {"id": "Q1", "title": "t", "status": "pending", "reason": "r", "graded": "g", "next_grading": None,
           "already_seen": "a", "inputs": "i", "ci_only": False, "inputs_present": True}
    with pytest.raises(pe.SchemaError):
        schema.validate("scoreboard", pl.DataFrame([row], schema=schema.SCORE_DTYPES))
    schema.validate("scoreboard", pl.DataFrame([row | {"status": "collecting"}], schema=schema.SCORE_DTYPES))
