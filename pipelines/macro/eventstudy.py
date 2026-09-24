"""How far the expected policy path moved in the minutes around each scheduled release.

The measure is the change in the probability-weighted expected move, summed over the next two FOMC
decisions (Σ probability × bps, as on the rest of the page), from T0 - 10 min to T0 + 20 min, where T0 is
the official release time. It is read from one-minute quotes on both platforms, carrying each market's
last quote forward through minutes it did not trade. A market with no quote at all in the look-back
contributes nothing to the change rather than voiding the event.

The probability of a hike is not used: through 2025 it sat below 2% at every meeting, so its changes
are zero and measure nothing. The expected move keeps moving when cut odds move.

Placebo: the same clock window (08:20 to 08:50 New York time) on weekdays with no scheduled release of
the kinds in `config.PLACEBO_EXCLUDE`, no FOMC decision that day or the day before, and not a Thursday.
It gives the size of an ordinary half hour, against which a release-window move can be judged.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

import polars as pl

from pipelines.macro import config
from pipelines.macro.intraday import QUOTE_COLS, kalshi_minutes, polymarket_minutes, prices_at
from pipelines.predmarkets.fomc import BPS, MEETINGS

log = logging.getLogger("macro.eventstudy")

KX_SERIES = "KXFEDDECISION"


def next_meetings(day: str, k: int = config.NEXT_K) -> list[str]:
    """The next k meetings decided on or after `day` (a release at 08:30 precedes a 14:00 decision)."""
    return [m for m in sorted(MEETINGS, key=MEETINGS.get) if MEETINGS[m] >= day][:k]


def fetch_quotes(kc, pc, markets: pl.DataFrame, start: int, end: int, keep_from: int) -> tuple[pl.DataFrame, int]:
    """One-minute quotes for `markets` (platform, market_id, yes_token, source) over [start, end],
    trimmed to rows at or after `keep_from` plus each market's last priced row before it (the quote
    carried into the window). Returns the quotes and how many markets failed to fetch."""
    rows: list[dict] = []
    failed = 0
    for r in markets.iter_rows(named=True):
        try:
            if r["platform"] == "kalshi":
                hist = r.get("source") == "historical"
                got = kalshi_minutes(kc, KX_SERIES, r["market_id"], start, end, historical=hist)
            elif r.get("yes_token"):
                got = polymarket_minutes(pc, r["yes_token"], r["market_id"], start, end)
            else:
                got = []
        except Exception as exc:  # noqa: BLE001 - one market failing costs that market, not the event
            log.warning("minutes failed %s %s: %s", r["platform"], r["market_id"], exc)
            failed += 1
            continue
        got.sort(key=lambda q: q["ts"])
        before = [q for q in got if q["ts"] < keep_from and q["price"] is not None]
        # a ts=0 row with no price records "fetched" even for a market with no quotes, so a cached
        # file can tell a quiet market from one it never asked for
        rows.append(
            {
                "platform": r["platform"],
                "market_id": r["market_id"],
                "ts": 0,
                "bid": None,
                "ask": None,
                "trade": None,
                "price": None,
            }
        )
        rows += before[-1:] + [q for q in got if q["ts"] >= keep_from]
    return pl.DataFrame(rows, schema=QUOTE_COLS), failed


def delta_bps(markets: pl.DataFrame, quotes: pl.DataFrame, t_from: int, t_to: int) -> dict:
    """Change in Σ p × bps between two instants over `markets`, per platform, with coverage."""
    out = {}
    for pf in ("kalshi", "polymarket"):
        mk = markets.filter(pl.col("platform") == pf)
        if mk.height == 0:
            out[pf] = None
            continue
        q = quotes.filter(pl.col("platform") == pf)
        a, b = prices_at(q, t_from), prices_at(q, t_to)
        both = [r for r in mk.iter_rows(named=True) if r["market_id"] in a and r["market_id"] in b]
        d = sum(BPS[r["bucket"]] * (b[r["market_id"]] - a[r["market_id"]]) for r in both)
        level = sum(BPS[r["bucket"]] * a[r["market_id"]] for r in both) if len(both) == mk.height else None
        out[pf] = {
            "d_bps": round(d, 2),
            "level_pre_bps": None if level is None else round(level, 1),
            "coverage": f"{len(both)}/{mk.height}",
        }
    return out


def path(markets: pl.DataFrame, quotes: pl.DataFrame, t0: int, minutes: range) -> list[dict]:
    """Change in Σ p × bps from T0 - PRE_MIN to each minute in `minutes` (relative to T0)."""
    rows = []
    for pf in ("kalshi", "polymarket"):
        mk = markets.filter(pl.col("platform") == pf)
        q = quotes.filter(pl.col("platform") == pf)
        if mk.height == 0 or q.height == 0:
            continue
        base = prices_at(q, t0 - config.PRE_MIN * 60)
        for k in minutes:
            now = prices_at(q, t0 + k * 60)
            both = [r for r in mk.iter_rows(named=True) if r["market_id"] in base and r["market_id"] in now]
            rows.append(
                {
                    "platform": pf,
                    "minute": k,
                    "d_bps": round(
                        sum(BPS[r["bucket"]] * (now[r["market_id"]] - base[r["market_id"]]) for r in both), 2
                    ),
                }
            )
    return rows


def placebo_days(cal: pl.DataFrame, start: str, end: str) -> list[str]:
    busy = set(cal.filter(pl.col("release").is_in(list(config.PLACEBO_EXCLUDE)))["date_et"].to_list())
    fomc = set(MEETINGS.values())
    fomc |= {(date.fromisoformat(d) + timedelta(days=1)).isoformat() for d in MEETINGS.values()}
    out = []
    d = date.fromisoformat(start)
    while d.isoformat() <= end:
        s = d.isoformat()
        if d.weekday() < 5 and d.weekday() != 3 and s not in busy and s not in fomc and not _holiday(d):
            out.append(s)
        d += timedelta(days=1)
    return out


def _holiday(d: date) -> bool:
    """New Year, Juneteenth, Independence Day, Christmas, plus the Monday/Thursday federal holidays.
    Approximate on purpose: a missed holiday is just a quiet placebo window."""
    fixed = {(1, 1), (6, 19), (7, 4), (12, 25), (11, 11)}
    if (d.month, d.day) in fixed:
        return True
    nth = (d.day - 1) // 7 + 1
    monday_holidays = {(1, 3), (2, 3), (9, 1), (10, 2)}  # MLK, Presidents, Labor, Columbus
    if d.weekday() == 0 and (d.month, nth) in monday_holidays:
        return True
    return d.weekday() == 0 and d.month == 5 and (d + timedelta(days=7)).month == 6  # Memorial Day


# --- summary statistics -----------------------------------------------------------------------------
def ols(x: list[float], y: list[float]) -> dict | None:
    n = len(x)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return None
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / sxx
    icpt = my - slope * mx
    resid = [b - (icpt + slope * a) for a, b in zip(x, y, strict=True)]
    se = math.sqrt(sum(e * e for e in resid) / (n - 2) / sxx)
    syy = sum((b - my) ** 2 for b in y)
    r2 = 1 - sum(e * e for e in resid) / syy if syy else None
    return {"n": n, "slope": slope, "se": se, "t": slope / se if se else None, "r2": r2}


def quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def sign_agreement(pairs: list[tuple[float, float]]) -> dict:
    """Share of events where a non-zero hawkish surprise and a non-zero move point the same way."""
    use = [(s, d) for s, d in pairs if s and d]
    same = sum(1 for s, d in use if (s > 0) == (d > 0))
    return {"n": len(use), "same": same, "share": round(same / len(use), 3) if use else None}
