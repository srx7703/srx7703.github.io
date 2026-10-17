"""The Q1..Q10 scoreboard: each registered item's status, the reading it rests on, and when it is graded.

Section I of ``docs/EVALUATION_PLAN.md`` ("Cybersecurity (project I)") registers ten items. Their definitions,
populations and thresholds are code (``config``, ``frozen``, ``stats``, ``readings``) and are not restated here; this
module decides *when* an item is read, calls the registered reading, and lays out what the page shows. Status
vocabulary is ``config.QSTATUS``: ``collecting``, ``holds``, ``falsified``, ``undecidable``.

**No verdict before its grading date.** Until an item's first grading date its status is ``collecting`` and the
registered reading is not called for it; the evidence is then progress (snapshots collected, pairs formed), never an
estimate that could be read as a result. From the grading date the status is the ``readings`` result for the
registered window. The ten items are read separately and no adjustment is made, as the section says.

Three items are ``collecting`` here whatever the date, with the reason, because what grades them is not in this
module: Q5 needs Yahoo's daily history at grading time and its code is registered by an amendment before the first
reading; Q6 needs the ring C ledger as frozen on ``config.Q6['ledger_frozen_on']``; Q8 is a count of announcements that
is checked by hand. Q4 is graded here from a table of total returns the caller passes in (``returns``), because the
history is fetched at grading time and only the derived returns are committed; without it Q4 stays ``collecting``.

``already_seen`` is the section's "Already seen" line. For Q1, Q2, Q3, Q7 and Q9 it is the registered procedure applied
to the baseline file, so the numbers are the output of the code that will grade the item, formatted; for Q4, Q5, Q6 and
Q8 the figures are read straight from the baseline table; the deal facts that no table holds are the section's own
sentences (``SEEN_Q6_DEALS``, ``SEEN_Q8_EVENT``), kept verbatim and checked against the section by the tests. If the
baseline is not among ``runs`` the line says so instead of guessing.

The expected-outcome sentences of section I (from ``power.py``) are not reproduced: they are simulation results, not
readings, and the section prints them.

Everything returned has passed ``SCOREBOARD_SCHEMA`` and ``EVIDENCE_SCHEMA``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import pandera.polars as pa
import polars as pl

from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber import readings as R
from pipelines.cyber import regression as G
from pipelines.cyber import stats as S

QIDS = [f"Q{i}" for i in range(1, 11)]
UNSET: Any = object()  # "work out next_grading from the item's dates"


def _d(text: str) -> date:
    return date.fromisoformat(text)


WINDOW_LAST = _d(C.WINDOW_LAST)

# Retrospective facts of section I that no table holds, verbatim (the tests find each in the section).
SEEN_Q6_DEALS = (
    "Google paid about $32B for Wiz (closed 2026-03-11), about 32 to 64 times ARR depending on which of two "
    "secondary-sourced ARR figures is used. ServiceNow paid $7.75B for Armis, about 22.8 times a reported ARR of "
    "more than $340M."
)
SEEN_Q8_EVENT = (
    "signed a merger agreement with Proofpoint on 2026-10-15, so at least one same-definition event happened"
)
Q5_EXAMPLE = "CRWD"  # the company the section names to show what a dispersion reading looks like
Q8_RUMOURED = ("RPD", "4704.T")  # press reports of sale processes; neither has produced a deal


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    graded: str  # the registered grading date(s), as the page prints them
    dates: tuple[date, ...]  # grading dates in order (an interim reading is a date too), for ``next_grading``


def _items() -> dict[str, Item]:
    q4, q5, q8, q9, q10 = C.Q4, C.Q5, C.Q8, C.Q9, C.Q10
    first = {q: _d(getattr(C, q)["first"]) for q in ("Q1", "Q2", "Q3", "Q7")}
    rows = [
        Item("Q1", "Layer explains the multiple beyond growth", C.Q1["first"], (first["Q1"],)),
        Item("Q2", "Growth explains more of the multiple than free cash flow does", C.Q2["first"], (first["Q2"],)),
        Item("Q3", "The market pays for Rule of 40", C.Q3["first"], (first["Q3"],)),
        Item(
            "Q4",
            "Target-price upside does not predict the next twelve months' return",
            f"{q4['graded']} (quarterly interim readings are shown and not scored)",
            (_d(q4["graded"]),),
        ),
        Item(
            "Q5",
            "Target-price dispersion predicts later volatility",
            f"{q5['graded']} (window 1 alone is read on {q5['interim']} and is not scored)",
            (_d(q5["interim"]), _d(q5["graded"])),
        ),
        Item(
            "Q6",
            "Private headline valuations are above the public comparables even after the discount",
            f"on the {C.WINDOW_LAST} snapshot, once the ledger frozen on {C.Q6['ledger_frozen_on']} is committed",
            (WINDOW_LAST,),
        ),
        Item("Q7", "China's discount is not explained by growth", C.Q7["first"], (first["Q7"],)),
        Item(
            "Q8",
            "Consolidation continues (a calibration item)",
            f"{q8['deadline']} (a mid-point reading is shown on {q8['midterm']})",
            (_d(q8["midterm"]), _d(q8["deadline"])),
        ),
        Item(
            "Q9",
            "The AI-tailwind premium does not widen",
            f"{q9['graded']} (quarterly interim readings are shown)",
            (_d(q9["graded"]),),
        ),
        Item(
            "Q10",
            "A revision in revenue consensus tends to be followed by another in the same direction",
            f"the first Monday snapshot on or after {q10['first']} with at least {q10['min_pairs']} tested pairs, "
            f"and not later than {q10['last']}",
            (_d(q10["first"]), _d(q10["last"])),
        ),
    ]
    return {r.id: r for r in rows}


ITEMS = _items()


# --- formatting ---------------------------------------------------------------------------------------------
def _q(x: float, places: int) -> Decimal:
    """Round half up on the shortest decimal form, as a person rounds a printed figure (246.15 -> 246.2)."""
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _f(x: float, places: int = 2, *, sign: bool = False) -> str:
    text = f"{_q(x, places):f}"
    if text.startswith("-") and float(text) == 0:  # no "-0.00"
        text = text[1:]
    return "+" + text if sign and not text.startswith("-") else text


def _pct(x: float, places: int = 1, *, sign: bool = False) -> str:
    return _f(100.0 * x, places, sign=sign) + "%"


def _ci(lo: float, hi: float, places: int = 2, *, sign: bool = False) -> str:
    return f"{_f(lo, places, sign=sign)} to {_f(hi, places, sign=sign)}"


def _num(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


# --- evidence -----------------------------------------------------------------------------------------------
def ev(
    qid: str,
    label: str,
    value: Any = None,
    unit: str = "",
    *,
    lo: Any = None,
    hi: Any = None,
    level: str | None = None,
    period: str | None = None,
    as_of: date | str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """One number a reader needs, with its interval (if any), unit, period and a pointer to section I. A value that
    is not finite becomes null; a number carries the qualifier ``=`` (computed exactly from the stored tables)."""
    value, lo, hi = _num(value), _num(lo), _num(hi)
    if lo is None or hi is None:
        lo = hi = None
    return {
        "label": label,
        "value": value,
        "lo": lo,
        "hi": hi,
        "level": level if lo is not None else None,
        "qualifier": "=" if value is not None else None,
        "unit": unit,
        "period": period,
        "as_of": as_of.isoformat() if isinstance(as_of, date) else as_of,
        "ref": f"Section I, {qid}",
        "note": note,
    }


def emitter(qid: str, **defaults: Any) -> Callable[..., dict[str, Any]]:
    """``ev`` with the item and the period/as-of of a reading filled in."""

    def make(label: str, value: Any = None, unit: str = "", **kw: Any) -> dict[str, Any]:
        return ev(qid, label, value, unit, **{**defaults, **kw})

    return make


# --- context ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Ctx:
    today: date
    snaps: dict[date, pl.DataFrame]  # the formal Monday snapshots on or before today
    baseline: pl.DataFrame | None
    draws: int
    ledger: Mapping[str, Any] | None
    returns: Mapping[str, float] | None

    @property
    def window(self) -> dict[date, pl.DataFrame]:
        """The registered weekly window: Mondays from 2026-10-26 to ``config.WINDOW_LAST``."""
        return {d: f for d, f in self.snaps.items() if d <= WINDOW_LAST}

    @property
    def newest(self) -> date | None:
        return max(self.snaps) if self.snaps else None


def _result(status: str, reason: str, evidence: list[dict[str, Any]], *, next_grading: Any = UNSET) -> dict[str, Any]:
    return {"status": status, "reason": reason, "evidence": evidence, "next_grading": next_grading}


def _china_group(frame: pl.DataFrame) -> list[str]:
    present = set(frame["ticker"].to_list())
    return [t for t in C.FROZEN_Q7_CN if t in present]


def _panels(window: dict[date, pl.DataFrame], *, min_snapshots: int = R.MIN_SNAPSHOTS) -> tuple[R.Panel, R.Panel]:
    """The Q1 and Q2 panel and the Q7 A-share panel on ``window``, as the registered readings take them."""
    us = R.build_panel(window, F.q1_population, min_snapshots=min_snapshots)
    cn = R.build_panel(window, _china_group, need_fcf=False, min_snapshots=min_snapshots)
    return us, cn


def _decide(status: str, holds: str, falsified: str, numbers: str) -> str:
    """The sentence behind a status. Undecidable covers neither condition met and both met (rules 2 and 5)."""
    if status == "holds":
        return f"Holds: {holds}."
    if status == "falsified":
        return f"Falsified: {falsified}."
    return f"Undecidable: neither condition is established, or both are met (section I, rules 2 and 5); {numbers}."


def _snapshot_gate(ctx: Ctx, qid: str, first: date) -> dict[str, Any] | None:
    """Before the first grading date: progress and nothing else. None once the item is to be read."""
    if ctx.today >= first:
        return None
    window, e = ctx.window, emitter(qid, as_of=max(ctx.window) if ctx.window else None)
    evidence = [
        e(
            "Monday snapshots collected",
            len(window),
            "snapshots",
            note=f"of {C.WINDOW_WEEKS} in the window; a reading needs at least {C.MIN_SNAPSHOTS}",
        )
    ]
    if window:
        us, cn = _panels(window)
        evidence.append(e("Companies in the panel so far", len(us), "companies"))
        if qid == "Q7":
            evidence.append(e("A-share companies in the panel so far", len(cn), "companies"))
    reason = (
        f"Collecting: {len(window)} of {C.WINDOW_WEEKS} Monday snapshots ({C.FIRST_SNAPSHOT} to {C.WINDOW_LAST}) "
        f"so far. Graded {first.isoformat()}; no reading is made before then (section I, {qid})."
    )
    return _result("collecting", reason, evidence)


def _short_panel(panel: R.Panel, minimum: int) -> str:
    if not panel.enough_snapshots:
        return f"the window has {panel.n_snapshots} Monday snapshots and a reading needs at least {C.MIN_SNAPSHOTS}"
    return f"the panel has {len(panel)} companies and a reading needs at least {minimum}"


# --- Q1, Q2, Q3, Q7: the weekly panel -----------------------------------------------------------------------
def _panel_context(ctx: Ctx, qid: str) -> tuple[Callable[..., dict[str, Any]], R.Panel, R.Panel]:
    window = ctx.window
    us, cn = _panels(window)
    e = emitter(qid, period=f"{len(window)} Monday snapshots to {C.WINDOW_LAST}", as_of=max(window) if window else None)
    return e, us, cn


def _q1(ctx: Ctx) -> dict[str, Any]:
    gate = _snapshot_gate(ctx, "Q1", ITEMS["Q1"].dates[0])
    if gate:
        return gate
    e, us, _ = _panel_context(ctx, "Q1")
    minimum = int(C.Q1["min_n"])
    if not (us.enough_snapshots and len(us) >= minimum):
        reason = f"Undecidable: {_short_panel(us, minimum)} (section I, Q1)."
        return _result("undecidable", reason, [e("Companies in the panel", len(us), "companies")])
    out = R.q1(us, draws=ctx.draws)
    uplift, p, hi = _f(out["uplift"], 3, sign=True), _f(out["perm_p"], 3), _f(out["hi"], 3, sign=True)
    reason = _decide(
        out["status"],
        f"the uplift {uplift} is at least {C.Q1['uplift_holds']} and the permutation p {p} is at most "
        f"{C.Q1['p_holds']}",
        f"the upper end {hi} of the {C.Q1['boot_level']} interval for the uplift is below {C.Q1['uplift_holds']}",
        f"uplift {uplift}, permutation p {p}, upper end {hi}",
    )
    groups = ", ".join(f"{g} {n}" for g, n in Counter(us.layers).most_common())
    evidence = [
        e("Companies in the panel", out["n"], "companies", note=f"layer groups: {groups}"),
        e("Adjusted R² of growth alone", out["adj_r2_growth"], "adjusted R²"),
        e(
            "Uplift in adjusted R² from the layer groups",
            out["uplift"],
            "adjusted R²",
            lo=out["lo"],
            hi=out["hi"],
            level=C.Q1["boot_level"],
            note="adjusted R² with growth and layer groups minus adjusted R² with growth alone, on the panel means",
        ),
        e("Permutation p of the layer labels", out["perm_p"], "p", note="one-sided, add-one corrected"),
    ]
    return _result(out["status"], reason, evidence)


def _q2(ctx: Ctx) -> dict[str, Any]:
    gate = _snapshot_gate(ctx, "Q2", ITEMS["Q2"].dates[0])
    if gate:
        return gate
    e, us, _ = _panel_context(ctx, "Q2")
    minimum, level = int(C.Q2["min_n"]), C.Q2["level"]
    if not (us.enough_snapshots and len(us) >= minimum):
        reason = f"Undecidable: {_short_panel(us, minimum)} (section I, Q2)."
        return _result("undecidable", reason, [e("Companies in the panel", len(us), "companies")])
    out = R.q2(us, draws=ctx.draws)
    b, gap = _ci(out["b_lo"], out["b_hi"]), _ci(out["gap_lo"], out["gap_hi"], sign=True)
    reason = _decide(
        out["status"],
        f"R² {_f(out['r2'], 3)} is at least {C.Q2['r2_holds']}, the {level} interval for b ({b}) and the interval for "
        f"the difference ({gap}) both lie wholly above zero",
        f"the {level} interval for b ({b}) lies wholly at or below zero, or the interval for the difference ({gap}) "
        f"lies wholly below zero",
        f"R² {_f(out['r2'], 3)}, b interval {b}, difference interval {gap}",
    )
    evidence = [
        e("Companies in the panel", out["n"], "companies"),
        e("R² of log(EV/NTM revenue) on growth and FCF margin", out["r2"], "R²"),
        e(
            "Growth coefficient b",
            out["b"],
            "log points per unit of growth",
            lo=out["b_lo"],
            hi=out["b_hi"],
            level=level,
        ),
        e("Standardised growth coefficient", out["std_growth"], "standard deviations"),
        e("Standardised FCF margin coefficient", out["std_fcf"], "standard deviations"),
        e(
            "Growth minus FCF margin, standardised, in absolute value",
            out["gap"],
            "standard deviations",
            lo=out["gap_lo"],
            hi=out["gap_hi"],
            level=level,
        ),
    ]
    return _result(out["status"], reason, evidence)


def _q3(ctx: Ctx) -> dict[str, Any]:
    gate = _snapshot_gate(ctx, "Q3", ITEMS["Q3"].dates[0])
    if gate:
        return gate
    e, us, _ = _panel_context(ctx, "Q3")
    minimum, cut, level = int(C.Q3["min_group_n"]), C.Q3["cut"], C.Q3["level"]
    n_high = int((us.rule_of_40 >= float(cut)).sum()) if len(us) else 0
    n_low = len(us) - n_high
    counts = [
        e(f"Companies with Rule of 40 at or above {cut}", n_high, "companies"),
        e(f"Companies with Rule of 40 below {cut}", n_low, "companies"),
    ]
    if not (us.enough_snapshots and n_high >= minimum and n_low >= minimum):
        why = (
            _short_panel(us, 1)
            if not us.enough_snapshots
            else f"the groups have {n_high} and {n_low} companies and each needs at least {minimum}"
        )
        return _result("undecidable", f"Undecidable: {why} (section I, Q3).", counts)
    out = R.q3(us, draws=ctx.draws)
    interval = _ci(out["lo"], out["hi"])
    reason = _decide(
        out["status"],
        f"the ratio of medians {_f(out['ratio'])} is at least {C.Q3['ratio_holds']}",
        f"the {level} interval for the ratio ({interval}) lies wholly below {C.Q3['ratio_holds']}",
        f"ratio {_f(out['ratio'])}, {level} interval {interval}",
    )
    evidence = [
        *counts,
        e("Median EV/NTM revenue, Rule of 40 at or above the cut", out["median_high"], "x"),
        e("Median EV/NTM revenue, Rule of 40 below the cut", out["median_low"], "x"),
        e(
            "Ratio of the two medians",
            out["ratio"],
            "ratio",
            lo=out["lo"],
            hi=out["hi"],
            level=level,
            note="each group is resampled on its own; the group below the cut is " + ", ".join(out["low_tickers"]),
        ),
    ]
    return _result(out["status"], reason, evidence)


def _q7(ctx: Ctx) -> dict[str, Any]:
    gate = _snapshot_gate(ctx, "Q7", ITEMS["Q7"].dates[0])
    if gate:
        return gate
    e, us, cn = _panel_context(ctx, "Q7")
    minimum, level = int(C.Q7["min_n"]), C.Q7["level"]
    counts = [
        e("US companies in the panel", len(us), "companies"),
        e("A-share companies in the panel", len(cn), "companies"),
    ]
    if not (us.enough_snapshots and cn.enough_snapshots and len(cn) >= minimum and len(us) > 0):
        why = (
            _short_panel(us, 1)
            if not us.enough_snapshots
            else f"the A-share group has {len(cn)} companies in the panel and a reading needs at least {minimum}"
        )
        return _result("undecidable", f"Undecidable: {why} (section I, Q7).", counts)
    out = R.q7(us, cn, draws=ctx.draws)
    interval = _ci(out["lo"], out["hi"], sign=True)
    reason = _decide(
        out["status"],
        f"the {level} interval for the China coefficient ({interval}) lies wholly below zero",
        f"the {level} interval for the China coefficient ({interval}) lies wholly above zero",
        f"coefficient {_f(out['coef'], 2, sign=True)}, {level} interval {interval}",
    )
    evidence = [
        *counts,
        e(
            "China coefficient",
            out["coef"],
            "log points",
            lo=out["lo"],
            hi=out["hi"],
            level=level,
            note="coefficient on a China indicator in log(EV/NTM revenue) on growth plus the indicator",
        ),
        e(
            "A-share median multiple over the US median",
            out["ratio"],
            "ratio",
            lo=out["ratio_lo"],
            hi=out["ratio_hi"],
            level=level,
            note="shown beside the result and not graded",
        ),
    ]
    return _result(out["status"], reason, evidence)


# --- Q9 -----------------------------------------------------------------------------------------------------
def q9_days() -> tuple[list[date], list[date]]:
    """The four start Mondays and the four Mondays ending at ``config.Q9['end_last']``."""
    end_last = _d(C.Q9["end_last"])
    return [_d(x) for x in C.Q9["start"]], [end_last - timedelta(weeks=w) for w in (3, 2, 1, 0)]


def _mean_premium(snaps: Mapping[date, pl.DataFrame], days: list[date]) -> float | None:
    values = [G.premium_parts(snaps[d], d)["ratio"] for d in days if d in snaps]
    return None if len(values) < len(days) or any(v is None for v in values) else float(np.mean(values))


def _q9(ctx: Ctx) -> dict[str, Any]:
    start, end = q9_days()
    graded, limit = ITEMS["Q9"].dates[0], C.Q9["widening"]
    start_note = "mean of the weekly premium over the four start snapshots"
    if ctx.today < graded:
        have = [d for d in start if d in ctx.snaps]
        e = emitter("Q9")
        evidence = [
            e(
                "Start snapshots collected",
                len(have),
                "snapshots",
                as_of=max(have) if have else None,
                note=f"of {len(start)}: {', '.join(d.isoformat() for d in start)}",
            )
        ]
        p0 = _mean_premium(ctx.snaps, start)
        if p0 is not None:
            evidence.append(e("Start value of the premium", p0, "ratio", as_of=start[-1], note=start_note))
        if ctx.newest is not None:
            latest = G.premium_parts(ctx.snaps[ctx.newest], ctx.newest)["ratio"]
            note = "a weekly reading, not the end value"
            evidence.append(e("Premium on the latest Monday snapshot", latest, "ratio", as_of=ctx.newest, note=note))
        reason = (
            f"Collecting: {len(have)} of {len(start)} start snapshots so far. Graded {graded.isoformat()} on the four "
            f"Monday snapshots ending {C.Q9['end_last']}; no reading is made before then (section I, Q9)."
        )
        return _result("collecting", reason, evidence)
    out = R.q9(ctx.snaps, start=start, end=end)
    ok = out["start"] is not None and out["end"] is not None
    change = out["end"] / out["start"] - 1.0 if ok else None
    if ok:
        seen = (
            f"the end value {_f(out['end'])} is {_pct(change, 1, sign=True)} against the start value {_f(out['start'])}"
        )
        reason = _decide(
            out["status"],
            f"{seen}, at most {limit}",
            f"{seen}, more than {limit}",
            f"start {_f(out['start'])}, end {_f(out['end'])}",
        )
    else:
        reason = (
            "Undecidable: a member of the trio has no multiple, the rest has fewer than "
            f"{C.Q9['min_rest']} companies, or a snapshot is missing in the start or end weeks (section I, Q9)."
        )
    e = emitter("Q9", period=f"{start[0]} to {end[-1]}")
    evidence = [
        e("Start value of the premium", out["start"], "ratio", as_of=start[-1], note=start_note),
        e(
            "End value of the premium",
            out["end"],
            "ratio",
            as_of=end[-1],
            note=f"mean over the four snapshots ending {end[-1]}",
        ),
        e("End over start, minus one", change, "fraction", as_of=end[-1], note=f"the claim allows at most {limit}"),
    ]
    return _result(out["status"], reason, evidence)


# --- Q4 and Q5: the list at the first snapshot ------------------------------------------------------------------
def _start_table(ctx: Ctx) -> tuple[date, dict[str, float], dict[str, float]] | None:
    """Target upside and dispersion at the first snapshot for the Q4 and Q5 list, or None if it is not in yet."""
    day = _d(C.Q4["first_snapshot"])
    frame = ctx.snaps.get(day)
    if frame is None:
        return None
    upside, spread = {}, {}
    for r in frame.iter_rows(named=True):
        if r["ticker"] not in C.FROZEN_Q4_Q5:
            continue
        mean, price, high, low = r["target_mean"], r["price"], r["target_high"], r["target_low"]
        if mean and price:
            upside[r["ticker"]] = mean / price - 1.0
        if mean and high and low:
            spread[r["ticker"]] = (high - low) / mean
    return day, upside, spread


def _start_evidence(ctx: Ctx, qid: str) -> list[dict[str, Any]]:
    label = "Companies of the frozen list with a target upside"
    got = _start_table(ctx)
    if got is None:
        return [ev(qid, label, 0, "companies", note=f"the {C.Q4['first_snapshot']} snapshot is not in yet")]
    day, upside, spread = got
    e = emitter(qid, as_of=day)
    out = [e(label, len(upside), "companies", note=f"of {len(C.FROZEN_Q4_Q5)}")]
    if upside:
        out.append(
            e(
                "Median target upside at the first snapshot",
                float(np.median(list(upside.values()))),
                "fraction",
                note=C.UPSIDE,
            )
        )
    if spread and qid == "Q5":
        median = float(np.median(list(spread.values())))
        out.append(e("Median target dispersion at the first snapshot", median, "fraction", note=C.DISPERSION))
    return out


def _q4(ctx: Ctx) -> dict[str, Any]:
    graded = ITEMS["Q4"].dates[0]
    evidence = _start_evidence(ctx, "Q4")
    if ctx.today < graded:
        reason = (
            f"Collecting: the window runs from the close of {C.Q4['first_snapshot']} to the close of "
            f"{C.Q4['window_end']}. Graded {graded.isoformat()} from Yahoo's daily history, fetched at grading time "
            f"(section I, Q4)."
        )
        return _result("collecting", reason, evidence)
    got = _start_table(ctx)
    if ctx.returns is None or got is None:
        missing = (
            "the total-return table (derived from Yahoo's daily history) was not supplied"
            if ctx.returns is None
            else f"the {C.Q4['first_snapshot']} snapshot is missing"
        )
        return _result(
            "collecting",
            f"Collecting: the grading date has passed but {missing}; Q4 is not graded without it.",
            evidence,
        )
    upside = got[1]
    tickers = sorted(t for t in upside if t in ctx.returns and _num(ctx.returns[t]) is not None)
    x, y = np.array([upside[t] for t in tickers]), np.array([float(ctx.returns[t]) for t in tickers])
    out = R.q4(x, y, draws=ctx.draws)
    if out["n"] < int(C.Q4["min_n"]):
        reason = (
            f"Undecidable: {out['n']} companies have a return and a reading needs at least {C.Q4['min_n']} "
            f"(section I, Q4)."
        )
    else:
        rho, upper, p = _f(out["rho"], 3, sign=True), _f(out["upper"], 3), _f(out["p"], 4)
        reason = _decide(
            out["status"],
            f"the one-sided 95% upper bound {upper} for the Spearman correlation is below {C.Q4['upper_bound']}",
            f"the correlation {rho} is positive and the permutation p {p} is below {C.Q4['p']}",
            f"correlation {rho}, upper bound {upper}, permutation p {p}",
        )
    e = emitter("Q4", period=f"{C.Q4['first_snapshot']} to {C.Q4['window_end']}", as_of=graded)
    evidence = [
        *evidence,
        e("Companies with a full window or a deal return", out["n"], "companies"),
        e("Spearman correlation of upside and return", out.get("rho"), "correlation"),
        e("One-sided 95% upper bound (Fisher transform)", out.get("upper"), "correlation"),
        e("One-sided permutation p, positive correlation", out.get("p"), "p"),
    ]
    return _result(out["status"], reason, evidence)


def _q5(ctx: Ctx) -> dict[str, Any]:
    w1, w2 = C.Q5["windows"]
    reason = (
        "Collecting: the regression is registered by an amendment before its first reading and needs Yahoo's daily "
        f"history, fetched at grading time. Window 1 runs {w1[0]} to {w1[1]}, window 2 {w2[0]} to {w2[1]}; graded "
        f"{C.Q5['graded']} (section I, Q5)."
    )
    return _result("collecting", reason, _start_evidence(ctx, "Q5"))


# --- Q6 and Q8: what grades them is not in this module ------------------------------------------------------------
def _ledger_companies(ledger: Mapping[str, Any] | None) -> int | None:
    """Distinct companies in the ledger's private intervals, however they are handed over, or None."""
    rows = None if ledger is None else ledger.get("private_intervals")
    if rows is None:
        return None
    if isinstance(rows, pl.DataFrame):
        return int(rows["company"].n_unique()) if "company" in rows.columns else None
    try:
        return len({r["company"] for r in rows})
    except (KeyError, TypeError):
        return None


