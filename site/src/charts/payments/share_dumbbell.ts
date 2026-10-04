import { VL_SCHEMA, series, tok } from '../theme';
import { type ShareRow, LENS_LABEL, SEGMENT_LABEL, dumbbellData, largestLensGap, shareDefaults, NOTE_ROW, wrapRows } from './share_data';


/** Lens colours: generic slots, not brand colours; fixed order so the legend never reshuffles. */
export const LENS_DOMAIN = Object.values(LENS_LABEL);
export const LENS_RANGE = [series.a, series.b];

/**
 * Share lens, second chart: the same company's share through each lens, joined by a line.
 *
 * This is the chart questions Q1 and Q2 are about. A disclosed-pool share is a share of whoever
 * chose to report, so it flatters the companies that do; an official denominator is a share of a
 * government total, so it is narrower in scope. When the two disagree, the disagreement is the
 * finding. The line is a gap between two readings of one company, not a sum or a change over time,
 * and nothing is drawn for a lens without a share.
 *
 * Both lenses are read at a period they share where one exists. A segment where fewer than two lenses
 * hold shares shows the refusal sentences instead, so the chart is never empty.
 *
 * Driven by the unbound `segment` param; `lens` is declared too and brings the selected lens's
 * dots forward.
 */
export function shareDumbbellSpec(rows: ShareRow[], opts: { segment?: string; lens?: string } = {}) {
  const d = shareDefaults(rows);
  const segment = opts.segment ?? d.segment;
  const lens = opts.lens ?? d.lens;
  const { points, notes } = dumbbellData(rows);
  // Order companies within a segment by their larger share, so the widest readings sit on top.
  const maxBy = new Map<string, number>();
  points.forEach((p) => maxBy.set(`${p.segment}|${p.company}`, Math.max(maxBy.get(`${p.segment}|${p.company}`) ?? 0, p.value)));
  const values = [
    ...wrapRows(notes.map((n) => ({ segment: n.segment, company: NOTE_ROW, kind: 'note', note: n.text as string | null, order: -1 }))),
    ...points.map((p) => ({ ...p, kind: 'point', order: -(maxBy.get(`${p.segment}|${p.company}`) ?? 0) })),
  ];
  const y = {
    field: 'company', type: 'nominal', title: null,
    sort: { field: 'order', op: 'min', order: 'ascending' },
    axis: { labelLimit: 140, labelFontSize: 11, ticks: false, domain: false, grid: true, gridColor: tok('grid') },
  };
  const x = {
    field: 'value', type: 'quantitative', title: 'Share of each lens\'s own denominator',
    scale: { domain: [0, 1] }, axis: { format: '.0%', tickCount: 5 },
  };
  return {
    $schema: VL_SCHEMA,
    height: { step: 22 },
    params: [
      { name: 'segment', value: segment },
      { name: 'lens', value: lens },
    ],
    data: { values },
    transform: [{ filter: 'datum.segment == segment' }],
    encoding: { y },
    layer: [
      {
        transform: [
          { filter: "datum.kind == 'point'" },
          { aggregate: [{ op: 'min', field: 'value', as: 'lo' }, { op: 'max', field: 'value', as: 'hi' }, { op: 'count', as: 'n' }, { op: 'min', field: 'order', as: 'order' }], groupby: ['company'] },
          { filter: 'datum.n > 1' },
        ],
        mark: { type: 'rule', strokeWidth: 2, color: tok('ink-3'), opacity: 0.6 },
        encoding: { x: { field: 'lo', type: 'quantitative', scale: { domain: [0, 1] } }, x2: { field: 'hi' } },
      },
      {
        transform: [{ filter: "datum.kind == 'point'" }, { calculate: 'datum.lens == lens', as: 'is_lens' }],
        mark: { type: 'point', shape: 'circle', filled: true, size: 90, stroke: tok('bg'), strokeWidth: 1 },
        encoding: {
          x,
          color: {
            field: 'lens_label', type: 'nominal', scale: { domain: LENS_DOMAIN, range: LENS_RANGE },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' },
          },
          opacity: { condition: { test: 'datum.is_lens', value: 1 }, value: 0.55 },
          order: { field: 'is_lens', type: 'ordinal' },
          tooltip: [
            { field: 'company', type: 'nominal', title: 'Company' },
            { field: 'lens_label', type: 'nominal', title: 'Lens' },
            { field: 'value', type: 'quantitative', title: 'Share', format: '.1%' },
            { field: 'qualifier', type: 'nominal', title: 'Qualifier' },
            { field: 'period', type: 'nominal', title: 'Period' },
          ],
        },
      },
      {
        // The period both lenses are read at, and any company seen through one lens only; or, where
        // there is nothing to compare, the refusal sentences.
        transform: [{ filter: "datum.kind == 'note'" }],
        mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 11, fontStyle: 'italic', color: tok('ink-2') },
        encoding: {
          x: { value: 2 },
          text: { field: 'line', type: 'nominal' },
          tooltip: [{ field: 'note', type: 'nominal', title: 'Note' }],
        },
      },
    ],
  };
}

