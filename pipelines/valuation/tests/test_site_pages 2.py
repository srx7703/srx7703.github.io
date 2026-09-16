"""Guards for the two valuation pages and the chart specs they draw from.

Every assertion here corresponds to an acceptance finding that shipped on a public page: a sentence that
contradicted the table under it, a subtitle describing an encoding the chart did not have, two companies
drawn in one colour, a spread in PE points printed with a multiple's suffix. The pages are templates, so
the defect and its fix both live in the source text — these read the real files rather than a fixture.

The pages are held in ``site/src/content/drafts/`` while the findings are fixed and move back to
``site/src/content/projects/``; both locations are searched so the tests do not care which it is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "site" / "src"
CHARTS = SITE / "charts" / "valuation"
TOKENS = SITE / "styles" / "tokens.css"
PAGE_NAMES = {"optical": "optical-modules-valuation.mdx", "ssb": "solid-state-battery-valuation.mdx"}


def page_path(track: str) -> Path:
    name = PAGE_NAMES[track]
    for folder in ("projects", "drafts"):
        candidate = SITE / "content" / folder / name
        if candidate.exists():
            return candidate
    raise AssertionError(f"{name} is in neither src/content/projects nor src/content/drafts")


def page(track: str) -> str:
    return page_path(track).read_text(encoding="utf-8")


def chart(name: str) -> str:
    return (CHARTS / f"{name}.ts").read_text(encoding="utf-8")


def prose_only(text: str) -> str:
    """The page with its frontmatter, import/export lines and JSX expressions removed.

    What is left is the text a reader sees that nothing computed for them, so a count inside ``{...}``
    counts as templated here. Only expressions that contain no markup are stripped: collapsing
    ``{cond && (<Section>…)}`` wholesale would swallow the prose inside a conditional section, which is
    exactly where "Three issuers" sat above a two-row table.
    """
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not re.match(r"^\s*(import|export|//)\b", line))
    for _ in range(5):
        stripped = re.sub(r"\{[^{}<>]*\}", " ", text)
        if stripped == text:
            break
        text = stripped
    return text


def facts(track: str) -> dict:
    return json.loads((ROOT / "data" / "facts" / f"valuation_{track}.json").read_text(encoding="utf-8"))


def mart(track: str) -> list[dict]:
    return json.loads((ROOT / "data" / "marts" / "valuation" / f"{track}_companies.json").read_text(encoding="utf-8"))


TRACKS = sorted(PAGE_NAMES)


# --- P1: the pure plays' missing multiple, stated as the data has it ---------------------------------

def test_ssb_does_not_claim_the_pure_plays_have_no_earnings():
    """The page said "None of them has earnings" while printing Microvast's 4.4x trailing PE below it."""
    text = page("ssb")
    for claim in ("has no earnings", "have no earnings", "no earnings, so none", "absent because they have no earnings"):
        assert claim not in text, f"the page asserts {claim!r}; earnings are a property of the mart, not of the label"


def test_ssb_pure_play_earnings_sentence_is_derived_from_the_data():
    text = page("ssb")
    assert "pure_plays_with_earnings" in text and "trailing_pe != null" in text, (
        "the profitable pure plays must be counted from facts or the mart, not asserted in prose"
    )
    # And the derivation has to have something to find: at least one pure play does report a profit today.
    pure = {p["ticker"] for p in (facts("ssb").get("pure_plays") or facts("ssb").get("preprofit") or [])}
    earners = [r for r in mart("ssb") if r["ticker"] in pure and r["trailing_pe"] is not None]
    assert earners, "fixture drift: no pure play reports a trailing profit, so this guard proves nothing"


def test_dumbbell_subtitles_give_the_real_reason_a_listing_is_absent():
    """Absence from the dumbbell is a missing consensus, never a missing profit."""
    for track in TRACKS:
        text = page(track)
        assert "because they have no earnings" not in text
        assert "not plotted" in text, f"{track}: the dumbbell subtitle must say how many listings are missing"


# --- P2 / P19: counts in prose come from the data ----------------------------------------------------

def test_no_hand_typed_dual_listing_count():
    """"Three issuers" sat above a two-row table; spelled-out numbers are exempt from the prose guardrail."""
    for track in TRACKS:
        text = page(track)
        typed = re.search(r"\b(one|two|three|four|five|six)\s+issuers?\b", prose_only(text), re.I)
        assert not typed, f"{track}: the dual-listing count is hand-typed ({typed.group(0)!r})"
        if "dual_listings" in text:
            assert "facts.dual_listings.length" in text, f"{track}: template the dual-listing count from facts"