def _q6(ctx: Ctx) -> dict[str, Any]:
    e = emitter("Q6")
    evidence = [
        e("Scored companies needed", int(C.Q6["min_scored"]), "companies", note="fewer than this and it is undecidable")
    ]
    now = _ledger_companies(ctx.ledger)
    if now is not None:
        note = f"not the scored set: that is fixed by the ledger frozen on {C.Q6['ledger_frozen_on']}"
        evidence.append(
            e("Companies with a private-interval row in the ledger now", now, "companies", as_of=ctx.today, note=note)
        )
    reason = (
        f"Collecting: Q6 is scored on the {C.WINDOW_LAST} snapshot from the ring C ledger as it stands on "
        f"{C.Q6['ledger_frozen_on']}, committed with its hash that day. The frozen ledger and the scoring code land "
        f"later; no company is scored before then (section I, Q6)."
    )
    return _result("collecting", reason, evidence)


def _q8(ctx: Ctx) -> dict[str, Any]:
    e = emitter("Q8")
    evidence = [
        e("Companies on the list", len(C.FROZEN_Q8), "companies", note=", ".join(C.FROZEN_Q8)),
        e(
            "Announcements needed for Holds",
            int(C.Q8["min_deals"]),
            "announcements",
            note="definitive merger agreements or public tender offers; reported talks and unsolicited proposals "
            "do not count",
        ),
        e(
            "Window",
            None,
            "dates",
            note=f"after {C.Q8['after']} and on or before {C.Q8['deadline']}; mid-point reading {C.Q8['midterm']}",
        ),
    ]
    reason = (
        f"Collecting: Q8 is a count of announcements for the {len(C.FROZEN_Q8)} companies of the list "
        f"({', '.join(C.FROZEN_Q8)}), checked by hand against filings and press releases and entered at the deadline "
        f"{C.Q8['deadline']}; no code grades it and it is never undecidable (section I, Q8)."
    )
    return _result("collecting", reason, evidence)


