"""The private-company ledger and take rates (PLAN §3 M2 and M4, EXECUTION W2b).

Two jobs, one rule: a number keeps its qualifier from the page it was read on to the chart it ends up in.

1. The private ledger
---------------------
From ``private_metrics.json`` and ``events.json`` (W1c and W1f, verified), read through :func:`reference.rows`:

``ledger_timeline``   per private company, every chartable stated metric and valuation, plus the dated deal
                      events for the same companies (an event that repeats a metric row, same company, value and
                      date, is dropped so a Ramp round is not plotted twice). Each row carries its implied
                      interval: ``=`` is a point, ``>1.4T`` is a lower bound only, ``<1.8B`` an upper bound only,
                      ``~`` an estimate with no bound (:func:`economics.interval`).
``ledger_latest``     the latest ``current`` row per company and metric.
``ledger_reported``   the "reported, unconfirmed" table: every row that is not chartable, with the reason
                      (``never_charted_kind`` for ``reported_talks`` and ``third_party_estimate``, which are never
                      charted whatever their tag; ``unverified_tag`` for ``S``/``U``; otherwise
                      ``curator_not_chartable``, such as statutory accounts read only in a press report).
``private_intervals`` derived bounds, tag ``C``: revenue / volume for private companies that state both for the same
                      period and basis; Stripe net revenue from the registered peer take-rate band; and the Q6
                      inference chain for Brex card spend and net interchange from Capital One's purchase-price
                      allocation.

2. Take rates for public companies
----------------------------------
From ``kpi_disclosures_acceptance.json`` and ``kpi_disclosures_other.json``: revenue / volume per company per period,
for the metric pairs pinned in :data:`TAKE_RATE_PAIRS`, and only when

* both rows are chartable (tag ``V``/``C``);
* they share the period label **and** the as-of date (same window);
* they share the currency;
* they share the basis: an annualised figure is never divided by a period figure (section H share rules);
* their qualifiers do not bound the ratio from opposite sides (strict propagation; see :mod:`economics`).

Scope is fixed by the pair table itself: each pair names a numerator and a denominator that the company reports
for the same business (PayPal net revenues over PayPal TPV, Square gross profit over Square GPV, Wise cross-border
revenue over cross-border volume). ``ratio_kind`` says which comparisons make sense: ``gross_take_rate`` (revenue
over volume), ``net_take_rate`` (revenue net of pass-through network or partner costs), ``margin_retention`` (gross
profit, transaction margin or revenue less transaction costs over volume). Ratios of different kinds are never
compared. Every pair that fails a condition becomes a row in ``take_rates_refused`` with the reason, so a missing
dot on the page can be explained. Computed take rates are tag ``C``.

Usage::

    uv run python -m pipelines.payments.ledger [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, write_json
from pipelines.payments import config, reference
from pipelines.payments.economics import (
    CHARTABLE_TAGS,
    QUALIFIERS,
    Num,
    QualifierConflict,
    interval,
    q_div,
    q_mul,
    q_scale,
)

log = logging.getLogger("payments.ledger")

RATIO_KINDS = ("gross_take_rate", "net_take_rate", "margin_retention")
REPORTED_REASONS = ("never_charted_kind", "unverified_tag", "curator_not_chartable")


@dataclass(frozen=True)
class Pair:
    company: str
    numerator: str
    denominator: str
    ratio: str
    ratio_kind: str
    scope: str


TAKE_RATE_PAIRS: tuple[Pair, ...] = (
    Pair("PayPal", "net_revenues", "tpv", "net_revenues_per_tpv", "gross_take_rate",
         "PayPal group; net revenues include non-transaction revenue, TPV includes Venmo and Braintree"),
    Pair("PayPal", "transaction_margin_dollars", "tpv", "transaction_margin_per_tpv", "margin_retention",
         "PayPal group"),
    Pair("Block", "square_gross_profit", "square_gpv", "square_gross_profit_per_gpv", "margin_retention",
         "Square ecosystem only (not Cash App)"),
    Pair("Toast", "fintech_solutions_revenue", "gpv", "fintech_revenue_per_gpv", "gross_take_rate",
         "Toast fintech solutions (payments) revenue over GPV"),
    Pair("Shift4", "gross_revenue", "volume", "gross_revenue_per_volume", "gross_take_rate", "Shift4 group"),
    Pair("Shift4", "gross_revenue_less_network_fees", "volume", "net_revenue_per_volume", "net_take_rate",
         "Shift4 group, net of network fees"),
    Pair("Shopify", "merchant_solutions_revenue", "gmv", "merchant_solutions_revenue_per_gmv", "gross_take_rate",
         "Shopify platform; merchant solutions include more than payments"),
    Pair("Adyen", "net_revenue", "processed_volume", "net_revenue_per_processed_volume", "net_take_rate",
         "Adyen group, EUR, half-years"),
    Pair("Affirm", "Total revenue, net", "GMV", "revenue_per_gmv", "gross_take_rate",
         "Affirm platform; revenue includes interest and gain on sale"),
    Pair("Affirm", "Revenue less transaction costs", "GMV", "rltc_per_gmv", "margin_retention", "Affirm platform"),
    Pair("Klarna", "Total revenue", "GMV", "revenue_per_gmv", "gross_take_rate", "Klarna group"),
    Pair("Sezzle", "Total revenue", "GMV", "revenue_per_gmv", "gross_take_rate", "Sezzle group"),
    Pair("Zip", "Total income", "TTV", "income_per_ttv", "gross_take_rate", "Zip group, AUD"),
    Pair("BILL", "Total revenue", "Total payment volume", "revenue_per_tpv", "gross_take_rate",
         "BILL group; revenue includes subscription and float income"),
    Pair("Navan", "Usage revenue", "Gross booking volume", "usage_revenue_per_gbv", "gross_take_rate",
         "Navan usage revenue over gross booking volume"),
    Pair("Corpay", "Corporate Payments revenues, net", "Corporate Payments spend volume",
         "corporate_payments_revenue_per_spend", "gross_take_rate", "Corpay Corporate Payments segment"),
    Pair("Wise", "Cross-border revenue", "Cross-border volume", "cross_border_take_rate", "gross_take_rate",
         "Wise cross-border only (excludes card and interest revenue)"),
    Pair("Payoneer", "Revenue", "Volume", "revenue_per_volume", "gross_take_rate",
         "Payoneer group; revenue includes interest on customer funds"),
    Pair("dLocal", "Revenue", "TPV", "revenue_per_tpv", "gross_take_rate", "dLocal group"),
    Pair("Flywire", "Revenue less ancillary services", "Total payment volume", "net_revenue_per_tpv",
         "net_take_rate", "Flywire group, excluding ancillary services"),
    Pair("Marqeta", "Net revenue", "Total processing volume", "net_revenue_per_tpv", "net_take_rate",
         "Marqeta group, net of card-network costs"),
)

PRIVATE_PAIRS: tuple[Pair, ...] = (
    Pair("Ramp", "annualized_revenue", "annualized_purchase_volume", "revenue_per_purchase_volume",
         "gross_take_rate", "Ramp, annualised; purchase volume covers cards and bill payments"),
    Pair("Airwallex", "annualized_revenue", "annualized_transaction_volume", "revenue_per_transaction_volume",
         "gross_take_rate", "Airwallex, annualised, all products"),
    Pair("Mercury", "revenue", "transaction_volume", "revenue_per_transaction_volume", "gross_take_rate",
         "Mercury, calendar year"),
    Pair("Checkout.com", "annualized_net_revenue", "total_payment_volume", "net_revenue_per_tpv", "net_take_rate",
         "Checkout.com group"),
)

# PLAN §3 M4 item 4: Stripe's net revenue is bounded with the net take rate of listed acquiring peers. An
# assumption, labelled as one on the page; Stripe publishes volume only.
STRIPE_NET_TAKE_BAND_PCT = (0.30, 0.45)
# Section H, Q6 step 2: "At 20–45 days of receivables" turns Capital One's $815M of Brex loans into annual spend.
Q6_RECEIVABLE_DAYS = (20, 45)
Q6_NET_INTERCHANGE_LINE = ("corporate_card_bill", "net_interchange_after_rewards")  # "about 1.28%" in Q6 step 3


def basis(metric: str) -> str:
    """``annualized`` for a run-rate metric, ``period`` for a flow over the stated period."""
    m = metric.lower()
    return "annualized" if ("annualized" in m or "annualised" in m) else "period"


def _segment(company: str, *rows: dict | None) -> str | None:
    for r in rows:
        if r and r.get("segment"):
            return r["segment"]
    for seg, groups in config.UNIVERSE.items():
        for names in groups.values():
            if any(n == company or n.split("/")[0] == company or n.split(" ")[0] == company for n in names):
                return seg
    return None


def _chartable(r: dict) -> bool:
    return (r.get("chartable") is True and r.get("tag") in CHARTABLE_TAGS
            and r.get("metric_kind") not in config.NEVER_CHARTED_KINDS)


# --- schemas ---------------------------------------------------------------------------------

def _never_charted_absent(d: pa.PolarsData) -> pl.LazyFrame:
    return d.lazyframe.select(~pl.col("metric_kind").fill_null("").is_in(config.NEVER_CHARTED_KINDS))


def _interval_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    q = pl.col("qualifier")
    return d.lazyframe.select(
        pl.when(q == "=").then(pl.col("lower").is_not_null() & pl.col("upper").is_not_null())
        .when(q == ">").then(pl.col("lower").is_not_null() & pl.col("upper").is_null())
        .when(q == "<").then(pl.col("lower").is_null() & pl.col("upper").is_not_null())
        .when(q == "~").then(pl.col("estimate").is_not_null())
        .otherwise(pl.col("lower").is_null() & pl.col("upper").is_null() & pl.col("estimate").is_null()))


_Q = pa.Column(str, pa.Check.isin(list(QUALIFIERS)), nullable=True)
_TAG = pa.Column(str, pa.Check.isin(list(CHARTABLE_TAGS)))
_TRUE = pa.Column(pl.Boolean, pa.Check.eq(True))
_URL = pa.Column(str, pa.Check.str_startswith("https://"))
_DATE = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"))

TIMELINE_KEY = ["company", "source", "metric", "period", "date", "counterparty"]
TIMELINE_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "segment": pl.Utf8, "date": pl.Utf8, "period": pl.Utf8, "source": pl.Utf8,
    "item": pl.Utf8, "metric": pl.Utf8, "metric_kind": pl.Utf8, "event_kind": pl.Utf8, "counterparty": pl.Utf8,
    "value": pl.Float64, "unit": pl.Utf8, "currency": pl.Utf8, "qualifier": pl.Utf8, "lower": pl.Float64,
    "upper": pl.Float64, "estimate": pl.Float64, "status": pl.Utf8, "tag": pl.Utf8, "chartable": pl.Boolean,
    "stale": pl.Boolean, "company_confirmed": pl.Boolean, "source_url": pl.Utf8, "caveat": pl.Utf8,
}
TIMELINE_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "segment": pa.Column(str, nullable=True), "date": _DATE,
        "period": pa.Column(str), "source": pa.Column(str, pa.Check.isin(["private_metrics", "events"])),
        "item": pa.Column(str, pa.Check.isin(["metric", "event"])), "metric": pa.Column(str),
        "metric_kind": pa.Column(str, pa.Check.isin(config.METRIC_KINDS), nullable=True),
        "event_kind": pa.Column(str, pa.Check.isin(list(reference.EVENT_KINDS)), nullable=True),
        "counterparty": pa.Column(str), "value": pa.Column(pl.Float64, nullable=True),
        "unit": pa.Column(str, nullable=True), "currency": pa.Column(str, nullable=True), "qualifier": _Q,
        "lower": pa.Column(pl.Float64, nullable=True), "upper": pa.Column(pl.Float64, nullable=True),
        "estimate": pa.Column(pl.Float64, nullable=True),
        "status": pa.Column(str, pa.Check.isin(["current", "superseded", "disputed"]), nullable=True),
        "tag": _TAG, "chartable": _TRUE, "stale": pa.Column(pl.Boolean), "company_confirmed": pa.Column(pl.Boolean),
        "source_url": _URL, "caveat": pa.Column(str, nullable=True),
    },
    unique=TIMELINE_KEY, strict=True, coerce=False,
    checks=[pa.Check(_never_charted_absent, error="a never-charted metric_kind reached the timeline"),
            pa.Check(_interval_consistent, error="interval does not match its qualifier"),
            pa.Check(lambda d: d.lazyframe.select(pl.col("value").is_null() == pl.col("qualifier").is_null()),
                     error="a value needs a qualifier")],
)

LATEST_KEY = ["company", "metric"]
LATEST_SCHEMA = pa.DataFrameSchema(  # the timeline's columns, but every row has a value and is current
    dict(TIMELINE_SCHEMA.columns) | {
        "value": pa.Column(pl.Float64),
        "qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS))),
        "status": pa.Column(str, pa.Check.eq("current")),
    },
    unique=LATEST_KEY, strict=True, coerce=False, checks=TIMELINE_SCHEMA.checks,
)

REPORTED_KEY = ["source", "company", "metric", "period", "date", "counterparty", "value"]
REPORTED_DTYPES: dict[str, pl.DataType] = {
    "source": pl.Utf8, "company": pl.Utf8, "date": pl.Utf8, "period": pl.Utf8, "metric": pl.Utf8,
    "metric_kind": pl.Utf8, "counterparty": pl.Utf8, "value": pl.Float64, "unit": pl.Utf8, "currency": pl.Utf8,
    "qualifier": pl.Utf8, "status": pl.Utf8, "tag": pl.Utf8, "reason": pl.Utf8, "company_confirmed": pl.Boolean,
    "source_name": pl.Utf8, "source_url": pl.Utf8, "caveat": pl.Utf8,
}
REPORTED_SCHEMA = pa.DataFrameSchema(
    {
        "source": pa.Column(str, pa.Check.isin(["private_metrics", "events"])), "company": pa.Column(str),
        "date": _DATE, "period": pa.Column(str), "metric": pa.Column(str),
        "metric_kind": pa.Column(str, pa.Check.isin(config.METRIC_KINDS), nullable=True),
        "counterparty": pa.Column(str), "value": pa.Column(pl.Float64, nullable=True),
        "unit": pa.Column(str, nullable=True), "currency": pa.Column(str, nullable=True), "qualifier": _Q,
        "status": pa.Column(str, nullable=True), "tag": pa.Column(str, pa.Check.isin(list(reference.TAGS))),
        "reason": pa.Column(str, pa.Check.isin(list(REPORTED_REASONS))),
        "company_confirmed": pa.Column(pl.Boolean), "source_name": pa.Column(str, nullable=True),
        "source_url": _URL, "caveat": pa.Column(str, nullable=True),
    },
    unique=REPORTED_KEY, strict=True, coerce=False,
)

TAKE_KEY = ["company", "ratio", "period"]
TAKE_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "segment": pl.Utf8, "ratio": pl.Utf8, "ratio_kind": pl.Utf8, "scope": pl.Utf8,
    "basis": pl.Utf8, "period": pl.Utf8, "as_of": pl.Utf8, "currency": pl.Utf8, "numerator_metric": pl.Utf8,
    "numerator_value": pl.Float64, "numerator_qualifier": pl.Utf8, "denominator_metric": pl.Utf8,
    "denominator_value": pl.Float64, "denominator_qualifier": pl.Utf8, "take_rate_pct": pl.Float64,
    "qualifier": pl.Utf8, "tag": pl.Utf8, "chartable": pl.Boolean, "stale": pl.Boolean,
    "numerator_source_url": pl.Utf8, "denominator_source_url": pl.Utf8,
}
TAKE_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "segment": pa.Column(str, pa.Check.isin(list(reference.SEGMENTS)), nullable=True),
        "ratio": pa.Column(str), "ratio_kind": pa.Column(str, pa.Check.isin(list(RATIO_KINDS))),
        "scope": pa.Column(str), "basis": pa.Column(str, pa.Check.isin(["period", "annualized"])),
        "period": pa.Column(str), "as_of": _DATE, "currency": pa.Column(str),
        "numerator_metric": pa.Column(str), "numerator_value": pa.Column(pl.Float64, pa.Check.ge(0)),
        "numerator_qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS))),
        "denominator_metric": pa.Column(str), "denominator_value": pa.Column(pl.Float64, pa.Check.gt(0)),
        "denominator_qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS))),
        "take_rate_pct": pa.Column(pl.Float64, pa.Check.in_range(0.0, 100.0)),
        "qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS))),
        "tag": pa.Column(str, pa.Check.eq("C")), "chartable": _TRUE, "stale": pa.Column(pl.Boolean),
        "numerator_source_url": _URL, "denominator_source_url": _URL,
    },
    unique=TAKE_KEY, strict=True, coerce=False,
)
REFUSED_DTYPES: dict[str, pl.DataType] = {"company": pl.Utf8, "ratio": pl.Utf8, "period": pl.Utf8,
                                          "reason": pl.Utf8}
REFUSED_SCHEMA = pa.DataFrameSchema(
    {"company": pa.Column(str), "ratio": pa.Column(str), "period": pa.Column(str), "reason": pa.Column(str)},
    unique=["company", "ratio", "period"], strict=True, coerce=False,
)

INTERVAL_KEY = ["company", "metric", "period"]
INTERVAL_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8, "segment": pl.Utf8, "metric": pl.Utf8, "period": pl.Utf8, "as_of": pl.Utf8,
    "lower": pl.Float64, "upper": pl.Float64, "estimate": pl.Float64, "qualifier": pl.Utf8, "unit": pl.Utf8,
    "formula": pl.Utf8, "inputs": pl.Utf8, "assumption": pl.Utf8, "tag": pl.Utf8, "chartable": pl.Boolean,
    "stale": pl.Boolean, "source_url": pl.Utf8, "note": pl.Utf8,
}
INTERVAL_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str), "segment": pa.Column(str, nullable=True), "metric": pa.Column(str),
        "period": pa.Column(str), "as_of": _DATE, "lower": pa.Column(pl.Float64, nullable=True),
        "upper": pa.Column(pl.Float64, nullable=True), "estimate": pa.Column(pl.Float64, nullable=True),
        "qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS))), "unit": pa.Column(str),
        "formula": pa.Column(str), "inputs": pa.Column(str), "assumption": pa.Column(str, nullable=True),
        "tag": pa.Column(str, pa.Check.eq("C")), "chartable": _TRUE, "stale": pa.Column(pl.Boolean),
        "source_url": pa.Column(str), "note": pa.Column(str, nullable=True),
    },
    unique=INTERVAL_KEY, strict=True, coerce=False,
    checks=[pa.Check(lambda d: d.lazyframe.select(
        pl.col("lower").is_not_null() | pl.col("upper").is_not_null() | pl.col("estimate").is_not_null()),
        error="a derived interval needs at least one bound or an estimate"),
        pa.Check(lambda d: d.lazyframe.select(
            pl.col("lower").is_null() | pl.col("upper").is_null() | (pl.col("lower") <= pl.col("upper"))),
            error="lower bound above upper bound")],
)


# --- take rates ------------------------------------------------------------------------------


def take_rates(rows: list[dict], pairs: tuple[Pair, ...] = TAKE_RATE_PAIRS) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Revenue / volume per pinned pair and period, and the refusals with reasons."""
    ok: list[dict] = []
    refused: list[dict] = []
    for p in pairs:
        nums_all = [r for r in rows if r["company"] == p.company and r["metric"] == p.numerator]
        dens_all = [r for r in rows if r["company"] == p.company and r["metric"] == p.denominator]
        periods = sorted({r["period"] for r in nums_all + dens_all})
        for period in periods:
            rec, why = _pair_one(p, [r for r in nums_all if r["period"] == period],
                                 [r for r in dens_all if r["period"] == period])
            if rec:
                ok.append(rec | {"period": period})
            else:
                refused.append({"company": p.company, "ratio": p.ratio, "period": period, "reason": why})
    df = pl.DataFrame(ok, schema=TAKE_DTYPES) if ok else pl.DataFrame(schema=TAKE_DTYPES)
    ref = pl.DataFrame(refused, schema=REFUSED_DTYPES) if refused else pl.DataFrame(schema=REFUSED_DTYPES)
    return (TAKE_SCHEMA.validate(df.sort(TAKE_KEY)),  # type: ignore[return-value]
            REFUSED_SCHEMA.validate(ref.sort(["company", "ratio", "period"])))


