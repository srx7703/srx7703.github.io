/**
 * Large chart datasets of the cybersecurity page are loaded by URL instead of being inlined in the page's
 * data-spec attribute (CLAUDE.md: large marts are loaded by URL). The spec builders stay as they are; this module
 * splits a built spec into (a) the same spec with every large inline `data.values` replaced by
 * `{ url, name?, format: json }` and (b) the rows each URL serves. The prebuild script scripts/cyber-data.mjs writes
 * those rows to public/data/cyber/, so the page and its data files always come from the same marts, and every
 * finding title is still computed at build time (`figureText`).
 *
 * Same contract as charts/payments/external.ts (`splitSpec`, `urlSpec`, `dataFiles`, `DATA_BASE`,
 * `DICTIONARY_FILE`), plus a registry: every figure is one `fig({...})` line in FIGURES, so adding a figure is one
 * line. Differences from the payments module, all because a layered cyber spec repeats one dataset in several
 * layers and three figures of a dashboard share the same listings:
 *   - identical datasets are written once and shared by every spec that carries them (content-addressed within the
 *     build, named after the first figure that used them);
 *   - named `datasets` (Vega-Lite `datasets` + `data: { name }`) are moved out the same way.
 *
 * Datasets that hold `token:--…` strings stay inline: VegaChart resolves tokens in the spec only.
 *
 * Beyond the chart datasets this module is the registry of every file the page loads by URL (written by
 * scripts/cyber-data.mjs into public/data/cyber):
 *   - table files (`TABLE_FILES`, `PAGE_TABLES`): pre-formatted rows for a lazy <UrlTable>; never the raw price, market
 *     cap, enterprise value or target-price fields of the Yahoo-derived marts (the prebuild checks the keys);
 *   - title maps (`titles-<figure>.json`): the finding for every state of a dashboard's controls, fetched by
 *     DashboardControls only once a control leaves its default. `urlSpec` marks a spec with the URL (`_titlesSrc`) and
 *     VegaChart drops the inline map for it.
 * `page.ts` imports this module, so this module cannot import `page.ts`: the rows and maps that page.ts builds are handed in
 * by the prebuild (`dataFiles(page)`), and `PAGE_TABLES` / `TITLE_FILES` below say which export is which file.
 */
import listingsJson from '@data/marts/cyber/listings.json';
import layerSummaryJson from '@data/marts/cyber/layer_summary.json';
import regressionJson from '@data/marts/cyber/regression.json';
import targetsJson from '@data/marts/cyber/targets.json';
import recommendationsJson from '@data/marts/cyber/recommendations.json';
import chinaPanelJson from '@data/marts/cyber/china_panel.json';
import privateIntervalsJson from '@data/marts/cyber/private_intervals.json';
import ledgerTimelineJson from '@data/marts/cyber/ledger_timeline.json';
import eventsJson from '@data/marts/cyber/events.json';
import metricDictionary from '@data/marts/cyber/metric_dictionary.json';
import { layerMapRows, layerMapSpec, layerMapFinding, layerMapSubtitle, layerMapNotes } from './layer_map';
import { evGrowthRows, evGrowthSpec, evGrowthFinding, evGrowthSubtitle, evGrowthNotes } from './ev_growth_scatter';
import { rule40Rows, rule40ScatterSpec, rule40RankedSpec, rule40Finding, rule40Subtitle, rule40Notes } from './rule40_ranked';
import { targetDumbbellRows, targetDumbbellSpec, targetDumbbellFinding, targetDumbbellSubtitle, targetDumbbellNotes } from './target_dumbbell';
import { recommendationRows, recommendationSpec, recommendationFinding, recommendationSubtitle, recommendationNotes } from './recommendation_bars';
import { chinaPanelRows, chinaPanelSpec, chinaPanelFinding, chinaPanelSubtitle, chinaPanelNotes } from './china_panel';
import { ledgerIntervalSpec, ledgerIntervalFinding, ledgerIntervalSubtitle, ledgerIntervalNotes, intervalTableRows } from './ledger_intervals';
import {
  eventsSpec, eventsFinding, eventsSubtitle, eventsNotes, eventDumbbellSpec, eventDumbbellFinding, eventDumbbellSubtitle, eventDumbbellNotes,
  dumbbellTableRows, eventsTableRows,
} from './events';
import { ledgerTableRows } from './ledger_data';
import type {
  Listing, LayerSummary, TargetRow, RecommendationRow, ChinaRow, PrivateInterval, LedgerRow, EventRow, DictionaryRow,
  RegressionMart,
} from './types';

