import { VL_SCHEMA, series } from '../theme';

export type AdditionRow = {
  year: number; family: string; stage: string; family_label: string; mw: number; units: number;
};

/** Stage order, least to most committed. Drawn as one hue darkening, because stages are ordered. */
export const STAGES = ['announced', 'permitted', 'under_construction', 'commissioning'] as const;
const STAGE_LABEL: Record<string, string> = {
  announced: 'Announced',
  permitted: 'Permitted',
  under_construction: 'Under construction',
  commissioning: 'Commissioning',
};
/** Opacity, not hue: a sequential ramp for an ordered variable, so the palette stays at three slots. */
const STAGE_OPACITY = [0.3, 0.5, 0.78, 1];

/** Families below this share of the window are folded into "Other" rather than given their own bar. */
export const FAMILY_FLOOR = 0.01;

export type FoldResult = { rows: AdditionRow[]; folded: string[] };

/**
 * Collapse the long tail into one "Other" bar.
 *
 * Fifteen fuel families is more rows than a reader can hold, and the ones below a percent of the
 * window are not the finding. They are summed rather than dropped, so the bars still add to the
 * total the prose quotes, and the page names what went into the bar.
 */
export function foldFamilies(rows: AdditionRow[], floor = FAMILY_FLOOR): FoldResult {
  const total = rows.reduce((s, r) => s + r.mw, 0);
  if (!total) return { rows: [], folded: [] };
  const byFamily = new Map<string, number>();
  rows.forEach((r) => byFamily.set(r.family, (byFamily.get(r.family) ?? 0) + r.mw));
  const small = new Set([...byFamily].filter(([, mw]) => mw / total < floor).map(([f]) => f));
  const folded = [...small]
    .map((f) => rows.find((r) => r.family === f)?.family_label ?? f)
    .sort();
  const out = new Map<string, AdditionRow>();
  rows.forEach((r) => {
    const family = small.has(r.family) ? 'other_small' : r.family;
    const label = small.has(r.family) ? 'Other' : r.family_label;
    const key = `${family}|${r.stage}`;
    const prev = out.get(key);
    if (prev) { prev.mw += r.mw; prev.units += r.units; }
    else out.set(key, { ...r, family, family_label: label, mw: r.mw, units: r.units });
  });
  return { rows: [...out.values()], folded };
}

/**
 * What the grid is actually building, by fuel and by how committed it is.
 *
 * The bars are nameplate megawatts, which is not energy: a gigawatt of solar and a gigawatt of gas
 * do not produce the same number of megawatt-hours, and the page says so beside the chart rather
 * than silently inviting the comparison. The stage split is there because most planned capacity has
 * no regulatory approval yet, so the dark end of each bar is the part that is actually happening.
 */
export function additionsSpec(rows: AdditionRow[]) {
  const { rows: folded } = foldFamilies(rows);
  const totals = new Map<string, number>();
  folded.forEach((r) => totals.set(r.family_label, (totals.get(r.family_label) ?? 0) + r.mw));
  const order = [...totals].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const values = folded.map((r) => ({ ...r, stage_label: STAGE_LABEL[r.stage] ?? r.stage, gw: r.mw / 1000 }));
  return {
    $schema: VL_SCHEMA,
    height: Math.max(180, order.length * 26),
    data: { values },
    mark: { type: 'bar', height: 15, cornerRadiusEnd: 3 },
    encoding: {
      y: { field: 'family_label', type: 'nominal', sort: order, title: null, axis: { labelFontSize: 11, labelLimit: 170 } },
      x: { field: 'gw', type: 'quantitative', title: 'Nameplate capacity (GW)', stack: 'zero', axis: { tickCount: 6 } },
      color: { value: series.a },
      opacity: {
        field: 'stage_label', type: 'ordinal',
        scale: { domain: STAGES.map((s) => STAGE_LABEL[s]), range: STAGE_OPACITY },
        legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'square', symbolFillColor: series.a },
      },
      order: { field: 'stage_label', type: 'ordinal', sort: 'ascending' },
      tooltip: [
        { field: 'family_label', type: 'nominal', title: 'Fuel' },
        { field: 'stage_label', type: 'nominal', title: 'Stage' },
        { field: 'gw', type: 'quantitative', title: 'GW', format: '.1f' },
        { field: 'units', type: 'quantitative', title: 'Units' },
      ],
    },
  };
}
