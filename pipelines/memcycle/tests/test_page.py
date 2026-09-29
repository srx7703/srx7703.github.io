"""Page-level rules for site/src/content/projects/memory-cycles.mdx.

The site-wide guard (tests/test_site_numbers.py) allow-lists every YYYY-MM and small integer, because other
pages use them as definitions. On this page nearly every number is a month, a month count, a multiple or a
signed lead, so it gets a stricter check of its own: none of those may be typed by hand in prose or in a
string attribute. Also: exactly three key figures, the disclaimer before the first figure, and the post-hoc
fragility number only inside the robustness section.
"""

from __future__ import annotations

import re

import pytest

from pipelines.common.storage import REPO_ROOT

PAGE = REPO_ROOT / "site" / "src" / "content" / "projects" / "memory-cycles.mdx"
ATTR = re.compile(r'\b(?:title|subtitle|ariaLabel|label|sub|caption|summary|note|placeholder)="([^"]*)"')

FORBIDDEN = {
    "month": re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])\b"),
    "year": re.compile(r"\b(19[89]\d|20[0-3]\d)\b"),
    "month count": re.compile(r"\b\d+\s*(-\s*)?months?\b|\b\d+[–-]\d+\s*months?\b"),
    "multiple": re.compile(r"\b\d+(\.\d+)?\s?x\b"),
    "signed lead": re.compile(r"(?<![\w.])[+−-]\d+\b"),
    "percentage": re.compile(r"\d+(\.\d+)?\s?%"),
}
# definitions, not data
ALLOWED = [r"2020 = 100", r"Bry–Boschan", r"10-K"]


@pytest.fixture(scope="module")
def text() -> str:
    return PAGE.read_text(encoding="utf-8")


def _body(text: str) -> str:
    return re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)


def _prose(text: str) -> str:
    body = _body(text)
    body = re.sub(r'<Section id="changelog".*?</Section>', " ", body, flags=re.S)  # dated entries are metadata
    body = body[body.index('<div class="lede">'):]  # imports and (multi-line) exports all come before the lede
    attrs = "\n".join(m.group(1) for m in ATTR.finditer(body))
    for _ in range(4):
        body = re.sub(r"\{[^{}]*\}", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"`[^`]*`", " ", body)
    return body + "\n" + attrs


def test_no_hand_typed_months_counts_multiples_or_leads(text):
    prose = _prose(text)
    for a in ALLOWED:
        prose = re.sub(a, " ", prose)
    hits = [f"{kind}: {m.group(0)!r} in …{prose[max(0, m.start() - 30): m.end() + 30]}…"
            for kind, rx in FORBIDDEN.items() for m in rx.finditer(prose)]
    assert not hits, "\n".join(hits)


def test_frontmatter_summary_has_no_digits(text):
    front = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S).group(1)
    summary = re.search(r'^summary: "(.*)"$', front, flags=re.M).group(1)
    assert not re.search(r"\d", summary), summary


def test_status_is_in_progress(text):
    assert re.search(r"^status: in-progress$", text, flags=re.M)


def test_exactly_three_key_figures(text):
    assert len(re.findall(r"<KpiTile\b", text)) == 3
    assert len(re.findall(r"<KpiRow\b", text)) == 1


def test_disclaimer_before_the_first_figure(text):
    body = _body(text)
    disclaimer = body.find("Public data only; nothing here is investment advice.")
    first_fig = min(i for i in (body.find("<ChartCard"), body.find("<VegaChart"), body.find("<KpiRow")) if i >= 0)
    assert 0 <= disclaimer < first_fig


def test_post_hoc_numbers_only_in_robustness(text):
    body = _body(text)
    m = re.search(r'<Section id="robustness" title="([^"]*)">(.*?)</Section>', body, flags=re.S)
    assert m and "not pre-registered" in m.group(1).lower()
    outside = body[: m.start()] + body[m.end():]
    uses = [ln for ln in outside.splitlines() if re.search(r"\bflips\b", ln) and not ln.startswith("export const")]
    assert not uses, uses
    assert "{flips}" in m.group(2)


def test_video_credit_is_one_sentence_with_the_link(text):
    body = _body(text)
    assert body.count("facts.video.url") == 2  # the scope sentence and the source list
    assert "transcript" not in body.lower()


def test_appendix_sections(text):
    body = _body(text)
    app = body[body.index("<Appendix>"):]
    ids = re.findall(r'<Section id="([a-z-]+)"', app)
    assert ids == ["scope", "method", "limits", "evaluation", "data-quality", "sources", "build-notes", "changelog"]
    assert "evaluationPlan()" in body


def test_every_table_has_a_download(text):
    tables = re.findall(r'<DataTable id="([a-z0-9-]+)"', text)
    downloads = len(re.findall(r"<DownloadCsv\b", text))
    assert downloads >= len([t for t in tables if t != "evaluation-table"])
