"""The scoreboard: nothing is graded before its date, a graded item is the registered reading, the "already seen"
lines are the section's own, and every degenerate input (no runs, too few snapshots, too small a panel) gives a
well-formed board instead of a guess."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandera.errors
import polars as pl
import pytest

from pipelines.cyber import config as C
from pipelines.cyber import evaluate as E
from pipelines.cyber import frozen as F
from pipelines.cyber import readings as R

DRAWS = 200
PLAN = Path(__file__).resolve().parents[3] / "docs" / "EVALUATION_PLAN.md"
FIRST = date(2026, 10, 26)
GRADE_DAY = date(2027, 1, 15)


# --- the section, as text --------------------------------------------------------------------------------------
def section_i() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## Cybersecurity (project I)")
    nxt = text.find("\n## ", start + 10)
    return text[start : nxt if nxt != -1 else None]


WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}


def norm(text: str) -> str:
    """One line, no backticks, lower case, number words as digits: how a sentence is compared with the section's."""
    text = re.sub(r"\s+", " ", text.replace("`", "")).lower()
    return re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: WORDS[m.group(1)], text)


def item_text(n: int) -> str:
    h = section_i()
    start = h.index(f"**Q{n} ", h.index("### Items"))
    nxt = h.find("\n**Q", start + 5)
    return norm(h[start : nxt if nxt != -1 else h.index("### Described, not registered")])


def sentences(text: str) -> list[str]:
    return [s.strip().rstrip(".") for s in re.split(r"(?<=[A-Za-z0-9%)])\. (?=[A-Z])", text) if s.strip()]


# --- fixtures ---------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def baseline() -> pl.DataFrame:
    return F.load_baseline()


def week(baseline: pl.DataFrame, day: date, *, seed: int, rev: float = 1.0, **edits) -> pl.DataFrame:
    """The baseline's rows restamped to a Monday run, enterprise values shifted a little, revenue consensus times
    ``rev``. ``edits`` are keyword ``column=callable(frame)->Expr`` replacements."""
    rng = np.random.default_rng(seed)
    shift = pl.Series(np.exp(rng.normal(scale=0.04, size=baseline.height)))
    frame = baseline.with_columns(
        pl.lit(f"{day.isoformat()}T14:00:00+00:00").alias("snapshot_ts"),
        (pl.col("enterprise_value") * shift).alias("enterprise_value"),
        (pl.col("rev_fy1_avg") * rev).alias("rev_fy1_avg"),
        (pl.col("rev_fy0_avg") * rev).alias("rev_fy0_avg"),
    )
    for column, fn in edits.items():
        frame = frame.with_columns(fn(frame).alias(column))
    return frame


def monday(i: int) -> date:
    return FIRST + timedelta(weeks=i)


def runs_of(baseline: pl.DataFrame, frames: list[pl.DataFrame]) -> pl.DataFrame:
    return pl.concat([baseline, *frames], how="vertical_relaxed")


def twelve(baseline: pl.DataFrame, **edits) -> list[pl.DataFrame]:
    return [week(baseline, monday(i), seed=i, **edits) for i in range(C.WINDOW_WEEKS)]


@pytest.fixture(scope="module")
def full_window(baseline) -> pl.DataFrame:
    return runs_of(baseline, twelve(baseline))


def null_for(column: str, tickers):
    return lambda f: pl.when(pl.col("ticker").is_in(list(tickers))).then(None).otherwise(pl.col(column))


def by_id(board: list[dict]) -> dict[str, dict]:
    return {q["id"]: q for q in board}


# --- formatting ------------------------------------------------------------------------------------------------------
def test_figures_round_half_up_like_the_section_does() -> None:
    assert E._f(246.15, 1) == "246.2" and E._f(325.0, 0) == "325" and E._f(0.125, 2) == "0.13"
    assert E._f(-0.001, 2) == "0.00" and E._f(-0.054, 2, sign=True) == "-0.05" and E._f(0.0, 2, sign=True) == "+0.00"
    assert E._pct(0.13460888, 1, sign=True) == "+13.5%" and E._pct(0.1796, 0) == "18%"
    assert E._ci(-0.0606, 0.1244, 2, sign=True) == "-0.06 to +0.12"
    assert E._holds_parts([True, True, False]) == "The first two parts of Holds are met and the third is not"
    assert E._holds_parts([True, True, True]).startswith("All three") and "None" in E._holds_parts([False] * 3)


