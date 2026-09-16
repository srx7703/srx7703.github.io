import pandera.errors
import pytest

from pipelines.predmarkets import kalshi as kx
from pipelines.predmarkets import polymarket as pm
from pipelines.predmarkets.schema import (
    BOOKS_SCHEMA,
    DIM_SCHEMA,
    QUOTES_SCHEMA,
    books_frame,
    dim_frame,
    quotes_frame,
)
from pipelines.predmarkets.tests.test_flatten import KX_EVENT, PM_EVENT, TS


def test_quotes_and_dim_frames_split_wide_rows():
    rows = pm.flatten_markets([PM_EVENT], TS) + kx.flatten_markets([KX_EVENT], TS)
    q = quotes_frame(rows)
    d = dim_frame(rows)
    QUOTES_SCHEMA.validate(q)
    DIM_SCHEMA.validate(d)
    assert q.height == 2 and d.height == 2
    assert "question" not in q.columns and "yes_price" not in d.columns
    assert d.filter(d["platform"] == "kalshi")["series"][0] == "CONTROLH"


def test_quotes_schema_rejects_out_of_range_price():
    rows = pm.flatten_markets([PM_EVENT], TS)
    rows[0]["yes_price"] = 1.5
    with pytest.raises(pandera.errors.SchemaError):
        QUOTES_SCHEMA.validate(quotes_frame(rows))


def test_quotes_schema_rejects_duplicate_market():
    rows = pm.flatten_markets([PM_EVENT, PM_EVENT], TS)
    with pytest.raises(pandera.errors.SchemaError):
        QUOTES_SCHEMA.validate(quotes_frame(rows))


def test_dim_frame_dedupes_duplicate_market():
    rows = pm.flatten_markets([PM_EVENT, PM_EVENT], TS)
    assert dim_frame(rows).height == 1


def test_books_schema_accepts_summaries():
    b1 = pm.summarize_book(
        {"bids": [{"price": "0.5", "size": "1"}], "asks": [{"price": "0.6", "size": "1"}]},
        market_id="a",
        token_id="t",
        snapshot_ts=TS,
    )
    b2 = kx.summarize_orderbook(
        {"orderbook_fp": {"yes_dollars": [["0.5", "1"]], "no_dollars": [["0.4", "1"]]}},
        market_id="b",
        snapshot_ts=TS,
    )
    BOOKS_SCHEMA.validate(books_frame([b1, b2]))
