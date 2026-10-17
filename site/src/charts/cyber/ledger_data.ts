/**
 * Private ledger and events (dashboard 3): the data layer shared by figure 6 (valuation intervals), figure 7
 * (deal timeline), the announced-versus-filed dumbbell and the tables behind them.
 *
 * Inputs are the chartable marts `private_intervals`, `ledger_timeline` and `events` (see `types.ts`). A row reaches a
 * chart only if the curator marked it chartable, its tag is V or C and its metric kind is not one of the never-charted
 * kinds. The pipeline already guarantees that; `mayChart` repeats the rule here so a stale mart can never put a tag S or
 * U row on a figure. `reported_unconfirmed` is a table only and is not read here.
 *
 * Group `ledger`, six controls (`LEDGER_CONTROLS`). A figure declares an unbound top-level param for each control that
 * means something to it and ignores the rest:
 *
 *   control       figure 6 (valuations)   figure 7 (deals)                      dumbbell, tables
 *   layer         yes                     yes                                   no
 *   market        yes                     yes                                   no
 *   basis         yes (headline basis)    no (a deal has no valuation basis)    no
 *   metric_kind   no                      yes (the kind of price that sizes     no
 *                                         the mark, see `EVENT_PRICE_KINDS`)
 *   confirmed     yes                     yes                                   no
 *   hide_stale    yes (stale rounds)      no (a deal is not a round that goes   no
 *                                         stale)
 *
 * `CONTROL_NOTE` says this in one paragraph for the reader; print it under the controls. `confirmed` and `hide_stale`
 * are booleans (`true` = filter on). `DashboardControls` links a table by string equality of `data-<name>` attributes,
 * which cannot express "all", so the long tables are not linked to the controls: give each lazy `UrlTable` its own
 * selects (`ledgerTableSelects`, `eventsTableSelects`, ...). The rows come from `ledgerTableRows(timeline)` and its
 * siblings, written to files the page loads by URL (links and greyed cells need markup). Cells are HTML: every text
 * that reaches one goes through `htmlText`, which escapes it and marks Chinese runs `lang="zh-Hans"`.
 */
import { tok } from '../theme';
import type { EventRow, LedgerRow } from './types';

export const LEDGER_GROUP = 'ledger';

// --- text helpers (no number is ever typed into a string that reaches the page: counts come through these) -------
const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
/** A count as a word up to twelve ('two'), as digits above that. */
export const numWord = (n: number): string => (Number.isInteger(n) && n >= 0 && n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n));
/** `a, b and c`. */
export const joinAnd = (xs: readonly string[]): string => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
/** Upper-case the first letter. */
export const upFirst = (s: string): string => s.replace(/^./, (c) => c.toUpperCase());

/** Escape text for an HTML cell. */
export const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const CJK_RUN = /[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]+/g;
/** Text for an HTML cell: escaped, with each run of Chinese marked `lang="zh-Hans"` so a screen reader switches voice. */
export const htmlText = (s: string): string => esc(s).replace(CJK_RUN, (m) => `<span lang="zh-Hans">${m}</span>`);
/** An anchor to a public page, opening nothing else; the label is escaped and its Chinese runs are marked. */
export const linkHtml = (url: string | null | undefined, label: string | null | undefined): string =>
  url ? `<a href="${esc(url)}" rel="noopener">${htmlText(label || 'source')}</a>` : '—';

// --- what may be charted ------------------------------------------------------------------------------------------
export const CHARTABLE_TAGS = ['V', 'C'] as const;
export const NEVER_CHARTED_KINDS = ['secondary_mark', 'reported_talks', 'third_party_estimate'] as const;

export const tagCharts = (tag: string | null | undefined): boolean => tag === 'V' || tag === 'C';
export const kindCharts = (kind: string | null | undefined): boolean =>
  !kind || !(NEVER_CHARTED_KINDS as readonly string[]).includes(kind);

