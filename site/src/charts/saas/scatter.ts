import latest from '@data/marts/sec/saas_latest.json';
import { VL_SCHEMA, series, tok } from '../theme';

type Row = { ticker: string; name: string | null; quarter_end: string; revenue: number | null; rev_growth: number | null; fcf_margin: number | null; rule_of_40: number | null };

/** Revenue growth vs FCF margin, one point per company; the Rule-of-40 line is x + y = 40%. */
export function saasScatterSpec() {
  const rows = (latest as Row[])
    .filter((r) => r.rev_growth != null && r.fcf_margin != null)
    .map((r) => ({ ...r, pass: (r.rule_of_40 ?? 0) >= 40 ? 'Rule of 40 met' : 'Below 40' }));
  const pad = 0.05;
  const xs = rows.map((r) => r.rev_growth as number), ys = rows.map((r) => r.fcf_margin as number);
  const xd = [Math.floor((Math.min(-0.1, ...xs) - pad) * 10) / 10, Math.ceil((Math.max(0.5, ...xs) + pad) * 10) / 10];
  const yd = [Math.floor((Math.min(-0.1, ...ys) - pad) * 10) / 10, Math.ceil((Math.max(0.5, ...ys) + pad) * 10) / 10];
  // Rule-of-40 line x + y = 0.4, clipped to the plot: endpoints on the domain edges
  const lineX0 = Math.max(xd[0], 0.4 - yd[1]), lineX1 = Math.min(xd[1], 0.4 - yd[0]);
  const tooltip = [
    { field: 'name', type: 'nominal', title: 'Company' },
    { field: 'quarter_end', type: 'nominal', title: 'Latest quarter end' },
    { field: 'revenue', type: 'quantitative', title: 'Revenue, TTM', format: '$,.0f' },
    { field: 'rev_growth', type: 'quantitative', title: 'Revenue growth, YoY', format: '.1%' },
    { field: 'fcf_margin', type: 'quantitative', title: 'FCF margin', format: '.1%' },
    { field: 'rule_of_40', type: 'quantitative', title: 'Rule of 40 score', format: '.1f' },
  ];
  return {
    $schema: VL_SCHEMA,
    height: 380,
    layer: [
      {
        data: { values: [{ x: lineX0, y: 0.4 - lineX0 }, { x: lineX1, y: 0.4 - lineX1 }] },
        mark: { type: 'line', strokeDash: [4, 4], color: tok('border-strong'), strokeWidth: 1.5, clip: true },
        encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' } },
      },
      {
        data: { values: [{ x: lineX1 - 0.02, y: 0.4 - lineX1 + 0.02, label: 'Rule of 40: growth + FCF margin = 40%' }] },
        mark: { type: 'text', align: 'right', baseline: 'bottom', fontSize: 11, color: tok('ink-3'), clip: true },
        encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' }, text: { field: 'label' } },
      },
      {
        data: { values: rows },
        mark: { type: 'point', filled: true, size: 90, stroke: tok('surface'), strokeWidth: 2, clip: true },
        encoding: {
          x: { field: 'rev_growth', type: 'quantitative', title: 'Revenue growth, YoY (TTM)', axis: { format: '.0%', tickCount: 6 }, scale: { domain: xd } },
          y: { field: 'fcf_margin', type: 'quantitative', title: 'Free-cash-flow margin (TTM)', axis: { format: '.0%', tickCount: 6 }, scale: { domain: yd } },
          color: {
            field: 'pass', type: 'nominal', scale: { domain: ['Rule of 40 met', 'Below 40'], range: [series.a, series.neutral] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' },
          },
          tooltip,
        },
      },
      {
        data: { values: rows },
        mark: { type: 'text', align: 'left', dx: 8, dy: -1, fontSize: 11, color: tok('ink-2'), clip: true },
        encoding: {
          x: { field: 'rev_growth', type: 'quantitative' },
          y: { field: 'fcf_margin', type: 'quantitative' },
          text: { field: 'ticker' },
        },
      },
    ],
  };
}
