import { VL_SCHEMA, series } from '../theme';
import { STAGES } from './additions';

const STAGE_LABEL: Record<string, string> = {
  announced: 'Announced', permitted: 'Permitted',
  under_construction: 'Under construction', commissioning: 'Commissioning', in_service: 'In service',
};
const STAGE_OPACITY = [0.28, 0.45, 0.66, 0.85, 1];
const ALL_STAGES = [...STAGES, 'in_service'];

export type PathStage = Record<string, Record<string, number>>;

export function pathRows(byPath: PathStage, labels: Record<string, string>) {
  return Object.entries(byPath).flatMap(([path, stages]) =>
    Object.entries(stages).map(([stage, mw]) => ({
      path, stage, mw, gw: mw / 1000,
      path_label: labels[path] ?? path,
      stage_label: STAGE_LABEL[stage] ?? stage,
    })),
  );
}

/**
 * Contracted capacity by supply path — the *other* answer to where the power comes from.
 *
 * This chart is deliberately not comparable to the EIA additions chart beside it, and the page says
 * why in words. These are megawatts somebody signed for, which double-counts where a buyer and a
 * seller both announce, omits everything unannounced, and includes attributes bought from plant
 * that already existed. It is a record of intent. The EIA chart is a record of steel.
 *
 * The same five stage words are used on both, which is the one thing that does carry across.
 */
export function dealsByPathSpec(rows: ReturnType<typeof pathRows>) {
  const totals = new Map<string, number>();
  rows.forEach((r) => totals.set(r.path_label, (totals.get(r.path_label) ?? 0) + r.gw));
  const order = [...totals].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  return {
    $schema: VL_SCHEMA,
    height: Math.max(180, order.length * 28),
    data: { values: rows },
    mark: { type: 'bar', height: 15, cornerRadiusEnd: 3 },
    encoding: {
      y: { field: 'path_label', type: 'nominal', sort: order, title: null, axis: { labelFontSize: 11, labelLimit: 220 } },
      x: { field: 'gw', type: 'quantitative', title: 'Contracted capacity (GW), where disclosed', stack: 'zero' },
      color: { value: series.b },
      opacity: {
        field: 'stage_label', type: 'ordinal',
        scale: { domain: ALL_STAGES.map((s) => STAGE_LABEL[s]), range: STAGE_OPACITY },
        legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'square', symbolFillColor: series.b },
      },
      order: { field: 'stage_label', type: 'ordinal', sort: 'ascending' },
      tooltip: [
        { field: 'path_label', type: 'nominal', title: 'Supply path' },
        { field: 'stage_label', type: 'nominal', title: 'Stage' },
        { field: 'gw', type: 'quantitative', title: 'GW', format: '.2f' },
      ],
    },
  };
}
