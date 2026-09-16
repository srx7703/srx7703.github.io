import latest from '@data/marts/sec/saas_latest.json';
import { VL_SCHEMA, series, tok } from '../theme';

type Row = { ticker: string; name: string | null; rev_growth: number | null; fcf_margin: number | null; rule_of_40: number | null };

/** Companies ranked by Rule-of-40 score; the reference line marks 40. */
export function saasRankedSpec() {
  const rows = (latest as Row[])
    .filter((r) => r.rule_of_40 != null)
    .map((r) => ({ ...r, pass: (r.rule_of_40 ?? 0) >= 40 ? 'Rule of 40 met' : 'Below 40' }));
  return {
    $schema: VL_SCHEMA,
    height: rows.length * 22,
    layer: [
      {
        data: { values: rows },
        mark: { type: 'bar', size: 16, cornerRadiusEnd: 4 },
        encoding: {
          y: { field: 'ticker', type: 'nominal', sort: '-x', title: null, axis: { labelFontSize: 11 } },
          x: { field: 'rule_of_40', type: 'quantitative', title: 'Rule of 40 score (growth % + FCF margin %)', axis: { tickCount: 6 } },
          color: { field: 'pass', type: 'nominal', scale: { domain: ['Rule of 40 met', 'Below 40'], range: [series.a, series.neutral] }, legend: null },
          tooltip: [
            { field: 'name', type: 'nominal', title: 'Company' },
            { field: 'rev_growth', type: 'quantitative', title: 'Revenue growth', format: '.1%' },
            { field: 'fcf_margin', type: 'quantitative', title: 'FCF margin', format: '.1%' },
            { field: 'rule_of_40', type: 'quantitative', title: 'Score', format: '.1f' },
          ],
        },
      },
      {
        data: { values: rows },
        mark: { type: 'text', align: 'left', dx: 4, fontSize: 10, color: tok('ink-2') },
        encoding: {
          y: { field: 'ticker', type: 'nominal', sort: '-x' },
          x: { field: 'rule_of_40', type: 'quantitative' },
          text: { field: 'rule_of_40', type: 'quantitative', format: '.0f' },
        },
      },
      {
        data: { values: [{ x: 40 }] },
        mark: { type: 'rule', strokeDash: [4, 4], color: tok('border-strong') },
        encoding: { x: { field: 'x', type: 'quantitative' } },
      },
    ],
  };
}
