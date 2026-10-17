import { VL_SCHEMA, series, tok } from '../theme';
import type { Listing, TargetRow } from './types';
import { THIN_DASH, THIN_OPACITY } from './valuation_data';
import {
  type ControlName, type MarketGroup, type TargetPoint, type TargetsData, type TargetsState,
  FILL_WHOLE, LAYER_ORDER, MARKET_LABEL, MARKET_SHAPE, SKIP_TEXT, SUSPECT_RATIO, andList, buildTargetsData, cap, count,
  emptyViewReason, exposureLabel, fmtUpside, isWholeBusiness, median, numWord, pointInState, targetsParams, targetsState, titleMapFor, viewPhrase,
} from './targets_data';

/** Figure 4: the controls that drive it (the same names are unbound top-level params of the spec). */
export const DUMBBELL_KEYS: ControlName[] = ['layer', 'market', 'coverage'];

/** The first panel's header (lines joined by `|`): every listing outside China, Yahoo's targets in the currency each trades in. */
export const MAIN_PANEL_LABEL = 'Listed outside China|Yahoo targets';
/** The A-share panel's header. Static on purpose: it makes no claim about how many analysts, which the marks and the table carry. */
export const CN_PANEL_LABEL = 'China A-share listings|Yahoo targets|used in no registered claim';

const ZWSP = '​';
/** Characters per line for a sentence printed inside a panel (about 190px of 10.5px italic, which fits a phone). */
const WRAP = 44;
/** Pixels at the right of every panel kept for the column of mean targets (the widest value, `+2,457%`, is about 38px of 10px type, and the arrow beside it needs room). */
const COLUMN_PX = 50;
/** Panels narrower than this many pixels (the whole panel, column included) drop the lowest axis label so the 0% label stays clear. */
const NARROW_PX = 270;

export interface DumbbellRows extends TargetsData {
  /** The x axis: lowest whisker rounded down, and the highest covered whisker rounded up, both to quarters. */
  domain: [number, number];
  /** Header of the A-share panel. */
  cnLabel: string;
  /** The covered listing whose whisker sets the top of the axis (null when no listing is covered). */
  topCovered: { name: string; ticker: string; value: number } | null;
}

const floorTo = (x: number, step: number) => Math.floor(x / step + 1e-9) * step;
const ceilTo = (x: number, step: number) => Math.ceil(x / step - 1e-9) * step;

/** The A-share panel's header (kept as a function for callers that passed the points). */
export const aShareLabel = (_points?: TargetPoint[]): string => CN_PANEL_LABEL;

/** The two coverage labels the legend and the rows share. */
export const coverageLabels = (rows: Pick<TargetsData, 'thinBelow'>) => ({
  covered: `${cap(numWord(rows.thinBelow))} or more analysts`,
  thin: `Fewer than ${numWord(rows.thinBelow)} analysts`,
});

/** Data preparation: one point per listing with an implied upside, and the axis the points share. */
export function targetDumbbellRows(targets: TargetRow[], listings: Listing[] = []): DumbbellRows {
  const data = buildTargetsData(targets, listings);
  const pts = data.points;
  const lows = pts.flatMap((p) => [p.low, p.upside, 0].filter((v): v is number => v != null));
  const covered = pts.filter((p) => !p.thin);
  const basis = covered.length ? covered : pts;
  const rightEnd = (p: TargetPoint) => Math.max(p.high ?? p.upside, p.upside);
  const top = basis.length ? basis.reduce((m, p) => (rightEnd(p) > rightEnd(m) ? p : m), basis[0]) : null;
  const dLo = Math.min(-0.25, floorTo(Math.min(...(lows.length ? lows : [0])), 0.25));
  const dHi = Math.max(0.5, ceilTo(top ? Math.max(0, rightEnd(top)) : 0, 0.25));
  return {
    ...data, domain: [dLo, dHi], cnLabel: CN_PANEL_LABEL,
    topCovered: covered.length && top ? { name: top.name, ticker: top.ticker, value: rightEnd(top) } : null,
  };
}

