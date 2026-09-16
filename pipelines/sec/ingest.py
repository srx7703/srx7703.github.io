"""Fetch SEC XBRL companyfacts for the SaaS universe and keep the facts we use.

Usage:
    uv run python -m pipelines.sec.ingest [--tickers DDOG,NET]

Writes:
    data/snapshots/sec/facts/<TICKER>.parquet   duration facts for mapped tags (deduped, latest filing wins)
    data/snapshots/sec/companies.json           ticker -> cik, name, fetched_at, facts per metric

SEC asks for a descriptive User-Agent and <= 10 requests/second; both are honoured. Override the
User-Agent with SEC_USER_AGENT if you want to add a contact address.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

import polars as pl

from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, append_jsonl, utc_now, write_json, write_parquet
from pipelines.sec.config import TAG_MAP, TICKERS

log = logging.getLogger("sec.ingest")

SEC_UA = os.environ.get("SEC_USER_AGENT") or "portfolio-pipelines/0.1 (github.com/srx7703)"
FACT_COLUMNS = [
    "ticker",
    "cik",
    "metric",
    "tag",
    "unit",
    "start",
    "end",
    "days",
    "val",
    "fy",
    "fp",
    "form",
    "filed",
    "frame",
    "accn",
]
FACT_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "cik": pl.Utf8,
    "metric": pl.Utf8,
    "tag": pl.Utf8,
    "unit": pl.Utf8,
    "start": pl.Utf8,
    "end": pl.Utf8,
    "days": pl.Int64,
    "val": pl.Float64,
    "fy": pl.Int64,
    "fp": pl.Utf8,
    "form": pl.Utf8,
    "filed": pl.Utf8,
    "frame": pl.Utf8,
    "accn": pl.Utf8,
}


class SecClient:
    def __init__(self) -> None:
        self.http = HttpClient(min_interval=0.12, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})

    def tickers(self) -> dict[str, tuple[str, str]]:
        data = self.http.get_json("https://www.sec.gov/files/company_tickers.json")
        return {v["ticker"].upper(): (f"{int(v['cik_str']):010d}", v["title"]) for v in data.values()}

    def companyfacts(self, cik: str) -> dict:
        return self.http.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")


def extract_facts(cf: dict, ticker: str, cik: str) -> list[dict]:
    """Duration facts (start & end) for every mapped tag, all forms, deduped on (metric, tag, start, end)."""
    gaap = cf.get("facts", {}).get("us-gaap", {})
    rows: dict[tuple, dict] = {}
    for metric, (tags, unit) in TAG_MAP.items():
        for tag in tags:
            node = gaap.get(tag)
            if not node:
                continue
            for f in node.get("units", {}).get(unit, []):
                if not f.get("start") or not f.get("end") or f.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                    continue
                s, e = date.fromisoformat(f["start"]), date.fromisoformat(f["end"])
                key = (metric, tag, f["start"], f["end"])
                row = {
                    "ticker": ticker,
                    "cik": cik,
                    "metric": metric,
                    "tag": tag,
                    "unit": unit,
                    "start": f["start"],
                    "end": f["end"],
                    "days": (e - s).days,
                    "val": float(f["val"]),
                    "fy": f.get("fy"),
                    "fp": f.get("fp"),
                    "form": f.get("form"),
                    "filed": f.get("filed"),
                    "frame": f.get("frame"),
                    "accn": f.get("accn"),
                }
                prev = rows.get(key)
                if prev is None or (row["filed"] or "") > (prev["filed"] or ""):
                    rows[key] = row  # latest filing wins (restatements)
    return list(rows.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=",".join(TICKERS))
    args = ap.parse_args(argv)
    setup_logging()
    client = SecClient()
    lookup = client.tickers()
    out_dir = SNAP_DIR / "sec" / "facts"
    companies: dict[str, dict] = {}
    failed: list[str] = []
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        if t not in lookup:
            log.warning("%s not in SEC ticker file; skipped", t)
            failed.append(t)
            continue
        cik, name = lookup[t]
        try:
            cf = client.companyfacts(cik)
        except Exception as exc:  # noqa: BLE001 - keep going for the other companies
            log.exception("%s companyfacts failed", t)
            failed.append(f"{t}: {exc!r}")
            continue
        rows = extract_facts(cf, t, cik)
        df = pl.DataFrame([{c: r.get(c) for c in FACT_COLUMNS} for r in rows], schema=FACT_DTYPES)
        write_parquet(df, out_dir / f"{t}.parquet")
        per_metric = {m: int(n) for m, n in df.group_by("metric").agg(pl.len()).iter_rows()}
        companies[t] = {
            "cik": cik,
            "name": cf.get("entityName") or name,
            "fetched_at": utc_now().isoformat(timespec="seconds"),
            "facts": df.height,
            "facts_per_metric": per_metric,
            "last_end": df["end"].max() if df.height else None,
        }
        log.info("%s: %d facts, last period end %s", t, df.height, companies[t]["last_end"])
    now = utc_now().isoformat(timespec="seconds")
    write_json({"generated_at": now, "companies": companies, "failed": failed}, SNAP_DIR / "sec" / "companies.json")
    append_jsonl({"ts": now, "ok": len(companies), "failed": len(failed)}, SNAP_DIR / "sec" / "refresh_log.jsonl")
    return 1 if failed and len(failed) == len(TICKERS) else 0


if __name__ == "__main__":
    sys.exit(main())