type Spec = Record<string, unknown>;
type Rows = unknown[];

export const DATA_BASE = '/data/cyber';
/** Inline datasets smaller than this (serialised characters) are not worth a request. */
const MIN_CHARS = 4000;

/**
 * Split a built spec: every `data.values` array of at least MIN_CHARS characters (and without `token:` strings) is
 * replaced by a URL, and so is every large named dataset in `datasets` that a `data: { name }` refers to.
 * `shared` maps a serialised dataset to the file already written for it, so an identical dataset is stored once.
 */
export function splitSpec(id: string, spec: Spec, shared: Map<string, string> = new Map()): { spec: Spec; files: Record<string, Rows> } {
  const files: Record<string, Rows> = {};
  let n = 0;
  const fileFor = (values: Rows, s: string): string => {
    const hit = shared.get(s);
    if (hit) return hit;
    const file = `${id}-${n++}`;
    files[file] = values;
    shared.set(s, file);
    return file;
  };
  const splittable = (values: unknown): values is Rows => {
    if (!Array.isArray(values)) return false;
    const s = JSON.stringify(values);
    return s.length >= MIN_CHARS && !s.includes('token:');
  };
  const named = (spec.datasets && typeof spec.datasets === 'object' ? spec.datasets : {}) as Record<string, unknown>;
  const moved = new Set<string>();
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(walk);
    if (!v || typeof v !== 'object') return v;
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
      if (k === 'data' && x && typeof x === 'object' && !Array.isArray(x)) {
        const d = x as Spec;
        if (Array.isArray(d.values)) {
          const { values, ...rest } = d as Spec & { values: Rows };
          if (splittable(values)) {
            const file = fileFor(values, JSON.stringify(values));
            out[k] = { ...rest, url: `${DATA_BASE}/${file}.json`, format: { type: 'json' } };
          } else {
            out[k] = x; // small or token-bearing: inline as built
          }
          continue;
        }
        if (typeof d.name === 'string' && splittable(named[d.name])) {
          const values = named[d.name] as Rows;
          const { name, ...rest } = d;
          const file = fileFor(values, JSON.stringify(values));
          moved.add(name as string);
          out[k] = { ...rest, url: `${DATA_BASE}/${file}.json`, format: { type: 'json' } };
          continue;
        }
      }
      out[k] = walk(x);
    }
    return out;
  };
  const walked = walk(spec) as Spec;
  if (moved.size && walked.datasets && typeof walked.datasets === 'object') {
    const rest = Object.fromEntries(Object.entries(walked.datasets as Record<string, unknown>).filter(([k]) => !moved.has(k)));
    if (Object.keys(rest).length) walked.datasets = rest;
    else delete walked.datasets;
  }
  return { spec: walked, files };
}

// ---- the registry --------------------------------------------------------------------------------------------------

/** Dashboards of the page (SITE_SPEC.md), plus the figures that stand alone. */
export type Dashboard = 'valuation' | 'targets' | 'ledger' | 'china';

/**
 * One figure. `rows` is the data preparation from the marts, `spec` the Vega-Lite builder, `finding` and `subtitle`
 * the computed title and metric string (SITE_SPEC.md: every chart file exports `<name>Rows`, `<name>Spec`,
 * `<name>Finding`, `<name>Subtitle`).
 */
