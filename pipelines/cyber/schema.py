"""Pandera contracts for every table ``publish.py`` writes under ``data/marts/cyber/`` and for the facts file.

Tables whose module owns their contract keep it, re-used here and never copied, so there is one contract per table:
the ring C tables are :mod:`pipelines.cyber.ledger`'s, ``china_panel`` is :mod:`pipelines.cyber.valuation_history_em`'s,
``etf_crosscheck`` / ``excluded`` / ``metric_dictionary`` are :mod:`pipelines.cyber.etf`'s, the scoreboard and its
evidence are :mod:`pipelines.cyber.evaluate`'s and the regression object's three record lists are
:mod:`pipelines.cyber.regression`'s. What ``publish`` assembles itself is defined below: the listing table (every ring,
one row per pool listing), the layer summary, the targets table, the recommendation counts, the three headline KPIs and
the flat scoreboard.

:data:`MARTS` maps every mart name to its schema and :data:`DTYPES` to the dtypes an empty table of it has, so a source
that is absent yields an empty, valid table and not a missing file. :func:`validate` is the single gate ``publish``
calls before a write: a schema failure blocks the write (``CLAUDE.md``, rule 3).

The facts file is JSON and not a table. :func:`validate_facts` is its gate: the top-level keys the page reads exist, a
number is always a ``{value, qualifier, unit, period, as_of, source_url}`` record whose qualifier is one of ``= > < ~``,
there are exactly three KPIs and each is tagged ``V`` or ``C``, there are the ten scoreboard items, and every check has
a status. Nothing in it may be NaN or infinite, because JSON cannot say so.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from typing import Any

import pandera.polars as pa
import polars as pl

from pipelines.cyber import config as C
from pipelines.cyber import etf, evaluate, ledger, regression
from pipelines.cyber import reference as R
from pipelines.cyber import valuation_history_em as V

QUALIFIERS = list(R.QUALIFIERS)
CHARTABLE_TAGS = list(R.CHARTABLE_TAGS)
LAYERS = list(C.LAYERS)
MARKET_GROUPS = ["us", "china", "other_overseas"]
#: Why a listing has no EV/NTM revenue (``metrics._reason``). A priced listing with no multiple and no reason is a
#: silent miss, which ``publish`` records as a failed check.
EV_REASONS = ["not_priced", "currency_mismatch", "no_enterprise_value", "no_revenue_consensus"]
CHECK_STATUSES = ["pass", "warn", "fail"]
THIN = C.THIN_COVERAGE_BELOW

_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
_DATE = pa.Column(str, pa.Check.str_matches(_DATE_RE))
_DATE_NULL = pa.Column(str, pa.Check.str_matches(_DATE_RE), nullable=True)
_URL = pa.Column(str, pa.Check.str_startswith("http"))
_Q = pa.Column(str, pa.Check.isin(QUALIFIERS))
_S = pa.Column(str, nullable=True)
_F = pa.Column(pl.Float64, nullable=True)
_FPOS = pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True)
_IGE0 = pa.Column(pl.Int64, pa.Check.ge(0), nullable=True)
_B = pa.Column(pl.Boolean)
_BNULL = pa.Column(pl.Boolean, nullable=True)
_LAYER = pa.Column(str, pa.Check.isin(LAYERS))


def _finite(*columns: str) -> pa.Check:
    """No NaN and no infinity in these columns: JSON cannot carry them and a chart would read them as zero."""

    def check(d: pa.PolarsData) -> pl.LazyFrame:
        ok = [(~pl.col(c).is_nan() & ~pl.col(c).is_infinite()).fill_null(True) for c in columns]
        return d.lazyframe.select(pl.all_horizontal(ok) if ok else pl.lit(True))

    return pa.Check(check, error=f"NaN or infinity in {', '.join(columns)}")


# --- listings: every ring, one row per pool listing ----------------------------------------------------------------
YAHOO_RATING_BUCKETS = ["strong_buy", "buy", "hold", "sell", "strong_sell"]
EM_RATING_FIELDS = ["orgs", "buy", "add", "neutral", "reduce", "sell"]
_YAHOO_RATINGS = pl.Struct({k: pl.Int64 for k in YAHOO_RATING_BUCKETS})
_EM_RATINGS = pl.Struct({k: pl.Int64 for k in EM_RATING_FIELDS})

LISTINGS_KEY = ["ticker"]
LISTINGS_DTYPES: dict[str, Any] = {
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "name_cn": pl.Utf8,
    "country": pl.Utf8,
    "ring": pl.Utf8,
    "layer": pl.Utf8,
    "purity": pl.Utf8,
    "status": pl.Utf8,
    "status_date": pl.Utf8,
    "platform": pl.Boolean,
    "placement_basis": pl.Utf8,
    "also_in": pl.List(pl.Utf8),
    "market_group": pl.Utf8,
    "note": pl.Utf8,
    "priced": pl.Boolean,
    "currency": pl.Utf8,
    "financial_currency": pl.Utf8,
    "price": pl.Float64,
    "price_ts": pl.Utf8,
    "market_cap": pl.Float64,
    "enterprise_value": pl.Float64,
    "enterprise_value_usd": pl.Float64,
    "ntm_revenue": pl.Float64,
    "ev_ntm": pl.Float64,
    "ev_ntm_reason": pl.Utf8,
    "growth": pl.Float64,
    "fcf_margin": pl.Float64,
    "rule_of_40": pl.Float64,
    "target_mean": pl.Float64,
    "target_high": pl.Float64,
    "target_low": pl.Float64,
    "n_opinions": pl.Int64,
    "upside": pl.Float64,
    "dispersion": pl.Float64,
    "thin": pl.Boolean,
    "recommendation_mean": pl.Float64,
    "yahoo_ratings": _YAHOO_RATINGS,
    "em_ratings_6m": _EM_RATINGS,
    "rev_fy0_n": pl.Int64,
    "rev_fy1_n": pl.Int64,
    "em_rev0_n": pl.Int64,
    "fy0_end": pl.Utf8,
    "in_regression_population": pl.Boolean,
}


def _listing_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    """A multiple excludes its reason; a measure exists only for a listing the run holds a row for; ``thin`` follows
    the number of target analysts. ``priced`` means the run holds a row for the listing, which can still have no price
    (the capture writes a row, with nulls, for a listing whose fetch failed). The enterprise value in dollars exists
    only for a listing that has an enterprise value in its own currency, and has its sign."""
    c = pl.col
    return d.lazyframe.select(
        (c("priced") | (c("ev_ntm").is_null() & c("upside").is_null() & c("rule_of_40").is_null()))
        & (
            c("enterprise_value_usd").is_null()
            | (c("enterprise_value").is_not_null() & (c("enterprise_value_usd").sign() == c("enterprise_value").sign()))
        )
        & (c("ev_ntm").is_null() | c("ev_ntm_reason").is_null())
        & ((c("ring") != "A") | c("priced") | (c("ev_ntm_reason") == "not_priced"))
        & (c("thin").is_null() | (c("thin") == (c("n_opinions").fill_null(0) < THIN)))
    )


LISTINGS_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str, pa.Check.str_length(min_value=1)),
        "name": pa.Column(str),
        "name_cn": _S,
        "country": pa.Column(str),
        "ring": pa.Column(str, pa.Check.isin(["A", "B", "F"])),
        "layer": _LAYER,
        "purity": pa.Column(str, pa.Check.isin(C.PURITY)),
        "status": pa.Column(str, pa.Check.isin(C.STATUS)),
        "status_date": _S,
        "platform": _B,
        "placement_basis": pa.Column(str, pa.Check.isin(C.PLACEMENT_BASIS)),
        "also_in": pa.Column(pl.List(pl.Utf8)),
        "market_group": pa.Column(str, pa.Check.isin(MARKET_GROUPS)),
        "note": _S,
        "priced": _B,
        "currency": _S,
        "financial_currency": _S,
        "price": _FPOS,
        "price_ts": _S,
        "market_cap": _FPOS,
        "enterprise_value": _F,
        "enterprise_value_usd": _F,
        "ntm_revenue": _F,
        "ev_ntm": _FPOS,
        "ev_ntm_reason": pa.Column(str, pa.Check.isin(EV_REASONS), nullable=True),
        "growth": _F,
        "fcf_margin": _F,
        "rule_of_40": _F,
        "target_mean": _FPOS,
        "target_high": _FPOS,
        "target_low": _FPOS,
        "n_opinions": _IGE0,
        "upside": pa.Column(pl.Float64, pa.Check.gt(-1.0), nullable=True),
        "dispersion": pa.Column(pl.Float64, pa.Check.ge(0.0), nullable=True),
        "thin": _BNULL,
        "recommendation_mean": _F,
        "yahoo_ratings": pa.Column(_YAHOO_RATINGS, nullable=True),
        "em_ratings_6m": pa.Column(_EM_RATINGS, nullable=True),
        "rev_fy0_n": _IGE0,
        "rev_fy1_n": _IGE0,
        "em_rev0_n": _IGE0,
        "fy0_end": _S,
        "in_regression_population": _B,
    },
    unique=LISTINGS_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_listing_consistent, error="priced / multiple / reason / thin flags are inconsistent"),
        _finite(
            "price",
            "market_cap",
            "enterprise_value",
            "enterprise_value_usd",
            "ntm_revenue",
            "ev_ntm",
            "growth",
            "fcf_margin",
            "rule_of_40",
            "target_mean",
            "upside",
            "dispersion",
            "recommendation_mean",
        ),
    ],
)

# --- layer_summary: one row per layer ------------------------------------------------------------------------------
_L2_COUNT = pl.Struct({"l2": pl.Utf8, "n": pl.Int64})
LAYER_SUMMARY_KEY = ["layer"]
LAYER_SUMMARY_DTYPES: dict[str, Any] = {
    "layer": pl.Utf8,
    "code": pl.Int64,
    "name": pl.Utf8,
    "n_l2": pl.Int64,
    "n_a": pl.Int64,
    "n_a_us": pl.Int64,
    "n_a_china": pl.Int64,
    "n_a_other_overseas": pl.Int64,
    "n_b": pl.Int64,
    "n_b_us": pl.Int64,
    "n_b_china": pl.Int64,
    "n_b_other_overseas": pl.Int64,
    "n_f": pl.Int64,
    "n_private": pl.Int64,
    "n_private_overseas": pl.Int64,
    "n_private_china": pl.Int64,
    "private_by_l2": pl.List(_L2_COUNT),
    "n_population": pl.Int64,
    "n_population_with_multiple": pl.Int64,
    "population_median_ev_ntm": pl.Float64,
}


def _layer_counts_add_up(d: pa.PolarsData) -> pl.LazyFrame:
    """The listings of a ring add up over the market groups, the private companies of a layer over the two markets of
    the curated directory, and the population cannot exceed the layer."""
    c = pl.col
    return d.lazyframe.select(
        (c("n_a") == c("n_a_us") + c("n_a_china") + c("n_a_other_overseas"))
        & (c("n_b") == c("n_b_us") + c("n_b_china") + c("n_b_other_overseas"))
        & (c("n_private") == c("n_private_overseas") + c("n_private_china"))
        & (c("n_population_with_multiple") <= c("n_population"))
        & (c("population_median_ev_ntm").is_null() == (c("n_population_with_multiple") == 0))
        & (c("private_by_l2").list.eval(pl.element().struct.field("n")).list.sum() <= c("n_private"))
    )


LAYER_SUMMARY_SCHEMA = pa.DataFrameSchema(
    {
        "layer": pa.Column(str, pa.Check.isin(LAYERS)),
        "code": pa.Column(pl.Int64, pa.Check.in_range(1, len(LAYERS))),
        "name": pa.Column(str),
        "n_l2": pa.Column(pl.Int64, pa.Check.ge(1)),
        **{k: pa.Column(pl.Int64, pa.Check.ge(0)) for k in LAYER_SUMMARY_DTYPES if k.startswith(("n_a", "n_b", "n_f"))},
        "n_private": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_private_overseas": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_private_china": pa.Column(pl.Int64, pa.Check.ge(0)),
        "private_by_l2": pa.Column(pl.List(_L2_COUNT)),
        "n_population": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_population_with_multiple": pa.Column(pl.Int64, pa.Check.ge(0)),
        "population_median_ev_ntm": _FPOS,
    },
    unique=LAYER_SUMMARY_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_layer_counts_add_up, error="counts by market group do not add up to the ring counts"),
        pa.Check(lambda d: d.lazyframe.select(pl.len() == len(LAYERS)), error="one row for each of the layers"),
        _finite("population_median_ev_ntm"),
    ],
)

# --- targets: ring A outside China with a target mean -----------------------------------------------------------------
TARGETS_KEY = ["ticker"]
TARGETS_DTYPES: dict[str, Any] = {
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "layer": pl.Utf8,
    "purity": pl.Utf8,
    "market_group": pl.Utf8,
    "status": pl.Utf8,
    "currency": pl.Utf8,
    "price": pl.Float64,
    "price_ts": pl.Utf8,
    "target_mean": pl.Float64,
    "target_high": pl.Float64,
    "target_low": pl.Float64,
    "n_opinions": pl.Int64,
    "upside": pl.Float64,
    "dispersion": pl.Float64,
    "thin": pl.Boolean,
    "pence_quote": pl.Boolean,
    "in_q4_q5_list": pl.Boolean,
    "as_of": pl.Utf8,
}


def _upside_is_the_ratio(d: pa.PolarsData) -> pl.LazyFrame:
    """Upside is target mean over price minus one in the quote currency; a ratio, so pence cancel."""
    c = pl.col
    ratio = c("target_mean") / c("price") - 1.0
    return d.lazyframe.select(
        ((c("upside") - ratio).abs() <= 1e-9 * (1.0 + ratio.abs()))
        & (c("thin") == (c("n_opinions").fill_null(0) < THIN))
        & (c("pence_quote") == (c("currency") == "GBp"))
        & (c("dispersion").is_null() | (c("dispersion") >= 0.0))
    )


TARGETS_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "name": pa.Column(str),
        "layer": _LAYER,
        "purity": pa.Column(str, pa.Check.isin(C.PURITY)),
        "market_group": pa.Column(str, pa.Check.isin(["us", "other_overseas"])),
        "status": pa.Column(str, pa.Check.isin(C.STATUS)),
        "currency": pa.Column(str),
        "price": pa.Column(pl.Float64, pa.Check.gt(0.0)),
        "price_ts": _S,
        "target_mean": pa.Column(pl.Float64, pa.Check.gt(0.0)),
        "target_high": _FPOS,
        "target_low": _FPOS,
        "n_opinions": _IGE0,
        "upside": pa.Column(pl.Float64, pa.Check.gt(-1.0)),
        "dispersion": pa.Column(pl.Float64, pa.Check.ge(0.0), nullable=True),
        "thin": _B,
        "pence_quote": _B,
        "in_q4_q5_list": _B,
        "as_of": _DATE,
    },
    unique=TARGETS_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_upside_is_the_ratio, error="upside, thin or the pence flag disagree with the columns they follow"),
        _finite("price", "target_mean", "upside", "dispersion"),
    ],
)

# --- recommendations: Yahoo's five buckets and East Money's six-month counts, in two vocabularies ----------------
RECOMMENDATIONS_KEY = ["source", "scope", "ticker"]
YAHOO_COLUMNS = [*YAHOO_RATING_BUCKETS, "recommendation_mean"]
EM_COLUMNS = ["em_orgs", "em_buy", "em_add", "em_neutral", "em_reduce", "em_sell"]
RECOMMENDATIONS_DTYPES: dict[str, Any] = {
    "source": pl.Utf8,
    "scope": pl.Utf8,
    "ticker": pl.Utf8,
    "name": pl.Utf8,
    "layer": pl.Utf8,
    "market_group": pl.Utf8,
    "window": pl.Utf8,
    "n_listings": pl.Int64,
    "n_ratings": pl.Int64,
    **{k: pl.Int64 for k in YAHOO_RATING_BUCKETS},
    "recommendation_mean": pl.Float64,
    **{k: pl.Int64 for k in EM_COLUMNS},
    "as_of": pl.Utf8,
}


def _vocabularies_stay_apart(d: pa.PolarsData) -> pl.LazyFrame:
    """A Yahoo row holds no East Money field and the reverse; a listing row stands for one listing, a pooled row has
    no ticker; ``n_ratings`` is the sum of the counts the row has."""
    c = pl.col
    yahoo_cols, em_cols = YAHOO_COLUMNS, EM_COLUMNS
    yahoo_empty = pl.all_horizontal([c(k).is_null() for k in yahoo_cols])
    em_empty = pl.all_horizontal([c(k).is_null() for k in em_cols])
    y_sum = pl.sum_horizontal([c(k).fill_null(0) for k in YAHOO_RATING_BUCKETS])
    e_sum = pl.sum_horizontal([c(k).fill_null(0) for k in EM_COLUMNS if k != "em_orgs"])
    return d.lazyframe.select(
        pl.when(c("source") == "yahoo")
        .then(em_empty & (c("n_ratings") == y_sum))
        .otherwise(yahoo_empty & (c("n_ratings") == e_sum))
        & ((c("scope") == "pooled") == c("ticker").is_null())
        & ((c("scope") == "pooled") | (c("n_listings") == 1))
    )


RECOMMENDATIONS_SCHEMA = pa.DataFrameSchema(
    {
        "source": pa.Column(str, pa.Check.isin(["yahoo", "eastmoney"])),
        "scope": pa.Column(str, pa.Check.isin(["listing", "pooled"])),
        "ticker": _S,
        "name": _S,
        "layer": pa.Column(str, pa.Check.isin(LAYERS), nullable=True),
        "market_group": pa.Column(str, pa.Check.isin(MARKET_GROUPS), nullable=True),
        "window": pa.Column(str),
        "n_listings": pa.Column(pl.Int64, pa.Check.ge(0)),
        "n_ratings": pa.Column(pl.Int64, pa.Check.ge(0)),
        **{k: _IGE0 for k in YAHOO_RATING_BUCKETS},
        "recommendation_mean": pa.Column(pl.Float64, pa.Check.in_range(1.0, 5.0), nullable=True),
        **{k: _IGE0 for k in EM_COLUMNS},
        "as_of": _DATE_NULL,
    },
    unique=RECOMMENDATIONS_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_vocabularies_stay_apart, error="the two rating vocabularies are mixed, or a count is wrong"),
        _finite("recommendation_mean"),
    ],
)

# --- headline KPIs (exactly three) --------------------------------------------------------------------------------
KPI_DTYPES: dict[str, Any] = {
    "id": pl.Utf8,
    "label": pl.Utf8,
    "value": pl.Float64,
    "qualifier": pl.Utf8,
    "unit": pl.Utf8,
    "period": pl.Utf8,
    "source_url": pl.Utf8,
    "as_of": pl.Utf8,
    "tag": pl.Utf8,
    "note": pl.Utf8,
}
KPI_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, unique=True),
        "label": pa.Column(str),
        "value": pa.Column(pl.Float64),
        "qualifier": _Q,
        "unit": pa.Column(str),
        "period": pa.Column(str),
        "source_url": _URL,
        "as_of": _DATE,
        "tag": pa.Column(str, pa.Check.isin(CHARTABLE_TAGS)),
        "note": pa.Column(str),
    },
    strict=True,
    coerce=False,
    checks=[
        pa.Check(lambda d: d.lazyframe.select(pl.len() == 3), error="exactly three headline KPIs"),
        _finite("value"),
    ],
)

# --- the scoreboard: Q1..Q10 without the evidence list; the evidence is its own mart -------------------------------
SCOREBOARD_DTYPES: dict[str, Any] = {k: v for k, v in evaluate.SCORE_DTYPES.items() if k != "evidence"}
SCOREBOARD_SCHEMA = evaluate.SCOREBOARD_SCHEMA.remove_columns(["evidence"])
EVIDENCE_DTYPES: dict[str, Any] = dict(evaluate.FLAT_DTYPES)
EVIDENCE_SCHEMA = evaluate.EVIDENCE_SCHEMA

# --- checks ----------------------------------------------------------------------------------------------------------
CHECKS_SCHEMA = pa.DataFrameSchema(
    {
        "name": pa.Column(str, pa.Check.str_length(min_value=1)),
        "status": pa.Column(str, pa.Check.isin(CHECK_STATUSES)),
        "detail": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

# --- registry -------------------------------------------------------------------------------------------------------
#: Every table mart publish writes (as ``<name>.json``, a list of records) and the schema that gates it. ``regression``
#: is an object and is validated by :func:`validate_regression`; it is listed in :data:`OBJECT_MARTS`.
MARTS: dict[str, pa.DataFrameSchema] = {
    "listings": LISTINGS_SCHEMA,
    "layer_summary": LAYER_SUMMARY_SCHEMA,
    "targets": TARGETS_SCHEMA,
    "recommendations": RECOMMENDATIONS_SCHEMA,
    "china_panel": V.CHINA_PANEL_SCHEMA,
    "private_intervals": ledger.SCHEMAS["private_intervals"],
    "ledger_timeline": ledger.SCHEMAS["ledger_timeline"],
    "ledger_latest": ledger.SCHEMAS["ledger_latest"],
    "events": ledger.SCHEMAS["events"],
    "reported_unconfirmed": ledger.SCHEMAS["reported_unconfirmed"],
    "etf_crosscheck": etf.TABLE_SCHEMA,
    "excluded": etf.EXCLUDED_SCHEMA,
    "scoreboard": SCOREBOARD_SCHEMA,
    "scoreboard_evidence": EVIDENCE_SCHEMA,
    "metric_dictionary": etf.DICTIONARY_SCHEMA,
    "kpis": KPI_SCHEMA,
}
DTYPES: dict[str, dict[str, Any]] = {
    "listings": LISTINGS_DTYPES,
    "layer_summary": LAYER_SUMMARY_DTYPES,
    "targets": TARGETS_DTYPES,
    "recommendations": RECOMMENDATIONS_DTYPES,
    "china_panel": V.PANEL_DTYPES,
    **{name: ledger.DTYPES[name] for name in ledger.SCHEMAS},
    "etf_crosscheck": etf.TABLE_DTYPES,
    "excluded": etf.EXCLUDED_DTYPES,
    "scoreboard": SCOREBOARD_DTYPES,
    "scoreboard_evidence": EVIDENCE_DTYPES,
    "metric_dictionary": etf.DICTIONARY_DTYPES,
    "kpis": KPI_DTYPES,
}
OBJECT_MARTS = ("regression",)
#: Marts whose every row is a chartable one (tag V or C): ``ledger.CHARTED_MARTS``.
CHARTED_MARTS = ledger.CHARTED_MARTS
#: Mart names in the order they are listed in facts.marts and the spec.
ALL_MARTS = (
    "listings",
    "layer_summary",
    "regression",
    "targets",
    "recommendations",
    "china_panel",
    "private_intervals",
    "ledger_timeline",
    "ledger_latest",
    "events",
    "reported_unconfirmed",
    "etf_crosscheck",
    "excluded",
    "scoreboard",
    "scoreboard_evidence",
    "metric_dictionary",
    "kpis",
)
assert set(ALL_MARTS) == set(MARTS) | set(OBJECT_MARTS), "every mart has a contract"
assert set(DTYPES) == set(MARTS), "every table mart has dtypes for its empty form"


def empty(name: str) -> pl.DataFrame:
    """An empty table of mart ``name``, valid for every mart a source can leave empty (not for the three that always
    have rows: the KPIs, the layers and the ten scoreboard items)."""
    return pl.DataFrame(schema=DTYPES[name])


def frame(name: str, records: list[dict[str, Any]]) -> pl.DataFrame:
    """Records as a table with mart ``name``'s dtypes (missing keys become null), validated."""
    df = pl.DataFrame(records, schema=DTYPES[name]) if records else empty(name)
    return validate(name, df)


