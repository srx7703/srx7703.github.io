"""Case study: SEC-domain LoRA fine-tune of Gemma 2 27B / Gemma 4 31B (multi-horizon-financial-llm).

Reads the frozen 4-way evaluation report copied from the original repo and writes
data/facts/finllm.json plus chart marts. Nothing is recomputed.

Usage:
    uv run python -m pipelines.finllm.publish
"""

from __future__ import annotations

import json
import logging
import sys

from pipelines.common.log import setup_logging
from pipelines.common.storage import DATA_DIR, FACTS_DIR, MARTS_DIR, utc_now, write_json

log = logging.getLogger("finllm.publish")
SRC = DATA_DIR / "case_studies" / "finllm" / "evaluation_results_phase2.json"

MODELS = {
    "base": {"family": "Gemma 2 27B", "variant": "base"},
    "v2": {"family": "Gemma 2 27B", "variant": "+ SEC LoRA"},
    "base_gemma4": {"family": "Gemma 4 31B", "variant": "base"},
    "v2_gemma4": {"family": "Gemma 4 31B", "variant": "+ SEC LoRA"},
}


def build() -> dict:
    d = json.loads(SRC.read_text())
    res = d["results"]
    n = d["test_set_size"]
    summary = [
        {
            "key": k,
            **MODELS[k],
            "label": res[k]["label"],
            "f1": res[k]["bertscore_f1"],
            "p": res[k]["bertscore_p"],
            "r": res[k]["bertscore_r"],
        }
        for k in MODELS
    ]
    items = []
    for k in MODELS:
        for i, f1 in enumerate(res[k]["per_item_f1"], 1):
            items.append({"item": i, "key": k, **MODELS[k], "f1": f1})
    # per-item deltas for the Gemma 4 pair, sorted for the dumbbell chart
    g4 = [
        {"item": i + 1, "base": b, "lora": v, "delta": round(v - b, 4)}
        for i, (b, v) in enumerate(zip(res["base_gemma4"]["per_item_f1"], res["v2_gemma4"]["per_item_f1"], strict=True))
    ]
    g2 = [
        {"item": i + 1, "base": b, "lora": v, "delta": round(v - b, 4)}
        for i, (b, v) in enumerate(zip(res["base"]["per_item_f1"], res["v2"]["per_item_f1"], strict=True))
    ]
    (MARTS_DIR / "finllm").mkdir(parents=True, exist_ok=True)
    write_json(summary, MARTS_DIR / "finllm" / "summary.json")
    write_json(items, MARTS_DIR / "finllm" / "per_item.json")
    write_json({"gemma4": g4, "gemma2": g2}, MARTS_DIR / "finllm" / "paired.json")

    t2, t4 = d["paired_tests"]["gemma2_v2_vs_base"], d["paired_tests"]["gemma4_v2_vs_base"]
    facts = {
        "project": "financial-llm-sec",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "test_items": n,
        "dataset": {
            "qa_pairs": 1060,
            "filings": 381,
            "tickers": 69,
            "forms": ["10-K", "10-Q", "8-K"],
            "teacher": "Gemini 3.1 Pro",
        },
        "training": {"hardware": "TPU v6e-8 (Trillium)", "method": "LoRA via HF PEFT, PyTorch/XLA SPMD FSDPv2, bf16"},
        "summary": summary,
        "deltas_pct": d["deltas_pct"],
        "paired": {
            "gemma2": {
                "mean_delta": t2["mean_delta"],
                "t": t2["t_stat"],
                "p": t2["p_value"],
                "ci_95": t2["ci_95"],
                "wins": t2["wins"],
                "losses": t2["losses"],
            },
            "gemma4": {
                "mean_delta": t4["mean_delta"],
                "t": t4["t_stat"],
                "p": t4["p_value"],
                "ci_95": t4["ci_95"],
                "wins": t4["wins"],
                "losses": t4["losses"],
            },
        },
        "largest_item_gain_gemma4": max(g4, key=lambda r: r["delta"]),
        "smallest_item_gain_gemma4": min(g4, key=lambda r: r["delta"]),
        "links": {
            "repo": "https://github.com/srx7703/multi-horizon-financial-llm",
            "adapter_gemma2": "https://huggingface.co/Srx7703/gemma-2-27b-financial-adapter",
            "adapter_gemma4": "https://huggingface.co/Srx7703/gemma-4-31b-financial-adapter",
        },
        "sources": [
            {"name": "SEC EDGAR filings via edgartools", "url": "https://www.sec.gov/search-filings"},
            {"name": "BERTScore (Zhang et al., 2020)", "url": "https://arxiv.org/abs/1904.09675"},
        ],
    }
    write_json(facts, FACTS_DIR / "finllm.json")
    return facts


def main() -> int:
    setup_logging()
    f = build()
    log.info(
        "finllm facts: deltas=%s gemma4 wins=%s/%s", f["deltas_pct"], f["paired"]["gemma4"]["wins"], f["test_items"]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
