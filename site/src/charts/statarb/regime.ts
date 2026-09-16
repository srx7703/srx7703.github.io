import rows from '@data/marts/statarb/regime.json';
import { VL_SCHEMA, series, tok } from '../theme';

/** Sharpe by COVID regime (pre-crash / crash / recovery), one panel per regime. */
export function regimeSpec() {
  const regimes = ['pre_crash', 'crash', 'recovery'];
  const labels: Record<string, string> = { pre_crash: 'Pre-crash (Jan–Feb 19)', crash: 'Crash (Feb 20–Mar 23)', recovery: 'Recovery (Mar 24–Dec)' };
  const data = rows.map((r: any) => ({ ...r, regime_label: labels[r.regime] ?? r.regime }));
  return {
    $schema: VL_SCHEMA,
    data: { values: data },
    facet: { column: { field: 'regime_label', type: 'nominal', sort: regimes.map((r) => labels[r]), title: null, header: { labelFontSize: 12 } } },
    spec: {
      width: 170,
      height: 180,
      layer: [
        {
          mark: { type: 'bar', size: 18, cornerRadiusEnd: 4, color: series.a },
          encoding: {
            x: { field: 'strategy', type: 'nominal', sort: ['OLS', 'PCA', 'LASSO', 'Pairs', 'B&H'], title: null, axis: { labelAngle: 0, labelFontSize: 10 } },
            y: { field: 'sharpe', type: 'quantitative', title: 'Sharpe (annualised)', axis: { tickCount: 5 } },
            tooltip: [
              { field: 'strategy', type: 'nominal', title: 'Strategy' },
              { field: 'regime_label', type: 'nominal', title: 'Regime' },
              { field: 'sharpe', type: 'quantitative', title: 'Sharpe', format: '.2f' },
              { field: 'total_return', type: 'quantitative', title: 'Total return', format: '.1%' },
              { field: 'max_dd', type: 'quantitative', title: 'Max drawdown', format: '.1%' },
              { field: 'n_days', type: 'quantitative', title: 'Days' },
            ],
          },
        },
        { data: { values: [{ y: 0 }] }, mark: { type: 'rule', color: tok('border-strong') }, encoding: { y: { field: 'y', type: 'quantitative' } } },
      ],
    },
  };
}
