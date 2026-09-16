---
name: chart-review
description: Review a Vega-Lite spec or a rendered chart screenshot against this portfolio's chart rules (title states the finding, subtitle states the metric, source + as-of stamp, token palette, light/dark legibility, mobile layout) and return a concrete fix list. Use before any chart ships, when the owner asks to "review the chart", "check the viz", or after a design pass.
---

# chart-review

Read `docs/CHART_RULES.md`, then check the spec/screenshot against every rule and return a
numbered fix list ordered by severity. Do not rewrite the chart unless asked.

Checklist:
1. Title is a sentence that states the finding; subtitle names the metric, unit and window.
2. Colors come from the token palette; platform/party colors are consistent site-wide.
3. Axis labels are human units (%, $M, dates), no raw column names.
4. Default view is readable with no hover; tooltips add detail, never carry the message.
5. Source and "data as of" stamp present.
6. Works at 360px width (stacked layout, no horizontal page scroll).
7. Light and dark both legible: no pure black/white, contrast >= 4.5 on text.
8. No misleading encodings: probabilities on 0–1 or 0–100% axes, no truncated axes without a note.
