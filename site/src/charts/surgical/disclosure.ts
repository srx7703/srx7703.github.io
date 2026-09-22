import { VL_SCHEMA, series } from '../theme';

export type ScorecardRow = {
  maker: string; name: string; tier: string; tier_label: string; region: string;
  listed: boolean; pure_play: boolean; status: string | null; products: string[];
  metrics_published: string[]; n_metrics: number; latest_period: string | null;
  chartable: boolean; note: string | null;
};

const TIER_LABEL: Record<string, string> = {
  T1: 'Quarterly, auditable',
  T2: 'Compelled by listing rules',
  T3: 'In a prospectus',
  T4: 'Listed, discloses nothing',
  T5: 'Private or dark',
};

/**
 * Who publishes a unit figure and who does not.
 *
 * This is the page's lead rather than its preamble, so it is a chart and not a footnote. The bar length is the
 * count of distinct auditable metrics a maker publishes, which is the thing that actually varies: one company
 * publishes installed base, placements and procedures every quarter, and most publish nothing at all.
 *
 * A company at zero is drawn at zero deliberately here, and only here. Everywhere else on the page a
 * non-disclosing company is absent, because a zero on an installed-base chart reads as "sells none". On this
 * chart zero means "tells nobody", which is exactly what it is measuring.
 */
export function disclosureSpec(rows: ScorecardRow[]) {
  const values = rows.map((r) => ({
    ...r,
    tier_name: TIER_LABEL[r.tier] ?? r.tier,
    label: r.status ? `${r.name} (${r.status})` : r.name,
    metrics: r.metrics_published.join(', ') || 'none',
  }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(220, values.length * 19),
    data: { values },
    mark: { type: 'bar', height: 11, cornerRadiusEnd: 3 },
    encoding: {
      y: {
        field: 'label', type: 'nominal', title: null,
        sort: values.map((v) => v.label),
        axis: { labelFontSize: 10, labelLimit: 190 },
      },
      x: {
        field: 'n_metrics', type: 'quantitative',
        title: 'Distinct unit metrics published',
        axis: { tickMinStep: 1, tickCount: 4 },
        scale: { domain: [0, 3] },
      },
      color: {
        field: 'tier_name', type: 'nominal',
        scale: {
          domain: Object.values(TIER_LABEL),
          range: [series.a, series.a, series.c, series.neutral, series.neutral],
        },
        legend: { title: null, orient: 'top', direction: 'vertical', symbolType: 'square' },
      },
      opacity: { condition: { test: 'datum.n_metrics === 0', value: 0.35 }, value: 1 },
      tooltip: [
        { field: 'name', type: 'nominal', title: 'Company' },
        { field: 'tier_name', type: 'nominal', title: 'Disclosure' },
        { field: 'metrics', type: 'nominal', title: 'Publishes' },
        { field: 'latest_period', type: 'nominal', title: 'Latest' },
        { field: 'region', type: 'nominal', title: 'Home market' },
      ],
    },
  };
}
