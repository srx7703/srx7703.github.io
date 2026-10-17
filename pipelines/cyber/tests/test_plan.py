"""The pinned constants, the pool and every registered threshold must match the text of section I.

A constant is checked in the sentence that uses it, not anywhere in the section: changing ``Q1["min_n"]`` from 15 to
16 must fail even though the section contains a 16 somewhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipelines.cyber import amendments
from pipelines.cyber import config as C
from pipelines.cyber import frozen as F
from pipelines.cyber.pool import BY_TICKER, POOL
from pipelines.valuation.config import COMPANIES

PLAN = Path(__file__).resolve().parents[3] / "docs" / "EVALUATION_PLAN.md"
ROW = re.compile(r"^\| (\w+) \| ([AB]) \| (high|main|partial) \| (.+) \|$", re.M)
LISTING = re.compile(r"^`([^`]+)` (.+)$")


def section_i() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## Cybersecurity (project I)")
    nxt = text.find("\n## ", start + 10)
    return text[start : nxt if nxt != -1 else None]


def flat(text: str) -> str:
    """One line, single spaces, so a sentence that wraps across lines is still one sentence."""
    return re.sub(r"\s+", " ", text)


def item(n: int) -> str:
    h = section_i()
    start = h.index(f"**Q{n} ", h.index("### Items"))
    nxt = h.find("\n**Q", start + 5)
    end = nxt if nxt != -1 else h.index("### Described, not registered")
    return flat(h[start:end])


def _strings(obj, keys: bool = False):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if keys:
                yield from _strings(k)
            yield from _strings(v, keys)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, keys)


def _registered_table() -> dict[str, tuple[str, str, str, str]]:
    """Ticker -> (ring, layer, purity, name) as the universe table in section I says it."""
    out: dict[str, tuple[str, str, str, str]] = {}
    for layer, ring, purity, cell in ROW.findall(section_i()):
        for entry in cell.split("; "):
            m = LISTING.match(entry.strip())
            assert m, f"unreadable listing: {entry!r}"
            ticker, name = m.group(1), m.group(2)
            assert ticker not in out, f"{ticker} is listed twice in the universe table"
            out[ticker] = (ring, layer, purity, name)
    return out


def test_section_exists_with_the_headings_the_other_sections_have():
    h = section_i()
    for heading in (
        "### Amendments to this section",
        "### Items",
        "### Universe (pinned)",
        "### Pinned measurement choices",
    ):
        assert heading in h


def test_every_pinned_string_is_registered():
    h = section_i()
    layer_text = [(code, v["name"], v["l2"]) for code, v in C.LAYERS.items()]
    pinned = [
        layer_text, C.CHINA_ONLY_L2, C.PURITY, C.PLACEMENT_BASIS, C.STATUS, C.QSTATUS, C.METRIC_KINDS,
        C.NEVER_CHARTED_KINDS, C.NTM_WEIGHT, C.GROWTH, C.FCF_MARGIN, C.RULE_OF_40, C.UPSIDE, C.DISPERSION,
        C.EV_SOURCE, C.EM_RATING_WINDOW, C.DISCOUNT_BAND, C.BASELINE, C.BASELINE_FILE_DATE, C.FIRST_SNAPSHOT,
        C.WINDOW_LAST, C.BOOTSTRAP_DRAWS, C.SEED, C.Q8_JPY_PER_USD, C.Q1_Q2_BASELINE, C.FROZEN_Q4_Q5, C.FROZEN_Q7_CN,
        C.FROZEN_Q8, C.Q1, C.Q2, C.Q3, C.Q4, C.Q5, C.Q6, C.Q7, C.Q8, C.Q9, C.Q10,
    ]  # fmt: skip
    keyed = [C.DISCOUNT_BY_ROUND_YEAR, C.COMPARABLES_BY_LAYER]  # their keys are registered text too
    found = {s for obj in pinned for s in _strings(obj)} | {s for obj in keyed for s in _strings(obj, keys=True)}
    missing = sorted(s for s in found if s not in h and s not in flat(h))
    assert not missing, f"constants not in section I: {missing}"


def test_sentences_the_text_capitalises_are_registered():
    for sentence in (C.TRAILING_SOURCE, C.FY0_END):
        assert flat(sentence[0].upper() + sentence[1:]) in flat(section_i())


def _thresholds() -> dict[str, list[str]]:
    q1, q2, q3, q4, q5, q6, q7, q8, q9, q10 = (getattr(C, f"Q{i}") for i in range(1, 11))
    w1, w2 = q5["windows"]
    reg = F.registered_pool()
    a = [e for e in reg.values() if e.ring == "A"]
    n_cn = sum(e.ticker.endswith((".SS", ".SZ")) for e in a)
    return {
        "universe": [
            f"{len(a)} ring A listings ({len(a) - n_cn} outside China, {n_cn} in China), "
            f"{sum(e.ring == 'B' for e in reg.values())} ring B and {sum(e.ring == 'F' for e in reg.values())} ring F",
            f"The formal series starts with the Monday run of {C.FIRST_SNAPSHOT}",
            f"Fewer than {C.THIN_COVERAGE_BELOW} analysts",
            f"at least {C.CN_MEDIAN_MIN_BROKERS} East Money 2026E brokers",
            f"at least {C.CN_MEDIAN_MIN_N} companies",
            f"the {C.WINDOW_WEEKS} Monday snapshots from {C.FIRST_SNAPSHOT} to {C.WINDOW_LAST}",
            f"fewer than {C.MIN_PRICED_SHARE} of ring A was priced",
            f"With fewer than {C.MIN_SNAPSHOTS} snapshots in the window",
            f"at least {C.PANEL_MIN_SHARE} of the snapshots there are ({C.PANEL_MIN_WEEKS} of {C.WINDOW_WEEKS})",
            f"with {C.BOOTSTRAP_DRAWS} draws and seed {C.SEED}",
            f"fewer than {q1['merge_below']} panel members merged into one `other` group",
            f"the minimum below is {q1['min_n']}",
            f"{len(C.Q1_Q2_BASELINE)} companies:",
            f"{len(C.FROZEN_Q4_Q5)} companies",
            f"{len(C.FROZEN_Q7_CN)} companies",
            f"{len(C.FROZEN_Q8)} companies",
            f"enterprise value between {C.Q8['ev_floor']} and {C.Q8['ev_cap']}",
            f"JPY {C.Q8_JPY_PER_USD} per dollar",
            f"at least {q4['min_opinions']} target-price analysts",
            f"at least {q4['min_opinions']} analysts",
        ],
        "q1": [
            f"raises adjusted R² by at least {q1['uplift_holds']}",
            f"one-sided p of at most {q1['p_holds']}",
            f"upper end of the {q1['boot_level']} bootstrap interval for the uplift is below {q1['uplift_holds']}",
            f"the panel has fewer than {q1['min_n']} companies",
            f"Graded: {q1['first']}",
        ],
        "q2": [
            f"has R² of at least {q2['r2_holds']}",
            f"R² is at least {q2['r2_holds']}, the {q2['level']} bootstrap interval for b lies wholly above zero",
            f"the panel has fewer than {q2['min_n']} companies",
            f"Graded: {q2['first']}",
        ],
        "q3": [
            f"at or above {q3['cut']} is at least {q3['ratio_holds']} times",
            f"the {q3['level']} bootstrap interval for the ratio lies wholly below {q3['ratio_holds']}.",
            f"either group has fewer than {q3['min_group_n']} companies",
            f"Graded: {q3['first']}",
        ],
        "q4": [
            f"at the {q4['first_snapshot']} snapshot",
            f"to the close of {q4['window_end']}",
            f"is below +{q4['upper_bound']}.",
            f"gives p below {q4['p']}.",
            f"fewer than {q4['min_n']} companies of the list",
            f"Graded: {q4['graded']}",
        ],
        "q5": [
            f"Window 1 runs from {w1[0]} to {w1[1]} and window 2 from {w2[0]} to {w2[1]}",
            f"annualised with {q5['trading_days']} days",
            f"on the {q5['graded']} reading",
            f"its {q5['level']} interval lies wholly above zero",
            f"fewer than {q5['min_n']} companies in the pooled sample",
            f"Graded: {q5['graded']}",
            f"Window 1 alone is read on {q5['interim']}",
        ],
        "q6": [
            f"at least {q6['holds']} have",
            f"{q6['falsified']} or fewer are above",
            f"fewer than {q6['min_scored']} scored companies",
            f"primary round dated {q6['first_round_year']} or later",
            f"ledger as it stands on {q6['ledger_frozen_on']}",
        ],
        "q7": [
            f"the {q7['level']} bootstrap interval for the China coefficient lies wholly below zero",
            f"fewer than {q7['min_n']} A-share companies in the panel",
            f"Graded: {q7['first']}",
        ],
        "q8": [
            f"at least {q8['min_deals']} companies on the Q8 list",
            f"after {q8['after']} and on or before {q8['deadline']}",
            f"Graded: {q8['deadline']}",
            f"mid-point reading is shown on {q8['midterm']}",
        ],
        "q9": [
            f"no more than {q9['widening']} higher",
            "snapshots of " + ", ".join(q9["start"]),
            f"ending {q9['end_last']}",
            f"fewer than {q9['min_rest']} companies in either period",
            f"Graded: {q9['graded']}",
            ", ".join(f"`{t}`" for t in q9["trio"]),
        ],
        "q10": [
            f"are both {q10['move']} or more in size",
            "next pair starts at t+5",
            f"(the {q10['level']} level)",
            f"fewer than {q10['min_pairs']} tested pairs on {q10['last']}",
            f"on or after {q10['first']} with at least {q10['min_pairs']} tested pairs, and not later than {q10['last']}",
        ],
    }


def _found(phrase: str, body: str) -> bool:
    """The phrase, as a whole: one that ends in a digit must not run on into a longer number (0.3 is not 0.35)."""
    pattern = re.escape(flat(phrase))
    if phrase[-1].isdigit():
        pattern += r"(?!\d|\.\d)"
    return re.search(pattern, body) is not None


@pytest.mark.parametrize("where", ["universe", *[f"q{i}" for i in range(1, 11)]])
def test_every_threshold_is_registered_in_the_sentence_that_uses_it(where: str) -> None:
    body = flat(section_i()) if where == "universe" else item(int(where[1:]))
    missing = [phrase for phrase in _thresholds()[where] if not _found(phrase, body)]
    assert not missing, f"{where}: not in the registered sentence: {missing}"


def test_all_ten_questions_and_their_three_outcomes_are_registered():
    for i in range(1, 11):
        body = item(i)
        assert body.startswith(f"**Q{i} ")
        assert "Undecidable:" in body and ("Falsified:" in body) and ("Holds:" in body)


def test_the_registered_pool_file_is_the_universe_table_including_names():
    registered = _registered_table()
    frozen_file = {
        t: (e.ring, e.layer, e.purity, e.name) for t, e in F.registered_pool().items() if e.ring in ("A", "B")
    }
    assert registered == frozen_file


def test_the_live_pool_departs_from_the_registered_one_only_by_amendment():
    """pool.py is live; pool_registered.json is not. A move is allowed only in the direction a corporate event takes a
    company, or for a ticker listed in amendments.py, and every amended ticker appears in the section's Amendments."""
    order = {"listed": 0, "pending_acquisition": 1, "acquired": 2, "delisted": 2}
    registered = F.registered_pool()
    amended_text = section_i().split("### Amendments to this section")[1]
    for ticker, was in registered.items():
        now = BY_TICKER.get(ticker)
        assert now is not None, f"{ticker} left the pool; that is an amendment"
        if ticker not in amendments.MOVED:
            assert (now.layer, now.purity, now.name) == (was.layer, was.purity, was.name), ticker
        if now.status != was.status and was.ring != "F":
            assert order.get(now.status, -1) >= order.get(was.status, 0), f"{ticker}: status moved backwards"
        if now.ring != was.ring:
            assert (was.ring, now.ring) == ("A", "F") and now.status in ("acquired", "delisted"), ticker
    assert set(BY_TICKER) - set(registered) == set(amendments.ADDED)
    for ticker in (*amendments.MOVED, *amendments.ADDED):
        assert f"`{ticker}`" in amended_text, f"{ticker} is in amendments.py but not in the section's Amendments"


