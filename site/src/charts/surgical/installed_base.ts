import { VL_SCHEMA, series } from '../theme';

export type InstalledRow = {
  maker: string; name: string; value: number; period: string; geography: string;
  basis: string; placement_model: string | null; share_of_disclosed: number | null; caveat: string;
};

/**
 * Installed systems, by the company that disclosed them.
 *
 * The axis is a count, not a share, and that is deliberate. A share needs a denominator, and no free source
 * gives total installed systems worldwide. What the page can honestly show is who published a number and how
 * big it was, with the count of companies that published nothing printed beside the chart.
 *
 * A log scale, because the disclosed pool spans three orders of magnitude and a linear axis renders every
 * challenger as a line of zero width against the incumbent.
 */
export function installedBaseSpec(rows: InstalledRow[]) {
  const values = rows.map((r) => ({
    ...r,
    geo_label: r.geography === 'us' ? 'United States only' : 'Worldwide',
    label: `${r.name} (${r.period})`,
  }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(160, values.length * 30),
    data: { values },
    layer: [
      {
        mark: { type: 'bar', height: 15, cornerRadiusEnd: 3 },
        encoding: {
          y: { field: 'label', type: 'nominal', sort: values.map((v) => v.label), title: null,
               axis: { labelFontSize: 11, labelLimit: 210 } },
          x: {
            field: 'value', type: 'quantitative',
            title: 'Installed systems, log scale',
            scale: { type: 'log', base: 10, domainMin: 10 },
            axis: { tickCount: 4, format: ',' },
          },
          color: {
            field: 'geo_label', type: 'nominal',
            scale: { domain: ['Worldwide', 'United States only'], range: [series.a, series.c] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'square' },
          },
          tooltip: [
            { field: 'name', type: 'nominal', title: 'Company' },
            { field: 'value', type: 'quantitative', title: 'Systems', format: ',' },
            { field: 'period', type: 'nominal', title: 'As of' },
            { field: 'geo_label', type: 'nominal', title: 'Covers' },
            { field: 'share_of_disclosed', type: 'quantitative', title: 'Of the disclosed pool', format: '.1%' },
          ],
        },
      },
      {
        mark: { type: 'text', align: 'left', dx: 6, fontSize: 10 },
        encoding: {
          y: { field: 'label', type: 'nominal', sort: values.map((v) => v.label) },
          x: { field: 'value', type: 'quantitative' },
          text: { field: 'value', type: 'quantitative', format: ',' },
          color: { value: series.neutral },
        },
      },
    ],
  };
}
