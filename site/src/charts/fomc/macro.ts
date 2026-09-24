import { VL_SCHEMA, series, tok } from '../theme';

/**
 * One panel of the stacked macro figure. Four panels share one time axis (same explicit domain and a
 * fixed y-axis width, so the plots line up) and each keeps its own y axis: rates in %, inflation
 * compensation in %, oil in $/bbl, claims in thousands. Nothing is indexed, z-scored or put on a second
 * axis. Every panel carries the FOMC decision days as dashed rules; the rate panel also marks CPI and
 * jobs-report days along its floor.
 */
export type MacroPanel = 'rate' | 'inflation' | 'oil' | 'claims';

type SeriesDef = { key: string; label: string; color: string; step?: boolean };

const PANELS: Record<MacroPanel, { yTitle: string; format: string; series: SeriesDef[] }> = {
  rate: {
    yTitle: 'Percent',
    format: '.2f',
    series: [
      { key: 'target', label: 'Fed target (upper bound)', color: series.neutral, step: true },
      { key: 'implied_polymarket', label: 'Polymarket, next two meetings', color: series.polymarket },
      { key: 'dgs2', label: '2-year Treasury', color: series.b },
    ],
  },
  inflation: {
    yTitle: 'Percent',
    format: '.2f',
    series: [
      { key: 't5yie', label: '5-year breakeven', color: series.a },
      { key: 't5yifr', label: '5-year, 5-year forward', color: series.b },
    ],
  },
  oil: {
    yTitle: '$ per barrel',
    format: '.1f',
    series: [
      { key: 'wti', label: 'WTI', color: series.a },
      { key: 'brent', label: 'Brent', color: series.b },
    ],
  },
  claims: {
    yTitle: 'Thousands',
    format: '.0f',
    series: [{ key: 'ic4wsa', label: 'Initial claims, 4-week average (first print)', color: series.a, step: true }],
  },
};

export const MACRO_LABELS: Record<string, string> = Object.fromEntries(
  Object.values(PANELS).flatMap((p) => p.series.map((s) => [s.key, s.label])),
);

type Ctx = {
  start: string;
  end: string;
  decisions: string[];
  releases?: { release: string; date: string }[];
  height?: number;
};

const utc = (d: string) => Date.parse(`${d}T00:00:00Z`);

/** Vega expression mapping a series key to its label (a ternary chain; no object literals). */
const labelExpr = (defs: SeriesDef[]) =>
  defs.reduceRight((acc, s) => `datum.label == ${JSON.stringify(s.key)} ? ${JSON.stringify(s.label)} : ${acc}`, 'datum.label');

