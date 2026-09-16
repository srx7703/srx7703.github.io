# Chart rules

1. Title states the finding ("Both platforms price a Democratic House at ~86%"); subtitle states the metric, unit and window.
2. One palette, defined in `site/src/styles/tokens.css` and mirrored in `site/src/charts/theme.ts`:
   platforms — Polymarket, Kalshi; parties — Democratic, Republican; neutral greys for context.
3. Probabilities on a 0–100% axis; never truncate without a note in the subtitle.
4. Default view readable without hover; tooltips add precision only.
5. Source + "data as of" stamp on every chart (the `SourceStamp` component).
6. Mobile first: stack, wrap legends, no horizontal page scroll.
7. Light and dark: transparent chart background, text/grid colours from tokens, no pure black or white.
8. Event markers (election day, FOMC dates) share one style: dashed rule + small label.
9. Platform colours (Polymarket violet, Kalshi aqua) and party colours (Democratic blue, Republican red)
   are separate encodings and never share one chart: validated as adjacent pairs in both modes
   (`scripts/validate_palette.js` from the dataviz skill, 2026-09-16); the blue–violet pair is too close
   for an all-pairs form. On the paper surface (#f2f1ec, 2026-09-16 restyle) Kalshi green was re-stepped
   to #15966a so it clears 3:1; every chart still ships a legend and a table view.
10. Non-semantic series use the generic slots `--series-a/b/c` (reference palette slots 1–3, validated
    all-pairs in both modes; b and c re-stepped to #e0602a / #15966a for the paper surface). FOMC direction uses the diverging pair cut = blue, hike = red with the
    neutral grey as the hold midpoint; the five bps buckets are rolled up to three sides on charts
    because two steps of one hue cannot clear the dark-mode floor.
11. Figures carry no card chrome: title and subtitle above the plot, source stamp below, hairline rules only
    where a table needs them. Two-panel figures use `<ChartCard size="full">` so they run across the sidenote
    margin; chart text uses the sans token (`--font-sans`), never the serif. See `docs/DESIGN.md`.
