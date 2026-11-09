"""Panels and the registered tests for the server-maker page.

Everything that a pre-registered rule depends on is computed here, from the snapshots written by ``tw``, ``sec``,
``cn`` and ``fx`` and the curated layer in ``data/reference/servers/``. Pure functions over plain dicts so the tests
can pin each rule on small hand-made inputs.

Conventions: a quarterly series is ``{calendar quarter "YYYYQn": value}``; fiscal quarters are placed in the calendar
quarter holding most of their days (``quarter_of_end``). Values are in the reporting currency unless a name says USD.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, timedelta

import polars as pl

from pipelines.servers import config as cfg
from pipelines.servers.sec import instant, quarterly

Series = dict[str, float]

REV = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
COST = ("CostOfRevenue", "CostOfGoodsAndServicesSold")
CAPEX_TAG = {"AMZN": ("PaymentsToAcquireProductiveAssets",)}
CAPEX_DEFAULT = ("PaymentsToAcquirePropertyPlantAndEquipment",)


# ---- calendar helpers -------------------------------------------------------------------------------------

def quarter_of_end(end: date) -> str:
    """Calendar quarter holding most of the days of a ~13-week quarter that ends on ``end``."""
    mid = end - timedelta(days=45)
    return f"{mid.year}Q{(mid.month - 1) // 3 + 1}"


def q_add(q: str, k: int) -> str:
    y, n = int(q[:4]), int(q[-1]) - 1 + k
    return f"{y + n // 4}Q{n % 4 + 1}"


def q_range(first: str, last: str) -> list[str]:
    out, q = [], first
    while q <= last:
        out.append(q)
        q = q_add(q, 1)
    return out


def month_quarter(month: str) -> str:
    y, m = month.split("-")
    return f"{y}Q{(int(m) - 1) // 3 + 1}"


def by_quarter(values: dict[str, float]) -> Series:
    """Fiscal-quarter values keyed by period end → calendar-quarter keys. Two fiscal quarters landing in one calendar
    quarter is a data error, not something to sum: it raises."""
    ends = sorted(values)
    days = [date.fromisoformat(e) for e in ends]

    def periodic(d: date) -> bool:
        """A real fiscal quarter end recurs a year earlier or later (52/53-week calendars: 358-371 days)."""
        return any(358 <= abs((d - o).days) <= 371 for o in days)

    out: Series = {}
    chosen: dict[str, str] = {}
    for end in ends:
        q = quarter_of_end(date.fromisoformat(end))
        if q in out:
            prev = chosen[q]
            keep_new = periodic(date.fromisoformat(end)) and not periodic(date.fromisoformat(prev))
            keep_old = periodic(date.fromisoformat(prev)) and not periodic(date.fromisoformat(end))
            if not (keep_new or keep_old):
                # Both recur (Penguin files a 2020-08-28 quarter and a 2020-08-31 transition span): keep the one
                # closest to 91 days after the previous kept quarter end.
                before = [date.fromisoformat(e) for qq, e in chosen.items() if qq < q]
                if not before:
                    continue
                anchor = max(before) + timedelta(days=91)
                d_new = abs((date.fromisoformat(end) - anchor).days)
                d_old = abs((date.fromisoformat(prev) - anchor).days)
                keep_old = d_old <= d_new
            if keep_old:
                continue
        out[q], chosen[q] = values[end], end
    return out


def ttm(s: Series, end_q: str) -> float | None:
    qs = [q_add(end_q, -k) for k in range(4)]
    return sum(s[q] for q in qs) if all(q in s and s[q] is not None for q in qs) else None


def yoy(a: float | None, b: float | None) -> float | None:
    return a / b - 1 if a is not None and b else None


# ---- statistics -------------------------------------------------------------------------------------------

def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def ranks(xs: list[float]) -> list[float]:
    """Average ranks for ties (1-based)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(ranks(xs), ranks(ys))


def median(xs: Iterable[float]) -> float | None:
    v = sorted(xs)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


