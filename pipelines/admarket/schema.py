"""Pandera schemas for the advertising tables. A failure blocks the write.

``QUARTERLY``     every derived quarter of every series: positive revenue, unless the row is flagged as a known
                  derivation artefact (and then it is not published as a value).
``VIDEO_SHARE``   the nine-company subset by quarter: shares in (0, 1) that sum to one per quarter.
``VOLUME_PRICE``  the volume x price rows: whole-percent changes in a plausible range, gaps in points.
``FORECASTS``     the registered intervals and their status.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from pipelines.admarket import config as cfg

QUARTER = r"^20\d{2}Q[1-4]$"
_q = pa.Column(str, pa.Check.str_matches(QUARTER))

QUARTERLY = pa.DataFrameSchema(
    {
        "series": pa.Column(str, pa.Check.isin(list(cfg.SERIES))),
        "label": pa.Column(str),
        "ticker": pa.Column(str, pa.Check.isin(list(cfg.CIKS))),
        "quarter": _q,
        "value_usd_m": pa.Column(float, nullable=True),
        "method": pa.Column(str, pa.Check.isin(["3M", "FY-9M", "FY-3Q"])),
        "flag": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(lambda d: d.lazyframe.select(pl.col("flag").is_not_null() | (pl.col("value_usd_m") > 0)),
                 name="positive unless flagged"),
        pa.Check(lambda d: d.lazyframe.select(pl.col("flag").is_null() | pl.col("value_usd_m").is_null()),
                 name="flagged rows carry no value"),
    ],
    unique=["series", "quarter"],
    strict=True,
)

VIDEO_SHARE = pa.DataFrameSchema(
    {
        "quarter": _q,
        "company": pa.Column(str, pa.Check.isin(list(cfg.VIDEO_SUBSET))),
        "value_bn": pa.Column(float, pa.Check.gt(0.0)),
        "share": pa.Column(float, pa.Check.in_range(0.0, 1.0, include_min=False, include_max=False)),
    },
    checks=[
        pa.Check(lambda d: d.lazyframe.group_by("quarter").agg((pl.col("share").sum() - 1).abs() < 1e-9)
                 .select("share"), name="shares sum to one"),
    ],
    unique=["quarter", "company"],
    strict=True,
)

VOLUME_PRICE = pa.DataFrameSchema(
    {
        "series": pa.Column(str, pa.Check.isin(list(cfg.Q4_SERIES))),
        "label": pa.Column(str),
        "quarter": _q,
        "volume": pa.Column(pl.Int64, pa.Check.in_range(-100, 300)),
        "price": pa.Column(pl.Int64, pa.Check.in_range(-100, 300)),
        "reported": pa.Column(float),
        "implied": pa.Column(float),
        "gap_pp": pa.Column(float, pa.Check.in_range(-20.0, 20.0)),
    },
    unique=["series", "quarter"],
    strict=True,
)

FORECASTS = pa.DataFrameSchema(
    {
        "series": pa.Column(str, pa.Check.isin(list(cfg.FORECAST_SERIES))),
        "quarter": pa.Column(str, pa.Check.isin(list(cfg.FORECAST_TARGETS))),
        "point_m": pa.Column(pl.Int64, pa.Check.gt(0)),
        "low_m": pa.Column(pl.Int64, pa.Check.gt(0)),
        "high_m": pa.Column(pl.Int64, pa.Check.gt(0)),
        "actual_m": pa.Column(float, nullable=True),
        "status": pa.Column(str, pa.Check.isin(["pending", "hit", "miss"])),
        "filed": pa.Column(str, nullable=True),
        "error": pa.Column(float, nullable=True),
    },
    checks=[
        pa.Check(lambda d: d.lazyframe.select((pl.col("low_m") <= pl.col("point_m"))
                                              & (pl.col("point_m") <= pl.col("high_m"))),
                 name="low <= point <= high"),
        pa.Check(lambda d: d.lazyframe.select((pl.col("status") == "pending") == pl.col("actual_m").is_null()),
                 name="pending exactly when no actual"),
    ],
    unique=["series", "quarter"],
    strict=True,
)
