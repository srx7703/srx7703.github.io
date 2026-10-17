/**
 * Figure 6, private valuation intervals: per company three marks on one log axis in US dollars.
 *
 *   headline    the latest round valuation as a round reported it (a dot). Hollow when its basis is `reported` (a
 *               publication said so, the company did not; drawn only when the curator marked it chartable); dashed
 *               outline when the round is stale, and the label says "stale"; every dot is labelled with its round's
 *               year; an open arrow at the far end of the row when the figure is only a floor (`>`). A market other
 *               than overseas is a diamond rather than a circle. The tooltip carries the curator's caveat (a merger
 *               value, a part-secondary round, a tender mixed in).
 *   estimate    an interval, labelled as an estimate: headline x (1 + d - band) to headline x (1 + d + band), d the
 *               registered discount for the year of the round (an assumption, not data). Drawn as a bar under the
 *               dot with a solid outline; its tooltip names the discount used.
 *   comparable  a capped line: company-stated ARR times the lowest to highest EV/NTM revenue of the layer's listed
 *               comparables. An open arrow replaces the upper cap when the ARR is only a floor; the line is dashed
 *               (not faded) when fewer than three listed comparables stand behind it.
 *
 * The Q6 reading is a marker beside the comparable line: a filled orange triangle when the lower end of the estimate is
 * above the upper end of the comparable interval (`above`), an open circle when it is not. Nothing is drawn for a
 * company the pipeline did not score. The registered scoring is unchanged; a computed sub-line under the title names the
 * scored companies whose comparable set is thin or whose ARR is dated more than a year before the multiples
 * (`ledgerIntervalSubline`), because those two facts decide how much "above" is worth.
 *
 * Which companies are drawn is a parameter: a company with at least `minMarks` of its three marks, or a disclosed
 * headline of at least `sizeCut` dollars. The rest are counted in the notes with the reason.
 *
 * Text: `ledgerIntervalSubtitle` is the metric sentence only; everything else is in `ledgerIntervalNotes` (plain text,
 * paragraphs separated by a blank line) for the "How to read this" disclosure.
 *
 * Dashboard group `ledger`; params `layer`, `market`, `basis`, `confirmed`, `hide_stale` (see ledger_data.ts). A view
 * with nothing in it shows one sentence saying which filter emptied it.
 *
 * Narrow screens: below `NARROW_PLOT` pixels of plot the labels shorten to the value, a label that fits on neither side
 * of its row is left out (the tooltip keeps it), and the company names get a shorter limit.
 */
import { VL_SCHEMA, series, tok } from '../theme';
import type { PrivateInterval } from './types';
import {
  type LedgerDim, type LedgerState, LEDGER_DEFAULTS, ledgerParams, lit, reasonChain, reasonTitle, stateFilterExpr, passes,
  tagCharts, viewKeyRows, ledgerTitleMap, type LedgerTitleMap, ledgerTextMap, textLookupExpr, type LedgerTextMap, layerName,
  fmtMoney, fmtRange, fmtValue, fmtPct, fmtDay, fmtMonth, numWord, joinAnd, upFirst, LAYERS,
} from './ledger_data';

const NAME = 'iv_src';

/** The controls figure 6 answers to, in the key order of its `titles` map. */
export const INTERVAL_DIMS = ['layer', 'market', 'basis', 'confirmed', 'hide_stale'] as const satisfies readonly LedgerDim[];

export interface IntervalOpts {
  /** A company is drawn with at least this many of its three marks (default 2). */
  minMarks?: number;
  /** ... or with a disclosed headline of at least this many dollars (default 5e9). */
  sizeCut?: number;
  /** A comparable interval resting on fewer listed comparables than this is drawn dashed (default 3). */
  thinBelow?: number;
  /** An ARR dated more than this many months before the multiples is called out (default 12). */
  arrMaxMonths?: number;
}
export const INTERVAL_DEFAULTS = { minMarks: 2, sizeCut: 5e9, thinBelow: 3, arrMaxMonths: 12 } as const;
const resolve = (o: IntervalOpts = {}) => ({ ...INTERVAL_DEFAULTS, ...o });

/** Plot width in pixels below which the figure drops to short labels (a phone: the plot is 120 to 200 px). */
export const NARROW_PLOT = 300;

// --- the company model --------------------------------------------------------------------------------------------
const pos = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) && x > 0 ? x : null);
const usd = (u: string | null | undefined) => (u ?? 'USD') === 'USD';

/** `2026-10-16` minus `n` months (a day past the end of the month moves to its last day). */
function minusMonths(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1 - n, d));
  if (dt.getUTCMonth() !== (((m - 1 - n) % 12) + 12) % 12) dt.setUTCDate(0);
  return dt.toISOString().slice(0, 10);
}

export interface Headline {
  value: number; qualifier: string; basis: string; asOf: string | null; year: number | null; stale: boolean; confirmed: boolean; url: string | null;
  /** The curator's one-sentence caveat for the number (a merger value, a part-secondary round, a tender mixed in ...). */
  caveat: string | null;
}
export interface Estimate { lo: number; hi: number; discount: number | null; band: number | null; qualifier: string }
export interface Comparable {
  lo: number; hi: number; qualifier: string; arr: number | null; arrQualifier: string | null; multLow: number | null; multHigh: number | null;
  tickerLow: string | null; tickerHigh: string | null; tickers: string[]; asOf: string | null; multiplesAsOf: string | null; thin: boolean;
  /** The date of the ARR figure (its as-of date) and whether it is older than `arrMaxMonths` at the date of the multiples. */
  arrAsOf: string | null; arrOld: boolean;
}
export interface IntervalCompany {
  company: string; layer: string | null; market: string | null; marks: number; level: number;
  headline: Headline | null; estimate: Estimate | null; comparable: Comparable | null;
  /** Q6: decided by the pipeline (null when not scored). */
  above: boolean | null; scored: boolean; note: string | null;
  drawn: boolean;
  /** Why a company with no drawable mark is not drawn: the pipeline's own reason where it gives one. */
  reason: string | null;
  /** Why the comparable interval is missing for a company that has a round, or null. */
  noComparable: string | null;
}

