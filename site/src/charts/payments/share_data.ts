/**
 * Share lens (dashboard D2): the derivation shared by its two charts and its linked table.
 *
 * Source: `data/marts/payments/share_lenses.json` (one row per segment x lens x company x period,
 * with a role of member, excluded, marker or refused) and `share_pool_hhi.json` (concentration inside
 * each pool). A share is only ever a share of its own lens's denominator: lenses are shown side by
 * side and never added, averaged or netted. Every refusal the pipeline wrote is kept and printed as
 * a sentence, so a segment-and-lens combination with no shares still says why.
 */

export type ShareRow = {
  segment: string; lens: string; pool: string; company: string | null; period: string | null;
  value: number | null; unit: string; qualifier: string | null; role: string; basis?: string;
  refusal_reason: string | null; source_metric: string | null; stale: boolean;
};

export type PoolHhiRow = {
  segment: string; lens: string; pool: string; period: string; n_members: number; full_membership: boolean;
  members: string; excluded: string; pool_total: number; unit: string; total_qualifier: string;
  hhi: number | null; qualifier: string | null; basis?: string; refusal_reason: string | null;
};

export const SEGMENT_LABEL: Record<string, string> = {
  A: 'Merchant acquiring',
  B: 'Buy now, pay later',
  C: 'Corporate cards and spend',
  D: 'Cross-border and treasury',
  E: 'Issuing infrastructure',
  F: 'Stablecoins',
};

/** Control value -> mart lens. Short keys keep the shareable URL readable (?share.lens=official). */
export const LENS_KEY: Record<string, string> = { disclosed_pool: 'pool', official_denominator: 'official' };
export const LENS_LABEL: Record<string, string> = {
  pool: 'Disclosed pool',
  official: 'Official denominator',
};
export const LENS_NOTE: Record<string, string> = {
  pool: 'share of what the disclosing companies themselves report, added up',
  official: 'share of a government total (Federal Reserve, Census)',
};

const lensKey = (lens: string) => LENS_KEY[lens] ?? lens;

/** Sort key for the mixed period labels in the mart (2026-Q2, FY2025, 2026-03); null sorts first. */
export function periodKey(period: string | null): number {
  if (!period) return -Infinity;
  let m = /^(\d{4})-Q([1-4])$/.exec(period);
  if (m) return Number(m[1]) + Number(m[2]) / 4;
  m = /^FY(\d{4})$/.exec(period);
  if (m) return Number(m[1]) + 1 - 0.001; // a full year ranks just behind its own fourth quarter
  m = /^(\d{4})-(\d{2})$/.exec(period);
  if (m) return Number(m[1]) + Number(m[2]) / 12;
  return -Infinity;
}

/** The period each segment x lens is shown at: the latest one with at least one member share. */
export function displayPeriods(rows: ShareRow[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const r of rows) {
    if (r.role !== 'member' || r.value == null || !r.period) continue;
    const k = `${r.segment}|${lensKey(r.lens)}`;
    const prev = out.get(k);
    if (!prev || periodKey(r.period) > periodKey(prev)) out.set(k, r.period);
  }
  return out;
}

export type ShareBarRow = {
  segment: string; lens: string; company: string; period: string | null;
  kind: 'member' | 'note';
  value: number | null; qualifier: string | null;
  /** Shown beside the axis for a member, and as the row's sentence for anything else. */
  note: string | null;
  basis: string | null;
  order: number;
};

const SEGMENT_ROW = 'Whole segment';

/** Row label for a panel's heading sentence (zero-width, so it prints no axis label). */
export const NOTE_ROW = '\u200b';

/** Characters per line when a sentence is printed inside a chart: about 165px of 11px serif, which fits the plot left of a 140px label column on a 360px phone. Used on every viewport so no text mark needs a limit. */
export const WRAP_CHARS = 30;

/** Greedy word wrap, so a refusal sentence is printed whole rather than cut off with an ellipsis. */
export function wrapText(text: string, width = WRAP_CHARS): string[] {
  const out: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) { out.push(line); line = word; }
    else line = line ? `${line} ${word}` : word;
  }
  if (line) out.push(line);
  return out.length ? out : [''];
}

/**
 * Expand rows carrying a sentence into one row per wrapped line. Continuation lines get a distinct
 * zero-width label (so the y axis prints nothing) and an order just after their parent row.
 */
