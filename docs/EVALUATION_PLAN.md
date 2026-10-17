# Pre-registered evaluation plans

Committed before results are known. Each project page prints the git blob hash of this file so the
scoring rules can be checked against history. Changes after a project's first resolution must be
logged in the Changelog of that page with the reason.

## 2026 US midterms (project A) — scored after November 3, 2026

- Accuracy: Brier score and log loss of each platform's final pre-election price (last snapshot
  before 05:00 UTC on November 3, 2026) for every resolved tier-1 market, overall and by market type
  (chamber control, seat count, state race).
- Calibration: reliability diagram in 10 equal-width probability bins, plus sharpness.
- Lead time: days before election day when a market last crossed to the side that resolved YES and
  stayed there.
- Cross-platform: distribution of the Polymarket–Kalshi gap over time; whether it narrows in the
  final week.
- Baseline: Cook Political Report ratings mapped to fixed probabilities (Solid 0.95, Likely 0.85,
  Lean 0.70, Toss-up 0.50), scored the same way on the same markets.
- Resolutions come from Kalshi settlement results, cross-checked against Polymarket closed prices.

## FOMC decision markets (project B) — scored per meeting

- Per meeting: multi-outcome Brier score (sum over the five buckets of squared error, 0 to 2) of
  each platform's final pre-decision distribution, taken from the last snapshot before 17:30 UTC on
  decision day (fallback: last daily history point before decision day), plus the probability
  assigned to the realised outcome.
- Lead time: days before the decision when the realised outcome first became and stayed the modal
  outcome on each platform.
- Baselines: "no change" every meeting, and "same as last meeting", scored the same way.
- Cross-platform: daily gap in P(hike); whether it closes into the meeting.
- Bucket probabilities are used as quoted (not normalised); their sum is reported alongside.

### FOMC amendment, 2026-09-23 — macro overlay and release event study

Added with the page's macro overlay (policy rate, 2-year yield, inflation compensation, oil, jobless
claims) and the release event study, before any of the data it scores exists.

**What is and is not pre-registered.** Everything from January 2025 to September 23, 2026 was looked at
while the overlay and the event study were designed; a preview on 2026-09-22 found that dating prices by
their source label instead of the New York time they were observed flipped the sign of a correlation.
Statistics on that window are descriptive and the page labels them so. Only the four claims below are
pre-registered, only on data from **September 24, 2026** on. The page shows each as "collecting" until it
has its minimum sample, then "holding" or "failing" on the running tally; the verdicts are final on
**September 22, 2027**.

Definitions. *Two-meeting expectation*: Σ probability × bps over the next two FOMC decisions after day D,
from the evening price on D (New York time), Polymarket unless stated. *Implied rate*: the target's upper
bound on D + 1 (FRED `DFEDTARU`; it changes the day after a decision) plus the two-meeting expectation.
Daily changes are taken only between consecutive trading days inside one two-meeting window.

1. **Oil.** On days WTI (FRED `DCOILWTICO`) moves 5% or more from the previous trading day, the
   two-meeting expectation moves the same way on more than half of them. Minimum 8 such days.
2. **Treasuries.** Daily changes in the implied rate and in the 2-year yield (FRED `DGS2`) correlate
   above 0.3. Minimum 60 trading days.
3. **CPI.** The CPI surprise (first print minus the Kalshi ladder mean at T0 − 10 min) and the change in
   the Polymarket two-meeting expectation from T0 − 10 min to T0 + 20 min have the same sign in more than
   60% of releases where both are non-zero. Minimum 6 releases.
4. **Platforms.** The Polymarket and Kalshi two-meeting expectations differ by more than 5 bps on fewer
   than 10% of the days both have a complete outcome set. Minimum 60 days.

A fifth claim, that gold is not a Fed signal, was drafted on 2026-09-22 and dropped on 2026-09-23 along
with gold itself, before any forward data existed.

Event-study rules fixed here. T0 is the release time on the BLS and BEA published calendars, not the
Kalshi ladder's close. A release is used only if its ladder was still trading at T0 − 10 min. The
surprise is the first print minus the ladder mean at T0 − 10 min; the headline number is CPI m/m for
CPI, nonfarm payrolls for the jobs report and core PCE m/m for PCE. Reactions are read at T0 + 20 min and
at 16:00 New York time on Polymarket's one-minute prices, with Kalshi's alongside as a replication:
Kalshi listed each 2025 meeting only once the previous one had passed and its thin outcomes often went
hours without a quote, so its next-two-meeting set is incomplete at most 2025 releases. Placebo: the same
08:20–08:50 window on weekdays with no CPI, jobs, PCE, PPI, GDP or ECI release, no FOMC decision that day
or the day before, not a Thursday; every second such day; both platforms.

## Optical modules and solid-state batteries (projects C and D) — valuation and share

Two pages, one pipeline (`pipelines/valuation/`). Prices are captured daily, consensus weekly.

**What the history of this file does and does not prove.** This section was first committed in
`7af7c68` on 2026-09-16, and that same commit also carried the first snapshot parquets, both facts
files and the marts built from them. The rules were therefore *not* published before the data they
score, and nothing in the git history rules out their having been written with the first run's output
already visible. What the history does establish is that they cannot be changed after a later run
without the change showing up as a new blob hash on the page and as an amendment below. The guarantee
this section offers is accordingly: **fixed from the second weekly run onward, amendments logged.**
The pages must claim that and nothing stronger.

Item 3 reports from the first run, and item 5 reports whenever a cited source covers enough of the
pool. Item 4 reports as soon as a dual-listed issuer carries a forward PE on both of its lines. Item
1 cannot be scored until the 2026 annual reports land in 2027, and item 2 needs a revision series
several weeks long. Each unscored item returns the reason it is unscored, built from the track's own
data, rather than a placeholder number.

Every threshold that changes what an item reports is written out below. A threshold that lives only
in code sits outside the blob hash the pages print, so it could be moved without leaving a trace;
`pipelines/valuation/tests/test_evaluate.py` fails if a constant in the module is missing here.

1. **Consensus accuracy.** For every listing, the calendar-2026 consensus EPS in the snapshot
   nearest 2026-09-30, compared with the company's reported calendar-2026 EPS once the 2026 annual
   report is filed (scored from May 2027, when the last A-share and Japanese filers report).
   Reported as median absolute percentage error and median signed error, split by:
   estimate source (Yahoo consensus vs East Money broker mean), by track, and by whether the
   listing was thinly covered (fewer than three contributing analysts) at the time.
   Baselines, scored identically: (a) "last reported annual EPS, unchanged"; (b) East Money's own
   six-month mean for the A-shares.
   Expected direction, recorded now so it can be wrong: broker forecasts in both tracks will prove
   optimistic, and the error will be larger for the thinly covered names.

2. **Revision persistence.** Each week, split listings into upgraded / unchanged / downgraded by the
   week-on-week change in calendar-2026 consensus EPS. Four weeks later, measure the share of each
   group that moved the same way again. Reported as a 3x3 transition matrix per track, with the
   null hypothesis that next-period direction is independent of this-period direction (chi-square on
   the pooled table). Weeks where fewer than ten listings changed at all are dropped and the count
   of dropped weeks is reported.

   Operative definitions, so the test cannot be softened silently:
   - *Moved.* A change of more than **0.5%** in the calendar-2026 consensus EPS. Anything smaller is
     recorded as unchanged, because East Money rounds and Yahoo restates.
   - *A week.* The grouping leg runs between two vintage dates **7 days apart (±3)**.
   - *Four weeks later.* The second leg runs from the end of the grouping week to the vintage date
     **closest to 28 days after it, and no more than 4 days from that** — not "the first date at
     least 21 days later", which on a weekly grid is always three weeks and is an easier test.
     A window that has no partner at either distance is not scored.
   - *Which vintages count.* Only origins `yahoo_trend` and `snapshot`. `eastmoney_rebuilt` is a
     coverage series — East Money publishes each broker's report once, so rebuilding the mean "as of"
     an earlier date averages only the brokers who had published by then, and that mean moves when a
     new broker starts covering the stock. Differencing it would score coverage growth as a revision.
     `snapshot` is our own weekly capture of the current consensus, derived from each archived
     estimates file by `evaluate.capture_snapshot_vintages`; it is a true revision series from its
     second date onward, and it is what brings the East Money listings into this item.
   - *Minimum evidence.* At least **3** scorable windows, each with at least **10** listings that
     moved; quieter windows are dropped and counted.
   - *The chi-square.* Reported with its degrees of freedom, its p-value and the smallest expected
     cell. Empty rows and columns are dropped before the degrees of freedom are counted. When the
     smallest expected cell is below **5** the statistic is published with `reliable: false` rather
     than suppressed. The same listing contributes to overlapping windows, so the pooled transitions
     are not independent observations and the p-value is optimistic; that is stated with the result.

3. **Valuation dispersion.** The interquartile range of calendar-2026 forward PE across each track's
   priced listings, recorded every quarter end. Question: does the spread narrow as the 2026
   estimates firm up? Reported as the IQR series with the number of priced listings beside it,
   because a narrowing spread caused by companies dropping out of the pool is not a narrowing spread.

4. **Same earnings, two prices.** For dual-listed issuers (CATL, BYD, YOFC), the ratio of the A-line
   forward PE to the H-line forward PE, recorded quarterly. This is a control for item 3: it is the
   dispersion that remains when the earnings, the accounting and the forecasts are held identical
   and only the market changes.

