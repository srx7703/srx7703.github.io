# portfolio

Data-analysis portfolio by Song Ruoxuan: scheduled pipelines that snapshot public data, and a
static site that turns them into dashboards with written methodology and evaluation.

![snapshot-predmarkets](https://github.com/srx7703/srx7703.github.io/actions/workflows/snapshot-predmarkets.yml/badge.svg)
![valuation](https://github.com/srx7703/srx7703.github.io/actions/workflows/valuation.yml/badge.svg)

## What is here

| Path | What |
|---|---|
| `pipelines/predmarkets/` | Twice-daily snapshots of Polymarket and Kalshi quotes and order books for the 2026 US midterms and FOMC decision markets; history backfill; FOMC outcome grid + scorecard |
| `pipelines/sec/` | Weekly SEC XBRL companyfacts ingest for 29 SaaS companies, year-to-date differencing into clean quarters, TTM benchmark (Rule of 40) |
| `pipelines/statarb/` | Port of a finished stat-arb backtest (ISE 537) from frozen result tables |
| `pipelines/finllm/` | Port of the multi-horizon-financial-llm 4-way evaluation (Gemma 2/4 + SEC LoRA) |
| `pipelines/valuation/` | Daily prices and weekly analyst consensus for 68 listed optical-module and solid-state-battery companies worldwide; fiscal years restated onto calendar years; forward PE, forecast dispersion and market share on a computed and a cited basis |
| `tests/test_site_numbers.py` | Guardrail: no hand-typed data numbers in project-page prose |
| `data/` | Raw responses, normalized parquet snapshots, derived marts, KPI facts |
| `site/` | Astro 7 + Vega-Lite static site, deployed to GitHub Pages after every push and every data refresh |
| `docs/` | Data model, evaluation plans, design rules |

## Run locally

```
make all          # setup, tests, full snapshot, FOMC backfill, SEC refresh, marts/facts, site build
make test         # unit tests + prose-numbers guardrail
make snapshot     # prediction-market snapshot only
```

Prediction-market endpoints are keyless. The SEC API needs a descriptive `User-Agent` with a contact
address: set `SEC_USER_AGENT="your-app (you@example.com)"` locally and as a repository secret for Actions.

The valuation pipeline uses Yahoo Finance, East Money, SEC EDGAR and FRED, all keyless except SEC.
Yahoo rate-limits hard: the schedule keeps it to about seventy calls a weekday and a hundred more on
Mondays, and every payload is archived so a throttled run degrades instead of losing data.

Site: https://srx7703.github.io
