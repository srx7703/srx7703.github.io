import { VL_SCHEMA, series, tok } from '../theme';
import { lit, lookupExpr, reasonTitle } from './adoption_shared';
import {
  type LedgerPoint, type LedgerMetric, LEDGER_METRICS, LEDGER_PARAMS,
} from './ledger_data';

const NAME = 'ledger_src';

const METRIC_NAME: Record<string, string> = Object.fromEntries(LEDGER_METRICS.map((m) => [m.value, m.label.toLowerCase()]));

/** Axis label for the current `metric` param: dollars as $B/$T, take rates as %, counts as 70K. */
const AXIS_LABEL_EXPR = "metric == 'take_rate' ? format(datum.value, '.2~f') + '%' : metric == 'customers' ? format(datum.value, '.2~s') : "
  + "replace(replace(format(datum.value, '$.2~s'), 'G', 'B'), 'P', 'Q')";

/** 1-2-5 ticks across every decade a ledger family can span (0.01% to $10T); Vega keeps those in the domain. */
const LOG_TICKS = Array.from({ length: 16 }, (_, i) => i - 2).flatMap((k) => [1, 2, 5].map((m) => +(m * 10 ** k).toPrecision(1)));

/**
 * Which side of its mark the latest figure's label goes, decided from the room on each side at the
 * current plot width (the scale has 14px padding at each end; the right padding adds 16px of room).
 * The label goes right if it fits there, else left if it fits there, else to the roomier side; when
 * the full tag does not fit on the chosen side, the ' · <month>' suffix is dropped (it is in the
 * tooltip), so a date is never cut mid-word.
 */
const LABEL_SIDE = [
  { filter: 'datum.latest && datum.start != null && datum.end != null' },
  { calculate: 'width + 16 - (14 + datum.f_end * (width - 28)) - 4', as: 'room_r' },
  { calculate: '14 + datum.f_start * (width - 28) - 4', as: 'room_l' },
  { calculate: 'datum.room_r >= datum.w_tag || (datum.room_l < datum.w_tag && datum.room_r >= datum.room_l)', as: 'lab_right' },
  { calculate: '(datum.lab_right ? datum.room_r : datum.room_l) >= datum.w_tag ? datum.tag : datum.label', as: 'lab_text' },
];

/**
 * Private ledger, interval chart: every public number about each private company, one row per
 * company, in the family the `metric` control picks (revenue, volume, take rate, valuation,
 * customers), on a log axis because one family spans companies a hundred times apart in size.
 * Rows are ordered by each company's latest figure. The latest figure is drawn full size and labelled
 * with the number as stated and its month; earlier figures stay on the row, smaller and faded, so a
 * series of rounds reads as a progression (dates are in the tooltips and in the event timeline).
 *
 * How each qualifier is drawn (see ledger_data.ts): `=` filled point; `~` hollow point; `>` a lower
 * bound, a rule from the stated floor to an open arrowhead pointing right (the arrow's length is
 * drawing only, it asserts nothing about the true value); `<` the same pointing left; a closed
 * interval, a capped rule with its estimate as a point if one exists. Circles are company-stated
 * figures and valuation events; diamonds are counterparty filings and bounds the pipeline derives.
 *
 * Dashboard group `ledger`, params `company` (`all` or a company; the rest are greyed) and `metric`.
 * A company with nothing in the selected family keeps the chart and says so in words.
 */