def test_no_hand_typed_fiscal_year_share():
    """"Half this pool" was 14 of 34, and the sidenote is the justification for the whole restatement."""
    assert "Half this pool" not in page("optical")
    assert "fy_end_month !== 12" in page("optical"), "the off-December count must come from the mart"


def test_kpi_qualifier_does_not_call_a_consensus_count_a_price_count():
    """"15 of 34 listings priced" against a Data-quality row reading "34 of 34 priced"."""
    for track in TRACKS:
        text = page(track)
        assert "n_listings} listings priced" not in text, f"{track}: n_with_consensus is not a count of prices"
        assert "have a consensus multiple" in text


def test_share_chart_states_how_many_bars_it_draws():
    """The bar chart caps at twelve while the lede one paragraph above counted sixteen members."""
    for track in TRACKS:
        text = page(track)
        assert "shareTruncated" in text, f"{track}: the truncated bar count must be stated in the subtitle"
        assert "shareBarsSpec(share.members, SHARE_BARS_LIMIT)" in text, f"{track}: the page and the spec must cap alike"
    assert "SHARE_BARS_LIMIT" in chart("share_bars"), "the cap must be exported so the page can state it"
    optical = facts("optical")["share"]
    assert optical["n_members"] > 12, "fixture drift: the optical pool no longer overflows the chart, so this guard proves nothing"


def test_cited_rank_units_are_suppressed_whatever_they_say_after_rank():
    """`c.unit !== 'rank'` matched the exact string, so "rank (no share disclosed)" was concatenated onto a sentence."""
    for track in TRACKS:
        text = page(track)
        assert "c.unit !== 'rank'" not in text
        assert "startsWith('rank')" in text


def test_cited_share_table_separates_companies_from_market_sizes():
    """Half the "Company" column held `industry (…)` rows reporting a market size."""
    for track in TRACKS:
        assert "isIndustryRow" in page(track), f"{track}: industry-level rows need their own table"
    optical = facts("optical")["cited_share"]
    assert any(str(row["entity"]).startswith("industry") for row in optical), "fixture drift: no industry rows to split"


# --- P7: five series, five distinct colours ----------------------------------------------------------

def series_tokens() -> tuple[dict[str, str], dict[str, str]]:
    """(light, dark) maps of `--series-*` to hex, split at the first dark-mode block."""
    css = TOKENS.read_text(encoding="utf-8")
    cut = css.find("prefers-color-scheme: dark")
    light, dark = {}, {}
    for match in re.finditer(r"--series-([\w-]+):\s*(#[0-9a-fA-F]{3,8})", css):
        (light if match.start() < cut else dark)[match.group(1)] = match.group(2)
    return light, {**light, **dark}


def test_consensus_chart_colours_are_pairwise_distinct():
    """`--series-c` and `--series-kalshi` are the same hex, and slots 3 and 5 were both in use."""
    text = chart("consensus_series")
    match = re.search(r"SERIES_RANGE\s*=\s*\[([^\]]*)\]", text) or re.search(r"range:\s*\[([^\]]*)\]", text)
    assert match, "could not find the consensus colour range"
    names = re.findall(r"series\.(\w+)", match.group(1))
    assert names, "the colour range names no series tokens"
    for theme, tokens in zip(("light", "dark"), series_tokens(), strict=True):
        hexes = [tokens[n] for n in names]
        assert len(set(hexes)) == len(hexes), f"{theme}: two consensus series share a colour ({names} -> {hexes})"


def test_pages_plot_no_more_series_than_there_are_colours():
    text = chart("consensus_series")
    match = re.search(r"SERIES_RANGE\s*=\s*\[([^\]]*)\]", text)
    assert match, "the consensus range must be a named constant the page can size itself against"
    n_colours = len(re.findall(r"series\.(\w+)", match.group(1)))
    for track in TRACKS:
        body = page(track)
        assert "CONSENSUS_MAX_SERIES" in body, f"{track}: the deepest-coverage list must be capped by the chart's own limit"
        assert not re.search(r"\.slice\(0,\s*\d+\)\s*\n\s*\.map\(\(c\) => c\.ticker\)", body), (
            f"{track}: the series count is a literal, which can outrun the colour range"
        )
    assert "tickers.slice(0, CONSENSUS_MAX_SERIES)" in text, "the spec must cap the series itself, whatever the page passes"
    assert n_colours <= 3, "only --series-a/b/c are validated for non-semantic series (CHART_RULES rule 10)"


# --- P8: the quartile spread is PE points, not a multiple --------------------------------------------

def test_iqr_is_not_printed_through_the_multiple_formatter():
    """p75 - p25 = 122.90 rendered as "a spread of 122.9x"; the quartile *ratio* is 3.6x."""
    for track in TRACKS:
        text = page(track)
        assert not re.search(r"x1\(\s*facts\.iqr_fwd_pe_this\s*\)", text), (
            f"{track}: the IQR is a difference in PE points and x1 appends a multiple's suffix"
        )
        assert "spread of" not in text or "iqr" not in text


