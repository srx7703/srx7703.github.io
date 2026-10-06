"""Who keeps what from $100: per-$100 waterfalls and the sensitivity matrix (PLAN §3 M2 and M5, EXECUTION W2b).

Every line is "dollars a layer receives (or gives up) per $100 of the volume that layer is measured on". A
percentage of volume and a dollar figure per $100 are the same number, so a rate sheet's ``2.04% + $0.10`` and a
10-K's ``merchant network revenue / GMV`` land in one column, ``usd_per_100``.

Inputs
------
``data/reference/payments/waterfall_inputs.json`` (W1d, verified), read through :func:`reference.rows`, which
validates the file and strips private fields. Only rows with ``chartable: true`` (tag ``V`` or ``C``) are used;
a waterfall whose input is missing or not chartable is skipped and the skip is recorded as a failed check, so
the other waterfalls still land (CLAUDE.md rule 5).

The four books
--------------
``card_acquiring_us``   one $100 online card payment: what the merchant pays at Stripe's list price, what the
                        issuer receives at Visa's Traditional Rewards card-not-present rate, and the residual left
                        to the acquirer *and* the network together. Visa does not publish network fees, so the
                        network layer is a ``gap`` line (no value) and the residual is never called acquirer margin.
``bnpl_affirm``         Affirm FY2026 (July 2025 - June 2026): each filed revenue line and each of the four
                        transaction-cost lines divided by GMV, then revenue less transaction costs as the residual.
``corporate_card_bill`` BILL Spend and Expense, quarter ending 2026-06-30: transaction fees and rewards divided by
                        card TPV, net interchange after rewards as the residual, and Visa's Commercial CNP list
                        rate as a non-additive ``reference`` line.
``cross_border_wise`` / ``cross_border_fsb``  Wise's cross-border take rate by period against the FSB's average
                        cost of an MSME B2B cross-border payment (a ``benchmark``; never added to Wise).

Fixed per-transaction fees need a ticket size. Every waterfall here is quoted for one ``$100`` ticket
(:data:`TICKET_USD`), which is what "per $100" means for a rate sheet: ``2.04% + $0.10`` is ``$2.14``.

Roles (what may be added to what)
---------------------------------
``paid``       what the payer hands over (card acquiring only).
``take``       revenue a layer receives; additive within one waterfall and one period.
``cost``       a negative line (rewards, credit losses, funding); additive with ``take``.
``subtotal``   a sum of ``take`` lines, shown for reconciliation against a filed total.
``residual``   what is left: ``paid - takes`` or ``takes + costs``. Only written when it is derivable.
``component``  a published split of a benchmark (FSB FX margin and fee).
``reference`` / ``benchmark``  a list rate or an industry average beside the waterfall. Never added to it.
``gap``        a layer that exists but is not disclosed; value null.

Nothing is ever summed across waterfalls, periods or measurement layers. :func:`layer_totals` groups within one
waterfall and period only.

Qualifiers
----------
Every curated number carries ``=``, ``>``, ``<`` or ``~`` and a derived number inherits one by this rule
(:func:`propagate`), applied to the operations used here (sums, differences, products and ratios of positive
quantities):

1. Work out, for each input, the direction the result moves when that input rises (``+1`` or ``-1``). A sum
   or a numerator is ``+1``; a subtracted term or a denominator is ``-1``.
2. Translate each input's qualifier into a bound on the *result*: a ``>`` input with direction ``+1`` makes
   the result a lower bound (``>``); with direction ``-1`` an upper bound (``<``). ``<`` is the mirror image.
   ``=`` contributes nothing.
3. Any ``~`` input makes the result ``~``. No bounds at all gives ``=``. Bounds that all point the same way give
   that bound. Bounds that point opposite ways (a lower-bounded numerator over a lower-bounded denominator, say)
   give no bound at all: the result is ``~`` by default, or :class:`QualifierConflict` is raised when the caller
   asks for ``strict=True`` (take rates do, and record the refusal instead of charting a guess).

Sensitivity (registered bands)
------------------------------
:func:`sensitivity` varies the inputs that the registered thresholds in section H turn on, over grids that span
those thresholds, and returns tidy rows ``(question, scenario, input, input_value, output, output_value, ...)``:

* **Q4** net interchange after rewards = gross take x (1 - rewards share), over rewards shares 30-70% and gross
  take cut by 0/10/20 bps, graded against the 1.0-1.6% band and the 0.8% / 2.0% refutation zone, plus the
  break-even rewards share for each threshold.
* **Q7** Wise's next take rate against FY2026 (cut of at least 3 bps holds, flat or up refutes) and the FSB
  2026 average cost (at least 1.5% holds, 1.4% or below refutes).
* **Q8** a cut of 0-30 bps to Visa's Commercial CNP rate (2.60% + $0.10 or above holds; a cut of more than
  10 bps refutes) and what each cut does to BILL's net interchange.
* **M5** the PLAN's common shocks per book: credit losses +/-50%, interchange -10 bps, rewards share +10 pp,
  10% of merchants refusing commercial cards, the consumer card-product mix, and rate +/-100 bps where it is
  derivable (it is not from these inputs: rows say so rather than guess).

Usage::

    uv run python -m pipelines.payments.economics [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, write_json
from pipelines.payments import config, reference

log = logging.getLogger("payments.economics")

TICKET_USD = 100.0
QUALIFIERS = ("=", ">", "<", "~")
CHARTABLE_TAGS = ("V", "C")
ROLES = ("paid", "take", "cost", "subtotal", "residual", "component", "reference", "benchmark", "gap")
ADDITIVE_ROLES = ("take", "cost")
VERDICTS = ("inside_band", "outside_band", "refute_zone", "holds", "refuted", "neither", "not_derivable", "info")


# --- qualifier algebra -----------------------------------------------------------------------

_FLIP = {">": "<", "<": ">", "=": "=", "~": "~"}


class QualifierConflict(ValueError):
    """A derived number would mix a lower and an upper bound, so it has no bound at all."""


@dataclass(frozen=True)
class Num:
    """A number and its qualifier: ``Num(1.4e12, '>')`` is "more than 1.4 trillion"."""

    value: float
    qualifier: str = "="

    def __post_init__(self) -> None:
        if self.qualifier not in QUALIFIERS:
            raise ValueError(f"qualifier {self.qualifier!r} not in {QUALIFIERS}")


def propagate(terms: Iterable[tuple[str, int]], *, strict: bool = False) -> str:
    """Qualifier of a result from ``(input qualifier, direction of the result in that input)`` pairs.

    See the module docstring for the rule. ``strict`` raises :class:`QualifierConflict` instead of
    returning ``~`` when bounds point opposite ways.
    """
    terms = list(terms)
    for q, s in terms:
        if q not in QUALIFIERS:
            raise ValueError(f"qualifier {q!r} not in {QUALIFIERS}")
        if s not in (1, -1):
            raise ValueError(f"direction must be +1 or -1, got {s!r}")
    if any(q == "~" for q, _ in terms):
        return "~"
    bounds = {q if s > 0 else _FLIP[q] for q, s in terms if q != "="}
    if not bounds:
        return "="
    if len(bounds) == 1:
        return bounds.pop()
    if strict:
        raise QualifierConflict("inputs bound the result from opposite sides (> with <)")
    return "~"


def q_sum(*xs: Num, strict: bool = False) -> Num:
    return Num(sum(x.value for x in xs), propagate(((x.qualifier, 1) for x in xs), strict=strict))


def q_sub(a: Num, b: Num, *, strict: bool = False) -> Num:
    """``a - b``; the subtracted term's bound flips."""
    return Num(a.value - b.value, propagate([(a.qualifier, 1), (b.qualifier, -1)], strict=strict))


