"""Q1-Q9 grading rules, exactly as registered in section H, and qualifier propagation through derived numbers."""
from datetime import date

import polars as pl
import pytest

from pipelines.payments import config, economics, evaluate, reference
from pipelines.payments.economics import Num, QualifierConflict, propagate
from pipelines.payments.evaluate import Context, decide

TODAY = date(2026, 10, 4)


def _kpi(company, metric, period, value, q="=", tag="V", chartable=True, url="https://example.org/x"):
    return {"company": company, "metric": metric, "period": period, "value": value, "qualifier": q, "tag": tag,
            "chartable": chartable, "metric_kind": "filed_volume", "unit": "USD", "currency": "USD",
            "source_url": url, "as_of_date": "2026-01-01"}


@pytest.fixture(scope="module")
def real():
    return {k: reference.rows(k, TODAY) for k in reference.KINDS}


# --- qualified comparisons and propagation --------------------------------------------------------


@pytest.mark.parametrize("n,op,thr,want", [
    (Num(5.0), ">=", 5.0, True), (Num(4.9), ">=", 5.0, False),
    (Num(5.0, ">"), ">=", 5.0, True), (Num(4.0, ">"), ">=", 5.0, None),     # a lower bound below the bar
    (Num(4.0, "<"), ">=", 5.0, False), (Num(6.0, "<"), ">=", 5.0, None),    # an upper bound above the bar
    (Num(5.2, "~"), ">=", 5.0, True),                                       # ~ graded at its value
    (Num(2.1, ">"), ">", 2.0, True), (Num(0.7, "<"), "<", 0.8, True), (Num(0.9, "<"), "<", 0.8, None),
    (None, ">=", 1.0, None),
])
def test_decide(n, op, thr, want):
    assert decide(n, op, thr) is want


def test_growth_inherits_bounds():
    # a lower-bounded current value over an exact prior year gives a lower-bounded growth
    g = evaluate._growth(_kpi("A", "GMV", "p", 136.0, ">"), _kpi("A", "GMV", "q", 100.0))
    assert g.qualifier == ">" and g.value == pytest.approx(36.0)
    # a lower-bounded prior year (denominator) flips to an upper bound
    g = evaluate._growth(_kpi("A", "GMV", "p", 136.0), _kpi("A", "GMV", "q", 100.0, ">"))
    assert g.qualifier == "<"


def test_mixed_bounds_give_tilde_or_are_refused():
    # gap = growth_A - growth_K with both growths lower-bounded: the subtraction flips one, so bounds conflict
    assert propagate([(">", 1), (">", -1)]) == "~"
    with pytest.raises(QualifierConflict):
        propagate([(">", 1), (">", -1)], strict=True)
    assert propagate([(">", 1), ("<", -1)]) == ">"   # same direction after the flip: still a lower bound


def test_month_end_and_next_grading():
    assert evaluate.month_end("2026-11") == date(2026, 11, 30)
    assert evaluate.next_grading(["2026-11", "2027-02"], date(2026, 12, 1)) == "2027-02"
    assert evaluate.next_grading(["monthly", "2027-02 (letter)"], TODAY) == "2027-02"
    assert evaluate.next_grading(["2026-09"], TODAY) is None


# --- Q1 -------------------------------------------------------------------------------------------


