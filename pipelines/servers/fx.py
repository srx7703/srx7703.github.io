"""Monthly average exchange rates from FRED (no key): TWD and CNY per US dollar.

The valuation pipeline keeps only about a year of daily rates; the registered Q1 test converts Taiwan monthly revenue
at each month's average rate back to 2019, so this page fetches FRED's monthly-average series itself. Raw CSVs are
archived per fetch date; the snapshot is rebuilt from the newest CSV of each series.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import sys

import httpx
import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import utc_now, write_parquet
from pipelines.servers import config as cfg
from pipelines.servers.schema import FX_MONTHLY, validate

log = logging.getLogger("servers.fx")

SERIES = {"TWD": "EXTAUS", "CNY": "EXCHUS"}   # both are units of the currency per USD, monthly averages
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
RAW = cfg.RAW / "fred"


def parse(text: str, currency: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        v = list(row.values())
        try:
            out.append({"month": v[0][:7], "currency": currency, "per_usd": float(v[1])})
        except (ValueError, IndexError):
            continue
    return out


def fetch() -> int:
    today = utc_now().date().isoformat()
    failed = 0
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        for cur, sid in SERIES.items():
            try:
                r = c.get(URL.format(sid=sid))
                r.raise_for_status()
                if not parse(r.text, cur):
                    raise ValueError("no rows")
                p = RAW / sid / f"{today}.csv.gz"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(gzip.compress(r.content))
            except Exception as exc:  # noqa: BLE001
                log.error("%s failed: %s", sid, exc)
                failed += 1
    return 1 if failed else 0


def build() -> pl.DataFrame:
    rows = []
    for cur, sid in SERIES.items():
        files = sorted((RAW / sid).glob("*.csv.gz"))
        if files:
            rows += parse(gzip.decompress(files[-1].read_bytes()).decode(), cur)
    df = pl.DataFrame(rows, schema={"month": pl.Utf8, "currency": pl.Utf8, "per_usd": pl.Float64})
    df = validate(df.filter(pl.col("month") >= "2015-01").sort(["currency", "month"]), FX_MONTHLY)
    write_parquet(df, cfg.SNAP / "fx_monthly.parquet")
    log.info("fx_monthly: %d rows", df.height)
    return df


def main() -> int:
    setup_logging()
    rc = fetch()
    build()
    return rc


if __name__ == "__main__":
    sys.exit(main())