def _pair_one(p: Pair, nums: list[dict], dens: list[dict]) -> tuple[dict | None, str]:
    n_ok, d_ok = [r for r in nums if _chartable(r)], [r for r in dens if _chartable(r)]
    if not nums:
        return None, f"no {p.numerator} for this period"
    if not dens:
        return None, f"no {p.denominator} for this period"
    if not n_ok:
        return None, f"{p.numerator} not chartable (tag {nums[0].get('tag')})"
    if not d_ok:
        return None, f"{p.denominator} not chartable (tag {dens[0].get('tag')})"
    if len(n_ok) > 1 or len(d_ok) > 1:
        return None, f"ambiguous: {len(n_ok)} numerator and {len(d_ok)} denominator rows"
    n, d = n_ok[0], d_ok[0]
    if basis(p.numerator) != basis(p.denominator):
        return None, f"basis differs ({basis(p.numerator)} vs {basis(p.denominator)})"
    if (n.get("currency") or n.get("unit")) != (d.get("currency") or d.get("unit")):
        return None, f"currency differs ({n.get('currency')} vs {d.get('currency')})"
    if n.get("as_of_date") != d.get("as_of_date"):
        return None, f"as-of date differs ({n.get('as_of_date')} vs {d.get('as_of_date')})"
    if not float(d["value"]) > 0:
        return None, "denominator is not positive"
    try:
        r = q_div(q_scale(Num(float(n["value"]), n["qualifier"]), 100.0), Num(float(d["value"]), d["qualifier"]),
                  strict=True)
    except QualifierConflict:
        return None, f"qualifiers bound the ratio from opposite sides ({n['qualifier']} over {d['qualifier']})"
    return {
        "company": p.company, "segment": _segment(p.company, n, d), "ratio": p.ratio, "ratio_kind": p.ratio_kind,
        "scope": p.scope, "basis": basis(p.numerator), "as_of": n["as_of_date"],
        "currency": n.get("currency") or n.get("unit"),
        "numerator_metric": p.numerator, "numerator_value": float(n["value"]), "numerator_qualifier": n["qualifier"],
        "denominator_metric": p.denominator, "denominator_value": float(d["value"]),
        "denominator_qualifier": d["qualifier"], "take_rate_pct": round(r.value, 6), "qualifier": r.qualifier,
        "tag": "C", "chartable": True, "stale": bool(n.get("stale") or d.get("stale")),
        "numerator_source_url": n["source_url"], "denominator_source_url": d["source_url"],
    }, ""