def q_mul(a: Num, b: Num, *, strict: bool = False) -> Num:
    """Product of two positive quantities: increasing in both."""
    if a.value < 0 or b.value < 0:
        raise ValueError("q_mul is defined for non-negative quantities")
    return Num(a.value * b.value, propagate([(a.qualifier, 1), (b.qualifier, 1)], strict=strict))


def q_div(a: Num, b: Num, *, strict: bool = False) -> Num:
    """Ratio of two positive quantities: increasing in the numerator, decreasing in the denominator."""
    if b.value <= 0 or a.value < 0:
        raise ValueError("q_div is defined for a non-negative numerator over a positive denominator")
    return Num(a.value / b.value, propagate([(a.qualifier, 1), (b.qualifier, -1)], strict=strict))


def q_scale(a: Num, k: float) -> Num:
    """Multiply by an exact constant; a negative constant flips the bound."""
    return Num(a.value * k, a.qualifier if k >= 0 else _FLIP[a.qualifier])


def q_neg(a: Num) -> Num:
    return q_scale(a, -1.0)


def interval(value: float | None, qualifier: str | None) -> tuple[float | None, float | None, float | None]:
    """``(lower, upper, estimate)`` implied by a qualified number.

    ``=`` is a point (all three equal); ``>`` only a lower bound; ``<`` only an upper bound; ``~`` an estimate
    with no bound on either side.
    """
    if value is None or qualifier is None:
        return None, None, None
    if qualifier == "=":
        return value, value, value
    if qualifier == ">":
        return value, None, None
    if qualifier == "<":
        return None, value, None
    if qualifier == "~":
        return None, None, value
    raise ValueError(f"qualifier {qualifier!r} not in {QUALIFIERS}")


# --- registered thresholds (parsed from config, which test_plan ties to section H) ------------------


def pct(text: str) -> float:
    """``'1.6%'`` -> 1.6 (percent of volume, i.e. dollars per $100)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*%\s*", text)
    if not m:
        raise ValueError(f"not a percentage: {text!r}")
    return float(m.group(1))


def bps(text: str) -> float:
    """``'3 bps'`` -> 0.03 (dollars per $100)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*bps\s*", text)
    if not m:
        raise ValueError(f"not basis points: {text!r}")
    return float(m.group(1)) / 100.0