function companyOf(r: PrivateInterval, o: ReturnType<typeof resolve>): IntervalCompany | null {
  if (!r.company) return null;
  const hv = pos(r.headline_value);
  const headline: Headline | null = hv && usd(r.headline_unit) && tagCharts(r.headline_tag) && r.headline_basis && r.headline_basis !== 'undisclosed'
    ? {
      value: hv, qualifier: r.headline_qualifier ?? '=', basis: r.headline_basis, asOf: r.headline_as_of ?? r.headline_period,
      year: r.vintage_year, stale: r.stale_mark === true, confirmed: r.company_confirmed === true, url: r.headline_source_url,
      caveat: r.headline_caveat ?? null,
    }
    : null;
  const dl = pos(r.discount_lower);
  const du = pos(r.discount_upper);
  const estimate: Estimate | null = headline && r.discount_scored === true && dl && du && usd(r.discount_unit) && tagCharts(r.discount_tag)
    ? { lo: Math.min(dl, du), hi: Math.max(dl, du), discount: r.discount, band: r.discount_band, qualifier: r.discount_qualifier ?? '=' }
    : null;
  const cl = pos(r.comparable_lower);
  const cu = pos(r.comparable_upper);
  const tickers = (r.comparable_tickers ?? '').split(',').map((t) => t.trim()).filter(Boolean);
  const arrAsOf = r.comparable_as_of;
  const comparable: Comparable | null = r.comparable_scored === true && cl && cu && usd(r.comparable_unit) && tagCharts(r.comparable_tag)
    ? {
      lo: Math.min(cl, cu), hi: Math.max(cl, cu), qualifier: r.comparable_qualifier ?? '=', arr: pos(r.arr_value), arrQualifier: r.arr_qualifier,
      multLow: r.multiple_low, multHigh: r.multiple_high, tickerLow: r.multiple_low_ticker, tickerHigh: r.multiple_high_ticker, tickers,
      asOf: arrAsOf, multiplesAsOf: r.multiples_as_of, thin: tickers.length < o.thinBelow,
      arrAsOf, arrOld: !!arrAsOf && !!r.multiples_as_of && arrAsOf < minusMonths(r.multiples_as_of, o.arrMaxMonths),
    }
    : null;
  const marks = (headline ? 1 : 0) + (estimate ? 1 : 0) + (comparable ? 1 : 0);
  const drawn = marks > 0 && (marks >= o.minMarks || (headline?.basis === 'disclosed' && headline.value >= o.sizeCut));
  const scored = r.q6_scored === true && r.above != null && !!headline && !!estimate && !!comparable;
  return {
    company: r.company, layer: r.layer, market: r.market, marks, headline, estimate, comparable, drawn,
    level: headline?.value ?? comparable?.hi ?? 0,
    above: scored ? r.above : null, scored, note: scored ? r.above_note : null,
    reason: marks === 0 ? r.headline_reason ?? r.discount_reason ?? 'no chartable round valuation' : null,
    noComparable: headline && !comparable ? r.comparable_reason ?? 'no scored comparable' : null,
  };
}

/** Every directory company as a model; `drawn` says whether the figure draws it under `opts`. */
export function intervalCompanies(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): IntervalCompany[] {
  const o = resolve(opts);
  return rows.map((r) => companyOf(r, o)).filter((c): c is IntervalCompany => c !== null);
}

const filterable = (c: IntervalCompany) => ({
  layer: c.layer, market: c.market, basis: c.headline?.basis ?? null, confirmed: c.headline?.confirmed === true, stale: c.headline?.stale === true,
});

// --- axis ---------------------------------------------------------------------------------------------------------
function snap125(v: number, dir: 'down' | 'up'): number {
  const base = 10 ** Math.floor(Math.log10(v));
  const steps = [1, 2, 5, 10];
  if (dir === 'down') return steps.filter((s) => s * base <= v * (1 + 1e-9)).pop()! * base;
  return (steps.find((s) => s * base >= v * (1 - 1e-9)) ?? 10) * base;
}
const ticks125 = (lo: number, hi: number): number[] => {
  const out: number[] = [];
  for (let k = Math.floor(Math.log10(lo)); k <= Math.ceil(Math.log10(hi)); k++) {
    for (const m of [1, 2, 5]) {
      const v = +(m * 10 ** k).toPrecision(1);
      if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
    }
  }
  return out;
};
/** Dollar axis labels: $500M, $1B, $5B, $10B. */
const AXIS_LABEL_EXPR = "replace(format(datum.value, '$.2~s'), 'G', 'B')";

// --- rows ---------------------------------------------------------------------------------------------------------
type Row = Record<string, unknown>;
const CH_PX = 6; // estimated pixels per character at 10.5px Plex Sans (digits are 0.6em wide)

/** The curator's caveat for the headline, as the tooltip shows it. */
const tipCaveat = (h: Headline): string => h.caveat ?? '';

