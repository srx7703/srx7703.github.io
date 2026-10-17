/**
 * Figure 1, the layer map: one panel per layer of the taxonomy, in the order of the layer code, each a strip of the listed
 * companies by EV / NTM revenue on a log axis. Vega-Lite has no treemap, so this is small multiples with a facet by layer.
 *
 * What a panel shows
 *  - dots: ring A listings that have a multiple. Filled blue is a company whose whole business is security, hollow blue one whose
 *    main line of business is, hollow grey a ring B listing (ring B is classification only and carries no multiple in the
 *    current marts, so no grey dot appears; the encoding is kept for the day it is priced). Rows inside a panel are the listing
 *    markets (United States, other overseas, China A-share, the last drawn as diamonds); the vertical position inside a row is a
 *    fixed jitter from the ticker and carries no information. Thin coverage (fewer than three analysts behind the multiple) is a
 *    dashed outline and a lighter fill, the outline at full opacity (a faded outline fails 3:1 against the ground).
 *  - a dashed line at the layer median, only where three or more dots are in view, labelled with the median and the n behind it;
 *  - the four counts of the panel corner, written into the panel header: listed overseas, listed China (rings A and B, from
 *    `layer_summary`), private overseas, private China. Private companies are counts, never dots on the valuation axis: they
 *    have no market multiple. The ledger behind the private counts is curated from public sources, so the counts are a floor and
 *    not a census, and the figure draws no conclusion from them about where prices are set.
 *  - when the filters leave a panel with no dot, one sentence says so.
 *
 * Driven by the five unbound params of the `valuation` group (see valuation_data.ts); a panel whose layer is not the selected
 * one is removed whole. The facet is sorted by a numeric layer code (`code`), not by an explicit list of panels, so a panel that has
 * no row left drops out, and `autosize.resize` makes the view re-lay itself out (a view otherwise keeps the height it was first drawn
 * at), instead of leaving the other panels' height as blank space. The medians and the
 * empty-panel sentences are computed in the spec from the filtered rows (`joinaggregate`), so they follow the controls. One legend
 * explains the encodings: the exposure scale (`mark`) carries one more row, the thin-coverage class, that no dot is drawn in, and a
 * layer that draws no mark gives that row its dashed outline and lighter fill; the scale is shared by the dots and the legend, so the
 * facet hoists a single legend to the top instead of one per panel. Rule marks carry no text and are hidden from the accessibility
 * tree (`aria: false`).
 */
import { VL_SCHEMA, series, tok } from '../theme';
import type { LayerSummary, Listing, PrivateInterval } from './types';
import {
  type MissingMultiples, type Spec, type ValuationPoint, type ValuationState,
  COV_THIN_LABEL, LAYER_EXPR, MARKETS, MARKET_LABEL, MARKET_SHAPE, MIN_ANALYSTS, REST_EXPR, STATUS_PENDING_LABEL, THIN_DASH, THIN_OPACITY, VALUATION_DEFAULTS,
  andList, capFirst, emptySpec, fmtMult, fmtTimes, layerLabel, layerShort, layersByCode, logTicks, median, missingMultipleNote, missingMultiples,
  numberWord, plural, privateSplit, snapshotDate, valuationParams, valuationPoints, viewOf, wrapWords,
} from './valuation_data';

/** A layer median is drawn and ranked only with at least this many dots in view (the registered minimum group size). */
export const LAYER_MEDIAN_MIN_N = 3;
/**
 * A layer named as the high or low end in the title needs at least this many priced listings: the median of three companies is drawn
 * (three is the registered minimum) but cannot carry a sentence that names the layer as the cheapest or the dearest.
 */
export const LAYER_TITLE_MIN_N = 5;
/** Characters of a layer name per header line. */
const NAME_WIDTH = 40;

export const KIND_WHOLE = 'Ring A, whole business';
export const KIND_MAIN = 'Ring A, main line of business';
export const KIND_B = 'Ring B';

/** One panel: the counts of its corner. `private_*` are null when the private split is not known. */
export type LayerPanel = {
  layer: string; code: number; name: string; panel: string;
  listed_overseas: number; listed_china: number;
  private_overseas: number | null; private_china: number | null; private_total: number;
};

