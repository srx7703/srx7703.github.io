"""Turn fiscal-year consensus into calendar-year consensus.

Analysts forecast fiscal years. Half this pool does not end its fiscal year in December: Coherent,
Lumentum and Fabrinet end in June, the Japanese names in March, Broadcom and Ciena in early November,
Marvell and Semtech in January, Credo in April, MACOM in October, Cisco in July, Jabil in August.
Putting a June-year "FY2027" next to a December-year "2027E" on the same chart would compare different
spans of time, so every estimate is restated onto calendar years before anything is compared.

The rule is a month-overlap blend. A fiscal year ending on E covers ``(E - 1 year, E]``. Each of the
twelve months of a calendar year is assigned to whichever fiscal year contains it, and a fiscal year's
weight is the number of months it picks up:

    CY = Σ_FY  (months of CY inside FY / 12) × EPS(FY)

For a June year end that is the familiar half-and-half (``CY2026 = ½ FY2026 actual + ½ FY2027 estimate``);
for a March year end it is a quarter and three quarters; for Broadcom's early-November year end it is ten
twelfths and two twelfths. A December filer gets a single weight of 1 and passes through untouched.

Months rather than days is deliberate. Day-counting would make the same June filer 0.496/0.504 because
the second half of a year is three days longer, which is precision the underlying data does not have and
a weight no one could check by hand. Months also make a fiscal year that ends mid-month (Lumentum's
27 June, Broadcom's 2 November) land on the same weights as a month-end filer.

Two consequences are deliberate and are stated on the page:
  * A calendar year usually needs the *prior* fiscal year as well, so a non-December company's calendar
    2026 is part actual and part estimate. ``actual_weight`` records how much.
  * If the available fiscal years do not cover the calendar year, no number is produced. A partial cover
    would look like a low estimate rather than a missing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Every month of the calendar year must be covered by a supplied fiscal year. Month weights are exact
# twelfths, so a partial cover means a fiscal year is genuinely missing, not a rounding artefact.
MIN_COVERAGE = 0.999


@dataclass(frozen=True)
class FiscalEstimate:
    period_end: date
    eps: float
    mark: str  # "A" actual | "E" estimate
    n_analysts: int | None = None
    currency: str | None = None

    @property
    def period_start(self) -> date:
        """Fiscal years are treated as exactly one year long: (end - 1 year, end]."""
        return _minus_one_year(self.period_end) + timedelta(days=1)


@dataclass(frozen=True)
class CalendarEstimate:
    year: int
    eps: float
    coverage: float  # share of the calendar year covered before normalisation
    actual_weight: float  # share of the blend that is already-reported actuals
    n_analysts: int | None
    currency: str | None
    parts: tuple[tuple[str, float, float], ...]  # (period_end ISO, weight, eps) for the audit trail

    @property
    def is_blend(self) -> bool:
        return len(self.parts) > 1


def _minus_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, month=2, day=28)


def _month_marker(year: int, month: int) -> date:
    """The middle of a month, used to decide which fiscal year that month belongs to."""
    return date(year, month, 15)


def weights_for(estimates: list[FiscalEstimate], year: int) -> list[tuple[FiscalEstimate, float]]:
    """Weight of each fiscal year in one calendar year, as twelfths.

    Each month is assigned to at most one fiscal year, so weights never double-count. If two supplied
    fiscal years overlap (which would mean bad input), the earlier-ending one wins and the overlap is
    reported as missing coverage rather than counted twice.
    """
    ordered = sorted(estimates, key=lambda e: e.period_end)
    counts: dict[date, int] = {}
    for month in range(1, 13):
        marker = _month_marker(year, month)
        for e in ordered:
            if e.period_start <= marker <= e.period_end:
                counts[e.period_end] = counts.get(e.period_end, 0) + 1
                break
    by_end = {e.period_end: e for e in ordered}
    return [(by_end[end], n / 12.0) for end, n in counts.items()]


def calendarize(estimates: list[FiscalEstimate], year: int) -> CalendarEstimate | None:
    """Blend fiscal-year estimates into one calendar year, or None if they do not cover it."""
    parts = weights_for(estimates, year)
    if not parts:
        return None
    coverage = sum(w for _, w in parts)
    if coverage < MIN_COVERAGE:
        return None
    scale = 1.0 / coverage
    eps = sum(e.eps * w * scale for e, w in parts)
    actual_weight = sum(w * scale for e, w in parts if e.mark == "A")
    counts = [e.n_analysts for e, _ in parts if e.mark == "E" and e.n_analysts is not None]
    currencies = {e.currency for e, _ in parts if e.currency}
    return CalendarEstimate(
        year=year,
        eps=eps,
        coverage=coverage,
        actual_weight=actual_weight,
        # the thinnest contributing estimate governs: a blend is only as covered as its weakest part
        n_analysts=min(counts) if counts else None,
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        parts=tuple((e.period_end.isoformat(), round(w * scale, 6), e.eps) for e, w in parts),
    )


def fiscal_year_end_after(as_of: date, fy_end_month: int) -> date:
    """The first fiscal-year end strictly after `as_of`, used to cross-check a reported period end."""
    year = as_of.year
    candidate = _month_end(year, fy_end_month)
    if candidate <= as_of:
        candidate = _month_end(year + 1, fy_end_month)
    return candidate


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def fy_label(period_end: date) -> str:
    """Fiscal years are labelled by the calendar year they end in: 2027-06-30 -> FY2027."""
    return f"FY{period_end.year}"


def from_rows(rows: list[dict]) -> list[FiscalEstimate]:
    """Build FiscalEstimate objects from estimates-table rows, skipping anything without an EPS."""
    out: list[FiscalEstimate] = []
    for r in rows:
        eps = r.get("eps_avg")
        pe = r.get("period_end")
        if eps is None or not pe:
            continue
        out.append(
            FiscalEstimate(
                period_end=date.fromisoformat(pe),
                eps=float(eps),
                mark=r.get("mark") or "E",
                n_analysts=r.get("n_analysts"),
                currency=r.get("currency"),
            )
        )
    return sorted(out, key=lambda e: e.period_end)
