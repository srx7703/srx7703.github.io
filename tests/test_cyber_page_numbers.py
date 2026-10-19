"""Guardrail for the cybersecurity page: no hand-typed numbers in prose, in digits or in words.

Strict version of the payments guard (tests/test_payments_page_numbers.py) for
site/src/content/projects/cybersecurity-landscape.mdx. tests/test_site_numbers.py only flags numbers that look
like data (two or more digits, %, $); this page is stricter: every count it states (listings, layers, companies,
analysts, deals) must come from data/facts/cyber.json or a mart through an expression, so a single-digit count or a
number word left in the prose fails here.

What the guard reads, beyond the payments one:

* the page title and summary in the frontmatter (they cannot hold an expression, so they carry no number at all);
* string attributes that are prose (``alt``, ``note``, ``heading`` ...) as well as ``title``/``subtitle``/``caption``;
* the literal text inside expressions: the static part of a template literal and a string used as a value
  (``{cond ? 'two of them' : '...'}``), because an expression is stripped from the prose and a number typed inside
  it would otherwise never be seen. Strings used as keys (``V['x_2026']``, ``=== 'Q4'``) are code and are skipped;
* multi-line ``import`` statements, which the shared ``prose()`` helper does not strip.

Only identifiers (Q1 to Q10, L1/L2, fiscal-period labels, form and standard names, company names with a digit,
years) and a short list of definitional phrases are allowed, each with the reason next to it. An entry may name a
fact of the registered design (the twelve layers): ``test_allowed_phrases_match_the_registered_design`` pins it, so
the phrase cannot go stale. Add to the tables only for a constant that defines a method, never for a result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_site_numbers import prose

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site/src/content/projects/cybersecurity-landscape.mdx"
PAGE_TS = ROOT / "site/src/charts/cyber/page.ts"
SYNC = ROOT / "site/scripts/sync-data.mjs"
FACTS = ROOT / "data/facts/cyber.json"
MARTS = ROOT / "data/marts/cyber"

# --- digits -----------------------------------------------------------------------------------------------------
# (regex, why it is not a result). Applied before the check; whatever matches is blanked out.
DIGIT_ALLOW: list[tuple[str, str]] = [
    # identifiers of the registered design (section I of docs/EVALUATION_PLAN.md)
    (r"\bQ(?:[1-9]|10)\b", "pre-registered item ids Q1 to Q10"),
    (r"\bL[12]\b", "taxonomy depth names: L1 layer, L2 sub-sector"),
    (r"\b[1-4]Q\d{2}\b", "fiscal quarter labels such as 2Q26"),
    (r"\b20\d{2}H[12]\b", "half-year labels such as 2026H1"),
    (r"\bH[12]\b", "half-year labels"),
    # method names and standards
    (r"\bRule[-\s]of[-\s]40\b", "the name of the Rule of 40 measure"),
    (r"\b(?:8-K|10-K|10-Q|20-F)\b", "SEC form names"),
    (r"\bISO\s?27001\b", "a security standard's name"),
    (r"\bSOC\s?2\b", "a security attestation's name"),
    (r"\bFIDO2\b", "an authentication standard's name"),
    (r"\b3x3\b", "a name, not a count"),
    # years (and decades): the page dates things, the date is not a result
    (r"\b(?:19|20)\d{2}s?\b", "calendar years"),
    # company and product names with a digit (each must appear in a mart: test_company_names_are_in_the_data)
    (r"\b1Password\b", "company name"),
    (r"\b7AI\b", "company name"),
    (r"\b360 Security\b", "company name (360 Security Technology)"),
    (r"\bA10\b", "company name (A10 Networks)"),
    (r"\bBorder0\b", "company name"),
    (r"\bCy4gate\b", "company name"),
    (r"\bF5\b", "company name"),
    (r"\bGroup 11\b", "investor name"),
    (r"\bHorizon3\b", "company name (Horizon3.ai)"),
    (r"\bKnowBe4\b", "company name"),
    (r"\bRapid7\b", "company name"),
    (r"\bShift5\b", "company name"),
]
DIGIT_ALLOW_RE = re.compile("|".join(f"(?:{p})" for p, _ in DIGIT_ALLOW))
DIGITS = re.compile(r"\d+")

# --- number words -----------------------------------------------------------------------------------------------
NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
    r"|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|dozen|hundred|thousand|million"
    r"|billion|trillion)\b",
    re.IGNORECASE,
)
# Definitional phrases: the horizon of a metric, the page's own structure, and company names that are number words.
# Patterns are written with \s+ where the text may wrap across lines. Case-sensitive on purpose.
WORD_ALLOW: list[tuple[str, str]] = [
    (
        r"\b(?:[Nn]ext|[Tt]railing|[Ll]ast)[-\s]+twelve[-\s]+months?\b",
        "the horizon of EV/NTM and of the trailing measures",
    ),
    (r"\b[Tt]welve[-\s]+layers?\b", "the layer tree has twelve layers (pinned to config and facts by a test below)"),
    (
        r"\b[Tt]he\s+two\s+(?:rating\s+)?vocabularies\b",
        "the page's own structure: Yahoo's buckets and East Money's counts, never merged (pinned by a test below)",
    ),
    (r"\bZero\s+One\b", "company name (Zero One Technology)"),
    (
        r"\bTwenty(?=[,.;:)’]|\s+(?:raised|is|was|has|had|and|or|at|sells|builds|valuation|round|acquired|\())",
        "company name (Twenty), recognised by what follows it so that 'Twenty companies' still fails",
    ),
]
WORD_ALLOW_RE = [re.compile(p) for p, _ in WORD_ALLOW]

# Company names that need an entry above; each must appear in a mart (otherwise the entry is a typo or stale).
COMPANY_NAMES = [
    "1Password",
    "7AI",
    "360 Security",
    "A10",
    "Border0",
    "Cy4gate",
    "F5",
    "Group 11",
    "Horizon3",
    "KnowBe4",
    "Rapid7",
    "Shift5",
    "Twenty",
    "Zero One",
]

# Attributes that are prose beyond the ones tests/test_site_numbers.py reads.
EXTRA_ATTR = re.compile(
    r'\b(?:alt|aria-label|note|text|description|heading|placeholder|legend|reason|tooltip|hint)="([^"]*)"'
)


# --- reading the page -------------------------------------------------------------------------------------------
ESM_START = re.compile(
    r"^(?:import\s*(?:\{|\*|['\"]|[\w$]+\s*(?:,|from\b))"
    r"|export\s+(?:const|let|var|function|async|default|class)\b|export\s*[{*])"
)


def strip_esm(text: str) -> str:
    """Drop every ESM block: a line starting with `import` or `export` and the lines after it up to the first blank
    line. That is where MDX itself ends an ESM block (a blank line inside one breaks the build), so it removes
    multi-line `export const` code and multi-line imports without ever reaching the prose below them."""
    out: list[str] = []
    in_esm = False
    for line in text.splitlines():
        if ESM_START.match(line):
            in_esm = True
        elif not line.strip():
            in_esm = False
        if not in_esm:
            out.append(line)
    return "\n".join(out)


def frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    return m.group(1) if m else ""


def frontmatter_prose(text: str) -> str:
    """The page title and summary: shown on the home page and in the header, and unable to hold an expression."""
    head = frontmatter(text)
    vals = [m.group(1) for m in re.finditer(r'^(?:title|summary):\s*"(.*)"\s*$', head, flags=re.M)]
    return "\n".join(vals)


_OPERAND_BEFORE = ("?", ":", "||", "??", "+", "{", "=>", "&&")


def _closing_quote(s: str, i: int) -> int:
    """Index of the quote that closes the string opened at s[i], or -1 (a string cannot span a line, so an
    apostrophe in text inside an expression is not mistaken for one)."""
    q = s[i]
    j = i + 1
    while j < len(s) and s[j] != "\n":
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j
        j += 1
    return -1


def _template(s: str, i: int, out: list[str]) -> int:
    """Scan a template literal whose backtick is at s[i-1]; collect its static text, scan each `${...}` as code."""
    buf: list[str] = []
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            out.append("".join(buf))
            return i + 1
        if c == "$" and s[i + 1 : i + 2] == "{":
            buf.append(" ")
            i = _code(s, i + 2, out)
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return i


def _code(s: str, i: int, out: list[str]) -> int:
    """Scan JS code from s[i] (just after an opening brace) to the matching closing brace; return the index past it.
    Collect the text of template literals and of strings that stand in an operand position."""
    depth = 0
    while i < len(s):
        c = s[i]
        if s.startswith("/*", i):
            end = s.find("*/", i + 2)
            i = len(s) if end < 0 else end + 2
            continue
        if c in "'\"":
            close = _closing_quote(s, i)
            if close < 0:
                i += 1
                continue
            if s[:i].rstrip().endswith(_OPERAND_BEFORE):
                out.append(s[i + 1 : close])
            i = close + 1
            continue
        if c == "`":
            i = _template(s, i + 1, out)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return i + 1
            depth -= 1
        i += 1
    return i


_TOKEN = re.compile(r"```|`|\{")
_URL = re.compile(r"https?://\S+")
# one token that mixes letters with digits or underscores is an id or a format code ('fixed1', 'usd_per_100'), not prose
_IDENTIFIER = re.compile(r"(?=.*[A-Za-z_])(?=.*\d)[\w.\-/:#]+")


def expression_literals(text: str) -> str:
    """The prose-like literal text inside every `{...}` expression of an MDX body (see the module docstring).
    Backticks outside an expression are markdown code (braces in it are literal); inside one they are template
    literals, so the text is walked left to right and each kind is told apart by where it starts. Displayed code
    (`<pre>`, `<code>`), URLs and single-token ids are not prose and are dropped."""
    text = re.sub(r"<(style|script|pre|code)\b.*?</\1>", " ", text, flags=re.S | re.I)
    out: list[str] = []
    i = 0
    while m := _TOKEN.search(text, i):
        if m.group() == "{":
            i = _code(text, m.end(), out)
        else:
            end = text.find(m.group(), m.end())
            i = len(text) if end < 0 else end + len(m.group())
    kept = [_URL.sub(" ", x) for x in out]
    return "\n".join(x for x in kept if x.strip() and not _IDENTIFIER.fullmatch(x.strip()))


def page_prose(text: str | None = None) -> str:
    """Everything a reader reads in the body, minus the ESM block: prose, string attributes, expression literals."""
    text = PAGE.read_text(encoding="utf-8") if text is None else text
    body = strip_esm(text)
    extra = "\n".join(m.group(1) for m in EXTRA_ATTR.finditer(body))
    attrs_in_expr = expression_literals(re.sub(r"^---\n.*?\n---\n", "", body, flags=re.S))
    return "\n".join([prose(body), extra, attrs_in_expr])


def _hits(pattern: re.Pattern[str], body: str) -> list[str]:
    out = []
    for m in pattern.finditer(body):
        window = body[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
        out.append(f"'{m.group(0)}' in …{window.strip()}…")
    return out


def digit_hits(body: str) -> list[str]:
    return _hits(DIGITS, DIGIT_ALLOW_RE.sub(" ", body))


def word_hits(body: str) -> list[str]:
    for rx in WORD_ALLOW_RE:
        body = rx.sub(" ", body)
    return _hits(NUMBER_WORDS, body)


# --- tests on the real page -------------------------------------------------------------------------------------
def test_the_page_exists():
    assert PAGE.exists(), f"{PAGE.relative_to(ROOT)} does not exist: the guard has nothing to guard"


def test_esm_block_is_stripped():
    body = page_prose()
    assert "export const" not in body and "facts.values" not in body and "startsWith" not in body
    assert "from '@" not in body and 'from "@' not in body


def test_no_digits_outside_identifiers():
    hits = digit_hits(page_prose())
    assert not hits, "hand-typed digits in cybersecurity prose (template them from facts):\n" + "\n".join(hits)


def test_no_number_words_outside_definitions():
    hits = word_hits(page_prose())
    assert not hits, "number words in cybersecurity prose (template the count from facts):\n" + "\n".join(hits)


def test_title_and_summary_carry_no_number():
    """Frontmatter cannot hold an expression, so the title and summary state no count at all."""
    text = frontmatter_prose(PAGE.read_text(encoding="utf-8"))
    assert text, "the frontmatter has no quoted title and summary to check"
    hits = digit_hits(text) + word_hits(text)
    assert not hits, "numbers in the frontmatter title or summary:\n" + "\n".join(hits)


# --- the allow tables themselves --------------------------------------------------------------------------------
def test_allowed_phrases_match_the_registered_design():
    """'twelve layers' and 'the two vocabularies' are allowed as the page's own structure; each is true only while
    the tree has twelve layers and the ratings come in exactly two vocabularies."""
    from pipelines.cyber import config

    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    assert len(config.LAYERS) == 12 == facts["scope"]["n_layers"], (
        "the layer tree no longer has twelve layers: change the 'twelve layers' entries in WORD_ALLOW and the prose"
    )
    recs = json.loads((MARTS / "recommendations.json").read_text(encoding="utf-8"))
    assert {r["source"] for r in recs} == {"yahoo", "eastmoney"}, (
        "the ratings no longer have exactly two vocabularies: change the 'two vocabularies' entry in WORD_ALLOW"
    )


def test_company_names_are_in_the_data():
    """An allow entry for a company name must be a name that exists, or it is a loophole for a number."""
    haystack = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MARTS.glob("*.json")))
    missing = [n for n in COMPANY_NAMES if n not in haystack]
    assert not missing, f"allow-listed names not found in data/marts/cyber: {missing}"


# --- what the page may say about the data (owner decisions D1 to D5) ---------------------------------------------
def _reader_text() -> str:
    """The page's own words: the MDX body and every string literal of page.ts (the sentences it computes)."""
    return PAGE.read_text(encoding="utf-8") + "\n" + PAGE_TS.read_text(encoding="utf-8")


