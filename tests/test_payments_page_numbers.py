"""Guardrail for the payments page: no hand-typed numbers in prose, in digits or in words.

tests/test_site_numbers.py only flags numbers that look like data (two or more digits, %, $) and keeps
a broad allow-list. This page is stricter: every count it states (questions, providers, companies) must
come from data/facts/payments.json or a mart through an expression, so a single-digit count or a
number word left in the prose fails here. Only identifiers (Q1, D3, H1, form names, years) and a short
list of definitional phrases are allowed.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_site_numbers import prose

PAGE = Path(__file__).resolve().parents[1] / "site/src/content/projects/payments-landscape.mdx"

DIGIT_ALLOW = re.compile(r"\bQ\d\b|\bD\d\b|\bH[12]\b|\b8-K\b|\b10-K\b|\b20-F\b|\b2\d{3}\b")
DIGITS = re.compile(r"\d+")

NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|hundred|thousand)\b",
    re.IGNORECASE,
)
# definitional phrases: units of the books (per $100), the pre-registered web slice and its smoothing,
# and fixed names of the page's own structure
WORD_ALLOW = [
    r"hundred[-\s]dollars?", r"per[-\s]hundred", r"top ten thousand", r"three-crawl",
    r"four businesses", r"Four books", r"Three signals", r"three series",
    r"Capital\s+One",  # a proper name, not a count
]


def strip_exports(text: str) -> str:
    """Drop the frontmatter-adjacent export block: every line from an `export` line up to the first
    line that starts with `<` (the page body), so multi-line `export const` code never reaches prose()."""
    out, in_export = [], False
    for line in text.splitlines():
        if re.match(r"^export\b", line):
            in_export = True
            continue
        if in_export and line.startswith("<"):
            in_export = False
        if not in_export:
            out.append(line)
    return "\n".join(out)


def page_prose() -> str:
    return prose(strip_exports(PAGE.read_text(encoding="utf-8")))


def test_export_block_is_stripped():
    body = page_prose()
    assert "export const" not in body and "facts.values" not in body and "startsWith" not in body


def test_no_digits_outside_identifiers():
    body = DIGIT_ALLOW.sub(" ", page_prose())
    hits = []
    for m in DIGITS.finditer(body):
        window = body[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
        hits.append(f"'{m.group(0)}' in …{window.strip()}…")
    assert not hits, "hand-typed digits in payments prose (template them from facts):\n" + "\n".join(hits)


def test_no_number_words_outside_definitions():
    body = page_prose()
    for phrase in WORD_ALLOW:
        body = re.sub(phrase.replace(" ", r"\s+"), " ", body)  # phrases may wrap across lines
    hits = []
    for m in NUMBER_WORDS.finditer(body):
        window = body[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
        hits.append(f"'{m.group(0)}' in …{window.strip()}…")
    assert not hits, "number words in payments prose (template the count from facts):\n" + "\n".join(hits)
