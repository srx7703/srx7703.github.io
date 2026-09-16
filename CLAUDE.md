# CLAUDE.md — portfolio repo conventions

This repo is Ruoxuan Song's data-analysis portfolio: scheduled data pipelines (Python + Polars,
pandera-validated) that feed a static site (Astro + Vega-Lite). Everything is text and reproducible; nothing is
built by hand in a GUI tool.

## Layout

- `pipelines/common/`      shared http / storage / logging helpers
- `pipelines/<project>/`   one folder per data pipeline: ingest/snapshot → transform → publish (marts + facts) + `tests/`
- `data/raw/`              immutable API responses (json.gz), partitioned `<set>/<date>/<HHMM>/` — never edit or rewrite
- `data/snapshots/`        normalized per-run parquet tables + `dim_*.parquet` slowly-changing dimensions
- `data/marts/`            derived tables (parquet/json) the site reads; large ones are loaded by URL
- `data/facts/`            per-project KPI JSON; **every number in site prose comes from here**
- `site/`                  Astro site (components, Vega-Lite specs, project MDX pages)
- `docs/`                  data model, evaluation plans, design notes
- `.github/workflows/`     scheduled snapshots + deploy

## Rules

1. Numbers in narrative text are templated from `data/facts/<project>.json`, never typed by hand.
2. Raw snapshots are append-only. Fix bugs in transforms, not by rewriting history.
3. Every live pipeline validates its outputs with pandera before writing (a schema failure blocks the write); range, uniqueness and freshness checks are recorded in `facts.checks` and shown on the page. Frozen case-study ports (statarb, finllm) fingerprint their inputs instead. Parsers and derivation rules have pytest unit tests.
4. No secrets in the repo. Free-tier API keys (FRED, Tiingo) come from env vars / Actions secrets.
5. Platform or source failures are isolated: one failing source must not lose the others' data.
6. Charts follow `docs/CHART_RULES.md` (title states the finding, subtitle states the metric,
   source + as-of stamp, design tokens only, readable in light and dark).
7. Commits: conventional prefixes — `feat(midterms):`, `data(predmarkets):`, `site:`, `docs:`, `ci:`.

## Commands

```
uv sync                      # deps
uv run pytest                # tests
uv run ruff check .          # lint
make snapshot                # run all prediction-market snapshots locally
make publish                 # rebuild every mart and facts file
make site                    # build the site (runs the data sync hook)
make all                     # tests + snapshot + publish + site
```

Python runs from the repo root (`python -m pipelines.<pkg>.<module>`); `pythonpath = ["."]`.

## Working agreements with Claude Code

- Plan before adding a new project: question, metric definitions, dashboard composition.
- Use `.claude/skills/portfolio-project-scaffold` to create a project; do not hand-roll layouts.
- Before declaring a project done, run the Definition of Done checklist in `docs/DEFINITION_OF_DONE.md`.
- Never push to `main` or deploy without the owner's go-ahead in the session.
