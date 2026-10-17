/**
 * Valuation explorer (dashboard 1): the derivation shared by figures 1 to 3 and the linked table of every listing.
 *
 * Inputs are the `listings`, `layer_summary` and (for the private split and the regression) `private_intervals` and
 * `regression` marts of data/marts/cyber. Nothing is recomputed that the pipeline owns: a multiple is the mart's `ev_ntm`,
 * growth and FCF margin are the mart's, and a listing the pipeline refused a multiple for keeps its stated reason
 * (`missingMultipleNote`). What this file adds is only what a chart needs: a compact point row per listing, the five
 * dashboard controls, the one filter predicate those controls drive (written once as a Vega expression and once in
 * TypeScript, and checked against each other), and the figure titles for every combination of controls.
 *
 * Group `valuation`, five controls, each a top-level unbound Vega-Lite param of the same name in every spec:
 *   layer     `all` or a layer slug (select)
 *   market    `all` | `us` | `other_overseas` | `china`
 *   purity    `high_main` (whole business or main line of business) | `high` (whole business only)
 *   ring      `A` (priced) | `AB` (ring B is classification only and carries no multiple at present)
 *   coverage  `all` | `three` (at least three revenue analysts on both fiscal years, the registered minimum)
 *
 * Coverage here is the count behind the multiple. For a listing outside China it is Yahoo's revenue-estimate analysts for fiscal
 * year 0 and fiscal year +1, the minimum of the two (`n`). For an A-share it is East Money's broker count (`em_rev0_n`), because
 * East Money's revenue consensus, not Yahoo's, sets an A-share multiple (pipelines/cyber/frozen.py `_consensus`). It is not the
 * target-price analyst count (`thin` in the mart), which belongs to the targets figures.
 *
 * Thin coverage (fewer than MIN_ANALYSTS) is drawn with two channels and never with opacity alone (docs/CHART_RULES.md rule 14):
 * a dashed outline where the fill carries exposure (figures 1 and 2), a smaller mark where the fill carries a value (figure 3);
 * a fade, if any, touches the fill only and the outline stays at full opacity so it keeps 3:1 against the ground.
 */
import { VL_SCHEMA, tok } from '../theme';
import type { Listing, LayerSummary, PrivateInterval } from './types';
import layerSummaryJson from '@data/marts/cyber/layer_summary.json';
import factsJson from '@data/facts/cyber.json';

export type Spec = Record<string, unknown>;

// ---- controls ------------------------------------------------------------------------------------------------------

export const VALUATION_GROUP = 'valuation';
export const CONTROL_NAMES = ['layer', 'market', 'purity', 'ring', 'coverage'] as const;
export type ControlName = (typeof CONTROL_NAMES)[number];
export type ValuationState = Record<ControlName, string>;

/** The view a reader sees before touching a control. Every build-time title is the title of this state. */
export const VALUATION_DEFAULTS: ValuationState = { layer: 'all', market: 'all', purity: 'high_main', ring: 'A', coverage: 'all' };

/** Same shape as the `controls` prop of DashboardControls.astro. */
export type ControlOption = { value: string; label: string };
export type Control = { name: ControlName; label: string; options: ControlOption[]; default: string; type?: 'radio' | 'select' };

/** Listing markets: `china` is the A-share group (Shanghai, Shenzhen, STAR), everything else outside the US is `other_overseas`. */
export const MARKETS = ['us', 'other_overseas', 'china'] as const;
export const MARKET_LABEL: Record<string, string> = { us: 'United States', other_overseas: 'Other overseas', china: 'China A-share' };
/** Markets are told apart by shape, never by colour (SITE_SPEC rules). */
export const MARKET_SHAPE: Record<string, string> = { us: 'circle', other_overseas: 'triangle-up', china: 'diamond' };
export const EXPOSURE_LABEL: Record<string, string> = { high: 'Whole business', main: 'Main line of business', partial: 'Partial exposure' };
/** The two exposures of a ring A listing, as the legends print them. */
export const EXPOSURE_WHOLE = EXPOSURE_LABEL.high;
export const EXPOSURE_MAIN = EXPOSURE_LABEL.main;

/** Registered minimum for a consensus to count as covered (config.MIN_ANALYSTS). */
export const MIN_ANALYSTS = 3;
/**
 * Fill opacity of a mark whose multiple rests on fewer than MIN_ANALYSTS analysts, on top of its dashed outline. It fades the fill
 * only: the outline keeps full opacity (a thin hollow grey ring at 50% opacity measured 1.7:1 against the paper ground, 3:1 is the floor).
 */
