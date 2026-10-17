/**
 * Figure 3, the Rule of 40: expected growth against free-cash-flow margin with the x + y = 40 diagonal, and the companies ranked
 * by their score. Its own copy of the pattern (charts/saas is not imported); the two panels are two specs, because a concat
 * spec cannot be sized to the container the way a single or faceted spec is, and the page lays them side by side.
 *
 *   rule40ScatterSpec  the scatter panel: growth (x) against FCF margin (y), both in percent of revenue, with the diagonal on
 *                      which growth plus margin is 40 points; companies above it clear the rule.
 *   rule40RankedSpec   the ranked panel: every scored company as a bar, ranked by score, with a rule at 40; bars carry the
 *                      company's short name (the ticker is in the tooltip) and, for thin coverage, the analyst count.
 *
 * Both colour a company by the quarter of EV / NTM revenue it sits in, the quarters of every listing that has a multiple (not
 * of the filtered view, so the colours do not move when a control does). Quarters are one hue stepped in fill strength (ground to blue),
 * because four hues would not read as an order; a company with no multiple is hollow grey and says so in the legend. Markets are told apart
 * by shape in the scatter and exposure by outline (dashed is a main line of business). Thin coverage (fewer than three analysts behind the
 * multiple) is a smaller mark in the scatter and a thinner bar in the ranking, with the count in the bar's label: it is never a fade, because
 * the fill is the quarter and a faded fill would read as a lower quarter. The quarter legend is drawn once, on the scatter; a small key under
 * the ranking explains its outline and thin bars.
 *
 * Growth is expected growth (next-twelve-month revenue over trailing-twelve-month revenue minus one) and FCF margin is trailing:
 * the score adds a forward number to a trailing one, which the notes say. Axes of the scatter end at the 92nd percentile of
 * growth and run from the 10th percentile of FCF margin (padded by half a step at the top so no marker is cut by the frame); a company
 * outside them is named in the notes and still ranked. Rule marks and lines carry no text and are hidden from the accessibility tree.
 */
import { VL_SCHEMA, series, tok } from '../theme';
import type { Listing } from './types';
import {
  type KeyEntry, type Spec, type ValuationPoint, type ValuationState,
  COV_THIN_LABEL, FILTER_EXPR, KEY_SCALES, MARKETS, MARKET_LABEL, MARKET_SHAPE, MIN_ANALYSTS, VALUATION_DEFAULTS,
  EXPOSURE_MAIN, EXPOSURE_WHOLE, GROWTH_AXIS_HI_Q, MARGIN_AXIS_LO_Q, andList, ceilTo, clipDomain, emptySpec, emptyViewLayer, floorTo, fmtPct0, fmtTimes, fmtX, keyLayer, median,
  numberWord, plural, quantile, shortName, snapshotDate, valuationParams, valuationPoints, viewOf,
} from './valuation_data';

/** The Rule of 40 itself: growth plus FCF margin, in points, of at least this. Definition of the method, not a result. */
export const RULE_CUT = 40;
/** Area of a scatter mark in square pixels, and of one whose multiple rests on fewer than MIN_ANALYSTS analysts (the second channel of thin coverage). */
const MARK_SIZE = 84;
const THIN_MARK_SIZE = 34;
/** Thickness of a ranked bar in pixels, and of a thin one; the band of one bar is RANK_STEP pixels. */
const RANK_STEP = 14;
const BAR_PX = 10;
const THIN_BAR_PX = 5;
const SCATTER_H = 400;
/** Pixels kept free after the longest bar of the ranking for its label (the score, and for a thin-coverage company its count). */
const LABEL_PX = 84;

export type QuarterEdges = { edges: [number, number, number]; labels: [string, string, string, string] };

