# Acceptance review — site and pages (2026-09-16)

Reviewer: read-only Claude Code subagent. Live site = build of commit `7634a6d`; working tree had uncommitted work (DataTable/FilterBar/DownloadCsv, changelog page, `financial-llm-sec.mdx`, `tests/test_site_numbers.py`). Findings tagged (live) or (WT = working tree only) where it matters.

## (a) Checklist verdicts

| # | Item | Verdict | One-line reason |
|---|---|---|---|
| 1 | IA vs §7 | PARTIAL | Home/projects/project pages/how-i-work/about exist; `/changelog` 404 live (WT has it); no `/projects` filters; cards lack KPI/sparkline/screenshot; no resume anywhere |
| 2 | §5 skeleton per page | PARTIAL | Order right on all pages; Data quality missing on statarb (and finllm), weak elsewhere; SaaS pipeline status hand-typed; no SQL snippets in any Method |
| 3 | Numbers from facts | FAIL | 12 hand-typed result literals remain (saas ×4, statarb ×6, finllm ×3); guardrail test never scans chart titles, ignores 2-digit results, allow-lists results, and is not run in CI |
| 4 | Chart rules | PARTIAL | Tokens/stamps/0–100% axes/aria all good; but one hero title is false (statarb), one bar chart has a truncated baseline (finllm, WT), one title is a label not a finding (fomc path), scatter reference line overshoots fixed domains, facet chart not mobile-safe |
| 5 | Design system | PARTIAL (live) / near-PASS (WT) | No hex outside tokens; every colour has a dark value except `--series-neutral`; live site has 4 of 7 components, WT has all 7 |
| 6 | A11y + honesty | PARTIAL | Titles, `role="img"`+aria, alt, repo links all present; heading order h1→h3→h2 on every project page; several claims the data contradicts |
| 7 | How I work vs §7/§8 | PARTIAL | Covers CLAUDE.md, four skills, human/AI split, one failure; missing subagent reviewers, QA loop, CI gates, checkpoints; contains overclaims |

## (b) Findings, by severity

**HIGH**

1. Data commits never redeploy the site (`deploy-site.yml` push-only; data pushes use `GITHUB_TOKEN`). Evidence: refresh-weekly pushed `fc694fe` at 06:09Z but the last deploy was 06:05Z; live `facts/saas.json` still `06:01:31`.
2. Deploy has no test gate (no `uv run pytest` before build).
3. Hero chart title is false — statarb: "Costs decide the sign: at zero cost LASSO is roughly flat, at 20 bps every model loses". `tc_sensitivity.json` at 0 bps: OLS −0.47, PCA −0.53, LASSO −0.04 — every model already negative.
4. Latent wrong number on the FOMC lede after today's decision: the lede prints the larger of hike/cut even when `sideLabel` returns "no change"; "Both platforms have converged" uses Polymarket only.
5. SaaS pipeline status is hand-typed (`firstSnapshot="2026-09-16T05:53:33+00:00" nSnapshots={1} nDays={1}`) and already wrong after the weekly run.
6. Unit error in the finllm hero title: "+5.8 points of F1" is +4.8 points (+5.76 % relative).
7. Truncated bar chart in `charts/finllm/summary.ts` (`domain: [0.7, 0.9]`) with no note.

**MEDIUM**

8. Hand-typed result numbers: saas ariaLabel "29 software companies", "only company above 100 / clusters between 30 and 60", changelog "29 companies", "within 0.5%"; statarb "5 bps", "33 and 23 trading days", "Top-100", "Fifteen names", "85 tickers over 523", "the other 84", "20–27 days"; finllm "same 20 held-out", "p < 0.001", axis title "n = 20"; frontmatter summaries drift.
9. Guardrail blind where it matters: strips tags so `title=`/`subtitle=` attributes are never scanned; only ≥3-digit integers; allow-lists results.
10. Four incompatible "test Sharpe" numbers for LASSO on one page with no basis stated (−0.41, −0.04, +0.33, −0.264).
11. Scatter reference line overshoots fixed domains; no `clip`.
12. FOMC path title is a label, not a finding.
13. Faceted chart is not mobile-safe (3 × 170 px panels).
14. Midterms hero is a "time series" of 3 points within 7 minutes; x-axis format repeats the day; no election-day marker.
15. "Markets tracked 31,522" overstates (universe scan vs 1,603 order books actually tracked).
16. Data-quality sections do not show check counts; statarb and finllm have none; no freshness test.
17. Pre-registration has no anchor (`docs/EVALUATION_PLAN.md` + hash).
18. Heading order h1 → h3 (ChartCard) → h2.
19. Charts inside `<details>` render at minimum width; no re-render on toggle.

**LOW**

20. Kalshi-aqua rule requires a table view; `fomc/path.ts` card has none.
21. `saas/ranked.ts` `legend: null` while colour encodes two categories.
22. Second chart of each pair suppresses its legend; on mobile the lower chart has none.
23. `fomc/history.ts` x-axis `%b` repeats month names across a year; no decision-date marker.
24. `--series-neutral` has no dark override; `--ink: #ffffff` in dark contradicts "no pure white"; raw `'token:--ink-2'` in `balance.ts`.
25. `DataTable.astro` `role="button"` on `<th>` makes `aria-sort` invalid.
26. noscript text promises a table view that several cards lack.
27. How-i-work overclaims: "Numbers are never typed", "Unit tests on every parser", "lint … checks on the site"; home "the code is public" vs statarb "not public".
28. How-i-work gaps: reviewer subagents, QA loop, CI gates, checkpoints, skills not named; failure log has one entry.
29. IA leftovers: no `/projects` filters; cards lack KPI/sparkline/screenshot; no resume; `/changelog` 404 live.
30. Method sections quote no SQL/code; statarb case study exceeds the word target.

## (c) Three most valuable next improvements

1. Make the pipeline visibly alive: `workflow_run` trigger and a pytest deploy gate.
2. Fix the honesty defects in titles and template every result; harden the guardrail.
3. Real Data-quality tables from facts on all five pages; cards with KPI; `/projects` filters; a How-I-work page that names the reviewer subagents, the QA loop, the CI gates, and genuine failure-log entries.
