import { VL_SCHEMA, series, tok } from '../theme';
import { type CompanyRow, PURITY_DOMAIN, isThin, peScale, priced, purityOf, spansOrders } from './types';

const YEAR_DOMAIN = ['2026E', '2027E'];
const YEAR_RANGE = [series.neutral, series.a];

/** Every PE the dumbbell's axis has to hold, so the axis type and the subtitle are decided from one list. */
export const dumbbellPeValues = (rows: CompanyRow[]) =>
  priced(rows).flatMap((r) => [r.fwd_pe_2026, r.fwd_pe_2027]);

/** True when the chart draws a log axis, so the page can say "log scale" only when it is one (rule 13). */
export const dumbbellUsesLog = (rows: CompanyRow[]) => spansOrders(dumbbellPeValues(rows));

/**
 * Calendar-2026 to calendar-2027 forward PE, one dumbbell per listing, most expensive first.
 * Listings with no meaningful multiple are not plotted: a loss-making company has no PE, and drawing
 * it at zero or off the axis would read as "cheap". They are listed in the table on the page instead.
 *
 * Encoding follows docs/CHART_RULES.md rule 14 literally, and identically to the scatter beside it:
 * exposure is the *mark* — a company whose whole business is the track is filled, a diversified one is
 * hollow — and opacity is reserved for thin analyst coverage. The two cues used to be the same faded
 * mark meaning two different things two figures apart.
 */
export function peDumbbellSpec(rows: CompanyRow[]) {
  const plotted = priced(rows)
    .slice()
    .sort((a, b) => (b.fwd_pe_2026 as number) - (a.fwd_pe_2026 as number))
    .map((r) => ({
      label: r.name,
      name: r.name,
      ticker: r.ticker,
      pe26: r.fwd_pe_2026,
      pe27: r.fwd_pe_2027,
      growth: r.growth,
      coverage: r.coverage,
      n_analysts: r.n_analysts,
      purity: purityOf(r),
      thin: isThin(r),
    }));
  // The fill colour is resolved per row rather than through a second scale on the same field: a
  // conditional `fill` sharing the year scale merges into the stroke legend and disables it, which
  // silently removed the 2026E/2027E key from the chart.
  const fillFor = (purity: string, year: string) =>
    purity === PURITY_DOMAIN[0] ? YEAR_RANGE[YEAR_DOMAIN.indexOf(year)] : tok('bg');
  const long = plotted.flatMap((r) => [
    { ...r, year: '2026E', pe: r.pe26, fill_color: fillFor(r.purity, '2026E') },
    ...(r.pe27 != null ? [{ ...r, year: '2027E', pe: r.pe27, fill_color: fillFor(r.purity, '2027E') }] : []),
  ]);
  const tooltip = [
    { field: 'ticker', type: 'nominal', title: 'Listing' },
    { field: 'year', type: 'nominal', title: 'Calendar year' },
    { field: 'pe', type: 'quantitative', title: 'Forward PE', format: '.1f' },
    { field: 'growth', type: 'quantitative', title: '2026E to 2027E EPS growth', format: '+.1%' },
    { field: 'n_analysts', type: 'quantitative', title: 'Contributing analysts' },
    { field: 'purity', type: 'nominal', title: 'Exposure' },
  ];
  return {
    $schema: VL_SCHEMA,
    height: Math.max(180, plotted.length * 17),
    encoding: {
      y: {
        field: 'label', type: 'nominal', sort: plotted.map((r) => r.label), title: null,
        // 160px fits the longest name in the pool (Sanxiang Advanced Materials) at this size;
        // the 90px default truncated a third of the axis to "Everbright Photon…".
        axis: { labelFontSize: 10, labelLimit: 160 },
      },
    },
    layer: [
      {
        data: { values: plotted.filter((r) => r.pe27 != null) },
        mark: { type: 'rule', strokeWidth: 2, color: series.neutral },
        encoding: {
          x: {
            field: 'pe26', type: 'quantitative', title: 'Forward PE (price over calendar-year consensus EPS)',
            scale: peScale(dumbbellPeValues(rows)), axis: { tickCount: 6, format: '.0f' },
          },
          x2: { field: 'pe27' },
        },
      },
      {
        data: { values: long },
        // filled: false puts the year colour on the stroke; the per-row fill below is what makes a
        // pure play solid and leaves a diversified company as an outline.
        mark: { type: 'point', filled: false, size: 70, strokeWidth: 2 },
        encoding: {
          x: { field: 'pe', type: 'quantitative' },
          stroke: {
            field: 'year', type: 'nominal', scale: { domain: YEAR_DOMAIN, range: YEAR_RANGE },
            legend: {
              title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle',
              symbolStrokeWidth: 2, symbolSize: 90,
            },
          },
          fill: { field: 'fill_color', type: 'nominal', scale: null, legend: null },
          opacity: { condition: { test: 'datum.thin', value: 0.45 }, value: 1 },
          tooltip,
        },
      },
    ],
  };
}
