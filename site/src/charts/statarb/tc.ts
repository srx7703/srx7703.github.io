import rows from '@data/marts/statarb/tc_sensitivity.json';
import { VL_SCHEMA, series, tok } from '../theme';

const DOMAIN = ['OLS', 'PCA', 'LASSO'];
const RANGE = [series.a, series.b, series.c];

/** Out-of-sample Sharpe ratio as one-way transaction costs rise, by model. */
export function tcSpec() {
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 240,
    encoding: {
      x: { field: 'tc_bps', type: 'quantitative', title: 'One-way transaction cost (bps)', axis: { values: [0, 5, 10, 20, 50], grid: false } },
      y: { field: 'sharpe', type: 'quantitative', title: 'Sharpe ratio, 2020 out of sample', axis: { tickCount: 6 } },
      color: { field: 'strategy', type: 'nominal', scale: { domain: DOMAIN, range: RANGE }, legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' } },
      tooltip: [
        { field: 'strategy', type: 'nominal', title: 'Model' },
        { field: 'tc_bps', type: 'quantitative', title: 'TC (bps)' },
        { field: 'sharpe', type: 'quantitative', title: 'Sharpe', format: '.2f' },
        { field: 'ann_return', type: 'quantitative', title: 'Annual return', format: '.1%' },
      ],
    },
    layer: [
      { mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round' } },
      { mark: { type: 'point', filled: true, size: 64, stroke: tok('surface'), strokeWidth: 2 } },
      { data: { values: [{ y: 0 }] }, mark: { type: 'rule', color: tok('border-strong') }, encoding: { y: { field: 'y', type: 'quantitative' }, color: { value: tok('border-strong') } } },
    ],
  };
}
