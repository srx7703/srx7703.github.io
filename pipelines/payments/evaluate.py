"""The Q1-Q9 scoreboard: each registered question's status, the readings it rests on, and when it is graded.

Section H of ``docs/EVALUATION_PLAN.md`` ("New payments companies (project H)") registered nine falsifiable
questions on 2026-10-02/03. This module grades each one exactly as written there, from the tables the other W2
modules build, and never from anything else. Status vocabulary (``config.STATUSES``):

``collecting``   the inputs the grade needs are not in yet (a future filing, crawl or rate sheet, or a source
                 that only exists after a CI run), or the registered window is still open;
``holds``        the registered claim is met on the registered test;
``falsified``    the registered refutation condition is met;
``undecidable``  the registration's own "undecidable" condition is met, or the inputs exist but a bound points
                 the wrong way to decide (see "Grading a qualified number" below).

Every question returns its ``evidence`` (label, value, qualifier, unit, period, source), the dates it is graded,
and the "already seen" reading section H disclosed, so the page can show what a status rests on.

Inputs
------
Curated rows (``reference.rows``: validated, private fields stripped, chartable rows only are used for numbers),
the economics waterfall lines, the ledger's derived intervals, the share lens tables and, when they are on disk,
the CI-only marts: ``devstats_client_monthly`` (npm, Q1) and ``webtech_adoption`` (HTTP Archive, Q2). When a
CI-only source is absent the question is ``collecting`` with that reason; it never falls back to a guess.

Qualifiers in derived numbers
-----------------------------
Every derived number here is built with :mod:`pipelines.payments.economics` (``Num``, ``q_sub``, ``q_div``, ...)
and so inherits a qualifier by the rule documented there: a ``>`` input moving the result up makes the result a
lower bound (``>``), moving it down an upper bound (``<``); ``<`` mirrors; any ``~`` gives ``~``; bounds pointing
opposite ways give ``~`` (or are refused with ``strict=True``). Example: Q1's ratio is a download share (``=``)
over the *upper bound* of a volume share (``<``), so the ratio is a lower bound (``>``) on the ratio to the
true volume share.

Grading a qualified number
--------------------------
:func:`decide` turns a qualified number and a threshold into True, False or None (cannot tell). ``=`` and ``~``
are graded at their value (a ``~`` reading is approximate and the reason says so); ``>`` is the interval
``[value, inf)`` and ``<`` is ``(-inf, value]``. A comparison is True only when the whole interval satisfies it,
False only when none of it does; otherwise None, and the question is ``undecidable`` on that reading rather than
graded on a guess.

Usage::

    uv run python -m pipelines.payments.evaluate      # print the scoreboard (writes nothing; publish writes it)
"""

from __future__ import annotations

import calendar
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from pipelines.payments import config, devstats, economics, share
from pipelines.payments.config import STATUSES, UNIVERSE
from pipelines.payments.economics import Num, q_div, q_scale, q_sub, q_sum

log = logging.getLogger("payments.evaluate")

#: Section H was written 2026-10-02/03; readings dated on or before this are "already seen", never graded.
REGISTERED = date(2026, 10, 3)

#: Q2: the registered "already seen" reading is the 2026-08 crawl (Stripe 522, PayPal 478); the next six
#: monthly crawls after it are graded.
Q2_BASELINE_CRAWL = date(2026, 8, 1)
Q2_GEO = "United States of America"
Q2_RANK_TOP = "Top 10k"
Q2_RANK_ALL = "ALL"
Q2_CLIENT = "mobile"
Q2_CRAWLS = int(re.search(r"\d+", config.Q2["crawls"]).group())  # type: ignore[union-attr]
Q2_WINDOW = int(re.search(r"\d+", config.Q2["rolling"]).group())  # type: ignore[union-attr]

#: Q3: the two registered quarterly pairs, (Affirm fiscal quarter, its prior year) vs (Klarna calendar quarter,
#: its prior year).
Q3_PAIRS = ((("FY2027-Q1", "FY2026-Q1"), ("2026-Q3", "2025-Q3")),
            (("FY2027-Q2", "FY2026-Q2"), ("2026-Q4", "2025-Q4")))
#: Klarna's curated ``GMV`` row is global; Q3 needs Klarna's US GMV, a metric whose name says US.
KLARNA_US_GMV = re.compile(r"(^|\W)US(\W|$)")

#: Q4: BILL reports revenue net of rewards from the quarter ending 2026-09-30; readings from then on are graded.
Q4_FIRST_GRADED_AS_OF = "2026-09-30"
Q4_LINE = "net_interchange_after_rewards"
Q4_GRADED = ["2026-11", "2027-02"]

#: Q5 and Q6 grading dates as registered.
Q5_GRADED = "2026-11"
Q5_AFFIRM = "bnpl_affirm"
Q5_KLARNA = "bnpl_klarna"
Q5_MERCHANT_LAYERS = ("merchant side", "card network side")
Q6_GRADED = "2027-02"  # Capital One's FY2026 10-K

#: Q8: graded on the first Visa sheet effective after final approval of the 2026 interchange settlement. The
#: fairness hearing is 2026-11-16 (secondary source). Set the approval date (or ``Q8_VOID``) by amendment when
#: the court rules; until then the question is collecting.
Q8_HEARING = "2026-11-16"
Q8_FINAL_APPROVAL: date | None = None
Q8_VOID = False

#: Q9: roster entries that are business units of a larger company, so never "independent today".
Q9_UNITS = {"Afterpay": "part of Block", "PayPal Pay Later": "a PayPal product",
            "Shopify Payments": "a Shopify product", "Fiserv Clover": "a Fiserv business"}
