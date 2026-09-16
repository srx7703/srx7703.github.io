import { VL_SCHEMA, series, tok } from '../theme';
import type { RevisionRow } from './types';

/**
 * The published consensus as it stood, for the listings with the deepest coverage.
 *
 * For the A-shares this is rebuilt from East Money's per-broker report dates, so a rising line
 * combines two things: brokers changing their minds, and brokers starting to cover the stock at all.
 * The page says so, and the tooltip carries the date so the reader can see how young the series is.
 */
export function consensusSeriesSpec(rows: RevisionRow[], tickers: string[], year: number) {
  const wanted = new Set(tickers);
  const data = rows.filter((r) => wanted.has(r.ticker) && r.year === year);
  return {
    $schema: VL_SCHEMA,
    height: 260,
    data: { values: data },
    transform: [{ calculate: "utcFormat(toDate(datum.as_of), '%b %d, %Y')", as: 'as_of_label' }],
    encoding: {
      x: {
        field: 'as_of', type: 'temporal', title: null, scale: { type: 'utc' },
        axis: { format: '%b %y', grid: false, labelAngle: 0, tickCount: 6 },
      },
      y: {
        field: 'eps', type: 'quantitative', title: `Consensus EPS for ${year}, local currency`,
        scale: { zero: false }, axis: { tickCount: 5 },
      },
      color: {
        field: 'name', type: 'nominal',
        scale: { range: [series.a, series.b, series.c, series.polymarket, series.kalshi, series.neutral] },
        legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle', labelLimit: 140 },
      },
      tooltip: [
        { field: 'name', type: 'nominal', title: 'Company' },
        { field: 'as_of_label', type: 'nominal', title: 'Consensus as of' },
        { field: 'eps', type: 'quantitative', title: 'EPS', format: '.2f' },
      ],
    },
    layer: [
      { mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round' } },
      { mark: { type: 'point', filled: true, size: 45, stroke: tok('surface'), strokeWidth: 1.5 } },
    ],
  };
}
