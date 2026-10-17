"""The private-company ledger for the cybersecurity page (ring C): valuations, ARR, deals and what is never charted.

Inputs are the lists of rows that :func:`pipelines.cyber.reference.rows` returns (``private_directory``,
``private_metrics``, ``events``; ``curator_notes`` already stripped, and stripped again here by construction: every
output column is picked by name, so a working note cannot travel) and ``multiples``, a dict of ticker to EV/NTM
revenue for the listed comparables. Nothing here reads a file, the network or the clock; ``today`` is an argument.

A private valuation is three numbers, never one (section I, "Ring C and private valuations")
-----------------------------------------------------------------------------------------------
``headline``    the latest ``current`` ``primary_round_valuation`` row that may be charted: what a round reported, with
                its qualifier, basis, date, source, vintage year and stale mark.
``discount``    ``headline * (1 + d - band)`` to ``headline * (1 + d + band)``, ``d`` the registered discount for the
                year of the round (:data:`DISCOUNTS`, parsed from ``config.DISCOUNT_BY_ROUND_YEAR``) and ``band`` the
                registered band (:data:`BAND`, parsed from ``config.DISCOUNT_BAND``). A round in a year the table
                does not cover is not scored, and the reason is a column. The discounts are an assumption, not data.
``comparable``  company-stated ARR (``arr_company_stated``, latest) times the lowest to the highest EV/NTM revenue
                among ``config.COMPARABLES_BY_LAYER[layer]`` that are present in ``multiples``. A layer with no
                registered comparables, a company with no ARR, or no usable multiple is not scored, with the reason.
``above``       Q6: the lower end of the discount interval is higher than the upper end of the comparable interval.
                Decided only when all three numbers exist, the headline's tag is V or C and its basis is ``disclosed``,
                and both are in one currency (a currency is never converted). Otherwise ``None`` with the reason.
                The qualifiers travel (``discount_qualifier`` is the headline's, ``comparable_qualifier`` the ARR's);
                when one cuts against reading the comparison as firm (a headline that is only an upper bound, an ARR
                that is only a lower bound, or an approximation), ``above_note`` says so and ``above`` is still the
                registered rule at face value ("ARR as given"). Nothing is ever summed or averaged across companies.

What is charted and what is not
-------------------------------
A row is chartable only if the curator marked it ``chartable``, its tag is V or C, and its ``metric_kind`` is not one of
``config.NEVER_CHARTED_KINDS`` (:func:`reason_not_chartable` is the single rule; it does not trust the curator's flag
alone). Every other row, from ``private_metrics`` or ``events``, goes to ``reported_unconfirmed`` with the reason and is
never charted. ``CHARTED_MARTS`` hold chartable rows only, and :func:`charted_problems` proves it on any set of tables.
``ledger_timeline`` therefore holds every chartable metric row (all statuses, so superseded rounds are the history) and
the rows it does not hold are in ``reported_unconfirmed``: together they are all the published metric rows, except that
a figure that two curated files hold (a round in ``events`` and the same round in ``private_metrics``: same company,
day, kind of figure, value and source) is listed once, from ``private_metrics`` (:func:`_dedupe_reported`).

The ``reason`` is a closed code, and ``reason_label`` is its sentence for the reader. The first five are decided by
this module from the row; the rest are the row's own ``not_charted_code``, which the curator must give to every row
that is not chartable (a missing or unknown code is ``unclassified`` and fails a check).

====================  =====================================================================================
reason                the row is not chartable because
====================  =====================================================================================
never_charted_kind    its ``metric_kind`` is a secondary mark, reported talks or a third-party estimate
unverified_tag        its tag is S (search extract) or U (unverified)
undisclosed_basis     it is a valuation whose basis is ``undisclosed``
invalid_value         a metric row has no positive number
reported_basis        a publication reported it and the company has not stated it
round_size_only       it is the size of a round, with no valuation or price
amount_not_exact      it is a floor or a range ("several hundred million yuan")
mixed_amount          it mixes new money with something else (a tender, a credit line, stock)
no_figure_stated      the event states no amount or price
ip_asset_purchase     the buyer bought assets or intellectual property, not the company
combined_value        one value covers several companies or a stake
price_not_comparable  it is a maximum, a net or a partial amount, not the price paid
basis_unclear         a valuation whose basis is not settled (no pre- or post-money wording, or a sponsor deal)
superseded_figure     a later figure replaced it
too_old               it is too old to set beside the charted figures
outside_scope         the target is not a security company (see ``docs/cyber/CURATION.md``)
unclassified          the row is not chartable and carries no known ``not_charted_code``
====================  =====================================================================================

``reported_unconfirmed`` also carries ``reason_group``, the three-way split the page labels: ``never_charted_kind``
(the kind is never charted, whoever stated it), else ``company_confirmed_not_charted`` (the company confirmed the figure
and it is still not charted), else ``reported_by_a_publication`` (a publication reported it and the company has not
confirmed it). See :func:`reason_group`; every row falls into exactly one group.

Deals
-----
:func:`deals` counts acquisitions as deals, not events. A closing attaches to the open announcement before it, and a
``take_private`` row that repeats an ``acquisition_announced`` row (same company, same day, same counterparty: one
curated fact entered under both kinds) is folded into it, so that deal counts once. The ``events`` mart carries the
outcome as ``deal_start`` (true on the row that opens a counted deal, false on a folded take-private and on a closing
that attaches to an announcement), so counting ``deal_start`` rows by event date reproduces ``acquisitions_12m``.

Public entry point: :func:`build`. Tables it returns are validated by the module-level schemas in :data:`SCHEMAS`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import Any, NamedTuple

import pandera.errors
import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check
from pipelines.cyber import config as C
from pipelines.cyber import reference as R

HEADLINE_KIND = "primary_round_valuation"
ARR_KIND = "arr_company_stated"
SEC_KIND = "acquisition_price_sec"
DEAL_START_KINDS = ("acquisition_announced", "take_private")
DEAL_CLOSE_KIND = "acquisition_closed"
#: The page's headline valuation figure is in dollars. Other currencies are listed in ``latest_valuation_max_by_unit``
#: and never converted into it.
HEADLINE_UNIT = "USD"
#: A mart whose every row is a chartable row. The ``reported_unconfirmed`` table is deliberately not one.
CHARTED_MARTS = ("private_intervals", "ledger_timeline", "ledger_latest", "events")
#: Why a row the curator did not mark chartable is not charted: the closed vocabulary of the row's ``not_charted_code``,
#: each with the sentence the page shows.
NOT_CHARTED_CODES: dict[str, str] = {
    "round_size_only": "Round size only: no valuation or price is stated",
    "amount_not_exact": "Amount stated only as a range or a floor",
    "mixed_amount": "Amount mixes new money with something else",
    "no_figure_stated": "No amount or price is stated",
    "ip_asset_purchase": "Buys assets or intellectual property, not the company",
    "combined_value": "One value covers several companies or a stake",
    "price_not_comparable": "Not the price paid: a maximum, a net or a partial amount",
    "basis_unclear": "Valuation not settled: no pre- or post-money wording, or not a priced round",
    "superseded_figure": "Replaced by a later figure",
    "too_old": "Too old to set beside the charted figures",
    "outside_scope": "The target is not a security company",
}
#: Every reason a row can carry, with its sentence. The first five are decided from the row, the last is the fallback.
REASON_LABELS: dict[str, str] = {
    "never_charted_kind": "A kind that is never charted: reported talks, a third-party estimate or a secondary mark",
    "unverified_tag": "Seen only in a search extract, or not verified",
    "undisclosed_basis": "A valuation the company did not disclose",
    "invalid_value": "No positive amount",
    "reported_basis": "A publication reported it; the company has not stated it",
    **NOT_CHARTED_CODES,
    "unclassified": "No reason recorded",
}
REASONS = tuple(REASON_LABELS)
UNCLASSIFIED = "unclassified"
#: A comparable interval resting on fewer comparables than this is drawn pale; the page's interval figure uses the same
#: number. An ARR dated more than this many months before the multiples it is set against is named beside the figure.
THIN_COMPARABLES = 3
OLD_ARR_MONTHS = 12

#: The three groups of ``reported_unconfirmed`` the page labels; see :func:`reason_group`.
REASON_GROUPS = ("company_confirmed_not_charted", "reported_by_a_publication", "never_charted_kind")

assert {HEADLINE_KIND, ARR_KIND, SEC_KIND} <= set(C.METRIC_KINDS), "ledger kinds must be in the pinned vocabulary"
assert {*DEAL_START_KINDS, DEAL_CLOSE_KIND} <= set(R.EVENT_KINDS), "ledger event kinds must be curated kinds"


class LedgerError(ValueError):
    """The curated rows cannot be turned into a valid mart (a duplicate key, a value outside its vocabulary)."""


class Ledger(NamedTuple):
    """What :func:`build` returns; unpack it or read the attributes.

    ``tables``   name -> validated polars frame: ``private_intervals``, ``ledger_timeline``, ``ledger_latest``,
                 ``reported_unconfirmed``, ``events``.
    ``summary``  ``{"as_of", "private": {...}, "events": {...}}``: the counts that become ``facts.private`` and
                 ``facts.events`` (see :func:`summarise`).
    ``checks``   ``[{name, status, detail}]`` in the shape of ``pipelines.common.checks.check``.
    """

    tables: dict[str, pl.DataFrame]
    summary: dict[str, Any]
    checks: list[dict]


# --- the registered assumptions, parsed from config so they exist once ------------------------------------------------
_PERCENT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s*$")
_POINTS = re.compile(r"(\d+(?:\.\d+)?)\s*percentage points")


def parse_discounts(table: dict[str, str]) -> dict[int, float]:
    """``{"2021": "-60%"}`` to ``{2021: -0.6}``. A malformed entry stops the import: a wrong discount is a wrong
    result."""
    out: dict[int, float] = {}
    for year, text in table.items():
        match = _PERCENT.match(str(text))
        if not str(year).isdigit() or match is None:
            raise ValueError(f"DISCOUNT_BY_ROUND_YEAR entry {year!r}: {text!r} is not a year and a percentage")
        out[int(year)] = float(match.group(1)) / 100.0
    return out


def parse_band(text: str) -> float:
    """``"plus or minus 15 percentage points"`` to ``0.15``."""
    match = _POINTS.search(text)
    if match is None:
        raise ValueError(f"DISCOUNT_BAND {text!r} does not state a number of percentage points")
    return float(match.group(1)) / 100.0


DISCOUNTS: dict[int, float] = parse_discounts(C.DISCOUNT_BY_ROUND_YEAR)
BAND: float = parse_band(C.DISCOUNT_BAND)


# --- small helpers ----------------------------------------------------------------------------------------------------
_QRANK = {"=": 0, ">": 1, "<": 1, "~": 2}
_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _positive(v: object) -> bool:
    return _is_number(v) and v > 0  # type: ignore[operator]


def _r(x: float | None) -> float | None:
    """Drop float noise (4800000000.000001) without rounding away real digits."""
    return None if x is None else float(f"{x:.12g}")


def _str(v: object) -> str | None:
    return None if v is None or (isinstance(v, str) and not v.strip()) else str(v)


def implied_interval(value: float, qualifier: str) -> tuple[float | None, float | None, float | None]:
    """``(lower, upper, estimate)``: ``=`` is a point, ``>`` a lower bound only, ``<`` an upper bound only, ``~`` an
    estimate with no bound."""
    if qualifier == "=":
        return value, value, None
    if qualifier == ">":
        return value, None, None
    if qualifier == "<":
        return None, value, None
    return None, None, value


def reason_not_chartable(row: dict, *, metric: bool = True) -> str | None:
    """``None`` if the row may reach a chart, otherwise the first applicable entry of :data:`REASONS`.

    The curator's ``chartable`` flag is necessary and not sufficient: a tag S or U row, a never-charted kind, or an
    undisclosed valuation is refused even if the flag says true, so a mislabelled row cannot leak into a chart.
    ``metric=False`` is for events, which carry a value only sometimes. A row the curator did not mark chartable gets
    the row's own ``not_charted_code`` (``unclassified`` when it has none, or one outside :data:`NOT_CHARTED_CODES`).
    """
    kind = row.get("metric_kind")
    if kind in C.NEVER_CHARTED_KINDS:
        return "never_charted_kind"
    if row.get("tag") not in R.CHARTABLE_TAGS:
        return "unverified_tag"
    if kind == HEADLINE_KIND and row.get("basis") == "undisclosed":
        return "undisclosed_basis"
    if metric and not _positive(row.get("value")):
        return "invalid_value"
    if row.get("chartable") is True:
        return None
    if row.get("basis") == "reported":
        return "reported_basis"
    code = row.get("not_charted_code")
    return code if code in NOT_CHARTED_CODES else UNCLASSIFIED


def reason_group(reason: str, company_confirmed: bool) -> str:
    """One of :data:`REASON_GROUPS` for a non-chartable row: a never-charted kind first (a secondary mark or a
    third-party estimate is never charted, whoever confirmed it), then whether the company itself confirmed the figure,
    else it rests on a publication's report."""
    if reason == "never_charted_kind":
        return "never_charted_kind"
    return "company_confirmed_not_charted" if company_confirmed else "reported_by_a_publication"