# --- private ledger --------------------------------------------------------------------------

EVENT_METRIC = {"priced_round": "valuation", "tender": "valuation", "acquisition": "acquisition_price",
                "ipo": "ipo_proceeds", "direct_listing": "direct_listing", "spac": "spac_value"}


def _reason(r: dict) -> str:
    if r.get("metric_kind") in config.NEVER_CHARTED_KINDS:
        return "never_charted_kind"
    if r.get("tag") not in CHARTABLE_TAGS:
        return "unverified_tag"
    return "curator_not_chartable"


def _metric_rec(r: dict) -> dict:
    lo, hi, est = interval(float(r["value"]), r["qualifier"])
    return {
        "company": r["company"], "segment": _segment(r["company"], r), "date": r["as_of_date"],
        "period": r["period"], "source": "private_metrics", "item": "metric", "metric": r["metric"],
        "metric_kind": r.get("metric_kind"), "event_kind": None, "counterparty": "",
        "value": float(r["value"]), "unit": r.get("unit"), "currency": r.get("currency"), "qualifier": r["qualifier"],
        "lower": lo, "upper": hi, "estimate": est, "status": r.get("status"), "tag": r["tag"],
        "chartable": True, "stale": bool(r.get("stale")), "company_confirmed": bool(r.get("company_confirmed")),
        "source_url": r["source_url"], "caveat": r.get("caveat"),
    }