def test_the_served_list_on_the_page_is_the_one_the_site_serves():
    """D1: the page prints what the deployed site serves from page.ts SERVED_MARTS; the whitelist is in sync-data.mjs."""
    script = SYNC.read_text(encoding="utf-8")
    block = re.search(r"'marts/cyber':\s*new Set\(\[(.*?)\]\)", script, flags=re.S)
    assert block, "sync-data.mjs has no whitelist for marts/cyber"
    served = set(re.findall(r"'([a-z_]+)'", re.sub(r"//[^\n]*", "", block.group(1))))
    page = re.search(r"SERVED_MARTS = \[(.*?)\] as const", PAGE_TS.read_text(encoding="utf-8"), flags=re.S)
    assert page, "page.ts has no SERVED_MARTS"
    assert set(re.findall(r"'([a-z_]+)'", page.group(1))) == served, "the page's served list differs from sync-data.mjs"
    for never in ("listings", "targets", "recommendations"):
        assert never not in served, f"{never} is a Yahoo-derived mart and must not be served (D1)"


FORBIDDEN = [
    (r"paid database|employer document|job-description|candidate (?:sheet|list)|scouting|lead file", "D2: no claim about how the company list was found"),
    (r"typed (?:by hand|by a person)|curated by hand|hand-curated", "the ledger is written by curator agents, not typed by a person"),
    (r"platform vendor|platform security (?:vendor|buyer)", "D5: the reader-facing name is 'security vendor'"),
    (r"\bregistered (?:regression|model)\b", "the page's own model is not a registered reading"),
    (r"press reports? (?:and third-party estimates )?(?:are|is) never charted", "D8: a publication-reported valuation is drawn hollow when the curator marked it chartable"),
    (r"\bdisclosure:|\bno (?:financial )?positions?\b", "D2: the page says only what the owner stated: no conflict of interest, nothing about positions"),
]


