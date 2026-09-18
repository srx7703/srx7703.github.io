import { VL_SCHEMA, series } from '../theme';

export type ZoneGrowth = {
  zone: string; role: string; start_year: number; end_year: number;
  peak_mw_start: number; peak_mw_end: number; peak_growth_mw: number;
  adjustment_mw_start: number | null; adjustment_mw_end: number | null;
  adjustment_growth_mw: number | null; large_load_share: number | null;
};

const ZONE_LABEL: Record<string, string> = {
  PJM_RTO: 'PJM, whole RTO', DOM: 'Dominion (Virginia)', AEP: 'AEP (Ohio, West Virginia)',
  COMED: 'ComEd (Chicago)', PL: 'PPL (Pennsylvania)', PS: 'PSE&G (New Jersey)',
  BGE: 'BGE (Baltimore)', PECO: 'PECO (Philadelphia)', APS: 'Allegheny Power',
  ATSI: 'ATSI (northern Ohio)', DAY: 'Dayton', DEOK: 'Duke Ohio/Kentucky',
};

export const zoneLabel = (zone: string) => ZONE_LABEL[zone] ?? zone;

/** Zones with no published adjustment cannot be attributed and are named rather than drawn at zero. */
export function attributable(zones: ZoneGrowth[]): ZoneGrowth[] {
  return zones.filter((z) => z.adjustment_growth_mw != null && z.peak_growth_mw !== 0);
}

/**
 * How much of each PJM zone's forecast peak growth is the large-load adjustment.
 *
 * Two bars per zone rather than a share, because the share alone hides the size: a zone whose peak
 * grows 200 MW entirely on data centers and one whose peak grows 9 GW entirely on data centers both
 * read as 100%. The share is printed at the end of the pair as text.
 *
 * A bar longer than its neighbour means large load grew by more than the system peak did, which is
 * PJM's own finding — the rest of the load is shrinking — and is drawn as it comes out. The RTO row
 * is the sum of the zones and is labelled as such, never added to them.
 */
export function growthAttributionSpec(zones: ZoneGrowth[]) {
  const rows = attributable(zones);
  const order = [...rows].sort((a, b) => b.peak_growth_mw - a.peak_growth_mw).map((z) => zoneLabel(z.zone));
  const values = rows.flatMap((z) => [
    { zone: zoneLabel(z.zone), kind: 'Total peak growth', gw: z.peak_growth_mw / 1000, share: z.large_load_share, role: z.role },
    { zone: zoneLabel(z.zone), kind: 'Large-load adjustment', gw: (z.adjustment_growth_mw ?? 0) / 1000, share: z.large_load_share, role: z.role },
  ]);
  const labels = rows.map((z) => ({
    zone: zoneLabel(z.zone),
    share: z.large_load_share,
    gw: Math.max(z.peak_growth_mw, z.adjustment_growth_mw ?? 0) / 1000,
  }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(200, rows.length * 42),
    data: { values },
    layer: [
      {
        mark: { type: 'bar', height: 12, cornerRadiusEnd: 3 },
        encoding: {
          y: { field: 'zone', type: 'nominal', sort: order, title: null, axis: { labelFontSize: 11, labelLimit: 180 } },
          yOffset: { field: 'kind', type: 'nominal', sort: ['Total peak growth', 'Large-load adjustment'] },
          x: { field: 'gw', type: 'quantitative', title: 'Growth in summer peak, GW', axis: { tickCount: 6 } },
          color: {
            field: 'kind', type: 'nominal',
            scale: { domain: ['Total peak growth', 'Large-load adjustment'], range: [series.neutral, series.a] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'square' },
          },
          tooltip: [
            { field: 'zone', type: 'nominal', title: 'Zone' },
            { field: 'kind', type: 'nominal', title: 'Measure' },
            { field: 'gw', type: 'quantitative', title: 'GW', format: '.2f' },
            { field: 'share', type: 'quantitative', title: 'Large load / total growth', format: '.0%' },
          ],
        },
      },
      {
        data: { values: labels },
        mark: { type: 'text', align: 'left', dx: 6, fontSize: 10, baseline: 'middle' },
        encoding: {
          y: { field: 'zone', type: 'nominal', sort: order },
          x: { field: 'gw', type: 'quantitative' },
          text: { field: 'share', type: 'quantitative', format: '.0%' },
          color: { value: series.neutral },
        },
      },
    ],
  };
}
