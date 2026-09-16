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
Registered 2026-09-16, before the first snapshot was published. All five items are scored; the
first three need at least two more quarters of snapshots before they say anything.

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

Scoring code lives in `pipelines/valuation/evaluate.py` and writes to
`data/marts/valuation/evaluation.json`. Until a scoring date arrives it returns an empty result and
the pages say the evaluation has not run yet, rather than showing a placeholder number.