def _monthly(months, shares):
    rows = []
    for m, s in zip(months, shares, strict=True):
        stripe = int(s * 1000)
        rest = 1000 - stripe
        for co, n in (("Stripe", stripe), ("Adyen", rest // 4), ("PayPal", rest // 4), ("Checkout.com", rest // 4),
                      ("Airwallex", rest - 3 * (rest // 4))):
            rows.append({"registry": "npm", "side": "client", "series": co, "company": co, "packages": "x",
                         "period": m, "downloads": n, "base_month": None, "index": None})
    return pl.DataFrame(rows, schema={"registry": pl.Utf8, "side": pl.Utf8, "series": pl.Utf8, "company": pl.Utf8,
                                      "packages": pl.Utf8, "period": pl.Date, "downloads": pl.Int64,
                                      "base_month": pl.Date, "index": pl.Float64})


def test_q1_collecting_without_npm(real):
    r = evaluate.q1(Context(TODAY, kpi=real["kpi_disclosures_acceptance"] + real["kpi_disclosures_other"],
                            private=real["private_metrics"]))
    assert r.status == "collecting" and r.ci_only and not r.inputs_present
    assert "CI run" in r.reason
    up = next(e for e in r.evidence if e["label"].startswith("Stripe share of disclosed volume"))
    assert up["qualifier"] == "<" and 0 < up["value"] < 1


def test_q1_undecidable_while_volume_bound_rests_on_unbounded_members(real):
    """Adyen (EUR not converted) and Airwallex (no calendar-year figure) enter at [0, inf): adding 0 for them
    inflates the upper bound, so a low download share must not falsify the claim; Q1 reads undecidable."""
    kpi = real["kpi_disclosures_acceptance"] + real["kpi_disclosures_other"]
    months = [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]
    ctx = Context(date(2027, 1, 5), kpi=kpi, private=real["private_metrics"], denoms=real["denominators"],
                  client_monthly=_monthly(months, [0.5, 0.5, 0.5]))
    vol = evaluate.q1_volume(ctx)
    assert "Airwallex" in evaluate.q1_uninformative(vol)
    r = evaluate.q1(ctx)
    assert r.status == "undecidable"
    assert "no comparable calendar-year volume" in r.reason and "Airwallex" in r.reason
    ratio = next(e for e in r.evidence if e["label"].startswith("Download share /"))
    assert ratio["value"] is None
    up = next(e for e in r.evidence if e["label"].startswith("Stripe share of disclosed volume"))
    assert "NOT INFORMATIVE" in up["note"]
    series = evaluate.q1_series(ctx)
    assert series["volume_share_upper"].null_count() == series.height


def _bounded_vol(upper):
    return {"year": 2025, "lower": 0.1, "upper": upper, "upper_if_paypal_full_tpv": None,
            "members": {c: {"low": 1.0, "high": 2.0, "basis": "test"}
                        for c in ("Stripe", "Adyen", "PayPal", "Checkout.com", "Airwallex")}}


def test_q1_ratio_is_a_lower_bound_and_falsifies_when_bound_is_informative(real, monkeypatch):
    monkeypatch.setattr(evaluate, "q1_volume", lambda ctx: _bounded_vol(0.5))
    months = [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]
    ctx = Context(date(2027, 1, 5), client_monthly=_monthly(months, [0.5, 0.5, 0.5]))
    r = evaluate.q1(ctx)
    ratio = next(e for e in r.evidence if e["label"].startswith("Download share /"))
    assert ratio["qualifier"] == ">"           # '=' over an upper bound '<' is a lower bound
    assert ratio["value"] == pytest.approx(1.0)
    assert r.status == "falsified"             # 0.5 / 0.5 = 1.0 < 1.5 in three graded months


def test_q1_holds_when_ratio_clears_two(real, monkeypatch):
    monkeypatch.setattr(evaluate, "q1_volume", lambda ctx: _bounded_vol(0.4))
    ctx = Context(date(2026, 11, 5), client_monthly=_monthly([date(2026, 10, 1)], [0.88]))
    assert evaluate.q1(ctx).status == "holds"  # 0.88 / 0.4 = 2.2 >= 2.0


def test_q1_reads_eur_usd_from_curated_denominators(real):
    fx = {"metric": evaluate.EUR_USD_METRIC, "period": "FY2025", "value": 1.10, "qualifier": "=", "tag": "V",
          "chartable": True}
    kpi = real["kpi_disclosures_acceptance"] + real["kpi_disclosures_other"]
    without = evaluate.q1_volume(Context(TODAY, kpi=kpi, private=real["private_metrics"]))
    with_fx = evaluate.q1_volume(Context(TODAY, kpi=kpi, private=real["private_metrics"], denoms=[fx]))
    if without["year"] != 2025:
        pytest.skip("Stripe's latest curated year is not 2025")
    assert without["members"]["Adyen"]["high"] is None
    if "not converted" in without["members"]["Adyen"]["basis"]:  # Adyen has both halves curated
        assert with_fx["members"]["Adyen"]["high"] is not None
        assert "Adyen" not in evaluate.q1_uninformative(with_fx)
    # an unchartable or duplicated FX row is never used
    assert evaluate.eur_usd_row(Context(TODAY, denoms=[fx | {"chartable": False}]), 2025) is None
    assert evaluate.eur_usd_row(Context(TODAY, denoms=[fx, fx]), 2025) is None


# --- Q2 -------------------------------------------------------------------------------------------


def _adoption(stripe, paypal, start=date(2026, 6, 1)):
    rows = []
    for i, (s, p) in enumerate(zip(stripe, paypal, strict=True)):
        y, m = divmod(start.month - 1 + i, 12)
        d = date(start.year + y, m + 1, 1)
        for tech, n in (("Stripe", s), ("PayPal", p)):
            for rank in ("Top 10k", "ALL"):
                rows.append({"technology": tech, "date": d, "geo": "United States of America", "rank": rank,
                             "client": "mobile", "origins": n if rank == "Top 10k" else n * 10,
                             "total_origins": 10000, "share": None})
    return pl.DataFrame(rows)


def test_q2_absent_is_collecting():
    r = evaluate.q2(Context(TODAY))
    assert r.status == "collecting" and not r.inputs_present and r.graded == [config.Q2["graded"]]


def test_q2_holds_after_six_crawls():
    # Jun-Aug build the baseline rolling mean (lead 44), then six crawls keep the lead
    r = evaluate.q2(Context(TODAY, adoption=_adoption([522] * 9, [478] * 9)))
    assert r.status == "holds"


def test_q2_collecting_mid_window():
    r = evaluate.q2(Context(TODAY, adoption=_adoption([522] * 5, [478] * 5)))
    assert r.status == "collecting" and "2 of 6" in r.reason


def test_q2_falsified_when_gap_halves():
    r = evaluate.q2(Context(TODAY, adoption=_adoption([522, 522, 522, 480, 480, 480], [478] * 6)))
    assert r.status == "falsified" and "more than half" in r.reason


def test_q2_falsified_when_paypal_retakes_lead():
    r = evaluate.q2(Context(TODAY, adoption=_adoption([522, 522, 522, 200, 200, 200], [478] * 6)))
    assert r.status == "falsified" and "retook" in r.reason


# --- Q3 -------------------------------------------------------------------------------------------


def _q3_rows(a1=136.0, k1=127.0, a2=136.0, k2=127.0, qa="="):
    rows = []
    for (ac, ap), (kc, kp), av, kv in zip([p[0] for p in evaluate.Q3_PAIRS], [p[1] for p in evaluate.Q3_PAIRS],
                                          (a1, a2), (k1, k2), strict=True):
        rows += [_kpi("Affirm", "GMV", ac, av, qa), _kpi("Affirm", "GMV", ap, 100.0),
                 _kpi("Klarna", "US GMV", kc, kv), _kpi("Klarna", "US GMV", kp, 100.0)]
    return rows


def test_q3_holds_when_both_gaps_clear_5pp():
    r = evaluate.q3(Context(TODAY, kpi=_q3_rows()))
    assert r.status == "holds"
    gap = next(e for e in r.evidence if e["label"] == "Pair 1: gap")
    assert gap["value"] == pytest.approx(9.0) and gap["unit"] == "percentage points"


def test_q3_falsified_when_one_gap_is_short():
    assert evaluate.q3(Context(TODAY, kpi=_q3_rows(a2=130.0))).status == "falsified"


def test_q3_lower_bound_below_bar_is_undecidable():
    # Affirm growth only known as a lower bound (> +30%): a 3 pp gap cannot refute or confirm
    r = evaluate.q3(Context(TODAY, kpi=_q3_rows(a1=130.0, a2=136.0, qa=">")))
    assert r.status == "undecidable"


def test_q3_global_klarna_gmv_is_not_us(real):
    r = evaluate.q3(Context(TODAY, kpi=real["kpi_disclosures_other"]))
    assert r.status == "collecting" and "global" in r.reason
    seen = r.evidence[0]
    assert "already seen" in seen["label"] and seen["value"] > 0


# --- Q4 -------------------------------------------------------------------------------------------


def _lines(rows):
    base = {k: None for k in economics.LINE_DTYPES}
    recs = [base | {"waterfall": "corporate_card_x", "segment": "C", "company": "X", "layer": "x",
                    "line": evaluate.Q4_LINE, "role": "residual", "order": 0, "formula": "f", "inputs": "i",
                    "source_url": "https://example.org/x", "tag": "C", "chartable": True, "stale": False}
            | {"period": p, "as_of": a, "usd_per_100": v, "qualifier": "="} for p, a, v in rows]
    return pl.DataFrame(recs, schema=economics.LINE_DTYPES)


def test_q4_two_quarters_above_two_percent_falsify():
    r = evaluate.q4(Context(TODAY, waterfall_lines=_lines([("Q3", "2026-09-30", 2.1), ("Q4", "2026-12-31", 2.2)])))
    assert r.status == "falsified"


def test_q4_one_quarter_out_is_not_enough():
    r = evaluate.q4(Context(TODAY, waterfall_lines=_lines([("Q3", "2026-09-30", 2.1), ("Q4", "2026-12-31", 1.3)])))
    assert r.status == "collecting"


def test_q4_holds_after_final_grading_when_inside_band():
    r = evaluate.q4(Context(date(2027, 3, 1), waterfall_lines=_lines([("Q3", "2026-09-30", 1.3),
                                                                     ("Q4", "2026-12-31", 1.2)])))
    assert r.status == "holds"


def test_q4_pre_registration_quarter_is_only_already_seen():
    r = evaluate.q4(Context(date(2027, 3, 1), waterfall_lines=_lines([("Q2", "2026-06-30", 2.5)])))
    assert r.status == "collecting" and "2026-09-30" in r.reason


# --- Q5 / Q6 ---------------------------------------------------------------------------------------


def test_q5_klarna_split_missing(real):
    lines, _ = economics.waterfalls(real["waterfall_inputs"])
    assert evaluate.q5(Context(TODAY, waterfall_lines=lines)).status == "collecting"
    late = evaluate.q5(Context(date(2026, 12, 1), waterfall_lines=lines))
    assert late.status == "undecidable" and "does not disclose" in late.reason


def test_q6_waits_for_capital_one_then_undecidable(real):
    assert evaluate.q6(Context(TODAY, private=real["private_metrics"])).status == "collecting"
    assert evaluate.q6(Context(date(2027, 3, 1), private=real["private_metrics"])).status == "undecidable"


def test_q6_reported_revenue_never_enters_evidence(real):
    from pipelines.payments import ledger
    tables, _ = ledger.build(TODAY)
    r = evaluate.q6(Context(TODAY, private=real["private_metrics"], intervals=tables["private_intervals"]))
    assert all(e["value"] != 700000000 for e in r.evidence)
    assert {e["qualifier"] for e in r.evidence} == {">", "<"}


def test_q6_capital_one_disclosure_grades(real):
    disc = [{"company": "Brex", "metric": m, "period": "FY2026", "value": v, "qualifier": "=", "tag": "V",
             "chartable": True, "metric_kind": "counterparty_filing", "unit": "USD", "as_of_date": "2026-12-31",
             "source_url": "https://example.org/cof", "status": "current"}
            for m, v in (("net_interchange", 150e6), ("revenue", 700e6))]
    assert evaluate.q6(Context(TODAY, private=disc)).status == "holds"


# --- Q7 / Q8 ---------------------------------------------------------------------------------------


def _wf(line, period, value, wf):
    return {"waterfall": wf, "line": line, "period": period, "value": value, "qualifier": "=", "tag": "V",
            "chartable": True, "as_of_date": "2026-11-15", "source_url": "https://example.org/x"}


def test_q7_collecting_on_curated_rows(real):
    r = evaluate.q7(Context(TODAY, waterfall_rows=real["waterfall_inputs"]))
    assert r.status == "collecting" and "FSB 2026" in r.reason
    seen = next(e for e in r.evidence if e["label"].startswith("Wise take rate change"))
    assert seen["value"] == pytest.approx(-0.06)


@pytest.mark.parametrize("h1,fsb,want", [(0.48, 1.6, "holds"), (0.53, 1.6, "falsified"), (0.48, 1.4, "falsified"),
                                         (0.50, 1.6, "undecidable")])
def test_q7_grades(real, h1, fsb, want):
    rows = real["waterfall_inputs"] + [_wf("take_rate", "FY2027-H1", h1, "cross_border_wise"),
                                       _wf("avg_cost_b2b_msme_2026", "2026", fsb, "cross_border_fsb")]
    assert evaluate.q7(Context(TODAY, waterfall_rows=rows)).status == want


def test_q8_collecting_until_final_approval(real):
    r = evaluate.q8(Context(TODAY, waterfall_rows=real["waterfall_inputs"]))
    assert r.status == "collecting" and r.evidence[0]["value"] == 2.7


@pytest.mark.parametrize("rate,want", [(2.65, "holds"), (2.55, "falsified")])
def test_q8_first_sheet_after_approval(real, monkeypatch, rate, want):
    monkeypatch.setattr(evaluate, "Q8_FINAL_APPROVAL", date(2026, 12, 1))
    new = _wf("interchange_commercial_cnp_pct", "2027-04 schedule", rate, "corporate_card_bill") | {
        "as_of_date": "2027-04-17"}
    assert evaluate.q8(Context(TODAY, waterfall_rows=real["waterfall_inputs"] + [new])).status == want


def test_q8_void_is_undecidable(monkeypatch):
    monkeypatch.setattr(evaluate, "Q8_VOID", True)
    assert evaluate.q8(Context(TODAY)).status == "undecidable"


# --- Q9 -------------------------------------------------------------------------------------------


def test_acquisition_target_reads_the_id(real):
    targets = {r["id"]: evaluate.acquisition_target(r) for r in real["events"] if r["event_kind"] == "acquisition"}
    assert targets["capitalone:brex:announce"] == "Brex"
    assert targets["stripe:bridge:close"] == "Bridge"
    assert targets["avidxchange:take_private:announce"] == "AvidXchange"
    assert targets["nuvei:payoneer:announce"] == "Payoneer"
    assert targets["stripe:openrouter:announce"] is None
    assert targets["globalpayments:worldpay:announce"] is None


def _deal(target, d, value=None):
    return {"id": f"acq:{target.lower()}:announce", "company": target, "counterparty": "Acquirer",
            "event_kind": "acquisition", "event_date": d, "value": value, "unit": "USD" if value else None,
            "qualifier": "=" if value else None, "tag": "V", "chartable": True, "source_url": "https://example.org/x"}


def test_q9_real_rows_already_seen_and_collecting(real):
    r = evaluate.q9(Context(TODAY, events=real["events"], private=real["private_metrics"]))
    assert r.status == "collecting"
    assert r.evidence[0]["value"] == 3.0  # Brex, BVNK, Payoneer in the 12 months before registration


def test_q9_holds_with_two_small_independent_targets(real):
    ev = real["events"] + [_deal("Sezzle", "2027-01-10", 1e9), _deal("Flywire", "2027-03-01", 2e9),
                           _deal("Payoneer", "2027-02-01", 1e9), _deal("Shift4", "2027-04-01", 9e9)]
    r = evaluate.q9(Context(date(2027, 5, 1), events=ev))
    assert r.status == "holds"
    counted = next(e for e in r.evidence if e["label"].startswith("Qualifying"))
    assert counted["note"] == "Sezzle, Flywire"   # Payoneer excluded, Shift4 above $5B


def test_q9_falsified_after_deadline_with_one(real):
    r = evaluate.q9(Context(date(2027, 10, 1), events=real["events"] + [_deal("Sezzle", "2027-01-10", 1e9)]))
    assert r.status == "falsified"


def test_q9_unknown_valuation_is_not_counted():
    r = evaluate.q9(Context(TODAY, events=[_deal("Zip", "2026-12-01")]))
    assert r.status == "collecting"
    assert any(e["label"].startswith("Announced since registration, valuation") for e in r.evidence)


# --- the board ------------------------------------------------------------------------------------


def test_scoreboard_isolates_a_broken_question(monkeypatch):
    def boom(ctx):
        raise RuntimeError("bad input")
    monkeypatch.setattr(evaluate, "QUESTIONS", (evaluate.q1, boom, evaluate.q9))
    out = evaluate.scoreboard(Context(TODAY))
    assert [r.status for r in out] == ["collecting"] * 3
    assert "grading error" in out[1].reason


def test_statuses_are_the_registered_vocabulary():
    with pytest.raises(ValueError):
        evaluate.Result("Q1", "t", "maybe", "", [], "", "")
    assert config.STATUSES == ["collecting", "holds", "falsified", "undecidable"]
