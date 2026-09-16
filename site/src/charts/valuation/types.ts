/** Shape of one row in `data/marts/valuation/<track>_companies.json`, written by pipelines/valuation/publish.py. */
export type CompanyRow = {
  ticker: string;
  name: string;
  name_cn: string | null;
  track: 'optical' | 'ssb';
  purity: 'high' | 'main' | 'partial';
  market: string;
  fy_end_month: number;
  segment_line: string;
  price: number | null;
  price_currency: string | null;
  market_cap: number | null;
  reporting_currency: string | null;
  ttm_net_income: number | null;
  ttm_revenue: number | null;
  ttm_period_end: string | null;
  ttm_basis: 'ttm' | 'last_fy' | null;
  ttm_n_quarters: number | null;
  trailing_pe: number | null;
  trailing_pe_nm: string | null;
  eps_2026: number | null;
  eps_2027: number | null;
  estimate_currency: string | null;
  fwd_pe_2026: number | null;
  fwd_pe_2026_nm: string | null;
  fwd_pe_2027: number | null;
  fwd_pe_2027_nm: string | null;
  growth: number | null;
  n_analysts: number | null;
  coverage: 'covered' | 'thin' | 'unknown';
  dispersion: number | null;
  blend: boolean;
  actual_weight: number | null;
  estimate_source: string | null;
  revision_30d: number | null;
  revision_90d: number | null;
};

export type RevisionRow = { ticker: string; name: string; as_of: string; year: number; eps: number };

export type ShareMember = {
  ticker: string;
  name: string;
  purity: string;
  revenue_usd: number;
  currency: string;
  basis: string;
  share: number;
  period_end: string | null;
};

/** Listings a chart can plot: priced, with a calendar-2026 multiple. */
export const priced = (rows: CompanyRow[]) => rows.filter((r) => r.fwd_pe_2026 != null);

/**
 * Exposure drives the *mark*, never the colour (docs/CHART_RULES.md rule 14): a company whose whole
 * business is the track is drawn filled, a diversified company is drawn hollow. The labels are written
 * to be true on both tracks — a battery maker with a solid-state roadmap is "partial exposure", and
 * saying its business *is* solid-state would be the one claim the page exists to prevent.
 */
export const PURITY_DOMAIN = ['whole business', 'partial exposure'];
export const purityOf = (r: CompanyRow) => (r.purity === 'high' ? PURITY_DOMAIN[0] : PURITY_DOMAIN[1]);

/**
 * Thin coverage fades the mark (rule 14). "unknown" fades too: a listing whose broker count the source
 * never published is not a confident consensus, and drawing it at full strength let a 97.4x listing with
 * no analyst count set the top of a headline range.
 */
export const isThin = (r: Pick<CompanyRow, 'coverage'>) => r.coverage === 'thin' || r.coverage === 'unknown';

/**
 * docs/CHART_RULES.md rule 13: a log axis is for a distribution spanning "more than about two orders
 * of magnitude", and is never used to flatter a series. 1.5 orders (a factor of ~32) is where "about"
 * stops. The optical pool spans 12.8x to 760.7x and earns one; the solid-state pool spans 10.3x to
 * 97.4x — under one order — where a log axis would pull the top of the range down towards the bottom
 * and make the pool look tighter than it is.
 */
export const LOG_MIN_ORDERS = 1.5;

export const spansOrders = (values: (number | null | undefined)[]): boolean => {
  const v = values.filter((x): x is number => x != null && x > 0);
  if (v.length < 2) return false;
  return Math.log10(Math.max(...v) / Math.min(...v)) >= LOG_MIN_ORDERS;
};

/**
 * The PE scale a chart should use, and the word the subtitle has to match. The padding keeps the
 * cheapest and priciest listings off the edge of the plot: both are named in the page's headline and
 * `clip: true` was cutting them in half.
 */
export const peScale = (values: (number | null | undefined)[]) =>
  spansOrders(values)
    ? ({ type: 'log' as const, nice: false, padding: 12 })
    : ({ type: 'linear' as const, nice: true, zero: false, padding: 12 });
