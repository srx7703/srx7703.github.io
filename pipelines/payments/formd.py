"""SEC Form D monitor: priced private raises and tenders filed by the private companies in the universe.

An auxiliary signal for the private-company ledger (PLAN §3 M4 and M7, EXECUTION W2e). A Form D is the notice
a US issuer files within 15 days of first selling securities under Regulation D. It states the offering size
and the amount sold so far, and nothing about valuation. It is a ``counterparty_filing``-grade fact about the
*raise*, filed by the company itself, so the ledger can cite it; it is never read as revenue or a valuation.
Many rounds of non-US issuers (Checkout.com, Airwallex, Nium, Rapyd) are not filed in the US at all, so an
empty result is "no US Form D", not "no raise".

Sources (public; SEC asks for a descriptive User-Agent with a contact address)
-----------------------------------------------------------------------------
* Company submissions JSON, when the CIK is pinned::

      GET https://data.sec.gov/submissions/CIK0001691342.json
      {"cik": "0001691342", "name": "Stripe, Inc.",
       "filings": {"recent": {"accessionNumber": [...], "filingDate": [...], "form": ["D", "D/A", ...],
                              "primaryDocument": ["xslFormDX01/primary_doc.xml", ...]}, "files": [...]}}

* EDGAR full-text search otherwise (discovers the CIK; hits are filtered by the issuer's legal name, so a
  fund that merely names "Stripe" in its own Form D never matches)::

      GET https://efts.sec.gov/LATEST/search-index?q="Stripe, Inc."&forms=D&from=0
      {"hits": {"total": {"value": 15}, "hits": [{"_id": "0001691342-17-000001:primary_doc.xml",
        "_source": {"ciks": ["0001691342"], "display_names": ["Stripe, Inc. (CIK 0001691342)"],
                    "file_date": "2017-03-30", "form": "D", "adsh": "0001691342-17-000001", ...}}]}}

* The filing itself, raw XML (the ``xslFormDX01/`` prefix in ``primaryDocument`` is the rendered view)::

      GET https://www.sec.gov/Archives/edgar/data/1691342/000169134223000002/primary_doc.xml
      ... <offeringSalesAmounts><totalOfferingAmount>6869866984</totalOfferingAmount>
          <totalAmountSold>6869866984</totalAmountSold> ... </offeringSalesAmounts>

``totalOfferingAmount`` can be the word ``Indefinite``; that row keeps a null amount and
``offering_indefinite = true``.

Contact rule
------------
``SEC_USER_AGENT`` (an Actions secret, "<name> <contact email>") is required and checked before any network
call; there is no fallback to a default string or to anyone's personal address (EXECUTION §0.7).

Failure isolation (CLAUDE.md rule 5)
------------------------------------
Every company fails on its own (submissions, then full-text search as a fallback), and every filing document
fails on its own: a filing whose XML cannot be read keeps its row with null amounts and is counted in a check.

Usage (runs in GitHub Actions; this container has no outbound network)::

    SEC_USER_AGENT='name contact@example.org' uv run python -m pipelines.payments.formd [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx
import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, uniqueness
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, RAW_DIR, run_stamp, utc_now, write_json_gz, write_parquet
from pipelines.payments.config import UNIVERSE

log = logging.getLogger("payments.formd")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"
FORMS = ("D", "D/A")
EFTS_PAGE = 100       # the search API's fixed page size
EFTS_MAX_HITS = 500   # per company; a legal-name phrase never has more Form D hits than this


@dataclass(frozen=True)
class Issuer:
    """A universe company's US Form D issuer: full-text query phrase, legal-name regex, CIK when verified."""
    query: str
    name: str
    cik: str | None = None


#: Private companies in config.UNIVERSE. The CIK is pinned only when it was read on EDGAR ([V]); the rest are
#: discovered by full-text search and the legal-name regex (``display_names`` start with the entity name).
#: Public filers (10-K/20-F) and acquired entities with ambiguous names (Bridge, Tempo) are not monitored.
ISSUERS: dict[str, Issuer] = {
    "Stripe": Issuer('"Stripe, Inc."', r"^Stripe, Inc\.", cik="0001691342"),  # [V] EDGAR, 2026-10-04
    "Checkout.com": Issuer('"Checkout.com"', r"^Checkout\b"),
    "Rapyd": Issuer('"Rapyd"', r"^Rapyd\b"),
    "Highnote": Issuer('"Highnote"', r"^Highnote\b"),
    "Ramp": Issuer('"Ramp Business Corporation"', r"^Ramp Business Corp"),
    "Brex": Issuer('"Brex"', r"^Brex,? Inc\b"),
    "Mercury": Issuer('"Mercury Technologies"', r"^Mercury Technologies"),
    "Rippling": Issuer('"People Center, Inc."', r"^People Center"),
    "Airwallex": Issuer('"Airwallex"', r"^Airwallex\b"),
    "Ebury": Issuer('"Ebury"', r"^Ebury\b"),
    "Nium": Issuer('"Nium"', r"^Nium\b"),
    "Lithic": Issuer('"Lithic"', r"^Lithic\b"),
    "Unit": Issuer('"Unit Finance"', r"^Unit Finance\b"),
    "BVNK": Issuer('"BVNK"', r"^BVNK\b"),
}

