import { VL_SCHEMA, series, tok } from '../theme';

export type VpRow = { series: string; label: string; quarter: string; volume: number; price: number; reported: number; implied: number; gap_pp: number };

export const PANELS: Record<string, { title: string; volume: string; price: string }> = {
  meta_ads: { title: 'Meta advertising', volume: 'ad impressions', price: 'average price per ad' },
  google_search_clicks: { title: 'Google Search & other', volume: 'paid clicks', price: 'cost per click' },
  google_network: { title: 'Google Network', volume: 'impressions', price: 'cost per impression' },
};

/**
 * Q4 small multiples, one panel per series: year-over-year change in volume and in price (bars, side by side) and the
 * reported revenue growth they should multiply to (tick). One unit on one axis: percent change on a year earlier.
 */
export function volPriceSpec(rows: VpRow[]) {
  const domain = ['Volume', 'Price', 'Reported revenue growth'];
  const values = rows.flatMap((r) => {
    const p = PANELS[r.series];
    const panel = `${p.title}: volume = ${p.volume}, price = ${p.price}`;
    return [
      { panel, quarter: r.quarter, kind: 'Volume', v: r.volume / 100, l: 'bar' },
      { panel, quarter: r.quarter, kind: 'Price', v: r.price / 100, l: 'bar' },
      { panel, quarter: r.quarter, kind: 'Reported revenue growth', v: r.reported / 100, l: 'dot', implied: r.implied / 100, gap: r.gap_pp },
    ];
  });
  const order = Object.keys(PANELS).map((k) => `${PANELS[k].title}: volume = ${PANELS[k].volume}, price = ${PANELS[k].price}`);
  const quarters = [...new Set(rows.map((r) => r.quarter))].sort();
  const x = { field: 'quarter', type: 'ordinal', title: null, sort: quarters, scale: { domain: quarters, paddingInner: 0.25 },
              axis: { labelExpr: "substring(datum.value, 5) == '1' ? substring(datum.value, 0, 4) : ''", labelAngle: 0, ticks: false } };
  const y = { field: 'v', type: 'quantitative', title: 'Change on a year earlier', axis: { format: '.0%', tickCount: 5 } };
  const color = { field: 'kind', type: 'nominal', scale: { domain, range: [series.a, series.b, tok('ink')] },
                  legend: { title: null, orient: 'top', labelLimit: 0 } };
  return {
    $schema: VL_SCHEMA,
    data: { values },
    facet: { row: { field: 'panel', type: 'nominal', sort: order, title: null,
                    header: { labelAngle: 0, labelAlign: 'left', labelAnchor: 'start', labelFontSize: 12, labelPadding: 4, labelOrient: 'top', labelColor: tok('ink-2') } } },
    spacing: 18,
    _facetColumns: 1,
    spec: {
      height: 150,
      layer: [
        { mark: { type: 'rule', color: series.neutral }, encoding: { y: { datum: 0, type: 'quantitative' } } },
        { transform: [{ filter: "datum.l === 'bar'" }], mark: { type: 'bar' },
          encoding: { x, y, color, xOffset: { field: 'kind', sort: ['Volume', 'Price'] },
                      tooltip: [{ field: 'quarter', title: 'Quarter' }, { field: 'kind', title: 'Measure' }, { field: 'v', title: 'Change', format: '+.0%' }] } },
        // a short horizontal tick across the quarter's pair of bars: where volume x price should land
        { transform: [{ filter: "datum.l === 'dot'" }], mark: { type: 'tick', thickness: 2.5, bandSize: 14 },
          encoding: { x, y, color,
                      tooltip: [{ field: 'quarter', title: 'Quarter' }, { field: 'v', title: 'Reported growth', format: '+.1%' },
                                { field: 'implied', title: 'Volume x price', format: '+.1%' }, { field: 'gap', title: 'Gap, points', format: '+.1f' }] } },
      ],
    },
  };
}