# --- Q10 ----------------------------------------------------------------------------------------------------
def _pair_counts(pairs: list[tuple[float, float]]) -> tuple[int, int]:
    """Pairs formed and pairs tested (both moves of at least ``readings.MOVE``), without grading anything."""
    return len(pairs), sum(1 for a, b in pairs if abs(a) >= R.MOVE and abs(b) >= R.MOVE)


def _next_monday(day: date) -> date:
    return day + timedelta(days=(R.MONDAY - day.weekday()) % 7)


def _q10(ctx: Ctx) -> dict[str, Any]:
    first, last = ITEMS["Q10"].dates
    minimum, days = int(C.Q10["min_pairs"]), sorted(ctx.snaps)
    tickers = sorted({t for f in ctx.snaps.values() for t in f["ticker"].to_list()})
    us_t, cn_t = [t for t in tickers if F.is_us_listed(t)], [t for t in tickers if F.is_china(t)]
    series = {t: R.tracked_series(ctx.snaps, t) for t in (*us_t, *cn_t)} if days else {}

    def pairs_upto(day: date, group: list[str]) -> list[tuple[float, float]]:
        return [p for t in group for p in R.q10_pairs({d: v for d, v in series[t].items() if d <= day})]

    def progress() -> list[dict[str, Any]]:
        formed, tested = _pair_counts(pairs_upto(days[-1], us_t)) if days else (0, 0)
        e = emitter("Q10", as_of=days[-1] if days else None)
        return [
            e("Monday snapshots collected", len(days), "snapshots"),
            e("Pairs formed so far", formed, "pairs", note="US-listed ring A companies"),
            e(
                "Tested pairs so far",
                tested,
                "pairs",
                note=f"both moves of at least {C.Q10['move']}; {minimum} are needed",
            ),
        ]

    if ctx.today < first:
        evidence = progress()
        reason = (
            f"Collecting: {int(evidence[2]['value'])} tested pairs so far. Graded on the first Monday snapshot on or "
            f"after {first.isoformat()} with at least {minimum} tested pairs; the direction counts are not shown "
            f"before then (section I, Q10)."
        )
        return _result("collecting", reason, evidence, next_grading=first.isoformat())

    graded_on, result = None, None
    for day in (d for d in days if first <= d <= last):
        res = R.q10(pairs_upto(day, us_t))
        if res["tested"] >= minimum:
            graded_on, result = day, res
            break
    if result is None and ctx.today >= last:
        upto = max((d for d in days if d <= last), default=None)
        graded_on, result = last, R.q10(pairs_upto(upto, us_t) if upto else [])
    if result is None:
        evidence = progress()
        reason = (
            f"Collecting: {int(evidence[2]['value'])} tested pairs so far and {minimum} are needed (section I, Q10)."
        )
        # the next Monday run that is not in yet: after the newest snapshot, and not in the past
        after_newest = _next_monday(days[-1] + timedelta(days=1)) if days else first
        nxt = max(after_newest, _next_monday(max(ctx.today, first)))
        return _result("collecting", reason, evidence, next_grading=nxt.isoformat() if nxt <= last else None)

    level, same, opposite = C.Q10["level"], result["same"], result["opposite"]
    p_opposite = S.binomial_p_at_least(opposite, result["tested"]) if result["tested"] else 1.0
    if result["tested"] < minimum:
        reason = (
            f"Undecidable: {result['tested']} tested pairs on {last} and a reading needs at least {minimum} "
            f"(section I, Q10)."
        )
    else:
        reason = _decide(
            result["status"],
            f"same-direction pairs ({same}) outnumber opposite ones ({opposite}) and the one-sided p "
            f"{_f(result['p_same'], 4)} is at most {level}",
            f"opposite-direction pairs ({opposite}) outnumber same-direction ones ({same}) and the one-sided p "
            f"{_f(p_opposite, 4)} is at most {level}",
            f"{same} same, {opposite} opposite, one-sided p of the same-direction count {_f(result['p_same'], 4)}",
        )
    cn_pairs = [(a, b) for a, b in pairs_upto(graded_on, cn_t) if abs(a) >= R.MOVE and abs(b) >= R.MOVE]
    cn_same = sum(1 for a, b in cn_pairs if a * b > 0)
    e = emitter("Q10", period=f"pairs to {graded_on}", as_of=graded_on)
    evidence = [
        e("Graded as of", None, "date", note="the first Monday snapshot with enough tested pairs, or the last date"),
        e("Tested pairs", result["tested"], "pairs"),
        e("Same-direction pairs", same, "pairs"),
        e("Opposite-direction pairs", opposite, "pairs"),
        e("Flat pairs", result["flat"], "pairs", note="second move under the threshold; counted and not tested"),
        e("One-sided p of the same-direction count", result["p_same"], "p", note="exact binomial against one half"),
        e(
            "A-share tested pairs",
            len(cn_pairs),
            "pairs",
            note=f"{cn_same} same-direction; shown and not part of the verdict; Yahoo's consensus as served",
        ),
    ]
    return _result(result["status"], reason, evidence, next_grading=None)