/**
 * Finding for the dumbbell's figure title: the widest disagreement between lenses, from the mart.
 * With `segment`, only that segment is read (the chart filters on the `segment` param, so the title
 * must follow it); when no company there has a share through both lenses, the title says why.
 */
export function shareDumbbellFinding(rows: ShareRow[], segment?: string) {
  const pts = (v: number) => Math.round(v * 100);
  if (segment == null) {
    const gap = largestLensGap(rows);
    if (!gap) return { title: 'No company has a share through more than one lens', gap: null };
    const seg = SEGMENT_LABEL[gap.segment] ?? gap.segment;
    return {
      title: `${gap.company}'s share of ${seg.toLowerCase()} reads ${pts(gap.low.value)}% through the ${gap.low.lens_label.toLowerCase()} lens and ${pts(gap.high.value)}% through the ${gap.high.lens_label.toLowerCase()} lens`,
      gap,
    };
  }
  const segName = SEGMENT_LABEL[segment] ?? segment;
  const { points, notes } = dumbbellData(rows, [segment]);
  const byCompany = new Map<string, typeof points>();
  points.forEach((p) => byCompany.set(p.company, [...(byCompany.get(p.company) ?? []), p]));
  let gap: { segment: string; company: string; low: (typeof points)[number]; high: (typeof points)[number]; gap: number } | null = null;
  for (const [company, ps] of byCompany) {
    if (ps.length < 2) continue;
    const s = [...ps].sort((a, b) => a.value - b.value);
    const g = s[s.length - 1].value - s[0].value;
    if (!gap || g > gap.gap) gap = { segment, company, low: s[0], high: s[s.length - 1], gap: g };
  }
  if (!gap) {
    let reason: string;
    if (!points.length) {
      const text = notes.find((n) => n.segment === segment)?.text ?? '';
      reason = text.replace(/^Nothing to compare across lenses for [^.]*\.\s*/, '').replace(/\.$/, '') || 'no rows published';
    } else {
      reason = 'each company with a share appears through one lens only';
    }
    return { title: `No company in ${segName.toLowerCase()} has a share through both lenses: ${reason}`, gap: null };
  }
  return {
    title: `${gap.company}'s share of ${segName.toLowerCase()} reads ${pts(gap.low.value)}% through the ${gap.low.lens_label.toLowerCase()} lens and ${pts(gap.high.value)}% through the ${gap.high.lens_label.toLowerCase()} lens`,
    gap,
  };
}

/** Subtitle for the dumbbell figure. */
export function shareDumbbellSubtitle(): string {
  return 'Each company\'s share through each lens, 0 to 100% of that lens\'s own denominator, read at a period both lenses share where one exists. '
    + 'The line is the gap between two readings, not a sum or a trend. Segments with shares in fewer than two lenses say why.';
}