/** The single rule: marked chartable, tag V or C, and not a never-charted kind. A missing flag is not chartable. */
export function mayChart(r: { chartable?: boolean | null; tag?: string | null; metric_kind?: string | null }): boolean {
  return r.chartable === true && tagCharts(r.tag) && kindCharts(r.metric_kind);
}

/** A round that has gone stale (a newer round exists or none for two years) or a row overdue for its re-check. */
export const isStale = (r: { stale?: boolean | null; stale_mark?: boolean | null }): boolean => r.stale === true || r.stale_mark === true;

// --- layers -------------------------------------------------------------------------------------------------------
/** The twelve layers in code order: full name (control options, tables) and a short name (axis labels). */
export const LAYERS = [
  { key: 'network_edge', name: 'Network and edge security', short: 'Network and edge' },
  { key: 'endpoint', name: 'Endpoint and XDR', short: 'Endpoint, XDR' },
  { key: 'identity', name: 'Identity and access', short: 'Identity' },
  { key: 'cloud_appsec', name: 'Cloud and application security', short: 'Cloud, AppSec' },
  { key: 'data_privacy', name: 'Data security and privacy', short: 'Data security' },
  { key: 'secops', name: 'Security operations', short: 'Security ops' },
  { key: 'email_human', name: 'Email, collaboration and human risk', short: 'Email and human' },
  { key: 'ot_iot', name: 'OT, IoT and ICS', short: 'OT and IoT' },
  { key: 'grc_insurance', name: 'GRC, compliance and cyber insurance', short: 'GRC, insurance' },
  { key: 'services', name: 'Services, MSSP, consulting and distribution', short: 'Services' },
  { key: 'ai_security', name: 'AI security and AI-native security operations', short: 'AI security' },
  { key: 'infra_hw', name: 'Infrastructure, hardware and post-quantum', short: 'Infra, hardware' },
] as const;
export const LAYER_NAME: Record<string, string> = Object.fromEntries(LAYERS.map((l) => [l.key, l.name]));
export const LAYER_SHORT: Record<string, string> = Object.fromEntries(LAYERS.map((l) => [l.key, l.short]));
export const layerName = (k: string | null | undefined): string => (k ? LAYER_NAME[k] ?? k : '—');
export const layerShort = (k: string | null | undefined): string => (k ? LAYER_SHORT[k] ?? k : '—');

// --- controls -----------------------------------------------------------------------------------------------------
export interface LedgerState {
  layer: string;
  market: string;
  metric_kind: string;
  basis: string;
  confirmed: boolean;
  hide_stale: boolean;
}
export type LedgerDim = keyof LedgerState;
export const LEDGER_DEFAULTS: LedgerState = { layer: 'all', market: 'all', metric_kind: 'all', basis: 'all', confirmed: false, hide_stale: false };

export interface ControlOption { value: string | boolean; label: string }
export interface LedgerControl { name: LedgerDim; label: string; options: ControlOption[]; default: string | boolean; type?: 'radio' | 'select' }

/** The metric kinds the ledger carries (closed vocabulary pinned in `config.METRIC_KINDS`, charted ones only): table labels. */
export const METRIC_KIND_LABEL: Record<string, string> = {
  primary_round_valuation: 'Round valuation',
  round_amount: 'Round amount raised',
  arr_company_stated: 'ARR, as the company states it',
  tender_valuation: 'Tender-offer valuation',
  acquisition_price_announced: 'Acquisition price, announced',
  acquisition_price_sec: 'Acquisition price, in a filing',
  take_private_price: 'Take-private price',
};
export const kindLabel = (k: string | null | undefined): string => (k ? METRIC_KIND_LABEL[k] ?? k.replace(/_/g, ' ') : '—');

/**
 * The kinds of price a mark of the deal timeline can carry, which is all the Kind control offers: the price that sizes
 * a mark (the filing figure where it is drawn, else the announced or take-private price) or the amount of a funding
 * round. A valuation, an ARR or a tender price is never an event's price, so those kinds are not offered here; the long
 * ledger table filters on every kind.
 */
export const EVENT_PRICE_KINDS = ['acquisition_price_announced', 'acquisition_price_sec', 'take_private_price', 'round_amount'] as const;

