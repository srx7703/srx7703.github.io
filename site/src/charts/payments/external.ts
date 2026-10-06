/**
 * Large chart datasets are loaded by URL instead of being inlined in the page's data-spec
 * attribute (CLAUDE.md: large marts are loaded by URL). The spec builders stay as they are; this
 * module splits a built spec into (a) the same spec with every large inline `data.values` replaced
 * by `{ url, name?, format: json }` and (b) the rows each URL serves. The prebuild script
 * scripts/payments-data.mjs writes those rows to public/data/payments/, so the page and its data
 * files always come from the same marts, and every finding title is still computed at build time.
 *
 * Datasets that hold `token:--…` strings stay inline: VegaChart resolves tokens in the spec only.
 */
import takeRates from '@data/marts/payments/take_rates.json';
import shareLenses from '@data/marts/payments/share_lenses.json';
import shareHhi from '@data/marts/payments/share_pool_hhi.json';
import ledgerTimeline from '@data/marts/payments/ledger_timeline.json';
import privateIntervals from '@data/marts/payments/private_intervals.json';
import eventsJson from '@data/marts/payments/events.json';
import productsJson from '@data/marts/payments/products.json';
import sensitivityJson from '@data/marts/payments/sensitivity.json';
import metricDictionary from '@data/marts/payments/metric_dictionary.json';
import { companyPoints } from './company_data';
import { companySeriesSpec } from './company_series';
import { companyScatterSpec } from './company_scatter';
import { shareBarsSpec } from './share_bars';
import { shareDumbbellSpec } from './share_dumbbell';
import { ledgerPoints } from './ledger_data';
import { ledgerIntervalSpec } from './ledger_intervals';
import { eventsSpec, type EventRow } from './events';
import { productsSpec } from './products';
import { sensitivitySpec, type GridKey } from './sensitivity';

type Spec = Record<string, unknown>;
type Rows = unknown[];

export const DATA_BASE = '/data/payments';
/** Inline datasets smaller than this (serialised characters) are not worth a request. */
const MIN_CHARS = 4000;

export function splitSpec(id: string, spec: Spec): { spec: Spec; files: Record<string, Rows> } {
  const files: Record<string, Rows> = {};
  let n = 0;
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(walk);
    if (!v || typeof v !== 'object') return v;
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
      if (k === 'data' && x && typeof x === 'object' && Array.isArray((x as Spec).values)) {
        const { values, ...rest } = x as Spec & { values: Rows };
        const s = JSON.stringify(values);
        if (s.length >= MIN_CHARS && !s.includes('token:')) {
          const file = `${id}-${n++}`;
          files[file] = values;
          out[k] = { ...rest, url: `${DATA_BASE}/${file}.json`, format: { type: 'json' } };
        } else {
          out[k] = x; // small or token-bearing: inline as built
        }
        continue;
      }
      out[k] = walk(x);
    }
    return out;
  };
  return { spec: walk(spec) as Spec, files };
}

const points = companyPoints(takeRates);
const ledgerPts = ledgerPoints(ledgerTimeline, privateIntervals);
const builders: Record<string, () => Spec> = {
  'company-series': () => companySeriesSpec(points),
  'company-scatter': () => companyScatterSpec(points),
  'share-bars': () => shareBarsSpec(shareLenses, shareHhi),
  'share-dumbbell': () => shareDumbbellSpec(shareLenses),
  'ledger-intervals': () => ledgerIntervalSpec(ledgerPts),
  'ledger-events': () => eventsSpec(eventsJson as unknown as EventRow[]),
  products: () => productsSpec(productsJson),
  ...Object.fromEntries((['q4', 'q7_wise', 'q7_fsb', 'q8', 'q8_bill'] as GridKey[]).map((k) => [
    `sensitivity-${k}`, () => sensitivitySpec(sensitivityJson, k),
  ])),
};

let cache: Map<string, ReturnType<typeof splitSpec>> | null = null;
const built = () => (cache ??= new Map(Object.entries(builders).map(([id, b]) => [id, splitSpec(id, b())])));

/** The page's spec for chart `id`, with its large datasets pointing at their URLs. */
export function urlSpec(id: string): Spec {
  const hit = built().get(id);
  if (!hit) throw new Error(`no URL-loaded spec registered for chart ${id}`);
  return hit.spec;
}

// The metric dictionary table is loaded by URL as well (it is the largest table on the page).
export const DICTIONARY_FILE = 'metric-dictionary';
export const dictionaryRows = () => metricDictionary.map((r) => ({
  ...r,
  source: r.source_url
    ? `<a href="${r.source_url}" title="${(r.source_name ?? '').replace(/"/g, '&quot;')}">source</a>`
    : (r.source_name ?? '—'),
}));

/** Every file the endpoint serves: chart datasets plus the dictionary rows. */
export function dataFiles(): Record<string, Rows> {
  const out: Record<string, Rows> = { [DICTIONARY_FILE]: dictionaryRows() };
  for (const { files } of built().values()) Object.assign(out, files);
  return out;
}