GRADERS: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "Q1": _q1, "Q2": _q2, "Q3": _q3, "Q4": _q4, "Q5": _q5, "Q6": _q6, "Q7": _q7, "Q8": _q8, "Q9": _q9, "Q10": _q10,
}  # fmt: skip


# --- already seen -------------------------------------------------------------------------------------------
NO_BASELINE = (
    f"The baseline file ({C.BASELINE_FILE_DATE}) is not among the runs supplied, so its reading is not reproduced "
    f"here; section I prints it."
)


def _baseline_readings(frame: pl.DataFrame, draws: int) -> dict[str, Any]:
    """The registered readings on the baseline file, read at its market date (2026-10-16)."""
    as_of = G.market_date(_d(C.BASELINE_FILE_DATE))
    us, cn = _panels({as_of: frame}, min_snapshots=1)
    return {
        "as_of": as_of,
        "frame": frame,
        "rows": {r["ticker"]: r for r in frame.iter_rows(named=True)},
        "us": us,
        "cn": cn,
        "q1": R.q1(us, draws=draws),
        "q2": R.q2(us, draws=draws),
        "q3": R.q3(us, draws=draws),
        "q7": R.q7(us, cn, draws=draws),
    }


def _signed(items: list[tuple[str, float]]) -> str:
    return ", ".join(f"{t} {_pct(v, 1, sign=True)}" for t, v in items)


