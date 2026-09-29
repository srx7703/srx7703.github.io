"""LOCAL ONLY. Rebuild the frozen stock-side table and the inputs manifest from the owner's local closes.

Replaces ``research_ref/freeze_v0.py`` and ``research_ref/prepare_inputs.py``. The closes behind the leads and
multiples come from vendors whose terms forbid redistribution (Yahoo, KRX via Naver/Daum, FinMind/TWSE mirrors,
kabudragon, archived pages) and this repo is public, so they stay under ``MEMCYCLE_INPUTS`` on the owner's
machine. Only derived months, leads, ratios and flags come back into the repo.

The research code fixed three inputs by writing new files next to the raw ones. Here the fixes are transforms
applied in memory, so the local raw folder is only ever read:

* **Elpida**: the verifier's full 89-month kabudragon series replaces the partial month-end merge.
* **Qimonda**: partial month-end closes merged with the monthly highs and lows from the SEC 424B2; months with
  only a high keep ``close`` blank, which makes any window containing them incomplete.
* **Korea**: since the KRX after-market launch on 2026-09-14, Daum and Naver "closes" are after-market last
  trades. From ``KRX_OFFICIAL_FROM`` the official regular-session close (Yahoo's) replaces them.

Each fix is rebuilt byte for byte as the research code wrote it, so its sha256 can be checked against the
manifest entry of the file the research code produced.

Run from the repo root, on the owner's machine:
    MEMCYCLE_INPUTS=/path/to/raw_local/raw python -m pipelines.memcycle.freeze --check   # compare, write nothing
    MEMCYCLE_INPUTS=/path/to/raw_local/raw python -m pipelines.memcycle.freeze           # rewrite the frozen files
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from pipelines.memcycle import config as cfg
from pipelines.memcycle import ingest, metrics
from pipelines.memcycle import transform as T
from pipelines.memcycle.publish import main_run, runs

MERGED_FIELDS = ["ticker", "entity", "month", "close", "high", "low", "month_end_date", "currency", "adj_basis",
                 "source", "notes"]


def inputs_dir() -> Path:
    p = os.environ.get("MEMCYCLE_INPUTS")
    if not p:
        raise SystemExit("MEMCYCLE_INPUTS is not set; freeze.py runs only where the local closes are")
    return Path(p)


def _csv_bytes(fields: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------------------------
# input fixes (in memory)
# ---------------------------------------------------------------------------------------------


def merged_delisted(raw: Path, name: str) -> bytes:
    """Partial month-end closes + monthly highs/lows, as research_ref/prepare_inputs.py wrote them."""
    closes = {r["month"]: r for r in _rows(raw / "delisted" / f"{name}_monthend_close_partial.csv")}
    hl = {r["period"]: r for r in _rows(raw / "delisted" / f"{name}_quarterly_hl.csv")
          if r["period_type"].startswith("month")}
    ref = next(iter(closes.values()))
    out = []
    for m in sorted(set(closes) | set(hl)):
        c, h = closes.get(m, {}), hl.get(m, {})
        out.append({"ticker": ref["ticker"], "entity": ref["entity"], "month": m,
                    "close": c.get("close", ""), "high": h.get("high", ""), "low": h.get("low", ""),
                    "month_end_date": c.get("month_end_date", ""), "currency": ref["currency"],
                    "adj_basis": ref["adj_basis"],
                    "source": " | ".join(filter(None, [c.get("source"), h.get("source")])),
                    "notes": " | ".join(filter(None, [c.get("notes"),
                                                      h.get("period_type") and f"hl:{h['period_type']}"]))})
    return _csv_bytes(MERGED_FIELDS, out)


def korea_official(raw: Path, ticker: str) -> bytes:
    base = _rows(raw / "kr" / f"{ticker}_monthly.csv")
    yahoo = {r["month"]: r for r in _rows(raw / "kr" / f"{ticker}_yahoo_monthly.csv")}
    out = []
    for r in base:
        if r["month"] >= cfg.KRX_OFFICIAL_FROM and r["month"] in yahoo:
            y = yahoo[r["month"]]
            r = {**r, "close": y["close"], "high": y["high"], "low": y["low"],
                 "source": "Yahoo (official regular-session close after the 2026-09-14 after-market launch)",
                 "notes": (r.get("notes") or "") + " | replaced by Yahoo regular-session close"}
        out.append(r)
    return _csv_bytes(list(base[0].keys()), out)


def fixed_inputs(raw: Path) -> dict[str, bytes]:
    """Relative path -> bytes of every file the research code derived from the raw inputs."""
    elpida = raw / "delisted_verify" / "elpida_monthly_from_kabudragon.csv"
    return {
        "delisted/elpida_full_monthly.csv": elpida.read_bytes(),
        "delisted/qimonda_merged_monthly.csv": merged_delisted(raw, "qimonda"),
        "kr/000660_monthly_official.csv": korea_official(raw, "000660"),
        "kr/005930_monthly_official.csv": korea_official(raw, "005930"),
    }


class Inputs:
    """Reads a relative input path from the fixed files first, then from the raw folder."""

    def __init__(self, raw: Path):
        self.raw = raw
        self.fixed = fixed_inputs(raw)

    def bytes(self, rel: str) -> bytes | None:
        if rel in self.fixed:
            return self.fixed[rel]
        p = self.raw / rel
        return p.read_bytes() if p.exists() else None

    def monthly(self, rel: str) -> metrics.Px | None:
        b = self.bytes(rel)
        return None if b is None else metrics.parse_monthly(b.decode())

    def fx(self) -> dict:
        out = {}
        for cur, (fn, usd_per_unit) in cfg.FX.items():
            last = {}
            for r in _rows(self.raw / fn):
                v = r.get(fn[5:-4]) or list(r.values())[1]
                if v in ("", "."):
                    continue
                last[r[list(r)[0]][:7]] = float(v)  # chronological rows: the last observation in a month wins
            out[cur] = (last, usd_per_unit)
        return out


# ---------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------


def company_cycles(inp: Inputs) -> tuple[list[dict], list[str]]:
    snap = ingest.snapshot()
    all_runs = runs(snap)
    cal = {p: main_run(all_runs, p)["turns"] for p in ("DRAM", "NAND")}
    fx = inp.fx()
    bench = {mk: [(lab, inp.monthly(fn)) for lab, fn in lst] for mk, lst in cfg.BENCH.items()}
    rows, missing = [], []
    for c in cfg.COMPANIES:
        px = inp.monthly(c.local_file)
        if px is None:
            missing.append(f"{c.key} ({c.local_file})")
            continue
        if c.key in cfg.LAST_USE:
            px = {m: v for m, v in px.items() if m <= cfg.LAST_USE[c.key]}
        have = metrics.closes(px)
        latest, first = max(have), min(have)
        delisted = latest < cfg.DATA_END
        for prod, turns in cal.items():
            if prod == "NAND" and not c.nand_panel:
                continue
            for cyc in T.cycles_from(turns):
                r = metrics.analyse_company(px, cyc, latest, first, delisted)
                r.update({"product": prod, "company": c.key, "label": c.label, "group": c.group,
                          "dram_maker": c.dram_maker, "market": c.market, "currency": c.currency,
                          "first_month": first, "last_month": latest, "delisted_or_out_of_scope": delisted})
                if r.get("status") == "ok":
                    metrics.add_usd_and_bench(r, px, cyc, c.currency, latest, fx, bench[c.market])
                rows.append(r)
    return rows, missing


def frozen_bytes(rows: list[dict]) -> bytes:
    keep = [f for f in metrics.FIELDS if f not in cfg.CLOSE_COLUMNS]
    fmt = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in keep} for r in rows]
    return _csv_bytes(keep, fmt)


def manifest(inp: Inputs, research_head: str, scripts: dict) -> dict:
    files = ({"ecos/ecos_402Y016.csv"} | {c.local_file for c in cfg.COMPANIES}
             | {f for lst in cfg.BENCH.values() for _, f in lst} | {fn for fn, _ in cfg.FX.values()}
             | set(cfg.FIX_INPUTS))
    out = []
    for f in sorted(files):
        b = inp.bytes(f)
        if b is None:
            out.append({"path": f, "present": False})
            continue
        entry = {"path": f, "present": True, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        if f.endswith(".csv"):
            rs = list(csv.DictReader(io.StringIO(b.decode())))
            entry["rows"] = len(rs)
            key = "month" if rs and "month" in rs[0] else ("period" if rs and "period" in rs[0] else None)
            if key:
                ms = sorted(r[key] for r in rs if r.get(key))
                entry["first"], entry["last"] = ms[0], ms[-1]
            for k in ("source", "adj_basis", "currency"):
                if rs and k in rs[0]:
                    entry[k] = rs[0][k][:200]
        out.append(entry)
    return {"note": "Local inputs (not redistributed; not re-verifiable in CI). Paths are relative to MEMCYCLE_INPUTS.",
            "research_repo_head": research_head, "research_scripts_sha256": scripts, "inputs": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed files; write nothing")
    args = ap.parse_args(argv)
    inp = Inputs(inputs_dir())
    rows, missing = company_cycles(inp)
    new_frozen = frozen_bytes(rows)
    old = json.loads(cfg.MANIFEST_PATH.read_text())
    new_manifest = manifest(inp, old["research_repo_head"], old["research_scripts_sha256"])
    if args.check:
        ok = True
        if new_frozen != cfg.FROZEN_PATH.read_bytes():
            print("frozen table differs from", cfg.FROZEN_PATH)
            ok = False
        if new_manifest["inputs"] != old["inputs"]:
            diff = [a["path"] for a, b in zip(new_manifest["inputs"], old["inputs"], strict=False) if a != b]
            print("manifest differs:", diff or "entry count")
            ok = False
        print("missing:", missing)
        print("OK" if ok else "DIFFERENT")
        return 0 if ok else 1
    cfg.FROZEN_PATH.write_bytes(new_frozen)
    cfg.MANIFEST_PATH.write_text(json.dumps(new_manifest, indent=1))
    print("wrote", cfg.FROZEN_PATH, "and", cfg.MANIFEST_PATH, "; missing:", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
