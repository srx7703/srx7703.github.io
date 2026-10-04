"""Unit tests for pipelines.payments.devstats, offline.

fixtures/devstats/ copies the response shapes of api.npmjs.org/downloads/range (including npm's trailing 0
for a day not yet computed and its error body) and pypistats.org/api/packages/{pkg}/overall. Long series are
synthesised by a fake client that parses the requested URL, so chunking and URL building are exercised too.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import polars as pl
import pytest

from pipelines.payments import config
from pipelines.payments import devstats as d

FIX = Path(__file__).parent / "fixtures" / "devstats"
URL_RX = re.compile(r"^https://api\.npmjs\.org/downloads/range/(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})/(.+)$")

#: Per-day downloads by package for the synthetic series.
DAILY = {
    "@stripe/stripe-js": 1000, "@adyen/adyen-web": 40, "@paypal/paypal-js": 50, "braintree-web": 30,
    "@checkout.com/checkout-web-components": 5, "@airwallex/components-sdk": 10,
    "stripe": 3000, "@adyen/api-library": 35, "braintree": 60,
}


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeNpm:
    def __init__(self, fail: dict[str, Exception] | None = None, lag_days: int = 0, scale: dict | None = None) -> None:
        self.fail = fail or {}
        self.lag_days = lag_days
        self.scale = scale or {}
        self.urls: list[str] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.urls.append(path)
        m = URL_RX.match(path)
        assert m, path
        start, end, pkg_path = date.fromisoformat(m[1]), date.fromisoformat(m[2]), m[3]
        pkg = unquote(pkg_path)
        if pkg in self.fail:
            raise self.fail[pkg]
        last_real = end - timedelta(days=self.lag_days)
        days, cur = [], start
        while cur <= end:
            n = DAILY[pkg] if cur <= last_real else 0
            n = int(n * self.scale.get((pkg, cur.strftime("%Y-%m")), 1))
            days.append({"downloads": n, "day": cur.isoformat()})
            cur += timedelta(days=1)
        return {"start": start.isoformat(), "end": end.isoformat(), "package": pkg, "downloads": days}


class FakePypi:
    def __init__(self, start: date, end: date, fail: set[str] | None = None) -> None:
        self.start, self.end, self.fail = start, end, fail or set()
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        pkg = path.rstrip("/").split("/")[-2]
        if pkg in self.fail:
            raise httpx.HTTPStatusError("HTTP 429", request=httpx.Request("GET", path),
                                        response=httpx.Response(429))
        rows, cur = [], self.start
        while cur <= self.end:
            rows.append({"category": "with_mirrors", "date": cur.isoformat(), "downloads": 999})
            rows.append({"category": "without_mirrors", "date": cur.isoformat(), "downloads": 100})
            cur += timedelta(days=1)
        return {"data": rows, "package": pkg, "type": "overall_downloads"}


# --- whitelist ------------------------------------------------------------------------------


def test_client_whitelist_matches_config_and_never_includes_wrappers():
    assert set(d.CLIENT_PACKAGES) == {p for ps in config.NPM_CLIENT_SDKS.values() for p in ps}
    for w in config.NPM_EXCLUDED_WRAPPERS + config.NOT_COMPANY_PACKAGES["npm"]:
        assert w not in d.CLIENT_PACKAGES
    assert d.CLIENT_PACKAGES["braintree-web"] == "PayPal"


def test_pypi_whitelist_excludes_not_company_packages():
    assert set(d.PYPI_PACKAGES) == {"stripe", "adyen", "braintree"}
    assert not set(d.PYPI_PACKAGES) & set(config.NOT_COMPANY_PACKAGES["pypi"])


def test_wrapper_in_whitelist_is_refused(monkeypatch):
    monkeypatch.setitem(d.NPM_CLIENT_SDKS, "Stripe", ["@stripe/stripe-js", "@stripe/react-stripe-js"])
    with pytest.raises(ValueError, match="wrapper"):
        d._client_packages()


def test_registered_q1_thresholds_parsed():
    assert (d.Q1_HOLDS, d.Q1_REFUTED, d.Q1_CONSECUTIVE) == (2.0, 1.5, 3)


# --- urls and chunks -----------------------------------------------------------------------


def test_scoped_package_url_keeps_literal_scope_slash():
    url = d.npm_range_url("@stripe/stripe-js", date(2026, 1, 1), date(2026, 6, 30))
    assert url == "https://api.npmjs.org/downloads/range/2026-01-01:2026-06-30/@stripe/stripe-js"
    assert "%2F" not in url and "%40" not in url
    assert d.npm_path("@checkout.com/checkout-web-components") == "@checkout.com/checkout-web-components"
    assert d.npm_path("braintree-web") == "braintree-web"
    # httpx sends the path unchanged (no re-encoding of '@' or '/')
    assert httpx.URL(url).raw_path == b"/downloads/range/2026-01-01:2026-06-30/@stripe/stripe-js"


@pytest.mark.parametrize("bad", ["@stripe/../x", "Stripe", "a b", "@x/y/z", "../etc", ""])
def test_invalid_package_names_refused(bad):
    with pytest.raises(ValueError):
        d.npm_path(bad)


def test_date_chunks_contiguous_and_within_npm_limit():
    chunks = d.date_chunks(date(2020, 1, 1), date(2026, 10, 3))
    assert chunks[0][0] == date(2020, 1, 1) and chunks[-1][1] == date(2026, 10, 3)
    for (_, b), (c, _) in zip(chunks, chunks[1:], strict=False):
        assert c == b + timedelta(days=1)
    assert all((b - a).days + 1 <= d.NPM_MAX_DAYS for a, b in chunks)
    assert d.NPM_MAX_DAYS <= 18 * 28  # never more than 18 short months
    assert d.date_chunks(date(2026, 1, 2), date(2026, 1, 1)) == []


# --- parse ----------------------------------------------------------------------------------


def test_parse_npm_fixture_and_trailing_unreported_day():
    df = d.parse_npm_range(_load("npm_range_stripe_js.json"), "@stripe/stripe-js")
    assert df.height == 4 and df["downloads"].sum() == 2338296 + 2431364 + 2401177
    trimmed = d.trim_unreported(df, date(2026, 9, 4))
    assert trimmed["day"].max() == date(2026, 9, 3)


def test_long_idle_zero_run_is_kept():
    days = [{"registry": "npm", "package": "x", "day": date(2026, 1, 1) + timedelta(days=i),
             "downloads": 5 if i < 3 else 0} for i in range(20)]
    df = pl.DataFrame(days, schema=d.DAILY_DTYPES)
    assert d.trim_unreported(df, date(2026, 1, 20)).height == 20


def test_parse_npm_rejects_error_and_wrong_package():
    with pytest.raises(ValueError, match="npm error"):
        d.parse_npm_range(_load("npm_error_404.json"), "@checkout.com/checkout-web-components")
    with pytest.raises(ValueError, match="asked for"):
        d.parse_npm_range(_load("npm_range_stripe_js.json"), "@adyen/adyen-web")


def test_parse_pypi_keeps_without_mirrors_only():
    df = d.parse_pypi_overall(_load("pypistats_stripe.json"), "stripe")
    assert df["downloads"].to_list() == [598321, 620877]
    with pytest.raises(ValueError):
        d.parse_pypi_overall({"data": "nope"}, "stripe")


# --- end to end ----------------------------------------------------------------------------

END = date(2026, 3, 31)


def _tables(npm: FakeNpm, pypi: FakePypi | None = None, start: date = date(2024, 11, 1)):
    res = d.fetch_npm(npm, [*d.CLIENT_PACKAGES, *config.NPM_SERVER_SDKS], start, END)
    pres = d.fetch_pypi(pypi or FakePypi(END - timedelta(days=179), END), list(d.PYPI_PACKAGES))
    return res, pres, d.build_tables(res.daily, pres.daily)


def test_company_sums_and_complete_months_only():
    res, _, t = _tables(FakeNpm(lag_days=1))
    assert not res.failed
    cm = t.client_monthly
    # PayPal = paypal-js + braintree-web; the lagging last day makes 2026-03 incomplete -> dropped
    jan = cm.filter((pl.col("series") == "PayPal") & (pl.col("period") == date(2026, 1, 1)))
    assert jan["downloads"].item() == (50 + 30) * 31
    assert jan["packages"].item() == "@paypal/paypal-js,braintree-web"
    assert cm["period"].max() == date(2026, 2, 1)
    assert set(cm["series"].unique()) == set(config.NPM_CLIENT_SDKS)
    # no wrapper ever requested
    assert not any("react" in u for u in res.raw)


def test_weekly_periods_are_complete_iso_weeks():
    _, _, t = _tables(FakeNpm())
    w = t.client_weekly
    assert all(p.weekday() == 0 for p in w["period"].to_list())
    assert w["period"].max() + timedelta(days=6) <= END
    assert w.filter(pl.col("series") == "Stripe")["downloads"].unique().to_list() == [7000]
    # the first partial week (2024-11-01 is a Friday) is not shown
    assert w["period"].min() == date(2024, 11, 4)


def test_index_against_base_month():
    _, _, t = _tables(FakeNpm(scale={("@stripe/stripe-js", "2026-02"): 1.5}))
    s = t.client_monthly.filter(pl.col("series") == "Stripe").sort("period")
    assert s.filter(pl.col("period") == d.BASE_MONTH)["index"].item() == 100.0
    feb = s.filter(pl.col("period") == date(2026, 2, 1))
    assert feb["index"].item() == pytest.approx(1500 * 28 / (1000 * 31) * 100)
    assert s["base_month"].unique().to_list() == [d.BASE_MONTH]


def test_server_table_separate_and_pypi_indexed_on_first_month():
    pypi = FakePypi(END - timedelta(days=179), END)
    _, _, t = _tables(FakeNpm(), pypi)
    assert set(t.client_monthly["side"].unique()) == {"client"}
    sm = t.server_monthly
    assert set(sm.filter(pl.col("registry") == "npm")["series"].unique()) == set(config.NPM_SERVER_SDKS)
    py = sm.filter(pl.col("registry") == "pypi")
    assert set(py["series"].unique()) == {"stripe", "adyen", "braintree"}
    assert py["downloads"].max() <= 100 * 31  # without_mirrors only (with_mirrors rows read 999/day)
    first = py["period"].min()
    assert py.filter(pl.col("period") == first)["index"].unique().to_list() == [100.0]
    assert pypi.calls[0] == ("https://pypistats.org/api/packages/stripe/overall", {"mirrors": "false"})


def test_failed_package_drops_only_its_company():
    npm = FakeNpm(fail={"braintree-web": httpx.ConnectError("boom")})
    res, _, t = _tables(npm)
    assert res.failed == ["npm:braintree-web"]
    assert any(c["status"] == "fail" and "braintree-web" in c["name"] for c in res.checks)
    assert res.checks[0]["status"] == "warn"  # some packages fetched
    cm = t.client_monthly
    assert "PayPal" not in cm["series"].to_list()  # never a partial PayPal sum
    assert {"Stripe", "Adyen", "Checkout.com", "Airwallex"} <= set(cm["series"].to_list())
    checks = d.quality_checks(t, res.daily, today=END + timedelta(days=1))
    comp = next(c for c in checks if c["name"] == "Client SDK companies")
    assert comp["status"] == "fail" and "PayPal" in comp["detail"]


def test_failed_pypi_package_isolated():
    _, pres, t = _tables(FakeNpm(), FakePypi(END - timedelta(days=179), END, fail={"adyen"}))
    assert pres.failed == ["pypi:adyen"]
    assert set(t.server_monthly.filter(pl.col("registry") == "pypi")["series"].unique()) == {"stripe", "braintree"}


# --- Q1 -------------------------------------------------------------------------------------


def _monthly(stripe: list[int], months: list[date]) -> pl.DataFrame:
    rows = []
    for m, s in zip(months, stripe, strict=True):
        for company in config.NPM_CLIENT_SDKS:
            rows.append({"registry": "npm", "side": "client", "series": company, "company": company,
                         "packages": "x", "period": m, "downloads": s if company == "Stripe" else 25,
                         "base_month": None, "index": None})
    return pl.DataFrame(rows, schema=d.MONTHLY_DTYPES)


MONTHS = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1),
          date(2027, 1, 1)]


def test_q1_share_and_ratio_use_volume_upper_bound():
    q = d.q1_series(_monthly([900] * 6, MONTHS), 0.40)
    r = q.filter(pl.col("month") == date(2026, 10, 1)).row(0, named=True)
    assert r["download_share"] == pytest.approx(900 / 1000)
    assert r["ratio"] == pytest.approx(0.9 / 0.40)
    assert r["status"] == "holds"
    # pre-registration months are shown, never graded
    assert q.filter(pl.col("month") < d.Q1_FIRST_GRADED_MONTH)["status"].unique().to_list() == ["collecting"]


def test_q1_falsified_after_three_consecutive_graded_months_below_bar():
    # share 0.5 / 0.40 = 1.25 < 1.5 ; share 0.9 / 0.4 = 2.25
    stripe = [100, 100, 100, 100, 100, 900]  # 100/(100+100) = 0.5
    q = d.q1_series(_monthly(stripe, MONTHS), 0.40)
    # Aug/Sep below but ungraded: the streak starts in October
    assert q["consecutive_below"].to_list() == [0, 0, 1, 2, 3, 0]
    assert q["status"].to_list() == ["collecting", "collecting", "collecting", "collecting", "falsified",
                                     "falsified"]  # sticky
    assert d.q1_status(q)["status"] == "falsified"


def test_q1_streak_resets_and_between_bars_is_collecting():
    stripe = [100, 100, 100, 100, 300, 100]  # 0.75/0.4 = 1.875: between 1.5 and 2.0 breaks the streak
    q = d.q1_series(_monthly(stripe, MONTHS), 0.40)
    assert q["consecutive_below"].to_list() == [0, 0, 1, 2, 0, 1]
    assert q.filter(pl.col("month") == date(2026, 12, 1))["status"].item() == "collecting"
    assert "falsified" not in q["status"].to_list()


def test_q1_needs_whole_pool_and_volume_bound():
    m = _monthly([900] * 6, MONTHS).filter(~((pl.col("series") == "Airwallex") & (pl.col("period") == MONTHS[3])))
    q = d.q1_series(m, None)
    assert MONTHS[3] not in q["month"].to_list()
    assert q.filter(pl.col("month") >= d.Q1_FIRST_GRADED_MONTH)["status"].unique().to_list() == ["undecidable"]
    vol = pl.DataFrame({"month": MONTHS, "volume_share_upper": [0.3, 0.3, 0.3, 0.3, 0.5, 0.5]})
    q2 = d.q1_series(_monthly([900] * 6, MONTHS), vol)
    assert q2.filter(pl.col("month") == MONTHS[4])["ratio"].item() == pytest.approx(0.9 / 0.5)


def test_q1_end_to_end_from_fake_npm():
    _, _, t = _tables(FakeNpm())
    q = d.q1_series(t.client_monthly, 0.32)
    share = 1000 / (1000 + 40 + 50 + 30 + 5 + 10)
    assert q["download_share"].unique().to_list() == [pytest.approx(share)]
    assert d.q1_status(q)["status"] == "collecting"  # all months are before registration


# --- run / write ---------------------------------------------------------------------------


def test_run_writes_validated_marts(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "NPM_START", date(2024, 11, 1))
    tables, q1, checks = d.run(FakeNpm(), FakePypi(END - timedelta(days=179), END), volume_share_upper=0.32,
                               end=END, out_dir=tmp_path / "marts", raw_root=tmp_path / "raw")
    names = sorted(p.name for p in (tmp_path / "marts").iterdir())
    assert names == ["devstats_client_monthly.parquet", "devstats_client_weekly.parquet", "devstats_q1.parquet",
                     "devstats_server_monthly.parquet", "devstats_server_weekly.parquet"]
    assert pl.read_parquet(tmp_path / "marts" / "devstats_q1.parquet").height == q1.height
    assert list((tmp_path / "raw" / "payments" / "devstats").glob("*/*/*.json.gz"))
    assert all(c["status"] != "fail" for c in checks if c["name"] != "Downloads freshness")
