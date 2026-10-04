import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyPoint, defaultCompany, latestPoints, fmtPct } from './company_data';

/** Iso-take-rate lines, as fractions of volume. */
export const ISO_RATES = [0.001, 0.003, 0.01, 0.03, 0.1];

export type ScatterSplit = {
  plotted: CompanyPoint[];
  /** Companies left off the scatter, each with the reason in words (shown in the chart when selected). */
  excluded: { company: string; reason: string }[];
};

/**
 * Which latest points can share dollar axes.
 *
 * The scatter is in US dollars per quarter. A company that reports in another currency is left off
 * rather than converted (the pipeline converts nothing), and one that reports half-years is left off
 * rather than halved. Both get a sentence, which the chart prints when that company is selected.
 */
export function scatterSplit(points: CompanyPoint[]): ScatterSplit {
  const plotted: CompanyPoint[] = [];
  const excluded: ScatterSplit['excluded'] = [];
  for (const p of latestPoints(points)) {
    const why: string[] = [];
    if (p.currency !== 'USD') why.push(`reports in ${p.currency}, and this chart is in US dollars with no currency conversion`);
    if (p.cadence !== 'quarter') why.push(`files ${p.cadence === 'half' ? 'half-years' : 'annual totals'} only, and this chart compares quarters`);
    if (why.length) excluded.push({ company: p.company, reason: `${p.company} is not on this chart: it ${why.join('; it ')}.` });
    else plotted.push(p);
  }
  return { plotted, excluded };
}

const rateLabel = (r: number) => fmtPct(r * 100).replace(/\.0+%$/, '%').replace(/(\.\d*?)0+%$/, '$1%');

/**
 * Company explorer, right chart: latest-quarter volume against revenue, both on log scales, with
 * diagonal lines of equal take rate. A company's distance above or below a line is how much more or
 * less of each dollar it keeps; its position along the line is only its size.
 *
 * Filled circles are gross take rates (a revenue line over volume); hollow circles are net take rates
 * or margins, which sit lower by construction, so the encoding keeps the reader from comparing
 * Block's gross profit with Toast's revenue as if they were the same line.
 *
 * Driven by the unbound `company` param. `metric` is declared too so the group can set it freely,
 * but this chart does not change with it.
 */
