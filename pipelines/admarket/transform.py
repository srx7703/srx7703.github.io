"""Per-filing revenue facts -> quarterly series; text records -> volume/price rows.

The derivation is the research's (``code/extract.py``), unchanged:

* a fact counts for a series when its concept is a revenue concept, it covers a duration, and its dimensions match
  the series' rule: no dimensions for a total; the rule's axis on one of its members for a line, with any other
  dimension allowed only on the segment or consolidation axis (Meta reports advertising inside its Family of Apps
  segment). For one (start, end, concept), the fact with the fewest dimensions wins, then the latest filing, so a
  restatement replaces the original figure;
* one concept per period: ``RevenueFromContractWithCustomerExcludingAssessedTax`` over ``Revenues``;
* a span of 80 to 100 days is a quarter; its calendar quarter is that of the end date minus 15 days, which puts
  52/53-week quarters ending a few days into the next month in the right place;
* a span of 350 to 380 days is a fiscal year; if its fourth quarter has no three-month fact, it is the year minus the
  nine-month year-to-date with the same start, or, when no nine-month figure is tagged, the year minus its three
  three-month quarters.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pipelines.admarket import config as cfg
from pipelines.admarket.parse import parse_text, quarter_of_report


def cal_q(end: date) -> str:
    e = end - timedelta(days=15)
    return f"{e.year}Q{(e.month - 1) // 3 + 1}"


def shift(q: str, n: int) -> str:
    y, k = int(q[:4]), int(q[5]) - 1 + n
    return f"{y + k // 4}Q{k % 4 + 1}"


def quarters_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 4 + int(b[5]) - int(a[5])


def _matches(rule: cfg.Rule, dims: dict[str, str]) -> bool:
    if rule.members is None:
        return not dims
    if dims.get(rule.axis) not in rule.members:
        return False
    return all(a == rule.axis or a in cfg.SEGMENT_AXES for a in dims)


def quarterly(filings: list[dict[str, Any]], rule: cfg.Rule, with_method: bool = False) -> dict[str, Any]:
    """quarter -> value (USD) for one rule, from filings ordered oldest filing first. With ``with_method``, the
    value is a (value, method) pair; method is '3M', 'FY-9M' or 'FY-3Q'."""
    vals: dict[tuple[str, str, str], tuple[float, str, int]] = {}
    for f in filings:
        if f["ticker"] != rule.ticker:
            continue
        for x in f["facts"]:
            if x["concept"] not in cfg.REV or not _matches(rule, x["dims"]):
                continue
            key = (x["start"], x["end"], x["concept"])
            nd = len(x["dims"])
            prev = vals.get(key)
            if prev is None or nd < prev[2] or (nd == prev[2] and f["filed"] >= prev[1]):
                vals[key] = (x["value"], f["filed"], nd)
    per: dict[tuple[date, date], float] = {}
    for (s, e, c), (v, _f, _nd) in vals.items():
        k = (date.fromisoformat(s), date.fromisoformat(e))
        if k not in per or c == cfg.REV[0]:
            per[k] = v
    q: dict[str, tuple[float, str]] = {}
    for (s, e), v in per.items():
        if 80 <= (e - s).days <= 100:
            q[cal_q(e)] = (v, "3M")
    for (s, e), v in per.items():
        if 350 <= (e - s).days <= 380:
            key = cal_q(e)
            if key in q:
                continue
            ytd = [vv for (s2, e2), vv in per.items() if s2 == s and 260 <= (e2 - s2).days <= 285]
            if ytd:
                q[key] = (v - ytd[0], "FY-9M")
            else:
                three = [vv for (s2, e2), vv in per.items() if s <= s2 and e2 < e and 80 <= (e2 - s2).days <= 100]
                if len(three) == 3:
                    q[key] = (v - sum(three), "FY-3Q")
    out = {k: q[k] for k in sorted(q) if k >= cfg.FIRST_QUARTER}
    return out if with_method else {k: v for k, (v, _m) in out.items()}


def all_series(filings: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {r.series: quarterly(filings, r) for r in cfg.RULES}


def composite(series: dict[str, dict[str, float]], name: str) -> dict[str, float]:
    """A named series of the panel: a raw series, or 'applovin' = the advertising segment until the apps business
    was sold, total revenue from 2025Q2."""
    if name != "applovin":
        return series[name]
    app = dict(series["applovin_ads"])
    app.update({k: v for k, v in series["applovin_total"].items() if k >= cfg.APPLOVIN_TOTAL_FROM})
    return dict(sorted(app.items()))


def windowed(series: dict[str, dict[str, float]], name: str, start: str | None) -> dict[str, float]:
    s = composite(series, name)
    return {k: v for k, v in s.items() if start is None or k >= start}


def first_reported(filings: list[dict[str, Any]], name: str, quarter: str) -> dict | None:
    """The first figure a company reported for a quarter (forecast scoring uses it, not later restatements): the
    derivation run on the filings up to and including the first filing date at which the quarter exists."""
    raw = "applovin_total" if name == "applovin" and quarter >= cfg.APPLOVIN_TOTAL_FROM else (
        "applovin_ads" if name == "applovin" else name)
    rule = next(r for r in cfg.RULES if r.series == raw)
    own = [f for f in filings if f["ticker"] == rule.ticker]
    q_end = {"1": "-03-31", "2": "-06-30", "3": "-09-30", "4": "-12-31"}[quarter[5]]
    for d in sorted({f["filed"] for f in own if f["filed"] > quarter[:4] + q_end}):
        q = quarterly([f for f in own if f["filed"] <= d], rule)
        if quarter in q:
            acc = next(f["accession"] for f in own if f["filed"] == d)
            return {"value": q[quarter], "filed": d, "accession": acc}
    return None


def volume_price_rows(filings: list[dict[str, Any]]) -> list[dict]:
    rows = []
    for f in filings:
        t = f.get("text_record")
        if not t:
            continue
        # re-parse the kept snippets rather than trusting the ingest-time "parsed" field, so a parser fix applies
        # to every filing without rewriting the raw records
        for series, r in parse_text(f["ticker"], t["snippets"]).items():
            rows.append({"series": series, "quarter": quarter_of_report(f["period"]), "volume": r.get("volume"),
                         "price": r.get("price"), "period": f["period"], "accession": f["accession"]})
    return sorted(rows, key=lambda r: (r["series"], r["quarter"]))