_UNIVERSE_NAMES = {c for layer in UNIVERSE.values() for group in layer.values() for c in group}
_unknown = sorted(set(ISSUERS) - _UNIVERSE_NAMES)
if _unknown:
    raise ValueError(f"Form D issuers outside config.UNIVERSE: {_unknown}")


class SecUserAgentMissing(RuntimeError):
    """Raised before any network call when SEC_USER_AGENT is unset."""


def sec_user_agent() -> str:
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise SecUserAgentMissing(
            "SEC_USER_AGENT is not set. The Form D monitor needs it (an Actions secret: "
            "'<name> <contact email>'); it never falls back to a default or personal address."
        )
    if "@" not in ua:
        raise SecUserAgentMissing("SEC_USER_AGENT has no contact email in it; SEC answers 403 to such clients.")
    return ua


# --- client ----------------------------------------------------------------------------------


class SecClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...
    def get_text(self, url: str) -> str: ...


class SecHttp(HttpClient):
    """HttpClient plus a text GET for filing XML, under SEC's 10 requests/second limit."""

    def __init__(self, user_agent: str) -> None:
        super().__init__(min_interval=0.12, max_retries=3,
                         headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    def get_text(self, url: str) -> str:
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.get(url, headers={"Accept": "application/xml, text/xml, */*"})
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


# --- parse -----------------------------------------------------------------------------------


def _cik10(cik: str | int) -> str:
    return f"{int(cik):010d}"


def filing_url(cik: str, accession: str, doc: str = "primary_doc.xml") -> str:
    doc = doc.rsplit("/", 1)[-1]  # drop the xslFormDX01/ rendering prefix: we want the raw XML
    return ARCHIVE_URL.format(cik_int=int(cik), acc_nodash=accession.replace("-", ""), doc=doc)


def parse_submissions(payload: Any, cik: str) -> list[dict]:
    """Form D / D/A filings from a submissions JSON (``filings.recent``; private issuers have few filings)."""
    if not isinstance(payload, dict) or "filings" not in payload:
        raise ValueError(f"malformed submissions payload: {str(payload)[:200]}")
    if _cik10(payload.get("cik", cik)) != _cik10(cik):
        raise ValueError(f"submissions for CIK {payload.get('cik')}, asked for {cik}")
    recent = payload["filings"].get("recent") or {}
    cols = ["accessionNumber", "filingDate", "form"]
    if not all(isinstance(recent.get(c), list) for c in cols):
        raise ValueError("filings.recent lacks accessionNumber/filingDate/form")
    n = len(recent["accessionNumber"])
    if any(len(recent[c]) != n for c in cols):
        raise ValueError("filings.recent columns have different lengths")
    docs = recent.get("primaryDocument") or ["primary_doc.xml"] * n
    out = []
    for acc, fdate, form, doc in zip(recent["accessionNumber"], recent["filingDate"], recent["form"], docs,
                                     strict=False):
        if form in FORMS:
            out.append({"cik": _cik10(cik), "accession": acc, "filing_date": date.fromisoformat(fdate),
                        "form": form, "doc": doc or "primary_doc.xml"})
    return out


def parse_efts(payload: Any, name_regex: str) -> tuple[list[dict], int]:
    """Full-text-search hits whose issuer name matches ``name_regex`` -> filings, plus the total hit count."""
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), dict):
        raise ValueError(f"malformed search payload: {str(payload)[:200]}")
    total = int(((payload["hits"].get("total") or {}).get("value")) or 0)
    rx = re.compile(name_regex, re.I)
    out = []
    for h in payload["hits"].get("hits") or []:
        src = h.get("_source") or {}
        form = src.get("form") or src.get("file_type")
        if form not in FORMS:
            continue
        doc = str(h.get("_id", "")).split(":", 1)[-1] or "primary_doc.xml"
        # A filing can list several entities; keep the CIK whose display name is the issuer.
        for cik, disp in zip(src.get("ciks") or [], src.get("display_names") or [], strict=False):
            if rx.search(disp.strip()):
                out.append({"cik": _cik10(cik), "accession": src["adsh"],
                            "filing_date": date.fromisoformat(src["file_date"]), "form": form, "doc": doc})
                break
    return out, total


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _amount(text: str | None) -> tuple[float | None, bool]:
    t = (text or "").strip().replace(",", "").replace("$", "")
    if not t:
        return None, False
    if t.lower() == "indefinite":
        return None, True
    return float(t), False


