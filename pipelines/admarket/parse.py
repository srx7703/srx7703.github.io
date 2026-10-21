"""Parsers: XBRL instance -> revenue facts, and 10-Q text -> volume/price changes.

Ported from the research (``code/xbrl.py``, ``code/volume_price.py``). The XBRL reader is a regex reader on purpose:
it needs contexts (period + explicit dimension members) and numeric facts, nothing else, and it reads every instance
format the twelve filers have used since 2020 (inline-XBRL ``_htm.xml`` and the older ``-YYYYMMDD.xml``).
"""

from __future__ import annotations

import html
import re
from datetime import date

from pipelines.admarket.config import REV

# ---- XBRL -------------------------------------------------------------------------------------------------
_CTX = re.compile(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', re.S)
_MEM = re.compile(r'<xbrldi:explicitMember dimension="([^"]+)">([^<]+)</xbrldi:explicitMember>')
_TYPED = re.compile(r"<xbrldi:typedMember")
_START = re.compile(r"<(?:xbrli:)?startDate>([^<]+)<")
_END = re.compile(r"<(?:xbrli:)?endDate>([^<]+)<")
_INST = re.compile(r"<(?:xbrli:)?instant>([^<]+)<")
_FACT = re.compile(r'<([\w-]+:[\w-]+)\b([^>]*?)contextRef="([^"]+)"([^>]*)>([^<]*)</\1>')


def _d(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


def read_xbrl(text: str) -> list[tuple[str, date | None, date, dict[str, str], float]]:
    """Every numeric fact as (concept, start, end, dims, value). Instant facts have start None; contexts with a
    typed member are skipped. Concept and dimension names lose their namespace prefix."""
    ctx: dict[str, tuple[date | None, date, dict[str, str]]] = {}
    for cid, body in _CTX.findall(text):
        if _TYPED.search(body):
            continue
        s, e, i = _START.search(body), _END.search(body), _INST.search(body)
        dims = {k.split(":")[-1]: v.split(":")[-1] for k, v in _MEM.findall(body)}
        end = _d(e.group(1)) if e else _d(i.group(1))
        ctx[cid] = (_d(s.group(1)) if s else None, end, dims)
    facts = []
    for concept, _a1, cid, _a2, val in _FACT.findall(text):
        if cid not in ctx:
            continue
        try:
            v = float(val.strip())
        except ValueError:
            continue
        s, e, dims = ctx[cid]
        facts.append((concept.split(":")[-1], s, e, dims, v))
    return facts


def revenue_facts(text: str) -> list[dict]:
    """The facts the panel can use: revenue concepts over a duration, as JSON-ready dicts (what the repo keeps)."""
    out = []
    for c, s, e, dims, v in read_xbrl(text):
        if c in REV and s is not None:
            out.append({"concept": c, "start": s.isoformat(), "end": e.isoformat(), "dims": dims, "value": v})
    return out


# ---- 10-Q text --------------------------------------------------------------------------------------------
_CELL = r"\(?(?:\d+|\u2014|\u2013)\)?\s*%"  # an em or en dash is a zero ("—%")
GOOGLE_LABELS = {
    "google_search_clicks": ("Paid clicks change", "Cost-per-click change"),
    "google_network": ("Impressions change", "Cost-per-impression change"),
}
META_ANCHORS = ("price per ad", "ad impressions", "number of ads")
_META_SUBJECT = {
    "volume": r"(?:ad impressions|number of ads) (?:delivered )?(?:across our Family of Apps )?",
    "price": r"average price per ad ",
}
_META_OVERVIEW = {
    "volume": r"(?:[Aa]d impressions|[Nn]umber of ads) (?:delivered )?(?:across our Family of Apps )?",
    "price": r"[Aa]verage price per ad ",
}
_META_LEAD =r"During the three (?:and (?:six|nine) )?months ended [A-Za-z]+ \d+, 20\d\d ?, (?:the )?"
_CHANGE = r"(increased|decreased) by (\d+)%"
SNIPPET_SEP = " ¶¶ "   # cannot occur inside a matched sentence or a table row


def plain_text(raw: str) -> str:
    """Filing HTML to one line of text: tags dropped, entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def _signed(tok: str, word: str | None = None) -> int:
    digits = re.sub(r"\D", "", tok)
    if not digits:  # "—%": Alphabet prints a zero change as a dash
        return 0
    v = int(digits)
    neg = tok.strip().startswith("(") or word == "decreased"
    return -v if neg else v


def google_table(t: str, vol_label: str, price_label: str) -> tuple[int, int] | None:
    """Three-month (volume, price) changes from one Alphabet table. Older tables carry two years of columns (the
    prior-year change first), so the header decides which column is the current quarter."""
    i = t.find(vol_label)
    if i < 0:
        return None
    head = t[max(0, i - 300):i]
    head = head[head.rfind("Three Months Ended"):]
    years = re.findall(r"20\d\d", head)
    col = 1 if len(years) >= 2 and years[0] != years[1] else 0

    def row(label: str) -> int:
        j = t.find(label, i) + len(label)
        return _signed(re.findall(_CELL, t[j:j + 40])[col])

    return row(vol_label), row(price_label)


def meta_changes(t: str) -> dict[str, int]:
    """Ad impressions and average price per ad, three-month change. Two wordings: the results section ("During the
    three months ended June 30, 2025, ad impressions ... increased by 11%") and, in some 2022 filings only, the
    executive overview ("Ad impressions ... in the third quarter of 2022 increased by 17%").

    The research built the overview pattern by slicing the first character off the results pattern, which for the
    volume pattern produced an unbalanced parenthesis; it never ran because every filing matched the results wording
    for volume. Here the overview pattern is written out (subject capitalised at the start of a sentence)."""
    out: dict[str, int] = {}
    for key, subject in _META_SUBJECT.items():
        m = re.search(_META_LEAD + subject + _CHANGE, t)
        if not m:
            m = re.search(_META_OVERVIEW[key] + r"in the \w+ quarter of 20\d\d " + _CHANGE, t)
        if m:
            out[key] = _signed(m.group(2), m.group(1))
    return out


def parse_text(ticker: str, t: str) -> dict[str, dict[str, int]]:
    """series -> {volume, price} for one 10-Q's text (or its kept snippets)."""
    if ticker == "META":
        r = meta_changes(t)
        return {"meta_ads": r} if r else {}
    out = {}
    for series, labels in GOOGLE_LABELS.items():
        r = google_table(t, *labels)
        if r:
            out[series] = {"volume": r[0], "price": r[1]}
    return out


def _windows(t: str, anchors: list[tuple[int, int]]) -> str:
    """Merge [start, end) windows in document order and join them with SNIPPET_SEP."""
    merged: list[list[int]] = []
    for a, b in sorted(anchors):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return SNIPPET_SEP.join(t[a:b] for a, b in merged)


def snippets(ticker: str, t: str) -> str:
    """The part of a 10-Q's text the parser needs, small enough to commit. For Alphabet: from 400 characters before
    the first volume label (the table header) to 3,000 after it, per table. For Meta: 400 characters either side of
    every mention of an anchor phrase. ``parse_text`` gives the same result on the snippets as on the full text;
    ``tests/test_parsers.py`` checks the windowing and ingest checks it on every new filing."""
    wins: list[tuple[int, int]] = []
    if ticker == "META":
        low = t.lower()
        for a in META_ANCHORS:
            for m in re.finditer(re.escape(a), low):
                wins.append((max(0, m.start() - 400), min(len(t), m.end() + 400)))
    else:
        for vol, _price in GOOGLE_LABELS.values():
            i = t.find(vol)
            if i >= 0:
                wins.append((max(0, i - 400), min(len(t), i + 3000)))
    return _windows(t, wins)


def quarter_of_report(period: str) -> str:
    """A 10-Q's report date (YYYY-MM-DD) to its calendar quarter, as the text parser labels rows."""
    y, mth = period[:4], int(period[5:7])
    return f"{y}Q{(mth - 1) // 3 + 1}"