Q9_EXCLUDED = {"Payoneer": "excluded by the registration (acquisition by Nuvei pending)"}

ROSTER: list[str] = [c for seg in UNIVERSE.values() for grp in seg.values() for c in grp]


def _money(text: str) -> float:
    m = re.fullmatch(r"\s*\$(\d+(?:\.\d+)?)\s*([BMK]?)\s*", text)
    if not m:
        raise ValueError(f"not a dollar amount: {text!r}")
    return float(m.group(1)) * {"B": 1e9, "M": 1e6, "K": 1e3, "": 1.0}[m.group(2)]


Q9_DEADLINE = date.fromisoformat(config.Q9["deadline"])
Q9_CAP = _money(config.Q9["valuation_cap"])
Q9_MIN = int(config.Q9["min_deals"])
Q3_GAP_PP = float(re.search(r"\d+(?:\.\d+)?", config.Q3["gap_pp"]).group())  # type: ignore[union-attr]
Q5_CREDIT_SHARE = economics.pct(config.Q5["credit_share"]) / 100.0


# --- qualified comparisons -----------------------------------------------------------------------------


def _interval(n: Num) -> tuple[float, float]:
    if n.qualifier in ("=", "~"):
        return n.value, n.value
    if n.qualifier == ">":
        return n.value, math.inf
    return -math.inf, n.value


def decide(n: Num | None, op: str, threshold: float) -> bool | None:
    """Whether a qualified number satisfies ``op threshold``: True, False, or None when its bound cannot tell."""
    if n is None:
        return None
    lo, hi = _interval(n)
    if op == ">=":
        return True if lo >= threshold else (False if hi < threshold else None)
    if op == ">":
        return True if lo > threshold else (False if hi <= threshold else None)
    if op == "<=":
        return True if hi <= threshold else (False if lo > threshold else None)
    if op == "<":
        return True if hi < threshold else (False if lo >= threshold else None)
    raise ValueError(f"unknown comparison {op!r}")


def month_end(label: str) -> date:
    """``'2026-11'`` (or any text starting with a ``YYYY-MM``) -> 2026-11-30."""
    m = re.match(r"\s*(\d{4})-(\d{2})", label)
    if not m:
        raise ValueError(f"no YYYY-MM in {label!r}")
    y, mo = int(m.group(1)), int(m.group(2))
    return date(y, mo, calendar.monthrange(y, mo)[1])


def next_grading(dates: list[str], today: date) -> str | None:
    """The first registered grading month (``YYYY-MM``) that has not ended yet, from free-text grading dates."""
    for d in dates:
        for ym in re.findall(r"\d{4}-\d{2}", d):
            if month_end(ym) >= today:
                return ym
    return None


# --- context and result --------------------------------------------------------------------------------


@dataclass
class Context:
    """Everything the nine grades read. Curated lists are ``reference.rows`` output; frames may be empty."""

    today: date
    kpi: list[dict] = field(default_factory=list)
    private: list[dict] = field(default_factory=list)
    denoms: list[dict] = field(default_factory=list)  # reference.rows("denominators"); Q1 reads EUR/USD here
    events: list[dict] = field(default_factory=list)
    waterfall_rows: list[dict] = field(default_factory=list)
    waterfall_lines: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=economics.LINE_DTYPES))
    intervals: pl.DataFrame | None = None
    client_monthly: pl.DataFrame | None = None   # devstats (CI only)
    adoption: pl.DataFrame | None = None         # webtech (CI only)
    ci_errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Result:
    id: str
    title: str
    status: str
    reason: str
    graded: list[str]
    already_seen: str
    inputs: str
    ci_only: bool = False
    inputs_present: bool = True
    evidence: list[dict] = field(default_factory=list)
    next_grading: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} not in {STATUSES}")

    def row(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status, "reason": self.reason,
                "graded": "; ".join(self.graded), "next_grading": self.next_grading,
                "already_seen": self.already_seen, "inputs": self.inputs, "ci_only": self.ci_only,
                "inputs_present": self.inputs_present}


def ev(label: str, n: Num | None, unit: str, period: str | None = None, source_url: str | None = None,
       note: str | None = None) -> dict:
    return {"label": label, "value": None if n is None else round(float(n.value), 6) + 0.0,
            "qualifier": None if n is None else n.qualifier, "unit": unit, "period": period,
            "source_url": source_url, "note": note}


def _num(r: dict) -> Num:
    return Num(float(r["value"]), r["qualifier"])


def _chartable(r: dict) -> bool:
    return r.get("chartable") is True and r.get("tag") in ("V", "C") \
        and r.get("metric_kind") not in config.NEVER_CHARTED_KINDS


def _kpi(ctx: Context, company: str, metric: str | re.Pattern, period: str) -> dict | None:
    hits = [r for r in ctx.kpi if r["company"] == company and r["period"] == period and _chartable(r)
            and (r["metric"] == metric if isinstance(metric, str) else bool(metric.search(r["metric"])))]
    return hits[0] if len(hits) == 1 else None


def _growth(cur: dict, prev: dict) -> Num:
    """Year-on-year growth in percent, qualifier propagated: (cur / prev - 1) x 100."""
    return q_scale(q_sub(q_div(_num(cur), _num(prev)), Num(1.0)), 100.0)


# --- Q1 -------------------------------------------------------------------------------------------------


def _stripe_year(ctx: Context) -> int | None:
    years = [int(m.group(1)) for r in ctx.private if r["company"] == "Stripe" and r["metric"] == "total_volume"
             and _chartable(r) and (m := re.fullmatch(r"FY(\d{4})", str(r["period"])))]
    return max(years) if years else None