def _seen_q1(b: dict[str, Any]) -> str:
    us, out = b["us"], b["q1"]
    groups = ", ".join(f"{g} {n}" for g, n in Counter(us.layers).most_common())
    other = ", ".join(t for t, g in zip(us.tickers, us.layers, strict=True) if g == S.OTHER)
    return (
        f"{out['n']} companies in the groups {groups}; {S.OTHER} is {other}. Growth alone has an adjusted R² of "
        f"{_f(out['adj_r2_growth'])}. Adding the layers changes it by {_f(out['uplift'], 2, sign=True)}, the "
        f"permutation p is {_f(out['perm_p'])}, and the {C.Q1['boot_level']} bootstrap interval for the uplift is "
        f"{_ci(out['lo'], out['hi'], sign=True)}. Applied to the baseline, the rule reads {out['status']}."
    )


ORDINALS = ["first", "second", "third"]
CARDINALS = {1: "one", 2: "two", 3: "three"}


def _holds_parts(parts: list[bool]) -> str:
    """Which of the three conditions of Q2's Holds are met, in the section's wording when the first k are."""
    met = sum(parts)
    if met == len(parts):
        return "All three parts of Holds are met"
    if met == 0:
        return "None of the three parts of Holds is met"
    if parts[:met] == [True] * met:  # the first k are met and the rest are not
        rest = ORDINALS[met:]
        head = "The first part of Holds is" if met == 1 else f"The first {CARDINALS[met]} parts of Holds are"
        return f"{head} met and the {' and '.join(rest)} {'is' if len(rest) == 1 else 'are'} not"
    named = [name for name, ok in zip(ORDINALS, parts, strict=True) if ok]
    return f"Of the three parts of Holds only the {' and '.join(named)} {'is' if len(named) == 1 else 'are'} met"


