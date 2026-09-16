"""Turn XBRL duration facts into clean quarterly series, TTM metrics and the benchmark table.

Usage:
    uv run python -m pipelines.sec.transform

Writes:
    data/marts/sec/saas_quarterly.parquet        one row per ticker x quarter_end x metric (quarterly, ttm)
    data/marts/sec/saas_ttm.json                 TTM metrics and ratios per ticker x quarter_end
    data/marts/sec/saas_latest.json              latest TTM benchmark row per company
    data/marts/sec/saas_coverage.json            tags used, quarters available, reconciliation per company
    data/facts/saas.json                         KPI numbers for the page

Quarterly derivation (per ticker, metric), from duration facts (start, end, value):
    1. a fact whose duration is one fiscal quarter (80-100 days) is used as-is;
    2. otherwise the quarter is the difference between consecutive spans sharing a start date
       (6M-3M, 9M-6M, FY-9M) - this is how cash-flow items, reported year-to-date only, become
       quarterly, and how Q4 is recovered from the 10-K.
Where a quarter is available both ways the two are reconciled and the agreement rate is reported.
Facts from several tags for one metric are merged, higher-priority tag winning on identical spans.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date

import pandera.polars as pa
import polars as pl

from pipelines.common.checks import check, freshness, non_null, uniqueness
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR, utc_now, write_json
from pipelines.sec.config import TAG_MAP

log = logging.getLogger("sec.transform")

Q_MIN, Q_MAX = 80, 100  # days in one fiscal quarter (52/53-week years included)
TTM_MIN, TTM_MAX = 250, 290  # days from first to last quarter end in a 4-quarter window

# Contract on the benchmark table the page is built from (validated before facts are written).
LATEST_SCHEMA = pa.DataFrameSchema(
    {
        "ticker": pa.Column(str),
        "quarter_end": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "revenue": pa.Column(float, pa.Check.gt(0)),
        "rev_growth": pa.Column(float, pa.Check.in_range(-1.0, 5.0), nullable=True),
        "gross_margin": pa.Column(float, pa.Check.in_range(-2.0, 1.0), nullable=True),
        "op_margin": pa.Column(float, pa.Check.in_range(-5.0, 1.0), nullable=True),
        "fcf_margin": pa.Column(float, pa.Check.in_range(-5.0, 1.0), nullable=True),
        "rule_of_40": pa.Column(float, pa.Check.in_range(-500.0, 500.0), nullable=True),
    },
    unique=["ticker"],
)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def merge_metric_facts(df: pl.DataFrame, metric: str) -> tuple[dict[tuple[str, str], float], list[str]]:
    """Merge duration facts across the metric's fallback tags: (start, end) -> value.

    Tags are applied in priority order and never overwrite a span already filled, so a company that
    switched tags over time keeps its full history. Returns the spans and the tags actually used.
    """
    spans: dict[tuple[str, str], float] = {}
    used: list[str] = []
    sub = df.filter(pl.col("metric") == metric)
    for tag in TAG_MAP[metric][0]:
        rows = sub.filter(pl.col("tag") == tag)
        if rows.height == 0:
            continue
        added = 0
        for r in rows.iter_rows(named=True):
            key = (r["start"], r["end"])
            if key not in spans:
                spans[key] = float(r["val"])
                added += 1
        if added:
            used.append(tag)
    return spans, used


def quarterly_series(
    spans: dict[tuple[str, str], float],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Quarterly values by period end from duration spans; also (direct, derived) pairs for reconciliation."""
    direct: dict[str, float] = {}
    ytd: dict[str, dict[str, float]] = {}
    for (s, e), v in spans.items():
        d = _days(s, e)
        if Q_MIN <= d <= Q_MAX:
            direct[e] = v
        elif d > Q_MAX:
            ytd.setdefault(s, {})[e] = v
    derived: dict[str, float] = {}
    for s, ends in ytd.items():
        chain = [
            (e, v) for (s2, e), v in spans.items() if s2 == s and Q_MIN <= _days(s2, e) <= Q_MAX
        ]  # 3M with same start
        chain += list(ends.items())
        chain.sort()
        for (pe, pv), (e, v) in zip(chain, chain[1:], strict=False):
            if Q_MIN <= _days(pe, e) <= Q_MAX:
                derived[e] = v - pv
    recon = {e: (direct[e], derived[e]) for e in direct if e in derived}
    return {**derived, **direct}, recon


AVERAGE_METRICS = {"diluted_shares"}  # weighted averages, not flows: never differenced or summed


def ttm(qs: dict[str, float], agg: str = "sum") -> dict[str, float]:
    ends = sorted(qs)
    out: dict[str, float] = {}
    for i in range(3, len(ends)):
        window = ends[i - 3 : i + 1]
        if TTM_MIN <= _days(window[0], window[-1]) <= TTM_MAX:
            total = sum(qs[e] for e in window)
            out[ends[i]] = total / 4 if agg == "mean" else total
    return out