function tipEstimate(c: IntervalCompany): Row {
  const e = c.estimate as Estimate;
  const h = c.headline as Headline;
  const band = e.band != null ? `${Math.round(e.band * 100)} points` : 'the registered band';
  return {
    tip_what: 'Estimate, not data',
    tip_value: `${fmtRange(e.lo, e.hi)}${h.qualifier === '>' ? ' or more' : ''}`,
    tip_how: `Headline ${fmtValue(h.value, 'USD', h.qualifier)} less the registered discount for a ${h.year ?? 'round'} round: ${fmtPct(e.discount)}, plus or minus ${band}. An assumption, not a measurement.`,
    tip_basis: h.basis, tip_asof: fmtDay(h.asOf), tip_caveat: tipCaveat(h),
  };
}
function tipHeadline(c: IntervalCompany): Row {
  const h = c.headline as Headline;
  return {
    tip_what: 'Latest round valuation',
    tip_value: fmtValue(h.value, 'USD', h.qualifier),
    tip_how: `${h.basis === 'reported' ? 'Reported by a publication, not stated by the company, and marked chartable by the curator. ' : 'As the company or a filing states it. '}${h.stale ? `Stale: the ${h.year ?? ''} round has not been refreshed. ` : ''}${h.confirmed ? 'Company-confirmed.' : 'Not company-confirmed.'}`,
    tip_basis: h.basis, tip_asof: fmtDay(h.asOf), tip_caveat: tipCaveat(h),
  };
}
function tipComparable(c: IntervalCompany, thinBelow: number, arrMaxMonths: number): Row {
  const m = c.comparable as Comparable;
  const mult = m.multLow != null && m.multHigh != null
    ? `${m.multLow.toFixed(1)}x (${m.tickerLow ?? '?'}) to ${m.multHigh.toFixed(1)}x (${m.tickerHigh ?? '?'}) EV/NTM revenue`
    : 'listed comparables’ EV/NTM revenue range';
  const flag = c.scored ? (c.above ? ' Q6: the estimate sits entirely above this interval.' : ' Q6: the estimate is not entirely above this interval.') : '';
  const old = m.arrOld ? ` The ARR is dated ${fmtMonth(m.arrAsOf)}, more than ${arrMaxMonths === 12 ? 'a year' : `${arrMaxMonths} months`} before the multiples (${fmtMonth(m.multiplesAsOf)}).` : '';
  return {
    tip_what: 'Public-comparable interval',
    tip_value: `${fmtRange(m.lo, m.hi)}${m.qualifier === '>' ? ' or more' : ''}`,
    tip_how: `ARR ${fmtValue(m.arr, 'USD', m.arrQualifier)} (${fmtMonth(m.arrAsOf)}) x ${mult}; comparables ${m.tickers.join(', ') || 'none'}${m.thin ? ` (fewer than ${numWord(thinBelow)}, drawn dashed)` : ''}.${old}${flag}${c.note ? ` Note: ${c.note}.` : ''}`,
    tip_basis: 'disclosed ARR x listed multiples', tip_asof: fmtDay(m.multiplesAsOf), tip_caveat: '',
  };
}

/** The extent of the log axis for the drawn companies, snapped to 1-2-5 values. */
function domainOf(drawn: readonly IntervalCompany[]): [number, number] {
  const vs = drawn.flatMap((c) => [c.headline?.value, c.estimate?.lo, c.estimate?.hi, c.comparable?.lo, c.comparable?.hi]).filter((v): v is number => v != null);
  if (!vs.length) return [1e8, 1e10];
  return [snap125(Math.min(...vs) * 0.8, 'down'), snap125(Math.max(...vs) * 1.25, 'up')];
}

/**
 * The figure's dataset: one row per mark, per label and per marker, plus the `view` rows that let the title say which
 * filter emptied the chart. Only drawn companies; only values that are positive dollars on a log axis.
 */
export function ledgerIntervalRows(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): Row[] {
  const o = resolve(opts);
  const cs = intervalCompanies(rows, opts).filter((c) => c.drawn);
  const [d0, d1] = domainOf(cs);
  const span = Math.log(d1 / d0);
  const frac = (v: number) => Math.log(v / d0) / span;
  const out: Row[] = [];
  for (const c of cs) {
    const h = c.headline;
    const common = {
      row_type: 'mark', company: c.company, layer: c.layer ?? '', layer_label: layerName(c.layer), market: c.market ?? '',
      basis: h?.basis ?? '', confirmed: h?.confirmed === true, stale: h?.stale === true, level: c.level,
      shape: c.market === 'china' ? 'diamond' : 'circle',
    };
    if (c.estimate && h) {
      out.push({ ...common, kind: 'estimate', lo: c.estimate.lo, hi: c.estimate.hi, hollow: h.basis === 'reported', ...tipEstimate(c) });
    }
    if (h) {
      out.push({
        ...common, kind: 'headline', v: h.value, hollow: h.basis === 'reported',
        ...tipHeadline(c),
      });
      // The label sits beside whichever end of the headline's row reaches furthest (and after the open arrow of a floor).
      const lo = Math.min(h.value, c.estimate?.lo ?? h.value);
      const hi = Math.max(h.value, c.estimate?.hi ?? h.value);
      const arrow = h.qualifier === '>' ? 1 : 0;
      const value = fmtValue(h.value, 'USD', h.qualifier, 'symbol');
      const text = `${value}${h.year ? ` · ${h.year}` : ''}${h.stale ? ', stale' : ''}`;
      out.push({
        ...common, kind: 'label_head', text, text_short: value, flag: '', arrow_r: arrow, f_start: frac(lo), f_end: frac(hi),
        extra_w: arrow * 9,
      });
    }
    const m = c.comparable;
    if (m) {
      out.push({ ...common, kind: 'comparable', lo: m.lo, hi: m.hi, thin: m.thin, arrow: m.qualifier === '>', ...tipComparable(c, o.thinBelow, o.arrMaxMonths) });
      const flag = c.scored ? (c.above ? 'above' : 'not_above') : '';
      const range = `${fmtRange(m.lo, m.hi)}${m.qualifier === '>' ? '+' : ''}`;
      const word = c.above ? 'above' : 'not above';
      const text = c.scored ? `${range} · ${word}` : range;
      out.push({
        ...common, kind: 'label_comp', text, text_short: c.scored ? word : range, flag, arrow_r: 0, f_start: frac(m.lo), f_end: frac(m.hi),
        extra_w: flag ? 14 : 0, ...tipComparable(c, o.thinBelow, o.arrMaxMonths),
      });
    }
  }
  const views = viewKeyRows(cs.map(filterable), INTERVAL_DIMS);
  return [...out, ...views] as Row[];
}

