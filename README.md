# portfolio

Data-analysis portfolio by Ruoxuan Song: scheduled pipelines that snapshot public data, and a
static site that turns them into dashboards with written methodology and evaluation.

![snapshot-predmarkets](https://github.com/srx7703/srx7703.github.io/actions/workflows/snapshot-predmarkets.yml/badge.svg)

## What is here

| Path | What |
|---|---|
| `pipelines/predmarkets/` | Twice-daily snapshots of Polymarket and Kalshi quotes and order books for the 2026 US midterms and FOMC decision markets |
| `data/` | Raw responses, normalized parquet snapshots, derived marts, KPI facts |
| `site/` | Astro site (coming) |
| `docs/` | Data model, evaluation plans, design rules |

## Run locally

```
uv sync
uv run pytest
uv run python -m pipelines.predmarkets.snapshot --set midterms
```

Everything uses free, keyless public endpoints (Polymarket Gamma/CLOB, Kalshi public API).
