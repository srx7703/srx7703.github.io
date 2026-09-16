import facts from '@data/facts/midterms.json';
import { VL_SCHEMA, series } from '../theme';

/** Latest Polymarket balance-of-power probabilities as horizontal bars (single series). */
export function balanceSpec() {
  const h = facts.headline as Record<string, { label: string; polymarket?: number }>;
  const rows = ['bop_dd', 'bop_rd', 'bop_rr', 'bop_dr']
    .map((k) => ({ key: k, label: h[k]?.label ?? k, prob: h[k]?.polymarket ?? null }))
    .filter((r) => r.prob != null);
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 150,
    encoding: {
      y: { field: 'label', type: 'nominal', title: null, sort: '-x', axis: { labelLimit: 200 } },
      x: { field: 'prob', type: 'quantitative', title: 'Probability (Polymarket)', scale: { domain: [0, 1] }, axis: { format: '.0%', tickCount: 5 } },
      tooltip: [
        { field: 'label', type: 'nominal', title: 'Outcome' },
        { field: 'prob', type: 'quantitative', title: 'Probability', format: '.1%' },
      ],
    },
    layer: [
      { mark: { type: 'bar', size: 22, cornerRadiusEnd: 4, color: series.polymarket } },
      {
        mark: { type: 'text', align: 'left', dx: 6, fontSize: 12, color: 'token:--ink-2' },
        encoding: { text: { field: 'prob', type: 'quantitative', format: '.1%' } },
      },
    ],
  };
}