def rate_plus_fixed(text: str) -> tuple[float, float]:
    """``'2.60% + $0.10'`` -> (2.60, 0.10)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s*\+\s*\$(\d+(?:\.\d+)?)\s*", text)
    if not m:
        raise ValueError(f"not 'x% + $y': {text!r}")
    return float(m.group(1)), float(m.group(2))


def per_100(pct_rate: float, fixed_usd: float, ticket: float = TICKET_USD) -> float:
    """Dollars per $100 of volume for a rate of ``pct_rate``% plus ``fixed_usd`` per transaction."""
    return pct_rate + fixed_usd * 100.0 / ticket


# --- schemas ---------------------------------------------------------------------------------

LINE_KEY = ["waterfall", "period", "line"]
LINE_DTYPES: dict[str, pl.DataType] = {
    "waterfall": pl.Utf8,
    "segment": pl.Utf8,
    "company": pl.Utf8,
    "period": pl.Utf8,
    "as_of": pl.Utf8,
    "layer": pl.Utf8,
    "line": pl.Utf8,
    "role": pl.Utf8,
    "order": pl.Int64,
    "usd_per_100": pl.Float64,
    "qualifier": pl.Utf8,
    "formula": pl.Utf8,
    "inputs": pl.Utf8,
    "source_url": pl.Utf8,
    "tag": pl.Utf8,
    "chartable": pl.Boolean,
    "stale": pl.Boolean,
    "note": pl.Utf8,
}


def _value_iff_not_gap(df: pa.PolarsData) -> pl.LazyFrame:
    return df.lazyframe.select((pl.col("role") == "gap") == pl.col("usd_per_100").is_null())


def _qualifier_iff_value(df: pa.PolarsData) -> pl.LazyFrame:
    return df.lazyframe.select(pl.col("usd_per_100").is_null() == pl.col("qualifier").is_null())


def _costs_negative(df: pa.PolarsData) -> pl.LazyFrame:
    return df.lazyframe.select((pl.col("role") != "cost") | (pl.col("usd_per_100") <= 0))


LINE_SCHEMA = pa.DataFrameSchema(
    {
        "waterfall": pa.Column(str),
        "segment": pa.Column(str, pa.Check.isin(list(reference.SEGMENTS))),
        "company": pa.Column(str),
        "period": pa.Column(str),
        "as_of": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "layer": pa.Column(str),
        "line": pa.Column(str),
        "role": pa.Column(str, pa.Check.isin(list(ROLES))),
        "order": pa.Column(pl.Int64, pa.Check.ge(0)),
        "usd_per_100": pa.Column(pl.Float64, pa.Check.in_range(-100.0, 100.0), nullable=True),
        "qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS)), nullable=True),
        "formula": pa.Column(str),
        "inputs": pa.Column(str),
        "source_url": pa.Column(str, pa.Check.str_startswith("https://")),
        "tag": pa.Column(str, pa.Check.isin(list(CHARTABLE_TAGS))),  # only V/C reach a mart
        "chartable": pa.Column(pl.Boolean, pa.Check.eq(True)),
        "stale": pa.Column(pl.Boolean),
        "note": pa.Column(str, nullable=True),
    },
    unique=LINE_KEY,
    strict=True,
    coerce=False,
    checks=[
        pa.Check(_value_iff_not_gap, error="gap lines carry no value; every other line does"),
        pa.Check(_qualifier_iff_value, error="a value needs a qualifier and a gap has none"),
        pa.Check(_costs_negative, error="cost lines are negative"),
    ],
)

SENS_KEY = ["question", "waterfall", "scenario", "input_value", "output"]
SENS_DTYPES: dict[str, pl.DataType] = {
    "question": pl.Utf8,
    "waterfall": pl.Utf8,
    "scenario": pl.Utf8,
    "input": pl.Utf8,
    "input_value": pl.Float64,
    "input_unit": pl.Utf8,
    "output": pl.Utf8,
    "output_value": pl.Float64,
    "qualifier": pl.Utf8,
    "base_output_value": pl.Float64,
    "delta": pl.Float64,
    "band_low": pl.Float64,
    "band_high": pl.Float64,
    "refute_low": pl.Float64,
    "refute_high": pl.Float64,
    "verdict": pl.Utf8,
    "note": pl.Utf8,
}
SENS_SCHEMA = pa.DataFrameSchema(
    {
        "question": pa.Column(str, pa.Check.isin(["Q4", "Q7", "Q8", "M5"])),
        "waterfall": pa.Column(str),
        "scenario": pa.Column(str),
        "input": pa.Column(str),
        "input_value": pa.Column(pl.Float64),
        "input_unit": pa.Column(str),
        "output": pa.Column(str),
        "output_value": pa.Column(pl.Float64, nullable=True),
        "qualifier": pa.Column(str, pa.Check.isin(list(QUALIFIERS)), nullable=True),
        "base_output_value": pa.Column(pl.Float64, nullable=True),
        "delta": pa.Column(pl.Float64, nullable=True),
        "band_low": pa.Column(pl.Float64, nullable=True),
        "band_high": pa.Column(pl.Float64, nullable=True),
        "refute_low": pa.Column(pl.Float64, nullable=True),
        "refute_high": pa.Column(pl.Float64, nullable=True),
        "verdict": pa.Column(str, pa.Check.isin(list(VERDICTS))),
        "note": pa.Column(str, nullable=True),
    },
    unique=SENS_KEY,
    strict=True,
    coerce=False,
    checks=[pa.Check(lambda d: d.lazyframe.select(
        (pl.col("verdict") == "not_derivable") == pl.col("output_value").is_null()),
        error="only not_derivable rows lack an output value")],
)


# --- input index -----------------------------------------------------------------------------


class MissingInput(KeyError):
    """A waterfall input is absent, ambiguous or not chartable."""


class Inputs:
    """Chartable ``waterfall_inputs`` rows by ``(waterfall, line[, period])``."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.usable = [r for r in rows if r.get("chartable") is True and r.get("tag") in CHARTABLE_TAGS]

    def row(self, waterfall: str, line: str, period: str | None = None) -> dict:
        hits = [r for r in self.usable if r["waterfall"] == waterfall and r["line"] == line
                and (period is None or r.get("period") == period)]
        if len(hits) != 1:
            any_row = [r for r in self.rows if r["waterfall"] == waterfall and r["line"] == line
                       and (period is None or r.get("period") == period)]
            why = ("not chartable" if any_row and not hits else
                   "absent" if not hits else f"ambiguous ({len(hits)} rows; pass a period)")
            raise MissingInput(f"{waterfall}/{line}" + (f"/{period}" if period else "") + f": {why}")
        return hits[0]

    def num(self, waterfall: str, line: str, period: str | None = None) -> Num:
        r = self.row(waterfall, line, period)
        return Num(float(r["value"]), r["qualifier"])


@dataclass
class _Line:
    layer: str
    line: str
    role: str
    num: Num | None
    formula: str
    inputs: list[dict]
    tag: str = "C"
    note: str | None = None


def _frame(waterfall: str, segment: str, company: str, period: str, lines: list[_Line]) -> pl.DataFrame:
    recs = []
    for i, ln in enumerate(lines):
        srcs = list(dict.fromkeys(r["source_url"] for r in ln.inputs))
        recs.append({
            "waterfall": waterfall, "segment": segment, "company": company, "period": period,
            "as_of": max(r["as_of_date"] for r in ln.inputs),
            "layer": ln.layer, "line": ln.line, "role": ln.role, "order": i,
            "usd_per_100": None if ln.num is None else round(ln.num.value, 6),
            "qualifier": None if ln.num is None else ln.num.qualifier,
            "formula": ln.formula,
            "inputs": ", ".join(f"{r['waterfall']}/{r['line']}" for r in ln.inputs),
            "source_url": " ".join(srcs),
            "tag": ln.tag,
            "chartable": all(r.get("chartable") is True for r in ln.inputs),
            "stale": any(bool(r.get("stale")) for r in ln.inputs),
            "note": ln.note,
        })
    return pl.DataFrame(recs, schema=LINE_DTYPES)


