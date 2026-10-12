# payments-landscape: handoff for the next session

Written 2026-10-12 at the end of the cloud build session, so a local session (Claude Desktop or CLI) can
continue without that conversation. Read this file, then `STATUS.md`, `EXECUTION.md` and `PLAN.md` in this folder.

## Where things stand

- **Live:** https://srx7703.github.io/projects/payments-landscape/. Merged to main as PR #3 (merge commit, not
  squashed: commit `fc8d488` adding section H of `docs/EVALUATION_PLAN.md` is the pre-registration timestamp).
  PR #4 fixed the first deploy (see "Known traps").
- **Waves W0–W4 are done.** W5 is in progress: the workflow is installed and the page status is still `in-progress`.
- **Weekly workflow:** `.github/workflows/payments.yml` (Wednesdays 07:41 UTC plus manual dispatch). The owner
  installed it by hand as a compact single-step script. It runs every source, publishes, commits `data/`, then
  starts `deploy-site.yml` itself, because deploy-site's `workflow_run` list does not name `payments`. It exits 1
  if any source failed, and prints `failed: <names>`.

## Open work, in order

1. **Fix the two failing sources.** Both runs so far (manual run 37395354997 on 2026-10-06, scheduled run
   37639269048 on 2026-10-07) ended `failed: sec devstats`. webtech, jobs, formd, reference and publish succeeded,
   so data was committed and the site redeployed.
   - **`sec`:** `pipelines.sec.ingest --universe payments` wrote snapshots for 18 tickers into
     `data/snapshots/sec_payments/`. The failure is in that step or in `pipelines.sec.transform_payments`. Read the
     run log and reproduce locally against the committed snapshots. Pinned CIKs and tickers for KLAR, NAVN and WSE,
     and the FISV ticker change, were never verified against EDGAR, because the cloud container could not reach
     sec.gov.
   - **`devstats`:** the npm/PyPI client was written and tested only against offline fixtures. Check the real
     api.npmjs.org range responses (scoped package URLs, 18-month chunking) and pypistats.
   - **Done when** a manual dispatch of `payments.yml` ends green. Then set the `ci_sources` flags in facts and
     check that the adoption dashboard shows data instead of reason text.
2. **Tidy up.**
   - Delete `docs/payments/ci/` (a parked copy of an older workflow version).
   - Update the W5 row in `STATUS.md`.
3. **After 7 consecutive green weekly runs:**
   - set the page frontmatter status to `live`;
   - add a `payments-landscape` FEEDS entry in `site/src/lib/freshness.ts` with keys `['generated_at']`. The test
     mapping in `tests/test_home_freshness.py` already exists, and the test forbids a FEEDS entry while the page is
     not live.

## Decisions already made (do not reopen without the owner)

- All PLAN §8 defaults apply:
  - no market prices or Yahoo data;
  - reported or third-party numbers are never charted;
  - hiring is published as counts only;
  - no Google Trends or Google Cloud;
  - the SEC contact comes only from the `SEC_USER_AGENT` Actions secret;
  - never use the owner's personal email.
- **Q1** stays as registered and reads `undecidable` (Airwallex and Checkout.com have no calendar-year volume;
  PayPal has no merchant split). Section H amendments are the owner's, made in the Chinese file first.
- **Baselines** are the "already seen at registration" readings. Q5 and Q6 have none.
- **The Q6 and Stripe take-rate bands** are labelled assumptions in `pipelines/payments/ledger.py` and are never graded.
- **EUR/USD 2025** is the Fed G.5A annual average (FRED AEXUSEU, 1.1306), stored in `denominators.json`.
- **The owner's standing rules for sessions:**
  - reply in Chinese;
  - do not keep asking for confirmation;
  - still stop for a merge or deploy to main, a paid source or a rights doubt, and any change to another
    project's published numbers.

## Known traps

- **Lockfile toolchain.** `site/package-lock.json` was written on macOS, and CI installs the lockfile toolchain
  (astro 7.3.2, `@astrojs/language-server` 2.16.16). A newer local toolchain can pass `astro check` while CI fails:
  PR #4 needed `Astro.props as Props` in `DashboardControls.astro`. Before pushing site changes, run `astro check`
  on the lockfile versions. On Linux that also means adding the native bindings with `npm install --no-save`
  (`@rolldown/binding-linux-x64-gnu`, `@bruits/satteri-linux-x64-gnu`, `@astrojs/compiler-binding-linux-x64-gnu`).
  Never commit a lockfile change made just for that.
- **Stray write.** `pipelines/valuation/tests/test_site_pages.py` rewrites `data/marts/valuation/evaluation.json`.
  Run `git checkout` on that file after the full test suite.
- **Prebuild data.** The page's large chart datasets are written to `site/public/data/payments/` at prebuild by
  `site/scripts/payments-data.mjs`, and the DAG SVG by `site/scripts/payments-dag.mjs`. Both run from
  `npm run build`.
- **Number guards.** `tests/test_payments_page_numbers.py` is strict: every numeral and number word in the MDX
  prose must come from `data/facts/payments.json`.
- **Golden test.** `pipelines/sec/tests/test_universe_isolation.py` keeps the SaaS benchmark marts byte-identical.
  Any diff there is a stop-and-ask (C4).
- **Workflow files.** A cloud-session token cannot push `.github/workflows/*`. A local session with the owner's
  git credentials can.

## Map

- **Pipeline:** `pipelines/payments/`
  - `config.py`: pinned constants, mirrored in section H; `tests/test_plan.py` keeps them in sync.
  - `reference.py`: the curation validator.
  - Source clients: `webtech.py`, `devstats.py`, `jobs.py`, `formd.py`.
  - Analysis: `economics.py`, `ledger.py`, `share.py`.
  - Assembly: `schema.py`, `evaluate.py`, `publish.py`.
- **SEC universe split:** `pipelines/sec/` (`config.UNIVERSES`, `transform_payments.py`).
- **Curated inputs:** `data/reference/payments/*.json`. Only rows tagged V or C are chartable.
- **Outputs:** `data/marts/payments/` and `data/facts/payments.json`.
- **Page:** `site/src/content/projects/payments-landscape.mdx`.
  - Specs: `site/src/charts/payments/`.
  - Components: `site/src/components/DashboardControls.astro`, `UrlTable.astro`.
