# Cybersecurity page: pipeline contract

Slug `cybersecurity-landscape`; pipeline `pipelines/cyber/`; facts `data/facts/cyber.json`; marts `data/marts/cyber/`.
Section I of `docs/EVALUATION_PLAN.md` is binding: definitions, populations and the item rules are code in
`config.py`, `frozen.py`, `stats.py`, `readings.py`; never restate them. Rules of the repository (`CLAUDE.md`): numbers in
prose come from facts only, pandera validates before every write, a failing source never loses the others, no secrets.

## Inputs

| input | where | notes |
|---|---|---|
| consensus runs | `data/snapshots/cyber/consensus/*.parquet` | `metrics.load_runs()`; a run is usable if 80% of ring A is priced; the baseline (2026-10-17 file) is a run but never part of a series; weekly snapshots are the last good run of each Monday (`readings.weekly_snapshots`) |
| pool | `pipelines/cyber/pool.py` (live), `data/reference/cyber/pool_registered.json` (as registered) | classification only |
| curated ring C | `data/reference/cyber/{private_directory,private_metrics,events}.json` | `reference.rows(kind)` validates and strips `curator_notes` |
| CIBR holdings | `data/reference/cyber/cibr_holdings_2026-10-15.json` | tickers and names only |
| East Money A-share valuation history | fetched by `valuation_history_em.py` | PS_TTM, PB, PE history by day since 2018 for the A-share ring A and B listings |

## Modules and their entry points

Every module has pure functions that take frames/dicts and return frames/dicts (no I/O, no clock), a pandera schema in
`schema.py`, and tests with fixtures under `pipelines/cyber/tests/`.

| module | entry | produces |
|---|---|---|
| `metrics.py` (exists) | `listing_rows(df, as_of)` | one row per listing, every ring |
| `regression.py` | `build(runs, today)` | `latest` {n, r2, adj_r2, adj_r2_no_layer, slope, slope_ci, fcf_coef, china_dummy, as_of, population}, `series` (one entry per usable Monday, same fields), `residuals` (per company: ticker, ev_ntm, fitted, residual, top5 flag), `premium` {ai_trio_median, rest_median, ratio, as_of}, `premium_series`. Uses `readings.build_panel` / `stats` for the registered estimators, so a weekly point and a verdict agree |
| `ledger.py` | `build(directory, metrics, events, multiples)` | `private_intervals` (per company: headline, discount interval, public-comparable interval; each with value, qualifier, basis, as_of, source_url, vintage_year, stale_mark, tag), `ledger_timeline` (all published metric rows, long), `ledger_latest`, `reported_unconfirmed` (every non-chartable row with the reason; never charted), `events` (chartable events; announced price and SEC price in two columns; buyer type), `private_summary` |
| `valuation_history_em.py` | `build(history, listings)` and a `main` that fetches | `china_panel`: per A-share listing PS_TTM, its percentile since 2018, 2026H1 revenue growth year on year (East Money), market cap, audit-opinion/ST flags only if curated |
| `etf.py` | `build(cibr, pool, excluded)` | `etf_crosscheck`: CIBR holdings in the pool by ring, in the excluded list, or missing (a failed check if missing) |
| `evaluate.py` | `score(runs, ledger, today)` | `scoreboard`: Q1..Q10 `{id, title, status, reason, graded, next_grading, already_seen, evidence}`; status `collecting` until the item's first grading date, then the `readings` result; the baseline readings are `already_seen` |
| `publish.py` | `main(--dry-run)` | all marts + `data/facts/cyber.json`; regression guard (a run that would publish fewer than half the listings of the existing facts does not overwrite; a failed check is recorded in the existing facts); `facts.checks` |

## Marts (`data/marts/cyber/*.json`, list-of-records unless stated)

`listings` (all 186 rows from `metrics.listing_rows`), `layer_summary` (per layer and L2: counts of listings by ring and
market group, private companies, median EV/NTM of the registered population), `regression` (object: latest, series,
residuals, premium), `targets` (ring A outside China with a target: upside, dispersion, n_opinions, thin, target
mean/high/low, quote currency), `recommendations` (Yahoo five-bucket counts per listing and pooled; East Money 6-month
counts per A-share listing and pooled; the two vocabularies are never merged), `china_panel`, `private_intervals`,
`ledger_timeline`, `ledger_latest`, `events`, `reported_unconfirmed`, `etf_crosscheck`, `excluded`, `scoreboard`,
`metric_dictionary`, `kpis`.

## Facts (`data/facts/cyber.json`)

Every number the page prints is in facts with `value`, `qualifier`, `unit`, `period`, `as_of`, `source_url` (the shape of
`payments.publish.values`). Top-level keys: `generated_at`, `as_of`, `registered` (the section-I commit date),
`scope{n_layers, n_subsectors, n_listed, n_private, n_markets, by_ring{}, by_layer{}}`, `universe{etfs[], excluded[]}`,
`regression{...latest + premium}`, `ev_sales{n_with, n_refused, refused_reasons[], median, p25, p75, by_layer{},
pure_play_median, us_pure_play_median, cn_median, cn_us_ratio, ev_source}`, `rule_of_40{n_scored, n_pass, median_score,
leaders[3], laggards[3]}`, `targets{n_with_target, n_without, n_thin, median_upside, p25_upside, p75_upside, by_layer{},
by_market{}, most_upside{}, least_upside{}, n_gbp_pence_normalised, as_of}`, `recommendations{yahoo{}, eastmoney{}}`,
`cn{n_listed, median_ps_ttm, ps_percentile{}, n_with_em_consensus, n_with_yahoo_target}`, `private{n_directory,
n_ledger_rows, n_chartable, n_reported_unconfirmed, n_undisclosed, n_stale, by_layer{}, latest_valuation_max{company,
value, qualifier, basis, as_of, source_url}, n_with_arr}`, `events{n, by_kind{}, by_buyer_type{}, acquisitions_12m,
n_sec_priced, latest{}}`, `kpis[3]` (exactly three: pure-play median EV/NTM, median target upside, highest private
valuation round, each tag V or C), `scoreboard[]`, `counts{}`, `coverage{}`, `checks[]`, `sources[]`, `marts{}`.

## Checks (recorded in `facts.checks`, shown on the page)

Row counts per ring against the pool; every priced ring A listing has a price; price age; consensus coverage; a multiple
missing for a stated reason (never silently); target price and quote currency agree (pence); `n_opinions` (target
analysts) is not mixed with EPS analysts; target mean moved more than 30% in a week; analyst count halved in a week;
East Money rating rows that are wholly null; curated files validate and are fresh (90 days); no `curator_notes` in any
mart; no row from a blocked source host; regression population equals `frozen.q1_population`; CIBR cross-check complete;
every figure in a chartable mart has tag V or C.

## Rights

No Yahoo payload in any mart (parsed fields only, as in the capture table). No vendor data, no job text, no personal
data, nothing from an employer document. Private companies appear only through curated rows with a public source.
