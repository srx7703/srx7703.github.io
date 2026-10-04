"""Per-$100 waterfalls, qualifier propagation and the sensitivity matrix.

The golden tests read the real curated file (data/reference/payments/waterfall_inputs.json) and restate the
arithmetic by hand, so a change to either the file or the code that moves a published line fails here.
"""

from __future__ import annotations

import json
from datetime import date

import pandera.errors
import polars as pl
import pytest

from pipelines.payments import economics as E
from pipelines.payments import reference
from pipelines.payments.economics import Num, QualifierConflict, propagate, q_div, q_sub, q_sum

TODAY = date(2026, 10, 4)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return reference.rows("waterfall_inputs", TODAY)


@pytest.fixture(scope="module")
def lines(rows) -> pl.DataFrame:
    df, checks = E.waterfalls(rows)
    assert not [c for c in checks if c["status"] == "fail"], checks
    return df


def line(df: pl.DataFrame, waterfall: str, name: str, period: str | None = None) -> dict:
    hit = df.filter((pl.col("waterfall") == waterfall) & (pl.col("line") == name))
    if period:
        hit = hit.filter(pl.col("period") == period)
    assert hit.height == 1, (waterfall, name, period, hit)
    return hit.row(0, named=True)


# --- qualifier rule --------------------------------------------------------------------------


@pytest.mark.parametrize(("terms", "expected"), [
    ([("=", 1), ("=", -1)], "="),
    ([(">", 1), ("=", 1)], ">"),            # lower-bounded input in a sum -> lower bound
    ([(">", -1)], "<"),                      # ... subtracted or in a denominator -> upper bound
    ([("<", -1)], ">"),
    ([(">", 1), ("<", -1)], ">"),            # both push the same way
    ([(">", 1), (">", 1)], ">"),
    ([(">", 1), ("<", 1)], "~"),             # opposite bounds -> no bound
    ([(">", 1), (">", -1)], "~"),
    ([("~", 1), ("=", 1)], "~"),             # an estimate anywhere makes an estimate
    ([("~", 1), (">", 1)], "~"),
])
def test_propagate_rule(terms, expected):
    assert propagate(terms) == expected


def test_strict_refuses_opposite_bounds():
    with pytest.raises(QualifierConflict):
        propagate([(">", 1), ("<", 1)], strict=True)
    # strict only bites on a real conflict
    assert propagate([(">", 1), ("<", -1)], strict=True) == ">"
    assert propagate([("~", 1), (">", 1)], strict=True) == "~"


def test_operations_carry_qualifiers():
    # ">$1B revenue" over "$100B volume" is "more than 1%"
    assert q_div(Num(1e9, ">"), Num(1e11)) == Num(0.01, ">")
    # "$1B" over ">$100B" is "less than 1%"
    assert q_div(Num(1e9), Num(1e11, ">")).qualifier == "<"
    # ">" over "<" is still a lower bound
    assert q_div(Num(1e9, ">"), Num(1e11, "<")).qualifier == ">"
    # ">" over ">" has no bound: '~' by default, refused when strict
    assert q_div(Num(1e9, ">"), Num(1e11, ">")).qualifier == "~"
    with pytest.raises(QualifierConflict):
        q_div(Num(1e9, ">"), Num(1e11, ">"), strict=True)
    assert q_sub(Num(5, ">"), Num(1, "<")) == Num(4, ">")
    assert q_sub(Num(5, ">"), Num(1, ">")).qualifier == "~"
    assert q_sum(Num(1, ">"), Num(2, "<")).qualifier == "~"
    assert E.q_neg(Num(3, ">")) == Num(-3, "<")


def test_bad_qualifier_rejected():
    with pytest.raises(ValueError):
        Num(1.0, "≥")
    with pytest.raises(ValueError):
        propagate([("?", 1)])


@pytest.mark.parametrize(("q", "expected"), [
    ("=", (5.0, 5.0, 5.0)), (">", (5.0, None, None)), ("<", (None, 5.0, None)), ("~", (None, None, 5.0))])
def test_interval(q, expected):
    assert E.interval(5.0, q) == expected


