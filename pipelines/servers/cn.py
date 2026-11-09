"""A-share makers: quarterly income statement and balance sheet from East Money F10.

Usage:
    uv run python -m pipelines.servers.cn [fetch|build]

East Money's F10 tables (``RPT_F10_FINANCE_GINCOME`` / ``RPT_F10_FINANCE_GBALANCE``) carry the statutory statements
as filed. Income figures are year-to-date and are differenced into quarters within a year; a missing interim breaks
that year's chain. Operating profit here is *core* operating profit: total operating revenue less total operating
cost (cost of sales, taxes and surcharges, selling, administrative, R&D and finance expense). The reported 营业利润 is
not used because it also carries investment income, fair-value changes and subsidies, which are not what a margin
comparison with the other markets means.

Raw responses are archived per fetch date under ``data/raw/servers/eastmoney/<code>/`` and never rewritten.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import polars as pl

from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import read_json_gz, utc_now, write_json_gz, write_parquet
from pipelines.servers import config as cfg
from pipelines.servers.schema import CN_QUARTERLY, validate
from pipelines.servers.tw import difference_ytd

log = logging.getLogger("servers.cn")

URL = "https://datacenter.eastmoney.com/securities/api/data/get"
TABLES = {
    "income": ("RPT_F10_FINANCE_GINCOME", "APP_F10_GINCOME"),
    "balance": ("RPT_F10_FINANCE_GBALANCE", "F10_FINANCE_GBALANCE"),
}
Q_OF_MONTH = {3: 1, 6: 2, 9: 3, 12: 4}


def secucode(key: str) -> str:
    code, ex = key.split(".")
    return f"{code}.{'SH' if ex == 'SS' else 'SZ'}"


def _raw_path(code: str, table: str, today: str) -> Path:
    d = cfg.RAW_EM / code / table
    p, n = d / f"{today}.json.gz", 1
    while p.exists():
        n += 1
        p = d / f"{today}_{n}.json.gz"
    return p


def fetch() -> int:
    http = HttpClient(min_interval=0.5)
    today = utc_now().date().isoformat()
    failed = []
    for key in cfg.CN_CODES:
        for table, (rpt, sty) in TABLES.items():
            params = {"type": rpt, "sty": sty, "filter": f'(SECUCODE="{secucode(key)}")', "p": 1, "ps": 200,
                      "sr": -1, "st": "REPORT_DATE", "source": "HSF10", "client": "PC"}
            try:
                body = http.get_json(URL, params=params)
                rows = (body.get("result") or {}).get("data") or []
                if not rows:
                    raise ValueError(f"empty response: {body.get('message')}")
                write_json_gz({"params": params, "fetched": today, "data": rows}, _raw_path(key, table, today))
                log.info("%s %s: %d periods", key, table, len(rows))
            except Exception as exc:  # noqa: BLE001
                log.error("%s %s failed: %s", key, table, exc)
                failed.append(f"{key} {table}")
    return 1 if failed else 0


def _newest(d: Path) -> Path | None:
    files = sorted(d.glob("*.json.gz"))
    return files[-1] if files else None


def _f(x) -> float | None:
    return float(x) if x is not None else None


def build() -> pl.DataFrame:
    rows = []
    for key in cfg.CN_CODES:
        fi, fb = _newest(cfg.RAW_EM / key / "income"), _newest(cfg.RAW_EM / key / "balance")
        if fi is None:
            continue
        inc = []
        for r in read_json_gz(fi)["data"]:
            d = date.fromisoformat(r["REPORT_DATE"][:10])
            if d.year < 2018 or d.month not in Q_OF_MONTH:
                continue
            rev, cogs = _f(r.get("OPERATE_INCOME")), _f(r.get("OPERATE_COST"))
            tot_rev, tot_cost = _f(r.get("TOTAL_OPERATE_INCOME")), _f(r.get("TOTAL_OPERATE_COST"))
            inc.append({"code": key, "year": d.year, "q": Q_OF_MONTH[d.month],
                        "revenue": tot_rev,
                        "gross_profit": (rev - cogs) if rev is not None and cogs is not None else None,
                        "operating_income": (tot_rev - tot_cost) if tot_rev is not None and tot_cost is not None
                        else None,
                        "net_income_parent": _f(r.get("PARENT_NETPROFIT"))})
        single = difference_ytd(inc, ["revenue", "gross_profit", "operating_income", "net_income_parent"])
        bal = {}
        if fb is not None:
            for r in read_json_gz(fb)["data"]:
                d = date.fromisoformat(r["REPORT_DATE"][:10])
                if d.month in Q_OF_MONTH:
                    bal[(d.year, Q_OF_MONTH[d.month])] = r
        for s in single:
            b = bal.get((s["year"], s["q"]), {})
            m = 3 * s["q"]
            rows.append({**s, "inventory": _f(b.get("INVENTORY")), "receivables": _f(b.get("ACCOUNTS_RECE")),
                         "total_assets": _f(b.get("TOTAL_ASSETS")),
                         "period_end": date(s["year"], m, 30 if m in (6, 9) else 31).isoformat()})
    schema = {"code": pl.Utf8, "year": pl.Int64, "q": pl.Int64, "quarter": pl.Utf8, "revenue": pl.Float64,
              "gross_profit": pl.Float64, "operating_income": pl.Float64, "net_income_parent": pl.Float64,
              "inventory": pl.Float64, "receivables": pl.Float64, "total_assets": pl.Float64, "period_end": pl.Utf8}
    df = validate(pl.DataFrame(rows, schema=schema).sort(["code", "quarter"]), CN_QUARTERLY)
    write_parquet(df, cfg.SNAP / "cn_quarterly.parquet")
    log.info("cn_quarterly: %d rows", df.height)
    return df


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="fetch", choices=["fetch", "build"])
    a = ap.parse_args(argv)
    rc = fetch() if a.what == "fetch" else 0
    build()
    return rc


if __name__ == "__main__":
    sys.exit(main())