// --- the sub-line under the title ----------------------------------------------------------------------------------
/** Wrap `text` into lines of at most `max` characters, breaking at spaces. */
function wrap(text: string, max: number): string {
  const lines: string[] = [];
  let cur = '';
  for (const w of text.split(' ')) {
    if (cur && (cur + ' ' + w).length > max) { lines.push(cur); cur = w; } else cur = cur ? `${cur} ${w}` : w;
  }
  if (cur) lines.push(cur);
  return lines.join('\n');
}
const monthsText = (n: number): string => (n === 12 ? 'a year' : `${numWord(n)} months`);
/** A month and year that must not break across lines: `Apr 2025`. */
const monthNb = (iso: string | null): string => fmtMonth(iso).replace(' ', '\u00a0');

/**
 * The scored companies a view holds whose comparable set is thin or whose ARR is dated more than `arrMaxMonths` before
 * the multiples (those two facts decide how much an "above" is worth), as a short computed sentence; '' when there are
 * none. `names` come from the drawn, scored companies the control state lets through, above-the-interval ones first.
 */
export function ledgerIntervalSubline(rows: readonly PrivateInterval[], opts: IntervalOpts & { state?: Partial<LedgerState> } = {}): string {
  const { state, ...iopts } = opts;
  const o = resolve(iopts);
  const vis = intervalCompanies(rows, iopts).filter((c) => c.drawn && c.scored && passes(filterable(c), state ?? LEDGER_DEFAULTS, INTERVAL_DIMS));
  const order = (a: IntervalCompany, b: IntervalCompany) => Number(b.above === true) - Number(a.above === true) || a.company.localeCompare(b.company);
  const thin = vis.filter((c) => c.comparable?.thin).sort(order);
  const old = vis.filter((c) => c.comparable?.arrOld).sort(order);
  const parts: string[] = [];
  if (thin.length) {
    const counts = [...new Set(thin.map((c) => (c.comparable as Comparable).tickers.length))];
    const set = counts.length === 1 ? `${numWord(counts[0])} listed ${counts[0] === 1 ? 'comparable' : 'comparables'}` : `fewer than ${numWord(o.thinBelow)} listed comparables`;
    parts.push(`${joinAnd(thin.map((c) => c.company))} ${thin.length === 1 ? 'rests' : 'rest'} on ${set}.`);
  }
  if (old.length) {
    parts.push(`${old.length === 1 ? 'ARR' : 'ARRs'} dated more than ${monthsText(o.arrMaxMonths)} before the multiples: ${joinAnd(old.map((c) => `${c.company} (${monthNb((c.comparable as Comparable).arrAsOf)})`))}.`);
  }
  return wrap(parts.join(' '), 40);
}

const sublineMemo = new WeakMap<object, Map<string, LedgerTextMap>>();
/** The sub-line for every state of the controls, as a lookup the spec's title evaluates (only states with a sub-line are listed). */
export function ledgerIntervalSublineMap(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): LedgerTextMap {
  const k = JSON.stringify(resolve(opts));
  const byOpts = sublineMemo.get(rows) ?? new Map<string, LedgerTextMap>();
  sublineMemo.set(rows, byOpts);
  let hit = byOpts.get(k);
  if (!hit) {
    hit = ledgerTextMap(INTERVAL_DIMS, (state) => ledgerIntervalSubline(rows, { ...opts, state }));
    byOpts.set(k, hit);
  }
  return hit;
}

// --- spec ---------------------------------------------------------------------------------------------------------
const TOP = -7; // px offset of the headline and estimate sub-row inside a company's band
const BOTTOM = 8; // ... and of the comparable sub-row

/**
 * Label and marker positions in pixels, from the fractions of the (fixed) axis the rows carry and the rendered plot
 * width (the scale has 14px padding at each end; the right edge has 16px of extra room). The label goes right of the
 * row's furthest end if it fits there, else left of its nearest end; a label that fits on neither side is left out (the
 * tooltip keeps what it says). `lead` is the room an open arrow takes; a flag marker takes 12px more before the text.
 */