export interface Figure<R = unknown> {
  id: string;
  dashboard: Dashboard;
  rows: () => R;
  spec: (rows: R) => Spec;
  finding: (rows: R) => string;
  subtitle: (rows: R) => string;
  /** The reading guide of the figure (`<name>Notes`): plain text, paragraphs separated by a blank line. It goes in the card's "How to read this". */
  notes?: (rows: R) => string;
  /** Keep every dataset inline (no URL split): the spec reads a dataset by name with `indata`, which a URL source would break. */
  inline?: boolean;
}
/** Erase the row type so figures with different rows share one array; the one place the types meet is the call site. */
const fig = <R>(f: Figure<R>): Figure => f as unknown as Figure;
/** Mart rows, typed from types.ts (the JSON imports are inferred narrower than the nullable row types). */
export const marts = {
  listings: listingsJson as unknown as Listing[],
  layerSummary: layerSummaryJson as unknown as LayerSummary[],
  regression: regressionJson as unknown as RegressionMart,
  targets: targetsJson as unknown as TargetRow[],
  recommendations: recommendationsJson as unknown as RecommendationRow[],
  chinaPanel: chinaPanelJson as unknown as ChinaRow[],
  privateIntervals: privateIntervalsJson as unknown as PrivateInterval[],
  ledgerTimeline: ledgerTimelineJson as unknown as LedgerRow[],
  events: eventsJson as unknown as EventRow[],
};

/** Every figure of the page, in page order. Adding a figure is one `fig({...})` line here. */
export const FIGURES: Figure[] = [
  // dashboard 1: valuation explorer (group `valuation`)
  fig({ id: 'layer-map', dashboard: 'valuation',
    rows: () => layerMapRows(marts.listings, marts.layerSummary, { privateRows: marts.privateIntervals }),
    spec: layerMapSpec, finding: layerMapFinding, subtitle: layerMapSubtitle, notes: layerMapNotes }),
  fig({ id: 'ev-growth', dashboard: 'valuation',
    rows: () => evGrowthRows(marts.listings, marts.regression),
    spec: evGrowthSpec, finding: evGrowthFinding, subtitle: evGrowthSubtitle, notes: evGrowthNotes }),
  fig({ id: 'rule40-scatter', dashboard: 'valuation',
    rows: () => rule40Rows(marts.listings),
    spec: rule40ScatterSpec, finding: rule40Finding, subtitle: rule40Subtitle, notes: rule40Notes }),
  fig({ id: 'rule40-ranked', dashboard: 'valuation',
    rows: () => rule40Rows(marts.listings),
    spec: rule40RankedSpec, finding: rule40Finding, subtitle: rule40Subtitle, notes: rule40Notes }),
  // dashboard 2: targets and ratings (group `targets`)
  fig({ id: 'target-dumbbell', dashboard: 'targets',
    rows: () => targetDumbbellRows(marts.targets, marts.listings),
    spec: targetDumbbellSpec, finding: targetDumbbellFinding, subtitle: targetDumbbellSubtitle, notes: targetDumbbellNotes }),
  fig({ id: 'recommendation-bars', dashboard: 'targets',
    rows: () => recommendationRows(marts.recommendations, marts.listings),
    spec: recommendationSpec, finding: recommendationFinding, subtitle: recommendationSubtitle, notes: recommendationNotes }),
  // dashboard 3: private ledger and events (group `ledger`)
  // inline: the empty-view sentence reads this dataset by name with `indata`, which a URL source would break
  fig({ id: 'ledger-intervals', dashboard: 'ledger', inline: true,
    rows: () => marts.privateIntervals,
    spec: ledgerIntervalSpec, finding: ledgerIntervalFinding, subtitle: ledgerIntervalSubtitle, notes: ledgerIntervalNotes }),
  fig({ id: 'events', dashboard: 'ledger',
    rows: () => marts.events,
    spec: eventsSpec, finding: eventsFinding, subtitle: eventsSubtitle, notes: eventsNotes }),
  fig({ id: 'events-dumbbell', dashboard: 'ledger',
    rows: () => marts.events,
    spec: eventDumbbellSpec, finding: eventDumbbellFinding, subtitle: eventDumbbellSubtitle, notes: eventDumbbellNotes }),
  // the China section (figure 8)
  fig({ id: 'china-panel', dashboard: 'china',
    rows: () => chinaPanelRows(marts.chinaPanel),
    spec: chinaPanelSpec, finding: chinaPanelFinding, subtitle: chinaPanelSubtitle, notes: chinaPanelNotes }),
];

