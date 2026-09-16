# Site design

The site reads like a set of research notes, not a dashboard product. Three references, re-implemented in
this repo's own CSS (nothing copied): the paper-and-serif look of the Dante Astro theme, Tufte's sidenote
layout, and Distill's article header and appendix.

## Tokens (`site/src/styles/tokens.css`)

- Ground `#f2f1ec`, muted surface `#eae9e1`, ink `#171717`; dark theme inverts to `#171717` / `#232320` / `#f2f1ec`.
  Links are ink with a dashed underline; there is no brand colour. `--positive` / `--negative` are for checks only.
- Type: Newsreader (variable, optical sizes) for headings and running text, IBM Plex Sans for bylines, labels,
  tables and chart text, the system mono for code. Both families are self-hosted via `@fontsource-variable`.
- Series colours are unchanged in role (Polymarket violet, Kalshi green, Democratic blue, Republican red,
  generic a/b/c, cut/hold/hike) and were re-validated on both surfaces; see `docs/CHART_RULES.md`.

## Layout (`site/src/styles/global.css`)

- One text column (`--col`, 46rem) with a right margin (`--margin`, 19rem) for sidenotes; the page container is
  their sum (68rem). Below 72rem the margin folds into the column: sidenotes become indented notes, full-width
  figures become 100%. Project rows on the home and list pages use the margin for the latest headline number.
- Header: serif nav row with the theme toggle, then the masthead (name + one line). Footer: dashed rule.
- Project pages: `[slug].astro` renders the Distill-style header (status line, title, dek, byline with author,
  start date, tools and the evaluation-plan blob hash for live projects) and a citation footer.

## Components

| Component | Use |
|---|---|
| `ChartCard` | A figure: title = finding, subtitle = metric; `size="full"` spans the margin (two-panel charts). Stamp goes in the `stamp` slot. |
| `KpiRow` / `KpiTile` | The key-figures row: label, value, qualifier, ruled top and bottom. Three items, never more. |
| `Sidenote` | Numbered note in the margin; inline, right after the phrase, on the same line as the text. |
| `MarginNote` | Unnumbered margin note (a caveat, a source). |
| `Appendix` | Wraps Method, Evaluation, Data quality, Build notes, Changelog; smaller type after the results. |
| `Section` | Titled section with an anchor; `wide` lets a big table span the margin. |
| `PipelineStatus` | One plain line: data as of, snapshot count, cadence, Actions badge. |
| `ProjectPreview` | A project row on the home and list pages: title, status line, summary, latest headline number and a `MiniChart`. |
| `MiniChart` | 220 x 60 inline SVG drawn at build time from marts/facts (`lib/minicharts.ts`); colours are series tokens. |

## Photos

Sources are exported by hand (no EXIF, colour at 88%) to `site/public/photos/` as JPEG + WebP at 1x and 2x, with
width and height set on the `<img>`. Places: a 56px avatar in the masthead, one wide photo under the home intro, a
margin portrait and one column-width photo on About. The home photo's right 15% was outpainted (Bria) to centre the
subject; the rest is unedited.

## Not allowed

Cards with borders and radius, pill badges, KPI tiles, three-column feature grids, gradient or coloured heroes,
tech-stack tag strings in headers, emoji as section markers, Inter as a body face. If a new element needs
separation, use a hairline rule or white space.
