"""Unit tests for pipelines.payments.share: four lenses, never summed, qualifiers carried through.

Synthetic inputs for every rule, plus tests on the real curated files (segment A's latest common period and
the Fed FEDS BNPL table). No test reads or writes the marts directory unless it passes a tmp path.
"""

from __future__ import annotations

from datetime import date

import pandera.errors
import polars as pl
import pytest

from pipelines.payments import reference
from pipelines.payments import share as s
from pipelines.payments.config import UNIVERSE

# --- helpers ---------------------------------------------------------------------------------

_QEND = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def kpi(company: str, metric: str, period: str, value: float, *, q: str = "=", unit: str = "USD",
        chartable: bool = True, kind: str = "filed_volume", as_of: str | None = None, tag: str = "V") -> dict:
    if as_of is None:
        if period.startswith("FY"):
            as_of = f"{period[2:6]}-12-31"
        elif period.startswith("H"):
            as_of = f"{period[3:]}-{'06-30' if period[1] == '1' else '12-31'}"
        else:
            as_of = f"{period[:4]}-{_QEND[int(period[-1])]}"
    return {"company": company, "metric": metric, "period": period, "value": value, "unit": unit,
            "qualifier": q, "metric_kind": kind, "chartable": chartable, "tag": tag, "as_of_date": as_of,
            "stale": False}


POOL = s.Pool(
    id="T_pool", segment="A", label="test pool", basis="test basis",
    members=(s.Member("Alpha", "vol", "Alpha", "global", "payment volume"),
             s.Member("Beta", "vol", "Beta", "global", "payment volume"),
             s.Member("Gamma", "vol", "Gamma", "global", "payment volume"),
             s.Member("Euro", "vol", "Euro", "global", "payment volume")),
    markers=(s.Marker("Private", "total_volume", "private test marker"),),
    not_in_pool={"Omega": "revenue only"},
)


def pool_rows(long: list[dict], period: str, role: str = "member") -> dict[str, dict]:
    return {r["company"]: r for r in long if r["period"] == period and r["role"] == role and r["pool"] == "T_pool"}


# --- qualifier algebra -----------------------------------------------------------------------


@pytest.mark.parametrize(("qs", "out"), [
    (["=", "="], "="), (["=", ">"], ">"), (["=", "<"], "<"), ([">", ">"], ">"),
    ([">", "<"], "~"), (["=", "~"], "~"), (["<", "~", ">"], "~"), ([], "="),
])
def test_q_sum(qs, out):
    assert s.q_sum(qs) == out


def test_q_ratio_flips_the_denominator():
    assert s.q_ratio(">", "=") == ">"
    assert s.q_ratio("=", ">") == "<"   # a lower-bound denominator gives an upper-bound ratio
    assert s.q_ratio("<", ">") == "<"
    assert s.q_ratio(">", ">") == "~"   # bounds pointing opposite ways: neither bound holds


def test_q_hhi_any_bound_is_approximate():
    assert s.q_hhi(["=", "="]) == "="
    assert s.q_hhi(["=", ">", "<"]) == "~"


def test_unknown_qualifier_refused():
    with pytest.raises(ValueError):
        s.q_sum(["=", "?"])


def test_bounds():
    assert s.bounds(10.0, "=") == (10.0, 10.0)
    assert s.bounds(10.0, ">") == (10.0, float("inf"))
    assert s.bounds(10.0, "<") == (0.0, 10.0)
    lo, hi = s.bounds(100.0, "~")
    assert lo < 100.0 < hi


# --- calendar alignment ----------------------------------------------------------------------


@pytest.mark.parametrize(("period", "as_of", "label"), [
    ("FY2026-Q4", "2026-06-30", "2026-Q2"),   # Affirm, fiscal year ends June
    ("FY2027-Q1", "2026-06-30", "2026-Q2"),   # Wise, fiscal year ends March
    ("2026-Q2", "2026-06-30", "2026-Q2"),
    ("FY2026-H2", "2026-06-30", "H1-2026"),   # Zip
    ("H2-2025", "2025-12-31", "H2-2025"),     # Adyen
    ("FY2025", "2025-12-31", "FY2025"),
])
def test_calendar_period_aligns_by_end_date(period, as_of, label):
    assert s.calendar_period(period, as_of) == (label, None)