def test_registered_threshold_parsers():
    assert E.pct("1.6%") == 1.6
    assert E.bps("10 bps") == pytest.approx(0.10)
    assert E.rate_plus_fixed("2.60% + $0.10") == (2.60, 0.10)
    assert E.per_100(2.04, 0.10) == pytest.approx(2.14)
    with pytest.raises(ValueError):
        E.pct("1.6")


# --- golden lines ----------------------------------------------------------------------------


def test_golden_visa_sheet_lines(lines):
    # Visa sheet effective 2026-04-18, Traditional Rewards CNP: 2.04% + $0.10 on a $100 ticket
    #   = 2.04 + 0.10 x 100 / 100 = 2.14
    iss = line(lines, "card_acquiring_us", "interchange_visa_traditional_rewards_cnp")
    assert iss["usd_per_100"] == pytest.approx(2.14, abs=1e-9)
    # Stripe list price 2.9% + $0.30 = 3.20; residual to acquirer + network = 3.20 - 2.14 = 1.06
    assert line(lines, "card_acquiring_us", "merchant_price_stripe_list")["usd_per_100"] == pytest.approx(3.20)
    res = line(lines, "card_acquiring_us", "acquirer_and_network_residual")
    assert res["usd_per_100"] == pytest.approx(1.06, abs=1e-9)
    assert res["role"] == "residual"
    # Visa Commercial CNP 2.70% + $0.10 = 2.80 (Q8's "current" reading)
    ref = line(lines, "corporate_card_bill", "visa_commercial_cnp_list")
    assert ref["usd_per_100"] == pytest.approx(2.70 + 0.10, abs=1e-9)
    assert ref["role"] == "reference"  # never added to BILL's lines
    gap = line(lines, "card_acquiring_us", "network_fees")
    assert gap["role"] == "gap" and gap["usd_per_100"] is None and gap["qualifier"] is None


def test_golden_bill_interchange(lines):
    # BILL 8-K, quarter ending 2026-06-30, Spend and Expense:
    #   transaction fees 184.6M / TPV 7,100M x 100 = 2.6000
    #   rewards           94.0M / 7,100M x 100     = 1.323944 (a cost, so -1.323944)
    #   net (184.6 - 94.0) / 7,100 x 100 = 90.6 / 71 = 1.276056
    assert line(lines, "corporate_card_bill", "transaction_fees")["usd_per_100"] == pytest.approx(2.6, abs=1e-6)
    assert line(lines, "corporate_card_bill", "rewards")["usd_per_100"] == pytest.approx(-94.0 / 71, abs=1e-6)
    net = line(lines, "corporate_card_bill", "net_interchange_after_rewards")
    assert net["usd_per_100"] == pytest.approx(90.6 / 71, abs=1e-6)
    assert round(net["usd_per_100"], 2) == 1.28  # section H, Q4 "already seen"
    assert net["period"] == "FY2026-Q4" and net["qualifier"] == "="


def test_golden_affirm_fy2026_per_gmv(lines):
    gmv = 50_200_000_000
    # merchant network revenue 1,149,932,000 / 50,200,000,000 x 100 = 2.290701
    assert line(lines, "bnpl_affirm", "merchant_network")["usd_per_100"] == pytest.approx(
        1_149_932_000 / gmv * 100, abs=1e-6)
    assert line(lines, "bnpl_affirm", "merchant_network")["usd_per_100"] == pytest.approx(2.290701, abs=1e-6)
    # interest income 2,047,485,000 / GMV x 100 = 4.078655
    assert line(lines, "bnpl_affirm", "interest_income")["usd_per_100"] == pytest.approx(4.078655, abs=1e-6)
    # provision for credit losses -796,650,000 / GMV x 100 = -1.586952
    assert line(lines, "bnpl_affirm", "provision_for_credit_losses")["usd_per_100"] == pytest.approx(
        -1.586952, abs=1e-6)
    # revenue less transaction costs:
    #   (1,149,932 + 293,990 + 2,047,485 + 596,553 + 173,123 [= 4,261,083, the filed total is 4,261,082])
    #   - (311,864 + 796,650 + 454,016 + 613,587 [= 2,176,117])  = 2,084,966 thousand
    #   / 50,200,000 thousand x 100 = 4.153319  -> "4.15" in section H, Q5
    rltc = line(lines, "bnpl_affirm", "revenue_less_transaction_costs")
    assert rltc["usd_per_100"] == pytest.approx(2_084_966_000 / gmv * 100, abs=1e-6)
    assert round(rltc["usd_per_100"], 2) == 4.15
    assert rltc["tag"] == "C" and rltc["chartable"]


