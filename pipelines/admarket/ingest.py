"""Fetch new 10-Q / 10-K filings of the twelve advertising companies and keep only what the panel needs.

Usage:
    SEC_USER_AGENT='<name> <contact email>' uv run python -m pipelines.admarket.ingest [--tickers META,GOOGL]
    uv run python -m pipelines.admarket.ingest --from-local <research data dir>   # one-off seed, no network

For each company: the submissions JSON (``filings.recent`` plus every page listed in ``filings.files``) gives the
filing index. Every 10-Q and 10-K with a report date from 2020 on that is not yet in ``data/raw/admarket/filings.json``
is fetched: the filing's ``index.json``, then its XBRL instance. The instance is parsed in memory and only its revenue
duration facts (a few dozen per filing) are written, as
``data/raw/admarket/xbrl/<TICKER>/<period>_<form>_<accession>.json.gz``. The instance itself is not kept: EDGAR is a
permanent public archive, and the twelve companies' instances since 2020 are about half a gigabyte. For Meta and
Alphabet 10-Qs the primary HTML document is also fetched and reduced to the text windows the volume/price parser
reads (``data/raw/admarket/text/...``); the parser must give the same answer on the windows as on the whole text, or
the filing is logged as a failure and nothing is written for it.

Per-filing files are immutable (one accession, one file) and never rewritten. Each company fails on its own: an error
is logged and recorded in the refresh log, the other companies still run, and the exit code is non-zero.

SEC requires a User-Agent with a contact address. ``SEC_USER_AGENT`` is an Actions secret; this module refuses to make
any request without it and never falls back to a default string or writes it anywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from pipelines.admarket import config as cfg
from pipelines.admarket import parse
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import append_jsonl, read_json_gz, utc_now, write_json, write_json_gz

log = logging.getLogger("admarket.ingest")

SUBMISSIONS = "https://data.sec.gov/submissions/{name}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"


class SecUserAgentMissing(RuntimeError):
    """Raised before any network call when SEC_USER_AGENT is unset or has no contact address."""


def sec_user_agent() -> str:
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise SecUserAgentMissing(
            "SEC_USER_AGENT is not set. The advertising pipeline needs it (an Actions secret: "
            "'<name> <contact email>'); it never falls back to a default or personal address."
        )
    if "@" not in ua:
        raise SecUserAgentMissing("SEC_USER_AGENT has no contact email in it; SEC answers 403 to such clients.")
    return ua


class SecHttp(HttpClient):
    """HttpClient plus a text GET for filing documents, under SEC's 10 requests/second limit."""

    def __init__(self, user_agent: str) -> None:
        super().__init__(min_interval=0.12, max_retries=3, timeout=90.0,
                         headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    def get_text(self, url: str) -> str:
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.get(url, headers={"Accept": "*/*"})
                self.calls += 1
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code} for {url}", request=r.request, response=r)
                r.raise_for_status()
                return r.text
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if (status is not None and 400 <= status < 500 and status != 429) or attempt == self.max_retries:
                    raise
                time.sleep(min(30.0, 2**attempt + random.uniform(0, 0.5)))
        raise RuntimeError("unreachable")


# ---- pure helpers (unit-tested) ---------------------------------------------------------------------------

def submission_rows(page: dict) -> list[dict]:
    """Rows of one submissions page (``filings.recent`` or a page from ``filings.files``)."""
    cols = ("form", "accessionNumber", "reportDate", "filingDate", "primaryDocument")
    if not all(isinstance(page.get(c), list) for c in cols):
        raise ValueError(f"submissions page lacks one of {cols}")
    n = len(page["form"])
    if any(len(page[c]) != n for c in cols):
        raise ValueError("submissions columns have different lengths")
    return [{"form": f, "accession": a, "period": r, "filed": d, "primary": p}
            for f, a, r, d, p in zip(*(page[c] for c in cols), strict=True)]


def wanted(rows: list[dict]) -> list[dict]:
    """10-Q and 10-K (no amendments) with a report date from 2020 on, one per accession."""
    seen, out = set(), []
    for r in rows:
        if r["form"] in cfg.FORMS and (r["period"] or "") >= cfg.MIN_REPORT_DATE and r["accession"] not in seen:
            seen.add(r["accession"])
            out.append(r)
    return out


def pick_instance(names: list[str]) -> str | None:
    """The XBRL instance in a filing directory: the inline-XBRL extract ``*_htm.xml`` when there is one, otherwise
    the older ``<ticker>-YYYYMMDD.xml`` (not the cal/def/lab/pre linkbases)."""
    htm = [n for n in names if n.endswith("_htm.xml")]
    if htm:
        return htm[0]
    old = [n for n in names if re.search(r"\d{8}\.xml$", n) and not re.search(r"_(cal|def|lab|pre)\.xml$", n)
           and not any(k in n for k in ("cal", "def", "lab", "pre"))]
    return old[0] if old else None


def xbrl_path(ticker: str, period: str, form: str, accession: str) -> Path:
    return cfg.RAW_XBRL / ticker / f"{period}_{form}_{accession}.json.gz"


def text_path(ticker: str, period: str, form: str, accession: str) -> Path:
    return cfg.RAW_TEXT / ticker / f"{period}_{form}_{accession}.json.gz"


def text_record(ticker: str, meta: dict, full_text: str, source: str) -> dict:
    """Snippets plus what the parser reads from them; raises if the snippets and the full text disagree."""
    snip = parse.snippets(ticker, full_text)
    on_snip, on_full = parse.parse_text(ticker, snip), parse.parse_text(ticker, full_text)
    if on_snip != on_full:
        raise ValueError(f"{ticker} {meta['period']}: snippets parse to {on_snip}, full text to {on_full}")
    return {**meta, "ticker": ticker, "source": source, "snippets": snip, "parsed": on_full}