@pytest.mark.parametrize(("period", "as_of"), [
    ("FY2027-Q2", "2026-07-31"),   # Navan: quarter ends July
    ("FY2026", "2026-06-30"),      # a June fiscal year is not a calendar year
    ("2026-03", "2026-03-31"),     # an annualised run rate stated in a month
])
def test_calendar_period_refuses_unaligned(period, as_of):
    label, reason = s.calendar_period(period, as_of)
    assert label is None and reason


# --- lens 1: disclosed pool ------------------------------------------------------------------


def _synthetic_kpi() -> list[dict]:
    rows = []
    for p, (a, b, c) in {"2026-Q1": (60, 30, 10), "2026-Q2": (50, 30, 20)}.items():
        rows += [kpi("Alpha", "vol", p, a), kpi("Beta", "vol", p, b), kpi("Gamma", "vol", p, c)]
    rows += [kpi("Euro", "vol", "H1-2026", 999, unit="EUR")]
    return rows


def test_pool_shares_and_hhi():
    long, hhi = s.disclosed_pool(_synthetic_kpi(), [], (POOL,))
    m = pool_rows(long, "2026-Q2")
    assert set(m) == {"Alpha", "Beta", "Gamma"}
    assert m["Alpha"]["value"] == pytest.approx(0.5)
    assert m["Gamma"]["value"] == pytest.approx(0.2)
    assert all(r["qualifier"] == "=" and r["unit"] == s.UNIT_POOL for r in m.values())
    h = next(x for x in hhi if x["period"] == "2026-Q2")
    assert h["hhi"] == pytest.approx(50**2 + 30**2 + 20**2)
    assert h["pool_total"] == pytest.approx(100) and h["n_members"] == 3 and h["qualifier"] == "="


def test_currency_mismatch_is_an_excluded_row_without_a_number():
    long, hhi = s.disclosed_pool(_synthetic_kpi(), [], (POOL,))
    euro = pool_rows(long, "2026-Q2", "excluded")["Euro"]
    assert euro["value"] is None and "EUR" in euro["refusal_reason"]
    h = next(x for x in hhi if x["period"] == "2026-Q2")
    assert h["pool_total"] == pytest.approx(100)  # the EUR 999 never reaches the sum
    assert "Euro (" in h["excluded"]


def test_lower_bound_member_propagates():
    rows = [kpi("Alpha", "vol", "2026-Q2", 50, q=">"), kpi("Beta", "vol", "2026-Q2", 30),
            kpi("Gamma", "vol", "2026-Q2", 20)]
    long, hhi = s.disclosed_pool(rows, [], (POOL,))
    m = pool_rows(long, "2026-Q2")
    assert m["Alpha"]["qualifier"] == ">"   # its own lower bound: its share is a lower bound
    assert m["Beta"]["qualifier"] == "<"    # the rest of the pool is a lower bound: others' shares are upper bounds
    h = next(x for x in hhi if x["period"] == "2026-Q2")
    assert h["total_qualifier"] == ">" and h["qualifier"] == "~"


def test_mixed_bounds_become_approximate():
    rows = [kpi("Alpha", "vol", "2026-Q2", 50, q=">"), kpi("Beta", "vol", "2026-Q2", 30, q="<"),
            kpi("Gamma", "vol", "2026-Q2", 20)]
    long, hhi = s.disclosed_pool(rows, [], (POOL,))
    m = pool_rows(long, "2026-Q2")
    assert m["Gamma"]["qualifier"] == "~"   # rest = '>' + '<'
    assert m["Alpha"]["qualifier"] == ">"   # self '>', rest '<' flips to '>'
    assert next(x for x in hhi if x["period"] == "2026-Q2")["total_qualifier"] == "~"


