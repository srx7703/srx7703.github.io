# payments-landscape — execution plan for an autonomous (multi-agent) build

This is the runbook an orchestrating Claude session follows end to end. The research design is in
`支付赛道_研究方案_20261002.md` (Chinese, same folder; it goes into the repo as `docs/payments/PLAN.md`).
Reply to the owner in Chinese; code, docs and the page are in English.

## 0. Authority

**Defaults adopted from PLAN §8**, unless the owner overrides them:
1. Slug `payments-landscape`; working title "Who keeps what: what new payments companies sell, move and keep".
2. v0 scope by segment:
   - A (acquiring) and B (BNPL): full share.
   - C (corporate cards) and D (cross-border): ledgers, pool rankings and private intervals.
   - E and F: classification table only.
3. No market prices or market caps in v0. No new Yahoo data.
4. Reported or third-party numbers go in a "reported, unconfirmed" column and are never charted.
5. No market-wide denominator for commercial cards; pool ranking only.
6. Hiring is published as counts only. App Store data is not used.
7. The SEC contact comes only from `SEC_USER_AGENT` (an env var or Actions secret), never from the owner's personal address.
8. Publication-policy check: the owner does it before the page is made public. It does not block the build.
9. Section H of the pre-registration is committed before any formal data pull (target 2026-10-05).
10. No Google Trends applications and no Google Cloud.

**The orchestrator may do without asking:**
- create the worktree and branch;
- write code, data and docs on the branch;
- run tests and builds;
- fetch public keyless data (SEC with `SEC_USER_AGENT`, HTTP Archive, npm, pypistats, Greenhouse/Ashby, FRED, Fed/FSB pages);
- spawn agents;
- commit to the branch.

**Owner checkpoints (stop and ask):**
- **C1** — before the first push of the branch, which makes the H-section timestamp public;
- **C2** — before marking the PR ready for review;
- **C3** — before any merge to `main` or deploy;
- **C4** — any credential, any paid source, any rights doubt, or any change that alters another project's published numbers (for example the SaaS benchmark).

## 1. Ground rules for every agent

- **Repo rules.** `CLAUDE.md` and `docs/` are binding:
  - prose numbers come only from `data/facts/payments.json`;
  - pandera validates before every write;
  - CHART_RULES and DESIGN apply (no cards or brand colours; exactly three KPIs);
  - conventional commits.
- **Verification tags:**
  - `[V]` — read on the primary page today;
  - `[S]` — seen only in a search extract;
  - `[C]` — computed from `[V]` inputs;
  - `[U]` — unverified.

  Only `[V]` and `[C]` reach facts. Dates come from the page text.
- **Data rights:**
  - Never commit vendor or paid data (Yahoo, Similarweb, Crunchbase, PitchBook, Nilson, Statista, paywalled articles).
  - Never commit job-description text, only counts.
  - Never use the owner's personal email anywhere.
- **File ownership.** Each agent writes only the files listed for its task. Agents never run `git` write commands; the orchestrator commits after each wave.
- **iCloud.** The Desktop syncs to iCloud, so never write the same file from two processes. If a `" 2"` conflict copy appears, delete it and report it.
- **Known traps:**
  - npm `affirm`, PyPI `affirm` and `airwallex`, and the Greenhouse token `wise` are not the companies.
  - Shopify-hosted processing is invisible to web detection.
  - HTTP Archive barely detects Adyen or Square.
  - SEC `companyfacts` lags, so fall back to `frames`.
  - Klarna is a 20-F filer using IFRS; Wise is a 20-F filer using US GAAP.
  - `pipelines/sec/transform.build()` reads every facts parquet, so isolate the universe.
  - `pipelines/valuation/tests/test_site_pages.py` rewrites `data/marts/valuation/evaluation.json`. Restore that file before every commit.

## 2. Waves

Each wave has a single acceptance gate. The orchestrator runs it and fixes failures (up to 3 rounds) before starting the next wave. After every wave it updates `docs/payments/STATUS.md` and sends the owner a 3-line Chinese update.

### W0 Setup and pre-registration (orchestrator; serial; about 0.5 day)

