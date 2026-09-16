# Acceptance review — pipeline and data layer (2026-09-16)

Reviewer: read-only Claude Code subagent. Audited state: `main` @ `7634a6d` (origin one data commit ahead: `fc694fe`). A sibling session was modifying the tree during the audit (uncommitted: `pipelines/finllm/`, `data/facts/finllm.json`, `tests/test_site_numbers.py`, new site components); graded the committed state. No writes, network calls or git state changes were made.

## (a) Checklist

| # | Item | Verdict | One-line reason |
|---|---|---|---|
| 1 | Repo structure vs proposal §3 | PARTIAL | All layers present; sensible merges (one `predmarkets` pipeline, one snapshot workflow) and an extra `data/snapshots` layer are improvements. Gaps: no cross-project `tests/` (facts↔prose, links, spec compile), no `docs/EVALUATION_PLAN.md`, no proposal copy in `docs/`, `Makefile` has no publish/sec/site targets so the "one command rebuilds everything" criterion is unmet, no FOMC decision-day extra run. |
| 2 | Data layers | PASS | raw = append-only json.gz at `data/raw/predmarkets/<set>/<date>/<HHMM>/`; snapshots = narrow quotes/books parquet + dim deltas; marts derived; facts per project. Caveat: `history/<platform>.parquet` and `sec/facts/<T>.parquet` are overwrite-in-place, documented. |
| 3 | Tests and validation | PARTIAL | pandera schemas exist and run before every write. `uv run pytest -q`: 18 passed at HEAD. ruff: 0 findings at HEAD. No freshness test; no pandera on SEC, backfill history, marts or facts. |
| 4 | Workflows | PARTIAL | Crons/scopes correct. Three real defects: bot pushes never trigger `deploy-site` (F1), a publish failure discards the run's snapshot (F3), rebase-conflict retry cannot recover (F10). `deploy-site.yml` has no pytest gate. |
| 5 | Docs | PARTIAL | Mostly accurate. Stale: DuckDB / `transform.sql` / `metrics.py` / `evaluate.py` claims in `CLAUDE.md` and `storage.py`, Brier cutoff wording in `DATA_MODEL.md`, "deployed on every push" in `README.md`, missing `finllm` and Makefile coverage. |
| 6 | Facts contract | PARTIAL | Pages import facts at build time. But three KPIs are computed misleadingly (F4, F5, F6) and four pages hand-type result numbers (F8); no CI guard at HEAD. |
| 7 | Robustness | PARTIAL | Rate limits fine; runs take 105 s / 60 s vs 40-min timeout. Will break or mislead within 7 weeks: FOMC scorecard never populates (F2), empty `SEC_USER_AGENT` secret → 403 (F7), ~200 MB clone by election day (F9), one 404 kills a platform's run (F15), no resolution capture for midterms (F16). |

## (b) Findings, by severity

**HIGH**

1. **Scheduled data commits never rebuild the site.** Data workflows push with the default `GITHUB_TOKEN` (`.github/workflows/snapshot-predmarkets.yml`, `refresh-weekly.yml`), and GitHub does not fire `on: push` workflows for GITHUB_TOKEN pushes; `deploy-site.yml` is push-only. Site data is copied at build time, so `facts.json` updates in git but the page's "data as of" stays stale. Fix: `on: workflow_run` in `deploy-site.yml` for both data workflows (or dispatch it from them).
2. **FOMC scorecard will stay empty.** `resolutions()` reads only the latest `kalshi_tier1_events.json.gz` and looks for `result == "yes"`; those events come from `iter_events(status="open")`, so a settled event drops out of the file before or shortly after it resolves. Verified: all 167 FOMC tier-1 markets in raw are `status=active`, `result=""`. Fix: fetch settled events, persist an append-only `resolutions.json`, read it in `resolutions()`.
3. **A publish error throws away the snapshot just taken.** The publish step precedes the commit step with no `continue-on-error` / `if: always()`. Fix: `continue-on-error: true` on publish, `if: always()` on the commit step, surface publish failure in the final step.

**MEDIUM**

4. `markets_tracked` / `live_markets_tracked` flip twice a day: `publish.py` takes the latest coverage row regardless of scope (19,973/11,549 after a full run, 431/1,007 after a tier1 run). Fix: restrict to `scope == "full"` runs.
5. "Live markets" is inflated by dead books: live = `prob ∈ [0.02, 0.98]`; 5,844 of 11,549 Polymarket markets have spread ≥ 0.50 and 5,190 have zero volume, yet 10,943 count as live. Fix: live = spread ≤ 0.10 and some volume; state the definition in facts.
6. Pre-registered Brier cutoff is misstated: code uses `date < decision_date` while docs and page say "last observation before 17:30 UTC on decision day". Multi-outcome Brier ranges 0–2 and bucket probabilities are not normalised (Kalshi sums 1.01–1.455). Fix: implement the timestamp cutoff on snapshot rows, state the range, disclose the sum.
7. `SEC_USER_AGENT` handling: an empty secret yields `""` (`os.environ.get(..., default)`) → SEC 403; the default UA has no contact; the secret's value is persisted into the committed `companies.json`. Fix: `or default`, drop `user_agent` from `companies.json`.
8. Hand-typed result numbers in prose, no guard at HEAD: `saas-benchmark.mdx` pipeline status props and "clusters between 30 and 60"; `statarb-2019-2020.mdx` 85 / 523 / 15 / 20–27 days / 33 and 23 days; `financial-llm-sec.mdx` "+5.8 … +3.5". Fix: route through facts; harden the guardrail.
9. Repo growth ≈ 2.1 MB/day (raw tier-1 events re-archived every run although the set barely changes). Fix: archive raw tier-1 events only on full runs; consider `fetch-depth: 1`.
10. Push retry loop cannot recover from a rebase conflict. Fix: `git rebase --abort` then `git pull --rebase -X theirs`.

**LOW**

11. Stale docs: DuckDB and `transform.sql`/`metrics.py`/`evaluate.py` referenced in `CLAUDE.md`, `storage.py`, `midterms-2026.mdx`; `README.md` "deployed on every push"; `Makefile` lacks publish/backfill/sec/site targets; `DATA_MODEL.md` omits `finllm`.
12. Kalshi contract volume labelled `volume_usd` in `midterms_coverage.json`. Fix: `volume` + `volume_unit`.
13. pandera only on predmarkets snapshot tables; none on backfill history, SEC marts or facts.
14. One 4xx on a retired Kalshi series aborts the whole Kalshi side of a run. Fix: try/except per series.
15. No resolution capture for project A: settled midterm markets disappear from `closed=false` / `status=open` queries. Fix: a settled sweep of tier-1 events writing `resolutions.json`.
16. `fomc.py` drops a platform that had no quotes in the latest run, and the page dereferences both unconditionally → build failure. Fix: always emit both keys or guard the page.
17. Cosmetic: trailing spaces in `top_races_polymarket` titles; `join_asof` sortedness warning.

## (c) Better than the proposal asked for

1. A `data/snapshots` layer with narrow quotes plus append-only dimension deltas (first full run 1.9 MB dimension, next run 8 KB).
2. Platform-failure isolation with partial-data commit and a red status.
3. SEC data quality automated and larger than asked: direct-vs-derived quarter reconciliation with a published agreement rate, per-company tag-coverage mart, 29 companies instead of 20, SBC / R&D / S&M ratios.