def _event_rec(r: dict) -> dict:
    v = r.get("value")
    v = None if v is None else float(v)
    q = r.get("qualifier") if v is not None else None
    lo, hi, est = interval(v, q)
    metric = EVENT_METRIC.get(r["event_kind"], r["event_kind"])
    if v is None:
        metric = r["event_kind"]
    return {
        "company": r["company"], "segment": _segment(r["company"], r), "date": r["event_date"],
        "period": r["event_date"][:7], "source": "events", "item": "event", "metric": metric,
        "metric_kind": r.get("metric_kind"), "event_kind": r["event_kind"], "counterparty": r.get("counterparty") or "",
        "value": v, "unit": r.get("unit") if v is not None else None,
        "currency": r.get("currency") if v is not None else None, "qualifier": q, "lower": lo, "upper": hi,
        "estimate": est, "status": None, "tag": r["tag"], "chartable": True, "stale": bool(r.get("stale")),
        "company_confirmed": bool(r.get("company_confirmed")), "source_url": r["source_url"],
        "caveat": r.get("caveat"),
    }


def timeline(metrics: list[dict], events: list[dict]) -> pl.DataFrame:
    """Chartable metrics and valuations per private company, with deal events that do not repeat a metric row."""
    roster = {r["company"] for r in metrics}
    recs = [_metric_rec(r) for r in metrics if _chartable(r)]
    seen = {(r["company"], float(r["value"]), r["as_of_date"]) for r in metrics if r.get("value") is not None}
    for e in events:
        if e["company"] not in roster or not _chartable(e):
            continue
        if e.get("value") is not None and (e["company"], float(e["value"]), e["event_date"]) in seen:
            continue  # the same fact as a metric row (a Ramp round, Brex's announced price)
        recs.append(_event_rec(e))
    df = pl.DataFrame(recs, schema=TIMELINE_DTYPES) if recs else pl.DataFrame(schema=TIMELINE_DTYPES)
    return TIMELINE_SCHEMA.validate(df.sort(["company", "date", "metric"]))  # type: ignore[return-value]