/** Numbers the page's prose quotes, all computed from the rows (never typed). */
export function targetStats(rows: DumbbellRows) {
  const main = rows.points.filter((p) => p.panel === 'main');
  const covered = main.filter((p) => !p.thin);
  const thin = main.filter((p) => p.thin);
  const ext = (xs: TargetPoint[], dir: 1 | -1) => (xs.length ? xs.reduce((m, p) => (dir * p.upside > dir * m.upside ? p : m), xs[0]) : null);
  const pick = (p: TargetPoint | null) => (p ? { name: p.name, ticker: p.ticker, value: p.upside, n: p.n_opinions } : null);
  const mainLine = main.filter((p) => !isWholeBusiness(p.purity));
  return {
    n: main.length, nCovered: covered.length, nThin: thin.length,
    medianAll: median(main.map((p) => p.upside)), medianCovered: median(covered.map((p) => p.upside)),
    coveredMost: pick(ext(covered, 1)), coveredLeast: pick(ext(covered, -1)),
    thinMost: pick(ext(thin, 1)), thinLeast: pick(ext(thin, -1)),
    nMainLine: mainLine.length, nMainLineCovered: mainLine.filter((p) => !p.thin).length,
    medianWholeCovered: median(covered.filter((p) => isWholeBusiness(p.purity)).map((p) => p.upside)),
    suspects: rows.points.filter((p) => p.suspect).map((p) => ({ name: p.name, ticker: p.ticker, value: p.upside, n: p.n_opinions })),
    nCn: rows.points.filter((p) => p.panel === 'cn').length,
    nNotQ4: covered.filter((p) => p.in_q4_list === false).map((p) => p.name),
  };
}

// ---- spec ------------------------------------------------------------------------------------------------------------

const wrapLines = (text: string, width = WRAP): string[] => {
  const out: string[] = [];
  let line = '';
  for (const w of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + w.length > width) { out.push(line); line = w; } else line = line ? `${line} ${w}` : w;
  }
  if (line) out.push(line);
  return out.length ? out : [''];
};

/** The sentence a view shows when the first panel has nothing to draw, or null when it has rows. */
export function dumbbellReason(rows: DumbbellRows, st: TargetsState): string | null {
  const main = rows.points.filter((p) => p.panel === 'main');
  if (!rows.points.length) return 'No analyst target prices are published on this build.';
  const a = main.filter((p) => st.layer === 'all' || p.layer === st.layer);
  const b = a.filter((p) => st.market === 'all' || p.market_group === st.market);
  const c = b.filter((p) => st.coverage === 'all' || !p.thin);
  if (c.length) return null;
  return emptyViewReason({ noun: 'Yahoo target price', has: 'a Yahoo target price' }, st, {
    layer: a.length, market: b.length, coverage: c.length, thin: b.length, listed: rows.listedByLayer[st.layer] ?? 0,
  });
}

/** Axis ticks: the low end, then every whole 100%. Four labels fit a phone panel; half-steps hid the 0% and +100% labels. */
export function dumbbellTicks(domain: [number, number]): number[] {
  const [lo, hi] = domain;
  const ticks = new Set<number>();
  if (lo < 0) ticks.add(+lo.toFixed(2));
  for (let v = 0; v <= hi + 1e-9; v += 1) ticks.add(v);
  if (hi - Math.floor(hi + 1e-9) > 0.4) ticks.add(+hi.toFixed(2));
  return Array.from(ticks).sort((x, y) => x - y);
}

