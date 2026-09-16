import series from '@data/marts/predmarkets/midterms_headline.json';
import { VL_SCHEMA, series as tokens, tok } from '../theme';

type Row = { snapshot_ts: string; key: string; platform: string; prob: number | null };

/** Polymarket minus Kalshi, in percentage points, for the Democratic-control probability of each chamber. */
export function gapSpec() {
  const byTs: Record<string, Record<string, Record<string, number>>> = {};
  for (const r of series as Row[]) {
    if (!['house_dem', 'senate_dem'].includes(r.key) || r.prob == null) continue;
    ((byTs[r.snapshot_ts] ??= {})[r.key] ??= {})[r.platform] = r.prob;
  }
  const rows = Object.entries(byTs).flatMap(([ts, keys]) =>
    Object.entries(keys)
      .filter(([, p]) => p.polymarket != null && p.kalshi != null)
      .map(([key, p]) => ({ ts, chamber: key === 'house_dem' ? 'House' : 'Senate', gap_pt: (p.polymarket - p.kalshi) * 100 })),
  );
  const t = rows.map((r) => new Date(r.ts).getTime());
  const spanDays = t.length ? (Math.max(...t) - Math.min(...t)) / 86400000 : 0;
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    transform: [{ calculate: "utcFormat(toDate(datum.ts), '%b %d, %H:%M')", as: 'ts_utc' }],
    height: 200,
    encoding: {
      x: { field: 'ts', type: 'temporal', title: null, scale: { type: 'utc' }, axis: { format: spanDays < 3 ? '%b %d %H:%M' : '%b %d', grid: false, labelAngle: 0, tickCount: 5 } },
      y: { field: 'gap_pt', type: 'quantitative', title: 'Polymarket minus Kalshi (pt)', axis: { tickCount: 5 } },
      color: { field: 'chamber', type: 'nominal', scale: { domain: ['House', 'Senate'], range: [tokens.a, tokens.b] }, legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' } },
      tooltip: [
        { field: 'ts_utc', type: 'nominal', title: 'Snapshot (UTC)' },
        { field: 'chamber', type: 'nominal', title: 'Chamber' },
        { field: 'gap_pt', type: 'quantitative', title: 'Gap (pt)', format: '+.1f' },
      ],
    },
    layer: [
      { data: { values: [{ y: 0 }] }, mark: { type: 'rule', color: tok('border-strong') }, encoding: { y: { field: 'y', type: 'quantitative' }, color: { value: tok('border-strong') } } },
      { mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round' } },
      { mark: { type: 'point', filled: true, size: 64, stroke: tok('surface'), strokeWidth: 2 } },
    ],
  };
}
