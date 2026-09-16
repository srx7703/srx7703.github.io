"""Market share on two explicitly separate bases.

**Basis A — computed.** Each company's revenue for the track, divided by the sum over the peer pool.
Every input is a filing, so the number is reproducible and the denominator is stated. It is a share
*of this pool*, not of the world market, and the page says so: the pool is listed companies we can
source, and it leaves out private vendors (Huawei in optical, WeLion and ProLogium in batteries) and
any diversified company that does not break the track out separately.

The numerator is **revenue for this track**, and there are exactly two ways to get one:

  * a **disclosed segment** figure, read off a filing and recorded with its publisher, URL and date in
    ``data/reference/share_*.json``. This always wins when one exists, whatever ``share_basis`` says,
    because a filer's own segment note is a better answer to "how much of this company competes here"
    than its consolidated revenue can ever be;
  * the company's **total revenue**, which is honest only when the whole company really does compete
    in this market — an optical module specialist, a cell maker.

``Company.share_basis`` decides what happens when there is no segment figure, and it is a question
about *competition*, not about how exciting the company is:
  * ``total`` — fall back to consolidated revenue, because the track is the business;
  * ``segment`` — the track is one business inside a larger group, so without a sourced figure the
    company is excluded and listed as excluded, with the reason it is out;
  * ``none`` — the company is at a different layer and ``share_note`` says which. Chip and materials
    suppliers sell into the vendors, so their revenue is already inside their customers'; Fabrinet
    builds modules its customers book as their own; Toyota buys cells rather than selling them.
    Counting any of them alongside would add the same dollar to the pool twice.

Every member carries the ``basis`` it entered on and the period that figure covers, and every
exclusion carries a ``reason_class`` as well as a sentence, so the page can say which listing was
measured how instead of implying one rule applied to all of them.

**Basis B — cited.** Shares and rankings published by research houses and trade bodies, reproduced with
publisher, URL and date. Nothing here is recomputed; it is quoted. These are the numbers that cover the
private vendors basis A cannot see. A quoted row reaches a page only if it names a publisher, a URL and
a publication date *and* the curator recorded confidence in it; the rest are held back with the reason,
because "re-read the page before publishing" is a note to ourselves, not a citation.

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

# How a pool member's revenue was measured. These strings reach the page, beside the bar.
BASIS_TOTAL = "total revenue"
BASIS_SEGMENT = "disclosed segment"

# Why a company is not in the computed pool. The classes exist so a count can be split into clauses
# that are each true; the per-row `reason` is the sentence a reader sees.
REASON_OTHER_LAYER = "other_layer"
REASON_NO_DISCLOSURE = "no_disclosure"
REASON_NOT_RECORDED = "not_recorded"
REASON_NO_DATA = "no_data"

EXCLUSION_CLAUSES = {
    REASON_OTHER_LAYER: "as suppliers or contract manufacturers at another layer",
    REASON_NO_DISCLOSURE: "for not disclosing revenue for this track separately",
    REASON_NOT_RECORDED: "for want of a sourced revenue figure for this track",
    REASON_NO_DATA: "for want of a usable revenue figure",
}

# A quoted figure may be published only with all three of these and a confidence the curator stood
# behind. `low` means the adversarial pass did not re-read the page; that is a backlog note, not a
# citation, and it does not belong on a public page.
QUOTED_KINDS = ("cited_share", "shipments", "capacity")
REQUIRED_QUOTE_FIELDS = ("source_name", "source_url", "publish_date")
PUBLISHABLE_CONFIDENCE = ("high", "medium")
_FIELD_WORDS = {"source_name": "publisher", "source_url": "URL", "publish_date": "publication date"}


def reference_path(track: str) -> Path:
    return REFERENCE_DIR / TRACKS[track]["reference"]


def load_reference(track: str) -> dict:
    """Read the curated reference file. A missing file is not an error: the page shows basis A only."""
    path = reference_path(track)
    if not path.exists():
        log.warning("no reference file at %s; cited share and shipments will be empty", path)
        return {"track": track, "segment_revenue": [], "cited_share": [], "shipments": [], "capacity": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("segment_revenue", *QUOTED_KINDS):
        data.setdefault(key, [])
    return data


def segment_revenue_index(reference: dict) -> dict[str, dict]:
    """Latest disclosed track revenue per ticker, keyed by ticker.

    Rows follow the shape documented in ``data/reference/README.md``: ``ticker``, a numeric ``value``
    with its ``currency``, the ``period`` it covers, a ``basis`` naming where in the filing it was
    read, and the usual publisher / URL / date. A row without a ticker or a value is skipped, and the
    latest ``period`` wins when a company has more than one.
    """
    out: dict[str, dict] = {}
    for row in reference.get("segment_revenue", []):
        t = row.get("ticker")
        if not t or row.get("value") is None:
            continue
        prev = out.get(t)
        if prev is None or str(row.get("period", "")) > str(prev.get("period", "")):
            out[t] = row
    return out


def _excluded(reason_class: str, reason: str) -> dict:
    return {"revenue": None, "currency": "", "basis": "excluded", "note": reason,
            "period": None, "reason_class": reason_class}


def _revenue_basis(company: Company, ttm: dict | None, segments: dict[str, dict]) -> dict:
    """How one company enters basis A: a revenue figure with its basis and period, or why it is out."""
    if company.share_basis == "none":
        return _excluded(REASON_OTHER_LAYER, company.share_note or "not a competitor in this market")

    # A sourced figure for *this track* beats consolidated revenue whatever `share_basis` says.
    # `share_basis="total"` is a claim that the whole company competes here, and for a diversified
    # filer that claim is an overstatement its own segment note contradicts: Coherent's group revenue
    # is its Datacenter & Communications segment plus an industrial laser business that competes with
    # nobody in this pool. Where the reference file records the segment, the segment is used.
    seg = segments.get(company.ticker) or segments.get(company.primary)
    if seg is not None:
        return {
            "revenue": seg["value"],
            "currency": seg.get("currency") or USD,
            "basis": BASIS_SEGMENT,
            "note": seg.get("basis") or "revenue disclosed for this business",
            "period": seg.get("period"),
            "reason_class": None,
        }

    if company.share_basis == "total":
        rev = (ttm or {}).get("ttm_revenue")
        if rev is None:
            return _excluded(REASON_NO_DATA, "no trailing revenue on file")
        if rev <= 0:
            # A pre-revenue developer has nothing to divide, and a zero in the numerator would put a
            # 0% bar on the chart as if it were a measured share rather than an absent one.
            return _excluded(REASON_NO_DATA, "reports no revenue over the trailing twelve months")
        return {
            "revenue": rev,
            "currency": (ttm or {}).get("currency"),
            "basis": BASIS_TOTAL,
            "note": "the company's whole revenue competes in this market",
            "period": (ttm or {}).get("period_end"),
            "reason_class": None,
        }

    # Being out of the pool for want of a recorded figure is a different statement from being out
    # because the company does not publish one, and the page must not blur them. Every sentence here
    # has to be true of the company as well as of us: we do not know what a listing marked "unknown"
    # discloses, so the sentence says only what we hold.
    reason, reason_class = {
        "yes": ("publishes a revenue line for this business, but no sourced figure has been recorded here yet",
                REASON_NOT_RECORDED),
        "bundled": ("reports this business bundled into a wider segment, so no clean figure exists",
                    REASON_NO_DISCLOSURE),
        "no": ("does not publish revenue for this business separately", REASON_NO_DISCLOSURE),
    }.get(
        company.segment_line,
        ("no sourced revenue figure for this business is recorded for this listing", REASON_NOT_RECORDED),
    )
    return _excluded(reason_class, reason)


def one_listing_per_issuer(companies: list[Company]) -> list[Company]:
    """One row per issuer, preferring the primary line.

    A dual-listed issuer is two listings with one set of accounts, so it must contribute one member —
    and, just as much, one *exclusion*: counting YOFC's A and H lines as two excluded companies
    inflates the "N listed companies are excluded" sentence the page prints.
    """
    by_issuer: dict[str, Company] = {}
    order: list[str] = []
    for c in companies:
        if c.primary not in by_issuer:
            by_issuer[c.primary] = c
            order.append(c.primary)
        elif c.ticker == c.primary:
            by_issuer[c.primary] = c  # the primary line wins even if a secondary was seen first
    return [by_issuer[p] for p in order]


def computed_share(
    companies: list[Company],
    ttm_by_ticker: dict[str, dict],
    reference: dict,
    rates: dict[str, float],
) -> dict:
    """Basis A. Dual listings collapse to their primary so an issuer is counted once, in or out."""
    segments = segment_revenue_index(reference)
    members: list[dict] = []
    excluded: list[dict] = []

    for c in one_listing_per_issuer(companies):
        got = _revenue_basis(c, ttm_by_ticker.get(c.primary), segments)
        rev, ccy = got["revenue"], got["currency"]
        if rev is None:
            excluded.append({"ticker": c.ticker, "name": c.name, "purity": c.purity,
                             "reason": got["note"], "reason_class": got["reason_class"]})
            continue
        usd = convert(rev, ccy, USD, rates)
        if usd is None:
            excluded.append({"ticker": c.ticker, "name": c.name, "purity": c.purity,
                             "reason": f"no exchange rate for {ccy}", "reason_class": REASON_NO_DATA})
            continue
        members.append({
            "ticker": c.ticker,
            "name": c.name,
            "name_cn": c.name_cn or None,
            "purity": c.purity,
            "revenue_usd": usd,
            "revenue_native": rev,
            "currency": ccy,
            "basis": got["basis"],
            "note": got["note"],
            "period_end": got["period"],
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
        "basis_counts": {b: len([m for m in members if m["basis"] == b]) for b in (BASIS_SEGMENT, BASIS_TOTAL)},
        "excluded_counts": {k: len([e for e in excluded if e["reason_class"] == k]) for k in EXCLUSION_CLAUSES},
        "top3_share": top3,
        "top5_share": top5,
        "hhi": sum((m["share"] * 100) ** 2 for m in members) if members else None,
    }


def quote_block(row: dict) -> str | None:
    """Why this quoted row may not be published, or None if it may."""
    missing = [_FIELD_WORDS[f] for f in REQUIRED_QUOTE_FIELDS if not row.get(f)]
    if missing:
        return "no " + ", ".join(missing)
    confidence = str(row.get("confidence") or "unrecorded").lower()
    if confidence not in PUBLISHABLE_CONFIDENCE:
        return f"confidence recorded as {confidence}"
    return None


def _split_quotes(reference: dict, kind: str) -> tuple[list[dict], list[dict]]:
    """(publishable, held back) for one quoted block of the reference file. Pure; `withheld_quotes` logs."""
    ok: list[dict] = []
    held: list[dict] = []
    for row in reference.get(kind, []):
        why = quote_block(row)
        if why is None:
            ok.append(row)
            continue
        held.append({"kind": kind, "entity": row.get("entity"), "metric": row.get("metric"), "reason": why})
    return ok, held


# Fields that stay behind. `data/facts/*.json` is copied verbatim into the site's public directory, so a
# field that exists only to talk to the next curator is world-readable the moment it is written: notes
# saying "re-read the page before publishing" or "the adversarial check found these had been fused"
# are addressed to us and read, to anyone else, as doubt about the number beside them. The
# reader-facing half of the same thought is `caveat`, which the page renders.
INTERNAL_FIELDS = frozenset({"curator_notes", "publish_date_raw", "original_publisher_notes", "review_notes"})


def public(row: dict) -> dict:
    """One cited row with the curator's working notes stripped out."""
    return {k: v for k, v in row.items() if k not in INTERNAL_FIELDS}