const SIDE = [
  // At narrow plots the labels drop to their short form (the marker and the tooltip keep the rest).
  { calculate: `width < ${NARROW_PLOT} ? datum.text_short : datum.text`, as: 'lab_text' },
  { calculate: `length(datum.lab_text) * ${CH_PX} + datum.extra_w`, as: 'w_text' },
  { calculate: '14 + datum.f_end * (width - 28)', as: 'px_r' },
  { calculate: '14 + datum.f_start * (width - 28)', as: 'px_l' },
  { calculate: 'width + 16 - datum.px_r - 4', as: 'room_r' },
  { calculate: 'datum.px_l - 4', as: 'room_l' },
  { calculate: 'datum.room_r >= datum.w_text || (datum.room_l < datum.w_text && datum.room_r >= datum.room_l)', as: 'lab_right' },
  { calculate: 'datum.room_r >= datum.w_text || datum.room_l >= datum.w_text', as: 'fits' },
  { calculate: "datum.px_r + (datum.arrow_r ? 16 : 7) + (datum.flag != '' ? 12 : 0)", as: 't_right' },
  { calculate: "datum.px_l - 7 - (datum.flag != '' ? 14 : 0)", as: 't_left' },
  { calculate: 'datum.lab_right ? datum.px_r + (datum.arrow_r ? 16 : 7) + 2 : datum.px_l - 9', as: 'm_x' },
];
const px = (field: string) => ({ field, type: 'quantitative', scale: null, axis: null });

export function ledgerIntervalSpec(rows: readonly PrivateInterval[], opts: IntervalOpts & { state?: Partial<LedgerState> } = {}) {
  const { state, ...iopts } = opts;
  const values = ledgerIntervalRows(rows, iopts);
  const [d0, d1] = domainOf(intervalCompanies(rows, iopts).filter((c) => c.drawn));
  // The dollar axis is drawn twice, below the last row and above the first (46 rows are a tall figure), by two layers
  // that are present whenever anything is drawn (the headline dot and an invisible twin), their axes resolved
  // independently; every other layer shares the scale and draws no axis.
  const axisDef = { labelExpr: AXIS_LABEL_EXPR, values: ticks125(d0, d1), labelOverlap: 'greedy', labelSeparation: 8, title: null };
  const x = (field: string, axis: Record<string, unknown> | null = null) => ({
    field, type: 'quantitative', title: null,
    scale: { type: 'log', domain: [d0, d1], nice: false, padding: 14 },
    axis,
  });
  const y = {
    field: 'company', type: 'nominal', title: null,
    sort: { field: 'level', op: 'max', order: 'descending' },
    axis: { labelLimit: { expr: `width < ${NARROW_PLOT} ? 90 : 112` }, ticks: false, domain: false, grid: true, labelFontSize: 11 },
  };
  const tooltip = [
    { field: 'company', type: 'nominal', title: 'Company' },
    { field: 'layer_label', type: 'nominal', title: 'Layer' },
    { field: 'tip_what', type: 'nominal', title: 'Mark' },
    { field: 'tip_value', type: 'nominal', title: 'Value' },
    { field: 'tip_how', type: 'nominal', title: 'How it is made' },
    { field: 'tip_caveat', type: 'nominal', title: 'Caveat' },
    { field: 'tip_basis', type: 'nominal', title: 'Basis' },
    { field: 'tip_asof', type: 'nominal', title: 'As of' },
  ];
  const kind = (k: string) => ({ filter: `datum.kind == '${k}'` });
  const ink2 = tok('ink-2');
  const thinDash = { condition: { test: 'datum.thin', value: [4, 3] }, value: [1, 0] };

  // A label that fits on neither side is left out (the marker and the tooltip keep what it says).
  const textLayers = (k: 'label_head' | 'label_comp', offset: number) => [
    {
      transform: [kind(k), ...SIDE, { filter: 'datum.fits && datum.lab_right' }],
      mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 10.5, yOffset: offset, aria: false },
      encoding: { x: px('t_right'), text: { field: 'lab_text', type: 'nominal' }, color: { value: ink2 } },
    },
    {
      transform: [kind(k), ...SIDE, { filter: 'datum.fits && !datum.lab_right' }],
      mark: { type: 'text', align: 'right', baseline: 'middle', fontSize: 10.5, yOffset: offset, aria: false },
      encoding: { x: px('t_left'), text: { field: 'lab_text', type: 'nominal' }, color: { value: ink2 } },
    },
  ];
  const flagLayers = ['above', 'not_above'].map((f) => {
    const filled = f === 'above';
    return {
      // The marker stays with its label when the label fits; with no label it sits beside the row's end.
      transform: [kind('label_comp'), { filter: `datum.flag == '${f}'` }, ...SIDE],
      mark: { type: 'point', shape: filled ? 'triangle-up' : 'circle', size: filled ? 60 : 36, strokeWidth: 1.5, yOffset: BOTTOM, filled, aria: false },
      encoding: { x: px('m_x'), color: { value: filled ? series.b : series.neutral }, tooltip },
    };
  });

  const dims = INTERVAL_DIMS;
  const reasons = {
    layer: "layer == 'all' ? 'No private company has a priced\\nround in the ledger.' : 'No private company in this layer has\\na priced round in the ledger.'",
    market: "market == 'china' ? 'No private company in China has a\\nround valuation in the ledger; see the\\nledger table below.' : 'No private company in this layer and\\nmarket has a priced round.'",
    basis: lit('None of these rounds has this basis\n(disclosed or reported).'),
    confirmed: lit('None of these rounds is stated by\nthe company itself.'),
    hide_stale: lit('Every round left is stale, and stale\nrounds are hidden.'),
  };
  const reason = reasonChain(NAME, dims, reasons);
  const sub = textLookupExpr(ledgerIntervalSublineMap(rows, iopts));
  return {
    $schema: VL_SCHEMA,
    // The reason a view is empty, or else the sub-line that qualifies the title's count for the view on screen.
    title: reasonTitle(`(${reason}) != '' ? (${reason}) : (${sub})`),
    height: { step: 30 },
    padding: { right: 20 },
    params: ledgerParams(dims, state),
    data: { values, name: NAME },
    transform: [{ filter: `datum.row_type == 'mark' && ${stateFilterExpr(dims)}` }],
    encoding: { y },
    resolve: { axis: { x: 'independent' } },
    layer: [
      {
        // Estimate: a bar with a solid outline (the outline is what keeps it visible), dashed when the round is stale,
        // hollow when a publication reported it.
        transform: [kind('estimate')],
        mark: { type: 'bar', height: 9, yOffset: TOP },
        encoding: {
          x: x('lo'), x2: { field: 'hi' },
          fill: { condition: { test: 'datum.hollow', value: 'transparent' }, value: series.a },
          fillOpacity: { condition: { test: 'datum.stale', value: 0.28 }, value: 0.42 },
          stroke: { value: series.a },
          strokeWidth: { value: 1.2 },
          strokeDash: { condition: { test: 'datum.stale', value: [3, 2] }, value: [1, 0] },
          tooltip,
        },
      },
      {
        // Comparable interval: full-strength line, dashed when fewer than `thinBelow` listed comparables stand behind it.
        transform: [kind('comparable')],
        mark: { type: 'rule', strokeWidth: 2, yOffset: BOTTOM, aria: false },
        encoding: { x: x('lo'), x2: { field: 'hi' }, color: { value: series.c }, strokeDash: thinDash, tooltip },
      },
      {
        // Caps: both ends of a closed interval, the lower end only when the upper end is an arrow.
        transform: [kind('comparable'), { fold: ['lo', 'hi'], as: ['side', 'cap'] }, { filter: "!(datum.arrow && datum.side == 'hi')" }],
        mark: { type: 'tick', orient: 'vertical', thickness: 2, size: 9, yOffset: BOTTOM, aria: false },
        encoding: { x: x('cap'), color: { value: series.c }, tooltip },
      },
      {
        // Open arrowhead where the ARR is only a floor, so the interval may reach higher.
        transform: [kind('comparable'), { filter: 'datum.arrow' }],
        mark: { type: 'point', shape: 'triangle-right', filled: false, size: 44, strokeWidth: 1.5, yOffset: BOTTOM, aria: false },
        encoding: { x: x('hi'), color: { value: series.c }, tooltip },
      },
      {
        // Headline: filled when disclosed, hollow when reported, dashed outline and half fill when stale.
        transform: [kind('headline')],
        mark: { type: 'point', size: 64, strokeWidth: 1.8, yOffset: TOP },
        encoding: {
          x: x('v', axisDef),
          shape: { field: 'shape', type: 'nominal', scale: { domain: ['circle', 'diamond'], range: ['circle', 'diamond'] }, legend: null },
          stroke: { value: series.a },
          fill: { condition: { test: '!datum.hollow', value: series.a }, value: 'transparent' },
          fillOpacity: { condition: { test: 'datum.stale', value: 0.45 }, value: 1 },
          strokeDash: { condition: { test: 'datum.stale', value: [3, 2] }, value: [1, 0] },
          tooltip,
        },
      },
      {
        // A floor (`>`) is marked with an open arrowhead at the far end of the headline's row: "and more".
        transform: [kind('label_head'), { filter: 'datum.arrow_r == 1' }, ...SIDE, { calculate: 'datum.px_r + 8', as: 'a_x' }],
        mark: { type: 'point', shape: 'triangle-right', filled: false, size: 40, strokeWidth: 1.5, yOffset: TOP, aria: false },
        encoding: { x: px('a_x'), color: { value: series.a } },
      },
      ...textLayers('label_head', TOP),
      ...textLayers('label_comp', BOTTOM),
      ...flagLayers,
      {
        // The same dollar axis above the first row (an invisible layer carries it).
        transform: [kind('headline')],
        mark: { type: 'point', opacity: 0, aria: false },
        encoding: { x: x('v', { ...axisDef, orient: 'top', grid: false }) },
      },
    ],
  };
}

