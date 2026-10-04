/**
 * Private ledger (dashboard D4): the derivation shared by the interval chart, the event timeline and
 * the linked table.
 *
 * Inputs: `data/marts/payments/ledger_timeline.json` (every company-stated private number, valuation
 * event and counterparty filing, chartable rows only) and `private_intervals.json` (bounds the pipeline
 * derives, e.g. Stripe net revenue from its stated volume and listed peers' take rates). Reported or
 * third-party numbers are never in either file; they live in `reported_unconfirmed.json`, table only.
 *
 * Qualifiers travel with each number and decide the mark:
 *   `=` a point; `~` a hollow point; `>` a lower bound, drawn as a rule rising to an open arrow;
 *   `<` an upper bound, the same pointing down; lower != upper a closed interval with caps.
 *
 * Group `ledger`, two controls (unbound top-level params in both charts):
 *   `company` (`all` or a company) and `metric` (a metric family below).
 */

export type LedgerTimelineRow = {
  company: string; segment: string; date: string; period: string; source: string; item: string;
  metric: string; metric_kind: string | null; event_kind: string | null; counterparty: string | null;
  value: number | null; unit: string | null; currency: string | null; qualifier: string | null;
  lower: number | null; upper: number | null; estimate: number | null; status: string | null;
  tag: string; chartable: boolean; stale: boolean; company_confirmed: boolean | null;
  source_url: string | null; caveat?: string | null;
};

export type PrivateIntervalRow = {
  company: string; segment: string; metric: string; period: string; as_of: string;
  lower: number | null; upper: number | null; estimate: number | null; qualifier: string; unit: string;
  formula: string; inputs: string; assumption: string | null; tag: string; chartable: boolean; stale: boolean;
  source_url: string | null; note?: string | null;
};

export const LEDGER_GROUP = 'ledger';

/** Metric families the `metric` control offers, in control order. */
export const LEDGER_METRICS = [
  { value: 'revenue', label: 'Revenue', unit: 'USD' },
  { value: 'volume', label: 'Volume', unit: 'USD' },
  { value: 'take_rate', label: 'Take rate', unit: 'percent' },
  { value: 'valuation', label: 'Valuation', unit: 'USD' },
  { value: 'customers', label: 'Customers', unit: 'count' },
] as const;
export type LedgerMetric = (typeof LEDGER_METRICS)[number]['value'];

const FAMILY: Record<string, LedgerMetric> = {
  annualized_revenue: 'revenue', revenue: 'revenue', net_revenue: 'revenue', annualized_net_revenue: 'revenue',
  implied_net_revenue: 'revenue', implied_net_interchange: 'revenue',
  annualized_transaction_volume: 'volume', total_volume: 'volume', transaction_volume: 'volume',
  annualized_purchase_volume: 'volume', total_payment_volume: 'volume', implied_annual_card_spend: 'volume',
  valuation: 'valuation', acquisition_price_announced: 'valuation',
  customers: 'customers',
};

/** Which family a ledger metric belongs to, or null when it is table-only (acquisition accounting). */
export function metricFamily(metric: string): LedgerMetric | null {
  if (metric.startsWith('take_rate:')) return 'take_rate';
  return FAMILY[metric] ?? null;
}

export const METRIC_LABEL: Record<string, string> = {
  annualized_revenue: 'Annualized revenue (run rate)',
  revenue: 'Revenue, full year',
  net_revenue: 'Net revenue, full year',
  annualized_net_revenue: 'Annualized net revenue',
  implied_net_revenue: 'Implied net revenue (derived bound)',
  implied_net_interchange: 'Implied net interchange (derived bound)',
  annualized_transaction_volume: 'Annualized transaction volume',
  total_volume: 'Total payment volume, calendar year',
  transaction_volume: 'Transaction volume, full year',
  annualized_purchase_volume: 'Annualized purchase volume',
  total_payment_volume: 'Total payment volume',
  implied_annual_card_spend: 'Implied annual card spend (derived bound)',
  valuation: 'Valuation',
  acquisition_price_announced: 'Acquisition price at signing',
  customers: 'Customers',
  'take_rate:revenue_per_transaction_volume': 'Revenue / transaction volume (derived)',
  'take_rate:revenue_per_purchase_volume': 'Revenue / purchase volume (derived)',
};
export const metricLabel = (m: string) =>
  METRIC_LABEL[m] ?? m.replace(/^take_rate:/, 'take rate: ').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const KIND_LABEL: Record<string, string> = {
  stated_run_rate: 'Company-stated run rate', stated_volume: 'Company-stated figure',
  primary_round_valuation: 'Priced round', tender_valuation: 'Tender offer',
  acquisition_price: 'Acquisition price', counterparty_filing: 'Counterparty filing', derived: 'Derived bound',
};
export const kindLabel = (k: string | null | undefined) => (k ? KIND_LABEL[k] ?? k.replace(/_/g, ' ') : '—');