export type Rule40Data = {
  /** Every listing with a multiple, growth, FCF margin or score: the dataset figure 2 shares. */
  points: ValuationPoint[];
  /** Listings with a Rule-of-40 score, before any filter. */
  scored: number;
  /** True when none has one: both specs are then one sentence. */
  empty: boolean;
  /** Quarters of EV / NTM among listings that have a multiple; null when fewer than four have one. */
  quarters: QuarterEdges | null;
  nWithMultiple: number;
  x: { domain: [number, number] };
  y: { domain: [number, number] };
  /** The Rule-of-40 diagonal clipped to the plot, null when it does not cross it. */
  diagonal: { growth: number; fcf: number }[] | null;
  /** Scored listings outside the scatter's axes. */
  offAxis: { ticker: string; name: string; growth: number; fcf: number }[];
  /** The three best scores inside the scatter's axes, named on the chart. */
  leaders: { ticker: string; name: string; growth: number; fcf: number; r40: number; layer: string; ring: string; market: string; purity: string; cov3: boolean }[];
  /** Day of the newest price (`YYYY-MM-DD`), for the figure's as-of stamp. */
  asOf: string | null;
  /** Ring A listings without a score, by what is missing. */
  unscored: { n: number; groups: { label: string; n: number }[] };
  defaults: ValuationState;
};

/** Quarters of the listed set's EV / NTM, with the legend label of each (a multiple range). */
export function quarterEdges(evs: number[]): QuarterEdges | null {
  if (evs.length < 4) return null;
  const s = [...evs].sort((a, b) => a - b);
  const [q1, q2, q3] = [0.25, 0.5, 0.75].map((q) => quantile(s, q)) as [number, number, number];
  return {
    edges: [q1, q2, q3],
    labels: [`Up to ${fmtX(q1)}`, `${fmtX(q1)} to ${fmtX(q2)}`, `${fmtX(q2)} to ${fmtX(q3)}`, `Above ${fmtX(q3)}`],
  };
}

/** Data preparation: scores, quarter edges, axis ranges, the diagonal, what is left out. */
export function rule40Rows(listings: Listing[], defaults: ValuationState = VALUATION_DEFAULTS): Rule40Data {
  const points = valuationPoints(listings);
  const scored = points.filter((p) => p.r40 != null && p.growth != null && p.fcf != null);
  const evs = points.filter((p) => p.ev_ntm != null).map((p) => p.ev_ntm as number);
  const quarters = quarterEdges(evs);
  const xDomain = clipDomain(scored.map((p) => p.growth as number), 0.1, 0, GROWTH_AXIS_HI_Q);
  const [yLo, yHi] = clipDomain(scored.map((p) => p.fcf as number), 0.1, MARGIN_AXIS_LO_Q, 1);
  // the top of the margin axis is the largest margin in the sample, so a marker there would be cut in half by the frame: pad by half a step
  const yDomain: [number, number] = [yLo, Math.round((yHi + 0.05) * 1e6) / 1e6];
  const inside = (p: ValuationPoint) => (p.growth as number) >= xDomain[0] && (p.growth as number) <= xDomain[1] && (p.fcf as number) >= yDomain[0] && (p.fcf as number) <= yDomain[1];
  const cut = RULE_CUT / 100;
  // x + y = cut, clipped to the box
  const gx0 = Math.max(xDomain[0], cut - yDomain[1]);
  const gx1 = Math.min(xDomain[1], cut - yDomain[0]);
  const diagonal = gx1 > gx0 ? [gx0, gx1].map((g) => ({ growth: Math.round(g * 1e4) / 1e4, fcf: Math.round((cut - g) * 1e4) / 1e4 })) : null;
  const leaders = scored.filter(inside).sort((a, b) => (b.r40 as number) - (a.r40 as number)).slice(0, 3).map((p) => ({
    ticker: p.ticker, name: p.name, growth: p.growth as number, fcf: p.fcf as number, r40: p.r40 as number,
    layer: p.layer, ring: p.ring, market: p.market, purity: p.purity, cov3: p.cov3,
  }));
  const by = new Map<string, number>();
  for (const l of listings) {
    if (l.ring !== 'A' || l.rule_of_40 != null) continue;
    const label = l.growth == null && l.fcf_margin == null ? 'no growth estimate and no FCF margin' : l.growth == null ? 'no growth estimate' : l.fcf_margin == null ? 'no FCF margin' : 'no score';
    by.set(label, (by.get(label) ?? 0) + 1);
  }
  const groups = [...by].map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  return {
    points, scored: scored.length, empty: scored.length === 0, quarters, nWithMultiple: evs.length,
    x: { domain: xDomain }, y: { domain: yDomain }, diagonal,
    offAxis: scored.filter((p) => !inside(p)).map((p) => ({ ticker: p.ticker, name: p.name, growth: p.growth as number, fcf: p.fcf as number })).sort((a, b) => b.growth - a.growth),
    leaders,
    asOf: snapshotDate(listings),
    unscored: { n: groups.reduce((a, g) => a + g.n, 0), groups },
    defaults,
  };
}

