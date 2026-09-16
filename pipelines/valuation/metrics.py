"""Valuation maths: trailing PE, calendar-year forward PE, dispersion and revisions.

Everything here is a pure function of the snapshot tables, so the numbers on the page can be
recomputed from the archived data at any time.

Three rules run through the whole module:

**Currencies never mix silently.** A listing's price currency, its reporting currency and the currency
its consensus is quoted in can all differ. CATL's Hong Kong line trades in HKD, reports in CNY and has a
CNY consensus; Ilika is quoted in pence and reports in pounds. Every ratio converts explicitly through
the FRED rates and records which currencies it used.

**A negative denominator is not a PE.** A loss-making company gets ``None`` and a reason, never a
negative multiple. Roughly a third of the solid-state pool is pre-profit, and a negative PE on a chart
reads as "cheap".

**Every output carries its own provenance.** Each metric comes back with the inputs it used and, where
it was blended across fiscal years, how much of the blend is already-reported actuals.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Collection, Iterable, Mapping
from datetime import date

from pipelines.valuation.calendarize import (
    CalendarEstimate,
    CalendarResult,
    calendar_estimate_or_reason,
    from_rows,
)
from pipelines.valuation.config import THIN_COVERAGE_BELOW, Company

log = logging.getLogger("valuation.metrics")

# Venues that quote in a minor unit, mapped to the major currency the financials are stated in.
PENCE_UNITS = {"GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
PENCE_DIVISOR = 100.0

# Reasons a ratio is not meaningful. These strings reach the page, so they are written for a reader.
NM_NO_EARNINGS = "no trailing earnings"
NM_LOSS = "loss-making over the trailing twelve months"
NM_NEG_FORECAST = "consensus forecasts a loss"
NM_NO_CONSENSUS = "no consensus for this year"
NM_NO_PRICE = "no price"
NM_NO_FX = "no exchange rate"
# Three different facts used to arrive as NM_NO_CONSENSUS, and only one of them is a statement about
# analysts. A source that failed to fetch is ours, not theirs; a fiscal calendar that runs off the end
# of what the source publishes is a limit of the panel we hold, not an absence of coverage.
NM_SOURCE_UNAVAILABLE = "consensus source returned nothing this run"
NM_PARTIAL_COVER = "the fiscal years on file ({fiscal_years}) cover {months} of the 12 months of calendar {year}"


def major_currency(currency: str | None) -> str | None:
    """"GBp" is a quote unit, not a currency; the FX table only knows "GBP"."""
    if currency is None:
        return None
    return PENCE_UNITS.get(currency, currency)


def normalise_quote(price: float | None, currency: str | None) -> tuple[float | None, str | None]:
    """London and a few other venues quote in minor units. Convert to the major unit once, here."""
    if price is None or currency is None:
        return price, currency
    if currency in PENCE_UNITS:
        return price / PENCE_DIVISOR, PENCE_UNITS[currency]
    return price, currency


def convert(amount: float | None, frm: str | None, to: str | None, rates: dict[str, float]) -> float | None:
    """Convert through USD using FRED rates expressed as units of the currency per 1 USD."""
    if amount is None or not frm or not to:
        return None
    if frm == to:
        return amount
    r_from, r_to = rates.get(frm), rates.get(to)
    if not r_from or not r_to:
        return None
    return amount / r_from * r_to


def _finite(x: float | None) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return x


def trailing_pe(
    *,
    market_cap: float | None,
    market_cap_currency: str | None,
    ttm_net_income: float | None,
    reporting_currency: str | None,
    rates: dict[str, float],
) -> tuple[float | None, str | None]:
    """Market cap over trailing-twelve-month attributable profit, both in the reporting currency.

    Using market cap rather than price-over-EPS sidesteps share-count conventions, which differ across
    these markets: A-share "basic EPS" is on the weighted average count for the period, while a US
    filer's diluted count includes options that a Chinese filer would not report the same way.
    """
    if market_cap is None:
        return None, NM_NO_PRICE
    if ttm_net_income is None:
        return None, NM_NO_EARNINGS
    if ttm_net_income <= 0:
        return None, NM_LOSS
    # Unlike the price, market cap is published in the MAJOR unit even for a pence-quoted listing
    # (Ilika: 23.5 GBp x 198.9m shares = the 46.7m GBP Yahoo reports), so it is relabelled, not divided.
    cap = convert(market_cap, major_currency(market_cap_currency), reporting_currency, rates)
    if cap is None:
        return None, NM_NO_FX
    return _finite(cap / ttm_net_income), None


def forward_pe(
    *,
    price: float | None,
    price_currency: str | None,
    eps: float | None,
    eps_currency: str | None,
    rates: dict[str, float],
) -> tuple[float | None, str | None]:
    """Price over calendar-year consensus EPS, with the consensus converted into the price currency."""
    px, px_ccy = normalise_quote(price, price_currency)
    if px is None:
        return None, NM_NO_PRICE
    if eps is None:
        return None, NM_NO_CONSENSUS
    if eps <= 0:
        return None, NM_NEG_FORECAST
    # The consensus can arrive in the same minor unit the price is quoted in. Relabelling "GBp" as
    # "GBP" without dividing would make the multiple a hundred times too small, so both sides of the
    # ratio go through the same normalisation.
    eps_major, eps_ccy = normalise_quote(eps, eps_currency)
    eps_in_px = convert(eps_major, eps_ccy, px_ccy, rates)
    if eps_in_px is None:
        return None, NM_NO_FX
    return _finite(px / eps_in_px), None


def expected_growth(eps_this: float | None, eps_next: float | None) -> float | None:
    """Year-on-year growth of the consensus. Undefined unless both years are profitable."""
    if eps_this is None or eps_next is None or eps_this <= 0 or eps_next <= 0:
        return None
    return _finite(eps_next / eps_this - 1.0)


def dispersion(low: float | None, high: float | None, avg: float | None) -> float | None:
    """Spread between the highest and lowest forecast, as a share of the mean."""
    if None in (low, high, avg) or not avg:
        return None
    return _finite((high - low) / abs(avg))


def revision(current: float | None, past: float | None) -> float | None:
    """Change in the consensus since an earlier vintage, as a share of the earlier figure."""
    if current is None or past is None or not past:
        return None
    return _finite(current / abs(past) - math.copysign(1.0, past))


def calendar_results(rows: list[dict], years: tuple[int, ...]) -> dict[int, CalendarResult]:
    """Calendar-year blend per year for one listing, each carrying the coverage behind it."""
    fiscal = from_rows(rows)
    return {y: calendar_estimate_or_reason(fiscal, y) for y in years}


def calendar_estimates(rows: list[dict], years: tuple[int, ...]) -> dict[int, CalendarEstimate]:
    """Calendar-year consensus for one listing, years that could not be covered left out."""
    return {y: r.estimate for y, r in calendar_results(rows, years).items() if r.estimate is not None}


def sources_with_rows(estimates_by_ticker: Mapping[str, Iterable[dict]]) -> frozenset[str]:
    """Which estimate sources produced at least one row this run.

    A source that fetched nothing at all is a failed fetch, and every listing routed to it is blank for
    that reason rather than because analysts do not cover it. Pass the result to ``company_row`` as
    ``sources_ran``; see ``NM_SOURCE_UNAVAILABLE``.
    """
    return frozenset(r["source"] for rows in estimates_by_ticker.values() for r in rows if r.get("source"))


def consensus_reason(
    result: CalendarResult,
    *,
    company: Company,
    estimate_rows: list[dict],
    sources_ran: Collection[str] | None = None,
) -> str:
    """Why this listing has no calendar-year consensus, said as something a reader can check.

    Order matters. Partial cover is checked first because those listings *do* have a consensus — the
    fiscal years on file simply run out before the calendar year does, and saying "no consensus" of a
    company with a full analyst panel is false. A failed fetch is next, and is only claimed when the run
    is known to have produced nothing from that source; without that knowledge we do not assert it.
    """
    if result.coverage > 0.0 and result.estimate is None:
        return NM_PARTIAL_COVER.format(
            fiscal_years=", ".join(result.fiscal_years),
            months=result.months_covered,
            year=result.year,
        )
    if not estimate_rows and sources_ran is not None and company.estimates not in sources_ran:
        return NM_SOURCE_UNAVAILABLE
    return NM_NO_CONSENSUS


# A revision is a change of mind over a fixed panel of analysts. "eastmoney_rebuilt" is not that: each
# broker publishes once, so its mean moves as coverage grows. It is fine to plot as "the consensus as it
# stood" and wrong to difference into a revision, so the revision columns take only these origins.
REVISION_ORIGINS = ("yahoo_trend", "snapshot")


def vintage_estimates(
    vintage_rows: list[dict], years: tuple[int, ...], *, origins: tuple[str, ...] | None = None
) -> dict[str, dict[int, float]]:
    """Calendar-year consensus as of each earlier date, so revisions compare like with like.

    A vintage is only usable if that date's fiscal rows still cover the calendar year; a date with a
    single fiscal year on file is skipped rather than reported as a collapse in the estimate. Pass
    `origins` to restrict which kind of vintage counts; see REVISION_ORIGINS.
    """
    if origins is not None:
        vintage_rows = [r for r in vintage_rows if r.get("origin") in origins]
    by_date: dict[str, list[dict]] = {}
    for r in vintage_rows:
        as_of = r.get("as_of")
        if as_of:
            by_date.setdefault(as_of, []).append(r)
    out: dict[str, dict[int, float]] = {}
    for as_of, rows in by_date.items():
        cal = calendar_estimates(rows, years)
        if cal:
            out[as_of] = {y: ce.eps for y, ce in cal.items()}
    return out


def nearest_vintage(vintages: dict[str, dict[int, float]], target: date, year: int) -> tuple[str, float] | None:
    """The vintage closest to a target date that actually has a figure for `year`."""
    candidates = [(d, v[year]) for d, v in vintages.items() if year in v]
    if not candidates:
        return None
    best = min(candidates, key=lambda dv: abs((date.fromisoformat(dv[0]) - target).days))
    if abs((date.fromisoformat(best[0]) - target).days) > 30:
        return None
    return best


def coverage_flag(n_analysts: int | None) -> str:
    """How much weight a reader should put on the consensus."""
    if n_analysts is None:
        return "unknown"
    if n_analysts < THIN_COVERAGE_BELOW:
        return "thin"
    return "covered"


def company_row(
    company: Company,
    *,
    price_row: dict | None,
    estimate_rows: list[dict],
    vintage_rows: list[dict],
    ttm: dict | None,
    rates: dict[str, float],
    years: tuple[int, int],
    as_of: date,
    sources_ran: Collection[str] | None = None,
) -> dict:
    """One fully-derived row for the marts and the page.

    ``sources_ran`` is the set of estimate sources that produced rows anywhere in this run, from
    ``sources_with_rows``. It is what separates "this listing's source failed" from "analysts do not
    cover this listing"; leave it None and the row falls back to the weaker, non-committal reason.
    """
    y0, y1 = years
    price = (price_row or {}).get("price")
    price_ccy = (price_row or {}).get("currency")
    px_major, px_ccy_major = normalise_quote(price, price_ccy)
    reporting_ccy = (ttm or {}).get("currency") or (price_row or {}).get("financial_currency")

    cal = calendar_results(estimate_rows, years)
    ce0, ce1 = cal[y0].estimate, cal[y1].estimate

    # A consensus with no stated currency is left null by the fetcher rather than guessed, which is the
    # right call there and unusable here: CALB's Hong Kong line then has a HKD price, a CNY income
    # statement and a null-currency EPS, so no multiple can be formed at all. The company's own
    # reporting currency is the only defensible fallback, and the row records that it was assumed.
    stated_ccy = (ce0.currency if ce0 else None) or (ce1.currency if ce1 else None)
    estimate_ccy = stated_ccy or reporting_ccy
    assumed_ccy = stated_ccy is None and estimate_ccy is not None

    t_pe, t_nm = trailing_pe(
        market_cap=(price_row or {}).get("market_cap"),
        market_cap_currency=price_ccy,
        ttm_net_income=(ttm or {}).get("ttm_net_income"),
        reporting_currency=reporting_ccy,
        rates=rates,
    )
    f0, f0_nm = forward_pe(
        price=price, price_currency=price_ccy,
        eps=ce0.eps if ce0 else None, eps_currency=(ce0.currency if ce0 else None) or estimate_ccy, rates=rates,
    )
    f1, f1_nm = forward_pe(
        price=price, price_currency=price_ccy,
        eps=ce1.eps if ce1 else None, eps_currency=(ce1.currency if ce1 else None) or estimate_ccy, rates=rates,
    )

    def sharpen(nm: str | None, year: int) -> str | None:
        """Replace the catch-all reason with the one that is actually true for this listing."""
        if nm != NM_NO_CONSENSUS:
            return nm
        return consensus_reason(cal[year], company=company, estimate_rows=estimate_rows, sources_ran=sources_ran)

    f0_nm, f1_nm = sharpen(f0_nm, y0), sharpen(f1_nm, y1)

    # The spread is blended on the same weights as the mean it is divided by, and is None unless every
    # contributing fiscal year carries a range; see calendarize._blend_bound.
    disp = dispersion(ce0.eps_low, ce0.eps_high, ce0.eps) if ce0 else None

    vints = vintage_estimates(vintage_rows, years, origins=REVISION_ORIGINS)
    rev: dict[str, float | None] = {}
    for label, days in (("30d", 30), ("90d", 90)):
        hit = nearest_vintage(vints, date.fromordinal(as_of.toordinal() - days), y0)
        rev[f"revision_{label}"] = revision(ce0.eps if ce0 else None, hit[1] if hit else None)
        rev[f"revision_{label}_as_of"] = hit[0] if hit else None

    n_analysts = ce0.n_analysts if ce0 else None
    return {
        "ticker": company.ticker,
        "name": company.name,
        "name_cn": company.name_cn or None,
        "track": company.track,
        "purity": company.purity,
        "market": company.market,
        "fy_end_month": company.fy_end_month,
        "segment_line": company.segment_line,
        "price": px_major,
        "price_currency": major_currency(px_ccy_major),
        "market_cap": (price_row or {}).get("market_cap"),
        "reporting_currency": reporting_ccy,
        "ttm_net_income": (ttm or {}).get("ttm_net_income"),
        "ttm_revenue": (ttm or {}).get("ttm_revenue"),
        "ttm_period_end": (ttm or {}).get("period_end"),
        "ttm_basis": (ttm or {}).get("basis"),
        "ttm_n_quarters": (ttm or {}).get("n_quarters"),
        "trailing_pe": t_pe,
        "trailing_pe_nm": t_nm,
        f"eps_{y0}": ce0.eps if ce0 else None,
        f"eps_{y1}": ce1.eps if ce1 else None,
        "estimate_currency": estimate_ccy,
        "estimate_currency_assumed": assumed_ccy,
        f"fwd_pe_{y0}": f0,
        f"fwd_pe_{y0}_nm": f0_nm,
        f"fwd_pe_{y1}": f1,
        f"fwd_pe_{y1}_nm": f1_nm,
        "growth": expected_growth(ce0.eps if ce0 else None, ce1.eps if ce1 else None),
        "n_analysts": n_analysts,
        "coverage": coverage_flag(n_analysts),
        "dispersion": disp,
        "blend": bool(ce0 and ce0.is_blend),
        "actual_weight": ce0.actual_weight if ce0 else None,
        "estimate_source": (estimate_rows[0].get("source") if estimate_rows else None),
        **rev,
    }
