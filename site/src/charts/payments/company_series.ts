import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyPoint, type Metric, defaultCompany, lookupExpr } from './company_data';

/**
 * Company explorer, left chart: one metric over time for the selected company, with the other
 * companies in its segment drawn grey behind it.
 *
 * Driven by two unbound params, `company` and `metric`, which `<DashboardControls group="company">`
 * sets through `view.signal`. The peer rule depends on the metric. A take rate is a ratio, so every
 * peer in the segment can sit on the same axis. Volume and revenue are amounts in each company's
 * reporting currency over its own reporting period, so a peer is only drawn when both match the
 * selected company: a euro half-year beside a dollar quarter is two different units, and the site
 * converts neither. The subtitle says so.
 *
 * The y-axis title is a text mark, because a Vega-Lite axis title cannot follow a param.
 */
export function companySeriesSpec(points: CompanyPoint[], opts: { company?: string; metric?: Metric } = {}) {
  const company = opts.company ?? defaultCompany(points);
  const metric: Metric = opts.metric ?? 'take_rate';
  const segOf = new Map(points.map((p) => [p.company, p.segment]));
  const cmpOf = new Map(points.map((p) => [p.company, p.cmp]));
  const currencyOf = new Map(points.map((p) => [p.company, p.currency]));
  const y = { field: 'y', type: 'quantitative', title: null, scale: { zero: true }, axis: { tickCount: 5, format: ',~r', minExtent: 36 } };
  const x = { field: 'as_of', type: 'temporal', title: null, axis: { format: '%Y', tickCount: 'year', labelAngle: 0 } };
  const tooltip = [
    { field: 'company', type: 'nominal', title: 'Company' },
    { field: 'period', type: 'nominal', title: 'Period' },
    { field: 'volume_b', type: 'quantitative', title: 'Volume (bn, reporting currency)', format: ',.1f' },
    { field: 'revenue_b', type: 'quantitative', title: 'Revenue line (bn)', format: ',.2f' },
    { field: 'take_rate_pct', type: 'quantitative', title: 'Take rate (%)', format: '.2f' },
    { field: 'currency', type: 'nominal', title: 'Currency' },
    { field: 'numerator_metric', type: 'nominal', title: 'Revenue line' },
    { field: 'qualifier', type: 'nominal', title: 'Qualifier' },
  ];
  return {
    $schema: VL_SCHEMA,
    height: 260,
    params: [
      { name: 'company', value: company },
      { name: 'metric', value: metric },
    ],
    data: { values: points.map((p) => ({ ...p, volume_b: p.volume / 1e9, revenue_b: p.revenue / 1e9 })) },
    transform: [
      { calculate: lookupExpr(segOf), as: 'sel_seg' },
      { calculate: lookupExpr(cmpOf), as: 'sel_cmp' },
      { calculate: 'datum.company == company', as: 'is_sel' },
      {
        filter: "datum.is_sel || (datum.segment == datum.sel_seg && (metric == 'take_rate' || datum.cmp == datum.sel_cmp))",
      },
      {
        calculate: "metric == 'volume' ? datum.volume_b : metric == 'revenue' ? datum.revenue_b : datum.take_rate_pct",
        as: 'y',
      },
    ],
    layer: [
      {
        transform: [{ filter: '!datum.is_sel' }],
        mark: { type: 'line', strokeWidth: 1.25, interpolate: 'linear', point: { size: 14, filled: true } },
        encoding: {
          x, y,
          detail: { field: 'company', type: 'nominal' },
          color: { value: series.neutral },
          opacity: { value: 0.55 },
          tooltip,
        },
      },
      {
        transform: [{ filter: 'datum.is_sel' }],
        mark: { type: 'line', strokeWidth: 2.25, point: { size: 34, filled: true } },
        encoding: { x, y, color: { value: series.a }, tooltip },
      },
      {
        // Peer names at their last point, small and grey, so the background is identifiable.
        transform: [
          { filter: '!datum.is_sel' },
          { joinaggregate: [{ op: 'max', field: 'as_of', as: 'last' }], groupby: ['company'] },
          { filter: 'datum.as_of == datum.last' },
        ],
        mark: { type: 'text', align: 'left', dx: 6, fontSize: 10, color: tok('ink-3') },
        encoding: { x, y, text: { field: 'company' } },
      },
      {
        transform: [
          { filter: 'datum.is_sel' },
          { joinaggregate: [{ op: 'max', field: 'as_of', as: 'last' }] },
          { filter: 'datum.as_of == datum.last' },
        ],
        mark: { type: 'text', align: 'left', dx: 7, dy: -9, fontSize: 11, fontWeight: 'bold', color: series.a },
        encoding: { x, y, text: { field: 'company' } },
      },
      {
        // Axis title that follows the metric and the selected company's currency.
        data: { values: [{}] },
        mark: {
          type: 'text', align: 'left', baseline: 'bottom', fontSize: 11, color: tok('ink-2'),
          text: {
            expr: `metric == 'take_rate' ? 'Take rate, % of volume' : (metric == 'volume' ? 'Volume' : 'Revenue line') + ', billions of ' + ${lookupExpr(currencyOf)} + ' per period'`,
          },
        },
        encoding: { x: { value: 0 }, y: { value: -8 } },
      },
    ],
  };
}

/** Subtitle for the series figure: the metric, the unit and the peer rule. */
export function companySeriesSubtitle(points: CompanyPoint[]): string {
  const first = points.reduce((m, p) => (p.as_of < m ? p.as_of : m), points[0]?.as_of ?? '');
  const last = points.reduce((m, p) => (p.as_of > m ? p.as_of : m), '');
  return `Filed volume, revenue line and their ratio for the selected company (colour), ${first.slice(0, 7)} to ${last.slice(0, 7)}, `
    + `with the rest of its segment in grey. Amounts are in each company's reporting currency, unconverted; for volume and revenue `
    + `only peers with the same currency and reporting period are drawn. One headline ratio per company; quarters where filed, otherwise half-years.`;
}