/** Counts and medians of the view: how many clear the rule, and what the two groups trade at. */
export function rule40Summary(data: Rule40Data, state: ValuationState = data.defaults) {
  const view = viewOf(data.points, state).filter((p) => p.r40 != null && p.growth != null && p.fcf != null);
  const pass = view.filter((p) => (p.r40 as number) >= RULE_CUT);
  const rest = view.filter((p) => (p.r40 as number) < RULE_CUT);
  const evOf = (xs: ValuationPoint[]) => xs.filter((p) => p.ev_ntm != null).map((p) => p.ev_ntm as number);
  const pe = evOf(pass);
  const re = evOf(rest);
  return {
    n: view.length, nPass: pass.length,
    medianScore: median(view.map((p) => p.r40 as number)),
    passEv: median(pe), restEv: median(re), nPassEv: pe.length, nRestEv: re.length,
  };
}

/**
 * Fewest companies with a multiple in each group for a comparison of the two groups' multiples to be stated in the title. It is stricter than
 * the registered minimum group size of three: the median of three companies cannot carry a sentence that says one group trades above the other.
 */
export const MIN_GROUP_N = 5;

/** Title: how many clear the rule and what the two groups trade at; the direction word is read off the two medians. */
export function rule40Finding(data: Rule40Data, state: ValuationState = data.defaults): string {
  const s = rule40Summary(data, state);
  if (!s.n) return 'No listed company in this view has a Rule-of-40 score';
  // "0 of 1 scored company clears", "1 of 3 scored companies clears", "20 of 64 scored companies clear"
  const head = `${s.nPass} of ${plural(s.n, 'scored company', 'scored companies')} ${s.nPass === 1 || s.n === 1 ? 'clears' : 'clear'} the Rule of ${RULE_CUT}`;
  if (s.passEv == null || s.restEv == null || s.nPassEv < MIN_GROUP_N || s.nRestEv < MIN_GROUP_N) {
    return `${head}${s.medianScore != null ? `; the median score is ${s.medianScore.toFixed(0).replace('-', '−')} points` : ''}`;
  }
  const a = s.passEv.toFixed(1);
  const b = s.restEv.toFixed(1);
  if (a === b) return `${head}, and they trade at the same median ${fmtTimes(s.passEv)} revenue as the rest`;
  return `${head}, and they trade at a median ${fmtTimes(s.passEv)} revenue, ${s.passEv > s.restEv ? 'above' : 'below'} the ${fmtTimes(s.restEv)} of the rest`;
}

/** Subtitle: both panels' metric and unit, in one sentence. Colour, marks, the quarters and what is left out are in `rule40Notes`. */
export function rule40Subtitle(data: Rule40Data): string {
  if (data.empty) return 'Rule of 40: expected growth plus free-cash-flow margin, in points. No listing has both on this snapshot.';
  return `Rule of 40 score: expected growth plus trailing free-cash-flow margin, in points, for the companies that have both; fill is the quarter of EV / NTM revenue.`;
}

/**
 * What a reader needs beyond the subtitle, as plain text (no markup): paragraphs separated by a blank line. The page puts it in the figure's
 * "How to read this" block. Counts describe the full pool; the title counts the view the controls select.
 */