def test_affirm_layers_sum_within_waterfall(lines):
    totals = E.layer_totals(lines).filter(pl.col("waterfall") == "bnpl_affirm")
    by = dict(zip(totals["layer"], totals["usd_per_100"], strict=True))
    # credit side = interest + gain on sale + servicing = (2,047,485 + 596,553 + 173,123) / 50,200,000 x 100
    assert by["credit side"] == pytest.approx(2_817_161 / 50_200_000 * 100, abs=1e-5)
    adds = lines.filter((pl.col("waterfall") == "bnpl_affirm") & pl.col("role").is_in(["take", "cost"]))
    assert adds["usd_per_100"].sum() == pytest.approx(
        line(lines, "bnpl_affirm", "revenue_less_transaction_costs")["usd_per_100"], abs=1e-5)
    # grouping never crosses books
    assert E.layer_totals(lines).select(["waterfall", "period", "layer"]).is_unique().all()


def test_wise_and_fsb_are_not_added(lines):
    wise = line(lines, "cross_border_wise", "cross_border_take_rate", "FY2026")
    assert wise["usd_per_100"] == 0.52 and wise["tag"] == "V"
    fsb = line(lines, "cross_border_fsb", "avg_cost_b2b_msme", "2025")
    assert fsb["role"] == "benchmark"
    assert "benchmark" not in E.ADDITIVE_ROLES and "reference" not in E.ADDITIVE_ROLES


def test_reconciliation_checks_pass(lines, rows):
    checks = E.reconciliation_checks(lines, rows)
    assert checks and all(c["status"] == "pass" for c in checks), checks


def test_only_chartable_lines_reach_the_mart(lines):
    assert lines["chartable"].all()
    assert set(lines["tag"]) <= {"V", "C"}


def test_non_chartable_input_drops_its_book_only(rows):
    broken = [dict(r, chartable=False, tag="S") if r["line"] == "gmv" else r for r in rows]
    df, checks = E.waterfalls(broken)
    assert "bnpl_affirm" not in set(df["waterfall"])
    assert {"card_acquiring_us", "corporate_card_bill", "cross_border_wise"} <= set(df["waterfall"])
    assert any(c["name"] == "Waterfall bnpl_affirm" and c["status"] == "fail" and "not chartable" in c["detail"]
               for c in checks)


def test_bound_input_propagates_into_lines(rows):
    # If GMV were stated as "more than $50.2B", every per-GMV revenue line becomes an upper bound.
    bounded = [dict(r, qualifier=">") if r["line"] == "gmv" else r for r in rows]
    df, _ = E.waterfalls(bounded)
    assert line(df, "bnpl_affirm", "merchant_network")["qualifier"] == "<"
    assert line(df, "bnpl_affirm", "provision_for_credit_losses")["qualifier"] == ">"  # a negative line flips
    assert line(df, "bnpl_affirm", "revenue_less_transaction_costs")["qualifier"] == "~"


def test_line_schema_rejects_unverified_tag(lines):
    bad = lines.with_columns(pl.when(pl.col("order") == 0).then(pl.lit("S")).otherwise(pl.col("tag")).alias("tag"))
    with pytest.raises(pandera.errors.SchemaError):
        E.LINE_SCHEMA.validate(bad)


# --- sensitivity -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sens(rows) -> pl.DataFrame:
    df, checks = E.sensitivity(rows)
    assert not checks, checks
    return df