def test_evidence_row_keeps_nan_out_and_pairs_value_with_qualifier() -> None:
    row = E.ev("Q1", "x", float("nan"), "p", lo=float("inf"), hi=1.0, level="90%", as_of=date(2026, 10, 26))
    assert row["value"] is None and row["qualifier"] is None and row["lo"] is None and row["level"] is None
    assert row["as_of"] == "2026-10-26" and row["ref"] == "Section I, Q1"
    full = E.ev("Q1", "x", 0.5, "p", lo=0.1, hi=0.9, level="90%")
    assert (full["qualifier"], full["lo"], full["hi"], full["level"]) == ("=", 0.1, 0.9, "90%")


# --- the section, against the module -------------------------------------------------------------------------------------
def test_titles_and_grading_dates_are_the_sections() -> None:
    heads = dict(re.findall(r"^\*\*(Q\d+) (.+?)\.?\*\*$", section_i(), re.M))
    assert {k: v.rstrip(".") for k, v in heads.items()} == {q: i.title for q, i in E.ITEMS.items()}
    for n in range(1, 11):
        text = item_text(n)
        for d in E.ITEMS[f"Q{n}"].dates:
            assert d.isoformat() in text, f"Q{n}: {d} is not a date the section gives"
    assert C.Q10["min_pairs"] in item_text(10) and E.Q5_EXAMPLE.lower() in item_text(5)
    assert all(t.lower() in item_text(8) for t in E.Q8_RUMOURED)


def test_the_facts_no_table_holds_are_the_sections_sentences() -> None:
    assert norm(E.SEEN_Q6_DEALS) in item_text(6)
    assert norm(E.SEEN_Q8_EVENT) in item_text(8)


def test_already_seen_is_the_sections_line_at_the_registered_draws(baseline) -> None:
    board = by_id(E.score(pl.concat([baseline]), None, date(2026, 10, 17)))  # the registered draw count
    for n in (1, 2, 3, 4, 5, 7, 8, 9, 10):
        section = item_text(n)
        for sentence in sentences(board[f"Q{n}"]["already_seen"]):
            assert norm(sentence) in section, f"Q{n}: {sentence!r}"
    # Q6: the section's deals verbatim, then two numbers computed from the baseline
    q6 = board["Q6"]["already_seen"]
    assert q6.startswith(E.SEEN_Q6_DEALS)
    tail = "is at 7.3x ev/ntm revenue on the baseline date against a population median of 7.3x"
    assert tail in norm(q6) and tail in item_text(6)


def test_already_seen_numbers_are_the_registered_readings_on_the_baseline(baseline) -> None:
    board = by_id(E.score(pl.concat([baseline]), None, date(2026, 10, 17), draws=DRAWS))
    snaps = {date(2026, 10, 16): baseline}
    us = R.build_panel(snaps, F.q1_population, min_snapshots=1)
    q1 = R.q1(us, draws=DRAWS)
    assert f"the rule reads {q1['status']}" in board["Q1"]["already_seen"]
    assert E._f(q1["uplift"], 2, sign=True) in board["Q1"]["already_seen"]
    assert E._f(R.q2(us, draws=DRAWS)["r2"]) in board["Q2"]["already_seen"]
    assert "reads holds" in board["Q3"]["already_seen"] and "reads holds" in board["Q7"]["already_seen"]
    assert board["Q9"]["already_seen"].endswith("a premium of 4.84.")


# --- nothing is graded before its date ------------------------------------------------------------------------------------
def spy_on_readings(monkeypatch) -> list[tuple[str, int]]:
    """Record every call to a registered reading with the number of snapshots behind its panel."""
    calls: list[tuple[str, int]] = []

    def wrap(name):
        real = getattr(R, name)

        def wrapper(*args, **kw):
            calls.append((name, args[0].n_snapshots if hasattr(args[0], "n_snapshots") else -1))
            return real(*args, **kw)

        return wrapper

    for name in ("q1", "q2", "q3", "q7"):
        monkeypatch.setattr(R, name, wrap(name))
    for name in ("q4", "q9", "q10"):  # the baseline text never calls these
        monkeypatch.setattr(R, name, lambda *a, _n=name, **k: pytest.fail(f"R.{_n} was called before its date"))
    return calls