def test_the_page_makes_no_claim_the_owner_decisions_rule_out():
    text = _reader_text()
    hits = [f"{why}: '{m.group(0)}'" for pattern, why in FORBIDDEN for m in re.finditer(pattern, text, flags=re.I)]
    assert not hits, "\n".join(hits)


def test_the_disclaimer_says_what_decision_d3_requires():
    body = " ".join(PAGE.read_text(encoding="utf-8").split())
    for sentence in (
        "Data are taken as served, may be delayed or wrong, and are not audited.",
        "The author is a student researcher and not a licensed adviser.",
    ):
        assert sentence in body, f"the disclaimer lacks: {sentence}"


def test_the_summary_claims_no_more_than_the_registered_baseline():
    """D4: the dek says the layers add no visible fit in this sample and that it cannot rule a layer effect out."""
    head = frontmatter_prose(PAGE.read_text(encoding="utf-8"))
    assert "adds no visible fit" in head and "too small to rule a layer effect out" in head
    assert "not on the layer" not in head


def test_listings_tracked_by_an_earlier_page_are_named_where_the_page_says_no_yahoo_response_is_committed():
    """The cyber capture commits no Yahoo response, but the valuation job of an earlier page does for the listings it tracks; the page names
    them (page.ts OVERLAP_TICKERS) and this test recomputes the overlap from the config."""
    from pipelines.cyber.pool import ring
    from pipelines.valuation.config import OPTICAL, POWER, SSB, SURGICAL

    earlier = {c.ticker for c in OPTICAL + SSB + POWER + SURGICAL}
    overlap = sorted(e.ticker for e in ring("A") if e.ticker in earlier)
    listed = re.search(r"OVERLAP_TICKERS = \[(.*?)\]", PAGE_TS.read_text(encoding="utf-8"))
    assert listed, "page.ts has no OVERLAP_TICKERS"
    assert sorted(re.findall(r"'([^']+)'", listed.group(1))) == overlap, (
        f"ring A listings also tracked by an earlier page are {overlap}: update OVERLAP_TICKERS in page.ts and the Yahoo paragraph"
    )