#: Q1 converts Adyen's EUR processed volume with a curated, sourced calendar-year average EUR/USD rate (USD per
#: EUR) from ``denominators.json``. It is not in the curated file yet; until a curator adds it, Adyen stays
#: unconverted and Q1 cannot be graded (see :func:`q1_uninformative`).
EUR_USD_METRIC = "eur_usd_annual_average"


def eur_usd_row(ctx: Context, year: int) -> dict | None:
    """The curated calendar-``year`` average EUR/USD row (USD per EUR), when one chartable ``=`` row exists."""
    hits = [d for d in ctx.denoms if d.get("metric") == EUR_USD_METRIC and str(d.get("period")) == f"FY{year}"
            and d.get("chartable") is True and d.get("tag") in ("V", "C")]
    return hits[0] if len(hits) == 1 else None


def q1_volume(ctx: Context) -> dict | None:
    year = _stripe_year(ctx)
    if year is None:
        return None
    fx = eur_usd_row(ctx, year)
    return share.q1_volume_share(ctx.kpi, ctx.private, year, None if fx is None else float(fx["value"]))


def q1_uninformative(vol: dict | None) -> list[str]:
    """Q1 members (other than Stripe) entered as [0, inf) because their comparable calendar-year volume is missing.

    Each such member adds 0 to the denominator of the upper bound, so the bound is mechanically inflated (and the
    ratio deflated) by missing data, not by the registered test. While this list is non-empty Q1 is not graded.
    """
    if vol is None:
        return []
    return sorted(k for k, m in vol["members"].items() if k != "Stripe" and m["high"] is None)


def q1_series(ctx: Context) -> pl.DataFrame | None:
    """devstats' monthly Q1 readings against the share lens's volume upper bound (None when npm is absent).

    The bound is passed only when it is informative (no member unbounded for lack of data); otherwise it is
    None and every graded month reads ``undecidable``.
    """
    if ctx.client_monthly is None or ctx.client_monthly.is_empty():
        return None
    vol = q1_volume(ctx)
    upper = None if (vol is None or q1_uninformative(vol)) else vol["upper"]
    return devstats.q1_series(ctx.client_monthly, upper)


def q1(ctx: Context) -> Result:
    graded = ["monthly", "2027-02 (Stripe's next annual letter)"]
    vol = q1_volume(ctx)
    missing = q1_uninformative(vol)
    evidence = []
    if vol is not None:
        note = ("upper bound of an interval; PayPal enters as [0, full TPV] because TPV includes Venmo/P2P"
                + (f"; NOT INFORMATIVE: {', '.join(missing)} entered at 0 (no comparable calendar-year volume), "
                   "so this bound is inflated by missing data and is not graded" if missing else ""))
        evidence.append(ev("Stripe share of disclosed volume, upper bound", None if vol["upper"] is None
                           else Num(vol["upper"], "<"), "fraction", f"FY{vol['year']}", note=note))
        if vol["upper_if_paypal_full_tpv"] is not None:
            evidence.append(ev("Stripe share with PayPal at full TPV (context, not graded)",
                               Num(vol["upper_if_paypal_full_tpv"], "<"), "fraction", f"FY{vol['year']}"))
    res = Result("Q1", "Developer mindshare is more concentrated than money", "collecting", "",
                 graded, "download share 84-90% depending on the whitelist; volume share about 32% (computed)",
                 "devstats (npm client SDKs, CI only); share.q1_volume_share (curated KPI and private ledger)",
                 ci_only=True, evidence=evidence)
    series = q1_series(ctx)
    if series is None:
        res.inputs_present = False
        err = ctx.ci_errors.get("devstats")
        res.reason = (f"npm download marts unreadable: {err}" if err else
                      "npm download counts (devstats) exist only after a CI run; none on disk yet")
        return res
    st = devstats.q1_status(series)
    res.status = st["status"]
    if st["ratio"] is not None and st["volume_share_upper"]:
        ratio = q_div(Num(st["download_share"]), Num(st["volume_share_upper"], "<"))
    else:
        ratio = None
    res.evidence = [ev("Stripe share of client-SDK npm downloads", None if st["download_share"] is None
                       else Num(st["download_share"]), "fraction", st["month"]),
                    ev("Download share / volume-share upper bound", ratio, "ratio", st["month"],
                       note="a lower bound on the ratio to the true volume share; the registered test grades it"),
                    ev("Graded months in a row below the refutation bar", Num(float(st["consecutive_below"])),
                       "months", st["month"])] + evidence
    res.reason = {
        "holds": f"latest graded ratio is at least {config.Q1['ratio_holds']}",
        "falsified": f"ratio below {config.Q1['ratio_refuted']} in {config.Q1['consecutive_refreshes']} "
                     "consecutive graded refreshes",
        "undecidable": (f"volume-share upper bound rests on members with no comparable calendar-year volume "
                        f"({', '.join(missing)})" if missing
                        else "the volume-share upper bound is unknown, so the ratio cannot be formed"),
        "collecting": "no graded month yet at or above the bar, and no refuting streak"
                      if st["month"] and st["month"] >= devstats.Q1_FIRST_GRADED_MONTH.isoformat()
                      else "only pre-registration months so far; graded from 2026-10",
    }[res.status]
    return res


# --- Q2 -------------------------------------------------------------------------------------------------


def q2_series(adoption: pl.DataFrame) -> pl.DataFrame:
    """Stripe and PayPal origins on US mobile, top-10k and all origins, with the 3-crawl rolling mean."""
    a = adoption.filter((pl.col("geo") == Q2_GEO) & (pl.col("client") == Q2_CLIENT)
                        & pl.col("rank").is_in([Q2_RANK_TOP, Q2_RANK_ALL])
                        & pl.col("technology").is_in(["Stripe", "PayPal"]))
    a = a.with_columns(pl.format("US / {} / {}", pl.col("rank"), pl.col("client")).alias("slice"))
    return (a.sort(["slice", "technology", "date"])
            .with_columns(pl.col("origins").cast(pl.Float64).rolling_mean(Q2_WINDOW)
                          .over(["slice", "technology"]).alias("rolling_mean_3m"))
            .select("slice", "technology", "date", "origins", "total_origins", "share", "rolling_mean_3m"))