_QRANK = {"=": 0, ">": 1, "<": 1, "~": 2}


def latest(tl: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Latest ``current`` value per company and metric; ties on date prefer ``=`` over a bound over ``~``."""
    cur = tl.filter((pl.col("source") == "private_metrics") & (pl.col("status") == "current")
                    & pl.col("value").is_not_null())
    cur = cur.with_columns(pl.col("qualifier").replace_strict(_QRANK, return_dtype=pl.Int64).alias("_q"))
    top = cur.group_by(LATEST_KEY).agg(pl.col("date").max().alias("_d"))
    cand = cur.join(top, on=LATEST_KEY).filter(pl.col("date") == pl.col("_d"))
    ties = cand.height - cand.unique(subset=LATEST_KEY).height
    out = cand.sort(["company", "metric", "_q", "value"]).unique(subset=LATEST_KEY, keep="first",
                                                                 maintain_order=True)
    return LATEST_SCHEMA.validate(out.drop("_q", "_d").sort(LATEST_KEY)), ties  # type: ignore[return-value]


def reported(metrics: list[dict], events: list[dict]) -> pl.DataFrame:
    """The "reported, unconfirmed" table: every non-chartable row, never charted, with its reason."""
    recs = []
    for src, rows in (("private_metrics", metrics), ("events", events)):
        for r in rows:
            if _chartable(r):
                continue
            v = r.get("value")
            recs.append({
                "source": src, "company": r["company"],
                "date": r.get("as_of_date") if src == "private_metrics" else r["event_date"],
                "period": r.get("period") or r.get("event_date", "")[:7],
                "metric": r.get("metric") or EVENT_METRIC.get(r.get("event_kind", ""), r.get("event_kind", "")),
                "metric_kind": r.get("metric_kind"), "counterparty": r.get("counterparty") or "",
                "value": None if v is None else float(v), "unit": r.get("unit"), "currency": r.get("currency"),
                "qualifier": r.get("qualifier") if v is not None else None, "status": r.get("status"),
                "tag": r["tag"], "reason": _reason(r), "company_confirmed": bool(r.get("company_confirmed")),
                "source_name": r.get("source_name"), "source_url": r["source_url"], "caveat": r.get("caveat"),
            })
    df = pl.DataFrame(recs, schema=REPORTED_DTYPES) if recs else pl.DataFrame(schema=REPORTED_DTYPES)
    return REPORTED_SCHEMA.validate(df.sort(["company", "date", "metric"]))  # type: ignore[return-value]


def _band(lo: Num, hi: Num) -> tuple[float | None, float | None, float | None, str]:
    """Bounds from the two ends of an assumption band, each carrying the input's qualifier.

    A lower end survives only if it is not itself an upper bound, and vice versa; ``~`` keeps both ends as an
    approximate band.
    """
    q = lo.qualifier if lo.qualifier == hi.qualifier else "~"
    if q == "~":
        return lo.value, hi.value, None, "~"
    lower = lo.value if q in ("=", ">") else None
    upper = hi.value if q in ("=", "<") else None
    return lower, upper, None, q


def _r(x: float | None) -> float | None:
    """Drop float noise (8550000000.000001) without rounding away real digits."""
    return None if x is None else float(f"{x:.12g}")


def _interval_rec(company: str, metric: str, period: str, as_of: str, lo, hi, est, q: str, unit: str,
                  formula: str, inputs: list[dict], assumption: str | None, note: str | None) -> dict:
    return {
        "company": company, "segment": _segment(company, *inputs), "metric": metric, "period": period,
        "as_of": as_of, "lower": _r(lo), "upper": _r(hi), "estimate": _r(est), "qualifier": q, "unit": unit,
        "formula": formula,
        "inputs": ", ".join(f"{r.get('company', r.get('waterfall'))}/{r.get('metric', r.get('line'))}/{r['period']}"
                            for r in inputs),
        "assumption": assumption, "tag": "C", "chartable": True,
        "stale": any(bool(r.get("stale")) for r in inputs),
        "source_url": " ".join(dict.fromkeys(r["source_url"] for r in inputs)), "note": note,
    }


def private_intervals(metrics: list[dict], waterfall_rows: list[dict]) -> tuple[pl.DataFrame, pl.DataFrame,
                                                                                 list[dict]]:
    """Derived bounds for private companies, the take-rate refusals among them, and checks."""
    recs: list[dict] = []
    checks: list[dict] = []
    tr, refused = take_rates(metrics, PRIVATE_PAIRS)
    for r in tr.iter_rows(named=True):
        lo, hi, est = interval(r["take_rate_pct"], r["qualifier"])
        recs.append(_interval_rec(
            r["company"], f"take_rate:{r['ratio']}", r["period"], r["as_of"], lo, hi, est, r["qualifier"],
            "percent", f"{r['numerator_metric']} ({r['numerator_qualifier']}{r['numerator_value']:,.0f}) / "
            f"{r['denominator_metric']} ({r['denominator_qualifier']}{r['denominator_value']:,.0f}) x 100",
            [m for m in metrics if m["company"] == r["company"] and m["period"] == r["period"]
             and m["metric"] in (r["numerator_metric"], r["denominator_metric"]) and _chartable(m)],
            None, r["scope"]))

    def one(company: str, metric: str, period: str | None = None) -> dict | None:
        hits = [m for m in metrics if m["company"] == company and m["metric"] == metric and _chartable(m)
                and m.get("status") == "current" and (period is None or m["period"] == period)]
        return max(hits, key=lambda m: m["as_of_date"]) if hits else None

    # Stripe net revenue from the peer net take-rate band (an assumption).
    vol = one("Stripe", "total_volume")
    if vol:
        v = Num(float(vol["value"]), vol["qualifier"])
        a, b = STRIPE_NET_TAKE_BAND_PCT
        lo, hi, est, q = _band(q_mul(v, Num(a / 100)), q_mul(v, Num(b / 100)))
        recs.append(_interval_rec(
            "Stripe", "implied_net_revenue", vol["period"], vol["as_of_date"], lo, hi, est, q, "USD",
            f"total volume {v.qualifier}{v.value:,.0f} x [{a:.2f}%, {b:.2f}%]", [vol],
            f"net take rate of {a:.2f}-{b:.2f}% of volume, from listed acquiring peers (PLAN M4)",
            "a bound, not a disclosure: Stripe publishes volume only"))
    else:
        checks.append(check("Stripe implied net revenue", False, "no chartable current Stripe total_volume",
                            warn=True))

    # Q6 chain: Brex loans at acquisition -> annual card spend -> net interchange.
    loans = one("Brex", "ppa_loans")
    net_rows = [w for w in waterfall_rows if (w["waterfall"], w["line"]) == Q6_NET_INTERCHANGE_LINE
                and w.get("chartable") is True and w.get("tag") in CHARTABLE_TAGS]
    if loans and len(net_rows) == 1:
        L = Num(float(loans["value"]), loans["qualifier"])
        d_lo, d_hi = Q6_RECEIVABLE_DAYS
        s_lo, s_hi = q_div(q_scale(L, 365.0), Num(float(d_hi))), q_div(q_scale(L, 365.0), Num(float(d_lo)))
        lo, hi, est, q = _band(s_lo, s_hi)
        assumption = f"{d_lo}-{d_hi} days of card receivables (section H, Q6)"
        recs.append(_interval_rec(
            "Brex", "implied_annual_card_spend", "2026-04-07", loans["as_of_date"], lo, hi, est, q, "USD",
            f"loans {L.value:,.0f} x 365 / [{d_hi}, {d_lo}] days", [loans], assumption,
            "Capital One purchase-price allocation; inference chain step 2"))
        nr = net_rows[0]
        rate = Num(float(nr["value"]) / 100, nr["qualifier"])
        lo2, hi2, est2, q2 = _band(q_mul(s_lo, rate), q_mul(s_hi, rate))
        recs.append(_interval_rec(
            "Brex", "implied_net_interchange", "2026-04-07", max(loans["as_of_date"], nr["as_of_date"]), lo2, hi2,
            est2, q2, "USD", f"implied card spend x {nr['value']:g}% (BILL net interchange after rewards)",
            [loans, nr], assumption + f"; net interchange {nr['value']:g}% of spend as at BILL",
            "inference chain step 3; compare with the reported (unconfirmed) Brex revenue in ledger_reported"))
    else:
        checks.append(check("Brex inference chain", False,
                            "missing Brex ppa_loans or the BILL net interchange line", warn=True))
    df = pl.DataFrame(recs, schema=INTERVAL_DTYPES) if recs else pl.DataFrame(schema=INTERVAL_DTYPES)
    return INTERVAL_SCHEMA.validate(df.sort(INTERVAL_KEY)), refused, checks  # type: ignore[return-value]


# --- run -------------------------------------------------------------------------------------

TABLES = ("ledger_timeline", "ledger_latest", "ledger_reported", "private_intervals", "private_take_rates_refused",
          "take_rates", "take_rates_refused")


def _load(kind: str, today: date | None, checks: list[dict]) -> list[dict] | None:
    try:
        return reference.rows(kind, today)
    except Exception as exc:  # noqa: BLE001 - one unreadable file must not lose the others (rule 5)
        log.error("%s unreadable: %s", kind, exc)
        checks.append(check(f"Reference {kind}", False, f"{type(exc).__name__}: {exc}"[:300]))
        return None


def build(today: date | None = None) -> tuple[dict[str, pl.DataFrame], list[dict]]:
    checks: list[dict] = []
    metrics = _load("private_metrics", today, checks)
    events = _load("events", today, checks)
    wf = _load("waterfall_inputs", today, checks) or []
    kpi = [r for k in ("kpi_disclosures_acceptance", "kpi_disclosures_other") for r in (_load(k, today, checks) or [])]
    tables: dict[str, pl.DataFrame] = {}

    if metrics is not None:
        tl = timeline(metrics, events or [])
        lt, ties = latest(tl)
        rep = reported(metrics, events or [])
        iv, iv_ref, iv_checks = private_intervals(metrics, wf)
        tables |= {"ledger_timeline": tl, "ledger_latest": lt, "ledger_reported": rep, "private_intervals": iv,
                   "private_take_rates_refused": iv_ref}
        checks += iv_checks
        n_co = tl["company"].n_unique() if tl.height else 0
        checks.append(check("Private ledger", n_co > 0,
                            f"{n_co} companies, {tl.height} timeline rows, {lt.height} latest values, "
                            f"{rep.height} reported-unconfirmed rows, {iv.height} derived intervals"))
        checks.append(uniqueness(tl, TIMELINE_KEY, "timeline rows", name="Ledger key uniqueness"))
        checks.append(check("Latest-value ties", ties == 0,
                            f"{ties} company-metric(s) with two current rows on the same date", warn=True))
        stale = int(tl["stale"].sum()) if tl.height else 0
        checks.append(check("Private ledger freshness", stale == 0,
                            f"{stale} timeline row(s) unchecked for more than {reference.SPEC.stale_days} days",
                            warn=True))
        leaked = sum(int(t.filter(pl.col("metric_kind").is_in(config.NEVER_CHARTED_KINDS)).height)
                     for t in (tl, lt) if "metric_kind" in t.columns)
        checks.append(check("Never-charted kinds kept out", leaked == 0,
                            f"{leaked} reported_talks / third_party_estimate row(s) in charted tables; "
                            f"{rep.filter(pl.col('reason') == 'never_charted_kind').height} listed as reported"))
    if kpi:
        tr, ref = take_rates(kpi)
        tables |= {"take_rates": tr, "take_rates_refused": ref}
        no_rate = sorted({f"{p.company}/{p.ratio}" for p in TAKE_RATE_PAIRS}
                         - {f"{c}/{r}" for c, r in zip(tr["company"], tr["ratio"], strict=True)})
        checks.append(check("Take rates", not no_rate,
                            f"{tr.height} computed across {tr['company'].n_unique() if tr.height else 0} companies, "
                            f"{ref.height} period(s) refused" + (f"; no rate at all: {', '.join(no_rate)}"
                                                                if no_rate else ""), warn=True))
        mixed = tr.filter(pl.col("qualifier") != "=").height if tr.height else 0
        checks.append(check("Take-rate qualifiers", True,
                            f"{mixed} of {tr.height} take rates carry a bound or ~ from their inputs"))
    return tables, checks


def write(tables: dict[str, pl.DataFrame], out_dir: Path | None = None) -> list[Path]:
    out = out_dir or MARTS_DIR / "payments"
    return [write_json(df.to_dicts(), out / f"{name}.json") for name, df in tables.items()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and validate, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    tables, checks = build()
    for c in checks:
        log.info("[%s] %s: %s", c["status"], c["name"], c["detail"])
    if not args.dry_run:
        for p in write(tables):
            log.info("wrote %s", p)
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