# --- the four books --------------------------------------------------------------------------

VISA_CONSUMER_TIERS = {
    "traditional_rewards": "interchange_credit_consumer_cnp_traditional_rewards",
    "all_other": "interchange_credit_consumer_cnp_all_other",
    "signature_preferred": "interchange_credit_consumer_cnp_signature_preferred",
    "infinite_spend_qualified": "interchange_credit_consumer_cnp_infinite_spend_qualified",
    "debit_exempt": "interchange_debit_exempt_cnp",
    "debit_regulated": "interchange_debit_regulated_cnp",
}
BASE_CONSUMER_TIER = "traditional_rewards"


def _rate_line(inp: Inputs, waterfall: str, stem: str) -> tuple[Num, list[dict], str]:
    """``<stem>_pct`` + ``<stem>_fixed`` on a $100 ticket."""
    p_row, f_row = inp.row(waterfall, f"{stem}_pct"), inp.row(waterfall, f"{stem}_fixed")
    p, f = Num(float(p_row["value"]), p_row["qualifier"]), Num(float(f_row["value"]), f_row["qualifier"])
    n = q_sum(p, q_scale(f, 100.0 / TICKET_USD))
    return n, [p_row, f_row], f"{p.value:g} + {f.value:.2f} x 100 / {TICKET_USD:g}"


def card_acquiring_us(inp: Inputs) -> pl.DataFrame:
    wf = "card_acquiring_us"
    paid, paid_rows, paid_f = _rate_line(inp, wf, "list_price_stripe_domestic_card")
    issuer, iss_rows, iss_f = _rate_line(inp, wf, VISA_CONSUMER_TIERS[BASE_CONSUMER_TIER])
    resid = q_sub(paid, issuer)
    lines = [
        _Line("merchant", "merchant_price_stripe_list", "paid", paid, paid_f, paid_rows,
              note="Stripe list price for a domestic card; negotiated pricing for large merchants is lower."),
        _Line("issuer", "interchange_visa_traditional_rewards_cnp", "take", issuer, iss_f, iss_rows,
              note="Visa Traditional Rewards credit, card not present, sheet effective 2026-04-18."),
        _Line("network", "network_fees", "gap", None, "not disclosed", iss_rows,
              note="Visa does not publish US network assessment fees; part of the residual below."),
        _Line("acquirer + network", "acquirer_and_network_residual", "residual", resid,
              f"{paid.value:.2f} - {issuer.value:.2f}", paid_rows + iss_rows,
              note="Acquirer markup and network fees together; not acquirer margin."),
    ]
    return _frame(wf, "A", "Stripe / Visa", "2026-04-18 sheet; list price 2026-10-03", lines)


AFFIRM_REVENUE = [  # (input line, layer)
    ("revenue_merchant_network", "merchant side"),
    ("revenue_card_network", "card network side"),
    ("revenue_interest_income", "credit side"),
    ("revenue_gain_on_sales_of_loans", "credit side"),
    ("revenue_servicing_income", "credit side"),
]
AFFIRM_COSTS = ["cost_loss_on_loan_purchase_commitment", "cost_provision_for_credit_losses", "cost_funding",
                "cost_processing_and_servicing"]


def bnpl_affirm(inp: Inputs) -> pl.DataFrame:
    wf = "bnpl_affirm"
    gmv_row = inp.row(wf, "gmv")
    gmv = Num(float(gmv_row["value"]), gmv_row["qualifier"])
    lines: list[_Line] = []
    revs: list[Num] = []
    for name, layer in AFFIRM_REVENUE:
        r = inp.row(wf, name)
        x = q_div(q_scale(Num(float(r["value"]), r["qualifier"]), 100.0), gmv)
        revs.append(x)
        lines.append(_Line(layer, name.removeprefix("revenue_"), "take", x,
                           f"{r['value'] / 1e6:,.3f}M / {gmv.value / 1e6:,.1f}M x 100", [r, gmv_row]))
    rev_rows = [inp.row(wf, n) for n, _ in AFFIRM_REVENUE]
    subtotal = q_sum(*revs)
    lines.append(_Line("all revenue", "total_revenue", "subtotal", subtotal,
                       "sum of the five revenue lines per $100", rev_rows + [gmv_row]))
    costs: list[Num] = []
    cost_rows = []
    for name in AFFIRM_COSTS:
        r = inp.row(wf, name)
        cost_rows.append(r)
        x = q_neg(q_div(q_scale(Num(float(r["value"]), r["qualifier"]), 100.0), gmv))
        costs.append(x)
        lines.append(_Line("transaction costs", name.removeprefix("cost_"), "cost", x,
                           f"-{r['value'] / 1e6:,.3f}M / {gmv.value / 1e6:,.1f}M x 100", [r, gmv_row]))
    resid = q_sum(subtotal, *costs)
    lines.append(_Line("Affirm (kept)", "revenue_less_transaction_costs", "residual", resid,
                       "total revenue per $100 + the four transaction-cost lines per $100",
                       rev_rows + cost_rows + [gmv_row],
                       note="Revenue less transaction costs (Affirm's non-GAAP RLTC), computed from filed lines."))
    return _frame(wf, "B", "Affirm", gmv_row["period"], lines)


