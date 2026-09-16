import series from '@data/marts/predmarkets/midterms_headline.json';
import { PLATFORM_DOMAIN, PLATFORM_LABEL_EXPR, PLATFORM_RANGE, VL_SCHEMA, tok } from '../theme';

type Row = { snapshot_ts: string; key: string; platform: string; prob: number | null };

const KEY_BY_CHAMBER: Record<string, string> = { House: 'house_dem', Senate: 'senate_dem' };

/** Democratic-control probability over time, one line per platform, for one chamber. */
export function headlineSpec(chamber: 'House' | 'Senate', showLegend = true) {
  const key = KEY_BY_CHAMBER[chamber];
  const rows = (series as Row[])
    .filter((r) => r.key === key && r.prob != null)
    .map((r) => ({ ts: r.snapshot_ts, prob: r.prob, platform: r.platform }));
  const tooltip = [
    { field: 'ts', type: 'temporal', title: 'Snapshot (UTC)', format: '%b %d, %H:%M' },
    { field: 'platform', type: 'nominal', title: 'Platform' },
    { field: 'prob', type: 'quantitative', title: 'Probability', format: '.1%' },
  ];
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 240,
    encoding: {
      x: { field: 'ts', type: 'temporal', title: null, axis: { format: '%b %d', grid: false, labelAngle: 0, tickCount: 6 } },
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
