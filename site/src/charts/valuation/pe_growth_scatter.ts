import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyRow, PURITY_DOMAIN, isThin, peScale, priced, purityOf, spansOrders } from './types';

/** The listings the scatter can draw: priced, and with a growth figure to put them on the x axis. */
export const scatterRows = (rows: CompanyRow[]) => priced(rows).filter((r) => r.growth != null);

/** True when the chart draws a log axis, so the page can say "log scale" only when it is one (rule 13). */
export const scatterUsesLog = (rows: CompanyRow[]) => spansOrders(scatterRows(rows).map((r) => r.fwd_pe_2026));

/**
 * Forward PE against the growth the consensus expects. A multiple only means something next to the
 * growth it is paying for, so this is the chart that separates "expensive" from "expensive for a reason".
 *
 * Exposure is in the mark and not in the colour (docs/CHART_RULES.md rule 14), matching the dumbbell
 * above it: filled is a company whose whole business is the track, hollow is a diversified company
 * priced on everything it sells. Opacity means thin (or unpublished) analyst coverage on both charts.
 */
export function peGrowthScatterSpec(rows: CompanyRow[]) {
  const plotted = scatterRows(rows).map((r) => ({
    ticker: r.ticker,
    name: r.name,
    pe: r.fwd_pe_2026,
    growth: r.growth,
    coverage: r.coverage,
    n_analysts: r.n_analysts,
    purity: purityOf(r),
    thin: isThin(r),
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
        mark: { type: 'point', filled: false, size: 95, strokeWidth: 2, clip: true },
        encoding: {
          x: {
            field: 'growth', type: 'quantitative', title: 'Consensus EPS growth, 2026E to 2027E',
            scale: { nice: true, zero: false, padding: 12 }, axis: { format: '+.0%', tickCount: 6 },
          },
          y: {
            field: 'pe', type: 'quantitative', title: 'Forward PE on calendar-2026 consensus',
            scale: peScale(plotted.map((r) => r.pe)), axis: { tickCount: 6, format: '.0f' },
          },
          // One colour for every listing: the colour channel carries no variable here, so exposure can
          // have the mark. The hollow entry in the legend is a real outline, not an empty swatch.
          stroke: { value: series.a },
          fill: {
            field: 'purity', type: 'nominal',
            scale: { domain: PURITY_DOMAIN, range: [series.a, tok('bg')] },
            legend: {
              title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle',
              symbolStrokeColor: series.a, symbolStrokeWidth: 2, symbolSize: 110,
            },
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