export const THIN_OPACITY = 0.4;
/** Outline dash of a thin-coverage mark (figures 1 and 2, where the fill carries exposure). Figure 3 uses a dash for exposure instead. */
export const THIN_DASH: [number, number] = [2, 1.6];
/** Number words for the counts a sentence may need, so no count is typed into a string that reaches the page. */
const NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'];
export const numberWord = (n: number): string => NUMBER_WORDS[n] ?? String(n);
export const capFirst = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
/** The two coverage classes as a legend and a tooltip print them. */
export const COV_OK_LABEL = `${capFirst(numberWord(MIN_ANALYSTS))} or more analysts`;
export const COV_THIN_LABEL = `Fewer than ${numberWord(MIN_ANALYSTS)} analysts`;
/** A Vega expression over `datum.cov3` giving the class label. */
export const COV_LABEL_EXPR = `datum.cov3 ? ${JSON.stringify(COV_OK_LABEL)} : ${JSON.stringify(COV_THIN_LABEL)}`;

/** Layers in the order of their code, from layer_summary. */
export const layersByCode = (summary: LayerSummary[]): LayerSummary[] =>
  [...summary].filter((s) => s.layer != null).sort((a, b) => (a.code ?? 0) - (b.code ?? 0));

/** `code. name` as the layer control and the panel headers print it. */
export const layerLabel = (s: Pick<LayerSummary, 'code' | 'name' | 'layer'>): string => `${s.code ?? '?'}. ${s.name ?? s.layer ?? ''}`;

/** The name up to its first comma, for a title where the full layer name would not fit. */
export const layerShort = (name: string | null | undefined): string => (name ?? '').split(',')[0].trim();

/** The `controls` prop for `<DashboardControls group="valuation">`; layer options come from layer_summary. */
export function valuationControls(summary: LayerSummary[], defaults: ValuationState = VALUATION_DEFAULTS): Control[] {
  return [
    {
      name: 'layer', label: 'Layer', default: defaults.layer, type: 'select',
      options: [{ value: 'all', label: 'All layers' }, ...layersByCode(summary).map((s) => ({ value: s.layer as string, label: layerLabel(s) }))],
    },
    {
      name: 'market', label: 'Market', default: defaults.market, type: 'radio',
      options: [{ value: 'all', label: 'All markets' }, ...MARKETS.map((m) => ({ value: m, label: MARKET_LABEL[m] }))],
    },
    {
      name: 'purity', label: 'Exposure', default: defaults.purity, type: 'radio',
      options: [{ value: 'high_main', label: 'Whole or main business' }, { value: 'high', label: 'Whole business only' }],
    },
    {
      name: 'ring', label: 'Ring', default: defaults.ring, type: 'radio',
      options: [{ value: 'A', label: 'Ring A' }, { value: 'AB', label: 'Rings A and B' }],
    },
    {
      name: 'coverage', label: 'Coverage', default: defaults.coverage, type: 'radio',
      options: [{ value: 'all', label: 'Any coverage' }, { value: 'three', label: 'Three or more analysts' }],
    },
  ];
}

/**
 * One sentence for the page to print beside the ring control: while no ring B listing carries a multiple, choosing "Rings A and B" leaves
 * figures 1 to 3 unchanged (ring B is classification only), and a reader who sees nothing move should be told why. Empty when ring B is priced.
 */
export function ringBNote(listings: Pick<Listing, 'ring' | 'ev_ntm'>[]): string {
  const b = listings.filter((l) => l.ring === 'B');
  if (!b.length || b.some((l) => l.ev_ntm != null && l.ev_ntm > 0)) return '';
  return `Ring B (${plural(b.length, 'listing')}) has no multiple, so choosing rings A and B leaves the figures as they are; the table of listings shows the difference.`;
}

/** The controls of the dashboard as built from the published layer_summary mart. */
export const VALUATION_CONTROLS: Control[] = valuationControls(layerSummaryJson as unknown as LayerSummary[]);

/** Unbound top-level params every spec of the group declares (the dashboard writes them with `view.signal`). */
export const valuationParams = (defaults: ValuationState = VALUATION_DEFAULTS) =>
  CONTROL_NAMES.map((name) => ({ name, value: defaults[name] }));

// ---- the point row -------------------------------------------------------------------------------------------------

/**
 * One listing as a chart sees it. Figures 2 and 3 embed the same array, so the page stores it once. Field names are short
 * because the array is shipped to the browser.
 */
