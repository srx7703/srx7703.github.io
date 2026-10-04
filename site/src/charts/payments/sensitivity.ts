import { VL_SCHEMA, series, tok } from '../theme';

export type SensitivityRow = {
  question: string; waterfall: string; scenario: string; input: string; input_value: number; input_unit: string;
  output: string; output_value: number | null; qualifier: string | null; base_output_value: number | null;
  delta: number | null; band_low: number | null; band_high: number | null; refute_low: number | null;
  refute_high: number | null; verdict: string; note: string | null;
};

type Grid = {
  question: string;
  scenario: (s: string) => boolean;
  outputs: string[];
  /** Series key and label for a row (colour). */
  seriesOf: (r: SensitivityRow) => string;
  x: string; y: string;
  /** A marker for the observed base case, if the grid has one. */
  base?: (r: SensitivityRow) => boolean;
};

const OUTPUT_LABEL: Record<string, string> = {
  bill_net_interchange_rewards_fixed_usd_per_100: 'Rewards fixed in dollars',
  bill_net_interchange_rewards_share_fixed_usd_per_100: 'Rewards fixed as a share',
};

/** The registered grids the page draws (section H: Q4, Q7, Q8). */
export const SENSITIVITY_GRIDS: Record<string, Grid> = {
  q4: {
    question: 'Q4', scenario: (s) => s.startsWith('rewards_share|') || s === 'base', outputs: ['net_interchange_usd_per_100'],
    seriesOf: (r) => (r.scenario === 'base' ? 'Observed (BILL, latest quarter)'
      : `Gross take cut ${r.scenario.split('gross_cut_')[1]?.replace('bps', ' bps') ?? ''}`),
    x: 'Rewards share of card transaction fees (%)', y: 'Net interchange after rewards, $ per $100',
    base: (r) => r.scenario === 'base',
  },
  q7_wise: {
    question: 'Q7', scenario: (s) => s === 'wise_next_take_rate', outputs: ['change_vs_fy2026_usd_per_100'],
    seriesOf: () => 'Wise next reported take rate',
    x: 'Wise take rate in the next half-year report (%)', y: 'Change against FY2026, $ per $100',
  },
  q7_fsb: {
    question: 'Q7', scenario: (s) => s === 'fsb_2026_avg_cost', outputs: ['fsb_avg_cost_usd_per_100'],
    seriesOf: () => 'FSB 2026 average cost',
    x: 'FSB 2026 average cost of a B2B MSME payment (%)', y: 'Industry average cost, $ per $100',
  },
  q8: {
    question: 'Q8', scenario: (s) => s === 'commercial_cnp_cut', outputs: ['interchange_usd_per_100'],
    seriesOf: () => 'Visa commercial CNP rate',
    x: 'Cut to the Visa commercial card-not-present rate (bps)', y: 'Interchange, $ per $100',
  },
  q8_bill: {
    question: 'Q8', scenario: (s) => s === 'commercial_cnp_cut',
    outputs: ['bill_net_interchange_rewards_fixed_usd_per_100', 'bill_net_interchange_rewards_share_fixed_usd_per_100'],
    seriesOf: (r) => OUTPUT_LABEL[r.output] ?? r.output,
    x: 'Cut to the Visa commercial card-not-present rate (bps)', y: 'BILL net interchange after rewards, $ per $100',
  },
};
export type GridKey = keyof typeof SENSITIVITY_GRIDS;

const HOLDS = ['holds', 'inside_band'];
const REFUTED = ['refuted', 'refute_zone'];
export const VERDICT_CLASS = (v: string) => (HOLDS.includes(v) ? 'holds' : REFUTED.includes(v) ? 'refuted' : v === 'info' ? 'info' : 'neither');
export const VERDICT_LABEL: Record<string, string> = { holds: 'Holds', refuted: 'Refuted', neither: 'Neither', info: 'Not graded' };

export function gridRows(rows: SensitivityRow[], key: GridKey) {
  const g = SENSITIVITY_GRIDS[key];
  return rows.filter((r) => r.question === g.question && g.scenario(r.scenario) && g.outputs.includes(r.output) && r.output_value != null);
}

type Zone = { cls: 'holds' | 'refuted'; lo: number; hi: number };

/**
 * Shaded zones, from the data: the registered band where the rows carry one (band_low/band_high),
 * otherwise the span of the rows with that verdict, extended to the axis edge on the side away from
 * the other verdicts. An inner edge snaps to a registered threshold (band or refute field) when one
 * lies between the class's last value and the next value outside it. Ungraded rows draw no zone.
 */
