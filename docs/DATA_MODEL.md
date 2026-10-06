# Data model — prediction-market snapshots

Pipeline: `pipelines/predmarkets/snapshot.py`. Runs via GitHub Actions
(`snapshot-predmarkets.yml`): a **full** snapshot at 06:17 UTC (universe quotes + tier-1 order
books) and a **tier1** snapshot at 18:17 UTC (quotes + books for tracked markets only), plus
manual dispatch.

## Sources (all public, no API key)

| Platform | Endpoint | Used for |
|---|---|---|
| Polymarket Gamma | `GET /events?tag_id=&closed=false&limit=100&offset=` | universe discovery + market-level quotes (`outcomePrices`, `bestBid/Ask`, `volume`, `volume24hr`, `liquidity`) |
| Polymarket Gamma | `GET /events/slug/{slug}` | headline events |
| Polymarket CLOB | `GET /book?token_id=` | tier-1 order books (YES token) |
| Polymarket CLOB | `GET /prices-history?market=&interval=&fidelity=` | backfill (not used by the snapshotter) |
| Kalshi | `GET /events?status=open&with_nested_markets=true&limit=200&cursor=` | universe scan / per-series events |
| Kalshi | `GET /markets/{ticker}/orderbook?depth=` | tier-1 order books |
| Kalshi | `GET /series/{series}/markets/{ticker}/candlesticks` | backfill (not used by the snapshotter) |
| Kalshi | `GET /historical/cutoff` | `market_settled_ts`: markets settled before it (about two months back) are served only by `/historical` |
| Kalshi | `GET /historical/markets/{ticker}/candlesticks` | backfill of archived markets (fields `price.close`, `volume`, `open_interest`) |
| Kalshi | `GET /historical/markets?event_ticker=&cursor=` | results of archived events, which `/events?status=settled` lists without markets |

Tag ids (Polymarket, discovered 2026-09-15): midterms=102289, senate-midterms=104093,
governor-midterms=104094, fomc=100478, fed-rates=100196.

## Market sets

- `midterms`: Polymarket universe = tag *Midterms* (~1.2k events / ~11.5k markets). Tier-1 =
  headline events (balance of power, House/Senate control, seat counts, governor count) + all
  state Senate/Governor winner events. Kalshi universe = category *Elections* with any market closing in
  [2026-11-01, 2027-12-01) — Kalshi closes 2026 race markets on 2027-11-03, a year after
  election day. Tier-1 = control/seat-count series + state race series
  (`SENATEXX`, `SENATEPARTYXX`, `GOVPARTYXX`, `KXSENATEXX`, `KXGOVXX`, `HOUSEXXnn`); the list
  discovered by the last full run is saved to `kalshi_tier1_series.json` for tier1 runs.
- `fomc`: Polymarket tags *fomc* + *Fed Rates*; Kalshi series `KXFEDDECISION`, `KXFED`, ...

## Tables

`data/snapshots/predmarkets/<set>/quotes/<date>/<HHMM>.parquet` — one narrow row per market
per run (numbers only; join to the dimension on `platform, market_id`).

| column | meaning |
|---|---|
| `snapshot_ts` | run timestamp (UTC ISO) |
| `platform` | `polymarket` / `kalshi` |
| `market_id` | Polymarket numeric market id / Kalshi market ticker |
| `event_id` | Polymarket event id / Kalshi event ticker |
| `yes_price` | platform-displayed YES price (Polymarket `outcomePrices[0]`, Kalshi last price) |
| `best_bid`, `best_ask`, `mid`, `last_trade` | top of book in YES terms |
| `volume`, `volume_24h`, `liquidity`, `open_interest` | activity (USD / contracts) |
| `closed`, `active` | status flags |

`data/snapshots/predmarkets/<set>/dim_markets/<date>/<HHMM>.parquet` — dimension **deltas**:
only markets that are new or whose attributes changed that run (`event_slug`, `event_title`,
`series`, `market_slug`, `question`, `outcome_yes`, `condition_id`, `yes_token`, `no_token`,
`end_date`, `tags`) with `first_seen` and `valid_from`. Consolidate with
`pipelines.predmarkets.read.dim()` (latest `valid_from` per key).

