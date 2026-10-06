/**
 * Adoption tracker (dashboard D3): what the three charts share.
 *
 * The three marts behind this dashboard (`webtech_q2.json`, `devstats_monthly.json`,
 * `jobs_by_function.json`) are written by `pipelines/payments/publish.py` only after a CI run of the
 * webtech / devstats / jobs pipelines, so a local build usually has none of them. Each spec therefore
 * takes a `source` that is either the mart's URL (the normal case: the charts load it by URL), the rows
 * themselves, or `null` when the page knows the mart is absent (`facts.ci_sources.<name> === false`).
 * A missing or empty file never draws an empty chart: the chart's title states why, using `indata()` and
 * `data()` against the named source dataset, because Vega's aggregate emits nothing on empty input.
 *
 * Group `adoption`, two controls (unbound top-level params of the same names in every spec):
 * - `source`: which slice of each source is drawn. `registered` is the slice the evaluation plan
 *   grades (Q2: US top-10k origins, mobile; Q1: client-side SDKs). `descriptive` is the broader slice
 *   kept for context (all US origins; server SDKs). The hiring chart has no slices and ignores it.
 * - `company`: the company drawn in colour; `all` draws every series alike.
 */
import { tok } from '../theme';

export const ADOPTION_GROUP = 'adoption';

/** Where the page loads each mart from (synced to public/data by site/scripts/sync-data.mjs). */
export const ADOPTION_URLS = {
  web: '/data/marts/payments/webtech_q2.json',
  npm: '/data/marts/payments/devstats_monthly.json',
  jobs: '/data/marts/payments/jobs_by_function.json',
} as const;

/** `facts.ci_sources` key for each chart. */
export const ADOPTION_CI_SOURCE = { web: 'webtech', npm: 'devstats', jobs: 'jobs' } as const;

export type WebRow = {
  slice: string; technology: string; date: string; origins: number;
  total_origins: number | null; share: number | null; rolling_mean_3m: number | null;
};
export type NpmRow = {
  side: 'client' | 'server'; series: string; company: string | null; period: string;
  downloads: number; index: number | null;
};
export type JobsRow = {
  snapshot_date: string; ats: string; board: string; function: string; postings: number; openings: number;
};

/** A mart URL, the rows themselves, or null when the page knows the file is absent. */
export type Source<T> = string | T[] | null;

/** Slices per source, keyed by the `source` control value. Names match pipelines/payments/evaluate.py. */
export const WEB_SLICE: Record<string, string> = {
  registered: 'US / Top 10k / mobile',
  descriptive: 'US / ALL / mobile',
};
export const NPM_SIDE: Record<string, string> = { registered: 'client', descriptive: 'server' };

export const SOURCE_OPTIONS = [
  { value: 'registered', label: 'Pre-registered slice' },
  { value: 'descriptive', label: 'Broader slice' },
];

/** Job-board token (pipelines/payments/config.py ATS_BOARDS) -> company name used across the page. */
export const BOARD_COMPANY: Record<string, string> = {
  stripe: 'Stripe', brex: 'Brex', mercury: 'Mercury', affirm: 'Affirm', adyen: 'Adyen', chime: 'Chime',
  block: 'Block/Square', toast: 'Toast', sezzle: 'Sezzle', billcom: 'BILL',
  ramp: 'Ramp', airwallex: 'Airwallex', plaid: 'Plaid',
};

/** Companies each source can see (pinned in config; the charts still check the data itself). */
export const WEB_COMPANIES = ['Stripe', 'PayPal'];
export const NPM_COMPANIES = ['Stripe', 'Adyen', 'PayPal', 'Checkout.com', 'Airwallex'];
export const JOBS_COMPANIES = Object.values(BOARD_COMPANY);

const ORDER = ['Stripe', 'PayPal', 'Adyen', 'Checkout.com', 'Airwallex', 'Ramp', 'Brex', 'Mercury', 'BILL',
  'Affirm', 'Sezzle', 'Block/Square', 'Toast', 'Chime', 'Plaid'];
export const ADOPTION_COMPANIES = ORDER.filter((c) =>
  WEB_COMPANIES.includes(c) || NPM_COMPANIES.includes(c) || JOBS_COMPANIES.includes(c));

/** The exact `controls` prop for <DashboardControls group="adoption">. */
export const ADOPTION_CONTROLS = [
  { name: 'source', label: 'Slice', options: SOURCE_OPTIONS, default: 'registered' },
  {
    name: 'company', label: 'Company', type: 'select' as const, default: 'Stripe',
    options: [{ value: 'all', label: 'All companies' }, ...ADOPTION_COMPANIES.map((c) => ({ value: c, label: c }))],
  },
];

export const ADOPTION_PARAMS = (opts: { source?: string; company?: string } = {}) => [
  { name: 'source', value: opts.source ?? 'registered' },
  { name: 'company', value: opts.company ?? 'Stripe' },
];

/**
 * Vega-Lite `data` for a source. The dataset is always named, so the reason layer can ask whether it
 * holds anything (`data(name)`) and whether it holds the selected company (`indata(name, field, …)`).
 */
export function namedData<T>(source: Source<T>, name: string) {
  if (typeof source === 'string') return { url: source, name, format: { type: 'json' } };
  return { values: source ?? [], name };
}

/** A Vega expression string literal (single-quoted, escaped). */
export const lit = (s: string) => `'${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n')}'`;

/**
 * The chart's own title, set to the string `reasonExpr` evaluates to (empty: no reason). It sits above
 * the plot, anchored to the left edge of the whole chart (axis labels included), so it never lands on
 * axis labels, axis titles or point labels however narrow the plot gets. Keep each line of a reason
 * under about 34 characters (phone width) and break longer ones with '\n'.
 */
export function reasonTitle(reasonExpr: string) {
  return {
    text: { expr: `split(${reasonExpr}, '\\n')` },
    anchor: 'start', frame: 'bounds', orient: 'top', offset: 6,
    fontSize: 12, fontStyle: 'italic', fontWeight: 'normal', color: tok('ink-2'), lineHeight: 16, limit: 0,
  };
}

/** A y-axis `labelExpr` that blanks the band labels while a reason is shown (so the reason has the room). */
export const labelsUnlessReason = (reasonExpr: string) => `(${reasonExpr}) != '' ? '' : datum.label`;

/** A Vega expression mapping a param to a value through a JS record (object literal lookup). */
export const lookupExpr = (map: Record<string, string>, param: string) =>
  `({${Object.entries(map).map(([k, v]) => `${lit(k)}: ${lit(v)}`).join(', ')}})[${param}]`;

/** True when the source has no usable rows: the reason the whole chart shows instead of data. */
export const emptyExpr = (name: string) => `length(data(${lit(name)})) == 0`;

export const emptyReason = (what: string) =>
  `Not on this build yet:\n${what} comes\nfrom a scheduled CI run.`;

/** Rows from a source passed as rows (titles and tables are computed at build time only from rows). */
export const rowsOf = <T>(source: Source<T> | undefined): T[] => (Array.isArray(source) ? source : []);
