"""Build the memory-cycle marts and facts. CI-safe: reads only what is in the repo.

Inputs
    data/raw/memcycle/ecos/<run>/                 ECOS 402Y016 raw responses (via ingest -> snapshot)
    data/case_studies/memcycle/frozen/            stock-side results exported on the owner's machine
    data/case_studies/memcycle/q3_versions.json   the Q3 verdict at each research commit

Writes
    data/snapshots/memcycle/ecos_402Y016.parquet  normalised ECOS rows
    data/marts/memcycle/price_index.json          contract-currency index from each series' start (ECOS only)
    data/marts/memcycle/turns.json                every registered run of the turning-point rule
    data/marts/memcycle/calendar.json             main-rule phases, cycle numbers, deletions, sensitivity
    data/marts/memcycle/leads.json                stock-peak month and lead per company-cycle (no closes)
    data/marts/memcycle/multiples.json            up-leg multiples, ratios only
    data/marts/memcycle/evaluation.json           the registered items and their status
    data/facts/memcycle.json                      every number the page prints

**The regression guard.** The frozen stock-side table was computed against the calendar dated from the
registration pull. If the calendar recomputed here ever disagrees with the price peaks the frozen rows were
measured against, or the Q3 verdict recomputed from the frozen rows disagrees with the last research version,
this module raises before writing anything: the frozen leads would be measured against peaks that no longer
exist, and the page would be quietly wrong.

Run: ``python -m pipelines.memcycle.publish``
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import sys
from pathlib import Path

import polars as pl

from pipelines.common.checks import check, file_fingerprint
from pipelines.common.log import setup_logging
from pipelines.common.storage import utc_now, write_json, write_parquet
from pipelines.memcycle import config as cfg
from pipelines.memcycle import evaluate, ingest, schema
from pipelines.memcycle import transform as T

log = logging.getLogger("memcycle.publish")


class RegressionError(RuntimeError):
    """The recomputed calendar or verdict disagrees with what the frozen results were built on."""


# ---------------------------------------------------------------------------------------------
# frozen stock-side table
# ---------------------------------------------------------------------------------------------

_BOOL = {"complete", "price_peak_confirmed", "dram_maker", "upleg_truncated_by_listing",
         "delisted_or_out_of_scope", "partial_latest_month"}
_INT = {"lead_months", "lead_months_high", "trough_lead_months"}
_FLOAT = {"drawdown_to_trough", "upleg_multiple", "upleg_multiple_hl", "upleg_multiple_usd",
          "upleg_multiple_usd_series", "bench1_multiple", "excess1", "bench2_multiple", "excess2"}


def _typed(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if v == "":
            out[k] = None
        elif k in _BOOL:
            if v not in ("True", "False"):
                raise ValueError(f"{k}={v!r} is not a boolean")
            out[k] = v == "True"
        elif k in _INT:
            out[k] = int(v)
        elif k in _FLOAT:
            out[k] = float(v)
        else:
            out[k] = v
    return out


def read_frozen(path: Path = cfg.FROZEN_PATH) -> list[dict]:
    with path.open(newline="") as f:
        rows = [_typed(r) for r in csv.DictReader(f)]
    frozen_frame(rows)  # validate before anything reads the rows
    return rows


def frozen_frame(rows: list[dict]) -> pl.DataFrame:
    cols = list(schema.FROZEN_SCHEMA.columns)
    dtypes = {c: (pl.Boolean if c in _BOOL else pl.Int64 if c in _INT else pl.Float64 if c in _FLOAT else pl.Utf8)
              for c in cols}
    missing = set(cols) - set(rows[0]) if rows else set()
    extra = set(rows[0]) - set(cols) if rows else set()
    if missing or extra:
        raise ValueError(f"frozen table columns differ from the schema: missing {sorted(missing)}, "
                         f"extra {sorted(extra)}")
    df = pl.DataFrame(rows, schema=dtypes)
    return schema.FROZEN_SCHEMA.validate(df)


# ---------------------------------------------------------------------------------------------
# price calendar
# ---------------------------------------------------------------------------------------------


def series(df: pl.DataFrame, item: str, basis: str) -> tuple[list[str], list[float]]:
    s = (df.filter((pl.col("item_code") == item) & (pl.col("basis") == basis)
                   & (pl.col("month") >= cfg.SERIES_START[item])).sort("month"))
    return s["month"].to_list(), s["value"].to_list()


def runs(df: pl.DataFrame) -> list[dict]:
    """The ten registered runs: C at 20/30/0%, D and W at 20%, for DRAM and flash."""
    out = []
    for item in cfg.SERIES_START:
        for basis in (cfg.MAIN_BASIS, *cfg.SENS_BASES):
            months, values = series(df, item, basis)
            for amp in (cfg.AMP_MAIN, *cfg.AMP_SENS):
                if basis != cfg.MAIN_BASIS and amp != cfg.AMP_MAIN:
                    continue
                tp, deletions = T.date_turns_logged(months, values, amp)
                out.append({"product": cfg.PRODUCT[item], "basis": basis, "amp": amp, "turns": tp,
                            "deletions": deletions, "months": months, "values": values})
    return out


def main_run(all_runs: list[dict], product: str) -> dict:
    return next(r for r in all_runs if r["product"] == product and r["basis"] == cfg.MAIN_BASIS
                and r["amp"] == cfg.AMP_MAIN)


def turns_frame(all_runs: list[dict]) -> pl.DataFrame:
    rows = [{"product": r["product"], "basis": r["basis"], "amp_threshold": r["amp"], "kind": t.kind,
             "month": t.month, "confirmed": t.confirmed} for r in all_runs for t in r["turns"]]
    return schema.TURNS_SCHEMA.validate(pl.DataFrame(rows))


def phases_frame(all_runs: list[dict]) -> pl.DataFrame:
    rows = []
    for product in ("DRAM", "NAND"):
        for p in T.phases(main_run(all_runs, product)["turns"]):
            rows.append({"product": product, **p})
    return schema.PHASES_SCHEMA.validate(pl.DataFrame(rows))


def cycles(phases: list[dict]) -> list[dict]:
    """Number the cycles trough -> peak -> trough. A leading peak with no trough before it is cycle 0."""
    out, n = [], 0
    for i, p in enumerate(phases):
        if p["from_kind"] == "P" and i == 0:
            out.append({"cycle": 0, "trough": None, "peak": p["from"], "next_trough": p["to"],
                        "up_months": None, "up_change": None, "down_months": p["months"],
                        "down_change": p["change"], "peak_confirmed": True, "complete": False})
        if p["from_kind"] != "T":
            continue
        n += 1
        down = phases[i + 1] if i + 1 < len(phases) else None
        out.append({"cycle": n, "trough": p["from"], "peak": p["to"], "next_trough": down["to"] if down else None,
                    "up_months": p["months"], "up_change": p["change"],
                    "down_months": down["months"] if down else None, "down_change": down["change"] if down else None,
                    "peak_confirmed": p["to_confirmed"],
                    "complete": bool(down) and down["to_confirmed"] and p["to_confirmed"]})
    return out


def sensitivity(all_runs: list[dict], product: str) -> list[dict]:
    """Each run against the main rule: every main turn is paired with the nearest same-kind turn within
    ``HALF_WINDOW`` months; unpaired turns are added or dropped (ported from research_ref/phase1_tables.py)."""
    base = [(t.kind, t.month) for t in main_run(all_runs, product)["turns"]]
    out = []
    for r in all_runs:
        if r["product"] != product:
            continue
        free = [(t.kind, t.month) for t in r["turns"]]
        shifts, dropped = [], []
        for k, m in base:
            cand = [c for c in free if c[0] == k and abs(T.months_between(m, c[1])) <= cfg.HALF_WINDOW]
            if not cand:
                dropped.append(f"{k}{m}")
                continue
            c = min(cand, key=lambda c, m=m: abs(T.months_between(m, c[1])))
            free.remove(c)
            if c[1] != m:
                shifts.append({"kind": k, "main": m, "this": c[1], "months": T.months_between(m, c[1])})
        out.append({"product": product, "basis": r["basis"], "basis_name": cfg.BASIS_NAME[r["basis"]],
                    "amp": r["amp"], "n_turns": len(r["turns"]),
                    "n_peaks_confirmed": sum(1 for t in r["turns"] if t.kind == "P" and t.confirmed),
                    "shifts": shifts, "added": [f"{k}{m}" for k, m in free], "dropped": dropped,
                    "identical": not shifts and not free and not dropped})
    return out


def deleted_rallies(run: dict) -> list[dict]:
    """Rises the duration step removed although they cleared the amplitude threshold: real rallies that were
    too short (or sat in too short a cycle) to count. The page draws them so the reader can see them."""
    out = []
    for d in run["deletions"]:
        if d.step != "duration" or d.a.kind != "T" or d.change < cfg.AMP_MAIN:
            continue
        out.append({"product": run["product"], "order": d.order, "from": d.a.month, "to": d.b.month,
                    "months": d.months, "change": round(d.change, 4),
                    "under_min_phase": d.months < cfg.MIN_PHASE,
                    "margin_log": round(d.margin, 4) if d.margin is not None else None})
    return out


# ---------------------------------------------------------------------------------------------
# stock side (from the frozen rows)
# ---------------------------------------------------------------------------------------------


def exclusion_reason(r: dict) -> str | None:
    """Why a company-cycle is not in its Q3 sample, in words; None if it is."""
    if r["status"] != "ok":
        return r["status"]
    if not r["price_peak_confirmed"]:
        return "price peak not confirmed (the running cycle)"
    if not r["complete"]:
        if r["first_month"] > (r["window"] or "(0000-00")[1:8]:
            return "listed inside the window"
        if r["delisted_or_out_of_scope"]:
            return "delisted or out of scope inside the window"
        return "a month in the window has no close"
    if r["group"] == "unregistered":
        return "not on the registered list"
    return None


def partial_latest_month(r: dict) -> bool:
    """True when the stock peak or trough is the month still in progress (after the last ECOS month).

    Derived from months rather than taken from the frozen column: the research code set that flag whenever a
    local file's notes said "partial", which also marked Elpida and Qimonda rows whose files are *partial
    archives*, not an unfinished month. The frozen table keeps the research value so it stays reproducible.
    """
    return any(m is not None and m > cfg.DATA_END for m in (r.get("stock_peak"), r.get("stock_trough")))


def lead_rows(rows: list[dict]) -> list[dict]:
    samples = evaluate.samples(rows)
    member = {k: {(r["product"], r["company"], r["price_peak"]) for r in v} for k, v in samples.items()}
    out = []
    for r in rows:
        key = (r["product"], r["company"], r["price_peak"])
        out.append({
            "product": r["product"], "company": r["company"], "label": r["label"], "group": r["group"],
            "dram_maker": r["dram_maker"], "price_peak": r["price_peak"],
            "price_peak_confirmed": r["price_peak_confirmed"], "window": r["window"],
            "stock_peak_month": r["stock_peak"], "lead_months": r["lead_months"],
            "stock_high_month": r["stock_peak_high_month"], "lead_months_intramonth": r["lead_months_high"],
            "stock_trough_month": r["stock_trough"], "trough_lead_months": r["trough_lead_months"],
            "drawdown_to_trough": r["drawdown_to_trough"], "complete": r["complete"],
            "in_main": key in member["main_pure"], "in_dram_makers": key in member["sens_dram_makers_only"],
            "in_diversified": key in member["diversified_dram"], "in_nand_panel": key in member["nand_panel"],
            "excluded_because": exclusion_reason(r),
            "partial_latest_month": partial_latest_month(r),
        })
    return out


def multiple_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r["status"] != "ok" or r["group"] == "unregistered":
            continue
        why = None
        if r["upleg_truncated_by_listing"]:
            why = f"measured from the listing month ({r['first_month']}), not from a cycle low"
        out.append({
            "product": r["product"], "company": r["company"], "label": r["label"], "group": r["group"],
            "price_peak": r["price_peak"], "price_peak_confirmed": r["price_peak_confirmed"],
            "upleg_start": r["upleg_start"], "upleg_low_month": r["upleg_low"], "stock_peak_month": r["stock_peak"],
            "months_low_to_peak": T.months_between(r["upleg_low"], r["stock_peak"]),
            "multiple": r["upleg_multiple"], "multiple_intramonth": r["upleg_multiple_hl"],
            "multiple_usd": r["upleg_multiple_usd"], "bench1": r["bench1"], "bench1_multiple": r["bench1_multiple"],
            "excess1": r["excess1"], "bench2": r["bench2"], "bench2_multiple": r["bench2_multiple"],
            "excess2": r["excess2"], "truncated_by_listing": r["upleg_truncated_by_listing"],
            "spans_2000_bubble": r["upleg_start"] < "2000-01",
            "complete": r["complete"], "excluded_because": why,
        })
    return out


# ---------------------------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------------------------


def regression_guard(all_runs: list[dict], rows: list[dict], q3: dict, versions: dict) -> list[dict]:
    """Raise if the frozen rows no longer match the calendar or the registered verdict; else return checks."""
    problems = []
    for product in ("DRAM", "NAND"):
        cal = {(t.month, t.confirmed) for t in main_run(all_runs, product)["turns"] if t.kind == "P"}
        frozen = {(r["price_peak"], r["price_peak_confirmed"]) for r in rows if r["product"] == product}
        if frozen != cal:
            problems.append(f"{product}: frozen peaks {sorted(frozen ^ cal)} differ from the recomputed calendar")
    last = versions["versions"][-1]["q3"]
    for k, v in q3.items():
        if {kk: vv for kk, vv in v.items() if kk != "leads"} != last[k]:
            problems.append(f"Q3 {k}: recomputed {v} differs from research version {versions['versions'][-1]}")
    if problems:
        raise RegressionError("; ".join(problems))
    return [
        check("Calendar matches the frozen rows", True,
              "every price peak the frozen stock-side rows were measured against is a peak of the recomputed "
              "calendar, with the same confirmation flag"),
        check("Q3 reproduces the research result", True,
              f"all four groups equal research commit {versions['versions'][-1]['research_commit']}"),
    ]


# ---------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------


def _med(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def calendar_facts(run: dict, cyc: list[dict], item: str) -> dict:
    turns = run["turns"]
    confirmed_peaks = [t for t in turns if t.kind == "P" and t.confirmed]
    last_turn = turns[-1]
    ups = [c for c in cyc if c["up_change"] is not None and c["peak_confirmed"]]
    downs = [c for c in cyc if c["down_change"] is not None and c["cycle"] > 0 and c["complete"]]
    current = next((c for c in reversed(cyc) if not c["peak_confirmed"]), None)
    months, values = run["months"], run["values"]
    out = {
        "series_start": cfg.SERIES_START[item],
        "last_month": months[-1],
        "n_turns": len(turns),
        "n_complete_cycles": sum(1 for c in cyc if c["complete"]),
        "first_complete_trough": next((c["trough"] for c in cyc if c["complete"]), None),
        "n_confirmed_peaks": len(confirmed_peaks),
        "leading_peak": cyc[0]["peak"] if cyc and cyc[0]["cycle"] == 0 else None,
        "leading_decline_months": cyc[0]["down_months"] if cyc and cyc[0]["cycle"] == 0 else None,
        "leading_decline_change": round(cyc[0]["down_change"], 4) if cyc and cyc[0]["cycle"] == 0 else None,
        "last_turn": {"kind": last_turn.kind, "month": last_turn.month, "confirmed": last_turn.confirmed},
        "last_turn_is_series_end": last_turn.month == months[-1],
        "median_up_months": _med([c["up_months"] for c in ups]),
        "median_up_change": round(_med([c["up_change"] for c in ups]), 4) if ups else None,
        "median_down_months": _med([c["down_months"] for c in downs]),
        "median_down_change": round(_med([c["down_change"] for c in downs]), 4) if downs else None,
        "min_up_months": min((c["up_months"] for c in ups), default=None),
        "up_legs_at_min_phase": [c["peak"] for c in cyc if c["up_months"] == cfg.MIN_PHASE],
        "index_span_orders": round(math.log10(max(values) / min(values)), 2),
        "current": None,
    }
    if current:
        out["current"] = {"trough": current["trough"], "to": current["peak"], "months": current["up_months"],
                          "change": round(current["up_change"], 4),
                          "rank_among_rises": 1 + sum(1 for c in ups if c["up_change"] > current["up_change"]),
                          "n_rises": len(ups) + 1}
    return out


def rule_trough_vs_low(run: dict) -> dict | None:
    """The leading decline's rule trough against the index low inside it (they differ for DRAM 1995-2003)."""
    turns = run["turns"]
    if len(turns) < 2 or turns[0].kind != "P":
        return None
    a, b = turns[0], turns[1]
    months, values = run["months"], run["values"]
    inside = [(v, m) for m, v in zip(months, values, strict=True) if a.month <= m <= b.month]
    min_v, min_m = min(inside, key=lambda x: (x[0], x[1]))
    return {"rule_trough": b.month, "index_min_month": min_m, "index_min_vs_rule_trough": round(min_v / b.value - 1, 4)}


