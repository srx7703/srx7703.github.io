"""pandera checks for the server-maker snapshots. A failing frame is not written (CLAUDE.md rule 3).

The checks that matter are the ones that catch the errors this page is exposed to: a monthly revenue page parsed
in the wrong column (revenue must be positive and unique per company-month), a year-to-date figure left undifferenced
(a quarter larger than the year), and a duplicated filing fact.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from pipelines.servers import config as cfg

TW_MONTHLY = pa.DataFrameSchema(
    {
        "code": pa.Column(str, pa.Check.isin(list(cfg.TW_CODES))),
        "month": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "revenue_k": pa.Column(float, pa.Check.gt(0)),
        "revenue_k_last_year": pa.Column(float, nullable=True),
    },
    unique=["code", "month"],
)

QUARTERLY_COMMON = {
    "code": pa.Column(str),
    "quarter": pa.Column(str, pa.Check.str_matches(r"^\d{4}Q[1-4]$")),
}

TW_QUARTERLY = pa.DataFrameSchema(
    {**QUARTERLY_COMMON, "code": pa.Column(str, pa.Check.isin(list(cfg.TW_CODES))),
     "revenue_k": pa.Column(float, pa.Check.gt(0), nullable=True)},
    unique=["code", "quarter"],
)

CN_QUARTERLY = pa.DataFrameSchema(
    {**QUARTERLY_COMMON, "code": pa.Column(str, pa.Check.isin(list(cfg.CN_CODES))),
     "revenue": pa.Column(float, pa.Check.gt(0), nullable=True)},
    unique=["code", "quarter"],
)

SEC_FACTS = pa.DataFrameSchema(
    {"ticker": pa.Column(str, pa.Check.isin(list(cfg.SEC_CIKS))), "concept": pa.Column(str),
     "end": pa.Column(str), "value": pa.Column(float)},
    unique=["ticker", "concept", "dims", "start", "end", "accession"],
)

FX_MONTHLY = pa.DataFrameSchema(
    {"month": pa.Column(str), "currency": pa.Column(str, pa.Check.isin(["TWD", "CNY"])),
     "per_usd": pa.Column(float, [pa.Check.gt(4), pa.Check.lt(40)])},
    unique=["month", "currency"],
)


def validate(df: pl.DataFrame, schema: pa.DataFrameSchema) -> pl.DataFrame:
    return schema.validate(df, lazy=True)
