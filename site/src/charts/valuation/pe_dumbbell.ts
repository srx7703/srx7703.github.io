import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyRow, PURITY_DOMAIN, priced, purityOf } from './types';

/**
 * Calendar-2026 to calendar-2027 forward PE, one dumbbell per listing, most expensive first.
 * Listings with no meaningful multiple are not plotted: a loss-making company has no PE, and drawing
 * it at zero or off the axis would read as "cheap". They are listed in the table on the page instead.
 */
export function peDumbbellSpec(rows: CompanyRow[]) {
  const plotted = priced(rows)
    .slice()
    .sort((a, b) => (b.fwd_pe_2026 as number) - (a.fwd_pe_2026 as number))
    .map((r) => ({
      label: r.ticker,
      name: r.name,
      pe26: r.fwd_pe_2026,
      pe27: r.fwd_pe_2027,
      growth: r.growth,
      coverage: r.coverage,
      n_analysts: r.n_analysts,
      purity: purityOf(r),
    }));
  const long = plotted.flatMap((r) => [
    { ...r, year: '2026E', pe: r.pe26 },
    ...(r.pe27 != null ? [{ ...r, year: '2027E', pe: r.pe27 }] : []),
  ]);
  const tooltip = [
    { field: 'name', type: 'nominal', title: 'Company' },
    { field: 'label', type: 'nominal', title: 'Listing' },
    { field: 'year', type: 'nominal', title: 'Calendar year' },
    { field: 'pe', type: 'quantitative', title: 'Forward PE', format: '.1f' },
    { field: 'growth', type: 'quantitative', title: '2026E to 2027E EPS growth', format: '+.1%' },
    { field: 'n_analysts', type: 'quantitative', title: 'Contributing analysts' },
  ];
  return {
    $schema: VL_SCHEMA,
    height: Math.max(180, plotted.length * 17),
    encoding: {
      y: {
        field: 'label', type: 'nominal', sort: plotted.map((r) => r.label), title: null,
        axis: { labelFontSize: 10, labelLimit: 90 },
      },
    },
    layer: [
      {
        data: { values: plotted.filter((r) => r.pe27 != null) },
        mark: { type: 'rule', strokeWidth: 2, color: series.neutral },
        encoding: {
          x: {
            field: 'pe26', type: 'quantitative', title: 'Forward PE (price over calendar-year consensus EPS)',
            scale: { type: 'log', nice: false }, axis: { tickCount: 6, format: '.0f' },
          },
          x2: { field: 'pe27' },
        },
      },
      {
        data: { values: long },
        mark: { type: 'point', filled: true, size: 70, stroke: tok('surface'), strokeWidth: 1.5 },
        encoding: {
          x: { field: 'pe', type: 'quantitative' },
          color: {
            field: 'year', type: 'nominal', scale: { domain: ['2026E', '2027E'], range: [series.neutral, series.a] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' },
          },
          opacity: { condition: { test: "datum.purity == '" + PURITY_DOMAIN[1] + "'", value: 0.45 }, value: 1 },
          tooltip,
        },
      },
    ],
  };
}
