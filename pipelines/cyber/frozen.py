"""The registered populations and the next-twelve-month arithmetic, computed from a consensus snapshot.

Section I freezes two lists by name (Q4/Q5 and Q8) and defines three populations by rule (Q1/Q2, the A-share
qualifiers for Q7, and the weekly panel). This module is the rule: ``tests/test_frozen.py`` recomputes every list
from the committed baseline snapshot and fails if ``config`` disagrees, so a list cannot be edited by hand to
suit a result. The same functions are what the weekly analysis calls.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from pipelines.common.storage import REPO_ROOT, SNAP_DIR
from pipelines.cyber.pool import Entry, market_of

BASELINE_FILE = SNAP_DIR / "cyber" / "consensus" / "2026-10-17.parquet"
REGISTERED_POOL_FILE = REPO_ROOT / "data" / "reference" / "cyber" / "pool_registered.json"
CIBR_FILE = REPO_ROOT / "data" / "reference" / "cyber" / "cibr_holdings_2026-10-15.json"
# The one non-dollar enterprise value among the Q8 candidates. Yahoo JPY=X, close of 2026-10-16.
BASELINE_PER_USD = {"USD": 1.0, "JPY": 157.99}
MIN_ANALYSTS = 3
US_LISTED_MARKET = "us"


def is_china(ticker: str) -> bool:
    return ticker.endswith((".SS", ".SZ"))


def is_us_listed(ticker: str) -> bool:
    """Listed on a US exchange: Yahoo gives US listings no suffix. A Canadian or other suffixed line is not."""
    return market_of(ticker) == US_LISTED_MARKET and "." not in ticker


def registered_pool(path: Path = REGISTERED_POOL_FILE) -> dict[str, Entry]:
    """The pool as registered. Populations are computed from this and not from the live pool, so a company that is
    acquired after registration stays in the list it was frozen into."""
    rows = json.loads(path.read_text(encoding="utf-8"))["pool"]
    return {r["ticker"]: Entry(**{**r, "also_in": tuple(r["also_in"])}) for r in rows}


def load_baseline(path: Path = BASELINE_FILE) -> pl.DataFrame:
    return pl.read_parquet(path)


def load_cibr(path: Path = CIBR_FILE) -> list[str]:
    return [h["ticker"] for h in json.loads(path.read_text(encoding="utf-8"))["holdings"]]


# --- next-twelve-month arithmetic ----------------------------------------------------------------------------
def ntm_weight(fy0_end: str | None, as_of: date) -> float | None:
    """Share of the next twelve months that falls in fiscal year 0: days to its end over 365, between 0 and 1.

    Fiscal year 0 is the first year whose annual report Yahoo has not processed, so it can already have ended
    (a December company in January). Then the weight is 0 and the next twelve months are fiscal year +1.
    """
    if not fy0_end:
        return None
    days = (date.fromisoformat(fy0_end) - as_of).days
    return min(1.0, max(0.0, days / 365.0))


def _consensus(row: Mapping[str, Any]) -> tuple[str | None, Any, Any]:
    """Fiscal-year-0 end and the two revenue means. A-shares use East Money's consensus, whose years are calendar
    years; everyone else uses Yahoo's, dated by ``fy0_end``."""
    if is_china(row["ticker"]):
        year = row.get("em_year0")
        return (f"{year}-12-31" if year else None), row.get("em_rev0_mean"), row.get("em_rev1_mean")
    return row.get("fy0_end"), row.get("rev_fy0_avg"), row.get("rev_fy1_avg")


def ntm_revenue(row: Mapping[str, Any], as_of: date) -> float | None:
    fy0_end, rev0, rev1 = _consensus(row)
    w = ntm_weight(fy0_end, as_of)
    if w is None or rev1 is None or (w > 0 and rev0 is None):
        return None
    return w * (rev0 or 0.0) + (1.0 - w) * rev1