def cited_share(reference: dict) -> list[dict]:
    """Basis B, passed through unchanged. Rows that fail the publication bar are held back, loudly."""
    ok, _ = _split_quotes(reference, "cited_share")
    return [
        public(r)
        for r in sorted(ok, key=lambda r: (str(r.get("period", "")), str(r.get("entity", ""))), reverse=True)
    ]


def withheld_quotes(reference: dict) -> list[dict]:
    """Every quoted row held back from the page, across all three blocks, with the reason."""
    held: list[dict] = []
    for kind in QUOTED_KINDS:
        held.extend(_split_quotes(reference, kind)[1])
    for row in held:
        log.warning("holding back %s row %r: %s", row["kind"], row["entity"], row["reason"])
    return [public(r) for r in held]


def shipments_and_capacity(reference: dict) -> dict:
    """Solid-state only: what has actually shipped and what has been announced.

    The solid-state page shows this instead of a market-share chart. There is no market to take a share
    of yet, and a share chart built on a market of nearly zero would be a chart about rounding.
    """
    ship, _ = _split_quotes(reference, "shipments")
    cap, _ = _split_quotes(reference, "capacity")
    return {
        "shipments": [public(r) for r in sorted(ship, key=lambda r: str(r.get("period", "")), reverse=True)],
        "capacity": [
            public(r) for r in sorted(cap, key=lambda r: (str(r.get("start_year", "")), str(r.get("entity", ""))))
        ],
    }