`data/snapshots/predmarkets/<set>/books/<date>/<HHMM>.parquet` — tier-1 order-book summaries:
best bid/ask, spread, mid, level counts, and depth within 5c and 10c of the touch on each side
(shares/contracts and USD).

`data/raw/predmarkets/<set>/<date>/<HHMM>/` — trimmed raw tier-1 events and every raw book
fetched that run (json.gz), for reproducibility.

## Known limitations

- Order books are only captured for markets priced in [0.02, 0.98] at run time.
- Polymarket universe raw JSON is not archived (≈60 MB per run); quotes parquet is the record.
- Kalshi field names follow the 2026 API (`*_dollars`, `*_fp` strings); older `yes_bid` ints are gone.


# Data model — SEC XBRL SaaS benchmark

Pipeline: `pipelines/sec/ingest.py` + `transform.py`, weekly (`refresh-weekly.yml`, Mondays 07:17 UTC).

- `data/snapshots/sec/facts/<TICKER>.parquet` — duration facts for the mapped tags (10-K/10-Q only),
  deduplicated on (metric, tag, start, end) with the latest filing winning. Overwritten weekly; git
  history keeps prior versions. `companies.json` records CIK, name, fetch time and coverage.
- `data/marts/sec/saas_quarterly.parquet` — ticker x quarter_end x metric with `quarterly` and `ttm`.
- `data/marts/sec/saas_ttm.json` / `saas_latest.json` — TTM metrics and ratios (growth, margins, SBC %, Rule of 40).
- `data/marts/sec/saas_coverage.json` — tags used per metric, quarter counts, direct-vs-derived reconciliation.
- Quarterly derivation: one-quarter spans (80–100 days) used directly; otherwise consecutive spans
  with the same start are differenced (6M−3M, 9M−6M, FY−9M). Weighted-average share counts are
  averages (never differenced; TTM = mean of four quarters).

# Data model — FOMC decision markets

- `data/snapshots/predmarkets/fomc/history/<platform>.parquet` — daily backfilled prices
  (Polymarket `prices-history`, Kalshi candlesticks), refreshed weekly by merging on
  (platform, market_id, date): new rows win a shared key and no stored row is dropped, so a failed or
  404'd fetch keeps the market's history. Kalshi markets settled before the historical cutoff are
  fetched from `/historical`.
- `pipelines/predmarkets/fomc.py` maps both platforms to one meeting/outcome grid
  (cut50, cut25, hold, hike25, hike50), rolls up to cut/hold/hike for charts, and scores resolved
  meetings with the multi-outcome Brier score (0 = perfect, 2 = certain and wrong) of the last snapshot
  taken before 17:30 UTC on decision day (fallback: last daily history point before decision day).
- `data/snapshots/predmarkets/<set>/resolutions.json` — append-only store of settled tier-1 markets
  (Kalshi `result`, Polymarket closed 1/0 prices), captured by every full run so results survive the
  markets leaving the open listings. Kalshi events settled before the historical cutoff are read from
  `/historical/markets`, once per event, so the store also holds older settlements of the tier-1 series
  (e.g. 2024 races for the midterms set); scoring looks results up by market id.
  Also the basis for scoring the midterms after November 3.
- `data/snapshots/predmarkets/fomc/archive/` — decision markets that closed before the snapshot
  pipeline first saw them (the January 2025 to July 2026 meetings), written by
  `pipelines/predmarkets/archive.py`: `markets.parquet` (platform, market_id, meeting, bucket, yes_token,
  result, source) and `daily.parquet` (platform, market_id, ts, price, src). `ts` is the raw epoch second;
  `src` is `daily` / `hourly_fill` (Polymarket) or `trade` / `quote_mid` (Kalshi; the mid only when the
  spread is at most 10 cents). A closed market's history does not change, so stored markets are skipped.
- Price time: `fomc.daily_prices` dates every price by the New York day it was observed. The backfill's
  `date` is the UTC date of the bar timestamp (`backfill.backfill_polymarket`, `backfill.kalshi_candle_row`),
  one day late for both platforms, and is shifted back a day at read time; archive rows use the New York
  date of `ts - 1 s`; snapshots use the New York date of `snapshot_ts`. The stored files are not rewritten.
- Kalshi outcomes are mapped by ticker suffix (`C26` cut50, `C25` cut25, `H0` hold, `H25` hike25, `H26`
  hike50); the outcome labels were reworded twice ("No cut/hike", "No change", "Fed maintains rate").
