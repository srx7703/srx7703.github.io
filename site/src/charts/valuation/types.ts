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

/** Purity drives the mark: a company whose business IS the track is filled, the rest are outlined. */
export const PURITY_DOMAIN = ['the track is the business', 'partial exposure'];
export const purityOf = (r: CompanyRow) => (r.purity === 'high' ? PURITY_DOMAIN[0] : PURITY_DOMAIN[1]);
