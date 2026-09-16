import { VL_SCHEMA, series, tok } from '../theme';
import type { RevisionRow } from './types';

/**
 * Non-semantic company series use the generic slots only (docs/CHART_RULES.md rules 9 and 10).
 * Three is the whole supply: `--series-c` and `--series-kalshi` are the same hex on both surfaces
 * (#15966a light, #199e70 dark), and the platform and party slots carry a meaning of their own, so a
 * five-slot range drew two companies in one colour with one legend swatch between them.
 */
const SERIES_RANGE = [series.a, series.b, series.c];

/** How many listings the consensus chart can distinguish. The page slices its own list to this. */
export const CONSENSUS_MAX_SERIES = SERIES_RANGE.length;

/**
 * The published consensus as it stood, for the listings with the deepest coverage.
 *
 * For the A-shares this is rebuilt from East Money's per-broker report dates, so a rising line
 * combines two things: brokers changing their minds, and brokers starting to cover the stock at all.
 * The page says so, and the tooltip carries the date so the reader can see how young the series is.
 */
export function consensusSeriesSpec(rows: RevisionRow[], tickers: string[], year: number) {
  // Never plot more lines than there are pairwise-distinct colours, whatever the page passes.
  const shown = tickers.slice(0, CONSENSUS_MAX_SERIES);
  const wanted = new Set(shown);
  const data = rows.filter((r) => wanted.has(r.ticker) && r.year === year);
  // Fix the legend order to the order the page ranked the listings in (deepest coverage first) rather
  // than to whichever row happens to come first in the mart, so a company keeps its colour between runs.
  const nameOf = new Map(data.map((r) => [r.ticker, r.name]));
  const domain = shown.map((t) => nameOf.get(t)).filter((n): n is string => n != null);
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
        scale: { domain, range: SERIES_RANGE.slice(0, Math.max(1, domain.length)) },
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