export function companyScatterSpec(points: CompanyPoint[], opts: { company?: string } = {}) {
  const company = opts.company ?? defaultCompany(points);
  const { plotted, excluded } = scatterSplit(points);
  const xs = plotted.map((p) => p.volume);
  const ys = plotted.map((p) => p.revenue);
  const x0 = Math.min(...xs) / 2.2;
  const x1 = Math.max(...xs) * 2.2;
  const y0 = Math.min(...ys) / 2.2;
  const y1 = Math.max(...ys) * 2.2;
  // Each iso line is clipped to the plotted box so it never stretches the scale domains.
  const lines = ISO_RATES.flatMap((r) => {
    const a = Math.max(x0, y0 / r);
    const b = Math.min(x1, y1 / r);
    if (!(b > a)) return [];
    const label = rateLabel(r);
    return [
      { rate: label, volume: a, revenue: a * r, end: false },
      { rate: label, volume: b, revenue: b * r, end: true },
    ];
  });
  const xScale = { type: 'log', domain: [x0, x1], nice: false };
  const yScale = { type: 'log', domain: [y0, y1], nice: false };
  const moneyLabel = "'$' + replace(format(datum.value, '~s'), 'G', 'B')";
  const x = { field: 'volume', type: 'quantitative', scale: xScale, title: 'Volume in the latest quarter (US$, log scale)', axis: { labelExpr: moneyLabel, tickCount: 5, grid: false } };
  const y = { field: 'revenue', type: 'quantitative', scale: yScale, title: 'Revenue line in the quarter (US$, log scale)', axis: { labelExpr: moneyLabel, tickCount: 5, grid: false } };
  return {
    $schema: VL_SCHEMA,
    height: 340,
    params: [
      { name: 'company', value: company },
      { name: 'metric', value: 'take_rate' },
    ],
    layer: [
      {
        data: { values: lines },
        mark: { type: 'line', strokeDash: [3, 3], strokeWidth: 1, color: tok('ink-3'), opacity: 0.6, clip: true },
        encoding: { x, y, detail: { field: 'rate', type: 'nominal' } },
      },
      {
        data: { values: lines.filter((l) => l.end) },
        mark: { type: 'text', align: 'right', baseline: 'bottom', dx: -2, dy: -3, fontSize: 10, color: tok('ink-3') },
        encoding: { x, y, text: { field: 'rate', type: 'nominal' } },
      },
      {
        data: { values: plotted.map((p) => ({ ...p, gross: p.ratio_kind === 'gross_take_rate' })) },
        layer: [
          {
            transform: [{ calculate: 'datum.company == company', as: 'is_sel' }],
            mark: { type: 'point', shape: 'circle', filled: true, strokeWidth: 1.5 },
            encoding: {
              x, y,
              size: { condition: { test: 'datum.is_sel', value: 130 }, value: 60 },
              color: { condition: { test: 'datum.is_sel', value: series.a }, value: series.neutral },
              stroke: { condition: { test: 'datum.is_sel', value: series.a }, value: series.neutral },
              fillOpacity: { condition: { test: 'datum.gross', value: 0.9 }, value: 0 },
              opacity: { value: 1 },
              order: { field: 'is_sel', type: 'ordinal' },
              tooltip: [
                { field: 'company', type: 'nominal', title: 'Company' },
                { field: 'period', type: 'nominal', title: 'Period' },
                { field: 'volume', type: 'quantitative', title: 'Volume (US$)', format: '$.3~s' },
                { field: 'revenue', type: 'quantitative', title: 'Revenue line (US$)', format: '$.3~s' },
                { field: 'take_rate_pct', type: 'quantitative', title: 'Take rate (%)', format: '.2f' },
                { field: 'ratio_kind_label', type: 'nominal', title: 'Ratio' },
                { field: 'numerator_metric', type: 'nominal', title: 'Revenue line' },
              ],
            },
          },
          {
            transform: [{ filter: 'datum.company == company' }],
            mark: { type: 'text', align: 'left', dx: 9, dy: -2, fontSize: 11, fontWeight: 'bold', color: series.a },
            encoding: { x, y, text: { field: 'company', type: 'nominal' } },
          },
        ],
      },
      {
        // The reason, in words, when the selected company cannot be placed on dollar-quarter axes.
        data: { values: excluded },
        transform: [{ filter: 'datum.company == company' }],
        mark: { type: 'text', align: 'left', baseline: 'top', fontSize: 12, color: tok('ink-2'), limit: { expr: 'width - 8' } },
        encoding: { x: { value: 6 }, y: { value: 6 }, text: { field: 'reason', type: 'nominal' } },
      },
    ],
  };
}

/** Finding for the scatter's figure title, computed from the plotted points. */
export function companyScatterFinding(points: CompanyPoint[]) {
  const { plotted, excluded } = scatterSplit(points);
  const gross = plotted.filter((p) => p.ratio_kind === 'gross_take_rate').sort((a, b) => a.take_rate_pct - b.take_rate_pct);
  const lo = gross[0];
  const hi = gross[gross.length - 1];
  const spread = lo && hi ? hi.take_rate_pct / lo.take_rate_pct : null;
  const title = lo && hi && spread
    ? `Gross take rates run from ${fmtPct(lo.take_rate_pct)} (${lo.company}) to ${fmtPct(hi.take_rate_pct)} (${hi.company}), a ${Math.round(spread)}-fold range`
    : 'Volume against revenue, latest quarter';
  return { title, plotted: plotted.length, excluded, low: lo ?? null, high: hi ?? null, spread };
}

/** Subtitle for the scatter: metric, unit, scale, encoding and what was left out. */
export function companyScatterSubtitle(points: CompanyPoint[]): string {
  const { plotted, excluded } = scatterSplit(points);
  const lines = ISO_RATES.map((r) => rateLabel(r)).join(', ');
  const left = excluded.length
    ? ` ${excluded.length} ${excluded.length === 1 ? 'company is' : 'companies are'} left off (${excluded.map((e) => e.company).join(', ')}): other currency or no quarterly filing.`
    : '';
  return `Filed volume against the filed revenue line for each company's latest quarter, ${plotted.length} companies, US dollars, log scale on both axes. `
    + `Dashed diagonals mark take rates of ${lines}. Filled circles are gross take rates; hollow ones are net take rates or margins.${left}`;
}