/** The sentence a screen reader gets for one listing's mark (the chart's own field names never reach it). */
export function dumbbellAria(p: TargetPoint): string {
  const range = p.low != null && p.high != null && p.high > p.low ? `, lowest single target ${fmtUpside(p.low)}, highest ${fmtUpside(p.high)}` : '';
  const analysts = p.n_opinions == null ? 'number of target analysts not stated' : `${count(p.n_opinions, 'target analyst')}${p.thin ? ' (thin coverage)' : ''}`;
  return `${p.name}, ${p.ticker}, ${p.market_label}, ${p.exposure}: mean target implies ${fmtUpside(p.upside)} against the price${range}; ${analysts}${p.suspect ? '; likely stale or split-affected, unverified' : ''}`;
}

/** Rows the spec carries: one per point, plus the reason sentences of every view the first panel cannot draw. */
export function dumbbellSpecRows(rows: DumbbellRows) {
  const [, dHi] = rows.domain;
  const cov = coverageLabels(rows);
  const pts = rows.points.map((p) => {
    const off = p.upside > dHi;
    const hiOff = (p.high ?? p.upside) > dHi;
    return {
      k: 'pt',
      panel_label: p.panel === 'cn' ? rows.cnLabel : MAIN_PANEL_LABEL,
      ticker: p.ticker, name: p.name, label: p.label, layer: p.layer, layer_label: p.layer_label,
      market_group: p.market_group, market_label: p.market_label, thin: p.thin,
      exposure: p.exposure, exposure_label: isWholeBusiness(p.purity) ? FILL_WHOLE : p.exposure,
      coverage_label: p.thin ? cov.thin : cov.covered,
      n_opinions: p.n_opinions, currency: p.currency,
      quote: p.pence ? 'pence (the ratio needs no conversion)' : (p.currency ?? ''),
      status: p.status && p.status !== 'listed' ? p.status.replace(/_/g, ' ') : null,
      suspect: p.suspect ? 'likely stale or split-affected, unverified' : null,
      aria: dumbbellAria(p),
      upside: p.upside, low: p.low, high: p.high, off, hi_off: hiOff && !off,
      edge: dHi, x_mark: off ? dHi : p.upside,
      lab: fmtUpside(p.upside),
      ord: -p.upside,
    };
  });
  const states: Record<string, unknown>[] = [];
  const layers = ['all', ...LAYER_ORDER, ...Array.from(new Set(rows.points.map((p) => p.layer))).filter((l) => !LAYER_ORDER.includes(l))];
  for (const layer of layers) {
    for (const market of ['all', 'us', 'other_overseas']) {
      for (const coverage of ['all', 'three']) {
        const reason = dumbbellReason(rows, targetsState({ layer, market, coverage }));
        if (reason) wrapLines(reason).forEach((line, i) => states.push({ k: 'state', panel_label: MAIN_PANEL_LABEL, layer, market, coverage, label: ZWSP.repeat(i + 1), line, ord: i }));
      }
    }
  }
  return [...pts, ...states];
}

/** The Vega expression a row must satisfy at the current params; identical in meaning to `pointInState`. */
export const DUMBBELL_FILTER = "datum.k == 'state' ? (datum.layer == layer && datum.market == market && datum.coverage == coverage)"
  + " : ((layer == 'all' || datum.layer == layer) && (market == 'all' || datum.market_group == market) && (coverage == 'all' || !datum.thin))";

const SHAPE_DOMAIN = (['us', 'other_overseas', 'china'] as MarketGroup[]).map((m) => MARKET_LABEL[m]);
const SHAPE_RANGE = (['us', 'other_overseas', 'china'] as MarketGroup[]).map((m) => MARKET_SHAPE[m]);
/** Dash patterns: solid for a covered listing, dashed for a thin one (the page-wide mark of thin coverage, with a lighter fill, never opacity alone). */
const SOLID = [1, 0];
const DASHED = [...THIN_DASH];
/** Dot area in px squared. */
const DOT_SIZE = 66;
/** Area of the arrow that stands for a mean beyond the axis. */
const ARROW_SIZE = 34;