export type ValuationPoint = {
  ticker: string;
  name: string;
  layer: string;
  ring: string;
  market: string;
  purity: string;
  /** Enterprise value over next-twelve-month revenue, the mart's number; null where the pipeline refused one. */
  ev_ntm: number | null;
  /** Next-twelve-month revenue over trailing-twelve-month revenue, minus one (fraction). */
  growth: number | null;
  /** Trailing free cash flow over trailing revenue (fraction). */
  fcf: number | null;
  /** 100 x (growth + FCF margin), points. */
  r40: number | null;
  /**
   * Enterprise value in US dollars: the mart's `enterprise_value_usd`, the quoted value of a dollar listing or that value
   * converted at the FRED rate of its currency (`facts.coverage.fx_as_of`); null for a listing whose currency has no rate on file.
   */
  ev_usd: number | null;
  /** Quote currency. */
  ccy: string;
  /** Analysts behind the multiple: the smaller of fiscal year 0 and fiscal year +1 revenue analysts, or East Money's brokers for an A-share. */
  n: number | null;
  /** At least MIN_ANALYSTS analysts behind the multiple. */
  cov3: boolean;
  /** Listing status as a label: `Listed`, `Acquisition pending` (the price follows the offer) and so on. */
  status: string;
  /** Deterministic jitter in [-1, 1] from the ticker, so a strip plot never moves between renders. */
  jit: number;
};

/** FNV-1a on the ticker, mapped to [-1, 1]. */
export function hashJitter(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return Math.round(((h / 0xffffffff) * 2 - 1) * 1000) / 1000;
}

export const STATUS_LABEL: Record<string, string> = { listed: 'Listed', delisted: 'Delisted', acquired: 'Acquired', pending_acquisition: 'Acquisition pending' };
/** The `status` label of a listing whose price follows a pending offer: its multiple reflects the deal, not the market. */
export const STATUS_PENDING_LABEL = STATUS_LABEL.pending_acquisition;

const r6 = (x: number | null | undefined): number | null => (x == null || !Number.isFinite(x) ? null : Math.round(x * 1e6) / 1e6);
const num = (x: number | null | undefined): number | null => (x == null || !Number.isFinite(x) ? null : x);

/**
 * Analysts behind the multiple. An A-share (`market_group` china) is counted by East Money's brokers (`em_rev0_n`), the consensus
 * that sets its multiple; null when East Money published none. Every other listing is the smaller of Yahoo's two fiscal-year revenue
 * counts, null when neither is published.
 */
export const analystsOf = (l: Pick<Listing, 'rev_fy0_n' | 'rev_fy1_n'> & Partial<Pick<Listing, 'market_group' | 'em_rev0_n'>>): number | null => {
  if (l.market_group === 'china') return l.em_rev0_n ?? null;
  return l.rev_fy0_n == null && l.rev_fy1_n == null ? null : Math.min(l.rev_fy0_n ?? 0, l.rev_fy1_n ?? 0);
};

/**
 * Enterprise value in dollars: the mart's `enterprise_value_usd` (the pipeline converts at the FRED rate of the listing's currency,
 * `facts.coverage.fx_as_of`), otherwise the quoted enterprise value of a listing quoted in US dollars, otherwise null. No exchange
 * rate is applied in this file.
 */
export function enterpriseValueUsd(l: Listing): number | null {
  const published = (l as unknown as Record<string, unknown>).enterprise_value_usd;
  if (typeof published === 'number' && published > 0) return published;
  return l.currency === 'USD' && l.enterprise_value != null && l.enterprise_value > 0 ? l.enterprise_value : null;
}

/**
 * Every listing with at least one of a multiple, growth, FCF margin or Rule-of-40 score, as a point row; ordered by ticker.
 * A non-positive multiple is never a point value (the pipeline already refuses one; this is the second lock).
 */
export function valuationPoints(listings: Listing[]): ValuationPoint[] {
  const out: ValuationPoint[] = [];
  for (const l of listings) {
    if (!l.ticker) continue;
    const ev = num(l.ev_ntm) != null && (l.ev_ntm as number) > 0 ? r6(l.ev_ntm) : null;
    const growth = r6(l.growth);
    const fcf = r6(l.fcf_margin);
    const r40 = r6(l.rule_of_40);
    if (ev == null && growth == null && fcf == null && r40 == null) continue;
    const n = analystsOf(l);
    out.push({
      ticker: l.ticker,
      name: l.name ?? l.ticker,
      layer: l.layer ?? 'other',
      ring: l.ring ?? '',
      market: l.market_group ?? 'other_overseas',
      purity: l.purity ?? 'partial',
      ev_ntm: ev,
      growth,
      fcf,
      r40,
      ev_usd: enterpriseValueUsd(l),
      ccy: l.currency ?? '',
      n,
      cov3: n != null && n >= MIN_ANALYSTS,
      status: STATUS_LABEL[l.status ?? 'listed'] ?? String(l.status),
      jit: hashJitter(l.ticker),
    });
  }
  return out.sort((a, b) => a.ticker.localeCompare(b.ticker));
}

