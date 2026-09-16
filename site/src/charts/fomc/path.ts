import meetings from '@data/marts/predmarkets/fomc_meetings.json';
import { PLATFORM_DOMAIN, PLATFORM_LABEL_EXPR, PLATFORM_RANGE, VL_SCHEMA, tok } from '../theme';

type Meeting = { meeting: string; decision_date: string; platforms: Record<string, { expected_bps: number | null }> };

/** Expected rate change per upcoming meeting, by platform (probability-weighted bps). */
export function fomcPathSpec() {
  const rows = (meetings as Meeting[]).flatMap((m) =>
    Object.entries(m.platforms).map(([platform, v]) => ({ meeting: m.decision_date, platform, bps: v.expected_bps })),
  );
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 200,
    encoding: {
      x: { field: 'meeting', type: 'ordinal', title: 'Decision date', axis: { labelAngle: 0, labelExpr: "utcFormat(toDate(datum.value), '%b %d, %Y')" } },
      y: { field: 'bps', type: 'quantitative', title: 'Expected change (bps)', axis: { tickCount: 5 } },
      xOffset: { field: 'platform', type: 'nominal', scale: { domain: PLATFORM_DOMAIN } },
      color: {
        field: 'platform', type: 'nominal', scale: { domain: PLATFORM_DOMAIN, range: PLATFORM_RANGE },
        legend: { title: null, orient: 'top', direction: 'horizontal', labelExpr: PLATFORM_LABEL_EXPR },
      },
      tooltip: [
        { field: 'meeting', type: 'nominal', title: 'Decision date' },
        { field: 'platform', type: 'nominal', title: 'Platform' },
        { field: 'bps', type: 'quantitative', title: 'Expected change (bps)', format: '.1f' },
      ],
    },
    layer: [
      { mark: { type: 'bar', size: 20, cornerRadiusEnd: 4 } },
      { mark: { type: 'text', dy: -6, fontSize: 11, color: tok('ink-2') }, encoding: { text: { field: 'bps', type: 'quantitative', format: '.0f' } } },
      // the zero rule drops the inherited x/xOffset/color channels so it adds no 'null' category to the x scale
      { data: { values: [{ y: 0 }] }, mark: { type: 'rule', color: tok('border-strong') }, encoding: { x: null, xOffset: null, color: null, tooltip: null, y: { field: 'y', type: 'quantitative' } } },
    ],
  };
}