// --- finding, subtitle, notes, titles -----------------------------------------------------------------------------
export interface IntervalStats {
  total: number; drawn: number; noRound: number; comparableOnly: number; belowCut: number;
  withRound: number; withComparable: number; scored: number; above: number; aboveNames: string[];
  reported: number; reportedNames: string[]; stale: number; floors: number; thin: number; chinese: number;
  /** Scored companies whose comparable set is thin / whose ARR is dated more than `arrMaxMonths` before the multiples. */
  thinScored: string[]; oldArrScored: string[];
  /** The layers (full names, in layer order) in which a drawn comparable interval rests on fewer than `thinBelow` listed comparables. */
  thinLayers: string[];
  noComparableReasons: { reason: string; n: number }[];
  discounts: { year: number; discount: number }[]; band: number | null;
}

/** Counts behind the finding and the notes, for the companies a state lets through (default: all). */
export function ledgerIntervalStats(rows: readonly PrivateInterval[], opts: IntervalOpts & { state?: Partial<LedgerState> } = {}): IntervalStats {
  const { state, ...iopts } = opts;
  const all = intervalCompanies(rows, iopts);
  const vis = all.filter((c) => c.drawn && passes(filterable(c), state ?? LEDGER_DEFAULTS, INTERVAL_DIMS));
  const reasons = new Map<string, number>();
  for (const c of vis) {
    if (!c.noComparable) continue;
    const key = /no registered public comparables/.test(c.noComparable) ? 'its layer has no registered listed comparables'
      : /ARR/.test(c.noComparable) ? 'the company states no current ARR' : c.noComparable;
    reasons.set(key, (reasons.get(key) ?? 0) + 1);
  }
  const scored = vis.filter((c) => c.scored);
  const above = scored.filter((c) => c.above === true);
  const years = new Map<number, number>();
  for (const r of rows) if (r.discount_scored === true && r.vintage_year != null && r.discount != null) years.set(r.vintage_year, r.discount);
  const reported = vis.filter((c) => c.headline?.basis === 'reported');
  return {
    total: all.length, drawn: vis.length,
    noRound: all.filter((c) => c.marks === 0).length,
    comparableOnly: all.filter((c) => !c.drawn && !c.headline && c.comparable).length,
    belowCut: all.filter((c) => !c.drawn && c.headline).length,
    withRound: vis.filter((c) => c.headline).length,
    withComparable: vis.filter((c) => c.comparable).length,
    scored: scored.length, above: above.length, aboveNames: above.map((c) => c.company),
    reported: reported.length, reportedNames: reported.map((c) => c.company).sort(),
    stale: vis.filter((c) => c.headline?.stale).length,
    floors: vis.filter((c) => c.comparable?.qualifier === '>').length,
    thin: vis.filter((c) => c.comparable?.thin).length,
    chinese: vis.filter((c) => c.market === 'china').length,
    thinLayers: LAYERS.filter((l) => vis.some((c) => c.layer === l.key && c.comparable?.thin)).map((l) => l.name as string),
    thinScored: scored.filter((c) => c.comparable?.thin).map((c) => c.company),
    oldArrScored: scored.filter((c) => c.comparable?.arrOld).map((c) => c.company),
    noComparableReasons: [...reasons].map(([reason, n]) => ({ reason, n })).sort((a, b) => b.n - a.n),
    discounts: [...years].map(([year, discount]) => ({ year, discount })).sort((a, b) => a.year - b.year),
    band: rows.find((r) => r.discount_band != null)?.discount_band ?? null,
  };
}