def test_no_registered_reading_runs_on_the_window_before_its_first_grading_date(full_window, monkeypatch) -> None:
    calls = spy_on_readings(monkeypatch)
    board = E.score(full_window, None, GRADE_DAY - timedelta(days=1), draws=DRAWS)
    assert {q["status"] for q in board} == {"collecting"}
    assert sorted(calls) == [("q1", 1), ("q2", 1), ("q3", 1), ("q7", 1)], "only the baseline's own reading was made"
    by = by_id(board)
    assert all(by[q]["next_grading"] == "2027-01-15" for q in ("Q1", "Q2", "Q3", "Q7"))
    ev1 = {e["label"]: e for e in by["Q1"]["evidence"]}
    assert ev1["Monday snapshots collected"]["value"] == 12 and ev1["Companies in the panel so far"]["value"] == 17
    assert {e["label"]: e["value"] for e in by["Q7"]["evidence"]}["A-share companies in the panel so far"] == 6
    assert not any(e["lo"] is not None for q in board for e in q["evidence"]), "no interval before a grading date"


def test_the_grading_day_itself_reads_the_window(full_window) -> None:
    before = by_id(E.score(full_window, None, GRADE_DAY - timedelta(days=1), draws=DRAWS))
    on = by_id(E.score(full_window, None, GRADE_DAY, draws=DRAWS))
    for q in ("Q1", "Q2", "Q3", "Q7"):
        assert before[q]["status"] == "collecting" and on[q]["status"] != "collecting" and on[q]["next_grading"] is None
    assert on["Q10"]["status"] == "collecting" and on["Q10"]["next_grading"] == "2027-01-18"


def test_weeks_after_the_window_do_not_move_a_graded_reading(baseline, full_window) -> None:
    def wild(f: pl.DataFrame) -> pl.Expr:
        return pl.col("enterprise_value") * 5.0

    later = [week(baseline, monday(i), seed=i, enterprise_value=wild) for i in range(12, 16)]  # 2027-01-18 to 02-08
    longer = runs_of(baseline, [*twelve(baseline), *later])
    a = by_id(E.score(full_window, None, GRADE_DAY, draws=DRAWS))
    b = by_id(E.score(longer, None, date(2027, 2, 10), draws=DRAWS))
    for q in ("Q1", "Q2", "Q3", "Q7"):
        assert a[q]["status"] == b[q]["status"] and a[q]["evidence"] == b[q]["evidence"], q


def test_each_graded_item_is_the_registered_reading_of_the_window(full_window) -> None:
    board = by_id(E.score(full_window, None, GRADE_DAY, draws=DRAWS))
    window = {d: f for d, f in E.G.series_snapshots(full_window, GRADE_DAY).items() if d <= date(2027, 1, 11)}
    assert len(window) == 12
    us, cn = E._panels(window)
    assert len(us) == 17 and len(cn) == 6 and us.enough_snapshots
    want = {
        "Q1": R.q1(us, draws=DRAWS),
        "Q2": R.q2(us, draws=DRAWS),
        "Q3": R.q3(us, draws=DRAWS),
        "Q7": R.q7(us, cn, draws=DRAWS),
    }
    for q, out in want.items():
        assert board[q]["status"] == out["status"], q
    ev = {q: {e["label"]: e for e in board[q]["evidence"]} for q in want}
    assert ev["Q1"]["Uplift in adjusted R² from the layer groups"]["value"] == pytest.approx(want["Q1"]["uplift"])
    assert ev["Q1"]["Uplift in adjusted R² from the layer groups"]["lo"] == pytest.approx(want["Q1"]["lo"])
    assert ev["Q1"]["Uplift in adjusted R² from the layer groups"]["level"] == "90%"
    assert ev["Q2"]["Growth coefficient b"]["hi"] == pytest.approx(want["Q2"]["b_hi"])
    assert ev["Q3"]["Ratio of the two medians"]["value"] == pytest.approx(want["Q3"]["ratio"])
    assert ev["Q7"]["China coefficient"]["lo"] == pytest.approx(want["Q7"]["lo"])
    assert ev["Q7"]["China coefficient"]["value"] < 0
    assert board["Q7"]["status"] == "holds" and board["Q7"]["reason"].startswith("Holds: ")
    assert board["Q1"]["status"] == "undecidable" and "rules 2 and 5" in board["Q1"]["reason"]