export const BASIS_LABEL: Record<string, string> = { disclosed: 'Disclosed', reported: 'Reported', undisclosed: 'Undisclosed' };
export const MARKET_LABEL: Record<string, string> = { overseas: 'Overseas', china: 'China' };

/**
 * The exact `controls` prop for <DashboardControls group="ledger">. The labels say which figure a control moves; see
 * `CONTROL_NOTE`. Basis offers the two bases a drawn valuation can have: an undisclosed round has no valuation, so
 * choosing it could only empty the figure.
 */
export const LEDGER_CONTROLS: LedgerControl[] = [
  {
    name: 'layer', label: 'Layer', type: 'select', default: 'all',
    options: [{ value: 'all', label: 'All layers' }, ...LAYERS.map((l) => ({ value: l.key as string, label: l.name as string }))],
  },
  {
    name: 'market', label: 'Market', type: 'radio', default: 'all',
    options: [{ value: 'all', label: 'Both' }, { value: 'overseas', label: 'Overseas' }, { value: 'china', label: 'China' }],
  },
  {
    name: 'metric_kind', label: 'Kind of price (deals)', type: 'select', default: 'all',
    options: [{ value: 'all', label: 'All kinds' }, ...EVENT_PRICE_KINDS.map((value) => ({ value: value as string, label: METRIC_KIND_LABEL[value] }))],
  },
  {
    name: 'basis', label: 'Basis (valuations)', type: 'radio', default: 'all',
    options: [{ value: 'all', label: 'Any' }, { value: 'disclosed', label: 'Disclosed' }, { value: 'reported', label: 'Reported' }],
  },
  {
    name: 'confirmed', label: 'Source', type: 'radio', default: false,
    options: [{ value: false, label: 'Everything with a public link' }, { value: true, label: 'Company-confirmed only' }],
  },
  {
    name: 'hide_stale', label: 'Old rounds (valuations)', type: 'radio', default: false,
    options: [{ value: false, label: 'Show stale rounds' }, { value: true, label: 'Hide stale rounds' }],
  },
];

/** Which of the two linked figures a control moves ('valuations' is figure 6, 'deals' the timeline). */
export const CONTROL_SCOPE: Record<LedgerDim, readonly ('valuations' | 'deals')[]> = {
  layer: ['valuations', 'deals'], market: ['valuations', 'deals'], confirmed: ['valuations', 'deals'],
  basis: ['valuations'], hide_stale: ['valuations'], metric_kind: ['deals'],
};

/**
 * One paragraph under the controls: which control drives which figure, and what drives neither. Static on purpose: it
 * describes the wiring (`CONTROL_SCOPE`), which holds whatever the data say.
 */
export const CONTROL_NOTE =
  'Layer, market and source change both figures below. Basis and old rounds are about valuations, so they change the valuation figure only (a deal is not a round that can go stale); '
  + 'kind of price changes the deal timeline only. The comparison of announced and filed prices and the tables do not follow these controls; the tables have their own filters.';

/** Top-level, unbound Vega-Lite params for the controls a figure uses (values are the defaults unless overridden). */
export function ledgerParams(dims: readonly LedgerDim[], opts: Partial<LedgerState> = {}) {
  return dims.map((name) => ({ name, value: opts[name] ?? LEDGER_DEFAULTS[name] }));
}

// --- filtering by control state ----------------------------------------------------------------------------------
/** What a figure's rows must carry for the controls to filter them. `basis` and `metric_kind` are optional per figure. */
export interface Filterable {
  layer: string | null;
  market: string | null;
  basis?: string | null;
  metric_kind?: string | null;
  confirmed: boolean;
  stale: boolean;
}