5. **Computed share versus cited share.** For each track, the computed pool share (basis A) against
   the most recent third-party share or ranking (basis B), for every company where both exist.
   Reported as the rank correlation between the two orderings and the median absolute difference in
   share, with an attribution of the gap to (a) private vendors absent from the pool, (b) companies
   excluded for not disclosing track revenue, (c) a different market definition by the publisher.
   Scored at each annual update of the cited source.

   Operative definitions:
   - *The rank correlation* is **Spearman's rho**, computed on midranks so that ties cannot invent an
     ordering, over the companies one citation covers. It is reported only at **3 or more** matched
     companies; below that the coefficient is determined by its own arithmetic.
   - *What counts as a cited ordering.* Rankings count, not only percentages — most optical sources
     publish "1 Innolight; 2 Eoptolink; …" and no share at all, and discarding them would throw away
     the rows this item was written for. Three forms of the cited `value` field are read: an ordered
     list; one clause per company carrying a figure (`CATL 289.6 GWh / 39.9% share`); and a citation
     whose `entity` is itself a single pool member, where the first unsigned percentage in the value
     is that company's cited share. Only `value` is read — a second ordering parked in `period` would
     mix two years into one comparison. A figure the curator inferred rather than the publisher
     printed ("INFERRED", "not stated") is not scored, because basis B exists to be somebody else's
     number.
   - *Matching a citation to a pool member.* An exact company name wins outright; otherwise anything
     carrying a conjunction, a comma, or an aggregate word (combined, total, industry, market, top,
     share, suppliers, vendors, …) is refused before matching is attempted, and what is left must be
     a whole-word prefix of exactly one pool member. A bare substring match would bind
     "CATL+BYD combined SHARE 54.6%" to CATL and "REPT 16.9 GWh (+118.5%, new entrant displacing
     Sunwoda)" to Sunwoda.
   - *Which citation is scored.* Every usable comparison is published with its pairs and the exact
     source text each figure was read out of. The headline rank correlation is the comparison with
     the most matched companies, ties broken by the alphabetically first source name; the headline
     share difference is the comparison with the most matched percentage shares, broken the same way.
     Both rules are mechanical so that the choice cannot follow the answer.
   - *Ordering by quantity.* When a citation ranks companies by a physical quantity rather than a
     share (GWh of battery usage, say), the ordering is used and the unit is reported; all companies
     in one comparison must carry the same unit. A quantity ordering supports the rank correlation
     and not the share difference.

Scoring code lives in `pipelines/valuation/evaluate.py` and writes to
`data/marts/valuation/evaluation.json`. Until a scoring date arrives an item returns
`{"status": "not_yet", "why": …}`, with the reason built from that track's own data, rather than a
placeholder number.

### Amendments to this section

Logged because the blob hash on the page changes with them, and a pre-registration whose changes are
not listed is not one. No item had resolved when any of these were made.

- **2026-09-16, after the first run.** Item 2: registered constants written out (0.5% move threshold,
  the 7-day grouping leg, the 28-day horizon and its tolerance, the 10-mover and 3-window minimums,
  the origins that count, the expected-cell floor), because the code had them and this file did not.
  The horizon in code was "at least 21 days", which on a weekly cadence scored three weeks, not the
  four registered; the code was changed to match this file, not the other way round. The chi-square
  registered here had never been implemented and now is. Item 5: the rank correlation registered here
  had never been implemented and now is; the matcher had been discarding every citation whose unit
  was not a clean percent, which excluded every ranking, and now reads rankings too. The
  pre-registration paragraph at the top of this section was rewritten to say what the git history
  actually supports.

## US data-center electricity (project E) — where the increment comes from

One page, two pipelines (`pipelines/power/` for the physical and demand layers, `pipelines/valuation/`
for the company pool). The generator inventory and retail sales are monthly, the PJM load forecast is
annual, the curated layers are updated by hand.

**What the history of this file does and does not prove.** This section was committed on 2026-09-18,
the same day as the first snapshots, the first facts file and the page itself. The rules were
therefore *not* published before the data they score, and nothing in the git history rules out their
having been written with the first run's output already visible — indeed items 1 and 3 were written
knowing the current values, which is exactly why each records an expected direction that a later run
can falsify. What the history establishes is the same guarantee the valuation section offers:
**fixed from the second run onward, amendments logged.** The page must claim that and nothing more.

Items 1, 2 and 4 report from the first run. Item 3 needs a second 860M vintage twelve months after
the first, so it reports from September 2027. Item 5 reports as soon as two quarters of a utility's
own disclosure are on file. Each unscored item returns the reason it is unscored, built from the
project's own data.

1. **The physical build is not the contracted build.** For each supply path in
   `pipelines/power/config.PATHS`, compare its share of contracted megawatts (the curated deal table,
   disclosed capacity only) with the share of planned EIA capacity attributable to the same
   technology family. Reported as the rank correlation between the two orderings and the absolute
   gap per path.
   Expected direction, recorded now so it can be wrong: the two orderings will disagree, with
   nuclear ranking far higher on contracts than on construction and solar far higher on construction
   than on contracts. The page's central claim fails if the rank correlation is above 0.7.

2. **Commercial-sector growth is concentrated, not general.** Each month, the share of national
   trailing-twelve-month commercial sales growth accounted for by the five states with the largest
   absolute commercial growth. Reported as that share and as the Herfindahl index of state-level
   commercial growth.
   Expected direction: the top five states hold more than half of national commercial growth, and
   the concentration rises over the scored period. A fall toward the states' share of population
   would mean the sector proxy has stopped tracking data centers and the page's use of it is wrong.

3. **Deferred retirements keep growing.** Comparing the 860M edition of each September with the one
   twelve months earlier, the megawatts whose retirement moved later or was withdrawn, and the coal
   share of them.
   Expected direction: the deferred total rises year on year while load growth continues. A fall
   would mean either that the queue has cleared or that the deferrals already taken were enough, and
   the page's framing of retirements as a supply path would need revising. Baseline, scored
   identically: the same diff computed for 2019-2020, before the current load growth.

4. **PJM's large-load share is not an artefact of one zone.** Per zone, the large-load adjustment's
   growth as a share of summer-peak growth over the forecast's first five years. Reported as the
   distribution across zones and the count above 1.0, never clipped.
   Expected direction: the median zone sits above 0.8 and at least a third sit above 1.0. If the
   RTO figure turns out to rest on one or two zones, the page's national framing is too strong and
   must be narrowed to those zones by name.

5. **Utility pipelines shrink when collateral is attached.** For each utility with two or more
   quarters in the curated table, the change in its own headline pipeline figure, and whether the
   quarter introduced a collateral or agreement requirement.
   Expected direction: a figure defined by signed agreements with money behind it falls relative to
   one defined by interconnection requests, and the first quarter a utility attaches collateral
   shows the largest single fall. Exelon's Q1-to-Q2 2026 cut from 18 GW to about 11 GW is the
   observation that prompted this item and is therefore excluded from the scored sample.

Registered thresholds, written here because a threshold that lives only in code sits outside the
blob hash the page prints: item 1's rank-correlation failure line is 0.70; item 2's concentration
uses the top five states and the failure line is the five states' share of US population; item 3
compares editions exactly twelve months apart (`config.DEFERRAL_COMPARISON_MONTHS`); item 4 uses a
five-year window from the forecast's first year and the zone must carry both a peak and a published
adjustment; item 5 counts only utilities with at least two quarters on file.

Scoring code will live in `pipelines/power/evaluate.py` and write to `data/marts/power/evaluation.json`.
Until a scoring date arrives an item returns `{"status": "not_yet", "why": …}` with the reason built
from the project's own data.

### Amendments to this section

None yet.

## Soft-tissue surgical robots (project F) — disclosure, utilisation and price

One page, two pipelines (`pipelines/surgical/` for units, tenders, quota and clearances;
`pipelines/valuation/` for the listed pool). One source is fetched and three are curated by hand.

**What the history of this file does and does not prove.** This section was committed on 2026-09-21, the same
day as the first run and the page. The rules were therefore not published before the data they score, and
items 1 and 3 were written knowing the current values — which is exactly why each records an expected
direction a later run can falsify. The guarantee is the same one the other sections offer: **fixed from the
second run onward, amendments logged.**

Items 1, 2 and 4 report from the first run. Item 3 needs a second year of tender awards, so it reports from
September 2027. Item 5 needs a maker outside the current two to begin publishing, and may never report —
which is itself the result.

1. **A system-count share and a procedure share rank this pool differently.** For every company publishing
   both, its share of disclosed installed systems against its share of disclosed procedures. Reported as the
   two shares and the gap, per company per year.
   Expected direction, recorded now so it can be wrong: the incumbent's procedure share exceeds its system
   share, because its installed base is older and works harder. If the two converge to within 5 points, the
   page's central argument that a system count is a poor description of this market is weakened and the
   framing must change.

2. **Utilisation separates the pool, and does not converge.** Procedures per installed system per year, per
   product, and the ratio between the highest and the lowest.
   Expected direction: the spread widens or holds. A narrowing spread would mean placement quality is
   levelling and the incumbent's advantage is in installed base rather than in use, which is a different
   investment case from the one this page describes.

3. **Domestic Chinese award prices converge on the imported ones.** The ratio of the median domestic award
   price to the median imported-system award price, by half-year, from the curated panel.
   Expected direction: the discount narrows as domestic systems add configuration rather than cut price. If
   it widens instead, the market is in a price war and the unit-share gains it produces are worth less than
   they look. Registered threshold: measured on purchases only, excluding leases, service contracts, bundled
   lots and any award whose unit price was derived by division; a half-year with fewer than five qualifying
   awards on either side is reported as not scored rather than scored thinly.

4. **The licence ceiling binds, or it does not.** Cumulative disclosed installed systems in China against the
   819-system national permit ceiling, and the date any successor plan appears.
   Expected direction: no successor plan is published during the scored period and the disclosed installed
   base stays below the lapsed ceiling. A successor plan appearing, or the ceiling being exceeded without
   one, both falsify the page's treatment of the quota as a live constraint.

5. **Disclosure quality is itself tracked.** Per company per quarter, the count of distinct auditable unit
   metrics published, as `publish.disclosure_scorecard` computes it.
   Expected direction: the count does not improve. Nobody outside the two current disclosers starts
   publishing units, and no diversified medtech begins reporting surgical robotics as a segment. If somebody
   does, the page's central finding has a shelf life and the date it expired should be recorded.

Registered thresholds, written here because a threshold living only in code sits outside the blob hash the
page prints: item 1's convergence line is 5 percentage points; item 3 requires at least five qualifying
awards per side per half-year and excludes the row classes named above; item 4's ceiling is the 819 figure in
国卫财务发〔2023〕18号, which expired 2025-12-31; item 5 counts only the metrics in `config.UNIT_BASIS`.