# ---- panels -----------------------------------------------------------------------------------------------

def fx_monthly(fx: pl.DataFrame, currency: str) -> dict[str, float]:
    return {r["month"]: r["per_usd"] for r in fx.filter(pl.col("currency") == currency).iter_rows(named=True)}


def tw_monthly_series(monthly: pl.DataFrame, code: str) -> dict[str, float]:
    """Monthly revenue in NTD (not thousands)."""
    return {r["month"]: 1000 * r["revenue_k"] for r in monthly.filter(pl.col("code") == code).iter_rows(named=True)}


def to_usd_monthly(s: dict[str, float], twd: dict[str, float]) -> dict[str, float]:
    return {m: v / twd[m] for m, v in s.items() if m in twd}


def monthly_to_quarter(s: dict[str, float], months_per_q: int = 3) -> Series:
    """Calendar-quarter sums of the first ``months_per_q`` months (3 = whole quarter, 2 = Q7's signal window).
    A quarter missing one of those months is left out."""
    acc: dict[str, list[float]] = {}
    for m, v in s.items():
        if (int(m[5:7]) - 1) % 3 < months_per_q:
            acc.setdefault(month_quarter(m), []).append(v)
    return {q: sum(v) for q, v in sorted(acc.items()) if len(v) == months_per_q}


def company_panel(sec_facts: pl.DataFrame, tw_q: pl.DataFrame, cn_q: pl.DataFrame, lenovo: list[dict]) -> dict:
    """Per maker: {'currency', 'revenue', 'gross_profit', 'operating_income', 'inventory', 'receivables'} as calendar
    quarter series in reporting-currency units."""
    out: dict[str, dict] = {}
    for m in cfg.MAKERS:
        if m.market == "us":
            rev = quarterly(sec_facts, m.key, REV)
            gp = quarterly(sec_facts, m.key, ("GrossProfit",))
            cost = quarterly(sec_facts, m.key, COST)
            for e, r in rev.items():
                if e not in gp and e in cost:
                    gp[e] = r - cost[e]
            oi = quarterly(sec_facts, m.key, ("OperatingIncomeLoss",))
            inv = instant(sec_facts, m.key, ("InventoryNet",))
            ar = instant(sec_facts, m.key, ("AccountsReceivableNetCurrent",))
            out[m.key] = {"currency": "USD", "revenue": by_quarter(rev), "gross_profit": by_quarter(gp),
                          "operating_income": by_quarter(oi), "inventory": by_quarter_instant(inv),
                          "receivables": by_quarter_instant(ar)}
        elif m.market == "tw":
            sub = tw_q.filter(pl.col("code") == m.key)
            out[m.key] = {"currency": "TWD", **{k: {r["quarter"]: 1000 * r[f"{k}_k"] for r in sub.iter_rows(named=True)
                                                   if r[f"{k}_k"] is not None}
                                               for k in ("revenue", "gross_profit", "operating_income", "inventory",
                                                         "receivables")}}
        elif m.market == "cn":
            sub = cn_q.filter(pl.col("code") == m.key)
            out[m.key] = {"currency": "CNY", **{k: {r["quarter"]: r[k] for r in sub.iter_rows(named=True)
                                                   if r[k] is not None}
                                               for k in ("revenue", "gross_profit", "operating_income", "inventory",
                                                         "receivables")}}
        elif m.key == "0992.HK":
            ser: dict[str, dict] = {"revenue": {}, "gross_profit": {}, "operating_income": {}}
            for r in lenovo:
                k = {"group_revenue": "revenue", "group_gross_profit": "gross_profit",
                     "group_operating_profit": "operating_income"}.get(r["metric"])
                if k:
                    ser[k][quarter_of_end(date.fromisoformat(r["period_end"]))] = float(r["value"]) * r["_scale"]
            out[m.key] = {"currency": "USD", **ser, "inventory": {}, "receivables": {}}
    return out