/** The Vega expression that keeps a row (a `datum`) when it matches the current param values of `dims`. */
export function stateFilterExpr(dims: readonly LedgerDim[]): string {
  const part: Record<LedgerDim, string> = {
    layer: "(layer == 'all' || datum.layer == layer)",
    market: "(market == 'all' || datum.market == market)",
    basis: "(basis == 'all' || datum.basis == basis)",
    metric_kind: "(metric_kind == 'all' || datum.metric_kind == metric_kind)",
    confirmed: '(!confirmed || datum.confirmed)',
    hide_stale: '(!hide_stale || !datum.stale)',
  };
  return dims.map((d) => part[d]).join(' && ') || 'true';
}

/** The same rule in TypeScript (titles are computed at build time with it). */
export function passes(row: Filterable, state: Partial<LedgerState>, dims: readonly LedgerDim[]): boolean {
  const s = { ...LEDGER_DEFAULTS, ...state };
  return dims.every((d) => {
    switch (d) {
      case 'layer': return s.layer === 'all' || row.layer === s.layer;
      case 'market': return s.market === 'all' || row.market === s.market;
      case 'basis': return s.basis === 'all' || row.basis === s.basis;
      case 'metric_kind': return s.metric_kind === 'all' || row.metric_kind === s.metric_kind;
      case 'confirmed': return !s.confirmed || row.confirmed;
      case 'hide_stale': return !s.hide_stale || !row.stale;
    }
  });
}

const optionsOf = (d: LedgerDim): (string | boolean)[] => (LEDGER_CONTROLS.find((c) => c.name === d)?.options ?? []).map((o) => o.value);

/** Every combination of the controls' options for `dims` (the cartesian product, in `dims` order). */
export function stateCombos(dims: readonly LedgerDim[]): LedgerState[] {
  let acc: Partial<LedgerState>[] = [{}];
  for (const d of dims) acc = acc.flatMap((a) => optionsOf(d).map((v) => ({ ...a, [d]: v })));
  return acc.map((a) => ({ ...LEDGER_DEFAULTS, ...a }));
}

/** The key `DashboardControls` builds for a state: control values joined with '|' in `dims` order. */
export const stateKey = (state: Partial<LedgerState>, dims: readonly LedgerDim[]): string =>
  dims.map((d) => String(({ ...LEDGER_DEFAULTS, ...state })[d])).join('|');

export interface LedgerTitleMap {
  /** The controls that change the title somewhere (pass as `titleKeys`); the rest never do, for this data. */
  keys: LedgerDim[];
  /** For <VegaChart titles>: `default` is the most common title (usually the sentence for a view with nothing in it); every other state has its own key. */
  titles: Record<string, string>;
  /** The title of the view with every control at its default, which is what the page prints at build time. */
  initial: string;
}

/**
 * One finding per combination of the controls in `dims`, as a `titles` map for <VegaChart group titles titleKeys>.
 * Two reductions keep the map small, and neither changes any title: a control that changes no title for this data is
 * left out of the keys (it is held at its default when the title is computed), and the most common title becomes
 * `default`, so only the states that differ from it are listed. The all-default view always has its own key.
 */
export function ledgerTitleMap(dims: readonly LedgerDim[], titleFor: (state: LedgerState) => string): LedgerTitleMap {
  const effective = dims.filter((d) => {
    const others = dims.filter((x) => x !== d);
    return stateCombos(others).some((s) => new Set(optionsOf(d).map((v) => titleFor({ ...s, [d]: v }))).size > 1);
  });
  const entries = stateCombos(effective).map((s) => [stateKey(s, effective), titleFor(s)] as const);
  const freq = new Map<string, number>();
  entries.forEach(([, t]) => freq.set(t, (freq.get(t) ?? 0) + 1));
  const common = [...freq].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? titleFor(LEDGER_DEFAULTS);
  const initial = titleFor(LEDGER_DEFAULTS);
  const initialKey = stateKey(LEDGER_DEFAULTS, effective);
  const titles: Record<string, string> = { default: common };
  for (const [k, t] of entries) if (t !== common || k === initialKey) titles[k] = t;
  return { keys: effective, titles, initial };
}

export interface LedgerTextMap {
  /** The controls that change the text somewhere (held at their defaults otherwise). */
  keys: LedgerDim[];
  /** Key (control values joined with '|' in `keys` order) to text, for the states that have one; a state not listed has none. */
  entries: Record<string, string>;
}

