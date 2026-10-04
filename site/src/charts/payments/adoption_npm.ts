import { VL_SCHEMA, series } from '../theme';
import {
  type Source, type NpmRow, ADOPTION_PARAMS, NPM_SIDE,
  namedData, reasonLayer, lookupExpr, emptyExpr, emptyReason, lit, rowsOf,
} from './adoption_shared';

const NAME = 'npm_src';

/**
 * npm download index: monthly downloads of each company's pinned SDK set, indexed to the series'
 * common base month = 100 (`devstats_monthly.json`; the base is set in pipelines/payments/devstats.py). CI servers re-download packages on every build,
 * which inflates levels by an unknown and company-specific factor, so only the shape is read, never
 * the level: an index on a log axis, where equal slopes mean equal growth rates.
 *
 * `source=registered` draws the client-side SDKs Q1 is graded on (one series per company);
 * `source=descriptive` draws the server SDKs (one series per package, descriptive only). The selected
 * company is drawn in colour, the rest in grey; `company=all` draws every series in grey with labels.
 *
 * Dashboard group `adoption`, params `source` and `company`.
 */
export function npmIndexSpec(source: Source<NpmRow>, opts: { source?: string; company?: string } = {}) {
  const side = lookupExpr(NPM_SIDE, 'source');
  const focus = "(company != 'all' && datum.company == company)";
  const x = { field: 'period', type: 'temporal', title: null, axis: { format: '%b %Y', tickCount: 6, labelOverlap: true } };
  const y = {
    field: 'index', type: 'quantitative', title: 'Downloads, base month = 100 (log scale)',
    scale: { type: 'log' }, axis: { format: ',.0f', tickCount: 5 },
  };
  const color = { condition: { test: focus, value: series.a }, value: series.neutral };
  const reason = [
    `${emptyExpr(NAME)} ? ${lit(emptyReason('the npm download file'))}`,
    `!indata(${lit(NAME)}, 'side', ${side}) ? 'No ' + ${side} + '-side packages\\nin the download file.'`,
    `company != 'all' && !indata(${lit(NAME)}, 'company', company) ? company + ' has no pinned\\nnpm package in this tracker.'`,
    "''",
  ].join(' : ');
  return {
    $schema: VL_SCHEMA,
    height: 260,
    params: ADOPTION_PARAMS(opts),
    data: namedData(source, NAME),
    layer: [
      {
        transform: [
          { filter: `datum.side == ${side} && datum.index != null && datum.index > 0` },
          { calculate: `${focus} ? 1 : 0`, as: 'focus' },
        ],
        layer: [
          {
            mark: { type: 'line', strokeWidth: 1.6, interpolate: 'monotone' },
            encoding: {
              x, y, detail: { field: 'series', type: 'nominal' }, color,
              strokeWidth: { condition: { test: focus, value: 2.4 }, value: 1.4 },
              opacity: { condition: { test: `company == 'all' || ${focus}`, value: 1 }, value: 0.5 },
              order: { field: 'focus', type: 'quantitative' },
              tooltip: [
                { field: 'series', type: 'nominal', title: 'Series' },
                { field: 'company', type: 'nominal', title: 'Company' },
                { field: 'period', type: 'temporal', title: 'Month', format: '%b %Y' },
                { field: 'downloads', type: 'quantitative', title: 'Downloads', format: ',' },
                { field: 'index', type: 'quantitative', title: 'Index', format: ',.0f' },
              ],
            },
          },
          {
            transform: [
              { joinaggregate: [{ op: 'max', field: 'period', as: 'last' }], groupby: ['series'] },
              { filter: 'datum.period == datum.last' },
            ],
            mark: { type: 'text', align: 'left', dx: 4, fontSize: 10, limit: 120 },
            encoding: {
              x, y, text: { field: 'series', type: 'nominal' }, color,
              opacity: { condition: { test: `company == 'all' || ${focus}`, value: 1 }, value: 0.6 },
            },
          },
        ],
      },
      reasonLayer(reason),
    ],
    padding: { right: 70 },
  };
}

/** Finding for the figure title: fastest index at the last month within the slice (client SDKs by default, server SDKs for the broader slice). */
export function npmIndexFinding(source?: Source<NpmRow>, slice: 'registered' | 'descriptive' | string = 'registered') {
  const key = slice === 'descriptive' ? 'descriptive' : 'registered';
  const kind = key === 'descriptive' ? 'server SDK' : 'browser SDK';
  const rows = rowsOf(source).filter((r) => r.side === NPM_SIDE[key] && r.index != null);
  const last = rows.reduce((m, r) => (r.period > m ? r.period : m), '');
  const atLast = rows.filter((r) => r.period === last).sort((a, b) => (b.index ?? 0) - (a.index ?? 0));
  if (!atLast.length) {
    return { title: 'Developer downloads of the payment SDKs, indexed', ready: false, last: null, top: null, base: null };
  }
  const top = atLast[0];
  const month = new Date(`${last}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', year: 'numeric' });
  return {
    title: `${top.series}'s ${kind} downloads have grown fastest since the base month (index ${Math.round(top.index ?? 0)} in ${month})`,
    ready: true, last, top, base: rows.find((r) => r.series === top.series && Math.abs((r.index ?? 0) - 100) < 1e-9)?.period ?? null,
  };
}

export function npmIndexSubtitle(): string {
  return 'Monthly npm downloads of each company\'s pinned SDK packages, indexed to a common base month = 100, log scale. '
    + 'Pre-registered slice: browser SDKs (Q1); broader slice: server SDKs, one line per package. Automated builds inflate levels, so only the trend is read.';
}