def test_non_chartable_and_run_rate_rows_are_excluded_with_reasons():
    rows = [kpi("Alpha", "vol", "2026-Q2", 50), kpi("Beta", "vol", "2026-Q2", 30, chartable=False, tag="S"),
            kpi("Gamma", "vol", "2026-Q2", 80, kind="stated_run_rate")]
    long, hhi = s.disclosed_pool(rows, [], (POOL,))
    ex = pool_rows(long, "2026-Q2", "excluded")
    assert "not chartable" in ex["Beta"]["refusal_reason"]
    assert "run rate" in ex["Gamma"]["refusal_reason"]
    # one member left: the share is refused, not 100%
    alpha = pool_rows(long, "2026-Q2")["Alpha"]
    assert alpha["value"] is None and "one-company" in alpha["refusal_reason"]
    h = next(x for x in hhi if x["period"] == "2026-Q2")
    assert h["hhi"] is None and h["refusal_reason"]


def test_year_pool_sums_quarters_and_carries_qualifiers():
    rows = [kpi("Alpha", "vol", f"2025-Q{i}", 10, q="~" if i == 4 else "=") for i in (1, 2, 3, 4)]
    rows += [kpi("Beta", "vol", "FY2025", 60)]
    rows += [kpi("Gamma", "vol", f"2025-Q{i}", 5) for i in (1, 2, 3)]  # Q4 missing: no FY sum
    long, hhi = s.disclosed_pool(rows, [], (POOL,))
    m = pool_rows(long, "FY2025")
    assert set(m) == {"Alpha", "Beta"}
    assert m["Alpha"]["value"] == pytest.approx(0.4) and m["Alpha"]["qualifier"] == "~"
    assert "complete calendar 2025" in pool_rows(long, "FY2025", "excluded")["Gamma"]["refusal_reason"]


def test_private_marker_never_enters_the_pool_sum():
    private = [kpi("Private", "total_volume", "FY2026", 10_000, kind="stated_volume"),
               kpi("Private", "total_volume", "FY2025", 9_000, kind="reported_talks", chartable=False),
               kpi("Private", "total_volume", "FY2024", 8_000, kind="stated_volume", chartable=False, tag="S")]
    rows = [kpi(c, "vol", f"2026-Q{i}", v) for c, v in (("Alpha", 6), ("Beta", 4)) for i in (1, 2, 3, 4)]
    long, hhi = s.disclosed_pool(rows, private, (POOL,))
    h = next(x for x in hhi if x["period"] == "FY2026")
    assert h["pool_total"] == pytest.approx(40)
    markers = [r for r in long if r["role"] == "marker"]
    assert len(markers) == 1  # unconfirmed and non-chartable private rows are dropped entirely
    mk = markers[0]
    assert mk["value"] == 10_000 and mk["unit"] == "USD" and mk["qualifier"] == "="
    assert "marker, not a share" in mk["basis"] and "same period" in mk["basis"]
    assert pool_rows(long, "FY2026")["Alpha"]["value"] == pytest.approx(0.6)


def test_not_in_pool_companies_are_listed():
    long, _ = s.disclosed_pool(_synthetic_kpi(), [], (POOL,))
    omega = [r for r in long if r["company"] == "Omega"]
    assert len(omega) == 1 and omega[0]["role"] == "excluded" and omega[0]["refusal_reason"] == "revenue only"


def test_every_core_company_is_accounted_for():
    """Each core roster company of A-E is a pool member, a marker, or excluded with a reason."""
    for seg in "ABCDE":
        pool = next(p for p in s.POOLS if p.segment == seg)
        covered = ({m.label for m in pool.members} | {m.company for m in pool.markers} | set(pool.not_in_pool))
        missing = set(UNIVERSE[seg]["core"]) - covered
        assert not missing, (seg, missing)
    assert "F" in s.NO_POOL and "F" in s.NO_DENOMINATOR


# --- lens 2: official denominator ------------------------------------------------------------


def _denom(series, metric, period, value, *, unit="USD", q="=", as_of="2025-12-31"):
    return {"series": series, "metric": metric, "period": period, "value": value, "unit": unit, "qualifier": q,
            "chartable": True, "as_of_date": as_of, "stale": False}


