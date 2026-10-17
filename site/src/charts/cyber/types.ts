/** Row types of the marts in data/marts/cyber/*.json (see docs/cyber/PIPELINE_SPEC.md). Every field is nullable: a missing
 * value is a fact about the source and the charts decide what to do with it. Generated from the published marts, then kept by hand. */

/** One row of listings.json. */
export interface Listing {
  ticker: string | null;
  name: string | null;
  name_cn: string | null;
  country: string | null;
  ring: string | null;
  layer: string | null;
  purity: string | null;
  status: string | null;
  status_date: string | null;
  platform: boolean | null;
  placement_basis: string | null;
  also_in: unknown[] | null;
  market_group: string | null;
  note: string | null;
  priced: boolean | null;
  currency: string | null;
  financial_currency: string | null;
  price: number | null;
  price_ts: string | null;
  market_cap: number | null;
  enterprise_value: number | null;
  ntm_revenue: number | null;
  ev_ntm: number | null;
  ev_ntm_reason: string | null;
  growth: number | null;
  fcf_margin: number | null;
  rule_of_40: number | null;
  target_mean: number | null;
  target_high: number | null;
  target_low: number | null;
  n_opinions: number | null;
  upside: number | null;
  dispersion: number | null;
  thin: boolean | null;
  recommendation_mean: number | null;
  yahoo_ratings: Record<string, unknown> | null;
  em_ratings_6m: Record<string, unknown> | null;
  rev_fy0_n: number | null;
  rev_fy1_n: number | null;
  em_rev0_n: number | null;
  fy0_end: string | null;
  in_regression_population: boolean | null;
}

/** One row of targets.json. */
export interface TargetRow {
  ticker: string | null;
  name: string | null;
  layer: string | null;
  purity: string | null;
  market_group: string | null;
  status: string | null;
  currency: string | null;
  price: number | null;
  price_ts: string | null;
  target_mean: number | null;
  target_high: number | null;
  target_low: number | null;
  n_opinions: number | null;
  upside: number | null;
  dispersion: number | null;
  thin: boolean | null;
  pence_quote: boolean | null;
  in_q4_q5_list: boolean | null;
  as_of: string | null;
}

/** One row of recommendations.json. */
export interface RecommendationRow {
  source: string | null;
  scope: string | null;
  ticker: string | null;
  name: string | null;
  layer: string | null;
  market_group: string | null;
  window: string | null;
  n_listings: number | null;
  n_ratings: number | null;
  strong_buy: number | null;
  buy: number | null;
  hold: number | null;
  sell: number | null;
  strong_sell: number | null;
  recommendation_mean: number | null;
  em_orgs: number | null;
  em_buy: number | null;
  em_add: number | null;
  em_neutral: string | null;
  em_reduce: string | null;
  em_sell: string | null;
  as_of: string | null;
}

/** One row of china_panel.json. */
export interface ChinaRow {
  ticker: string | null;
  name: string | null;
  name_cn: string | null;
  ring: string | null;
  ps_ttm: number | null;
  ps_percentile: number | null;
  pb: number | null;
  pb_percentile: number | null;
  total_market_cap: number | null;
  n_history_days: number | null;
  history_start: string | null;
  as_of: string | null;
  revenue_yoy: number | null;
  revenue_period: string | null;
  flags: Record<string, unknown> | null;
}

/** One row of ledger_timeline.json. */
export interface LedgerRow {
  company: string | null;
  layer: string | null;
  market: string | null;
  date: string | null;
  period: string | null;
  metric: string | null;
  metric_kind: string | null;
  value: number | null;
  unit: string | null;
  qualifier: string | null;
  lower: number | null;
  upper: number | null;
  estimate: number | null;
  basis: string | null;
  status: string | null;
  tag: string | null;
  chartable: boolean | null;
  stale: boolean | null;
  stale_mark: boolean | null;
  vintage_year: number | null;
  company_confirmed: boolean | null;
  definition: string | null;
  source_name: string | null;
  source_url: string | null;
  publish_date: string | null;
  last_checked: string | null;
  caveat: string | null;
}

/** One row of ledger_latest.json. */
export interface LedgerLatestRow {
  company: string | null;
  layer: string | null;
  market: string | null;
  date: string | null;
  period: string | null;
  metric: string | null;
  metric_kind: string | null;
  value: number | null;
  unit: string | null;
  qualifier: string | null;
  lower: number | null;
  upper: number | null;
  estimate: number | null;
  basis: string | null;
  status: string | null;
  tag: string | null;
  chartable: boolean | null;
  stale: boolean | null;
  stale_mark: boolean | null;
  vintage_year: number | null;
  company_confirmed: boolean | null;
  definition: string | null;
  source_name: string | null;
  source_url: string | null;
  publish_date: string | null;
  last_checked: string | null;
  caveat: string | null;
}