/**
 * Figure 4. One dumbbell per listing outside China, sorted by mean implied upside: the dot is the mean target over the
 * price minus one, the whisker runs from the lowest to the highest single target computed the same way. Only the ratio is
 * drawn (currencies differ, and a pence quote needs no conversion for a ratio). Markets are told apart by shape; exposure by
 * the fill (filled and blue: security is the whole business; hollow and grey: its main line); thin coverage (fewer than three
 * target analysts) by a dashed outline and whisker and a lighter fill, as in the valuation figures (the outline keeps full strength,
 * only the fill is lightened).
 * The mean is printed in its own column at the right of the panel, headed `mean`, so no number reads as a whisker end.
 * The A-share listings' Yahoo targets sit on their own small panel under their own header and move with the layer and coverage
 * controls but disappear when a market is picked.
 *
 * Linear axis clipped at the top of the highest covered whisker: a listing whose mean (or whisker) goes beyond it is marked
 * with an arrow on the edge, never with a dot at a value it does not have, and its true mean is in the column. A view with
 * nothing to draw prints one sentence saying why.
 *
 * Dashboard group `targets`, unbound top-level params `layer`, `market`, `coverage`. The first-panel facet header and the
 * second's are two rows of one row-facet, with independent y scales so each panel is as tall as its own rows.
 */