def test_frozen_lists_are_registered_verbatim_and_are_in_the_pool():
    h = section_i()
    reg = F.registered_pool()
    for frozen in (C.Q1_Q2_BASELINE, C.FROZEN_Q4_Q5, C.FROZEN_Q7_CN, C.FROZEN_Q8):
        assert "`" + ", ".join(frozen) + "`" in flat(h)
        assert len(set(frozen)) == len(frozen)
        for t in frozen:
            assert reg[t].ring == "A" and reg[t].status == "listed", t
    assert len(C.FROZEN_Q4_Q5) >= int(C.Q4["min_n"]) and len(C.Q1_Q2_BASELINE) >= int(C.Q1["min_n"])
    assert all(reg[t].purity == "high" for t in (*C.Q1_Q2_BASELINE, *C.FROZEN_Q7_CN, *C.FROZEN_Q8))
    assert not any(t.endswith((".SS", ".SZ")) for t in C.FROZEN_Q4_Q5), "A-shares have their own panel"
    assert sum(reg[t].purity == "main" for t in C.FROZEN_Q4_Q5) == 10, "the section says ten of them are main"


def test_every_ring_a_listing_is_priced_by_exactly_one_track():
    ring_a = {e.ticker for e in POOL if e.ring == "A"}
    cyber = {c.ticker for c in COMPANIES if c.track == "cyber"}
    elsewhere = {c.ticker for c in COMPANIES if c.track != "cyber"}
    assert cyber | (ring_a & elsewhere) == ring_a and not cyber & elsewhere
    assert ring_a & elsewhere == {"CSCO"}, "the section names Cisco as the one listing another page prices"
    for c in COMPANIES:
        if c.track == "cyber":
            assert c.layer == BY_TICKER[c.ticker].layer and c.purity == BY_TICKER[c.ticker].purity in ("high", "main")