def test_feds_shares_hhi_and_reconciliation():
    denoms = [_denom(s.FEDS_SERIES, s.FEDS_TOTAL, "FY2025", 100e9)]
    vals = {"affirm": 40, "afterpay_block": 30, "klarna": 10, "paypal": 10, "sezzle": 5, "zip": 5}
    for k, v in vals.items():
        denoms.append(_denom(s.FEDS_SERIES, f"us_bnpl_issuance_{k}", "FY2025", v * 1e9))
        denoms.append(_denom(s.FEDS_SERIES, f"us_bnpl_issuance_share_{k}", "FY2025", v, unit="percent"))
    long, hhi, recon = s.official_denominator([], denoms, ())
    members = {r["company"]: r for r in long if r["role"] == "member"}
    assert members["Affirm"]["value"] == pytest.approx(0.4) and members["Affirm"]["qualifier"] == "="
    assert members["PayPal Pay Later"]["value"] == pytest.approx(0.1)
    assert hhi[0]["hhi"] == pytest.approx(sum(v**2 for v in vals.values()))
    assert hhi[0]["lens"] == "official_denominator"
    assert all(abs(r["computed_pp"] - r["printed_pp"]) < 1e-9 for r in recon)


def test_scope_mismatch_is_refused_with_a_reason():
    pool = s.Pool(id="B_t", segment="B", label="t", basis="t",
                  members=(s.Member("Glob", "GMV", "Glob", "global", "BNPL GMV"),))
    denoms = [_denom(s.FEDS_SERIES, s.FEDS_TOTAL, "FY2025", 100e9)]
    rows = [kpi("Glob", "GMV", "FY2025", 10e9)]
    long = s._company_vs_denominator(rows, denoms, (pool,))
    assert len(long) == 1
    r = long[0]
    assert r["role"] == "refused" and r["value"] is None
    assert "scope" in r["refusal_reason"] and "measure" in r["refusal_reason"]


def test_like_for_like_denominator_gives_a_qualified_number():
    pool = s.Pool(id="A_t", segment="A", label="t", basis="t",
                  members=(s.Member("Dom", "vol", "Dom", "US", "US retail e-commerce sales"),))
    denoms = [_denom("census_ecomm_nsa", "us_retail_ecommerce_sales", "2026-Q2", 400.0, as_of="2026-06-30")]
    rows = [kpi("Dom", "vol", "2026-Q2", 100.0, q=">")]
    long = s._company_vs_denominator(rows, denoms, (pool,))
    assert len(long) == 1 and long[0]["role"] == "member"
    assert long[0]["value"] == pytest.approx(0.25) and long[0]["qualifier"] == ">"


def test_period_mismatch_is_refused():
    pool = s.Pool(id="A_t", segment="A", label="t", basis="t",
                  members=(s.Member("Dom", "vol", "Dom", "US", "US retail e-commerce sales"),))
    denoms = [_denom("census_ecomm_nsa", "us_retail_ecommerce_sales", "2026-Q2", 400.0, as_of="2026-06-30")]
    long = s._company_vs_denominator([kpi("Dom", "vol", "2026-Q1", 100.0)], denoms, (pool,))
    assert long[0]["role"] == "refused" and long[0]["refusal_reason"].startswith("period")


# --- lens 3 and 4: marts that exist only after CI --------------------------------------------


def _adoption() -> pl.DataFrame:
    d = date(2026, 8, 1)
    rows = []
    for tech, n in (("Stripe", 5000), ("PayPal", 4000), ("Adyen", 69), ("Apple Pay", 9000)):
        for geo in ("ALL", "United States of America"):
            for rank in ("Top 10k", "Top 100k", "ALL"):
                for client in ("mobile", "desktop"):
                    rows.append({"technology": tech, "date": d, "geo": geo, "rank": rank, "client": client,
                                 "origins": n, "total_origins": 100_000, "share": n / 100_000})
    return pl.DataFrame(rows, schema={"technology": pl.Utf8, "date": pl.Date, "geo": pl.Utf8, "rank": pl.Utf8,
                                      "client": pl.Utf8, "origins": pl.Int64, "total_origins": pl.Int64,
                                      "share": pl.Float64})


def test_web_coverage_from_adoption():
    out = s.web_coverage(_adoption())
    assert {r["company"] for r in out} == {"Stripe", "PayPal", "Adyen"}  # wallets are not roster companies
    stripe = [r for r in out if r["company"] == "Stripe"]
    assert len(stripe) == 12 and all(r["value"] == pytest.approx(0.05) for r in stripe)
    assert all(r["unit"] == s.UNIT_ORIGINS and "coverage, not share" in r["basis"] for r in stripe)
    adyen = [r for r in out if r["company"] == "Adyen"]
    assert all(r["value"] is None and "barely detected" in r["refusal_reason"] for r in adyen)


