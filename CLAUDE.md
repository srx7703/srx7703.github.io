# CLAUDE.md — portfolio repo conventions

This repo is Ruoxuan Song's data-analysis portfolio: scheduled data pipelines (Python + DuckDB)
that feed a static site (Astro + Vega-Lite). Everything is text and reproducible; nothing is
built by hand in a GUI tool.

## Layout

- `pipelines/common/`      shared http / storage / logging helpers
- `pipelines/<project>/`   one folder per data pipeline; `snapshot.py` / `transform.sql` / `metrics.py` / `evaluate.py` / `tests/`
- `data/raw/`              immutable API responses (json.gz), partitioned `<set>/<date>/<HHMM>/` — never edit or rewrite
- `data/snapshots/`        normalized per-run parquet tables + `dim_*.parquet` slowly-changing dimensions
- `data/marts/`            DuckDB-derived tables the site reads
- `data/facts/`            per-project KPI JSON; **every number in site prose comes from here**
- `site/`                  Astro site (components, Vega-Lite specs, project MDX pages)
- `docs/`                  data model, evaluation plans, design notes
- `.github/workflows/`     scheduled snapshots + deploy

## Rules

1. Numbers in narrative text are templated from `data/facts/<project>.json`, never typed by hand.
2. Raw snapshots are append-only. Fix bugs in transforms, not by rewriting history.
3. Every pipeline has pandera schemas on its outputs and pytest unit tests on its parsers.
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
```

Python runs from the repo root (`python -m pipelines.<pkg>.<module>`); `pythonpath = ["."]`.

## Working agreements with Claude Code

- Plan before adding a new project: question, metric definitions, dashboard composition.
- Use `.claude/skills/portfolio-project-scaffold` to create a project; do not hand-roll layouts.
- Before declaring a project done, run the Definition of Done checklist in `docs/DEFINITION_OF_DONE.md`.
- Never push to `main` or deploy without the owner's go-ahead in the session.
