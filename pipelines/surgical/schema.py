"""Polars dtypes and pandera checks for the soft-tissue surgical robot tables.

Four tables. One is fetched, three are curated, and the split is the project's defining constraint:

``clearances``  openFDA 510(k) and De Novo records. Machine-readable, no key, refreshed every run.
``units``       installed base, placements, procedures and production, per maker per period. Curated from
                primary filings because no vendor publishes a machine-readable series.
``tenders``     Chinese public-hospital award records: hospital, brand, model, quantity, unit price. Curated,
                because the portal searches titles only and rate-limits aggressively.
``quota``       China's provincial licence ceiling for laparoscopic systems. Curated from one official PDF.

The schema's real job is stopping one specific error. Five companies in this pool publish a number they each call
"units", and they are counting five different things: systems in the field, systems placed this period, systems
manufactured, systems whose title transferred, and operations performed. ``units.basis`` is therefore NOT
nullable and is constrained to a closed vocabulary, and nothing downstream may sum across it. A schema that let
``basis`` be blank would let a chart add Intuitive's installed base to Surgerii's production and call the total a
market.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from pipelines.surgical.config import PLACEMENT_MODEL, TIER_ORDER, UNIT_BASIS

CLEARANCE_KEY = ["registry", "clearance_id"]
UNIT_KEY = ["maker", "product", "metric", "basis", "period"]
TENDER_KEY = ["notice_id"]
QUOTA_KEY = ["province", "plan"]

CLEARANCES_DTYPES: dict[str, pl.DataType] = {
    "snapshot_ts": pl.Utf8,
    "registry": pl.Utf8,          # fda_510k | fda_denovo
    "clearance_id": pl.Utf8,      # K250725, DEN250068
    "applicant": pl.Utf8,
    "device_name": pl.Utf8,
    "product_code": pl.Utf8,
    "decision_date": pl.Utf8,
    "decision": pl.Utf8,
    "regulation_number": pl.Utf8,
    "maker": pl.Utf8,             # resolved to a config.MAKERS ticker where we can, else null
    "in_scope": pl.Boolean,       # soft tissue, per config.SCOPE_*
}

UNITS_DTYPES: dict[str, pl.DataType] = {
    "maker": pl.Utf8,
    "product": pl.Utf8,
    "metric": pl.Utf8,            # installed_base | placements | procedures | production | units_sold | orders
    "basis": pl.Utf8,             # a key of config.UNIT_BASIS — the company's own counting rule
    "period": pl.Utf8,            # 2026-Q2 | 2025 | 2026-06-30
    "value": pl.Float64,
    "unit": pl.Utf8,              # systems | procedures
    "geography": pl.Utf8,         # global | us | cn | ex-cn — Procept's quarterly figures are US only
    "placement_model": pl.Utf8,   # a key of config.PLACEMENT_MODEL
    "tier": pl.Utf8,
    # Whose number this is. A company's own filing and a consultant's estimate OF that company are different
    # kinds of fact and the page must never silently pick one: at 2024-12-31 Intuitive's own 8-K says 9,902
    # da Vinci systems and Frost & Sullivan, in a competitor's IPO prospectus, says 9,629 for the same date.
    "source_kind": pl.Utf8,
    "source_name": pl.Utf8,
    "source_url": pl.Utf8,
    "publish_date": pl.Utf8,
    "last_checked": pl.Utf8,
    "quote": pl.Utf8,
    "caveat": pl.Utf8,
}

TENDERS_DTYPES: dict[str, pl.DataType] = {
    "notice_id": pl.Utf8,
    "hospital": pl.Utf8,
    "province": pl.Utf8,
    "brand": pl.Utf8,
    "model": pl.Utf8,
    "maker": pl.Utf8,
    "quantity": pl.Float64,
    "unit_price_cny": pl.Float64,
    "total_price_cny": pl.Float64,
    "award_date": pl.Utf8,
    "contract_kind": pl.Utf8,     # purchase | lease | maintenance — a lease fee is not a machine price
    # Whether the award buys a complete clinical system. A teaching arm, a single component or a simulator
    # has a price, and it is not the price of a robot; one such row at CNY 1.09m made the panel's spread
    # look three times wider than it is.
    "complete_system": pl.Boolean,
    "source_url": pl.Utf8,
    "last_checked": pl.Utf8,
    "caveat": pl.Utf8,
}

QUOTA_DTYPES: dict[str, pl.DataType] = {
    "province": pl.Utf8,
    "plan": pl.Utf8,              # "14th" — no 15th Five-Year Plan equivalent exists as of 2026-09-21
    "permitted_total": pl.Float64,
    "newly_added": pl.Float64,
    "source_name": pl.Utf8,
    "source_url": pl.Utf8,
    "publish_date": pl.Utf8,
    "expires": pl.Utf8,
    "caveat": pl.Utf8,
}

_date = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"), nullable=True)
_url = pa.Column(str, pa.Check.str_startswith("http"))

CLEARANCES_SCHEMA = pa.DataFrameSchema(
    {
        "registry": pa.Column(str, pa.Check.isin(["fda_510k", "fda_denovo"])),
        "clearance_id": pa.Column(str),
        "applicant": pa.Column(str, nullable=True),
        "product_code": pa.Column(str, nullable=True),
        "decision_date": _date,
        "in_scope": pa.Column(bool),
    },
    unique=CLEARANCE_KEY,
    coerce=False,
)

UNITS_SCHEMA = pa.DataFrameSchema(
    {
        "maker": pa.Column(str),
        "metric": pa.Column(str, pa.Check.isin(list(UNIT_BASIS))),
        # Not nullable, deliberately. A unit figure whose counting rule is unknown cannot be compared with
        # anything, and a blank here is how a chart ends up adding production to installed base.
        "basis": pa.Column(str, pa.Check.isin(list(UNIT_BASIS))),
        "period": pa.Column(str, pa.Check.str_matches(r"^\d{4}(-(Q[1-4]|\d{2}(-\d{2})?))?$")),
        "value": pa.Column(float, pa.Check.ge(0.0)),
        "geography": pa.Column(str, pa.Check.isin(["global", "us", "cn", "ex-cn", "eu", "jp", "kr"])),
        "placement_model": pa.Column(str, pa.Check.isin(list(PLACEMENT_MODEL)), nullable=True),
        "tier": pa.Column(str, pa.Check.isin(list(TIER_ORDER))),
        "source_kind": pa.Column(str, pa.Check.isin(["company", "third_party"])),
        "source_url": _url,
        "caveat": pa.Column(str, pa.Check.str_length(1)),
    },
    unique=UNIT_KEY,
    coerce=False,
)

TENDERS_SCHEMA = pa.DataFrameSchema(
    {
        "notice_id": pa.Column(str),
        "hospital": pa.Column(str),
        "quantity": pa.Column(float, pa.Check.gt(0.0), nullable=True),
        "unit_price_cny": pa.Column(float, pa.Check.gt(0.0), nullable=True),
        # A leasing award names the leasing company as winner, leaves the brand blank and prints a multi-year
        # lease fee in the unit-price column. Recording the kind is what stops that fee entering an ASP series.
        "contract_kind": pa.Column(str, pa.Check.isin(["purchase", "lease", "maintenance"])),
        "complete_system": pa.Column(bool),
        "award_date": _date,
        "source_url": _url,
        "caveat": pa.Column(str, pa.Check.str_length(1)),
    },
    unique=TENDER_KEY,
    coerce=False,
)

QUOTA_SCHEMA = pa.DataFrameSchema(
    {
        "province": pa.Column(str),
        "plan": pa.Column(str),
        "permitted_total": pa.Column(float, pa.Check.ge(0.0)),
        "newly_added": pa.Column(float, pa.Check.ge(0.0)),
        "source_url": _url,
        "caveat": pa.Column(str, pa.Check.str_length(1)),
    },
    unique=QUOTA_KEY,
    coerce=False,
)


def _frame(rows: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    cols = list(dtypes)
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=dtypes)


def clearances_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, CLEARANCES_DTYPES).unique(subset=CLEARANCE_KEY, keep="first", maintain_order=True)


def units_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, UNITS_DTYPES).unique(subset=UNIT_KEY, keep="first", maintain_order=True)


def tenders_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, TENDERS_DTYPES).unique(subset=TENDER_KEY, keep="first", maintain_order=True)


def quota_frame(rows: list[dict]) -> pl.DataFrame:
    return _frame(rows, QUOTA_DTYPES).unique(subset=QUOTA_KEY, keep="first", maintain_order=True)