def parse_formd_xml(xml: str) -> dict:
    """Offering amounts and first-sale date from a Form D primary_doc.xml."""
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    _strip_ns(root)
    amounts = root.find(".//offeringSalesAmounts")
    if amounts is None:
        raise ValueError("no offeringSalesAmounts in the filing")
    offered, indefinite = _amount(amounts.findtext("totalOfferingAmount"))
    sold, _ = _amount(amounts.findtext("totalAmountSold"))
    first_sale = root.findtext(".//dateOfFirstSale/value")
    issuer = root.findtext(".//primaryIssuer/entityName") or root.findtext(".//issuer/entityName")
    return {
        "total_offering_amount": offered,
        "offering_indefinite": indefinite,
        "total_amount_sold": sold,
        "date_of_first_sale": date.fromisoformat(first_sale.strip()) if first_sale and first_sale.strip() else None,
        "issuer_name": issuer.strip() if issuer else None,
    }


# --- schema ----------------------------------------------------------------------------------

FORMD_KEY = ["company", "accession"]
FORMD_DTYPES: dict[str, pl.DataType] = {
    "company": pl.Utf8,
    "cik": pl.Utf8,
    "filing_date": pl.Date,
    "form": pl.Utf8,
    "accession": pl.Utf8,
    "total_offering_amount": pl.Float64,   # USD; null when indefinite or unread
    "offering_indefinite": pl.Boolean,
    "total_amount_sold": pl.Float64,       # USD; null when unread
    "date_of_first_sale": pl.Date,
    "source": pl.Utf8,                     # submissions | efts
    "url": pl.Utf8,
}
FORMD_SCHEMA = pa.DataFrameSchema(
    {
        "company": pa.Column(str, pa.Check.isin(list(ISSUERS))),
        "cik": pa.Column(str, pa.Check.str_matches(r"^\d{10}$")),
        "filing_date": pa.Column(pl.Date),
        "form": pa.Column(str, pa.Check.isin(list(FORMS))),
        "accession": pa.Column(str, pa.Check.str_matches(r"^\d{10}-\d{2}-\d{6}$")),
        "total_offering_amount": pa.Column(float, pa.Check.ge(0.0), nullable=True),
        "offering_indefinite": pa.Column(bool),
        "total_amount_sold": pa.Column(float, pa.Check.ge(0.0), nullable=True),
        "date_of_first_sale": pa.Column(pl.Date, nullable=True),
        "source": pa.Column(str, pa.Check.isin(["submissions", "efts"])),
        "url": pa.Column(str, pa.Check.str_startswith("https://www.sec.gov/Archives/edgar/data/")),
    },
    unique=FORMD_KEY,
    strict=True,
    coerce=False,
)


# --- fetch -----------------------------------------------------------------------------------