def q2(ctx: Context) -> Result:
    res = Result("Q2", "The top of the web is changing hands", "collecting", "", [config.Q2["graded"]],
                 "Stripe 522, PayPal 478 (2026-08 crawl)",
                 "webtech (HTTP Archive Tech Report, US, top-10k origins, mobile; CI only)", ci_only=True)
    if ctx.adoption is None or ctx.adoption.is_empty():
        res.inputs_present = False
        err = ctx.ci_errors.get("webtech")
        res.reason = (f"web-coverage mart unreadable: {err}" if err else
                      "HTTP Archive counts (webtech) exist only after a CI run; none on disk yet")
        return res
    s = q2_series(ctx.adoption)
    top = s.filter(pl.col("slice") == f"US / {Q2_RANK_TOP} / {Q2_CLIENT}")
    wide = (top.pivot(on="technology", index="date", values="rolling_mean_3m").sort("date")
            if top.height else pl.DataFrame())
    if wide.is_empty() or not {"Stripe", "PayPal"} <= set(wide.columns):
        res.reason = "the US top-10k mobile slice has no Stripe and PayPal series"
        res.inputs_present = False
        return res
    wide = wide.with_columns((pl.col("Stripe") - pl.col("PayPal")).alias("lead"))
    base = wide.filter(pl.col("date") == Q2_BASELINE_CRAWL)
    if base.is_empty() or base["lead"][0] is None:
        res.reason = f"the baseline crawl {Q2_BASELINE_CRAWL:%Y-%m} has no 3-crawl rolling mean in the table"
        return res
    b = float(base["lead"][0])
    window = wide.filter(pl.col("date") > Q2_BASELINE_CRAWL).head(Q2_CRAWLS)
    res.evidence.append(ev("Stripe lead over PayPal, baseline (3-crawl mean)", Num(b), "origins",
                           f"{Q2_BASELINE_CRAWL:%Y-%m}"))
    refuted = None
    for r in window.iter_rows(named=True):
        if r["lead"] is None:
            continue
        if r["PayPal"] > r["Stripe"]:
            refuted = f"PayPal retook the top-10k lead on the rolling mean in the {r['date']:%Y-%m} crawl"
            break
        if r["lead"] < b / 2:
            refuted = f"the gap shrank by more than half ({r['lead']:.1f} vs {b:.1f}) in the {r['date']:%Y-%m} crawl"
            break
    if window.height:
        last = window.row(-1, named=True)
        res.evidence += [ev("Stripe, top-10k US mobile (3-crawl mean)", Num(float(last["Stripe"])), "origins",
                            f"{last['date']:%Y-%m}"),
                         ev("PayPal, top-10k US mobile (3-crawl mean)", Num(float(last["PayPal"])), "origins",
                            f"{last['date']:%Y-%m}"),
                         ev("Graded crawls observed", Num(float(window.height)), "crawls", f"{last['date']:%Y-%m}",
                            note=f"of {Q2_CRAWLS}")]
    allw = s.filter(pl.col("slice") == f"US / {Q2_RANK_ALL} / {Q2_CLIENT}")
    if allw.height:
        latest = allw["date"].max()
        cur = {r["technology"]: r["rolling_mean_3m"]
               for r in allw.filter(pl.col("date") == latest).iter_rows(named=True)}
        if cur.get("PayPal") is not None and cur.get("Stripe") is not None:
            res.evidence.append(ev("PayPal minus Stripe, all US origins (context, not graded)",
                                   Num(float(cur["PayPal"] - cur["Stripe"])), "origins", f"{latest:%Y-%m}",
                                   note="section H also says PayPal still leads across all US origins; "
                                        "its refutation clause grades only the top-10k slice"))
    if refuted:
        res.status, res.reason = "falsified", refuted
    elif window.height >= Q2_CRAWLS:
        res.status, res.reason = "holds", f"Stripe kept the top-10k lead across all {Q2_CRAWLS} graded crawls"
    else:
        res.reason = f"{window.height} of {Q2_CRAWLS} graded crawls in; no refutation so far"
    return res


# --- Q3 -------------------------------------------------------------------------------------------------