def validate(name: str, df: pl.DataFrame) -> pl.DataFrame:
    """Validate one table mart against its registered schema; raises ``pandera.errors.SchemaError`` before a write."""
    if name not in MARTS:
        raise KeyError(f"no schema registered for mart {name!r}")
    return MARTS[name].validate(df)  # type: ignore[return-value]


def validate_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return CHECKS_SCHEMA.validate(
        pl.DataFrame(checks, schema={"name": pl.Utf8, "status": pl.Utf8, "detail": pl.Utf8})
    ).to_dicts()  # type: ignore[union-attr]


REGRESSION_KEYS = (
    "today",
    "spec",
    "latest_age_days",
    "latest",
    "baseline",
    "series",
    "residuals",
    "premium",
    "premium_series",
)


def validate_regression(obj: dict[str, Any]) -> dict[str, Any]:
    """The regression object: its keys, and each of its record lists through the regression module's schemas."""
    missing = [k for k in REGRESSION_KEYS if k not in obj]
    if missing:
        raise ValueError(f"regression mart is missing {', '.join(missing)}")
    for key, validator in (
        ("series", regression.validate_points),
        ("residuals", regression.validate_residuals),
        ("premium_series", regression.validate_premiums),
    ):
        validator(obj[key])
    for key, validator in (("latest", regression.validate_points), ("premium", regression.validate_premiums)):
        if obj[key] is not None:
            validator([obj[key]])
    base = obj["baseline"]
    if set(base) != {"regression", "premium"}:
        raise ValueError("regression.baseline must hold exactly 'regression' and 'premium'")
    if base["regression"] is not None:
        regression.validate_points([base["regression"]])
    if base["premium"] is not None:
        regression.validate_premiums([base["premium"]])
    return obj