/** How far an open-ended bound's arrow reaches on the log axis (a drawing length, not a number). */
export const ARROW_FACTOR = 1.45;

export type LedgerPoint = {
  company: string; segment: string; family: LedgerMetric; metric: string; metric_label: string;
  date: string; period: string; kind: string; kind_label: string; shape: 'stated' | 'derived';
  qualifier: string; unit: string;
  lower: number | null; upper: number | null; estimate: number | null;
  /** Where the point mark sits; null for a bound or an interval without an estimate. */
  point: number | null;
  /** Rule ends: an interval's lower and upper, or a bound and its arrow tip. */
  lo: number | null; hi: number | null;
  /** 'up' for `>`, 'down' for `<`, null otherwise. */
  arrow: 'up' | 'down' | null; arrow_at: number | null;
  interval: boolean; approx: boolean; confirmed: boolean | null; status: string | null;
  label: string; note: string | null; source_url: string | null;
};

const fmtUsd = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1e12) return `$${+(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${+(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${+(v / 1e6).toFixed(1)}M`;
  return `$${Math.round(v).toLocaleString('en-US')}`;
};
/** Format one ledger value in its unit (used in labels, tooltips and the table). */
export function fmtValue(v: number | null | undefined, unit: string): string {
  if (v == null) return '—';
  if (unit === 'percent') return `${+v.toFixed(2)}%`;
  if (unit === 'count') return Math.round(v).toLocaleString('en-US');
  return fmtUsd(v);
}
/** The number as stated: qualifier, then value or range. */
export function statedLabel(p: { qualifier: string; lower: number | null; upper: number | null; estimate: number | null; unit: string }) {
  const { qualifier: q, lower, upper, estimate, unit } = p;
  if (lower != null && upper != null && lower !== upper) {
    return `${fmtValue(lower, unit)} to ${fmtValue(upper, unit)}${estimate != null ? ` (est. ${fmtValue(estimate, unit)})` : ''}`;
  }
  if (q === '>') return `> ${fmtValue(lower, unit)}`;
  if (q === '<') return `< ${fmtValue(upper ?? lower, unit)}`;
  const v = estimate ?? lower ?? upper;
  return `${q === '~' ? '~ ' : ''}${fmtValue(v, unit)}`;
}

function toPoint(base: {
  company: string; segment: string; metric: string; date: string; period: string; kind: string | null;
  qualifier: string | null; unit: string | null; lower: number | null; upper: number | null; estimate: number | null;
  confirmed: boolean | null; status: string | null; note: string | null; source_url: string | null;
}): LedgerPoint | null {
  const family = metricFamily(base.metric);
  if (!family) return null;
  const q = base.qualifier ?? '=';
  const unit = base.unit ?? 'USD';
  const { lower, upper, estimate } = base;
  const interval = lower != null && upper != null && lower !== upper;
  let lo: number | null = null; let hi: number | null = null; let point: number | null = null;
  let arrow: LedgerPoint['arrow'] = null; let arrowAt: number | null = null;
  if (interval) { lo = lower; hi = upper; point = estimate; }
  else if (q === '>' && lower != null) { lo = lower; hi = lower * ARROW_FACTOR; arrow = 'up'; arrowAt = hi; }
  else if (q === '<' && (upper ?? lower) != null) { hi = (upper ?? lower) as number; lo = hi / ARROW_FACTOR; arrow = 'down'; arrowAt = lo; }
  else point = estimate ?? lower ?? upper;
  if (point == null && lo == null) return null;
  if ((point != null && point <= 0) || (lo != null && lo <= 0)) return null; // log axis: nothing at or below zero
  const kind = base.kind ?? 'derived';
  return {
    company: base.company, segment: base.segment, family, metric: base.metric, metric_label: metricLabel(base.metric),
    date: base.date, period: base.period, kind, kind_label: kindLabel(kind),
    shape: kind === 'derived' || kind === 'counterparty_filing' ? 'derived' : 'stated',
    qualifier: q, unit, lower, upper, estimate, point, lo, hi, arrow, arrow_at: arrowAt, interval,
    approx: q === '~', confirmed: base.confirmed, status: base.status,
    label: statedLabel({ qualifier: q, lower, upper, estimate, unit }), note: base.note, source_url: base.source_url,
  };
}

