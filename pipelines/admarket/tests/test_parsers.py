"""Parsers: XBRL instance reader, Alphabet's volume/price tables, Meta's wording variants, filing selection."""

from __future__ import annotations

import pytest

from pipelines.admarket import ingest, parse

XBRL = """<xbrli:xbrl>
<xbrli:context id="c1"><xbrli:entity/><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate>
<xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c2"><xbrli:entity><xbrli:segment>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">goog:GoogleNetworkMember</xbrldi:explicitMember>
</xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate>
<xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c3"><xbrli:entity/><xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="c4"><xbrli:entity><xbrli:segment><xbrldi:typedMember dimension="x:A">1</xbrldi:typedMember>
</xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate>
<xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="c1" unitRef="usd" decimals="-6">96428000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
<us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="c2" unitRef="usd" decimals="-6">7354000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
<us-gaap:Revenues contextRef="c4" unitRef="usd">1</us-gaap:Revenues>
<us-gaap:Assets contextRef="c3" unitRef="usd">450000000000</us-gaap:Assets>
<us-gaap:Revenues contextRef="c3" unitRef="usd">5</us-gaap:Revenues>
<dei:DocumentType contextRef="c1">10-Q</dei:DocumentType>
<us-gaap:CostOfRevenue contextRef="c1" unitRef="usd">40000000000</us-gaap:CostOfRevenue>
</xbrli:xbrl>"""


def test_read_xbrl_contexts_dims_and_instants():
    facts = parse.read_xbrl(XBRL)
    by = {(c, tuple(sorted(d.items()))): (s, e, v) for c, s, e, d, v in facts}
    total = by[("RevenueFromContractWithCustomerExcludingAssessedTax", ())]
    assert total[0].isoformat() == "2025-04-01" and total[2] == 96428000000.0
    net = by[("RevenueFromContractWithCustomerExcludingAssessedTax", (("ProductOrServiceAxis", "GoogleNetworkMember"),))]
    assert net[2] == 7354000000.0                      # namespace prefixes dropped from axis and member
    assert by[("Assets", ())][0] is None               # an instant has no start
    assert not [f for f in facts if f[0] == "DocumentType"]   # non-numeric facts dropped
    assert not [f for f in facts if f[4] == 1.0]       # typed-member contexts skipped


def test_revenue_facts_keeps_only_revenue_durations():
    facts = parse.revenue_facts(XBRL)
    assert {f["concept"] for f in facts} == {"RevenueFromContractWithCustomerExcludingAssessedTax"}
    assert len(facts) == 2                             # the instant "Revenues" and CostOfRevenue are not kept
    assert facts[1]["dims"] == {"ProductOrServiceAxis": "GoogleNetworkMember"}


# ---- Alphabet tables ---------------------------------------------------------------------------------------

ONE_YEAR_TWO_PERIODS = ("The following table presents changes in our paid clicks and cost-per-click: Three Months Ended "
                        "June 30, Six Months Ended June 30, 2021 2021 Paid clicks change 26 % 25 % Cost-per-click change "
                        "31 % 16 % Paid clicks increased ...")
TWO_YEARS = ("Three Months Ended Six Months Ended June 30, June 30, 2019 2020 2019 2020 Paid clicks change 28 % 8 % 33 % "
             "10 % Cost-per-click change (12) % (15) % (13) % (8) % Impressions change 4 % (1) % ... ")
BOTH_TABLES = ("Three Months Ended Six Months Ended June 30, 2025 June 30, 2025 Google Search & other Paid clicks change "
               "4 % 3 % Cost-per-click change 8 % 8 % Google Network Impressions change (6) % (5) % Cost-per-impression "
               "change 6 % 5 % Changes in paid clicks ...")
DASH_ZERO = ("Three Months Ended June 30, Six Months Ended June 30, 2022 2022 Paid clicks change 12% 14% Cost-per-click "
             "change —% 4% Paid clicks increased")


def test_google_one_year_header_reads_the_first_column():
    assert parse.google_table(ONE_YEAR_TWO_PERIODS, "Paid clicks change", "Cost-per-click change") == (26, 31)


def test_google_two_year_header_reads_the_current_year_column():
    """Older tables carry the prior-year change first: 2019 2020 -> the second column is the quarter."""
    assert parse.google_table(TWO_YEARS, "Paid clicks change", "Cost-per-click change") == (8, -15)


def test_google_parentheses_are_negative_and_both_tables_parse():
    assert parse.parse_text("GOOGL", BOTH_TABLES) == {
        "google_search_clicks": {"volume": 4, "price": 8},
        "google_network": {"volume": -6, "price": 6},
    }


def test_google_dash_is_zero_not_the_next_column():
    """The research regex skipped '—%' and read the six-month +4% as the quarter's change (2022Q2)."""
    assert parse.google_table(DASH_ZERO, "Paid clicks change", "Cost-per-click change") == (12, 0)


def test_google_missing_table_is_none():
    assert parse.google_table("no table here", "Paid clicks change", "Cost-per-click change") is None