/** The day of the newest price in the listings mart (`YYYY-MM-DD`), for a figure's as-of stamp; null when nothing is priced. */
export const snapshotDate = (listings: Pick<Listing, 'price_ts'>[]): string | null =>
  listings.map((l) => l.price_ts?.slice(0, 10) ?? '').filter(Boolean).sort().pop() ?? null;

// ---- the filter, once in TypeScript and once as a Vega expression ---------------------------------------------------

export type Filterable = { layer: string; market: string; purity: string; ring: string; cov3: boolean };

/** True when a row is in the view the state describes. Mirrors FILTER_EXPR exactly (the harness checks that on every combination). */
export function passes(p: Filterable, s: ValuationState): boolean {
  return (s.layer === 'all' || p.layer === s.layer)
    && (s.market === 'all' || p.market === s.market)
    && (s.purity === 'high_main' ? p.purity === 'high' || p.purity === 'main' : p.purity === 'high')
    && (s.ring === 'AB' ? p.ring !== 'F' : p.ring === 'A')
    && (s.coverage === 'all' || p.cov3);
}

export const viewOf = <T extends Filterable>(rows: T[], s: ValuationState = VALUATION_DEFAULTS): T[] => rows.filter((p) => passes(p, s));

/** The layer part of the predicate alone (a panel of figure 1 is hidden as a whole when its layer is not selected). */
export const LAYER_EXPR = "(layer == 'all' || datum.layer == layer)";
/** Everything but the layer. */
export const REST_EXPR = "(market == 'all' || datum.market == market)"
  + " && (purity == 'high_main' ? (datum.purity == 'high' || datum.purity == 'main') : datum.purity == 'high')"
  + " && (ring == 'AB' ? datum.ring != 'F' : datum.ring == 'A')"
  + " && (coverage == 'all' || datum.cov3)";
/** The full predicate, in the five params' names; a Vega expression over `datum`. */
export const FILTER_EXPR = `${LAYER_EXPR} && ${REST_EXPR}`;

// ---- small numeric helpers the three charts share ---------------------------------------------------------------------

/** Linear-interpolation quantile of an ascending array (the pipeline's quartiles are the same type 7). */
export function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const h = (sorted.length - 1) * q;
  const lo = Math.floor(h);
  const hi = Math.ceil(h);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (h - lo);
}
export const median = (xs: number[]): number | null => (xs.length ? quantile([...xs].sort((a, b) => a - b), 0.5) : null);
export const mean = (xs: number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;
export const floorTo = (x: number, step: number): number => Math.floor(x / step + 1e-9) * step;
export const ceilTo = (x: number, step: number): number => Math.ceil(x / step - 1e-9) * step;
const clean = (x: number): number => Math.round(x * 1e6) / 1e6;

/** Axis ranges of figures 2 and 3 end at these quantiles, so a few extremes do not squeeze everything else; those beyond are named in the subtitle. */
export const GROWTH_AXIS_HI_Q = 0.92;
export const MARGIN_AXIS_LO_Q = 0.1;

/** An axis range that is not stretched by a few extremes: from the `loQ` to the `hiQ` quantile, widened to whole steps and to include 0. */
export function clipDomain(values: number[], step: number, loQ = 0, hiQ = 0.95): [number, number] {
  const s = [...values].sort((a, b) => a - b);
  if (!s.length) return [0, step];
  const lo = Math.min(quantile(s, loQ), 0);
  const hi = Math.max(quantile(s, hiQ), 0);
  return [clean(floorTo(lo, step)), clean(Math.max(ceilTo(hi, step), floorTo(lo, step) + step))];
}

/** Nice log-axis ticks (1, 3, 10, ...) that fall inside a domain. */
export function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)) - 1; e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 3]) {
      const v = clean(m * 10 ** e);
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  return out;
}