def _ranked(rows: list[dict]) -> list[dict]:
    """Newest first by the date the fact describes, then the publication date; ties prefer ``=`` over a bound over
    ``~``, then the source URL, so a rerun picks the same row."""
    ordered = sorted(rows, key=lambda r: (_QRANK.get(r.get("qualifier"), 3), str(r.get("source_url", ""))))
    return sorted(ordered, key=lambda r: (str(r.get("as_of_date", "")), str(r.get("publish_date", ""))), reverse=True)


def _tied(ordered: list[dict]) -> bool:
    return len(ordered) > 1 and ordered[0].get("as_of_date") == ordered[1].get("as_of_date")


def _year(text: object) -> int | None:
    return int(text[:4]) if isinstance(text, str) and text[:4].isdigit() else None


def _one_year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def _need_company(name: str, rows: list[dict]) -> None:
    missing = [i for i, r in enumerate(rows) if _str(r.get("company")) is None]
    if missing:
        raise LedgerError(f"{name}: row(s) {missing[:5]} have no company")


def _iso(value: object, what: str, company: object) -> date:
    """A real calendar date (the schema only checks the shape, and 2026-02-30 has the shape)."""
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise LedgerError(f"events: {company}: {what} {value!r} is not a date") from exc


def _frame(recs: list[dict], dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(recs, schema=dtypes) if recs else pl.DataFrame(schema=dtypes)


def _validate(name: str, schema: pa.DataFrameSchema, df: pl.DataFrame) -> pl.DataFrame:
    try:
        return schema.validate(df)  # type: ignore[return-value]
    except pandera.errors.SchemaError as exc:
        raise LedgerError(f"{name} does not satisfy its schema: {str(exc)[:600]}") from exc


# --- schemas ----------------------------------------------------------------------------------------------------------
_DATE = pa.Column(str, pa.Check.str_matches(_DATE_RE))
_DATE_NULL = pa.Column(str, pa.Check.str_matches(_DATE_RE), nullable=True)
_URL = pa.Column(str, pa.Check.str_startswith("http"))
_URL_NULL = pa.Column(str, pa.Check.str_startswith("http"), nullable=True)
_Q = pa.Column(str, pa.Check.isin(list(R.QUALIFIERS)))
_Q_NULL = pa.Column(str, pa.Check.isin(list(R.QUALIFIERS)), nullable=True)
_TAG_CHARTED = pa.Column(str, pa.Check.isin(list(R.CHARTABLE_TAGS)))
_TAG_CHARTED_NULL = pa.Column(str, pa.Check.isin(list(R.CHARTABLE_TAGS)), nullable=True)
_TRUE = pa.Column(pl.Boolean, pa.Check.eq(True))
_LAYER = pa.Column(str, pa.Check.isin(list(C.LAYERS)))
_MARKET = pa.Column(str, pa.Check.isin(list(R.MARKETS)))
_BASIS = pa.Column(str, pa.Check.isin(list(C.BASIS)))
_BASIS_NULL = pa.Column(str, pa.Check.isin(list(C.BASIS)), nullable=True)
_CHARTED_KINDS = [k for k in C.METRIC_KINDS if k not in C.NEVER_CHARTED_KINDS]
_KIND_CHARTED = pa.Column(str, pa.Check.isin(_CHARTED_KINDS))
_KIND_CHARTED_NULL = pa.Column(str, pa.Check.isin(_CHARTED_KINDS), nullable=True)
_BOOL = pa.Column(pl.Boolean)
_BOOL_NULL = pa.Column(pl.Boolean, nullable=True)
_F = pa.Column(pl.Float64, nullable=True)
_FPOS = pa.Column(pl.Float64, pa.Check.gt(0), nullable=True)
_S = pa.Column(str, nullable=True)
_INT_NULL = pa.Column(pl.Int64, nullable=True)


def _interval_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    q = pl.col("qualifier")
    return d.lazyframe.select(
        pl.when(q == "=").then(pl.col("lower").is_not_null() & pl.col("upper").is_not_null())
        .when(q == ">").then(pl.col("lower").is_not_null() & pl.col("upper").is_null())
        .when(q == "<").then(pl.col("lower").is_null() & pl.col("upper").is_not_null())
        .otherwise(pl.col("estimate").is_not_null())
    )  # fmt: skip


# Timeline and latest: one chartable private_metrics row per line.
TIMELINE_KEY = ["company", "metric", "metric_kind", "period", "date", "status"]
TIMELINE_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "layer": pl.Utf8, "market": pl.Utf8, "date": pl.Utf8, "period": pl.Utf8, "metric": pl.Utf8,
    "metric_kind": pl.Utf8, "value": pl.Float64, "unit": pl.Utf8, "qualifier": pl.Utf8, "lower": pl.Float64,
    "upper": pl.Float64, "estimate": pl.Float64, "basis": pl.Utf8, "status": pl.Utf8, "tag": pl.Utf8,
    "chartable": pl.Boolean, "stale": pl.Boolean, "stale_mark": pl.Boolean, "vintage_year": pl.Int64,
    "company_confirmed": pl.Boolean, "definition": pl.Utf8, "source_name": pl.Utf8, "source_url": pl.Utf8,
    "publish_date": pl.Utf8, "last_checked": pl.Utf8, "caveat": pl.Utf8,
}  # fmt: skip
TIMELINE_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "layer": _LAYER, "market": _MARKET, "date": _DATE, "period": pa.Column(str),
        "metric": pa.Column(str), "metric_kind": _KIND_CHARTED, "value": pa.Column(pl.Float64, pa.Check.gt(0)),
        "unit": pa.Column(str), "qualifier": _Q, "lower": _F, "upper": _F, "estimate": _F, "basis": _BASIS,
        "status": pa.Column(str, pa.Check.isin(list(R.METRIC_STATUSES))), "tag": _TAG_CHARTED, "chartable": _TRUE,
        "stale": _BOOL, "stale_mark": _BOOL_NULL,
        "vintage_year": _INT_NULL,
        "company_confirmed": _BOOL, "definition": _S, "source_name": _S, "source_url": _URL, "publish_date": _DATE,
        "last_checked": _DATE, "caveat": _S,
    },
    unique=TIMELINE_KEY, strict=True, coerce=False,
    checks=[pa.Check(_interval_consistent, error="interval does not match its qualifier")],
)  # fmt: skip

