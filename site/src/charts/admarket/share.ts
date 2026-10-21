import { VL_SCHEMA, series, tok } from '../theme';

export type ShareRow = { quarter: string; company: string; value_bn: number; share: number };

/** '2026Q2' -> the quarter's last day, so a point sits where the quarter closes. */
export const quarterEnd = (q: string) => `${q.slice(0, 4)}-${['03-31', '06-30', '09-30', '12-31'][Number(q[5]) - 1]}`;

export const SHARE_GROUPS = ['Google', 'Meta', 'Amazon'];
export const OTHERS = 'Other six';
const COLORS = [series.a, series.b, series.c, series.neutral];

/** Google, Meta, Amazon and the other six summed, per quarter. */
export function shareLines(rows: ShareRow[]) {
  const by = new Map<string, Record<string, number>>();
  for (const r of rows) {
    const g = SHARE_GROUPS.includes(r.company) ? r.company : OTHERS;
    const m = by.get(r.quarter) ?? {};
    m[g] = (m[g] ?? 0) + r.share;
    by.set(r.quarter, m);
  }
  return [...by.entries()].flatMap(([quarter, m]) =>
    [...SHARE_GROUPS, OTHERS].map((group) => ({ quarter, date: quarterEnd(quarter), group, share: m[group] ?? 0 })));
}

/**
 * Hero: each company's share of the video's nine-company advertising total, quarterly. Four lines, labelled at their
 * right end so the chart reads without the legend; the video's last quarter is a dashed event rule (CHART_RULES 8).
 */
export function shareSpec(rows: ShareRow[], videoEnd: string, yMax: number) {
  const values = shareLines(rows);
  const last = values.map((v) => v.quarter).sort().pop();
  const domain = [...SHARE_GROUPS, OTHERS];
  const x = { field: 'date', type: 'temporal', title: null, axis: { format: '%Y', tickCount: 6, labelOverlap: true } };
  const y = { field: 'share', type: 'quantitative', title: 'Share of the nine-company total', scale: { domain: [0, yMax] },
              axis: { format: '.0%', tickCount: 6 } };
  const color = { field: 'group', type: 'nominal', scale: { domain, range: COLORS }, legend: { title: null, orient: 'top', labelLimit: 0 } };
  return {
    $schema: VL_SCHEMA,
    height: 320,
    layer: [
      {
        data: { values: [{ date: quarterEnd(videoEnd), t: 'video ends' }] },
        layer: [
          { mark: { type: 'rule', strokeDash: [4, 3], color: series.neutral }, encoding: { x: { field: 'date', type: 'temporal' } } },
          { mark: { type: 'text', align: 'right', dx: -4, y: 6, baseline: 'top', fontSize: 10, color: tok('ink-2') },
            encoding: { x: { field: 'date', type: 'temporal' }, text: { field: 't' } } },
        ],
      },
      {
        data: { values },
        layer: [
          { mark: { type: 'line', strokeWidth: 2, point: { size: 16 } },
            encoding: { x, y, color,
                        tooltip: [{ field: 'quarter', title: 'Quarter' }, { field: 'group', title: 'Company' },
                                  { field: 'share', title: 'Share', format: '.1%' }] } },
          { transform: [{ filter: `datum.quarter === '${last}'` }],
            mark: { type: 'text', align: 'left', dx: 6, fontSize: 11 },
            encoding: { x, y, color, text: { field: 'share', format: '.1%' } } },
        ],
      },
    ],
  };
}