export function rule40Notes(data: Rule40Data): string {
  if (data.empty) return 'No listing has both expected growth and a free-cash-flow margin on this snapshot, so there is no Rule-of-40 score to show.';
  const paras: string[] = [];
  const few = numberWord(MIN_ANALYSTS);
  paras.push([
    `Expected growth is next-twelve-month revenue over trailing-twelve-month revenue, minus one; the margin is trailing free cash flow over trailing revenue, so the score adds a forward number to a trailing one (${data.scored} companies scored in the full pool; the title counts the view the controls select).`,
    `One panel plots the two parts in percent of revenue with the diagonal where they sum to ${RULE_CUT}; the other ranks the scores.`,
    'A deep loss offset by very fast growth scores like a profitable grower, so the top of the ranking is not a ranking of strong businesses.',
  ].join(' '));
  paras.push([
    data.quarters
      ? `Fill strength is the quarter of EV / NTM revenue among the ${data.nWithMultiple} listings that have a multiple (${data.quarters.labels.join(', ')}); hollow grey has no multiple.`
      : 'Fewer than four listings have a multiple, so dots are not graded by quarter.',
    'Shape is the listing market; a dashed outline is a main line of business, a solid one a whole business.',
    `A smaller mark, a thinner bar and a count after the score mean fewer than ${few} analysts behind the multiple: Yahoo's revenue analysts, or East Money's brokers for an A-share.`,
  ].join(' '));
  const left: string[] = [];
  if (data.unscored.n) left.push(`Not scored: ${data.unscored.n} listed without a score (${data.unscored.groups.map((g) => `${g.label} ${g.n}`).join(', ')}).`);
  if (data.offAxis.length) left.push(`Beyond the scatter's axes, but ranked: ${andList(data.offAxis.map((o) => `${o.name} (${fmtPct0(o.growth)} growth, ${fmtPct0(o.fcf)} margin)`))}.`);
  if (left.length) paras.push(left.join(' '));
  return paras.join('\n\n');
}

// ---- specs ---------------------------------------------------------------------------------------------------------------

/** The quarter of a listing's multiple as a rank: 1 (lowest) to 4 (highest), 0 for no multiple. A Vega expression over `datum`. */
const quarterCalc = (q: QuarterEdges | null): string => {
  if (!q) return '0';
  const [e1, e2, e3] = q.edges;
  return `datum.ev_ntm == null ? 0 : datum.ev_ntm <= ${e1} ? 1 : datum.ev_ntm <= ${e2} ? 2 : datum.ev_ntm <= ${e3} ? 3 : 4`;
};

/**
 * Encoding channels shared by the two panels. Quarter -> fill: a continuous ramp from the page ground to the series blue is sampled
 * at ranks 1 to 4, so the four steps are one hue, ordered, and follow the light and dark ground; rank 0 (no multiple) is the ground
 * itself, drawn with a grey outline. Exposure -> dashed outline. The outline is always at full opacity. Thin coverage is the caller's:
 * a smaller mark in the scatter, a thinner bar in the ranking.
 */
function quarterEncoding(q: QuarterEdges | null, withLegend: boolean) {
  const labelExpr = q ? q.labels.map((l, i) => `datum.value == ${i + 1} ? ${JSON.stringify(l)}`).join(' : ') + ' : \'\'' : "''";
  return {
    fill: {
      field: 'qrank', type: 'quantitative',
      scale: { type: 'linear', domain: [0, 4], range: [tok('bg'), series.a], interpolate: 'rgb', clamp: true },
      legend: q && withLegend ? {
        type: 'symbol', values: [1, 2, 3, 4], labelExpr,
        title: 'EV / NTM revenue quarter', titleLimit: 0, titleFontSize: 11, titleColor: tok('ink-2'),
        orient: 'top', direction: 'horizontal', columns: { expr: 'width < 330 ? 1 : 2' }, labelFontSize: 11, labelLimit: 0, columnPadding: 10, rowPadding: 3,
        symbolType: 'circle', symbolSize: 90, symbolStrokeWidth: 1.6, symbolStrokeColor: series.a,
      } : null,
    },
    stroke: { condition: { test: 'datum.qrank == 0', value: series.neutral }, value: series.a },
    strokeDash: { condition: { test: "datum.purity != 'high'", value: [3, 2] }, value: [1, 0] },
  };
}