1. `git worktree add ~/Desktop/portfolio-payments -b payments-landscape origin/main`, then `uv sync`.
2. Run the `portfolio-project-scaffold` skill (slug `payments-landscape`, pipeline `payments`, facts `payments.json`, status `in-progress`).
3. Copy in the plan and this runbook as `docs/payments/PLAN.md` and `EXECUTION.md`.
4. Write section H of `docs/EVALUATION_PLAN.md`:
   - Q1–Q9 exactly as in PLAN §2, with today's "already seen" readings disclosed;
   - the pinned npm package whitelist, ATS board tokens and company universe;
   - the closed `metric_kind` vocabulary.

   Add `pipelines/payments/tests/test_plan.py`, which checks that every constant in `config.py` appears in section H.
5. Commit with `docs(payments): ...`. **C1**: ask before pushing.

**Gate:** `uv run pytest` and `uv run ruff check .` pass; the scaffold builds (`cd site && npm run build`).

### W1 Reference data (parallel; 6 curators, each followed by 1 adversarial verifier; about 1.5 days)

Each curator owns exactly one file under `data/reference/payments/`. Every row validates against `common/curation.py` and carries:
- URL;
- publish date and as-of date;
- definition;
- `metric_kind`;
- `company_confirmed`;
- `last_checked`;
- the tag.

| task | file | content |
|---|---|---|
| W1a | `kpi_disclosures_acceptance.json` | Volume (TPV/GPV) and net revenue by quarter for 8 quarters, from 8-K Ex.99.1 or IR pages: PayPal, Block/Square, Toast, Fiserv Clover, Global Payments, Shift4, Shopify Payments, Adyen (half-years) |
| W1b | `kpi_disclosures_other.json` | Affirm, Klarna, Sezzle, Zip; BILL, Navan, Corpay; Wise, Payoneer, dLocal, Flywire; Marqeta; Circle (USDC float) |
| W1c | `private_metrics.json` | Stripe, Ramp, Brex (including the Capital One purchase-price allocation), Airwallex, Checkout.com, Mercury, Rapyd, Nium. Every number has a qualifier (=, >, <, ~) and a status field |
| W1d | `waterfall_inputs.json` | Visa interchange sheet (2026-04-18), Stripe list pricing, Affirm FY26 per-$100 lines, BILL interchange and rewards, FSB cost KPIs, Wise take rate |
| W1e | `products.json` | Company × 9 product lines: status, launch date, source. Covers about 25 core companies |
| W1f | `events.json` + `denominators.json` | 2025-26 IPOs, M&A and priced rounds. Denominators: Fed FEDS BNPL table, Census and FRED e-commerce, Fed Payments Study, FSB |

Each verifier re-opens a random 30%, plus every row that feeds the KPI row or a chart title. It returns per-row verdicts; failed rows are fixed or dropped.

**Gate:** every row validates; no `[S]` or `[U]` row is marked chartable.

### W2 Pipeline (parallel on disjoint modules, then integration; about 3 days)

| task | owns | done when |
|---|---|---|
| W2a SEC adaptation | `pipelines/sec/*` | universe isolation; accepts 20-F and `ifrs-full`; `frames` fallback with a recorded check. **Golden test: the SaaS marts and facts are byte-identical before and after** (any diff stops the build, see C4) |
| W2b economics + ledger | `pipelines/payments/economics.py`, `ledger.py` | per-$100 waterfalls and the sensitivity matrix; qualifier propagation; golden tests against Affirm 10-K, BILL 8-K, Visa sheet |
| W2c share | `pipelines/payments/share.py` | four lenses (disclosed pool, official denominator, web coverage, developer downloads); never sums across layers; pool HHI; BNPL from the FEDS table |
| W2d webtech | `pipelines/payments/webtech.py` | HTTP Archive Tech Report client (monthly from 2020-01; US vs all; top-10k/100k/all; mobile/desktop) and the detection-coverage table |
| W2e signals | `devstats.py`, `jobs.py`, `formd.py` | npm and pypistats with the pinned whitelist (server and client); Greenhouse/Ashby counts with a function classifier; SEC Form D monitor; each source fails independently |
| W2f integration (after a–e) | `schema.py`, `evaluate.py`, `publish.py` | pandera schemas; Q1–Q9 status; marts and `data/facts/payments.json`; regression guard (a run that loses more than half the companies does not overwrite); `facts.checks` |

