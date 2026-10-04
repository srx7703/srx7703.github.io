import { VL_SCHEMA, series } from '../theme';
import {
  type Source, type WebRow, ADOPTION_PARAMS, WEB_SLICE, WEB_COMPANIES,
  namedData, reasonTitle, lookupExpr, emptyExpr, emptyReason, lit, rowsOf,
} from './adoption_shared';

const NAME = 'web_src';

/**
 * Web coverage: how many US origins load each processor's script, month by month (HTTP Archive Tech
 * Report via `webtech_q2.json`). Coverage, never share of payments: the unit is a website, not a dollar.
 *
 * The line is the 3-crawl rolling mean the evaluation plan grades (Q2); the faint dots are each crawl.
 * The mart carries Stripe and PayPal only, on two slices: US top-10k origins (`source=registered`) and
 * all US origins (`source=descriptive`), both mobile. Selecting a company the view does not track
 * dims both lines and says so; a missing mart says so in place of the chart.
 *
 * Dashboard group `adoption`, params `source` and `company` (see adoption_shared.ts).
 */
export function webCoverageSpec(source: Source<WebRow>, opts: { source?: string; company?: string } = {}) {
  const slice = lookupExpr(WEB_SLICE, 'source');
  const on = "(company == 'all' || datum.technology == company)";
  const x = { field: 'date', type: 'temporal', title: null, axis: { format: '%b %Y', tickCount: 6, labelOverlap: true } };
  const y = {
    field: 'rolling_mean_3m', type: 'quantitative', title: 'Origins detected (3-crawl mean)',
    scale: { zero: true }, axis: { format: ',.0f', tickCount: 5 },
  };
  const color = {
    field: 'technology', type: 'nominal',
    scale: { domain: WEB_COMPANIES, range: [series.a, series.b] },
    legend: null,
  };
  const reason = [
    `${emptyExpr(NAME)} ? ${lit(emptyReason('the HTTP Archive file'))}`,
    `!indata(${lit(NAME)}, 'slice', ${slice}) ? 'This slice (' + ${slice} + ')\\nis not in the coverage file.'`,
    `company != 'all' && !indata(${lit(NAME)}, 'technology', company) ? company + ' is not in the\\nHTTP Archive view, which tracks\\nStripe and PayPal only.'`,
    "''",
  ].join(' : ');
  return {
    $schema: VL_SCHEMA,
    title: reasonTitle(reason),
    height: 260,
    params: ADOPTION_PARAMS(opts),
    data: namedData(source, NAME),
    layer: [
      {
        transform: [{ filter: `datum.slice == ${slice}` }],
        layer: [
          {
            mark: { type: 'point', filled: true, size: 14 },
            encoding: {
              x, y: { ...y, field: 'origins' }, color,
              opacity: { condition: { test: on, value: 0.35 }, value: 0.1 },
            },
          },
          {
            transform: [{ filter: 'datum.rolling_mean_3m != null' }],
            mark: { type: 'line', strokeWidth: 2, interpolate: 'monotone' },
            encoding: {
              x, y, color,
              opacity: { condition: { test: on, value: 1 }, value: 0.25 },
              tooltip: [
                { field: 'technology', type: 'nominal', title: 'Technology' },
                { field: 'date', type: 'temporal', title: 'Crawl', format: '%b %Y' },
                { field: 'origins', type: 'quantitative', title: 'Origins', format: ',' },
                { field: 'rolling_mean_3m', type: 'quantitative', title: '3-crawl mean', format: ',.0f' },
                { field: 'share', type: 'quantitative', title: 'Of all origins in slice', format: '.2%' },
              ],
            },
          },
          {
            // Direct labels at each line's last crawl, in the line's colour; no legend to decode.
            transform: [
              { filter: 'datum.rolling_mean_3m != null' },
              { joinaggregate: [{ op: 'max', field: 'date', as: 'last' }], groupby: ['technology'] },
              { filter: 'datum.date == datum.last' },
            ],
            mark: { type: 'text', align: 'right', baseline: 'bottom', dy: -6, fontSize: 11, fontWeight: 600 },
            encoding: { x, y, text: { field: 'technology', type: 'nominal' }, color, opacity: { condition: { test: on, value: 1 }, value: 0.4 } },
          },
        ],
      },
    ],
  };
}

/**
 * Finding for the figure title at the default view (registered slice), computed from the rows when the
 * page has them at build time; otherwise a title that states the status (`ciRan` = facts.ci_sources.webtech).
 */
export function webCoverageFinding(source?: Source<WebRow>, slice: 'registered' | 'descriptive' | string = 'registered', ciRan = false) {
  const key = slice === 'descriptive' ? 'descriptive' : 'registered';
  const where = key === 'descriptive' ? 'US websites overall' : 'top-10k US websites';
  const rows = rowsOf(source).filter((r) => r.slice === WEB_SLICE[key] && r.rolling_mean_3m != null);
  const last = rows.reduce((m, r) => (r.date > m ? r.date : m), '');
  const at = (t: string) => rows.find((r) => r.technology === t && r.date === last)?.rolling_mean_3m ?? null;
  const s = at('Stripe');
  const p = at('PayPal');
  if (s == null || p == null) {
    // The status is the finding: say why there is no comparison yet (facts.ci_sources.webtech).
    const title = ciRan
      ? `Web coverage on ${where} is not in this build's HTTP Archive file, so Stripe and PayPal are not compared here`
      : 'Web coverage is not published yet: the first scheduled HTTP Archive run has not landed';
    return { title, ready: false, last: null, stripe: null, paypal: null };
  }
  const lead = s >= p ? 'Stripe' : 'PayPal';
  const lag = lead === 'Stripe' ? 'PayPal' : 'Stripe';
  const month = new Date(`${last}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', year: 'numeric' });
  return {
    title: `${lead} is detected on more ${where} than ${lag} (${Math.round(Math.max(s, p))} against ${Math.round(Math.min(s, p))}, ${month})`,
    ready: true, last, stripe: s, paypal: p,
  };
}

export function webCoverageSubtitle(): string {
  return 'US origins whose pages load each processor\'s script, HTTP Archive monthly crawl, mobile; line = 3-crawl rolling mean, dots = single crawls. '
    + 'Pre-registered slice: top 10k origins by traffic (Q2); broader slice: all US origins. Coverage counts websites, not dollars.';
}