export function ledgerIntervalSpec(points: LedgerPoint[], opts: { company?: string; metric?: LedgerMetric } = {}) {
  const lastDate = new Map<string, string>();
  points.forEach((p) => {
    const k = `${p.company}|${p.family}`;
    if (!lastDate.has(k) || p.date > (lastDate.get(k) as string)) lastDate.set(k, p.date);
  });
  // Each family's extent on the log axis places a mark as a fraction of the plot, so LABEL_SIDE can
  // choose the side of its label from the room at the rendered width (no label runs off on a phone).
  const extent = new Map<string, [number, number]>();
  points.forEach((p) => {
    const vs = [p.lo, p.hi, p.point].filter((v): v is number => v != null && v > 0).map(Math.log);
    const e = extent.get(p.family) ?? [Infinity, -Infinity];
    extent.set(p.family, [Math.min(e[0], ...vs), Math.max(e[1], ...vs)]);
  });
  const values = points.map((p) => {
    const key = `${p.company}|${p.family}`;
    const latest = p.date === lastDate.get(key);
    const month = new Date(`${p.date}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', year: 'numeric' });
    const start = p.lo ?? p.point; const end = p.hi ?? p.point;
    const [a, b] = extent.get(p.family) as [number, number];
    const fracOf = (v: number | null) => (b > a && v ? (Math.log(v) - a) / (b - a) : 0);
    const tag = `${p.label} · ${month}`;
    return {
      ...p, key, latest, level: latest ? (p.point ?? p.lo) : null, start, end,
      f_start: fracOf(start), f_end: fracOf(end), tag,
      // Estimated rendered widths at 10.5px Plex Sans (about 0.5em a character), for the side choice.
      w_tag: Math.ceil(tag.length * 5.3),
    };
  });
  const on = "(company == 'all' || datum.company == company)";
  const y = {
    field: 'company', type: 'nominal', title: null,
    sort: { field: 'level', op: 'max', order: 'descending' },
    axis: { labelLimit: 110, ticks: false, domain: false, grid: true, labelFontSize: 11.5 },
  };
  const xBase = {
    type: 'quantitative', title: null,
    scale: { type: 'log', nice: false, padding: 14 },
    axis: { labelExpr: AXIS_LABEL_EXPR, values: LOG_TICKS, labelOverlap: 'greedy' },
  };
  const color = { condition: { test: on, value: series.a }, value: series.neutral };
  const opacity = {
    condition: [{ test: `${on} && datum.latest`, value: 1 }, { test: on, value: 0.4 }, { test: 'datum.latest', value: 0.4 }],
    value: 0.18,
  };
  const size = (latest: number, earlier: number) => ({ condition: { test: 'datum.latest', value: latest }, value: earlier });
  const tooltip = [
    { field: 'company', type: 'nominal', title: 'Company' },
    { field: 'metric_label', type: 'nominal', title: 'Figure' },
    { field: 'label', type: 'nominal', title: 'As stated' },
    { field: 'date', type: 'temporal', title: 'As of', format: '%b %d, %Y' },
    { field: 'kind_label', type: 'nominal', title: 'Kind' },
    { field: 'note', type: 'nominal', title: 'Caveat' },
  ];
  const reason = [
    `!indata(${lit(NAME)}, 'family', metric) ? 'No private company has a public\\n' + ${lookupExpr(METRIC_NAME, 'metric')} + ' figure in the ledger.'`,
    `company != 'all' && !indata(${lit(NAME)}, 'key', company + '|' + metric) ? company + ' has no public\\n' + ${lookupExpr(METRIC_NAME, 'metric')} + ' figure in the ledger;\\nothers are shown in grey.'`,
    "''",
  ].join(' : ');
  return {
    $schema: VL_SCHEMA,
    title: reasonTitle(reason),
    height: { step: 38 },
    params: LEDGER_PARAMS(opts),
    data: { values, name: NAME },
    layer: [
      {
        transform: [{ filter: 'datum.family == metric' }],
        encoding: { y },
        resolve: { scale: { shape: 'independent' } },
        layer: [
          {
            // Interval and bound rules.
            transform: [{ filter: 'datum.lo != null' }],
            mark: { type: 'rule' },
            encoding: {
              x: { ...xBase, field: 'lo' }, x2: { field: 'hi' }, color, opacity, tooltip,
              strokeWidth: size(2.2, 1.4),
            },
          },
          {
            // Caps on closed intervals.
            transform: [{ filter: 'datum.interval' }, { fold: ['lo', 'hi'], as: ['side', 'cap'] }],
            mark: { type: 'tick', orient: 'vertical', thickness: 2 },
            encoding: { x: { ...xBase, field: 'cap' }, color, opacity, tooltip, size: size(12, 8) },
          },
          {
            // Open arrowheads on one-sided bounds.
            transform: [{ filter: 'datum.arrow != null' }],
            mark: { type: 'point', filled: false, strokeWidth: 1.6 },
            encoding: {
              x: { ...xBase, field: 'arrow_at' }, color, opacity, tooltip, size: size(70, 34),
              shape: { field: 'arrow', type: 'nominal', scale: { domain: ['up', 'down'], range: ['triangle-right', 'triangle-left'] }, legend: null },
            },
          },
          {
            // Stated points: filled unless approximate.
            transform: [{ filter: 'datum.point != null' }],
            mark: { type: 'point', strokeWidth: 1.6 },
            encoding: {
              x: { ...xBase, field: 'point' }, opacity, tooltip, size: size(70, 30),
              stroke: color,
              fill: { condition: { test: `!datum.approx && ${on}`, value: series.a }, value: 'transparent' },
              shape: { field: 'shape', type: 'nominal', scale: { domain: ['stated', 'derived'], range: ['circle', 'diamond'] }, legend: null },
            },
          },
          {
            // The latest figure as stated, above its mark and running right ...
            transform: [...LABEL_SIDE, { filter: 'datum.lab_right' }],
            mark: { type: 'text', align: 'left', baseline: 'bottom', dx: 2, dy: -7, fontSize: 10.5 },
            encoding: {
              x: { ...xBase, field: 'end' }, text: { field: 'lab_text', type: 'nominal' },
              color: { condition: { test: on, value: tok('ink-2') }, value: series.neutral },
            },
          },
          {
            // ... or running left when there is more room on that side of the mark.
            transform: [...LABEL_SIDE, { filter: '!datum.lab_right' }],
            mark: { type: 'text', align: 'right', baseline: 'bottom', dx: -2, dy: -7, fontSize: 10.5 },
            encoding: {
              x: { ...xBase, field: 'start' }, text: { field: 'lab_text', type: 'nominal' },
              color: { condition: { test: on, value: tok('ink-2') }, value: series.neutral },
            },
          },
        ],
      },
    ],
    padding: { right: 20 },
  };
}

/**
 * Finding for the default view (revenue): the company whose latest revenue figure is largest, with
 * the number as stated (bounds keep their qualifier). Computed from the points.
 */
export function ledgerIntervalFinding(points: LedgerPoint[], metric: LedgerMetric = 'revenue') {
  const fam = points.filter((p) => p.family === metric);
  const latest = new Map<string, LedgerPoint>();
  for (const p of fam) {
    const prev = latest.get(p.company);
    if (!prev || p.date > prev.date) latest.set(p.company, p);
  }
  const level = (p: LedgerPoint) => p.point ?? p.lo ?? 0;
  const ranked = [...latest.values()].sort((a, b) => level(b) - level(a));
  // Only company-stated figures are ranked: a derived band or a counterparty filing is drawn, never
  // named as a company's figure in the title.
  const stated = ranked.filter((p) => p.shape === 'stated');
  const top = stated[0];
  const name = METRIC_NAME[metric] ?? metric;
  if (!ranked.length) return { title: `No private company has a public ${name} figure`, companies: 0, bounds: 0, derived: 0, top: null };
  const bounds = ranked.filter((p) => p.arrow != null || p.interval).length;
  const derived = ranked.length - stated.length;
  const tail = `${bounds} of ${ranked.length} latest figures are bounds, not points`
    + (derived ? `, and ${derived} ${derived === 1 ? 'is' : 'are'} derived or from a counterparty and not ranked` : '');
  if (!top) return { title: `No private company states a ${name} figure itself; ${tail}`, companies: ranked.length, bounds, derived, top: null };
  const month = new Date(`${top.date}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', year: 'numeric' });
  return {
    title: `${top.company} states the largest latest ${name} figure among the private companies: ${top.label}, ${top.metric_label.toLowerCase()}, as of ${month}; ${tail}`,
    companies: ranked.length, bounds, derived, top,
  };
}

export function ledgerIntervalSubtitle(): string {
  return 'Every public number about each private company, by the date it applies to, log scale. Filled point: stated exactly; hollow: approximate (~); '
    + 'rule to an open arrow: a lower bound (>), whose length is drawing only; capped rule: a range. Circles are company statements and priced events, diamonds are counterparty filings and derived bounds.';
}

