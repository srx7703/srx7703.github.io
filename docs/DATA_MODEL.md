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
