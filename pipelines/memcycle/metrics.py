"""Stock-side windows, leads and up-leg multiples (prereg §2-3), ported from ``research_ref/phase1.py``.

Every function here is pure: it takes a month -> {close, high, low, partial} mapping and a price cycle and
returns months, leads and ratios. In CI there are no closes (vendor terms; the repo is public), so these run
only inside ``freeze.py`` on the owner's machine and in unit tests on synthetic series. ``publish.py`` reads the
frozen output they produced.

Definitions, for cycle k (price trough T(k-1) -> peak P(k) -> trough T(k)):

* **stock peak**: the month with the highest month-end close in (T(k-1), T(k)]; ties go to the earliest month.
* **stock trough**: the lowest close in (P(k), P(k+1)], or to the latest month for the running cycle.
* **lead** = price-peak month minus stock-peak month. Positive means the stock topped first.
* **complete**: every month of the window has a close, and neither the listing month nor the delisting month
  falls inside it (implementation note 5, correction 4).
* **up-leg multiple**: the stock-peak close over the lowest close in (previous price peak, stock peak].
  Auxiliary: intramonth high over intramonth low on the same windows. Excess: over the local benchmark's
  multiple on the same two months. USD: both ends converted at that month's FRED rate.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from pipelines.memcycle.transform import add_months, month_range, months_between

FIELDS = [
    "product", "company", "label", "group", "dram_maker", "market", "currency", "first_month", "last_month",
    "price_peak", "price_peak_confirmed", "window", "complete", "status", "stock_peak", "stock_peak_close",
    "lead_months", "stock_peak_high_month", "lead_months_high", "stock_trough", "stock_trough_close",
    "trough_lead_months", "drawdown_to_trough", "upleg_low", "upleg_low_close", "upleg_multiple",
    "upleg_start", "upleg_truncated_by_listing", "upleg_multiple_hl", "upleg_low_hl_month", "upleg_multiple_usd",
    "upleg_multiple_usd_series", "delisted_or_out_of_scope", "bench1", "bench1_multiple", "excess1", "bench2",
    "bench2_multiple", "excess2", "partial_latest_month",
]

Px = dict[str, dict]  # month -> {'close': float | None, 'high': ..., 'low': ..., 'partial': bool}


def _ym(m: str) -> int:
    return int(m.replace("-", ""))


def read_monthly(path: Path) -> Px | None:
    return parse_monthly(path.read_text()) if path.exists() else None


def parse_monthly(text: str) -> Px | None:
    """A local monthly file (month, close, high, low, notes). Months with neither a close nor a high are skipped;
    a month with only a high keeps ``close=None``, which makes any window containing it incomplete."""
    rows: Px = {}
    for r in csv.DictReader(io.StringIO(text, newline="")):
        if not r.get("close") and not r.get("high"):
            continue
        rows[r["month"]] = {
            "close": float(r["close"]) if r.get("close") else None,
            "high": float(r["high"]) if r.get("high") else None,
            "low": float(r["low"]) if r.get("low") else None,
            "partial": "partial" in (r.get("notes") or "").lower(),
        }
    return rows or None


def closes(px: Px) -> list[str]:
    return [m for m in px if px[m]["close"] is not None]


def to_usd(value: float, currency: str, month: str, fx: dict) -> float | None:
    if currency == "USD":
        return value
    table, usd_per_unit = fx[currency]
    r = table.get(month)
    if r is None:
        return None
    return value * r if usd_per_unit else value / r


def analyse_company(px: Px, cyc: dict, latest: str, first: str, delisted: bool) -> dict:
    """Stock peak, trough, lead and multiples for one company in one price cycle."""
    pk = cyc["peak"]
    res: dict = {"price_peak": pk.month, "price_peak_confirmed": pk.confirmed}
    if cyc["prev_trough"] is None:
        res["status"] = "excluded: no preceding price trough (first ECOS peak)"
        return res
    win_end = cyc["trough"].month if cyc["trough"] else latest
    win = month_range(cyc["prev_trough"].month, win_end)
    have = [m for m in win if m in px and px[m]["close"] is not None]
    res["window"] = f"({cyc['prev_trough'].month}, {win_end}]"
    res["complete"] = (len(have) == len(win) and first <= cyc["prev_trough"].month
                       and not (delisted and latest <= win_end))
    if not have:
        res["status"] = "no data in window"
        return res
    sp = max(have, key=lambda m: (px[m]["close"], -_ym(m)))
    res["stock_peak"] = sp
    res["stock_peak_close"] = px[sp]["close"]
    res["lead_months"] = months_between(sp, pk.month)
    highs = [m for m in win if m in px and px[m]["high"] is not None]
    hp = None
    if highs:
        hp = max(highs, key=lambda m: (px[m]["high"], -_ym(m)))
        res["stock_peak_high_month"] = hp
        res["lead_months_high"] = months_between(hp, pk.month)
    t_end = cyc["next_peak"].month if cyc["next_peak"] else latest
    tw = [m for m in month_range(pk.month, t_end) if m in px and px[m]["close"] is not None]
    if tw:
        st = min(tw, key=lambda m: (px[m]["close"], _ym(m)))
        res["stock_trough"] = st
        res["stock_trough_close"] = px[st]["close"]
        res["drawdown_to_trough"] = px[st]["close"] / px[sp]["close"] - 1 if st > sp else None
        if cyc["trough"]:
            res["trough_lead_months"] = months_between(st, cyc["trough"].month)
    up_start = cyc["prev_peak"].month if cyc["prev_peak"] else min(closes(px))
    uw = [m for m in month_range(up_start, sp) if m in px and px[m]["close"] is not None]
    if uw:
        lo = min(uw, key=lambda m: (px[m]["close"], _ym(m)))
        res["upleg_low"] = lo
        res["upleg_low_close"] = px[lo]["close"]
        res["upleg_multiple"] = px[sp]["close"] / px[lo]["close"]
        res["upleg_start"] = up_start
        # the low sits in the first 3 months of trading after a listing that came after the previous price peak:
        # the multiple is measured from (near) the IPO rather than from a cycle low
        res["upleg_truncated_by_listing"] = first > up_start and lo <= add_months(first, 2)
    if hp is not None:
        lw = [m for m in month_range(up_start, hp) if m in px and px[m]["low"] is not None]
        if lw:
            lo_hl = min(lw, key=lambda m: (px[m]["low"], _ym(m)))
            res["upleg_multiple_hl"] = px[hp]["high"] / px[lo_hl]["low"]
            res["upleg_low_hl_month"] = lo_hl
    res["status"] = "ok"
    return res


def add_usd_and_bench(r: dict, px: Px, cyc: dict, currency: str, latest: str, fx: dict,
                      bench: list[tuple[str, Px | None]]) -> dict:
    """USD multiples, benchmark multiples and excess for an 'ok' row (prereg §3)."""
    lo, sp = r["upleg_low"], r["stock_peak"]
    ulo, usp = to_usd(px[lo]["close"], currency, lo, fx), to_usd(px[sp]["close"], currency, sp, fx)
    r["upleg_multiple_usd"] = usp / ulo if ulo and usp else None
    # alternative: pick the low and the peak on the USD-converted series itself (same windows)
    usd = {m: to_usd(v["close"], currency, m, fx) for m, v in px.items() if v["close"] is not None}
    end = cyc["trough"].month if cyc["trough"] else latest
    pwin = [m for m in month_range(cyc["prev_trough"].month, end) if usd.get(m)]
    if pwin:
        usp2 = max(pwin, key=lambda m: (usd[m], -_ym(m)))
        uw2 = [m for m in month_range(r["upleg_start"], usp2) if usd.get(m)]
        if uw2:
            r["upleg_multiple_usd_series"] = usd[usp2] / min(usd[m] for m in uw2)
    for i, (blab, bpx) in enumerate(bench):
        if bpx and lo in bpx and sp in bpx:
            bm = bpx[sp]["close"] / bpx[lo]["close"]
            r[f"bench{i + 1}"] = blab
            r[f"bench{i + 1}_multiple"] = bm
            r[f"excess{i + 1}"] = r["upleg_multiple"] / bm
    r["partial_latest_month"] = any(px[m]["partial"] for m in (sp, r.get("stock_trough", sp)))
    return r