- Outputs: `data/marts/predmarkets/fomc_history.json` (every open meeting), `fomc_meetings.json`,
  `data/facts/fomc.json`.

# Data model — FOMC macro layer and release event study

Written by `pipelines/macro/build.py` (config in `pipelines/macro/config.py`).

- `data/snapshots/macro/fred_daily.parquet` — FRED daily series (series, date, value) from the keyless
  graph CSV: `DFEDTARU`, `DGS2`, `T5YIE`, `T5YIFR`, `DCOILWTICO`, `DCOILBRENTEU`. Merged; new rows win.
- `data/snapshots/macro/claims_first_print.parquet` — `IC4WSA` as first published (series, week_end,
  release_date, value), from ALFRED multi-vintage CSVs (at most 12 vintages per request; the parser
  rejects a response whose columns differ from the request). Merged; the stored first print is kept.
- `pipelines/macro/release_calendar.csv` — official release times (CPI, jobs, PPI, ECI from the BLS
  schedule; PCE and GDP from the BEA schedule), maintained by hand each December because BLS refuses
  scripted downloads of its calendar.
- `data/snapshots/macro/ladders.json` — settled Kalshi release ladders (`KXCPI`, `KXCPICORE`,
  `KXPAYROLLS`, `KXU3`, `KXPCECORE`) since January 2025, trimmed to strike, result, value and close time.
- `data/snapshots/macro/consensus.json` — per release and ladder: the ladder mean at T0 - 10 min,
  the first print (from `expiration_value` when it agrees with the settled results, else from the
  results), the surprise and the per-strike quotes used.
