"""Polars dtypes and pandera checks for the valuation snapshot tables.

Six tables, each written once per run under ``data/snapshots/valuation/``:

``prices``       one row per listing: last close, market cap, share count.
``estimates``    the current consensus, one row per (ticker, source, period_end).
``vintages``     the consensus as it stood on earlier dates. Two origins, and they do NOT mean the
                 same thing. ``yahoo_trend`` is a true revision series: Yahoo restates the mean over a
                 fixed analyst panel at 7/30/60/90 days ago, so a move is a revision.
                 ``eastmoney_rebuilt`` is a coverage series: East Money publishes each broker's report
                 once, so rebuilding the mean "as of" an earlier date means averaging only the brokers
                 who had published by then. That mean moves when a new broker starts covering the
                 stock as well as when someone changes their mind, and early points rest on two or
                 three reports. It answers "what was the published consensus then", not "who revised".
                 ``snapshot`` is our own weekly capture, which is a true revision series from the
                 second snapshot onwards. Only ``yahoo_trend`` and ``snapshot`` may be used to measure
                 revisions; ``n_analysts`` is the panel size behind that point, which for
                 ``eastmoney_rebuilt`` is the growing broker count and is the reason the series moves.
``brokers``      East Money's per-broker forecast detail, the evidence behind the A-share mean.
``fundamentals`` quarterly net income and revenue, already differenced out of cumulative filings.
``fx``           daily FRED rates, needed because a listing's price currency and its reporting
                 currency differ (CATL's H line trades in HKD and reports in CNY).
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

PRICE_KEY = ["ticker"]
ESTIMATE_KEY = ["ticker", "source", "period_end"]
VINTAGE_KEY = ["ticker", "source", "period_end", "as_of"]
BROKER_KEY = ["ticker", "org", "publish_date", "year"]
FUNDAMENTAL_KEY = ["ticker", "period_end"]
FX_KEY = ["date", "currency"]

PRICES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "price": pl.Float64,
    "currency": pl.Utf8,  # the currency the price is quoted in (GBp for London pence)
    "financial_currency": pl.Utf8,  # the currency the company reports in
    "market_cap": pl.Float64,
    "shares_outstanding": pl.Float64,
    "price_ts": pl.Utf8,
}

ESTIMATES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "source": pl.Utf8,  # "yahoo" | "eastmoney"
    "period_end": pl.Utf8,  # fiscal period end, YYYY-MM-DD
    "fy_label": pl.Utf8,  # e.g. "FY2027"
    "mark": pl.Utf8,  # "A" actual | "E" estimate
    "eps_avg": pl.Float64,
    "eps_low": pl.Float64,
    "eps_high": pl.Float64,
    "n_analysts": pl.Int64,
    "currency": pl.Utf8,  # currency the EPS is stated in
    "net_profit_avg": pl.Float64,  # East Money also forecasts attributable net profit
}

VINTAGES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "source": pl.Utf8,
    "period_end": pl.Utf8,
    "as_of": pl.Utf8,  # the date this mean was true
    "eps_avg": pl.Float64,
    "n_analysts": pl.Int64,
    "currency": pl.Utf8,
    "origin": pl.Utf8,  # "yahoo_trend" | "eastmoney_rebuilt" | "snapshot"
}

BROKERS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "org": pl.Utf8,
    "researcher": pl.Utf8,
    "publish_date": pl.Utf8,
    "year": pl.Int64,
    "mark": pl.Utf8,
    "eps": pl.Float64,
    "net_profit": pl.Float64,
    "rating": pl.Utf8,
}

FUNDAMENTALS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "ticker": pl.Utf8,
    "period_end": pl.Utf8,
    "net_income": pl.Float64,  # attributable to the parent, quarterly (not cumulative)
    "revenue": pl.Float64,
    "currency": pl.Utf8,
    "source": pl.Utf8,  # "eastmoney" | "sec" | "yahoo"
    "derived": pl.Boolean,  # true when the quarter came from differencing cumulative filings
}

FX_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "date": pl.Utf8,
    "currency": pl.Utf8,
    "per_usd": pl.Float64,  # units of `currency` per 1 USD
    "series": pl.Utf8,
}

_pos = pa.Column(float, pa.Check.gt(0.0), nullable=True)
_any_float = pa.Column(float, nullable=True)

PRICES_SCHEMA = pa.DataFrameSchema(
    {
        "snapshot_ts": pa.Column(str),
        "ticker": pa.Column(str),
        "price": _pos,
        "currency": pa.Column(str, nullable=True),
        "market_cap": _pos,
        "shares_outstanding": _pos,
    },
    unique=PRICE_KEY,
    coerce=False,
)

ESTIMATES_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "source": pa.Column(str, pa.Check.isin(["yahoo", "eastmoney"])),
        "period_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "mark": pa.Column(str, pa.Check.isin(["A", "E"])),
        "eps_avg": _any_float,
        "n_analysts": pa.Column(int, pa.Check.ge(0), nullable=True),
    },
    unique=ESTIMATE_KEY,
    coerce=False,
)

VINTAGES_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "source": pa.Column(str, pa.Check.isin(["yahoo", "eastmoney"])),
        "period_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "as_of": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "eps_avg": _any_float,
        "origin": pa.Column(str, pa.Check.isin(["yahoo_trend", "eastmoney_rebuilt", "snapshot"])),
    },
    unique=VINTAGE_KEY,
    coerce=False,
)

BROKERS_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "org": pa.Column(str),
        "publish_date": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "year": pa.Column(int, pa.Check.in_range(2015, 2035)),
        "mark": pa.Column(str, pa.Check.isin(["A", "E"])),
        "eps": _any_float,
    },
    unique=BROKER_KEY,
    coerce=False,
)

FUNDAMENTALS_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "period_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "net_income": _any_float,
        "revenue": pa.Column(float, pa.Check.ge(0.0), nullable=True),
        "currency": pa.Column(str, nullable=True),
        "source": pa.Column(str, pa.Check.isin(["eastmoney", "sec", "yahoo"])),
    },
    unique=FUNDAMENTAL_KEY,
    coerce=False,
)

FX_SCHEMA = pa.DataFrameSchema(
    {
        "date": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "currency": pa.Column(str),
        "per_usd": pa.Column(float, pa.Check.gt(0.0)),
    },
    unique=FX_KEY,
    coerce=False,
)


def _frame(rows: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    cols = list(dtypes)
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=dtypes)


def prices_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, PRICES_DTYPES).unique(subset=PRICE_KEY, keep="first", maintain_order=True)


def estimates_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, ESTIMATES_DTYPES).unique(subset=ESTIMATE_KEY, keep="first", maintain_order=True)


def vintages_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, VINTAGES_DTYPES).unique(subset=VINTAGE_KEY, keep="first", maintain_order=True)


def brokers_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, BROKERS_DTYPES).unique(subset=BROKER_KEY, keep="first", maintain_order=True)


def fundamentals_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, FUNDAMENTALS_DTYPES).unique(subset=FUNDAMENTAL_KEY, keep="first", maintain_order=True)


def fx_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, FX_DTYPES).unique(subset=FX_KEY, keep="first", maintain_order=True)
