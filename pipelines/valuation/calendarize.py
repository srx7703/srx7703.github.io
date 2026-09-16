"""Turn fiscal-year consensus into calendar-year consensus.

Analysts forecast fiscal years. Fourteen of the 34 optical listings, and seven of the 34 on the battery
track, do not end the fiscal year in December: Coherent, Lumentum and Fabrinet end in June, the Japanese
names in March, Broadcom and Ciena in early November, Marvell and Semtech in January, Credo in April,
MACOM in October, Cisco in July, Jabil in August. Putting a June-year "FY2027" next to a December-year
"2027E" on the same chart would compare different spans of time, so every estimate is restated onto
calendar years before anything is compared.

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

The low and the high of the forecast range are blended with those same weights, so the spread the page
reports is the spread of the calendar year it is printed against. Taking the lowest forecast of one
fiscal year and the highest of the next would fold a year of earnings growth into "disagreement".

Two consequences are deliberate and are stated on the page:
  * A calendar year usually needs the *prior* fiscal year as well, so a non-December company's calendar
    2026 is part actual and part estimate. ``actual_weight`` records how much.
  * If the available fiscal years do not cover the calendar year, no number is produced. A partial cover
    would look like a low estimate rather than a missing one. That is not the same situation as having
    nothing on file, and a reader is owed the difference, so ``calendar_estimate_or_reason`` hands back
    the coverage and the fiscal years behind it either way. ``calendarize`` keeps its original contract.
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
    eps_low: float | None = None  # lowest forecast on the panel; a reported actual has no range
    eps_high: float | None = None

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
    # Blended on the same weights as `eps`, or None when any contributing fiscal year has no range.
    eps_low: float | None = None
    eps_high: float | None = None

    @property
    def is_blend(self) -> bool:
        return len(self.parts) > 1


@dataclass(frozen=True)
class CalendarResult:
    """One calendar year's blend, plus the coverage that produced it or stopped it.

    ``calendarize`` returns ``None`` for two situations that are not the same fact: no fiscal years on
    file at all, and fiscal years that cover only part of the year. A caller that has to explain the
    blank to a reader needs to tell them apart, so this carries both. ``estimate`` is the blend when the
    year is covered; ``coverage`` and ``fiscal_years`` describe what was on file either way.
    """

    year: int
    estimate: CalendarEstimate | None
    coverage: float  # share of the calendar year the fiscal years on file do cover
    fiscal_years: tuple[str, ...]  # FY labels on file, so a reason can name them

    @property
    def months_covered(self) -> int:
        """Coverage in whole months. Weights are exact twelfths, so this is not a rounding fudge."""
        return round(self.coverage * 12)


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


def _blend_bound(pairs: list[tuple[float | None, float]]) -> float | None:
    """Blend a low or a high on the mean's own weights, or return None if any part has no bound.

    Falling back to whichever parts do carry a bound is what made the spread column wrong: it divided
    one fiscal year's full range by a mean blended from two, so a listing whose calendar year straddles
    two forecast years was reported as the most contested name in the pool when it was not.
    """
    total = 0.0
    for value, weight in pairs:
        if value is None:
            return None
        total += value * weight
    return total


def calendar_estimate_or_reason(estimates: list[FiscalEstimate], year: int) -> CalendarResult:
    """Blend fiscal-year estimates into one calendar year, keeping the coverage when it cannot.

    Same maths as ``calendarize``; the difference is that a failure comes back described rather than as
    a bare ``None``, so the caller can say whether nothing was on file or the years on file fell short.
    """
    labels = tuple(sorted({fy_label(e.period_end) for e in estimates}))
    parts = weights_for(estimates, year)
    coverage = sum(w for _, w in parts)
    if not parts or coverage < MIN_COVERAGE:
        return CalendarResult(year=year, estimate=None, coverage=coverage, fiscal_years=labels)
    scale = 1.0 / coverage
    weighted = [(e, w * scale) for e, w in parts]
    counts = [e.n_analysts for e, _ in weighted if e.mark == "E" and e.n_analysts is not None]
    currencies = {e.currency for e, _ in weighted if e.currency}
    estimate = CalendarEstimate(
        year=year,
        eps=sum(e.eps * w for e, w in weighted),
        coverage=coverage,
        actual_weight=sum(w for e, w in weighted if e.mark == "A"),
        # the thinnest contributing estimate governs: a blend is only as covered as its weakest part
        n_analysts=min(counts) if counts else None,
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        parts=tuple((e.period_end.isoformat(), round(w, 6), e.eps) for e, w in weighted),
        eps_low=_blend_bound([(e.eps_low, w) for e, w in weighted]),
        eps_high=_blend_bound([(e.eps_high, w) for e, w in weighted]),
    )
    return CalendarResult(year=year, estimate=estimate, coverage=coverage, fiscal_years=labels)


def calendarize(estimates: list[FiscalEstimate], year: int) -> CalendarEstimate | None:
    """Blend fiscal-year estimates into one calendar year, or None if they do not cover it."""
    return calendar_estimate_or_reason(estimates, year).estimate


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
                eps_low=r.get("eps_low"),
                eps_high=r.get("eps_high"),
            )
        )
    return sorted(out, key=lambda e: e.period_end)