export function gridZones(rows: SensitivityRow[], lo: number, hi: number): Zone[] {
  const graded = rows.filter((r) => r.verdict !== 'info');
  const thresholds = [...new Set(graded.flatMap((r) => [r.band_low, r.band_high, r.refute_low, r.refute_high]).filter((v): v is number => v != null))];
  const zones: Zone[] = [];
  for (const cls of ['holds', 'refuted'] as const) {
    const ins = graded.filter((r) => VERDICT_CLASS(r.verdict) === cls).map((r) => r.output_value as number);
    const outs = graded.filter((r) => VERDICT_CLASS(r.verdict) !== cls).map((r) => r.output_value as number);
    if (!ins.length) continue;
    const band = graded.find((r) => r.band_low != null || r.band_high != null);
    if (cls === 'holds' && band) {
      zones.push({ cls, lo: band.band_low ?? lo, hi: band.band_high ?? hi });
      continue;
    }
    const min = Math.min(...ins); const max = Math.max(...ins);
    const snap = (edge: number, toward: number) => {
      const between = thresholds.filter((t) => (toward > edge ? t >= edge && t < toward : t <= edge && t > toward));
      return between.length ? between.sort((a, b) => Math.abs(a - edge) - Math.abs(b - edge))[0] : edge;
    };
    const below = outs.filter((v) => v < min); const above = outs.filter((v) => v > max);
    if (!outs.length) zones.push({ cls, lo, hi });
    else if (!above.length) zones.push({ cls, lo: snap(min, Math.max(...below)), hi });
    else if (!below.length) zones.push({ cls, lo, hi: snap(max, Math.min(...above)) });
    else zones.push({ cls, lo: snap(min, Math.max(...below)), hi: snap(max, Math.min(...above)) });
  }
  return zones;
}

/**
 * Sensitivity dots for one registered grid: each scenario input on x, the output it implies on y, one
 * line of dots per series. Shaded bands are the registered "holds" zone and the refutation zone,
 * computed from the rows (gridZones). Dot shape says the verdict at that input: filled circle holds,
 * hollow circle neither, cross refuted, small dot not graded. Static: no dashboard group.
 */
export function sensitivitySpec(rows: SensitivityRow[], key: GridKey) {
  const g = SENSITIVITY_GRIDS[key];
  const here = gridRows(rows, key);
  const pts = here.map((r) => ({
    ...r, series: g.seriesOf(r), cls: VERDICT_CLASS(r.verdict), cls_label: VERDICT_LABEL[VERDICT_CLASS(r.verdict)],
    base: g.base ? g.base(r) : false,
  }));
  const ys = pts.map((p) => p.output_value as number);
  const thresholds = here.flatMap((r) => [r.band_low, r.band_high, r.refute_low, r.refute_high]).filter((v): v is number => v != null);
  const all = [...ys, ...thresholds];
  const span = Math.max(...all) - Math.min(...all) || Math.abs(all[0] ?? 1) || 1;
  const lo = Math.min(...all) - span * 0.08;
  const hi = Math.max(...all) + span * 0.08;
  const zones = here.length ? gridZones(here, lo, hi).map((z) => ({ ...z, lo: Math.max(lo, z.lo), hi: Math.min(hi, z.hi), label: z.cls === 'holds' ? 'holds' : 'refuted' })) : [];
  const seriesNames = [...new Set(pts.filter((p) => !p.base).map((p) => p.series))];
  const range = [series.a, series.b, series.c, series.neutral].slice(0, Math.max(1, seriesNames.length));
  const x = { field: 'input_value', type: 'quantitative', title: g.x, scale: { zero: false, nice: false, padding: 10 }, axis: { tickCount: 6 } };
  const y = { field: 'output_value', type: 'quantitative', title: g.y, scale: { domain: [lo, hi], nice: false, zero: false }, axis: { format: '$.2f', tickCount: 5 } };
  const color = {
    field: 'series', type: 'nominal', scale: { domain: seriesNames, range },
    legend: seriesNames.length > 1 ? { title: null, orient: 'top', direction: 'horizontal', labelLimit: 220 } : null,
  };
  return {
    $schema: VL_SCHEMA,
    height: 240,
    layer: [
      {
        data: { values: zones },
        mark: { type: 'rect', opacity: 0.1 },
        encoding: {
          y: { field: 'lo', type: 'quantitative', scale: { domain: [lo, hi] } }, y2: { field: 'hi' },
          color: { field: 'cls', type: 'nominal', scale: { domain: ['holds', 'refuted'], range: [tok('positive'), tok('negative')] }, legend: null },
        },
      },
      {
        data: { values: zones },
        // Zone names sit in the right margin, clear of the dots.
        mark: { type: 'text', align: 'left', baseline: 'top', dx: 6, dy: 2, fontSize: 10.5, fontStyle: 'italic', x: { expr: 'width' } },
        encoding: {
          y: { field: 'hi', type: 'quantitative' },
          text: { field: 'label', type: 'nominal' },
          color: { field: 'cls', type: 'nominal', scale: { domain: ['holds', 'refuted'], range: [tok('positive'), tok('negative')] }, legend: null },
        },
      },
      {
        data: { values: pts },
        layer: [
          {
            transform: [{ filter: '!datum.base' }],
            mark: { type: 'line', strokeWidth: 1.2, opacity: 0.6 },
            encoding: { x, y, color, detail: { field: 'series', type: 'nominal' } },
          },
          {
            transform: [{ filter: '!datum.base' }],
            mark: { type: 'point', strokeWidth: 1.6 },
            encoding: {
              x, y,
              stroke: color,
              fill: { condition: { test: "datum.cls == 'holds' || datum.cls == 'info'", field: 'series', type: 'nominal', scale: { domain: seriesNames, range } }, value: 'transparent' },
              shape: { field: 'cls', type: 'nominal', scale: { domain: ['holds', 'neither', 'refuted', 'info'], range: ['circle', 'circle', 'cross', 'circle'] }, legend: null },
              size: { condition: { test: "datum.cls == 'info'", value: 24 }, value: 60 },
              tooltip: [
                { field: 'series', type: 'nominal', title: 'Series' },
                { field: 'input', type: 'nominal', title: 'Input' },
                { field: 'input_value', type: 'quantitative', title: 'Input value' },
                { field: 'input_unit', type: 'nominal', title: 'Unit' },
                { field: 'output_value', type: 'quantitative', title: '$ per $100', format: '.2f' },
                { field: 'cls_label', type: 'nominal', title: 'Verdict' },
                { field: 'note', type: 'nominal', title: 'Note' },
              ],
            },
          },
          {
            // The observed case, ringed in ink.
            transform: [{ filter: 'datum.base' }],
            mark: { type: 'point', shape: 'diamond', size: 110, filled: false, strokeWidth: 2, color: tok('ink') },
            encoding: {
              x, y,
              tooltip: [
                { field: 'series', type: 'nominal', title: 'Case' },
                { field: 'input_value', type: 'quantitative', title: 'Input value', format: '.1f' },
                { field: 'output_value', type: 'quantitative', title: '$ per $100', format: '.2f' },
                { field: 'cls_label', type: 'nominal', title: 'Verdict' },
              ],
            },
          },
        ],
      },
    ],
    resolve: { scale: { color: 'independent' } },
    padding: { right: 56 },
  };
}

