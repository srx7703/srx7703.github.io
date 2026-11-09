"""Taiwan makers: monthly revenue and quarterly statements from MOPS (公開資訊觀測站).

Usage:
    uv run python -m pipelines.servers.tw monthly [--backfill]   # refresh the last four months (or everything)
    uv run python -m pipelines.servers.tw quarterly [--backfill]
    uv run python -m pipelines.servers.tw build                  # parse raw -> snapshots, no network

Monthly revenue: one static page per month lists every TWSE-listed company (columns: this month, last month, same
month last year, two changes, year to date, year to date last year, change, note), in thousands of New Taiwan
dollars, cp950-encoded. A month can be revised after first publication (更正), so the last four months are fetched
again on every run and kept beside the earlier fetches; the newest fetch of a month wins when parsing.

Quarterly statements: MOPS ``ajax_t163sb04`` (income statement summary) and ``ajax_t163sb05`` (balance sheet
summary), one POST per market and quarter, also in thousands of NTD. Income-statement figures are year-to-date, so
Q2 = H1 - Q1 and so on; differencing never crosses a year, and a missing interim breaks that year's chain rather
than inventing a quarter. Balance-sheet figures are point-in-time.

Raw pages are stored gzipped exactly as served (``data/raw/servers/mops_*/<period>/<fetch date>.html.gz``) and never
rewritten. Only the pool's rows are carried into the snapshots.
"""

from __future__ import annotations

import argparse
import gzip
import html
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import utc_now, write_parquet
from pipelines.servers import config as cfg
from pipelines.servers.schema import TW_MONTHLY, TW_QUARTERLY, validate

log = logging.getLogger("servers.tw")

UA = {"User-Agent": "Mozilla/5.0 (compatible; portfolio-pipelines; +https://github.com/srx7703/srx7703.github.io)"}

# ---- pure helpers (unit-tested) ---------------------------------------------------------------------------
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def roc(year: int) -> int:
    return year - 1911