export type LayerMapOpts = {
  /** Private companies with their layer and market (the `private_intervals` mart), for the overseas / China split of the corner counts. */
  privateRows?: Pick<PrivateInterval, 'company' | 'layer' | 'market'>[];
  defaults?: ValuationState;
};

export type LayerMapData = {
  /** The faceted dataset: one `pt` row per dot and one `corner` row per panel. Every row carries the numeric layer `code` the facet is sorted by. */
  rows: Record<string, unknown>[];
  /** The dots (every listing with a multiple, all rings), for titles and the page. */
  points: ValuationPoint[];
  panels: LayerPanel[];
  /** Listings with a multiple whose price follows a pending offer: the multiple reflects the deal, not the market. */
  pending: { ticker: string; name: string; layer: string }[];
  /** Dots with a multiple, before any filter. */
  plotted: number;
  /** True when no listing has a multiple at all: the panels then say so and the page may swap in a sentence. */
  empty: boolean;
  missing: MissingMultiples;
  /** Ring B listings in the pool, and how many of them carry a multiple. */
  ringBListed: number;
  ringBPriced: number;
  /** Whether the corner counts split private companies into overseas and China (false: one total). */
  privateSplit: boolean;
  /** Listings with a multiple whose layer is not in `layer_summary`: not drawn, and said so in the notes. */
  unplaced: number;
  /** Log x range and ticks. */
  x: { domain: [number, number]; ticks: number[] };
  asOf: string | null;
  defaults: ValuationState;
};

const n0 = (x: number | null | undefined): number => x ?? 0;
const maybe = (s: LayerSummary, key: string): number | null => {
  const v = (s as unknown as Record<string, unknown>)[key];
  return typeof v === 'number' ? v : null;
};

/** The corner counts of every layer, in layer-code order. */
export function layerPanels(summary: LayerSummary[], opts: LayerMapOpts = {}): { panels: LayerPanel[]; split: boolean } {
  const split = opts.privateRows ? privateSplit(opts.privateRows) : {};
  let allSplit = true;
  const panels = layersByCode(summary).map((s) => {
    const key = s.layer as string;
    let po = maybe(s, 'n_private_overseas');
    let pc = maybe(s, 'n_private_china');
    if ((po == null || pc == null) && opts.privateRows) {
      const e = split[key] ?? { overseas: 0, china: 0 };
      // use the split only if it accounts for exactly the summary's private count: a mismatch is printed as a total, not as a wrong split
      if (e.overseas + e.china === n0(s.n_private)) { po = e.overseas; pc = e.china; } else { po = null; pc = null; }
    }
    if (po == null || pc == null) { po = null; pc = null; allSplit = false; }
    const listedOverseas = n0(s.n_a_us) + n0(s.n_a_other_overseas) + n0(s.n_b_us) + n0(s.n_b_other_overseas);
    const listedChina = n0(s.n_a_china) + n0(s.n_b_china);
    const head = wrapWords(layerLabel(s), NAME_WIDTH);
    const privateLine = po != null && pc != null ? `Private: ${po} overseas, ${pc} China` : `Private: ${n0(s.n_private)}`;
    return {
      layer: key, code: s.code ?? 0, name: s.name ?? key,
      panel: [...head, `Listed (A+B): ${listedOverseas} overseas, ${listedChina} China`, privateLine].join('\n'),
      listed_overseas: listedOverseas, listed_china: listedChina,
      private_overseas: po, private_china: pc, private_total: n0(s.n_private),
    };
  });
  return { panels, split: allSplit && panels.length > 0 };
}

const kindOf = (p: ValuationPoint): string => (p.ring === 'A' ? (p.purity === 'high' ? KIND_WHOLE : KIND_MAIN) : KIND_B);