/** A multiple's number: `4.7`, `29.8`, `120`. */
export const fmtMult = (v: number): string => (v >= 100 ? String(Math.round(v)) : v.toFixed(1));
/** A multiple as text: `4.7x`, `29.8x`, `120x`. */
export const fmtX = (v: number): string => `${fmtMult(v)}x`;
/** A multiple in prose: `4.7 times`. */
export const fmtTimes = (v: number): string => `${fmtMult(v)} times`;
/** A fraction as a signed percent with a true minus sign: `+12%`, `−54%`. */
export const fmtSigned = (frac: number): string => `${frac < 0 ? '−' : '+'}${Math.abs(Math.round(frac * 100))}%`;
export const fmtPct0 = (frac: number): string => `${frac < 0 ? '−' : ''}${Math.abs(Math.round(frac * 100))}%`;
export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/**
 * A company's name as a short axis label: parenthetical aliases dropped (`T Stamp (Trust Stamp)` is `T Stamp`) and a trailing corporate word
 * (`Inc`, `Ltd`, `Holdings`, `Technologies` ...) cut; the full name stays in the tooltip.
 */
export function shortName(name: string): string {
  const base = name.replace(/\s*\([^)]*\)/g, '').trim();
  const tail = /[\s,]+(inc\.?|corp\.?|corporation|ltd\.?|limited|plc|holdings?|technolog(?:y|ies)|software|systems|group|co\.?|s\.?a\.?|n\.?v\.?|ag|se)$/i;
  let cut = base;
  for (let i = 0; i < 4 && tail.test(cut); i++) cut = cut.replace(tail, '').trim();
  return cut || base || name;
}

/** Greedy word wrap to at most `width` characters a line. */
export function wrapWords(text: string, width: number): string[] {
  const lines: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) { lines.push(line); line = word; }
    else line = line ? `${line} ${word}` : word;
  }
  if (line) lines.push(line);
  return lines.length ? lines : [''];
}

/** A Vega expression turning an enterprise value into the legend label `$1B`. */
export const USD_LABEL_EXPR = "'$' + replace(format(datum.value, '~s'), 'G', 'B')";
/** The same in a tooltip, over `datum.ev_usd`: `$229B`, never d3's `G`; a listing with no exchange rate says so. */
export const USD_TEXT_EXPR = "datum.ev_usd == null ? 'no exchange rate on file' : '$' + replace(format(datum.ev_usd, '.3~s'), 'G', 'B')";

// ---- facts the figure notes read ---------------------------------------------------------------------------------------

type FactsSlice = {
  scoreboard?: { id: string; already_seen?: string | null }[];
  coverage?: { fx_as_of?: string | null };
};
const facts = factsJson as unknown as FactsSlice;

/** The day of the exchange rates behind the converted enterprise values (`facts.coverage.fx_as_of`), null when facts carry none. */
export const fxAsOf = (): string | null => facts.coverage?.fx_as_of ?? null;

/**
 * The reading of a registered item on the baseline snapshot (`holds`, `falsified` or `undecidable`), read from the "already seen" text of the
 * item in `facts.scoreboard`; null when that text has none. The page's own helper (page.ts `baselineOf`) reads the same text.
 */
export function baselineReading(id: string): string | null {
  const text = facts.scoreboard?.find((q) => q.id === id)?.already_seen ?? '';
  const m = /\breads (holds|falsified|undecidable)\b/i.exec(text);
  return m ? m[1].toLowerCase() : null;
}

/** `2026-10-09` as `9 Oct 2026`. */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

/** `A`, `A and B`, `A, B and C`. */
export const andList = (xs: string[]): string => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

// ---- a key legend written by hand --------------------------------------------------------------------------------------

/** One row of a key legend: the symbol is drawn from these properties, whatever the dots of the figure are encoded by. */
export type KeyEntry = {
  label: string;
  /** A Vega symbol name (`circle`, `triangle-up`, `diamond`, `stroke` for a horizontal line) or an SVG path in the unit square. Default `circle`. */
  shape?: string;
  /** Default transparent. */
  fill?: string;
  fillOpacity?: number;
  /** Default `ink-3`. */
  stroke?: string;
  strokeWidth?: number;
  dash?: number[];
  /** Symbol area in square pixels. Default 70. */
  size?: number;
};

/** The scale channels a key layer owns: a layered spec that carries one declares these independent so they do not merge with its marks'. */
export const KEY_SCALES = {
  shape: 'independent', fill: 'independent', stroke: 'independent', strokeDash: 'independent', size: 'independent', fillOpacity: 'independent', strokeWidth: 'independent',
} as const;

