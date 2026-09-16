"""Case study: statistical arbitrage on US large caps (ISE 537 final project, 2026).

Reads the frozen result tables in data/case_studies/statarb/tables and writes
data/facts/statarb.json plus chart marts. Nothing is recomputed; this is a port of finished work.

Usage:
    uv run python -m pipelines.statarb.publish
"""

from __future__ import annotations

import csv
import logging
import sys

from pipelines.common.log import setup_logging
from pipelines.common.storage import DATA_DIR, FACTS_DIR, MARTS_DIR, utc_now, write_json

log = logging.getLogger("statarb.publish")
TABLES = DATA_DIR / "case_studies" / "statarb" / "tables"


def read(name: str) -> list[dict]:
    with (TABLES / f"{name}.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        o: dict = {}
        for k, v in r.items():
            try:
                o[k] = float(v) if v not in ("", None) else None
            except ValueError:
                o[k] = v
        out.append(o)
    return out


def build() -> dict:
    main = read("main_results")
    tt = read("train_vs_test_sharpe")
    ci = read("sharpe_ci")
    tc = read("tc_sensitivity")
    regime = read("regime_breakdown")
    svr = read("static_vs_rolling")
    pca = {r["metric"]: r["value"] for r in read("pca_diagnostics")}
    hl = read("half_life_summary")
    memmel = read("memmel_tests")
    audit = read("lookahead_audit")
    sparsity = read("lasso_sparsity")

    (MARTS_DIR / "statarb").mkdir(parents=True, exist_ok=True)
    write_json(
        [
            {
                "strategy": r["Strategy"],
                "tc_bps": r["TC_bps"],
                "sharpe": r["Sharpe"],
                "ann_return": r["Ann Return"],
            }
            for r in tc
        ],
        MARTS_DIR / "statarb" / "tc_sensitivity.json",
    )
    write_json(
        [
            {
                "strategy": r["Strategy"],
                "regime": r["Regime"],
                "sharpe": r["Sharpe"],
                "total_return": r["Total Return"],
                "max_dd": r["Max DD"],
                "n_days": r["N days"],
            }
            for r in regime
        ],
        MARTS_DIR / "statarb" / "regime.json",
    )
    write_json(
        [
            {
                "strategy": r["Strategy"],
                "sharpe": r["Sharpe (point)"],
                "lo": r["CI lower (2.5%)"],
                "hi": r["CI upper (97.5%)"],
            }
            for r in ci
        ],
        MARTS_DIR / "statarb" / "sharpe_ci.json",
    )
    write_json(
        [
            {
                "strategy": r["Strategy"],
                "train": r["Train Sharpe (2019)"],
                "test": r["Test Sharpe (2020)"],
                "gap": r["Gap"],
            }
            for r in tt
        ],
        MARTS_DIR / "statarb" / "train_test.json",
    )

    by_strat = {r["Strategy"]: r for r in main}
    best = min((r for r in main if r["Strategy"] in ("OLS", "PCA", "LASSO")), key=lambda r: -r["Sharpe"])
    nz = [r["n_nonzero"] for r in sparsity if r.get("n_nonzero") is not None]
    facts = {
        "project": "statarb-2019-2020",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "universe": {
            "tickers": 85,
            "candidates": 100,
            "dropped": 15,
            "days": 523,
            "train": "2019",
            "test": "2020",
        },
        "tc_bps": 5,
        "main_results": [
            {
                "strategy": r["Strategy"],
                "ann_return": r["Ann Return"],
                "ann_vol": r["Ann Vol"],
                "sharpe": r["Sharpe"],
                "max_dd": r["Max DD"],
                "hit_rate": r["Hit Rate"],
                "trades": r["# Trades"],
            }
            for r in main
        ],
        "best_statarb": {
            "strategy": best["Strategy"],
            "sharpe": best["Sharpe"],
            "ann_return": best["Ann Return"],
            "max_dd": best["Max DD"],
        },
        "buy_and_hold": {
            "sharpe": by_strat["B&H"]["Sharpe"],
            "ann_return": by_strat["B&H"]["Ann Return"],
            "max_dd": by_strat["B&H"]["Max DD"],
        },
        "train_test": [
            {
                "strategy": r["Strategy"],
                "train": r["Train Sharpe (2019)"],
                "test": r["Test Sharpe (2020)"],
                "gap": r["Gap"],
            }
            for r in tt
        ],
        "smallest_gap": min(tt, key=lambda r: r["Gap"])["Strategy"],
        "pca": {
            "k": int(pca["k_chosen"]),
            "cum_var": pca["cum_var_at_k"],
            "corr_pc1_market": pca["corr_PC1_market_proxy"],
        },
        "half_life": [
            {
                "model": r["Model"],
                "mean_days": r["Mean half-life (days)"],
                "median_days": r["Median half-life (days)"],
            }
            for r in hl
        ],
        "rolling": [
            {
                "variant": r["Variant"],
                "static": r["Static Sharpe"],
                "rolling": r["Rolling Sharpe"],
                "improvement": r["Improvement"],
            }
            for r in svr
        ],
        "sharpe_ci": [
            {
                "strategy": r["Strategy"],
                "sharpe": r["Sharpe (point)"],
                "lo": r["CI lower (2.5%)"],
                "hi": r["CI upper (97.5%)"],
            }
            for r in ci
        ],
        "memmel": [{"test": r["Test"], "z": r["z-stat"], "p": r["p-value"]} for r in memmel],
        "lookahead_audit": {
            "checks": len(audit),
            "passed": sum(1 for r in audit if str(r.get("passed")).lower() == "true"),
        },
        "lasso_hedges_mean": round(sum(nz) / len(nz), 1) if nz else None,
        "sources": [
            {
                "name": "Yahoo Finance daily adjusted closes via yfinance",
                "url": "https://github.com/ranaroussi/yfinance",
            },
            {
                "name": "Avellaneda & Lee (2010), Statistical arbitrage in the US equities market",
                "url": "https://doi.org/10.1080/14697680903124632",
            },
        ],
    }
    write_json(facts, FACTS_DIR / "statarb.json")
    return facts


def main() -> int:
    setup_logging()
    f = build()
    log.info(
        "statarb facts: best=%s b&h sharpe=%s audit=%s",
        f["best_statarb"],
        f["buy_and_hold"]["sharpe"],
        f["lookahead_audit"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