def corporate_card_bill(inp: Inputs) -> pl.DataFrame:
    wf = "corporate_card_bill"
    tpv_r, fee_r, rew_r = (inp.row(wf, n) for n in ("spend_expense_tpv", "spend_expense_transaction_fees",
                                                     "rewards_expense"))
    tpv = Num(float(tpv_r["value"]), tpv_r["qualifier"])
    fees = Num(float(fee_r["value"]), fee_r["qualifier"])
    rew = Num(float(rew_r["value"]), rew_r["qualifier"])
    gross = q_div(q_scale(fees, 100.0), tpv)
    rewards = q_neg(q_div(q_scale(rew, 100.0), tpv))
    net = q_sum(gross, rewards)
    ref, ref_rows, ref_f = _rate_line(inp, wf, "interchange_commercial_cnp")
    lines = [
        _Line("BILL (card program)", "transaction_fees", "take", gross,
              f"{fees.value / 1e6:.1f}M / {tpv.value / 1e6:,.0f}M x 100", [fee_r, tpv_r],
              note="Mainly interchange; BILL does not split interchange from other card fees."),
        _Line("cardholder", "rewards", "cost", rewards,
              f"-{rew.value / 1e6:.1f}M / {tpv.value / 1e6:,.0f}M x 100", [rew_r, tpv_r]),
        _Line("BILL (card program)", "net_interchange_after_rewards", "residual", net,
              f"({fees.value / 1e6:.1f}M - {rew.value / 1e6:.1f}M) / {tpv.value / 1e6:,.0f}M x 100",
              [fee_r, rew_r, tpv_r],
              note="Before credit losses, processing and funding costs."),
        _Line("issuer (Visa list rate)", "visa_commercial_cnp_list", "reference", ref, ref_f, ref_rows,
              note="Visa Commercial Card Not Present rate on a $100 ticket; a list rate, not BILL's realised "
                   "rate, and not added to the lines above."),
    ]
    return _frame(wf, "C", "BILL", tpv_r["period"], lines)


def cross_border(inp: Inputs) -> list[pl.DataFrame]:
    out: list[pl.DataFrame] = []
    wf = "cross_border_wise"
    for line, layer in (("take_rate", "Wise (all customers)"), ("take_rate_business", "Wise (business customers)")):
        for r in sorted((r for r in inp.usable if r["waterfall"] == wf and r["line"] == line),
                        key=lambda r: r["as_of_date"]):
            n = Num(float(r["value"]), r["qualifier"])
            out.append(_frame(wf, "D", "Wise", r["period"], [
                _Line(layer, f"cross_border_{line}", "take", n, f"stated {n.value:g}% of cross-border volume",
                      [r], tag="V", note="Cross-border fee only; excludes card, interest and other revenue.")]))
    wf = "cross_border_fsb"
    avg = [r for r in inp.usable if r["waterfall"] == wf and re.fullmatch(r"avg_cost_b2b_msme_\d{4}", r["line"])]
    for r in sorted(avg, key=lambda r: r["as_of_date"]):
        lines = [_Line("industry average (FSB)", "avg_cost_b2b_msme", "benchmark", Num(float(r["value"]),
                       r["qualifier"]), f"stated {r['value']:g}%", [r], tag="V",
                       note="G20 average cost of an MSME B2B cross-border payment; a benchmark, never added to Wise.")]
        for comp in ("fx_component", "fee_component"):
            try:
                c = inp.row(wf, f"avg_cost_b2b_msme_{comp}", r["period"])
            except MissingInput:
                continue
            lines.append(_Line("industry average (FSB)", f"avg_cost_b2b_msme_{comp}", "component",
                               Num(float(c["value"]), c["qualifier"]), f"stated {c['value']:g}%", [c], tag="V"))
        out.append(_frame(wf, "D", "FSB", r["period"], lines))
    return out


EXPECTED_WATERFALLS = ("card_acquiring_us", "bnpl_affirm", "corporate_card_bill", "cross_border_wise",
                       "cross_border_fsb")
BUILDERS = {
    "card_acquiring_us": lambda inp: [card_acquiring_us(inp)],
    "bnpl_affirm": lambda inp: [bnpl_affirm(inp)],
    "corporate_card_bill": lambda inp: [corporate_card_bill(inp)],
    "cross_border": cross_border,
}


def waterfalls(rows: list[dict]) -> tuple[pl.DataFrame, list[dict]]:
    """All waterfall lines (chartable only) and a check per book; a failing book does not stop the others."""
    inp = Inputs(rows)
    frames: list[pl.DataFrame] = []
    checks: list[dict] = []
    for name, build in BUILDERS.items():
        try:
            frames.extend(build(inp))
        except (MissingInput, ValueError) as exc:
            log.error("waterfall %s skipped: %s", name, exc)
            checks.append(check(f"Waterfall {name}", False, f"skipped: {exc}"))
    df = pl.concat(frames) if frames else pl.DataFrame(schema=LINE_DTYPES)
    dropped = df.filter(~pl.col("chartable"))
    if dropped.height:
        checks.append(check("Waterfall chartable inputs", False,
                            f"{dropped.height} line(s) rest on a non-chartable input and were dropped", warn=True))
    df = df.filter(pl.col("chartable")).sort(["waterfall", "period", "order"])
    return LINE_SCHEMA.validate(df), checks  # type: ignore[return-value]


def layer_totals(lines: pl.DataFrame) -> pl.DataFrame:
    """Sum of additive lines (``take``/``cost``) per waterfall, period and layer, with propagated qualifiers.

    Grouping always includes waterfall and period, so nothing is summed across books or periods.
    """
    add = lines.filter(pl.col("role").is_in(list(ADDITIVE_ROLES)))
    out = []
    for (wf, period, layer), g in add.group_by(["waterfall", "period", "layer"], maintain_order=True):
        n = q_sum(*(Num(v, q) for v, q in zip(g["usd_per_100"], g["qualifier"], strict=True)))
        out.append({"waterfall": wf, "period": period, "layer": layer, "usd_per_100": n.value,
                    "qualifier": n.qualifier, "lines": g.height})
    return pl.DataFrame(out, schema={"waterfall": pl.Utf8, "period": pl.Utf8, "layer": pl.Utf8,
                                     "usd_per_100": pl.Float64, "qualifier": pl.Utf8, "lines": pl.Int64})