const byId = new Map<string, Figure>();
const rowsCache = new Map<string, unknown>();
const rowsOf = (f: Figure): unknown => {
  if (!rowsCache.has(f.id)) rowsCache.set(f.id, f.rows());
  return rowsCache.get(f.id);
};
const figure = (id: string): Figure => {
  if (!byId.size) for (const f of FIGURES) {
    if (byId.has(f.id)) throw new Error(`figure id ${f.id} is registered twice`);
    byId.set(f.id, f);
  }
  const hit = byId.get(id);
  if (!hit) throw new Error(`no figure registered with id ${id}; registered: ${FIGURES.map((x) => x.id).join(', ')}`);
  return hit;
};

/** Ids of the registered figures, in page order. */
export const figureIds = (): string[] => FIGURES.map((f) => f.id);

/**
 * The figure's build-time title (the finding), subtitle (the metric, unit and window: one short sentence) and notes (the reading
 * guide: plain text, paragraphs separated by a blank line, empty when the figure has none), computed from its rows.
 */
export function figureText(id: string): { title: string; subtitle: string; notes: string } {
  const f = figure(id);
  const rows = rowsOf(f);
  return { title: f.finding(rows), subtitle: f.subtitle(rows), notes: f.notes ? f.notes(rows) : '' };
}

/** The figure's rows after data preparation (for a linked table or a test). */
export const figureRows = <R = unknown>(id: string): R => rowsOf(figure(id)) as R;

// ---- URL-loaded specs ----------------------------------------------------------------------------------------------

let cache: Map<string, ReturnType<typeof splitSpec>> | null = null;
const built = () => {
  if (!cache) {
    cache = new Map();
    const shared = new Map<string, string>();
    for (const f of FIGURES) {
      const spec = f.spec(rowsOf(f));
      cache.set(f.id, f.inline ? { spec, files: {} } : splitSpec(f.id, spec, shared));
    }
  }
  return cache;
};

/**
 * Pixels VegaChart keeps free beside a single-view chart. The charts of this page are laid out with `autosize: fit-x,
 * contains: padding`, so the width already contains the axes and the default allowance of 70 px only wastes a fifth of a
 * phone's width.
 */
export const WIDTH_INSET = 12;

/** Figures whose title follows the dashboard controls: the title map of each is a file (`titles-<id>.json`). */
export const TITLED_FIGURES = ['layer-map', 'ev-growth', 'rule40-scatter', 'target-dumbbell', 'recommendation-bars', 'ledger-intervals', 'events'] as const;

/** URL of the title map of a figure. */
export const titlesUrl = (id: string): string => `${DATA_BASE}/titles-${id}.json`;
/** URL of a table file (`TABLE_FILES` / `PAGE_TABLES`). */
export const tableUrl = (name: string): string => `${DATA_BASE}/${name}.json`;

/**
 * The page's spec for chart `id`, with its large datasets pointing at their URLs, the free width beside the plot
 * (`_widthInset`), the instruction to carry the spec in a script element instead of an attribute (`_specInScript`) and, for a figure whose title follows the controls, the URL of its title map (`_titlesSrc`). VegaChart reads
 * and removes these private keys.
 */
export function urlSpec(id: string): Spec {
  figure(id); // a clear error for an unknown id
  const hit = built().get(id);
  if (!hit) throw new Error(`no URL-loaded spec registered for chart ${id}`);
  const extra: Spec = { _specInScript: true };
  if (!hit.spec.facet && !hit.spec.spec) extra._widthInset = WIDTH_INSET; // a faceted spec sizes its own panels
  if ((TITLED_FIGURES as readonly string[]).includes(id)) extra._titlesSrc = titlesUrl(id);
  return { ...hit.spec, ...extra };
}

// The metric dictionary table is loaded by URL as well, like the payments dictionary.
export const DICTIONARY_FILE = 'metric-dictionary';
export const dictionaryRows = (): DictionaryRow[] => (metricDictionary as unknown as DictionaryRow[]).map((r) => ({ ...r }));

/**
 * Table datasets served as files next to the chart datasets (for a <UrlTable src={tableUrl(name)}>): file name to rows. The rows are
 * pre-formatted strings (UrlTable formats only 'num' columns) and are what the page states as the table's `total`. Chinese text
 * is wrapped in `lang="zh-Hans"` when the file is written. Adding a table that is too large to inline is one line here. The raw
 * ledger is not here: it is read from the synced mart, /data/marts/cyber/ledger_timeline.json (the CSV download).
 */
