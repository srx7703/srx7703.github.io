import { VL_SCHEMA, series, tok } from '../theme';
import {
  type ShareRow, type PoolHhiRow, LENS_LABEL, SEGMENT_LABEL,
  shareBarRows, hhiNotes, shareDefaults, displayPeriods, NOTE_ROW, wrapRows,
} from './share_data';


/**
 * Share lens, first chart: each company's share in the selected segment and lens, at the latest
 * period that lens has shares for.
 *
 * Every company the pipeline knows about in that segment keeps its row. A member gets a bar; a company
 * that is excluded from the pool, refused by the lens, or only states a volume outside the pool gets
 * its reason as a sentence in the same row, so the reader sees who is missing and why rather than a
 * pool that silently looks complete. A segment and lens with no shares at all (corporate cards
 * through the official lens, for instance) shows the refusal sentence and nothing else.
 *
 * The first row is the pool's concentration (HHI) at the same period, or the reason it is not
 * computed. Shares are on a fixed 0-100% axis. Lenses are never combined: the chart shows one at a
 * time and the dumbbell beside it sets them side by side.
 *
 * Driven by the unbound params `segment` (A-F) and `lens` (`pool` | `official`).
 */
export function shareBarsSpec(rows: ShareRow[], hhi: PoolHhiRow[], opts: { segment?: string; lens?: string } = {}) {
  const d = shareDefaults(rows);
  const segment = opts.segment ?? d.segment;
  const lens = opts.lens ?? d.lens;
  const bars = shareBarRows(rows);
  const notes = hhiNotes(rows, hhi)
    .filter((n) => n.text)
    .map((n) => ({
      segment: n.segment, lens: n.lens, company: NOTE_ROW, period: n.period, kind: 'hhi',
      value: null, qualifier: null, note: n.text, basis: null, order: -1,
    }));
  const values = wrapRows([...notes, ...bars]).map((r) => ({
    ...r,
    lens_label: LENS_LABEL[r.lens] ?? r.lens,
    segment_label: SEGMENT_LABEL[r.segment] ?? r.segment,
  }));
  const y = {
    field: 'company', type: 'nominal', title: null,
    sort: { field: 'order', op: 'min', order: 'ascending' },
    axis: { labelLimit: 140, labelFontSize: 11, ticks: false, domain: false },
  };
  const x = {
    field: 'value', type: 'quantitative', title: 'Share of the lens denominator',
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
    transform: [{ filter: 'datum.segment == segment && datum.lens == lens' }],
    encoding: { y },
    layer: [
      {
        transform: [{ filter: "datum.kind == 'member'" }],
        mark: { type: 'bar', height: 13, cornerRadiusEnd: 2, color: series.a },
        encoding: {
          x,
          tooltip: [
            { field: 'company', type: 'nominal', title: 'Company' },
            { field: 'value', type: 'quantitative', title: 'Share', format: '.1%' },
            { field: 'qualifier', type: 'nominal', title: 'Qualifier' },
            { field: 'period', type: 'nominal', title: 'Period' },
            { field: 'lens_label', type: 'nominal', title: 'Lens' },
            { field: 'basis', type: 'nominal', title: 'Denominator' },
          ],
        },
      },
      {
        transform: [
          { filter: "datum.kind == 'member'" },
          { calculate: "(datum.qualifier && datum.qualifier != '=' ? datum.qualifier + ' ' : '') + format(datum.value, '.0%')", as: 'label' },
        ],
        mark: { type: 'text', align: 'left', dx: 4, fontSize: 11, color: tok('ink-2') },
        encoding: { x, text: { field: 'label', type: 'nominal' } },
      },
      {
        // The concentration sentence that heads the panel.
        transform: [{ filter: "datum.kind == 'hhi'" }],
        mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 11, color: tok('ink-2') },
        encoding: { x: { value: 2 }, text: { field: 'line', type: 'nominal' }, tooltip: { field: 'note', type: 'nominal', title: 'Concentration' } },
      },
      {
        // Reasons, in words, for every row without a share.
        transform: [{ filter: "datum.kind == 'note'" }],
        mark: { type: 'text', align: 'left', baseline: 'middle', fontSize: 11, fontStyle: 'italic', color: tok('ink-3') },
        encoding: {
          x: { value: 2 },
          text: { field: 'line', type: 'nominal' },
          tooltip: [
            { field: 'company', type: 'nominal', title: 'Company' },
            { field: 'note', type: 'nominal', title: 'Why there is no bar' },
            { field: 'period', type: 'nominal', title: 'Period' },
          ],
        },
      },
    ],
  };
}

/**
 * Finding for the bars' figure title at the default view (the figure title is static; the chart
 * itself follows the controls). Computed from the mart.
 */
export function shareBarsFinding(rows: ShareRow[]) {
  const d = shareDefaults(rows);
  const period = displayPeriods(rows).get(`${d.segment}|${d.lens}`) ?? null;
  const bars = shareBarRows(rows).filter((r) => r.segment === d.segment && r.lens === d.lens);
  const members = bars.filter((r) => r.kind === 'member');
  const top = members[0];
  const segment = SEGMENT_LABEL[d.segment] ?? d.segment;
  const title = top && top.value != null
    ? `${top.company} has the largest share in ${segment.toLowerCase()} through the ${LENS_LABEL[d.lens].toLowerCase()} lens: ${Math.round(top.value * 100)}% (${period})`
    : `${segment}: no shares through the ${LENS_LABEL[d.lens].toLowerCase()} lens`;
  return { title, segment: d.segment, lens: d.lens, period, members: members.length, notes: bars.length - members.length, top: top ?? null };
}

/** Subtitle for the bars figure. */
export function shareBarsSubtitle(): string {
  return 'Share of the selected lens\'s denominator, 0 to 100%, at the latest period that lens has shares for. '
    + 'Disclosed pool: share of what the disclosing companies report, added up, each in its own volume definition. '
    + 'Official denominator: share of a government total. Rows without a bar say why. The two lenses are never added together.';
}