def q3(ctx: Context) -> Result:
    graded = list(config.Q3["graded"])
    res = Result("Q3", "BNPL share is moving to Affirm", "collecting", "", graded,
                 "+36% against +27%; Affirm's GMV includes small Canada and UK volumes",
                 "curated KPI file: Affirm GMV (fiscal quarters), Klarna US GMV (calendar quarters)")
    # the latest already-seen Affirm year-on-year pair, for context
    seen = sorted({r["period"] for r in ctx.kpi if r["company"] == "Affirm" and r["metric"] == "GMV"
                   and _chartable(r) and re.fullmatch(r"FY\d{4}-Q\d", r["period"])}, reverse=True)
    for p in seen:
        prior = f"FY{int(p[2:6]) - 1}{p[6:]}"
        cur, prev = _kpi(ctx, "Affirm", "GMV", p), _kpi(ctx, "Affirm", "GMV", prior)
        if cur and prev:
            res.evidence.append(ev("Affirm GMV growth, latest charted quarter (already seen)", _growth(cur, prev),
                                   "percent", f"{p} vs {prior}", cur["source_url"]))
            break
    gaps: list[bool | None] = []
    missing: list[str] = []
    for i, ((a_cur, a_prev), (k_cur, k_prev)) in enumerate(Q3_PAIRS, 1):
        ac, ap = _kpi(ctx, "Affirm", "GMV", a_cur), _kpi(ctx, "Affirm", "GMV", a_prev)
        kc, kp = _kpi(ctx, "Klarna", KLARNA_US_GMV, k_cur), _kpi(ctx, "Klarna", KLARNA_US_GMV, k_prev)
        if not (ac and ap):
            missing.append(f"pair {i}: Affirm {a_cur} GMV not published yet" if not ac
                           else f"pair {i}: Affirm {a_prev} GMV missing")
        if not (kc and kp):
            missing.append(f"pair {i}: Klarna US GMV for {k_cur} and {k_prev} not in the curated KPI file "
                           "(Klarna's curated GMV row is global)")
        if ac and ap and kc and kp:
            ga, gk = _growth(ac, ap), _growth(kc, kp)
            gap = q_sub(ga, gk)
            ok = decide(gap, ">=", Q3_GAP_PP)
            gaps.append(ok)
            res.evidence += [ev(f"Pair {i}: Affirm GMV growth", ga, "percent", f"{a_cur} vs {a_prev}",
                                ac["source_url"]),
                             ev(f"Pair {i}: Klarna US GMV growth", gk, "percent", f"{k_cur} vs {k_prev}",
                                kc["source_url"]),
                             ev(f"Pair {i}: gap", gap, "percentage points", graded[i - 1])]
        else:
            gaps.append(None)
    res.next_grading = next_grading(graded, ctx.today)
    if any(g is False for g in gaps):
        res.status, res.reason = "falsified", f"a pair's growth gap is below {config.Q3['gap_pp']}"
    elif all(g is True for g in gaps):
        res.status, res.reason = "holds", f"both pairs clear {config.Q3['gap_pp']}"
    elif not missing:
        res.status, res.reason = "undecidable", "a pair's gap carries a bound that cannot settle the threshold"
    else:
        res.reason = "; ".join(missing)
        res.inputs_present = False
    return res


# --- Q4 -------------------------------------------------------------------------------------------------


def q4(ctx: Context) -> Result:
    graded = list(Q4_GRADED)
    lo, hi = (economics.pct(x) for x in config.Q4["band"])
    r_hi, r_lo = economics.pct(config.Q4["refute_high"]), economics.pct(config.Q4["refute_low"])
    res = Result("Q4", "Corporate-card net interchange settles at 1.0-1.6% of card volume", "collecting", "",
                 graded, "BILL gross 2.60%, rewards 51% of interchange, net about 1.28% (computed)",
                 "economics waterfall: net interchange after rewards per US spend-management issuer and quarter")
    lines = ctx.waterfall_lines.filter(pl.col("line") == Q4_LINE).sort(["company", "as_of"])
    issuers_out_band = False
    refuted = None
    for company, g in lines.group_by("company", maintain_order=True):
        streak = 0
        for r in g.iter_rows(named=True):
            n = Num(r["usd_per_100"], r["qualifier"])
            graded_q = r["as_of"] >= Q4_FIRST_GRADED_AS_OF
            res.evidence.append(ev(f"{company[0]} net interchange after rewards" + ("" if graded_q
                                   else " (already seen)"), n, "percent of card volume", r["period"],
                                   r["source_url"].split(" ")[0]))
            if not graded_q:
                continue
            out = decide(n, ">", r_hi) is True or decide(n, "<", r_lo) is True
            streak = streak + 1 if out else 0
            if streak >= 2 and refuted is None:
                refuted = f"{company[0]} outside {config.Q4['refute_low']}-{config.Q4['refute_high']} for two " \
                          f"consecutive quarters (to {r['period']})"
            if decide(n, ">=", lo) is not True or decide(n, "<=", hi) is not True:
                issuers_out_band = True
    forward = lines.filter(pl.col("as_of") >= Q4_FIRST_GRADED_AS_OF)
    res.next_grading = next_grading(graded, ctx.today)
    if refuted:
        res.status, res.reason = "falsified", refuted
    elif forward.is_empty():
        res.reason = (f"no quarter ending on or after {Q4_FIRST_GRADED_AS_OF} in the waterfall inputs yet "
                      "(BILL's first net-of-rewards quarter)")
        res.inputs_present = False
    elif ctx.today > month_end(graded[-1]):
        res.status, res.reason = (("undecidable", "outside the 1.0-1.6% band but not in the refutation zone")
                                  if issuers_out_band else ("holds", "every graded quarter inside the band"))
    else:
        res.reason = f"{forward.height} graded quarter(s) in; final grading {graded[-1]}"
    return res


# --- Q5 -------------------------------------------------------------------------------------------------


def _split(lines: pl.DataFrame, wf: str) -> tuple[Num, Num] | None:
    """(merchant-side take, residual kept) per $100 for one BNPL waterfall, or None when absent."""
    w = lines.filter(pl.col("waterfall") == wf)
    m = w.filter((pl.col("role") == "take") & pl.col("layer").is_in(list(Q5_MERCHANT_LAYERS)))
    r = w.filter(pl.col("role") == "residual")
    if m.is_empty() or r.height != 1:
        return None
    merchant = q_sum(*(Num(v, q) for v, q in zip(m["usd_per_100"], m["qualifier"], strict=True)))
    return merchant, Num(r["usd_per_100"][0], r["qualifier"][0])


