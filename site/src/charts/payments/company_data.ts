/**
 * Company explorer (dashboard D1): the derivation shared by its two charts and its linked table.
 *
 * Everything here comes from `data/marts/payments/take_rates.json`, where each row is one filed ratio
 * with its numerator (a revenue line) and denominator (a volume line) carried beside it. So the three
 * metrics the explorer offers are read off the same filed rows: volume is the denominator, revenue is
 * the numerator, take rate is the ratio. Nothing is recomputed and nothing is converted.
 *
 * Two choices are made once, here, so both charts and the table agree:
 * - one headline ratio per company (gross take rate before net before margin retention), because a
 *   company with two ratios would otherwise draw two volume lines that are the same line;
 * - one cadence per company (quarters where filed, otherwise half-years), because a half-year beside a
 *   quarter draws a jump in volume that did not happen. Annual totals are dropped where quarters exist.
 */
import { SEGMENT_LABEL } from './share_data';

export type TakeRateRow = {
  company: string; segment: string; ratio: string; ratio_kind: string; scope: string; basis: string;
  period: string; as_of: string; currency: string;
  numerator_metric: string; numerator_value: number; numerator_qualifier: string;
  denominator_metric: string; denominator_value: number; denominator_qualifier: string;
  take_rate_pct: number; qualifier: string; tag: string; chartable: boolean; stale: boolean;
  numerator_source_url?: string; denominator_source_url?: string;
};

export type Cadence = 'quarter' | 'half' | 'year';

export type CompanyPoint = {
  company: string; segment: string; segment_label: string;
  ratio: string; ratio_kind: string; ratio_kind_label: string; scope: string;
  numerator_metric: string; denominator_metric: string;
  period: string; as_of: string; currency: string; cadence: Cadence;
  /** `currency|cadence`: volumes and revenues are only drawn against peers that share it. */
  cmp: string;
  volume: number; revenue: number; take_rate_pct: number;
  /** `=` when both lines are filed exact, `~` when either is approximate or bounded. */
  qualifier: string;
  source_url: string | null;
};

export const RATIO_KIND_LABEL: Record<string, string> = {
  gross_take_rate: 'Gross take rate (revenue / volume)',
  net_take_rate: 'Net take rate (net revenue / volume)',
  margin_retention: 'Margin retained (gross profit or margin / volume)',
};
const RATIO_KIND_RANK: Record<string, number> = { gross_take_rate: 0, net_take_rate: 1, margin_retention: 2 };

export const METRICS = [
  { value: 'volume', label: 'Volume' },
  { value: 'revenue', label: 'Revenue' },
  { value: 'take_rate', label: 'Take rate' },
] as const;
export type Metric = (typeof METRICS)[number]['value'];

export const cadenceOf = (period: string): Cadence =>
  /Q\d$/.test(period) ? 'quarter' : /H\d/.test(period) ? 'half' : 'year';

const CADENCE_LABEL: Record<Cadence, string> = { quarter: 'quarterly', half: 'half-yearly', year: 'annual' };

/** Chartable, non-stale rows only; the mart keeps the rest for the table of refusals. */
function usable(rows: TakeRateRow[]): TakeRateRow[] {
  return rows.filter((r) => r.chartable && !r.stale && r.numerator_value > 0 && r.denominator_value > 0);
}

/** The ratio each company is drawn with: gross before net before margin, then the most periods. */
export function headlineRatios(rows: TakeRateRow[]): Map<string, string> {
  const byCompany = new Map<string, Map<string, { kind: string; n: number }>>();
  for (const r of usable(rows)) {
    const m = byCompany.get(r.company) ?? new Map();
    const e = m.get(r.ratio) ?? { kind: r.ratio_kind, n: 0 };
    e.n += 1;
    m.set(r.ratio, e);
    byCompany.set(r.company, m);
  }
  const out = new Map<string, string>();
  for (const [company, ratios] of byCompany) {
    const best = [...ratios].sort((a, b) =>
      (RATIO_KIND_RANK[a[1].kind] ?? 9) - (RATIO_KIND_RANK[b[1].kind] ?? 9) || b[1].n - a[1].n || a[0].localeCompare(b[0]))[0];
    out.set(company, best[0]);
  }
  return out;
}

/** One series per company: its headline ratio, at its finest filed cadence, oldest first. */
export function companyPoints(rows: TakeRateRow[]): CompanyPoint[] {
  const head = headlineRatios(rows);
  const picked = usable(rows).filter((r) => head.get(r.company) === r.ratio);
  const cadenceByCompany = new Map<string, Cadence>();
  for (const r of picked) {
    const c = cadenceOf(r.period);
    const prev = cadenceByCompany.get(r.company);
    const order: Cadence[] = ['quarter', 'half', 'year'];
    if (!prev || order.indexOf(c) < order.indexOf(prev)) cadenceByCompany.set(r.company, c);
  }
  return picked
    .filter((r) => cadenceOf(r.period) === cadenceByCompany.get(r.company))
    .map((r) => {
      const cadence = cadenceOf(r.period);
      const exact = r.numerator_qualifier === '=' && r.denominator_qualifier === '=' && r.qualifier === '=';
      return {
        company: r.company,
        segment: r.segment,
        segment_label: SEGMENT_LABEL[r.segment] ?? r.segment,
        ratio: r.ratio,
        ratio_kind: r.ratio_kind,
        ratio_kind_label: RATIO_KIND_LABEL[r.ratio_kind] ?? r.ratio_kind,
        scope: r.scope,
        numerator_metric: r.numerator_metric,
        denominator_metric: r.denominator_metric,
        period: r.period,
        as_of: r.as_of,
        currency: r.currency,
        cadence,
        cmp: `${r.currency}|${cadence}`,
        volume: r.denominator_value,
        revenue: r.numerator_value,
        take_rate_pct: r.take_rate_pct,
        qualifier: exact ? '=' : '~',
        source_url: r.numerator_source_url ?? r.denominator_source_url ?? null,
      };
    })
    .sort((a, b) => a.company.localeCompare(b.company) || a.as_of.localeCompare(b.as_of));
}