export function wrapRows<T extends { company: string; note: string | null; order: number }>(rows: T[], width = WRAP_CHARS): (T & { line: string })[] {
  let k = 0;
  return rows.flatMap((r) => {
    if (r.note == null) return [{ ...r, line: '' }];
    return wrapText(r.note, width).map((line, i) => (i === 0
      ? { ...r, line }
      : { ...r, company: NOTE_ROW.repeat(2 + k++), line, order: r.order + i / 100 }));
  });
}

const usdShort = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (a >= 1e9) return `$${Math.round(v / 1e9)}B`;
  return `$${Math.round(v / 1e6)}M`;
};

/**
 * One row per company for every segment x lens: a bar if the company has a share in the period
 * shown, otherwise a sentence (excluded from the pool, refused, or a volume stated outside the pool).
 * A combination with no company rows at all gets one "Whole segment" sentence from the refusal the
 * pipeline wrote, or a generic one if it wrote none, so no combination renders an empty chart.
 */
export function shareBarRows(rows: ShareRow[], segments = Object.keys(SEGMENT_LABEL)): ShareBarRow[] {
  const periods = displayPeriods(rows);
  const out: ShareBarRow[] = [];
  for (const segment of segments) {
    for (const lens of Object.keys(LENS_LABEL)) {
      const here = rows.filter((r) => r.segment === segment && lensKey(r.lens) === lens);
      const period = periods.get(`${segment}|${lens}`) ?? null;
      const members = here
        .filter((r) => r.role === 'member' && r.value != null && r.period === period && r.company)
        .sort((a, b) => (b.value as number) - (a.value as number));
      const seen = new Set<string>();
      members.forEach((r, i) => {
        seen.add(r.company as string);
        out.push({
          segment, lens, company: r.company as string, period, kind: 'member',
          value: r.value, qualifier: r.qualifier, note: null, basis: r.basis ?? null, order: i,
        });
      });
      // Non-members: the row nearest the period shown, one per company.
      const best = new Map<string, ShareRow>();
      for (const raw of here) {
        // A member row without a value is a refusal (a one-company pool, for instance).
        if (raw.role === 'member' && raw.value != null) continue;
        const r = raw.role === 'member' ? { ...raw, role: 'refused' } : raw;
        const name = r.company ?? SEGMENT_ROW;
        if (seen.has(name)) continue;
        const prev = best.get(name);
        const score = (x: ShareRow) => (x.period === period ? Infinity : periodKey(x.period));
        if (!prev || score(r) > score(prev)) best.set(name, r);
      }
      // Members of this lens in other periods that are absent from the period shown.
      for (const r of here) {
        if (r.role !== 'member' || r.value == null || !r.company || seen.has(r.company)) continue;
        const prev = best.get(r.company);
        if (prev && prev.role !== 'absent') continue;
        if (!prev || periodKey(r.period) > periodKey(prev.period)) best.set(r.company, { ...r, role: 'absent' });
      }
      let i = members.length;
      const rank: Record<string, number> = { marker: 0, absent: 1, excluded: 2, refused: 3 };
      const pending: ShareBarRow[] = [];
      [...best.entries()]
        .sort((a, b) => (rank[a[1].role] ?? 9) - (rank[b[1].role] ?? 9) || a[0].localeCompare(b[0]))
        .forEach(([name, r]) => {
          let note: string;
          if (r.role === 'marker' && r.value != null) {
            note = `outside the pool: states ${r.qualifier && r.qualifier !== '=' ? `${r.qualifier} ` : ''}${usdShort(r.value)} of volume (${r.period}), not comparable with pool members`;
          } else if (r.role === 'absent') {
            note = `no share for ${period ?? 'this period'}; last in the pool for ${r.period}`;
          } else {
            note = `${r.role === 'excluded' ? 'not in the pool' : 'no share'}: ${r.refusal_reason ?? 'no reason recorded'}`;
          }
          pending.push({ segment, lens, company: name, period: r.period, kind: 'note', value: null, qualifier: null, note, basis: r.basis ?? null, order: i++ });
        });
      // Companies refused for the same reason share one sentence, so the reason is read once.
      const byNote = new Map<string, ShareBarRow[]>();
      pending.forEach((r) => byNote.set(r.note as string, [...(byNote.get(r.note as string) ?? []), r]));
      for (const group of byNote.values()) {
        const [first] = group;
        if (group.length === 1) { out.push(first); continue; }
        const names = group.map((g) => g.company);
        out.push({
          ...first,
          company: `${group.length} companies`,
          note: `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}: ${first.note}`,
        });
      }
      if (!members.length && !best.size) {
        out.push({
          segment, lens, company: SEGMENT_ROW, period: null, kind: 'note', value: null, qualifier: null,
          note: `no share: the pipeline published no ${LENS_LABEL[lens].toLowerCase()} rows for this segment`,
          basis: null, order: 0,
        });
      }
    }
  }
  return out;
}