def test_too_few_snapshots_make_the_four_panel_items_undecidable_without_a_reading(baseline, monkeypatch) -> None:
    runs = runs_of(baseline, twelve(baseline)[:9])  # nine Mondays against a minimum of ten
    calls = spy_on_readings(monkeypatch)
    board = by_id(E.score(runs, None, date(2027, 2, 1), draws=DRAWS))
    for q in ("Q1", "Q2", "Q3", "Q7"):
        assert board[q]["status"] == "undecidable" and "at least 10" in board[q]["reason"], q
    assert sorted(calls) == [("q1", 1), ("q2", 1), ("q3", 1), ("q7", 1)], "no reading of the thin window"


def test_a_panel_below_the_minimum_is_undecidable(baseline) -> None:
    drop = [t for t in F.q1_population(baseline) if t not in ("CRWD", "NET", "PANW", "ZS", "OKTA", "FTNT", "S", "SAIL")]
    runs = runs_of(baseline, twelve(baseline, enterprise_value=null_for("enterprise_value", drop)))
    board = by_id(E.score(runs, None, GRADE_DAY, draws=DRAWS))
    assert (
        board["Q1"]["status"] == "undecidable"
        and "8 companies and a reading needs at least 15" in board["Q1"]["reason"]
    )
    assert board["Q2"]["status"] == "undecidable" and "at least 15" in board["Q2"]["reason"]
    assert board["Q3"]["status"] == "undecidable" and "each needs at least 5" in board["Q3"]["reason"]
    assert {e["label"]: e["value"] for e in board["Q1"]["evidence"]} == {"Companies in the panel": 8}


def test_fewer_than_five_a_share_companies_make_q7_undecidable(baseline) -> None:
    runs = runs_of(baseline, twelve(baseline, em_rev1_mean=null_for("em_rev1_mean", C.FROZEN_Q7_CN[:2])))
    q7 = by_id(E.score(runs, None, GRADE_DAY, draws=DRAWS))["Q7"]
    assert q7["status"] == "undecidable" and "4 companies in the panel" in q7["reason"]
    assert {e["label"]: e["value"] for e in q7["evidence"]}["A-share companies in the panel"] == 4


# --- Q9 --------------------------------------------------------------------------------------------------------------------------------
START = [date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]
END = [date(2027, 9, 20), date(2027, 9, 27), date(2027, 10, 4), date(2027, 10, 11)]


def trio_scaled(baseline: pl.DataFrame, days: list[date], factor: float, seed0: int) -> list[pl.DataFrame]:
    def scale(f: pl.DataFrame) -> pl.Expr:
        scaled = pl.col("enterprise_value") * factor
        return pl.when(pl.col("ticker").is_in(C.Q9["trio"])).then(scaled).otherwise(pl.col("enterprise_value"))

    return [week(baseline, d, seed=seed0 + i, enterprise_value=scale) for i, d in enumerate(days)]


