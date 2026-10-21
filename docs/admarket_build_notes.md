# ad-market build notes (work log)

Running log for the `ad-market` page build on branch `ad-market` (worktree `~/src/portfolio-ads`).
Port of the local research in `~/Desktop/Claude Code/广告行业研究/` (read-only); nothing pushed.

## Plan

1. `pipelines/admarket/`: config, xbrl + text parsers, ingest (SEC, incremental, `--from-local` seed),
   transform (quarterly panel, volume/price rows), metrics (shares, Q2, Q3, Q4, forecast scoring), schema, publish.
2. Seed `data/raw/admarket/` from the local research download (per-filing revenue-fact extracts and
   volume/price text snippets only; the 493 MB of instances stay local).
3. Tests: parsers, derivation rules, prereg parsing, reconcile against the research numbers, page numbers.
4. Workflow `admarket.yml`, page `ad-market.mdx`, charts, card KPI + mini chart, OG card, README, DATA_MODEL.

## Log

- 2026-10-21: read conventions (CLAUDE.md, scaffold skill, DESIGN, CHART_RULES, DoD, DATA_MODEL) and the
  memcycle precedent; read the research code and outputs. SEC_USER_AGENT is not set on this machine, so the
  seed is built from the local research download and the live ingest path is covered by tests only.
  Revenue-fact extracts for all 286 filings are ~0.14 MB gzipped in total.
- Seeded `data/raw/admarket/` from the research download: 286 filings (revenue facts only) + 40 Meta/Alphabet
  10-Q text-window records, 1.4 MB in all. Snippet parsing equals full-text parsing for all 40 (checked on write).
- Ported `extract.py` as `transform.quarterly`: all 19 series reproduce `ad_revenue_quarterly.json` exactly
  (0 differing cells, same quarter sets). Volume/price rows: 60/60 identical to `volume_price.json`.
- Found in the research parser: the Meta "executive overview" fallback built its volume pattern by slicing a
  character off the results pattern, giving an unbalanced parenthesis. It never ran (every filing matched the
  results wording for volume), so results are unaffected; the port writes the fallback out explicitly.
- `applovin_total` has a negative 2023Q4 (FY2023 restated to continuing operations in a 2025 10-K minus the original
  nine-month figure). The research never used it (the panel uses the advertising segment before 2025Q2); the port
  keeps it in the snapshot, flags it, and does not publish it as a value.
- Second research-parser bug, this one with an effect: Alphabet prints a zero change as "—%". The research cell
  regex skipped it, so for 2022Q2 the cost-per-click column read the six-month figure (+4%) instead of the
  three-month zero. Fixed (dash = 0). Effect: Google Search 2022Q2 price 4 -> 0, implied 16.5 -> 12.0, gap +3.0 ->
  -1.5; mean absolute gap 0.99 -> 0.92. The max gap (3.1, 2021Q2) and the strict verdict are unchanged. The other dash
  cell (2020Q2 Network six-month column) was never read. Transform re-parses the committed snippets, so the raw
  records are not rewritten.
- metrics + publish written; all checks pass. Reproduced: shares 2026Q2 47.8/34.7/11.6/1.1, CAGR 15.4%, TTM +19.9%,
  Q2 gap 21.7 (47.3->30.2 vs 17.0->21.6), Q3 -0.8% vs +18.0% (ex-Reddit 17.8), ex-Network open web +15.7%,
  Q4 max gaps 0.8 / 3.1 / 3.3; rule A recomputed: 90/90 registered numbers and the band match.
- Tests: parsers (XBRL reader, Alphabet one/two-year tables, dash = zero, Meta wording variants incl. the overview
  fallback, snippet equivalence, filing selection with pagination, SEC_USER_AGENT guard), derivation (quarter mapping,
  Q4 = FY - 9M / FY - 3Q, restatements, fewest dims, concept preference, first-reported), metrics (Q2/Q3 verdicts,
  Q4 gaps, rule A, scoring, prereg parsing), reconcile (384/384 registered snapshot cells, all research numbers),
  public safety (no instances, no email addresses), page (no hand-typed digit in prose).
- Workflow `admarket.yml`: weekdays 09:23 UTC, SEC_USER_AGENT secret, ingest + publish with continue-on-error,
  commit-back, redeploy only when data changed. Not triggered.
- Page `ad-market.mdx`, charts `site/src/charts/admarket/`, card KPI + mini chart, OG card (only the new PNG changed).
  Checked in the browser (light/dark, 375 px: no horizontal scroll). Lighthouse 99/100/100/100.

## Definition of Done walk-through (2026-10-21)

- [ ] Pipeline green on Actions for 7 consecutive days: not yet (never run; must not be triggered from this branch).
- [x] pandera schemas (quarterly, video share, volume/price, forecasts) + business-rule tests; freshness check (warn
      after 140 days without a new filing).
- [x] Prose numbers from facts: page test forbids any hand-typed digit in prose (allow-list: Q1-Q6, B1-B7, 10-Q/10-K);
      site-wide guard passes; reconcile test passes.
- [x] Method and Evaluation written. Baselines: Q2 control group, Q3 walled gardens, Q4 reported growth, rule A the
      registered calibration band (60% / 95%).
- [x] Charts: finding titles, metric subtitles, source + as-of stamps, token palette, light/dark checked.
- [x] Lighthouse >= 90 (99/100/100/100), usable on a phone.
- [x] Project row: summary + headline number (Google share) from facts; mini chart from the share mart.
- [x] Sidenotes for definitions; appendix holds Scope, Method, Limits, Evaluation, Data quality, Sources, Build notes,
      Changelog.
- [x] Build notes on the page say what the AI executed and what the author decided.

Open items for the owner: EVALUATION_PLAN.md has no section J (the page cites the prereg files directly); the
live ingest path has never run against SEC from here (no SEC_USER_AGENT locally); Q6 (capex) is not built.
