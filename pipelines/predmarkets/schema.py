"""Polars dtypes and pandera checks for the snapshot tables.

`flatten_markets()` produces wide rows; they are split into a narrow numeric *quotes* table
(written every run) and a string *dimension* table (written as deltas only when rows are new
or changed), which keeps the twice-daily git commits small.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

KEY = ["platform", "market_id"]

QUOTES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "event_id": pl.Utf8,
    "yes_price": pl.Float64,
    "best_bid": pl.Float64,
    "best_ask": pl.Float64,
    "mid": pl.Float64,
    "last_trade": pl.Float64,
    "volume": pl.Float64,
    "volume_24h": pl.Float64,
    "liquidity": pl.Float64,
    "open_interest": pl.Float64,
    "closed": pl.Boolean,
    "active": pl.Boolean,
}

DIM_DTYPES: dict[str, pl.DataType] = {
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "event_id": pl.Utf8,
    "event_slug": pl.Utf8,
    "event_title": pl.Utf8,
    "series": pl.Utf8,
    "market_slug": pl.Utf8,
    "question": pl.Utf8,
    "outcome_yes": pl.Utf8,
    "condition_id": pl.Utf8,
    "yes_token": pl.Utf8,
    "no_token": pl.Utf8,
    "end_date": pl.Utf8,
    "tags": pl.Utf8,
}

BOOKS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "platform": pl.Utf8,
    "market_id": pl.Utf8,
    "token_id": pl.Utf8,
    "best_bid": pl.Float64,
    "best_ask": pl.Float64,
    "spread": pl.Float64,
    "mid": pl.Float64,
    "n_bid_levels": pl.Int64,
    "n_ask_levels": pl.Int64,
    "book_ts": pl.Utf8,
    "bid_depth_5c": pl.Float64,
    "ask_depth_5c": pl.Float64,
    "bid_depth_5c_usd": pl.Float64,
    "ask_depth_5c_usd": pl.Float64,
    "bid_depth_10c": pl.Float64,
    "ask_depth_10c": pl.Float64,
    "bid_depth_10c_usd": pl.Float64,
    "ask_depth_10c_usd": pl.Float64,
}

_prob = pa.Column(float, pa.Check.in_range(0.0, 1.0), nullable=True)
_nonneg = pa.Column(float, pa.Check.ge(0.0), nullable=True)

QUOTES_SCHEMA = pa.DataFrameSchema(
    {
        "snapshot_ts": pa.Column(str),
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "event_id": pa.Column(str),
        "yes_price": _prob,
        "best_bid": _prob,
        "best_ask": _prob,
        "mid": _prob,
        "volume": _nonneg,
        "volume_24h": _nonneg,
        "liquidity": _nonneg,
        "open_interest": _nonneg,
    },
    unique=KEY,
    coerce=False,
)

DIM_SCHEMA = pa.DataFrameSchema(
    {
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "event_id": pa.Column(str),
        "question": pa.Column(str, nullable=True),
    },
    unique=KEY,
    coerce=False,
)

BOOKS_SCHEMA = pa.DataFrameSchema(
    {
        "platform": pa.Column(str, pa.Check.isin(["polymarket", "kalshi"])),
        "market_id": pa.Column(str),
        "best_bid": _prob,
        "best_ask": _prob,
        "spread": pa.Column(float, pa.Check.in_range(-1.0, 1.0), nullable=True),
        "bid_depth_5c": _nonneg,
        "ask_depth_5c": _nonneg,
    },
    unique=KEY,
    coerce=False,
)


def _frame(rows: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    cols = list(dtypes)
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=dtypes)


def quotes_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, QUOTES_DTYPES)


def dim_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, DIM_DTYPES).unique(subset=KEY, keep="first", maintain_order=True)


def books_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, BOOKS_DTYPES)