export function targetDumbbellSpec(rows: DumbbellRows, opts: { state?: Partial<TargetsState> } = {}) {
  const [dLo, dHi] = rows.domain;
  const values = dumbbellSpecRows(rows);
  const cov = coverageLabels(rows);
  const wholeTest = `datum.exposure_label == ${JSON.stringify(FILL_WHOLE)}`;
  const x = (field: string) => ({
    field, type: 'quantitative',
    title: 'Analyst mean target vs price (% above or below)',
    // the right COLUMN_PX of every panel is the column of mean targets, outside the scale
    scale: { domain: [dLo, dHi], range: [0, { signal: `child_width - ${COLUMN_PX}` }], clamp: true, nice: false },
    // the lowest label is dropped when the plot is too narrow for it to clear the 0% label (a phone panel); the gridline stays
    axis: { values: dumbbellTicks(rows.domain), labelExpr: `datum.value == 0 ? '0%' : (datum.value < 0 && child_width < ${NARROW_PX} ? '' : format(datum.value, '+.0%'))`, grid: true, gridColor: tok('grid'), labelFontSize: 10.5, titleFontSize: 11, labelFlush: true },
  });
  const y = {
    field: 'label', type: 'nominal', title: null,
    sort: { field: 'ord', op: 'min', order: 'ascending' },
    axis: { labelLimit: 84, labelFontSize: 10, ticks: false, domain: false, grid: true, gridColor: tok('grid'), gridOpacity: 0.5, labelPadding: 4 },
  };
  const tooltip = [
    { field: 'name', type: 'nominal', title: 'Company' },
    { field: 'ticker', type: 'nominal', title: 'Listing' },
    { field: 'market_label', type: 'nominal', title: 'Market' },
    { field: 'layer_label', type: 'nominal', title: 'Layer' },
    { field: 'exposure', type: 'nominal', title: 'Security exposure' },
    { field: 'upside', type: 'quantitative', title: 'Mean target vs price', format: '+.1%' },
    { field: 'low', type: 'quantitative', title: 'Lowest target vs price', format: '+.1%' },
    { field: 'high', type: 'quantitative', title: 'Highest target vs price', format: '+.1%' },
    { field: 'n_opinions', type: 'quantitative', title: 'Target analysts' },
    { field: 'quote', type: 'nominal', title: 'Quoted in' },
    { field: 'status', type: 'nominal', title: 'Status' },
    { field: 'suspect', type: 'nominal', title: 'Target check' },
  ];
  // Two legends side by side at the top (a third would be wider than a phone, and Vega-Lite cannot stack legends that share an orient): the
  // market shapes, and one for the fill and size of a dot, whose three entries are drawn by a layer that plots nothing (see the legend layer).
  const legendBase = { title: null, orient: 'top', direction: 'vertical', columns: 1, labelFontSize: 11, labelLimit: 200, offset: 8 };
  const keyWhole = FILL_WHOLE;
  const keyMain = exposureLabel('main');
  const keyThin = cov.thin;
  const dotKeys = [keyWhole, keyMain, keyThin];
  const dotLegend = { ...legendBase, symbolType: 'circle', symbolStrokeWidth: 1.6 };
  const marketLegend = { ...legendBase, symbolFillColor: 'transparent', symbolStrokeColor: tok('ink-2'), symbolStrokeWidth: 1.6, symbolSize: DOT_SIZE };
  const exposureStrokeValue = { condition: { test: wholeTest, value: series.a }, value: series.neutral };
  const hasWhisker = "datum.k == 'pt' && datum.low != null && datum.high != null && datum.high > datum.low";
  return {
    $schema: VL_SCHEMA,
    params: targetsParams(DUMBBELL_KEYS, opts.state),
    data: { values },
    transform: [{ filter: DUMBBELL_FILTER }],
    facet: {
      row: {
        field: 'panel_label', type: 'nominal', title: null, sort: [MAIN_PANEL_LABEL, rows.cnLabel],
        header: { labelOrient: 'top', labelAnchor: 'start', labelAngle: 0, labelAlign: 'left', labelFontSize: 12, labelPadding: 14, labelLimit: 0, labelColor: tok('ink'), labelExpr: "split(datum.label, '|')", labelLineHeight: 14 },
      },
    },
    _facetColumns: 1,
    // re-measure the panels when a control changes the number of rows (the default only measures once)
    autosize: { type: 'pad', resize: true },
    padding: { left: 0, right: 0, top: 0, bottom: 2 },
    spec: {
      width: 300,
      height: { step: 15 },
      layer: [
        {
          // the price: a target at the price is 0%. A reference line with no text of its own, hidden from the accessibility tree
          transform: [{ filter: "datum.k == 'pt'" }, { aggregate: [{ op: 'count', as: 'n' }] }, { calculate: '0', as: 'zero' }],
          mark: { type: 'rule', color: tok('ink-3'), strokeWidth: 1, aria: false },
          encoding: { x: x('zero') },
        },
        {
          // whisker: lowest to highest single target; dashed when fewer than three analysts stand behind it
          transform: [{ filter: hasWhisker }],
          mark: { type: 'rule', strokeWidth: 1.6, opacity: 1, aria: false },
          encoding: {
            x: x('low'), x2: { field: 'high' }, y, tooltip,
            stroke: exposureStrokeValue,
            strokeDash: { condition: { test: 'datum.thin', value: DASHED }, value: SOLID },
          },
        },
        {
          // whisker caps, only where the end is on the axis
          transform: [
            { filter: hasWhisker },
            { fold: ['low', 'high'], as: ['end', 'cap'] },
            { filter: 'datum.cap <= datum.edge' },
          ],
          mark: { type: 'tick', orient: 'vertical', thickness: 1.6, size: 7, opacity: 1, aria: false },
          encoding: { x: x('cap'), y, stroke: exposureStrokeValue },
        },
        {
          // the mean: filled blue when security is the whole business, hollow grey when it is the main line; thin coverage keeps the
          // full-strength outline, dashes it and lightens only the fill. A mean beyond the axis is an arrow on the edge instead (never a dot
          // at a value the listing does not have); its true value is in the column
          transform: [{ filter: "datum.k == 'pt'" }],
          mark: { type: 'point', filled: true, strokeWidth: 1.6, opacity: 1 },
          encoding: {
            x: x('x_mark'), y, tooltip, description: { field: 'aria', type: 'nominal' },
            shape: {
              condition: [
                { test: 'datum.off', value: 'triangle-right' },
                { test: "datum.market_group == 'us'", value: MARKET_SHAPE.us },
                { test: "datum.market_group == 'other_overseas'", value: MARKET_SHAPE.other_overseas },
              ],
              value: MARKET_SHAPE.china,
            },
            fill: { condition: [{ test: 'datum.off', value: tok('ink-2') }, { test: wholeTest, value: series.a }], value: tok('bg') },
            stroke: { condition: [{ test: 'datum.off', value: tok('ink-2') }, { test: wholeTest, value: series.a }], value: series.neutral },
            fillOpacity: { condition: { test: `datum.thin && !datum.off && ${wholeTest}`, value: THIN_OPACITY }, value: 1 },
            strokeDash: { condition: { test: 'datum.thin && !datum.off', value: DASHED }, value: SOLID },
            size: { condition: { test: 'datum.off', value: ARROW_SIZE }, value: DOT_SIZE },
          },
        },
        {
          // the two legends (the dot's exposure and thin coverage, and the market's shape): this layer plots nothing (its filter passes no row); its scales have explicit domains, which is all a legend needs.
          // Using separate fields keeps the plotted dots free of any scale, so no legend can be merged or disabled by accident.
          transform: [{ filter: 'false' }],
          mark: { type: 'point', filled: true, strokeWidth: 1.6, opacity: 1, aria: false },
          encoding: {
            y,
            shape: { field: 'legend_market', type: 'nominal', scale: { domain: SHAPE_DOMAIN, range: SHAPE_RANGE }, legend: marketLegend },
            fill: { field: 'dot_key', type: 'ordinal', sort: dotKeys, scale: { type: 'ordinal', domain: dotKeys, range: [series.a, tok('bg'), 'transparent'] }, legend: dotLegend },
            stroke: { field: 'dot_key', type: 'ordinal', sort: dotKeys, scale: { type: 'ordinal', domain: dotKeys, range: [series.a, series.neutral, series.a] }, legend: dotLegend },
            strokeDash: { field: 'dot_key', type: 'ordinal', sort: dotKeys, scale: { type: 'ordinal', domain: dotKeys, range: [SOLID, SOLID, DASHED] }, legend: dotLegend },
          },
        },
        {
          // the same arrow where only the whisker runs beyond the axis (the dot is drawn and carries the description)
          transform: [{ filter: "datum.k == 'pt' && datum.hi_off" }],
          mark: { type: 'point', shape: 'triangle-right', filled: true, size: ARROW_SIZE, color: tok('ink-2'), opacity: 1, aria: false },
          encoding: { x: x('edge'), y, tooltip },
        },
        {
          // the column of mean targets, right-aligned at the panel's right edge (text, so the accessibility tree skips it: the value is in the tooltip)
          transform: [{ filter: "datum.k == 'pt'" }],
          mark: { type: 'text', align: 'right', baseline: 'middle', fontSize: 10, color: tok('ink-2'), aria: false },
          encoding: { x: { value: { expr: 'child_width' } }, y, text: { field: 'lab', type: 'nominal' } },
        },
        {
          // the column's header, once per panel
          transform: [{ filter: "datum.k == 'pt'" }, { aggregate: [{ op: 'count', as: 'n' }] }, { calculate: "'mean'", as: 'hdr' }],
          mark: { type: 'text', align: 'right', baseline: 'bottom', fontSize: 10, fontStyle: 'italic', color: tok('ink-2'), aria: false },
          encoding: { x: { value: { expr: 'child_width' } }, y: { value: -3 }, text: { field: 'hdr', type: 'nominal' } },
        },
        {
          // a view with nothing to draw says why
          transform: [{ filter: "datum.k == 'state'" }],
          mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 10.5, fontStyle: 'italic', color: tok('ink-2') },
          encoding: { x: { value: 2 }, y, text: { field: 'line', type: 'nominal' }, description: { field: 'line', type: 'nominal' } },
        },
      ],
    },
    resolve: { scale: { y: 'independent' }, axis: { x: 'independent' } },
  };
}