def by_quarter_instant(values: dict[str, float]) -> Series:
    out: Series = {}
    for end, v in values.items():
        # a balance belongs to the fiscal quarter it closes, placed by the same rule as that quarter's flows
        out[quarter_of_end(date.fromisoformat(end))] = v
    return out


def server_series(sec_facts: pl.DataFrame, lenovo: list[dict]) -> dict[str, Series]:
    """The OEM server lines of Q1, in USD by calendar quarter."""
    isg = ("ConsolidationItemsAxis=OperatingSegmentsMember;"
           "StatementBusinessSegmentsAxis=InfrastructureSolutionsGroupMember")
    storage = ("ConsolidationItemsAxis=OperatingSegmentsMember;ProductOrServiceAxis=StorageMember;"
               "StatementBusinessSegmentsAxis=InfrastructureSolutionsGroupMember")
    i, s = quarterly(sec_facts, "DELL", REV, isg), quarterly(sec_facts, "DELL", REV, storage)
    dell = {e: i[e] - s[e] for e in i if e in s}
    hpe = quarterly(sec_facts, "HPE", REV, "StatementBusinessSegmentsAxis=ServerSegmentMember")
    older = quarterly(sec_facts, "HPE", REV,
                      "ConsolidationItemsAxis=OperatingSegmentsMember;StatementBusinessSegmentsAxis=ServerSegmentMember")
    for e, v in older.items():
        hpe.setdefault(e, v)
    smci = quarterly(sec_facts, "SMCI", REV)
    len_isg = {r["period_end"]: float(r["value"]) * r["_scale"] for r in lenovo if r["metric"] == "revenue"}
    return {"DELL": by_quarter(dell), "HPE": by_quarter(hpe), "SMCI": by_quarter(smci), "0992.HK": by_quarter(len_isg)}


def capex_series(sec_facts: pl.DataFrame) -> dict[str, Series]:
    return {t: by_quarter(quarterly(sec_facts, t, CAPEX_TAG.get(t, CAPEX_DEFAULT))) for t in cfg.Q7_CAPEX}


# ---- Q1 ---------------------------------------------------------------------------------------------------

def q1(oem: dict[str, Series], oem_last_end: dict[str, str | None], odm_usd_monthly: dict[str, dict[str, float]],
       odm_window_end: str = "2026-09") -> dict:
    """Registered Q1. ``oem_last_end`` gives each OEM's qualifying latest calendar quarter (None = not reported)."""
    def odm_g(codes: tuple[str, ...], end_m: str) -> float | None:
        def window(last: str) -> list[str]:
            y, mm = map(int, last.split("-"))
            out = []
            for k in range(12):
                n = y * 12 + mm - 1 - k
                out.append(f"{n // 12:04d}-{n % 12 + 1:02d}")
            return out
        cur = window(end_m)
        prev = [f"{int(m[:4]) - 1:04d}{m[4:]}" for m in cur]
        try:
            a = sum(odm_usd_monthly[c][m] for c in codes for m in cur)
            b = sum(odm_usd_monthly[c][m] for c in codes for m in prev)
        except KeyError:
            return None
        return a / b - 1

    def oem_g() -> tuple[float | None, list[str]]:
        missing = [k for k, q in oem_last_end.items() if q is None]
        if missing:
            return None, missing
        a = [ttm(oem[k], q) for k, q in oem_last_end.items()]
        b = [ttm(oem[k], q_add(q, -4)) for k, q in oem_last_end.items()]
        if any(x is None for x in a + b):
            return None, [k for k, q in oem_last_end.items()
                          if ttm(oem[k], q) is None or ttm(oem[k], q_add(q, -4)) is None]
        return sum(a) / sum(b) - 1, []

    g_oem, missing = oem_g()
    groups = {"primary": cfg.Q1_ODM, "robustness": ("2382", "2356", "6669")}
    res = {"g_oem": g_oem, "oem_missing": missing, "groups": {}}
    verdicts = []
    for name, codes in groups.items():
        g = odm_g(codes, odm_window_end)
        r = (1 + g) / (1 + g_oem) if g is not None and g_oem is not None else None
        v = None if r is None else ("holds" if r >= 1.08 else "refuted" if r <= 1.00 else "undecided")
        res["groups"][name] = {"codes": list(codes), "g_odm": g, "R": r, "verdict": v}
        verdicts.append(v)
    if None in verdicts:
        res["verdict"] = "pending"
    else:
        res["verdict"] = verdicts[0] if len(set(verdicts)) == 1 and verdicts[0] != "undecided" else "undecided"
    return res