Scoring code will live in `pipelines/surgical/evaluate.py` and write to `data/marts/surgical/evaluation.json`.
Until a scoring date arrives an item returns `{"status": "not_yet", "why": …}` built from the project's own data.

### Amendments to this section

None yet.

## Memory-chip price cycles (project G) — contract-price calendar and stock timing

A frozen case study (`pipelines/memcycle/`), with no scheduled workflow. The price side is recomputed from
the Bank of Korea's ECOS raw responses on every build. The stock side comes from month-end closes that
vendor terms forbid redistributing, so it is computed on the owner's machine and only months, leads,
ratios and flags enter the repo.

**The authoritative text is Chinese and it predates this section.** The rules were registered in
`docs/prereg/memory-cycles/预注册_周期定时与检验规则_20260928.md`. That file came into this repo with its
five original commits, and its git blob ids are the same as in the owner's private research repo:

| blob | committed | what it added |
|---|---|---|
| `9f90587a` | 2026-09-28 13:11 −07:00 | rules and Q3/Q6/Q7/Q8 criteria |
| `4b4a9e94` | 2026-09-28 14:59 −07:00 | implementation notes 1–3 |
| `77fdbb45` | 2026-09-28 15:00 −07:00 | implementation notes 4–6, Q3 sample boundaries |
| `e4a15ecd` | 2026-09-28 17:33 −07:00 | corrections after the independent rule review |
| `bedd0de4` | 2026-09-28 18:02 −07:00 | Q3 roster as registered |

This section translates those rules and adds nothing to them. Where the two differ, the Chinese file wins.

**What the history does and does not prove.** The commit dates were self-reported by a private repo. They
become publicly checkable only from the first push of this branch, so they are evidence of order, not proof
of it. The prereg's §0 lists what had been seen before registration.

- The price calendar rule was fixed before the calendar was computed. It is not blind: an informal scan
  of the same index had already been made.
- Implementation notes 1–3 are not blind either (correction 3). The first trial run happened after the new
  data had been downloaded.
- Q3 was computed after registration. Its sample was then corrected to match the registered roster, after
  the result had been seen (correction 4). `data/case_studies/memcycle/q3_versions.json` records the verdict
  at every research commit, and it is "falsified" in all four versions. The page shows that history in
  full.
- Q7 is not blind, because the price data had been seen.
- Only Q6 and Q8 are forward tests.

**Merging.** The branch reaches `main` by fast-forward or `git rebase --committer-date-is-author-date`. It is
never squashed, because squashing would replace the five registration commits with one dated at merge.

### Items

1. **Price-cycle calendar** (descriptive, resolved). ECOS table `402Y016`, DRAM item `30911201AA` from
   1995-01 and flash item `30911202AA` from 2000-01. The main basis is contract currency (C); the US dollar
   (D) and Korean won (W) bases are sensitivity checks. The rule is a simplified Bry–Boschan on log levels
   with no smoothing:
   - candidates are extremes of a 13-month window, 6 months either side, with ties going to the earliest month;
   - peaks and troughs alternate;
   - every phase lasts at least 6 months and every cycle at least 15 months;
   - every rise is at least +20% and every fall at least −20%.

   Durations are resolved before amplitudes. Each violation removes the two ends of the smallest violating
   phase, and the rule then returns to the alternation step. Thresholds of 30% and 0% are the registered
   sensitivity. A turn within 6 months of the last observation is unconfirmed. One cycle is trough, peak,
   trough.

2. **Q3: stocks peak one to three quarters before contract prices** (resolved: **falsified**). A stock peak
   is the highest month-end close in (T(k−1), T(k)]. The lead is the price-peak month minus the stock-peak
   month. The sample is the pure-play company-cycles with a confirmed price peak, a preceding trough and a
   complete window.
   - **Holds** if at least 60% lead by 1 month or more and the median lead is between 3 and 9 months.
   - **Falsified** if fewer than 50% lead by 1 month or more.
   - **Inconclusive** otherwise.

   The same rule is reported separately for DRAM makers only, for the diversified group and for the NAND
   panel against the flash calendar. None of these enters the verdict. Infineon counts until 2006-04.
   ProMOS is not on the registered list. Toshiba is registered but has no data, and it is reported as
   missing.

3. **Up-leg multiples** (descriptive). The multiple is the stock-peak close divided by the lowest month-end
   close from the previous price peak up to the stock peak. The auxiliary basis is intramonth low to high.
   Excess is the stock's multiple over the local benchmark's multiple on the same two months. A row whose
   low falls within three months of a listing after the previous price peak is flagged and left out of any
   comparison.

4. **Q6: a capex-to-revenue warning threshold** (not yet). The candidate thresholds are the 50th to 90th
   percentiles, in steps of 5, of the pool's trailing-four-quarter capex over memory revenue on data to
   2015Q4. The threshold is chosen by Youden's J, with ties going to the lower one. The event is a DRAM
   price peak 4 to 8 quarters later. It is scored out of sample from 2016Q1 and falsified if precision is no
   more than the base rate plus 10 percentage points, or if recall is below 50%.

5. **Q7: smaller swings after consolidation** (not yet; not blind). Three measures are each split at 2013,
   the year of Micron's acquisition of Elpida: the pure-play basket's peak-to-trough drawdown, DRAM industry
   revenue, and the pool's operating margin. A measure has "shrunk" if the later median is below the earlier
   one. The three are reported side by side and never pooled.

6. **Q8: the next downturn is shallower** (not yet; forward, deadline 2028-12-31). A new peak is confirmed
   when the index has spent 6 months below it and has fallen 20%. Two measures cover the 12 months after it:
   the index's largest fall and the worst quarterly fall in industry revenue. Each is compared with its
   historical median.
   - **Holds** if both are below their medians.
   - **Falsified** if either reaches its median.
   - **Not scored** if data is missing.

Registered constants. This list is checked against `pipelines/memcycle/config.py` by
`pipelines/memcycle/tests/test_plan.py`, because a threshold that lives only in code sits outside the blob
hash the page prints:

- `HALF_WINDOW = 6`
- `MIN_PHASE = 6`
- `MIN_CYCLE = 15`
- `AMP_MAIN = 0.20`
- `AMP_SENS = (0.30, 0.0)`
- `SERIES_START = {DRAM: 1995-01, flash: 2000-01}`
- `LEAD_MIN = 1`
- `Q3_HOLDS_SHARE = 0.60`
- `Q3_MEDIAN_BAND = (3, 9)`
- `Q3_FALSIFY_SHARE = 0.50`
- `INFINEON_LAST_MONTH = 2006-04`
- `Q6_PERCENTILES = 50..90 step 5`
- `Q6_TRAIN_END = 2015Q4`
- `Q6_TEST_START = 2016Q1`
- `Q6_HORIZON_QUARTERS = (4, 8)`
- `Q6_PRECISION_MARGIN = 0.10`
- `Q6_MIN_RECALL = 0.50`
- `Q7_SPLIT_YEAR = 2013`
- `Q8_CONFIRM_MONTHS = 6`
- `Q8_CONFIRM_FALL = 0.20`
- `Q8_WINDOW_MONTHS = 12`
- `Q8_DEADLINE = 2028-12-31`

Scoring code lives in `pipelines/memcycle/evaluate.py` and writes `data/marts/memcycle/evaluation.json`.
Q6, Q7 and Q8 return `{"status": "not_yet", "why": …}` until their data exists.

### Amendments to this section

None yet. Amendments to the rules are made in the Chinese file first, by the owner, and then imported here.

## New payments companies (project H) — who keeps what, and share by lens

A project in progress (`pipelines/payments/`, page `payments-landscape`). It sorts "new payments" companies into four
businesses and compares two things: what each keeps from every $100 it handles, and its share as measured by four labelled
lenses. The study design is in `docs/payments/PLAN.md` (Chinese) and the build runbook in `docs/payments/EXECUTION.md`.

**Written 2026-10-02/03, before any formal pipeline pull.** Scouting agents had already read some numbers while
designing the study. Each question below states its "already seen" reading, so none of them is fully blind. They become
forward tests from the next data release onwards. Status vocabulary for every item: `collecting`, `holds`, `falsified`,
`undecidable`. The page shows the status and the reading it rests on.

**Merging.** The branch reaches `main` by fast-forward or `git rebase --committer-date-is-author-date`. It is never
squashed, because the commit that adds this section is its registration timestamp.

### Universe (pinned)

Each company sits in the layer of its largest revenue source. Tickers are checked against SEC's ticker map in W2.
A company that cannot be confirmed there is logged as an amendment, never silently dropped.

| layer | core (charted, in share) | classification table only |
|---|---|---|
| A merchant acceptance | Stripe, Adyen, Checkout.com, PayPal (incl. Braintree), Block/Square, Shopify Payments, Toast, Fiserv Clover, Global Payments (incl. Worldpay), Shift4 | Rapyd, Highnote |
| B BNPL | Affirm, Klarna, Afterpay (Block), PayPal Pay Later, Zip, Sezzle | — |
| C corporate cards, spend management, business banking | Ramp, Brex (Capital One since 2026-04-07), BILL, Navan, Mercury, Corpay | Rippling, Expensify, AvidXchange |
| D cross-border B2B and treasury | Airwallex, Wise, Payoneer (acquisition by Nuvei pending), dLocal, Flywire | Ebury, Nium |
| E issuing infrastructure (control) | Marqeta | Lithic, Unit |
| F stablecoin rails (control, never in share) | Circle | Bridge, Tempo, BVNK |

### Pinned measurement choices

- **npm client SDK set (Q1).**
  - Stripe `@stripe/stripe-js`.
  - Adyen `@adyen/adyen-web`.
  - PayPal/Braintree `@paypal/paypal-js` + `braintree-web`.
  - Checkout.com `@checkout.com/checkout-web-components`.
  - Airwallex `@airwallex/components-sdk`.

  Framework wrappers such as `@stripe/react-stripe-js` and `@paypal/react-paypal-js` are excluded. They install the core
  package, so counting them double counts. The npm package `affirm` and the PyPI packages `affirm` and `airwallex` are
  not the companies' SDKs and are never used.
