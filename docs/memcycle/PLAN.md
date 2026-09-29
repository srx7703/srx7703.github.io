# Memory-chip price cycles — project plan (v1)

Owner: Song Ruoxuan. Drafted 2026-09-28 from a five-agent read of this repo; defaults below are the
recommended options and are confirmed or changed by the owner at PR review.

## Question and scope

**Question.** Since 1995, how many DRAM (and flash) contract-price cycles have there been, and do memory
stocks peak before prices do?

- **Price series.** Bank of Korea ECOS table 402Y016 (export price index by item): DRAM `30911201AA` from 1995-01, flash `30911202AA` from 2000-01.
  - The primary basis is contract currency (C). USD (D) and KRW (W) are reported as sensitivity checks only.
  - The index is a contract-price series for Korean exporters. Before about 2006 the flash item is mostly NOR.
- **Companies.** The pre-registered roster:
  - Pure-play group, which enters the verdict: Micron, SK hynix (with its Hyundai Electronics and Hynix history), Nanya, Winbond, Powerchip (PSC and PSMC), Elpida, Qimonda, Inotera, SanDisk (old and new), Kioxia.
  - Diversified group, shown only: Samsung, Infineon up to 2006-04, WDC, Seagate. Toshiba is registered but has no data.
- **Inspiration.** A YouTube video by 美投讲美股 (2026-09-26) on past memory upcycles. The page credits it in one sentence with a link. There is no transcript, no claim table and no tally.

## Pre-registration

The Chinese original is the authoritative text: `docs/prereg/memory-cycles/预注册_周期定时与检验规则_20260928.md`.

- **Import.** It was imported with its 5 local commits. Author and committer dates are preserved, and the blob ids equal those in the owner's private research repo: 9f90587a, 4b4a9e94, 77fdbb45, e4a15ecd, bedd0de4.
- **Dates prove little.** The dates are self-reported by a private repo. They become publicly checkable only from the first push of this branch.
- **Blindness.** §0 of the file lists what had already been seen. Implementation notes 1–3 are not blind (see revision 3).
  - Q3 was computed after local registration. Its roster was then corrected to match the registered list after the results had been seen. `data/case_studies/memcycle/q3_versions.json` records the verdict at every research commit; it is "falsified" in all four versions.
  - Q7 is not blind, because the price data had been seen. Only Q6 and Q8 are genuinely forward.
- **Merging.** The branch must reach `main` by fast-forward or `git rebase --committer-date-is-author-date`. Never squash it.

## Evaluation items (English section G in `docs/EVALUATION_PLAN.md`)

| item | status | registered test |
|---|---|---|
| Price-cycle calendar | resolved (descriptive) | simplified Bry–Boschan on log levels: 6-month half-window, phases of at least 6 months, cycles of at least 15 months, amplitude 20% (30% and 0% as sensitivity) |
| Q3: stocks peak 1–3 quarters before prices | resolved: **falsified** | pure-play company-cycles. Holds if at least 60% lead by one month or more and the median lead is 3–9 months; falsified if under 50% lead by one month or more; otherwise inconclusive |
| Up-leg multiples | descriptive | month-end closes from the lowest close after the previous price peak up to the stock peak. Auxiliary: intramonth low to high. Excess over the local benchmark |
| Q6: capex/revenue warning threshold | not yet | percentile grid 50..90 in steps of 5, chosen by Youden's J on data to 2015Q4; falsified if out-of-sample precision is no more than base + 10 pp, or recall is below 50% |
| Q7: smaller swings after consolidation | not yet (not blind) | three measures (stock drawdown, industry revenue, margin), split at 2013 |
| Q8: next downturn shallower | not yet (forward, deadline 2028-12-31) | next peak confirmed by the prereg §6 rule (6 months below the peak and −20%); first-year index fall and worst revenue QoQ compared with their historical medians |

## Data layers (the repo is public)

| layer | what | in git? |
|---|---|---|
| `data/raw/memcycle/ecos/2026-09-28/1316/` | ECOS raw API responses (json.gz), append-only | yes: BOK terms allow reuse with attribution ("Source: Bank of Korea ECOS") |
| `data/snapshots/memcycle/` | normalised ECOS rows parsed from raw | yes |
| `data/case_studies/memcycle/frozen/` | derived stock-side results only: months, lead months, ratios, flags. No closes, no index levels, no rebased paths | yes |
| `data/case_studies/memcycle/inputs_manifest.json` | sha256 and coverage of every local input | yes |
| owner's local `raw_local/` | stock closes (Yahoo, KRX via Naver/Daum, TWSE/FinMind, kabudragon, archives), benchmark indices, FRED FX | **no**: vendor terms forbid redistribution |
| `data/marts/memcycle/*.json` | price_index (ECOS C only), turns, calendar, leads, multiples, evaluation | yes (public via sync-data) |
| `data/facts/memcycle.json` | every number used in prose | yes |

## Pipeline (`pipelines/memcycle/`, names from the scaffold skill)

| module | what it does |
|---|---|
| `config.py` | roster, registered constants, sources |
| `ingest.py` | ECOS raw → snapshot. No network fetch in v1 |
| `transform.py` | turning-point rule, ported from `research_ref/bb.py` in plain Python |
| `metrics.py` | windows, leads, multiples |
| `evaluate.py` | Q3 verdict; Q6/Q7/Q8 return `not_yet` with the reason |
| `schema.py` | pandera schemas |
| `freeze.py` | **local only**; replaces `research_ref/freeze_v0.py` and applies the input fixes as transforms, never by rewriting raw |
| `publish.py` | CI-safe. Recomputes the calendar from ECOS, reads the frozen tables, validates, writes marts and facts; includes the regression guard |

