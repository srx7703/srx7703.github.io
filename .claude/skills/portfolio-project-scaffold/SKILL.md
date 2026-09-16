---
name: portfolio-project-scaffold
description: Scaffold a new portfolio project end to end — pipeline folder (ingest/transform/metrics/evaluate/tests), facts JSON contract, Vega-Lite chart stubs, project MDX page and Actions workflow — following this repo's layout. Use when the owner says "new project", "add a project", "scaffold <name>", or starts a pipeline/dashboard that does not exist yet.
---

# portfolio-project-scaffold

Create everything a project needs so that the first commit already runs green.

## Inputs to confirm first (ask, do not guess)
1. Project slug (kebab-case) and title.
2. The question the page answers in one sentence.
3. Data sources (URL, auth, rate limits, refresh cadence).
4. The three key figures (the ruled `KpiRow`) and the hero chart.
5. Evaluation: metric, baseline, and what "good" means.

## Steps
1. `pipelines/<slug>/` with `__init__.py`, `ingest.py`, `transform.py` (DuckDB SQL inline or `transform.sql`), `metrics.py`, `evaluate.py`, `tests/test_parsers.py`. Reuse `pipelines/common`.
2. `data/facts/<slug>.json` contract: `generated_at`, `last_updated`, KPI keys, `sources[]`. Every number the page shows lives here.
3. `site/src/charts/<slug>/*.ts` — Vega-Lite spec builders that inline data from `data/marts/`.
4. `site/src/content/projects/<slug>.mdx` following the page skeleton: takeaway paragraph with `<Sidenote>` definitions → `PipelineStatus` → `KpiRow` → figures (`ChartCard`, `size="full"` for two-panel charts) → result sections → `<Appendix>` holding Method → Evaluation → Data quality → Build notes → Changelog. Layout and components are described in `docs/DESIGN.md`.
5. `.github/workflows/<slug>.yml` cron + commit-back, copied from `snapshot-predmarkets.yml`.
6. Run `uv run pytest`, `uv run ruff check .`, `npm run build` in `site/`, and the browser preview before reporting done.
7. Update `README.md` table and `docs/DATA_MODEL.md`.

Finish with the checklist in `docs/DEFINITION_OF_DONE.md`.
