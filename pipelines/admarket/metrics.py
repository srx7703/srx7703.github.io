"""Shares, growth, the registered tests (Q2, Q3), the volume x price check (Q4) and forecast scoring.

Ported from the research (``panel.py``, ``tests_q2_q3.py``, ``q4_decompose.py``, ``forecast.py``). Inputs are the
quarterly series from ``transform.all_series`` (quarter -> USD). Growth rates are fractions; gaps in Q4 are percentage
points rounded to one decimal, as in the research, because the method check compares the rounded gap with 3 points.
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Any

from pipelines.admarket import config as cfg
from pipelines.admarket.transform import first_reported, quarters_between, shift, windowed

Series = dict[str, dict[str, float]]


def prev_year(q: str) -> str:
    return shift(q, -4)


def yoy(s: dict[str, float], q: str) -> float | None:
    a, b = s.get(q), s.get(prev_year(q))
    return a / b - 1 if a and b else None


# ---- the video's nine-company subset ----------------------------------------------------------------------

def video_members(series: Series) -> dict[str, dict[str, float]]:
    return {name: windowed(series, s, start) for name, (s, start) in cfg.VIDEO_SUBSET.items()}


def _expected(name: str, q: str) -> bool:
    start = cfg.VIDEO_SUBSET[name][1]
    return start is None or q >= start


def last_complete_quarter(members: dict[str, dict[str, float]]) -> str:
    """The latest quarter in which every member expected in it has reported (a quarter with only some companies
    reported would understate the total and overstate the early reporters' shares)."""
    last = max(q for s in members.values() for q in s)
    q = last
    while q >= cfg.PANEL_START:
        if all(q in s for n, s in members.items() if _expected(n, q)):
            return q
        q = shift(q, -1)
    raise ValueError("no complete quarter in the video subset")


def video_panel(series: Series) -> dict[str, Any]:
    m = video_members(series)
    last = last_complete_quarter(m)
    qs = [shift(cfg.PANEL_START, i) for i in range(quarters_between(cfg.PANEL_START, last) + 1)]
    rows = []
    for q in qs:
        tot = sum(s[q] for s in m.values() if q in s)
        rows.append({"quarter": q, "total": tot, "values": {k: s.get(q) for k, s in m.items()},
                     "share": {k: (s[q] / tot if q in s else None) for k, s in m.items()}})
    t = {r["quarter"]: r["total"] for r in rows}
    share = {r["quarter"]: r["share"] for r in rows}
    first = cfg.PANEL_START
    v_first, v_last = cfg.VIDEO["first_quarter"], cfg.VIDEO["last_quarter"]
    span = quarters_between(first, last) / 4
    ttm_now = sum(t[shift(last, -i)] for i in range(4))
    ttm_before = sum(t[shift(last, -4 - i)] for i in range(4))
    meta_2022 = {q: share[q]["Meta"] for q in qs if q.startswith("2022")}
    meta_low_q = min(meta_2022, key=meta_2022.get)
    app_over_snap = next((q for q in qs if (share[q]["AppLovin"] or 0) > (share[q]["Snap"] or 0)), None)
    pending = shift(last, 1)
    reported_next = [n for n, s in m.items() if pending in s]
    return {
        "rows": rows,
        "first_quarter": first, "last_quarter": last, "years": span,
        "total_first_bn": t[first] / 1e9, "total_last_bn": t[last] / 1e9,
        "total_video_end_bn": t[v_last] / 1e9,
        "cagr": (t[last] / t[first]) ** (1 / span) - 1,
        "ttm_yoy": ttm_now / ttm_before - 1,
        "video_window": {
            "first": v_first, "last": v_last, "years": quarters_between(v_first, v_last) / 4,
            "cagr_correct": (t[v_last] / t[v_first]) ** (1 / (quarters_between(v_first, v_last) / 4)) - 1,
            "cagr_four_years": (t[v_last] / t[v_first]) ** (1 / 4) - 1,
            "cagr_claimed": cfg.VIDEO["cagr_claimed"],
        },
        "share_first": share[first], "share_last": share[last], "share_video_end": share[v_last],
        "meta_2022_low": {"quarter": meta_low_q, "share": meta_2022[meta_low_q]},
        "applovin_passes_snap": app_over_snap,
        "yoy_last": {k: yoy(s, last) for k, s in m.items()},
        "yoy_video_end": {k: yoy(s, v_last) for k, s in m.items()},
        "next_quarter": {"quarter": pending, "reported": reported_next, "n_reported": len(reported_next),
                         "n_members": len(m)},
        "four_quarter_mean_share": {
            "Google": {"before": mean(share[q]["Google"] for q in cfg.Q1_BEFORE if q in share)},
        },
    }


def q1_status(series: Series) -> dict:
    """Q1 (Google's share, four-quarter means): collecting until 2027Q2 is complete for all nine."""
    m = video_members(series)
    last = last_complete_quarter(m)
    done = [q for q in cfg.Q1_AFTER if q <= last]
    out = {"status": "collecting", "quarters_in": len(done), "quarters_needed": len(cfg.Q1_AFTER)}
    if len(done) == len(cfg.Q1_AFTER):
        def share(q):
            tot = sum(s[q] for s in m.values() if q in s)
            return m["Google"][q] / tot
        before = mean(share(q) for q in cfg.Q1_BEFORE)
        after = mean(share(q) for q in cfg.Q1_AFTER)
        drop = before - after
        verdict = "holds" if drop >= cfg.Q1_THRESHOLD else ("refuted" if after >= before else "undecided")
        out.update(status="resolved", before=before, after=after, verdict=verdict)
    return out


def q5_status(series: Series) -> dict:
    """Q5 (Trade Desk growth at least half of AppLovin's in two of the four quarters refutes): collecting."""
    ttd, app = series["ttd_total"], windowed(series, "applovin", cfg.APPLOVIN_FROM)
    rows = []
    for q in cfg.Q5_QUARTERS:
        a, b = yoy(ttd, q), yoy(app, q)
        if a is not None and b is not None:
            rows.append({"quarter": q, "ttd": a, "applovin": b, "ttd_at_least_half": a >= 0.5 * b})
    out = {"status": "collecting", "quarters_in": len(rows), "quarters_needed": len(cfg.Q5_QUARTERS), "rows": rows}
    if len(rows) == len(cfg.Q5_QUARTERS):
        n = sum(r["ttd_at_least_half"] for r in rows)
        out.update(status="resolved", verdict="refuted" if n >= 2 else "holds", n_ttd_half=n)
    return out


# ---- Q2: ATT ---------------------------------------------------------------------------------------------

def g2(s: dict[str, float], q: str) -> float:
    return (s[q] / s[shift(q, -8)]) ** 0.5 - 1


def q2(series: Series) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for name, grp in (("affected", cfg.Q2_AFFECTED), ("control", cfg.Q2_CONTROL)):
        before = sum(g2(series[s], q) for s in grp for q in cfg.Q2_BEFORE) / (len(cfg.Q2_BEFORE) * len(grp))
        after = sum(g2(series[s], q) for s in grp for q in cfg.Q2_AFTER) / (len(cfg.Q2_AFTER) * len(grp))
        res[name] = {
            "before": before, "after": after, "drop": before - after,
            "members": {s: {"before": mean(g2(series[s], q) for q in cfg.Q2_BEFORE),
                            "after": mean(g2(series[s], q) for q in cfg.Q2_AFTER)} for s in grp},
        }
    gap = res["affected"]["drop"] - res["control"]["drop"]
    res["gap"] = gap
    res["verdict"] = "holds" if gap >= cfg.Q2_THRESHOLD else "refuted"
    res["threshold"] = cfg.Q2_THRESHOLD
    path = []
    for s in cfg.Q2_AFFECTED + cfg.Q2_CONTROL:
        grp = "affected" if s in cfg.Q2_AFFECTED else "control"
        for q in sorted(series[s]):
            if q >= cfg.PANEL_START and shift(q, -8) in series[s]:
                path.append({"series": s, "label": cfg.SERIES_LABEL[s], "group": grp, "quarter": q,
                             "g2": g2(series[s], q)})
    res["path"] = path
    groups = []
    for grp, members in (("affected", cfg.Q2_AFFECTED), ("control", cfg.Q2_CONTROL)):
        qs = sorted({p["quarter"] for p in path if p["series"] in members})
        for q in qs:
            vals = [p["g2"] for p in path if p["series"] in members and p["quarter"] == q]
            if len(vals) == len(members):
                groups.append({"group": grp, "quarter": q, "g2": mean(vals)})
    res["group_path"] = groups
    return res


# ---- Q3: open web vs walled gardens ----------------------------------------------------------------------

def ttm(series: Series, grp: tuple[str, ...], end: str) -> float:
    qs = [shift(end, -i) for i in range(4)]
    return sum(series[s][q] for s in grp for q in qs if q in series[s])


def cagr3(series: Series, grp: tuple[str, ...]) -> float:
    return (ttm(series, grp, cfg.Q3_TO_END) / ttm(series, grp, cfg.Q3_FROM_END)) ** (1 / cfg.Q3_YEARS) - 1


def q3(series: Series) -> dict[str, Any]:
    walled_ex = tuple(s for s in cfg.Q3_WALLED if s != "reddit_ads")
    open_ex = tuple(s for s in cfg.Q3_OPEN if s != "google_network")
    res: dict[str, Any] = {
        "open": cagr3(series, cfg.Q3_OPEN), "walled": cagr3(series, cfg.Q3_WALLED),
        "walled_ex_reddit": cagr3(series, walled_ex),
        "members": {s: cagr3(series, (s,)) for s in cfg.Q3_OPEN + walled_ex},
    }
    d = res["walled"] - res["open"]
    res["gap"] = d
    res["verdict"] = "holds" if d >= cfg.Q3_THRESHOLD else ("refuted" if d <= 0 else "undecided")
    res["threshold"] = cfg.Q3_THRESHOLD
    # descriptive, added after the verdict (not registered)
    open_ttm = ttm(series, cfg.Q3_OPEN, cfg.Q3_TO_END)
    res["descriptive"] = {
        "open_ex_network": cagr3(series, open_ex),
        "network_ttm_bn": ttm(series, ("google_network",), cfg.Q3_TO_END) / 1e9,
        "others_ttm_bn": ttm(series, open_ex, cfg.Q3_TO_END) / 1e9,
        "network_share_of_open": ttm(series, ("google_network",), cfg.Q3_TO_END) / open_ttm,
        "walled_minus_open_ex_network": res["walled"] - cagr3(series, open_ex),
    }
    # chart path: trailing-four-quarter revenue indexed to the registered base window = 100
    chart = {"Open web": cfg.Q3_OPEN, "Independent ad tech": open_ex,
             "Walled gardens (without Reddit)": walled_ex}
    starts = {s: min(series[s]) for g in chart.values() for s in g}
    last = min(max(series[s]) for g in chart.values() for s in g)
    path = []
    end = "2020Q4"
    while end <= last:
        if all(shift(end, -3) >= starts[s] for g in chart.values() for s in g):
            for label, grp in chart.items():
                v = ttm(series, grp, end)
                path.append({"group": label, "quarter": end, "ttm_bn": v / 1e9,
                             "index": 100 * v / ttm(series, grp, cfg.Q3_FROM_END)})
        end = shift(end, 1)
    res["path"] = path
    return res


# ---- Q4: volume x price ----------------------------------------------------------------------------------

def q4(series: Series, vp_rows: list[dict]) -> dict[str, Any]:
    rows = []
    for r in vp_rows:
        if r.get("volume") is None or r.get("price") is None:
            continue
        s = series[cfg.Q4_SERIES[r["series"]]["revenue"]]
        q = r["quarter"]
        if q not in s or prev_year(q) not in s:
            continue
        reported = s[q] / s[prev_year(q)] - 1
        implied = (1 + r["volume"] / 100) * (1 + r["price"] / 100) - 1
        rows.append({"series": r["series"], "label": cfg.Q4_SERIES[r["series"]]["label"], "quarter": q,
                     "volume": r["volume"], "price": r["price"],
                     "reported": round(reported * 100, 1), "implied": round(implied * 100, 1),
                     "gap_pp": round((implied - reported) * 100, 1)})
    verdict, summary = {}, {}
    for s in cfg.Q4_SERIES:
        mine = [x for x in rows if x["series"] == s]
        if not mine:
            continue
        g = [abs(x["gap_pp"]) for x in mine]
        worst = max(mine, key=lambda x: abs(x["gap_pp"]))
        verdict[s] = {"n": len(g), "max_abs_gap_pp": max(g), "mean_abs_gap_pp": round(sum(g) / len(g), 2),
                      "usable": max(g) <= cfg.Q4_MAX_GAP_PP, "worst_quarter": worst["quarter"],
                      "over_by_pp": round(max(g) - cfg.Q4_MAX_GAP_PP, 1),
                      "n_over": sum(1 for x in g if x > cfg.Q4_MAX_GAP_PP)}
        by_year: dict[str, dict] = {}
        for x in mine:
            y = by_year.setdefault(x["quarter"][:4], {"vol": [], "price": []})
            y["vol"].append(x["volume"])
            y["price"].append(x["price"])
        latest = mine[-1]
        summary[s] = {
            "latest": {k: latest[k] for k in ("quarter", "volume", "price", "reported")},
            "years": {y: {"vol_min": min(v["vol"]), "vol_max": max(v["vol"]), "price_min": min(v["price"]),
                          "price_max": max(v["price"]), "n": len(v["vol"])} for y, v in sorted(by_year.items())},
            "price_min": {k: min(mine, key=lambda x: x["price"])[k] for k in ("quarter", "price")},
            "volume_min": {k: min(mine, key=lambda x: x["volume"])[k] for k in ("quarter", "volume")},
            "volume_max": {k: max(mine, key=lambda x: x["volume"])[k] for k in ("quarter", "volume")},
            "price_positive_since": _positive_since([(x["quarter"], x["price"]) for x in mine]),
            "volume_negative_since": _negative_since([(x["quarter"], x["volume"]) for x in mine]),
            "first_quarter": mine[0]["quarter"],
        }
    return {"rows": rows, "verdict": verdict, "summary": summary, "max_gap_pp": cfg.Q4_MAX_GAP_PP}


def _positive_since(pairs: list[tuple[str, int]]) -> str | None:
    since = None
    for q, v in pairs:
        since = (since or q) if v > 0 else None
    return since


def _negative_since(pairs: list[tuple[str, int]]) -> str | None:
    since = None
    for q, v in pairs:
        since = (since or q) if v < 0 else None
    return since


# ---- forecasts: rule A recomputation and scoring ---------------------------------------------------------

def forecast_series(series: Series, upto: str | None = None) -> dict[str, dict[str, float]]:
    out = {}
    for name, (s, start) in cfg.FORECAST_SERIES.items():
        w = windowed(series, s, start)
        out[name] = {k: v for k, v in w.items() if upto is None or k <= upto}
    return out


def _predict(s: dict[str, float], last: str, h: int) -> float | None:
    base, prev = s.get(shift(last, h - 4)), s.get(shift(last, -4))
    if not base or not prev or last not in s:
        return None
    return base * s[last] / prev


def _quantile(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    i = (len(xs) - 1) * p
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def rule_a(series: Series) -> dict[str, Any]:
    """Recompute the registered rule A from the panel as it stood at registration (quarters up to 2026Q2)."""
    fs = forecast_series(series, cfg.FORECAST_LAST)
    errs: dict[int, list[float]] = {1: [], 2: []}
    for s in fs.values():
        for last in sorted(s):
            if last < cfg.BACKTEST_FROM:
                continue
            for h in (1, 2):
                t, p = shift(last, h), _predict(s, last, h)
                if p and t in s:
                    errs[h].append(math.log(s[t] / p))
    band = {h: (_quantile(e, 0.1), _quantile(e, 0.9)) for h, e in errs.items()}
    fc = {}
    for name, s in fs.items():
        for h in (1, 2):
            p = _predict(s, cfg.FORECAST_LAST, h)
            if p:
                fc.setdefault(name, {})[shift(cfg.FORECAST_LAST, h)] = {
                    "point": p, "low": p * math.exp(band[h][0]), "high": p * math.exp(band[h][1])}
    return {"band": band, "n": {h: len(e) for h, e in errs.items()}, "forecasts": fc}


def recompute_matches(recomputed: dict, registered: dict) -> dict:
    """Registered integers ($m) against the recomputation rounded to $m."""
    cells = mismatches = 0
    bad = []
    for name, by_q in registered["forecasts"].items():
        for q, reg in by_q.items():
            rc = recomputed["forecasts"].get(name, {}).get(q)
            for k in ("point", "low", "high"):
                cells += 1
                if rc is None or round(rc[k] / 1e6) != reg[k]:
                    mismatches += 1
                    bad.append(f"{name} {q} {k}")
    band_ok = all(
        round(math.exp(recomputed["band"][h][i]) - 1, 3) == round(registered["band"][h][i], 3)
        and recomputed["n"][h] == registered["band"][h][2]
        for h in (1, 2) for i in (0, 1))
    return {"cells": cells, "mismatches": mismatches, "bad": bad[:10], "band_ok": band_ok}


def score_a(filings: list[dict], registered: dict) -> dict[str, Any]:
    rows = []
    for name, by_q in registered["forecasts"].items():
        s = cfg.FORECAST_SERIES[name][0]
        for q, reg in by_q.items():
            fr = first_reported(filings, s, q)
            row = {"series": name, "quarter": q, "point_m": reg["point"], "low_m": reg["low"], "high_m": reg["high"],
                   "actual_m": None, "status": "pending", "filed": None, "error": None}
            if fr:
                a = fr["value"] / 1e6
                row.update(actual_m=round(a, 1), filed=fr["filed"], error=a / reg["point"] - 1,
                           status="hit" if reg["low"] <= a <= reg["high"] else "miss")
            rows.append(row)
    scored = [r for r in rows if r["status"] != "pending"]
    hits = sum(r["status"] == "hit" for r in scored)
    out = {"rows": rows, "n": len(rows), "n_scored": len(scored), "hits": hits,
           "hit_rate": hits / len(scored) if scored else None, "status": "pending", "verdict": None}
    if scored and len(scored) == len(rows):
        rate = hits / len(rows)
        out.update(status="resolved", verdict="miscalibrated" if rate <= cfg.A_MISCALIBRATED_AT_OR_BELOW
                   else ("too wide" if rate >= cfg.A_TOO_WIDE_AT_OR_ABOVE else "calibrated"))
    elif scored:
        out["status"] = "partial"
    return out


def score_b(filings: list[dict], series: Series, registered: dict) -> list[dict]:
    """Rule B calls for 2026Q3. The 2026Q3 side is each company's first reported figure; the 2025Q3 side is the
    current panel. A call stays pending until every company it needs has reported."""
    q, base = cfg.FORECAST_TARGETS[0], prev_year(cfg.FORECAST_TARGETS[0])
    cache: dict[str, float | None] = {}

    def now(s: str) -> float | None:
        if s not in cache:
            fr = first_reported(filings, s, q)
            cache[s] = fr["value"] if fr else None
        return cache[s]

    def before(s: str) -> float:
        return windowed(series, s, None)[base]

    def growth(grp: tuple[str, ...]) -> float | None:
        vals = [now(s) for s in grp]
        return None if None in vals else sum(vals) / sum(before(s) for s in grp) - 1

    video = {n: s for n, (s, _st) in cfg.VIDEO_SUBSET.items()}

    def share(n: str, at_now: bool) -> float | None:
        vals = {k: (now(s) if at_now else before(s)) for k, s in video.items()}
        return None if None in vals.values() else vals[n] / sum(vals.values())

    def cmp(a, b, fn):
        return None if a is None or b is None else fn(a, b)

    g_ttd = cmp(now("ttd_total"), before("ttd_total"), lambda a, b: a / b - 1)
    g_rd = cmp(now("reddit_ads"), before("reddit_ads"), lambda a, b: a / b - 1)
    g_open, g_wall = growth(cfg.Q3_OPEN), growth(cfg.Q3_WALLED)
    results = {
        "B1": cmp(share("Google", True), share("Google", False), lambda a, b: a < b),
        "B2": cmp(share("Meta", True), share("Meta", False), lambda a, b: a > b),
        "B3": None if g_ttd is None else g_ttd < 0.10,
        "B4": cmp(now("applovin"), now("snap_total"), lambda a, b: a > b),
        "B5": None if g_rd is None else g_rd > 0.40,
        "B6": cmp(g_open, g_wall, lambda a, b: a < b),
        "B7": cmp(now("google_network"), before("google_network"), lambda a, b: a < b),
    }
    out = []
    for c in registered["calls"]:
        r = results.get(c["id"])
        out.append({"id": c["id"], "call": c["text"], "status": "pending" if r is None else ("true" if r else "false")})
    return out
