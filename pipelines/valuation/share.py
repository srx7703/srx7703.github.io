"""Market share on two explicitly separate bases.

**Basis A — computed.** Each company's trailing-twelve-month revenue for the track, divided by the sum
over the peer pool. Every input is a filing, so the number is reproducible and the denominator is
stated. It is a share *of this pool*, not of the world market, and the page says so: the pool is listed
companies we can source, and it leaves out private vendors (Huawei in optical, WeLion and ProLogium in
batteries) and any diversified company that does not break the track out separately.

Who enters is decided by ``Company.share_basis``, which is a question about *competition*, not about
how exciting the company is:
  * ``total`` — the company's whole revenue competes in this market (an optical module specialist, a
    cell maker);
  * ``segment`` — the track is one business inside a larger group, so a sourced and dated segment
    figure from ``data/reference/share_*.json`` is required, and without one the company is excluded;
  * ``none`` — the company is at a different layer and ``share_note`` says which. Chip and materials
    suppliers sell into the vendors, so their revenue is already inside their customers'; Fabrinet
    builds modules its customers book as their own; Toyota buys cells rather than selling them.
    Counting any of them alongside would add the same dollar to the pool twice.

**Basis B — cited.** Shares and rankings published by research houses and trade bodies, reproduced with
publisher, URL and date. Nothing here is recomputed; it is quoted. These are the numbers that cover the
private vendors basis A cannot see.

The two bases disagree, and the size of the disagreement is one of the pre-registered evaluation items.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipelines.common.storage import DATA_DIR
from pipelines.valuation.config import TRACKS, Company
from pipelines.valuation.metrics import convert

log = logging.getLogger("valuation.share")

REFERENCE_DIR = DATA_DIR / "reference"
USD = "USD"


def reference_path(track: str) -> Path:
    return REFERENCE_DIR / TRACKS[track]["reference"]


def load_reference(track: str) -> dict:
    """Read the curated reference file. A missing file is not an error: the page shows basis A only."""
    path = reference_path(track)
    if not path.exists():
        log.warning("no reference file at %s; cited share and shipments will be empty", path)
        return {"track": track, "segment_revenue": [], "cited_share": [], "shipments": [], "capacity": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("segment_revenue", "cited_share", "shipments", "capacity"):
        data.setdefault(key, [])
    return data


def segment_revenue_index(reference: dict) -> dict[str, dict]:
    """Latest disclosed track revenue per ticker, keyed by ticker."""
    out: dict[str, dict] = {}
    for row in reference.get("segment_revenue", []):
        t = row.get("ticker")
        if not t or row.get("value") is None:
            continue
        prev = out.get(t)
        if prev is None or str(row.get("period", "")) > str(prev.get("period", "")):
            out[t] = row
    return out


def _revenue_basis(company: Company, ttm: dict | None, segments: dict[str, dict]) -> tuple[float | None, str, str, str]:
    """(revenue, currency, basis, note) for one company under basis A."""
    if company.share_basis == "none":
        return None, "", "excluded", company.share_note or "not a competitor in this market"
    if company.share_basis == "total":
        rev = (ttm or {}).get("ttm_revenue")
        ccy = (ttm or {}).get("currency")
        if rev is None:
            return None, "", "excluded", "no trailing revenue on file"
        return rev, ccy, "total revenue", "the company's whole revenue competes in this market"
    seg = segments.get(company.ticker)
    if seg is not None:
        return seg["value"], seg.get("currency", USD), "disclosed segment", seg.get("basis", "segment disclosure")
    # Being out of the pool for want of a recorded figure is a different statement from being out
    # because the company does not publish one, and the page should not blur them.
    reason = {
        "yes": "publishes a revenue line for this business, but no sourced figure has been recorded here yet",
        "bundled": "reports this business bundled into a wider segment, so no clean figure exists",
        "no": "does not publish revenue for this business separately",
    }.get(company.segment_line, "segment disclosure not checked yet")
    return None, "", "excluded", reason


def computed_share(
    companies: list[Company],
    ttm_by_ticker: dict[str, dict],
    reference: dict,
    rates: dict[str, float],
) -> dict:
    """Basis A. Dual listings collapse to their primary so an issuer is counted once."""
    segments = segment_revenue_index(reference)
    seen: set[str] = set()
    members: list[dict] = []
    excluded: list[dict] = []

    for c in companies:
        if c.primary in seen:
            continue  # the A line already carries this issuer
        rev, ccy, basis, note = _revenue_basis(c, ttm_by_ticker.get(c.primary), segments)
        if rev is None:
            excluded.append({"ticker": c.ticker, "name": c.name, "reason": note, "purity": c.purity})
            continue
        usd = convert(rev, ccy, USD, rates)
        if usd is None:
            excluded.append({"ticker": c.ticker, "name": c.name, "purity": c.purity,
                             "reason": f"no exchange rate for {ccy}"})
            continue
        seen.add(c.primary)
        members.append({
            "ticker": c.ticker,
            "name": c.name,
            "name_cn": c.name_cn or None,
            "purity": c.purity,
            "revenue_usd": usd,
            "revenue_native": rev,
            "currency": ccy,
            "basis": basis,
            "note": note,
            "period_end": ((ttm_by_ticker.get(c.primary) or {}).get("period_end")
                           if basis == "total revenue" else segments.get(c.ticker, {}).get("period")),
        })

    total = sum(m["revenue_usd"] for m in members)
    for m in members:
        m["share"] = m["revenue_usd"] / total if total else None
    members.sort(key=lambda m: m["revenue_usd"], reverse=True)

    top3 = sum(m["share"] for m in members[:3]) if members else None
    top5 = sum(m["share"] for m in members[:5]) if members else None
    return {
        "members": members,
        "excluded": excluded,
        "pool_revenue_usd": total,
        "n_members": len(members),
        "top3_share": top3,
        "top5_share": top5,
        "hhi": sum((m["share"] * 100) ** 2 for m in members) if members else None,
    }


def cited_share(reference: dict) -> list[dict]:
    """Basis B, passed through unchanged. Anything without a source URL is dropped, loudly."""
    out = []
    for row in reference.get("cited_share", []):
        if not row.get("source_url"):
            log.warning("dropping cited share without a source URL: %s", row.get("entity"))
            continue
        out.append(row)
    return sorted(out, key=lambda r: (str(r.get("period", "")), str(r.get("entity", ""))), reverse=True)


def shipments_and_capacity(reference: dict) -> dict:
    """Solid-state only: what has actually shipped and what has been announced.

    The solid-state page shows this instead of a market-share chart. There is no market to take a share
    of yet, and a share chart built on a market of nearly zero would be a chart about rounding.
    """
    ship = [r for r in reference.get("shipments", []) if r.get("source_url")]
    cap = [r for r in reference.get("capacity", []) if r.get("source_url")]
    return {
        "shipments": sorted(ship, key=lambda r: str(r.get("period", "")), reverse=True),
        "capacity": sorted(cap, key=lambda r: (str(r.get("start_year", "")), str(r.get("entity", "")))),
    }
