"""Page-level rules for site/src/content/projects/ad-market.mdx.

The site-wide guard (tests/test_site_numbers.py) allow-lists years and small integers. This page is stricter: no digit
may be typed by hand in prose or in a string attribute, so every share, growth rate, quarter, count and date comes
from data/facts/admarket.json or a mart through an expression. The only digits allowed are identifiers and fixed names:
the question ids (Q1 to Q6, B1 to B7) and the form names 10-Q and 10-K. Even the forecasts' interval level and the
iOS version that shipped ATT come from the facts file. Also: exactly three key figures, the disclaimer before the first figure, the
post-verdict Q3 description kept apart from the verdict, status in progress and not featured.
"""

from __future__ import annotations

import re

import pytest

from pipelines.common.storage import REPO_ROOT

PAGE = REPO_ROOT / "site" / "src" / "content" / "projects" / "ad-market.mdx"
ATTR = re.compile(r'\b(?:title|subtitle|ariaLabel|label|sub|caption|summary|note|placeholder|detail)="([^"]*)"')
ALLOWED = [r"\bQ[1-6]\b", r"\bB[1-7]\b", r"\b10-[QK]s?\b"]


@pytest.fixture(scope="module")
def text() -> str:
    return PAGE.read_text(encoding="utf-8")


def _body(text: str) -> str:
    return re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)


def _prose(text: str) -> str:
    body = _body(text)
    body = body[body.index('<div class="lede">'):]   # imports and exports all come before the lede
    attrs = "\n".join(m.group(1) for m in ATTR.finditer(body))
    for _ in range(5):
        body = re.sub(r"\{[^{}]*\}", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"`[^`]*`", " ", body)
    return body + "\n" + attrs


def test_no_hand_typed_digits_in_prose(text):
    prose = _prose(text)
    for a in ALLOWED:
        prose = re.sub(a, " ", prose)
    hits = [f"{m.group(0)!r} in …{prose[max(0, m.start() - 40): m.end() + 40]}…" for m in re.finditer(r"\d+", prose)]
    assert not hits, "\n".join(hits)


def test_frontmatter(text):
    front = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S).group(1)
    summary = re.search(r'^summary: "(.*)"$', front, flags=re.M).group(1)
    assert not re.search(r"\d", summary), summary
    assert re.search(r"^status: in-progress$", front, flags=re.M)
    assert re.search(r"^featured: false$", front, flags=re.M)


def test_exactly_three_key_figures(text):
    assert len(re.findall(r"<KpiTile\b", text)) == 3
    assert len(re.findall(r"<KpiRow\b", text)) == 1


def test_disclaimer_before_the_first_figure(text):
    body = _body(text)
    disclaimer = body.find("not investment advice")
    first_fig = min(i for i in (body.find("<ChartCard"), body.find("<VegaChart"), body.find("<KpiRow")) if i >= 0)
    assert 0 <= disclaimer < first_fig


def test_post_verdict_description_is_labelled_and_after_the_verdict(text):
    body = _body(text)
    sec = re.search(r'<Section id="open-web".*?</Section>', body, flags=re.S).group(0)
    verdict = sec.find("{q3.verdict}</strong>")
    note = sec.find("made after the verdict and is a description, not a test")
    first_use = sec.find("D.open_ex_network", sec.find("</ChartCard>"))
    assert 0 <= verdict < note < first_use


def test_video_is_cited_with_its_link(text):
    body = _body(text)
    assert body.count("V.url") >= 2   # the scope note and the source list
    assert "transcript" not in body.lower()


def test_appendix_sections(text):
    body = _body(text)
    app = body[body.index("<Appendix>"):]
    ids = re.findall(r'<Section id="([a-z-]+)"', app)
    assert ids == ["scope", "method", "limits", "evaluation", "data-quality", "sources", "build-notes", "changelog"]


def test_every_table_has_a_download(text):
    tables = re.findall(r'<DataTable id="([a-z0-9-]+)"', text)
    downloads = len(re.findall(r"<DownloadCsv\b", text))
    assert downloads >= len([t for t in tables if t != "evaluation-table"])


def test_every_chart_has_a_stamp(text):
    assert len(re.findall(r"<ChartCard\b", text)) == len(re.findall(r'<SourceStamp slot="stamp"', text))
