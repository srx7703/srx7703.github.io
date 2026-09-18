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
