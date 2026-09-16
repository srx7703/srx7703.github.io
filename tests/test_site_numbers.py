"""Guardrail: numbers in project-page prose must come from facts/marts via expressions.

Scans site/src/content/projects/*.mdx, removes frontmatter, import/export lines, JSX expressions
`{...}`, HTML attributes and inline code, then flags numeric literals that look like data
(percentages, currency, counts >= 100, decimals) in the remaining prose. Definitional constants are
allow-listed below; add to ALLOW only for numbers that define a method, never for results.
"""

from __future__ import annotations

import re
from pathlib import Path

PAGES = sorted((Path(__file__).resolve().parents[1] / "site/src/content/projects").glob("*.mdx"))

ALLOW = [
    r"Rule of 40", r"Rule-of-40", r"\b40\b", r"\b80[–-]100 days\b", r"\b0\.5%", r"17:30 UTC", r"\b2[0-9]{3}\b",
    r"\b(25|50)\+? bps\b", r"[−-]?(25|50)\b", r"\b10-[KQ]\b", r"\b8-K\b", r"20-F", r"S&amp;P 500", r"\b60-day\b",
    r"\|z\| = 2", r"\b0\.5\b", r"\b1%\b", r"\b99%\b", r"\b2%\b", r"\b98%\b",
    r"\b(3|6|9)M\b", r"\bQ[1-4]\b", r"\bFY\b", r"\b95%\b", r"\b10 (probability )?bins\b", r"\b(top|Top)-?100\b",
    r"\b(five|four|three|two|one)\b", r"\b6-month\b", r"\bsix-month\b", r"\b(0\.95|0\.85|0\.70|0\.50)\b",
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}\b",
    r"\bv[0-9]\b", r"\b19[0-9]{2}\b", r"\d{4}-\d{2}(-\d{2})?", r"\b30B\b", r"\b(27|31)B\b",
    r"\b(rank )?8\b", r"\b0\.001\b", r"\b(p|t) (<|=|from|to)", r"\b\d{1,2}:\d{2}\b",
    # definitional constants stated in Method sections
    r"between 2% and 98%", r"2% and 98%", r"1% and 99%", r"\$1 in the", r"bootstrap 95%", r"95% interval", r"ISE 537",
    r"\b10 (cents|bps|equal|probab)", r"10-cent", r"\b(12|18)-month", r"\b(30|60)[- ]day", r"\b(20|50)\+? bps", r"\b[12]\d{3}\b", r"\bH[12]\b",
    r"\b(17|05):(30|00)", r"\bTop-?", r"\b0\.(70|75)\b", r"rank \d", r"\b(3|6|9)M\b", r"\d{2}bps", r"\b(24|48|36) h", r"starts at 0\.\d+",
]

DATA_NUMBER = re.compile(r"(?<![\w.-])(?:\$\s?\d[\d,]*(?:\.\d+)?[MBK]?|\d[\d,]*(?:\.\d+)?\s?%|\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{2,})(?![\w-])")


ATTR = re.compile(r'\b(?:title|subtitle|ariaLabel|label|sub|caption|summary)="([^"]*)"')


def prose(text: str) -> str:
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)  # frontmatter
    attrs = "\n".join(m.group(1) for m in ATTR.finditer(text))  # string attributes are prose too
    text = "\n".join(line for line in text.splitlines() if not re.match(r"^\s*(import|export)\b", line))
    # strip JSX expressions (nested braces up to 3 levels), attributes, tags, inline code
    for _ in range(3):
        text = re.sub(r"\{[^{}]*\}", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"`[^`]*`", " ", text)
    return text + "\n" + attrs


def test_no_hand_typed_data_numbers_in_prose():
    violations = []
    for page in PAGES:
        body = prose(page.read_text(encoding="utf-8"))
        for m in DATA_NUMBER.finditer(body):
            window = body[max(0, m.start() - 12) : m.end() + 12]
            if any(re.search(a, window) for a in ALLOW):
                continue
            violations.append(f"{page.name}: '{m.group(0)}' in …{window.strip()}…")
    assert not violations, "hand-typed numbers in prose (route them through facts/marts):\n" + "\n".join(violations)
