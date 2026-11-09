import { VL_SCHEMA, series, tok } from '../theme';

/** Group colours: fixed order, never cycled (docs/CHART_RULES.md rule 10). */
export const GROUP_DOMAIN = ['oem', 'odm', 'ems', 'cn'];
export const GROUP_LABEL: Record<string, string> = {
  oem: 'Brand makers', odm: 'Taiwan makers (ODM)', ems: 'Contract manufacturers', cn: 'Chinese makers',
};
const GROUP_RANGE = [series.a, series.b, series.c, series.neutral];
const groupColor = {
  field: 'group_label', type: 'nominal',
  scale: { domain: GROUP_DOMAIN.map((g) => GROUP_LABEL[g]), range: GROUP_RANGE },
  legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' },
};

export type GrowthRow = { quarter: string; oem: number | null; odm: number | null; odm_robust: number | null };

/** Figure 1: trailing-four-quarter growth of the brand makers' server lines against the Taiwan makers' revenue. */
export function growthSpec(rows: GrowthRow[]) {
  const labels: Record<string, string> = {
    oem: 'Brand makers, server lines', odm: 'Quanta + Wistron + Inventec', odm_robust: 'Quanta + Inventec + Wiwynn',
  };
  const values = rows.filter((r) => r.quarter >= '2023Q1').flatMap((r) =>
    (['oem', 'odm', 'odm_robust'] as const).filter((k) => r[k] != null).map((k) => ({
      quarter: r.quarter, series: labels[k], growth: r[k],
    })));
  const domain = [labels.oem, labels.odm, labels.odm_robust];
  return {
    $schema: VL_SCHEMA,
    height: 280,
    data: { values },
    layer: [
      { mark: { type: 'rule', strokeDash: [2, 2], color: tok('border-strong') }, encoding: { y: { datum: 0 } } },
      {
        mark: { type: 'line', strokeWidth: 2, point: { size: 30, filled: true } },
        encoding: {
          x: { field: 'quarter', type: 'ordinal', title: null, axis: { labelAngle: -45, labelFontSize: 10 } },
          y: { field: 'growth', type: 'quantitative', title: 'Year-over-year growth, trailing four quarters',
               axis: { format: '.0%', tickCount: 6 } },
          color: { field: 'series', type: 'nominal', scale: { domain, range: [series.a, series.b, series.c] },
                   legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'stroke' } },
          strokeDash: { field: 'series', type: 'nominal', scale: { domain, range: [[1, 0], [1, 0], [5, 3]] }, legend: null },
          tooltip: [
            { field: 'series', type: 'nominal', title: 'Series' },
            { field: 'quarter', type: 'ordinal', title: 'Quarter' },
            { field: 'growth', type: 'quantitative', title: 'Growth', format: '+.1%' },
          ],
        },
      },
    ],
  };
}

export type ScoreRow = {
  key: string; name: string; group: string; comparator: boolean; ttm_end: string;
  growth: number | null; gross_margin: number | null; operating_margin: number | null;
  gross_margin_2023: number | null; operating_margin_2023: number | null;
  inventory_days: number | null; inventory_days_2023: number | null;
};

/** A before/after dumbbell: one row per company, the 2023 value hollow, the latest value filled. */
function dumbbell(rows: { name: string; group: string; before: number; after: number }[], title: string, fmt: string) {
  const sorted = rows.slice().sort((a, b) => b.after - a.after);
  const values = sorted.map((r) => ({ ...r, group_label: GROUP_LABEL[r.group] }));
  const long = values.flatMap((r) => [
    { ...r, when: 'Four quarters to mid-2023', v: r.before },
    { ...r, when: 'Latest four quarters', v: r.after },
  ]);
  const y = { field: 'name', type: 'nominal', sort: sorted.map((r) => r.name), title: null,
              axis: { labelFontSize: 10, labelLimit: 170 } };
  return {
    $schema: VL_SCHEMA,
    height: Math.max(160, sorted.length * 18),
    layer: [
      {
        data: { values },
        mark: { type: 'rule', strokeWidth: 2, color: series.neutral },
        encoding: { y, x: { field: 'before', type: 'quantitative', title, axis: { format: fmt } }, x2: { field: 'after' } },
      },
      {
        data: { values: long },
        mark: { type: 'point', size: 70, strokeWidth: 2 },
        encoding: {
          y, x: { field: 'v', type: 'quantitative' },
          color: groupColor,
          fill: { condition: { test: "datum.when == 'Latest four quarters'", field: 'group_label', type: 'nominal',
                               scale: { domain: GROUP_DOMAIN.map((g) => GROUP_LABEL[g]), range: GROUP_RANGE }, legend: null },
                  value: tok('bg') },
          shape: { field: 'when', type: 'nominal', scale: { domain: ['Four quarters to mid-2023', 'Latest four quarters'], range: ['circle', 'circle'] },
                   legend: null },
          tooltip: [
            { field: 'name', type: 'nominal', title: 'Company' },
            { field: 'when', type: 'nominal', title: 'Window' },
            { field: 'v', type: 'quantitative', title, format: fmt === '.0%' ? '.1%' : '.0f' },
          ],
        },
      },
    ],
  };
}