# --- P11 / P12: the chart says what the page says ----------------------------------------------------

def test_share_bar_legend_does_not_claim_the_track_is_the_business():
    """On the battery page every bar read "total revenue (the track is the business)" — CATL's is not."""
    text = chart("share_bars")
    assert "the track is the business" not in text
    assert "whole-company revenue" in text


def test_chart_source_stamps_are_not_a_fixed_slice_of_the_source_list():
    """The share chart named SEC EDGAR and FRED for a pool they supply 0.3% of."""
    for track in TRACKS:
        text = page(track)
        assert "sources.slice(" not in text, f"{track}: a chart's sources must not be positions in a fixed array"
        assert "shareSources" in text and "priceSources" in text


def test_ssb_share_chart_is_labelled_as_the_conventional_battery_pool():
    text = page("ssb")
    assert "not of solid-state" in text, "the share chart's title must say what the share is not"
    assert "Share of trailing-twelve-month revenue across the listings that disclose revenue for this business" not in text, (
        "the battery page is still carrying the optical page's subtitle verbatim"
    )


# --- P13 / rule 14: exposure in the mark, opacity for coverage ---------------------------------------

def test_hollow_marks_exist_wherever_a_subtitle_promises_them():
    dumbbell = chart("pe_dumbbell")
    scatter = chart("pe_growth_scatter")
    for track in TRACKS:
        if "hollow marks" in page(track):
            assert "filled: false" in dumbbell, "a subtitle promises hollow marks the dumbbell does not draw"
            assert "filled: false" in scatter, "a subtitle promises hollow marks the scatter does not draw"
    assert "filled: true" not in dumbbell


def test_opacity_means_the_same_thing_on_both_charts():
    """A faded mark meant "partial exposure" on one chart and "fewer than three analysts" on the next."""
    for name in ("pe_dumbbell", "pe_growth_scatter"):
        text = chart(name)
        conditions = re.findall(r"opacity:\s*\{\s*condition:\s*\{\s*test:\s*'([^']*)'", text)
        assert conditions == ["datum.thin"], f"{name}: opacity encodes {conditions}, not thin coverage"


def test_exposure_is_not_encoded_in_colour():
    """Rule 14 forbids it outright: colour is already carrying the series."""
    scatter = chart("pe_growth_scatter")
    assert not re.search(r"color:\s*\{\s*field:\s*'purity'", scatter)
    assert re.search(r"fill:\s*\{\s*field:\s*'purity'", scatter), "exposure belongs in the mark"


def test_unknown_coverage_fades_like_thin_coverage():
    """Grinm has no published analyst count, was drawn at full strength, and set the top of a headline."""
    types_ts = chart("types")
    assert "coverage === 'unknown'" in types_ts, "an unpublished broker count is not a confident consensus"
    unknown = [r for r in mart("ssb") if r["fwd_pe_2026"] is not None and r["coverage"] == "unknown"]
    assert unknown, "fixture drift: no unknown-coverage listing is priced, so this guard proves nothing"


# --- rule 13: a log axis is for a wide distribution --------------------------------------------------

def test_log_axis_is_chosen_from_the_data_not_hard_coded():
    for name in ("pe_dumbbell", "pe_growth_scatter"):
        text = chart(name)
        assert "type: 'log'" not in text.replace("type: 'log' as const", ""), f"{name}: the log axis is unconditional"
        assert "peScale(" in text


@pytest.mark.parametrize("track", TRACKS)
def test_subtitle_says_log_scale_only_when_the_axis_is_one(track):
    """The solid-state pool spans a factor of 9.4 and still drew a log axis, which flatters it."""
    pes = [r[k] for r in mart(track) for k in ("fwd_pe_2026", "fwd_pe_2027") if r[k] is not None and r[k] > 0]
    wide = (max(pes) / min(pes)) >= 10 ** 1.5
    assert "dumbbellUsesLog(companies) ? '; log scale' : ''" in page(track), (
        f"{track}: the subtitle must be templated off the same test the chart uses"
    )
    if not wide:
        assert "type: 'log'" not in chart("pe_dumbbell"), f"{track} spans {max(pes) / min(pes):.1f}x, under two orders"


# --- P14: exposure travels with the extremes ---------------------------------------------------------