LATEST_KEY = ["company", "metric", "metric_kind"]
LATEST_SCHEMA = pa.DataFrameSchema(
    dict(TIMELINE_SCHEMA.columns) | {"status": pa.Column(str, pa.Check.eq("current"))},
    unique=LATEST_KEY, strict=True, coerce=False, checks=TIMELINE_SCHEMA.checks,
)  # fmt: skip

# Everything that is not chartable, from either file, with the reason. Never charted.
REPORTED_KEY = ["source", "company", "metric", "metric_kind", "period", "date", "counterparty", "value", "source_url"]
REPORTED_DTYPES: dict[str, pl.DataType] = {
    "source": pl.Utf8, "company": pl.Utf8, "layer": pl.Utf8, "market": pl.Utf8, "date": pl.Utf8, "period": pl.Utf8,
    "metric": pl.Utf8, "metric_kind": pl.Utf8, "counterparty": pl.Utf8, "value": pl.Float64, "unit": pl.Utf8,
    "qualifier": pl.Utf8, "basis": pl.Utf8, "status": pl.Utf8, "tag": pl.Utf8, "reason": pl.Utf8,
    "reason_group": pl.Utf8, "reason_label": pl.Utf8, "company_confirmed": pl.Boolean, "source_name": pl.Utf8,
    "source_url": pl.Utf8, "publish_date": pl.Utf8, "caveat": pl.Utf8,
}  # fmt: skip


def _reason_group_follows_the_rule(d: pa.PolarsData) -> pl.LazyFrame:
    c = pl.col
    return d.lazyframe.select(
        c("reason_group")
        == pl.when(c("reason") == "never_charted_kind").then(pl.lit("never_charted_kind"))
        .when(c("company_confirmed")).then(pl.lit("company_confirmed_not_charted"))
        .otherwise(pl.lit("reported_by_a_publication"))
    )  # fmt: skip


REPORTED_SCHEMA = pa.DataFrameSchema(
    {
        "source": pa.Column(str, pa.Check.isin(["private_metrics", "events"])), "company": pa.Column(str),
        "layer": _LAYER, "market": _MARKET, "date": _DATE, "period": pa.Column(str), "metric": pa.Column(str),
        "metric_kind": pa.Column(str, pa.Check.isin(list(C.METRIC_KINDS)), nullable=True),
        "counterparty": pa.Column(str), "value": _F, "unit": _S, "qualifier": _Q_NULL, "basis": _BASIS_NULL,
        "status": _S, "tag": pa.Column(str, pa.Check.isin(list(R.TAGS))),
        "reason": pa.Column(str, pa.Check.isin(list(REASONS))),
        "reason_group": pa.Column(str, pa.Check.isin(list(REASON_GROUPS))), "reason_label": pa.Column(str),
        "company_confirmed": _BOOL,
        "source_name": _S, "source_url": _URL, "publish_date": _DATE, "caveat": _S,
    },
    unique=REPORTED_KEY, strict=True, coerce=False,
    checks=[pa.Check(lambda d: d.lazyframe.select(pl.col("value").is_null() == pl.col("qualifier").is_null()),
                     error="a value needs a qualifier"),
            pa.Check(_reason_group_follows_the_rule, error="reason_group disagrees with reason and company_confirmed"),
            pa.Check(lambda d: d.lazyframe.select(
                pl.col("reason_label") == pl.col("reason").replace_strict(REASON_LABELS, default=None)),
                error="reason_label is not the sentence of the reason")],
)  # fmt: skip

# Events: chartable only. The announced price and the price a filing records sit in two columns; the chart prefers
# the filing.
EVENTS_KEY = ["company", "event_kind", "event_date", "counterparty"]
EVENTS_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "layer": pl.Utf8, "market": pl.Utf8, "event_kind": pl.Utf8, "event_date": pl.Utf8,
    "closed_date": pl.Utf8, "counterparty": pl.Utf8, "buyer_type": pl.Utf8, "announced_value": pl.Float64,
    "unit": pl.Utf8, "qualifier": pl.Utf8, "metric_kind": pl.Utf8, "sec_value": pl.Float64,
    "sec_qualifier": pl.Utf8, "sec_source_url": pl.Utf8, "chart_value": pl.Float64, "chart_qualifier": pl.Utf8,
    "chart_basis": pl.Utf8, "chart_metric_kind": pl.Utf8, "deal_start": pl.Boolean, "status_after": pl.Utf8,
    "tag": pl.Utf8, "chartable": pl.Boolean, "stale": pl.Boolean, "company_confirmed": pl.Boolean,
    "as_of_date": pl.Utf8, "source_name": pl.Utf8, "source_url": pl.Utf8, "publish_date": pl.Utf8, "caveat": pl.Utf8,
}  # fmt: skip


def _event_prices_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    """The chart price is the amount a filing records if there is one, else the announced amount, each with its own
    qualifier."""
    c = pl.col
    chosen_value = pl.coalesce(c("sec_value"), c("announced_value"))
    chosen_qualifier = pl.when(c("sec_value").is_not_null()).then(c("sec_qualifier")).otherwise(c("qualifier"))
    chosen_basis = (
        pl.when(c("sec_value").is_not_null()).then(pl.lit("sec_filing"))
        .when(c("announced_value").is_not_null()).then(pl.lit("announced"))
        .otherwise(pl.lit(None, pl.Utf8))
    )  # fmt: skip
    return d.lazyframe.select(
        (c("sec_value").is_null() == c("sec_qualifier").is_null())
        & (c("announced_value").is_null() | c("qualifier").is_not_null())
        & c("chart_value").eq_missing(chosen_value)
        & c("chart_qualifier").eq_missing(chosen_qualifier)
        & c("chart_basis").eq_missing(chosen_basis)
    )


def _chart_metric_kind_follows_the_price(d: pa.PolarsData) -> pl.LazyFrame:
    """The metric kind of the price that is drawn: the filing's (``acquisition_price_sec``) when the filing price is
    drawn, the announcement row's own kind when the announced price is, none when nothing is."""
    c = pl.col
    drawn = (
        pl.when(c("sec_value").is_not_null()).then(pl.lit(SEC_KIND))
        .when(c("announced_value").is_not_null()).then(c("metric_kind"))
        .otherwise(pl.lit(None, pl.Utf8))
    )  # fmt: skip
    return d.lazyframe.select(c("chart_metric_kind").eq_missing(drawn))


def _deal_start_follows_the_rule(d: pa.PolarsData) -> pl.LazyFrame:
    """An announcement always opens a deal; a take-private opens one unless an announcement of the same company, day and
    counterparty is in the table (then it is the same deal and does not count again); an IPO, a round or a delisting is
    no deal. A closing may open a deal (seen alone) or attach to one, which the table alone cannot tell."""
    c = pl.col
    kind, twin = c("event_kind"), ["company", "event_date", "counterparty"]
    folded = (kind == "take_private") & (kind == "acquisition_announced").any().over(twin)
    return d.lazyframe.select(
        pl.when(kind == "acquisition_announced").then(c("deal_start"))
        .when(kind == "take_private").then(c("deal_start") == ~folded)
        .when(kind == DEAL_CLOSE_KIND).then(True)
        .otherwise(~c("deal_start"))
    )  # fmt: skip


EVENTS_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "layer": _LAYER, "market": _MARKET,
        "event_kind": pa.Column(str, pa.Check.isin(list(R.EVENT_KINDS))), "event_date": _DATE,
        "closed_date": _DATE_NULL, "counterparty": pa.Column(str),
        "buyer_type": pa.Column(str, pa.Check.isin(list(R.BUYER_TYPES)), nullable=True),
        "announced_value": _FPOS, "unit": _S, "qualifier": _Q_NULL, "metric_kind": _KIND_CHARTED_NULL,
        "sec_value": _FPOS, "sec_qualifier": _Q_NULL, "sec_source_url": _URL_NULL, "chart_value": _FPOS,
        "chart_qualifier": _Q_NULL,
        "chart_basis": pa.Column(str, pa.Check.isin(["sec_filing", "announced"]), nullable=True),
        "chart_metric_kind": _KIND_CHARTED_NULL, "deal_start": _BOOL,
        "status_after": pa.Column(str, pa.Check.isin(list(C.STATUS)), nullable=True), "tag": _TAG_CHARTED,
        "chartable": _TRUE, "stale": _BOOL, "company_confirmed": _BOOL, "as_of_date": _DATE, "source_name": _S,
        "source_url": _URL, "publish_date": _DATE, "caveat": _S,
    },
    unique=EVENTS_KEY, strict=True, coerce=False,
    checks=[
        pa.Check(_event_prices_consistent, error="announced price, SEC price and chart price disagree"),
        pa.Check(_chart_metric_kind_follows_the_price, error="chart_metric_kind is not the kind of the drawn price"),
        pa.Check(_deal_start_follows_the_rule, error="deal_start disagrees with the deal rule (a deal counts once)"),
        pa.Check(lambda d: d.lazyframe.select(
            pl.when(pl.col("event_kind").is_in(list(DEAL_START_KINDS) + [DEAL_CLOSE_KIND]))
            .then(pl.col("buyer_type").is_not_null()).otherwise(True)),
            error="an acquisition or take-private needs a buyer_type"),
    ],
)  # fmt: skip

