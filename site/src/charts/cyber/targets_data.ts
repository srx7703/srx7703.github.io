/**
 * Targets and ratings (dashboard 2): the data layer shared by figure 4 (target dumbbell), figure 5 (ratings bars) and the
 * linked tables.
 *
 * Sources, all from `data/marts/cyber/` and typed in `./types`:
 *   `targets`          ring A listings outside China with a Yahoo target price (the mart is the population of figure 4);
 *   `listings`         the A-share ring A listings, whose Yahoo targets sit on their own small panel, and the ring A
 *                      universe that lets a missing row be counted with its reason;
 *   `recommendations`  Yahoo five-bucket counts and East Money six-month counts per listing; the two vocabularies are
 *                      read side by side and never merged, added or averaged;
 *   `china_panel`      read by `china_panel.ts` only.
 *
 * Only a ratio of two prices in the same currency is ever drawn (implied upside), so currencies and pence quotes need no
 * conversion. A ratio whose denominator is missing or not positive is not drawn; it is counted with the reason.
 * Thin analyst coverage (fewer than the registered threshold, three) is a flag carried on every row.
 *
 * Dashboard group `targets`, four controls; each is also an unbound top-level param of the same name in the specs that
 * use it (`layer`, `market`, `coverage` in the dumbbell; all four in the ratings bars):
 *   layer     `all` or one of the twelve layers of the registered tree;
 *   market    `all`, `us` or `other_overseas` (A-share listings are never selected by it: they only appear in the
 *             dumbbell's own panel while `all` is selected, and in the East Money panel of the ratings);
 *   coverage  `all` or `three` (at least three analysts behind the number drawn);
 *   source    `yahoo` or `eastmoney`: which rating source the ratings figure's title and the table follow (both panels are always drawn in full).
 */
import type { ChinaRow, Listing, RecommendationRow, TargetRow } from './types';
import { EXPOSURE_LABEL } from './valuation_data';
import listingsJson from '@data/marts/cyber/listings.json';

export const TARGETS_GROUP = 'targets';

/**
 * Exposure of a listing as the legends print it (one vocabulary with the valuation figures, `valuation_data.EXPOSURE_LABEL`):
 * a pure play is `high` (security is the whole business), `main` its main line of business, `partial` only part of it.
 * Figures 4 and 8 draw `high` filled and every other exposure hollow (CHART_RULES 14).
 */
/**
 * The listings mart as the build reads it (never served: the page imports it from `data/`). The China figure and the China table
 * read each listing's exposure from it when a caller does not pass the mart, so a purity is never silently missing.
 */
export const BUILD_LISTINGS = listingsJson as unknown as Listing[];
export const exposureLabel = (purity: string | null | undefined): string => (purity ? EXPOSURE_LABEL[purity] ?? purity : 'Exposure not stated');
/** Legend labels of the two fills: whole business, and every smaller exposure together. */
export const FILL_WHOLE = EXPOSURE_LABEL.high;
export const FILL_PART = 'Main line or part of the business';
export const isWholeBusiness = (purity: string | null | undefined): boolean => purity === 'high';

// ---- vocabulary ----------------------------------------------------------------------------------------------------

/** The twelve layers of the registered tree (`pipelines/cyber/config.py` LAYERS), in tree order. */
export const LAYER_LABEL: Record<string, string> = {
  network_edge: 'Network and edge security',
  endpoint: 'Endpoint and XDR',
  identity: 'Identity and access',
  cloud_appsec: 'Cloud and application security',
  data_privacy: 'Data security and privacy',
  secops: 'Security operations',
  email_human: 'Email, collaboration and human risk',
  ot_iot: 'OT, IoT and ICS',
  grc_insurance: 'GRC, compliance and cyber insurance',
  services: 'Services, MSSP, consulting and distribution',
  ai_security: 'AI security and AI-native security operations',
  infra_hw: 'Infrastructure, hardware and post-quantum',
};
export const LAYER_ORDER = Object.keys(LAYER_LABEL);
export const layerLabel = (layer: string | null | undefined): string =>
  (layer ? LAYER_LABEL[layer] ?? layer : 'Unplaced');

export type MarketGroup = 'us' | 'other_overseas' | 'china';
/** Listing market as the legends and controls print it (same words as the valuation dashboard). */
export const MARKET_LABEL: Record<MarketGroup, string> = { us: 'United States', other_overseas: 'Other overseas', china: 'China A-share' };
/** The same markets inside a sentence. */
export const MARKET_SHORT: Record<MarketGroup, string> = { us: 'US', other_overseas: 'other overseas', china: 'A-share' };
/** Markets are told apart by shape, never by colour (SITE_SPEC rules). Vega-Lite shape names, as in the valuation figures. */
export const MARKET_SHAPE: Record<MarketGroup, string> = { us: 'circle', other_overseas: 'triangle-up', china: 'diamond' };
export const MARKET_ORDER: MarketGroup[] = ['us', 'other_overseas', 'china'];
const isMarket = (v: string | null | undefined): v is MarketGroup => v === 'us' || v === 'other_overseas' || v === 'china';