- `data/snapshots/macro/events/<release>_<date>.parquet`, `placebo/<date>.parquet` — one-minute quotes
  (platform, market_id, ts, bid, ask, trade, price) for the decision markets of the next two meetings,
  from an hour before the window (plus each market's last earlier quote) to 16:00 New York time. Written
  only when every market fetched, so a partial fetch is retried on the next run.
- Outputs: `data/marts/predmarkets/fomc_macro_daily.json` (date, panel, series, value, segment),
  `fomc_event_study.json` (one row per release), `fomc_event_paths.json` (mean path by release type,
  surprise sign, platform and minute), `data/facts/fomc_macro.json`.

# Case study — stat-arb 2019–2020

Frozen result tables from the original project live in `data/case_studies/statarb/tables`;
`pipelines/statarb/publish.py` turns them into `data/facts/statarb.json` and chart marts. Nothing is recomputed.

# Case study — Financial LLM (SEC LoRA)

`data/case_studies/finllm/evaluation_results_phase2.json` is the frozen 4-way BERTScore report from
srx7703/multi-horizon-financial-llm; `pipelines/finllm/publish.py` writes `data/facts/finllm.json`
and per-item / paired marts for the charts. Nothing is recomputed.

---

# Case study — memory-chip price cycles

Frozen case study (`pipelines/memcycle/`), no scheduled workflow. Rules: `docs/EVALUATION_PLAN.md`
section G, translating `docs/prereg/memory-cycles/`.

| Layer | Path | In git |
|---|---|---|
| Raw | `data/raw/memcycle/ecos/2026-09-28/1316/*.json.gz` — ECOS 402Y016 responses (sample key, ten rows per page), item list and pull log | yes (BOK: reuse with attribution) |
| Snapshot | `data/snapshots/memcycle/ecos_402Y016.parquet` — `item_code, item_name, basis, month, value, unit, weight, retrieved_at, preliminary`; key `(item_code, basis, month)`; DRAM and flash, bases C/D/W | yes |
| Frozen | `data/case_studies/memcycle/frozen/company_cycles_derived.csv` — one row per company × price peak: months, leads, ratios, flags; no close columns (strict pandera schema) | yes |
| Manifest | `data/case_studies/memcycle/inputs_manifest.json` — sha256, rows and coverage of every local input; `q3_versions.json` — the Q3 verdict at each research commit | yes |
| Local only | `MEMCYCLE_INPUTS` on the owner's machine — month-end closes, benchmark indices, FRED FX | **no** (vendor terms) |

Marts (`data/marts/memcycle/`): `price_index` (contract-currency index from each series' start, the only
level-valued table), `turns` (ten registered runs), `calendar` (phases, numbered cycles, the deletion log,
deleted rallies, sensitivity), `leads` (stock-peak month and lead per company-cycle, sample membership and
the exclusion reason), `multiples` (up-leg ratios only), `evaluation`. Facts: `data/facts/memcycle.json`.

`publish.py` refuses to write if the recomputed calendar's peaks differ from those the frozen rows were
measured against, or if the recomputed Q3 differs from the last research version. `freeze.py` rebuilds the
frozen table and manifest locally, applying the input fixes (Elpida full series, Qimonda merge, KRX official
closes) in memory; `tests/test_reconcile.py` has a local tier that checks it byte for byte.

# Data model — valuation (optical modules and solid-state batteries)

Pipeline: `pipelines/valuation/`. Runs via GitHub Actions (`valuation.yml`): prices and FX on
weekdays at 06:47 UTC, the full run including analyst consensus on Mondays at 07:47 UTC. Two pages
are built from it, one per track, sharing every table.

## Sources (all public, no paid key)

| Source | Endpoint | Used for |
|---|---|---|
| Yahoo Finance | `yfinance.Ticker(t).info` | last close, quote currency, reporting currency, market cap, shares |
| Yahoo Finance | `quoteSummary?modules=earningsTrend` | consensus EPS for the current and next fiscal year, with the period **end date**, the analyst count, the high/low, and the mean as it stood 7/30/60/90 days ago |
| Yahoo Finance | `Ticker(t).quarterly_income_stmt` / `.income_stmt` | quarterly and annual net income and revenue outside China and the US |
| East Money | `PC_HSF10/ProfitForecast/PageAjax?code=SZ300308` | A-share consensus: **per-broker** forecasts (`ycmx`) with publisher, researcher, publish date, EPS and attributable net profit for four years, plus East Money's own six-month mean (`jgyc`) and the rating distribution (`pjtj`) |
| East Money | `datacenter-web/api/data/v1/get?reportName=RPT_LICO_FN_CPD` | A-share quarterly reports (cumulative year-to-date, differenced here) |
| SEC EDGAR | XBRL `companyfacts` | US net income and revenue, through the machinery already written for the SaaS benchmark |
| FRED | `fredgraph.csv?id=DEXCHUS` and five more | daily CNY, JPY, KRW, TWD, HKD and GBP rates |

Neither Yahoo nor East Money is a documented public API. Both are treated as sources that can change
shape or rate-limit without notice: every payload is archived before parsing, every company is
fetched inside its own try/except, and a failed source never discards the sources that worked.

## Raw archive policy

Payloads land in `data/raw/valuation/<source>/<date>/` as gzipped JSON or CSV, append-only. The one
deliberate exception is SEC `companyfacts`: EDGAR is itself a permanent public archive with stable
URLs, the filings cannot be silently rewritten, and one weekly run of the 17 US listings would add
about 6 MB. As in `pipelines/sec`, the extracted facts are kept and the payload is not.

## Tables

`data/snapshots/valuation/prices/<date>.parquet` — one row per listing.

| column | meaning |
|---|---|
| `price`, `currency` | last close in the **quote** currency; `GBp` means London pence, not pounds |
| `financial_currency` | the currency the company reports in, which can differ (CATL's H line: HKD / CNY) |
| `market_cap`, `shares_outstanding` | as published; for an H line the cap is the whole issuer at the H price |
| `price_ts` | when that quote was struck, so a stale market can be seen |

`data/snapshots/valuation/estimates/<date>-{yahoo,eastmoney}.parquet` — the current consensus, one
row per (ticker, source, fiscal period end), carrying `eps_avg/low/high`, `n_analysts`, the currency
the EPS is quoted in, and for East Money the mean attributable net profit. A prior-year **actual**
row (`mark = "A"`) is emitted too, because a non-December filer needs it to build a calendar year.

`data/snapshots/valuation/vintages/<date>-{yahoo,eastmoney}.parquet` — the consensus as it stood
earlier. Two origins that do not mean the same thing:

- `yahoo_trend` — Yahoo's own restatement of the mean at 7/30/60/90 days ago over a fixed panel. A
  move here is a **revision**.
- `eastmoney_rebuilt` — each broker publishes once in the window, so averaging the brokers who had
  published by an earlier date gives the consensus **as it stood**, not a revision series: the mean
  moves when new coverage arrives as well as when someone changes their mind, and the earliest
  points rest on two or three reports. `n_analysts` is the cohort size behind each point, and it is
  the reason the early series rises.
- `snapshot` — our own weekly capture, a true revision series from the second week onwards.

Only `yahoo_trend` and `snapshot` are used to score revision persistence.

`data/snapshots/valuation/brokers/<date>-eastmoney.parquet` — the per-broker evidence behind the
A-share mean: house, researchers, publish date, year, EPS, net profit, rating. This is what makes
the A-share consensus reproducible rather than taken on trust.

`data/snapshots/valuation/fundamentals/<date>*.parquet` — quarterly attributable net income and
revenue per listing, already differenced out of cumulative filings, with `derived` marking which
quarters came from differencing and `source` naming where they came from.
`fundamentals_annual/` holds annual rows for the listings whose quarterly statements Yahoo does not
carry (the Japanese names, CALB, Ilika), so those get a fiscal-year trailing multiple clearly
labelled as such instead of no multiple at all.

`data/snapshots/valuation/fx/<date>.parquet` — 400 days of daily rates per currency as units per
USD. `DEXUSUK` is published the other way round and is inverted on the way in.

## Derived

`pipelines/valuation/calendarize.py` restates fiscal-year consensus onto calendar years by month
overlap, because half the pool does not end its year in December. `metrics.py` computes the ratios,
converting currencies explicitly at every step and returning a written reason instead of a number
whenever a denominator is not positive. `share.py` builds the two share bases. `publish.py` writes
`data/marts/valuation/*.json` and `data/facts/valuation_{optical,ssb}.json`; `evaluate.py` scores
the pre-registered plan and returns "not yet" with a reason for anything that cannot be scored.

# Data model — payments landscape

Pipeline: `pipelines/payments/`. Page: `/projects/payments-landscape/`. Question: what do payments companies
keep from every $100 they move, how concentrated is each layer, and how do private valuations line up with
what the companies disclose. The universe (segments A acceptance, B BNPL, C spend management and B2B,
D cross-border, E issuing, F stablecoin; `core` rows charted, `table` rows listed only) is pinned in
`pipelines/payments/config.py` and registered in `docs/EVALUATION_PLAN.md`.

Refresh: `make publish` runs `python -m pipelines.payments.publish`, which rebuilds every mart and the facts
file from the reference layer plus whatever CI-source parquet is on disk. The scheduled weekly workflow
(`payments.yml`, added at ship preparation on the `power.yml` pattern) also runs the CI-only sources. A
missing CI source is a warn check and an unreadable one a failed check; the rest of the page still builds.
A regression guard refuses to overwrite facts and marts if a run would cover fewer than half the companies
the existing facts file covers.

## Reference layer (curated, hand-checked)

`data/reference/payments/*.json`, validated by `pipelines/payments/reference.py` on top of the generic rules
in `pipelines/common/curation.py` (reachable `https://` link, publication date, caveat, re-check date, no
working notes, whole file or nothing). Every row carries:

| field | meaning |
|---|---|
| `tag` | `V` read on the primary page, `S` search extract only, `C` computed from `V` inputs, `U` unverified. Only `V`/`C` may be chartable |
| `metric_kind` | closed vocabulary in `config.py`; `reported_talks` and `third_party_estimate` are never chartable, whatever the tag |
| `qualifier` | `=`, `>`, `<`, `~`; "more than $1.4tn" keeps its `>` everywhere downstream |
| `as_of_date` | the period or date the figure describes, distinct from when it was published |

| file | grain | source |
|---|---|---|
| `kpi_disclosures_acceptance.json` | company x metric x period, acceptance (segment A) volumes and revenue | filings, shareholder letters, IR releases |
| `kpi_disclosures_other.json` | company x metric x period, segments B-F | filings, IR releases |
| `private_metrics.json` | private company x metric x date (valuations, rounds, stated volume or revenue) | company statements, filings; press reports kept as non-chartable |
| `waterfall_inputs.json` | waterfall x period x input line | income-statement lines from filings |
| `denominators.json` | official market totals per scope and period (Census e-commerce, Fed BNPL issuance and similar) | official statistics |
| `products.json` | company x product line, with status and launch date | product pages, launch announcements |
| `events.json` | company x event kind x date x counterparty (IPO, acquisition, priced round, tender, listing) | filings, company announcements |

## Marts (`data/marts/payments/*.json`)

Each is validated by its pandera schema (`pipelines/payments/schema.py` `MARTS`) before any file is written.
`waterfall_lines`, `ledger_timeline`, `ledger_latest`, `private_intervals`, `take_rates`, `products` and
`events` only ever hold chartable rows (`schema.CHARTED_MARTS`, asserted by publish).

| table | grain | key | source / built by |
|---|---|---|---|
| `waterfall_lines` | one line of a per-$100 waterfall (take, cost, kept) for one company-period | `waterfall, period, line` | `economics.py` from `waterfall_inputs` |
| `sensitivity` | one scenario of a registered sensitivity band for one waterfall output | `question, waterfall, scenario, input_value, output` | `economics.py` |
| `take_rates` | revenue over the company's own volume, one ratio per company-period; `ratio_kind` is `gross_take_rate`, `net_take_rate` or `margin_retention` and is never compared across kinds | `company, ratio, period` | `ledger.py` from KPI disclosures |
| `take_rates_refused`, `private_take_rates_refused` | a ratio that was not computed, with the written reason | `company, ratio, period` | `ledger.py` |
| `ledger_timeline` | one dated private-company figure (valuation, round, stated volume) | `company, source, metric, period, date, counterparty` | `ledger.py` from `private_metrics` + `events` |
| `ledger_latest` | latest current value per private company and metric | `company, metric` | `ledger.py` |
| `private_intervals` | a derived bound (lower/upper/estimate) where only an interval is defensible | `company, metric, period` | `ledger.py` |
| `share_lenses` | one company's share in one pool under one lens (`disclosed_pool`, `official_denominator`); refusals carry `refusal_reason`; lenses are never mixed or summed | `lens, pool, company, period, role, source_metric` | `share.py` from KPI disclosures + `denominators` |
| `share_pool_hhi` | concentration inside each closed pool | `lens, pool, period` | `share.py` |
| `products` | company x product line matrix | `company, product_line` | `products.json` |
| `events` | 2025-26 deal timeline | `company, event_kind, event_date, counterparty` | `events.json` |
| `metric_dictionary` | every metric: definition, kind, unit, comparability, source, which mart uses it | `dataset, scope, metric` | publish |
| `reported_unconfirmed` | every non-chartable row with the reason; shown only as a table column labelled "reported, unconfirmed", never charted | `source, company, metric, period, date, counterparty, value` | publish |
| `scoreboard` | one pre-registered question (Q1-Q9): status, reason, next grading date, CI inputs present | `id` | `evaluate.py` |
| `scoreboard_evidence` | the readings behind each question | `id, label` | `evaluate.py` |
| `kpis` | the three headline KPIs, each with value, qualifier, unit, period, source URL and as-of | `id` | publish |
| `webtech_q2`, `devstats_q1`, `devstats_monthly`, `jobs_by_function`, `formd_filings`, `sec_latest` | small views of the CI-only sources, written only when that source is on disk | `slice, technology, date`; `month`; `side, series, period`; `snapshot_date, ats, board, function`; `company, accession`; `ticker` | `webtech.py`, `devstats.py`, `jobs.py`, `formd.py`, SEC via publish |

CI-only sources (npm downloads, HTTP Archive, Greenhouse/Ashby board counts, SEC Form D, SEC XBRL) write
parquet next to the marts; `devstats`, `webtech` and `formd` archive their payloads append-only under
`data/raw/payments/<source>/<date>/<HHMM>/`. Job boards are stored as counts only, never job text.

## Facts (`data/facts/payments.json`)

`kpis` (exactly three, the page's KPI row), `values` (every templated number, keyed by a dotted id such as
`hhi.disclosed_pool.A_payment_volume`, each with `value`, `qualifier`, `unit`, `period`, `as_of`, `source_url`), `counts`, `coverage`, `scoreboard`, `ci_sources`, `marts` (name to
path under `data/marts/`, loaded by the page by URL from `/data/marts/payments/`), `checks` (range,
uniqueness, qualifier, freshness and regression-guard results shown in the page's Data quality section),
`sources`, `generated_at` and `registered`. Every number in the page prose and the home row is read from here.