const list = joinAnd;

/**
 * Figure title, computed from the rows (for the default view, or for the view `state` describes): how many of the
 * companies scored on all three numbers have an age-discounted valuation entirely above their comparable interval.
 */
export function ledgerIntervalFinding(rows: readonly PrivateInterval[], opts: IntervalOpts & { state?: Partial<LedgerState> } = {}): string {
  const s = ledgerIntervalStats(rows, opts);
  if (s.drawn === 0) return 'No private company in this view has a priced round to draw';
  if (s.scored === 0) {
    return `${s.drawn} private ${s.drawn === 1 ? 'company has' : 'companies have'} a priced round in this view; none has all three numbers, so none can be set against its listed comparables`;
  }
  const names = s.above ? `: ${list(s.aboveNames)}` : '';
  const head = s.scored === 1 ? 'The one private company' : `${s.above} of the ${s.scored} private companies`;
  if (s.scored === 1) {
    return `${head} with all three numbers ${s.above ? 'sits above' : 'does not sit above'} its listed-comparable interval${names}`;
  }
  return `${head} with all three numbers sit${s.above === 1 ? 's' : ''} above their listed-comparable interval after the age discount${names}`;
}

/** Subtitle: the metric, unit and scale only. The key to the marks and the counts are in `ledgerIntervalNotes`. */
export function ledgerIntervalSubtitle(_rows?: readonly PrivateInterval[], _opts: IntervalOpts = {}): string {
  return 'Valuation in US dollars, log scale, one row per private company with a priced round: the round as stated, an age-discounted estimate and an interval from listed comparables.';
}

/**
 * Everything the subtitle leaves out, as plain text (no HTML) for the "How to read this" disclosure: paragraphs separated
 * by a blank line. Counts are for all views, from the rows.
 */
export function ledgerIntervalNotes(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): string {
  const o = resolve(opts);
  const s = ledgerIntervalStats(rows, opts);
  const disc = s.discounts.length
    ? `${fmtPct(s.discounts[0].discount)} for a ${s.discounts[0].year} round to ${fmtPct(s.discounts[s.discounts.length - 1].discount)} for a ${s.discounts[s.discounts.length - 1].year} round`
    : 'by the year of the round';
  const band = s.band != null ? `plus or minus ${Math.round(s.band * 100)} points` : 'with a registered band';
  const left = [
    s.noRound ? `${s.noRound} have no priced round in the ledger` : '',
    s.comparableOnly ? `${s.comparableOnly} have a comparable interval but no priced round` : '',
    s.belowCut ? `${s.belowCut} fall below the cut (fewer than ${numWord(o.minMarks)} marks and no disclosed headline of ${fmtMoney(o.sizeCut)} or more)` : '',
  ].filter(Boolean).join('; ');
  const noComp = s.noComparableReasons.length
    ? `${s.withRound - s.withComparable} of the ${s.withRound} drawn rounds have no comparable interval (${s.noComparableReasons.map((r) => `${r.n}: ${r.reason}`).join('; ')}).`
    : '';
  const reported = s.reported
    ? `Hollow dot: a publication reported the figure, not the company, and the curator marked it chartable (${list(s.reportedNames)}). Reports of talks, third-party estimates and secondary marks are never charted.`
    : 'No drawn figure is a publication’s report. Reports of talks, third-party estimates and secondary marks are never charted.';
  return [
    `${s.drawn} of ${s.total} private companies are drawn${left ? `; ${left}` : ''}. Counts are for the whole ledger, whatever the controls select.`,
    `Dot: the latest round valuation as stated. ${reported} Dashed outline and half fill: a stale round (a newer round exists or none for two years), labelled "stale" with its year. Open arrow: the figure is only a floor. Diamond: a company in China. The tooltip carries the curator’s caveat for the figure (a merger value, a part-secondary round, a tender mixed in).`,
    `Bar with an outline: an estimate, not data: the round less the registered discount for its year (${disc}), ${band}.`,
    `Capped line: company-stated ARR times the lowest to highest EV/NTM revenue of the listed comparables. Open arrow: the ARR is only a floor. Dashed line: fewer than ${numWord(o.thinBelow)} listed comparables stand behind it${s.thinLayers.length ? ` (${s.thinLayers.length === 1 ? 'layer' : 'layers'}: ${s.thinLayers.join('; ')})` : ''}. ${noComp}`.trim(),
    `Triangle: the estimate lies entirely above the comparable interval; open circle: it does not. The scoring is the registered one and is not changed; the line under the title names the scored companies whose comparable set is thin (${s.thinScored.length ? list(s.thinScored) : 'none'}) or whose ARR is dated more than ${monthsText(o.arrMaxMonths)} before the multiples (${s.oldArrScored.length ? list(s.oldArrScored) : 'none'}).`,
  ].join('\n\n');
}

