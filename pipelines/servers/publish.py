"""Assemble the marts and the facts file for the AI server makers page.

Usage:
    uv run python -m pipelines.servers.publish

Reads the snapshots of ``tw``, ``sec``, ``cn`` and ``fx``, the valuation marts (``servers`` track, plus Cisco and
Jabil from the optical track), the frozen Q5 snapshot and the curated layer in ``data/reference/servers/``.

Writes:
    data/marts/servers/quarterly.json   per maker and calendar quarter: revenue, margins, working capital, in USD
    data/marts/servers/monthly.json     Taiwan monthly revenue and its year-over-year change
    data/marts/servers/q7.json          the Q7 signal and target series
    data/facts/servers.json             everything the page prints

Rules: every number the page prints is in the facts file; the registered tests run exactly as written in
``docs/prereg/ai-servers/`` and are reported as pending until their grading date when their data is incomplete; a
curated row without a source URL never reaches the facts; ``curator_notes`` never leaves ``data/reference``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date

import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, utc_now, write_json
from pipelines.servers import config as cfg
from pipelines.servers import metrics as M
from pipelines.servers.forecast import shift

log = logging.getLogger("servers.publish")

SOURCES = [
    {"name": "MOPS 公開資訊觀測站 (monthly revenue, quarterly statements)", "url": "https://mopsov.twse.com.tw/"},
    {"name": "SEC EDGAR (10-Q / 10-K XBRL)", "url": "https://www.sec.gov/edgar/search/"},
    {"name": "East Money F10 statements", "url": "https://emweb.securities.eastmoney.com/"},
    {"name": "FRED monthly exchange rates (EXTAUS, EXCHUS)", "url": "https://fred.stlouisfed.org/series/EXTAUS"},
    {"name": "Yahoo Finance / East Money consensus (valuation pipeline)", "url": "https://finance.yahoo.com/"},
    {"name": "Company filings and press releases, curated (data/reference/servers)",
     "url": "https://github.com/srx7703/srx7703.github.io/tree/main/data/reference/servers"},
    {"name": "IDC, TrendForce, Dell'Oro free press releases, curated",
     "url": "https://github.com/srx7703/srx7703.github.io/blob/main/data/reference/share_servers.json"},
]
GRADING_DATE = date(2026, 12, 31)
REFERENCE_KINDS = ("lenovo", "customers", "capabilities", "private", "disclosures")
PUBLIC_DROP = ("curator_notes",)


# ---- inputs -----------------------------------------------------------------------------------------------

def _read(name: str) -> pl.DataFrame:
    p = cfg.SNAP / name
    return pl.read_parquet(p) if p.exists() else pl.DataFrame()


def reference(kind: str) -> dict:
    p = cfg.REFERENCE_DIR / f"{kind}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def public_rows(rows: list[dict]) -> list[dict]:
    """Rows fit for the facts file: sourced, not low-confidence, with the working notes stripped."""
    out = []
    for r in rows:
        if not r.get("source_url") or r.get("confidence") == "low":
            continue
        out.append({k: v for k, v in r.items() if k not in PUBLIC_DROP})
    return out


def lenovo_rows() -> list[dict]:
    rows = reference("lenovo").get("rows", [])
    scale = {"millions": 1e6, "thousands": 1e3, "billions": 1e9, "units": 1.0}
    return [{**r, "_scale": scale[r["unit"]]} for r in rows]


# ---- registered forecasts ---------------------------------------------------------------------------------

_ROW = re.compile(r"^\| (\d{4}) [^|]+\| ([^|]+)\| ([\d,]+) \| ([+-][\d.]+)% \| ([\d,]+) \| "
                  r"([\d,]+) – ([\d,]+) \|", re.M)


def registered_forecasts() -> list[dict]:
    text = cfg.PREREG_FORECASTS.read_text(encoding="utf-8")
    n = lambda s: float(s.replace(",", ""))  # noqa: E731
    return [{"code": m[0], "months_used": [x.strip() for x in m[1].split(",")], "base_k": n(m[2]),
             "point_k": n(m[4]), "lo80_k": n(m[5]), "hi80_k": n(m[6])} for m in _ROW.findall(text)]


def first_print(month: str) -> pl.DataFrame:
    """F1 is scored on revenue as first shown on MOPS: the earliest archived fetch of the target month."""
    from pipelines.servers import tw
    d = cfg.RAW_TW_MONTHLY / month
    files = sorted(d.glob("*.html.gz")) if d.exists() else []
    if not files:
        return pl.DataFrame(schema={"code": pl.Utf8, "month": pl.Utf8, "revenue_k": pl.Float64})
    import gzip
    page = gzip.decompress(files[0].read_bytes()).decode("cp950", errors="replace")
    rows = tw.parse_monthly(page, month, cfg.TW_CODES)
    prev = _read("tw_monthly.parquet").filter(pl.col("month") < month).select("code", "month", "revenue_k")
    return pl.concat([prev, pl.DataFrame(rows).select("code", "month", "revenue_k")]) if rows else prev


def score_forecasts(reg: list[dict], monthly: pl.DataFrame, target: str = "2026-11") -> dict:
    rows, inside, errs, naive_errs = [], 0, [], []
    for f in reg:
        s = {r["month"]: r["revenue_k"] for r in monthly.filter(pl.col("code") == f["code"]).iter_rows(named=True)}
        actual = s.get(target)
        naive = s.get(f["months_used"][-1])
        row = {**f, "name": cfg.BY_KEY[f["code"]].name, "actual_k": actual, "naive_k": naive}
        if actual is not None:
            row["inside"] = f["lo80_k"] <= actual <= f["hi80_k"]
            row["error"] = f["point_k"] / actual - 1
            row["naive_error"] = (naive / actual - 1) if naive else None
            inside += row["inside"]
            errs.append(abs(row["error"]))
            if row["naive_error"] is not None:
                naive_errs.append(abs(row["naive_error"]))
        rows.append(row)
    scored = sum(1 for r in rows if r["actual_k"] is not None)
    return {"target": target, "rows": rows, "n": len(rows), "scored": scored, "inside": inside,
            "median_abs_error": M.median(errs), "naive_median_abs_error": M.median(naive_errs),
            "status": "scored" if scored == len(rows) else "pending" if scored == 0 else "partial"}


# ---- panels -----------------------------------------------------------------------------------------------

def quarter_fx(fx: pl.DataFrame, currency: str) -> dict[str, float]:
    if currency == "USD":
        return {}
    acc: dict[str, list[float]] = {}
    for m, v in M.fx_monthly(fx, currency).items():
        acc.setdefault(M.month_quarter(m), []).append(v)
    return {q: sum(v) / len(v) for q, v in acc.items() if len(v) == 3}


def quarterly_mart(panel: dict, fx: pl.DataFrame) -> list[dict]:
    rows = []
    for key, p in panel.items():
        rate = quarter_fx(fx, p["currency"])
        for q in sorted(p["revenue"]):
            if q < "2019Q1":
                continue
            k = 1.0 if p["currency"] == "USD" else (1 / rate[q] if q in rate else None)
            rev, gp, oi = p["revenue"].get(q), p["gross_profit"].get(q), p["operating_income"].get(q)
            rows.append({
                "key": key, "name": cfg.BY_KEY[key].name, "group": cfg.BY_KEY[key].group, "quarter": q,
                "currency": p["currency"], "revenue": rev,
                "revenue_usd": rev * k if rev is not None and k else None,
                "gross_margin": gp / rev if gp is not None and rev else None,
                "operating_margin": oi / rev if oi is not None and rev else None,
            })
    return rows


def working_capital(p: dict, end_q: str) -> dict | None:
    """Inventory and receivable days at a quarter end, on trailing-four-quarter cost of sales and revenue."""
    rev, gp = M.ttm(p["revenue"], end_q), M.ttm(p["gross_profit"], end_q)
    inv, ar = p["inventory"].get(end_q), p["receivables"].get(end_q)
    if rev is None or gp is None:
        return None
    cogs = rev - gp
    return {"inventory_days": inv / cogs * 365 if inv is not None and cogs > 0 else None,
            "receivable_days": ar / rev * 365 if ar is not None and rev > 0 else None}


def latest_common(p: dict) -> str | None:
    qs = sorted(q for q in p["revenue"] if M.ttm(p["revenue"], q) is not None
                and M.ttm(p["operating_income"], q) is not None)
    return qs[-1] if qs else None


def scorecard(panel: dict) -> list[dict]:
    rows = []
    for m in cfg.MAKERS:
        p = panel.get(m.key)
        if not p:
            continue
        q = latest_common(p)
        if q is None:
            continue
        cur, prev = M.margins(p, q, need_gp=False), M.margins(p, M.q_add(q, -4), need_gp=False)
        base = M.margins(p, "2023Q2", need_gp=False)
        wc, wc0 = working_capital(p, q), working_capital(p, "2023Q2")
        rows.append({
            "key": m.key, "name": m.name, "name_cn": m.name_cn, "group": m.group, "comparator": m.comparator,
            "ttm_end": q, "currency": p["currency"], "revenue": cur["revenue"],
            "growth": M.yoy(cur["revenue"], prev["revenue"]) if prev else None,
            "gross_margin": cur["gross_margin"], "operating_margin": cur["operating_margin"],
            "gross_margin_2023": base["gross_margin"] if base else None,
            "operating_margin_2023": base["operating_margin"] if base else None,
            "inventory_days": wc["inventory_days"] if wc else None,
            "inventory_days_2023": wc0["inventory_days"] if wc0 else None,
            "receivable_days": wc["receivable_days"] if wc else None,
        })
    return rows


def group_growth(oem: dict[str, M.Series], usd: dict[str, dict[str, float]], first: str = "2021Q1") -> list[dict]:
    """Trailing-four-quarter US-dollar revenue growth by quarter, OEM server lines vs the Q1 ODM groups. Same series
    definitions as the registered Q1, run over every quarter where all members have data (descriptive, not graded)."""
    def odm_q(codes: tuple[str, ...]) -> M.Series:
        months = set.intersection(*(set(usd[c]) for c in codes))
        return M.monthly_to_quarter({m: sum(usd[c][m] for c in codes) for m in months}, 3)

    series = {"oem": None, "odm": odm_q(cfg.Q1_ODM), "odm_robust": odm_q(("2382", "2356", "6669"))}
    out = []
    last = max(max(s) for s in oem.values() if s)
    for q in M.q_range(first, last):
        row = {"quarter": q}
        a = [M.ttm(oem[k], q) for k in oem]
        b = [M.ttm(oem[k], M.q_add(q, -4)) for k in oem]
        row["oem"] = sum(a) / sum(b) - 1 if all(x is not None for x in a + b) else None
        for k in ("odm", "odm_robust"):
            row[k] = M.yoy(M.ttm(series[k], q), M.ttm(series[k], M.q_add(q, -4)))
        out.append(row)
    return out


_IDC = re.compile(r"\(([\d.]+)%\).*?(\d)Q(\d\d): [$\d,.]+ \(([\d.]+)%\)")


def idc_odm(rows: list[dict]) -> list[dict]:
    """IDC's ODM Direct share, latest release first: the share and the year-earlier share, as IDC printed them."""
    out = []
    for r in rows:
        if r.get("entity", "").startswith("ODM Direct") and r.get("period", "")[:1].isdigit():
            m = _IDC.search(str(r["value"]))
            if m:
                out.append({"period": r["period"], "share": float(m.group(1)) / 100,
                            "share_year_ago": float(m.group(4)) / 100, "source_url": r["source_url"],
                            "publish_date": r["publish_date"]})
    return sorted(out, key=lambda x: x["publish_date"], reverse=True)


