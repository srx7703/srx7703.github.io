import { VL_SCHEMA, series, tok } from '../theme';

export type IndexRow = { product: string; month: string; value: number; preliminary: boolean; nor_era: boolean };
export type PhaseRow = { product: string; from: string; from_kind: string; to: string; to_kind: string; months: number; change: number; to_confirmed: boolean };
export type TurnRow = { product: string; basis: string; amp_threshold: number; kind: string; month: string; confirmed: boolean };
export type RallyRow = { product: string; from: string; to: string; months: number; change: number; under_min_phase: boolean };

export const PANEL: Record<string, string> = { DRAM: 'DRAM', NAND: 'Flash (mostly NOR before 2006)' };
const day = (m: string) => `${m}-01`;

/**
 * The registered price-cycle calendar: the contract-currency index for DRAM and flash, one panel each.
 *
 * Log scale, because the DRAM index falls through more than three orders of magnitude between 1995 and its
 * low (CHART_RULES 13); on a linear axis every cycle after 2008 is a flat line along the bottom.
 *
 * Grey bands are the rising phases the rule dates. The orange bands are the point of the chart: rises that
 * cleared the amplitude threshold but were removed by the duration step, so a reader who remembers 1999-2002
 * can see the rallies the calendar does not count. The latest peak is hollow because it is the last month
 * published, not a confirmed turn. Flash before 2006 is drawn grey: the item then was mostly NOR.
 */
export function calendarSpec(index: IndexRow[], phases: PhaseRow[], turns: TurnRow[], rallies: RallyRow[]) {
  // Compact rows (the index alone is ~700 months): short keys, with the date, panel and NOR flag derived in
  // the spec, so the page does not inline a large JSON blob.
  const values: Record<string, unknown>[] = [];
  for (const r of index) values.push({ l: 'line', p: r.product, m: r.month, v: r.value });
  for (const p of phases.filter((x) => x.from_kind === 'T')) {
    values.push({ l: 'rise', p: p.product, m: p.from, m2: p.to, n: p.months, c: p.change });
  }
  for (const r of rallies) values.push({ l: 'deleted', p: r.product, m: r.from, m2: r.to, n: r.months, c: r.change });
  const val = new Map(index.map((r) => [`${r.product}|${r.month}`, r.value]));
  for (const t of turns.filter((x) => x.basis === 'C' && x.amp_threshold === 0.2)) {
    values.push({ l: 'turn', p: t.product, m: t.month, v: val.get(`${t.product}|${t.month}`), k: t.kind === 'P' ? 'Peak' : 'Trough', ok: t.confirmed });
  }
  const norUntil = index.filter((r) => r.nor_era).map((r) => r.month).sort().pop() ?? '';
  const last = index.map((r) => r.month).sort().pop() ?? '';
  const derive = [
    { calculate: "datum.m + '-01'", as: 'date' },
    { calculate: "datum.m2 ? datum.m2 + '-01' : null", as: 'date2' },
    { calculate: `datum.p === 'DRAM' ? '${PANEL.DRAM}' : '${PANEL.NAND}'`, as: 'panel' },
    { calculate: `!(datum.p === 'NAND' && datum.m <= '${norUntil}')`, as: 'main' },
    { calculate: `datum.m === '${last}'`, as: 'preliminary' },
  ];
  const x = { field: 'date', type: 'temporal', title: null, axis: { format: '%Y', tickCount: 8, labelOverlap: true } };
  const y = { field: 'v', type: 'quantitative', title: 'Index, 2020 = 100, log scale',
              scale: { type: 'log', base: 10, nice: false },
              axis: { values: [10, 100, 1000, 10000, 100000, 1000000], format: '~s', grid: true } };
  const only = (layer: string, extra = '') => ({ filter: `datum.l === '${layer}'${extra}` });
  return {
    $schema: VL_SCHEMA,
    data: { values },
    transform: derive,
    facet: { row: { field: 'panel', type: 'nominal', sort: [PANEL.DRAM, PANEL.NAND], title: null,
                    header: { labelAngle: 0, labelAlign: 'left', labelAnchor: 'start', labelFontSize: 12, labelPadding: 4, labelOrient: 'top', labelColor: tok('ink-2') } } },
    resolve: { scale: { y: 'independent' } },
    spacing: 18,
    _facetColumns: 1,
    spec: {
      height: 180,
      layer: [
        {
          transform: [only('rise')],
          mark: { type: 'rect', color: series.neutral, opacity: 0.18 },
          encoding: {
            x, x2: { field: 'date2' },
            tooltip: [
              { field: 'm', title: 'Trough' }, { field: 'm2', title: 'Peak' },
              { field: 'n', title: 'Months rising' }, { field: 'c', title: 'Rise', format: '+.0%' },
            ],
          },
        },
        {
          transform: [only('deleted')],
          mark: { type: 'rect', color: series.b, opacity: 0.35 },
          encoding: {
            x, x2: { field: 'date2' },
            tooltip: [
              { field: 'm', title: 'Rally from' }, { field: 'm2', title: 'to' },
              { field: 'n', title: 'Months' }, { field: 'c', title: 'Rise', format: '+.0%' },
            ],
          },
        },
        {
          transform: [only('line')],
          mark: { type: 'line', strokeWidth: 1.4, color: series.neutral },
          encoding: { x, y },
        },
        {
          transform: [only('line', ' && datum.main')],
          mark: { type: 'line', strokeWidth: 1.6, color: series.a },
          encoding: {
            x, y,
            tooltip: [{ field: 'm', title: 'Month' }, { field: 'v', title: 'Index', format: ',.1f' },
                      { field: 'preliminary', title: 'Preliminary' }],
          },
        },
        {
          transform: [only('turn', ' && datum.ok')],
          mark: { type: 'point', filled: true, size: 36, color: series.a, opacity: 1 },
          encoding: {
            x, y, shape: { field: 'k', type: 'nominal', scale: { domain: ['Peak', 'Trough'], range: ['triangle-up', 'triangle-down'] },
                           legend: { title: null, orient: 'top', direction: 'horizontal' } },
            tooltip: [{ field: 'k', title: 'Turn' }, { field: 'm', title: 'Month' }],
          },
        },
        {
          transform: [only('turn', ' && !datum.ok')],
          mark: { type: 'point', filled: false, size: 70, strokeWidth: 1.6, color: series.a, shape: 'triangle-up' },
          encoding: {
            x, y,
            tooltip: [{ field: 'm', title: 'Last month published' }, { value: 'not a confirmed peak', title: 'Status' }],
          },
        },
      ],
    },
  };
}