def test_q9_collects_until_the_end_weeks_and_then_follows_the_registered_rule(baseline) -> None:
    start = trio_scaled(baseline, START, 1.0, 0)
    assert C.Q9["start"] == [d.isoformat() for d in START] and E.q9_days() == (START, END)
    mid = by_id(E.score(runs_of(baseline, start), None, date(2027, 6, 1), draws=DRAWS))["Q9"]
    assert mid["status"] == "collecting" and mid["next_grading"] == "2027-10-12"
    ev = {e["label"]: e for e in mid["evidence"]}
    assert ev["Start snapshots collected"]["value"] == 4 and ev["Start value of the premium"]["value"] > 1
    three = by_id(E.score(runs_of(baseline, start[:3]), None, date(2027, 6, 1), draws=DRAWS))["Q9"]
    assert "Start value of the premium" not in {e["label"] for e in three["evidence"]}

    for factor, status in ((1.05, "holds"), (1.6, "falsified")):
        runs = runs_of(baseline, start + trio_scaled(baseline, END, factor, 100))
        q9 = by_id(E.score(runs, None, date(2027, 10, 12), draws=DRAWS))["Q9"]
        want = R.q9({d: f for d, f in E.G.series_snapshots(runs, date(2027, 10, 12)).items()}, start=START, end=END)
        assert q9["status"] == want["status"] == status and q9["next_grading"] is None
        ev = {e["label"]: e["value"] for e in q9["evidence"]}
        assert ev["Start value of the premium"] == pytest.approx(want["start"])
        assert ev["End value of the premium"] == pytest.approx(want["end"])
        assert ev["End over start, minus one"] == pytest.approx(want["end"] / want["start"] - 1)
        assert q9["reason"].startswith("Holds: " if status == "holds" else "Falsified: ")


def test_q9_without_the_end_snapshots_is_undecidable_not_a_guess(baseline) -> None:
    runs = runs_of(baseline, trio_scaled(baseline, START, 1.0, 0))
    q9 = by_id(E.score(runs, None, date(2027, 10, 12), draws=DRAWS))["Q9"]
    assert q9["status"] == "undecidable" and "snapshot is missing" in q9["reason"]
    assert {e["label"]: e["value"] for e in q9["evidence"]}["End value of the premium"] is None


# --- Q10 --------------------------------------------------------------------------------------------------------------------------------
def revision_weeks(baseline: pl.DataFrame, n: int, factor) -> list[pl.DataFrame]:
    return [week(baseline, monday(i), seed=i, rev=factor(i)) for i in range(n)]


def same_direction(i: int) -> float:
    return 1.01**i  # every company's consensus rises 1% a week: every tested pair is same-direction


def opposite_direction(i: int) -> float:
    return {0: 1.00, 1: 1.02}.get(i % 5, 1.01)  # up into week 1 mod 5, back down at week 5: opposite


def test_q10_collects_before_its_date_without_showing_a_direction(baseline) -> None:
    runs = runs_of(baseline, revision_weeks(baseline, 8, same_direction))
    q10 = by_id(E.score(runs, None, date(2026, 12, 20), draws=DRAWS))["Q10"]
    assert q10["status"] == "collecting" and q10["next_grading"] == "2027-01-15"
    labels = {e["label"] for e in q10["evidence"]}
    assert labels == {"Monday snapshots collected", "Pairs formed so far", "Tested pairs so far"}
    assert {e["label"]: e["value"] for e in q10["evidence"]}["Tested pairs so far"] > 0


def test_q10_is_graded_on_the_first_monday_with_thirty_tested_pairs_and_stays_there(baseline) -> None:
    runs = runs_of(baseline, revision_weeks(baseline, 16, same_direction))  # Mondays to 2027-02-08
    just = by_id(E.score(runs, None, date(2027, 1, 17), draws=DRAWS))["Q10"]
    assert just["status"] == "collecting" and just["next_grading"] == "2027-01-18", (
        "the 18th is the first Monday after the 15th"
    )
    done = by_id(E.score(runs, None, date(2027, 1, 18), draws=DRAWS))["Q10"]
    assert done["status"] == "holds" and done["next_grading"] is None and done["reason"].startswith("Holds: ")
    ev = {e["label"]: e for e in done["evidence"]}
    assert ev["Graded as of"]["as_of"] == "2027-01-18"
    assert (
        ev["Opposite-direction pairs"]["value"] == 0
        and ev["Same-direction pairs"]["value"] == ev["Tested pairs"]["value"] >= 30
    )
    assert ev["One-sided p of the same-direction count"]["value"] < 0.001
    later = by_id(E.score(runs, None, date(2027, 2, 9), draws=DRAWS))["Q10"]
    assert later["evidence"] == done["evidence"] and later["status"] == "holds", (
        "later weeks do not move the graded reading"
    )