const fmtIn = (r: SensitivityRow) => (r.input_unit === 'bps' ? `${+r.input_value.toFixed(1)} bps` : r.input_unit === 'percent' ? `${+r.input_value.toFixed(2)}%` : `${+r.input_value.toFixed(2)}`);

/** Finding for a grid's figure title, computed from its rows. */
export function sensitivityFinding(rows: SensitivityRow[], key: GridKey) {
  const here = gridRows(rows, key);
  const by = (cls: string) => here.filter((r) => VERDICT_CLASS(r.verdict) === cls).sort((a, b) => a.input_value - b.input_value);
  const holds = by('holds'); const refuted = by('refuted');
  const first = <T,>(a: T[]) => a[0]; const last = <T,>(a: T[]) => a[a.length - 1];
  let title = 'Sensitivity grid';
  if (key === 'q4') {
    const noCut = holds.filter((r) => r.scenario.endsWith('gross_cut_0bps'));
    title = noCut.length
      ? `Net interchange stays inside the registered band for rewards shares from ${fmtIn(first(noCut))} to ${fmtIn(last(noCut))} with no cut to gross take`
      : 'Net interchange leaves the registered band at every rewards share';
  } else if (key === 'q7_wise') {
    title = holds.length && refuted.length
      ? `The Wise leg of Q7 holds at a next take rate of ${fmtIn(last(holds))} or lower and is refuted at ${fmtIn(first(refuted))} or higher`
      : 'The Wise leg of Q7 across next-period take rates';
  } else if (key === 'q7_fsb') {
    title = holds.length && refuted.length
      ? `The FSB leg of Q7 holds if the 2026 average cost is ${fmtIn(first(holds))} or more and is refuted at ${fmtIn(last(refuted))} or less`
      : 'The FSB leg of Q7 across 2026 average costs';
  } else if (key === 'q8') {
    title = holds.length && refuted.length
      ? `The registered floor holds through a ${fmtIn(last(holds))} cut and is refuted from ${fmtIn(first(refuted))}`
      : 'Q8 across cuts to the commercial rate';
  } else if (key === 'q8_bill') {
    const end = here.filter((r) => r.input_value === Math.max(...here.map((x) => x.input_value)));
    const vals = end.map((r) => r.output_value as number);
    title = end.length
      ? `At a ${fmtIn(end[0])} cut, BILL would keep $${Math.min(...vals).toFixed(2)} to $${Math.max(...vals).toFixed(2)} per $100 after rewards`
      : 'BILL net interchange across cuts to the commercial rate';
  }
  return { title, holds: holds.length, refuted: refuted.length, rows: here.length };
}

export function sensitivitySubtitle(key: GridKey): string {
  const g = SENSITIVITY_GRIDS[key];
  return `${g.y} for each registered input on the ${g.question} grid. Filled circle: holds; hollow: neither; cross: refuted; small dot: not graded. `
    + 'Green band: the registered "holds" zone; red band: the refutation zone, both from the evaluation plan. These are scenarios, not forecasts.';
}
