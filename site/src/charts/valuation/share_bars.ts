import { VL_SCHEMA, series } from '../theme';
import type { ShareMember } from './types';

/** How many bars the chart draws. The page states the cap in the subtitle rather than truncating silently. */
export const SHARE_BARS_LIMIT = 12;

const SEGMENT_LABEL = 'disclosed segment revenue';
const WHOLE_LABEL = 'whole-company revenue';

/** True when publish.py recorded a segment figure rather than consolidated revenue, however it words it. */
const isSegment = (basis: string | null | undefined) => /segment/i.test(String(basis ?? ''));

/**
 * Share of the peer pool's trailing revenue. This is a share of the listed companies we can source,
 * not of the world market: private vendors and companies that do not disclose revenue for the track
 * are absent, and the page says who and why.
 *
 * The legend says where each company's revenue number came from — its whole income statement, or a
 * disclosed segment — and nothing else. It used to append a claim that the track was the company's
 * whole business, which on the battery page told the reader that CATL's business is solid-state.
 */
export function shareBarsSpec(members: ShareMember[], limit = SHARE_BARS_LIMIT) {
  const rows = members.slice(0, limit).map((m) => ({
    ...m,
    basis_label: isSegment(m.basis) ? SEGMENT_LABEL : WHOLE_LABEL,
  }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(160, rows.length * 22),
    data: { values: rows },
    mark: { type: 'bar', height: 13, cornerRadiusEnd: 3 },
    encoding: {
      y: { field: 'name', type: 'nominal', sort: rows.map((r) => r.name), title: null, axis: { labelFontSize: 10, labelLimit: 160 } },
      x: {
        field: 'share', type: 'quantitative', title: 'Share of the peer pool',
        axis: { format: '.0%', tickCount: 5 },
      },
      color: {
        field: 'basis_label', type: 'nominal',
        scale: { domain: [WHOLE_LABEL, SEGMENT_LABEL], range: [series.a, series.neutral] },
        legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'square' },
      },
      tooltip: [
        { field: 'ticker', type: 'nominal', title: 'Listing' },
        { field: 'share', type: 'quantitative', title: 'Share of pool', format: '.1%' },
        { field: 'revenue_usd', type: 'quantitative', title: 'Revenue, TTM (USD)', format: '$,.0f' },
        { field: 'basis', type: 'nominal', title: 'Revenue basis' },
        { field: 'period_end', type: 'nominal', title: 'As of' },
      ],
    },
  };
}
