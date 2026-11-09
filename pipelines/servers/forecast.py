"""The registered monthly-revenue forecast for the Taiwan makers, and its scoring.

Method (docs/prereg/ai-servers/2026-11-09_forecasts.md): the point forecast for month t is revenue in the same month
a year earlier times one plus the mean year-over-year growth of the three most recent months published before t.
The 80% interval multiplies the point by the 10th and 90th percentiles of the same formula's past errors (actual /
forecast) for that company, over every month from 2019-01 to the month before the registration.

Usage:
    uv run python -m pipelines.servers.forecast            # print the forecast table (used once, at registration)
"""

from __future__ import annotations

import math
import sys

import polars as pl

from pipelines.servers import config as cfg

FORECAST_CODES = ("2382", "6669", "3231", "2356", "2317", "2376", "2357", "3706")
BACKTEST_FIRST = "2019-01"


def series(monthly: pl.DataFrame, code: str) -> dict[str, float]:
    return {r["month"]: r["revenue_k"] for r in monthly.filter(pl.col("code") == code).iter_rows(named=True)}


def shift(month: str, k: int) -> str:
    y, m = map(int, month.split("-"))
    n = y * 12 + (m - 1) + k
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def yoy(s: dict[str, float], month: str) -> float | None:
    a, b = s.get(month), s.get(shift(month, -12))
    return a / b - 1 if a and b else None


def point(s: dict[str, float], target: str, latest: str) -> tuple[float, list[str]] | None:
    """Forecast for ``target`` from the three months ending at ``latest`` (the newest month known at the time)."""
    used = [shift(latest, -k) for k in (2, 1, 0)]
    rates = [yoy(s, m) for m in used]
    base = s.get(shift(target, -12))
    if base is None or any(r is None for r in rates):
        return None
    return base * (1 + sum(rates) / 3), used


def backtest_errors(s: dict[str, float], first: str, last: str) -> list[float]:
    """log(actual / forecast) for every month in [first, last], forecasting each month from the month before it."""
    out, m = [], first
    while m <= last:
        p = point(s, m, shift(m, -1))
        if p and s.get(m):
            out.append(math.log(s[m] / p[0]))
        m = shift(m, 1)
    return out


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def forecast_table(monthly: pl.DataFrame, target: str) -> list[dict]:
    rows = []
    for code in FORECAST_CODES:
        s = series(monthly, code)
        known = [m for m in s if m < target]
        latest = max(known)
        p = point(s, target, latest)
        if p is None:
            continue
        errs = backtest_errors(s, BACKTEST_FIRST, shift(latest, -1))
        lo, hi = quantile(errs, 0.10), quantile(errs, 0.90)
        rows.append({"code": code, "name": cfg.BY_KEY[code].name, "target": target, "latest_used": latest,
                     "months_used": p[1], "base_last_year_k": s[shift(target, -12)],
                     "mean_yoy": p[0] / s[shift(target, -12)] - 1, "point_k": p[0],
                     "lo80_k": p[0] * math.exp(lo), "hi80_k": p[0] * math.exp(hi), "n_backtest": len(errs),
                     "backtest_median_abs_pct": 100 * quantile([abs(math.exp(e) - 1) for e in errs], 0.5)})
    return rows


def main() -> int:
    monthly = pl.read_parquet(cfg.SNAP / "tw_monthly.parquet")
    target = sys.argv[1] if len(sys.argv) > 1 else "2026-11"
    for r in forecast_table(monthly, target):
        print(f"| {r['code']} {r['name']} | {', '.join(r['months_used'])} | {r['base_last_year_k']:,.0f} | "
              f"{100 * r['mean_yoy']:+.1f}% | {r['point_k']:,.0f} | {r['lo80_k']:,.0f} – {r['hi80_k']:,.0f} | "
              f"{r['n_backtest']} | {r['backtest_median_abs_pct']:.1f}% |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