/**
 * Text per control state, for a line a figure draws itself (a Vega expression looks it up with `textLookupExpr`). Only
 * the controls that change the text are keys, and only the states with a non-empty text are listed, so a figure whose
 * line is empty in most views stays small.
 */
export function ledgerTextMap(dims: readonly LedgerDim[], textFor: (state: LedgerState) => string): LedgerTextMap {
  const effective = dims.filter((d) => {
    const others = dims.filter((x) => x !== d);
    return stateCombos(others).some((s) => new Set(optionsOf(d).map((v) => textFor({ ...s, [d]: v }))).size > 1);
  });
  const entries: Record<string, string> = {};
  for (const s of stateCombos(effective)) {
    const t = textFor(s);
    if (t) entries[stateKey(s, effective)] = t;
  }
  return { keys: effective, entries };
}

/** The Vega expression for the text of the current control state, '' when the state has none. */
export function textLookupExpr(map: LedgerTextMap): string {
  const obj = `{${Object.entries(map.entries).map(([k, v]) => `${lit(k)}: ${lit(v)}`).join(', ')}}`;
  const key = map.keys.length ? `join([${map.keys.join(', ')}], '|')` : "''";
  return Object.keys(map.entries).length ? `(${obj})[${key}] || ''` : "''";
}

/**
 * Rows that let a figure say why it is empty, from the data itself: one `{row_type: 'view', key}` per prefix of the
 * control state, for the states in which at least one row survives. A figure can then test, in order, whether the layer
 * alone, layer and market, and so on still leave a row (`reasonChain`), without a loop in Vega.
 * Prefix keys are the values of the first i dims joined with '|' (a boolean as 'true' or 'false').
 */
export function viewKeyRows(rows: readonly Filterable[], dims: readonly LedgerDim[]): { row_type: 'view'; key: string }[] {
  const keys = new Set<string>();
  for (const r of rows) {
    let prefixes: string[] = [''];
    dims.forEach((d, i) => {
      let vals: (string | boolean)[];
      switch (d) {
        case 'layer': vals = r.layer ? [r.layer, 'all'] : ['all']; break;
        case 'market': vals = r.market ? [r.market, 'all'] : ['all']; break;
        case 'basis': vals = r.basis ? [r.basis, 'all'] : ['all']; break;
        case 'metric_kind': vals = r.metric_kind ? [r.metric_kind, 'all'] : ['all']; break;
        case 'confirmed': vals = r.confirmed ? [false, true] : [false]; break;
        case 'hide_stale': vals = r.stale ? [false] : [false, true]; break;
      }
      prefixes = prefixes.flatMap((p) => vals.map((v) => (i === 0 ? String(v) : `${p}|${String(v)}`)));
      prefixes.forEach((p) => keys.add(p));
    });
  }
  return [...keys].sort().map((key) => ({ row_type: 'view' as const, key }));
}

/** A Vega expression string literal (single-quoted, escaped; '\n' becomes a line break in a title). */
export const lit = (s: string): string => `'${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n')}'`;

/**
 * Vega expression for the first reason a view is empty: for each dim in order, the text to show when no row survives
 * the controls up to and including that dim (`texts[dim]` is a Vega expression, usually `lit(...)`). Empty string when
 * something is drawn. The dataset `src` must contain the `viewKeyRows`.
 */
export function reasonChain(src: string, dims: readonly LedgerDim[], texts: Partial<Record<LedgerDim, string>>): string {
  const steps = dims.map((d, i) => `!indata(${lit(src)}, 'key', ${dims.slice(0, i + 1).join(" + '|' + ")}) ? ${texts[d] ?? lit('Nothing matches these filters.')}`);
  return [...steps, "''"].join(' : ');
}