# ---- Q2 ---------------------------------------------------------------------------------------------------

Q2_GRADED = ("DELL", "SMCI", "CLS", "2382", "6669", "3231", "2356", "601138.SS", "000977.SZ")
Q2_REPORTED = ("0992.HK", "HPE", "2376")
Q2_PLACEBO = ("CSCO", "4938", "000066.SZ")
Q2_BEFORE, Q2_AFTER = "2023Q2", "2026Q3"


def margins(p: dict, end_q: str, need_gp: bool = True) -> dict | None:
    rev, gp, oi = ttm(p["revenue"], end_q), ttm(p["gross_profit"], end_q), ttm(p["operating_income"], end_q)
    if rev is None or oi is None or rev <= 0 or (need_gp and gp is None):
        return None
    return {"revenue": rev, "gross_margin": gp / rev if gp is not None else None, "operating_margin": oi / rev,
            "operating_income": oi}


def q2_member(p: dict, before: str = Q2_BEFORE, after: str = Q2_AFTER) -> dict:
    b, a = margins(p, before), margins(p, after)
    if b is None or a is None:
        return {"before": b, "after": a, "pattern": None, "weak_pattern": None}
    pattern = (a["gross_margin"] <= b["gross_margin"] - 0.005) and (a["operating_margin"] >= b["operating_margin"])
    weak = (a["gross_margin"] < b["gross_margin"]) and (a["operating_income"] > b["operating_income"])
    return {"before": b, "after": a, "pattern": pattern, "weak_pattern": weak}


def q2(panel: dict, after: str = Q2_AFTER) -> dict:
    rows = {k: q2_member(panel[k], after=after) for k in (*Q2_GRADED, *Q2_REPORTED, *Q2_PLACEBO) if k in panel}
    graded = [k for k in Q2_GRADED if rows.get(k, {}).get("pattern") is not None]
    n = len(graded)
    shows = sum(1 for k in graded if rows[k]["pattern"])
    placebo = sum(1 for k in Q2_PLACEBO if rows.get(k, {}).get("pattern"))
    if n == 0:
        verdict = "pending"
    elif shows >= math.ceil(2 * n / 3):
        verdict = "holds"
    elif shows < n / 2:
        verdict = "refuted"
    else:
        verdict = "undecided"
    if placebo >= 2 and verdict != "pending":
        verdict = "undecided"
    no_wistron = [k for k in graded if k != "3231"]
    return {"rows": rows, "n": n, "shows": shows, "placebo_shows": placebo, "verdict": verdict,
            "missing": [k for k in Q2_GRADED if k not in graded],
            "without_wistron": {"n": len(no_wistron), "shows": sum(1 for k in no_wistron if rows[k]["pattern"])},
            "weak_shows": sum(1 for k in graded if rows[k]["weak_pattern"])}


# ---- Q5 ---------------------------------------------------------------------------------------------------

Q5_MEMBERS = ("DELL", "HPE", "SMCI", "0992.HK", "PENG", "2382.TW", "6669.TW", "3231.TW", "2356.TW", "2317.TW",
              "2376.TW", "2357.TW", "3706.TW", "601138.SS", "CLS", "JBL", "FLEX", "SANM", "000977.SZ", "000938.SZ",
              "603019.SS", "603296.SS")


def market_group(ticker: str) -> str:
    if ticker.endswith((".SS", ".SZ")):
        return "cn"
    if ticker.endswith(".TW"):
        return "tw"
    return "us_hk"