def months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _num(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if s in ("", "-", "--", "不適用", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cells_of(page: str) -> list[list[str]]:
    """Every table row as a list of plain-text cells."""
    out = []
    for r in _ROW.findall(page):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip() for c in _CELL.findall(r)]
        if cells:
            out.append(cells)
    return out


def parse_monthly(page: str, month: str, codes: tuple[str, ...]) -> list[dict]:
    """Rows for ``codes`` from one month's page: revenue this month and the same month a year earlier (thousand NTD)."""
    out = []
    for c in cells_of(page):
        if len(c) >= 7 and c[0] in codes:
            rev, prev_year = _num(c[2]), _num(c[4])
            if rev is None:
                continue
            out.append({"code": c[0], "name": c[1], "month": month, "revenue_k": rev, "revenue_k_last_year": prev_year,
                        "note": c[10] if len(c) > 10 else ""})
    return out


def header_index(headers: list[str], *names: str) -> int | None:
    for n in names:
        if n in headers:
            return headers.index(n)
    return None


INCOME_FIELDS = {
    "revenue_k": ("營業收入", "收益", "收入"),
    "gross_profit_k": ("營業毛利（毛損）淨額", "營業毛利（毛損）"),
    "operating_income_k": ("營業利益（損失）",),
    "net_income_parent_k": ("淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"),
}
BALANCE_FIELDS = {
    "inventory_k": ("存貨",),
    "receivables_k": ("應收帳款淨額", "應收票據及帳款淨額"),
    "total_assets_k": ("資產總計", "資產總額"),
}


def parse_summary_table(page: str, codes: tuple[str, ...], fields: dict[str, tuple[str, ...]]) -> list[dict]:
    """MOPS t163sb04/05 pages hold one table per industry template, each with its own header row. Read every table
    with the header that precedes its rows, and take the named columns where the template has them."""
    out: list[dict] = []
    headers: list[str] = []
    for c in cells_of(page):
        if c and c[0] in ("公司代號", "公司 代號"):
            headers = c
            continue
        if headers and c and c[0] in codes and len(c) == len(headers):
            row: dict = {"code": c[0], "name": c[1]}
            for k, names in fields.items():
                i = header_index(headers, *names)
                row[k] = _num(c[i]) if i is not None else None
            out.append(row)
    return out


def quarter_label(year: int, q: int) -> str:
    return f"{year}Q{q}"


def difference_ytd(rows: list[dict], value_cols: list[str]) -> list[dict]:
    """Turn year-to-date rows (code, year, q, cols...) into single quarters. A missing earlier quarter of the same
    year leaves the later quarter out instead of inventing a longer 'quarter'."""
    by = {(r["code"], r["year"], r["q"]): r for r in rows}
    out = []
    for (code, year, q), r in sorted(by.items()):
        if q == 1:
            vals = {c: r[c] for c in value_cols}
        else:
            prev = by.get((code, year, q - 1))
            if prev is None:
                continue
            vals = {c: (r[c] - prev[c]) if r[c] is not None and prev[c] is not None else None for c in value_cols}
        out.append({"code": code, "year": year, "q": q, "quarter": quarter_label(year, q), **vals})
    return out


# ---- network ----------------------------------------------------------------------------------------------

def _raw_path(base: Path, period: str, today: str) -> Path:
    d = base / period
    p = d / f"{today}.html.gz"
    n = 1
    while p.exists():
        n += 1
        p = d / f"{today}_{n}.html.gz"
    return p


def _save(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(body)


def _get(client: httpx.Client, url: str, data: dict | None = None) -> bytes:
    for attempt in range(4):
        try:
            r = client.post(url, data=data) if data else client.get(url)
            if r.status_code == 200 and len(r.content) > 2000:
                return r.content
            raise httpx.HTTPStatusError(f"HTTP {r.status_code}, {len(r.content)} bytes", request=r.request, response=r)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if attempt == 3:
                raise
            log.warning("%s failed (%s); retrying", url, exc)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_monthly(backfill: bool) -> int:
    today = utc_now().date()
    last = f"{today.year:04d}-{today.month:02d}"
    # The current month's page exists but is empty until the 10th of the next month; the newest real month is last-1.
    all_months = months(cfg.TW_MONTHLY_FIRST, last)[:-1]
    targets = all_months if backfill else all_months[-4:]
    if not backfill:
        targets += [m for m in all_months if not (cfg.RAW_TW_MONTHLY / m).exists()]
    failed = []
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as client:
        for m in sorted(set(targets)):
            if backfill and (cfg.RAW_TW_MONTHLY / m).exists():
                continue
            y, mm = map(int, m.split("-"))
            try:
                body = _get(client, cfg.MOPS_MONTHLY_URL.format(roc=roc(y), m=mm))
            except Exception as exc:  # noqa: BLE001 - one month must not stop the rest
                log.error("monthly %s failed: %s", m, exc)
                failed.append(m)
                continue
            page = body.decode("cp950", errors="replace")
            if not parse_monthly(page, m, cfg.TW_CODES):
                log.warning("monthly %s: page has no pool rows yet; not stored", m)
                continue
            _save(_raw_path(cfg.RAW_TW_MONTHLY, m, today.isoformat()), body)
            log.info("monthly %s stored", m)
            time.sleep(1.0)
    return 1 if failed else 0


def fetch_quarterly(backfill: bool) -> int:
    today = utc_now().date()
    qs = []
    y, q = 2019, 1
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        qs.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    qs = qs[:-1]  # the current quarter has not reported
    targets = qs if backfill else qs[-3:]
    failed = []
    with httpx.Client(timeout=90, headers=UA, follow_redirects=True) as client:
        for (y, q) in targets:
            label = quarter_label(y, q)
            for kind in ("ajax_t163sb04", "ajax_t163sb05"):
                sub = cfg.RAW_TW_QUARTERLY / kind.removeprefix("ajax_")
                if backfill and (sub / label).exists():
                    continue
                data = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "isQuery": "Y",
                        "TYPEK": "sii", "year": str(roc(y)), "season": f"{q:02d}"}
                try:
                    body = _get(client, cfg.MOPS_QUARTERLY_URL.format(kind=kind), data)
                except Exception as exc:  # noqa: BLE001
                    log.error("%s %s failed: %s", kind, label, exc)
                    failed.append(f"{kind} {label}")
                    continue
                page = body.decode("utf-8", errors="replace")
                fields = INCOME_FIELDS if kind.endswith("04") else BALANCE_FIELDS
                if not parse_summary_table(page, cfg.TW_CODES, fields):
                    log.warning("%s %s: no pool rows (not yet published?); not stored", kind, label)
                    continue
                _save(_raw_path(sub, label, today.isoformat()), body)
                log.info("%s %s stored", kind, label)
                time.sleep(3.0)  # MOPS throttles bursts of POSTs
    return 1 if failed else 0


# ---- build snapshots from raw (no network) ----------------------------------------------------------------

def _newest(d: Path) -> Path | None:
    files = sorted(d.glob("*.html.gz"), key=lambda p: (p.name.split(".")[0].split("_")[0],
                                                       int(p.name.split(".")[0].split("_")[1]) if "_" in p.name else 1))
    return files[-1] if files else None


def build_monthly() -> pl.DataFrame:
    rows = []
    for d in sorted(p for p in cfg.RAW_TW_MONTHLY.glob("*") if p.is_dir()):
        f = _newest(d)
        if f is None:
            continue
        page = gzip.decompress(f.read_bytes()).decode("cp950", errors="replace")
        for r in parse_monthly(page, d.name, cfg.TW_CODES):
            rows.append({**r, "fetched": f.name.split(".")[0][:10]})
    df = pl.DataFrame(rows, schema={"code": pl.Utf8, "name": pl.Utf8, "month": pl.Utf8, "revenue_k": pl.Float64,
                                    "revenue_k_last_year": pl.Float64, "note": pl.Utf8, "fetched": pl.Utf8})
    return df.sort(["code", "month"])


def build_quarterly() -> pl.DataFrame:
    inc, bal = [], []
    for kind, fields, sink in (("t163sb04", INCOME_FIELDS, inc), ("t163sb05", BALANCE_FIELDS, bal)):
        for d in sorted(p for p in (cfg.RAW_TW_QUARTERLY / kind).glob("*") if p.is_dir()):
            f = _newest(d)
            if f is None:
                continue
            y, q = int(d.name[:4]), int(d.name[-1])
            page = gzip.decompress(f.read_bytes()).decode("utf-8", errors="replace")
            for r in parse_summary_table(page, cfg.TW_CODES, fields):
                sink.append({**r, "year": y, "q": q})
    single = difference_ytd(inc, list(INCOME_FIELDS))
    bal_by = {(r["code"], r["year"], r["q"]): r for r in bal}
    rows = []
    for r in single:
        b = bal_by.get((r["code"], r["year"], r["q"]), {})
        rows.append({**r, **{k: b.get(k) for k in BALANCE_FIELDS},
                     "period_end": date(r["year"], 3 * r["q"], 30 if r["q"] in (2, 3) else 31).isoformat()})
    schema = {"code": pl.Utf8, "year": pl.Int64, "q": pl.Int64, "quarter": pl.Utf8,
              **{k: pl.Float64 for k in INCOME_FIELDS}, **{k: pl.Float64 for k in BALANCE_FIELDS},
              "period_end": pl.Utf8}
    return pl.DataFrame(rows, schema=schema).sort(["code", "quarter"])


def build() -> None:
    m = validate(build_monthly(), TW_MONTHLY)
    write_parquet(m, cfg.SNAP / "tw_monthly.parquet")
    log.info("tw_monthly: %d rows, %s to %s", m.height, m["month"].min(), m["month"].max())
    q = validate(build_quarterly(), TW_QUARTERLY)
    write_parquet(q, cfg.SNAP / "tw_quarterly.parquet")
    log.info("tw_quarterly: %d rows, %s to %s", q.height, q["quarter"].min() if q.height else None,
             q["quarter"].max() if q.height else None)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["monthly", "quarterly", "build", "all"])
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args(argv)
    rc = 0
    if a.what in ("monthly", "all"):
        rc |= fetch_monthly(a.backfill)
    if a.what in ("quarterly", "all"):
        rc |= fetch_quarterly(a.backfill)
    build()
    return rc


if __name__ == "__main__":
    sys.exit(main())