# ---- Meta wording -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("During the three months ended March 31, 2024, ad impressions delivered across our Family of Apps increased by "
     "20% year-over-year. During the three months ended March 31, 2024, the average price per ad increased by 6% "
     "year-over-year.", {"volume": 20, "price": 6}),
    ("During the three and six months ended June 30, 2021, the number of ads delivered increased by 6% and 9%. "
     "During the three and six months ended June 30, 2021, the average price per ad increased by 47% and 39%.",
     {"volume": 6, "price": 47}),
    ("During the three and nine months ended September 30, 2022 , ad impressions increased by 17% and 15%. During "
     "the three and nine months ended September 30, 2022, the average price per ad decreased by 18% and 13%.",
     {"volume": 17, "price": -18}),
])
def test_meta_results_wording_variants(text, expected):
    assert parse.meta_changes(text) == expected


def test_meta_executive_overview_fallback_for_both_measures():
    text = ("Ad impressions delivered across our Family of Apps in the third quarter of 2022 increased by 17% "
            "year-over-year, and the average price per ad in the third quarter of 2022 decreased by 18% year-over-year.")
    assert parse.meta_changes(text) == {"volume": 17, "price": -18}


def test_meta_first_match_wins_over_later_mentions():
    text = ("During the three months ended June 30, 2025, ad impressions increased by 11%. "
            "During the three months ended June 30, 2025, the average price per ad increased by 9%. "
            "During the three months ended June 30, 2024, ad impressions increased by 10%.")
    assert parse.meta_changes(text) == {"volume": 11, "price": 9}


def test_snippets_parse_like_the_full_text():
    filler = " lorem ipsum" * 400
    meta = filler + " During the three months ended June 30, 2025, ad impressions increased by 11%." + filler + \
        " During the three months ended June 30, 2025, the average price per ad increased by 9%." + filler
    snip = parse.snippets("META", meta)
    assert len(snip) < len(meta) / 3
    assert parse.parse_text("META", snip) == parse.parse_text("META", meta) == {"meta_ads": {"volume": 11, "price": 9}}
    goog = filler + " " + BOTH_TABLES + filler
    assert parse.parse_text("GOOGL", parse.snippets("GOOGL", goog)) == parse.parse_text("GOOGL", goog)


def test_plain_text_and_quarter_of_report():
    assert parse.plain_text("<p>Paid&nbsp;clicks\n change</p><td>4&#160;%</td>") == " Paid clicks change 4 % "   # a no-break space is whitespace too
    assert parse.quarter_of_report("2025-06-30") == "2025Q2"
    assert parse.quarter_of_report("2024-12-31") == "2024Q4"


# ---- filing selection and the SEC guard ------------------------------------------------------------------

def _page(rows):
    cols = ("form", "accessionNumber", "reportDate", "filingDate", "primaryDocument")
    return {c: [r[i] for r in rows] for i, c in enumerate(cols)}


def test_submission_pages_and_wanted_forms():
    recent = _page([("10-Q", "a-1", "2026-06-30", "2026-07-23", "q.htm"), ("8-K", "a-2", "", "2026-07-23", "k.htm"),
                    ("10-K/A", "a-3", "2025-12-31", "2026-03-01", "ka.htm")])
    older = _page([("10-K", "a-4", "2019-12-31", "2020-02-01", "k19.htm"), ("10-Q", "a-5", "2020-03-31", "2020-04-28", "q1.htm"),
                   ("10-Q", "a-1", "2026-06-30", "2026-07-23", "q.htm")])
    rows = ingest.submission_rows(recent) + ingest.submission_rows(older)
    got = ingest.wanted(rows)
    assert [r["accession"] for r in got] == ["a-1", "a-5"]   # no 8-K, no amendment, nothing before 2020, no duplicate


def test_submission_rows_rejects_ragged_pages():
    bad = _page([("10-Q", "a-1", "2026-06-30", "2026-07-23", "q.htm")])
    bad["form"].append("10-K")
    with pytest.raises(ValueError):
        ingest.submission_rows(bad)


def test_pick_instance():
    assert ingest.pick_instance(["goog-20250630.htm", "goog-20250630_htm.xml", "goog-20250630_cal.xml"]) == \
        "goog-20250630_htm.xml"
    assert ingest.pick_instance(["meta-20200331.xml", "meta-20200331_cal.xml", "meta-20200331_lab.xml"]) == \
        "meta-20200331.xml"
    assert ingest.pick_instance(["R1.htm", "FilingSummary.xml"]) is None


def test_sec_user_agent_is_required_before_any_request(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ingest.SecUserAgentMissing, match="not set"):
        ingest.sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "portfolio-bot")
    with pytest.raises(ingest.SecUserAgentMissing, match="no contact email"):
        ingest.sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "portfolio-bot ci@example.org")
    assert ingest.sec_user_agent() == "portfolio-bot ci@example.org"


def test_run_refuses_without_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert ingest.main(["--tickers", "META"]) == 2


def test_text_record_refuses_snippets_that_parse_differently(monkeypatch):
    monkeypatch.setattr(parse, "snippets", lambda ticker, t: "")
    with pytest.raises(ValueError, match="snippets parse"):
        ingest.text_record("GOOGL", {"period": "2025-06-30"}, BOTH_TABLES, "x")