/**
 * A layer that draws no mark and only produces one legend whose rows are `entries`: the legend of a Vega-Lite spec lists the values of a
 * scale, so each channel (shape, fill, stroke, dash, size, fill opacity, stroke width) is a scale over the row labels whose range is the
 * property of each row. This is how a figure shows, in one block, marks that are encoded by different fields (market by shape, exposure by
 * fill, thin coverage by a dashed outline, a ring, a line) without a legend per field. The legend flows into one, two or three columns
 * by the width of the chart (an expression over `width`), so it never runs past the plot on a phone. The layer's spec must declare
 * `resolve: { scale: KEY_SCALES }`.
 */
export function keyLayer(entries: KeyEntry[], opts: { orient?: 'top' | 'bottom' } = {}): Spec {
  const domain = entries.map((e) => e.label);
  const of = <T,>(f: (e: KeyEntry) => T): T[] => entries.map(f);
  const n = entries.length;
  // columns are as wide as the longest row (a `|` break counts as a space): as many as fit the plot's width, one on a phone
  const colWidth = Math.round(Math.max(...domain.map((l) => l.length)) * 6.6 + 40);
  const columns = `max(1, min(${Math.min(3, n)}, floor(width / ${colWidth})))`;
  return {
    data: { values: [{ key: domain[0] }] },
    transform: [{ filter: 'false' }],
    mark: { type: 'point', filled: true, aria: false },
    encoding: {
      shape: {
        field: 'key', type: 'nominal', scale: { domain, range: of((e) => e.shape ?? 'circle') },
        legend: {
          title: null, orient: opts.orient ?? 'top', direction: 'horizontal', columns: { expr: columns },
          labelFontSize: 11, labelLimit: 0, columnPadding: 16, rowPadding: 4,
          // a label may carry a `|` where it can break: in a single column (a phone) the row wraps there, elsewhere it reads as a space
          labelExpr: `${columns} < 2 ? split(datum.label, '|') : replace(datum.label, '|', ' ')`,
        },
      },
      fill: { field: 'key', type: 'nominal', scale: { domain, range: of((e) => e.fill ?? 'transparent') } },
      stroke: { field: 'key', type: 'nominal', scale: { domain, range: of((e) => e.stroke ?? tok('ink-3')) } },
      strokeDash: { field: 'key', type: 'nominal', scale: { domain, range: of((e) => e.dash ?? [1, 0]) } },
      size: { field: 'key', type: 'ordinal', scale: { domain, range: of((e) => e.size ?? 70) } },
      fillOpacity: { field: 'key', type: 'ordinal', scale: { domain, range: of((e) => e.fillOpacity ?? 1) } },
      strokeWidth: { field: 'key', type: 'ordinal', scale: { domain, range: of((e) => e.strokeWidth ?? 1.6) } },
    },
  };
}

// ---- listed without a multiple ---------------------------------------------------------------------------------------

/** The pipeline's reason codes in words. A code this file does not know is printed as its own words. */
export const REASON_LABEL: Record<string, string> = {
  no_revenue_consensus: 'no revenue consensus',
  currency_mismatch: 'currency mismatch',
  no_enterprise_value: 'no positive enterprise value',
  not_priced: 'not priced',
};
export const reasonLabel = (code: string): string => REASON_LABEL[code] ?? code.replace(/_/g, ' ');

export type MissingGroup = { reason: string; label: string; n: number; tickers: string[] };
export type MissingMultiples = { n: number; groups: MissingGroup[] };

/**
 * Ring A listings (the priced ring) that have no multiple, grouped by the pipeline's stated reason. A multiple of zero or below
 * counts as missing with the reason `non_positive_multiple`; a row with none recorded is `no_reason_recorded`, never dropped silently.
 */
export function missingMultiples(listings: Listing[], rings: string[] = ['A']): MissingMultiples {
  const by = new Map<string, string[]>();
  for (const l of listings) {
    if (!l.ring || !rings.includes(l.ring)) continue;
    if (l.ev_ntm != null && l.ev_ntm > 0) continue;
    const reason = l.ev_ntm != null ? 'non_positive_multiple' : l.ev_ntm_reason ?? 'no_reason_recorded';
    by.set(reason, [...(by.get(reason) ?? []), l.ticker ?? '?']);
  }
  const groups = [...by].map(([reason, tickers]) => ({ reason, label: reasonLabel(reason), n: tickers.length, tickers: tickers.sort() }))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  return { n: groups.reduce((a, g) => a + g.n, 0), groups };
}