def test_optical_extremes_carry_their_exposure():
    """A television maker and a laser-chip maker were the two ends of "optical module valuation"."""
    text = page("optical")
    assert "endsProse" in text and "partialEnds" in text, "the exposure of a named extreme must be read from the mart"
    assert "{ key: 'purity', label: 'Exposure' }" in text, "the optical table needs an exposure column"
    rows = {r["ticker"]: r for r in mart("optical")}
    ends = [facts("optical")[k] for k in ("cheapest", "priciest") if facts("optical").get(k)]
    assert any(rows[e["ticker"]]["purity"] != "high" for e in ends), (
        "fixture drift: neither extreme is a diversified group, so this guard proves nothing"
    )


def test_pool_gets_cheaper_claim_is_gated_on_the_data():
    """"every listing gets cheaper on 2027" was asserted whenever a cheapest and a priciest existed."""
    for track in TRACKS:
        text = page(track)
        assert "and every listing gets cheaper on ${y1}`" not in text
        assert "cheapenClause" in text, f"{track}: the claim must be counted, not asserted"


# --- P15: the optical page shows the comparison it pre-registers -------------------------------------

def test_optical_page_renders_the_dual_listing_section():
    text = page("optical")
    assert 'Section id="dual"' in text, "the page pre-registers the A/H comparison and never showed it"
    assert facts("optical")["dual_listings"], "fixture drift: no dual listing to show"


def test_a_wholly_null_comparison_column_says_why():
    for track in TRACKS:
        assert "d.premium == null" in page(track) and "The comparison column is empty" in page(track)


# --- P16 / P17 / P18: the claims the appendix makes --------------------------------------------------

def test_pages_do_not_claim_the_rules_predate_the_first_snapshot():
    """The plan, both facts files and every snapshot landed in one commit."""
    for track in TRACKS:
        text = page(track)
        assert "before the first snapshot" not in text
        assert "cannot be tuned to the answer" not in text
        assert "in the same commit" in text, f"{track}: say what the history actually supports"


def test_pages_footnote_the_accounting_basis_of_the_consensus():
    """Adjusted non-GAAP EPS and 归母净利润 sit in adjacent columns with nothing saying so."""
    for track in TRACKS:
        text = page(track)
        assert "non-GAAP" in text and "归母净利润" in text, f"{track}: proposal §8 requires the basis footnote"
        assert "Estimate source" in text, f"{track}: the reader needs to see which row is on which basis"


def test_disclaimer_is_above_the_first_chart():
    """It was the last paragraph of the appendix; the proposal asks for it at the top."""
    for track in TRACKS:
        text = page(track)
        first_chart = text.index("<ChartCard")
        disclaimer = text.index("nothing here is investment advice")
        assert disclaimer < first_chart, f"{track}: most readers never reach the appendix"


# --- S9: the scorer's output reaches a reader --------------------------------------------------------

def test_pages_render_the_pre_registered_scorer_output():
    """`evaluation.json` was rendered nowhere, so every "not yet, because…" was addressed to nobody."""
    from pipelines.valuation.evaluate import build

    keys = sorted(next(iter(build()["tracks"].values())))
    for track in TRACKS:
        text = page(track)
        assert "marts/valuation/evaluation.json" in text, f"{track}: nothing reads the scorer's output"
        for key in keys:
            assert f"evalStatus('{key}')" in text, f"{track}: evaluation item {key} has no status on the page"
        assert "import.meta.glob" in text, f"{track}: a missing scorer output must not break the build"


# --- S16: a ratio of two currencies is not a ratio ---------------------------------------------------

def test_price_to_sales_is_not_divided_across_currencies():
    text = page("ssb")
    assert "p.market_cap / p.ttm_revenue" not in text.replace(
        "? p.market_cap / p.ttm_revenue", ""
    ), "the template divides a market value by a revenue without checking the currencies"
    assert "p.price_currency === p.reporting_currency" in text


# --- P10 (rendering half): a capacity table holds capacity -------------------------------------------

def test_rows_that_disclaim_being_capacity_stay_out_of_the_capacity_table():
    """One row's own reference note reads "MUST NOT be tabulated in a capacity column"."""
    text = page("ssb")
    match = re.search(r"NOT_CAPACITY = /(.+?)/i;", text)
    assert match, "the solid-state page must decide what a capacity row is"
    pattern = re.compile(match.group(1).replace("\\\\", "\\"), re.I)
    unit_ok = re.compile(r"\b[GM]Wh\b", re.I)
    for row in facts("ssb").get("capacity", []):
        blob = f"{row.get('unit', '')} {row.get('metric', '')}"
        is_capacity = bool(unit_ok.search(str(row.get("unit", "")))) and not pattern.search(blob)
        disclaims = re.search(r"NOT (?:built|a capacity)|not a capacity figure|no GWh", blob, re.I)
        assert not (is_capacity and disclaims), f"{row.get('entity')} disclaims being capacity and is tabulated as one"
