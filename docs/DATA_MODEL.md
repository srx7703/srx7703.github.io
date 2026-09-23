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
- Outputs: `data/marts/predmarkets/fomc_history.json`, `fomc_meetings.json`, `data/facts/fomc.json`.

# Case study — stat-arb 2019–2020

Frozen result tables from the original project live in `data/case_studies/statarb/tables`;
`pipelines/statarb/publish.py` turns them into `data/facts/statarb.json` and chart marts. Nothing is recomputed.

# Case study — Financial LLM (SEC LoRA)

`data/case_studies/finllm/evaluation_results_phase2.json` is the frozen 4-way BERTScore report from
srx7703/multi-horizon-financial-llm; `pipelines/finllm/publish.py` writes `data/facts/finllm.json`
and per-item / paired marts for the charts. Nothing is recomputed.

---

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
