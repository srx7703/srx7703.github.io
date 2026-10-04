"""Payments-universe SEC financials: quarterly series where filers report quarters, LTM for everyone.

Usage:
    uv run python -m pipelines.sec.transform --universe payments     (or: python -m pipelines.sec.transform_payments)

Reads only data/snapshots/sec_payments/ (written by `pipelines.sec.ingest --universe payments`, in CI), and
only the tickers in config.UNIVERSES["payments"]. The SaaS benchmark is untouched: different snapshot
directory, different outputs, and its own build() never looks here.

Writes:
    data/marts/payments/sec_quarterly.parquet   ticker x quarter_end x metric (quarterly, ttm), quarterly filers only
    data/marts/payments/sec_ltm.parquet         ticker x period_end x metric: last-twelve-months value and its basis
    data/marts/payments/sec_latest.parquet      latest LTM row per company with growth and margins
    data/marts/payments/sec_coverage.json       tags/taxonomy/currency per company + the checks for facts.checks

Why an LTM table and not only TTM-of-quarters: foreign private issuers (Klarna, dLocal, Wise) file a 20-F
once a year and furnish interim results on 6-K, often half-yearly and sometimes without XBRL. So each
metric's LTM at a period end comes from the best basis available, in this order:
    "4Q"      sum of four consecutive fiscal quarters (the SaaS rule, transform.ttm);
    "FY"      a fiscal-year span (350-380 days) as filed;
    "FY+YTD"  latest FY + current year-to-date - prior-year year-to-date (e.g. FY25 + H1 26 - H1 25 from a 6-K).
Half-years are deliberately not put in the quarterly table: they are not quarters, and mixing them in would
break the 4-quarter TTM rule. They only feed the FY+YTD basis.

Values stay in each filer's reporting currency (column `currency`); no FX conversion happens here, and a
non-USD reporter is flagged in the checks so nothing downstream sums currencies by accident.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, freshness, uniqueness
from pipelines.common.log import setup_logging
from pipelines.common.storage import MARTS_DIR, SNAP_DIR, utc_now, write_json, write_parquet
from pipelines.sec.config import PAYMENTS_FILERS, UNIVERSES
from pipelines.sec.ingest import FRAMES_FORM
from pipelines.sec.transform import (
    Q_MIN,
    merge_metric_facts,
    quarterly_series,
    ttm,
    universe_fact_files,
    yoy,
)

log = logging.getLogger("sec.transform_payments")

FY_MIN, FY_MAX = 350, 380
BASIS_ORDER = ("4Q", "FY", "FY+YTD")
STALE_DAYS = 200  # a latest LTM older than this is a gap worth showing (a 20-F filer's H1 6-K is ~ 6 months)

QUARTERLY_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "quarter_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "metric": pa.Column(str),
        "quarterly": pa.Column(float),
        "ttm": pa.Column(float, nullable=True),
        "currency": pa.Column(str, pa.Check.str_matches(r"^[A-Z]{3}$")),
    },
    unique=["ticker", "quarter_end", "metric"],
)
LTM_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "period_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "metric": pa.Column(str),
        "ltm": pa.Column(float),
        "basis": pa.Column(str, pa.Check.isin(list(BASIS_ORDER))),
        "currency": pa.Column(str, pa.Check.str_matches(r"^[A-Z]{3}$")),
    },
    unique=["ticker", "period_end", "metric"],
)
LATEST_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "company": pa.Column(str),
        "period_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "basis": pa.Column(str, pa.Check.isin(list(BASIS_ORDER))),
        "currency": pa.Column(str, pa.Check.str_matches(r"^[A-Z]{3}$")),
        "revenue": pa.Column(float, pa.Check.gt(0)),
        "rev_growth": pa.Column(float, pa.Check.in_range(-1.0, 10.0), nullable=True),
        "gross_margin": pa.Column(float, pa.Check.in_range(-2.0, 1.0), nullable=True),
        "op_margin": pa.Column(float, pa.Check.in_range(-10.0, 1.0), nullable=True),
        "net_margin": pa.Column(float, pa.Check.in_range(-10.0, 5.0), nullable=True),
        "fcf_margin": pa.Column(float, pa.Check.in_range(-10.0, 5.0), nullable=True),
        "latest_from_frames": pa.Column(bool),
    },
    unique=["ticker"],
)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def ltm_series(spans: dict[tuple[str, str], float]) -> dict[str, tuple[float, str]]:
    """Last-twelve-months value per period end from duration spans: end -> (value, basis).

    Preference per end: 4Q (four consecutive quarters) > FY (as filed) > FY+YTD.
    """
    out: dict[str, tuple[float, str]] = {}
    q, _ = quarterly_series(spans)
    for e, v in ttm(q).items():
        out[e] = (v, "4Q")
    fys = {(s, e): v for (s, e), v in spans.items() if FY_MIN <= (_d(e) - _d(s)).days <= FY_MAX}
    for (_, e), v in fys.items():
        out.setdefault(e, (v, "FY"))
    ytds = {(s, e): v for (s, e), v in spans.items() if Q_MIN <= (_d(e) - _d(s)).days < FY_MIN}
    for (s, e), v in ytds.items():
        if e in out:
            continue
        # the fiscal year that ended just before this year-to-date span began
        fy = next(((fs, fe) for (fs, fe) in fys if 0 <= (_d(s) - _d(fe)).days <= 10), None)
        if fy is None:
            continue
        length = (_d(e) - _d(s)).days
        prev = next(
            (
                pv
                for (ps, pe), pv in ytds.items()
                if abs((_d(ps) - _d(fy[0])).days) <= 10
                and abs((_d(pe) - _d(ps)).days - length) <= 10
                and abs((_d(pe) - (_d(e) - timedelta(days=365))).days) <= 20
            ),
            None,
        )
        if prev is not None:
            out[e] = (fys[fy] + v - prev, "FY+YTD")
    return out


def build(snap_dir: Path | None = None, marts_dir: Path | None = None) -> dict:
    universe = UNIVERSES["payments"]
    snap_root = (snap_dir or SNAP_DIR) / universe.snap_subdir
    out_root = (marts_dir or MARTS_DIR) / "payments"
    snapshot = json.loads((snap_root / "companies.json").read_text())
    companies = snapshot.get("companies", {})
    names = {f["ticker"]: f["company"] for f in PAYMENTS_FILERS}

    q_rows: list[dict] = []
    l_rows: list[dict] = []
    coverage: dict[str, dict] = {}
    frames_ends: dict[str, set[str]] = {}
    for path in universe_fact_files(snap_root / "facts", universe.tickers):
        ticker = path.stem
        df = pl.read_parquet(path)
        if df.height == 0:
            coverage[ticker] = {"company": names.get(ticker), "tags": {}, "note": "no mapped facts"}
            continue
        currency = df.filter(pl.col("metric") == "revenue")["unit"].mode().first() or df["unit"].mode().first()
        df = df.filter(pl.col("unit") == currency)  # one currency per filer, never mixed
        frames_ends[ticker] = set(df.filter(pl.col("form") == FRAMES_FORM)["end"].to_list())
        cov: dict = {
            "company": names.get(ticker),
            "name": companies.get(ticker, {}).get("name"),
            "currency": currency,
            "taxonomy": sorted({t.split(":", 1)[0] for t in df["tag"].unique().to_list()}),
            "forms": sorted(df["form"].drop_nulls().unique().to_list()),
            "tags": {},
            "quarters": {},
            "ltm_basis": {},
        }
        for metric in universe.tag_map:
            spans, used = merge_metric_facts(df, metric, universe.tag_map)
            if not spans:
                continue
            cov["tags"][metric] = used
            q, _ = quarterly_series(spans)
            t = ttm(q)
            cov["quarters"][metric] = len(q)
            for e, v in q.items():
                q_rows.append(
                    {"ticker": ticker, "quarter_end": e, "metric": metric, "quarterly": v, "ttm": t.get(e),
                     "currency": currency}
                )
            lt = ltm_series(spans)
            for e, (v, basis) in lt.items():
                l_rows.append(
                    {"ticker": ticker, "period_end": e, "metric": metric, "ltm": v, "basis": basis,
                     "currency": currency}
                )
            if lt:
                cov["ltm_basis"][metric] = lt[max(lt)][1]
        coverage[ticker] = cov

    quarterly = pl.DataFrame(
        q_rows,
        schema={"ticker": pl.Utf8, "quarter_end": pl.Utf8, "metric": pl.Utf8, "quarterly": pl.Float64,
                "ttm": pl.Float64, "currency": pl.Utf8},
    ).sort("ticker", "metric", "quarter_end")
    ltm = pl.DataFrame(
        l_rows,
        schema={"ticker": pl.Utf8, "period_end": pl.Utf8, "metric": pl.Utf8, "ltm": pl.Float64,
                "basis": pl.Utf8, "currency": pl.Utf8},
    ).sort("ticker", "metric", "period_end")

    latest = latest_table(ltm, names, frames_ends)

    checks = list(snapshot.get("checks", []))  # ingest-side: frames fallback used / missing, lookup failures
    for f in snapshot.get("failed", []):
        checks.append(check("Company fetch", False, str(f)))
    n_univ = len(universe.tickers)
    checks += [
        check(
            "Companies with financials",
            latest.height == n_univ,
            f"{latest.height} of {n_univ} payments filers have a latest LTM revenue",
            warn=latest.height >= n_univ // 2,
        ),
        uniqueness(latest, ["ticker"], "companies"),
    ]
    non_usd = {r["ticker"]: r["currency"] for r in latest.iter_rows(named=True) if r["currency"] != "USD"}
    if non_usd:
        checks.append(
            check(
                "Reporting currency",
                False,
                "not USD, shown in own currency (no FX applied): "
                + ", ".join(f"{t} {c}" for t, c in sorted(non_usd.items())),
                warn=True,
            )
        )
    today = utc_now().date()
    stale = [r["ticker"] for r in latest.iter_rows(named=True) if (today - _d(r["period_end"])).days > STALE_DAYS]
    checks.append(
        check("Latest period", not stale, f"latest LTM older than {STALE_DAYS} days: {', '.join(stale) or 'none'}",
              warn=True)
    )
    frames_latest = latest.filter(pl.col("latest_from_frames"))["ticker"].to_list()
    if frames_latest:
        checks.append(
            check("Frames fallback", False, "latest quarter taken from SEC frames for " + ", ".join(frames_latest),
                  warn=True)
        )
    log_path = snap_root / "refresh_log.jsonl"
    refreshes = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()] if log_path.exists() else []
    checks.append(freshness(refreshes[-1]["ts"] if refreshes else None, 24 * 8, "SEC refresh"))

    # Contracts before anything is written: a schema failure blocks the write.
    QUARTERLY_SCHEMA.validate(quarterly)
    LTM_SCHEMA.validate(ltm)
    LATEST_SCHEMA.validate(latest.select(list(LATEST_SCHEMA.columns)))

    write_parquet(quarterly, out_root / "sec_quarterly.parquet")
    write_parquet(ltm, out_root / "sec_ltm.parquet")
    write_parquet(latest, out_root / "sec_latest.parquet")
    result = {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "companies": coverage,
        "checks": checks,
        "sources": [
            {
                "name": "SEC EDGAR XBRL companyfacts and frames APIs",
                "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            }
        ],
    }
    write_json(result, out_root / "sec_coverage.json")
    return result


def latest_table(ltm: pl.DataFrame, names: dict[str, str], frames_ends: dict[str, set[str]]) -> pl.DataFrame:
    """Latest LTM row per company (the period end of its latest revenue LTM), with growth and margins."""
    metrics = ["revenue", "gross_profit", "op_income", "net_income", "ocf", "capex", "cap_software", "sbc"]
    if ltm.height == 0:
        wide = pl.DataFrame(schema={"ticker": pl.Utf8, "quarter_end": pl.Utf8, **dict.fromkeys(metrics, pl.Float64)})
    else:
        wide = ltm.pivot(on="metric", index=["ticker", "period_end"], values="ltm").rename(
            {"period_end": "quarter_end"}
        )
    for m in metrics:
        if m not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(m))
    basis = ltm.filter(pl.col("metric") == "revenue").select(
        "ticker", pl.col("period_end").alias("quarter_end"), "basis", "currency"
    )
    wide = (
        yoy(wide.filter(pl.col("revenue").is_not_null()), "revenue")
        .join(basis, on=["ticker", "quarter_end"], how="left")
        .with_columns(
            (pl.col("revenue") / pl.col("revenue_prev") - 1).alias("rev_growth"),
            (pl.col("gross_profit") / pl.col("revenue")).alias("gross_margin"),
            (pl.col("op_income") / pl.col("revenue")).alias("op_margin"),
            (pl.col("net_income") / pl.col("revenue")).alias("net_margin"),
            pl.when(pl.col("capex").is_null() | pl.col("ocf").is_null())
            .then(None)
            .otherwise((pl.col("ocf") - pl.col("capex") - pl.col("cap_software").fill_null(0.0)) / pl.col("revenue"))
            .alias("fcf_margin"),
            (pl.col("sbc") / pl.col("revenue")).alias("sbc_pct"),
        )
    )
    latest = (
        wide.sort("quarter_end")
        .group_by("ticker", maintain_order=True)
        .last()
        .rename({"quarter_end": "period_end"})
        .with_columns(
            pl.col("ticker").replace_strict(names, default=None, return_dtype=pl.Utf8).alias("company"),
            pl.struct("ticker", "period_end")
            .map_elements(lambda r: r["period_end"] in frames_ends.get(r["ticker"], set()), return_dtype=pl.Boolean)
            .alias("latest_from_frames"),
        )
        .sort("ticker")
    )
    if latest.height == 0:
        latest = latest.with_columns(pl.lit(None, dtype=pl.Boolean).alias("latest_from_frames"))
    return latest


def main() -> int:
    setup_logging()
    r = build()
    bad = [c for c in r["checks"] if c["status"] == "fail"]
    log.info("payments SEC: %d companies, %d checks (%d failing)", len(r["companies"]), len(r["checks"]), len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
