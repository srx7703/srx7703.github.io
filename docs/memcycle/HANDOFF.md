# Handoff: build the memory-cycles page (cloud session)

Branch `memory-cycles`. Read `CLAUDE.md`, then `docs/memcycle/PLAN.md` (the design and the owner decisions),
then this file. Reply to the owner in Chinese; code, docs and the page are in English.

## Already on the branch

- **Pre-registration.** `docs/prereg/memory-cycles/预注册_周期定时与检验规则_20260928.md` came in with its 5 original commits, dated 2026-09-28 13:11 to 18:02 −07:00. It is append-only. Never edit it here; amendments are added by the owner locally and then imported.
- **ECOS raw.** `data/raw/memcycle/ecos/2026-09-28/1316/*.json.gz` holds the raw ECOS responses from the registration pull: 402Y016, DRAM and flash, bases C, D and W, 1971-01 to 2026-08, with 2026-08 still preliminary.
- **Frozen results.** `data/case_studies/memcycle/frozen/company_cycles_derived.csv` has the stock-side results, one row per company per price cycle, with months, leads, ratios and flags but no closes. Alongside it are `inputs_manifest.json` and `q3_versions.json`.
- **Golden outputs.** `pipelines/memcycle/tests/fixtures/golden/` has the research outputs to reproduce: `price_turns.csv`, `price_phases_main.csv`, `summary.json`, `company_cycles_derived.csv` and the normalised `ecos_402Y016.csv`.
- **Research code.** `pipelines/memcycle/research_ref/` holds the code that produced them (`bb.py`, `phase1.py`, `prepare_inputs.py`, `phase1_tables.py`) and `freeze_v0.py`, the local export that wrote the files above. This is reference code: port it, don't import it.

## What you cannot do here, and must not try

- **Rebuilding the stock side.** Stock and benchmark closes are on the owner's Mac only. Vendor terms forbid redistribution and this repo is public. Do not fetch stock prices, commit price series, rebased paths or benchmark levels, and do not work around bot checks (Stooq, Cloudflare).
- **Local-tier tests.** Tests that need local inputs must be `skipif(not MEMCYCLE_INPUTS)`. `freeze.py` is written here but first run by the owner locally.
- **Pushing and merging.** Do not push to `main`, merge, deploy, enable auto-merge or squash. Open a **draft PR** from `memory-cycles` and stop. The owner reviews and gives the go-ahead. Keep history linear, so later syncs with main use `git rebase --committer-date-is-author-date`.
- **Video material.** No video transcript, claim table or quotes. The credit is one sentence and a link: 美投讲美股, "存储还能涨吗？历史上三轮暴涨…", 2026-09-26, https://www.youtube.com/watch?v=viQjQ3mgeOc

## Order of work

Follow `PLAN.md` steps 3–9:

1. **Section G** of `docs/EVALUATION_PLAN.md` plus `test_plan.py`.
2. **The package:** `config`, `ingest`, `transform`, `metrics`, `evaluate`, `schema`, `freeze` (local), `publish`, with tests. Golden reproduction must pass before anything else is built on it.
3. **Marts and facts** via `publish.py`, and the flips-to-inconclusive diagnostic.
4. **Charts, page and registries.**
5. **Checks.**
   - Run the `data-audit`, `chart-review` and `writeup` skills.
   - Build the site and preview it at 360 px in light and dark.
   - Run Lighthouse (`docs/LIGHTHOUSE.md`) and walk the Definition of Done. Mark "7 days green" N/A because the project is frozen, and give the reason.
6. **Commit and open the PR.** Use conventional prefixes: `feat(memcycle):`, `data(memcycle):`, `site:`, `docs(memcycle):`. Open a draft PR with the DoD checklist and the list of owner decisions to confirm.

## Findings to write up (numbers must come from facts, never typed)

**Calendar.** The registered rule is simplified Bry–Boschan on the log level of ECOS C. DRAM has 6 complete cycles (trough, peak, trough) since 2003, plus a "cycle 0" peak at 1995-07 and the current upswing from the 2023-08 trough. That upswing is +471% to 2026-08. 2026-08 is the end of the series, not a turn: contract prices are still rising. Caveats that must be on the page:
- The rule dates one 95-month decline from 1995-07 to 2003-06. It removed three real, under-6-month contract rallies: 1999-06→11 (+77%), 2000-03→08 (+52%) and 2001-10→2002-03 (+249%).
- Whether 1999–2000 counts as a cycle hinges on a 0.5% gap between two index values.
- The rule trough 2003-06 is not the low. The index low is 2001-10, 42% lower, and the contract trough was Nov 2001.
- P1995-07 exists only because the series is started in 1995-01.
- The DRAM 2006 and NAND 2007 rises are exactly 6 months, right at the minimum.
- From 2017 the index moves in quarterly steps, so turns are uncertain by 1–3 months.
- Flash before about 2006 is mostly NOR.
- Level dating on a series that falls 7–40% a year understates upswings. The detrended views are post-hoc and are not shown in v1.
- Every dated turn matches independent contract-price history within 2 quarters. Spot prices peaked about 2–6 months earlier than ECOS (2010, 2018, 2021).

**Q3, registered: falsified.** 33 pure-play company-cycles; 42.4% led by a month or more; median lead −2 months.
- DRAM makers only: 30 pairs / 43.3% / −1.
- Diversified: 19 / 36.8% / −5.
- NAND panel against the flash calendar: 20 / 15% / −8.

Read it as: stocks peaked around the same month as contract prices, not quarters ahead.
- The verdict holds in all four research versions (`q3_versions.json`) and was independently recomputed.
- It is fragile: 3 more leading pairs would make it "inconclusive". Four pairs flip on a 0.6–0.7% gap in one month-end close, and all those closes were checked against two or three sources.
- Post-hoc observations (robustness section only, and only if the owner opts in):
  - After 2013, 9 of 13 pairs led (median +3); before 2013, 5 of 20.
  - Moving the rule trough to the true 2001-10 low gives about 51%, which is inconclusive.
  - Half of the negative leads are "bleed": a stock high made during the next rally is booked to the prior cycle.

**Multiples** (descriptive).
- The basis matters. Micron 1993–95 is about 26x on daily closes (the video's figure) but about 18x on month-end closes. The current Micron up-leg is 23.1x month-end and 25.9x intramonth.
- Kioxia (54.7x) and the new SanDisk (70.8x) are measured from their listing months. Flag them and leave them out of any comparison.
- The registered window starts at the previous price peak. For the 2004 cycle that means a start in 1995, so those rows span the 2000 bubble. Disclose this.

**Data quality.**
- Every extreme value was checked against a second source, with a maximum deviation of 0.01%. Official anchors were the Micron 10-K ranges, TWSE and KRX.
- Hynix's 2003 capital reduction is adjusted 21:1; KRX uses a factor of 19.63.
- Korean closes after 2026-09-14 are official regular-session closes (Yahoo), not the after-market prices that Naver and Daum show.
- Elpida has a full 89-month series (kabudragon, cross-checked). Qimonda is annotation only. Toshiba is registered but has no data.

## Afterwards (owner's Mac, not the cloud)

- Run `freeze.py` against `raw_local` and pass the local-tier test.
- Update after Micron's FQ4 results (2026-09-30) and the next ECOS release (mid-October; 2026-08 turns final).
- Phase 3 (Q6) needs OpenDART for Samsung and SK hynix financials. The key is in the owner's local `.env`. Never commit it.