const tooltip = [
  { field: 'name', type: 'nominal', title: 'Company' },
  { field: 'ticker', type: 'nominal', title: 'Listing' },
  { field: 'r40', type: 'quantitative', title: 'Rule of 40 score', format: '.1f' },
  { field: 'growth', type: 'quantitative', title: 'Expected growth', format: '.1%' },
  { field: 'fcf', type: 'quantitative', title: 'FCF margin', format: '.1%' },
  { field: 'ev_ntm', type: 'quantitative', title: 'EV / NTM revenue', format: '.1f' },
  { field: 'n', type: 'quantitative', title: 'Analysts behind the multiple' },
  { field: 'status', type: 'nominal', title: 'Status' },
];

/** The scatter panel. Driven by the five params of the `valuation` group. */
export function rule40ScatterSpec(data: Rule40Data, opts: { defaults?: ValuationState } = {}): Spec {
  if (data.empty) return emptySpec('No listing has both expected growth and a free-cash-flow margin on this snapshot, so there is no Rule-of-40 score to plot.');
  const defaults = opts.defaults ?? data.defaults;
  const enc = quarterEncoding(data.quarters, true);
  const x = {
    field: 'growth', type: 'quantitative', scale: { domain: data.x.domain, nice: false },
    axis: { title: 'Expected revenue growth', labelExpr: "replace(format(datum.value, '.0%'), '-', '−')", tickCount: { expr: 'width < 330 ? 4 : 6' }, grid: false },
  };
  const y = {
    field: 'fcf', type: 'quantitative', scale: { domain: data.y.domain, nice: false },
    axis: { title: 'Free-cash-flow margin, trailing', labelExpr: "replace(format(datum.value, '.0%'), '-', '−')", tickCount: 6, grid: true },
  };
  const keyEntries: KeyEntry[] = [
    ...MARKETS.map((m) => ({ label: MARKET_LABEL[m], shape: MARKET_SHAPE[m], fill: tok('ink-3'), stroke: tok('ink-3'), size: MARK_SIZE })),
    { label: `${EXPOSURE_WHOLE}:|solid outline`, stroke: series.a, strokeWidth: 1.6, size: MARK_SIZE },
    { label: `${EXPOSURE_MAIN}:|dashed outline`, stroke: series.a, strokeWidth: 1.6, dash: [3, 2], size: MARK_SIZE },
    { label: `${COV_THIN_LABEL}:|smaller mark`, stroke: series.a, strokeWidth: 1.6, size: THIN_MARK_SIZE },
  ];
  const layers: Spec[] = [
    { data: { values: [{ growth: 0 }] }, mark: { type: 'rule', color: tok('border'), strokeDash: [3, 3], aria: false }, encoding: { x } },
  ];
  if (data.diagonal) {
    const end = data.diagonal[data.diagonal.length - 1];
    layers.push({
      data: { values: data.diagonal },
      mark: { type: 'line', color: tok('ink-2'), strokeWidth: 1.6, strokeDash: [6, 4], clip: true, aria: false },
      encoding: { x, y },
    });
    layers.push({
      data: { values: [{ growth: end.growth, fcf: end.fcf }] },
      // the full sentence on a wide chart, the name alone on a phone, where the longer text would run into the axis
      transform: [{ calculate: `width < 330 ? 'Rule of ${RULE_CUT}' : 'Rule of ${RULE_CUT}: growth + margin = ${RULE_CUT}'`, as: 'rule_text' }],
      mark: { type: 'text', align: 'right', baseline: 'top', dx: -6, dy: 6, fontSize: 10, color: tok('ink-2') },
      encoding: { x, y, text: { field: 'rule_text', type: 'nominal' } },
    });
  }
  layers.push({
    data: { values: data.points },
    transform: [
      { filter: 'datum.r40 != null && datum.growth != null && datum.fcf != null' },
      { filter: FILTER_EXPR },
      { calculate: quarterCalc(data.quarters), as: 'qrank' },
    ],
    mark: { type: 'point', filled: true, strokeWidth: 1.6, clip: true },
    encoding: {
      x, y,
      shape: { field: 'market', type: 'nominal', scale: { domain: [...MARKETS], range: MARKETS.map((m) => MARKET_SHAPE[m]) }, legend: null },
      // thin coverage: a smaller mark. The fill is the quarter and the outline is at full opacity, so nothing is faded.
      size: { condition: { test: '!datum.cov3', value: THIN_MARK_SIZE }, value: MARK_SIZE },
      ...enc,
      tooltip,
    },
  });
  layers.push(emptyViewLayer(data.points, `datum.r40 != null && datum.growth != null && datum.fcf != null && ${FILTER_EXPR}`, 'No listed company in this view has a Rule-of-40 score.'));
  // the three best scores, by ticker (a name is too long beside neighbours this close), in two rows so adjacent labels do not collide
  const leaderRows = [...data.leaders].sort((a, b) => a.growth - b.growth);
  [0, 1].forEach((parity) => {
    const rows = leaderRows.filter((_, i) => i % 2 === parity);
    if (!rows.length) return;
    layers.push({
      data: { values: rows },
      transform: [{ filter: FILTER_EXPR }],
      mark: { type: 'text', align: 'left', dx: 8, dy: parity === 0 ? -8 : 9, fontSize: 11, color: tok('ink'), clip: true },
      encoding: { x, y, text: { field: 'ticker', type: 'nominal' } },
    });
  });
  layers.push(keyLayer(keyEntries, { orient: 'bottom' }));
  return {
    $schema: VL_SCHEMA, height: SCATTER_H, params: valuationParams(defaults), resolve: { scale: KEY_SCALES },
    title: { text: 'Growth against cash margin', anchor: 'start', fontSize: 12, fontWeight: 'normal', color: tok('ink-2'), offset: 8 },
    layer: layers,
  };
}