/** Data preparation: the corner counts, the dots, and the shared log range. */
export function layerMapRows(listings: Listing[], summary: LayerSummary[], opts: LayerMapOpts = {}): LayerMapData {
  const defaults = opts.defaults ?? VALUATION_DEFAULTS;
  const { panels, split } = layerPanels(summary, opts);
  const panelOf = new Map(panels.map((p) => [p.layer, p]));
  const withMultiple = valuationPoints(listings).filter((p) => p.ev_ntm != null);
  const points = withMultiple.filter((p) => panelOf.has(p.layer));
  const rows: Record<string, unknown>[] = [];
  for (const p of points) {
    const lane = Math.max(0, MARKETS.indexOf(p.market as (typeof MARKETS)[number]));
    const pa = panelOf.get(p.layer) as LayerPanel;
    rows.push({
      kind: 'pt',
      panel: pa.panel, code: pa.code,
      layer: p.layer, ring: p.ring, market: p.market, purity: p.purity, cov3: p.cov3,
      ticker: p.ticker, name: p.name, ev_ntm: p.ev_ntm,
      y: Math.round((lane + p.jit * 0.3) * 1000) / 1000,
      mark: kindOf(p), market_label: MARKET_LABEL[p.market] ?? p.market, n: p.n, status: p.status,
    });
  }
  for (const pa of panels) {
    rows.push({
      kind: 'corner', panel: pa.panel, code: pa.code, layer: pa.layer,
      note: pa.listed_overseas + pa.listed_china === 0 ? 'No listed company in this layer' : 'No listing with a multiple in this view',
    });
  }
  const evs = points.map((p) => p.ev_ntm as number);
  const lo = evs.length ? Math.min(...evs) / 1.3 : 0.3;
  const hi = evs.length ? Math.max(...evs) * 1.3 : 40;
  const ring = listings.filter((l) => l.ring === 'B');
  return {
    rows, points, panels,
    pending: points.filter((p) => p.status === STATUS_PENDING_LABEL).map((p) => ({ ticker: p.ticker, name: p.name, layer: p.layer })),
    plotted: points.length,
    empty: points.length === 0,
    missing: missingMultiples(listings),
    ringBListed: ring.length,
    ringBPriced: ring.filter((l) => l.ev_ntm != null && l.ev_ntm > 0).length,
    privateSplit: split,
    unplaced: withMultiple.length - points.length,
    x: { domain: [lo, hi], ticks: logTicks(lo, hi) },
    asOf: snapshotDate(listings),
    defaults,
  };
}

const MEDIAN_EDGE = LAYER_MEDIAN_MIN_N;

/** Medians of the layers with at least `minN` dots in view (default: enough to draw the median), in the order of the layer code. */
export function layerMedians(data: LayerMapData, state: ValuationState = data.defaults, minN: number = MEDIAN_EDGE) {
  const view = viewOf(data.points, state);
  return data.panels.flatMap((pa) => {
    const xs = view.filter((p) => p.layer === pa.layer).map((p) => p.ev_ntm as number);
    const med = median(xs);
    return med != null && xs.length >= minN ? [{ layer: pa.layer, name: pa.name, median: med, n: xs.length }] : [];
  });
}

/** Layers where the ledger holds more private companies than the pool holds listed ones (rings A and B), from the corner counts. */
export const privateOutnumber = (data: LayerMapData) => data.panels.filter((p) => p.private_total > p.listed_overseas + p.listed_china);

/**
 * Title: what the medians of the layers in view say, and (for the whole map) in how many layers the ledger holds more private companies than
 * the pool holds listed ones. Every number is computed; the direction of every comparison is read off the numbers. A layer is named as an
 * end of the range only with LAYER_TITLE_MIN_N or more priced listings, and its count is printed beside it.
 */
export function layerMapFinding(data: LayerMapData, state: ValuationState = data.defaults): string {
  const view = viewOf(data.points, state);
  if (!view.length) return 'No listed company in this view has a multiple';
  const meds = layerMedians(data, state, LAYER_TITLE_MIN_N);
  const whole = state.layer === 'all' && state.market === 'all';
  const tail = whole && data.panels.length
    ? `; the ledger holds more private companies than the pool holds listed ones in ${privateOutnumber(data).length} of ${data.panels.length} layers`
    : '';
  const need = `${numberWord(LAYER_TITLE_MIN_N)} or more priced listings`;
  if (meds.length >= 2) {
    const sorted = [...meds].sort((a, b) => a.median - b.median);
    const lo = sorted[0];
    const hi = sorted[sorted.length - 1];
    return `Layer medians run from ${fmtTimes(lo.median)} revenue (${layerShort(lo.name)}, ${plural(lo.n, 'listing')}) to ${fmtMult(hi.median)} (${layerShort(hi.name)}, ${plural(hi.n, 'listing')}) across the ${meds.length} layers with ${need}${tail}`;
  }
  if (meds.length === 1) return `Only ${layerShort(meds[0].name)} has ${need} in this view, at a median ${fmtTimes(meds[0].median)} revenue${tail}`;
  return `No layer has ${need} in this view (${plural(view.length, 'listing')} with a multiple)${tail}`;
}