# Private intervals: one row per company, three numbers and the Q6 reading. Chartable by construction: the headline and
# the ARR are chosen among chartable rows only, and the two derived intervals are tag C.
INTERVAL_KEY = ["company"]
INTERVAL_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "layer": pl.Utf8, "market": pl.Utf8, "name_cn": pl.Utf8, "l2": pl.Utf8,
    "directory_status": pl.Utf8, "in_directory": pl.Boolean,
    # 1. the headline: what a round reported
    "headline_metric": pl.Utf8, "headline_period": pl.Utf8, "headline_value": pl.Float64, "headline_unit": pl.Utf8,
    "headline_qualifier": pl.Utf8, "headline_basis": pl.Utf8, "headline_as_of": pl.Utf8,
    "headline_source_url": pl.Utf8, "headline_tag": pl.Utf8, "vintage_year": pl.Int64, "stale_mark": pl.Boolean,
    "company_confirmed": pl.Boolean, "headline_caveat": pl.Utf8, "headline_reason": pl.Utf8,
    # 2. the discount interval: an assumption applied to the headline
    "discount": pl.Float64, "discount_band": pl.Float64, "discount_lower": pl.Float64, "discount_upper": pl.Float64,
    "discount_unit": pl.Utf8, "discount_qualifier": pl.Utf8, "discount_basis": pl.Utf8, "discount_as_of": pl.Utf8,
    "discount_source_url": pl.Utf8, "discount_tag": pl.Utf8, "discount_scored": pl.Boolean,
    "discount_reason": pl.Utf8,
    # 3. the public-comparable interval: company-stated ARR times the comparables' EV/NTM revenue range
    "arr_value": pl.Float64, "arr_unit": pl.Utf8, "arr_qualifier": pl.Utf8, "arr_tag": pl.Utf8,
    "arr_company_confirmed": pl.Boolean, "multiple_low": pl.Float64, "multiple_high": pl.Float64,
    "multiple_low_ticker": pl.Utf8, "multiple_high_ticker": pl.Utf8, "comparable_tickers": pl.Utf8,
    "comparable_missing": pl.Utf8, "multiples_as_of": pl.Utf8, "comparable_lower": pl.Float64,
    "comparable_upper": pl.Float64, "comparable_unit": pl.Utf8, "comparable_qualifier": pl.Utf8,
    "comparable_basis": pl.Utf8, "comparable_as_of": pl.Utf8, "comparable_source_url": pl.Utf8,
    "comparable_tag": pl.Utf8, "comparable_scored": pl.Boolean, "comparable_reason": pl.Utf8,
    # the Q6 reading
    "above": pl.Boolean, "q6_scored": pl.Boolean, "above_reason": pl.Utf8, "above_note": pl.Utf8,
}  # fmt: skip


def _implies(antecedent: pl.Expr, consequent: pl.Expr) -> pl.Expr:
    return (~antecedent.fill_null(False)) | consequent.fill_null(False)


def _intervals_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    c = pl.col
    ordered = (
        (c("discount_lower").is_null() | c("discount_upper").is_null() | (c("discount_lower") <= c("discount_upper")))
        & (c("comparable_lower").is_null() | c("comparable_upper").is_null()
           | (c("comparable_lower") <= c("comparable_upper")))
    )  # fmt: skip
    flags = (
        ((c("discount_lower").is_not_null() & c("discount_upper").is_not_null()) == c("discount_scored"))
        & ((c("comparable_lower").is_not_null() & c("comparable_upper").is_not_null()) == c("comparable_scored"))
        & (c("q6_scored") == c("above").is_not_null())
    )  # fmt: skip
    why = (
        (c("discount_scored") | c("discount_reason").is_not_null())
        & (c("comparable_scored") | c("comparable_reason").is_not_null())
        & (c("headline_value").is_not_null() | c("headline_reason").is_not_null())
        & (c("above").is_not_null() | c("above_reason").is_not_null())
    )  # fmt: skip
    # Q6 is decided only on a disclosed headline of tag V or C, with both intervals scored, in one currency.
    decided = _implies(
        c("above").is_not_null(),
        c("discount_scored") & c("comparable_scored") & (c("headline_basis") == "disclosed")
        & c("headline_tag").is_in(list(R.CHARTABLE_TAGS)) & (c("headline_unit") == c("arr_unit")),
    )  # fmt: skip
    return d.lazyframe.select(ordered & flags & why & decided)


INTERVALS_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "layer": _LAYER, "market": _MARKET, "name_cn": _S, "l2": _S,
        "directory_status": pa.Column(str, pa.Check.isin(list(R.DIRECTORY_STATUSES)), nullable=True),
        "in_directory": _BOOL,
        "headline_metric": _S, "headline_period": _S, "headline_value": _FPOS, "headline_unit": _S,
        "headline_qualifier": _Q_NULL, "headline_basis": _BASIS_NULL, "headline_as_of": _DATE_NULL,
        "headline_source_url": _URL_NULL, "headline_tag": _TAG_CHARTED_NULL,
        "vintage_year": _INT_NULL,
        "stale_mark": _BOOL_NULL, "company_confirmed": _BOOL_NULL, "headline_caveat": _S, "headline_reason": _S,
        "discount": _F, "discount_band": _FPOS, "discount_lower": _FPOS, "discount_upper": _FPOS,
        "discount_unit": _S, "discount_qualifier": _Q_NULL, "discount_basis": _BASIS_NULL,
        "discount_as_of": _DATE_NULL, "discount_source_url": _URL_NULL,
        "discount_tag": pa.Column(str, pa.Check.eq("C"), nullable=True), "discount_scored": _BOOL,
        "discount_reason": _S,
        "arr_value": _FPOS, "arr_unit": _S, "arr_qualifier": _Q_NULL, "arr_tag": _TAG_CHARTED_NULL,
        "arr_company_confirmed": _BOOL_NULL, "multiple_low": _FPOS, "multiple_high": _FPOS,
        "multiple_low_ticker": _S, "multiple_high_ticker": _S, "comparable_tickers": _S, "comparable_missing": _S,
        "multiples_as_of": _DATE_NULL, "comparable_lower": _FPOS, "comparable_upper": _FPOS, "comparable_unit": _S,
        "comparable_qualifier": _Q_NULL, "comparable_basis": _BASIS_NULL, "comparable_as_of": _DATE_NULL,
        "comparable_source_url": _URL_NULL, "comparable_tag": pa.Column(str, pa.Check.eq("C"), nullable=True),
        "comparable_scored": _BOOL, "comparable_reason": _S,
        "above": _BOOL_NULL, "q6_scored": _BOOL, "above_reason": _S, "above_note": _S,
    },
    unique=INTERVAL_KEY, strict=True, coerce=False,
    checks=[pa.Check(_intervals_consistent, error="scored flags, reasons or the Q6 reading are inconsistent")],
)  # fmt: skip

SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "private_intervals": INTERVALS_SCHEMA,
    "ledger_timeline": TIMELINE_SCHEMA,
    "ledger_latest": LATEST_SCHEMA,
    "reported_unconfirmed": REPORTED_SCHEMA,
    "events": EVENTS_SCHEMA,
}
DTYPES: dict[str, dict[str, pl.DataType]] = {
    "private_intervals": INTERVAL_DTYPES,
    "ledger_timeline": TIMELINE_DTYPES,
    "ledger_latest": TIMELINE_DTYPES,
    "reported_unconfirmed": REPORTED_DTYPES,
    "events": EVENTS_DTYPES,
}


# --- metric rows ------------------------------------------------------------------------------------------------------
def _metric_rec(r: dict) -> dict:
    value = float(r["value"])
    lower, upper, estimate = implied_interval(value, r.get("qualifier"))  # type: ignore[arg-type]
    year, mark = r.get("vintage_year"), r.get("stale_mark")
    return {
        "company": r.get("company"), "layer": r.get("layer"), "market": r.get("market"), "date": r.get("as_of_date"),
        "period": _str(r.get("period")), "metric": r.get("metric"), "metric_kind": r.get("metric_kind"),
        "value": value, "unit": _str(r.get("unit")), "qualifier": r.get("qualifier"), "lower": lower,
        "upper": upper, "estimate": estimate, "basis": r.get("basis"), "status": r.get("status"),
        "tag": r.get("tag"), "chartable": True, "stale": bool(r.get("stale")),
        "stale_mark": mark if isinstance(mark, bool) else None,
        "vintage_year": year if isinstance(year, int) and not isinstance(year, bool) else None,
        "company_confirmed": bool(r.get("company_confirmed")), "definition": _str(r.get("definition")),
        "source_name": _str(r.get("source_name")), "source_url": r.get("source_url"),
        "publish_date": r.get("publish_date"), "last_checked": r.get("last_checked"), "caveat": _str(r.get("caveat")),
    }  # fmt: skip


def _reported_rec(source: str, r: dict, reason: str) -> dict:
    is_metric = source == "private_metrics"
    value, sec = r.get("value"), r.get("sec_value")
    if _positive(value):
        shown, qualifier = float(value), r.get("qualifier")
    elif not is_metric and _positive(sec):
        shown, qualifier = float(sec), r.get("sec_qualifier") if r.get("sec_qualifier") in R.QUALIFIERS else "="
    else:
        shown, qualifier = None, None
    return {
        "source": source, "company": r.get("company"), "layer": r.get("layer"), "market": r.get("market"),
        "date": r.get("as_of_date") if is_metric else r.get("event_date"),
        "period": _str(r.get("period")) or str(r.get("event_date", ""))[:7],
        "metric": r.get("metric") if is_metric else r.get("event_kind"), "metric_kind": r.get("metric_kind"),
        "counterparty": _str(r.get("counterparty")) or "", "value": shown, "unit": _str(r.get("unit")),
        "qualifier": qualifier, "basis": r.get("basis") if is_metric else None,
        "status": r.get("status") if is_metric else None, "tag": r.get("tag"),
        "reason": reason, "reason_group": reason_group(reason, bool(r.get("company_confirmed"))),
        "reason_label": REASON_LABELS[reason],
        "company_confirmed": bool(r.get("company_confirmed")),
        "source_name": _str(r.get("source_name")), "source_url": r.get("source_url"),
        "publish_date": r.get("publish_date"), "caveat": _str(r.get("caveat")),
    }  # fmt: skip