def q5(rows: list[dict]) -> dict:
    """rows: {ticker, fwd_pe, n_analysts, growth, operating_margin}. Members only; qualification applied here."""
    ok = [r for r in rows if r["ticker"] in Q5_MEMBERS and r.get("fwd_pe") is not None and r["fwd_pe"] > 0
          and (r.get("n_analysts") or 0) >= 3 and r.get("growth") is not None and r.get("operating_margin") is not None]
    meds = {g: median(r["fwd_pe"] for r in ok if market_group(r["ticker"]) == g) for g in ("cn", "tw", "us_hk")}
    for r in ok:
        r["adj_pe"] = r["fwd_pe"] / meds[market_group(r["ticker"])]
    n = len(ok)
    rg = spearman([r["adj_pe"] for r in ok], [r["growth"] for r in ok]) if n >= 3 else None
    rm = spearman([r["adj_pe"] for r in ok], [r["operating_margin"] for r in ok]) if n >= 3 else None
    if n < 15 or rg is None or rm is None:
        verdict = "undecided"
    elif rg >= 0.30 and rg - rm >= 0.20:
        verdict = "holds"
    elif rm > 0 and rm >= rg:
        verdict = "refuted"
    else:
        verdict = "undecided"
    return {"n": n, "rho_growth": rg, "rho_margin": rm, "verdict": verdict, "medians": meds, "rows": ok,
            "excluded": sorted(set(Q5_MEMBERS) - {r["ticker"] for r in ok})}


# ---- Q7 ---------------------------------------------------------------------------------------------------

def q7_series(signal_q: Series, capex_total_q: Series, first: str = cfg.Q7_FIRST, last: str = cfg.Q7_LAST,
              lead: int = 0) -> list[dict]:
    out = []
    for q in q_range(first, last):
        tq = q_add(q, lead)
        s = yoy(signal_q.get(q), signal_q.get(q_add(q, -4)))
        t = yoy(capex_total_q.get(tq), capex_total_q.get(q_add(tq, -4)))
        if s is not None and t is not None:
            out.append({"quarter": q, "target_quarter": tq, "signal": s, "target": t})
    return out


def q7_measures(pairs: list[dict]) -> dict:
    corr = pearson([p["signal"] for p in pairs], [p["target"] for p in pairs])
    same, total = 0, 0
    for a, b in zip(pairs, pairs[1:], strict=False):
        if "quarter" in a and q_add(a["quarter"], 1) != b["quarter"]:
            continue   # a hole in either series: never compare non-adjacent quarters
        ds, dt = b["signal"] - a["signal"], b["target"] - a["target"]
        total += 1
        if ds != 0 and dt != 0 and (ds > 0) == (dt > 0):
            same += 1
    share = same / total if total else None
    if corr is None or share is None:
        v = "pending"
    elif corr >= 0.5 and share >= 0.65:
        v = "holds"
    elif corr < 0.3 or share < 0.55:
        v = "refuted"
    else:
        v = "undecided"
    return {"n": len(pairs), "corr": corr, "same_direction": share, "n_changes": total, "verdict": v}


def q7(signal_primary: Series, signal_wiwynn: Series, capex_total: Series) -> dict:
    prim = q7_measures(q7_series(signal_primary, capex_total))
    wiw = q7_measures(q7_series(signal_wiwynn, capex_total))
    lead = q7_measures(q7_series(signal_primary, capex_total, last=q_add(cfg.Q7_LAST, -1), lead=1))
    agree = prim["verdict"] == wiw["verdict"] and prim["verdict"] in ("holds", "refuted")
    v = prim["verdict"] if agree else "undecided"
    return {"primary": prim, "wiwynn": wiw, "lead_exploratory": lead, "verdict": v,
            "pairs": q7_series(signal_primary, capex_total), "pairs_wiwynn": q7_series(signal_wiwynn, capex_total)}
