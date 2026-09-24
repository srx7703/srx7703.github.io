"""Scheduled data releases, and what Kalshi's ladders expected each one to print.

Consensus forecasts from Bloomberg or Reuters surveys are paid. The free substitute used here is the
market's own: Kalshi lists a ladder of "Will CPI rise more than x%?" contracts for every release, and
their prices ten minutes before the release form a survival curve, P(print > x), whose mean is the
market's expected print. Surprise = first print - that mean.

Two traps shape the code:

- The ladder's own ``close_time`` is not the release time. For releases the 2025 shutdown postponed,
  Kalshi closed the ladder at the originally scheduled time weeks earlier, so release times come from
  the official BLS and BEA calendars (`config.CALENDAR_PATH`) and a ladder is matched to a release only
  if it was still trading ten minutes before it.
- ``expiration_value`` is free text: "0.40", "0.3%", "22,000", once "Above 0.2%". It is parsed, then
  checked against every market's settled result; where the two disagree, or the text does not parse,
  the print is recovered from the results (the lowest strike that settled No, on a ladder spaced at the
  print's own resolution).
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import polars as pl

from pipelines.macro import config
from pipelines.macro.intraday import kalshi_minutes, price_at
from pipelines.predmarkets.kalshi import KalshiClient

log = logging.getLogger("macro.releases")

ET = ZoneInfo("America/New_York")
#: Ladder quotes wider than this are not a probability.
LADDER_MAX_SPREAD = 0.25


def et_epoch(day: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ET).timestamp())


def calendar() -> pl.DataFrame:
    """Official release times: release, t0 (epoch seconds), release_et, date_et, title, source_url."""
    with config.CALENDAR_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        day, hhmm = r["release_et"].split(" ")
        out.append({**r, "date_et": day, "t0": et_epoch(day, hhmm)})
    return pl.DataFrame(out).sort("t0")


def iso_epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def strike_of(m: dict, resolution: float) -> float | None:
    """The "above x" threshold, rounded to the print's resolution (Kalshi stores 4.1 as 4.099999 and
    old payroll strikes as 99999)."""
    s = m.get("floor_strike")
    if s is None:
        hit = re.search(r"-T(-?\d+(?:\.\d+)?)$", m.get("ticker") or "")
        s = float(hit.group(1)) if hit else None
    if s is None or m.get("strike_type") not in (None, "greater"):
        return None
    return round(round(float(s) / resolution) * resolution, 6)


def parse_value(text: object) -> float | None:
    t = str(text or "").strip().replace(",", "").rstrip("%").strip()
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def settled_print(markets: list[dict], resolution: float) -> tuple[float | None, str]:
    """The first print, from `expiration_value` if it agrees with every settled result, else from the
    results alone. Returns (value, how)."""
    ladder = [(strike_of(m, resolution), m.get("result")) for m in markets]
    ladder = [(s, r) for s, r in ladder if s is not None and r in ("yes", "no")]
    parsed = next((v for v in (parse_value(m.get("expiration_value")) for m in markets) if v is not None), None)
    if parsed is not None and all((parsed > s + 1e-9) == (r == "yes") for s, r in ladder):
        return parsed, "expiration_value"
    yes = [s for s, r in ladder if r == "yes"]
    no = [s for s, r in ladder if r == "no"]
    if yes and no and min(no) - max(yes) <= resolution + 1e-9:
        return min(no), "results"
    return None, "unresolved"


def pav_decreasing(y: list[float]) -> list[float]:
    """Least-squares non-increasing fit (pool adjacent violators): a survival curve cannot rise."""
    blocks: list[list[float]] = []  # [sum, count]
    for v in y:
        blocks.append([v, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    out: list[float] = []
    for s, n in blocks:
        out += [s / n] * int(n)
    return out


def ladder_mean(strikes: list[float], survival: list[float], resolution: float) -> float:
    """Mean of the print implied by P(print > strike).

    Mass between two strikes goes to the upper strike when they are one resolution step apart (the only
    value a print can take there), otherwise to the midpoint. The two tails go half a step beyond the
    outermost strikes (one full step above the top one on a unit-spaced ladder).
    """
    pairs = sorted(zip(strikes, survival, strict=True))
    xs = [s for s, _ in pairs]
    sv = [min(1.0, max(0.0, p)) for p in pav_decreasing([p for _, p in pairs])]
    step_lo = xs[1] - xs[0] if len(xs) > 1 else resolution
    step_hi = xs[-1] - xs[-2] if len(xs) > 1 else resolution
    unit_lo, unit_hi = step_lo <= resolution + 1e-9, step_hi <= resolution + 1e-9
    total = (1 - sv[0]) * (xs[0] if unit_lo else xs[0] - step_lo / 2)
    for i in range(len(xs) - 1):
        gap = xs[i + 1] - xs[i]
        at = xs[i + 1] if gap <= resolution + 1e-9 else (xs[i] + xs[i + 1]) / 2
        total += (sv[i] - sv[i + 1]) * at
    total += sv[-1] * (xs[-1] + resolution if unit_hi else xs[-1] + step_hi / 2)
    return total


def ladder_events(kc: KalshiClient, series: str, since: str) -> list[dict]:
    """Every settled event of a ladder series closing on or after `since`, with its markets."""
    out = []
    for ev in kc.iter_events(status="settled", series_ticker=series):
        markets = ev.get("markets") or []
        source = "live"
        if not markets:
            markets, source = list(kc.iter_historical_markets(event_ticker=ev["event_ticker"])), "historical"
        closes = sorted({m.get("close_time") for m in markets if m.get("close_time")})
        if not closes or closes[-1][:10] < since:
            continue
        out.append(
            {
                "series": series,
                "event": ev["event_ticker"],
                "close_ts": iso_epoch(closes[-1]),
                "markets": markets,
                "source": source,
            }
        )
    return out


def match(cal: pl.DataFrame, ladders: list[dict], release: str) -> tuple[list[dict], list[dict]]:
    """Pair each official release of this type with the ladder event that was trading into it.

    Returns (matched, unmatched official releases with the reason)."""
    matched, missing = [], []
    rows = cal.filter(pl.col("release") == release).iter_rows(named=True)
    for r in rows:
        t0 = r["t0"]
        pre = t0 - config.PRE_MIN * 60
        hit = [e for e in ladders if pre <= e["close_ts"] <= t0 + config.LADDER_CLOSE_AFTER_T0_MAX_H * 3600]
        if hit:
            matched.append(
                {"release": release, "t0": t0, "date_et": r["date_et"], "title": r["title"], "ladder": hit[0]}
            )
        else:
            before = sorted((e for e in ladders if t0 - 90 * 86400 < e["close_ts"] < pre), key=lambda e: e["close_ts"])
            if before:
                hours = (t0 - before[-1]["close_ts"]) / 3600
                gap = f"{hours:.1f} hours" if hours < 48 else f"{hours / 24:.0f} days"
                last = before[-1]["event"]
                reason = f"no ladder trading at T0 - {config.PRE_MIN} min; {last} closed {gap} earlier"
            else:
                reason = "no ladder listed"
            missing.append({"release": release, "t0": t0, "date_et": r["date_et"], "reason": reason})
    return matched, missing


def consensus(kc: KalshiClient, ev: dict, t0: int) -> dict:
    """Ladder-implied mean at T0 - PRE_MIN, the settled print, and the surprise."""
    series = ev["series"]
    res = config.LADDERS[series]["resolution"]
    at = t0 - config.PRE_MIN * 60
    strikes, surv, quotes = [], [], []
    for m in ev["markets"]:
        s = strike_of(m, res)
        if s is None:
            continue
        rows = kalshi_minutes(
            kc,
            series,
            m["ticker"],
            at - config.CARRY_HOURS * 3600,
            at,
            max_spread=LADDER_MAX_SPREAD,
            historical=ev.get("source") == "historical",
        )
        rows.sort(key=lambda q: q["ts"])
        p = price_at([q["ts"] for q in rows], [q["price"] for q in rows], at)
        quotes.append({"ticker": m["ticker"], "strike": s, "p": p})
        if p is not None:
            strikes.append(s)
            surv.append(p)
    actual, how = settled_print(ev["markets"], res)
    mean = ladder_mean(strikes, surv, res) if len(strikes) >= 2 else None
    surprise = actual - mean if actual is not None and mean is not None else None
    scale = 1000.0 if series == "KXPAYROLLS" else 1.0  # payrolls reported in thousands
    return {
        "series": series,
        "event": ev["event"],
        "n_strikes": len(strikes),
        "n_markets": len(quotes),
        "consensus": None if mean is None else round(mean / scale, 4),
        "actual": None if actual is None else round(actual / scale, 4),
        "actual_from": how,
        "surprise": None if surprise is None else round(surprise / scale, 4),
        "hawkish_surprise": None
        if surprise is None
        else round(config.LADDERS[series]["hawkish"] * surprise / scale, 4),
        "quotes": quotes,
    }


def utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