export function fomcMacroPanelSpec(panel: MacroPanel, ctx: Ctx) {
  const p = PANELS[panel];
  const keys = p.series.map((s) => s.key);
  const H = ctx.height ?? 170;
  const color = {
    field: 'series',
    type: 'nominal',
    scale: { domain: keys, range: p.series.map((s) => s.color) },
    legend:
      keys.length > 1
        ? {
            // three long labels do not fit one row on a phone, and a Vega legend does not wrap by itself
            title: null, orient: 'top', direction: 'horizontal', symbolType: 'stroke', labelLimit: 320,
            ...(keys.length > 2 ? { columns: 1 } : {}),
            labelExpr: labelExpr(p.series),
          }
        : null,
  };
  const x = {
    field: 'date', type: 'temporal', title: null,
    scale: { type: 'utc', domain: [utc(ctx.start), utc(ctx.end)] },
    axis: { format: '%b %y', grid: false, labelAngle: 0, tickCount: 7 },
  };
  const inPanel = `indexof(${JSON.stringify(keys)}, datum.series) >= 0`;
  const stepKeys = p.series.filter((s) => s.step).map((s) => s.key);
  const lineKeys = p.series.filter((s) => !s.step).map((s) => s.key);
  const tooltip = [
    { field: 'date_label', type: 'nominal', title: 'Date' },
    ...p.series.map((s) => ({ field: s.key, type: 'quantitative', title: s.label, format: p.format })),
  ];
  const layers: Record<string, unknown>[] = [
    {
      data: { values: ctx.decisions.map((d) => ({ date: d, what: 'FOMC decision' })) },
      transform: [{ calculate: 'toDate(datum.date)', as: 'date' }],
      mark: { type: 'rule', strokeDash: [4, 4], opacity: 0.55 },
      encoding: {
        x,
        y: null,
        color: { value: tok('ink-3') },
        tooltip: [{ field: 'what', title: 'Event' }],
      },
    },
  ];
  if (ctx.releases?.length) {
    layers.push({
      data: { values: ctx.releases.map((r) => ({ date: r.date, what: r.release === 'cpi' ? 'CPI release' : 'Jobs report' })) },
      transform: [{ calculate: 'toDate(datum.date)', as: 'date' }],
      mark: { type: 'rule', strokeWidth: 1.5 },
      encoding: {
        x,
        y: { value: H - 7 },
        y2: { value: H },
        color: { value: tok('ink-2') },
        tooltip: [{ field: 'what', title: 'Event' }],
      },
    });
  }
  if (lineKeys.length) {
    layers.push({
      transform: [{ filter: `indexof(${JSON.stringify(lineKeys)}, datum.series) >= 0` }],
      mark: { type: 'line', strokeWidth: 2, strokeJoin: 'round', strokeCap: 'round' },
      // `segment` breaks the implied-rate line at each decision, where the two-meeting window rolls on
      encoding: { x, color, detail: { field: 'segment' } },
    });
  }
  if (stepKeys.length) {
    layers.push({
      transform: [{ filter: `indexof(${JSON.stringify(stepKeys)}, datum.series) >= 0` }],
      mark: { type: 'line', strokeWidth: 2, interpolate: 'step-after' },
      encoding: { x, color },
    });
  }
  // last value of each series, labelled; labels are nudged apart by rank so close values do not collide
  layers.push(
    {
      transform: [
        { filter: inPanel },
        { joinaggregate: [{ op: 'max', field: 'date', as: 'last' }], groupby: ['series'] },
        { filter: 'datum.date == datum.last' },
      ],
      mark: { type: 'point', filled: true, size: 56, stroke: tok('surface'), strokeWidth: 2, opacity: 1 },
      encoding: { x, color },
    },
    {
      transform: [
        { filter: inPanel },
        { joinaggregate: [{ op: 'max', field: 'date', as: 'last' }], groupby: ['series'] },
        { filter: 'datum.date == datum.last' },
        { window: [{ op: 'row_number', as: 'rk' }], sort: [{ field: 'value', order: 'descending' }] },
        { joinaggregate: [{ op: 'count', as: 'n' }] },
        { calculate: '(datum.rk - (datum.n + 1) / 2) * 12', as: 'off' },
      ],
      mark: { type: 'text', align: 'left', dx: 8, dy: { expr: 'datum.off' }, fontSize: 12, color: tok('ink-2') },
      encoding: { x, text: { field: 'value', type: 'quantitative', format: p.format } },
    },
    {
      transform: [
        { filter: inPanel },
        { pivot: 'series', value: 'value', groupby: ['date'] },
        { calculate: "utcFormat(datum.date, '%b %d, %Y')", as: 'date_label' },
      ],
      params: [{ name: `hover_${panel}`, select: { type: 'point', fields: ['date'], nearest: true, on: 'pointerover', clear: 'pointerout' } }],
      mark: { type: 'rule', strokeDash: [2, 3] },
      encoding: {
        x,
        y: null,
        color: { value: tok('ink-3') },
        opacity: { condition: { param: `hover_${panel}`, empty: false, value: 0.9 }, value: 0 },
        tooltip,
      },
    },
  );
  return {
    $schema: VL_SCHEMA,
    data: { url: '/data/marts/predmarkets/fomc_macro_daily.json' },
    transform: [{ calculate: 'toDate(datum.date)', as: 'date' }],
    height: H,
    encoding: {
      y: {
        field: 'value',
        type: 'quantitative',
        title: p.yTitle,
        scale: { zero: false, nice: true },
        axis: { format: p.format.replace('.2f', '.1f'), tickCount: 4, minExtent: 44, maxExtent: 44 },
      },
    },
    layer: layers,
  };
}
