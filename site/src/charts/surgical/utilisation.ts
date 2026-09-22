import { VL_SCHEMA, series } from '../theme';

export type UtilRow = {
  maker: string; name: string; product: string; year: string; geography: string;
  procedures: number; installed_base: number; procedures_per_system: number; caveat: string;
};

/**
 * Procedures per installed system per year: the bridge between an installed-base share and a procedure share.
 *
 * This is the number that separates a franchise from a fleet of idle machines, and it is the only place on the
 * page where two disclosures are divided into each other. That division is legal only within one company, one
 * product, one year and one geography, which is why so few series appear: most makers publish one side or
 * neither.
 *
 * Plotted as lines by product rather than by company, because Intuitive's two products sit an order of
 * magnitude apart and averaging them would hide both.
 */
export function utilisationSpec(rows: UtilRow[]) {
  const values = rows.map((r) => ({
    ...r,
    key: r.product ? `${r.name} — ${r.product}` : r.name,
    geo_label: r.geography === 'us' ? 'United States only' : 'Worldwide',
  }));
  const keys = [...new Set(values.map((v) => v.key))];
  const last = values.filter((v) => v.year === String(Math.max(...values.map((x) => Number(x.year)))));
  return {
    $schema: VL_SCHEMA,
    height: 280,
    data: { values },
    layer: [
      {
        mark: { type: 'line', strokeWidth: 2, point: { size: 40, filled: true } },
        encoding: {
          x: { field: 'year', type: 'ordinal', title: null, axis: { labelAngle: 0 } },
          y: {
            field: 'procedures_per_system', type: 'quantitative',
            title: 'Procedures per installed system per year',
            scale: { zero: true }, axis: { tickCount: 5 },
          },
          color: {
            field: 'key', type: 'nominal',
            scale: { domain: keys, range: [series.a, series.b, series.c, series.neutral] },
            legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'stroke' },
          },
          strokeDash: {
            field: 'geo_label', type: 'nominal',
            scale: { domain: ['Worldwide', 'United States only'], range: [[1, 0], [4, 3]] },
            legend: { title: null, orient: 'top', direction: 'vertical' },
          },
          tooltip: [
            { field: 'key', type: 'nominal', title: 'Product' },
            { field: 'year', type: 'ordinal', title: 'Year' },
            { field: 'procedures_per_system', type: 'quantitative', title: 'Per system', format: '.1f' },
            { field: 'procedures', type: 'quantitative', title: 'Procedures', format: ',' },
            { field: 'installed_base', type: 'quantitative', title: 'Installed base', format: ',' },
            { field: 'geo_label', type: 'nominal', title: 'Covers' },
          ],
        },
      },
      {
        data: { values: last },
        mark: { type: 'text', align: 'left', dx: 8, fontSize: 10 },
        encoding: {
          x: { field: 'year', type: 'ordinal' },
          y: { field: 'procedures_per_system', type: 'quantitative' },
          text: { field: 'procedures_per_system', type: 'quantitative', format: '.0f' },
          color: { value: series.neutral },
        },
      },
    ],
  };
}
