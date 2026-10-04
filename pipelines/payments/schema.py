"""Pandera contracts for every table ``publish.py`` writes under ``data/marts/payments/`` and for facts.

Tables built by the W2 modules keep the schema that module owns (re-used here, never copied, so one contract
per table): ``economics.LINE_SCHEMA``/``SENS_SCHEMA``, the ``ledger`` schemas and ``share.LONG_SCHEMA``/
``HHI_SCHEMA``. The tables publish assembles itself (the curated product matrix and event timeline, the metric
dictionary, the "reported, unconfirmed" table, the Q1-Q9 scoreboard, the three headline KPIs and the small
views of the CI-only sources) are defined below.

:data:`MARTS` maps every mart name to its schema; :func:`validate` is the single gate publish calls before a
write, so a schema failure blocks the write (CLAUDE.md rule 3).
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from pipelines.payments import config, economics, ledger, reference, share

QUALIFIERS = list(reference.QUALIFIERS)
CHARTABLE_TAGS = list(reference.CHARTABLE_TAGS)

_DATE = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"))
_DATE_NULL = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"), nullable=True)
_URL = pa.Column(str, pa.Check.str_startswith("https://"))
_Q = pa.Column(str, pa.Check.isin(QUALIFIERS))
_Q_NULL = pa.Column(str, pa.Check.isin(QUALIFIERS), nullable=True)
_SEG = pa.Column(str, pa.Check.isin(list(reference.SEGMENTS)), nullable=True)


def _value_iff_qualifier(d: pa.PolarsData) -> pl.LazyFrame:
    return d.lazyframe.select(pl.col("value").is_null() == pl.col("qualifier").is_null())


# --- curated tables (chartable rows only) ---------------------------------------------------------

PRODUCTS_KEY = ["company", "product_line"]
PRODUCTS_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "segment": pl.Utf8, "product_line": pl.Utf8, "product_status": pl.Utf8,
    "launch_date": pl.Utf8, "exit_date": pl.Utf8, "as_of": pl.Utf8, "last_checked": pl.Utf8, "stale": pl.Boolean,
    "tag": pl.Utf8, "source_name": pl.Utf8, "source_url": pl.Utf8, "caveat": pl.Utf8,
}
PRODUCTS_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "segment": _SEG, "product_line": pa.Column(str),
        "product_status": pa.Column(str, pa.Check.isin(list(reference.PRODUCT_STATUSES))),
        "launch_date": pa.Column(str, pa.Check.str_matches(r"^\d{4}(-\d{2}(-\d{2})?)?$"), nullable=True),
        "exit_date": pa.Column(str, pa.Check.str_matches(r"^\d{4}(-\d{2}(-\d{2})?)?$"), nullable=True),
        "as_of": _DATE, "last_checked": _DATE, "stale": pa.Column(pl.Boolean),
        "tag": pa.Column(str, pa.Check.isin(CHARTABLE_TAGS)), "source_name": pa.Column(str), "source_url": _URL,
        "caveat": pa.Column(str, nullable=True),
    },
    unique=PRODUCTS_KEY, strict=True, coerce=False,
)

EVENTS_KEY = ["company", "event_kind", "event_date", "counterparty"]
EVENTS_DTYPES: dict[str, pl.DataType] = {
    "event_date": pl.Utf8, "company": pl.Utf8, "segment": pl.Utf8, "event_kind": pl.Utf8, "counterparty": pl.Utf8,
    "target": pl.Utf8, "value": pl.Float64, "unit": pl.Utf8, "qualifier": pl.Utf8, "metric_kind": pl.Utf8,
    "tag": pl.Utf8, "stale": pl.Boolean, "source_name": pl.Utf8, "source_url": pl.Utf8, "caveat": pl.Utf8,
}
EVENTS_SCHEMA = pa.DataFrameSchema(
    {
        "event_date": _DATE, "company": pa.Column(str), "segment": _SEG,
        "event_kind": pa.Column(str, pa.Check.isin(list(reference.EVENT_KINDS))), "counterparty": pa.Column(str),
        "target": pa.Column(str, nullable=True), "value": pa.Column(pl.Float64, pa.Check.ge(0), nullable=True),
        "unit": pa.Column(str, nullable=True), "qualifier": _Q_NULL,
        "metric_kind": pa.Column(str, pa.Check.isin([k for k in config.METRIC_KINDS
                                                     if k not in config.NEVER_CHARTED_KINDS]), nullable=True),
        "tag": pa.Column(str, pa.Check.isin(CHARTABLE_TAGS)), "stale": pa.Column(pl.Boolean),
        "source_name": pa.Column(str), "source_url": _URL, "caveat": pa.Column(str, nullable=True),
    },
    unique=EVENTS_KEY, strict=True, coerce=False,
    checks=[pa.Check(_value_iff_qualifier, error="a value needs a qualifier")],
)

DICT_KEY = ["dataset", "scope", "metric"]
DICT_DTYPES: dict[str, pl.DataType] = {
    "dataset": pl.Utf8, "scope": pl.Utf8, "metric": pl.Utf8, "metric_kind": pl.Utf8, "unit": pl.Utf8,
    "definition": pl.Utf8, "comparable": pl.Utf8, "source_name": pl.Utf8, "source_url": pl.Utf8,
    "n_rows": pl.Int64, "mart": pl.Utf8,
}
DICT_SCHEMA = pa.DataFrameSchema(
    {
        "dataset": pa.Column(str), "scope": pa.Column(str), "metric": pa.Column(str),
        "metric_kind": pa.Column(str, pa.Check.isin([k for k in config.METRIC_KINDS
                                                     if k not in config.NEVER_CHARTED_KINDS])),
        "unit": pa.Column(str), "definition": pa.Column(str, pa.Check.str_length(min_value=1)),
        "comparable": pa.Column(str), "source_name": pa.Column(str), "source_url": pa.Column(str),
        "n_rows": pa.Column(pl.Int64, pa.Check.ge(1)), "mart": pa.Column(str),
    },
    unique=DICT_KEY, strict=True, coerce=False,
)

REPORTED_SOURCES = list(reference.KINDS)
REPORTED_KEY = ["source", "company", "metric", "period", "date", "counterparty", "value"]
REPORTED_DTYPES = dict(ledger.REPORTED_DTYPES)
REPORTED_SCHEMA = pa.DataFrameSchema(
    dict(ledger.REPORTED_SCHEMA.columns) | {"source": pa.Column(str, pa.Check.isin(REPORTED_SOURCES))},
    unique=REPORTED_KEY, strict=True, coerce=False,
)

# --- the Q1-Q9 scoreboard ------------------------------------------------------------------------

SCORE_KEY = ["id"]
SCORE_DTYPES: dict[str, pl.DataType] = {
    "id": pl.Utf8, "title": pl.Utf8, "status": pl.Utf8, "reason": pl.Utf8, "graded": pl.Utf8,
    "next_grading": pl.Utf8, "already_seen": pl.Utf8, "inputs": pl.Utf8, "ci_only": pl.Boolean,
    "inputs_present": pl.Boolean,
}
SCORE_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, pa.Check.isin([f"Q{i}" for i in range(1, 10)])), "title": pa.Column(str),
        "status": pa.Column(str, pa.Check.isin(config.STATUSES)), "reason": pa.Column(str),
        "graded": pa.Column(str), "next_grading": pa.Column(str, nullable=True),
        "already_seen": pa.Column(str), "inputs": pa.Column(str), "ci_only": pa.Column(pl.Boolean),
        "inputs_present": pa.Column(pl.Boolean),
    },
    unique=SCORE_KEY, strict=True, coerce=False,
)
EVIDENCE_DTYPES: dict[str, pl.DataType] = {
    "id": pl.Utf8, "label": pl.Utf8, "value": pl.Float64, "qualifier": pl.Utf8, "unit": pl.Utf8,
    "period": pl.Utf8, "source_url": pl.Utf8, "note": pl.Utf8,
}
EVIDENCE_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, pa.Check.isin([f"Q{i}" for i in range(1, 10)])), "label": pa.Column(str),
        "value": pa.Column(pl.Float64, nullable=True), "qualifier": _Q_NULL, "unit": pa.Column(str),
        "period": pa.Column(str, nullable=True), "source_url": pa.Column(str, nullable=True),
        "note": pa.Column(str, nullable=True),
    },
    unique=["id", "label"], strict=True, coerce=False,
    checks=[pa.Check(_value_iff_qualifier, error="a value needs a qualifier")],
)

# --- headline KPIs (exactly three) ----------------------------------------------------------------

KPI_DTYPES: dict[str, pl.DataType] = {
    "id": pl.Utf8, "label": pl.Utf8, "value": pl.Float64, "qualifier": pl.Utf8, "unit": pl.Utf8,
    "period": pl.Utf8, "source_url": pl.Utf8, "as_of": pl.Utf8, "note": pl.Utf8,
}
KPI_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, unique=True), "label": pa.Column(str), "value": pa.Column(pl.Float64),
        "qualifier": _Q, "unit": pa.Column(str), "period": pa.Column(str), "source_url": _URL, "as_of": _DATE,
        "note": pa.Column(str),
    },
    strict=True, coerce=False,
    checks=[pa.Check(lambda d: d.lazyframe.select(pl.len() == 3), error="exactly three headline KPIs")],
)

# --- views of the CI-only sources (written only when the source is on disk) ------------------------

WEB_Q2_KEY = ["slice", "technology", "date"]
WEB_Q2_DTYPES: dict[str, pl.DataType] = {
    "slice": pl.Utf8, "technology": pl.Utf8, "date": pl.Utf8, "origins": pl.Int64, "total_origins": pl.Int64,
    "share": pl.Float64, "rolling_mean_3m": pl.Float64,
}
WEB_Q2_SCHEMA = pa.DataFrameSchema(
    {
        "slice": pa.Column(str), "technology": pa.Column(str), "date": _DATE,
        "origins": pa.Column(pl.Int64, pa.Check.ge(0)), "total_origins": pa.Column(pl.Int64, nullable=True),
        "share": pa.Column(pl.Float64, pa.Check.in_range(0.0, 1.0), nullable=True),
        "rolling_mean_3m": pa.Column(pl.Float64, pa.Check.ge(0.0), nullable=True),
    },
    unique=WEB_Q2_KEY, strict=True, coerce=False,
)

Q1_VIEW_DTYPES: dict[str, pl.DataType] = {
    "month": pl.Utf8, "stripe_downloads": pl.Int64, "pool_downloads": pl.Int64, "download_share": pl.Float64,
    "volume_share_upper": pl.Float64, "ratio": pl.Float64, "ratio_qualifier": pl.Utf8, "graded": pl.Boolean,
    "below_refute": pl.Boolean, "consecutive_below": pl.Int64, "status": pl.Utf8,
}
Q1_VIEW_SCHEMA = pa.DataFrameSchema(
    {
        "month": _DATE, "stripe_downloads": pa.Column(pl.Int64, pa.Check.ge(0)),
        "pool_downloads": pa.Column(pl.Int64, pa.Check.gt(0)),
        "download_share": pa.Column(pl.Float64, pa.Check.in_range(0.0, 1.0)),
        "volume_share_upper": pa.Column(pl.Float64, pa.Check.in_range(0.0, 1.0), nullable=True),
        "ratio": pa.Column(pl.Float64, pa.Check.ge(0.0), nullable=True), "ratio_qualifier": _Q_NULL,
        "graded": pa.Column(pl.Boolean), "below_refute": pa.Column(pl.Boolean),
        "consecutive_below": pa.Column(pl.Int64, pa.Check.ge(0)),
        "status": pa.Column(str, pa.Check.isin(config.STATUSES)),
    },
    unique=["month"], strict=True, coerce=False,
)

DOWNLOADS_KEY = ["side", "series", "period"]
DOWNLOADS_DTYPES: dict[str, pl.DataType] = {
    "side": pl.Utf8, "series": pl.Utf8, "company": pl.Utf8, "period": pl.Utf8, "downloads": pl.Int64,
    "index": pl.Float64,
}
DOWNLOADS_SCHEMA = pa.DataFrameSchema(
    {
        "side": pa.Column(str, pa.Check.isin(["client", "server"])), "series": pa.Column(str),
        "company": pa.Column(str, nullable=True), "period": _DATE,
        "downloads": pa.Column(pl.Int64, pa.Check.ge(0)), "index": pa.Column(pl.Float64, nullable=True),
    },
    unique=DOWNLOADS_KEY, strict=True, coerce=False,
)

JOBS_KEY = ["snapshot_date", "ats", "board", "function"]
JOBS_DTYPES: dict[str, pl.DataType] = {
    "snapshot_date": pl.Utf8, "ats": pl.Utf8, "board": pl.Utf8, "function": pl.Utf8, "postings": pl.Int64,
    "openings": pl.Int64,
}
JOBS_SCHEMA = pa.DataFrameSchema(
    {
        "snapshot_date": _DATE, "ats": pa.Column(str, pa.Check.isin(list(config.ATS_BOARDS))),
        "board": pa.Column(str), "function": pa.Column(str),
        "postings": pa.Column(pl.Int64, pa.Check.ge(0)), "openings": pa.Column(pl.Int64, pa.Check.ge(0)),
    },
    unique=JOBS_KEY, strict=True, coerce=False,
)

FORMD_VIEW_KEY = ["company", "accession"]
FORMD_VIEW_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "filing_date": pl.Utf8, "form": pl.Utf8, "accession": pl.Utf8,
    "total_offering_amount": pl.Float64, "offering_indefinite": pl.Boolean, "total_amount_sold": pl.Float64,
    "date_of_first_sale": pl.Utf8, "url": pl.Utf8,
}
FORMD_VIEW_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "filing_date": _DATE, "form": pa.Column(str, pa.Check.isin(["D", "D/A"])),
        "accession": pa.Column(str), "total_offering_amount": pa.Column(pl.Float64, pa.Check.ge(0), nullable=True),
        "offering_indefinite": pa.Column(pl.Boolean),
        "total_amount_sold": pa.Column(pl.Float64, pa.Check.ge(0), nullable=True),
        "date_of_first_sale": _DATE_NULL, "url": _URL,
    },
    unique=FORMD_VIEW_KEY, strict=True, coerce=False,
)

SEC_VIEW_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8, "company": pl.Utf8, "period_end": pl.Utf8, "basis": pl.Utf8, "currency": pl.Utf8,
    "revenue": pl.Float64, "rev_growth": pl.Float64, "gross_margin": pl.Float64, "op_margin": pl.Float64,
    "net_margin": pl.Float64, "fcf_margin": pl.Float64, "latest_from_frames": pl.Boolean,
}
SEC_VIEW_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, unique=True), "company": pa.Column(str, nullable=True), "period_end": _DATE,
        "basis": pa.Column(str), "currency": pa.Column(str, pa.Check.str_matches(r"^[A-Z]{3}$")),
        "revenue": pa.Column(pl.Float64, pa.Check.gt(0)),
        "rev_growth": pa.Column(pl.Float64, pa.Check.in_range(-1.0, 10.0), nullable=True),
        "gross_margin": pa.Column(pl.Float64, pa.Check.in_range(-2.0, 1.0), nullable=True),
        "op_margin": pa.Column(pl.Float64, pa.Check.in_range(-10.0, 1.0), nullable=True),
        "net_margin": pa.Column(pl.Float64, pa.Check.in_range(-10.0, 5.0), nullable=True),
        "fcf_margin": pa.Column(pl.Float64, pa.Check.in_range(-10.0, 5.0), nullable=True),
        "latest_from_frames": pa.Column(pl.Boolean),
    },
    strict=True, coerce=False,
)

# --- registry -------------------------------------------------------------------------------------

#: Every mart publish writes (as ``<name>.json``) and the schema that gates it.
MARTS: dict[str, pa.DataFrameSchema] = {
    "waterfall_lines": economics.LINE_SCHEMA,
    "sensitivity": economics.SENS_SCHEMA,
    "ledger_timeline": ledger.TIMELINE_SCHEMA,
    "ledger_latest": ledger.LATEST_SCHEMA,
    "private_intervals": ledger.INTERVAL_SCHEMA,
    "private_take_rates_refused": ledger.REFUSED_SCHEMA,
    "take_rates": ledger.TAKE_SCHEMA,
    "take_rates_refused": ledger.REFUSED_SCHEMA,
    "share_lenses": share.LONG_SCHEMA,
    "share_pool_hhi": share.HHI_SCHEMA,
    "products": PRODUCTS_SCHEMA,
    "events": EVENTS_SCHEMA,
    "metric_dictionary": DICT_SCHEMA,
    "reported_unconfirmed": REPORTED_SCHEMA,
    "scoreboard": SCORE_SCHEMA,
    "scoreboard_evidence": EVIDENCE_SCHEMA,
    "kpis": KPI_SCHEMA,
    "webtech_q2": WEB_Q2_SCHEMA,
    "devstats_q1": Q1_VIEW_SCHEMA,
    "devstats_monthly": DOWNLOADS_SCHEMA,
    "jobs_by_function": JOBS_SCHEMA,
    "formd_filings": FORMD_VIEW_SCHEMA,
    "sec_latest": SEC_VIEW_SCHEMA,
}

#: Tables that only ever hold chartable (tag V/C) rows; publish asserts it on top of each schema.
CHARTED_MARTS = ("waterfall_lines", "ledger_timeline", "ledger_latest", "private_intervals", "take_rates",
                 "products", "events")


def validate(name: str, df: pl.DataFrame) -> pl.DataFrame:
    """Validate one mart against its registered schema; raises before anything is written."""
    if name not in MARTS:
        raise KeyError(f"no schema registered for mart {name!r}")
    return MARTS[name].validate(df)  # type: ignore[return-value]