def _seen_q2(b: dict[str, Any]) -> str:
    out = b["q2"]
    parts = [out["r2"] >= float(C.Q2["r2_holds"]), out["b_lo"] > 0, out["gap_lo"] > 0]
    return (
        f"R² {_f(out['r2'])}. The interval for b is {_ci(out['b_lo'], out['b_hi'], 1)}. The standardised coefficients "
        f"are {_f(out['std_growth'])} for growth and {_f(out['std_fcf'])} for FCF margin, a difference of "
        f"{_f(out['gap'], 2, sign=True)} with an interval of {_ci(out['gap_lo'], out['gap_hi'], sign=True)}. "
        f"{_holds_parts(parts)}, so the rule reads {out['status']} on the baseline."
    )


def _seen_q3(b: dict[str, Any]) -> str:
    out, cut = b["q3"], C.Q3["cut"]
    return (
        f"{out['n_high']} companies at or above {cut} with a median of {_f(out['median_high'], 1)}x and "
        f"{out['n_low']} below with a median of {_f(out['median_low'], 1)}x, a ratio of {_f(out['ratio'], 1)}. The "
        f"{C.Q3['level']} interval is {_ci(out['lo'], out['hi'], 1)}. The group below {cut} is "
        f"{', '.join(out['low_tickers'])}. The rule reads {out['status']} on the baseline."
    )


