# Cybersecurity page: site contract

Page `site/src/content/projects/cybersecurity-landscape.mdx` (status `in-progress` until the pipeline has run green for
seven days, then `live`); chart builders `site/src/charts/cyber/`; data `data/facts/cyber.json` and
`data/marts/cyber/*.json` (see `PIPELINE_SPEC.md`; run `uv run python -m pipelines.cyber.publish` to rebuild them). The
payments page (`payments-landscape.mdx`, `site/src/charts/payments/`, `site/scripts/payments-*.mjs`,
`tests/test_payments_page_numbers.py`) is the working model of every pattern here; copy its structure, not its words.

## Rules (binding; they are checked)

- `docs/CHART_RULES.md`, `docs/DESIGN.md`, `docs/DEFINITION_OF_DONE.md`: paper ground, serif text, one column with a
  sidenote margin, no cards, pills or brand colours, tokens only (`site/src/charts/theme.ts`, `tok()`, `series.a/b/c`),
  light and dark, phone width without horizontal scroll. Every figure: title = the finding (computed from data at build
  time), subtitle = the metric and unit (say "log scale" when it is), source stamp with an as-of date, no dual axis.
- **No hand-typed number in prose or in title/subtitle/caption attributes**: everything comes from `facts` or the marts
  through expressions. `tests/test_site_numbers.py` and a page-specific strict guard (`tests/test_cyber_page_numbers.py`)
  enforce it. Definitional constants go in an allow list only when they define a method, never a result.
- A ratio with a non-positive denominator is never plotted; a missing multiple is listed with its stated reason. Thin
  analyst coverage fades the mark; exposure (purity) is encoded in the mark, not the colour; markets are told apart by
  shape, not colour; the two rating vocabularies (Yahoo five buckets, East Money counts) are never merged.
- Rows with tag S or U or a never-charted kind never reach a chart or a KPI; `reported_unconfirmed` is a table only.
- No Yahoo payload, no job-description text, no personal data, nothing from an employer document, no vendor data on
  the page. Private companies appear only through curated rows with a public link.
- Honest status: the scoreboard says "collecting" where an item has not reached its first grading date, and the baseline
  readings are shown as "already seen", never as verdicts. The page opens with what the baseline shows, including that
  the layer dummies add nothing to growth in the first cross-section, because that is what the registered numbers say.
- Disclaimer before the first figure: public data only, not investment advice.

## Modules every chart file exports

`<name>Rows(...)` (pure data preparation from a mart), `<name>Spec(rows, opts?)` (a Vega-Lite spec object using
`VL_SCHEMA`), `<name>Finding(rows)` (title string computed from the rows), `<name>Subtitle(rows)` (metric string).
Dashboard controls are exported as arrays shaped like `DashboardControls`' `controls` prop, with a `default` for each.
Large datasets are split to URL-loaded files by `external.ts` as in `charts/payments/external.ts`.

## Figures (v0)

1. **Layer map** (`layer_map.ts`): small multiples, one panel per layer; y = sub-sector where the mart has it, x = listed
   companies by EV/NTM (log scale), ring A solid, ring B grey hollow, A-share its own shape; four counts in each panel
   corner (listed overseas, listed China, private overseas, private China) from `layer_summary`. The caption says how to
   read it: few listed and many private points in a layer means pricing power sits in private rounds and M&A.
2. **EV/NTM against growth** (`ev_growth_scatter.ts`): log y, markers by market, size by enterprise value, `main` grey and
   out of the regression, the fitted line and the five largest residuals named; the subtitle states n and R2 from
   `regression.latest` and says the layers add (or do not add) fit, in the words the numbers support.
3. **Rule of 40** (`rule40_ranked.ts`, own copy; do not import from `charts/saas`): growth against FCF margin with the
   x + y = 40 diagonal, and a ranked bar panel.
4. **Target upside dumbbell** (`target_dumbbell.ts`): one row per listing outside China, implied upside only (no prices:
   currencies differ), low-mean-high whiskers, sorted, thin coverage hollow; A-share Yahoo targets on their own panel
   labelled as Yahoo's.