def _monthly(side: str, series: dict[str, tuple[str, int]], months: list[date]) -> pl.DataFrame:
    rows = []
    for m in months:
        for name, (company, n) in series.items():
            rows.append({"registry": "npm", "side": side, "series": name, "company": company, "packages": name,
                         "period": m, "downloads": n, "base_month": None, "index": None})
    return pl.DataFrame(rows, schema={"registry": pl.Utf8, "side": pl.Utf8, "series": pl.Utf8,
                                      "company": pl.Utf8, "packages": pl.Utf8, "period": pl.Date,
                                      "downloads": pl.Int64, "base_month": pl.Date, "index": pl.Float64})


def test_downloads_share_latest_complete_month():
    full = {c: (c, n) for c, n in zip(s.Q1_COMPANIES, (80, 5, 10, 3, 2), strict=True)}
    cm = _monthly("client", full, [date(2026, 7, 1), date(2026, 8, 1)])
    partial = _monthly("client", {"Stripe": ("Stripe", 99)}, [date(2026, 9, 1)])  # incomplete month ignored
    out = s.developer_downloads(pl.concat([cm, partial]), None)
    assert {r["period"] for r in out} == {"2026-08"}
    assert sum(r["value"] for r in out) == pytest.approx(1.0)
    assert next(r for r in out if r["company"] == "Stripe")["value"] == pytest.approx(0.8)
    assert next(r for r in out if r["company"] == "Airwallex")["segment"] == "D"


def test_absent_marts_give_empty_lenses_and_warn_checks(tmp_path):
    t = s.build(marts_dir=tmp_path)
    assert t.long.filter(pl.col("lens").is_in(["web_coverage", "developer_downloads"])).is_empty()
    by = {c["name"]: c for c in t.checks}
    assert by["Share input: web coverage"]["status"] == "warn"
    assert by["Share input: developer downloads"]["status"] == "warn"


def test_corrupt_mart_fails_its_lens_only(tmp_path):
    (tmp_path / "webtech_adoption.parquet").write_bytes(b"not a parquet file at all")
    t = s.build(marts_dir=tmp_path)
    assert t.long.filter(pl.col("lens") == "disclosed_pool").height > 0
    assert t.long.filter(pl.col("lens") == "official_denominator").height > 0
    assert t.long.filter(pl.col("lens") == "web_coverage").is_empty()
    by = {c["name"]: c for c in t.checks}
    assert by["Share input: web coverage"]["status"] == "fail"
    assert "read/derive failed" in by["Share input: web coverage"]["detail"]
    assert by["Share input: developer downloads"]["status"] == "warn"  # absent, not broken


def test_drifted_mart_columns_fail_their_lens_only(tmp_path):
    pl.DataFrame({"unexpected": [1, 2]}).write_parquet(tmp_path / "devstats_client_monthly.parquet")
    t = s.build(marts_dir=tmp_path)
    assert t.long.filter(pl.col("lens") == "disclosed_pool").height > 0
    assert t.long.filter(pl.col("lens") == "developer_downloads").is_empty()
    by = {c["name"]: c for c in t.checks}
    assert by["Share input: developer downloads"]["status"] == "fail"
    assert by["Share input: web coverage"]["status"] == "warn"


def test_build_with_marts_present(tmp_path):
    _adoption().write_parquet(tmp_path / "webtech_adoption.parquet")
    full = {c: (c, n) for c, n in zip(s.Q1_COMPANIES, (80, 5, 10, 3, 2), strict=True)}
    _monthly("client", full, [date(2026, 8, 1)]).write_parquet(tmp_path / "devstats_client_monthly.parquet")
    t = s.build(marts_dir=tmp_path)
    assert t.long.filter(pl.col("lens") == "web_coverage").height > 0
    assert t.long.filter(pl.col("lens") == "developer_downloads").height == 5
    assert {c["name"]: c["status"] for c in t.checks}["Share input: web coverage"] == "pass"
    paths = s.write(t, tmp_path / "out")
    assert all(p.exists() for p in paths)