def test_q10_waits_for_thirty_tested_pairs_not_for_the_date_alone(baseline) -> None:
    movers = F.q1_population(baseline)[:12]  # two pairs each by week 12 (24 tested), three by week 15 (36)

    def moving(i: int):
        def edit(f: pl.DataFrame) -> pl.Expr:
            moved = pl.col("rev_fy1_avg") * 1.01**i
            return pl.when(pl.col("ticker").is_in(movers)).then(moved).otherwise(pl.col("rev_fy1_avg"))

        return edit

    runs = runs_of(baseline, [week(baseline, monday(i), seed=i, rev_fy1_avg=moving(i)) for i in range(16)])
    wait = by_id(E.score(runs, None, date(2027, 2, 1), draws=DRAWS))["Q10"]
    got = {e["label"]: e["value"] for e in wait["evidence"]}
    assert wait["status"] == "collecting" and 10 < got["Tested pairs so far"] < 30
    assert wait["next_grading"] == "2027-02-08", "the Monday run that is not in yet"
    done = by_id(E.score(runs, None, date(2027, 2, 8), draws=DRAWS))["Q10"]
    ev = {e["label"]: e for e in done["evidence"]}
    assert done["status"] == "holds" and ev["Graded as of"]["as_of"] == "2027-02-08"
    assert ev["Tested pairs"]["value"] >= 30


def test_q10_opposite_revisions_falsify(baseline) -> None:
    runs = runs_of(baseline, revision_weeks(baseline, 14, opposite_direction))
    q10 = by_id(E.score(runs, None, date(2027, 1, 25), draws=DRAWS))["Q10"]
    ev = {e["label"]: e["value"] for e in q10["evidence"]}
    assert q10["status"] == "falsified" and ev["Same-direction pairs"] == 0 and ev["Opposite-direction pairs"] >= 30


def test_q10_without_thirty_tested_pairs_collects_until_the_last_date_and_then_is_undecidable(baseline) -> None:
    runs = runs_of(baseline, revision_weeks(baseline, 14, lambda i: 1.0))  # consensus never moves: all pairs are flat
    mid = by_id(E.score(runs, None, date(2027, 6, 1), draws=DRAWS))["Q10"]
    assert mid["status"] == "collecting" and mid["next_grading"] == "2027-06-07"
    end = by_id(E.score(runs, None, date(2027, 10, 15), draws=DRAWS))["Q10"]
    assert end["status"] == "undecidable" and end["next_grading"] is None and "fewer" not in end["reason"]
    assert "0 tested pairs on 2027-10-15" in end["reason"]
    ev = {e["label"]: e["value"] for e in end["evidence"]}
    assert ev["Tested pairs"] == 0 and ev["Flat pairs"] == 0


# --- Q4 ---------------------------------------------------------------------------------------------------------------------------------
def returns_for(baseline: pl.DataFrame, slope: float, n: int | None = None) -> dict[str, float]:
    rng = np.random.default_rng(3)
    rows = {r["ticker"]: r for r in baseline.iter_rows(named=True)}
    out = {}
    for t in C.FROZEN_Q4_Q5[:n]:
        up = rows[t]["target_mean"] / rows[t]["price"] - 1
        out[t] = slope * up + rng.normal(scale=0.15)
    return out


def test_q4_needs_its_date_and_a_return_table(baseline, monkeypatch) -> None:
    runs = runs_of(baseline, [week(baseline, FIRST, seed=0)])
    returns = returns_for(baseline, 1.0)
    early = by_id(E.score(runs, None, date(2027, 10, 25), draws=DRAWS, returns=returns))["Q4"]
    assert early["status"] == "collecting" and early["next_grading"] == "2027-10-26"
    assert not any(e["label"].startswith("Spearman") for e in early["evidence"])
    ev = {e["label"]: e for e in early["evidence"]}
    assert (
        ev["Companies of the frozen list with a target upside"]["value"] == 34
        and ev["Median target upside at the first snapshot"]["as_of"] == "2026-10-26"
    )

    late = by_id(E.score(runs, None, date(2027, 10, 26), draws=DRAWS))["Q4"]
    assert late["status"] == "collecting" and "not supplied" in late["reason"] and late["next_grading"] is None
    nosnap = by_id(E.score(pl.concat([baseline]), None, date(2027, 10, 26), draws=DRAWS, returns=returns))["Q4"]
    assert nosnap["status"] == "collecting" and "2026-10-26 snapshot is missing" in nosnap["reason"]


