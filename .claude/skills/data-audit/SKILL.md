---
name: data-audit
description: Audit a pipeline's output tables for schema drift, freshness, ranges, duplicates and anomalies, and write the "Data quality" section for the project page. Use after changing a pipeline, when a scheduled run looks off, or when the owner says "audit the data", "check freshness", "is the pipeline healthy".
---

# data-audit

1. Run `uv run pytest` and the project's pandera schemas on the latest outputs.
2. Freshness: latest `snapshot_ts` vs now; flag > 1.5 × cadence.
3. Row counts per run over the last 14 runs; flag drops > 30%.
4. Duplicates on the declared key; null rates per column vs the previous run.
5. Range checks (probabilities in [0,1], volumes >= 0, dates parseable).
6. Cross-source reconciliation where two sources describe the same thing (e.g. Polymarket vs Kalshi headline markets).
7. Write findings as a table (check, status, detail) into the project's Data quality section and, if anything fails, open a fix rather than editing raw snapshots.