def yoy(values: pl.DataFrame, col: str) -> pl.DataFrame:
    """Add <col>_prev: the value one year earlier (nearest quarter end within 20 days), per ticker."""
    left = values.select("ticker", "quarter_end", col).with_columns(pl.col("quarter_end").str.to_date().alias("qd"))
    right = left.select(
        "ticker",
        pl.col("qd").dt.offset_by("1y").alias("qd"),
        pl.col(col).alias(f"{col}_prev"),
    ).sort("qd")
    joined = left.sort("ticker", "qd").join_asof(
        right.sort("ticker", "qd"), on="qd", by="ticker", strategy="nearest", tolerance="20d", check_sortedness=False
    )
    return values.join(joined.select("ticker", "quarter_end", f"{col}_prev"), on=["ticker", "quarter_end"], how="left")


def build() -> dict:
    facts_dir = SNAP_DIR / "sec" / "facts"
    companies = json.loads((SNAP_DIR / "sec" / "companies.json").read_text())["companies"]
    rows: list[dict] = []
    coverage: dict[str, dict] = {}
    for path in sorted(facts_dir.glob("*.parquet")):
        ticker = path.stem
        df = pl.read_parquet(path)
        cov: dict = {
            "name": companies.get(ticker, {}).get("name"),
            "tags": {},
            "quarters": {},
            "reconciliation": {},
        }
        series: dict[str, dict[str, float]] = {}
        for metric in TAG_MAP:
            spans, used = merge_metric_facts(df, metric)
            if not spans:
                continue
            if metric in AVERAGE_METRICS:
                q = {e: v for (st, e), v in spans.items() if Q_MIN <= _days(st, e) <= Q_MAX}
                recon = {}
            else:
                q, recon = quarterly_series(spans)
            series[metric] = q
            cov["tags"][metric] = used
            cov["quarters"][metric] = len(q)
            if recon:
                ok = sum(1 for d, v in recon.values() if abs(d - v) <= max(1.0, 0.005 * abs(d)))
                cov["reconciliation"][metric] = {"pairs": len(recon), "agree": ok}
        if "gross_profit" not in series and "revenue" in series and "cogs" in series:
            series["gross_profit"] = {
                e: series["revenue"][e] - series["cogs"][e] for e in series["revenue"] if e in series["cogs"]
            }
            cov["tags"]["gross_profit"] = ["derived: revenue - cogs"]
            cov["quarters"]["gross_profit"] = len(series["gross_profit"])
        for metric, q in series.items():
            t = ttm(q, "mean" if metric in AVERAGE_METRICS else "sum")
            for e, v in q.items():
                rows.append({"ticker": ticker, "quarter_end": e, "metric": metric, "quarterly": v, "ttm": t.get(e)})
        coverage[ticker] = cov

    quarterly = pl.DataFrame(
        rows,
        schema={
            "ticker": pl.Utf8,
            "quarter_end": pl.Utf8,
            "metric": pl.Utf8,
            "quarterly": pl.Float64,
            "ttm": pl.Float64,
        },
    ).sort("ticker", "metric", "quarter_end")
    wide = quarterly.pivot(on="metric", index=["ticker", "quarter_end"], values="ttm").sort("ticker", "quarter_end")
    for m in TAG_MAP:
        if m not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(m))
    wide = (
        yoy(wide, "revenue")
        .with_columns(
            (pl.col("revenue") / pl.col("revenue_prev") - 1).alias("rev_growth"),
            (pl.col("gross_profit") / pl.col("revenue")).alias("gross_margin"),
            (pl.col("op_income") / pl.col("revenue")).alias("op_margin"),
            (pl.col("net_income") / pl.col("revenue")).alias("net_margin"),
            pl.when(pl.col("capex").is_null())
            .then(None)
            .otherwise((pl.col("ocf") - pl.col("capex") - pl.col("cap_software").fill_null(0.0)) / pl.col("revenue"))
            .alias("fcf_margin"),  # no capex tag -> no FCF, rather than a silently flattering number
            (pl.col("sbc") / pl.col("revenue")).alias("sbc_pct"),
            (pl.col("rnd") / pl.col("revenue")).alias("rnd_pct"),
            (pl.col("snm") / pl.col("revenue")).alias("snm_pct"),
        )
        .with_columns(((pl.col("rev_growth") + pl.col("fcf_margin")) * 100).alias("rule_of_40"))
    )

    latest = (
        wide.filter(pl.col("revenue").is_not_null())
        .sort("quarter_end")
        .group_by("ticker", maintain_order=True)
        .last()
        .with_columns(
            pl.col("ticker").map_elements(lambda t: coverage.get(t, {}).get("name"), return_dtype=pl.Utf8).alias("name")
        )
        .sort("rule_of_40", descending=True, nulls_last=True)
    )

    (MARTS_DIR / "sec").mkdir(parents=True, exist_ok=True)
    quarterly.write_parquet(MARTS_DIR / "sec" / "saas_quarterly.parquet")
    write_json(wide.to_dicts(), MARTS_DIR / "sec" / "saas_ttm.json")
    write_json(latest.to_dicts(), MARTS_DIR / "sec" / "saas_latest.json")
    write_json(coverage, MARTS_DIR / "sec" / "saas_coverage.json")

    scored = latest.filter(pl.col("rule_of_40").is_not_null())
    recon_pairs = sum(r.get("pairs", 0) for c in coverage.values() for r in c["reconciliation"].values())
    recon_ok = sum(r.get("agree", 0) for c in coverage.values() for r in c["reconciliation"].values())

    def med(col: str) -> float | None:
        s = scored[col].drop_nulls()
        return round(float(s.median()), 4) if s.len() else None

    core = ("revenue", "gross_profit", "op_income", "ocf", "capex", "sbc")
    missing = {t: [m for m in core if m not in c["quarters"]] for t, c in coverage.items()}
    missing = {t: ms for t, ms in missing.items() if ms}
    missing_latest = {
        r["ticker"]: [m for m in core if r.get(m) is None]
        for r in latest.iter_rows(named=True)
        if any(r.get(m) is None for m in core)
    }
    LATEST_SCHEMA.validate(latest)

    def brief(r: dict) -> dict:
        return {
            "ticker": r["ticker"],
            "name": r["name"],
            "quarter_end": r["quarter_end"],
            "rule_of_40": round(r["rule_of_40"], 1),
            "rev_growth": round(r["rev_growth"], 4),
            "fcf_margin": round(r["fcf_margin"], 4),
        }

    log_path = SNAP_DIR / "sec" / "refresh_log.jsonl"
    refreshes = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()] if log_path.exists() else []
    r40 = scored["rule_of_40"].drop_nulls()
    facts = {
        "project": "saas-benchmark",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "first_refresh": refreshes[0]["ts"] if refreshes else None,
        "last_refresh": refreshes[-1]["ts"] if refreshes else None,
        "n_refreshes": len(refreshes),
        "rule_of_40_p25": round(float(r40.quantile(0.25)), 1) if r40.len() else None,
        "rule_of_40_p75": round(float(r40.quantile(0.75)), 1) if r40.len() else None,
        "companies": latest.height,
        "companies_scored": scored.height,
        "latest_quarter_end_max": latest["quarter_end"].max(),
        "latest_quarter_end_min": latest["quarter_end"].min(),
        "median_rev_growth": med("rev_growth"),
        "median_gross_margin": med("gross_margin"),
        "median_op_margin": med("op_margin"),
        "median_fcf_margin": med("fcf_margin"),
        "median_sbc_pct": med("sbc_pct"),
        "rule_of_40_pass": int((scored["rule_of_40"] >= 40).sum()),
        "leaders": [brief(r) for r in scored.head(5).iter_rows(named=True)],
        "laggards": [brief(r) for r in scored.tail(3).iter_rows(named=True)],
        "reconciliation": {
            "pairs": recon_pairs,
            "agree": recon_ok,
            "rate": round(recon_ok / recon_pairs, 4) if recon_pairs else None,
            "tolerance": "0.5%",
        },
        "checks": [
            check(
                "Companies fetched",
                latest.height == len(coverage),
                f"{latest.height} of {len(coverage)} companies have a scored latest quarter",
            ),
            non_null(latest, ["revenue", "gross_profit", "op_income", "ocf", "capex"], id_col="ticker", warn_up_to=6),
            uniqueness(latest, ["ticker"], "companies"),
            check(
                "Reconciliation",
                (recon_ok / recon_pairs if recon_pairs else 0) >= 0.95,
                f"{recon_ok:,} of {recon_pairs:,} direct-vs-derived quarters agree within 0.5%",
                warn=(recon_ok / recon_pairs if recon_pairs else 0) >= 0.9,
            ),
            freshness(refreshes[-1]["ts"] if refreshes else None, 24 * 8, "refresh"),
        ],
        "quarters_total": int(quarterly.filter(pl.col("metric") == "revenue").height),
        "missing_metrics": missing,
        "missing_latest": missing_latest,
        "sources": [
            {
                "name": "SEC EDGAR XBRL companyfacts API",
                "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            }
        ],
    }
    write_json(facts, FACTS_DIR / "saas.json")
    return facts


def main() -> int:
    setup_logging()
    f = build()
    log.info(
        "saas facts: companies=%s scored=%s median growth=%s median fcf=%s rule40 pass=%s recon=%s missing=%s",
        f["companies"],
        f["companies_scored"],
        f["median_rev_growth"],
        f["median_fcf_margin"],
        f["rule_of_40_pass"],
        f["reconciliation"],
        f["missing_metrics"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
