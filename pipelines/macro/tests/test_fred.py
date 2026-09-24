import pytest

from pipelines.macro import fred

CSV = """observation_date,IC4WSA_20250918,IC4WSA_20250925,IC4WSA_20251120
2025-09-06,240750,240750,240750
2025-09-13,240000,240250,240250
2025-09-20,,237500,237750
2025-09-27,,,234750
2025-10-04,,,227500
"""


def test_first_print_is_the_earliest_vintage_and_is_dated_by_it():
    cells = fred.parse_vintages(CSV, "IC4WSA", ["2025-09-18", "2025-09-25", "2025-11-20"])
    rows = {r["week_end"]: (r["release_date"], r["value"]) for r in fred.first_prints(cells, "2025-09-18")}
    # 09-06 and 09-13 were already out before the first vintage fetched: their first print is unknown
    assert "2025-09-13" not in rows
    assert rows["2025-09-20"] == ("2025-09-25", 237500.0)  # not the revised 237,750
    # weeks held back by the shutdown are dated by the day they finally came out
    assert rows["2025-09-27"] == ("2025-11-20", 234750.0) and rows["2025-10-04"][0] == "2025-11-20"


def test_a_silently_capped_vintage_list_is_an_error():
    with pytest.raises(ValueError, match="requested"):
        fred.parse_vintages(CSV, "IC4WSA", ["2025-09-18", "2025-09-25", "2025-11-20", "2025-11-26"])


def test_vintage_dates_come_from_the_form_options():
    html = '<option value="2025-09-18">2025-09-18</option><option value="2024-01-04">x</option>'
    assert fred.vintage_dates(html, "2025-01-01") == ["2025-09-18"]