def test_q4_follows_the_registered_rule_once_returns_are_in(baseline) -> None:
    runs = runs_of(baseline, [week(baseline, FIRST, seed=0)])
    day = date(2027, 10, 26)
    for slope, status in ((1.0, "falsified"), (-1.0, "holds")):
        returns = returns_for(baseline, slope)
        q4 = by_id(E.score(runs, None, day, draws=DRAWS, returns=returns))["Q4"]
        got = {e["label"]: e["value"] for e in q4["evidence"]}
        assert q4["status"] == status and q4["next_grading"] is None
        assert got["Companies with a full window or a deal return"] == 34
        first = E.G.series_snapshots(runs, day)[FIRST]
        rows = {r["ticker"]: r for r in first.iter_rows(named=True)}
        tickers = sorted(returns)
        want = R.q4(
            np.array([rows[t]["target_mean"] / rows[t]["price"] - 1 for t in tickers]),
            np.array([returns[t] for t in tickers]),
            draws=DRAWS,
        )
        assert got["Spearman correlation of upside and return"] == pytest.approx(want["rho"])
        assert got["One-sided 95% upper bound (Fisher transform)"] == pytest.approx(want["upper"])
    few = by_id(E.score(runs, None, day, draws=DRAWS, returns=returns_for(baseline, 1.0, n=20)))["Q4"]
    assert few["status"] == "undecidable" and "20 companies have a return" in few["reason"]
    nan = {**returns_for(baseline, 1.0), "AKAM": float("nan")}
    assert {
        e["label"]: e["value"] for e in by_id(E.score(runs, None, day, draws=DRAWS, returns=nan))["Q4"]["evidence"]
    }["Companies with a full window or a deal return"] == 33, (
        "a company without a return is not scored, and is counted out"
    )


# --- Q5, Q6, Q8: what grades them is not here ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("today", [date(2026, 10, 17), date(2027, 4, 30), date(2028, 1, 1)])
def test_q5_q6_and_q8_stay_collecting_whatever_the_date(full_window, today) -> None:
    board = by_id(E.score(full_window, None, today, draws=DRAWS))
    for q in ("Q5", "Q6", "Q8"):
        assert board[q]["status"] == "collecting", q
    assert "amendment" in board["Q5"]["reason"] and "Yahoo's daily history" in board["Q5"]["reason"]
    assert C.Q6["ledger_frozen_on"] in board["Q6"]["reason"] and "hash" in board["Q6"]["reason"]
    assert "by hand" in board["Q8"]["reason"] and C.Q8["deadline"] in board["Q8"]["reason"]
    assert all(t in board["Q8"]["reason"] for t in C.FROZEN_Q8)


def test_q8_gives_its_list_and_dates_and_never_a_verdict(baseline) -> None:
    q8 = by_id(E.score(pl.concat([baseline]), None, date(2026, 10, 17), draws=DRAWS))["Q8"]
    ev = {e["label"]: e for e in q8["evidence"]}
    assert ev["Companies on the list"]["note"] == ", ".join(C.FROZEN_Q8) and ev["Companies on the list"]["value"] == 6
    assert ev["Announcements needed for Holds"]["value"] == 2
    assert C.Q8["after"] in ev["Window"]["note"] and C.Q8["deadline"] in ev["Window"]["note"]
    assert q8["next_grading"] == C.Q8["midterm"]
    assert (
        by_id(E.score(pl.concat([baseline]), None, date(2027, 5, 1), draws=DRAWS))["Q8"]["next_grading"]
        == C.Q8["deadline"]
    )


def test_q6_reads_only_a_count_from_the_ledger(baseline) -> None:
    def n_companies(ledger):
        q6 = by_id(E.score(pl.concat([baseline]), ledger, date(2026, 10, 17), draws=DRAWS))["Q6"]
        return {e["label"]: e["value"] for e in q6["evidence"]}.get(
            "Companies with a private-interval row in the ledger now"
        )

    rows = [{"company": "A"}, {"company": "A"}, {"company": "B"}]
    assert n_companies(None) is None and n_companies({}) is None
    assert n_companies({"private_intervals": rows}) == 2
    assert n_companies({"private_intervals": pl.DataFrame(rows)}) == 2
    assert n_companies({"private_intervals": [{"firm": "A"}]}) is None, "an unreadable ledger is not counted"
    assert n_companies({"private_intervals": pl.DataFrame({"x": [1]})}) is None
    assert n_companies({"private_intervals": 7}) is None