def _split_metrics(metrics: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(chartable rows, reported records)``: every published metric row lands in exactly one of them."""
    charted, reported = [], []
    for r in metrics:
        why = reason_not_chartable(r, metric=True)
        if why is None:
            charted.append(r)
        else:
            reported.append(_reported_rec("private_metrics", r, why))
    return charted, reported


def _dedupe_reported(recs: list[dict]) -> tuple[list[dict], int]:
    """``(records, n_dropped)``: a figure that both curated files hold (a round in ``events`` and the same round in
    ``private_metrics``) is listed once. The key is company, day, kind of figure, value and source page; a record with
    no value (an event that states none) is never merged. The ``private_metrics`` record is kept, because it carries the
    basis, status and definition the event row does not."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in sorted(recs, key=lambda r: r["source"] != "private_metrics"):  # stable: metrics first, order kept
        if r["value"] is not None:
            key = (r["company"], r["date"], r["metric_kind"], r["value"], r["source_url"])
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out, len(recs) - len(out)


def _latest_rows(charted: list[dict]) -> tuple[list[dict], int]:
    """Latest ``current`` chartable row per company, metric and kind, and how many groups tied on the date."""
    groups: dict[tuple, list[dict]] = {}
    for r in charted:
        if r.get("status") == "current":
            groups.setdefault((r.get("company"), r.get("metric"), r.get("metric_kind")), []).append(r)
    out, ties = [], 0
    for rows in groups.values():
        ordered = _ranked(rows)
        ties += _tied(ordered)
        out.append(ordered[0])
    return out, ties


# --- the three numbers ------------------------------------------------------------------------------------------------
def _clean_multiples(multiples: dict[str, float] | None) -> dict[str, float]:
    """Only a positive finite number is a multiple; a missing or unpriced comparable is left out, never zero-filled."""
    return {str(t): float(v) for t, v in (multiples or {}).items() if _positive(v)}


def _headline_block(h: dict | None, reason: str | None) -> dict:
    if h is None:
        return {k: None for k in (
            "headline_metric", "headline_period", "headline_value", "headline_unit", "headline_qualifier",
            "headline_basis", "headline_as_of", "headline_source_url", "headline_tag", "vintage_year", "stale_mark",
            "company_confirmed", "headline_caveat")} | {"headline_reason": reason}  # fmt: skip
    year, mark = h.get("vintage_year"), h.get("stale_mark")
    return {
        "headline_metric": h.get("metric"), "headline_period": _str(h.get("period")),
        "headline_value": float(h["value"]), "headline_unit": _str(h.get("unit")),
        "headline_qualifier": h.get("qualifier"), "headline_basis": h.get("basis"),
        "headline_as_of": h.get("as_of_date"), "headline_source_url": h.get("source_url"),
        "headline_tag": h.get("tag"),
        "vintage_year": year if isinstance(year, int) and not isinstance(year, bool) else None,
        "stale_mark": mark if isinstance(mark, bool) else None,
        "company_confirmed": bool(h.get("company_confirmed")), "headline_caveat": _str(h.get("caveat")),
        "headline_reason": None,
    }  # fmt: skip


def _discount_block(h: dict | None, headline_reason: str | None) -> dict:
    """The headline times ``1 + d -/+ band``. Both ends scale by a positive factor, so the interval keeps the headline's
    qualifier: a headline that is only a lower bound gives an interval of lower bounds."""
    empty = {k: None for k in (
        "discount", "discount_band", "discount_lower", "discount_upper", "discount_unit", "discount_qualifier",
        "discount_basis", "discount_as_of", "discount_source_url", "discount_tag")}  # fmt: skip
    if h is None:
        return empty | {"discount_scored": False, "discount_reason": headline_reason}
    year = h.get("vintage_year")
    if not isinstance(year, int) or isinstance(year, bool):
        return empty | {"discount_scored": False, "discount_reason": "the valuation row has no vintage year"}
    if year not in DISCOUNTS:
        covered = ", ".join(str(y) for y in sorted(DISCOUNTS))
        return empty | {"discount_scored": False,
                        "discount_reason": f"round year {year} is not in the discount table ({covered})"}  # fmt: skip
    d = DISCOUNTS[year]
    low_factor, high_factor = 1.0 + d - BAND, 1.0 + d + BAND
    if low_factor <= 0:
        return empty | {"discount": d, "discount_band": BAND, "discount_scored": False,
                        "discount_reason": "the discount interval would not be positive"}  # fmt: skip
    value = float(h["value"])
    return {
        "discount": d, "discount_band": BAND, "discount_lower": _r(value * low_factor),
        "discount_upper": _r(value * high_factor), "discount_unit": _str(h.get("unit")),
        "discount_qualifier": h.get("qualifier"), "discount_basis": h.get("basis"),
        "discount_as_of": h.get("as_of_date"), "discount_source_url": h.get("source_url"), "discount_tag": "C",
        "discount_scored": True, "discount_reason": None,
    }  # fmt: skip


def _comparable_block(layer: str, arr: dict | None, multiples: dict[str, float], multiples_as_of: str | None) -> dict:
    """ARR as given times the lowest and highest EV/NTM revenue among the layer's registered comparables that are
    present in ``multiples``."""
    empty = {k: None for k in (
        "arr_value", "arr_unit", "arr_qualifier", "arr_tag", "arr_company_confirmed", "multiple_low",
        "multiple_high", "multiple_low_ticker", "multiple_high_ticker", "comparable_tickers", "comparable_missing",
        "multiples_as_of", "comparable_lower", "comparable_upper", "comparable_unit", "comparable_qualifier",
        "comparable_basis", "comparable_as_of", "comparable_source_url", "comparable_tag")}  # fmt: skip
    if arr is not None:
        empty |= {
            "arr_value": float(arr["value"]), "arr_unit": _str(arr.get("unit")), "arr_qualifier": arr.get("qualifier"),
            "arr_tag": arr.get("tag"), "arr_company_confirmed": bool(arr.get("company_confirmed")),
        }  # fmt: skip
    expected = C.COMPARABLES_BY_LAYER.get(layer)
    if expected is None:
        return empty | {"comparable_scored": False,
                        "comparable_reason": f"layer {layer} has no registered public comparables"}  # fmt: skip
    if arr is None:
        return empty | {"comparable_scored": False, "comparable_reason": "no current chartable company-stated ARR"}
    present = {t: multiples[t] for t in expected if t in multiples}
    if not present:
        none_priced = f"none of the comparables ({', '.join(expected)}) has a multiple in this run"
        return empty | {"comparable_scored": False, "comparable_reason": none_priced}
    low_t, high_t = min(present, key=present.__getitem__), max(present, key=present.__getitem__)
    arr_value = float(arr["value"])
    missing = [t for t in expected if t not in present]
    return empty | {
        "multiple_low": present[low_t], "multiple_high": present[high_t], "multiple_low_ticker": low_t,
        "multiple_high_ticker": high_t, "comparable_tickers": ", ".join(present),
        "comparable_missing": ", ".join(missing) or None, "multiples_as_of": multiples_as_of,
        "comparable_lower": _r(arr_value * present[low_t]), "comparable_upper": _r(arr_value * present[high_t]),
        "comparable_unit": _str(arr.get("unit")), "comparable_qualifier": arr.get("qualifier"),
        "comparable_basis": arr.get("basis"), "comparable_as_of": arr.get("as_of_date"),
        "comparable_source_url": arr.get("source_url"), "comparable_tag": "C", "comparable_scored": True,
        "comparable_reason": None,
    }  # fmt: skip


def _above_block(h: dict | None, headline_reason: str, disc: dict, comp: dict) -> dict:
    """The Q6 reading. ``above`` is decided only if every condition of the rule holds; otherwise it says why not."""

    def undecided(why: str) -> dict:
        return {"above": None, "q6_scored": False, "above_reason": why, "above_note": None}

    if h is None:
        return undecided(headline_reason)
    if not disc["discount_scored"]:
        return undecided(f"discount interval not scored: {disc['discount_reason']}")
    if not comp["comparable_scored"]:
        return undecided(f"comparable interval not scored: {comp['comparable_reason']}")
    if h.get("tag") not in R.CHARTABLE_TAGS:  # unreachable while the headline is chosen among chartable rows
        return undecided(f"headline tag is {h.get('tag')}, not V or C")
    if h.get("basis") != "disclosed":
        return undecided(f"headline basis is {h.get('basis')}, not disclosed")
    if disc["discount_unit"] != comp["comparable_unit"]:
        return undecided(
            f"headline is in {disc['discount_unit']} and ARR in {comp['comparable_unit']}; "
            "a currency is never converted"
        )
    notes = []
    if disc["discount_qualifier"] == "<":
        notes.append("the headline is an upper bound, so the discount interval may lie lower")
    if comp["comparable_qualifier"] == ">":
        notes.append("the ARR is a lower bound, so the comparable interval may reach higher")
    if "~" in (disc["discount_qualifier"], comp["comparable_qualifier"]):
        notes.append("an approximate figure is used at face value")
    return {
        "above": bool(disc["discount_lower"] > comp["comparable_upper"]), "q6_scored": True, "above_reason": None,
        "above_note": "; ".join(notes) or None,
    }  # fmt: skip


def _company_rows(
    directory: dict[str, dict], metrics: list[dict], multiples: dict[str, float], multiples_as_of: str | None
) -> tuple[list[dict], dict[str, Any]]:
    """One ``private_intervals`` record per company in the directory or the ledger, plus the facts the checks report."""
    by_company: dict[str, list[dict]] = {}
    for r in metrics:
        by_company.setdefault(r["company"], []).append(r)
    diag: dict[str, Any] = {
        k: []
        for k in ("orphans", "layer_disagreements", "multiple_headlines", "vintage_mismatch", "missing_comparables")
    }
    recs = []
    for company in sorted(set(directory) | set(by_company)):
        rows, d = by_company.get(company, []), directory.get(company)
        layer = d.get("layer") if d else rows[0].get("layer")
        market = d.get("market") if d else rows[0].get("market")
        if d is None:
            diag["orphans"].append(company)
        if d and any(r.get("layer") != d.get("layer") for r in rows):
            diag["layer_disagreements"].append(company)
        live = [r for r in rows if r.get("status") == "current" and reason_not_chartable(r) is None]
        heads = _ranked([r for r in live if r.get("metric_kind") == HEADLINE_KIND])
        arrs = _ranked([r for r in live if r.get("metric_kind") == ARR_KIND])
        h, arr = (heads[0] if heads else None), (arrs[0] if arrs else None)
        if len(heads) > 1:
            diag["multiple_headlines"].append(company)
        if h is not None and isinstance(h.get("vintage_year"), int) and h["vintage_year"] != _year(h.get("as_of_date")):
            diag["vintage_mismatch"].append(company)
        why = "no current chartable primary-round valuation"
        disc, comp = _discount_block(h, why), _comparable_block(layer, arr, multiples, multiples_as_of)
        if comp["comparable_missing"]:
            diag["missing_comparables"].append(f"{company}: {comp['comparable_missing']}")
        recs.append(
            {
                "company": company, "layer": layer, "market": market,
                "name_cn": _str(d.get("name_cn")) if d else None, "l2": _str(d.get("l2")) if d else None,
                "directory_status": _str(d.get("status")) if d else None, "in_directory": d is not None,
                **_headline_block(h, why), **disc, **comp, **_above_block(h, why, disc, comp),
            }
        )  # fmt: skip
    return recs, diag


# --- events -----------------------------------------------------------------------------------------------------------
def _sec_amount(r: dict) -> tuple[float | None, str | None, bool]:
    """``(sec_value, qualifier, invalid)``: the amount a filing records, from ``sec_value`` or from a row whose kind is
    ``acquisition_price_sec``. A filing states an amount, so a ``sec_value`` is ``=`` unless the row says otherwise."""
    raw = r.get("sec_value")
    if raw is not None:
        ok = _positive(raw) and bool(_str(r.get("unit")))
        q = r.get("sec_qualifier")
        return (float(raw), q if q in R.QUALIFIERS else "=", False) if ok else (None, None, True)
    if r.get("metric_kind") == SEC_KIND and _positive(r.get("value")):
        return float(r["value"]), r.get("qualifier"), False
    return None, None, False


def _event_rec(r: dict) -> dict:
    _iso(r.get("event_date"), "event_date", r.get("company"))
    if _str(r.get("closed_date")) is not None:
        _iso(r.get("closed_date"), "closed_date", r.get("company"))
    value = float(r["value"]) if _positive(r.get("value")) else None
    sec, sec_q, _ = _sec_amount(r)
    announced = None if r.get("metric_kind") == SEC_KIND else value
    if sec is not None:  # the chart prefers the amount a filing records
        chart, chart_q, chart_basis = sec, sec_q, "sec_filing"
    elif announced is not None:
        chart, chart_q, chart_basis = announced, r.get("qualifier"), "announced"
    else:
        chart, chart_q, chart_basis = None, None, None
    priced = announced is not None or sec is not None
    sec_url = _str(r.get("sec_source_url")) or (r.get("source_url") if "sec.gov" in str(r.get("source_url")) else None)
    return {
        "company": r.get("company"), "layer": r.get("layer"), "market": r.get("market"),
        "event_kind": r.get("event_kind"), "event_date": r.get("event_date"), "closed_date": _str(r.get("closed_date")),
        "counterparty": _str(r.get("counterparty")) or "", "buyer_type": _str(r.get("buyer_type")),
        "announced_value": announced, "unit": _str(r.get("unit")) if priced else None,
        "qualifier": r.get("qualifier") if announced is not None else None, "metric_kind": r.get("metric_kind"),
        "sec_value": sec, "sec_qualifier": sec_q, "sec_source_url": sec_url if sec is not None else None,
        "chart_value": chart, "chart_qualifier": chart_q, "chart_basis": chart_basis,
        "chart_metric_kind": SEC_KIND if chart_basis == "sec_filing" else r.get("metric_kind") if chart_basis else None,
        "deal_start": False,  # set once every event is known: :func:`_mark_deal_starts`
        "status_after": _str(r.get("status_after")), "tag": r.get("tag"), "chartable": True,
        "stale": bool(r.get("stale")), "company_confirmed": bool(r.get("company_confirmed")),
        "as_of_date": r.get("as_of_date"), "source_name": _str(r.get("source_name")), "source_url": r.get("source_url"),
        "publish_date": r.get("publish_date"), "caveat": _str(r.get("caveat")),
    }  # fmt: skip


def _party(e: dict) -> str:
    """The counterparty as the events mart keeps it (``''`` for none), compared without case or outer spaces."""
    return (_str(e.get("counterparty")) or "").strip().casefold()


def _deal_groups(events: list[dict]) -> list[dict]:
    """The deals of ``events`` with the events that make each up: ``company``, ``start``, ``closed``, ``opener`` (the
    event that opens the deal) and ``members`` (the opener, a folded take-private and an attached closing, in order).

    Per company, in date order, a closing attaches to the open announcement before it; a closing with none, or a
    take-private, stands alone. A take-private that repeats an announcement (same company, same day, same counterparty:
    one deal entered under both kinds) is folded into that announcement and is not a second deal.
    """
    out: list[dict] = []
    order = {k: i for i, k in enumerate((*DEAL_START_KINDS, DEAL_CLOSE_KIND))}
    wanted = [e for e in events if e.get("event_kind") in order]
    open_deal: dict[str, dict] = {}
    announced: dict[tuple[str, str, str], dict] = {}
    for e in sorted(wanted, key=lambda e: (e["event_date"], order[e["event_kind"]], str(e.get("counterparty")))):
        company, kind = e["company"], e["event_kind"]
        if kind == DEAL_CLOSE_KIND:
            deal = open_deal.pop(company, None)
            if deal is None:
                out.append({"company": company, "start": e["event_date"], "closed": e["event_date"], "opener": e,
                            "members": [e]})  # fmt: skip
            else:
                deal["closed"] = e["event_date"]
                deal["members"].append(e)
            continue
        twin = announced.get((company, e["event_date"], _party(e))) if kind == "take_private" else None
        if twin is not None:
            twin["members"].append(e)
            twin["closed"] = twin["closed"] or _str(e.get("closed_date"))
            continue
        deal = {"company": company, "start": e["event_date"], "closed": _str(e.get("closed_date")), "opener": e,
                "members": [e]}  # fmt: skip
        out.append(deal)
        if kind != "take_private":
            open_deal[company] = deal
            announced.setdefault((company, e["event_date"], _party(e)), deal)
    return out


def deals(events: list[dict]) -> list[dict]:
    """Acquisitions as deals, not events: an announcement and its closing are one deal, and a take-private that repeats
    an announcement (same company, same day, same counterparty) is the same deal.

    Per company, in date order, a closing attaches to the open announcement before it; a closing with none, or a
    take-private with no such announcement, stands alone. ``start`` is the announcement date (the closing date for a
    closing seen alone) and ``closed`` the closing date: the closing event's date, or the ``closed_date`` an
    announcement or the take-private folded into it carries.
    """
    return [{k: d[k] for k in ("company", "start", "closed")} for d in _deal_groups(events)]


def _event_key(e: dict) -> tuple[str, str, str, str]:
    return (e["company"], e["event_kind"], e["event_date"], _str(e.get("counterparty")) or "")


def _mark_deal_starts(recs: list[dict]) -> list[dict]:
    """``recs`` (event records) with ``deal_start`` true on the event that opens each deal :func:`deals` counts."""
    openers = {_event_key(d["opener"]) for d in _deal_groups(recs)}
    return [{**r, "deal_start": _event_key(r) in openers} for r in recs]


# --- summary ----------------------------------------------------------------------------------------------------------
def _valuation_max(rows: list[dict]) -> dict | None:
    """The highest headline among ``rows`` (all in one currency); an exact figure beats a bound at the same value."""
    if not rows:
        return None
    top = sorted(rows, key=lambda r: (-r["headline_value"], _QRANK.get(r["headline_qualifier"], 3), r["company"]))[0]
    return {
        "company": top["company"], "value": top["headline_value"], "unit": top["headline_unit"],
        "qualifier": top["headline_qualifier"], "basis": top["headline_basis"], "period": top["headline_period"],
        "as_of": top["headline_as_of"], "source_url": top["headline_source_url"], "tag": top["headline_tag"],
        "metric": top["headline_metric"], "vintage_year": top["vintage_year"], "stale_mark": top["stale_mark"],
        "company_confirmed": top["company_confirmed"], "caveat": top["headline_caveat"],
    }  # fmt: skip


def _months_before(older: date, newer: date) -> int:
    """Whole calendar months from ``older`` to ``newer`` (0 if ``older`` is not earlier)."""
    months = (newer.year - older.year) * 12 + newer.month - older.month - (1 if newer.day < older.day else 0)
    return max(months, 0)


def q6_basis(intervals: list[dict]) -> dict[str, Any]:
    """What the Q6 comparison rests on, for the sub-line under the interval figure: for every company whose comparable
    interval is scored, how many comparables stand behind it (:data:`THIN_COMPARABLES`) and how old its ARR is against
    the multiples it is multiplied by (:data:`OLD_ARR_MONTHS`). It describes the comparison and never changes it:
    ``above`` is the registered rule at face value."""
    rows = []
    for r in intervals:
        if not r["comparable_scored"]:
            continue
        n = len([t for t in (r["comparable_tickers"] or "").split(",") if t.strip()])
        arr_on, mult_on = r["comparable_as_of"], r["multiples_as_of"]
        months = _months_before(date.fromisoformat(arr_on), date.fromisoformat(mult_on)) if arr_on and mult_on else None
        old = bool(arr_on and mult_on and date.fromisoformat(arr_on) < _one_year_before(date.fromisoformat(mult_on)))
        rows.append({
            "company": r["company"], "layer": r["layer"], "scored": bool(r["q6_scored"]), "above": r["above"],
            "n_comparables": n, "thin": n < THIN_COMPARABLES, "arr_as_of": arr_on, "arr_months_old": months,
            "arr_old": old,
        })  # fmt: skip
    scored = [x for x in rows if x["scored"]]
    return {
        "thin_below": THIN_COMPARABLES, "old_arr_months": OLD_ARR_MONTHS, "companies": rows,
        "n_thin": sum(1 for x in scored if x["thin"]), "n_old_arr": sum(1 for x in scored if x["arr_old"]),
        "thin_layers": sorted({x["layer"] for x in rows if x["thin"]}),
    }  # fmt: skip


def summarise(
    directory: list[dict],
    metrics: list[dict],
    tables: dict[str, pl.DataFrame],
    *,
    today: date | None,
    multiples_as_of: str | None,
    reported_duplicates: int = 0,
) -> dict[str, Any]:
    """The counts and the named figures that become ``facts.private`` and ``facts.events``.

    ``private``: ``n_directory`` companies in the directory; ``n_companies`` rows of ``private_intervals`` (directory
    plus any ledger company missing from it); ``n_ledger_rows`` published metric rows; ``n_chartable`` of them in
    ``ledger_timeline``; ``n_reported_unconfirmed`` rows in ``reported_unconfirmed`` (metric rows and events);
    ``n_undisclosed`` metric rows whose basis is ``undisclosed``; ``n_stale`` headlines whose ``stale_mark`` is true
    (a newer round exists or none for two years) and ``n_recheck_overdue`` metric rows not re-opened within the
    curation limit; ``n_with_arr`` companies with a chartable current company-stated ARR; ``n_with_headline``,
    ``n_discount_scored``, ``n_comparable_scored`` companies for which that number exists; ``n_q6_scored`` those on
    which ``above`` is decided and ``n_q6_above`` those where it is true; ``by_layer`` directory companies in each of
    the twelve layers (zeros kept); ``latest_valuation_max`` the highest headline in dollars (``HEADLINE_UNIT``) with
    its qualifier, basis, period, as_of, source_url, tag, vintage year and stale mark, and
    ``latest_valuation_max_by_unit`` the same per currency, because a currency is never converted.
    Counts for prose that must not be computed from a mart: ``n_china_rows`` curated metric rows of Chinese companies
    (every status, chartable or not), ``n_china_headline_rounds`` Chinese companies with a headline valuation,
    ``n_comparable_layers`` layers with registered public comparables, ``n_stale_ledger_rows`` rows of the ledger
    (``ledger_timeline``) that are not superseded and are stale (a stale mark or a re-check overdue; ``n_stale`` counts
    headline rounds only) and ``n_reported_by_group`` the rows of ``reported_unconfirmed`` in each of
    :data:`REASON_GROUPS`. ``n_reported_by_reason`` counts them by closed reason code (:data:`REASONS`, zeros kept),
    ``n_reported_duplicates`` the rows left out because both curated files hold the same figure, and ``q6`` is
    :func:`q6_basis`: the thin comparable sets and the old ARR dates behind the comparison.

    ``events`` (chartable events only: a search-extract or reported-talks event is a row of ``reported_unconfirmed``,
    not a number on the page): ``n``, ``n_unconfirmed``, ``by_kind`` and ``by_buyer_type`` (every vocabulary entry,
    zeros kept), ``acquisitions_12m`` deals that start in the 12 months to ``today`` (:func:`deals`: an announcement and
    its closing are one deal, and a take-private that repeats an announcement is the same deal; it is the number of
    ``deal_start`` events in the window) and, of those, ``acquisitions_announced_12m`` dated by their announcement
    (or take-private) and ``acquisitions_first_recorded_at_completion_12m`` dated by their completion because the
    ledger holds no announcement of them; ``acquisitions_12m_by_buyer_type`` splits the same deals by the buyer type of
    the row that opens them. ``acquisitions_not_charted_12m`` is the companies with an acquisition in the window that is
    in scope and recorded but kept off the charts (the price is not chartable), so it is not one of those deals.
    ``acquisitions_closed_12m`` is the deals closed in the window, whenever announced, and
    ``acquisitions_closed_12m_announced_earlier`` those of them that started before it. ``n_sec_priced`` is events with
    a filing price, ``n_sec_priced_deals`` the deals that have one and ``n_sec_priced_deals_12m`` those of them that
    start in the window. Also the window dates (``window_from``, and ``window_to`` which ``window_end`` repeats under
    the name the page uses), and ``latest`` the newest event.

    ``today`` (an argument, never the clock) ends the window.
    """
    intervals, timeline, reported, ev = (
        tables["private_intervals"],
        tables["ledger_timeline"],
        tables["reported_unconfirmed"],
        tables["events"],
    )
    rows = intervals.to_dicts()
    heads = [r for r in rows if r["headline_value"] is not None]
    by_unit = {u: _valuation_max([r for r in heads if r["headline_unit"] == u])
               for u in sorted({r["headline_unit"] for r in heads})}  # fmt: skip
    layers = Counter(d.get("layer") for d in directory)
    private = {
        "n_directory": len(directory), "n_companies": len(rows), "n_ledger_rows": len(metrics),
        "n_chartable": timeline.height, "n_reported_unconfirmed": reported.height,
        "n_undisclosed": sum(1 for r in metrics if r.get("basis") == "undisclosed"),
        "n_stale": sum(1 for r in heads if r["stale_mark"]),
        "n_recheck_overdue": sum(1 for r in metrics if r.get("stale")),
        "n_with_arr": sum(1 for r in rows if r["arr_value"] is not None), "n_with_headline": len(heads),
        "n_discount_scored": sum(1 for r in rows if r["discount_scored"]),
        "n_comparable_scored": sum(1 for r in rows if r["comparable_scored"]),
        "n_q6_scored": sum(1 for r in rows if r["q6_scored"]), "n_q6_above": sum(1 for r in rows if r["above"]),
        "by_layer": {layer: layers.get(layer, 0) for layer in C.LAYERS},
        "latest_valuation_max": by_unit.get(HEADLINE_UNIT), "latest_valuation_max_by_unit": by_unit,
        "multiples_as_of": multiples_as_of,
        "n_china_rows": sum(1 for r in metrics if r.get("market") == "china"),
        "n_china_headline_rounds": sum(1 for r in heads if r["market"] == "china"),
        "n_comparable_layers": len(C.COMPARABLES_BY_LAYER),
        "n_stale_ledger_rows": sum(
            1 for r in timeline.iter_rows(named=True)
            if r["status"] != "superseded" and (r["stale"] or r["stale_mark"])),
        "n_reported_by_group": {g: int((reported["reason_group"] == g).sum()) for g in REASON_GROUPS},
        "n_reported_by_reason": {k: int((reported["reason"] == k).sum()) for k in REASONS},
        "n_reported_duplicates": reported_duplicates,
        "q6": q6_basis(rows),
    }  # fmt: skip

    charted = ev.to_dicts()
    kinds = Counter(e["event_kind"] for e in charted)
    buyers = Counter(e["buyer_type"] for e in charted if e["buyer_type"])
    start = _one_year_before(today) if today else None
    groups = _deal_groups(charted)
    all_deals = [{k: d[k] for k in ("company", "start", "closed")} for d in groups]

    def inside(day: str | None) -> bool:
        return day is not None and start is not None and today is not None and start < date.fromisoformat(day) <= today

    opened = [d for d in groups if inside(d["start"])]
    counted_companies = {d["company"] for d in opened}
    off_the_charts = {
        r["company"]
        for r in reported.filter(pl.col("source") == "events").to_dicts()
        if r["metric"] in (*DEAL_START_KINDS, DEAL_CLOSE_KIND)
        and r["reason"] != "outside_scope"
        and inside(r["date"])
        and r["company"] not in counted_companies
    }
    by_deal_buyer = Counter(
        next((m["buyer_type"] for m in d["members"] if m["buyer_type"]), None) or "none" for d in opened
    )
    sec_priced_deals = [d for d in groups if any(m["sec_value"] is not None for m in d["members"])]

    newest = max(charted, key=lambda e: (e["event_date"], e["company"])) if charted else None
    events = {
        "n": len(charted), "n_unconfirmed": reported.filter(pl.col("source") == "events").height,
        "by_kind": {k: kinds.get(k, 0) for k in R.EVENT_KINDS},
        "by_buyer_type": {b: buyers.get(b, 0) for b in R.BUYER_TYPES},
        "acquisitions_12m": len(opened),
        "acquisitions_announced_12m": sum(1 for d in opened if d["opener"]["event_kind"] in DEAL_START_KINDS),
        "acquisitions_first_recorded_at_completion_12m": sum(
            1 for d in opened if d["opener"]["event_kind"] == DEAL_CLOSE_KIND),
        "acquisitions_12m_by_buyer_type": {b: by_deal_buyer.get(b, 0) for b in R.BUYER_TYPES},
        "acquisitions_not_charted_12m": len(off_the_charts),
        "acquisitions_closed_12m": sum(1 for d in all_deals if inside(d["closed"])),
        "acquisitions_closed_12m_announced_earlier": sum(
            1 for d in all_deals if inside(d["closed"]) and not inside(d["start"])),
        "n_sec_priced": sum(1 for e in charted if e["sec_value"] is not None),
        "n_sec_priced_deals": len(sec_priced_deals),
        "n_sec_priced_deals_12m": sum(1 for d in sec_priced_deals if inside(d["start"])),
        "window_from": start.isoformat() if start else None, "window_to": today.isoformat() if today else None,
        "window_end": today.isoformat() if today else None,
        "latest": None if newest is None else {k: newest[k] for k in (
            "company", "event_kind", "event_date", "counterparty", "buyer_type", "chart_value", "chart_qualifier",
            "chart_basis", "chart_metric_kind", "unit", "source_url", "status_after")},
    }  # fmt: skip
    return {"as_of": today.isoformat() if today else None, "private": private, "events": events}


# --- checks -----------------------------------------------------------------------------------------------------------
def charted_problems(tables: dict[str, pl.DataFrame]) -> list[str]:
    """Why a chartable mart is not chartable: a tag other than V or C, a never-charted kind, an unmarked row.

    Run it on the tables before they are written; ``build`` runs it already and records the result as a check.
    """
    out: list[str] = []
    for name in CHARTED_MARTS:
        df = tables.get(name)
        if df is None or df.is_empty():
            continue
        for column, bad in (("tag", lambda c: ~c.is_in(list(R.CHARTABLE_TAGS))),
                            ("headline_tag", lambda c: c.is_not_null() & ~c.is_in(list(R.CHARTABLE_TAGS))),
                            ("arr_tag", lambda c: c.is_not_null() & ~c.is_in(list(R.CHARTABLE_TAGS))),
                            ("metric_kind", lambda c: c.is_in(C.NEVER_CHARTED_KINDS)),
                            ("chartable", lambda c: ~c)):  # fmt: skip
            if column in df.columns:
                n = df.filter(bad(pl.col(column))).height
                if n:
                    out.append(f"{name}: {n} row(s) with {column} that may not be charted")
    return out


def _build_checks(
    metrics: list[dict], events: list[dict], tables: dict[str, pl.DataFrame], summary: dict[str, Any],
    diag: dict[str, Any], ties: int,
) -> list[dict]:  # fmt: skip
    private, ev = summary["private"], summary["events"]
    timeline, reported = tables["ledger_timeline"], tables["reported_unconfirmed"]
    from_metrics = reported.filter(pl.col("source") == "private_metrics").height
    problems = charted_problems(tables)
    leaked = [c for df in tables.values() for c in df.columns if "curator_notes" in c]
    leaked += [k for k in (*summary, *private, *ev) if "curator_notes" in k]
    invalid_sec = sum(1 for e in events if _sec_amount(e)[2])
    unclassified = sorted(
        {f"{r['company']} {r['date']}" for r in reported.filter(pl.col("reason") == UNCLASSIFIED).to_dicts()}
    )
    overdue = private["n_recheck_overdue"]
    return [
        check("Private ledger", private["n_directory"] > 0,
              f"{private['n_directory']} companies in the directory, {private['n_ledger_rows']} metric rows, "
              f"{ev['n']} chartable events" if private["n_directory"] else "the ring C directory is empty",
              warn=True),
        check("Ledger rows accounted for", timeline.height + from_metrics == len(metrics),
              f"{timeline.height} chartable + {from_metrics} kept off the charts = {len(metrics)} metric rows"),
        check("Never-charted kinds kept out", not problems,
              "no tag S/U row, never-charted kind or unmarked row in a chartable mart" if not problems
              else "; ".join(problems)),
        check("No private notes in output", not leaked,
              "no private-notes column or key in any table or the summary" if not leaked else ", ".join(leaked)),
        check("Every unpublished row has a reason", not unclassified,
              "every row kept off the charts carries a known reason code" if not unclassified
              else f"{len(unclassified)} row(s) with no known not_charted_code: " + ", ".join(unclassified[:5])),
        check("Ring C ledger matches directory", not diag["orphans"] and not diag["layer_disagreements"],
              "every ledger company is in the directory in the same layer" if not (
                  diag["orphans"] or diag["layer_disagreements"])
              else f"not in the directory: {diag['orphans'] or 'none'}; layer differs: "
                   f"{diag['layer_disagreements'] or 'none'}", warn=not diag["layer_disagreements"]),
        check("One headline per company", not diag["multiple_headlines"],
              "one current primary-round valuation per company" if not diag["multiple_headlines"]
              else "more than one current valuation for " + ", ".join(diag["multiple_headlines"]), warn=True),
        check("Round year matches vintage year", not diag["vintage_mismatch"],
              "every headline's vintage year is the year of its date" if not diag["vintage_mismatch"]
              else "differs for " + ", ".join(diag["vintage_mismatch"]), warn=True),
        check("Latest-value ties", ties == 0, f"{ties} company-metric(s) with two current rows on the same date",
              warn=True),
        check("Public comparables present", not diag["missing_comparables"],
              "every registered comparable has a multiple in this run" if not diag["missing_comparables"]
              else "missing: " + "; ".join(diag["missing_comparables"]), warn=True),
        check("Event SEC prices", invalid_sec == 0,
              f"{invalid_sec} event(s) with a sec_value that is not a positive number in a stated currency"),
        check("Private ledger freshness", overdue == 0,
              f"{overdue} metric row(s) unchecked for more than {R.SPEC.stale_days} days", warn=True),
        check("Q6 inputs", private["n_q6_scored"] >= int(C.Q6["min_scored"]),
              f"{private['n_q6_scored']} of {private['n_companies']} companies can be scored, "
              f"{private['n_q6_above']} above (Q6 needs {C.Q6['min_scored']} scored)", warn=True),
    ]  # fmt: skip


# --- the entry point --------------------------------------------------------------------------------------------------
def _fallback_today(*row_lists: list[dict]) -> date | None:
    """With no ``today``, the day the curator last opened a page: deterministic, derived from the inputs."""
    days = []
    for rows in row_lists:
        for r in rows:
            try:
                days.append(date.fromisoformat(str(r["last_checked"])))
            except (KeyError, ValueError):
                continue
    return max(days) if days else None


def build(
    directory: list[dict],
    metrics: list[dict],
    events: list[dict],
    multiples: dict[str, float] | None,
    *,
    today: date | None = None,
    multiples_as_of: str | None = None,
) -> Ledger:
    """Turn the curated ring C rows into the ledger marts, the counts for facts, and the checks.

    ``directory``, ``metrics``, ``events``  what ``reference.rows("private_directory" | "private_metrics" | "events")``
                    return (any may be empty).
    ``multiples``   ticker -> EV/NTM revenue of the listed comparables of ``config.COMPARABLES_BY_LAYER`` (a missing,
                    null or non-positive entry is ignored; a comparable without a multiple narrows the range and
                    is listed in ``comparable_missing``).
    ``today``       the date the 12-month acquisition window ends on; if omitted, the newest ``last_checked``.
    ``multiples_as_of``  the snapshot date of ``multiples`` (``YYYY-MM-DD``), carried into ``private_intervals``.

    Returns a :class:`Ledger` of ``tables``, ``summary`` and ``checks``. Raises :class:`LedgerError` on a duplicate
    company in the directory or a table that breaks its schema (a duplicate key, a value outside its vocabulary).
    The inputs are not modified.
    """
    directory, metrics, events = list(directory or []), list(metrics or []), list(events or [])
    for label, rows in (("private_directory", directory), ("private_metrics", metrics), ("events", events)):
        _need_company(label, rows)
    names = Counter(d.get("company") for d in directory)
    dup = sorted(str(n) for n, k in names.items() if k > 1)
    if dup:
        raise LedgerError("duplicate company in private_directory: " + ", ".join(dup))
    by_name = {d["company"]: d for d in directory}
    today = today or _fallback_today(directory, metrics, events)
    clean = _clean_multiples(multiples)

    charted, reported_recs = _split_metrics(metrics)
    intervals, diag = _company_rows(by_name, metrics, clean, multiples_as_of)
    latest_rows, ties = _latest_rows(charted)
    charted_events = []
    for e in events:
        why = reason_not_chartable(e, metric=False)
        if why is None:
            charted_events.append(e)
        else:
            reported_recs.append(_reported_rec("events", e, why))

    reported_recs, n_duplicates = _dedupe_reported(reported_recs)
    tables = {
        "private_intervals": _validate("private_intervals", INTERVALS_SCHEMA, _frame(intervals, INTERVAL_DTYPES)).sort(
            "company"),
        "ledger_timeline": _validate(
            "ledger_timeline", TIMELINE_SCHEMA, _frame([_metric_rec(r) for r in charted], TIMELINE_DTYPES)
        ).sort(["company", "date", "metric", "metric_kind", "period", "status"]),
        "ledger_latest": _validate(
            "ledger_latest", LATEST_SCHEMA, _frame([_metric_rec(r) for r in latest_rows], TIMELINE_DTYPES)
        ).sort(LATEST_KEY),
        "reported_unconfirmed": _validate(
            "reported_unconfirmed", REPORTED_SCHEMA, _frame(reported_recs, REPORTED_DTYPES)
        ).sort(["company", "date", "metric", "source", "source_url"]),
        "events": _validate(
            "events", EVENTS_SCHEMA,
            _frame(_mark_deal_starts([_event_rec(e) for e in charted_events]), EVENTS_DTYPES),
        ).sort(["event_date", "company", "event_kind", "counterparty"]),
    }  # fmt: skip
    summary = summarise(
        directory, metrics, tables, today=today, multiples_as_of=multiples_as_of, reported_duplicates=n_duplicates
    )
    checks = _build_checks(metrics, events, tables, summary, diag, ties)
    return Ledger(tables, summary, checks)