/** `7 listed without a multiple: currency mismatch 3, no revenue consensus 3, no positive enterprise value 1`; empty when none. */
export function missingMultipleNote(m: MissingMultiples): string {
  return m.n ? `${m.n} listed without a multiple: ${m.groups.map((g) => `${g.label} ${g.n}`).join(', ')}` : '';
}

// ---- the private split of figure 1 -----------------------------------------------------------------------------------

export type PrivateSplit = Record<string, { overseas: number; china: number }>;

/** Distinct private companies per layer, split by `market` (`china` or anything else: overseas). */
export function privateSplit(rows: Pick<PrivateInterval, 'company' | 'layer' | 'market'>[]): PrivateSplit {
  const seen = new Set<string>();
  const out: PrivateSplit = {};
  for (const r of rows) {
    if (!r.layer || !r.company) continue;
    const key = `${r.layer}|${r.company}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const e = (out[r.layer] ??= { overseas: 0, china: 0 });
    if (r.market === 'china') e.china += 1; else e.overseas += 1;
  }
  return out;
}

// ---- titles for every combination ---------------------------------------------------------------------------------------

/**
 * The controls that can change what a figure shows, given its rows: a control whose every option leaves every row in
 * place is left out of the title keys, so the title map stays as small as the data allows (ring, today, since ring B
 * carries no multiple).
 */
export function activeControls(points: Filterable[]): ControlName[] {
  const layers = new Set(points.map((p) => p.layer));
  const markets = new Set(points.map((p) => p.market));
  const purities = new Set(points.map((p) => p.purity));
  const active: ControlName[] = [];
  if (layers.size > 1) active.push('layer');
  if (markets.size > 1) active.push('market');
  if (purities.has('main') || purities.has('partial')) active.push('purity');
  if (points.some((p) => p.ring !== 'A')) active.push('ring');
  if (points.some((p) => !p.cov3)) active.push('coverage');
  return active;
}

/**
 * `titles` and `titleKeys` for `<VegaChart group="valuation" titles={...} titleKeys={...}>`: the figure's title for every
 * combination of the controls that matter, keyed by the control values joined with `|` in control order, plus `default`.
 * `titleOf(state)` receives a full state (inactive controls at their default).
 */
export function valuationTitleMap(
  points: Filterable[],
  titleOf: (state: ValuationState) => string,
  controls: Control[] = VALUATION_CONTROLS,
): { keys: ControlName[]; map: Record<string, string> } {
  const keys = CONTROL_NAMES.filter((n) => activeControls(points).includes(n));
  const optionsOf = (n: ControlName) => (controls.find((c) => c.name === n)?.options ?? []).map((o) => o.value);
  const defaults = Object.fromEntries(controls.map((c) => [c.name, c.default])) as ValuationState;
  let combos: ValuationState[] = [{ ...defaults }];
  for (const k of keys) combos = combos.flatMap((c) => optionsOf(k).map((v) => ({ ...c, [k]: v })));
  const map: Record<string, string> = {};
  for (const c of combos) map[keys.map((k) => c[k]).join('|')] = titleOf(c);
  map.default = titleOf(defaults);
  return { keys, map };
}

// ---- the table of every listing ---------------------------------------------------------------------------------------

/**
 * Filter attributes of the linked table. `DataTable` writes `data-<key>` for every `filterKeys` entry, and these keys are
 * `<control>-any`: a space-separated list of every value of that control for which the row is in view. The row is hidden
 * when the control's current value is not in its list (an exact-equality `data-<control>` cannot say "all" and "us" at once).
 * DashboardControls.astro must read `data-<control>-any` for this to filter; until it does, these attributes are inert and the
 * table simply shows every row. A delisted or acquired listing (ring F) is in neither ring A nor ring B, so the ring control
 * never hides it; the other four controls do.
 */
export const LISTING_FILTER_KEYS = CONTROL_NAMES.map((n) => `${n}-any`);

export const LISTING_TABLE_COLUMNS = [
  { key: 'name', label: 'Company' },
  { key: 'ticker', label: 'Ticker' },
  { key: 'layer_name', label: 'Layer' },
  { key: 'market_label', label: 'Market' },
  { key: 'ring', label: 'Ring' },
  { key: 'exposure', label: 'Exposure' },
  { key: 'ev_ntm', label: 'EV / NTM revenue (x)', format: 'fixed1' as const },
  { key: 'ev_note', label: 'No multiple because' },
  { key: 'growth', label: 'Expected growth', format: 'pct' as const },
  { key: 'fcf_margin', label: 'FCF margin', format: 'pct' as const },
  { key: 'rule_of_40', label: 'Rule of 40', format: 'fixed1' as const },
  { key: 'analysts', label: 'Analysts behind the multiple', format: 'num' as const },
  { key: 'status', label: 'Status' },
];

/** A row of the table: display fields plus the `<control>-any` lists. */
export type ListingTableRow = Record<string, string | number | null>;

/** One row per listing of the mart (every ring), ordered by layer code, then ring, then ticker. */
export function listingTableRows(listings: Listing[], summary: LayerSummary[]): ListingTableRow[] {
  const layers = new Map(layersByCode(summary).map((s) => [s.layer as string, s]));
  const rank = (l: Listing) => layers.get(l.layer ?? '')?.code ?? 99;
  return [...listings].filter((l) => l.ticker).sort((a, b) => rank(a) - rank(b) || (a.ring ?? '').localeCompare(b.ring ?? '') || (a.ticker as string).localeCompare(b.ticker as string)).map((l) => {
    const n = analystsOf(l);
    const f: Filterable = { layer: l.layer ?? 'other', market: l.market_group ?? 'other_overseas', purity: l.purity ?? 'partial', ring: l.ring ?? '', cov3: n != null && n >= MIN_ANALYSTS };
    const any = {
      'layer-any': `all ${f.layer}`,
      'market-any': `all ${f.market}`,
      'purity-any': [f.purity === 'high' || f.purity === 'main' ? 'high_main' : '', f.purity === 'high' ? 'high' : ''].filter(Boolean).join(' '),
      // a delisted or acquired listing (ring F) is in neither ring A nor ring B; it stays in the table whatever the ring control says
      'ring-any': f.ring === 'A' || f.ring === 'F' ? 'A AB' : 'AB',
      'coverage-any': f.cov3 ? 'all three' : 'all',
    };
    const hasMultiple = l.ev_ntm != null && l.ev_ntm > 0;
    return {
      name: l.name ?? l.ticker,
      ticker: l.ticker,
      layer_name: layers.get(l.layer ?? '')?.name ?? l.layer,
      market_label: MARKET_LABEL[f.market] ?? f.market,
      ring: l.ring,
      exposure: EXPOSURE_LABEL[f.purity] ?? f.purity,
      ev_ntm: hasMultiple ? (l.ev_ntm as number) : null,
      ev_note: hasMultiple ? '' : l.ring === 'A' ? (l.ev_ntm_reason ? reasonLabel(l.ev_ntm_reason) : 'no reason recorded') : l.ring === 'B' ? 'ring B is not priced' : '',
      growth: l.growth,
      fcf_margin: l.fcf_margin,
      rule_of_40: l.rule_of_40,
      analysts: n,
      status: STATUS_LABEL[l.status ?? ''] ?? l.status,
      ...any,
    };
  });
}

// ---- empty state ------------------------------------------------------------------------------------------------------

/**
 * A text layer that prints `message` when no row of the layer's dataset satisfies `predicate` (a Vega expression over `datum` and
 * the five params): the controls can leave a figure with nothing to draw, and then it says why in one sentence instead of showing
 * an empty frame. An aggregate over no rows emits no row, so the count is a `joinaggregate` over every row and one row is picked
 * with `row_number`. The layer's dataset must have at least one row; a figure with none at all is `emptySpec`.
 */
export function emptyViewLayer(data: unknown[], predicate: string, message: string, y = 22): Spec {
  return {
    data: { values: data },
    transform: [
      { calculate: `(${predicate}) ? 1 : 0`, as: 'in_view' },
      { joinaggregate: [{ op: 'sum', field: 'in_view', as: 'n_in_view' }] },
      { window: [{ op: 'row_number', as: 'rn_in_view' }] },
      { filter: 'datum.rn_in_view == 1 && datum.n_in_view == 0' },
    ],
    mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 12, color: tok('ink-2'), limit: { expr: 'width - 16' } },
    encoding: { x: { value: 8 }, y: { value: y }, text: { value: message } },
  };
}

/** A spec that says one sentence instead of drawing an empty chart. */
export function emptySpec(message: string, height = 80): Spec {
  return {
    $schema: VL_SCHEMA,
    height,
    data: { values: [{ message }] },
    mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 13, color: tok('ink-2'), limit: { expr: 'width - 8' } },
    encoding: { x: { value: 4 }, y: { value: height / 2 }, text: { field: 'message', type: 'nominal' } },
  };
}