def q5(ctx: Context) -> Result:
    res = Result("Q5", "BNPL is a lending business", "collecting", "", [Q5_GRADED],
                 "Affirm 4.15 against Klarna 1.22 per $100 kept (computed, different periods)",
                 "economics waterfalls: Affirm FY2026 per-$100 lines; a Klarna merchant/credit split")
    a = _split(ctx.waterfall_lines, Q5_AFFIRM)
    if a:
        res.evidence += [ev("Affirm merchant and card-network revenue per $100", a[0], "usd_per_100", "FY2026"),
                         ev("Affirm revenue less transaction costs per $100", a[1], "usd_per_100", "FY2026")]
    k = _split(ctx.waterfall_lines, Q5_KLARNA)
    res.next_grading = next_grading([Q5_GRADED], ctx.today)
    if a is None or k is None:
        res.inputs_present = False
        why = ("Klarna does not disclose a merchant/credit split (no Klarna per-$100 waterfall in the curated "
               "inputs)" if k is None else "the Affirm waterfall is missing")
        if k is None and ctx.today > month_end(Q5_GRADED):
            res.status, res.reason = "undecidable", why + "; registered as undecidable in that case"
        else:
            res.reason = why + f"; undecidable if still absent after {Q5_GRADED}"
        return res
    gap = q_sub(a[1], k[1], strict=False)
    merchant_gap = q_sub(a[0], k[0], strict=False)
    if gap.value <= 0:
        res.status, res.reason = "falsified", "Affirm does not keep more per $100 than Klarna"
        return res
    merchant_share = q_div(merchant_gap, gap) if merchant_gap.value >= 0 else Num(0.0, merchant_gap.qualifier)
    credit_share = q_sub(Num(1.0), merchant_share)
    res.evidence += [ev("Klarna kept per $100", k[1], "usd_per_100"), ev("Retention gap", gap, "usd_per_100"),
                     ev("Credit-side share of the gap", credit_share, "fraction")]
    if decide(merchant_share, ">", 0.5) is True:
        res.status, res.reason = "falsified", "the merchant side explains more than half of the gap"
    elif decide(credit_share, ">=", Q5_CREDIT_SHARE) is True:
        res.status, res.reason = "holds", f"credit side explains at least {config.Q5['credit_share']} of the gap"
    else:
        res.status, res.reason = "undecidable", "credit side below the claim but merchant side not above half"
    return res


# --- Q6 -------------------------------------------------------------------------------------------------


def q6(ctx: Context) -> Result:
    res = Result("Q6", "Brex earns less than half of its revenue from card interchange", "collecting", "",
                 [Q6_GRADED + " (Capital One FY2026 10-K)"],
                 "$815M of loans in Capital One's purchase-price allocation -> $6.6-14.9B of card spend -> "
                 "$85-190M of net interchange, against about $700M of stated annualised revenue (secondary source)",
                 "ledger private_intervals (Q6 chain); a Capital One disclosure of Brex net interchange and revenue")
    iv = ctx.intervals if ctx.intervals is not None else pl.DataFrame(schema=dict.fromkeys(["company"], pl.Utf8))
    for metric, label in (("implied_annual_card_spend", "Brex implied annual card spend"),
                          ("implied_net_interchange", "Brex implied net interchange")):
        hit = iv.filter((pl.col("company") == "Brex") & (pl.col("metric") == metric)) if iv.height else iv
        if hit.height:
            r = hit.row(0, named=True)
            for end in ("lower", "upper"):
                if r[end] is not None:
                    res.evidence.append(ev(f"{label}, {end} end", Num(r[end], ">" if end == "lower" else "<"),
                                           r["unit"], r["period"], r["source_url"].split(" ")[0],
                                           note=r["assumption"]))
    disc = [r for r in ctx.private if r["company"] == "Brex" and r.get("metric_kind") == "counterparty_filing"
            and _chartable(r)]
    ni = [r for r in disc if "interchange" in r["metric"]]
    rev = [r for r in disc if "revenue" in r["metric"]]
    res.next_grading = next_grading([Q6_GRADED], ctx.today)
    pair = [(n, v) for n in ni for v in rev if n["period"] == v["period"]]
    if pair:
        n, v = max(pair, key=lambda p: p[0]["as_of_date"])
        s = q_div(_num(n), _num(v))
        res.evidence.append(ev("Brex net interchange / revenue (Capital One)", s, "fraction", n["period"],
                               n["source_url"]))
        if decide(s, ">", 0.5) is True:
            res.status, res.reason = "falsified", "Capital One's figures show net interchange above half of revenue"
        elif decide(s, "<", 0.5) is True:
            res.status, res.reason = "holds", "Capital One's figures show net interchange below half of revenue"
        else:
            res.status, res.reason = "undecidable", "the disclosed ratio's bound cannot settle one half"
        return res
    res.inputs_present = False
    note = ("the revenue denominator is a secondary-source figure, listed only in the reported, unconfirmed "
            "table and never used in a computed number")
    if ctx.today > month_end(Q6_GRADED):
        res.status, res.reason = "undecidable", f"no Capital One disclosure by its FY2026 10-K; {note}"
    else:
        res.reason = f"no Capital One disclosure of Brex net interchange and revenue yet; {note}"
    return res


# --- Q7 -------------------------------------------------------------------------------------------------


def _wf_row(ctx: Context, wf: str, line: str, period: str | None = None) -> dict | None:
    hits = [r for r in ctx.waterfall_rows if r["waterfall"] == wf and r["line"] == line and _chartable(r)
            and (period is None or r.get("period") == period)]
    return max(hits, key=lambda r: r["as_of_date"]) if hits else None