export const TABLE_FILES: Record<string, () => Rows> = {
  [DICTIONARY_FILE]: dictionaryRows,
  'ledger-table': () => ledgerTableRows(marts.ledgerTimeline),
  'intervals-table': () => intervalTableRows(marts.privateIntervals),
};

/**
 * Table files whose rows page.ts holds (its exported arrays, the same ones the page counts for `total`), or that are built with
 * something page.ts defines (the events window). page.ts imports this module, so the prebuild passes the page module to
 * `dataFiles(page)`. File name to the name of the page export that holds the rows (several names: the first that is an array),
 * or to a function of the page module.
 */
export const PAGE_TABLES: Record<string, string | string[] | ((page: Record<string, any>) => Rows)> = {
  'reported-table': ['reportedRows', 'reportedTableRows'],
  'etf-table': ['etfRows', 'etfTableRows'],
  'china-table': ['chinaRows', 'chinaTableRows'],
  'listing-table': 'listingRows',
  'targets-table': 'targetTableRows',
  'ratings-table': 'ratingTableRows',
  'events-table': (p) => eventsTableRows(marts.events, p.EVENTS_OPTS),
  'dumbbell-table': (p) => dumbbellTableRows(marts.events, p.EVENTS_OPTS),
};

/** Where page.ts keeps the title map of a figure (the same object its `titles` prop gets): file `titles-<id>` to the map. */
export const TITLE_FILES: Record<string, (page: Record<string, any>) => Record<string, string>> = {
  'layer-map': (p) => p.valuationTitles.layerMap.map,
  'ev-growth': (p) => p.valuationTitles.evGrowth.map,
  'rule40-scatter': (p) => p.valuationTitles.rule40.map,
  'target-dumbbell': (p) => p.targetTitles,
  'recommendation-bars': (p) => p.ratingTitles,
  'ledger-intervals': (p) => p.privateFigures.intervals.titles,
  events: (p) => p.privateFigures.events.titles,
};

// ---- Chinese text in table files ----------------------------------------------------------------------------------------------

const CJK = /[\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]+/g;
/** Wrap runs of Chinese, Japanese or Korean characters outside HTML tags in `<span lang="zh-Hans">` (a screen reader switches voice). */
export const markCjk = (text: string): string =>
  text.includes('lang="zh') ? text : text.split(/(<[^>]*>)/).map((part) => (part.startsWith('<') ? part : part.replace(CJK, (m) => `<span lang="zh-Hans">${m}</span>`))).join('');
const markRows = (rows: Rows): Rows =>
  rows.map((r) => (r && typeof r === 'object' ? Object.fromEntries(Object.entries(r as Record<string, unknown>).map(([k, v]) => [k, typeof v === 'string' ? markCjk(v) : v])) : r));

/**
 * Every file the prebuild writes: chart datasets, table files and title maps. `page` is the module `page.ts` (see `PAGE_TABLES`);
 * without it only the files that do not depend on page.ts are returned.
 */
export function dataFiles(page?: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const add = (name: string, rows: unknown, from: string) => {
    if (name in out) throw new Error(`data file ${name} is produced twice (second time by ${from})`);
    out[name] = rows;
  };
  for (const { files } of built().values()) for (const [name, rows] of Object.entries(files)) add(name, rows, 'a chart dataset');
  const tables: Record<string, Rows> = {};
  for (const [name, rows] of Object.entries(TABLE_FILES)) tables[name] = rows();
  if (page) {
    for (const [name, src] of Object.entries(PAGE_TABLES)) {
      // a function of the page module, or the first of the named exports that is an array (a name that holds a function is not rows)
      const rows = typeof src === 'function' ? src(page) : [src].flat().map((k) => page[k]).find((v) => Array.isArray(v));
      if (Array.isArray(rows)) tables[name] = rows;
      else console.warn(`[cyber-data] page.ts exports no array ${[src].flat().join(' or ')}: the table file ${name}.json is not written`);
    }
    for (const [id, pick] of Object.entries(TITLE_FILES)) add(`titles-${id}`, pick(page), 'a title map'); // an object per file
  }
  for (const [name, rows] of Object.entries(tables)) add(name, markRows(rows), 'a table');
  return out;
}
