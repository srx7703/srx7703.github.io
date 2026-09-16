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
   for an all-pairs form. Kalshi aqua is below 3:1 on the light surface, so every chart using it ships
   a legend and a table view.
10. Non-semantic series use the generic slots `--series-a/b/c` (reference palette slots 1–3, validated
    all-pairs in both modes). FOMC direction uses the diverging pair cut = blue, hike = red with the
    neutral grey as the hold midpoint; the five bps buckets are rolled up to three sides on charts
    because two steps of one hue cannot clear the dark-mode floor.