def q7(ctx: Context) -> Result:
    graded = ["2026-10", "2026-11"]
    res = Result("Q7", "Cross-border: challengers cut price, the industry average does not move", "collecting", "",
                 ["2026-10 to 2026-11"],
                 "Wise 0.58% to 0.52%; FSB 1.5% in 2023, 1.6% in 2024 and 2025",
                 "waterfall inputs: Wise take rate (FY2026, H1 FY27), FSB average MSME B2B cost (2026)")
    fy26, fy25 = _wf_row(ctx, "cross_border_wise", "take_rate", "FY2026"), \
        _wf_row(ctx, "cross_border_wise", "take_rate", "FY2025")
    if fy26 and fy25:
        res.evidence.append(ev("Wise take rate change FY2025 to FY2026 (already seen)", q_sub(_num(fy26), _num(fy25)),
                               "percentage points", "FY2026 vs FY2025", fy26["source_url"]))
    fsb25 = _wf_row(ctx, "cross_border_fsb", "avg_cost_b2b_msme_2025")
    if fsb25:
        res.evidence.append(ev("FSB average MSME B2B cost (already seen)", _num(fsb25), "percent", "2025",
                               fsb25["source_url"]))
    q1_27 = _wf_row(ctx, "cross_border_wise", "take_rate", "FY2027-Q1")
    if q1_27:
        res.evidence.append(ev("Wise take rate FY2027-Q1 (context, not the graded half-year)", _num(q1_27), "percent",
                               "FY2027-Q1", q1_27["source_url"]))
    h1 = _wf_row(ctx, "cross_border_wise", "take_rate", "FY2027-H1")
    fsb26 = _wf_row(ctx, "cross_border_fsb", "avg_cost_b2b_msme_2026")
    verdicts = []
    missing = []
    if h1 and fy26:
        ch = q_sub(_num(h1), _num(fy26))
        res.evidence.append(ev("Wise H1 FY27 take rate minus FY2026", ch, "percentage points", "FY2027-H1",
                               h1["source_url"]))
        verdicts.append(economics.q7_wise_verdict(ch.value) if ch.qualifier in ("=", "~") else "neither")
    else:
        missing.append("Wise H1 FY27 take rate not in the waterfall inputs yet")
    if fsb26:
        res.evidence.append(ev("FSB average MSME B2B cost", _num(fsb26), "percent", "2026", fsb26["source_url"]))
        verdicts.append(economics.q7_fsb_verdict(fsb26["value"]) if fsb26["qualifier"] in ("=", "~") else "neither")
    else:
        missing.append("FSB 2026 average cost not in the waterfall inputs yet")
    res.next_grading = next_grading(graded, ctx.today)
    if "refuted" in verdicts:
        res.status, res.reason = "falsified", ("Wise's H1 FY27 take rate is flat or up, or the FSB 2026 average is "
                                               f"{config.Q7['fsb_refute']} or below")
    elif missing:
        res.inputs_present = False
        res.reason = "; ".join(missing) + (" (past the registered grading window)"
                                           if ctx.today > month_end(graded[-1]) else "")
    elif all(v == "holds" for v in verdicts):
        res.status, res.reason = "holds", (f"Wise cut at least {config.Q7['wise_cut_bps']} and the FSB average "
                                           f"stayed at or above {config.Q7['fsb_floor']}")
    else:
        res.status, res.reason = "undecidable", "neither the claim nor its refutation is met"
    return res


# --- Q8 -------------------------------------------------------------------------------------------------


def q8(ctx: Context) -> Result:
    res = Result("Q8", "Commercial-card interchange does not fall after the settlement", "collecting", "",
                 [f"after the fairness hearing ({Q8_HEARING})", "the spring 2027 Visa sheet"],
                 f"{config.Q8['current']}, effective 2026-04-18",
                 "waterfall inputs: Visa US Commercial Card-Not-Present rate by sheet")
    pcts = sorted((r for r in ctx.waterfall_rows if r["waterfall"] == "corporate_card_bill"
                   and r["line"] == "interchange_commercial_cnp_pct" and _chartable(r)),
                  key=lambda r: r["as_of_date"])
    cur, _ = economics.rate_plus_fixed(config.Q8["current"])
    for r in pcts:
        res.evidence.append(ev("Visa Commercial CNP ad valorem rate", _num(r), "percent", r["as_of_date"],
                               r["source_url"]))
    if Q8_VOID:
        res.status, res.reason = "undecidable", "void: the court rejected the settlement"
        return res
    if Q8_FINAL_APPROVAL is None:
        res.inputs_present = False
        res.reason = (f"the settlement has no final approval yet (fairness hearing {Q8_HEARING}); graded on the "
                      "first Visa sheet effective after it")
        return res
    after = [r for r in pcts if date.fromisoformat(r["as_of_date"]) > Q8_FINAL_APPROVAL]
    if not after:
        res.inputs_present = False
        res.reason = f"no Visa sheet effective after final approval ({Q8_FINAL_APPROVAL}) yet"
        return res
    first = after[0]
    cut = q_sub(Num(cur), _num(first))
    res.evidence.append(ev("Cut to Commercial CNP vs 2026-04-18 sheet", cut, "percentage points",
                           first["as_of_date"], first["source_url"]))
    v = economics.q8_verdict(cut.value)
    res.status = "holds" if v == "holds" else "falsified"
    res.reason = (f"rate stays at or above {config.Q8['floor']}" if v == "holds"
                  else f"cut by more than {config.Q8['cut_bps']}")
    return res


# --- Q9 -------------------------------------------------------------------------------------------------


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_ROSTER_NORM = {_norm(c): c for c in ROSTER} | {"block": "Block/Square", "square": "Block/Square"}