def load_index() -> list[dict]:
    return json.loads(cfg.INDEX_PATH.read_text()) if cfg.INDEX_PATH.exists() else []


def save_index(index: list[dict]) -> None:
    index = sorted(index, key=lambda x: (x["ticker"], x["period"], x["form"], x["accession"]))
    write_json(index, cfg.INDEX_PATH)


def _rel(p: Path) -> str:
    return p.relative_to(cfg.RAW).as_posix()


# ---- network run ------------------------------------------------------------------------------------------

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
            log.warning("%s %s %s: no XBRL instance in the filing", ticker, r["form"], r["period"])
            continue
        meta = {"form": r["form"], "period": r["period"], "filed": r["filed"], "accession": r["accession"]}
        facts = parse.revenue_facts(http.get_text(base + inst))
        xp = xbrl_path(ticker, r["period"], r["form"], r["accession"])
        rec = {**meta, "ticker": ticker, "instance": inst, "source": base + inst, "extracted_at": now, "facts": facts}
        tp = None
        if ticker in cfg.TEXT_TICKERS and r["form"] == "10-Q":
            full = parse.plain_text(http.get_text(base + r["primary"]))
            trec = text_record(ticker, meta, full, base + r["primary"])
            tp = text_path(ticker, r["period"], r["form"], r["accession"])
            write_json_gz({**trec, "extracted_at": now}, tp)
        write_json_gz(rec, xp)
        added.append({"ticker": ticker, "cik": cik, **meta, "n_facts": len(facts), "xbrl": _rel(xp),
                      "text": _rel(tp) if tp else None})
        log.info("%s %s %s: %d revenue facts%s", ticker, r["form"], r["period"], len(facts), " + text" if tp else "")
    return added


def run(tickers: list[str]) -> int:
    ua = sec_user_agent()   # before any request
    http = SecHttp(ua)
    index = load_index()
    known = {x["accession"] for x in index}
    now = utc_now().isoformat(timespec="seconds")
    failed, added = {}, []
    for tk in tickers:
        try:
            new = fetch_company(http, tk, cfg.CIKS[tk], known, now)
            added += new
            known |= {x["accession"] for x in new}
        except Exception as exc:  # noqa: BLE001 - one company must not stop the others
            log.error("%s failed: %s", tk, exc)
            failed[tk] = str(exc)[:300]
    if added:
        save_index(index + added)
    append_jsonl({"at": now, "mode": "sec", "added": [f"{x['ticker']} {x['form']} {x['period']}" for x in added],
                  "failed": failed, "requests": http.calls}, cfg.REFRESH_LOG)
    log.info("added %d filings, %d companies failed, %d requests", len(added), len(failed), http.calls)
    return 1 if failed else 0


# ---- one-off seed from the research download --------------------------------------------------------------

def seed_from_local(src: Path) -> int:
    """Build the per-filing extracts from the research folder's download (``raw_xbrl/index.json`` + instances,
    ``raw_text/<TICKER>_<period>_<form>.htm``), without network access. Same extraction as the live run."""
    local = json.loads((src / "raw_xbrl" / "index.json").read_text())
    index = load_index()
    known = {x["accession"] for x in index}
    now = utc_now().isoformat(timespec="seconds")
    added = []
    for x in local:
        if x["accession"] in known or x["form"] not in cfg.FORMS or x["period"] < cfg.MIN_REPORT_DATE:
            continue
        tk = x["ticker"]
        meta = {"form": x["form"], "period": x["period"], "filed": x["filed"], "accession": x["accession"]}
        raw = (src / "raw_xbrl" / x["file"]).read_text(encoding="utf8", errors="replace")
        facts = parse.revenue_facts(raw)
        xp = xbrl_path(tk, x["period"], x["form"], x["accession"])
        write_json_gz({**meta, "ticker": tk, "instance": None, "source": "research download (EDGAR), seeded",
                       "extracted_at": now, "facts": facts}, xp)
        tp = None
        htm = src / "raw_text" / f"{tk}_{x['period']}_{x['form']}.htm"
        if tk in cfg.TEXT_TICKERS and x["form"] == "10-Q" and htm.exists():
            full = parse.plain_text(htm.read_text(encoding="utf8", errors="replace"))
            tp = text_path(tk, x["period"], x["form"], x["accession"])
            write_json_gz({**text_record(tk, meta, full, "research download (EDGAR), seeded"), "extracted_at": now}, tp)
        added.append({"ticker": tk, "cik": cfg.CIKS[tk], **meta, "n_facts": len(facts), "xbrl": _rel(xp),
                      "text": _rel(tp) if tp else None})
        known.add(x["accession"])
    save_index(index + added)
    append_jsonl({"at": now, "mode": "seed", "added": len(added)}, cfg.REFRESH_LOG)
    log.info("seeded %d filings", len(added))
    return 0


def load_filings() -> list[dict[str, Any]]:
    """Every extracted filing with its facts (and text record when there is one), oldest filing first."""
    out = []
    for x in load_index():
        rec = read_json_gz(cfg.RAW / x["xbrl"])
        text = read_json_gz(cfg.RAW / x["text"]) if x.get("text") else None
        out.append({**x, "facts": rec["facts"], "text_record": text})
    return sorted(out, key=lambda f: (f["filed"], f["accession"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=",".join(cfg.CIKS))
    ap.add_argument("--from-local", type=Path, help="seed from a local research download instead of SEC")
    args = ap.parse_args(argv)
    setup_logging()
    if args.from_local:
        return seed_from_local(args.from_local)
    try:
        return run([t.strip().upper() for t in args.tickers.split(",") if t.strip()])
    except SecUserAgentMissing as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
