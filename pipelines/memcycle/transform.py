"""Turning-point dating for a monthly index: the registered rule, ported from ``research_ref/bb.py``.

The rule is the simplified Bry-Boschan procedure of prereg §1, applied to log levels with no smoothing:

1. **Candidates.** A month is a candidate peak (trough) when it is the highest (lowest) value of the 13-month
   window around it, six months either side. Ties go to the earliest month, implemented as "strictly above
   (below) every earlier month in the window" (implementation note 3). Candidates in the first six months of
   the series are dropped; candidates within six months of the last observation are kept and flagged
   unconfirmed, because their window is truncated.
2. **Alternation.** Of two adjacent peaks keep the higher, of two adjacent troughs the lower; ties keep the
   earlier.
3. **Durations.** Every phase lasts at least ``MIN_PHASE`` months and every full cycle (peak to peak, trough to
   trough) at least ``MIN_CYCLE``. While any violation exists, the two endpoints of the smallest phase (by
   absolute log change) *among the phases implicated in a violation* are removed and the procedure returns to
   step 2 (implementation note 1). A short cycle implicates both of its phases.
4. **Amplitude.** Only once no duration violation is left: every rise is at least ``+amp`` and every fall at
   most ``-amp``, resolved the same way. ``amp = 0`` switches the test off.

Step 3 is resolved before step 4, as the text orders them (correction 3). The last phase, ending at an
unconfirmed turn, obeys both constraints like any other (implementation note 2).

On top of the research code this module records every deletion, in order, because the page shows what the rule
removed: three real contract-price rallies of under six months, between 1999 and 2002, are exactly what a
reader who remembers those years will look for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pipelines.memcycle.config import HALF_WINDOW, MIN_CYCLE, MIN_PHASE

EPS = 1e-9  # so an exact +-20% phase is not flagged by floating-point noise


# ---------------------------------------------------------------------------------------------
# month arithmetic on 'YYYY-MM' strings
# ---------------------------------------------------------------------------------------------


def months_between(a: str, b: str) -> int:
    """Signed number of months from ``a`` to ``b``."""
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def add_months(m: str, k: int) -> str:
    y, mo = map(int, m.split("-"))
    t = y * 12 + (mo - 1) + k
    return f"{t // 12}-{t % 12 + 1:02d}"


def month_range(a: str, b: str) -> list[str]:
    """Months in the half-open window (a, b]."""
    out, m = [], add_months(a, 1)
    while m <= b:
        out.append(m)
        m = add_months(m, 1)
    return out


# ---------------------------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    i: int  # position in the series
    month: str  # YYYY-MM
    kind: str  # 'P' or 'T'
    value: float
    confirmed: bool


@dataclass(frozen=True)
class Deletion:
    """One application of step 3 or 4: the phase ``a -> b`` was removed."""

    order: int
    step: str  # 'duration' | 'amplitude'
    a: Turn
    b: Turn
    margin: float | None  # log-change gap to the runner-up among the violating phases; None if it was alone

    @property
    def months(self) -> int:
        return months_between(self.a.month, self.b.month)

    @property
    def change(self) -> float:
        return self.b.value / self.a.value - 1


def candidates(months: list[str], values: list[float]) -> list[Turn]:
    n = len(values)
    out = []
    for i in range(HALF_WINDOW, n):
        lo, hi = i - HALF_WINDOW, min(n - 1, i + HALF_WINDOW)
        win = values[lo : hi + 1]
        before = values[lo:i]
        x = values[i]
        is_peak = x >= max(win) and all(x > v for v in before)
        is_trough = x <= min(win) and all(x < v for v in before)
        if is_peak or is_trough:
            out.append(Turn(i, months[i], "P" if is_peak else "T", x, i + HALF_WINDOW <= n - 1))
    return out


def alternate(tp: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for t in tp:
        if out and out[-1].kind == t.kind:
            keep_new = t.value > out[-1].value if t.kind == "P" else t.value < out[-1].value
            if keep_new:
                out[-1] = t
        else:
            out.append(t)
    return out


def _logchg(a: Turn, b: Turn) -> float:
    return abs(math.log(b.value / a.value))


def duration_violations(tp: list[Turn]) -> set[int]:
    """Indices k of phases (tp[k] -> tp[k+1]) implicated in a duration violation (step 3)."""
    bad: set[int] = set()
    for k in range(len(tp) - 1):
        if months_between(tp[k].month, tp[k + 1].month) < MIN_PHASE:
            bad.add(k)
    for k in range(len(tp) - 2):
        if months_between(tp[k].month, tp[k + 2].month) < MIN_CYCLE:
            bad.update({k, k + 1})
    return bad


def amplitude_violations(tp: list[Turn], amp: float) -> set[int]:
    """Indices k of phases whose rise or fall is smaller than ``amp`` (step 4); ``amp = 0`` means no test."""
    bad: set[int] = set()
    if amp <= 0:
        return bad
    for k in range(len(tp) - 1):
        a, b = tp[k], tp[k + 1]
        ratio = b.value / a.value
        if (a.kind == "T" and ratio - 1 < amp - EPS) or (a.kind == "P" and ratio - 1 > -amp + EPS):
            bad.add(k)
    return bad


def date_turns_logged(months: list[str], values: list[float], amp: float) -> tuple[list[Turn], list[Deletion]]:
    """The registered rule, returning the turns and the ordered list of deletions that produced them."""
    if len(months) != len(values):
        raise ValueError("months and values differ in length")
    if any(v <= 0 for v in values):
        raise ValueError("the rule works on log levels; every value must be positive")
    tp = alternate(candidates(months, values))
    log: list[Deletion] = []
    while True:
        dur = duration_violations(tp)
        bad, step = (dur, "duration") if dur else (amplitude_violations(tp, amp), "amplitude")
        if not bad:
            return tp, log
        ranked = sorted(bad, key=lambda j: (_logchg(tp[j], tp[j + 1]), j))
        k = ranked[0]
        margin = _logchg(tp[ranked[1]], tp[ranked[1] + 1]) - _logchg(tp[k], tp[k + 1]) if len(ranked) > 1 else None
        log.append(Deletion(len(log) + 1, step, tp[k], tp[k + 1], margin))
        tp = alternate(tp[:k] + tp[k + 2 :])


def date_turns(months: list[str], values: list[float], amp: float) -> list[Turn]:
    return date_turns_logged(months, values, amp)[0]


def phases(tp: list[Turn]) -> list[dict]:
    rows = []
    for a, b in zip(tp, tp[1:], strict=False):
        rows.append({
            "from": a.month, "from_kind": a.kind, "to": b.month, "to_kind": b.kind,
            "months": months_between(a.month, b.month),
            "change": b.value / a.value - 1,
            "to_confirmed": b.confirmed,
        })
    return rows


def cycles_from(turns: list[Turn]) -> list[dict]:
    """(T(k-1), P(k), T(k), P(k+1)) around every peak, plus the previous peak; missing ends are None."""
    out = []
    for i, t in enumerate(turns):
        if t.kind != "P":
            continue
        out.append({
            "prev_trough": turns[i - 1] if i >= 1 and turns[i - 1].kind == "T" else None,
            "peak": t,
            "trough": turns[i + 1] if i + 1 < len(turns) else None,
            "next_peak": turns[i + 2] if i + 2 < len(turns) else None,
            "prev_peak": turns[i - 2] if i >= 2 else None,
        })
    return out