# --- schemas ---------------------------------------------------------------------------------


def _long_row(**over) -> dict:
    r = s._row(segment="A", lens="disclosed_pool", pool="p", company="X", period="2026-Q2", value=0.5,
               unit=s.UNIT_POOL, qualifier="=", role="member", basis="b", source_metric="X: vol")
    r.update(over)
    return r


@pytest.mark.parametrize("over", [
    {"value": 1.2},                                         # share above 1
    {"refusal_reason": "why"},                              # value and reason together
    {"value": None},                                        # neither value nor reason
    {"qualifier": None},                                    # value without qualifier
    {"role": "excluded", "refusal_reason": None},           # excluded row carrying a number
    {"lens": "market_share"},                               # not a registered lens
])
def test_long_schema_rejects(over):
    df = s._frame([_long_row(**over)], s.LONG_DTYPES)
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        s.LONG_SCHEMA.validate(df)


def test_long_schema_accepts_a_refusal():
    df = s._frame([_long_row(value=None, qualifier=None, role="refused", refusal_reason="scope")], s.LONG_DTYPES)
    s.LONG_SCHEMA.validate(df)


# --- the real curated files ------------------------------------------------------------------


@pytest.fixture(scope="module")
def real() -> s.ShareTables:
    return s.build(read_marts=False)


def test_real_segment_a_latest_common_period(real):
    period = s.latest_common_period(real.hhi, "A_payment_volume")
    assert period is not None
    h = real.hhi.filter((pl.col("pool") == "A_payment_volume") & (pl.col("period") == period)).row(0, named=True)
    members = set(h["members"].split(", "))
    assert {"PayPal", "Block/Square", "Shopify Payments", "Toast", "Shift4"} <= members
    assert not members & {"Stripe", "Checkout.com", "Adyen", "Fiserv Clover"}

    # recompute from the curated rows, independently of share.py's resolver
    metrics = {"PayPal": "tpv", "Block": "square_gpv", "Shopify": "gross_payments_volume", "Toast": "gpv",
               "Shift4": "volume"}
    rows = reference.rows("kpi_disclosures_acceptance")
    vals = {c: next(r for r in rows if r["company"] == c and r["metric"] == m and r["period"] == period)
            for c, m in metrics.items()}
    assert all(v["chartable"] for v in vals.values())
    total = sum(v["value"] for v in vals.values())
    assert h["pool_total"] == pytest.approx(total)
    shares = real.long.filter((pl.col("pool") == "A_payment_volume") & (pl.col("period") == period)
                              & (pl.col("role") == "member"))
    paypal = shares.filter(pl.col("company") == "PayPal").row(0, named=True)
    assert paypal["value"] == pytest.approx(vals["PayPal"]["value"] / total)
    assert shares["value"].sum() == pytest.approx(1.0)
    assert h["hhi"] == pytest.approx(sum((v["value"] / total * 100) ** 2 for v in vals.values()))
    expected_q = "=" if all(v["qualifier"] == "=" for v in vals.values()) else "~"
    assert h["qualifier"] == expected_q
    assert set(shares["qualifier"].to_list()) == {expected_q}

    excl = real.long.filter((pl.col("pool") == "A_payment_volume") & (pl.col("period") == period)
                            & (pl.col("role") == "excluded"))
    reasons = dict(zip(excl["company"].to_list(), excl["refusal_reason"].to_list(), strict=True))
    assert "EUR" in reasons["Adyen"] and "run rate" in reasons["Fiserv Clover"]
    stripe = real.long.filter((pl.col("company") == "Stripe") & (pl.col("role") == "marker"))
    assert stripe.height >= 1 and stripe["value"].is_not_null().all()


def test_real_feds_table(real):
    feds = real.hhi.filter(pl.col("pool") == s.FEDS_SERIES).row(0, named=True)
    assert feds["n_members"] == 6 and 2300 < feds["hhi"] < 2500  # PLAN §3 reads about 2,400
    assert {c["name"]: c["status"] for c in real.checks}["FEDS shares reconcile"] == "pass"
    afterpay = real.long.filter((pl.col("pool") == s.FEDS_SERIES) & (pl.col("company") == "Afterpay/Block"))
    assert afterpay["value"][0] == pytest.approx(53.7 / 156.7, abs=1e-3)


