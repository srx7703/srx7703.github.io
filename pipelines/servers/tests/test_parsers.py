from pipelines.servers import tw
from pipelines.servers.forecast import backtest_errors, point, shift

MONTHLY_ROW = ("<tr align=right><td align=center>6669</td><td align=left>緯穎</td><td nowrap> 91,264,205</td>"
               "<td nowrap> 86,316,601</td><td nowrap> 35,381,437</td><td nowrap> 5.73</td><Td nowrap> 157.94</td>"
               "<td nowrap> 749,487,034</td><td nowrap> 280,308,921</td><td nowrap> 167.37</td>"
               "<td align=left>主要係客戶需求強勁</td></tr>")


def test_monthly_page_row():
    r = tw.parse_monthly(MONTHLY_ROW, "2025-10", ("6669",))[0]
    assert r["revenue_k"] == 91264205 and r["revenue_k_last_year"] == 35381437


def test_summary_table_reads_each_template_with_its_own_header():
    page = ("<tr><th>公司代號</th><th>公司名稱</th><th>收益</th><th>營業利益</th></tr>"
            "<tr><td>9999</td><td>x</td><td>1</td><td>1</td></tr>"
            "<tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業毛利（毛損）淨額</th><th>營業利益（損失）</th></tr>"
            "<tr><td>2382</td><td>廣達</td><td>1,000</td><td>60</td><td>30</td></tr>")
    r = tw.parse_summary_table(page, ("2382",), tw.INCOME_FIELDS)[0]
    assert (r["revenue_k"], r["gross_profit_k"], r["operating_income_k"]) == (1000, 60, 30)


def test_year_to_date_is_differenced_and_a_gap_drops_the_quarter():
    rows = [{"code": "a", "year": 2026, "q": 1, "v": 10.0}, {"code": "a", "year": 2026, "q": 2, "v": 25.0},
            {"code": "a", "year": 2026, "q": 4, "v": 60.0}]
    out = {r["quarter"]: r["v"] for r in tw.difference_ytd(rows, ["v"])}
    assert out == {"2026Q1": 10.0, "2026Q2": 15.0}


def test_forecast_uses_only_months_before_the_target():
    s = {shift("2024-01", k): 100.0 * (1.1 ** (k // 12)) for k in range(36)}
    p, used = point(s, "2026-11", "2026-10")
    assert used == ["2026-08", "2026-09", "2026-10"]
    assert p == s["2025-11"] * 1.1
    assert all(abs(e) < 1e-9 for e in backtest_errors(s, "2025-02", "2026-10"))
