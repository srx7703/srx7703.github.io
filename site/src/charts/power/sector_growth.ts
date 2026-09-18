import { VL_SCHEMA, series } from '../theme';

const SECTOR_LABEL: Record<string, string> = {
  commercial: 'Commercial', industrial: 'Industrial',
  residential: 'Residential', transportation: 'Transportation',
};

export type SectorGrowth = { sector: string; twh: number; share: number | null };

export function sectorRows(growth: Record<string, number>, shares: Record<string, number | null>): SectorGrowth[] {
  return Object.keys(SECTOR_LABEL)
    .filter((s) => growth[s] != null)
    .map((s) => ({ sector: s, twh: growth[s], share: shares[s] ?? null }))
    .sort((a, b) => b.twh - a.twh);
}

/**
 * Where the last twelve months of national demand growth landed, by billing sector.
 *
 * One hue: this is a magnitude comparison across four categories on one axis, so colour would be
 * decoration. A sector that shrank is drawn to the left of zero in the negative token, because a
 * bar drawn at zero would read as "flat" when it is a decline.
 */
export function sectorGrowthSpec(rows: SectorGrowth[]) {
  const values = rows.map((r) => ({ ...r, label: SECTOR_LABEL[r.sector] ?? r.sector }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(130, values.length * 30),
    data: { values },
    mark: { type: 'bar', height: 16, cornerRadiusEnd: 3 },
    encoding: {
      y: { field: 'label', type: 'nominal', sort: values.map((v) => v.label), title: null, axis: { labelFontSize: 11 } },
      x: { field: 'twh', type: 'quantitative', title: 'Change in trailing-twelve-month sales (TWh)', axis: { tickCount: 6 } },
      color: {
        condition: { test: 'datum.twh < 0', value: 'token:--negative' },
        value: series.a,
      },
      tooltip: [
        { field: 'label', type: 'nominal', title: 'Sector' },
        { field: 'twh', type: 'quantitative', title: 'Change, TWh', format: '+.1f' },
        { field: 'share', type: 'quantitative', title: 'Share of total growth', format: '.0%' },
      ],
    },
  };
}