/** Subtitle: metric, unit and scale, in one sentence. Marks, counts and what is left out are in `layerMapNotes`. */
export function layerMapSubtitle(data: LayerMapData): string {
  if (data.empty) return 'Enterprise value over next-twelve-month revenue, log scale. No listing has a multiple on this snapshot, so the panels show only their counts.';
  return 'Enterprise value over next-twelve-month revenue, log scale: one dot per listed company with a multiple, in the panel of its layer.';
}

/**
 * What a reader needs beyond the subtitle, as plain text (no markup): paragraphs separated by a blank line. The page puts it in the figure's
 * "How to read this" block. Counts here describe the full pool; the title counts the view the controls select.
 */
export function layerMapNotes(data: LayerMapData): string {
  const paras: string[] = [];
  const few = numberWord(MIN_ANALYSTS);
  const med = numberWord(LAYER_MEDIAN_MIN_N);
  if (!data.empty) {
    paras.push([
      `Each panel is a layer of the taxonomy and each dot a listed company with a multiple, on one log axis (${data.plotted} in the full pool; the title counts the view the controls select).`,
      'Rows are listing markets, A-shares drawn as diamonds, and height within a row is jitter, not data.',
      `Filled is a company whose whole business is security; hollow, one whose main line of business is${data.ringBPriced ? '; hollow grey, a ring B listing' : ''}.`,
      `A dashed outline and a lighter fill mean fewer than ${few} analysts behind the multiple: Yahoo's revenue analysts, or East Money's brokers for an A-share.`,
      `A dashed vertical line is the layer median where ${med} or more dots show, labelled with the median and the number of companies behind it.`,
    ].join(' '));
  }
  paras.push([
    `Counts at the head of each panel are listed companies (rings A and B) and private companies, overseas and in China${data.privateSplit ? '' : ' (one total for private companies: the split is not in this mart)'}.`,
    'The private counts come from a ledger curated from public sources, so they are a floor and not a census: they show where the ledger has companies, not where the market is private.',
    'Private companies have no market multiple, so they are counts, not dots.',
  ].join(' '));
  if (data.pending.length) {
    const one = data.pending.length === 1;
    paras.push(`${andList(data.pending.map((p) => p.name))} ${one ? 'is' : 'are'} a pending acquisition: ${one ? 'its' : 'their'} price follows the offer, so ${one ? 'its multiple reflects' : 'their multiples reflect'} the deal and not the market. ${one ? 'It is' : 'They are'} still counted in the layer median.`);
  }
  paras.push('Layer medians differ, but layers also differ in growth: the next figure asks whether a layer adds anything once growth is allowed for.');
  const left: string[] = [];
  const miss = missingMultipleNote(data.missing);
  if (miss) left.push(`Not plotted: ${miss}.`);
  if (data.unplaced) left.push(`${capFirst(plural(data.unplaced, 'listing'))} with a multiple ${data.unplaced === 1 ? 'is' : 'are'} in a layer the summary does not list and ${data.unplaced === 1 ? 'is' : 'are'} not drawn.`);
  if (data.ringBListed && !data.ringBPriced) left.push(`Ring B (${plural(data.ringBListed, 'listing')}) has no multiple and appears only in the counts.`);
  if (left.length) paras.push(left.join(' '));
  return paras.join('\n\n');
}

/** Vertical range of a panel in lane units: lanes are at 0, 1, 2; the band above lane 0 holds the median label. */
const LANE_DOMAIN: [number, number] = [-1.3, 2.6];
const LANE_PX = 16;
const laneScale = { domain: LANE_DOMAIN, reverse: true };

const laneLabelExpr = `datum.value == 0 ? ${JSON.stringify(MARKET_LABEL.us)} : datum.value == 1 ? ${JSON.stringify(MARKET_LABEL.other_overseas)} : ${JSON.stringify(MARKET_LABEL.china)}`;

const tooltip = [
  { field: 'name', type: 'nominal', title: 'Company' },
  { field: 'ticker', type: 'nominal', title: 'Listing' },
  { field: 'ev_ntm', type: 'quantitative', title: 'EV / NTM revenue', format: '.1f' },
  { field: 'market_label', type: 'nominal', title: 'Market' },
  { field: 'mark', type: 'nominal', title: 'Exposure' },
  { field: 'n', type: 'quantitative', title: 'Analysts behind the multiple' },
  { field: 'status', type: 'nominal', title: 'Status' },
];

