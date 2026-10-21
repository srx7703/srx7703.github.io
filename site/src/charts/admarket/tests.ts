import { VL_SCHEMA, series, tok } from '../theme';
import { quarterEnd } from './share';

export type GroupPathRow = { group: string; quarter: string; g2: number };
export type OpenWebRow = { group: string; quarter: string; ttm_bn: number; index: number };

/** Mid-quarter date, for shading a window of quarters. */
const qStart = (q: string) => `${q.slice(0, 4)}-${['01-01', '04-01', '07-01', '10-01'][Number(q[5]) - 1]}`;

/**
 * Q2: two-year annualised growth of the affected group (Meta, Snap, Pinterest) and the control group (Google Search,
 * Microsoft search), unweighted means. The registered before and after windows are shaded and labelled; ATT's release
 * is the dashed event rule. One axis, one unit (CHART_RULES: no dual axes).
 */
export function attSpec(rows: GroupPathRow[], before: string[], after: string[], attDate: string) {
  const label: Record<string, string> = { affected: 'Affected: Meta, Snap, Pinterest', control: 'Control: Google Search, Microsoft search' };
  const values = rows.map((r) => ({ ...r, date: quarterEnd(r.quarter), name: label[r.group] }));
  const windows = [
    { a: qStart(before[0]), b: quarterEnd(before[before.length - 1]), t: 'before' },
    { a: qStart(after[0]), b: quarterEnd(after[after.length - 1]), t: 'after' },
  ];
  const x = { field: 'date', type: 'temporal', title: null, axis: { format: '%Y', tickCount: 6, labelOverlap: true } };
  return {
    $schema: VL_SCHEMA,
    height: 300,
    layer: [
      { data: { values: windows }, mark: { type: 'rect', color: series.neutral, opacity: 0.16 },
        encoding: { x: { field: 'a', type: 'temporal' }, x2: { field: 'b' } } },
      { data: { values: windows }, mark: { type: 'text', y: 4, baseline: 'top', fontSize: 10, color: tok('ink-2') },
        encoding: { x: { field: 'a', type: 'temporal' }, text: { field: 't' } } },
      { data: { values: [{ d: attDate, t: 'ATT' }] },
        layer: [
          { mark: { type: 'rule', strokeDash: [4, 3], color: series.neutral }, encoding: { x: { field: 'd', type: 'temporal' } } },
          { mark: { type: 'text', align: 'left', dx: 3, y: 18, baseline: 'top', fontSize: 10, color: tok('ink-2') },
            encoding: { x: { field: 'd', type: 'temporal' }, text: { field: 't' } } },
        ] },
      { data: { values: [{ z: 0 }] }, mark: { type: 'rule', color: series.neutral }, encoding: { y: { field: 'z', type: 'quantitative' } } },
      { data: { values },
        mark: { type: 'line', strokeWidth: 2, point: { size: 16 } },
        encoding: {
          x,
          y: { field: 'g2', type: 'quantitative', title: 'Two-year growth, annualised', axis: { format: '.0%' } },
          color: { field: 'name', type: 'nominal', scale: { domain: [label.affected, label.control], range: [series.b, series.a] },
                   legend: { title: null, orient: 'top', direction: 'vertical', labelLimit: 0 } },
          tooltip: [{ field: 'quarter', title: 'Quarter' }, { field: 'name', title: 'Group' },
                    { field: 'g2', title: 'Two-year growth, a year', format: '.1%' }],
        } },
    ],
  };
}

/**
 * Q3: trailing-four-quarter revenue of three groups, indexed to the registered base window (= 100). The walled
 * gardens are drawn without Reddit so the line does not jump when Reddit lists; the verdict includes it.
 */
export function openWebSpec(rows: OpenWebRow[], base: string, baseLabel: string) {
  const domain = ['Walled gardens (without Reddit)', 'Open web', 'Independent ad tech'];
  const values = rows.map((r) => ({ ...r, date: quarterEnd(r.quarter) }));
  const last = values.map((v) => v.quarter).sort().pop();
  const x = { field: 'date', type: 'temporal', title: null, axis: { format: '%Y', tickCount: 6, labelOverlap: true } };
  const y = { field: 'index', type: 'quantitative', title: `Trailing four quarters, ${baseLabel} = 100`, scale: { zero: false } };
  const color = { field: 'group', type: 'nominal', scale: { domain, range: [series.a, series.b, series.c] },
                  legend: { title: null, orient: 'top', direction: 'vertical', labelLimit: 0 } };
  return {
    $schema: VL_SCHEMA,
    height: 300,
    layer: [
      { data: { values: [{ d: quarterEnd(base), t: 'base window ends' }] },
        layer: [
          { mark: { type: 'rule', strokeDash: [4, 3], color: series.neutral }, encoding: { x: { field: 'd', type: 'temporal' } } },
          { mark: { type: 'text', align: 'left', dx: 3, y: 4, baseline: 'top', fontSize: 10, color: tok('ink-2') },
            encoding: { x: { field: 'd', type: 'temporal' }, text: { field: 't' } } },
        ] },
      { data: { values: [{ z: 100 }] }, mark: { type: 'rule', color: series.neutral, opacity: 0.6 }, encoding: { y: { field: 'z', type: 'quantitative' } } },
      { data: { values },
        layer: [
          { mark: { type: 'line', strokeWidth: 2 },
            encoding: { x, y, color,
                        tooltip: [{ field: 'quarter', title: 'Four quarters to' }, { field: 'group', title: 'Group' },
                                  { field: 'ttm_bn', title: 'Revenue, $bn', format: ',.1f' }, { field: 'index', title: 'Index', format: '.0f' }] } },
          { transform: [{ filter: `datum.quarter === '${last}'` }], mark: { type: 'text', align: 'left', dx: 5, fontSize: 11 },
            encoding: { x, y, color, text: { field: 'index', format: '.0f' } } },
        ] },
    ],
  };
}
