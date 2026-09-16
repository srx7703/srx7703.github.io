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