def reconciliation_checks(lines: pl.DataFrame, rows: list[dict]) -> list[dict]:
    """Computed per-$100 lines against the totals and ratios the companies (or W1 curators) stated."""
    inp = Inputs(rows)
    out: list[dict] = []

    def line(wf: str, name: str) -> float | None:
        hit = lines.filter((pl.col("waterfall") == wf) & (pl.col("line") == name))
        return None if hit.is_empty() else float(hit["usd_per_100"][0])

    def compare(name: str, computed: float | None, stated_fn, tol: float, what: str) -> None:
        try:
            stated = stated_fn()
        except MissingInput as exc:
            out.append(check(name, False, f"cannot check: {exc}", warn=True))
            return
        if computed is None:
            out.append(check(name, False, "computed line missing", warn=True))
            return
        ok = abs(computed - stated) <= tol
        out.append(check(name, ok, f"computed {computed:.4f} vs {what} {stated:.4f} (tolerance {tol:g})"))

    gmv = lambda: inp.num("bnpl_affirm", "gmv").value  # noqa: E731
    compare("Affirm revenue reconciles", line("bnpl_affirm", "total_revenue"),
            lambda: inp.num("bnpl_affirm", "revenue_total").value / gmv() * 100, 0.005, "filed total revenue / GMV")
    compare("Affirm merchant take vs stated", line("bnpl_affirm", "merchant_network"),
            lambda: inp.num("bnpl_affirm", "merchant_network_revenue_pct_gmv").value, 0.05,
            "stated % of GMV (rounded to 0.1)")
    for name, curated in (("transaction_fees", "take_rate_gross"), ("rewards", "rewards"),
                          ("net_interchange_after_rewards", "net_interchange_after_rewards")):
        v = line("corporate_card_bill", name)
        compare(f"BILL {name} vs curated", None if v is None else abs(v),
                lambda c=curated: inp.num("corporate_card_bill", c).value, 0.005, "W1 computed row")
    fees = lambda: inp.num("corporate_card_bill", "spend_expense_transaction_fees").value  # noqa: E731
    try:
        share = inp.num("corporate_card_bill", "rewards_expense").value / fees() * 100
    except MissingInput:
        share = None
    compare("BILL rewards share vs stated", share,
            lambda: inp.num("corporate_card_bill", "rewards_pct_interchange").value, 1.0,
            "stated % of interchange (rounded; BILL's base is interchange, ours transaction fees)")
    compare("Wise FY27-Q1 take rate recomputes",
            _safe(lambda: inp.num("cross_border_wise", "cross_border_revenue", "FY2027-Q1").value
                  / inp.num("cross_border_wise", "cross_border_volume", "FY2027-Q1").value * 100),
            lambda: inp.num("cross_border_wise", "take_rate", "FY2027-Q1").value, 0.01,
            "stated take rate (rounded to 0.01)")
    compare("FSB components sum", _safe(lambda: inp.num("cross_border_fsb", "avg_cost_b2b_msme_fx_component").value
                                        + inp.num("cross_border_fsb", "avg_cost_b2b_msme_fee_component").value),
            lambda: inp.num("cross_border_fsb", "avg_cost_b2b_msme_2025").value, 0.051, "stated 2025 average")
    q8_current = per_100(*rate_plus_fixed(config.Q8["current"]))
    compare("Q8 registered rate matches sheet", q8_current,
            lambda: _rate_line(inp, "corporate_card_bill", "interchange_commercial_cnp")[0].value, 1e-9,
            "Visa sheet Commercial CNP per $100")
    return out


def _safe(fn) -> float | None:
    try:
        return fn()
    except MissingInput:
        return None


# --- sensitivity -----------------------------------------------------------------------------


def _sens(question: str, waterfall: str, scenario: str, inp_name: str, inp_value: float, inp_unit: str,
          output: str, out: Num | None, base: float | None, verdict: str, *, band: tuple | None = None,
          refute: tuple | None = None, note: str | None = None) -> dict:
    val = None if out is None else round(out.value, 6)
    return {
        "question": question, "waterfall": waterfall, "scenario": scenario, "input": inp_name,
        "input_value": round(float(inp_value), 6), "input_unit": inp_unit, "output": output,
        "output_value": val, "qualifier": None if out is None else out.qualifier,
        "base_output_value": None if base is None else round(base, 6),
        "delta": None if (val is None or base is None) else round(val - base, 6) + 0.0,  # no -0.0
        "band_low": band[0] if band else None, "band_high": band[1] if band else None,
        "refute_low": refute[0] if refute else None, "refute_high": refute[1] if refute else None,
        "verdict": verdict, "note": note,
    }


def _grid(lo: float, hi: float, step: float) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 6) for i in range(n + 1)]


def q4_verdict(net: float) -> str:
    lo, hi = (pct(x) for x in config.Q4["band"])
    r_hi, r_lo = pct(config.Q4["refute_high"]), pct(config.Q4["refute_low"])
    if net > r_hi or net < r_lo:
        return "refute_zone"
    return "inside_band" if lo <= net <= hi else "outside_band"


def q7_wise_verdict(change_per_100: float) -> str:
    """Year-on-year change in Wise's take rate (dollars per $100): a cut of 3 bps or more holds, flat or up refutes."""
    c = round(change_per_100, 6)
    if c <= -bps(config.Q7["wise_cut_bps"]):
        return "holds"
    return "refuted" if c >= 0 else "neither"


def q7_fsb_verdict(avg_cost: float) -> str:
    a = round(avg_cost, 6)
    if a >= pct(config.Q7["fsb_floor"]):
        return "holds"
    return "refuted" if a <= pct(config.Q7["fsb_refute"]) else "neither"


def q8_verdict(cut_per_100: float) -> str:
    """Cut to the ad valorem Commercial CNP rate: more than 10 bps refutes; staying at or above the floor holds."""
    cur, _ = rate_plus_fixed(config.Q8["current"])
    floor, _ = rate_plus_fixed(config.Q8["floor"])
    c = round(cut_per_100, 6)
    if c > bps(config.Q8["cut_bps"]) or round(cur - c, 6) < floor:
        return "refuted"
    return "holds"