# --- nothing usable --------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "today",
    [
        date(2026, 10, 17),
        date(2027, 1, 15),
        date(2027, 4, 30),
        date(2027, 10, 12),
        date(2027, 10, 26),
        date(2028, 6, 1),
    ],
)
@pytest.mark.parametrize("kind", ["none", "empty", "baseline"])
def test_every_input_and_date_gives_a_well_formed_board(baseline, kind, today) -> None:
    runs = {"none": None, "empty": pl.DataFrame(), "baseline": pl.concat([baseline])}[kind]
    board = E.score(runs, None, today, draws=DRAWS)
    assert [q["id"] for q in board] == E.QIDS and all(q["status"] in C.QSTATUS for q in board)
    json.dumps(board, allow_nan=False)
    assert E.evidence_frame(board).height == sum(len(q["evidence"]) for q in board)
    by = by_id(board)
    for q in ("Q1", "Q2", "Q3", "Q7"):
        assert by[q]["status"] == ("collecting" if today < GRADE_DAY else "undecidable"), "no snapshots, no reading"
    if kind != "baseline":
        assert by["Q1"]["already_seen"] == E.NO_BASELINE and by["Q8"]["already_seen"] == E.NO_BASELINE
        assert by["Q10"]["already_seen"].startswith("Nothing;")
    assert by["Q9"]["status"] == ("collecting" if today < date(2027, 10, 12) else "undecidable")
    assert by["Q10"]["status"] == ("collecting" if today < date(2027, 10, 15) else "undecidable")


def test_a_run_after_today_is_ignored(full_window) -> None:
    board = by_id(E.score(full_window, None, date(2026, 11, 3), draws=DRAWS))
    assert {e["label"]: e["value"] for e in board["Q1"]["evidence"]}["Monday snapshots collected"] == 2


def test_the_baseline_is_never_one_of_the_snapshots(baseline) -> None:
    board = by_id(E.score(pl.concat([baseline]), None, GRADE_DAY, draws=DRAWS))
    assert board["Q1"]["status"] == "undecidable" and "0 Monday snapshots" in board["Q1"]["reason"]


# --- the schemas -------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def good(baseline) -> list[dict]:
    return E.score(pl.concat([baseline]), None, date(2026, 10, 17), draws=DRAWS)


def test_scoreboard_schema_rejects_what_it_should(good) -> None:
    def with_row(i, **kw):
        return [{**r, **kw} if k == i else r for k, r in enumerate(good)]

    for bad in (
        with_row(0, status="maybe"),
        with_row(0, status="holds"),  # a verdict keeps a next grading date
        with_row(0, next_grading="soon"),
        with_row(0, reason=""),
        good[:9],
        [*good[:9], {**good[0]}],
        list(reversed(good)),
    ):
        with pytest.raises(pandera.errors.SchemaError):
            E.validate_scoreboard(bad)
    assert E.validate_scoreboard(good) == good


def test_evidence_schema_rejects_what_it_should(good) -> None:
    base = good[0]["evidence"][0]
    for bad in (
        {**base, "qualifier": None},  # a value needs a qualifier
        {**base, "value": None},  # and a qualifier a value
        {**base, "lo": 2.0, "hi": 1.0, "level": "90%"},
        {**base, "lo": 1.0, "hi": None, "level": "90%"},
        {**base, "lo": 1.0, "hi": 2.0, "level": None},
        {**base, "unit": ""},
        {**base, "ref": "elsewhere"},
        {**base, "as_of": "yesterday"},
    ):
        with pytest.raises(pandera.errors.SchemaError):
            E.validate_scoreboard([{**good[0], "evidence": [bad]}, *good[1:]])
    twice = {**good[0], "evidence": [base, base]}
    with pytest.raises(pandera.errors.SchemaError):
        E.validate_scoreboard([twice, *good[1:]])