# --- the facts file ---------------------------------------------------------------------------------------------
FACT_KEYS = (
    "generated_at",
    "slug",
    "as_of",
    "registered",
    "scope",
    "universe",
    "regression",
    "ev_sales",
    "rule_of_40",
    "targets",
    "recommendations",
    "cn",
    "private",
    "events",
    "kpis",
    "scoreboard",
    "counts",
    "coverage",
    "checks",
    "sources",
    "marts",
)
VALUE_FIELDS = ("value", "qualifier", "unit", "period", "as_of", "source_url")


def walk(obj: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every node of a JSON-like tree with its dotted path (list items are ``[i]``)."""
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


#: Words from the author's working notes. A string a reader can see that contains one has shipped a note, so no string
#: in a mart or in the facts may match; :func:`working_note_strings` finds them and ``publish`` records the result as a
#: check. The curated files keep these words in their private notes only.
WORKING_NOTE_WORDS = re.compile(r"scouting|CROSSWALK|candidate (sheet|list)|lead file|curator|batch instruction", re.I)


def working_note_strings(obj: Any) -> list[str]:
    """Where a published value or key matches :data:`WORKING_NOTE_WORDS`: ``path: start of the string``. Takes the facts
    (a dict), a mart (a list of records) or a polars table."""
    if isinstance(obj, pl.DataFrame):
        obj = obj.to_dicts()
    out = []
    for path, node in walk(obj):
        if isinstance(node, str) and WORKING_NOTE_WORDS.search(node):
            out.append(f"{path}: {node[:60]}")
        elif isinstance(node, dict):
            out += [f"{path}.{k}" for k in node if WORKING_NOTE_WORDS.search(str(k))]
    return out


def non_finite(obj: Any) -> list[str]:
    """Paths of every NaN or infinite float in a JSON-like tree."""
    return [p for p, v in walk(obj) if isinstance(v, float) and not math.isfinite(v)]


def value_problems(facts: dict[str, Any]) -> list[str]:
    """Why a number in facts is not a number the page may print.

    A dict that has a ``value`` and a ``qualifier`` key is a published number. If its value is set it needs a qualifier
    from ``= > < ~``, a unit, and the ``period``, ``as_of`` and ``source_url`` keys (a value read off a source says
    which and when); an unset value carries no qualifier. The checks and the KPI list are walked like the rest.

    """
    out = []
    for path, node in walk(facts):
        if not (isinstance(node, dict) and "value" in node and "qualifier" in node):
            continue
        if node["value"] is None:
            if node["qualifier"] is not None:
                out.append(f"{path}: a qualifier without a value")
            continue
        if node["qualifier"] not in QUALIFIERS:
            out.append(f"{path}: qualifier {node['qualifier']!r} is not one of {' '.join(QUALIFIERS)}")
        absent = [k for k in VALUE_FIELDS if k not in node]
        if absent:
            out.append(f"{path}: missing {', '.join(absent)}")
        elif not node["unit"] or not node["source_url"]:
            out.append(f"{path}: a value needs a unit and a source_url")
    return out


def validate_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Raise ``ValueError`` naming every fault if the facts file is not fit to write."""
    faults = [f"missing top-level key {k!r}" for k in FACT_KEYS if k not in facts]
    faults += [f"not finite: {p}" for p in non_finite(facts)]
    faults += value_problems(facts)
    if len(facts.get("kpis", [])) != 3:
        faults.append(f"{len(facts.get('kpis', []))} KPIs, not three")
    if [r.get("id") for r in facts.get("scoreboard", [])] != evaluate.QIDS:
        faults.append("scoreboard is not Q1..Q10 in order")
    for k in facts.get("kpis", []):
        if k.get("tag") not in CHARTABLE_TAGS:
            faults.append(f"KPI {k.get('id')!r} has tag {k.get('tag')!r}, not V or C")
    if "checks" in facts:
        try:
            validate_checks(facts["checks"])
        except Exception as exc:  # noqa: BLE001 - reported with the rest
            faults.append(f"checks: {str(exc)[:200]}")
    if faults:
        raise ValueError("facts are not publishable: " + "; ".join(faults))
    json.dumps(facts, allow_nan=False)  # the last word: JSON itself must accept it
    return facts