@dataclass
class FetchResult:
    filings: pl.DataFrame
    checks: list[dict] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def search_filings(client: SecClient, issuer: Issuer, raw: dict[str, Any], label: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    while start < EFTS_MAX_HITS:
        payload = client.get_json(EFTS_URL, params={"q": issuer.query, "forms": "D", "from": start})
        raw[f"efts_{label}_{start}"] = payload
        hits, total = parse_efts(payload, issuer.name)
        out.extend(hits)
        start += EFTS_PAGE
        if start >= total:
            break
    return out


def list_filings(client: SecClient, company: str, issuer: Issuer, res: FetchResult) -> tuple[list[dict], str]:
    """Filings for one company: submissions when the CIK is pinned, full-text search otherwise or on failure."""
    label = _slug(company)
    if issuer.cik:
        try:
            payload = client.get_json(SUBMISSIONS_URL.format(cik=_cik10(issuer.cik)))
            res.raw[f"submissions_{label}"] = payload
            return parse_submissions(payload, issuer.cik), "submissions"
        except Exception as exc:  # noqa: BLE001 - fall back to search for this company only
            log.warning("submissions for %s failed (%s); trying full-text search", company, exc)
            res.checks.append(check(f"Form D submissions: {company}", False,
                                    f"{type(exc).__name__}: {exc}"[:200] + "; used full-text search", warn=True))
    return search_filings(client, issuer, res.raw, label), "efts"


def fetch_all(client: SecClient, *, issuers: dict[str, Issuer] | None = None) -> FetchResult:
    issuers = issuers or ISSUERS
    rows: list[dict] = []
    res = FetchResult(filings=pl.DataFrame(schema=FORMD_DTYPES))
    unread_docs: list[str] = []
    for company, issuer in issuers.items():
        try:
            filings, source = list_filings(client, company, issuer, res)
        except Exception as exc:  # noqa: BLE001 - isolate every company (rule 5)
            log.error("Form D %s failed: %s", company, exc)
            res.failed.append(company)
            res.checks.append(check(f"Form D fetch: {company}", False, f"{type(exc).__name__}: {exc}"[:300]))
            continue
        seen: set[str] = set()
        ciks = sorted({f["cik"] for f in filings})
        if len(ciks) > 1:
            res.checks.append(check(f"Form D issuer: {company}", False,
                                    f"{len(ciks)} CIKs match the legal name: {', '.join(ciks)}", warn=True))
        for f in sorted(filings, key=lambda x: x["filing_date"]):
            if f["accession"] in seen:
                continue
            seen.add(f["accession"])
            url = filing_url(f["cik"], f["accession"], f["doc"])
            row = {"company": company, "cik": f["cik"], "filing_date": f["filing_date"], "form": f["form"],
                   "accession": f["accession"], "total_offering_amount": None, "offering_indefinite": False,
                   "total_amount_sold": None, "date_of_first_sale": None, "source": source, "url": url}
            try:
                parsed = parse_formd_xml(client.get_text(url))
                row.update({k: parsed[k] for k in ("total_offering_amount", "offering_indefinite",
                                                    "total_amount_sold", "date_of_first_sale")})
            except Exception as exc:  # noqa: BLE001 - keep the filing, leave amounts null
                log.warning("Form D %s %s unreadable: %s", company, f["accession"], exc)
                unread_docs.append(f"{company} {f['accession']}")
            rows.append(row)
        log.info("Form D %s: %d filings (%s)", company, len(seen), source)
    if rows:
        res.filings = pl.DataFrame(rows, schema=FORMD_DTYPES).sort(["company", "filing_date", "accession"])
    n = len(issuers)
    res.checks.insert(0, check("Form D companies", not res.failed,
                               f"{n - len(res.failed)} of {n} companies searched"
                               + (f"; failed: {', '.join(res.failed)}" if res.failed else ""),
                               warn=len(res.failed) < n))
    res.checks.append(check("Form D documents", not unread_docs,
                            f"{len(rows) - len(unread_docs)} of {len(rows)} filing documents read"
                            + (f"; unread: {', '.join(unread_docs[:5])}" if unread_docs else ""), warn=True))
    return res


def quality_checks(filings: pl.DataFrame) -> list[dict]:
    out = [uniqueness(filings, FORMD_KEY, "company filings", name="Form D key uniqueness")]
    over = filings.filter(pl.col("total_offering_amount").is_not_null() & pl.col("total_amount_sold").is_not_null()
                          & (pl.col("total_amount_sold") > pl.col("total_offering_amount")))
    out.append(check("Form D amounts", over.height == 0,
                     f"{over.height} filings with amount sold above the offering amount", warn=True))
    return out


# --- write and run ---------------------------------------------------------------------------


def write(filings: pl.DataFrame, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or MARTS_DIR / "payments"
    return write_parquet(FORMD_SCHEMA.validate(filings), out_dir / "formd.parquet")


def archive_raw(raw: dict[str, Any], root: Path | None = None) -> Path:
    day, hhmm = run_stamp(utc_now())
    base = (root or RAW_DIR) / "payments" / "formd" / day / hhmm
    for slug, payload in raw.items():
        write_json_gz(payload, base / f"{slug}.json.gz")
    return base


def run(client: SecClient | None = None, *, out_dir: Path | None = None, raw_root: Path | None = None,
        write_outputs: bool = True) -> tuple[pl.DataFrame, list[dict]]:
    own = client is None
    if own:
        client = SecHttp(sec_user_agent())  # raises before any network call when the contact is missing
    try:
        res = fetch_all(client)  # type: ignore[arg-type]
    finally:
        if own:
            client.close()  # type: ignore[union-attr]
    filings = FORMD_SCHEMA.validate(res.filings)
    checks = res.checks + quality_checks(filings)
    if write_outputs:
        if res.raw:
            archive_raw(res.raw, raw_root)
        if filings.height:
            write(filings, out_dir)
        else:
            log.error("no Form D filings found for any company; nothing written")
    return filings, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and validate, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    try:
        filings, checks = run(write_outputs=not args.dry_run)
    except SecUserAgentMissing as exc:
        log.error("%s", exc)
        return 2
    for c in checks:
        log.info("[%s] %s: %s", c["status"], c["name"], c["detail"])
    log.info("%d Form D filings across %d companies", filings.height,
             filings["company"].n_unique() if filings.height else 0)
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
