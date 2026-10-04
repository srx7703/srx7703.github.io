import { VL_SCHEMA, series, tok } from '../theme';
import { lit, reasonTitle } from './adoption_shared';

export type EventRow = {
  event_date: string; company: string; segment: string; event_kind: string;
  counterparty: string | null; target: string | null; value: number | null; unit: string | null;
  qualifier: string | null; metric_kind: string | null; tag: string; stale: boolean;
  source_name: string; source_url: string; caveat: string | null;
  /** `acquirer:target` from the curated id: a signing and its closing share one deal. */
  deal?: string | null;
};

/** The plan's three classes of 2025-26 event (PLAN §6, chart 7). */
export const EVENT_CLASS: Record<string, string> = {
  ipo: 'listing', direct_listing: 'listing',
  acquisition: 'acquisition',
  priced_round: 'round', tender: 'round',
};
export const EVENT_CLASS_LABEL: Record<string, string> = {
  listing: 'Listing (IPO or direct listing)', acquisition: 'Acquisition', round: 'Priced round or tender',
};
const KIND_LABEL: Record<string, string> = {
  ipo: 'IPO', direct_listing: 'Direct listing', acquisition: 'Acquisition', priced_round: 'Priced round', tender: 'Tender offer',
};

const NAME = 'events_src';

export type EventPoint = EventRow & {
  cls: string; cls_label: string; kind_label: string; disclosed: boolean; value_label: string; what: string;
};

const usd = (v: number) => (v >= 1e9 ? `$${+(v / 1e9).toFixed(1)}B` : `$${+(v / 1e6).toFixed(0)}M`);

/** Mark-ready rows: stale and unclassified events dropped; disclosed values labelled with their qualifier. */
export function eventPoints(rows: EventRow[]): EventPoint[] {
  return rows
    .filter((r) => !r.stale && EVENT_CLASS[r.event_kind])
    .map((r) => {
      const disclosed = r.value != null && r.unit === 'USD';
      const q = r.qualifier && r.qualifier !== '=' ? `${r.qualifier} ` : '';
      const target = r.target || r.counterparty;
      return {
        ...r,
        cls: EVENT_CLASS[r.event_kind],
        cls_label: EVENT_CLASS_LABEL[EVENT_CLASS[r.event_kind]],
        kind_label: KIND_LABEL[r.event_kind] ?? r.event_kind,
        disclosed,
        value_label: disclosed ? `${q}${usd(r.value as number)}` : '',
        what: `${KIND_LABEL[r.event_kind] ?? r.event_kind}${target ? `: ${target}` : ''}`,
      };
    });
}

/**
 * 2025-26 event timeline: listings, acquisitions and priced rounds or tenders, one row per company,
 * ordered by each company's first event. Shape is the class; a filled mark has a disclosed dollar
 * value (printed beside it, with its qualifier, where it clears the row's next label), a hollow one does not. Values are not sized: a
 * tender price, a round's post-money and an acquisition price are different quantities.
 *
 * Joins dashboard group `ledger` through the param `company` (`all` or a company; the rest grey).
 * A company with no event keeps the chart and says so.
 */