export function marginSpec(rows: ScoreRow[]) {
  return dumbbell(rows.filter((r) => !r.comparator && r.gross_margin != null && r.gross_margin_2023 != null)
    .map((r) => ({ name: r.name, group: r.group, before: r.gross_margin_2023 as number, after: r.gross_margin as number })),
  'Gross margin, trailing four quarters', '.0%');
}

export function operatingSpec(rows: ScoreRow[]) {
  return dumbbell(rows.filter((r) => !r.comparator && r.operating_margin != null && r.operating_margin_2023 != null)
    .map((r) => ({ name: r.name, group: r.group, before: r.operating_margin_2023 as number, after: r.operating_margin as number })),
  'Operating margin, trailing four quarters', '.0%');
}

export function inventorySpec(rows: ScoreRow[]) {
  return dumbbell(rows.filter((r) => !r.comparator && r.inventory_days != null && r.inventory_days_2023 != null)
    .map((r) => ({ name: r.name, group: r.group, before: r.inventory_days_2023 as number, after: r.inventory_days as number })),
  'Inventory days (inventory over trailing cost of sales x 365)', '.0f');
}

export type Q5Row = { ticker: string; name: string; adj_pe: number; growth: number; operating_margin: number; fwd_pe: number };

/** Figure 4: market-adjusted forward PE against trailing revenue growth. */
export function valuationSpec(rows: Q5Row[], groupOf: Record<string, string>) {
  const values = rows.map((r) => ({ ...r, group_label: GROUP_LABEL[groupOf[r.ticker] ?? 'oem'] }));
  return {
    $schema: VL_SCHEMA,
    height: 320,
    data: { values },
    layer: [
      { mark: { type: 'rule', strokeDash: [2, 2], color: tok('border-strong') }, encoding: { y: { datum: 1 } } },
      {
        mark: { type: 'point', filled: true, size: 80, opacity: 1 },
        encoding: {
          x: { field: 'growth', type: 'quantitative', title: 'Revenue growth, trailing four quarters', axis: { format: '.0%' } },
          y: { field: 'adj_pe', type: 'quantitative', title: 'Forward PE over its market median', scale: { type: 'log' },
               axis: { format: '.1f', values: [0.5, 0.75, 1, 1.5, 2] } },
          color: groupColor,
          tooltip: [
            { field: 'name', type: 'nominal', title: 'Company' },
            { field: 'fwd_pe', type: 'quantitative', title: 'Forward PE (2027E)', format: '.1f' },
            { field: 'adj_pe', type: 'quantitative', title: 'Over market median', format: '.2f' },
            { field: 'growth', type: 'quantitative', title: 'Revenue growth', format: '+.0%' },
            { field: 'operating_margin', type: 'quantitative', title: 'Operating margin', format: '.1%' },
          ],
        },
      },
      {
        mark: { type: 'text', align: 'left', dx: 6, dy: -6, fontSize: 9 },
        encoding: {
          x: { field: 'growth', type: 'quantitative' }, y: { field: 'adj_pe', type: 'quantitative' },
          text: { field: 'ticker' }, color: { value: tok('ink-2') },
        },
      },
    ],
  };
}

export type Q7Pair = { quarter: string; signal: number; target: number };

/** Figure 5: the Taiwan makers' first-two-months growth against the four cloud companies' capex growth. */
export function q7Spec(pairs: Q7Pair[]) {
  const S = 'Quanta + Wistron + Inventec, first two months of the quarter';
  const T = 'Microsoft + Alphabet + Amazon + Meta capex';
  const values = pairs.flatMap((p) => [
    { quarter: p.quarter, series: S, v: p.signal }, { quarter: p.quarter, series: T, v: p.target },
  ]);
  return {
    $schema: VL_SCHEMA,
    height: 280,
    data: { values },
    layer: [
      { mark: { type: 'rule', strokeDash: [2, 2], color: tok('border-strong') }, encoding: { y: { datum: 0 } } },
      {
        mark: { type: 'line', strokeWidth: 2, point: { size: 25, filled: true } },
        encoding: {
          x: { field: 'quarter', type: 'ordinal', title: null, axis: { labelAngle: -45, labelFontSize: 9 } },
          y: { field: 'v', type: 'quantitative', title: 'Year-over-year growth', axis: { format: '.0%' } },
          color: { field: 'series', type: 'nominal', scale: { domain: [S, T], range: [series.b, series.a] },
                   legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'stroke' } },
          tooltip: [
            { field: 'series', type: 'nominal', title: 'Series' },
            { field: 'quarter', type: 'ordinal', title: 'Quarter' },
            { field: 'v', type: 'quantitative', title: 'Growth', format: '+.1%' },
          ],
        },
      },
    ],
  };
}