def test_dates_are_in_the_right_order():
    assert C.BASELINE < C.BASELINE_FILE_DATE <= C.FIRST_SNAPSHOT
    assert C.REGISTERED < C.FIRST_SNAPSHOT == C.FORMAL_SERIES_FROM
    assert C.Q8["after"] == C.BASELINE


def test_non_listed_statuses_carry_a_date():
    for e in POOL:
        if e.status != "listed":
            assert re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", e.status_date), e.ticker


def test_the_statements_first_registered_without_a_source_have_one_in_the_amendments():
    """Four statements of the registered text name no public source (the sale processes at RPD and 4704.T, the Wiz and
    Armis multiples of ARR). The dated amendment names an outlet, a date and a page for each and says why it was made."""
    text = section_i()
    amended = re.sub(r"\s+", " ", text.split("### Amendments to this section")[1])
    assert "(none yet)" not in amended and "2026-10-17" in amended
    assert "name no public source" in amended and "changes no threshold, population, list or reading" in amended
    sources = {
        "Rapid7 (RPD)": ("investing.com/news", "1 February 2023", "28 October 2024"),
        "Trend Micro (4704.T)": ("teiss.co.uk", "9 August 2024", "theprint.in", "13 February 2025"),
        "Wiz ARR": ("techcrunch.com/2026/03/11/", "11 March 2026", "techcrunch.com/2024/10/23/", "23 October 2024"),
        "Armis ARR and price": ("newsroom.servicenow.com", "23 December 2025"),
    }
    for what, needles in sources.items():
        for needle in needles:
            assert needle in amended, f"{what}: the amendment does not give {needle!r}"
    for statement in ("sale processes", "secondary-sourced", "a reported ARR of more than $340M"):
        assert statement in text.split("### Amendments to this section")[0], "the registered text itself is unchanged"
