import { SIDE_DOMAIN, SIDE_LABEL_EXPR, SIDE_RANGE, VL_SCHEMA, tok } from '../theme';

/** Daily cut / hold / hike probabilities for one meeting on one platform (data loaded by URL). */
export function fomcHistorySpec(
  meeting: string,
  platform: 'polymarket' | 'kalshi',
  showLegend = true,
  decisionDate?: string,
  opts: { height?: number; title?: string; yTitle?: string } = {},
) {
  const label = platform === 'polymarket' ? 'Polymarket' : 'Kalshi';
  const tooltip = [
    { field: 'date_utc', type: 'nominal', title: 'Date' },
    { field: 'side', type: 'nominal', title: 'Outcome' },
    { field: 'prob', type: 'quantitative', title: 'Probability', format: '.1%' },
  ];
  return {
    $schema: VL_SCHEMA,
    data: { url: '/data/marts/predmarkets/fomc_history.json' },
    transform: [
      { filter: `datum.meeting == '${meeting}' && datum.platform == '${platform}'` },
      { calculate: 'toDate(datum.date)', as: 'date' },
      { calculate: "utcFormat(datum.date, '%b %d, %Y')", as: 'date_utc' },
    ],
    height: opts.height ?? 240,
    ...(opts.title ? { title: { text: opts.title, anchor: 'start', fontSize: 13, fontWeight: 'normal', color: tok('ink'), offset: 6 } } : {}),
    encoding: {
      x: { field: 'date', type: 'temporal', title: null, scale: { type: 'utc' }, axis: { format: '%b %y', grid: false, labelAngle: 0, tickCount: 5 } },
      y: { field: 'prob', type: 'quantitative', title: opts.yTitle ?? `${label}: probability`, scale: { domain: [0, 1] }, axis: { format: '.0%', tickCount: 5 } },
      color: {
        field: 'side', type: 'nominal', scale: { domain: SIDE_DOMAIN, range: SIDE_RANGE },
        legend: showLegend ? { title: null, orient: 'top', direction: 'horizontal', labelExpr: SIDE_LABEL_EXPR, symbolType: 'circle' } : null,
      },
    },
    layer: [
      ...(decisionDate ? [{
        data: { values: [{ date: `${decisionDate}T00:00:00Z`, label: 'Decision' }] },
        mark: { type: 'rule', strokeDash: [4, 4] },
        encoding: { x: { field: 'date', type: 'temporal' }, color: { value: tok('ink-3') } },
      }] : []),
      // one continuous line: a gap (days on which not every outcome had a usable price) is joined straight
      // from the last usable day to the next; the page's method section says so
      { mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round', clip: true } },
      {
        transform: [{ joinaggregate: [{ op: 'max', field: 'date', as: 'last' }] }, { filter: 'datum.date == datum.last' }],
        mark: { type: 'point', filled: true, size: 64, stroke: tok('surface'), strokeWidth: 2 },
        encoding: { tooltip },
      },
      {
        // last values, nudged apart by rank so two close probabilities do not print on top of each other
        transform: [
          { joinaggregate: [{ op: 'max', field: 'date', as: 'last' }] },
          { filter: 'datum.date == datum.last' },
          { window: [{ op: 'row_number', as: 'rk' }], sort: [{ field: 'prob', order: 'descending' }] },
          { joinaggregate: [{ op: 'count', as: 'n' }] },
          { calculate: '(datum.rk - (datum.n + 1) / 2) * 12', as: 'off' },
        ],
        mark: { type: 'text', align: 'left', dx: 8, dy: { expr: 'datum.off' }, fontSize: 12, color: tok('ink-2') },
        encoding: { text: { field: 'prob', type: 'quantitative', format: '.0%' } },
      },
      {
        params: [{ name: 'hover', select: { type: 'point', fields: ['date'], nearest: true, on: 'pointerover', clear: 'pointerout' } }],
        mark: { type: 'rule', strokeDash: [2, 3] },
        encoding: {
          x: { field: 'date', type: 'temporal' },
          y: { value: 0 },
          color: { value: tok('ink-3') },
          opacity: { condition: { param: 'hover', empty: false, value: 0.9 }, value: 0 },
          tooltip,
        },
      },
    ],
  };
}
