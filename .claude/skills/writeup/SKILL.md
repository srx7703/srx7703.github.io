---
name: writeup
description: Draft or refresh a project page's Method, Evaluation and Build-notes sections from the pipeline code and data/facts/<slug>.json, with every number templated from facts. Use when the owner says "write up", "draft the method section", "update the narrative", or a project's facts changed.
---

# writeup

1. Read `data/facts/<slug>.json`, the pipeline modules and `docs/DATA_MODEL.md`.
2. Method: question, data sources (with endpoints), cleaning, metric definitions (quote the SQL/Python).
3. Evaluation: metric formulas, baselines, results table, limitations. If results are not available yet (pre-registered plan), say so explicitly.
4. Build notes: what Claude Code executed, what the author decided, tests that gate the page, known gaps.
5. Numbers: never type them. Reference facts keys (`{facts.headline.house_dem.polymarket}`) so the page re-renders when data refreshes.
6. Plain language first, one idea per sentence; no marketing adjectives.
7. A definition the takeaway depends on goes into a `<Sidenote>` right after the phrase it explains (inline, same line). Method, Evaluation, Data quality, Build notes and Changelog live inside `<Appendix>`, after the results.
