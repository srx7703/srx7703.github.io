import { VL_SCHEMA, series } from '../theme';
import type { ShareMember } from './types';

/**
 * Share of the peer pool's trailing revenue. This is a share of the listed companies we can source,
 * not of the world market: private vendors and companies that do not disclose revenue for the track
 * are absent, and the page says who and why.
 */
export function shareBarsSpec(members: ShareMember[], limit = 12) {
  const rows = members.slice(0, limit).map((m) => ({
    ...m,
    basis_label: m.basis === 'total revenue' ? 'total revenue (the track is the business)' : 'disclosed segment revenue',
  }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(160, rows.length * 22),
    data: { values: rows },
    mark: { type: 'bar', height: 13, cornerRadiusEnd: 3 },
    encoding: {
      y: { field: 'ticker', type: 'nominal', sort: rows.map((r) => r.ticker), title: null, axis: { labelFontSize: 10 } },
      x: {
        field: 'share', type: 'quantitative', title: 'Share of the peer pool, trailing twelve months',
        axis: { format: '.0%', tickCount: 5 },
      },
      color: {
        field: 'basis_label', type: 'nominal',
        scale: {
          domain: ['total revenue (the track is the business)', 'disclosed segment revenue'],
          range: [series.a, series.neutral],
        },
        legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'square' },
      },
      tooltip: [
        { field: 'name', type: 'nominal', title: 'Company' },
        { field: 'share', type: 'quantitative', title: 'Share of pool', format: '.1%' },
        { field: 'revenue_usd', type: 'quantitative', title: 'Revenue, TTM (USD)', format: '$,.0f' },
        { field: 'basis_label', type: 'nominal', title: 'Revenue basis' },
        { field: 'period_end', type: 'nominal', title: 'As of' },
      ],
    },
  };
}