5. **Ratings** (`recommendation_bars.ts`): two 100% stacked bars side by side, Yahoo five buckets and East Money six-month
   counts, vocabularies not merged, three colour slots only.
6. **Private valuation intervals** (`ledger_intervals.ts`): per company three marks (headline, discount interval labelled
   as an estimate, public-comparable interval), log x, `reported` hollow, stale rounds dashed with their year.
7. **Deal timeline** (`events.ts`): 2025 onward, size = consideration (filing price where there is one), colour = buyer
   type, shape = event kind; announced-to-filing short dumbbell where the two prices differ.
8. **China panel** (`china_panel.ts`): two facets with their own axes: PS_TTM percentile since 2018, and year-on-year
   revenue growth for the same listings in the same order.

## Dashboards (`DashboardControls` + `VegaChart group=...`, URL state, linked table)

1. Valuation explorer: layer, market, purity, ring, coverage; figures 1, 2, 3; table of every listing (filter by data
   attributes). 2. Targets and ratings: layer, market, coverage; figures 4, 5; table. 3. Private ledger and events: layer,
   company, market, kind, basis; figures 6, 7; the long ledger as a lazy `UrlTable` over `/data/marts/cyber/ledger_timeline.json`.
Unsupported combinations show one sentence with the reason, never an empty chart.

## Page order

Takeaway (facts only) and the three KPIs (`facts.kpis`, exactly three) -> disclaimer -> the scoreboard of the ten items
(`facts.scoreboard`, status words from `config.QSTATUS`) -> valuation dashboard -> targets and ratings dashboard ->
China (plus a short "for a Western reader" note: project-based box sales rather than subscriptions, state buyers, the three
compliance drivers, why ratings carry no information, state-owned parents) -> private companies and events -> tables
(every listing, excluded with reasons, reported-unconfirmed, ETF cross-check, China listed) -> **How the data is built**
(generated DAG svg, one short paragraph per source with fetch cadence, cleaning and known bias, the filterable metric
dictionary, data quality, reproducibility, limits) -> `<Appendix>` with Method, Evaluation (section I of
`docs/EVALUATION_PLAN.md`, hash from `@lib/prereg`), Data quality (`facts.checks`), Sources and data rights (Yahoo in its
own paragraph: what is kept, that it is not a raw archive, that the terms restrict this kind of store), Build notes (what the
AI executed, what the author decided), Changelog.

## Registries

`site/src/lib/cardKpis.ts` (one headline number read from facts), `minicharts.ts`, `changelog.ts` entry, README table,
`docs/DATA_MODEL.md`. `freshness.ts` registration only when the page is `live` (the test blocks it otherwise).
`.github/workflows/cyber.yml` and the `deploy-site.yml` trigger already exist.

## Open items before `status: live`

Known, small, and none of them changes a number on the page. Each is checked before the page is switched to `live`.

- **NCC Group (`NCC.L`) free-cash-flow margin is held back** (`metrics.FCF_HELD_BACK`). Yahoo's trailing free cash flow
  gives -78% of revenue, out of scale against its EV; the company stays in every table, its margin and Rule of 40 are
  empty and its note says why. Remove the entry once the figure has been read from the filings.
- **Ledger intervals figure ships its data inline** (about 125 KB of the 454 KB page) because the empty-view sentence reads
  the dataset by name with `indata`. A small named dataset would let it load from a file like the other figures.
- **Console warnings from Vega-Lite** on four figures (`fit-y` dropped for a discrete height, faceted independent scales
  from different forks of one source, an infinite extent in an empty facet). They change nothing on screen.
- **`prereg.ts`** exports the hash of section I; it does not yet export the registration commit.

## Owner decisions answered 2026-10-19

No conflict of interest (the page says exactly that, once, in the disclaimer); the company list comes from public sources only;
the parsed Yahoo fields may stay in the public git history (the baseline table already is).