/** Title object that shows a one-sentence reason above the plot (empty string: no reason, nothing shown). */
export function reasonTitle(reasonExpr: string) {
  return {
    text: { expr: `split(${reasonExpr}, '\\n')` },
    anchor: 'start', frame: 'bounds', orient: 'top', offset: 6,
    fontSize: 12, fontStyle: 'italic', fontWeight: 'normal', color: tok('ink-2'), lineHeight: 16, limit: 0,
  };
}

// --- number formatting --------------------------------------------------------------------------------------------
const MINUS = '−';
const sig = (x: number): string => String(+x.toPrecision(3));

/** Magnitude with a unit suffix: $6.8B, $870M, $1.15B. Three significant digits, trailing zeros dropped. */
function magnitude(v: number): { n: string; suffix: string } {
  const a = Math.abs(v);
  if (a >= 1e12) return { n: sig(v / 1e12), suffix: 'T' };
  if (a >= 1e9) return { n: sig(v / 1e9), suffix: 'B' };
  if (a >= 1e6) return { n: sig(v / 1e6), suffix: 'M' };
  if (a >= 1e3) return { n: sig(v / 1e3), suffix: 'K' };
  return { n: sig(v), suffix: '' };
}
const currencyPrefix = (unit: string | null | undefined): string => (!unit || unit === 'USD' ? '$' : `${unit} `);

/** A money amount without a qualifier: `$6.8B`, `CNY 300M`. A currency is never converted. */
export function fmtMoney(v: number | null | undefined, unit: string | null | undefined = 'USD'): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const { n, suffix } = magnitude(v);
  return `${currencyPrefix(unit)}${n}${suffix}`;
}

/** The qualifier as words ('more than', 'up to', 'about') or as a short symbol for tight labels ('>', '<', '~'). */
export function qualifierText(q: string | null | undefined, style: 'words' | 'symbol' = 'words'): string {
  if (q === '>') return style === 'words' ? 'more than ' : '>';
  if (q === '<') return style === 'words' ? 'up to ' : '<';
  if (q === '~') return style === 'words' ? 'about ' : '~';
  return '';
}