def _seen_q7(b: dict[str, Any]) -> str:
    us, cn, out = b["us"], b["cn"], b["q7"]
    cn_range = f"{_f(float(cn.mult.min()), 1)}x to {_f(float(cn.mult.max()), 1)}x"
    growth = f"{_pct(float(cn.growth.min()), 0)} to {_pct(float(cn.growth.max()), 0)}"
    return (
        f"The {len(cn)} A-share companies have EV/NTM revenue of {cn_range} and a median of "
        f"{_f(float(np.median(cn.mult)), 1)}x, against {_f(float(np.median(us.mult)), 1)}x for the {len(us)}, a ratio "
        f"of {_f(out['ratio'])} with a {C.Q7['level']} interval of {_ci(out['ratio_lo'], out['ratio_hi'])}. Their "
        f"growth is {growth} against a median of {_pct(float(np.median(us.growth)), 0)} for the {len(us)}. The China "
        f"coefficient is {_f(out['coef'], 2, sign=True)} with an interval of "
        f"{_ci(out['lo'], out['hi'], sign=True)}, so the rule reads {out['status']} on the baseline."
    )


def _seen_q9(b: dict[str, Any]) -> str:
    prem = G.premium_parts(b["frame"], b["as_of"])
    if prem["ratio"] is None:
        return f"On the baseline the premium is not defined: {prem['reason']}."
    trio = sorted((F.ev_ntm(b["rows"][t], b["as_of"]), t) for t in C.Q9["trio"] if t in b["rows"])
    middle = trio[len(trio) // 2][1]  # the median of three is the middle one
    return (
        f"On the baseline the median of the three is {_f(prem['ai_trio_median'], 1)}x (it is {middle}) and the median "
        f"of the other {prem['n_rest']} is {_f(prem['rest_median'], 1)}x, a premium of {_f(prem['ratio'])}."
    )


def _seen_q4(b: dict[str, Any]) -> str:
    upside = {
        t: r["target_mean"] / r["price"] - 1.0
        for t, r in b["rows"].items()
        if t in C.FROZEN_Q4_Q5 and r["target_mean"] and r["price"]
    }
    ordered = sorted(upside.items(), key=lambda kv: kv[1])
    median = _pct(float(np.median(list(upside.values()))), 1, sign=True)
    return (
        f"The median upside across the {len(upside)} companies is {median}. The lowest are {_signed(ordered[:3])}. "
        f"The highest are {_signed(ordered[-3:])}."
    )


def _seen_q5(b: dict[str, Any]) -> str:
    r = b["rows"].get(Q5_EXAMPLE)
    if not r or not (r["target_high"] and r["target_low"] and r["target_mean"]):
        return "The regression has not been run."
    spread = (r["target_high"] - r["target_low"]) / r["target_mean"]
    return (
        f"{Q5_EXAMPLE} has a high target of {_f(r['target_high'], 0)}, a low of {_f(r['target_low'], 0)} and a mean "
        f"of {_f(r['target_mean'], 1)}, a dispersion of {_f(spread)}. The regression has not been run."
    )


def _seen_q6(b: dict[str, Any]) -> str:
    rows, as_of = b["rows"], b["as_of"]
    mult = F.ev_ntm(rows["NTSK"], as_of) if "NTSK" in rows else None
    mults = [v for t in F.q1_population(b["frame"]) if (v := F.ev_ntm(rows[t], as_of))]
    if not (mult and mults):
        return SEEN_Q6_DEALS
    return (
        f"{SEEN_Q6_DEALS} Netskope is at {_f(mult, 1)}x EV/NTM revenue on the baseline date against a population "
        f"median of {_f(float(np.median(mults)), 1)}x."
    )


def _seen_q8(b: dict[str, Any]) -> str:
    rows = b["rows"]
    vrns = rows.get("VRNS", {}).get("enterprise_value")
    trend = F.enterprise_value_usd(rows["4704.T"]) if "4704.T" in rows else None
    if not vrns or not trend:
        return f"Varonis {SEEN_Q8_EVENT} in the registration week."
    return (
        f"Varonis meets the same definition (held by CIBR, purity high, enterprise value ${_f(vrns / 1e9, 1)}B) and "
        f"{SEEN_Q8_EVENT} in the registration week. Press reports describe sale processes at {Q8_RUMOURED[0]} and at "
        f"{Q8_RUMOURED[1]} (Trend Micro, whose enterprise value is ${_f(trend / 1e9, 1)}B at the baseline rate); "
        f"neither has produced a deal."
    )


def _seen_q10(b: dict[str, Any] | None) -> str:
    return f"Nothing; the {C.FIRST_SNAPSHOT} snapshot is t-1 of the first pair."


SEEN: dict[str, Callable[[Any], str]] = {
    "Q1": _seen_q1, "Q2": _seen_q2, "Q3": _seen_q3, "Q4": _seen_q4, "Q5": _seen_q5, "Q6": _seen_q6, "Q7": _seen_q7,
    "Q8": _seen_q8, "Q9": _seen_q9, "Q10": _seen_q10,
}  # fmt: skip
NEEDS_BASELINE = [q for q in QIDS if q != "Q10"]


# --- schemas ------------------------------------------------------------------------------------------------
_DATE_NULL = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"), nullable=True)
EVIDENCE_DTYPES: dict[str, Any] = {
    "label": pl.Utf8, "value": pl.Float64, "lo": pl.Float64, "hi": pl.Float64, "level": pl.Utf8, "qualifier": pl.Utf8,
    "unit": pl.Utf8, "period": pl.Utf8, "as_of": pl.Utf8, "ref": pl.Utf8, "note": pl.Utf8,
}  # fmt: skip
SCORE_DTYPES: dict[str, Any] = {
    "id": pl.Utf8, "title": pl.Utf8, "status": pl.Utf8, "reason": pl.Utf8, "graded": pl.Utf8, "next_grading": pl.Utf8,
    "already_seen": pl.Utf8, "evidence": pl.List(pl.Struct(EVIDENCE_DTYPES)),
}  # fmt: skip
FLAT_DTYPES: dict[str, Any] = {"id": pl.Utf8, **EVIDENCE_DTYPES}


def _ten_in_order(d: pa.PolarsData) -> pl.LazyFrame:
    return d.lazyframe.select(pl.col("id").implode() == pl.lit(QIDS, dtype=pl.List(pl.Utf8)))


def _dated_only_while_collecting(d: pa.PolarsData) -> pl.LazyFrame:
    """Only a collecting item has a next grading date; an item that has a verdict has been graded."""
    return d.lazyframe.select((pl.col("status") == "collecting") | pl.col("next_grading").is_null())


SCOREBOARD_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, pa.Check.isin(QIDS)),
        "title": pa.Column(str, pa.Check.str_length(min_value=1)),
        "status": pa.Column(str, pa.Check.isin(C.QSTATUS)),
        "reason": pa.Column(str, pa.Check.str_length(min_value=1)),
        "graded": pa.Column(str, pa.Check.str_length(min_value=1)),
        "next_grading": _DATE_NULL,
        "already_seen": pa.Column(str, pa.Check.str_length(min_value=1)),
        "evidence": pa.Column(pl.List(pl.Struct(EVIDENCE_DTYPES))),
    },
    unique=["id"], strict=True, coerce=False,
    checks=[
        pa.Check(_ten_in_order, error="exactly Q1..Q10, in order"),
        pa.Check(_dated_only_while_collecting, error="only a collecting item has a next grading date"),
    ],
)  # fmt: skip