def sensitivity(rows: list[dict]) -> tuple[pl.DataFrame, list[dict]]:
    inp = Inputs(rows)
    recs: list[dict] = []
    checks: list[dict] = []
    for name, fn in (("Q4", _sens_q4), ("Q7", _sens_q7), ("Q8", _sens_q8), ("M5", _sens_m5)):
        try:
            recs.extend(fn(inp))
        except (MissingInput, ValueError) as exc:
            log.error("sensitivity %s skipped: %s", name, exc)
            checks.append(check(f"Sensitivity {name}", False, f"skipped: {exc}"))
    df = pl.DataFrame(recs, schema=SENS_DTYPES) if recs else pl.DataFrame(schema=SENS_DTYPES)
    return SENS_SCHEMA.validate(df), checks  # type: ignore[return-value]


def _bill_base(inp: Inputs) -> tuple[Num, Num, Num]:
    wf = "corporate_card_bill"
    tpv, fees, rew = (inp.num(wf, n) for n in ("spend_expense_tpv", "spend_expense_transaction_fees",
                                                 "rewards_expense"))
    gross = q_div(q_scale(fees, 100.0), tpv)
    share = q_div(rew, fees)  # rewards / transaction fees
    net = q_sub(gross, q_div(q_scale(rew, 100.0), tpv))
    return gross, share, net


def _sens_q4(inp: Inputs) -> list[dict]:
    wf = "corporate_card_bill"
    gross, share, net = _bill_base(inp)
    band = tuple(pct(x) for x in config.Q4["band"])
    refute = (pct(config.Q4["refute_low"]), pct(config.Q4["refute_high"]))
    out = []
    for cut_bps in (0, 10, 20):
        g = q_sub(gross, Num(cut_bps / 100.0))
        for s in _grid(0.30, 0.70, 0.05):
            n = q_mul(g, Num(1.0 - s))
            out.append(_sens("Q4", wf, f"rewards_share|gross_cut_{cut_bps}bps", "rewards share of transaction fees",
                             s * 100, "percent", "net_interchange_usd_per_100", n, net.value, q4_verdict(n.value),
                             band=band, refute=refute,
                             note=f"gross take {g.value:.4f} per $100 ({cut_bps} bps below BILL FQ4'26)"))
    for thr in sorted({*band, *refute}):
        s = q_sub(Num(1.0), q_div(Num(thr), gross))
        out.append(_sens("Q4", wf, "breakeven_rewards_share", "net interchange threshold", thr, "usd_per_100",
                         "rewards_share_pct", q_scale(s, 100.0), share.value * 100, "info", band=band, refute=refute,
                         note=f"rewards share at which net interchange equals {thr:g} at gross {gross.value:.4f}"))
    out.append(_sens("Q4", wf, "base", "rewards share of transaction fees", share.value * 100, "percent",
                     "net_interchange_usd_per_100", net, net.value, q4_verdict(net.value), band=band, refute=refute,
                     note="BILL Spend and Expense, quarter ending 2026-06-30"))
    return out


def _sens_q7(inp: Inputs) -> list[dict]:
    wf = "cross_border_wise"
    fy26 = inp.num(wf, "take_rate", "FY2026")
    fy25 = inp.num(wf, "take_rate", "FY2025")
    out = [_sens("Q7", wf, "observed_fy25_fy26", "Wise take rate FY2026", fy26.value, "percent",
                 "yoy_change_usd_per_100", q_sub(fy26, fy25), None, q7_wise_verdict(fy26.value - fy25.value),
                 note="FY2025 0.58% to FY2026 0.52%")]
    for tr in _grid(0.45, 0.56, 0.01):
        ch = q_sub(Num(tr), fy26)
        out.append(_sens("Q7", wf, "wise_next_take_rate", "Wise H1 FY27 take rate", tr, "percent",
                         "change_vs_fy2026_usd_per_100", ch, 0.0, q7_wise_verdict(ch.value),
                         note="compared with the FY2026 annual rate; holds at a cut of 3 bps or more"))
    fsb = "cross_border_fsb"
    last = inp.num(fsb, "avg_cost_b2b_msme_2025")
    floor, refute = pct(config.Q7["fsb_floor"]), pct(config.Q7["fsb_refute"])
    for a in _grid(1.30, 1.70, 0.05):
        out.append(_sens("Q7", fsb, "fsb_2026_avg_cost", "FSB 2026 average B2B MSME cost", a, "percent",
                         "fsb_avg_cost_usd_per_100", Num(a), last.value, q7_fsb_verdict(a),
                         band=(floor, None), refute=(None, refute)))
        out.append(_sens("Q7", fsb, "fsb_2026_avg_cost", "FSB 2026 average B2B MSME cost", a, "percent",
                         "gap_to_wise_fy2026_usd_per_100", q_sub(Num(a), fy26), q_sub(last, fy26).value, "info",
                         note="FSB average minus Wise FY2026 take rate; different measures, shown side by side"))
    return out


def _sens_q8(inp: Inputs) -> list[dict]:
    wf = "corporate_card_bill"
    ref, _, _ = _rate_line(inp, wf, "interchange_commercial_cnp")
    fixed = inp.num(wf, "interchange_commercial_cnp_fixed")
    pct_now = inp.num(wf, "interchange_commercial_cnp_pct")
    floor, _ = rate_plus_fixed(config.Q8["floor"])
    gross, share, net = _bill_base(inp)
    out = []
    for cut in (0, 5, 10, 15, 20, 25, 30):
        c = cut / 100.0
        new = q_sum(q_sub(pct_now, Num(c)), q_scale(fixed, 100.0 / TICKET_USD))
        v = q8_verdict(c)
        out.append(_sens("Q8", wf, "commercial_cnp_cut", "cut to Commercial CNP rate", cut, "bps",
                         "interchange_usd_per_100", new, ref.value, v, band=(floor + fixed.value, None),
                         note="Visa Commercial Card Not Present on a $100 ticket"))
        out.append(_sens("Q8", wf, "commercial_cnp_cut", "cut to Commercial CNP rate", cut, "bps",
                         "bill_net_interchange_rewards_fixed_usd_per_100", q_sub(net, Num(c)), net.value, "info",
                         note="assumes BILL's gross take moves one for one with the list rate and rewards stay "
                              "at the same dollars per $100"))
        out.append(_sens("Q8", wf, "commercial_cnp_cut", "cut to Commercial CNP rate", cut, "bps",
                         "bill_net_interchange_rewards_share_fixed_usd_per_100",
                         q_mul(q_sub(gross, Num(c)), q_sub(Num(1.0), share)), net.value, "info",
                         note="assumes BILL's gross take moves one for one and rewards stay the same share"))
    return out