/**
 * The ranked panel: every scored company in the view as a bar, best first, labelled with its short name. A thin-coverage bar is thinner and
 * its label ends with the analyst count; a main-line company is outlined dashed. The quarter legend is on the scatter, not repeated here.
 */
export function rule40RankedSpec(data: Rule40Data, opts: { defaults?: ValuationState } = {}): Spec {
  if (data.empty) return emptySpec('No listing has a Rule-of-40 score on this snapshot, so there is nothing to rank.');
  const defaults = opts.defaults ?? data.defaults;
  const enc = quarterEncoding(data.quarters, false);
  // a bar's label is the company's short name; two companies that share one are told apart by their ticker
  const scored = data.points.filter((p) => p.r40 != null && p.growth != null && p.fcf != null);
  const nameCount = new Map<string, number>();
  for (const p of scored) nameCount.set(shortName(p.name), (nameCount.get(shortName(p.name)) ?? 0) + 1);
  const labelOf = (p: ValuationPoint) => (nameCount.get(shortName(p.name)) ?? 0) > 1 ? `${shortName(p.name)} (${p.ticker})` : shortName(p.name);
  // the bars' own rows: scored listings plus one placeholder row that carries the empty-view sentence (it keeps one band on the axis when nothing is in view)
  const rows = [
    ...scored.map((p) => ({ kind: 'pt', label: labelOf(p), ticker: p.ticker, name: p.name, layer: p.layer, ring: p.ring, market: p.market, purity: p.purity, cov3: p.cov3, ev_ntm: p.ev_ntm, r40: p.r40, growth: p.growth, fcf: p.fcf, n: p.n, status: p.status })),
    { kind: 'ph', label: ' ', ticker: ' ', name: '', layer: 'all', ring: 'A', market: 'us', purity: 'high', cov3: true, ev_ntm: null, r40: null, growth: null, fcf: null, n: null, status: '' },
  ];
  const lo = Math.min(0, ...rows.map((r) => (r.r40 as number | null) ?? 0));
  const hi = Math.max(RULE_CUT, ...rows.map((r) => (r.r40 as number | null) ?? 0));
  // headroom at the top for the label after the longest bar, in pixels (a label that ran past the plot would be cut by the frame): the top of the
  // axis is placed so that LABEL_PX of the plot are free after the largest score, but never more than half the plot
  const xLo = floorTo(lo, 10);
  const xHi = ceilTo(hi, 10);
  const x = { field: 'r40', type: 'quantitative', scale: { domain: { expr: `[${xLo}, ${xLo} + ${xHi - xLo} * width / max(width - ${LABEL_PX}, width * 0.5)]` }, nice: false }, axis: { title: 'Rule of 40 score (points)', labelExpr: "replace(format(datum.value, 'd'), '-', '−')", tickCount: 6, grid: true } };
  const sort = { field: 'r40', op: 'max', order: 'descending' };
  const y = { field: 'label', type: 'nominal', title: null, sort, axis: { labelFontSize: 11, ticks: false, domain: false, labelLimit: 110 } };
  // the label: the score, then for a thin-coverage company how many analysts (brokers for an A-share) stand behind the multiple; short on a phone
  const labelText = "format(datum.r40, '.0f') + (datum.cov3 ? '' : width < 330 ? ' · n=' + (datum.n == null ? '?' : datum.n) : ' · ' + (datum.n == null ? 'no analyst count' : datum.n + (datum.market == 'china' ? (datum.n == 1 ? ' broker' : ' brokers') : (datum.n == 1 ? ' analyst' : ' analysts'))))";
  return {
    $schema: VL_SCHEMA,
    height: { step: RANK_STEP },
    params: valuationParams(defaults),
    title: {
      text: 'Every scored company, best first', anchor: 'start', fontSize: 12, fontWeight: 'normal', color: tok('ink-2'), offset: 6,
      // the key, in words: the title block starts at the chart's left edge, where a legend would start at the plot's
      subtitle: [`Dashed outline: ${EXPOSURE_MAIN.toLowerCase()}.`, `Thinner bar and a count: ${COV_THIN_LABEL.toLowerCase()}.`],
      subtitleFontSize: 11, subtitleColor: tok('ink-3'), subtitleFontWeight: 'normal', subtitlePadding: 2,
    },
    data: { values: rows },
    transform: [
      { calculate: `(datum.kind == 'pt' && ${FILTER_EXPR}) ? 1 : 0`, as: 'show' },
      { joinaggregate: [{ op: 'sum', field: 'show', as: 'n_show' }] },
      { calculate: quarterCalc(data.quarters), as: 'qrank' },
    ],
    layer: [
      {
        transform: [{ filter: 'datum.show == 1' }],
        mark: { type: 'bar', strokeWidth: 1.4 },
        encoding: { x, y, ...enc, size: { condition: { test: '!datum.cov3', value: THIN_BAR_PX }, value: BAR_PX }, tooltip },
      },
      {
        transform: [{ filter: 'datum.show == 1' }, { calculate: 'max(datum.r40, 0)', as: 'label_x' }, { calculate: labelText, as: 'label_text' }],
        mark: { type: 'text', align: 'left', dx: 4, fontSize: 11, color: tok('ink-2') },
        encoding: {
          x: { field: 'label_x', type: 'quantitative' }, y: { field: 'label', type: 'nominal', sort },
          text: { field: 'label_text', type: 'nominal' },
        },
      },
      {
        data: { values: [{ r40: RULE_CUT }] },
        mark: { type: 'rule', color: tok('ink-2'), strokeDash: [5, 3], strokeWidth: 1.3, aria: false },
        encoding: { x: { field: 'r40', type: 'quantitative' } },
      },
      {
        transform: [{ filter: "datum.kind == 'ph' && datum.n_show == 0" }],
        mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 12, color: tok('ink-2'), limit: { expr: 'width - 8' } },
        encoding: { x: { value: 8 }, y: { field: 'label', type: 'nominal', sort }, text: { value: 'No listed company in this view has a Rule-of-40 score.' } },
      },
    ],
  };
}