export type HhiNote = { segment: string; lens: string; period: string | null; text: string; hhi: number | null; n_members: number | null };

/** One concentration sentence per segment x lens, at the period the bars show. */
export function hhiNotes(rows: ShareRow[], hhi: PoolHhiRow[], segments = Object.keys(SEGMENT_LABEL)): HhiNote[] {
  const periods = displayPeriods(rows);
  const out: HhiNote[] = [];
  for (const segment of segments) {
    for (const lens of Object.keys(LENS_LABEL)) {
      const period = periods.get(`${segment}|${lens}`) ?? null;
      const h = hhi.find((x) => x.segment === segment && lensKey(x.lens) === lens && x.period === period);
      let text: string;
      if (!period) text = '';
      else if (!h) text = `No concentration index published for ${period}.`;
      else if (h.hhi == null) text = `HHI not computed: ${h.refusal_reason ?? 'no reason recorded'}.`;
      else text = `HHI inside this pool, ${period}: ${Math.round(h.hhi).toLocaleString('en-US')} of 10,000 across ${h.n_members} members${h.full_membership ? '' : ' (partial membership)'}${h.qualifier && h.qualifier !== '=' ? ', approximate' : ''}.`;
      out.push({ segment, lens, period, text, hhi: h?.hhi ?? null, n_members: h?.n_members ?? null });
    }
  }
  return out;
}

export type DumbbellRow = {
  segment: string; company: string; lens: string; lens_label: string; period: string; value: number; qualifier: string | null;
};
export type DumbbellNote = { segment: string; text: string };

/**
 * The cross-lens comparison for each segment, at one period both lenses share where there is one
 * (otherwise each lens at its own latest period, and the note says so). Only member shares are
 * plotted; a segment with fewer than two lenses holding shares gets a sentence instead.
 */
export function dumbbellData(rows: ShareRow[], segments = Object.keys(SEGMENT_LABEL)) {
  const points: DumbbellRow[] = [];
  const notes: DumbbellNote[] = [];
  const lenses = Object.keys(LENS_LABEL);
  for (const segment of segments) {
    const members = rows.filter((r) => r.segment === segment && r.role === 'member' && r.value != null && r.company && r.period);
    const periodsByLens = new Map(lenses.map((l) => [l, new Set(members.filter((r) => lensKey(r.lens) === l).map((r) => r.period as string))]));
    const withData = lenses.filter((l) => (periodsByLens.get(l)?.size ?? 0) > 0);
    if (withData.length < 2) {
      const missing = lenses.filter((l) => !withData.includes(l));
        const reasons = missing.map((l) => {
        const seg = rows.find((r) => r.segment === segment && lensKey(r.lens) === l && r.role === 'refused' && !r.company);
        const any = rows
          .filter((r) => r.segment === segment && lensKey(r.lens) === l && r.refusal_reason)
          .sort((a, b) => periodKey(b.period) - periodKey(a.period))[0];
        return { lens: LENS_LABEL[l], reason: (seg ?? any)?.refusal_reason ?? 'no rows published' };
      });
      const same = reasons.length > 1 && reasons.every((r) => r.reason === reasons[0].reason);
      const said = same ? `Both lenses: ${reasons[0].reason}` : reasons.map((r) => `${r.lens}: ${r.reason}`).join('. ');
      notes.push({ segment, text: `Nothing to compare across lenses for ${SEGMENT_LABEL[segment] ?? segment}. ${said}.` });
      continue;
    }
    const common = [...(periodsByLens.get(lenses[0]) as Set<string>)]
      .filter((p) => lenses.every((l) => periodsByLens.get(l)?.has(p)))
      .sort((a, b) => periodKey(b) - periodKey(a))[0];
    const pick = (l: string) => common ?? [...(periodsByLens.get(l) as Set<string>)].sort((a, b) => periodKey(b) - periodKey(a))[0];
    for (const l of lenses) {
      const p = pick(l);
      members.filter((r) => lensKey(r.lens) === l && r.period === p).forEach((r) => points.push({
        segment, company: r.company as string, lens: l, lens_label: LENS_LABEL[l], period: p, value: r.value as number, qualifier: r.qualifier,
      }));
    }
    // Companies that appear in only one lens are named, so a lone dot is not read as agreement.
    const byCompany = new Map<string, Set<string>>();
    points.filter((p) => p.segment === segment).forEach((p) => byCompany.set(p.company, (byCompany.get(p.company) ?? new Set()).add(p.lens)));
    const lone = [...byCompany].filter(([, s]) => s.size < lenses.length).map(([c, s]) => `${c} (${LENS_LABEL[[...s][0]].toLowerCase()} only)`);
    const when = common ? `Both lenses at ${common}.` : `Each lens at its latest period: ${lenses.map((l) => `${LENS_LABEL[l].toLowerCase()} ${pick(l)}`).join(', ')}.`;
    notes.push({ segment, text: `${when}${lone.length ? ` One lens only: ${lone.join(', ')}.` : ''}` });
  }
  return { points, notes };
}

