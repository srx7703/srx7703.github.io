"""Polars dtypes and pandera checks for the data-center power tables.

Seven tables under ``data/snapshots/power/``. Three come from EIA workbooks, two from PJM's load
forecast, and two are the curated layers that no public file provides.

``generators``   EIA-860M: one row per generating unit per vintage, planned and operating alike.
``retirements``  EIA-860M: retirements, actual and planned, on one timeline.
``deferrals``    two 860M vintages differenced: units whose retirement moved later or disappeared.
``sales``        EIA-861M: monthly retail sales by state and sector, the demand denominator.
``steo``         EIA Short-Term Energy Outlook: the forecast half of that denominator.
``region_load``  PJM: peak and energy per zone per month, plus the large-load adjustment that is
                 almost entirely data centers.
``deals``        curated: what data-center operators actually signed, with a source per row.

The dtypes are deliberately wide where the source is: EIA ships capacities as floats with nulls and
years as floats, and coercing them early would hide a parse failure as a zero.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

GENERATOR_KEY = ["vintage", "plant_id", "generator_id"]
RETIREMENT_KEY = ["vintage", "plant_id", "generator_id", "kind"]
DEFERRAL_KEY = ["plant_id", "generator_id", "from_vintage", "to_vintage"]
SALES_KEY = ["period", "state", "sector"]
STEO_KEY = ["vintage", "series_id", "period"]
REGION_LOAD_KEY = ["iso", "zone", "year", "month", "metric"]
DEAL_KEY = ["deal_id"]

GENERATORS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "vintage": pl.Utf8,          # the 860M edition, "2026-07"
    "sheet": pl.Utf8,            # planned | operating | canceled
    "plant_id": pl.Utf8,
    "generator_id": pl.Utf8,
    "plant_name": pl.Utf8,
    "entity_name": pl.Utf8,
    "state": pl.Utf8,
    "county": pl.Utf8,
    "balancing_authority": pl.Utf8,
    "sector": pl.Utf8,
    "technology": pl.Utf8,
    "family": pl.Utf8,           # TECHNOLOGY_FAMILY[technology]
    "energy_source": pl.Utf8,
    "prime_mover": pl.Utf8,
    "nameplate_mw": pl.Float64,
    "net_summer_mw": pl.Float64,
    "status_code": pl.Utf8,      # the bracketed code only, "U"
    "stage": pl.Utf8,            # STATUS_STAGE[status_code]
    "operation_year": pl.Int64,
    "operation_month": pl.Int64,
}

RETIREMENTS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "vintage": pl.Utf8,
    "kind": pl.Utf8,             # actual | planned
    "plant_id": pl.Utf8,
    "generator_id": pl.Utf8,
    "plant_name": pl.Utf8,
    "state": pl.Utf8,
    "technology": pl.Utf8,
    "family": pl.Utf8,
    "nameplate_mw": pl.Float64,
    "retirement_year": pl.Int64,
    "retirement_month": pl.Int64,
}

DEFERRALS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "plant_id": pl.Utf8,
    "generator_id": pl.Utf8,
    "plant_name": pl.Utf8,
    "state": pl.Utf8,
    "technology": pl.Utf8,
    "family": pl.Utf8,
    "nameplate_mw": pl.Float64,
    "from_vintage": pl.Utf8,
    "to_vintage": pl.Utf8,
    "year_before": pl.Int64,     # retirement year in the older edition
    "year_after": pl.Int64,      # in the newer edition; null means the date was withdrawn entirely
    "deferred_years": pl.Float64,  # null when withdrawn, which is a deferral of unknown length
}

SALES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "period": pl.Utf8,           # "2026-06"
    "state": pl.Utf8,            # two-letter code, or "US" for the national total
    "sector": pl.Utf8,           # residential | commercial | industrial | transportation | total
    "sales_mwh": pl.Float64,
    "revenue_kusd": pl.Float64,
    "customers": pl.Float64,
    "price_cents_kwh": pl.Float64,
    "data_status": pl.Utf8,      # EIA marks months as final or preliminary; a revision is not news
}

STEO_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "vintage": pl.Utf8,          # STEO edition, "2026-09"
    "series_id": pl.Utf8,        # EIA's own id, e.g. ELCCP_US
    "label": pl.Utf8,
    "period": pl.Utf8,           # "2026-06" for monthly rows, "2026" for annual roll-ups
    "frequency": pl.Utf8,        # monthly | annual
    "value": pl.Float64,
    "unit": pl.Utf8,
}

REGION_LOAD_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "iso": pl.Utf8,
    "zone": pl.Utf8,
    "year": pl.Int64,
    "month": pl.Int64,           # 0 for annual rows such as the large-load adjustment
    "metric": pl.Utf8,           # peak_mw | energy_gwh | large_load_adjustment_mw
    "value": pl.Float64,
    "vintage": pl.Utf8,          # the forecast edition, "2026"
}

DEALS_DTYPES: dict[str, pl.DataType] = {
    "deal_id": pl.Utf8,
    "path": pl.Utf8,             # config.PATHS key
    "buyer": pl.Utf8,
    "seller": pl.Utf8,
    "technology": pl.Utf8,
    "mw": pl.Float64,            # null where no capacity was disclosed, never a guess
    "term_years": pl.Float64,
    "state": pl.Utf8,
    "announced_date": pl.Utf8,
    "stage": pl.Utf8,            # the same vocabulary as the generator table
    "company_confirmed": pl.Boolean,
    "source_name": pl.Utf8,
    "source_url": pl.Utf8,
    "publish_date": pl.Utf8,
    "last_checked": pl.Utf8,
    "caveat": pl.Utf8,           # what the number is not; rendered with the row
}

_mw = pa.Column(float, pa.Check.ge(0.0), nullable=True)
# The oldest units in the inventory are 1890s hydro that is still running, so the floor is 1880 and
# not 1900; a tighter bound rejects real rows from the Operating sheet.
_year = pa.Column(int, pa.Check.in_range(1880, 2100), nullable=True)
_date = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"), nullable=True)

GENERATORS_SCHEMA = pa.DataFrameSchema(
    {
        "vintage": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "sheet": pa.Column(str, pa.Check.isin(["planned", "operating", "canceled"])),
        "plant_id": pa.Column(str),
        "generator_id": pa.Column(str),
        "technology": pa.Column(str),
        "family": pa.Column(str),
        "nameplate_mw": _mw,
        "stage": pa.Column(str, nullable=True),
        "operation_year": _year,
    },
    unique=GENERATOR_KEY,
    coerce=False,
)

RETIREMENTS_SCHEMA = pa.DataFrameSchema(
    {
        "vintage": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "kind": pa.Column(str, pa.Check.isin(["actual", "planned"])),
        "nameplate_mw": _mw,
        "retirement_year": _year,
        "family": pa.Column(str),
    },
    unique=RETIREMENT_KEY,
    coerce=False,
)

DEFERRALS_SCHEMA = pa.DataFrameSchema(
    {
        "plant_id": pa.Column(str),
        "generator_id": pa.Column(str),
        "nameplate_mw": _mw,
        "year_before": pa.Column(int, pa.Check.in_range(1880, 2100)),
        "year_after": _year,
        # a deferral is later or withdrawn; a retirement pulled *earlier* is not one and must not be here
        "deferred_years": pa.Column(float, pa.Check.gt(0.0), nullable=True),
    },
    unique=DEFERRAL_KEY,
    coerce=False,
)

SALES_SCHEMA = pa.DataFrameSchema(
    {
        "period": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "state": pa.Column(str, pa.Check.str_length(2, 2)),
        "sector": pa.Column(str, pa.Check.isin(
            ["residential", "commercial", "industrial", "transportation", "total"])),
        "sales_mwh": pa.Column(float, pa.Check.ge(0.0), nullable=True),
    },
    unique=SALES_KEY,
    coerce=False,
)

STEO_SCHEMA = pa.DataFrameSchema(
    {
        "vintage": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "series_id": pa.Column(str),
        "period": pa.Column(str, pa.Check.str_matches(r"^\d{4}(-\d{2})?$")),
        "frequency": pa.Column(str, pa.Check.isin(["monthly", "annual"])),
        "value": pa.Column(float, nullable=True),
    },
    unique=STEO_KEY,
    coerce=False,
)

REGION_LOAD_SCHEMA = pa.DataFrameSchema(
    {
        "iso": pa.Column(str),
        "zone": pa.Column(str),
        "year": pa.Column(int, pa.Check.in_range(2000, 2100)),
        "month": pa.Column(int, pa.Check.in_range(0, 12)),
        "metric": pa.Column(str, pa.Check.isin(["peak_mw", "energy_gwh", "large_load_adjustment_mw"])),
        "value": pa.Column(float, nullable=True),
    },
    unique=REGION_LOAD_KEY,
    coerce=False,
)

DEALS_SCHEMA = pa.DataFrameSchema(
    {
        "deal_id": pa.Column(str),
        "path": pa.Column(str),
        "buyer": pa.Column(str),
        "mw": _mw,
        "stage": pa.Column(str, pa.Check.isin(
            ["announced", "permitted", "under_construction", "commissioning", "in_service"])),
        "source_url": pa.Column(str, pa.Check.str_startswith("http")),
        "publish_date": pa.Column(str, nullable=True),
        "caveat": pa.Column(str),
    },
    unique=DEAL_KEY,
    coerce=False,
)


def _frame(rows: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    cols = list(dtypes)
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=dtypes)


def generators_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, GENERATORS_DTYPES).unique(subset=GENERATOR_KEY, keep="first", maintain_order=True)


def retirements_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, RETIREMENTS_DTYPES).unique(subset=RETIREMENT_KEY, keep="first", maintain_order=True)


def deferrals_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, DEFERRALS_DTYPES).unique(subset=DEFERRAL_KEY, keep="first", maintain_order=True)


def sales_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, SALES_DTYPES).unique(subset=SALES_KEY, keep="first", maintain_order=True)


def steo_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, STEO_DTYPES).unique(subset=STEO_KEY, keep="first", maintain_order=True)


def region_load_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, REGION_LOAD_DTYPES).unique(subset=REGION_LOAD_KEY, keep="first", maintain_order=True)


def deals_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, DEALS_DTYPES).unique(subset=DEAL_KEY, keep="first", maintain_order=True)