# --- the guard must be able to fail ----------------------------------------------------------------------------
PLANTED = """---
title: "Twelve security layers"
summary: "Some 186 listings."
---
import facts from '@data/facts/cyber.json';
import {
  a,
  b,
} from '@charts/cyber/x';
export const n = 5;
export const rows = [
  1, 2, 3,
];

We track 12 layers, nine companies and 3 analysts.

<ChartCard title="Seven firms" subtitle={`${n} of 186 listings`} note="about 40 days" />

{n > 2 ? 'three of them' : 'none'} {V['usd_per_100'].value} {rows.find((r) => r.id === 'Q4')}
"""

CLEAN = """---
title: "Who pays for security"
summary: "Valuation against growth, by layer."
---
import facts from '@data/facts/cyber.json';
export const rows = V['x_2026'];

Twelve layers in Q1 to Q10; the Rule of 40, 1Password, F5 and Twenty raised in 2026H1 (2Q26) on next twelve months.

<ChartCard title={t} subtitle={`${n} listings, ${rows.find((r) => r.id === 'Q4')} of them`} />

{n > 2 ? 'more than a few of them' : 'none'}
"""


def test_the_guard_catches_planted_numbers():
    body = page_prose(PLANTED)
    digits = " ".join(digit_hits(body))
    words = " ".join(word_hits(body))
    for needle in ("'12'", "'3'", "'186'", "'40'"):
        assert needle in digits, f"digit {needle} not caught: {digits}"
    for needle in ("nine", "Seven", "three"):
        assert needle in words, f"word {needle} not caught: {words}"
    assert "'5'" not in digits and "'1'" not in digits, "ESM code leaked into the prose"
    fm = frontmatter_prose(PLANTED)
    assert digit_hits(fm) and word_hits(fm)


def test_the_guard_passes_clean_text():
    body = page_prose(CLEAN)
    assert not digit_hits(body), digit_hits(body)
    assert not word_hits(body), word_hits(body)
    fm = frontmatter_prose(CLEAN)
    assert not digit_hits(fm) and not word_hits(fm)


def test_a_number_word_that_is_a_name_only_passes_as_a_name():
    assert word_hits("Twenty raised a round.") == []
    assert word_hits("Twenty companies raised a round.") != []
    assert word_hits("Twenty-three companies.") != []


def test_the_conflict_of_interest_sentence_is_exactly_what_the_owner_stated():
    """D2, answered 2026-10-19: the owner stated there is no conflict of interest and that the company list is from public
    sources. The page carries that one sentence and nothing stronger."""
    body = " ".join(PAGE.read_text(encoding="utf-8").split())
    assert body.count("The author has no conflict of interest with the companies covered.") == 1
    assert len(re.findall(r"conflict of interest", body, flags=re.I)) == 1