/** Each company's most recent point. */
export function latestPoints(points: CompanyPoint[]): CompanyPoint[] {
  const last = new Map<string, CompanyPoint>();
  for (const p of points) {
    const prev = last.get(p.company);
    if (!prev || p.as_of > prev.as_of) last.set(p.company, p);
  }
  return [...last.values()].sort((a, b) => a.company.localeCompare(b.company));
}

/** Company option list, ordered by segment then name, labelled with the segment letter. */
export function companyOptions(points: CompanyPoint[]) {
  return latestPoints(points)
    .sort((a, b) => a.segment.localeCompare(b.segment) || a.company.localeCompare(b.company))
    .map((p) => ({ value: p.company, label: `${p.company} (${p.segment})` }));
}

/**
 * The default company: the one with the largest latest volume in its reporting currency among USD
 * filers. It is chosen by rule, so a new filing can change it without anybody editing the page.
 */
export function defaultCompany(points: CompanyPoint[]): string {
  const usd = latestPoints(points).filter((p) => p.currency === 'USD' && p.cadence === 'quarter');
  const pool = usd.length ? usd : latestPoints(points);
  return [...pool].sort((a, b) => b.volume - a.volume)[0]?.company ?? '';
}

/** The `controls` prop for `<DashboardControls group="company">`. */
export function companyControls(points: CompanyPoint[]) {
  return [
    { name: 'company', label: 'Company', options: companyOptions(points), default: defaultCompany(points), type: 'select' as const },
    { name: 'metric', label: 'Metric', options: METRICS.map((m) => ({ ...m })), default: 'take_rate' as Metric },
  ];
}

/**
 * A Vega expression mapping the `company` signal to a per-company value, written as a chain of
 * ternaries so it needs nothing beyond the core expression language.
 */
export function lookupExpr(map: Map<string, string>, signal = 'company', fallback = ''): string {
  const parts = [...map].map(([k, v]) => `${signal} == ${JSON.stringify(k)} ? ${JSON.stringify(v)} : `);
  return `(${parts.join('')}${JSON.stringify(fallback)})`;
}

/** Rows for the linked DataTable (filterKeys: ['company']): latest filed numbers, with links. */
export function companyTableRows(points: CompanyPoint[]) {
  return latestPoints(points)
    .sort((a, b) => a.segment.localeCompare(b.segment) || b.volume - a.volume)
    .map((p) => ({
      company: p.company,
      segment: p.segment_label,
      period: p.period,
      cadence: CADENCE_LABEL[p.cadence],
      currency: p.currency,
      volume: p.volume,
      revenue: p.revenue,
      take_rate: p.take_rate_pct / 100,
      ratio: `${p.numerator_metric} / ${p.denominator_metric}`,
      ratio_kind: p.ratio_kind_label,
      qualifier: p.qualifier,
      source: p.source_url ? `<a href="${p.source_url}">filing</a>` : '—',
    }));
}

/** Findings for the explorer's figure titles; numbers are computed here, never typed. */
export function companyFindings(points: CompanyPoint[]) {
  const latest = latestPoints(points);
  // The range is read within one ratio kind (gross take rate: revenue over volume); net take rates and
  // margins are different ratios and are never ranked against it.
  const gross = latest.filter((p) => p.ratio_kind === 'gross_take_rate');
  const sorted = [...gross].sort((a, b) => a.take_rate_pct - b.take_rate_pct);
  const lo = sorted[0];
  const hi = sorted[sorted.length - 1];
  const spread = lo && hi ? hi.take_rate_pct / lo.take_rate_pct : null;
  return {
    companies: latest.length,
    gross: gross.length,
    low: lo ? { company: lo.company, take_rate_pct: lo.take_rate_pct, period: lo.period, qualifier: lo.qualifier } : null,
    high: hi ? { company: hi.company, take_rate_pct: hi.take_rate_pct, period: hi.period, qualifier: hi.qualifier } : null,
    spread,
    currencies: [...new Set(latest.map((p) => p.currency))].sort(),
    nonQuarterly: latest.filter((p) => p.cadence !== 'quarter').map((p) => p.company).sort(),
  };
}

export const fmtPct = (pct: number): string =>
  `${pct >= 1 ? pct.toFixed(1) : pct.toFixed(2)}%`;

/** fmtPct with the point's qualifier: `~` when an input was rounded (e.g. GMV stated only to $0.1 billion). */
export const fmtQPct = (p: { take_rate_pct: number; qualifier?: string | null }): string =>
  `${p.qualifier && p.qualifier !== '=' ? p.qualifier : ''}${fmtPct(p.take_rate_pct)}`;