def idc_vendors(rows: list[dict], period: str) -> list[dict]:
    out = []
    for r in rows:
        if r.get("period") == period and "worldwide server vendor revenue" in r.get("metric", ""):
            m = _IDC.search(str(r["value"]))
            if m:
                out.append({"entity": r["entity"], "share": float(m.group(1)) / 100,
                            "share_year_ago": float(m.group(4)) / 100})
    return out


# ---- build ------------------------------------------------------------------------------------------------

def _usd_monthly(monthly: pl.DataFrame, fx: pl.DataFrame) -> dict[str, dict[str, float]]:
    twd = M.fx_monthly(fx, "TWD")
    return {c: M.to_usd_monthly(M.tw_monthly_series(monthly, c), twd) for c in cfg.TW_CODES}


def _signal(usd: dict[str, dict[str, float]], codes: tuple[str, ...]) -> M.Series:
    months = set.intersection(*(set(usd[c]) for c in codes))
    return M.monthly_to_quarter({m: sum(usd[c][m] for c in codes) for m in months}, 2)


def quarter_last_month(q: str) -> str:
    return f"{q[:4]}-{3 * int(q[-1]):02d}"


def oem_qualifying(oem: dict[str, M.Series], fiscal_ends: dict[str, list[str]]) -> dict[str, str | None]:
    """Q1: each OEM's latest fiscal quarter ending 2026-09-15..2026-11-15, as its calendar quarter."""
    out = {}
    for k, ends in fiscal_ends.items():
        ok = [e for e in ends if "2026-09-15" <= e <= "2026-11-15"]
        out[k] = M.quarter_of_end(date.fromisoformat(max(ok))) if ok else None
    return out