v1 has no scheduled workflow, like statarb and finllm, which are frozen case studies. Status is `in-progress`. v2, which adds a monthly ECOS monitor and flips the page to `live`, waits until data could confirm the 2026-08 peak, at the earliest the 2027-02 print.

## Page (`site/src/content/projects/memory-cycles.mdx`, English, surgical-robots skeleton)

Sections and components:

- Takeaway with Sidenotes explaining the contract-price index, basis C and "lead".
- The line "Public data only; nothing here is investment advice." placed before the first figure.
- A `KpiRow` with exactly three tiles:
  - complete DRAM cycles since 1995;
  - the Q3 verdict with its share;
  - the current rise since the 2023-08 trough.

Figures (all titles templated from facts):

1. **Price-cycle calendar.** DRAM and flash in two stacked panels, log y. The DRAM index spans more than three orders of magnitude, which justifies the log scale (CHART_RULES 13).
   - Rising phases are shaded; the latest peak is drawn hollow because it is unconfirmed.
   - The short rallies the rule deleted (1999, 2000, 2001–02) are made visible, and flash before 2006 is greyed.
2. **Up-leg comparison.** Months against percentage rise for each rising phase; the current phase is hollow.
3. **Lead strip.** Stock-peak month minus price-peak month for each company and cycle.
   - The registered 3–9-month band is shaded; pure plays are filled and the diversified group is hollow (CHART_RULES 14).

Tables (`DataTable` plus `DownloadCsv`, derived values only):

- the Q3 groups;
- the calendar and its sensitivity;
- leads;
- up-leg multiples (USD, auxiliary basis and excess), with listing-truncated rows excluded and the reason given in words.

Robustness section (`id="robustness"`), headed "not pre-registered":

- The only content by default is the number of company-cycles that would have to flip to reach "inconclusive", computed in code.

Appendix sections: Scope, Method, Limits, Evaluation (`evaluationPlan()`), Data quality, Sources, Build notes, Changelog.

Registries:

- `site/src/lib/cardKpis.ts` and `minicharts.ts` (DRAM log-index line);
- a Makefile target;
- `README.md`;
- `docs/DATA_MODEL.md`.

Leave `freshness.ts`, `test_home_freshness.py` and `deploy-site.yml` unchanged in v1.

## Tests

- **`test_parsers.py`.** ECOS parsing: counts, no duplicates or gaps, positive values, units, preliminary last month, rejects HTML.
- **`test_turning.py`.** Golden reproduction of `tests/fixtures/golden/price_turns.csv` (152 turns, 10 runs) and `price_phases_main.csv` (28 rows). It also covers these rule cases:
  - the start-month dependence of P1995-07;
  - ties and plateaus;
  - steps 3 and 4 applied sequentially;
  - an exact ±20% move passing;
  - the wrong-sign case at amplitude 0%;
  - the knife edge at 2000-08;
  - a seeded random-walk property test.
- **`test_metrics.py`.** Verdict boundaries (60% / 50% / median 3 and 9) and completeness when a company lists or delists inside a window (PSC 2010). Also:
  - the Infineon cutoff, ProMOS excluded, WDC out of the NAND panel, Toshiba shown as "registered, no data";
  - ties go to the earliest month;
  - the listing-truncation flag;
  - `partial_latest_month` only for the in-progress month.
- **`test_reconcile.py`.**
  - CI tier: marts and frozen tables equal the golden fixtures, and the Q3 groups are 33/0.424/−2, 30/0.433/−1, 19/0.368/−5 and 20/0.15/−8.
  - Local tier (skipped without `MEMCYCLE_INPUTS`): `freeze.py` reproduces the frozen tables byte for byte and the manifest hashes match.
- **`test_plan.py`.** Every constant in `config.py` appears in section G. Section G names the five blobs and makes no "blind" claim for Q3 or Q7. The vendored file's `git hash-object` equals the last listed blob.
- **`test_page.py`.**
  - Prose and string attributes contain no hand-typed YYYY-MM, month counts, multiples or signed leads. The global number guard misses these, so this page needs its own check.
  - Exactly three KPI tiles; the disclaimer appears before the first figure.
  - Post-hoc numbers appear only inside `#robustness`.
- **`test_public_safety.py`.** Every column in the memcycle marts and frozen tables is on an allow-list. Nothing matching close/open/high/low/price/level is allowed except in the ECOS `price_index` mart.

Existing suites, `ruff` and `npm run build` must stay green.

## Owner decisions (defaults applied; confirm at PR review)

1. v1 status is `in-progress`, frozen at the registration release, with no workflow.
2. The stock side publishes only months, leads, ratios and flags: no price paths and no benchmark series.
3. The pre-registration is imported with its dates, and the branch is never squashed.
4. The robustness section shows only the flips-to-inconclusive count. Whether to add the post-2013 split, which was chosen after the results were seen, is decided at review.
5. The video is credited in one sentence with a link.
6. The current-cycle market commentary (9/28 drawdowns, spot vs contract prices, TrendForce outlook) is left out of v1.
7. The owner's verification archive stays local (`raw_local/archive`).

## Effort

About 18–24 agent-hours for v1:

| work | hours |
|---|---|
| section G + `test_plan` | 2–3 |
| port + schemas + golden tests | 6–8 |
| facts, marts and diagnostic | 1–2 |
| charts + page + registries | 5–7 |
| review skills, 360 px light/dark preview, Lighthouse, DoD | 3–4 |