export function eventsSpec(rows: EventRow[], opts: { company?: string } = {}) {
  const pts = eventPoints(rows);
  // Each event's place on the time axis as a fraction of the domain, and its label's estimated width
  // (9.5px Plex Sans, about 0.5em a character), so the label layer can thin labels at narrow widths.
  const ms = pts.map((p) => Date.parse(`${p.event_date}T00:00:00Z`));
  const [t0, t1] = [Math.min(...ms), Math.max(...ms)];
  const values = pts.map((p, i) => ({
    ...p, t_frac: t1 > t0 ? (ms[i] - t0) / (t1 - t0) : 0.5, w_lab: Math.ceil(p.value_label.length * 4.9),
  }));
  const first = new Map<string, string>();
  values.forEach((v) => { if (!first.has(v.company) || v.event_date < (first.get(v.company) as string)) first.set(v.company, v.event_date); });
  const order = [...first].sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0])).map(([c]) => c);
  const on = "(company == 'all' || datum.company == company)";
  const x = { field: 'event_date', type: 'temporal', title: null, scale: { padding: 14 }, axis: { format: '%b %Y', tickCount: 6, labelOverlap: true } };
  const y = { field: 'company', type: 'nominal', title: null, sort: order, axis: { labelLimit: 130, ticks: false, domain: false, grid: true } };
  const color = { condition: { test: on, value: series.a }, value: series.neutral };
  const shape = {
    field: 'cls', type: 'nominal',
    scale: { domain: ['listing', 'acquisition', 'round'], range: ['triangle-up', 'square', 'circle'] },
    legend: {
      title: null, orient: 'top', direction: 'vertical', columns: 1, labelLimit: 230, symbolFillColor: series.a, symbolStrokeColor: series.a,
      labelExpr: `({'listing': 'Listing', 'acquisition': 'Acquisition', 'round': 'Priced round or tender'})[datum.label]`,
    },
  };
  return {
    $schema: VL_SCHEMA,
    title: reasonTitle(`company != 'all' && !indata(${lit(NAME)}, 'company', company) ? company + ' has no listing, acquisition\\nor priced round in 2025-26.' : ''`),
    height: { step: 24 },
    params: [{ name: 'company', value: opts.company ?? 'all' }],
    data: { values, name: NAME },
    layer: [
      {
        mark: { type: 'point', size: 70, strokeWidth: 1.6 },
        encoding: {
          x, y, shape, stroke: color,
          fill: { condition: { test: `datum.disclosed && ${on}`, value: series.a }, value: 'transparent' },
          opacity: { condition: { test: on, value: 1 }, value: 0.45 },
          tooltip: [
            { field: 'company', type: 'nominal', title: 'Company' },
            { field: 'what', type: 'nominal', title: 'Event' },
            { field: 'event_date', type: 'temporal', title: 'Date', format: '%b %d, %Y' },
            { field: 'value_label', type: 'nominal', title: 'Value' },
            { field: 'caveat', type: 'nominal', title: 'Caveat' },
          ],
        },
      },
      {
        // A label is printed only if it clears the next labelled event in its row at the rendered width
        // (the time scale has 14px padding at each end); a row's latest label always prints. Values that
        // are not printed stay in the tooltip and in the ledger table.
        transform: [
          { filter: 'datum.disclosed' },
          { window: [{ op: 'lead', field: 't_frac', as: 'next_frac' }], groupby: ['company'], sort: [{ field: 'event_date', order: 'ascending' }] },
          { filter: 'datum.next_frac == null || (datum.next_frac - datum.t_frac) * (width - 28) >= datum.w_lab + 4' },
        ],
        mark: { type: 'text', align: 'left', dx: 7, dy: -8, fontSize: 9.5 },
        encoding: {
          x, y, text: { field: 'value_label', type: 'nominal' },
          color: { condition: { test: on, value: tok('ink-2') }, value: series.neutral },
          opacity: { condition: { test: on, value: 1 }, value: 0.6 },
        },
      },
    ],
    padding: { right: 40 },
  };
}

/** Finding for the figure title: counts per class, computed from the rows. */
export function eventsFinding(rows: EventRow[]) {
  const pts = eventPoints(rows);
  const n = (c: string) => pts.filter((p) => p.cls === c).length;
  // Deals, not events: a signing and its closing count once; a deal has a price if any of its rows does.
  const deals = new Map<string, boolean>();
  pts.filter((p) => p.cls === 'acquisition').forEach((p) => {
    const k = p.deal ?? `${p.company}:${p.counterparty ?? ''}`;
    deals.set(k, (deals.get(k) ?? false) || p.disclosed);
  });
  const nDeals = deals.size;
  const undisclosed = [...deals.values()].filter((has) => !has).length;
  const span = pts.map((p) => p.event_date).sort();
  const yr = (d?: string) => (d ? d.slice(0, 4) : '');
  return {
    title: `${nDeals} acquisitions, ${n('round')} priced rounds or tenders and ${n('listing')} listings from ${yr(span[0])} to ${yr(span[span.length - 1])}; ${undisclosed} of the ${nDeals} acquisitions carry no dollar value in any curated row`,
    deals: nDeals, acquisitionEvents: n('acquisition'), acquisitions: nDeals, rounds: n('round'), listings: n('listing'), undisclosed, from: span[0] ?? null, to: span[span.length - 1] ?? null,
  };
}

export function eventsSubtitle(): string {
  return 'Listings, acquisitions and priced rounds or tenders involving the companies on this page, by date announced or closed; an acquisition can show twice, at signing and at closing, and counts once in the title. '
    + 'Filled: a dollar value was disclosed (printed beside it where there is room, otherwise in the tooltip and the ledger table); hollow: no value. Values are labelled, not sized, because they measure different things.';
}