/** Registered threshold (`config.THIN_COVERAGE_BELOW`): fewer analysts than this and a listing is thin. Only a fallback; the mart's own flag wins. */
export const THIN_BELOW = 3;

const WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
export const numWord = (n: number): string => (Number.isInteger(n) && n >= 0 && n < WORDS.length ? WORDS[n] : String(n));
export const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/** Plural-aware count: `count(1, 'listing')` = `1 listing`. */
export const count = (n: number, one: string, many = `${one}s`): string => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

/** Signed percent for an upside ratio: `0.193` = `+19%`; large values keep thousands separators. */
export const fmtUpside = (x: number | null | undefined): string => {
  if (x == null || !Number.isFinite(x)) return 'n/a';
  const p = Math.round(x * 100) || 0; // `|| 0` turns -0 into 0
  return `${p > 0 ? '+' : ''}${p.toLocaleString('en-US')}%`;
};
/** Unsigned percent of a share. */
export const fmtShare = (x: number | null | undefined, digits = 0): string =>
  (x == null || !Number.isFinite(x) ? 'n/a' : `${(x * 100).toFixed(digits)}%`);

/** Join `a, b and c` (or `a, b or c` with `word` = `or`). */
export const andList = (xs: string[], word: 'and' | 'or' = 'and'): string =>
  (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} ${word} ${xs[xs.length - 1]}`);

export const median = (xs: number[]): number | null => {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!s.length) return null;
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
/** Linear-interpolated quantile, q in [0, 1]. */
export const quantile = (xs: number[], q: number): number | null => {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!s.length) return null;
  const pos = (s.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
};

/** A short axis label: Chinese-character glosses and legal suffixes dropped, so 90px of label holds the recognisable part. */
export function shortName(name: string | null | undefined): string {
  let s = (name ?? '').replace(/\s*\([^)]*\)/g, '').trim();
  const suffix = /\s+(Technologies|Technology|Holdings?|Group|International|Systems|Software|Co\.?|Inc\.?|Ltd\.?|plc|AG|&)$/i;
  for (let i = 0; i < 4 && suffix.test(s) && s.replace(suffix, '').trim().length >= 3; i += 1) s = s.replace(suffix, '').trim();
  return s || (name ?? '');
}

/**
 * Names whose generic shortening still leaves a label wider than the 84px an axis keeps for it (about fifteen characters of
 * 10px type). Keys are the names exactly as the marts carry them; an unlisted name falls back to `shortName`.
 */
const AXIS_NAME: Record<string, string> = {
  'Palo Alto Networks': 'Palo Alto',
  'Hillstone Networks': 'Hillstone',
  'Acer Cyber Security (安碁資訊)': 'Acer Cyber',
  'Zhongfu Information': 'Zhongfu',
  'Corero Network Security': 'Corero',
  'Global Security Experts': 'Global Security',
  'CETC Cyberspace Security (ex-Westone)': 'CETC Cyberspace',
  'secunet Security Networks': 'secunet',
  'SSH Communications Security': 'SSH Comms',
  'AsiaInfo Security': 'AsiaInfo',
  'SDIC Intelligence (ex-Meiya Pico)': 'SDIC',
  'Wangsu Science & Technology': 'Wangsu',
  'TRS Information Technology': 'TRS',
};
/** The label an axis prints for a company: a short, recognisable name that fits 84px. Prose keeps using `shortName`. */
export const axisName = (name: string | null | undefined): string => AXIS_NAME[(name ?? '').trim()] ?? shortName(name);

/** Whole-number percentages of `parts` that add to exactly 100 (largest remainder), so a sentence's shares never sum to 101%. */
export function roundShares(parts: number[]): number[] {
  const total = parts.reduce((s, x) => s + x, 0);
  if (!(total > 0)) return parts.map(() => 0);
  const raw = parts.map((x) => (x / total) * 100);
  const out = raw.map((x) => Math.floor(x));
  let left = 100 - out.reduce((s, x) => s + x, 0);
  raw.map((x, i) => ({ i, r: x - Math.floor(x) })).sort((a, b) => b.r - a.r || a.i - b.i).forEach(({ i }) => { if (left > 0) { out[i] += 1; left -= 1; } });
  return out;
}

// ---- controls ------------------------------------------------------------------------------------------------------

export interface DashControl {
  name: string;
  label: string;
  options: { value: string; label: string }[];
  default: string;
  type?: 'radio' | 'select';
}

export interface TargetsState { layer: string; market: string; coverage: string; source: string }
export type ControlName = keyof TargetsState;
export const TARGETS_DEFAULTS: TargetsState = { layer: 'all', market: 'all', coverage: 'all', source: 'yahoo' };

export const SOURCE_LABEL: Record<string, string> = { yahoo: 'Yahoo', eastmoney: 'East Money' };
export const COVERAGE_LABEL: Record<string, string> = {
  all: 'Any coverage',
  three: `${cap(numWord(THIN_BELOW))} or more analysts`,
};

/** The `controls` prop for `<DashboardControls group="targets">`: same shape as the other dashboards, defaults included. */
export const TARGETS_CONTROLS: DashControl[] = [
  {
    name: 'layer', label: 'Layer', type: 'select', default: TARGETS_DEFAULTS.layer,
    options: [{ value: 'all', label: 'All layers' }, ...LAYER_ORDER.map((l, i) => ({ value: l, label: `${i + 1}. ${LAYER_LABEL[l]}` }))],
  },
  {
    name: 'market', label: 'Market (outside China only)', default: TARGETS_DEFAULTS.market,
    options: [
      { value: 'all', label: 'All markets' },
      { value: 'us', label: MARKET_LABEL.us },
      { value: 'other_overseas', label: MARKET_LABEL.other_overseas },
    ],
  },
  {
    name: 'coverage', label: 'Coverage (analysts per listing)', default: TARGETS_DEFAULTS.coverage,
    options: [{ value: 'all', label: COVERAGE_LABEL.all }, { value: 'three', label: COVERAGE_LABEL.three }],
  },
  {
    name: 'source', label: 'Ratings source (title and table)', default: TARGETS_DEFAULTS.source,
    options: [{ value: 'yahoo', label: SOURCE_LABEL.yahoo }, { value: 'eastmoney', label: SOURCE_LABEL.eastmoney }],
  },
];

export const targetsState = (s: Partial<TargetsState> = {}): TargetsState => ({ ...TARGETS_DEFAULTS, ...s });

/** Top-level Vega-Lite params for the named controls, at `state` (default view when omitted). */
export const targetsParams = (names: ControlName[], state: Partial<TargetsState> = {}) => {
  const st = targetsState(state);
  return names.map((name) => ({ name, value: st[name] }));
};

export const controlsFor = (names: ControlName[]): DashControl[] => TARGETS_CONTROLS.filter((c) => names.includes(c.name as ControlName));

/** Every combination of the named controls' options, the others at default. */
export function allStates(names: ControlName[]): TargetsState[] {
  return controlsFor(names).reduce<TargetsState[]>(
    (acc, c) => acc.flatMap((st) => c.options.map((o) => ({ ...st, [c.name]: o.value }))),
    [{ ...TARGETS_DEFAULTS }],
  );
}

/** Key a title map uses for a state: the named controls' values joined with `|`, in the order of `names`. */
export const stateKey = (st: TargetsState, names: ControlName[]): string => names.map((n) => st[n]).join('|');

/**
 * Title map for `<VegaChart group="targets" titles={map} titleKeys={names}>`: every combination of the named controls
 * -> the figure's finding for that view, plus `default`. `names` must be passed as `titleKeys` in the same order.
 */
export function titleMapFor(names: ControlName[], titleOf: (st: TargetsState) => string): Record<string, string> {
  const map: Record<string, string> = {};
  for (const st of allStates(names)) map[stateKey(st, names)] = titleOf(st);
  map.default = map[stateKey(TARGETS_DEFAULTS, names)];
  return map;
}

/**
 * The listings a view is about, as a noun phrase for a title: `5 US listings in Identity and access with at least three
 * analysts`. `where` overrides the market wording (East Money's panel is A-share whatever `market` says).
 */
export function viewPhrase(st: TargetsState, n: number, opts: { analysts?: string; where?: string } = {}): string {
  const plural = n !== 1;
  const base = opts.where
    ?? (st.market === 'us' ? 'US listing' : st.market === 'other_overseas' ? 'other overseas listing' : 'listing outside China');
  const noun = base.replace(/listing/, plural ? 'listings' : 'listing');
  const parts = [`${n.toLocaleString('en-US')} ${noun}`];
  if (st.layer !== 'all') parts.push(`in ${layerLabel(st.layer)}`);
  if (st.coverage === 'three') parts.push(`with at least ${numWord(THIN_BELOW)} ${opts.analysts ?? 'analysts'}`);
  return parts.join(' ');
}

// ---- figure 4: target points ---------------------------------------------------------------------------------------

export interface TargetPoint {
  ticker: string;
  name: string;
  /** Short, unique axis label. */
  label: string;
  layer: string;
  layer_label: string;
  purity: string | null;
  /** The exposure as a legend prints it (`exposureLabel`). */
  exposure: string;
  market_group: MarketGroup;
  market_label: string;
  panel: 'main' | 'cn';
  status: string | null;
  currency: string | null;
  /** Quoted in pence: the ratio below is unaffected, a price is not comparable with a pound price. */
  pence: boolean;
  price: number;
  target_mean: number;
  target_low: number | null;
  target_high: number | null;
  /** Implied upside ratios: target / price - 1. */
  upside: number;
  low: number | null;
  high: number | null;
  n_opinions: number | null;
  thin: boolean;
  /** Member of the registered Q4/Q5 list (the mart's `in_q4_q5_list`); null when the mart does not say (a listing read from `listings`). */
  in_q4_list: boolean | null;
  /** The mean target is more than `SUSPECT_RATIO` times the price: very likely a stale or split-affected single target, unverified. */
  suspect: boolean;
  as_of: string | null;
}

/**
 * A mean target above this many times the price is flagged as likely stale or split-affected and unverified (a price quoted
 * after a reverse split against a target set before it). The flag is a reading aid, not a correction: the row stays in the
 * figure and the table, and the tooltip and the table say it.
 */
export const SUSPECT_RATIO = 10;
export const SUSPECT_NOTE = `mean target is more than ${numWord(SUSPECT_RATIO)} times the price: likely stale or split-affected, unverified`;

export type SkipReason = 'price_not_positive' | 'no_target' | 'no_market';
export interface SkippedTarget { ticker: string; name: string; layer: string | null; market_group: MarketGroup | null; reason: SkipReason; text: string }

export const SKIP_TEXT: Record<SkipReason, string> = {
  price_not_positive: 'the price is missing or not positive, so there is no denominator',
  no_target: 'Yahoo has no target price',
  no_market: 'the market group is missing',
};

export interface TargetsData {
  points: TargetPoint[];
  skipped: SkippedTarget[];
  /** Analysts below which a listing is thin (read from the mart's own flags). */
  thinBelow: number;
  as_of: string | null;
  /** Ring A listings per layer, every market: tells a layer with no listed company from one with no target. */
  listedByLayer: Record<string, number>;
}

/**
 * Analyst count below which the mart flags a listing thin. The registered threshold wins when the flags agree with it (every
 * thin row has fewer analysts, every covered row at least that many); otherwise it is read back from the flags.
 */
export function thinBelowOf(rows: { n_opinions: number | null; thin: boolean | null }[]): number {
  const thin = rows.filter((r) => r.thin === true && r.n_opinions != null).map((r) => r.n_opinions as number);
  const covered = rows.filter((r) => r.thin === false && r.n_opinions != null).map((r) => r.n_opinions as number);
  const lo = thin.length ? Math.max(...thin) : -Infinity;
  const hi = covered.length ? Math.min(...covered) : Infinity;
  if (THIN_BELOW > lo && THIN_BELOW <= hi) return THIN_BELOW;
  if (Number.isFinite(hi)) return hi;
  return Number.isFinite(lo) ? lo + 1 : THIN_BELOW;
}

const PENCE = new Set(['GBp', 'GBX']);
const maxStr = (xs: (string | null | undefined)[]): string | null =>
  xs.reduce<string | null>((m, x) => (x && (!m || x > m) ? x : m), null);

interface TargetSource {
  ticker: string | null; name: string | null; layer: string | null; purity: string | null; market_group: string | null;
  status: string | null; currency: string | null; price: number | null; target_mean: number | null;
  target_low: number | null; target_high: number | null; n_opinions: number | null; thin: boolean | null;
  pence_quote?: boolean | null; as_of?: string | null; in_q4_q5_list?: boolean | null;
}

/**
 * Target rows -> points. A row whose price is missing or not positive has no denominator and is not drawn; a row with no
 * positive mean target has nothing to draw. Both are counted in `skipped` with the reason in words.
 */
export function buildTargetsData(targets: TargetRow[], listings: Listing[] = []): TargetsData {
  const asTarget = (t: TargetRow): TargetSource => t;
  const asListing = (l: Listing): TargetSource => ({ ...l, as_of: l.price_ts ? l.price_ts.slice(0, 10) : null });
  const ringA = listings.filter((l) => l.ring === 'A');
  const chinaA = ringA.filter((l) => l.market_group === 'china');
  const thinBelow = thinBelowOf([...targets, ...chinaA]);
  const points: TargetPoint[] = [];
  const skipped: SkippedTarget[] = [];
  const done = new Set<string>();

  const take = (r: TargetSource) => {
    if (!r.ticker || done.has(r.ticker)) return;
    done.add(r.ticker);
    const name = r.name ?? r.ticker;
    if (!isMarket(r.market_group)) {
      skipped.push({ ticker: r.ticker, name, layer: r.layer, market_group: null, reason: 'no_market', text: SKIP_TEXT.no_market });
      return;
    }
    const price = r.price;
    if (price == null || !(price > 0)) {
      skipped.push({ ticker: r.ticker, name, layer: r.layer, market_group: r.market_group, reason: 'price_not_positive', text: SKIP_TEXT.price_not_positive });
      return;
    }
    const mean = r.target_mean;
    if (mean == null || !(mean > 0)) {
      skipped.push({ ticker: r.ticker, name, layer: r.layer, market_group: r.market_group, reason: 'no_target', text: SKIP_TEXT.no_target });
      return;
    }
    const ratio = (v: number | null): number | null => (v != null && v > 0 ? v / price - 1 : null);
    points.push({
      ticker: r.ticker, name, label: axisName(name), layer: r.layer ?? 'unplaced', layer_label: layerLabel(r.layer),
      purity: r.purity, exposure: exposureLabel(r.purity), market_group: r.market_group, market_label: MARKET_LABEL[r.market_group],
      panel: r.market_group === 'china' ? 'cn' : 'main', status: r.status, currency: r.currency,
      pence: r.pence_quote === true || (r.currency != null && PENCE.has(r.currency)),
      price, target_mean: mean, target_low: r.target_low, target_high: r.target_high,
      upside: mean / price - 1, low: ratio(r.target_low), high: ratio(r.target_high),
      n_opinions: r.n_opinions, thin: r.thin ?? (r.n_opinions == null || r.n_opinions < thinBelow),
      in_q4_list: r.in_q4_q5_list ?? null, suspect: mean / price > SUSPECT_RATIO,
      as_of: r.as_of ?? null,
    });
  };

  targets.forEach((t) => take(asTarget(t)));
  chinaA.forEach((l) => take(asListing(l)));
  // Ring A listings outside China that the targets mart does not carry: counted, with the reason.
  ringA.filter((l) => l.market_group !== 'china').forEach((l) => take(asListing(l)));

  // Unique axis labels: two companies may shorten to the same name.
  const seen = new Map<string, number>();
  points.forEach((p) => seen.set(p.label, (seen.get(p.label) ?? 0) + 1));
  points.forEach((p) => { if ((seen.get(p.label) ?? 0) > 1) p.label = `${p.label} (${p.ticker})`; });

  const listedByLayer: Record<string, number> = {};
  ringA.forEach((l) => { if (l.layer) listedByLayer[l.layer] = (listedByLayer[l.layer] ?? 0) + 1; });
  points.sort((a, b) => b.upside - a.upside || a.ticker.localeCompare(b.ticker));
  return { points, skipped, thinBelow, as_of: maxStr(points.map((p) => p.as_of)), listedByLayer };
}

/** True when a point belongs to the view `st` (the same predicate the spec evaluates in Vega). */
export function pointInState(p: { layer: string; market_group: string; thin: boolean }, st: TargetsState): boolean {
  return (st.layer === 'all' || p.layer === st.layer)
    && (st.market === 'all' || p.market_group === st.market)
    && (st.coverage === 'all' || !p.thin);
}

// ---- figure 5: rating listings -------------------------------------------------------------------------------------

export type RatingSource = 'yahoo' | 'eastmoney';
export type Side = 'buy' | 'hold' | 'sell';
export interface RatingBucket { key: string; label: string; side: Side }

/** Yahoo's five buckets, best to worst; `key` is the field of the recommendations mart. */
export const YAHOO_BUCKETS: RatingBucket[] = [
  { key: 'strong_buy', label: 'strong buy', side: 'buy' },
  { key: 'buy', label: 'buy', side: 'buy' },
  { key: 'hold', label: 'hold', side: 'hold' },
  { key: 'sell', label: 'sell', side: 'sell' },
  { key: 'strong_sell', label: 'strong sell', side: 'sell' },
];
/** East Money's five labels, best to worst (buy, add, neutral, reduce, sell). */
export const EM_BUCKETS: RatingBucket[] = [
  { key: 'em_buy', label: 'buy', side: 'buy' },
  { key: 'em_add', label: 'add', side: 'buy' },
  { key: 'em_neutral', label: 'neutral', side: 'hold' },
  { key: 'em_reduce', label: 'reduce', side: 'sell' },
  { key: 'em_sell', label: 'sell', side: 'sell' },
];
export const BUCKETS: Record<RatingSource, RatingBucket[]> = { yahoo: YAHOO_BUCKETS, eastmoney: EM_BUCKETS };
/** Three colour slots only: the direction of a rating, never a merged vocabulary. */
export const SIDES: Side[] = ['buy', 'hold', 'sell'];
export const SIDE_LABEL: Record<Side, string> = { buy: 'Buy side', hold: 'Hold or neutral', sell: 'Sell side' };

export interface RatingListing {
  source: RatingSource;
  ticker: string;
  name: string;
  label: string;
  layer: string;
  layer_label: string;
  /** The listing's purity from the listings mart (null when the mart is not passed). */
  purity: string | null;
  market_group: MarketGroup;
  /** Analysts behind the counts: the sum of the printed counts. */
  n_ratings: number;
  thin: boolean;
  /** Bucket key -> printed count, or null when the source printed none (never read as zero). */
  counts: Record<string, number | null>;
  mean: number | null;
  window: string | null;
  as_of: string | null;
}

const toNum = (v: unknown): number | null => {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export interface RatingListings {
  yahoo: RatingListing[];
  eastmoney: RatingListing[];
  /** Listings the source returned a row for but with no rating behind it: no count printed, or counts of zero (not drawn, counted). */
  empty: Record<RatingSource, number>;
  /** Buckets no listing printed a count for (East Money: neutral, reduce, sell). */
  unprinted: Record<RatingSource, RatingBucket[]>;
  windows: Record<RatingSource, string | null>;
  /** Ring A listings per layer, every market (from the listings mart), to tell a layer with no listing from one with no rating. */
  listedByLayer: Record<string, number>;
  /** Analysts below which a listing is thin. */
  thinBelow: number;
  as_of: string | null;
}

/** Per-listing rating rows of the recommendations mart, one vocabulary per source. Pooled rows are ignored: they are re-derived so a filter can change them. */
export function buildRatingListings(recs: RecommendationRow[], thinBelow = THIN_BELOW, listings: Listing[] = []): RatingListings {
  const out: RatingListings = {
    yahoo: [], eastmoney: [], empty: { yahoo: 0, eastmoney: 0 }, unprinted: { yahoo: [], eastmoney: [] },
    windows: { yahoo: null, eastmoney: null }, listedByLayer: {}, thinBelow, as_of: null,
  };
  listings.filter((l) => l.ring === 'A' && l.layer).forEach((l) => { out.listedByLayer[l.layer as string] = (out.listedByLayer[l.layer as string] ?? 0) + 1; });
  const purityOf = new Map(listings.map((l) => [l.ticker, l.purity]));
  for (const r of recs) {
    if (r.scope !== 'listing' || !r.ticker) continue;
    const source: RatingSource | null = r.source === 'yahoo' ? 'yahoo' : r.source === 'eastmoney' ? 'eastmoney' : null;
    if (!source) continue;
    const market = isMarket(r.market_group) ? r.market_group : source === 'eastmoney' ? 'china' : null;
    if (!market) continue;
    const raw = r as unknown as Record<string, unknown>;
    const counts: Record<string, number | null> = {};
    BUCKETS[source].forEach((b) => { counts[b.key] = toNum(raw[b.key]); });
    out.windows[source] = out.windows[source] ?? r.window;
    if (!Object.values(counts).some((c) => c != null)) { out.empty[source] += 1; continue; }
    const n = Object.values(counts).reduce<number>((s, c) => s + (c ?? 0), 0);
    if (n === 0) { out.empty[source] += 1; continue; } // printed, but no rating behind it: nothing to draw
    const name = r.name ?? r.ticker;
    out[source].push({
      source, ticker: r.ticker, name, label: axisName(name), layer: r.layer ?? 'unplaced', layer_label: layerLabel(r.layer),
      purity: purityOf.get(r.ticker) ?? null, market_group: market, n_ratings: n, thin: n < thinBelow, counts, mean: r.recommendation_mean, window: r.window, as_of: r.as_of,
    });
  }
  for (const s of ['yahoo', 'eastmoney'] as RatingSource[]) {
    out.unprinted[s] = BUCKETS[s].filter((b) => !out[s].some((l) => l.counts[b.key] != null));
  }
  out.as_of = maxStr([...out.yahoo, ...out.eastmoney].map((l) => l.as_of));
  return out;
}

export interface RatingGroup {
  source: RatingSource;
  /** `pooled` or the market group the row is restricted to. */
  group: 'pooled' | MarketGroup;
  label: string;
  order: number;
  listings: number;
  ratings: number;
  /** Printed buckets only; a bucket nobody printed is absent, not zero. */
  counts: Record<string, number>;
}

/**
 * Pooled counts for one source at `st`: sums of the listings' printed counts. Yahoo is pooled over listings outside China
 * and, when `market` is `all`, also by market group; East Money is pooled over the A-share listings and ignores `market`.
 * `coverage` counts the rating analysts of each listing, the same count the bars show.
 */
export function ratingGroups(lists: RatingListings, source: RatingSource, st: TargetsState): RatingGroup[] {
  const rows = lists[source].filter((l) => (st.layer === 'all' || l.layer === st.layer)
    && (source === 'eastmoney' || st.market === 'all' || l.market_group === st.market)
    && (st.coverage === 'all' || !l.thin));
  const make = (group: RatingGroup['group'], label: string, order: number, subset: RatingListing[]): RatingGroup | null => {
    if (!subset.length) return null;
    const counts: Record<string, number> = {};
    BUCKETS[source].forEach((b) => {
      const printed = subset.filter((l) => l.counts[b.key] != null);
      if (printed.length) counts[b.key] = printed.reduce((s, l) => s + (l.counts[b.key] as number), 0);
    });
    return { source, group, label, order, listings: subset.length, ratings: subset.reduce((s, l) => s + l.n_ratings, 0), counts };
  };
  const groups: (RatingGroup | null)[] = [];
  if (source === 'eastmoney') {
    groups.push(make('pooled', 'All', 0, rows));
  } else if (st.market === 'all') {
    groups.push(make('pooled', 'All', 0, rows));
    (['us', 'other_overseas'] as MarketGroup[]).forEach((m, i) => groups.push(make(m, m === 'us' ? 'US' : 'Other', i + 1, rows.filter((l) => l.market_group === m))));
  } else {
    groups.push(make('pooled', st.market === 'us' ? 'US' : 'Other', 0, rows));
  }
  return groups.filter((g): g is RatingGroup => g != null);
}

/** Buy-side, hold and sell-side counts of a group: the direction of the printed ratings. */
export function bySide(g: RatingGroup): Record<Side, number> {
  const out: Record<Side, number> = { buy: 0, hold: 0, sell: 0 };
  BUCKETS[g.source].forEach((b) => { out[b.side] += g.counts[b.key] ?? 0; });
  return out;
}

// ---- reasons a view is empty ---------------------------------------------------------------------------------------

/**
 * The one sentence a figure shows instead of an empty chart. `have` says how many rows the layer, then the market, then
 * the coverage still leave; `listed` is the number of companies the listed pool has in that layer (every market).
 */
export function emptyViewReason(
  what: { noun: string; has: string }, st: TargetsState,
  have: { layer: number; market: number; coverage: number; listed: number; thin?: number },
): string {
  const inLayer = st.layer === 'all' ? '' : ` in ${layerLabel(st.layer)}`;
  const M = st.market === 'all' ? '' : `${MARKET_SHORT[st.market as MarketGroup] ?? st.market} `;
  if (have.layer === 0) {
    return have.listed === 0 && st.layer !== 'all'
      ? `${layerLabel(st.layer)} has no company in the listed pool (the priced pool), so there is no ${what.noun} to show here.`
      : `No ${M}listing${inLayer} has ${what.has}.`;
  }
  if (have.market === 0) return `No ${M}listing${inLayer} has ${what.has}.`;
  return `Every ${M}listing${inLayer} with ${what.has} has fewer than ${numWord(THIN_BELOW)} analysts behind it`
    + `${have.thin ? ` (${count(have.thin, 'listing')})` : ''}; set coverage to all listings to see them.`;
}

// ---- linked tables -------------------------------------------------------------------------------------------------

export interface TargetsTableRow {
  name: string;
  ticker: string;
  /** Filter keys (exact-match data attributes, see `tableVariants`). */
  layer: string;
  market: string;
  coverage: string;
  source: string;
  layer_label: string;
  market_label: string;
  coverage_label: string;
  source_label: string;
  analysts: number | null;
  upside: number | null;
  upside_low: number | null;
  upside_high: number | null;
  currency: string;
  /** Whole business, main line of business or partial exposure (a legend's words). */
  exposure: string;
  /** `yes` or `no`: in the registered Q4/Q5 list; `n/a` for a row that is not a target row. */
  registered: string;
  note: string;
  ratings: string;
  as_of: string;
}

const dash = '—';

/** Printed counts as a sentence in the source's own vocabulary, e.g. `strong buy 3, buy 6, hold 2`. */
export function ratingsText(l: RatingListing): string {
  const parts = BUCKETS[l.source].filter((b) => l.counts[b.key] != null).map((b) => `${b.label} ${l.counts[b.key]}`);
  return parts.length ? parts.join(', ') : 'none printed';
}

/**
 * The note a target row carries in the linked table: the pence quote, a status other than listed (a signed sale, for instance),
 * a covered listing the registered Q4 list leaves out, and a target more than `SUSPECT_RATIO` times the price.
 */
export function targetNote(p: TargetPoint): string {
  return [
    p.pence ? 'quoted in pence; the ratio is unaffected' : '',
    p.status && p.status !== 'listed' ? p.status.replace(/_/g, ' ') : '',
    p.in_q4_list === false && !p.thin ? 'not in the registered Q4 list' : '',
    p.suspect ? SUSPECT_NOTE : '',
  ].filter(Boolean).join('; ');
}

/**
 * One row per listing in the listed pool for figure 4's linked table: implied upside, whiskers and analysts count. Attributes: `layer`,
 * `market` and `coverage` (the strictest coverage view it appears in), no `source`.
 */
export function targetsTableRows(data: TargetsData): TargetsTableRow[] {
  const rows: TargetsTableRow[] = data.points.map((p) => ({
    name: p.name, ticker: p.ticker, layer: p.layer, market: p.market_group, coverage: p.thin ? 'all' : 'three', source: 'yahoo',
    layer_label: p.layer_label, market_label: p.market_label, coverage_label: p.thin ? `Thin (fewer than ${numWord(data.thinBelow)})` : COVERAGE_LABEL.three,
    source_label: SOURCE_LABEL.yahoo, analysts: p.n_opinions, upside: p.upside, upside_low: p.low, upside_high: p.high,
    currency: p.currency ?? dash,
    exposure: p.exposure, registered: p.in_q4_list == null ? 'n/a' : p.in_q4_list ? 'yes' : 'no',
    note: targetNote(p),
    ratings: dash, as_of: p.as_of ?? dash,
  }));
  data.skipped.forEach((s) => rows.push({
    name: s.name, ticker: s.ticker, layer: s.layer ?? 'unplaced', market: s.market_group ?? 'unplaced', coverage: 'all', source: 'yahoo',
    layer_label: layerLabel(s.layer), market_label: s.market_group ? MARKET_LABEL[s.market_group] : dash, coverage_label: dash,
    source_label: SOURCE_LABEL.yahoo, analysts: null, upside: null, upside_low: null, upside_high: null, currency: dash,
    exposure: dash, registered: 'n/a', note: `not drawn: ${s.text}`, ratings: dash, as_of: dash,
  }));
  return rows;
}

/**
 * One row per listing and rating source for figure 5's linked table, in each source's own vocabulary (never merged), with
 * the listing's implied upside beside it. Attributes: `layer`, `market`, `coverage` (rating analysts, the figure's own
 * count) and `source`.
 */
export function ratingsTableRows(lists: RatingListings, data: TargetsData): TargetsTableRow[] {
  const byTicker = new Map(data.points.map((p) => [p.ticker, p]));
  const rows: TargetsTableRow[] = [];
  (['yahoo', 'eastmoney'] as RatingSource[]).forEach((source) => {
    lists[source].forEach((l) => {
      const p = byTicker.get(l.ticker);
      rows.push({
        name: l.name, ticker: l.ticker, layer: l.layer, market: l.market_group, coverage: l.thin ? 'all' : 'three', source,
        layer_label: l.layer_label, market_label: MARKET_LABEL[l.market_group],
        coverage_label: l.thin ? `Thin (fewer than ${numWord(data.thinBelow)})` : COVERAGE_LABEL.three,
        source_label: SOURCE_LABEL[source], analysts: l.n_ratings, upside: p?.upside ?? null, upside_low: p?.low ?? null, upside_high: p?.high ?? null,
        currency: p?.currency ?? dash, exposure: p?.exposure ?? (l.purity ? exposureLabel(l.purity) : dash), registered: 'n/a',
        note: l.window ?? '', ratings: ratingsText(l), as_of: l.as_of ?? dash,
      });
    });
  });
  return rows;
}

/**
 * DashboardControls hides a table row whose `data-<control>` differs from the control's value, and has no notion of `all`.
 * This expands each row into the rows that make `all` work: one copy per control value the row is visible under (its own
 * layer and `all`; its own market and `all`; `all` and, if covered, `three`), so exactly one copy shows at a time.
 * Pass the keys the table's `filterKeys` lists; keys not passed keep the row's own value.
 */
export function tableVariants<T extends { layer: string; market: string; coverage: string }>(
  rows: T[], keys: ('layer' | 'market' | 'coverage')[] = ['layer', 'market', 'coverage'],
): T[] {
  return rows.flatMap((r) => {
    const vary = (key: 'layer' | 'market' | 'coverage'): string[] => {
      if (!keys.includes(key)) return [r[key]];
      if (key === 'coverage') return r.coverage === 'three' ? ['all', 'three'] : ['all'];
      return [r[key], 'all'];
    };
    return vary('layer').flatMap((layer) => vary('market').flatMap((market) => vary('coverage').map((coverage) => ({ ...r, layer, market, coverage }))));
  });
}


// ---- the China panel's table ---------------------------------------------------------------------------------------

export interface ChinaTableRow {
  name: string;
  name_cn: string;
  /** The Chinese name wrapped in `<span lang="zh-Hans">` for a screen reader; HTML, for a cell that is set as HTML. `name_cn` stays plain text. */
  name_cn_html: string;
  ticker: string;
  ring: string;
  /** Whole business, main line of business or partial exposure; `—` when the listings mart is not passed. */
  exposure: string;
  ps_ttm: number | null;
  /** 0 to 1; format `pct`. */
  ps_percentile: number | null;
  /** Fraction; format `pct`. */
  revenue_yoy: number | null;
  revenue_period: string;
  /** `yes`, `no`, or `n/a` when the percentile or the growth figure is missing. */
  top_quartile_while_falling: string;
  history_days: number | null;
  history_start: string;
  /** `yes` when the history is shorter than `SHORT_HISTORY_SHARE` of the longest one: its percentile is taken over a shorter window. */
  short_history: string;
  as_of: string;
}

/**
 * A listing's percentile is taken over its own history. A history shorter than this share of the longest one is marked on the
 * figure and in the table, because a younger listing's window can miss earlier peaks and then a high percentile is easier to reach.
 * Definitional (a display threshold), not a result.
 */
export const SHORT_HISTORY_SHARE = 0.75;
/** Days below which a history counts as short, from the longest history in `days`. */
export const shortHistoryBelow = (days: (number | null | undefined)[]): number =>
  Math.ceil(SHORT_HISTORY_SHARE * Math.max(0, ...days.filter((d): d is number => d != null && Number.isFinite(d))));

const escHtml = (t: string): string => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
/** A Chinese string as an inline element a screen reader reads in Chinese (`lang="zh-Hans"`), escaped; empty or `—` stays plain. */
export const zhSpan = (t: string | null | undefined): string => (t && t !== dash ? `<span lang="zh-Hans">${escHtml(t)}</span>` : dash);

/**
 * One row per A-share listing of `china_panel`, in percentile order, for the page's China table (no filter attributes). Pass the
 * `listings` mart to fill the exposure column (the build's own copy when omitted).
 */
export function chinaTableRows(rows: ChinaRow[], listings: Listing[] = BUILD_LISTINGS): ChinaTableRow[] {
  const purityOf = new Map(listings.map((l) => [l.ticker, l.purity]));
  const shortBelow = shortHistoryBelow(rows.map((r) => r.n_history_days));
  return rows.filter((r) => r.ticker).map((r) => {
    const flag = r.flags && typeof r.flags.ps_percentile_while_revenue_falling === 'boolean' ? (r.flags.ps_percentile_while_revenue_falling as boolean) : null;
    const purity = purityOf.get(r.ticker as string);
    return {
      name: r.name ?? (r.ticker as string), name_cn: r.name_cn ?? dash, name_cn_html: zhSpan(r.name_cn), ticker: r.ticker as string, ring: r.ring ?? dash,
      exposure: purity ? exposureLabel(purity) : dash,
      ps_ttm: r.ps_ttm, ps_percentile: r.ps_percentile, revenue_yoy: r.revenue_yoy, revenue_period: r.revenue_period ?? dash,
      top_quartile_while_falling: flag == null ? 'n/a' : flag ? 'yes' : 'no',
      history_days: r.n_history_days, history_start: r.history_start ?? dash,
      short_history: r.n_history_days != null && r.n_history_days < shortBelow ? 'yes' : 'no',
      as_of: r.as_of ?? dash,
    };
  }).sort((a, b) => (b.ps_percentile ?? -1) - (a.ps_percentile ?? -1) || a.ticker.localeCompare(b.ticker));
}
