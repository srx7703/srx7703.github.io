"""Build the advertising-market marts and facts. CI-safe: reads only what is in the repo.

Inputs
    data/raw/admarket/filings.json + xbrl/ + text/     per-filing extracts written by ingest
    docs/prereg/ad-market/                            the registered rules and forecasts (read, never written)
    data/reference/ad_market/                         the panel as registered (2026Q2), for the restatement check

Writes
    data/snapshots/admarket/quarterly.parquet         every derived quarter of every series (incl. flagged rows)
    data/snapshots/admarket/volume_price.parquet      parsed volume/price changes per 10-Q
    data/marts/admarket/quarterly.json                the same, values in $m; flagged rows carry no value
    data/marts/admarket/video_share.json              the video's nine-company subset by quarter
    data/marts/admarket/att_growth.json               two-year annualised growth paths (Q2)
    data/marts/admarket/open_web.json                 trailing-four-quarter revenue indexed to the Q3 base (Q3)
    data/marts/admarket/volume_price.json             volume x price against reported growth (Q4)
    data/marts/admarket/forecasts.json                registered intervals and their status
    data/marts/admarket/evaluation.json               every registered item and its status
    data/facts/admarket.json                          every number the page prints, plus the checks

Run: ``python -m pipelines.admarket.publish``
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl

from pipelines.admarket import config as cfg
from pipelines.admarket import evaluate, metrics, prereg, schema
from pipelines.admarket import transform as T
from pipelines.admarket.ingest import load_filings, load_index
from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import utc_now, write_json, write_parquet

log = logging.getLogger("admarket.publish")

FRESH_DAYS = 140   # a quarter (91 days) plus the 10-K filing window; older means a filing was missed


def reference_panel(path: Path = cfg.REFERENCE_CSV) -> dict[str, dict[str, float]]:
    """The registered snapshot: series -> quarter -> $bn (three decimals)."""
    with path.open() as f:
        rows = list(csv.reader(f))
    head = rows[0][1:]
    return {r[0]: {q: float(v) for q, v in zip(head, r[1:], strict=True) if v != ""} for r in rows[1:]}


def restatements(series: metrics.Series, ref: dict[str, dict[str, float]]) -> list[dict]:
    """Cells of the registered snapshot that the current panel no longer reproduces to the $ million."""
    out = []
    for s, by_q in ref.items():
        for q, v in by_q.items():
            now = series.get(s, {}).get(q)
            if now is None or abs(round(now / 1e9, 3) - v) > 1e-9:
                out.append({"series": s, "quarter": q, "registered_bn": v,
                            "now_bn": None if now is None else round(now / 1e9, 3)})
    return out


def quarterly_rows(filings: list[dict]) -> list[dict]:
    rows = []
    for r in cfg.RULES:
        app_seg_used = r.series == "applovin_total"
        for q, (v, method) in T.quarterly(filings, r, with_method=True).items():
            flag = None
            if v <= 0:
                flag = ("derivation artefact: a restated (continuing-operations) fiscal year minus the original "
                        "nine months; not used" if app_seg_used else "non-positive derived quarter; not used")
            rows.append({"series": r.series, "label": r.label, "ticker": r.ticker, "quarter": q,
                         "value_usd_m": None if flag else round(v / 1e6, 3), "method": method, "flag": flag})
    return rows


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build() -> dict:
    now = utc_now()
    filings = load_filings()
    index = load_index()
    series = T.all_series(filings)
    qrows = quarterly_rows(filings)
    qdf = schema.QUARTERLY.validate(pl.DataFrame(qrows, schema={
        "series": pl.Utf8, "label": pl.Utf8, "ticker": pl.Utf8, "quarter": pl.Utf8, "value_usd_m": pl.Float64,
        "method": pl.Utf8, "flag": pl.Utf8}))

    panel = metrics.video_panel(series)
    share_rows = [{"quarter": r["quarter"], "company": k, "value_bn": r["values"][k] / 1e9, "share": r["share"][k]}
                  for r in panel["rows"] for k in cfg.VIDEO_SUBSET if r["values"][k] is not None]
    schema.VIDEO_SHARE.validate(pl.DataFrame(share_rows))

    q1 = metrics.q1_status(series)
    q2 = metrics.q2(series)
    q3 = metrics.q3(series)
    vp_raw = T.volume_price_rows(filings)
    q4 = metrics.q4(series, vp_raw)
    schema.VOLUME_PRICE.validate(pl.DataFrame(
        [{k: r[k] for k in ("series", "label", "quarter", "volume", "price", "reported", "implied", "gap_pp")}
         for r in q4["rows"]], schema={"series": pl.Utf8, "label": pl.Utf8, "quarter": pl.Utf8, "volume": pl.Int64,
                                       "price": pl.Int64, "reported": pl.Float64, "implied": pl.Float64,
                                       "gap_pp": pl.Float64}))
    q5 = metrics.q5_status(series)

    reg = prereg.load()
    recomputed = metrics.rule_a(series)
    rc = metrics.recompute_matches(recomputed, reg)
    a = metrics.score_a(filings, reg)
    schema.FORECASTS.validate(pl.DataFrame(a["rows"], schema={
        "series": pl.Utf8, "quarter": pl.Utf8, "point_m": pl.Int64, "low_m": pl.Int64, "high_m": pl.Int64,
        "actual_m": pl.Float64, "status": pl.Utf8, "filed": pl.Utf8, "error": pl.Float64}))
    b = metrics.score_b(filings, series, reg)
    evaluation = {"files": reg["files"], "items": evaluate.items(q1, q2, q3, q4, q5, a, b)}

    # ---- checks ----
    ref = reference_panel()
    restated = restatements(series, ref)
    ref_sha = _sha256(cfg.REFERENCE_CSV)
    per_ticker = {tk: sum(1 for x in index if x["ticker"] == tk) for tk in cfg.CIKS}
    latest = max(index, key=lambda x: x["filed"])
    age = (now.date() - date.fromisoformat(latest["filed"])).days
    flagged = [r for r in qrows if r["flag"]]
    text_filings = [x for x in index if x["ticker"] in cfg.TEXT_TICKERS and x["form"] == "10-Q"]
    no_text = [f"{x['ticker']} {x['period']}" for x in text_filings if not x.get("text")]
    vp_by_series = {s: sum(1 for r in vp_raw if r["series"] == s) for s in cfg.Q4_SERIES}
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    leaked = [p.name for p in cfg.RAW.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")
              and email.search(p.read_text(encoding="utf-8"))]
    checks = [
        check("Filing extracts", all(per_ticker.values()),
              f"{len(index)} 10-Q/10-K filings across {len(per_ticker)} companies; the latest is "
              f"{latest['ticker']} {latest['form']} for {latest['period']}, filed {latest['filed']}"),
        check("Freshness", age <= FRESH_DAYS, f"latest filing {age} days ago (limit {FRESH_DAYS})", warn=True),
        check("Quarterly schema", True, f"{qdf.height} series-quarters pass the pandera schema (positive revenue, one "
                                        "row per series and quarter)"),
        check("Derivation artefacts flagged", True,
              f"{len(flagged)} derived quarter(s) flagged and not published as a value: "
              + (", ".join(f"{r['series']} {r['quarter']}" for r in flagged) or "none")),
        check("Video subset complete", True,
              f"all nine companies through {panel['last_quarter']}; {panel['next_quarter']['quarter']}: "
              f"{panel['next_quarter']['n_reported']} of {panel['next_quarter']['n_members']} reported"),
        check("Registered snapshot unchanged", ref_sha == reg["snapshot_sha256"] == cfg.REFERENCE_SHA256,
              f"data/reference/ad_market CSV sha256 {ref_sha[:12]} matches the hash in the forecast registration"),
        check("Restatements since registration", not restated,
              f"{len(restated)} of {sum(len(v) for v in ref.values())} registered cells differ from the current panel"
              + (": " + ", ".join(f"{r['series']} {r['quarter']}" for r in restated[:8]) if restated else ""),
              warn=True),
        check("Rule A reproduces the registered forecasts", rc["mismatches"] == 0 and rc["band_ok"],
              f"{rc['cells'] - rc['mismatches']} of {rc['cells']} registered numbers recomputed to the $ million; "
              f"back-test band and counts {'match' if rc['band_ok'] else 'differ'}", warn=True),
        check("Volume/price text parsed", not no_text and all(vp_by_series.values()),
              ", ".join(f"{cfg.Q4_SERIES[s]['label']}: {n} quarters" for s, n in vp_by_series.items())
              + (f"; 10-Qs without text: {', '.join(no_text)}" if no_text else ""), warn=True),
        check("Registered files unamended", not any(f["amendments"] for f in reg["files"]),
              "; ".join(f"{f['path'].rsplit('/', 1)[-1]} blob {f['blob'][:12]}, "
                        f"{len(f['amendments'])} amendment(s)" for f in reg["files"]), warn=True),
        check("No contact address in committed files", not leaked,
              f"{len(leaked)} raw index or log files contain an email address"
              + (f": {', '.join(leaked[:5])}" if leaked else "")),
    ]

    others = [k for k in cfg.VIDEO_SUBSET if k not in cfg.SHARE_CHART_GROUPS]
    facts = {
        "generated_at": now.isoformat(timespec="seconds"),
        "last_updated": now.date().isoformat(),
        "status": "in-progress",
        "data": {
            "n_filings": len(index), "n_companies": len(cfg.CIKS), "n_series": len(cfg.RULES),
            "first_quarter": min(r["quarter"] for r in qrows), "last_quarter": panel["last_quarter"],
            "latest_filing": {k: latest[k] for k in ("ticker", "form", "period", "filed")},
            "n_text_filings": len(text_filings), "first_report": cfg.MIN_REPORT_DATE,
        },
        "video": {**cfg.VIDEO, "claim_check": cfg.CLAIM_CHECK},
        "panel": {**{k: v for k, v in panel.items() if k != "rows"},
                  "others": others,
                  "others_share_last": sum(panel["share_last"][k] or 0 for k in others),
                  "snap_non_ad_share_last": 1 - series["snap_ads"][panel["last_quarter"]]
                  / series["snap_total"][panel["last_quarter"]]},
        "labels": cfg.SERIES_LABEL,
        "rules": {
            "applovin_total_from": cfg.APPLOVIN_TOTAL_FROM, "applovin_from": cfg.APPLOVIN_FROM,
            "reddit_video_from": cfg.REDDIT_VIDEO_FROM, "att_release": cfg.ATT_RELEASE, "att_ios": cfg.ATT_IOS,
            "q1_before": list(cfg.Q1_BEFORE), "q1_after": list(cfg.Q1_AFTER), "q1_threshold": cfg.Q1_THRESHOLD,
            "q2_before": list(cfg.Q2_BEFORE), "q2_after": list(cfg.Q2_AFTER), "q2_threshold": cfg.Q2_THRESHOLD,
            "q3_from": [T.shift(cfg.Q3_FROM_END, -3), cfg.Q3_FROM_END],
            "q3_to": [T.shift(cfg.Q3_TO_END, -3), cfg.Q3_TO_END],
            "q3_years": cfg.Q3_YEARS, "q3_threshold": cfg.Q3_THRESHOLD, "q4_max_gap_pp": cfg.Q4_MAX_GAP_PP,
            "backtest_from": cfg.BACKTEST_FROM,
        },
        "q1": q1,
        "q2": {k: v for k, v in q2.items() if k not in ("path", "group_path")},
        "q3": {k: v for k, v in q3.items() if k != "path"},
        "q4": {k: v for k, v in q4.items() if k != "rows"} | {"n_rows": len(q4["rows"])},
        "q5": {k: v for k, v in q5.items() if k != "rows"},
        "forecasts": {
            "status": a["status"], "n": a["n"], "n_scored": a["n_scored"], "hits": a["hits"],
            "hit_rate": a["hit_rate"], "verdict": a["verdict"],
            "band": {str(h): {"low": v[0], "high": v[1], "n": v[2]} for h, v in reg["band"].items()},
            "level": cfg.FORECAST_LEVEL,
            "miscalibrated_at_or_below": cfg.A_MISCALIBRATED_AT_OR_BELOW,
            "too_wide_at_or_above": cfg.A_TOO_WIDE_AT_OR_ABOVE,
            "recompute": rc, "targets": list(cfg.FORECAST_TARGETS), "last_reported": cfg.FORECAST_LAST,
            "n_series": len(reg["forecasts"]),
            "calls": b, "calls_pending": sum(c["status"] == "pending" for c in b),
            "calls_true": sum(c["status"] == "true" for c in b),
        },
        "prereg": {"files": reg["files"], "snapshot_sha256": reg["snapshot_sha256"],
                   "registered": "2026-10-21"},
        "restatements": restated,
        "checks": checks,
        "sources": cfg.SOURCES,
    }
    marts = {
        "quarterly": qrows,
        "video_share": share_rows,
        "att_growth": {"members": q2["path"], "groups": q2["group_path"]},
        "open_web": q3["path"],
        "volume_price": q4["rows"],
        "forecasts": a["rows"],
        "evaluation": evaluation,
    }
    return {"facts": facts, "marts": marts, "snapshot": qdf,
            "vp": pl.DataFrame(vp_raw, schema={"series": pl.Utf8, "quarter": pl.Utf8, "volume": pl.Int64,
                                               "price": pl.Int64, "period": pl.Utf8, "accession": pl.Utf8})}


def write(out: dict) -> None:
    fails = [c for c in out["facts"]["checks"] if c["status"] == "fail"]
    if fails:
        raise RuntimeError(f"checks failed, nothing written: {fails}")
    write_parquet(out["snapshot"], cfg.SNAP / "quarterly.parquet")
    write_parquet(out["vp"], cfg.SNAP / "volume_price.parquet")
    for name, obj in out["marts"].items():
        write_json(obj, cfg.MART_DIR / f"{name}.json")
    write_json(out["facts"], cfg.FACTS_PATH)


def main() -> int:
    setup_logging()
    out = build()
    write(out)
    f = out["facts"]
    log.info("panel through %s; Q2 %s, Q3 %s; forecasts %s (%d/%d scored)", f["data"]["last_quarter"],
             f["q2"]["verdict"], f["q3"]["verdict"], f["forecasts"]["status"], f["forecasts"]["n_scored"],
             f["forecasts"]["n"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