// ---- finding and subtitle ----------------------------------------------------------------------------------------------

/**
 * Finding for the view `state` (the default view when omitted), from the rows alone. It leads with the median over the listings
 * that have at least three target analysts (a median over single-analyst targets says little), then says what the thinner
 * listings above that range rest on. A view with no covered listing says so without a median, and a view with nothing says why.
 */
export function targetDumbbellFinding(rows: DumbbellRows, state: Partial<TargetsState> = {}): string {
  const st = targetsState(state);
  const empty = dumbbellReason(rows, st);
  if (empty) return empty;
  const main = rows.points.filter((p) => p.panel === 'main' && pointInState(p, st));
  const covered = main.filter((p) => !p.thin);
  const bar = `${numWord(rows.thinBelow)} or more analysts`;
  const analysts = (n: number) => (n <= 1 ? 'one analyst' : `at most ${numWord(n)} analysts`);
  if (main.length === 1) {
    const p = main[0];
    return `${p.name} is the only ${viewPhrase(st, 1).replace(/^1 /, '')}: its mean target implies ${fmtUpside(p.upside)}, from ${count(p.n_opinions ?? 0, 'analyst')}`;
  }
  if (!covered.length) {
    const most = Math.max(...main.map((p) => p.n_opinions ?? 0));
    const ups = main.map((p) => p.upside);
    return `None of the ${viewPhrase(st, main.length)} has ${bar} behind it; their mean targets run from ${fmtUpside(Math.min(...ups))} to ${fmtUpside(Math.max(...ups))}, each on ${analysts(most)}`;
  }
  const topCovered = Math.max(...covered.map((p) => p.upside));
  const lowCovered = Math.min(...covered.map((p) => p.upside));
  const beyond = main.filter((p) => p.thin && p.upside > topCovered);
  const where = viewPhrase({ ...st, coverage: 'three' }, covered.length);
  const head = covered.length === 1
    ? `The one ${where.replace(/^1 /, '')} implies ${fmtUpside(topCovered)}`
    : `Median implied upside is ${fmtUpside(median(covered.map((p) => p.upside)))} across ${where}`;
  if (beyond.length) {
    const most = Math.max(...beyond.map((p) => p.n_opinions ?? 0));
    const who = beyond.length === 1 ? 'the one' : beyond.length === 2 ? 'both' : `the ${beyond.length}`;
    const verb = beyond.length === 1 ? 'rests' : beyond.length === 2 ? 'rest' : 'all rest';
    return `${head}; ${who} above ${fmtUpside(topCovered)} ${verb} on ${analysts(most)}`;
  }
  if (covered.length === 1) return head;
  return `${head}; it runs from ${fmtUpside(lowCovered)} to ${fmtUpside(topCovered)}`;
}