/** Segments that have at least one row in the mart, as `<DashboardControls>` options. */
export function segmentOptions(rows: ShareRow[]) {
  const present = new Set(rows.map((r) => r.segment));
  return Object.keys(SEGMENT_LABEL).filter((s) => present.has(s)).map((s) => ({ value: s, label: `${s}. ${SEGMENT_LABEL[s]}` }));
}

/**
 * The default view: the segment where the two lenses disagree most for one company (the finding
 * the dumbbell exists for), shown through the official lens. Falls back to segment A, disclosed pool.
 */
export function shareDefaults(rows: ShareRow[]): { segment: string; lens: string } {
  const gap = largestLensGap(rows);
  return gap ? { segment: gap.segment, lens: 'official' } : { segment: 'A', lens: 'pool' };
}

/** The `controls` prop for `<DashboardControls group="share">`. */
export function shareControls(rows: ShareRow[]) {
  const d = shareDefaults(rows);
  return [
    { name: 'segment', label: 'Segment', options: segmentOptions(rows), default: d.segment, type: 'select' as const },
    { name: 'lens', label: 'Lens', options: Object.entries(LENS_LABEL).map(([value, label]) => ({ value, label })), default: d.lens },
  ];
}

/** The widest disagreement between lenses for one company, from the dumbbell's own points. */
export function largestLensGap(rows: ShareRow[]) {
  const { points } = dumbbellData(rows);
  const byKey = new Map<string, DumbbellRow[]>();
  points.forEach((p) => byKey.set(`${p.segment}|${p.company}`, [...(byKey.get(`${p.segment}|${p.company}`) ?? []), p]));
  let best: { segment: string; company: string; low: DumbbellRow; high: DumbbellRow; gap: number } | null = null;
  for (const [, ps] of byKey) {
    if (ps.length < 2) continue;
    const s = [...ps].sort((a, b) => a.value - b.value);
    const gap = s[s.length - 1].value - s[0].value;
    if (!best || gap > best.gap) best = { segment: s[0].segment, company: s[0].company, low: s[0], high: s[s.length - 1], gap };
  }
  return best;
}

/**
 * Rows for the linked DataTable (filterKeys: ['segment', 'lens']): one per segment x lens x company
 * at the period shown, with the denominator and whether the share is a share of a pool.
 */
export function shareTableRows(rows: ShareRow[]) {
  return shareBarRows(rows).map((r) => ({
    segment: r.segment,
    lens: r.lens,
    segment_label: SEGMENT_LABEL[r.segment] ?? r.segment,
    lens_label: LENS_LABEL[r.lens] ?? r.lens,
    company: r.company,
    period: r.period ?? '—',
    share: r.value,
    qualifier: r.qualifier ?? '',
    in_pool: r.lens === 'pool' ? 'yes, share of the disclosed pool only' : 'no, share of an official total',
    denominator: r.basis ?? '—',
    note: r.note ?? '',
  }));
}