def currencies_agree(row: Mapping[str, Any]) -> bool:
    """Yahoo states enterpriseValue in the quote currency (pence read as pounds) and the revenue consensus in the
    financial currency. Where they differ the ratio mixes currencies, so no multiple is computed."""
    quote, financial = row.get("currency"), row.get("financial_currency")
    if not quote or not financial:
        return False
    return ("GBP" if quote == "GBp" else quote) == financial


def ev_ntm(row: Mapping[str, Any], as_of: date) -> float | None:
    """Enterprise value over next-twelve-month revenue, or None if either is missing or the currencies differ."""
    ev, ntm = row.get("enterprise_value"), ntm_revenue(row, as_of)
    if not currencies_agree(row):
        return None
    return ev / ntm if ev and ev > 0 and ntm and ntm > 0 else None


def growth(row: Mapping[str, Any], as_of: date) -> float | None:
    ntm, ttm = ntm_revenue(row, as_of), row.get("ttm_revenue")
    return ntm / ttm - 1.0 if ntm and ttm and ttm > 0 else None


def fcf_margin(row: Mapping[str, Any]) -> float | None:
    fcf, ttm = row.get("ttm_free_cash_flow"), row.get("ttm_revenue")
    return fcf / ttm if fcf is not None and ttm and ttm > 0 else None


def enterprise_value_usd(row: Mapping[str, Any], per_usd: Mapping[str, float] = BASELINE_PER_USD) -> float | None:
    ev, ccy = row.get("enterprise_value"), row.get("financial_currency")
    return ev / per_usd[ccy] if ev and ccy in per_usd else None


# --- populations ---------------------------------------------------------------------------------------------
def _pool(ticker: str) -> Entry | None:
    return _REGISTERED.get(ticker)


_REGISTERED = registered_pool()


def q1_population(df: pl.DataFrame) -> list[str]:
    """Ring A, listed on a US exchange, purity high, status listed, and FY0 and FY+1 revenue consensus each from
    at least three analysts."""
    out = []
    for r in df.iter_rows(named=True):
        p = _pool(r["ticker"])
        if not p or p.ring != "A" or p.purity != "high" or p.status != "listed" or not is_us_listed(r["ticker"]):
            continue
        if (r["rev_fy0_n"] or 0) >= MIN_ANALYSTS and (r["rev_fy1_n"] or 0) >= MIN_ANALYSTS and currencies_agree(r):
            out.append(r["ticker"])
    return sorted(out)


def q4_q5_list(df: pl.DataFrame) -> list[str]:
    """Ring A, outside China, status listed, at least three target-price analysts."""
    out = []
    for r in df.iter_rows(named=True):
        p = _pool(r["ticker"])
        if (
            p
            and p.ring == "A"
            and p.status == "listed"
            and not is_china(r["ticker"])
            and (r["n_opinions"] or 0) >= MIN_ANALYSTS
        ):
            out.append(r["ticker"])
    return sorted(out)


def china_qualifiers(df: pl.DataFrame, *, min_brokers: int) -> list[str]:
    """A-share ring A names with purity high and at least ``min_brokers`` East Money brokers for the current year."""
    out = []
    for r in df.iter_rows(named=True):
        p = _pool(r["ticker"])
        if (
            p
            and p.ring == "A"
            and p.purity == "high"
            and is_china(r["ticker"])
            and (r["em_rev0_n"] or 0) >= min_brokers
        ):
            out.append(r["ticker"])
    return sorted(out)


def q8_list(df: pl.DataFrame, cibr: list[str], *, floor: float, cap: float) -> list[str]:
    """Held by CIBR, purity high, status listed, enterprise value between ``floor`` and ``cap`` US dollars."""
    held = set(cibr)
    out = []
    for r in df.iter_rows(named=True):
        p = _pool(r["ticker"])
        if not p or r["ticker"] not in held or p.purity != "high" or p.status != "listed":
            continue
        usd = enterprise_value_usd(r)
        if usd is not None and floor <= usd <= cap:
            out.append(r["ticker"])
    return sorted(out)