/** A number as stated: qualifier then amount ('more than $400M', 'about $870M', '$6.8B'). */
export function fmtValue(
  v: number | null | undefined, unit: string | null | undefined = 'USD', qualifier: string | null | undefined = '=',
  style: 'words' | 'symbol' = 'words',
): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${qualifierText(qualifier, style)}${fmtMoney(v, unit)}`;
}

/** A range with one shared suffix where both ends have it: `$2.04–4.08B`, otherwise `$870M–$1.2B`. */
export function fmtRange(lo: number | null | undefined, hi: number | null | undefined, unit: string | null | undefined = 'USD'): string {
  if (lo == null || hi == null) return fmtMoney(lo ?? hi, unit);
  const a = magnitude(lo);
  const b = magnitude(hi);
  if (a.suffix === b.suffix) return `${currencyPrefix(unit)}${a.n}–${b.n}${b.suffix}`;
  return `${fmtMoney(lo, unit)}–${fmtMoney(hi, unit)}`;
}

/** A signed percentage with a true minus: `−7.9%`, `+3%`. */
export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x == null || !Number.isFinite(x)) return '—';
  const s = (Math.abs(x) * 100).toFixed(digits);
  return `${x < 0 ? MINUS : x > 0 ? '+' : ''}${s}%`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** `2026-10-12` -> `Oct 2026`. Anything else is returned as it came. */
export function fmtMonth(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso ?? '');
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : iso ?? '—';
}
/** `2026-10-12` -> `12 Oct 2026`. */
export function fmtDay(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : iso ?? '—';
}

// --- the long ledger table ----------------------------------------------------------------------------------------
/**
 * One row of the lazy table. Every text is HTML-safe (`htmlText`); `value` and `status` of a superseded or stale row are
 * wrapped in a grey span and `status` says so in words, so the meaning does not rest on colour. Only those two cells carry
 * markup: UrlTable sorts on a cell's raw string, so a sort on any other column is not disturbed, and a sort on Status groups
 * the old rows together. `market`, `kind`, `confirmed` and `old` are filter keys (their values are exactly the options of
 * `ledgerTableSelects`), not columns.
 */
export interface LedgerTableRow {
  company: string;
  layer: string;
  market: string;
  figure: string;
  kind: string;
  value: string;
  amount: number | null;
  period: string;
  basis: string;
  tag: string;
  status: string;
  confirmed: 'yes' | 'no';
  old: 'yes' | 'no';
  source: string;
  last_checked: string;
}

/** What a tag letter means, for the hover text of the Tag column. */
const TAG_TITLE: Record<string, string> = { V: 'read on the primary page', C: 'computed from figures read on the page' };
const GREY = (s: string): string => `<span style="color:var(--ink-3)">${s}</span>`;

const FIGURE_LABEL: Record<string, string> = {
  round_valuation: 'Valuation at a priced round',
  round_amount: 'Amount raised in a round',
  arr: 'ARR, as the company states it',
  acquisition_price: 'Acquisition price, announced',
  acquisition_price_sec: 'Acquisition price, in a filing',
  take_private_price: 'Take-private price',
  secondary_offering_amount: 'Secondary offering, amount',
  secondary_offering_valuation: 'Secondary offering, valuation',
};
const figureLabel = (m: string | null): string => (m ? FIGURE_LABEL[m] ?? upFirst(m.replace(/_/g, ' ')) : '—');

/** The Status cell's words: superseded, stale round (a newer round exists or none for two years), overdue for its re-check, else the mart's own. */
export function ledgerStatus(r: { status?: string | null; stale?: boolean | null; stale_mark?: boolean | null }): string {
  if (r.status === 'superseded') return 'superseded';
  if (r.stale_mark === true) return 'stale round';
  if (r.stale === true) return 'overdue for re-check';
  return r.status ?? 'current';
}

/**
 * Rows for the lazy `UrlTable` over the whole ledger: every chartable row (superseded rows included: they are the
 * history), nothing else, with readable labels, the source as a link and old rows greyed and labelled. Sorted by
 * company, newest period first.
 */
export function ledgerTableRows(timeline: readonly LedgerRow[]): LedgerTableRow[] {
  return timeline
    .filter((r) => mayChart(r) && r.company)
    .map((r) => {
      const status = ledgerStatus(r);
      const old = r.status === 'superseded' || isStale(r);
      const grey = (s: string) => (old ? GREY(s) : s);
      return {
        company: esc(r.company as string),
        layer: layerName(r.layer),
        market: MARKET_LABEL[r.market ?? ''] ?? r.market ?? '—',
        figure: esc(figureLabel(r.metric)),
        kind: kindLabel(r.metric_kind),
        value: grey(esc(fmtValue(r.value, r.unit, r.qualifier))),
        amount: r.value != null && Number.isFinite(r.value) ? r.value : null,
        period: esc(r.period ?? r.date ?? '—'),
        basis: BASIS_LABEL[r.basis ?? ''] ?? r.basis ?? '—',
        tag: r.tag ? `<abbr title="${esc(TAG_TITLE[r.tag] ?? r.tag)}">${esc(r.tag)}</abbr>` : '—',
        status: grey(esc(status)),
        confirmed: r.company_confirmed === true ? 'yes' : 'no',
        old: old ? 'yes' : 'no',
        source: linkHtml(r.source_url, r.source_name),
        last_checked: r.last_checked ?? '—',
      } satisfies LedgerTableRow;
    })
    .sort((a, b) => a.company.localeCompare(b.company) || (b.period < a.period ? -1 : b.period > a.period ? 1 : 0));
}

/** The `columns` prop for <UrlTable>. `amount` is the raw number in the currency `value` shows, so the table can be sorted by size. */
export const LEDGER_TABLE_COLUMNS = [
  { key: 'company', label: 'Company' },
  { key: 'layer', label: 'Layer' },
  { key: 'figure', label: 'Figure' },
  { key: 'value', label: 'Value as stated' },
  { key: 'amount', label: 'Amount', format: 'num' as const, align: 'right' as const },
  { key: 'period', label: 'Period' },
  { key: 'basis', label: 'Basis' },
  { key: 'tag', label: 'Tag' },
  { key: 'status', label: 'Status' },
  { key: 'source', label: 'Source' },
  { key: 'last_checked', label: 'Last checked' },
];
/** The column that names the row, for a table that marks one (screen readers read it with every cell). */
export const LEDGER_TABLE_ROW_HEADER = 'company';
export const LEDGER_TABLE_CAPTION = 'The chartable private ledger: every figure with its qualifier, basis, tag and public source; superseded and stale rounds are greyed and say so in the Status column';

const uniq = (xs: readonly string[]): string[] => [...new Set(xs)].sort((a, b) => a.localeCompare(b));

/** The `selects` prop for <UrlTable>: filters on the columns the dashboard controls cover, options read from the rows. */
export function ledgerTableSelects(rows: readonly LedgerTableRow[]) {
  return [
    { column: 'layer', label: 'Layer', options: LAYERS.map((l) => l.name as string).filter((n) => rows.some((r) => r.layer === n)) },
    { column: 'market', label: 'Market', options: uniq(rows.map((r) => r.market)) },
    { column: 'kind', label: 'Kind', options: uniq(rows.map((r) => r.kind)) },
    { column: 'basis', label: 'Basis', options: uniq(rows.map((r) => r.basis)) },
    { column: 'confirmed', label: 'Company-confirmed', options: ['yes', 'no'] },
    { column: 'old', label: 'Superseded or stale', options: ['yes', 'no'] },
  ];
}

/**
 * The same table straight over the published mart (`/data/marts/cyber/ledger_timeline.json`), with no derived file:
 * the columns are the mart's own field names, so a cell is the raw value (the link is the URL as text, the qualifier its
 * symbol, nothing is greyed). Use `ledgerTableRows` with a prebuilt file instead: the page does, and keeps this raw
 * mart only for the CSV download, whose columns are the mart's.
 */
export const LEDGER_RAW_COLUMNS = [
  { key: 'company', label: 'Company' },
  { key: 'layer', label: 'Layer' },
  { key: 'metric', label: 'Figure' },
  { key: 'qualifier', label: 'Qualifier' },
  { key: 'value', label: 'Value', format: 'num' as const, align: 'right' as const },
  { key: 'unit', label: 'Currency' },
  { key: 'period', label: 'Period' },
  { key: 'basis', label: 'Basis' },
  { key: 'tag', label: 'Tag' },
  { key: 'status', label: 'Status' },
  { key: 'source_url', label: 'Source' },
  { key: 'last_checked', label: 'Last checked' },
];

/** `selects` for <UrlTable> over the raw mart: exact matches on the mart's own values. */
export const LEDGER_RAW_SELECTS = [
  { column: 'layer', label: 'Layer', options: LAYERS.map((l) => l.key as string) },
  { column: 'market', label: 'Market', options: Object.keys(MARKET_LABEL) },
  { column: 'metric_kind', label: 'Kind', options: Object.keys(METRIC_KIND_LABEL) },
  { column: 'basis', label: 'Basis', options: Object.keys(BASIS_LABEL) },
  { column: 'status', label: 'Status', options: ['current', 'superseded'] },
  { column: 'company_confirmed', label: 'Company-confirmed', options: ['true', 'false'] },
];

/** Counts for the table caption, from the rows. */
export function ledgerTableSummary(rows: readonly LedgerTableRow[]) {
  return {
    n: rows.length,
    companies: new Set(rows.map((r) => r.company)).size,
    superseded: rows.filter((r) => r.status.includes('superseded')).length,
    stale: rows.filter((r) => r.status.includes('stale round')).length,
    confirmed: rows.filter((r) => r.confirmed === 'yes').length,
  };
}

// --- shared reading of events and intervals ----------------------------------------------------------------------
/** The events a figure may draw: chartable by the single rule, with a company and a date. */
export function chartableEvents(rows: readonly EventRow[]): EventRow[] {
  return rows.filter((r) => mayChart(r) && r.company && r.event_date && r.event_kind);
}
