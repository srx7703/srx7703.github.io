import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyRow, PURITY_DOMAIN, priced, purityOf } from './types';

/**
 * Forward PE against the growth the consensus expects. A multiple only means something next to the
 * growth it is paying for, so this is the chart that separates "expensive" from "expensive for a reason".
 */
export function peGrowthScatterSpec(rows: CompanyRow[]) {
  const plotted = priced(rows)
    .filter((r) => r.growth != null)
    .map((r) => ({
      ticker: r.ticker,
      name: r.name,
      pe: r.fwd_pe_2026,
      growth: r.growth,
      coverage: r.coverage,
      n_analysts: r.n_analysts,
      purity: purityOf(r),
      thin: r.coverage === 'thin',
    }));
  const tooltip = [
    { field: 'name', type: 'nominal', title: 'Company' },
    { field: 'ticker', type: 'nominal', title: 'Listing' },
    { field: 'pe', type: 'quantitative', title: 'Forward PE, 2026E', format: '.1f' },
    { field: 'growth', type: 'quantitative', title: '2026E to 2027E EPS growth', format: '+.1%' },
    { field: 'n_analysts', type: 'quantitative', title: 'Contributing analysts' },
    { field: 'purity', type: 'nominal', title: 'Exposure' },
  ];
  return {
    $schema: VL_SCHEMA,
    height: 360,
    layer: [
      {
        data: { values: plotted },
        mark: { type: 'point', filled: true, size: 95, stroke: tok('surface'), strokeWidth: 2, clip: true },
        encoding: {
          x: {
            field: 'growth', type: 'quantitative', title: 'Consensus EPS growth, 2026E to 2027E',
            axis: { format: '+.0%', tickCount: 6 },
          },
          y: {
            field: 'pe', type: 'quantitative', title: 'Forward PE on calendar-2026 consensus',
            scale: { type: 'log', nice: false }, axis: { tickCount: 6, format: '.0f' },
          },
          color: {
            field: 'purity', type: 'nominal', scale: { domain: PURITY_DOMAIN, range: [series.a, series.neutral] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' },
          },
          opacity: { condition: { test: 'datum.thin', value: 0.4 }, value: 1 },
          tooltip,
        },
      },
      {
        data: { values: plotted },
        mark: { type: 'text', align: 'left', dx: 8, dy: -1, fontSize: 10, color: tok('ink-2'), clip: true },
        encoding: {
          x: { field: 'growth', type: 'quantitative' },
          y: { field: 'pe', type: 'quantitative' },
          text: { field: 'ticker' },
        },
      },
    ],
  };
}