- **Server SDKs (descriptive only).** npm `stripe`, `@adyen/api-library`, `braintree`.
- **Job boards (counts only, never text).**
  - Ashby: `ramp`, `airwallex`, `plaid`.
  - Greenhouse: `stripe`, `brex`, `mercury`, `affirm`, `adyen`, `chime`, `block`, `toast`, `sezzle`, `billcom`.

  Greenhouse `wise` is a different company and is excluded. Ownership of every token is re-verified in W2.
  A token that fails is dropped with an amendment.
- **Web coverage** comes from the HTTP Archive Tech Report, US origins, mobile client. It is always called "coverage",
  never share. The headline uses a 3-month rolling mean.
- **Closed `metric_kind` vocabulary.** `filed_revenue`, `filed_volume`, `stated_run_rate`, `stated_volume`,
  `primary_round_valuation`, `tender_valuation`, `reported_talks`, `acquisition_price`, `counterparty_filing`,
  `statutory_accounts`, `third_party_estimate`, `derived`. Only filed, stated (company-confirmed), counterparty and
  derived rows are charted. `reported_talks` and `third_party_estimate` never are.
- **Share rules.**
  - Share is computed only within one layer, one volume definition and one period. Layers are never summed.
  - Pool shares are labelled "share of disclosed pool".
  - Annualised figures are never mixed with TTM figures.

### Items

**Q1 Developer mindshare is more concentrated than money.**
- Claim: among Stripe, Adyen, Checkout.com, PayPal/Braintree and Airwallex, Stripe's share of client-SDK npm downloads
  (pinned set) is at least 2.0x its share of disclosed volume. Volume definitions differ, for example PayPal TPV includes
  Venmo. So the volume share is an interval and the test uses its upper bound.
- Refuted: the ratio is below 1.5 in 3 consecutive monthly refreshes.
- Graded: monthly. Formally scored on Stripe's next annual letter (about 2027-02).
- Already seen: download share 84–90% depending on the whitelist; volume share about 32% (computed).

**Q2 The top of the web is changing hands.**
- Claim: in the HTTP Archive Tech Report, US top-10k origins on mobile, Stripe's lead over PayPal holds or widens across
  the next 6 monthly crawls (3-month rolling mean). Across all US origins PayPal still leads.
- Refuted: PayPal retakes the top-10k lead on the rolling mean, or the gap shrinks by more than half.
- Graded: 2027-03.
- Already seen: Stripe 522, PayPal 478 (2026-08 crawl).

**Q3 BNPL share is moving to Affirm.**
- Claim: Affirm's GMV growth year on year exceeds Klarna's US GMV growth by at least 5 percentage points in each of the
  next two quarterly pairs: Affirm FQ1'27 against Klarna Q3'26, then Affirm FQ2'27 against Klarna Q4'26.
- Refuted: either pair's gap is below 5 percentage points.
- Graded: 2026-11 and 2027-02.
- Already seen: +36% against +27%. Affirm's GMV includes small Canada and UK volumes.

**Q4 Corporate-card net interchange settles at 1.0–1.6% of card volume.**
- Claim: interchange net of rewards stays between 1.0% and 1.6% of card volume. BILL reports revenue net of rewards from
  the quarter ending 2026-09-30, which gives a natural before/after test.
- Refuted: any US spend-management issuer shows above 2.0% or below 0.8% for two consecutive quarters.
- Graded: 2026-11 and 2027-02.
- Already seen: BILL gross 2.60%, rewards 51% of interchange, net about 1.28% (computed).

**Q5 BNPL is a lending business.**
- Claim: of the per-$100 retention gap between Affirm and Klarna (4.15 against 1.22, computed, different periods), at
  least 70% comes from the credit side (interest, gain on sale, servicing), not merchant fees.
- Refuted: the merchant side explains more than half.
- Undecidable: Klarna does not disclose the split.
- Graded: 2026-11.

**Q6 Brex earns less than half of its revenue from card interchange.**
- Claim, as an inference chain:
  1. Capital One's purchase-price allocation shows $815M of Brex loans.
  2. At 20–45 days of receivables, that implies $6.6–14.9B of annual card spend.
  3. At about 1.28% net interchange, that is $85–190M a year.
  4. Against about $700M of stated annualised revenue (secondary source), that is under half.
- Refuted: Capital One discloses figures showing net interchange above half.
- Undecidable: there is no disclosure by Capital One's FY2026 10-K (about 2027-02).

**Q7 Cross-border: challengers cut price, the industry average does not move.**
- Claim: Wise's cross-border take rate falls by at least 3 bps a year (0.58% to 0.52% last year), while the FSB's average
  cost of MSME B2B cross-border payments stays at or above 1.5% (1.5% in 2023, 1.6% in 2024 and 2025).
- Refuted: FSB's 2026 figure is 1.4% or below, or Wise's H1 FY27 take rate is flat or up.
- Graded: 2026-10 to 2026-11.

**Q8 Commercial-card interchange does not fall after the settlement.**
- Claim: in the first Visa rate sheet after final approval of the 2026 interchange settlement, US Commercial
  Card-Not-Present stays at or above 2.60% + $0.10.
- Refuted: it is cut by more than 10 bps.
- Void: the court rejects the settlement.
- Graded: after the fairness hearing (2026-11-16, date from a secondary source) and the spring 2027 sheet.
- Already seen: 2.70% + $0.10, effective 2026-04-18.

**Q9 Consolidation continues (a calibration item).**
- Claim: by 2027-09-30, at least 2 roster companies that are independent today and valued below $5B announce an
  acquisition. Payoneer does not count.
- Refuted: 0 or 1.
- Already seen: 3–4 roster deals in the past 12 months.

### Amendments to this section

(none yet)

## Cybersecurity (project I) — layers, valuation and what the analysts add

A project in progress (`pipelines/cyber/`, page `cybersecurity-landscape`). It maps cybersecurity companies, listed and
private, onto twelve layers, prices the listed ones on enterprise value over next-twelve-month revenue, sets the analysts'
target prices and ratings beside the prices, and compares private valuations with the public multiples. This section is the
pre-registration for the page. The study design is kept outside the repository. The listed pool was built from public sources:
ETF holdings lists, exchange listings, company filings, and Yahoo and East Money pages.

**Written 2026-10-16, before any formal pipeline pull.** A scouting pass had already read some numbers. The baseline is the
snapshot taken after the US close of 2026-10-16, stored as `data/snapshots/cyber/consensus/2026-10-17.parquet` (the file is named for
its UTC date). It is used to count populations and freeze lists, it is never part of a test series, and days to the end of fiscal year 0
are counted from 2026-10-16, its market date. **The formal series starts with the Monday run of 2026-10-26.** Runs before it, whenever this
section is committed, are not part of any series. This section must be on `main` before that run. If it is not, no snapshot is read until a dated amendment
moves every date in the items by the same whole number of weeks. A weekly snapshot is a Monday run. Scheduled runs start several hours after their 07:47 UTC
cron, so the price in a snapshot is normally a live quote for US listings, at or near the close for European ones and the day's close for Asian ones; each price
carries Yahoo's time stamp. Status vocabulary for every item: `collecting`, `holds`, `falsified`, `undecidable`. The page shows the status and the reading it
rests on.

**Merging.** This section, the pool, the layer tree, the capture code, the pinned constants and the baseline snapshot are added in one commit, and the branch reaches
`main` by fast-forward or `git rebase --committer-date-is-author-date`. It is never squashed. Any later change to this section goes under Amendments, never into
that commit. A commit date is evidence of order, not proof of it. The independent record is the time GitHub shows for the push and for the bot commit that
stores the 2026-10-26 snapshot. `pipelines/cyber/tests/` fails if the pool, the constants or the lists drift from this text.

**Not blind.** Q1, Q2, Q3, Q6, Q7, Q8 and Q9 are partly retrospective. The multiples, growth rates, FCF margins and layers of the companies in Q1, Q2, Q3,
Q7 and Q9 were seen before the thresholds below were written, and the weekly cross-sections overlap heavily with that first look. Q6 was written after the
headline multiples of three large deals were seen, and Q8 after the Varonis agreement, the enterprise values near its cutoffs and the press reports of two sale
processes were seen. The 90% and 0.10 levels of Q1, the 10% level of Q10, the ratio of 2 in Q3, and the edges of the Q8 list were chosen with the baseline reading in view.
Layers were assigned after the multiples were seen, from the product that anchors each company. Q1 scores the layer each company has in the universe table as first
committed; a later amendment that moves a company is shown beside the registered result and does not change what Q1 scores. The scouting pass counted 15 companies where the baseline
counts 17; they come from different pulls, and the baseline is the registered one. The numerical "already seen" readings of Q1, Q2, Q3, Q7 and Q9 were produced by
`pipelines/cyber/readings.py` on the baseline, and `tests/test_readings.py` pins them; the upside and dispersion figures of Q4 and Q5 are read straight from the baseline table. The deal multiples in Q6, the Meritech figures in Q2 and the press reports in Q8 do not come from the baseline.