def build(run_dir: Path | None = None) -> dict:
    now = utc_now()
    snap = ingest.snapshot(run_dir)
    all_runs = runs(snap)
    turns = turns_frame(all_runs)
    phases = phases_frame(all_runs)
    rows = read_frozen()
    q3 = evaluate.q3(rows)
    versions = json.loads(cfg.Q3_VERSIONS_PATH.read_text())
    checks = regression_guard(all_runs, rows, q3, versions)

    by_product = {p: [x for x in phases.to_dicts() if x["product"] == p] for p in ("DRAM", "NAND")}
    cyc = {p: cycles(ph) for p, ph in by_product.items()}
    dram, nand = main_run(all_runs, "DRAM"), main_run(all_runs, "NAND")
    rallies = deleted_rallies(dram) + deleted_rallies(nand)
    knife = min((d for d in dram["deletions"] if d.margin is not None), key=lambda d: d.margin)
    leads = lead_rows(rows)
    mult = multiple_rows(rows)

    # ---- marts ----
    start = pl.col("item_code").replace_strict(cfg.SERIES_START)
    price_index = (snap.filter((pl.col("basis") == cfg.MAIN_BASIS) & (pl.col("month") >= start))
                   .with_columns(pl.col("item_code").replace_strict(cfg.PRODUCT).alias("product"),
                                 ((pl.col("item_code") == cfg.FLASH_ITEM)
                                  & (pl.col("month") < cfg.FLASH_NOR_UNTIL)).alias("nor_era"))
                   .select("product", "month", "value", "preliminary", "nor_era").sort("product", "month"))
    calendar = {
        "phases": [{**x, "change": round(x["change"], 4)} for x in phases.to_dicts()],
        "cycles": {p: [{**c, "up_change": c["up_change"] and round(c["up_change"], 4),
                        "down_change": c["down_change"] and round(c["down_change"], 4)} for c in v]
                   for p, v in cyc.items()},
        "deletions": [{"product": r["product"], "order": d.order, "step": d.step, "from": d.a.month,
                       "from_kind": d.a.kind, "to": d.b.month, "to_kind": d.b.kind, "months": d.months,
                       "change": round(d.change, 4), "margin_log": d.margin and round(d.margin, 4)}
                      for r in (dram, nand) for d in r["deletions"]],
        "deleted_rallies": rallies,
        "sensitivity": sensitivity(all_runs, "DRAM") + sensitivity(all_runs, "NAND"),
    }
    q3_public = {k: {**v, "label": evaluate.GROUPS[k]} for k, v in q3.items()}
    flips = evaluate.flips_to_inconclusive(q3["main_pure"]["leads"])
    dcal = calendar_facts(dram, cyc["DRAM"], cfg.DRAM_ITEM)
    ncal = calendar_facts(nand, cyc["NAND"], cfg.FLASH_ITEM)
    current_peak_confirmed = dram["turns"][-1].confirmed
    evaluation = {
        "plan": "docs/EVALUATION_PLAN.md",
        "section": "G",
        "items": [
            {"id": "calendar", "status": "resolved", "kind": "descriptive",
             "result": f"{dcal['n_complete_cycles']} complete DRAM cycles, {ncal['n_complete_cycles']} flash"},
            {"id": "Q3", "status": "resolved", "verdict": q3["main_pure"]["verdict"],
             "result": {k: {kk: vv for kk, vv in v.items() if kk != "leads"} for k, v in q3.items()}},
            {"id": "multiples", "status": "resolved", "kind": "descriptive", "result": f"{len(mult)} rows"},
            *evaluate.not_yet(q3["main_pure"], dram["months"][-1], current_peak_confirmed),
        ],
    }

    # ---- checks ----
    per_series = snap.group_by("item_code", "basis").agg(pl.len().alias("n"), pl.col("month").min().alias("first"),
                                                         pl.col("month").max().alias("last"))
    gaps = 0
    for s in per_series.iter_rows(named=True):
        span = T.months_between(s["first"], s["last"]) + 1
        gaps += span - s["n"]
    prelim = snap.filter(pl.col("preliminary"))
    manifest = json.loads(cfg.MANIFEST_PATH.read_text())
    missing_inputs = [m["path"] for m in manifest["inputs"] if not m["present"]]
    all_falsified = all(v["q3"]["main_pure"]["verdict"] == "falsified" for v in versions["versions"])
    no_data = [r for r in rows if r["company"] in cfg.NO_DATA]
    no_data_files = {c.local_file for c in cfg.COMPANIES if c.key in cfg.NO_DATA}
    checks = [
        check("ECOS schema", True, f"{snap.height:,} rows across {per_series.height} series pass the pandera "
                                   "schema (positive values, unit 2020=100, one row per series-month)"),
        check("ECOS completeness", gaps == 0, f"{gaps} missing months between each series' first and last month"),
        check("Preliminary month flagged", prelim["month"].n_unique() == 1,
              f"{prelim.height} rows flagged preliminary, all in {prelim['month'].max()}, the latest month"),
        check("Calendar reproduction", True, f"{turns.height} turns across {len(all_runs)} registered runs and "
                                             f"{phases.height} main-rule phases, recomputed from the raw responses"),
        *checks,
        check("Q3 stable across research versions", all_falsified,
              f"the main verdict is '{q3['main_pure']['verdict']}' in all {len(versions['versions'])} recorded "
              "research versions"),
        file_fingerprint(cfg.FROZEN_PATH, "frozen stock-side table"),
        check("No closes in the public tables", True,
              "the frozen table passes a strict schema with no close, index-level or rebased column"),
        check("Local inputs", set(missing_inputs) <= no_data_files,
              f"{len(manifest['inputs']) - len(missing_inputs)} of {len(manifest['inputs'])} local inputs hashed "
              f"in the manifest; missing: {', '.join(missing_inputs) or 'none'} (registered, no data)",
              warn=True),
        check("Registered companies without data", not no_data,
              "Toshiba is registered in the diversified group and the NAND panel but has no clean history "
              "(Yahoo dropped it after the 2023 take-private); reported, never silently dropped", warn=True),
    ]

    truncated = [m for m in mult if m["truncated_by_listing"] and m["product"] == "DRAM"]
    mu_current = next((m for m in mult if m["company"] == "MU" and m["product"] == "DRAM"
                       and not m["price_peak_confirmed"]), None)
    facts = {
        "generated_at": now.isoformat(timespec="seconds"),
        "last_updated": now.date().isoformat(),
        "status": "in-progress",
        "frozen": True,
        "ecos": {
            "table": cfg.ECOS_TABLE, "items": {"DRAM": cfg.DRAM_ITEM, "flash": cfg.FLASH_ITEM},
            "basis": cfg.MAIN_BASIS, "basis_name": cfg.BASIS_NAME[cfg.MAIN_BASIS],
            "last_month": dram["months"][-1],
            "last_month_preliminary": bool(prelim.height),
            "retrieved_at": snap["retrieved_at"].max(),
            "flash_nor_until": cfg.FLASH_NOR_UNTIL,
        },
        "rule": {"half_window": cfg.HALF_WINDOW, "min_phase": cfg.MIN_PHASE, "min_cycle": cfg.MIN_CYCLE,
                 "amp_main": cfg.AMP_MAIN, "amp_sens": list(cfg.AMP_SENS)},
        "calendar": {"DRAM": dcal, "NAND": ncal},
        "leading_decline": rule_trough_vs_low(dram),
        "deleted_rallies": rallies,
        "knife_edge": {"deleted": f"{knife.a.kind}{knife.a.month}-{knife.b.kind}{knife.b.month}",
                       "from": knife.a.month, "to": knife.b.month, "margin_log": round(knife.margin, 4),
                       "margin_pct": round(math.exp(knife.margin) - 1, 4)},
        "q3": q3_public,
        "q3_rule": {"lead_min": cfg.LEAD_MIN, "holds_share": cfg.Q3_HOLDS_SHARE,
                    "median_band": list(cfg.Q3_MEDIAN_BAND), "falsify_share": cfg.Q3_FALSIFY_SHARE},
        "q3_versions": {"n": len(versions["versions"]), "all_falsified": all_falsified,
                        "commits": [v["research_commit"] for v in versions["versions"]]},
        "robustness": {"flips_to_inconclusive": flips, "post_hoc": True},
        "multiples": {
            "n_rows": len(mult),
            "truncated": [{"label": m["label"], "price_peak": m["price_peak"], "multiple": m["multiple"],
                           "first_month": next(r["first_month"] for r in rows if r["company"] == m["company"])}
                          for m in truncated],
            "n_spanning_2000": sum(1 for m in mult if m["spans_2000_bubble"]),
            "micron_current": mu_current and {"multiple": mu_current["multiple"],
                                              "intramonth": mu_current["multiple_intramonth"],
                                              "low_month": mu_current["upleg_low_month"],
                                              "peak_month": mu_current["stock_peak_month"]},
        },
        "n_companies_registered": sum(1 for c in cfg.COMPANIES if c.group != "unregistered"),
        "n_pure": sum(1 for c in cfg.COMPANIES if c.group == "pure"),
        "n_company_cycles": len(rows),
        "prereg": {"path": str(cfg.PREREG_PATH.relative_to(cfg.REPO_ROOT)),
                   "blobs": [{"blob": b, "at": at, "what": w} for b, at, w in cfg.PREREG_BLOBS]},
        "video": cfg.VIDEO,
        "checks": checks,
        "sources": cfg.SOURCES,
    }
    return {"snapshot": snap, "price_index": price_index.to_dicts(), "turns": turns.to_dicts(),
            "calendar": calendar, "leads": leads, "multiples": mult, "evaluation": evaluation, "facts": facts}


def write(out: dict) -> None:
    fails = [c for c in out["facts"]["checks"] if c["status"] == "fail"]
    if fails:
        raise RuntimeError(f"checks failed, nothing written: {fails}")
    write_parquet(out["snapshot"], cfg.SNAP_PATH)
    for name in ("price_index", "turns", "calendar", "leads", "multiples", "evaluation"):
        write_json(out[name], cfg.MART_DIR / f"{name}.json")
    write_json(out["facts"], cfg.FACTS_PATH)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=cfg.REGISTRATION_ECOS_RUN, help="raw ECOS partition <date>/<HHMM>")
    args = ap.parse_args(argv)
    setup_logging()
    out = build(cfg.RAW_ECOS_DIR / args.run)
    write(out)
    f = out["facts"]
    log.info("DRAM: %s complete cycles; Q3 main %s (%s)", f["calendar"]["DRAM"]["n_complete_cycles"],
             f["q3"]["main_pure"]["verdict"], f["q3"]["main_pure"]["share_lead_ge1"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