def _evidence_consistent(d: pa.PolarsData) -> pl.LazyFrame:
    v, lo, hi = pl.col("value"), pl.col("lo"), pl.col("hi")
    return d.lazyframe.select(
        (v.is_null() == pl.col("qualifier").is_null())  # a value has a qualifier and a qualifier a value
        & (lo.is_null() == hi.is_null())
        & (lo.is_null() | (lo <= hi))
        & (lo.is_null() | pl.col("level").is_not_null())
        & (v.is_null() | (pl.col("unit") != ""))
    )


EVIDENCE_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, pa.Check.isin(QIDS)),
        "label": pa.Column(str, pa.Check.str_length(min_value=1)),
        "value": pa.Column(pl.Float64, nullable=True),
        "lo": pa.Column(pl.Float64, nullable=True),
        "hi": pa.Column(pl.Float64, nullable=True),
        "level": pa.Column(str, pa.Check.str_matches(r"^\d+%$"), nullable=True),
        "qualifier": pa.Column(str, pa.Check.isin(["=", ">", "<", "~"]), nullable=True),
        "unit": pa.Column(str),
        "period": pa.Column(str, nullable=True),
        "as_of": _DATE_NULL,
        "ref": pa.Column(str, pa.Check.str_startswith("Section I, Q")),
        "note": pa.Column(str, nullable=True),
    },
    unique=["id", "label"], strict=True, coerce=False,
    checks=[pa.Check(_evidence_consistent, error="a value has a qualifier and a unit; an interval has both ends")],
)  # fmt: skip


def evidence_frame(board: list[dict[str, Any]]) -> pl.DataFrame:
    """The scoreboard's evidence as one flat table (id + the evidence columns), validated by ``EVIDENCE_SCHEMA``."""
    rows = [{"id": q["id"], **item} for q in board for item in q["evidence"]]
    return EVIDENCE_SCHEMA.validate(pl.DataFrame(rows, schema=FLAT_DTYPES))  # type: ignore[return-value]


def validate_scoreboard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows through ``SCOREBOARD_SCHEMA`` and ``EVIDENCE_SCHEMA`` and back to records."""
    board = SCOREBOARD_SCHEMA.validate(pl.DataFrame(rows, schema=SCORE_DTYPES)).to_dicts()  # type: ignore[union-attr]
    evidence_frame(board)
    return board


# --- the scoreboard -----------------------------------------------------------------------------------------
def _next_grading(qid: str, ctx: Ctx, result: dict[str, Any]) -> str | None:
    if result["next_grading"] is not UNSET:
        return result["next_grading"]
    if result["status"] != "collecting":
        return None
    upcoming = [d for d in ITEMS[qid].dates if d > ctx.today]
    return min(upcoming).isoformat() if upcoming else None


def score(
    runs: pl.DataFrame | None,
    ledger: Mapping[str, Any] | None,
    today: date,
    *,
    draws: int = S.DRAWS,
    returns: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """The ten items as ``[{id, title, status, reason, graded, next_grading, already_seen, evidence}]``, Q1 to Q10.

    ``runs`` is ``metrics.load_runs()`` (the baseline file included) or an empty frame; ``ledger`` is the ring C
    ledger's output (only ``private_intervals`` is read, for a count) or None; ``today`` is the day the page is built
    and fixes which items are graded. ``draws`` is the bootstrap and permutation draw count (the registered default
    is ``stats.DRAWS``). ``returns`` maps a ticker to its total return over the Q4 window and is read only for Q4,
    from its grading date. ``evidence`` is a list of ``{label, value, lo, hi, level, qualifier, unit, period, as_of,
    ref, note}``.
    """
    ctx = Ctx(
        today=today,
        snaps=G.series_snapshots(runs, today),
        baseline=G.baseline_frame(runs),
        draws=draws,
        ledger=ledger,
        returns=returns,
    )
    base = _baseline_readings(ctx.baseline, draws) if ctx.baseline is not None else None
    rows = []
    for qid, item in ITEMS.items():
        result = GRADERS[qid](ctx)
        seen = NO_BASELINE if base is None and qid in NEEDS_BASELINE else SEEN[qid](base)
        rows.append(
            {
                "id": qid,
                "title": item.title,
                "status": result["status"],
                "reason": result["reason"],
                "graded": item.graded,
                "next_grading": _next_grading(qid, ctx, result),
                "already_seen": seen,
                "evidence": result["evidence"],
            }
        )
    return validate_scoreboard(rows)
