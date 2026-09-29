"""Turning-point dating for monthly series, as pre-registered in
预注册_周期定时与检验规则_20260928.md §1 (simplified Bry-Boschan on log levels, no smoothing).

Rules, in order:
  1. candidate peak/trough: the max/min of a 13-month window (6 either side); ties -> earliest month.
     Candidates in the first 6 months of the series are dropped; candidates within 6 months of the
     last observation are kept but flagged unconfirmed (their window is truncated).
  2. alternation: of two adjacent peaks keep the higher, of two adjacent troughs the lower (ties -> earlier).
  3. durations: every phase >= 6 months, every full cycle (P->P, T->T) >= 15 months.
  4. amplitude: every phase moves at least `amp` (rise >= +amp, fall <= -amp) in level terms.
  Step 3 is resolved before step 4 (as the text orders them): while any duration violation exists, the two
  endpoints of the smallest phase (by |log change|) among the phases implicated in a duration violation are
  removed and the procedure returns to step 2; only then is step 4 applied the same way to amplitude
  violations, again returning to step 2 after each deletion.  (Until 2026-09-28 evening the two checks were
  pooled; a reviewer found the pooled order differs from the text only at thresholds of 51-60%, never at the
  registered 0/20/30%.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

HALF_WINDOW = 6
MIN_PHASE = 6
MIN_CYCLE = 15


@dataclass(frozen=True)
class Turn:
    i: int          # position in the series
    month: str      # YYYY-MM
    kind: str       # 'P' or 'T'
    value: float
    confirmed: bool


def _months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split('-'))
    yb, mb = map(int, b.split('-'))
    return (yb - ya) * 12 + (mb - ma)


def _candidates(months: list[str], values: list[float]) -> list[Turn]:
    n = len(values)
    out = []
    for i in range(HALF_WINDOW, n):
        lo, hi = i - HALF_WINDOW, min(n - 1, i + HALF_WINDOW)
        win = values[lo:hi + 1]
        before = values[lo:i]
        x = values[i]
        is_peak = x >= max(win) and all(x > v for v in before)
        is_trough = x <= min(win) and all(x < v for v in before)
        if is_peak or is_trough:
            out.append(Turn(i, months[i], 'P' if is_peak else 'T', x, i + HALF_WINDOW <= n - 1))
    return out


def _alternate(tp: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for t in tp:
        if out and out[-1].kind == t.kind:
            keep_new = t.value > out[-1].value if t.kind == 'P' else t.value < out[-1].value
            if keep_new:
                out[-1] = t
        else:
            out.append(t)
    return out


def _logchg(a: Turn, b: Turn) -> float:
    return abs(math.log(b.value / a.value))


EPS = 1e-9  # so an exact +-20% phase is not flagged by floating-point noise


def _duration_violations(tp: list[Turn]) -> set[int]:
    """Indices k of phases (tp[k] -> tp[k+1]) implicated in a duration violation (step 3)."""
    bad: set[int] = set()
    for k in range(len(tp) - 1):
        if _months_between(tp[k].month, tp[k + 1].month) < MIN_PHASE:
            bad.add(k)
    for k in range(len(tp) - 2):
        if _months_between(tp[k].month, tp[k + 2].month) < MIN_CYCLE:
            bad.update({k, k + 1})
    return bad


def _amplitude_violations(tp: list[Turn], amp: float) -> set[int]:
    """Indices k of phases whose rise/fall is smaller than amp (step 4); amp = 0 means no amplitude test."""
    bad: set[int] = set()
    if amp <= 0:
        return bad
    for k in range(len(tp) - 1):
        a, b = tp[k], tp[k + 1]
        ratio = b.value / a.value
        if (a.kind == 'T' and ratio - 1 < amp - EPS) or (a.kind == 'P' and ratio - 1 > -amp + EPS):
            bad.add(k)
    return bad


def date_turns(months: list[str], values: list[float], amp: float) -> list[Turn]:
    tp = _alternate(_candidates(months, values))
    while True:
        bad = _duration_violations(tp) or _amplitude_violations(tp, amp)
        if not bad:
            return tp
        k = min(bad, key=lambda j: (_logchg(tp[j], tp[j + 1]), j))
        tp = _alternate(tp[:k] + tp[k + 2:])


def phases(tp: list[Turn]) -> list[dict]:
    rows = []
    for a, b in zip(tp, tp[1:]):
        rows.append({
            'from': a.month, 'from_kind': a.kind, 'to': b.month, 'to_kind': b.kind,
            'months': _months_between(a.month, b.month),
            'change': b.value / a.value - 1,
            'to_confirmed': b.confirmed,
        })
    return rows