def test_real_tables_never_mix_lenses_or_layers(real):
    # each HHI row is one pool in one lens and one layer
    assert real.hhi.group_by(["pool", "period"]).agg(pl.col("lens").n_unique().alias("n"),
                                                     pl.col("segment").n_unique().alias("k")
                                                     ).filter((pl.col("n") > 1) | (pl.col("k") > 1)).is_empty()
    # every disclosed-pool share row belongs to a pool whose layer is its own
    pools = {p.id: p.segment for p in s.POOLS}
    pool_rows_ = real.long.filter(pl.col("lens") == "disclosed_pool")
    assert all(pools.get(p, seg[0]) == seg for p, seg in zip(pool_rows_["pool"], pool_rows_["segment"], strict=True)
               if p in pools)
    # no number from an unconfirmed or non-chartable private row
    private = reference.rows("private_metrics")
    banned = {r["value"] for r in private if not r["chartable"]} - {r["value"] for r in private if r["chartable"]}
    assert not set(real.long.filter(pl.col("role") == "marker")["value"].to_list()) & banned
    # C, D, E, F have no official denominator: refusals, no numbers
    off = real.long.filter((pl.col("lens") == "official_denominator") & pl.col("segment").is_in(["C", "D", "E", "F"]))
    assert off.height >= 4 and off["value"].is_null().all()
    # acquiring has no like-for-like official denominator in the curated files: every row is refused
    acq = real.long.filter((pl.col("lens") == "official_denominator") & (pl.col("segment") == "A"))
    assert acq.height > 0 and acq["value"].is_null().all()


def test_real_checks_pass_except_absent_marts(real):
    bad = [c for c in real.checks if c["status"] == "fail"]
    assert not bad, bad


# --- Q1 interval -----------------------------------------------------------------------------


def test_q1_interval_synthetic():
    kpi_rows = [kpi("PayPal", "tpv", f"2025-Q{i}", 100.0) for i in (1, 2, 3, 4)]
    kpi_rows += [kpi("Adyen", "processed_volume", p, 100.0, unit="EUR") for p in ("H1-2025", "H2-2025")]
    private = [kpi("Stripe", "total_volume", "FY2025", 400.0, kind="stated_volume"),
               kpi("Checkout.com", "total_payment_volume", "FY2025", 100.0, q=">", kind="stated_volume"),
               kpi("Airwallex", "annualized_transaction_volume", "2025-10", 50.0, kind="stated_volume",
                   as_of="2025-10-31")]
    out = s.q1_volume_share(kpi_rows, private, 2025, eur_usd=1.5)
    # PayPal TPV includes Venmo/P2P with no merchant split: it enters as [0, 400]
    assert out["members"]["PayPal"]["low"] == 0.0
    assert out["members"]["PayPal"]["high"] == pytest.approx(400.0)
    # upper = 400 / (400 + PayPal 0 + Adyen 300 + Checkout 100 + Airwallex 0)
    assert out["upper"] == pytest.approx(400 / 800)
    # context only: PayPal at its full TPV (not a bound)
    assert out["upper_if_paypal_full_tpv"] == pytest.approx(400 / 1200)
    assert out["upper"] > out["upper_if_paypal_full_tpv"]
    assert out["lower"] == 0.0  # Checkout.com is unbounded above
    assert out["members"]["Airwallex"]["low"] == 0.0
    loose = s.q1_volume_share(kpi_rows, private, 2025)  # no FX: Adyen unbounded below, a looser upper bound
    assert loose["upper"] == pytest.approx(400 / 500) and loose["upper"] > out["upper"]


def test_q1_paypal_missing_has_no_full_tpv_variant():
    private = [kpi("Stripe", "total_volume", "FY2025", 400.0, kind="stated_volume")]
    out = s.q1_volume_share([], private, 2025)
    assert out["members"]["PayPal"]["low"] == 0.0 and out["members"]["PayPal"]["high"] is None
    assert out["upper_if_paypal_full_tpv"] is None
    assert out["upper"] == pytest.approx(1.0)