def build() -> dict:
    now = utc_now()
    checks: list[dict] = []
    sec_facts, tw_q, cn_q = _read("sec_facts.parquet"), _read("tw_quarterly.parquet"), _read("cn_quarterly.parquet")
    monthly, fx = _read("tw_monthly.parquet"), _read("fx_monthly.parquet")
    lenovo = lenovo_rows()

    panel = M.company_panel(sec_facts, tw_q, cn_q, lenovo)
    oem = M.server_series(sec_facts, lenovo)
    usd = _usd_monthly(monthly, fx)

    # Q1 (registered): OEM fiscal ends come from the series' own period ends
    from pipelines.servers.sec import quarterly as sec_q
    isg_ends = list(sec_q(sec_facts, "DELL", M.REV, "ConsolidationItemsAxis=OperatingSegmentsMember;"
                          "StatementBusinessSegmentsAxis=InfrastructureSolutionsGroupMember"))
    hpe_ends = list(sec_q(sec_facts, "HPE", M.REV, "StatementBusinessSegmentsAxis=ServerSegmentMember"))
    smci_ends = list(sec_q(sec_facts, "SMCI", M.REV))
    len_ends = [r["period_end"] for r in lenovo if r["metric"] == "revenue"]
    qual = oem_qualifying(oem, {"DELL": isg_ends, "HPE": hpe_ends, "SMCI": smci_ends, "0992.HK": len_ends})
    q1 = M.q1(oem, qual, usd)
    # Ungraded check one year earlier, and a provisional reading on the latest quarter each OEM has reported.
    q1_prior = M.q1(oem, {k: M.q_add(v, -4) if v else None for k, v in qual.items()}, usd, "2025-09")
    latest_oem = {k: max((q for q in s if M.ttm(s, q) and M.ttm(s, M.q_add(q, -4))), default=None)
                  for k, s in oem.items()}
    # Provisional: OEMs on their latest reported quarter, Taiwan on the registered window (to September 2026).
    q1_prov = M.q1(oem, latest_oem, usd, "2026-09")
    q1.update({"qualifying": qual, "prior_year": q1_prior, "provisional": {**q1_prov, "quarters": latest_oem}})

    q2 = M.q2(panel)
    graded = now.date() >= GRADING_DATE
    for q in (q1, q2):
        q["grading_date"] = GRADING_DATE.isoformat()
        q["provisional_verdict"] = q["verdict"] if q is q2 else q["provisional"]["verdict"]
        if not graded:
            q["verdict"] = "pending"
        elif q["verdict"] in (None, "pending"):
            q["verdict"] = "undecided"   # registered: a member missing at grading makes the test undecided
    q2_latest = {}
    for k in (*M.Q2_GRADED, *M.Q2_REPORTED, *M.Q2_PLACEBO):
        if k in panel and (lq := latest_common(panel[k])):
            q2_latest[k] = lq
    q2["latest_quarters"] = q2_latest

    # Q5 (registered): the frozen snapshot plus company-level growth and margin at the snapshot date
    snap = pl.read_parquet(cfg.SNAP / "q5_valuation_2026-11-09.parquet")
    sc = {r["key"]: r for r in scorecard(panel)}
    val_key = {m.valuation: m.key for m in cfg.MAKERS} | {"JBL": "JBL"}
    q5_rows = []
    frozen = "growth" in snap.columns   # growth and margin are frozen with the multiples (as of 2026-11-09)
    for r in snap.iter_rows(named=True):
        s = r if frozen else sc.get(val_key.get(r["ticker"]), {})
        q5_rows.append({"ticker": r["ticker"], "name": r["name"], "fwd_pe": r["fwd_pe_2027"],
                        "n_analysts": r["n_analysts"], "growth": s.get("growth"),
                        "operating_margin": s.get("operating_margin"), "ttm_end": s.get("ttm_end")})
    q5 = M.q5(q5_rows)
    q5["all_rows"] = q5_rows

    # Q7 (registered)
    capex = M.capex_series(sec_facts)
    capex_total = {q: sum(capex[t][q] for t in capex) for q in M.q_range("2017Q1", cfg.Q7_LAST)
                   if all(q in capex[t] for t in capex)}
    q7 = M.q7(_signal(usd, cfg.Q7_SIGNAL), _signal(usd, ("6669",)), capex_total)

    forecasts = score_forecasts(registered_forecasts(), first_print("2026-11"))
    growth = group_growth(oem, usd)

    # monthly tracker
    mrows = []
    for c in cfg.TW_CODES:
        s = M.tw_monthly_series(monthly, c)
        for mth, v in s.items():
            if mth >= "2022-01":
                prev = s.get(shift(mth, -12))
                mrows.append({"code": c, "name": cfg.BY_KEY[c].name, "month": mth, "revenue_ntd": v,
                              "yoy": v / prev - 1 if prev else None})

    qmart = quarterly_mart(panel, fx)
    sc_rows = list(sc.values())
    MART = MARTS_DIR / "servers"
    write_json(qmart, MART / "quarterly.json")
    write_json(mrows, MART / "monthly.json")
    write_json(growth, MART / "group_growth.json")
    write_json({"pairs": q7["pairs"], "pairs_wiwynn": q7["pairs_wiwynn"],
                "capex": {t: s for t, s in capex.items()}}, MART / "q7.json")

    # curated layer
    ref = {k: reference(k) for k in REFERENCE_KINDS}
    share = json.loads((cfg.DATA_DIR / "reference" / "share_servers.json").read_text())

    latest_month = monthly["month"].max() if monthly.height else None
    checks += [
        check("Taiwan monthly revenue", monthly.height > 0 and latest_month is not None,
              f"{monthly['code'].n_unique() if monthly.height else 0} makers, through {latest_month}"),
        check("SEC filings", sec_facts.height > 0,
              f"{sec_facts['ticker'].n_unique() if sec_facts.height else 0} filers, {sec_facts.height} facts"),
        check("A-share statements", cn_q.height > 0, f"{cn_q['code'].n_unique() if cn_q.height else 0} companies"),
        check("Taiwan quarterly statements", tw_q.height > 0,
              f"{tw_q['code'].n_unique() if tw_q.height else 0} companies, through "
              f"{tw_q['quarter'].max() if tw_q.height else None}"),
        check("Lenovo curated quarters", bool(lenovo), f"{len(lenovo)} rows"),
        check("Q7 window complete", q7["primary"]["n"] == len(M.q_range(cfg.Q7_FIRST, cfg.Q7_LAST)),
              f"{q7['primary']['n']} of {len(M.q_range(cfg.Q7_FIRST, cfg.Q7_LAST))} quarters"),
        check("Q5 members qualifying", q5["n"] >= 15, f"{q5['n']} of {len(M.Q5_MEMBERS)}; excluded: "
              + ", ".join(q5["excluded"]), warn=q5["n"] < 15),
    ]

    facts = {
        "generated_at": now.isoformat(timespec="seconds"),
        "groups": cfg.GROUPS,
        "makers": [{"key": m.key, "name": m.name, "name_cn": m.name_cn, "group": m.group, "market": m.market,
                    "comparator": m.comparator} for m in cfg.MAKERS],
        "latest_month": latest_month,
        "scorecard": sc_rows,
        "q1": q1, "q2": q2, "q5": {k: v for k, v in q5.items()}, "q7": {k: v for k, v in q7.items()
                                                                         if not k.startswith("pairs")},
        "forecasts": forecasts,
        "customers": {"summary": ref["customers"].get("summary", []),
                      "named": public_rows(ref["customers"].get("named", []))},
        "capabilities": {"capabilities": ref["capabilities"].get("capabilities", {}),
                         "rows": ref["capabilities"].get("rows", [])},
        "private": {"rows": public_rows(ref["private"].get("rows", [])),
                    "adjacent": ref["private"].get("adjacent_names", []),
                    "excluded": ref["private"].get("excluded", [])},
        "disclosures": public_rows(ref["disclosures"].get("rows", [])),
        "cited_share": public_rows(share.get("cited_share", [])),
        "idc_odm": idc_odm(public_rows(share.get("cited_share", []))),
        "idc_vendors": idc_vendors(public_rows(share.get("cited_share", [])), "2Q26"),
        "group_growth": growth,
        "market_size": public_rows(share.get("financials", [])),
        # the registered thresholds, so page prose quotes them from here rather than typing them
        "rules": {"q1_holds_ratio": 1.08, "q5_rho_growth_min": 0.30, "q5_gap_min": 0.20, "q7_corr_min": 0.5,
                  "q7_direction_min": 0.65, "q2_gm_drop_min": 0.005, "forecast_interval": 0.80,
                  "big_customer": 0.10, "forecast_due": "2026-12-10", "gpus_per_rack": 72},
        "trendforce_csp": next((r for r in public_rows(share.get("financials", []))
                                if "share of NVIDIA rack procurement" in r.get("metric", "")), None),
        "prereg": {"q5_snapshot": "2026-11-09", "rules": "docs/prereg/ai-servers/2026-11-09_test_rules.md",
                   "forecasts": "docs/prereg/ai-servers/2026-11-09_forecasts.md"},
        "checks": checks,
        "sources": SOURCES,
    }
    write_json(facts, cfg.FACTS_PATH)
    return facts


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argparse.ArgumentParser(description="Build the server-maker marts and facts").parse_args(argv)
    facts = build()
    for c in facts["checks"]:
        log.info("check %-30s %-5s %s", c["name"], c["status"], c["detail"])
    return 1 if any(c["status"] == "fail" for c in facts["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