/** `titles` prop for `<VegaChart group="targets" titleKeys={DUMBBELL_KEYS}>`: the finding for every combination of the controls. */
export const targetDumbbellTitles = (rows: DumbbellRows): Record<string, string> =>
  titleMapFor(DUMBBELL_KEYS, (st) => targetDumbbellFinding(rows, st));

/** Subtitle: the metric, unit and window in one sentence. Everything else is in `targetDumbbellNotes`. */
export function targetDumbbellSubtitle(_rows?: DumbbellRows): string {
  return 'Implied upside in percent: the mean analyst target price over the price, minus one, with the lowest and highest single target as whiskers; Yahoo Finance targets as currently served, one row per listing.';
}

/**
 * How to read the figure: plain text, short paragraphs separated by a blank line (the page puts it in a "How to read this"
 * disclosure). Counts are for the full set; a control changes the rows drawn, not these sentences.
 */
export function targetDumbbellNotes(rows: DumbbellRows): string {
  const [dLo, dHi] = rows.domain;
  const s = targetStats(rows);
  const cn = rows.points.filter((p) => p.panel === 'cn');
  const pence = rows.points.filter((p) => p.pence).length;
  const off = rows.points.filter((p) => p.upside > dHi || (p.high ?? p.upside) > dHi);
  const offAllThin = off.length > 0 && off.every((p) => p.thin);
  const cov = coverageLabels(rows).thin.toLowerCase();
  const paras: string[] = [];
  paras.push(
    `The dot is the mean target over the price, minus one (0.10 means 10% above the price); a target at the price is 0%. The whisker runs from the lowest to the highest single target, computed the same way. The number at the right of each row is the mean, not a whisker end.`
    + ` Shape is the market (circle: United States, triangle: other overseas, diamond: China A-share, in its own panel).`
    + ` A filled blue dot is a company whose whole business is security; a hollow grey one is a company whose main line of business is.`
    + ` A dashed outline and a lighter fill mean ${cov} (the whisker is dashed too).`,
  );
  const axis = rows.topCovered
    ? `The axis is linear from ${fmtUpside(dLo)} to ${fmtUpside(dHi)}, which holds every whisker of a listing with at least ${numWord(rows.thinBelow)} target analysts (the highest, ${rows.topCovered.name}, ends at ${fmtUpside(rows.topCovered.value)}).`
    : `The axis is linear from ${fmtUpside(dLo)} to ${fmtUpside(dHi)}.`;
  paras.push(`${axis}${off.length ? ` An arrow on the right edge marks a mean or a whisker beyond it; the number in the column is the true mean. In the full set ${count(off.length, 'listing')} ${off.length === 1 ? 'is' : 'are'} marked this way${offAllThin ? `, ${off.length === 1 ? 'and it has' : 'all with'} fewer than ${numWord(rows.thinBelow)} analysts` : ''}.` : ''}`);
  const longer = rows.points.filter((p) => p.upside > dHi && p.high != null && p.high > p.upside * 1.02);
  if (longer.length) {
    paras.push(`${andList(longer.map((p) => `${p.name}'s highest single target is ${fmtUpside(p.high)}`))}; its whisker is cut at the edge, so the end is not drawn.`);
  }
  if (s.suspects.length) {
    paras.push(`A mean target more than ${numWord(SUSPECT_RATIO)} times the price (${andList(s.suspects.map((p) => p.name))}, ${s.suspects.every((p) => p.n === s.suspects[0].n) ? `each on ${numWord(s.suspects[0].n ?? 0)} ${s.suspects[0].n === 1 ? 'analyst' : 'analysts'}` : 'on one or two analysts'}) is probably a stale or split-affected single target and is unverified; it stays in the figure and the table with that note.`);
  }
  paras.push(`It is a ratio of two prices in one currency, so no currency conversion is needed${pence ? `, and the ${count(pence, 'listing')} quoted in pence (GBp) need none either` : ''}.`);
  const skipped = rows.skipped;
  const drawn = `In the full set ${count(s.n, 'listing')} outside China and ${count(cn.length, 'A-share listing')} are drawn`;
  if (skipped.length) {
    const reasons = Array.from(new Set(skipped.map((x) => x.reason)));
    paras.push(`${drawn}. Not drawn: ${andList(reasons.map((r) => `${count(skipped.filter((x) => x.reason === r).length, 'listing')} in the listed pool where ${SKIP_TEXT[r]}`))}.`);
  } else if (rows.points.length) {
    paras.push(`${drawn}. Not drawn: none; every listing in the listed pool has a positive price and a target.`);
  } else paras.push('Nothing is drawn: no target prices are published on this build.');
  paras.push(`Coverage counts target analysts. The market control applies to listings outside China only: the A-share panel ignores it, and it disappears when a market is picked.`);
  return paras.join('\n\n');
}