**Gate:**
- all tests pass, including golden tests, the unchanged-SaaS check and `test_plan`;
- `make publish` is idempotent;
- the facts include `generated_at` and every value the page needs.

### W3 Site (parallel after W2f; about 3 days)

| task | owns | done when |
|---|---|---|
| W3a dashboard controls | `site/src/components/DashboardControls.astro`, a small hook in `VegaChart.astro` | a filter row drives `view.signal` on every chart in its group and filters the linked DataTable; state lives in the URL query; keyboard-accessible; wraps at 360 px; no-JS fallback text. **Existing pages render unchanged** (build plus a screenshot check of 2 existing pages) |
| W3b specs D1, D2 | `site/src/charts/payments/company_*.ts`, `share_*.ts` | Company explorer (metric time series with peers greyed; volume × revenue log-log with iso-take-rate lines). Share lens (bars by lens; dumbbell across lenses). Unsupported combinations show a reason, not an empty chart |
| W3c specs D3, D4 + static figures | `adoption_*.ts`, `ledger_*.ts`, `waterfall.ts`, `products.ts`, `events.ts`, `sensitivity.ts` | Adoption tracker (web coverage, npm index, hiring by function). Private ledger (interval chart, event timeline). Per-$100 waterfalls. Product-convergence dot matrix. Sensitivity dots |
| W3d page | `site/src/content/projects/payments-landscape.mdx`, `site/scripts/` DAG generator | takeaway; 3 KPIs; the Q1–Q9 scoreboard table; the 4 dashboards; static figures; "How the data is built" (code-generated DAG SVG, per-source processing, filterable metric dictionary, `DataQuality`, reproducibility, limits); Appendix; disclaimer before the first figure; video-free |
| W3e registries | `cardKpis.ts`, `minicharts.ts`, Makefile, README, `docs/DATA_MODEL.md` | the home row shows a headline number read from facts |

**Gate:**
- `npm run build` passes (`astro check` clean);
- `tests/test_site_numbers.py` plus a page-specific number guard pass;
- every figure has a finding title, a metric subtitle and a SourceStamp.

### W4 Review loop (parallel reviewers; up to 3 rounds; about 1.5 days)

Reviewers:
- the `chart-review` skill on every spec;
- the `data-audit` skill (it writes the Data quality section);
- the `writeup` skill (Method, Evaluation, Build notes, with numbers templated);
- an adversarial fact checker that re-opens every curated number visible on the page;
- a rights checker that verifies no vendor data, job text or personal data is committed;
- a UX checker: preview screenshots at 360 / 768 / 1280 px in light and dark, dashboard interactions, URL state, Lighthouse (performance and accessibility ≥ 90).

The orchestrator fixes all blocking findings, then re-runs only the reviewers that failed.

**Gate:** zero blocking findings, and the DoD checklist walked with each item marked done or N/A with a reason.

### W5 Ship preparation (about 0.5 day, then 7 calendar days)

1. Add `.github/workflows/payments.yml` copied from the guarded `power.yml` pattern (weekly), the `freshness.ts` entry and the `test_home_freshness` mapping.
2. **C1** (if the branch is not pushed yet), then open a **draft** PR with the DoD checklist and the list of defaults to confirm.
3. **C2**: owner review.
4. **C3**: merge (no squash if section H's dates matter; rebase with `--committer-date-is-author-date`). The workflow then needs 7 green days before the status becomes `live`.

## 3. Budget and shape

- About 25–35 agent runs in total. The largest fan-out is W1: 12 agents (6 curators and 6 verifiers).
- Wall-clock is about 2–3 working days if the session runs uninterrupted, about 10–15 agent-days of work.
- The orchestrator keeps its own context small: agents return structured summaries and write files; the orchestrator reads diffs and test output, not raw pages.

## 4. Stop conditions

Stop and report to the owner if any of these happens:
- a test would require changing another project's published numbers;
- a source needs a key, an account or payment;
- a number central to a KPI or chart title cannot reach `[V]`;
- the build breaks existing pages and the cause is not understood after 2 rounds;
- any need to push to `main`, rewrite git history, or use personal data.