// --- the table behind the figure ----------------------------------------------------------------------------------
/** One row per drawn company, plain text only (so the same file can feed a table and a CSV download). */
export interface IntervalTableRow {
  company: string; layer: string; market: string; headline: string; headline_value: number | null; basis: string; estimate: string; comparable: string;
  arr: string; comparables: string; reading: string; status: string;
}

export function intervalTableRows(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): IntervalTableRow[] {
  return intervalCompanies(rows, opts)
    .filter((c) => c.drawn)
    .sort((a, b) => b.level - a.level || a.company.localeCompare(b.company))
    .map((c) => {
      const h = c.headline;
      const m = c.comparable;
      return {
        company: c.company, layer: layerName(c.layer), market: c.market === 'china' ? 'China' : 'Overseas',
        headline: h ? `${fmtValue(h.value, 'USD', h.qualifier)}${h.year ? `, ${h.year} round` : ''}` : '—',
        headline_value: h ? h.value : null,
        basis: h ? upFirst(h.basis) : '—',
        estimate: c.estimate ? `${fmtRange(c.estimate.lo, c.estimate.hi)}${h?.qualifier === '>' ? ' or more' : ''}` : '—',
        comparable: m ? `${fmtRange(m.lo, m.hi)}${m.qualifier === '>' ? ' or more' : ''}` : '—',
        arr: m ? `${fmtValue(m.arr, 'USD', m.arrQualifier)}${m.arrAsOf ? ` (${fmtMonth(m.arrAsOf)})` : ''}${m.arrOld ? ', old' : ''}` : '—',
        comparables: m ? m.tickers.join(', ') + (m.thin ? ' (thin)' : '') : '—',
        reading: c.scored ? (c.above ? 'above' : 'not above') : 'not scored',
        status: h?.stale ? 'stale round' : 'current',
      };
    });
}
export const INTERVAL_TABLE_COLUMNS = [
  { key: 'company', label: 'Company' },
  { key: 'layer', label: 'Layer' },
  { key: 'headline', label: 'Latest round valuation' },
  { key: 'basis', label: 'Basis' },
  { key: 'estimate', label: 'Estimate (not data)' },
  { key: 'comparable', label: 'Listed-comparable interval' },
  { key: 'arr', label: 'ARR (date)' },
  { key: 'comparables', label: 'Comparables' },
  { key: 'reading', label: 'Estimate against interval' },
  { key: 'status', label: 'Round' },
];
export const INTERVAL_TABLE_ROW_HEADER = 'company';
/** Filters for the interval table: the layers present, the reading and the round status. */
export function intervalTableSelects(rows: readonly IntervalTableRow[]) {
  return [
    { column: 'layer', label: 'Layer', options: LAYERS.map((l) => l.name as string).filter((n) => rows.some((r) => r.layer === n)) },
    { column: 'reading', label: 'Estimate against interval', options: ['above', 'not above', 'not scored'] },
    { column: 'status', label: 'Round', options: ['current', 'stale round'] },
  ];
}

const titleMemo = new WeakMap<object, Map<string, LedgerTitleMap>>();

/**
 * Titles for every state of the controls figure 6 answers to, with the controls that matter (`keys`), the map
 * (`titles`) and the build-time title (`initial`). Memoised per rows array and options.
 */
export function ledgerIntervalTitleMap(rows: readonly PrivateInterval[], opts: IntervalOpts = {}): LedgerTitleMap {
  const o = JSON.stringify(resolve(opts));
  const byOpts = titleMemo.get(rows) ?? new Map<string, LedgerTitleMap>();
  titleMemo.set(rows, byOpts);
  let hit = byOpts.get(o);
  if (!hit) {
    hit = ledgerTitleMap(INTERVAL_DIMS, (state) => ledgerIntervalFinding(rows, { ...opts, state }));
    byOpts.set(o, hit);
  }
  return hit;
}
/** `titles` for <VegaChart group="ledger" titles={...} titleKeys={ledgerIntervalTitleKeys(rows)}>. */
export const ledgerIntervalTitles = (rows: readonly PrivateInterval[], opts: IntervalOpts = {}): Record<string, string> => ledgerIntervalTitleMap(rows, opts).titles;
/** `titleKeys` for the same chart: the controls that change a title for this data, in control order. */
export const ledgerIntervalTitleKeys = (rows: readonly PrivateInterval[], opts: IntervalOpts = {}): LedgerDim[] => ledgerIntervalTitleMap(rows, opts).keys;