**How every item is judged.** Each item says what makes it `holds`, what makes it `falsified` (written Falsified in the items) and what makes it `undecidable`. The
rules apply in this order. (1) If a stated precondition for scoring fails, the item is `undecidable`. (2) If a Holds condition and a Falsified condition are both met,
the item is `undecidable`. (3) If a Falsified condition is met, the item is `falsified`. (4) If a Holds condition is met, the item is `holds`. (5) Otherwise the item is
`undecidable`, which means the data neither support nor contradict the claim. `readings.grade` is this order. Falsified needs evidence against the claim: for Q1 to Q5, Q7 and Q10 an
interval or a test that points against it; for Q6, Q8 and Q9 a count or a comparison of two numbers, as each item says. A result that is merely not significant is never
Falsified. With 15 to 34 companies, several items will end `undecidable`, and each item says how likely that is, from `pipelines/cyber/power.py` (300 simulated cross-sections
of the baseline's shape, 500 draws each, seed 20261026). The ten items are read separately and no adjustment is made for reading ten. Under pure noise a false `holds` is rare
for Q1, Q2, Q3, Q5, Q7 and Q10 (a few per cent each) but common for Q4 and Q9, which claim that nothing happens and `hold` about two runs in three when nothing is going on. The page labels them
that way. Intervals are percentile bootstraps that resample companies, with 10,000 draws and seed 20261026; permutation tests use the same draws and seed. Each procedure
starts its own random generator from that seed.

### Universe (pinned)

**Rings.** Ring A is the core pricing pool: purity high or main, with at least one analyst covering it (Yahoo
`numberOfAnalystOpinions` of 1 or more, or an East Money 2026E broker count of 1 or more). Ring B is classified and counted
but not priced. Ring C is the private directory and ledger. It is empty at registration, and `data/reference/cyber/` holds only the lists named below. Ring F is delisted or
acquired companies, kept as a reference and in no test. At registration there are 73 ring A listings (58 outside
China, 15 in China), 107 ring B and 6 ring F. Cisco (`CSCO`) is in ring A and is priced by the optical-modules page, so the cyber track
reads its price by ticker and does not price it twice. The pool as registered is stored in `data/reference/cyber/pool_registered.json` and never changes; `pool.py`
is the live pool and may depart from it only by an amendment listed in `amendments.py`.

**Layer tree.** One tree. A company sits in the layer of its largest cybersecurity revenue line and appears once on the layer
map. Other lines go in `also_in`. A company with three or more layers is flagged `platform` and still placed by its largest
line. `placement_basis` is one of `segment_disclosed`, `company_description`, `etf_classification`, `anchor_product`, and every row's is `anchor_product` at
registration: the layer comes from the product that anchors the company, and the segment table or the company description has not
yet been read for any row. The page labels every row unverified until it has been. `platform` and `also_in` are
descriptive, are used in no test, and are maintained in `pool.py` without an amendment. China's integrated platforms
have no layer of their own; each is placed by its anchor product.

| code | layer | name | sub-sectors |
|---|---|---|---|
| 1 | network_edge | Network and edge security | ngfw_appliance, sase_sse, ddos_waf_adc, ndr_visibility, carrier_security |
| 2 | endpoint | Endpoint and XDR | epp_edr_xdr, consumer_security, uem |
| 3 | identity | Identity and access | workforce_iam, iga_nhi, pam, idv_biometrics, keys_pki |
| 4 | cloud_appsec | Cloud and application security | cnapp, appsec_supply_chain, api_edge |
| 5 | data_privacy | Data security and privacy | dspm_dag, backup_resilience, encryption_keys, privacy_compliance |
| 6 | secops | Security operations | siem_analytics, soar_ai_soc, exposure_mgmt, threat_intel, mdr, forensics |
| 7 | email_human | Email, collaboration and human risk | email_security, awareness_training, browser_web |
| 8 | ot_iot | OT, IoT and ICS | ot_ics, iot_medical |
| 9 | grc_insurance | GRC, compliance and cyber insurance | grc_irm, third_party_risk, cyber_insurance |
| 10 | services | Services, MSSP, consulting and distribution | gov_defense_services, consulting_mssp, distribution, cyber_range_training |
| 11 | ai_security | AI security and AI-native security operations | ai_app_model, agent_security, ai_native_secops |
| 12 | infra_hw | Infrastructure, hardware and post-quantum | secure_chips, hsm_commercial_crypto, post_quantum, high_assurance_crypto |

Three sub-sectors exist only in China's market and are placed under the nearest layer and labelled: `forensics`, `cyber_range_training`, `hsm_commercial_crypto`.

**Purity.** `high`: cybersecurity is the main business (at least 70% of revenue where disclosed; otherwise the company
describes itself as a security company with no other reportable segment). `main`: an identifiable security business of
20–70% of revenue, or one of several stated main lines. `partial`: a small exposure, kept because an ETF or a common
classification lists the company. Purity is stored separately from the disclosed security share. Only `high` companies enter
the Q1, Q2, Q3, Q7 and Q9 populations. Q4 and Q5 use a frozen list that includes `main` companies, and the page flags them.

**Listings.** Ring A and ring B by layer and purity, with the name each ticker carries in `pool.py`. The layer, purity and
name of a ticker are those in this table. A ticker not in it is not in the pool. Moving a ticker is an amendment.

| layer | ring | purity | listings |
|---|---|---|---|
| network_edge | A | high | `002212.SZ` Topsec Technologies; `002439.SZ` Venustech; `002912.SZ` Sinovatio; `300369.SZ` NSFOCUS Technologies; `688168.SS` ABT Networks; `688651.SS` WebRAY; `CHKP` Check Point Software Technologies; `CNS.L` Corero Network Security; `CYR.DE` cyan AG; `FTNT` Fortinet; `INTZ` Intrusion; `NET` Cloudflare; `NTSK` Netskope; `PANW` Palo Alto Networks; `RDWR` Radware; `ZS` Zscaler |
| network_edge | A | main | `300454.SZ` Sangfor Technologies; `300768.SZ` DPtech; `ALLT` Allot; `ATEN` A10 Networks; `CSCO` Cisco Systems; `FFIV` F5 |
| endpoint | A | high | `4704.T` Trend Micro; `688561.SS` QI-ANXIN Technology Group; `CRWD` CrowdStrike; `FSECURE.HE` F-Secure; `GEN` Gen Digital; `S` SentinelOne |
| endpoint | A | main | `BB` BlackBerry; `MSFT` Microsoft |
| identity | A | high | `603232.SS` Koal Software; `688201.SS` Infosec Technologies; `ALLIX.PA` WALLIX Group; `BKYI` BIO-key International; `IDAI` T Stamp (Trust Stamp); `IDN` Intellicheck; `IGP.L` Intercede Group; `MITK` Mitek Systems; `OKTA` Okta; `OSPN` OneSpan; `SAIL` SailPoint; `SSH1V.HE` SSH Communications Security; `YUBICO.ST` Yubico |
| identity | A | main | `GBG.L` GB Group |
| cloud_appsec | A | main | `AKAM` Akamai Technologies |
| data_privacy | A | high | `RBRK` Rubrik; `VRNS` Varonis Systems |
| data_privacy | A | main | `CVLT` Commvault Systems |
| secops | A | high | `300188.SZ` SDIC Intelligence (ex-Meiya Pico); `688023.SS` DBAPPSecurity; `CY4.MI` Cy4gate; `QLYS` Qualys; `RPD` Rapid7; `TENB` Tenable Holdings |
| secops | A | main | `CGNT` Cognyte Software |
| email_human | A | high | `2326.T` Digital Arts; `4417.T` Global Security Experts |
| services | A | high | `6690.TWO` Acer Cyber Security (安碁資訊); `7765.TW` CHT Security (中華資安國際); `CTM` Castellum; `CYCU` Cycurion; `NCC.L` NCC Group; `SWG.L` Shearwater Group; `TLS` Telos |
| services | A | main | `KUD.SW` Kudelski; `WYY` WidePoint |
| infra_hw | A | high | `002268.SZ` CETC Cyberspace Security (ex-Westone); `688489.SS` Sansec Technology; `BTQ` BTQ Technologies; `LAES` SEALSQ; `SEN.AX` Senetas; `WKEY` WISeKey International Holding; `YSN.DE` secunet Security Networks |
| network_edge | B | high | `136540.KQ` WINS; `434480.KQ` MONITORAPP; `688030.SS` Hillstone Networks; `8298.TWO` Genie Networks (威睿科技); `CLAV.ST` Clavister Holding |
| network_edge | B | main | `3664.TWO` Array Inc. (安瑞-KY); `FCT.AX` FirstWave Cloud Technology |
| network_edge | B | partial | `300017.SZ` Wangsu Science & Technology; `3704.TW` Zyxel Group (合勤控); `603496.SS` Embedway Technologies; `688292.SS` Haohan Data Technology; `ALAR` Alarum Technologies; `FSLY` Fastly; `GRRR` Gorilla Technology Group; `HPE` Hewlett Packard Enterprise; `KEYS` Keysight Technologies; `NTCT` NetScout Systems |
| endpoint | B | high | `053800.KQ` AhnLab; `300352.SZ` VRV Software; `300659.SZ` Zhongfu Information; `3692.T` FFRI Security; `QUICKHEAL.NS` Quick Heal Technologies |
| endpoint | B | main | `688225.SS` AsiaInfo Security |
| endpoint | B | partial | `047560.KQ` ESTsoft; `601360.SS` 360 Security Technology; `AVGO` Broadcom |
| identity | B | high | `003029.SZ` JIT; `042510.KQ` Raonsecure; `131090.KQ` Secuve; `300579.SZ` BJCA; `4498.T` Cybertrust Japan; `6865.TWO` WebComm Technology (偉康科技); `AUID` authID |
| identity | B | main | `203650.KQ` Dream Security; `3040.T` Soliton Systems; `4475.T` HENNGE |
| identity | B | partial | `236200.KQ` Suprema; `300386.SZ` Feitian Technologies; `9449.T` GMO Internet Group; `AWRE` Aware; `YOU` Clear Secure |
| cloud_appsec | B | high | `4493.T` Cyber Security Cloud |
| cloud_appsec | B | partial | `DDOG` Datadog; `FROG` JFrog; `GOOGL` Alphabet; `GTLB` GitLab |
| data_privacy | B | high | `150900.KQ` Fasoo AI; `258790.KQ` SoftCamp; `AR9.AX` archTIS |
| data_privacy | B | partial | `HO.PA` Thales; `IBM` IBM; `OTEX` OpenText |
| secops | B | high | `067920.KQ` IGLOO Corporation; `1410.HK` Edvance International Holdings; `300311.SZ` Surfilter; `411080.KQ` SANDS LAB; `TAC.NS` TAC Infosec |
| secops | B | main | `PRO.AX` Prophecy International Holdings |
| secops | B | partial | `300229.SZ` TRS Information Technology; `ESTC` Elastic; `NABL` N-able; `NXL.AX` Nuix |
| email_human | B | high | `418250.KQ` SecuLetter |
| email_human | B | partial | `ZD` Ziff Davis |
| grc_insurance | B | high | `WHK.AX` WhiteHawk |
| grc_insurance | B | partial | `4847.T` Intelligent Wave; `HSX.L` Hiscox |
| services | B | high | `4398.T` BroadBand Security; `688244.SS` Integrity Technology; `CISO` CISO Global; `HUBC` HUB Cyber Security; `PLUR.V` Plurilock Security |
| services | B | main | `184230.KQ` SGA Solutions; `5133.T` Terilogy Holdings; `6590.TWO` Provision Information (普鴻資訊) |
| services | B | partial | `002368.SZ` Taiji Computer; `018260.KS` Samsung SDS; `2453.TW` Syscom Computer Engineering (凌群電腦); `3029.TW` Zero One Technology (零壹科技); `3132.T` Macnica Holdings; `3676.T` DIGITAL HEARTS HOLDINGS; `3762.T` TechMatrix; `3774.T` Internet Initiative Japan; `6050.T` E-Guardian; `6214.TW` Systex (精誠資訊); `ACN` Accenture; `ATO.PA` Atos; `BA.L` BAE Systems; `BAH` Booz Allen Hamilton; `CACI` CACI International; `ESLT` Elbit Systems; `INFY` Infosys; `LDO.MI` Leonardo; `LDOS` Leidos; `MAQ.AX` Macquarie Technology Group; `PSN` Parsons; `QQ.L` QinetiQ; `SAIC` Science Applications International; `SYS.L` SysGroup |
| infra_hw | B | high | `ADVE.ST` Advenica; `ARQQ` Arqit Quantum; `QNC.V` Quantum eMotion |
| infra_hw | B | partial | `688027.SS` QuantumCTek; `IFX.DE` Infineon Technologies; `NXPI` NXP Semiconductors; `RMBS` Rambus; `STM` STMicroelectronics |

Ring A listings whose status is not plain `listed` at registration:

| ticker | name | status | date |
|---|---|---|---|
| `VRNS` | Varonis Systems | pending_acquisition | 2026-10-15 |

Ring F, delisted or acquired, in no test. The date is that of the event that set the status:

| ticker | name | status | date |
|---|---|---|---|
| `JAMF` | Jamf Holding | delisted | 2026-01-30 |
| `WITH.HE` | WithSecure | delisted | 2025-11 |
| `CYBR` | CyberArk Software | acquired | 2026-02-11 |
| `DARK.L` | Darktrace | delisted | 2024-10-01 |
| `BEZ.L` | Beazley | delisted | 2026-10-02 |
| `EXN.PA` | Exclusive Networks | delisted | 2025-03-07 |

Considered and left out: `ANET`, `NTAP` and `DT`. Cybersecurity ETFs hold them, but they are a switching vendor, a storage
vendor and an observability vendor without a security line the page can place.

### Pinned measurement choices

- **What is kept.** The capture reads Yahoo and East Money and writes the parsed fields to `data/snapshots/cyber/consensus/`. It writes no raw payload for either
  source, and `.gitignore` keeps the daily price and weekly estimate modules' payloads for these tickers out of the repository. That is narrower than it sounds.
  Yahoo's terms restrict building a database of its data. The weekly table holds Yahoo's values as served (price, enterprise value, target prices, rating counts, revenue
  consensus), so it is a store of that kind, and the daily and weekly valuation tables hold these tickers' Yahoo prices and EPS too. This lowers the footprint and does
  not bring the repository inside the terms. A verdict uses the tables as committed, and a parser error is corrected by an amendment that names it, not by re-reading an archive. The valuation job still
  archives East Money payloads for the 15 A-share listings, as it does for the earlier tracks. The first formal snapshot is the first one in which every field named in this
  list is stored. Yahoo supplies price, enterprise value, target prices, the rating distribution, the revenue consensus for fiscal year 0 and +1, trailing revenue and free
  cash flow, and fiscal-year dates. East Money supplies the A-share revenue consensus by calendar year and the six-month rating counts. A null East Money rating
  count means it printed none, and the page reads it as zero only where it says so.
- **Enterprise value** is Yahoo enterpriseValue. Yahoo states it in the quote currency (pence read as pounds) and the revenue consensus in the financial currency. A multiple is
  computed only where the two agree. At registration `BTQ`, `CNS.L` and `KUD.SW` differ and are shown without a multiple; none is in a registered list. The United States dollar
  value of an enterprise value, used only for the $1B to $10B cutoff of Q8, uses the baseline rate JPY 157.99 per dollar for the one yen company and 1 for dollar companies.
- **Next-twelve-month revenue.** `w = days from the snapshot to the end of fiscal year 0, divided by 365, held between 0 and 1`, applied as `w` times the fiscal year 0 consensus plus `1 - w` times the
  fiscal year +1 consensus. The end of fiscal year 0 is Yahoo nextFiscalYearEnd. Fiscal year 0 is the first year whose annual report Yahoo has not
  yet processed, so a December company still carries 2026 as year 0 in January 2027, when `w` is 0 and the next twelve months are
  year +1. For A-shares the consensus is East Money's by calendar year, with the fiscal year ending on 31 December. An estimate row with no analysts behind it is a missing value.
- **Growth, FCF margin, Rule of 40.** `growth = NTM revenue / trailing-twelve-month revenue - 1`, forward over trailing, so it spans more than twelve months for a company a quarter past its last report. `FCF margin = trailing-twelve-month free cash flow / trailing-twelve-month revenue`.
  `Rule of 40 = 100 x (growth + FCF margin)`. Trailing figures are Yahoo totalRevenue and freeCashflow as served,
  for every listing including the A-shares. A company missing either figure is dropped from the panel and the page counts the drops.
- **Target upside.** `upside = targetMeanPrice / price - 1`, in the quote currency (pence divided by 100 first). **Dispersion.** `dispersion = (targetHighPrice - targetLowPrice) / targetMeanPrice`.
- **Returns and volatility** for Q4 and Q5 come from Yahoo's daily history, adjusted for splits and dividends, fetched at grading
  time, in the listing's quote currency, on the company's own exchange calendar (the last trading day on or before each date). The committed outputs are the derived returns and
  volatilities, not the history. Upside is measured against the price stored in the snapshot.
- **Thin coverage.** Fewer than 3 analysts: the company is drawn faded and flagged.
- **China.** The A-share group for Q7 is the frozen list below. A-shares are left out of Q4 and Q5 by design. East Money's report-list target field is empty for cybersecurity,
  and Yahoo's target for an A-share rests on one to nine analysts; the two sources are not comparable, so the Yahoo targets are stored and used in no item. A-share company medians on the page use
  East Money's consensus and only purity `high` with at least 2 East Money 2026E brokers, and
  are shown only when that gives at least 5 companies. The rating window is 6 months.
- **The panel** (Q1, Q2, Q3, Q7). The weekly window is the 12 Monday snapshots from 2026-10-26 to 2027-01-11. A Monday on which fewer than 80% of
  ring A was priced is not a snapshot, and if a Monday has more than one qualifying run the last is used. With fewer than 10 snapshots in the window, Q1, Q2, Q3 and Q7 are `undecidable`. A company is in the panel if its
  multiple, growth and FCF margin are all computable in at least 75% of the snapshots there are (9 of 12). Its values are the means over every snapshot
  in which they are computable, of log(EV/NTM revenue), growth and FCF margin, and each question fits one regression on those means. A median of multiples is a median of the exponentials of those mean logs.
  The weekly regressions are drawn on the page as a stability chart and do not count toward a verdict. Layer groups are the layers in the universe table, with any layer of
  fewer than 3 panel members merged into one `other` group, and an `other` group of fewer than 3 joining the largest layer. In a bootstrap draw a company keeps its group label and
  groups are not merged again; a permutation shuffles those labels. Four groups and two minimums sit at their thresholds on the baseline: `secops` has 3 of 3 members, the Rule of 40 group below 40 has 6 of
  a minimum 5, the Q7 A-share group has 6 of 5, and `300188.SZ` has 2 of the 2 brokers its qualification needs. One corporate event can change a status, and the page shows the panel count every week.
- **Ring C and private valuations.** Ring C is built only from public sources: company announcements, regulatory filings and
  named public databases. A company with no public source is not in the repository, not even by name. A private valuation is
  three numbers, never one. The headline is what a round or a deal reported. The discount interval applies the discount for the
  year of the round (below), with a band of plus or minus 15 percentage points; a round in a year not in the table is not scored. The public-comparable interval runs from the lowest to the highest
  EV/NTM revenue among the comparables in the table below, multiplied by company-stated ARR as given, although ARR is a trailing run-rate and EV/NTM is forward, which favours
  `above` in Q6. The discounts are an assumption, not data, and the page says so.

| year of the round | discount to the headline |
|---|---|
| 2021 | -60% |
| 2022 | -55% |
| 2023 | -35% |
| 2024 | -15% |
| 2025 | -5% |
| 2026 | 0% |

| layer of the private company | public comparables |
|---|---|
| network_edge | `ZS`, `NTSK`, `FTNT` |
| identity | `OKTA`, `SAIL` |
| cloud_appsec | `NET`, `RBRK`, `ZS` |
| endpoint | `CRWD`, `S` |

- **Closed `metric_kind` vocabulary** for the private ledger: `primary_round_valuation`, `round_amount`, `tender_valuation`, `arr_company_stated`, `acquisition_price_announced`, `acquisition_price_sec`, `take_private_price`, `secondary_mark`, `reported_talks`, `third_party_estimate`, `derived`. Rows of kind
  `secondary_mark`, `reported_talks`, `third_party_estimate` are shown in a table and never charted.
- **Status vocabulary** for every company, listed or private: `independent`, `pe_owned`, `pending_acquisition`, `acquired`, `listed`, `delisted`.
- **Leaving the pool.** A company that is acquired or taken private keeps its history and leaves ring A the day its status
  changes; it is not deleted. The daily price job returns an error for a ticker that no longer trades, so the move to ring F is made the day the delisting happens.

### Frozen lists and populations

`pipelines/cyber/frozen.py` computes each of these from the baseline snapshot and the pool as registered, and `pipelines/cyber/tests/test_frozen.py` fails if a
list here differs from its result.

- **Q1, Q2, Q3, Q7 and Q9 population** (a rule, re-counted every week): ring A, listed on a US exchange, purity `high`, status
  `listed`, a multiple that can be computed, and FY0 and FY+1 revenue consensus each from at least 3 analysts. On the baseline date it is
  17 companies: `CHKP, CRWD, FTNT, MITK, NET, NTSK, OKTA, OSPN, PANW, QLYS, RBRK, RPD, S, SAIL, TENB, TLS, ZS`. The study design estimated 35 to 45. The count falls short, which is why the minimum below is
  15 and not the 25 the design had, a number set after the count was seen. Three drop-outs make a panel short of that minimum.
- **Q4 and Q5 list** (ring A, outside China, status `listed`, at least 3 target-price analysts on the baseline
  date; 34 companies): `4704.T, AKAM, ALLT, ATEN, BB, CGNT, CHKP, CRWD, CSCO, CVLT, FFIV, FSECURE.HE, FTNT, GBG.L, GEN, IDN, MITK, MSFT, NCC.L, NET, NTSK, OKTA, OSPN, PANW, QLYS, RBRK, RPD, S, SAIL, TENB, TLS, YSN.DE, YUBICO.ST, ZS`. Ten of them are purity `main`.
- **Q7 A-share group** (ring A, purity `high`, at least 2 East Money brokers for 2026 on the baseline date;
  6 companies): `002212.SZ, 002439.SZ, 300188.SZ, 300369.SZ, 688489.SS, 688561.SS`. The group does not change. A member is in the panel if its multiple and growth are computable in at least 75% of the snapshots there are.
  `300454.SZ` (Sangfor) is purity `main` and is not in it. The US side of Q7 is the Q1 and Q2 panel.
- **Q8 list** (held by the CIBR ETF on 2026-10-15 as listed in `data/reference/cyber/cibr_holdings_2026-10-15.json`, purity
  `high`, status `listed`, enterprise value between $1B and $10B; 6 companies):
  `4704.T, NTSK, QLYS, RPD, S, TENB`. The ETF's published holdings are the operational definition of membership in the Nasdaq CTA Cybersecurity
  Index, which the fund tracks. `VRNS` is excluded because Proofpoint signed a merger agreement for it on 2026-10-15 (enterprise value $5.4B),
  and `SAIL` because its enterprise value is $10.4B and the ETF does not hold it. `RPD` is $1.11B and
  `RDWR` $0.94B, so `RPD` is in and `RDWR` is out; the list does not change after registration.

### Items

**Q1 Layer explains the multiple beyond growth.**
- Claim: on the panel, adding the L1 layer groups to the regression of log(EV/NTM revenue) on growth raises adjusted R² by at
  least 0.10. The uplift is adjusted R² with growth and layer groups minus adjusted R² with growth alone, on the panel means.
- Holds: the uplift is at least 0.10 and a permutation test of the layer labels across companies gives a one-sided p of at
  most 0.10.
- Falsified: the upper end of the 90% bootstrap interval for the uplift is below 0.10.
- Undecidable: the panel has fewer than 15 companies, or neither condition is met.
- Graded: 2027-01-15, with the weekly stability chart beside it.
- Already seen: 17 companies in the groups `network_edge` 6, `other` 4, `identity` 4, `secops` 3; `other` is `CRWD`, `RBRK`, `S`, `TLS`. Growth alone has an adjusted R² of 0.75.
  Adding the layers changes it by -0.05, the permutation p is 0.94, and the 90% bootstrap interval for the uplift is -0.06 to +0.12. Applied to the baseline, the rule reads `undecidable`.
- Expected: at this size the interval is about 0.18 wide, so Falsified needs an observed uplift near -0.08 or lower. In simulation, if the layers carry nothing the item ends falsified in 10% of runs and
  `undecidable` in 85%; if the layers carry 30% of what growth leaves unexplained it ends `holds` in 55%. Expect `undecidable`, with an uplift near zero or below, and the page prints the uplift and the permutation p whatever the status.

**Q2 Growth explains more of the multiple than free cash flow does.**
- Claim: on the panel, the regression log(EV/NTM revenue) = a + b·growth + c·FCF margin has R² of at least 0.5, b is
  positive, and the standardised coefficient on growth is larger in absolute value than the one on FCF margin.
- Holds: R² is at least 0.5, the 95% bootstrap interval for b lies wholly above zero, and the 95% interval for
  the standardised growth coefficient minus the standardised FCF coefficient, both in absolute value, lies wholly above zero.
- Falsified: the 95% interval for b lies wholly at or below zero, or the interval for the difference lies wholly below zero.
- Undecidable: the panel has fewer than 15 companies, or neither condition is met.
- Graded: 2027-01-15.
- Already seen: R² 0.84. The interval for b is 1.1 to 6.1. The standardised coefficients are 0.60 for growth and
  0.39 for FCF margin, a difference of +0.21 with an interval of -0.65 to +0.67. The first two parts of Holds are met
  and the third is not, so the rule reads `undecidable` on the baseline. Meritech Capital's July 2026 Software Pulse, which reports that growth is about 3.8 times as correlated with
  the ARR multiple as NTM FCF margin is on public SaaS companies, is the pattern this item looks for on cybersecurity companies. Its 2026 readings ran from 2.8 to 3.8. The item does not test that ratio.
- Expected: growth and FCF margin correlate at 0.70 in this population, so the interval for the difference is about 1.3 wide. In simulation with the baseline coefficients as the truth, the item ends `holds` in 15% of runs and
  `undecidable` in 85%. Expect `undecidable`.

**Q3 The market pays for Rule of 40.**
- Claim: on the panel, the median EV/NTM revenue of companies with Rule of 40 at or above 40 is at least 2 times
  the median of those below 40.
- Holds: the ratio of medians, on the panel means, is at least 2.
- Falsified: the 95% bootstrap interval for the ratio lies wholly below 2. Each group is resampled on its own.
- Undecidable: either group has fewer than 5 companies, or neither condition is met.
- Graded: 2027-01-15. Later quarter-end readings are shown beside it and do not replace it.
- Already seen: 11 companies at or above 40 with a median of 8.8x and 6 below with a median of 2.8x, a ratio of
  3.2. The 95% interval is 2.0 to 10.2, so the lower end sits on the threshold. The group below 40 is `CHKP, MITK, OSPN, RPD, TENB, TLS`; its six members are one above the minimum of 5, and `RPD`, one of them, has a reported sale process. The rule reads `holds` on the baseline.
- Expected: Holds is a point estimate and Falsified needs an interval, which leans the item toward `holds`, and the threshold was set below what was seen. In simulation with the baseline's group sizes and spread, a true ratio of 1.5, which would make the claim false, ends `holds` in 20% of runs and `falsified` in 10%; a true ratio equal to the baseline's ends `holds` in 90%.

**Q4 Target-price upside does not predict the next twelve months' return.**
- Claim: across the Q4 and Q5 list, the rank correlation between target upside at the 2026-10-26 snapshot and the total return from the close of 2026-10-26 to the close of 2027-10-25
  is below +0.35, as the upper bound in Holds defines it. A company acquired or delisted inside the window is scored at its return to its last trading day, including any deal price, and is flagged. It is never dropped.
- Holds: the one-sided 95% upper confidence bound for the Spearman correlation (Fisher transform) is below +0.35.
- Falsified: the correlation is positive and a one-sided permutation test (10,000 random permutations, mid-ranks for ties) gives p below 0.05.
- Undecidable: fewer than 30 companies of the list have a full window or a deal return, or neither condition is met (the data
  cannot rule out a modest positive relation).
- Graded: 2027-10-26. Quarterly interim readings are shown and not scored. Dispersion and rating changes are shown beside the result and are not part of it.
- Already seen: the median upside across the 34 companies is +13.5%. The lowest are `4704.T` -12.9%, `YUBICO.ST` -11.4%, `RPD` -6.4%. The highest are
  `GBG.L` +80.1%, `AKAM` +80.9%, `IDN` +125.9%.
- Expected: at 34 companies Holds means the sample correlation is below about +0.07, and Falsified means it is above about +0.29. With a true correlation of zero the item ends `holds` in 65% of runs,
  falsified in 5%; with a true correlation of 0.2 it ends `holds` in 25%. The 34 stocks share one 12-month window and sector moves; the bound and the test treat them as independent and understate the uncertainty.

**Q5 Target-price dispersion predicts later volatility.**
- Claim: on the Q4 and Q5 list, dispersion at the start of a window has a positive coefficient for the realised volatility of
  that window, controlling for the previous window's realised volatility, the log of the number of analysts, and a fixed effect for each window.
  Window 1 runs from 2026-10-26 to 2027-01-22 and window 2 from 2027-01-25 to 2027-04-23. Realised volatility is the standard deviation of daily log returns annualised with 252 days, on each company's own trading days. A
  company with a deal announced inside a window is left out of that window. The interval is a pairs bootstrap that resamples companies.
- Holds: on the 2027-04-30 reading, which pools both windows, the coefficient is positive and its 95% interval lies wholly above zero.
- Falsified: the 95% interval lies wholly at or below zero.
- Undecidable: fewer than 30 companies in the pooled sample, or neither condition is met.
- Graded: 2027-04-30. Window 1 alone is read on 2027-01-29 and is not scored.
- Already seen: `CRWD` has a high target of 325, a low of 132 and a mean of 246.2, a dispersion of
  0.78. The regression has not been run.
- Expected: Falsified needs a significantly negative coefficient and is expected to be rare. The code for this regression is registered by an amendment before the first reading.

**Q6 Private headline valuations are above the public comparables even after the discount.**
- Claim: among the scored private companies, at least 50% have a discount interval that lies wholly above the public-comparable interval.
- Scored companies: every company in ring C in a layer that has comparables in the table, with company-stated ARR in a public source and a primary round dated 2021 or later in a year the discount table covers.
  Ring C is built by the rules above, from public announcements and filings. The ledger as it stands on 2026-12-31 is committed with its hash that day and is the list; nobody chooses from it afterwards, and later edits are amendments that do not change this set. A
  company counts as above when the lower end of its discount interval is higher than the upper end of its public-comparable interval,
  with the comparables' multiples read on the 2027-01-11 snapshot.
- Holds: 50% or more of the scored companies are above.
- Falsified: 30% or fewer are above.
- Undecidable: fewer than 10 scored companies, or a share between the two thresholds.
- Graded: on the 2027-01-11 snapshot, once the ledger is committed.
- Already seen: the headline prices of three well-known deals are far above the public range. Google paid about $32B for Wiz (closed 2026-03-11),
  about 32 to 64 times ARR depending on which of two secondary-sourced ARR figures is used. ServiceNow paid $7.75B for Armis, about 22.8 times a reported ARR of more than $340M.
  Netskope, a year after its IPO, is at 7.3x EV/NTM revenue on the baseline date against a population median of 7.3x. Exits
  are recorded as they happen and are described, not scored. The discounts are an assumption, so this item tests the assumption and the market together, and the page says so.
- Expected: the scouting pass found few private companies in the four layers with both a company-stated ARR and a recent primary round, so the item is expected to read `undecidable`. If it is scored, a company is above only if the lower end of its discount interval is higher than its layer's public upper end times its ARR, and those upper ends on the baseline are network_edge 13.6x, identity 8.8x, cloud_appsec 29.8x, endpoint 32.8x.

**Q7 China's discount is not explained by growth.**
- Claim: a China indicator added to the regression of log(EV/NTM revenue) on growth, fitted on the Q7 A-share group and the Q1 and Q2 panel together, is negative. A-share revenue is East Money's consensus and trailing revenue is Yahoo's.
- Holds: the 95% bootstrap interval for the China coefficient lies wholly below zero.
- Falsified: that interval lies wholly above zero.
- Undecidable: fewer than 5 A-share companies in the panel, or neither condition is met.
- Graded: 2027-01-15. The ratio of the A-share median multiple to the US median is shown beside the result and is not graded.
- Already seen: the six A-share companies have EV/NTM revenue of 2.8x to 5.2x and a median of 4.4x, against 7.3x for the 17, a ratio of
  0.60 with a 95% interval of 0.35 to 1.10. Their growth is 18% to 31% against a median of 17% for the 17. The China coefficient is
  -0.98 with an interval of -1.34 to -0.67, so the rule reads `holds` on the baseline. A scouting pass that put the ratio near 0.45 used a looser set, with a one-broker company in the median, and a lower A-share growth figure; it is not the registered statistic.
- Expected: Holds is already met on the baseline and the question is whether it survives twelve weeks. Falsified is out of reach unless the sign reverses. Two A-share drop-outs would leave fewer than 5 and make the item `undecidable`.

**Q8 Consolidation continues (a calibration item).**
- Claim: at least 2 companies on the Q8 list are the subject of a definitive merger agreement or a public tender offer announced
  after 2026-10-16 and on or before 2027-10-15. Reported talks and unsolicited proposals do not count. A deal later withdrawn still counts.
- Holds: 2 or more.
- Falsified: 0 or 1.
- Undecidable: never. The outcome is a count of announcements, which can always be scored.
- Graded: 2027-10-15. A mid-point reading is shown on 2027-04-16.
- Already seen: Varonis meets the same definition (held by CIBR, purity high, enterprise value $5.4B) and signed a merger agreement with
  Proofpoint on 2026-10-15, so at least one same-definition event happened in the registration week. Press reports describe sale processes at
  `RPD` and at `4704.T` (Trend Micro, whose enterprise value is $4.6B at the baseline rate); neither has produced a deal.

**Q9 The AI-tailwind premium does not widen.**
- Claim: the premium of `PANW`, `CRWD`, `NET` over the rest of the Q1 and Q2 population, which is the median EV/NTM revenue of the three divided by the
  median of the rest, is no more than 20% higher at the end than at the start. The start value is the mean of the weekly premium over the
  snapshots of 2026-10-26, 2026-11-02, 2026-11-09, 2026-11-16. The end value is the mean over the four Monday snapshots ending 2027-10-11.
- Holds: the end value is at most 20% above the start value.
- Falsified: the end value is more than 20% above the start value.
- Undecidable: a member of the trio is acquired, or the rest has fewer than 8 companies in either period.
- Graded: 2027-10-12, with quarterly interim readings.
- Already seen: the trio was chosen because it sits at the top of the cross-section, which favours Holds through regression to the mean. On the baseline
  the median of the three is 29.8x (it is `NET`) and the median of the other 14 is 6.1x, a premium of 4.84.
- Expected: the premium rests on one company on one side and is noisy. If every multiple only moves at random over a year (log changes with a standard deviation of 0.3) and the premium has no drift, the end value is more than 20% above the start in about 25% of runs, so Falsified here is weak evidence and Holds is not strong evidence.

**Q10 A revision in revenue consensus tends to be followed by another in the same direction.**
- Claim: for each US-listed ring A company, take the revenue consensus for the fiscal year that is FY+1 on 2026-10-26 and follow that same year, matching by its end year, until it ends. A pair uses the
  snapshots t-1, t and t+4 weeks, and a company's next pair starts at t+5, so no two pairs of one company overlap. A pair is tested when the move from t-1 to t and the move from t to t+4 are both 0.5% or more in size. It is `same` if the two moves have the same direction and `opposite` if not. A pair whose
  second move is under 0.5% is `flat`; flat pairs are counted and shown and are not tested. A missing Monday voids any pair that uses it, and a pair that spans a change of fiscal-year-0 end is void. Same-direction pairs are more common than opposite ones. The test is an exact one-sided binomial test of the same-direction
  count against one half over the tested pairs. A-share companies are shown and do not decide the item.
- Holds: same-direction pairs outnumber opposite ones and the one-sided p is at most 0.10 (the 10% level).
- Falsified: opposite-direction pairs outnumber same-direction ones and the one-sided p is at most 0.10.
- Undecidable: fewer than 30 tested pairs on 2027-10-15, or neither condition is met. Until 30 tested pairs exist the status is `collecting`.
- Graded: on the first Monday snapshot on or after 2027-01-15 with at least 30 tested pairs, and not later than 2027-10-15.
- Already seen: nothing; the 2026-10-26 snapshot is t-1 of the first pair. Pairs are not independent in earnings weeks, when most companies revise together, and the page says so.

### Described, not registered

These are shown on the page and no verdict hangs on them.
- The split of Rule of 40 into growth type (growth above FCF margin) and FCF type. On the baseline only `NET` is a growth type, so the comparison has nothing to compare.
- The returns on 2026-02-20, the day Anthropic announced Claude Code Security, of ring A US names and their six-month return against IGV. The scouting pass saw
  SAIL -9.44%, OKTA -9.18%, NET -8.05%, CRWD -7.95% and ZS -5.47% on the day. The page does not claim the announcement caused them.
- East Money's six-month rating counts, whose neutral, reduce and sell fields were empty for every covered cybersecurity company at the scouting pass. The page reads empty as none and says so.
- The premium of cybersecurity over software generally, in chart notes only.
- Private acquisition prices as recorded in SEC filings against the press-release price, as a method rule for the ledger.
- A-share price-to-sales percentile beside revenue growth.
- Exits (IPOs and acquisitions of private companies) as they happen, with the price multiple of ARR.

### Amendments to this section

- **2026-10-17, sources named for four statements.** Section I was first registered with four statements that name no
  public source: the press reports of sale processes at `RPD` and at `4704.T` (Q8, and the remark on `RPD` in Q3), and the
  Wiz and Armis multiples of ARR in Q6. This entry names a source for each. It changes no threshold, population, list or
  reading, and the statements above stand as registered.
  - `RPD`: Reuters, "Exclusive-Cybersecurity firm Rapid7 explores sale -sources" (Milana Vinn), 1 February 2023, read on
    Investing.com: https://investing.com/news/stock-market-news/exclusivecybersecurity-firm-rapid7-explores-sale-sources-2993353 ;
    and Investing.com, "Rapid7 shares climb on potential sale talks with PE firms" (Senad Karaahmetovic), 28 October 2024,
    which reports preliminary talks with private equity firms about a possible sale:
    https://ca.investing.com/news/stock-market-news/rapid7-shares-climb-on-potential-sale-talks-with-pe-firms-3667663
  - `4704.T` (Trend Micro): Reuters, "Exclusive-Cybersecurity firm Trend Micro explores sale, sources say" (Milana Vinn),
    9 August 2024, as republished by teiss: https://www.teiss.co.uk/news/exclusive-cybersecurity-firm-trend-micro-explores-sale-sources-say-14456 ;
    and Reuters, "Exclusive-Buyout firms vie for cybersecurity firm Trend Micro, sources say" (Milana Vinn), 13 February 2025,
    as republished by ThePrint: https://theprint.in/tech/exclusive-buyout-firms-vie-for-cybersecurity-firm-trend-micro-sources-say/2491850/
  - These reports date from 2023 to 2025. No later report of a sale process at either company was found when this entry was
    written, and neither has announced a deal.
  - Wiz: the 32 to 64 times is $32B over about $1B of ARR or over about $500M. The $1B is TechCrunch (Rebecca Bellan), 11 March
    2026, which says Wiz crossed $1B in ARR in 2025 according to a source familiar with the matter:
    https://techcrunch.com/2026/03/11/google-completes-32b-acquisition-of-wiz . The $500M is TechCrunch (Julie Bort),
    23 October 2024, which attributes it to Wiz co-founder Roy Reznik speaking to CNBC:
    https://techcrunch.com/2024/10/23/wiz-hopes-to-hit-1b-in-arr-in-2025-before-an-ipo-after-turning-down-googles-23b .
    Neither is a figure Wiz published in a filing; both are reported.
  - Armis: the $7.75B price and the ARR of more than $340M are both in ServiceNow's own announcement, dated 23 December 2025 on
    its newsroom (the wire copy's address carries 22 December); the release says Armis "has surpassed $340 million" in ARR:
    https://newsroom.servicenow.com/press-releases/details/2025/ServiceNow-to-acquire-Armis-to-expand-cyber-exposure-and-security-across-the-full-attack-surface-in-IT-OT-and-medical-devices-for-companies-governments-and-critical-infrastructure-worldwide/default.aspx
    This is a figure the company stated, not a secondary one.