def _sens_m5(inp: Inputs) -> list[dict]:
    out: list[dict] = []
    # BNPL: credit losses +/-50%
    wf = "bnpl_affirm"
    gmv = inp.num(wf, "gmv")
    revenue = inp.num(wf, "revenue_total")
    costs = [inp.num(wf, c) for c in AFFIRM_COSTS]
    rltc = q_div(q_scale(q_sub(revenue, q_sum(*costs)), 100.0), gmv)
    prov = inp.num(wf, "cost_provision_for_credit_losses")
    for shock in (-0.5, 0.5):
        delta = q_div(q_scale(prov, 100.0 * abs(shock)), gmv)  # magnitude of the change per $100
        new = q_sub(rltc, delta) if shock > 0 else q_sum(rltc, delta)
        out.append(_sens("M5", wf, "credit_losses", "change in provision for credit losses", shock * 100,
                         "percent", "rltc_usd_per_100", new, rltc.value, "info"))
    for shock in (-100, 100):
        out.append(_sens("M5", wf, "rates", "change in funding rates", shock, "bps", "rltc_usd_per_100", None,
                         rltc.value, "not_derivable",
                         note="the funding balance is not among the curated inputs, so a rate shock cannot be "
                              "turned into dollars per $100"))
    # Corporate card: interchange -10 bps, rewards share +10 pp, 10% of merchants refuse commercial cards
    wf = "corporate_card_bill"
    gross, share, net = _bill_base(inp)
    out.append(_sens("M5", wf, "interchange_cut", "interchange cut", 10, "bps",
                     "net_interchange_usd_per_100", q_sub(net, Num(0.10)), net.value, "info",
                     note="rewards held at the same dollars per $100"))
    out.append(_sens("M5", wf, "rewards_share_up", "rewards share of transaction fees", 10, "percentage points",
                     "net_interchange_usd_per_100", q_mul(gross, q_sub(q_sub(Num(1.0), share), Num(0.10))),
                     net.value, "info"))
    out.append(_sens("M5", wf, "merchant_refusal", "share of spend at merchants refusing commercial cards", 10,
                     "percent", "net_interchange_usd_per_100_of_prior_spend", q_mul(net, Num(0.90)), net.value,
                     "info", note="the refused spend earns nothing; per $100 of spend before refusal"))
    # Card acquiring: interchange -10 bps at a fixed list price; card-product mix
    wf = "card_acquiring_us"
    paid, _, _ = _rate_line(inp, wf, "list_price_stripe_domestic_card")
    base_iss, _, _ = _rate_line(inp, wf, VISA_CONSUMER_TIERS[BASE_CONSUMER_TIER])
    base_res = q_sub(paid, base_iss)
    out.append(_sens("M5", wf, "interchange_cut", "interchange cut", 10, "bps",
                     "acquirer_and_network_residual_usd_per_100", q_sub(paid, q_sub(base_iss, Num(0.10))),
                     base_res.value, "info", note="merchant price held at Stripe's list price"))
    for i, (tier, stem) in enumerate(VISA_CONSUMER_TIERS.items()):
        try:
            iss, _, _ = _rate_line(inp, wf, stem)
        except MissingInput:
            continue
        out.append(_sens("M5", wf, f"card_product|{tier}", "Visa consumer card product (index)", i, "index",
                         "acquirer_and_network_residual_usd_per_100", q_sub(paid, iss), base_res.value, "info",
                         note=f"{tier.replace('_', ' ')} at {iss.value:.2f} per $100"))
    # Cross-border: rates move interest on balances, which the cross-border take rate excludes
    for shock in (-100, 100):
        out.append(_sens("M5", "cross_border_wise", "rates", "change in policy rates", shock, "bps",
                         "cross_border_take_usd_per_100", None, inp.num("cross_border_wise", "take_rate",
                                                                         "FY2026").value, "not_derivable",
                         note="interest on customer balances is outside the cross-border take rate and not "
                              "among the curated waterfall inputs"))
    return out


# --- run -------------------------------------------------------------------------------------


def build(today: date | None = None) -> tuple[dict[str, pl.DataFrame], list[dict]]:
    """Validated waterfall lines and sensitivity rows, plus the checks for ``facts.checks``."""
    try:
        rows = reference.rows("waterfall_inputs", today)
    except Exception as exc:  # noqa: BLE001 - a broken reference file is a failed check, not a crash
        log.error("waterfall_inputs unreadable: %s", exc)
        return ({"waterfall_lines": LINE_SCHEMA.validate(pl.DataFrame(schema=LINE_DTYPES)),
                 "sensitivity": SENS_SCHEMA.validate(pl.DataFrame(schema=SENS_DTYPES))},
                [check("Waterfall inputs", False, f"{type(exc).__name__}: {exc}"[:300])])
    lines, checks = waterfalls(rows)
    sens, s_checks = sensitivity(rows)
    checks += s_checks + reconciliation_checks(lines, rows)
    checks.append(uniqueness(lines, LINE_KEY, "waterfall lines", name="Waterfall key uniqueness"))
    stale = int(lines["stale"].sum()) if lines.height else 0
    checks.append(check("Waterfall input freshness", stale == 0,
                        f"{stale} line(s) rest on an input unchecked for more than {reference.SPEC.stale_days} days",
                        warn=True))
    books = set(lines["waterfall"].unique().to_list()) if lines.height else set()
    missing = sorted(set(EXPECTED_WATERFALLS) - books)
    checks.insert(0, check("Waterfalls", not missing,
                           f"{len(books)} waterfall(s), {lines.height} line(s), {sens.height} sensitivity row(s)"
                           + (f"; missing: {', '.join(missing)}" if missing else "")))
    return {"waterfall_lines": lines, "sensitivity": sens}, checks


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