/**
 * The Vega-Lite spec (facet by layer, one column). Takes the prepared `layerMapRows(...)` object, or, as a convenience,
 * `(listings, summary, rowOpts)` and prepares it. `{ defaults }` as the second argument sets the params' initial values.
 */
export function layerMapSpec(data: LayerMapData | Listing[], second?: LayerSummary[] | { defaults?: ValuationState }, rowOpts: LayerMapOpts = {}): Spec {
  const prepared = Array.isArray(data) ? layerMapRows(data, Array.isArray(second) ? second : [], rowOpts) : data;
  const opts = Array.isArray(second) ? {} : second ?? {};
  return buildLayerMapSpec(prepared, opts);
}

function buildLayerMapSpec(data: LayerMapData, opts: { defaults?: ValuationState }): Spec {
  if (!data.panels.length) return emptySpec('No layer is defined in this snapshot, so there is nothing to map.');
  const defaults = opts.defaults ?? data.defaults;
  const kinds = [KIND_WHOLE, KIND_MAIN, ...(data.ringBPriced ? [KIND_B] : []), COV_THIN_LABEL];
  const solid = [1, 0];
  const fillRange = [series.a, tok('bg'), ...(data.ringBPriced ? [tok('bg')] : []), tok('ink-3')];
  const strokeRange = [series.a, series.a, ...(data.ringBPriced ? [series.neutral] : []), tok('ink-3')];
  const fillScale = { domain: kinds, range: fillRange };
  const strokeScale = { domain: kinds, range: strokeRange };
  // one legend for the exposure scale, written once and given to both layers that use it (a conflicting `legend: null` would disable it)
  const legend = {
    title: null, orient: 'top', direction: 'horizontal', columns: { expr: 'child_width < 420 ? 1 : 2' }, symbolType: 'circle', symbolStrokeWidth: 1.6, symbolSize: 90,
    labelFontSize: 11, labelLimit: 0, columnPadding: 16, rowPadding: 3,
  };
  const logX = { type: 'log', domain: data.x.domain, nice: false };
  const x = {
    field: 'ev_ntm', type: 'quantitative', title: null,
    scale: logX,
    axis: { values: data.x.ticks, labelExpr: "datum.value + 'x'", grid: true, labelFontSize: 11, tickCount: 5 },
  };
  // the layer that draws nothing: it gives each row of the exposure scale its dash and fill opacity in the legend (the dots set theirs by condition)
  const legendLayer: Spec = {
    // no data of its own (a second source for the same scale would make the facet warn about merged domains): it takes the facet's rows and keeps none
    transform: [{ filter: 'false' }],
    mark: { type: 'point', filled: true, aria: false },
    encoding: {
      fill: { field: 'mark', type: 'nominal', scale: fillScale, legend },
      stroke: { field: 'mark', type: 'nominal', scale: strokeScale },
      strokeDash: { field: 'mark', type: 'nominal', scale: { domain: kinds, range: kinds.map((k) => (k === COV_THIN_LABEL ? [...THIN_DASH] : solid)) } },
      fillOpacity: { field: 'mark', type: 'ordinal', scale: { domain: kinds, range: kinds.map((k) => (k === COV_THIN_LABEL ? THIN_OPACITY : 1)) } },
    },
  };
  return {
    $schema: VL_SCHEMA,
    params: valuationParams(defaults),
    // a view lays itself out once by default; `resize` re-lays it out when a control removes panels, so the figure shrinks instead of keeping blank space
    autosize: { type: 'pad', resize: true },
    data: { values: data.rows },
    title: { text: 'EV / NTM revenue (x, log scale)', orient: 'bottom', anchor: 'middle', fontSize: 11, fontWeight: 'normal', color: tok('ink-2'), offset: 4 },
    transform: [
      { filter: LAYER_EXPR },
      { calculate: `(datum.kind == 'pt' && ${REST_EXPR}) ? 1 : 0`, as: 'show' },
      { calculate: 'datum.show == 1 ? datum.ev_ntm : null', as: 'ev_show' },
      {
        joinaggregate: [
          { op: 'sum', field: 'show', as: 'n_show' },
          { op: 'valid', field: 'ev_show', as: 'n_ev' },
          { op: 'median', field: 'ev_show', as: 'med' },
        ],
        groupby: ['panel'],
      },
    ],
    facet: {
      row: {
        field: 'panel', type: 'nominal', title: null,
        // sorted by the numeric layer code, never by a list of every panel: a panel with no row left in the view drops out and the figure re-lays out
        sort: { field: 'code', op: 'min' },
        header: {
          labelAngle: 0, labelAlign: 'left', labelAnchor: 'start', labelOrient: 'top', labelFontSize: 11, labelPadding: 4,
          labelLimit: 0, labelLineHeight: 13, labelColor: tok('ink-2'), labelExpr: "split(datum.value, '\\n')",
        },
      },
    },
    resolve: { axis: { x: 'independent' } },
    spacing: 10,
    _facetColumns: 1,
    spec: {
      height: Math.round((LANE_DOMAIN[1] - LANE_DOMAIN[0]) * LANE_PX),
      layer: [
        ...[0.5, 1.5].map((at) => ({
          // lane separators, one pair per panel
          transform: [{ filter: "datum.kind == 'corner'" }],
          mark: { type: 'rule', strokeDash: [2, 3], color: tok('grid'), strokeWidth: 1, aria: false },
          encoding: { y: { value: Math.round((at - LANE_DOMAIN[0]) * LANE_PX * 100) / 100 } },
        })),
        {
          transform: [{ filter: "datum.kind == 'corner'" }, { filter: `datum.n_ev >= ${LAYER_MEDIAN_MIN_N}` }],
          mark: { type: 'rule', strokeDash: [5, 3], strokeWidth: 1.4, color: tok('ink-2'), aria: false },
          encoding: { x: { field: 'med', type: 'quantitative', scale: logX } },
        },
        ...(['left', 'right'] as const).map((align) => ({
          // the median in words, in the free band above the first lane; right-aligned past the middle of the axis so it never leaves the panel
          transform: [
            { filter: "datum.kind == 'corner'" },
            { filter: `datum.n_ev >= ${LAYER_MEDIAN_MIN_N} && ${align === 'left' ? 'datum.med <= ' : 'datum.med > '}${Math.sqrt(data.x.domain[0] * data.x.domain[1])}` },
            { calculate: "'median ' + format(datum.med, '.1f') + 'x, n=' + datum.n_ev", as: 'med_text' },
          ],
          mark: { type: 'text', align, baseline: 'top', dx: align === 'left' ? 4 : -4, dy: 0, fontSize: 11, color: tok('ink-2') },
          encoding: {
            x: { field: 'med', type: 'quantitative', scale: logX },
            y: { value: 1 },
            text: { field: 'med_text', type: 'nominal' },
          },
        })),
        {
          transform: [{ filter: 'datum.show == 1' }],
          mark: { type: 'point', size: 62, strokeWidth: 1.6, filled: true, clip: true },
          encoding: {
            x,
            y: {
              field: 'y', type: 'quantitative', title: null,
              scale: laneScale,
              axis: { values: [0, 1, 2], labelExpr: laneLabelExpr, grid: false, ticks: false, domain: false, labelFontSize: 11, labelPadding: 4, labelOverlap: false },
            },
            shape: { field: 'market', type: 'nominal', scale: { domain: [...MARKETS], range: MARKETS.map((m) => MARKET_SHAPE[m]) }, legend: null },
            fill: { field: 'mark', type: 'nominal', scale: fillScale, legend },
            stroke: { field: 'mark', type: 'nominal', scale: strokeScale },
            // thin coverage: a dashed outline and a lighter fill; the outline keeps full opacity
            fillOpacity: { condition: { test: '!datum.cov3', value: THIN_OPACITY }, value: 1 },
            strokeDash: { condition: { test: '!datum.cov3', value: [...THIN_DASH] }, value: solid },
            tooltip,
          },
        },
        {
          transform: [{ filter: "datum.kind == 'corner'" }, { filter: 'datum.n_show == 0' }],
          mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 11, color: tok('ink-3'), limit: { expr: 'width - 12' } },
          encoding: { x: { value: 6 }, y: { value: 34 }, text: { field: 'note', type: 'nominal' } },
        },
        legendLayer,
      ],
    },
  };
}
