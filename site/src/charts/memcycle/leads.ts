import { VL_SCHEMA, series } from '../theme';

export type LeadRow = {
  product: string; company: string; label: string; group: string; price_peak: string; lead_months: number | null;
  stock_peak_month: string | null; in_main: boolean; in_diversified: boolean; in_nand_panel: boolean;
};

/**
 * The lead strip: for each confirmed DRAM price peak, how many months before it each company's stock peaked
 * (positive = the stock topped first). The registered 3-9-month band is shaded. Exposure is in the mark, not the
 * colour (CHART_RULES 14): pure plays, which enter the verdict, are filled; the diversified group is hollow.
 */
export function leadsSpec(rows: LeadRow[], band: [number, number]) {
  const values = rows
    .filter((r) => r.product === 'DRAM' && (r.in_main || r.in_diversified) && r.lead_months != null)
    .map((r) => ({ label: r.label, company: r.company, price_peak: r.price_peak, stock_peak_month: r.stock_peak_month,
                   lead_months: r.lead_months, exposure: r.in_main ? 'Pure play (in the verdict)' : 'Diversified (shown only)' }));
  const peaks = [...new Set(values.map((v) => v.price_peak))].sort();
  const lo = Math.min(...values.map((v) => v.lead_months as number), 0);
  const hi = Math.max(...values.map((v) => v.lead_months as number), band[1]);
  const x = { field: 'lead_months', type: 'quantitative', title: 'Lead, months (positive: stock first)',
              scale: { domain: [lo - 2, hi + 2], nice: false }, axis: { tickCount: 8 } };
  const y = { field: 'price_peak', type: 'ordinal', title: null, sort: peaks, axis: { labelFontSize: 11 } };
  return {
    $schema: VL_SCHEMA,
    height: Math.max(180, peaks.length * 34),
    layer: [
      {
        data: { values: [{ a: band[0], b: band[1] }] },
        mark: { type: 'rect', color: series.neutral, opacity: 0.16 },
        encoding: { x: { field: 'a', type: 'quantitative' }, x2: { field: 'b' } },
      },
      {
        data: { values: [{ a: band[0], b: band[1], t: 'registered band' }] },
        mark: { type: 'text', align: 'center', baseline: 'top', dy: -14, fontSize: 10, y: 0 },
        encoding: { x: { datum: (band[0] + band[1]) / 2, type: 'quantitative' }, text: { field: 't' }, color: { value: series.neutral } },
      },
      {
        data: { values: [{ z: 0 }] },
        mark: { type: 'rule', strokeDash: [4, 3], color: series.neutral },
        encoding: { x: { field: 'z', type: 'quantitative' } },
      },
      {
        data: { values },
        mark: { type: 'point', size: 60, strokeWidth: 1.6, opacity: 0.9 },
        encoding: {
          x, y,
          yOffset: { field: 'company', type: 'nominal', scale: { paddingOuter: 0.3 } },
          color: { value: series.a },
          fill: { field: 'exposure', type: 'nominal', scale: { domain: ['Pure play (in the verdict)', 'Diversified (shown only)'], range: [series.a, 'transparent'] },
                  legend: { title: null, orient: 'top', direction: 'vertical', symbolStrokeColor: series.a } },
          tooltip: [
            { field: 'label', title: 'Company' }, { field: 'price_peak', title: 'Price peak' },
            { field: 'stock_peak_month', title: 'Stock peak' }, { field: 'lead_months', title: 'Lead, months' },
            { field: 'exposure', title: 'Group' },
          ],
        },
      },
    ],
  };
}