def acquisition_target(r: dict) -> str | None:
    """The roster company acquired in an acquisition event, or None when the target is not on the roster.

    W1f writes event ids as ``acquirer:target:stage`` (``capitalone:brex:announce``, ``stripe:bridge:close``),
    while the row's ``company`` is sometimes the acquirer and sometimes the target. So the id's second part names
    the target; when it names neither party (``avidxchange:take_private:announce``), the row's company is the
    target. The target is then looked up in the pinned roster.
    """
    if r.get("event_kind") != "acquisition":
        return None
    parts = str(r.get("id") or "").split(":")
    cand = _norm(parts[1]) if len(parts) >= 2 else ""
    counterparties = re.split(r"[;,(]| with | from ", str(r.get("counterparty") or ""))
    parties = {_norm(r.get("company")), *(_norm(x) for x in counterparties)}
    if cand and any(cand == p or cand in p for p in parties if p):
        return _ROSTER_NORM.get(cand)
    return _ROSTER_NORM.get(_norm(r.get("company")))


def _valuation(ctx: Context, company: str, deal: dict) -> Num | None:
    """Deal value when the event states one, else the latest chartable private valuation before the deal."""
    if deal.get("value") is not None and deal.get("unit") == "USD":
        return _num(deal)
    vals = [r for r in ctx.private if r["company"] == company and _chartable(r)
            and r["metric"] in ("valuation", "acquisition_price_announced") and r.get("unit") == "USD"
            and r["as_of_date"] <= deal["event_date"]]
    return _num(max(vals, key=lambda r: r["as_of_date"])) if vals else None


def q9(ctx: Context) -> Result:
    res = Result("Q9", "Consolidation continues (a calibration item)", "collecting", "", [config.Q9["deadline"]],
                 "3-4 roster deals in the past 12 months", "curated events (acquisition announcements)")
    deals = sorted((r for r in ctx.events if r.get("event_kind") == "acquisition" and _chartable(r)),
                   key=lambda r: r["event_date"])
    first_by_target: dict[str, dict] = {}
    for r in deals:
        t = acquisition_target(r)
        if t and t not in first_by_target:
            first_by_target[t] = r
    taken_before = {t for t, r in first_by_target.items() if date.fromisoformat(r["event_date"]) <= REGISTERED}
    seen = sorted(t for t, r in first_by_target.items()
                  if date(REGISTERED.year - 1, REGISTERED.month, REGISTERED.day)
                  < date.fromisoformat(r["event_date"]) <= REGISTERED)
    res.evidence.append(ev("Roster companies announced as acquired in the 12 months before registration",
                           Num(float(len(seen))), "companies", note=", ".join(seen) or None))
    counted, unknown = [], []
    for t, r in first_by_target.items():
        d = date.fromisoformat(r["event_date"])
        if not (REGISTERED < d <= Q9_DEADLINE) or t in taken_before or t in Q9_UNITS or t in Q9_EXCLUDED:
            continue
        v = _valuation(ctx, t, r)
        ok = decide(v, "<", Q9_CAP)
        if ok is True:
            counted.append(t)
        elif ok is None:
            unknown.append(t)
    res.evidence.append(ev("Qualifying acquisitions announced since registration", Num(float(len(counted))),
                           "companies", f"to {min(ctx.today, Q9_DEADLINE).isoformat()}",
                           note=", ".join(counted) or None))
    if unknown:
        res.evidence.append(ev("Announced since registration, valuation not established", Num(float(len(unknown))),
                               "companies", note=", ".join(sorted(unknown))))
    res.next_grading = config.Q9["deadline"] if ctx.today <= Q9_DEADLINE else None
    if len(counted) >= Q9_MIN:
        res.status, res.reason = "holds", f"{len(counted)} qualifying roster acquisitions announced"
    elif ctx.today > Q9_DEADLINE:
        res.status, res.reason = "falsified", f"only {len(counted)} by {config.Q9['deadline']}"
    else:
        res.reason = (f"{len(counted)} of {Q9_MIN} so far; the window runs to {config.Q9['deadline']} "
                      f"(independent roster companies valued below {config.Q9['valuation_cap']}; Payoneer excluded)")
    return res


# --- the scoreboard -------------------------------------------------------------------------------------

QUESTIONS = (q1, q2, q3, q4, q5, q6, q7, q8, q9)


def scoreboard(ctx: Context) -> list[Result]:
    """All nine grades; one question that errors becomes ``collecting`` with the error, never a crash (rule 5)."""
    out = []
    for fn in QUESTIONS:
        try:
            out.append(fn(ctx))
        except Exception as exc:  # noqa: BLE001 - one broken grade must not lose the other eight
            log.exception("%s failed", fn.__name__)
            out.append(Result(fn.__name__.upper(), fn.__name__.upper(), "collecting",
                              f"grading error: {type(exc).__name__}: {exc}"[:300], [], "", "", inputs_present=False))
    for r in out:
        if r.next_grading is None and r.status == "collecting":
            r.next_grading = next_grading(r.graded, ctx.today)
    return out


def tables(results: list[Result]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(scoreboard, evidence) frames in the schema.py contract."""
    from pipelines.payments import schema

    sb = pl.DataFrame([r.row() for r in results], schema=schema.SCORE_DTYPES)
    evd = [{"id": r.id, **e} for r in results for e in r.evidence]
    evf = pl.DataFrame(evd, schema=schema.EVIDENCE_DTYPES) if evd else pl.DataFrame(schema=schema.EVIDENCE_DTYPES)
    return schema.validate("scoreboard", sb), schema.validate("scoreboard_evidence", evf)


def main(argv: list[str] | None = None) -> int:
    from pipelines.common.log import setup_logging
    from pipelines.payments import publish

    setup_logging()
    ctx, _, _ = publish.load_context()
    for r in scoreboard(ctx):
        log.info("%s %-11s %s", r.id, r.status, r.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