/** Every chartable ledger number as a mark-ready row (table-only metrics and non-chartable rows dropped). */
export function ledgerPoints(timeline: LedgerTimelineRow[], intervals: PrivateIntervalRow[] = []): LedgerPoint[] {
  const out: LedgerPoint[] = [];
  const seen = new Set<string>();
  for (const r of timeline) {
    if (!r.chartable || r.stale || (r.value == null && r.lower == null)) continue;
    const p = toPoint({
      company: r.company, segment: r.segment, metric: r.metric, date: r.date, period: r.period,
      kind: r.metric_kind, qualifier: r.qualifier, unit: r.unit, lower: r.lower ?? r.value, upper: r.upper,
      estimate: r.estimate, confirmed: r.company_confirmed, status: r.status, note: r.caveat ?? null, source_url: r.source_url,
    });
    if (!p) continue;
    const key = `${p.company}|${p.metric}|${p.date}|${p.label}`;
    if (seen.has(key)) continue; // an event and a metric row for the same round
    seen.add(key);
    out.push(p);
  }
  for (const r of intervals) {
    if (!r.chartable || r.stale) continue;
    const p = toPoint({
      company: r.company, segment: r.segment, metric: r.metric, date: r.as_of, period: r.period, kind: 'derived',
      qualifier: r.qualifier, unit: r.unit, lower: r.lower, upper: r.upper, estimate: r.estimate,
      confirmed: false, status: null, note: [r.assumption, r.note].filter(Boolean).join('; ') || null, source_url: r.source_url,
    });
    if (p) out.push(p);
  }
  return out.sort((a, b) => a.company.localeCompare(b.company) || a.date.localeCompare(b.date));
}

/** Companies with at least one chartable number, alphabetical. */
export const ledgerCompanies = (points: LedgerPoint[]) => [...new Set(points.map((p) => p.company))].sort((a, b) => a.localeCompare(b));

/** The exact `controls` prop for <DashboardControls group="ledger">. */
export function ledgerControls(points: LedgerPoint[], opts: { company?: string; metric?: LedgerMetric } = {}) {
  const families = new Set(points.map((p) => p.family));
  return [
    {
      name: 'metric', label: 'Metric', default: opts.metric ?? 'revenue', type: 'select' as const,
      options: LEDGER_METRICS.filter((m) => families.has(m.value)).map((m) => ({ value: m.value, label: m.label })),
    },
    {
      name: 'company', label: 'Company', default: opts.company ?? 'all', type: 'select' as const,
      options: [{ value: 'all', label: 'All companies' }, ...ledgerCompanies(points).map((c) => ({ value: c, label: c }))],
    },
  ];
}

export const LEDGER_PARAMS = (opts: { company?: string; metric?: string } = {}) => [
  { name: 'company', value: opts.company ?? 'all' },
  { name: 'metric', value: opts.metric ?? 'revenue' },
];

/**
 * Rows for the linked DataTable: one per public fragment (chartable ledger rows, including the
 * table-only acquisition accounting, and the derived bounds). `company` and `metric` carry the control
 * values for `data-company` / `data-metric`; `metric` is '' for rows outside every family.
 */
export function ledgerTableRows(timeline: LedgerTimelineRow[], intervals: PrivateIntervalRow[] = []) {
  const rows = timeline.filter((r) => r.chartable && !r.stale).map((r) => ({
    company: r.company,
    metric: metricFamily(r.metric) ?? '',
    item: r.item === 'event' && r.event_kind ? `${r.event_kind.replace(/_/g, ' ')}${r.counterparty ? `: ${r.counterparty}` : ''}` : metricLabel(r.metric),
    date: r.date,
    value: r.value == null && r.lower == null ? '—'
      : statedLabel({ qualifier: r.qualifier ?? '=', lower: r.lower ?? r.value, upper: r.upper, estimate: r.estimate, unit: r.unit ?? 'USD' }),
    kind: kindLabel(r.metric_kind),
    confirmed: r.company_confirmed == null ? '—' : r.company_confirmed ? 'yes' : 'no',
    status: r.status ?? '—',
    note: r.caveat ?? '',
    source: r.source_url ? `<a href="${r.source_url}">source</a>` : '—',
  }));
  const derived = intervals.filter((r) => r.chartable && !r.stale).map((r) => ({
    company: r.company,
    metric: metricFamily(r.metric) ?? '',
    item: metricLabel(r.metric),
    date: r.as_of,
    value: statedLabel(r),
    kind: kindLabel('derived'),
    confirmed: 'no',
    status: '—',
    note: [r.formula, r.assumption].filter(Boolean).join('; '),
    source: r.source_url ? `<a href="${r.source_url}">source</a>` : '—',
  }));
  return [...rows, ...derived].sort((a, b) => a.company.localeCompare(b.company) || a.date.localeCompare(b.date));
}

export const LEDGER_TABLE_COLUMNS = [
  { key: 'company', label: 'Company' },
  { key: 'item', label: 'Figure' },
  { key: 'date', label: 'As of' },
  { key: 'value', label: 'Value as stated' },
  { key: 'kind', label: 'Kind' },
  { key: 'confirmed', label: 'Company confirmed' },
  { key: 'note', label: 'Qualifier and caveat' },
  { key: 'source', label: 'Link' },
];