/** One row of private_intervals.json. */
export interface PrivateInterval {
  company: string | null;
  layer: string | null;
  market: string | null;
  name_cn: string | null;
  l2: string | null;
  directory_status: string | null;
  in_directory: boolean | null;
  headline_metric: string | null;
  headline_period: string | null;
  headline_value: number | null;
  headline_unit: string | null;
  headline_qualifier: string | null;
  headline_basis: string | null;
  headline_as_of: string | null;
  headline_source_url: string | null;
  headline_tag: string | null;
  vintage_year: number | null;
  stale_mark: boolean | null;
  company_confirmed: boolean | null;
  headline_caveat: string | null;
  headline_reason: string | null;
  discount: number | null;
  discount_band: number | null;
  discount_lower: number | null;
  discount_upper: number | null;
  discount_unit: string | null;
  discount_qualifier: string | null;
  discount_basis: string | null;
  discount_as_of: string | null;
  discount_source_url: string | null;
  discount_tag: string | null;
  discount_scored: boolean | null;
  discount_reason: string | null;
  arr_value: number | null;
  arr_unit: string | null;
  arr_qualifier: string | null;
  arr_tag: string | null;
  arr_company_confirmed: boolean | null;
  multiple_low: number | null;
  multiple_high: number | null;
  multiple_low_ticker: string | null;
  multiple_high_ticker: string | null;
  comparable_tickers: string | null;
  comparable_missing: string | null;
  multiples_as_of: string | null;
  comparable_lower: number | null;
  comparable_upper: number | null;
  comparable_unit: string | null;
  comparable_qualifier: string | null;
  comparable_basis: string | null;
  comparable_as_of: string | null;
  comparable_source_url: string | null;
  comparable_tag: string | null;
  comparable_scored: boolean | null;
  comparable_reason: string | null;
  above: boolean | null;
  q6_scored: boolean | null;
  above_reason: string | null;
  above_note: string | null;
}

/** One row of events.json. */
export interface EventRow {
  company: string | null;
  layer: string | null;
  market: string | null;
  event_kind: string | null;
  event_date: string | null;
  closed_date: string | null;
  counterparty: string | null;
  buyer_type: string | null;
  announced_value: number | null;
  unit: string | null;
  qualifier: string | null;
  metric_kind: string | null;
  sec_value: number | null;
  sec_qualifier: string | null;
  sec_source_url: string | null;
  chart_value: number | null;
  chart_qualifier: string | null;
  chart_basis: string | null;
  status_after: string | null;
  tag: string | null;
  chartable: boolean | null;
  stale: boolean | null;
  company_confirmed: boolean | null;
  as_of_date: string | null;
  source_name: string | null;
  source_url: string | null;
  publish_date: string | null;
  caveat: string | null;
}

/** One row of reported_unconfirmed.json. */
export interface ReportedRow {
  source: string | null;
  company: string | null;
  layer: string | null;
  market: string | null;
  date: string | null;
  period: string | null;
  metric: string | null;
  metric_kind: string | null;
  counterparty: string | null;
  value: number | null;
  unit: string | null;
  qualifier: string | null;
  basis: string | null;
  status: string | null;
  tag: string | null;
  reason: string | null;
  company_confirmed: boolean | null;
  source_name: string | null;
  source_url: string | null;
  publish_date: string | null;
  caveat: string | null;
}

/** One row of layer_summary.json. */
export interface LayerSummary {
  layer: string | null;
  code: number | null;
  name: string | null;
  n_l2: number | null;
  n_a: number | null;
  n_a_us: number | null;
  n_a_china: number | null;
  n_a_other_overseas: number | null;
  n_b: number | null;
  n_b_us: number | null;
  n_b_china: number | null;
  n_b_other_overseas: number | null;
  n_f: number | null;
  n_private: number | null;
  private_by_l2: unknown[] | null;
  n_population: number | null;
  n_population_with_multiple: number | null;
  population_median_ev_ntm: number | null;
}

/** One row of scoreboard.json. */
export interface ScoreRow {
  id: string | null;
  title: string | null;
  status: string | null;
  reason: string | null;
  graded: string | null;
  next_grading: string | null;
  already_seen: string | null;
}

/** One row of scoreboard_evidence.json. */
export interface EvidenceRow {
  id: string | null;
  label: string | null;
  value: number | null;
  lo: string | null;
  hi: string | null;
  level: string | null;
  qualifier: string | null;
  unit: string | null;
  period: string | null;
  as_of: string | null;
  ref: string | null;
  note: string | null;
}

/** One row of etf_crosscheck.json. */
export interface EtfRow {
  ticker: string | null;
  name: string | null;
  in_pool: boolean | null;
  ring: string | null;
  layer: string | null;
  purity: string | null;
  excluded_reason: string | null;
  status: string | null;
  company_status: string | null;
}

/** One row of excluded.json. */
export interface ExcludedRow {
  ticker: string | null;
  name: string | null;
  reason: string | null;
  kind: string | null;
}

/** One row of metric_dictionary.json. */
export interface DictionaryRow {
  metric: string | null;
  formula: string | null;
  unit: string | null;
  comparable_across: string | null;
  not_comparable_across: string | null;
  source: string | null;
}

/** One row of kpis.json. */
export interface Kpi {
  id: string | null;
  label: string | null;
  value: number | null;
  qualifier: string | null;
  unit: string | null;
  period: string | null;
  source_url: string | null;
  as_of: string | null;
  tag: string | null;
  note: string | null;
}

export type RegressionPoint = Record<string, unknown> & { as_of: string; n: number | null; r2: number | null; adj_r2: number | null; adj_r2_no_layer: number | null };
export interface RegressionMart { today: string; spec: Record<string, unknown>; latest: RegressionPoint | null; baseline: Record<string, unknown>; series: RegressionPoint[]; residuals: Record<string, unknown>[]; premium: Record<string, unknown> | null; premium_series: Record<string, unknown>[] }
