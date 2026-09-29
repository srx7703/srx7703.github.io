"""Pandera schemas for the memory-cycle tables. A failure blocks the write.

``ECOS_SCHEMA``      the normalised 402Y016 snapshot: six series, positive values, one row per series-month.
``TURNS_SCHEMA``     every registered run of the turning-point rule (two products, three bases, three thresholds).
``PHASES_SCHEMA``    the main calendar's phases: alternating kinds, every phase at least ``MIN_PHASE`` months.
``FROZEN_SCHEMA``    the stock-side results exported from the owner's machine. Months, leads, ratios and flags,
                     and nothing else: a close column appearing here is a licence breach, not a schema drift.

The last one is why the schema is strict (``strict=True``): an extra column fails validation instead of riding
along into a public mart.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from pipelines.memcycle.config import CLOSE_COLUMNS, COMPANIES, MIN_PHASE, PRODUCT

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
_month = pa.Column(str, pa.Check.str_matches(MONTH))
_month_null = pa.Column(str, pa.Check.str_matches(MONTH), nullable=True)
_ratio = pa.Column(float, pa.Check.in_range(0.01, 1000.0), nullable=True)
_lead = pa.Column(pl.Int64, pa.Check.in_range(-120, 120), nullable=True)
_flag = pa.Column(bool, nullable=True)
_text = pa.Column(str, nullable=True)

PRODUCTS = sorted(set(PRODUCT.values()))
COMPANY_KEYS = [c.key for c in COMPANIES]

ECOS_SCHEMA = pa.DataFrameSchema(
    {
        "item_code": pa.Column(str, pa.Check.isin(list(PRODUCT))),
        "item_name": pa.Column(str, pa.Check.str_length(min_value=1)),
        "basis": pa.Column(str, pa.Check.isin(["C", "D", "W"])),
        "month": _month,
        "value": pa.Column(float, pa.Check.gt(0.0)),
        "unit": pa.Column(str, pa.Check.eq("2020=100")),
        "weight": pa.Column(float, pa.Check.gt(0.0), nullable=True),
        "retrieved_at": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")),
        "preliminary": pa.Column(bool),
    },
    unique=["item_code", "basis", "month"],
    strict=True,
    coerce=False,
)

TURNS_SCHEMA = pa.DataFrameSchema(
    {
        "product": pa.Column(str, pa.Check.isin(PRODUCTS)),
        "basis": pa.Column(str, pa.Check.isin(["C", "D", "W"])),
        "amp_threshold": pa.Column(float, pa.Check.isin([0.0, 0.2, 0.3])),
        "kind": pa.Column(str, pa.Check.isin(["P", "T"])),
        "month": _month,
        "confirmed": pa.Column(bool),
    },
    unique=["product", "basis", "amp_threshold", "month"],
    strict=True,
    coerce=False,
)

PHASES_SCHEMA = pa.DataFrameSchema(
    {
        "product": pa.Column(str, pa.Check.isin(PRODUCTS)),
        "from": _month,
        "from_kind": pa.Column(str, pa.Check.isin(["P", "T"])),
        "to": _month,
        "to_kind": pa.Column(str, pa.Check.isin(["P", "T"])),
        "months": pa.Column(pl.Int64, pa.Check.ge(MIN_PHASE)),
        "change": pa.Column(float, pa.Check.gt(-1.0)),
        "to_confirmed": pa.Column(bool),
    },
    checks=[pa.Check(lambda d: d.lazyframe.select(pl.col("from_kind") != pl.col("to_kind")), name="alternates")],
    unique=["product", "from"],
    strict=True,
    coerce=False,
)

FROZEN_SCHEMA = pa.DataFrameSchema(
    {
        "product": pa.Column(str, pa.Check.isin(PRODUCTS)),
        "company": pa.Column(str, pa.Check.isin(COMPANY_KEYS)),
        "label": pa.Column(str),
        "group": pa.Column(str, pa.Check.isin(["pure", "diversified", "unregistered"])),
        "dram_maker": pa.Column(bool),
        "market": pa.Column(str, pa.Check.isin(["US", "KR", "TW", "JP", "DE"])),
        "currency": pa.Column(str, pa.Check.isin(["USD", "KRW", "TWD", "JPY", "EUR"])),
        "first_month": _month,
        "last_month": _month,
        "price_peak": _month,
        "price_peak_confirmed": pa.Column(bool),
        "window": pa.Column(str, pa.Check.str_matches(r"^\(\d{4}-\d{2}, \d{4}-\d{2}\]$"), nullable=True),
        "complete": _flag,
        "status": pa.Column(str),
        "stock_peak": _month_null,
        "lead_months": _lead,
        "stock_peak_high_month": _month_null,
        "lead_months_high": _lead,
        "stock_trough": _month_null,
        "trough_lead_months": _lead,
        "drawdown_to_trough": pa.Column(float, pa.Check.in_range(-1.0, 0.0), nullable=True),
        "upleg_low": _month_null,
        "upleg_multiple": _ratio,
        "upleg_start": _month_null,
        "upleg_truncated_by_listing": _flag,
        "upleg_multiple_hl": _ratio,
        "upleg_low_hl_month": _month_null,
        "upleg_multiple_usd": _ratio,
        "upleg_multiple_usd_series": _ratio,
        "delisted_or_out_of_scope": pa.Column(bool),
        "bench1": _text,
        "bench1_multiple": _ratio,
        "excess1": _ratio,
        "bench2": _text,
        "bench2_multiple": _ratio,
        "excess2": _ratio,
        "partial_latest_month": _flag,
    },
    checks=[
        pa.Check(lambda d: d.lazyframe.select(~pl.col("status").eq("ok") | pl.col("lead_months").is_not_null()),
                 name="ok rows carry a lead"),
    ],
    unique=["product", "company", "price_peak"],
    strict=True,
    coerce=False,
)
assert not set(CLOSE_COLUMNS) & set(FROZEN_SCHEMA.columns), "a close column may never be part of the frozen schema"