def test_q4_verdicts_follow_registered_band():
    assert E.q4_verdict(1.28) == "inside_band"
    assert E.q4_verdict(1.0) == "inside_band" and E.q4_verdict(1.6) == "inside_band"
    assert E.q4_verdict(1.7) == "outside_band" and E.q4_verdict(0.9) == "outside_band"
    assert E.q4_verdict(2.01) == "refute_zone" and E.q4_verdict(0.79) == "refute_zone"


def test_q4_grid_and_breakeven(sens):
    q4 = sens.filter(pl.col("question") == "Q4")
    base = q4.filter(pl.col("scenario") == "base").row(0, named=True)
    assert base["output_value"] == pytest.approx(90.6 / 71, abs=1e-6)
    assert (base["band_low"], base["band_high"], base["refute_low"], base["refute_high"]) == (1.0, 1.6, 0.8, 2.0)
    # 50% rewards at gross 2.60: 2.60 x 0.5 = 1.30
    r = q4.filter((pl.col("scenario") == "rewards_share|gross_cut_0bps") & (pl.col("input_value") == 50.0))
    assert r["output_value"][0] == pytest.approx(1.30)
    # break-even: net = 1.6 when rewards share = 1 - 1.6 / 2.6 = 38.46%
    be = q4.filter((pl.col("scenario") == "breakeven_rewards_share") & (pl.col("input_value") == 1.6))
    assert be["output_value"][0] == pytest.approx((1 - 1.6 / 2.6) * 100, abs=1e-4)


def test_q7_verdicts():
    assert E.q7_wise_verdict(-0.06) == "holds"
    assert E.q7_wise_verdict(-0.03) == "holds"      # exactly 3 bps holds
    assert E.q7_wise_verdict(-0.02) == "neither"
    assert E.q7_wise_verdict(0.0) == "refuted"     # flat refutes
    assert E.q7_fsb_verdict(1.5) == "holds" and E.q7_fsb_verdict(1.45) == "neither"
    assert E.q7_fsb_verdict(1.4) == "refuted"


def test_q8_verdicts_and_rows(sens):
    assert E.q8_verdict(0.10) == "holds"      # 2.70 - 0.10 = 2.60, at the floor
    assert E.q8_verdict(0.15) == "refuted"    # more than 10 bps
    q8 = sens.filter((pl.col("question") == "Q8") & (pl.col("output") == "interchange_usd_per_100"))
    cut10 = q8.filter(pl.col("input_value") == 10.0).row(0, named=True)
    assert cut10["output_value"] == pytest.approx(2.70)  # 2.60 + 0.10 on a $100 ticket
    assert cut10["verdict"] == "holds"
    assert q8.filter(pl.col("input_value") == 15.0)["verdict"][0] == "refuted"


def test_m5_credit_loss_shock(sens):
    m = sens.filter((pl.col("question") == "M5") & (pl.col("scenario") == "credit_losses"))
    up = m.filter(pl.col("input_value") == 50.0).row(0, named=True)
    # +50% provision = 0.5 x 796,650,000 / 50.2B x 100 = 0.793476 off RLTC
    assert up["delta"] == pytest.approx(-0.5 * 796_650_000 / 50_200_000_000 * 100, abs=1e-6)
    nd = sens.filter(pl.col("verdict") == "not_derivable")
    assert nd.height >= 2 and nd["output_value"].is_null().all()


def test_build_and_write(tmp_path):
    tables, checks = E.build(TODAY)
    assert checks[0]["name"] == "Waterfalls" and checks[0]["status"] == "pass"
    paths = E.write(tables, tmp_path)
    assert {p.name for p in paths} == {"waterfall_lines.json", "sensitivity.json"}
    data = json.loads((tmp_path / "waterfall_lines.json").read_text())
    assert data and all(r["chartable"] for r in data)


def test_build_survives_unreadable_reference(monkeypatch):
    def boom(kind, today=None):
        raise ValueError("bad file")
    monkeypatch.setattr(reference, "rows", boom)
    tables, checks = E.build(TODAY)
    assert tables["waterfall_lines"].is_empty()
    assert checks[0]["status"] == "fail"
