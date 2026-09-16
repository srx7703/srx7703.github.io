import rows from '@data/marts/statarb/sharpe_ci.json';
import { VL_SCHEMA, series, tok } from '../theme';

/** Sharpe point estimate with bootstrap 95% interval, 2020 out of sample, 5 bps TC. */
export function ciSpec() {
  const order = ['B&H', 'Pairs', 'LASSO', 'OLS', 'PCA'];
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 190,
    encoding: {
      y: { field: 'strategy', type: 'nominal', sort: order, title: null },
      tooltip: [
        { field: 'strategy', type: 'nominal', title: 'Strategy' },
        { field: 'sharpe', type: 'quantitative', title: 'Sharpe', format: '.2f' },
        { field: 'lo', type: 'quantitative', title: '2.5%', format: '.2f' },
        { field: 'hi', type: 'quantitative', title: '97.5%', format: '.2f' },
      ],
    },
    layer: [
      { mark: { type: 'rule', strokeWidth: 2, color: series.neutral }, encoding: { x: { field: 'lo', type: 'quantitative', title: 'Sharpe ratio (point estimate and bootstrap 95% interval)', axis: { tickCount: 7 } }, x2: { field: 'hi' } } },
      { mark: { type: 'point', filled: true, size: 90, color: series.a, stroke: tok('surface'), strokeWidth: 2 }, encoding: { x: { field: 'sharpe', type: 'quantitative' } } },
      { data: { values: [{ x: 0 }] }, mark: { type: 'rule', strokeDash: [4, 4], color: tok('border-strong') }, encoding: { x: { field: 'x', type: 'quantitative' } } },
    ],
  };
}
