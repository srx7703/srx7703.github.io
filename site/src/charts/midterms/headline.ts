import series from '@data/marts/predmarkets/midterms_headline.json';
import { PLATFORM_DOMAIN, PLATFORM_LABEL_EXPR, PLATFORM_RANGE, VL_SCHEMA, tok } from '../theme';

type Row = { snapshot_ts: string; key: string; platform: string; prob: number | null };

const KEY_BY_CHAMBER: Record<string, string> = { House: 'house_dem', Senate: 'senate_dem' };

/** Democratic-control probability over time, one line per platform, for one chamber. */
export function headlineSpec(chamber: 'House' | 'Senate', showLegend = true, electionDay = '2026-11-03') {
  const key = KEY_BY_CHAMBER[chamber];
  const rows = (series as Row[])
    .filter((r) => r.key === key && r.prob != null)
    .map((r) => ({ ts: r.snapshot_ts, prob: r.prob, platform: r.platform }));
  const ts = rows.map((r) => new Date(r.ts).getTime());
  const spanDays = ts.length ? (Math.max(...ts) - Math.min(...ts)) / 86400000 : 0;
  const xFormat = spanDays < 3 ? '%b %d %H:%M' : '%b %d';
  const tooltip = [
    { field: 'ts_utc', type: 'nominal', title: 'Snapshot (UTC)' },
    { field: 'platform', type: 'nominal', title: 'Platform' },
    { field: 'prob', type: 'quantitative', title: 'Probability', format: '.1%' },
  ];
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    transform: [{ calculate: "utcFormat(toDate(datum.ts), '%b %d, %H:%M')", as: 'ts_utc' }],
    height: 240,
    encoding: {
      x: { field: 'ts', type: 'temporal', title: null, scale: { type: 'utc' }, axis: { format: xFormat, grid: false, labelAngle: 0, tickCount: 5 } },
      y: {
        field: 'prob', type: 'quantitative', title: `Democrats win the ${chamber}`,
        scale: { domain: [0, 1] }, axis: { format: '.0%', tickCount: 5, gridDash: [0] },
      },
      color: {
        field: 'platform', type: 'nominal',
        scale: { domain: PLATFORM_DOMAIN, range: PLATFORM_RANGE },
        legend: showLegend ? { title: null, orient: 'top', direction: 'horizontal', labelExpr: PLATFORM_LABEL_EXPR, symbolType: 'circle' } : null,
      },
    },
    layer: [
      ...(spanDays >= 14 ? [{
        data: { values: [{ ts: `${electionDay}T00:00:00Z`, label: 'Election day' }] },
        mark: { type: 'rule', strokeDash: [4, 4], color: tok('ink-3') },
        encoding: { x: { field: 'ts', type: 'temporal' }, color: { value: tok('ink-3') } },
      }, {
        data: { values: [{ ts: `${electionDay}T00:00:00Z`, label: 'Election day' }] },
        mark: { type: 'text', align: 'right', dx: -4, dy: -100, fontSize: 11, color: tok('ink-3') },
        encoding: { x: { field: 'ts', type: 'temporal' }, text: { field: 'label' } },
      }] : []),
      { mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round' } },
      {
        mark: { type: 'point', filled: true, size: 64, stroke: tok('surface'), strokeWidth: 2 },
        encoding: { tooltip },
      },
      {
        params: [{ name: 'hover', select: { type: 'point', fields: ['ts'], nearest: true, on: 'pointerover', clear: 'pointerout' } }],
        mark: { type: 'rule', color: tok('ink-3'), strokeDash: [2, 3] },
        encoding: {
          x: { field: 'ts', type: 'temporal' },
          y: { value: 0 },
          opacity: { condition: { param: 'hover', empty: false, value: 0.9 }, value: 0 },
          color: { value: tok('ink-3') },
          tooltip,
        },
      },
    ],
  };
}
