"""US filers: the 10-Q / 10-K facts this page needs, read from each filing's XBRL instance.

Usage:
    SEC_USER_AGENT='<name> <contact email>' uv run python -m pipelines.servers.sec [--tickers DELL,HPE]
    uv run python -m pipelines.servers.sec build      # parse raw -> snapshots, no network

Same machinery as the advertising page (``pipelines.admarket.ingest``): the submissions index, every 10-Q and 10-K
from 2019 on not yet extracted, the filing's XBRL instance parsed in memory, and only the facts of
``config.SEC_CONCEPTS`` kept (with their dimensions, so segment revenue survives). One json.gz per accession, never
rewritten. Instances are read rather than companyfacts because companyfacts drops every dimensional fact, and the
server segments of Dell and HPE exist only as dimensional facts.

Celestica moved from Canadian (40-F / 6-K) to US domestic forms partway through the window; quarters it filed on
foreign forms are not in this table and the page says so.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from pipelines.admarket.ingest import SecHttp, pick_instance, sec_user_agent, submission_rows
from pipelines.admarket.parse import read_xbrl
from pipelines.common.log import setup_logging
from pipelines.common.storage import append_jsonl, read_json_gz, utc_now, write_json, write_json_gz, write_parquet
from pipelines.servers import config as cfg
from pipelines.servers.schema import SEC_FACTS, validate

log = logging.getLogger("servers.sec")

SUBMISSIONS = "https://data.sec.gov/submissions/{name}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"


def kept_facts(text: str) -> list[dict]:
    out = []
    for c, s, e, dims, v in read_xbrl(text):
        if c in cfg.SEC_CONCEPTS:
            out.append({"concept": c, "start": s.isoformat() if s else None, "end": e.isoformat(), "dims": dims,
                        "value": v})
    return out


def wanted(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        if r["form"] in cfg.FORMS and (r["period"] or "") >= cfg.MIN_REPORT_DATE and r["accession"] not in seen:
            seen.add(r["accession"])
            out.append(r)
    return out


def load_index() -> list[dict]:
    import json
    return json.loads(cfg.XBRL_INDEX.read_text()) if cfg.XBRL_INDEX.exists() else []


def _rel(p: Path) -> str:
    return str(p.relative_to(cfg.RAW))


def fetch_company(http: SecHttp, ticker: str, cik: int, known: set[str], now: str) -> list[dict]:
    top = http.get_json(SUBMISSIONS.format(name=f"CIK{cik:010d}.json"))
    rows = submission_rows(top["filings"]["recent"])
    for f in top["filings"].get("files", []):
        rows += submission_rows(http.get_json(SUBMISSIONS.format(name=f["name"])))
    added = []
    for r in wanted(rows):
        if r["accession"] in known:
            continue
        base = ARCHIVE.format(cik=cik, acc=r["accession"].replace("-", ""), name="")
        names = [x["name"] for x in http.get_json(base + "index.json")["directory"]["item"]]
        inst = pick_instance(names)
        if not inst:
            log.warning("%s %s %s: no XBRL instance", ticker, r["form"], r["period"])
            continue
        facts = kept_facts(http.get_text(base + inst))
        p = cfg.RAW_XBRL / ticker / f"{r['period']}_{r['form']}_{r['accession']}.json.gz"
        write_json_gz({"ticker": ticker, "form": r["form"], "period": r["period"], "filed": r["filed"],
                       "accession": r["accession"], "source": base + inst, "extracted_at": now, "facts": facts}, p)
        added.append({"ticker": ticker, "form": r["form"], "period": r["period"], "filed": r["filed"],
                      "accession": r["accession"], "n_facts": len(facts), "file": _rel(p)})
        log.info("%s %s %s: %d facts", ticker, r["form"], r["period"], len(facts))
    return added


def run(tickers: list[str]) -> int:
    http = SecHttp(sec_user_agent())
    index = load_index()
    known = {x["accession"] for x in index}
    now = utc_now().isoformat(timespec="seconds")
    failed, added = {}, []
    for tk in tickers:
        try:
            new = fetch_company(http, tk, cfg.SEC_CIKS[tk], known, now)
            added += new
            known |= {x["accession"] for x in new}
            if new:  # save as we go: a later company's failure must not lose this one's index rows
                write_json(index + added, cfg.XBRL_INDEX)
        except Exception as exc:  # noqa: BLE001 - one company must not stop the others
            log.error("%s failed: %s", tk, exc)
            failed[tk] = str(exc)[:300]
    append_jsonl({"at": now, "mode": "sec", "added": len(added), "failed": failed, "requests": http.calls},
                 cfg.REFRESH_LOG)
    return 1 if failed else 0


# ---- build: every vintage of every fact (one row per accession), so a quarter can be rebuilt on one basis ----

def build_facts() -> pl.DataFrame:
    rows: dict[tuple, dict] = {}
    for x in sorted(load_index(), key=lambda r: (r["filed"], r["accession"])):
        rec = read_json_gz(cfg.RAW / x["file"])
        for f in rec["facts"]:
            dims = ";".join(f"{k}={v}" for k, v in sorted(f["dims"].items()))
            key = (x["ticker"], f["concept"], dims, f["start"], f["end"], x["accession"])
            rows[key] = {"ticker": x["ticker"], "concept": f["concept"], "dims": dims, "start": f["start"],
                         "end": f["end"], "value": f["value"], "form": x["form"], "filed": x["filed"],
                         "accession": x["accession"]}
    df = pl.DataFrame(list(rows.values()), schema={
        "ticker": pl.Utf8, "concept": pl.Utf8, "dims": pl.Utf8, "start": pl.Utf8, "end": pl.Utf8,
        "value": pl.Float64, "form": pl.Utf8, "filed": pl.Utf8, "accession": pl.Utf8})
    return df.sort(["ticker", "concept", "dims", "end", "filed"])


def _days(s: str, e: str) -> int:
    return (date.fromisoformat(e) - date.fromisoformat(s)).days


def _vintages(sub: pl.DataFrame, concepts: tuple[str, ...]) -> dict[tuple[str, str], list[tuple[str, int, float]]]:
    """(start, end) -> [(filed, concept rank, value)] sorted oldest first. Concept order only breaks ties within one
    filing: a newer filing under a later-listed concept beats an older first print (Sanmina's FY2022 recast)."""
    rank = {c: i for i, c in enumerate(concepts)}
    out: dict[tuple[str, str], list[tuple[str, int, float]]] = defaultdict(list)
    for r in sub.iter_rows(named=True):
        out[(r["start"], r["end"])].append((r["filed"], -rank[r["concept"]], r["value"]))
    for v in out.values():
        v.sort()
    return out


def quarterly(facts: pl.DataFrame, ticker: str, concepts: tuple[str, ...], dims: str = "") -> dict[str, float]:
    """Single-quarter values keyed by period end.

    Spans of 80-100 days are quarters as filed (newest vintage). A longer span with the same start as a shorter one is
    differenced against it, which recovers fourth quarters. The shorter span is taken as it stood when the longer one
    was filed, so both sides share a basis: HPE recast its segments in fiscal 2026, and subtracting recast nine
    months from an annual total on the old definition produced a quarter on neither.
    """
    if facts.is_empty():
        return {}
    sub = facts.filter((pl.col("ticker") == ticker) & (pl.col("dims") == dims) & pl.col("concept").is_in(concepts)
                       & pl.col("start").is_not_null())
    vint = _vintages(sub, concepts)
    latest = {k: v[-1] for k, v in vint.items()}
    q: dict[str, float] = {}
    by_start: dict[str, list[str]] = defaultdict(list)
    for (s, e), (_f, _r, v) in latest.items():
        by_start[s].append(e)
        if 80 <= _days(s, e) <= 100:
            q.setdefault(e, v)
    for s, ends in by_start.items():
        ends.sort()
        for e1, e2 in zip(ends, ends[1:], strict=False):
            if not 80 <= _days(e1, e2) <= 100 or e2 in q:
                continue
            f2, _r2, v2 = latest[(s, e2)]
            earlier = [x for x in vint[(s, e1)] if x[0] <= f2]
            if earlier:
                q[e2] = v2 - earlier[-1][2]
    return dict(sorted(q.items()))


def instant(facts: pl.DataFrame, ticker: str, concepts: tuple[str, ...]) -> dict[str, float]:
    if facts.is_empty():
        return {}
    sub = facts.filter((pl.col("ticker") == ticker) & (pl.col("dims") == "") & pl.col("concept").is_in(concepts)
                       & pl.col("start").is_null())
    rank = {c: i for i, c in enumerate(concepts)}
    best: dict[str, tuple[str, int, float]] = {}
    for r in sub.iter_rows(named=True):
        cand = (r["filed"], -rank[r["concept"]], r["value"])
        if r["end"] not in best or cand > best[r["end"]]:
            best[r["end"]] = cand
    return {e: v[2] for e, v in sorted(best.items())}


def build() -> None:
    facts = validate(build_facts(), SEC_FACTS)
    write_parquet(facts, cfg.SNAP / "sec_facts.parquet")
    log.info("sec_facts: %d rows, %d tickers", facts.height, facts["ticker"].n_unique() if facts.height else 0)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="fetch", choices=["fetch", "build"])
    ap.add_argument("--tickers", default="")
    a = ap.parse_args(argv)
    rc = 0
    if a.what == "fetch":
        tickers = [t for t in a.tickers.split(",") if t] or list(cfg.SEC_CIKS)
        rc = run(tickers)
    build()
    return rc


if __name__ == "__main__":
    sys.exit(main())
