/**
 * Figure 7, the deal timeline, and its companion, the announced-to-filing dumbbell.
 *
 * Timeline. One mark per chartable event from `from` (default 2025-01-01) on.
 *   x       the event date (the announcement date; a closing is its own mark on its own date).
 *   y       the layer of the company bought, listed or funded (twelve layers). Chosen over buyer type because colour
 *           already carries the buyer: y answers "which parts of security are being bought", colour "by whom", and a
 *           buyer-type axis would repeat the colour and pile 100 of 187 events into one row.
 *           Marks in one layer are spread over sub-lanes by date and size so that none covers another (deterministic,
 *           no jitter). The layout is computed twice, for a wide plot and for a phone (`NARROW_PLOT`), and the spec
 *           picks one by the rendered width.
 *   size    the consideration of an acquisition or take-private, area on a log scale: the amount the buyer's filing
 *           records where there is one (the latest filing figure, i.e. the completed-deal note), else the announced
 *           price. Only a purchase price is sized: a round's amount, a listing and a closing (whose price sits on the
 *           announcement) are drawn at one small fixed size, hollow. A price in another currency is never converted;
 *           that mark is hollow too.
 *   colour  buyer type, eight types grouped into three colour slots plus grey (`BUYER_GROUPS`): security vendors;
 *           private equity; buyers from outside security (non-security software, strategic corporates, insurers, state
 *           buyers); grey for no acquirer (rounds, listings) and other. `BUYER_TYPE_RULE` states how the curator
 *           assigns "security vendor".
 *   shape   event kind: circle acquisition announced, square closed, triangle take-private, diamond IPO, cross round.
 *
 * What the title counts. Deals, not events, with the pipeline's own rule: an announcement and its closing are one deal,
 * and a take-private that repeats an announcement (same company, day and counterparty) is the same deal, so it counts
 * once (`eventDeals` mirrors `ledger._deal_groups`). A deal is dated by its announcement or, when the ledger first
 * records it at completion, by the completion. When the mart carries `deal_start`, `eventDeals` checks its own grouping
 * against it and throws if the two disagree, so the title's count is the mart's count by construction. Only deals with a
 * chartable record are in the mart: a deal whose price the curator did not mark comparable is in the "never charted"
 * table, not here, which is why the title says "with a chartable record".
 *
 * A dashed rule marks the start of the twelve-month window the title counts in.
 *
 * Dashboard group `ledger`; params `layer`, `market`, `metric_kind`, `confirmed` (see ledger_data.ts). `metric_kind` is
 * the kind of price a mark carries: the price that sizes it (`acquisition_price_sec` when the filing figure is drawn,
 * else the announced or take-private price) or a round's amount; a mark with no price has none and shows only under
 * "All kinds". Hide-stale is not a control of this figure: a deal is not a round that goes stale.
 *
 * Text: `eventsSubtitle` is the metric sentence only; the key to the marks and the rules are in `eventsNotes` and
 * `eventDumbbellNotes` (plain text, paragraphs separated by a blank line) for the "How to read this" disclosure.
 */
import { VL_SCHEMA, series, tok } from '../theme';
import type { EventRow } from './types';
import {
  type LedgerDim, type LedgerState, LAYERS, LEDGER_DEFAULTS, chartableEvents, fmtDay, fmtMoney, fmtPct, fmtRange, fmtValue, isStale,
  layerName, layerShort, ledgerParams, lit, passes, reasonChain, reasonTitle, stateFilterExpr, ledgerTitleMap, type LedgerTitleMap, viewKeyRows,
  numWord, joinAnd, upFirst, esc, htmlText, linkHtml, kindLabel, EVENT_PRICE_KINDS,
} from './ledger_data';

const NAME = 'ev_src';

/** The controls figure 7 answers to, in the key order of its `titles` map. */
export const EVENTS_DIMS = ['layer', 'market', 'metric_kind', 'confirmed'] as const satisfies readonly LedgerDim[];

/** The events mart's row as the pipeline writes it: `EventRow` plus the two fields the deal logic reads. */
export type EventMartRow = EventRow & { chart_metric_kind?: string | null; deal_start?: boolean | null };

export interface EventsOpts {
  /** First day drawn (default 2025-01-01). */
  from?: string;
  /** End of the twelve-month window of the title; pass `facts.events.window_to` (default: the latest event date). */
  asOf?: string;
  /** Most sub-lanes a layer may use on a wide plot (default 4); a crowded layer overlaps rather than grows. */
  maxLanes?: number;
  /** A purchase price of at least this many dollars is labelled beside its mark (default 2e9). */
  labelMin?: number;
  /** ... and on a phone (a plot narrower than `NARROW_PLOT`), where a label covers neighbouring marks: at least this many (default 5e9). */
  labelMinNarrow?: number;
}
export const EVENTS_DEFAULTS = { from: '2025-01-01', maxLanes: 4, labelMin: 2e9, labelMinNarrow: 5e9 } as const;

/** Plot width in pixels below which the timeline switches to its phone layout (smaller marks, tighter lanes, fewer axis labels). */
export const NARROW_PLOT = 420;

// --- vocabularies --------------------------------------------------------------------------------------------------
export const EVENT_KIND = [
  { kind: 'acquisition_announced', label: 'Acquisition announced', short: 'Announced', shape: 'circle' },
  { kind: 'acquisition_closed', label: 'Acquisition closed', short: 'Closed', shape: 'square' },
  { kind: 'take_private', label: 'Take-private', short: 'Take-private', shape: 'triangle-up' },
  { kind: 'ipo', label: 'IPO', short: 'IPO', shape: 'diamond' },
  { kind: 'round', label: 'Funding round', short: 'Funding round', shape: 'cross' },
  { kind: 'delisting', label: 'Delisting', short: 'Delisting', shape: 'triangle-down' },
] as const;
const KIND_LABEL: Record<string, string> = Object.fromEntries(EVENT_KIND.map((k) => [k.kind, k.label]));
const KIND_SHAPE: Record<string, string> = Object.fromEntries(EVENT_KIND.map((k) => [k.kind, k.shape]));

/** The eight buyer types in three colour slots plus grey. */
export const BUYER_GROUPS = [
  { key: 'platform', label: 'Security vendor', short: 'Security vendor', types: ['platform_security'], color: series.a },
  { key: 'financial', label: 'Private equity', short: 'Private equity', types: ['pe'], color: series.b },
  { key: 'outside', label: 'Buyer from outside security', short: 'Outside security', types: ['non_security_software', 'strategic', 'insurance', 'state'], color: series.c },
  { key: 'none', label: 'No acquirer, or other', short: 'No buyer, other', types: ['none', 'other'], color: series.neutral },
] as const;
export const BUYER_TYPE_LABEL: Record<string, string> = {
  platform_security: 'Security vendor', non_security_software: 'Software company outside security', pe: 'Private equity',
  insurance: 'Insurer', state: 'State buyer', strategic: 'Corporate outside security', other: 'Other', none: 'No acquirer (round or listing)',
};
export const buyerGroup = (t: string | null | undefined): (typeof BUYER_GROUPS)[number] =>
  BUYER_GROUPS.find((g) => (g.types as readonly string[]).includes(t ?? '')) ?? BUYER_GROUPS[3];

/** How the curator assigns "security vendor" (the stored value is `platform_security`); also written in docs/cyber/CURATION.md. */
export const BUYER_TYPE_RULE =
  'A buyer is a security vendor when security is its main revenue line or its stated core business, checked on its latest filing or about page. '
  + 'A take-private by a private-equity sponsor is private equity, and a merger with no named acquirer is other. The buyer type is the curator\u2019s label under this rule.';

/** The event kind as a word in a sentence: `take_private` is "take-private", never the stored value. */
export const EVENT_KIND_WORD: Record<string, string> = {
  take_private: 'take-private', acquisition_announced: 'acquisition announcement', acquisition_closed: 'completed acquisition',
  ipo: 'IPO', round: 'funding round', delisting: 'delisting',
};

/**
 * The newest event as a clause that says no more than the record does: a take-private or announcement that has not
 * closed (`status_after` is `pending_acquisition`) reads "agreed to be bought by", never "bought by".
 * `{ kind: 'take-private', clause: 'Varonis Systems, agreed to be bought by Proofpoint (Thoma Bravo-owned) at about $6.3B, not yet completed' }`.
 */
export function latestEventClause(e: {
  company: string; event_kind: string; counterparty: string | null; status_after?: string | null; price?: string | null;
}): { kind: string; clause: string; pending: boolean } {
  const pending = e.status_after === 'pending_acquisition' && (e.event_kind === 'take_private' || e.event_kind === 'acquisition_announced');
  const verb = pending ? 'agreed to be bought by' : 'bought by';
  const buyer = e.counterparty ? `, ${verb} ${e.counterparty}` : '';
  return {
    kind: EVENT_KIND_WORD[e.event_kind] ?? e.event_kind.replace(/_/g, '-'),
    clause: `${e.company}${buyer}${e.price ? ` at ${e.price}` : ''}${pending ? ', not yet completed' : ''}`, pending,
  };
}

// --- deals (mirror of pipelines/cyber/ledger.py _deal_groups) -----------------------------------------------------
const DEAL_ORDER: Record<string, number> = { acquisition_announced: 0, take_private: 1, acquisition_closed: 2 };
const isOpenerKind = (k: string | null | undefined): boolean => k === 'acquisition_announced' || k === 'take_private';

export interface Consideration {
  value: number; qualifier: string; basis: 'sec_filing' | 'announced'; unit: string;
  /** The `metric_kind` of the price drawn: `acquisition_price_sec`, else the announced row's (`acquisition_price_announced`, `take_private_price`). */
  kind: string;
  announced: number | null; announcedQualifier: string | null; filing: number | null; filingQualifier: string | null;
  /** The announced figure sits on an announcement or take-private row (stated at signing), not on a closing row. */
  announcedAtSigning: boolean;
  /** The kind of the event row that carries the filing figure (a closing row: the completed-deal note; an announcement row: a note written while pending). */
  filingFrom: string | null;
}
export interface Deal {
  company: string; layer: string | null; market: string | null;
  /** The announcement date (the closing date for a closing seen alone). */
  start: string;
  closed: string | null;
  counterparty: string | null; buyerType: string | null;
  /** The opener (an announcement, a take-private or a closing seen alone), then a folded take-private and an attached closing. */
  members: EventRow[];
  /** The best price on record: the buyer's filing where there is one, else the announced price. */
  consideration: Consideration | null;
}

const pos = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) && x > 0 ? x : null);

function considerationOf(members: readonly EventRow[]): Consideration | null {
  const sorted = [...members].sort((a, b) => (a.event_date ?? '').localeCompare(b.event_date ?? ''));
  // The announced figure is the earliest stated; the filing figure the latest (the completed-deal note replaces a note written while the deal was pending).
  const ann = sorted.find((m) => pos(m.announced_value));
  const sec = [...sorted].reverse().find((m) => pos(m.sec_value));
  const unit = (sec ?? ann)?.unit ?? null;
  if (!unit) return null;
  const announced = ann ? pos(ann.announced_value) : null;
  const filing = sec ? pos(sec.sec_value) : null;
  const best = filing ?? announced;
  if (best == null) return null;
  return {
    value: best, qualifier: (filing != null ? sec?.sec_qualifier : ann?.qualifier) ?? '=', basis: filing != null ? 'sec_filing' : 'announced', unit,
    kind: filing != null ? 'acquisition_price_sec' : (ann?.metric_kind ?? 'acquisition_price_announced'),
    announced, announcedQualifier: ann?.qualifier ?? null, filing, filingQualifier: sec?.sec_qualifier ?? null,
    announcedAtSigning: announced != null && isOpenerKind(ann?.event_kind), filingFrom: sec?.event_kind ?? null,
  };
}

/**
 * Acquisitions as deals: per company in date order, a closing attaches to the open announcement before it; a closing
 * with none, or a take-private, stands alone, and a take-private that repeats an announcement (same company, same day,
 * same counterparty without case or outer spaces) is folded into it. Same rule, same ordering and same ties as
 * `ledger._deal_groups` in the pipeline.
 */
const dealsMemo = new WeakMap<object, Deal[]>();
export function eventDeals(rows: readonly EventRow[]): Deal[] {
  const cached = dealsMemo.get(rows);
  if (cached) return cached;
  const deals = buildDeals(rows);
  dealsMemo.set(rows, deals);
  return deals;
}

const party = (e: EventRow): string => (e.counterparty ?? '').trim().toLowerCase();
const eventKey = (e: EventRow): string => `${e.company}|${e.event_kind}|${e.event_date}|${e.counterparty ?? ''}`;

function buildDeals(rows: readonly EventRow[]): Deal[] {
  const wanted = chartableEvents(rows).filter((e) => e.event_kind != null && e.event_kind in DEAL_ORDER);
  wanted.sort((a, b) => {
    const d = (a.event_date as string).localeCompare(b.event_date as string);
    if (d) return d;
    const o = DEAL_ORDER[a.event_kind as string] - DEAL_ORDER[b.event_kind as string];
    if (o) return o;
    const ca = String(a.counterparty);
    const cb = String(b.counterparty);
    return ca < cb ? -1 : ca > cb ? 1 : 0;
  });
  const out: Deal[] = [];
  const open = new Map<string, Deal>();
  const announced = new Map<string, Deal>();
  const make = (e: EventRow, start: string, closed: string | null): Deal => ({
    company: e.company as string, layer: e.layer, market: e.market, start, closed, counterparty: e.counterparty, buyerType: e.buyer_type,
    members: [e], consideration: null,
  });
  for (const e of wanted) {
    const company = e.company as string;
    if (e.event_kind === 'acquisition_closed') {
      const deal = open.get(company);
      if (!deal) out.push(make(e, e.event_date as string, e.event_date));
      else {
        deal.closed = e.event_date;
        deal.members.push(e);
        deal.counterparty ??= e.counterparty;
        deal.buyerType ??= e.buyer_type;
        open.delete(company);
      }
      continue;
    }
    const twin = e.event_kind === 'take_private' ? announced.get(`${company}|${e.event_date}|${party(e)}`) : undefined;
    if (twin) { // the same deal entered under both kinds: it counts once
      twin.members.push(e);
      twin.closed = twin.closed || e.closed_date || null;
      continue;
    }
    const deal = make(e, e.event_date as string, e.closed_date || null);
    out.push(deal);
    if (e.event_kind !== 'take_private') {
      open.set(company, deal);
      const k = `${company}|${e.event_date}|${party(e)}`;
      if (!announced.has(k)) announced.set(k, deal);
    }
  }
  for (const d of out) d.consideration = considerationOf(d.members);
  // The mart says which row opens each deal (`deal_start`); the grouping above must agree with it.
  if (wanted.some((e) => typeof (e as EventMartRow).deal_start === 'boolean')) {
    const mine = new Set(out.map((d) => eventKey(d.members[0])));
    const theirs = new Set(wanted.filter((e) => (e as EventMartRow).deal_start === true).map(eventKey));
    const diff = [...mine].filter((k) => !theirs.has(k)).concat([...theirs].filter((k) => !mine.has(k)));
    if (diff.length) throw new Error(`events.ts groups deals differently from the events mart's deal_start (${diff.slice(0, 3).join('; ')}${diff.length > 3 ? ' ...' : ''})`);
  }
  return out;
}

const oneYearBefore = (iso: string): string => {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y - 1, m - 1, d));
  if (dt.getUTCMonth() !== m - 1) dt.setUTCDate(0); // 29 February
  return dt.toISOString().slice(0, 10);
};

/** The window's end: the option, else the latest chartable event date. */
export function windowEnd(rows: readonly EventRow[], asOf?: string): string | null {
  if (asOf) return asOf;
  const ds = chartableEvents(rows).map((r) => r.event_date as string).sort();
  return ds.length ? ds[ds.length - 1] : null;
}

/** A folded take-private: the deal's announcement already draws and counts it, so it has no mark of its own. */
const isFolded = (e: EventRow, deal: Deal | null): boolean => deal != null && e.event_kind === 'take_private' && deal.members[0] !== e;

/**
 * The kind of price a mark carries, the value the Kind control filters on: the deal's chart price kind on its first
 * event (an announcement, a take-private, or a closing seen alone), a round's own kind when it states an amount, and ''
 * for a mark with no price (a closing that follows an announcement, a listing, an undisclosed price).
 */
export function priceKindOf(e: EventRow, deal: Deal | null): string {
  if (e.event_kind === 'round') return pos(e.announced_value) != null ? e.metric_kind ?? '' : '';
  if (deal && deal.members[0] === e && deal.consideration) return deal.consideration.kind;
  return '';
}

// --- rows ----------------------------------------------------------------------------------------------------------
type Row = Record<string, unknown>;
const SIZE_RANGE: [number, number] = [44, 760];
const SMALL = 34; // area of a mark with no consideration
/** The two lane layouts: [reference plot width px, px between sub-lanes, most lanes, area factor]. */
const WIDE = { refW: 520, pitch: 12, areaK: 1 };
const PHONE = { refW: 160, pitch: 9, lanes: 5, areaK: 0.4, smallArea: 22 };

function snap125(v: number, dir: 'down' | 'up'): number {
  const base = 10 ** Math.floor(Math.log10(v));
  const steps = [1, 2, 5, 10];
  if (dir === 'down') return steps.filter((s) => s * base <= v * (1 + 1e-9)).pop()! * base;
  return (steps.find((s) => s * base >= v * (1 - 1e-9)) ?? 10) * base;
}

const dayMs = (iso: string): number => Date.parse(`${iso}T00:00:00Z`);

interface Mark {
  e: EventRow; deal: Deal | null; kind: string; size: number | null; area: number; layer: string; lane: number; laneN: number;
}

/**
 * A purchase price may size a mark: the USD consideration of a deal, on the deal's first event (its announcement, a
 * take-private, or a closing seen alone). A closing that follows an announcement is not sized again.
 */
function sizeOf(e: EventRow, deal: Deal | null): number | null {
  if (!deal || deal.members[0] !== e) return null;
  const c = deal.consideration;
  return c && c.unit === 'USD' ? c.value : null;
}

/**
 * Places each mark of a layer on a sub-lane so that no mark covers another where it can be avoided, for a plot of
 * `refW` pixels, marks scaled by `k` (the phone layout draws them smaller), lanes `pitch` apart.
 */
function layout(marks: Mark[], spanDays: number, maxLanes: number, refW: number, pitch: number, k: number, set: (m: Mark, lane: number) => void): void {
  const pxPerDay = refW / Math.max(1, spanDays);
  const byLayer = new Map<string, Mark[]>();
  marks.forEach((m) => byLayer.set(m.layer, [...(byLayer.get(m.layer) ?? []), m]));
  for (const group of byLayer.values()) {
    group.sort((a, b) => (a.e.event_date as string).localeCompare(b.e.event_date as string) || a.area - b.area);
    const placed: { x: number; y: number; r: number }[] = [];
    for (const m of group) {
      const x = (dayMs(m.e.event_date as string) / 86400000) * pxPerDay;
      const r = Math.sqrt((m.area * k) / Math.PI);
      let best = 0;
      let bestClear = -Infinity;
      for (let lane = 0; lane < maxLanes; lane++) {
        const y = lane * pitch;
        const clear = Math.min(Infinity, ...placed.map((p) => Math.hypot(p.x - x, p.y - y) - (p.r + r) - 1));
        if (clear >= 0) { best = lane; bestClear = clear; break; }
        if (clear > bestClear) { best = lane; bestClear = clear; }
      }
      set(m, best);
      placed.push({ x, y: best * pitch, r });
    }
  }
}

/** Who filed the figure: the buyer, or (a take-private by a private-equity sponsor, which files nothing) the target. */
const filingOwner = (d: Deal): 'buyer' | 'target' => (d.buyerType === 'pe' || d.members.some((m) => m.event_kind === 'take_private') ? 'target' : 'buyer');

const tipPrice = (m: Mark): string => {
  const c = m.deal?.consideration;
  const e = m.e;
  if (e.event_kind === 'round') return e.announced_value != null ? `${fmtValue(e.announced_value, e.unit, e.qualifier)} raised (an amount, not a purchase price)` : 'amount not stated';
  if (!c) return e.event_kind === 'acquisition_closed' ? 'see the announcement' : 'price not disclosed';
  const who = m.deal && filingOwner(m.deal) === 'target' ? 'the target’s filing' : 'the buyer’s filing';
  const parts = [c.filing != null ? `${fmtValue(c.filing, c.unit, c.filingQualifier)} in ${who}` : null,
    c.announced != null ? `${fmtValue(c.announced, c.unit, c.announcedQualifier)} ${c.announcedAtSigning ? 'announced' : 'first stated at completion'}` : null].filter(Boolean);
  return parts.join('; ');
};

/** The deal's state as the ledger records it: completed on a date, or no completion recorded (never "pending" for a deal that closed). */
const tipStatus = (m: Mark): string => {
  const d = m.deal;
  if (!d || m.e.event_kind === 'round' || m.e.event_kind === 'ipo') return '';
  if (m.e.event_kind === 'acquisition_closed' && d.members[0] === m.e) return `completed ${fmtDay(m.e.event_date)}; first recorded at completion`;
  return d.closed ? `completed ${fmtDay(d.closed)}` : 'no completion recorded in the ledger';
};

export interface EventsData {
  rows: Row[];
  /** Pixel height of one layer band, and the layers drawn in order. */
  band: number;
  layers: string[];
  kinds: string[];
  groups: string[];
  sizeDomain: [number, number];
  from: string;
  to: string;
  windowFrom: string | null;
  nonUsd: number;
  labelMinNarrow: number;
}

/** The figure's dataset and the facts about its layout. */
export function eventsData(rows: readonly EventRow[], opts: EventsOpts = {}): EventsData {
  const o = { ...EVENTS_DEFAULTS, ...opts };
  const deals = eventDeals(rows);
  const dealOf = new Map<EventRow, Deal>();
  deals.forEach((d) => d.members.forEach((m) => dealOf.set(m, d)));
  const events = chartableEvents(rows).filter((e) => (e.event_date as string) >= o.from && KIND_SHAPE[e.event_kind as string] && e.layer && !isFolded(e, dealOf.get(e) ?? null));
  const to = windowEnd(rows, opts.asOf) ?? o.from;
  const lastDate = events.map((e) => e.event_date as string).sort().pop() ?? to;
  const end = lastDate > to ? lastDate : to;
  const sizes = events.map((e) => sizeOf(e, dealOf.get(e) ?? null)).filter((v): v is number => v != null);
  const sizeDomain: [number, number] = sizes.length ? [snap125(Math.min(...sizes) * 0.8, 'down'), snap125(Math.max(...sizes) * 1.25, 'up')] : [1e7, 1e11];
  const area = (v: number | null): number => (v == null ? SMALL
    : SIZE_RANGE[0] + ((Math.log(v) - Math.log(sizeDomain[0])) / (Math.log(sizeDomain[1]) - Math.log(sizeDomain[0]))) * (SIZE_RANGE[1] - SIZE_RANGE[0]));
  const marks: Mark[] = events.map((e) => {
    const deal = dealOf.get(e) ?? null;
    const size = sizeOf(e, deal);
    return { e, deal, kind: e.event_kind as string, size, area: area(size), layer: e.layer as string, lane: 0, laneN: 0 };
  });
  const span = (dayMs(end) - dayMs(o.from)) / 86400000;
  layout(marks, span, o.maxLanes, WIDE.refW, WIDE.pitch, WIDE.areaK, (m, l) => { m.lane = l; });
  layout(marks, span, PHONE.lanes, PHONE.refW, PHONE.pitch, PHONE.areaK, (m, l) => { m.laneN = l; });

  const present = new Set(marks.map((m) => m.layer));
  const known: string[] = LAYERS.map((l) => l.key);
  const layersUsed = [...known.filter((k) => present.has(k)), ...[...present].filter((k) => !known.includes(k)).sort()];
  const lanesIn = (layer: string) => Math.max(0, ...marks.filter((m) => m.layer === layer).map((m) => m.lane)) + 1;
  const lanesInN = (layer: string) => Math.max(0, ...marks.filter((m) => m.layer === layer).map((m) => m.laneN)) + 1;
  const maxLanes = Math.max(1, ...layersUsed.map(lanesIn));
  const band = Math.max(maxLanes * WIDE.pitch, Math.max(1, ...layersUsed.map(lanesInN)) * PHONE.pitch) + 16;
  const windowFrom = to ? oneYearBefore(to) : null;
  let nonUsd = 0;
  const dyOf = (m: Mark) => band / 2 + (m.lane - (lanesIn(m.layer) - 1) / 2) * WIDE.pitch;
  const dyOfN = (m: Mark) => band / 2 + (m.laneN - (lanesInN(m.layer) - 1) / 2) * PHONE.pitch;

  const out: Row[] = marks.map((m) => {
    const e = m.e;
    const g = buyerGroup(e.buyer_type);
    if (m.deal?.consideration && m.deal.consideration.unit !== 'USD' && m.deal.members[0] === e && m.kind !== 'acquisition_closed') nonUsd++;
    const detail = [
      e.counterparty ? (e.event_kind === 'round' ? `led by ${e.counterparty}` : e.event_kind === 'ipo' ? '' : `buyer: ${e.counterparty}`) : '',
    ].filter(Boolean).join('');
    return {
      row_type: 'mark', company: e.company, layer: e.layer, layer_label: layerShort(e.layer), market: e.market ?? '',
      kind: m.kind, kind_label: KIND_LABEL[m.kind], date: e.event_date, group: g.key, group_label: g.label,
      metric_kind: priceKindOf(e, m.deal), confirmed: e.company_confirmed === true, stale: isStale(e),
      size_value: m.size, area: m.area, area_n: m.size == null ? PHONE.smallArea : m.area * PHONE.areaK, filled: m.size != null,
      dy: dyOf(m), dy_n: dyOfN(m),
      tip_what: `${KIND_LABEL[m.kind]}${detail ? `, ${detail}` : ''}`,
      tip_buyer: e.buyer_type ? BUYER_TYPE_LABEL[e.buyer_type] ?? e.buyer_type : '—',
      tip_price: tipPrice(m), tip_layer: layerName(e.layer), tip_date: fmtDay(e.event_date),
      tip_status: tipStatus(m), tip_caveat: e.caveat ?? '',
    };
  });
  // Direct labels for the big purchase prices (they replace a size legend).
  const t0 = dayMs(o.from);
  const t1 = dayMs(end);
  const seen = new Set<string>();
  const labels: Row[] = marks.filter((m) => m.size != null && m.size >= o.labelMin).filter((m) => {
    const k = `${m.e.company}|${m.size}`; // a deal recorded under two kinds is labelled once
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).map((m) => {
    const e = m.e;
    const c = (m.deal as Deal).consideration as Consideration;
    const text = `${e.company} · ${fmtValue(c.value, c.unit, c.qualifier, 'symbol')}`;
    return {
      row_type: 'label', company: e.company, layer: e.layer, layer_label: layerShort(e.layer), market: e.market ?? '', metric_kind: priceKindOf(e, m.deal),
      confirmed: e.company_confirmed === true, stale: isStale(e), kind: m.kind, text, size_value: m.size, f: (dayMs(e.event_date as string) - t0) / Math.max(1, t1 - t0),
      r: Math.sqrt(m.area / Math.PI), r_n: Math.sqrt((m.area * PHONE.areaK) / Math.PI), w_text: Math.ceil(text.length * 5.4),
      dy: dyOf(m), dy_n: dyOfN(m),
    };
  });
  const views = viewKeyRows(
    (out as { layer: string; market: string; metric_kind: string; confirmed: boolean; stale: boolean }[]).map((r) => ({
      layer: r.layer, market: r.market, metric_kind: r.metric_kind || null, confirmed: r.confirmed, stale: false,
    })),
    EVENTS_DIMS,
  );
  const windowRow: Row[] = windowFrom ? [{ row_type: 'window', date: windowFrom, text: 'last 12 months' }] : [];
  return {
    rows: [...out, ...labels, ...windowRow, ...views] as Row[], band, layers: layersUsed,
    kinds: EVENT_KIND.map((k) => k.kind as string).filter((k) => marks.some((m) => m.kind === k)),
    groups: BUYER_GROUPS.map((g) => g.key as string).filter((k) => marks.some((m) => buyerGroup(m.e.buyer_type).key === k)),
    sizeDomain, from: o.from, to: end, windowFrom, nonUsd, labelMinNarrow: o.labelMinNarrow,
  };
}

/** `eventsRows(events, opts)`: the dataset of the timeline. */
export const eventsRows = (rows: readonly EventRow[], opts: EventsOpts = {}): Row[] => eventsData(rows, opts).rows;

// --- spec ----------------------------------------------------------------------------------------------------------
const quarterStarts = (from: string, to: string): { year: number; month: number; date: number }[] => {
  const out: { year: number; month: number; date: number }[] = [];
  const [fy, fm] = from.split('-').map(Number);
  const [ty, tm] = to.split('-').map(Number);
  for (let y = fy, m = Math.floor((fm - 1) / 3) * 3 + 1; y < ty || (y === ty && m <= tm); m += 3) {
    if (m > 12) { m -= 12; y += 1; if (!(y < ty || (y === ty && m <= tm))) break; }
    if (`${y}-${String(m).padStart(2, '0')}-01` >= from) out.push({ year: y, month: m, date: 1 });
  }
  return out;
};

export function eventsSpec(rows: readonly EventRow[], opts: EventsOpts & { state?: Partial<LedgerState> } = {}) {
  const { state, ...eopts } = opts;
  const d = eventsData(rows, eopts);
  const ink2 = tok('ink-2');
  const mark = `datum.row_type == 'mark' && ${stateFilterExpr(EVENTS_DIMS)}`;
  const narrow = `width < ${NARROW_PLOT}`;
  const x = {
    field: 'date', type: 'temporal', title: null,
    scale: { domain: [d.from, d.to], padding: 14 },
    // On a phone only January and July are labelled (two-digit year): quarter labels would collide.
    axis: {
      format: '%b %Y', values: quarterStarts(d.from, d.to), labelOverlap: 'greedy', labelSeparation: 6, grid: true,
      labelExpr: `${narrow} ? (month(datum.value) % 6 == 0 ? timeFormat(datum.value, '%b %y') : '') : timeFormat(datum.value, '%b %Y')`,
    },
  };
  const y = {
    field: 'layer_label', type: 'nominal', title: null,
    sort: d.layers.map(layerShort),
    scale: { paddingInner: 0, paddingOuter: 0 },
    axis: { labelLimit: { expr: `${narrow} ? 100 : 120` }, ticks: false, domain: false, grid: true, labelFontSize: 11 },
  };
  // The lane and the area for the rendered width (phone layout below NARROW_PLOT).
  const yOffset = { field: 'dy_e', type: 'quantitative', scale: { domain: [0, d.band], range: [0, d.band] } };
  const kindDomain = EVENT_KIND.filter((k) => d.kinds.includes(k.kind));
  const groupDomain = BUYER_GROUPS.filter((g) => d.groups.includes(g.key));
  const shape = {
    field: 'kind', type: 'nominal',
    scale: { domain: kindDomain.map((k) => k.kind), range: kindDomain.map((k) => k.shape) },
    legend: {
      title: null, orient: 'top', direction: 'vertical', columns: 1, labelLimit: 120, labelFontSize: 11, symbolFillColor: tok('ink-3'), symbolStrokeColor: tok('ink-3'),
      labelExpr: `({${kindDomain.map((k) => `${lit(k.kind)}: ${lit(k.short)}`).join(', ')}})[datum.label]`,
    },
  };
  const color = {
    field: 'group', type: 'nominal',
    scale: { domain: groupDomain.map((g) => g.key), range: groupDomain.map((g) => g.color) },
    legend: {
      title: null, orient: 'bottom', direction: 'vertical', columns: 1, labelLimit: 130, labelFontSize: 11, symbolType: 'circle', offset: 14,
      labelExpr: `({${groupDomain.map((g) => `${lit(g.key)}: ${lit(g.short)}`).join(', ')}})[datum.label]`,
    },
  };
  const tooltip = [
    { field: 'company', type: 'nominal', title: 'Company' },
    { field: 'tip_what', type: 'nominal', title: 'Event' },
    { field: 'tip_date', type: 'nominal', title: 'Date' },
    { field: 'tip_buyer', type: 'nominal', title: 'Buyer type' },
    { field: 'tip_price', type: 'nominal', title: 'Price' },
    { field: 'tip_status', type: 'nominal', title: 'Status' },
    { field: 'tip_layer', type: 'nominal', title: 'Layer' },
    { field: 'tip_caveat', type: 'nominal', title: 'Caveat' },
  ];
  // Labels of the big prices: right of the mark if there is room, else left (14px scale padding, 12px right padding).
  const labelBase = [
    { filter: `datum.row_type == 'label' && ${stateFilterExpr(EVENTS_DIMS)} && (!(${narrow}) || datum.size_value >= ${d.labelMinNarrow})` },
    { calculate: `${narrow} ? datum.r_n : datum.r`, as: 'r_e' },
    { calculate: '14 + datum.f * (width - 28)', as: 'px_m' },
    { calculate: 'width + 12 - datum.px_m - datum.r_e - 4', as: 'room_r' },
    { calculate: 'datum.px_m - datum.r_e - 4', as: 'room_l' },
    { calculate: 'datum.room_r >= datum.w_text || (datum.room_l < datum.w_text && datum.room_r >= datum.room_l)', as: 'lab_right' },
    { calculate: 'datum.room_r >= datum.w_text || datum.room_l >= datum.w_text', as: 'fits' },
    { calculate: 'datum.px_m + datum.r_e + 4', as: 't_right' },
    { calculate: 'datum.px_m - datum.r_e - 4', as: 't_left' },
  ];
  const px = (field: string) => ({ field, type: 'quantitative', scale: null, axis: null });
  const labelLayers = [true, false].flatMap((right) => {
    const t = [...labelBase, { filter: `datum.fits && ${right ? 'datum.lab_right' : '!datum.lab_right'}` },
      { calculate: right ? 'datum.t_right + datum.w_text' : 'datum.t_left - datum.w_text', as: 't_far' }];
    return [
      {
        // A paper-coloured patch behind the text keeps it legible over other marks.
        transform: t,
        mark: { type: 'bar', height: 13, fill: tok('bg'), fillOpacity: 0.85, aria: false },
        encoding: { x: px(right ? 't_right' : 't_left'), x2: { field: 't_far' }, y, yOffset },
      },
      {
        transform: t,
        mark: { type: 'text', align: right ? 'left' : 'right', baseline: 'middle', fontSize: 10, color: ink2, aria: false },
        encoding: { x: px(right ? 't_right' : 't_left'), y, yOffset, text: { field: 'text', type: 'nominal' } },
      },
    ];
  });
  const dims = EVENTS_DIMS;
  const reasons = {
    layer: `layer == 'all' ? ${lit(`No dated event since ${fmtDay(d.from)}\nin the ledger.`)} : ${lit(`No dated event since ${fmtDay(d.from)}\nin this layer.`)}`,
    market: `market == 'china' ? ${lit(`No dated event since ${fmtDay(d.from)} for a\ncompany in China; older ones are in the\nledger table below.`)} : ${lit('No dated event in this layer and market.')}`,
    metric_kind: lit('No event here carries this kind of price.\nPick another kind, or all kinds.'),
    confirmed: lit('None of these events is stated by the\ncompany itself.'),
  };
  return {
    $schema: VL_SCHEMA,
    title: reasonTitle(reasonChain(NAME, dims, reasons)),
    height: { step: d.band },
    padding: { right: 12 },
    params: ledgerParams(dims, state),
    data: { values: d.rows, name: NAME },
    transform: [
      { calculate: `${narrow} ? datum.dy_n : datum.dy`, as: 'dy_e' },
      { calculate: `${narrow} ? datum.area_n : datum.area`, as: 'area_e' },
    ],
    layer: [
      {
        // The twelve-month window of the title.
        transform: [{ filter: "datum.row_type == 'window'" }],
        mark: { type: 'rule', strokeDash: [4, 3], color: tok('ink-3'), strokeWidth: 1, aria: false },
        encoding: { x: { field: 'date', type: 'temporal' } },
      },
      {
        transform: [{ filter: "datum.row_type == 'window'" }],
        mark: { type: 'text', align: 'left', baseline: 'top', dx: 4, dy: 2, fontSize: 10, color: tok('ink-3'), aria: false },
        encoding: { x: { field: 'date', type: 'temporal' }, y: { value: 0 }, text: { field: 'text', type: 'nominal' } },
      },
      {
        // No price drawn: hollow, one small size.
        transform: [{ filter: `${mark} && !datum.filled` }],
        mark: { type: 'point', strokeWidth: { expr: `${narrow} ? 1 : 1.4` }, filled: false },
        encoding: {
          x, y, yOffset, shape, stroke: color, tooltip,
          size: { field: 'area_e', type: 'quantitative', scale: null },
          fill: { value: 'transparent' }, opacity: { value: 1 },
        },
      },
      {
        // A purchase price: filled, area on a log scale (precomputed in the rows).
        transform: [{ filter: `${mark} && datum.filled` }],
        mark: { type: 'point', filled: true, strokeWidth: 1.2 },
        encoding: {
          x, y, yOffset, shape, color, stroke: color, tooltip,
          fillOpacity: { value: 0.85 }, strokeOpacity: { value: 1 },
          size: { field: 'area_e', type: 'quantitative', scale: null },
        },
      },
      ...labelLayers,
    ],
  };
}

// --- stats, finding, subtitle, notes ------------------------------------------------------------------------------
export interface EventsStats {
  /** Deals dated inside the window: the mart's `deal_start` rows by event date (the pipeline's `acquisitions_12m` in the default state). */
  deals: number;
  /** ... of which dated by an announcement (or take-private), and first recorded at completion. */
  announced: number;
  completionDated: number;
  platform: number;
  share: number | null;
  priced: number;
  /** Deals dated inside the window whose price is a filing's figure, and the same over every date. */
  filingPriced: number;
  filingPricedAll: number;
  windowFrom: string | null;
  windowTo: string | null;
  byGroup: { key: string; label: string; n: number }[];
  /** Deals closed in the window, whenever announced (the pipeline's `acquisitions_closed_12m` in the default state). */
  closed: number;
  events: number;
}

/** The kind-of-price filter value of a deal member, as its mark carries it. */
const memberKind = (m: EventRow, d: Deal): string => priceKindOf(m, d);

/** Counts behind the title, for the deals a control state lets through. A deal passes if any of its events does. */
export function eventsStats(rows: readonly EventRow[], opts: EventsOpts & { state?: Partial<LedgerState> } = {}): EventsStats {
  const { state, ...eopts } = opts;
  const to = windowEnd(rows, eopts.asOf);
  const from = to ? oneYearBefore(to) : null;
  const viewed = eventDeals(rows).filter((dl) => dl.members.some((m) => passes(
    { layer: m.layer, market: m.market, metric_kind: memberKind(m, dl), confirmed: m.company_confirmed === true, stale: false }, state ?? LEDGER_DEFAULTS, EVENTS_DIMS)));
  const inside = (day: string | null) => day != null && from != null && to != null && day > from && day <= to;
  const deals = viewed.filter((dl) => inside(dl.start));
  const by = new Map<string, number>();
  deals.forEach((dl) => { const k = buyerGroup(dl.buyerType).key; by.set(k, (by.get(k) ?? 0) + 1); });
  const platform = by.get('platform') ?? 0;
  const announced = deals.filter((dl) => isOpenerKind(dl.members[0].event_kind)).length;
  return {
    deals: deals.length, announced, completionDated: deals.length - announced, platform, share: deals.length ? platform / deals.length : null,
    priced: deals.filter((dl) => dl.consideration != null).length,
    filingPriced: deals.filter((dl) => dl.consideration?.basis === 'sec_filing').length,
    filingPricedAll: viewed.filter((dl) => dl.consideration?.basis === 'sec_filing').length,
    windowFrom: from, windowTo: to,
    byGroup: BUYER_GROUPS.map((g) => ({ key: g.key as string, label: g.label as string, n: by.get(g.key) ?? 0 })),
    closed: viewed.filter((dl) => inside(dl.closed)).length,
    events: chartableEvents(rows).length,
  };
}

/**
 * What the deal count counts, as a phrase prose can reuse so the title, the lede and the paragraph agree:
 * `72 deals with a chartable record (46 dated by announcement, 26 first recorded at completion)`.
 */
export function eventsCounted(s: Pick<EventsStats, 'deals' | 'announced' | 'completionDated'>): string {
  const split = [s.announced ? `${s.announced} dated by announcement` : '', s.completionDated ? `${s.completionDated} first recorded at completion` : ''].filter(Boolean).join(', ');
  return `${s.deals} ${s.deals === 1 ? 'deal' : 'deals'} with a chartable record${split ? ` (${split})` : ''}`;
}

/** Figure title: the deals with a chartable record in the twelve months to the window end, how they split by what dates them, and the share security vendors bought. */
export function eventsFinding(rows: readonly EventRow[], opts: EventsOpts & { state?: Partial<LedgerState> } = {}): string {
  const s = eventsStats(rows, opts);
  if (s.windowTo == null) return 'No dated event in the ledger';
  if (s.deals === 0) return `No deal with a chartable record falls in the twelve months to ${fmtDay(s.windowTo)} in this view`;
  const when = `in the twelve months to ${fmtDay(s.windowTo)}`;
  if (s.deals === 1) {
    const dated = s.announced ? 'dated by announcement' : 'first recorded at completion';
    return `The one deal with a chartable record ${when} was ${s.platform ? '' : 'not '}bought by a security vendor; it is ${dated}`;
  }
  const split = s.announced && s.completionDated ? `; ${s.announced} are dated by announcement, ${s.completionDated} first recorded at completion`
    : s.completionDated ? '; all were first recorded at completion' : '; all are dated by announcement';
  const buyers = s.platform === 0 ? `None of the ${s.deals} deals with a chartable record ${when} was bought by a security vendor`
    : s.platform === s.deals ? `Security vendors bought all ${s.deals} deals with a chartable record ${when}`
      : `Security vendors bought ${s.platform} of the ${s.deals} deals with a chartable record ${when} (${Math.round((s.share as number) * 100)}%)`;
  return `${buyers}${split}`;
}

/** Subtitle: the metric, unit and window only. The key to the marks and the rules are in `eventsNotes`. */
export function eventsSubtitle(rows: readonly EventRow[], opts: EventsOpts = {}): string {
  const o = { ...EVENTS_DEFAULTS, ...opts };
  void rows;
  return `Dated events from ${fmtDay(o.from)}, one mark each, by the layer of the company bought, listed or funded; mark area is the deal price in US dollars on a log scale.`;
}

/** Everything the subtitle leaves out, as plain text for the "How to read this" disclosure (paragraphs separated by a blank line). */
export function eventsNotes(rows: readonly EventRow[], opts: EventsOpts = {}): string {
  const d = eventsData(rows, opts);
  const o = { ...EVENTS_DEFAULTS, ...opts };
  const s = eventsStats(rows, opts);
  const marks = d.rows.filter((r) => r.row_type === 'mark');
  const priced = marks.filter((r) => r.filled).length;
  const named = (g: (typeof BUYER_GROUPS)[number]) => g.types.map((t) => (BUYER_TYPE_LABEL[t] ?? t).toLowerCase());
  const outside = BUYER_GROUPS.find((g) => g.key === 'outside') as (typeof BUYER_GROUPS)[number];
  const grey = BUYER_GROUPS.find((g) => g.key === 'none') as (typeof BUYER_GROUPS)[number];
  const groups = `security vendors; private equity; buyers outside security (${named(outside).join(', ')}); grey for ${named(grey).join(' and ')}`;
  const other = d.nonUsd ? ` ${d.nonUsd} price${d.nonUsd === 1 ? '' : 's'} in another currency ${d.nonUsd === 1 ? 'is' : 'are'} drawn hollow, never converted.` : '';
  return [
    `What is counted: deals, not events. A deal is dated by its announcement or, when the ledger first records it at completion, by the completion; an announcement and its closing are one deal, and a take-private that repeats an announcement counts once. `
    + `${upFirst(eventsCounted(s))} fall in the twelve months to ${s.windowTo ? fmtDay(s.windowTo) : 'the window end'}; ${s.closed} ${s.closed === 1 ? 'deal' : 'deals'} closed in that window, whenever announced. `
    + `Every figure was re-read on a public page whose link is given in the events table, and the ledger is not a census of deals. A deal whose price was not marked comparable is in the \u201cReported or not comparable, never charted\u201d table, not here.`,
    `Size: ${priced} marks carry a purchase price, area on a log scale in US dollars: the amount in a filing where there is one (the latest on record), else the announced price. Prices of ${fmtMoney(o.labelMin)} or more are labelled (${fmtMoney(o.labelMinNarrow)} or more on a narrow screen). `
    + `Hollow marks carry none: undisclosed, a closing that follows an announcement (its price sits there), a funding round (money raised, not a price) or a listing.${other}`,
    `Colour is the buyer, eight types in three slots: ${groups}. ${BUYER_TYPE_RULE} Shape is the kind of event. Dashed line: start of the twelve-month window the title counts.`,
  ].join('\n\n');
}

const titleMemo = new WeakMap<object, Map<string, LedgerTitleMap>>();

/**
 * Titles for every state of the controls figure 7 answers to, with the controls that matter (`keys`), the map
 * (`titles`) and the build-time title (`initial`). Memoised per rows array and options.
 */
export function eventsTitleMap(rows: readonly EventRow[], opts: EventsOpts = {}): LedgerTitleMap {
  const o = JSON.stringify({ ...EVENTS_DEFAULTS, ...opts });
  const byOpts = titleMemo.get(rows) ?? new Map<string, LedgerTitleMap>();
  titleMemo.set(rows, byOpts);
  let hit = byOpts.get(o);
  if (!hit) {
    hit = ledgerTitleMap(EVENTS_DIMS, (state) => eventsFinding(rows, { ...opts, state }));
    byOpts.set(o, hit);
  }
  return hit;
}
/** `titles` for <VegaChart group="ledger" titles={...} titleKeys={eventsTitleKeys(rows, opts)}>. */
export const eventsTitles = (rows: readonly EventRow[], opts: EventsOpts = {}): Record<string, string> => eventsTitleMap(rows, opts).titles;
/** `titleKeys` for the same chart: the controls that change a title for this data, in control order. */
export const eventsTitleKeys = (rows: readonly EventRow[], opts: EventsOpts = {}): LedgerDim[] => eventsTitleMap(rows, opts).keys;

// --- the table behind the timeline ---------------------------------------------------------------------------------
/** One row per mark of the timeline, newest first. Cells are HTML-safe; `counted` says whether the mark opens a deal inside the title's window. */
export interface EventsTableRow {
  date: string; company: string; event: string; layer: string; buyer: string; buyer_type: string; price: string; price_kind: string;
  counted: 'yes' | 'no'; status: string; source: string;
}

export function eventsTableRows(rows: readonly EventRow[], opts: EventsOpts = {}): EventsTableRow[] {
  const o = { ...EVENTS_DEFAULTS, ...opts };
  const deals = eventDeals(rows);
  const dealOf = new Map<EventRow, Deal>();
  deals.forEach((d) => d.members.forEach((m) => dealOf.set(m, d)));
  const to = windowEnd(rows, opts.asOf);
  const from = to ? oneYearBefore(to) : null;
  return chartableEvents(rows)
    .filter((e) => (e.event_date as string) >= o.from && KIND_SHAPE[e.event_kind as string] && e.layer && !isFolded(e, dealOf.get(e) ?? null))
    .sort((a, b) => (b.event_date as string).localeCompare(a.event_date as string) || (a.company as string).localeCompare(b.company as string))
    .map((e) => {
      const deal = dealOf.get(e) ?? null;
      const mk: Mark = { e, deal, kind: e.event_kind as string, size: null, area: 0, layer: e.layer as string, lane: 0, laneN: 0 };
      const counted = deal != null && deal.members[0] === e && from != null && to != null && deal.start > from && deal.start <= to;
      const kind = priceKindOf(e, deal);
      return {
        date: e.event_date as string, company: htmlText(e.company as string), event: KIND_LABEL[e.event_kind as string], layer: layerName(e.layer),
        buyer: e.counterparty ? htmlText(e.counterparty) : '—', buyer_type: e.buyer_type ? BUYER_TYPE_LABEL[e.buyer_type] ?? e.buyer_type : '—',
        price: esc(e.event_kind === 'acquisition_closed' && deal && deal.members[0] !== e ? 'see the announcement' : tipPrice(mk)),
        price_kind: kind ? kindLabel(kind) : '—', counted: counted ? 'yes' : 'no', status: esc(tipStatus(mk) || '—'), source: linkHtml(e.source_url, e.source_name),
      } satisfies EventsTableRow;
    });
}
export const EVENTS_TABLE_COLUMNS = [
  { key: 'date', label: 'Date' },
  { key: 'company', label: 'Company' },
  { key: 'event', label: 'Event' },
  { key: 'layer', label: 'Layer' },
  { key: 'buyer', label: 'Buyer or lead investor' },
  { key: 'buyer_type', label: 'Buyer type' },
  { key: 'price', label: 'Price' },
  { key: 'counted', label: 'Opens a deal counted in the title' },
  { key: 'status', label: 'Status' },
  { key: 'source', label: 'Source' },
];
export const EVENTS_TABLE_ROW_HEADER = 'company';
export function eventsTableSelects(rows: readonly EventsTableRow[]) {
  const uniq = (xs: readonly string[]) => [...new Set(xs)].sort((a, b) => a.localeCompare(b));
  return [
    { column: 'event', label: 'Event', options: uniq(rows.map((r) => r.event)) },
    { column: 'buyer_type', label: 'Buyer type', options: uniq(rows.map((r) => r.buyer_type)) },
    { column: 'counted', label: 'Counted in the title', options: ['yes', 'no'] },
  ];
}

// --- announced-to-filing dumbbell ---------------------------------------------------------------------------------
export interface DumbbellRow {
  company: string; buyer: string; layer: string | null; announced: number; filing: number; change: number; differs: boolean;
  announcedQualifier: string; filingQualifier: string; unit: string; start: string; closed: string | null;
}
/** Why a deal with both an announced and a filed figure is not compared. */
export type LeftOutReason = 'ceiling' | 'open' | 'no_price_at_signing' | 'filer_is_target' | 'other_currency';
export interface DumbbellLeftOut { company: string; reasons: LeftOutReason[] }

/** Half a unit in the last digit of a rounded ("about") figure: 7.8e9 stands for anything from 7.75e9 to 7.85e9. */
function roundingSlack(v: number, q: string): number {
  if (q !== '~') return 0;
  const digits = v.toExponential().split('e')[0].replace('.', '').replace('-', '').replace(/0+$/, '');
  const exp = Math.floor(Math.log10(Math.abs(v)));
  return 0.5 * 10 ** (exp - Math.max(1, digits.length) + 1);
}
/** Two figures are the same when they differ by no more than the rounding of an approximate one (a cent of tolerance otherwise). */
export function sameWithinRounding(a: number, aq: string, f: number, fq: string): boolean {
  return Math.abs(f - a) <= Math.max(1, roundingSlack(a, aq) + roundingSlack(f, fq));
}

/**
 * Which deals can be compared like with like, and which have both figures but cannot be. Compared: a deal from `from`
 * on that has closed, both figures in US dollars, an announced price stated at signing as an exact or approximate figure
 * (not "up to" or "more than"), and a filing figure that is the latest on record (the completed-deal note, not one written
 * while the deal was pending). A take-private is left out because its filer is the target: a private-equity buyer files
 * nothing.
 */
export function eventDumbbellSelection(rows: readonly EventRow[], opts: EventsOpts = {}): { rows: DumbbellRow[]; leftOut: DumbbellLeftOut[] } {
  const from = opts.from ?? EVENTS_DEFAULTS.from;
  const picked: DumbbellRow[] = [];
  const leftOut: DumbbellLeftOut[] = [];
  for (const dl of eventDeals(rows)) {
    const c = dl.consideration;
    if (dl.start < from || !c || c.announced == null || c.filing == null) continue;
    const reasons: LeftOutReason[] = [];
    if (c.unit !== 'USD') reasons.push('other_currency');
    if (filingOwner(dl) === 'target') reasons.push('filer_is_target');
    if (c.announcedQualifier === '<' || c.announcedQualifier === '>') reasons.push('ceiling');
    if (!c.announcedAtSigning) reasons.push('no_price_at_signing');
    if (!dl.closed) reasons.push('open');
    if (reasons.length) { leftOut.push({ company: dl.company, reasons }); continue; }
    const aq = c.announcedQualifier ?? '=';
    const fq = c.filingQualifier ?? '=';
    picked.push({
      company: dl.company, buyer: dl.counterparty ?? '—', layer: dl.layer, announced: c.announced, filing: c.filing, change: c.filing / c.announced - 1,
      differs: !sameWithinRounding(c.announced, aq, c.filing, fq), announcedQualifier: aq, filingQualifier: fq, unit: c.unit, start: dl.start, closed: dl.closed,
    });
  }
  picked.sort((p, q) => p.change - q.change || p.company.localeCompare(q.company));
  return { rows: picked, leftOut: leftOut.sort((a, b) => a.company.localeCompare(b.company)) };
}

/** The deals compared like with like, one row each (see `eventDumbbellSelection`). `differs` is false when the two are the same number within rounding (not drawn as a dumbbell). */
export const eventDumbbellRows = (rows: readonly EventRow[], opts: EventsOpts = {}): DumbbellRow[] => eventDumbbellSelection(rows, opts).rows;

export function eventDumbbellSpec(rows: readonly EventRow[], opts: EventsOpts = {}) {
  const all = eventDumbbellRows(rows, opts);
  const drawn = all.filter((r) => r.differs);
  const vs = drawn.flatMap((r) => [r.announced, r.filing]);
  const [d0, d1] = vs.length ? [snap125(Math.min(...vs) * 0.8, 'down'), snap125(Math.max(...vs) * 1.25, 'up')] : [1e8, 1e10];
  const ticks: number[] = [];
  for (let k = Math.floor(Math.log10(d0)); k <= Math.ceil(Math.log10(d1)); k++) for (const m of [1, 2, 5]) { const v = +(m * 10 ** k).toPrecision(1); if (v >= d0 * (1 - 1e-9) && v <= d1 * (1 + 1e-9)) ticks.push(v); }
  const values = drawn.map((r) => ({
    company: r.company, announced: r.announced, filing: r.filing, hi: Math.max(r.announced, r.filing), change: r.change, pct: fmtPct(r.change),
    tip_buyer: r.buyer, tip_announced: fmtValue(r.announced, r.unit, r.announcedQualifier), tip_filing: fmtValue(r.filing, r.unit, r.filingQualifier),
    tip_dates: `announced ${fmtDay(r.start)}${r.closed ? `, closed ${fmtDay(r.closed)}` : ''}`, tip_layer: layerName(r.layer),
  }));
  const xs = (field: string) => ({
    field, type: 'quantitative', title: null,
    scale: { type: 'log', domain: [d0, d1], nice: false, padding: 14 },
    axis: { labelExpr: "replace(format(datum.value, '$.2~s'), 'G', 'B')", values: ticks, labelOverlap: 'greedy', labelSeparation: 8 },
  });
  const tooltip = [
    { field: 'company', type: 'nominal', title: 'Target' },
    { field: 'tip_buyer', type: 'nominal', title: 'Buyer' },
    { field: 'tip_announced', type: 'nominal', title: 'Announced price' },
    { field: 'tip_filing', type: 'nominal', title: 'In the filing' },
    { field: 'pct', type: 'nominal', title: 'Filing vs announced' },
    { field: 'tip_dates', type: 'nominal', title: 'Dates' },
  ];
  return {
    $schema: VL_SCHEMA,
    title: reasonTitle(values.length ? "''" : lit('No closed deal has two different prices on\nrecord that can be compared.')),
    height: { step: 24 },
    padding: { right: 44 },
    data: { values, name: 'db_src' },
    encoding: { y: { field: 'company', type: 'nominal', title: null, sort: { field: 'change', op: 'min', order: 'ascending' }, axis: { labelLimit: { expr: `width < ${NARROW_PLOT} ? 84 : 110` }, ticks: false, domain: false, grid: true, labelFontSize: 11 } } },
    layer: [
      {
        mark: { type: 'rule', strokeWidth: 2, color: tok('ink-3'), opacity: 0.85, aria: false },
        encoding: { x: xs('announced'), x2: { field: 'filing' }, tooltip },
      },
      {
        // The announced price: hollow.
        mark: { type: 'point', shape: 'circle', size: 70, strokeWidth: 1.6, filled: false },
        encoding: { x: xs('announced'), stroke: { value: tok('ink-2') }, fill: { value: 'transparent' }, tooltip },
      },
      {
        // The price in the filing: filled.
        mark: { type: 'point', shape: 'circle', size: 70, strokeWidth: 1.6, filled: true },
        encoding: { x: xs('filing'), color: { value: series.a }, tooltip },
      },
      {
        mark: { type: 'text', align: 'left', baseline: 'middle', dx: 9, fontSize: 10.5, aria: false },
        encoding: { x: xs('hi'), text: { field: 'pct', type: 'nominal' }, color: { value: tok('ink-2') } },
      },
    ],
  };
}

const median = (xs: number[]): number | null => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

/** Title: in how many of the closed deals compared like with like the filing records less, by how much. Computed from the rows that are drawn. */
export function eventDumbbellFinding(rows: readonly EventRow[], opts: EventsOpts = {}): string {
  const all = eventDumbbellRows(rows, opts);
  if (!all.length) return 'No closed acquisition has an announced price and a filed price that can be compared like with like';
  const lower = all.filter((r) => r.differs && r.change < 0);
  const same = all.filter((r) => !r.differs).length;
  const higher = all.filter((r) => r.differs && r.change > 0).length;
  if (!lower.length) return `None of the ${all.length} closed acquisitions with a comparable announced and filed price records less in the filing`;
  const worst = lower[0];
  const m = median(lower.map((r) => r.change)) as number;
  const tail = [same ? `${numWord(same)} ${same === 1 ? 'is' : 'are'} the same within rounding` : '', higher ? `${numWord(higher)} ${higher === 1 ? 'is' : 'are'} higher` : ''].filter(Boolean).join(', ');
  const tailText = tail ? `; ${tail}` : '';
  const scope = lower.length === all.length ? `all ${all.length}` : `${lower.length} of the ${all.length}`;
  return `The filing records less than the announced price in ${scope} closed acquisitions with a comparable announced and filed price: median ${fmtPct(m)} across ${lower.length === all.length ? 'them' : `those ${numWord(lower.length)}`}, widest ${fmtPct(worst.change)} (${worst.company})${tailText}`;
}

/** Subtitle: the metric, unit and scale only. */
export function eventDumbbellSubtitle(rows: readonly EventRow[], opts: EventsOpts = {}): string {
  const drawn = eventDumbbellRows(rows, opts).filter((r) => r.differs);
  const lo = drawn.length ? Math.min(...drawn.flatMap((r) => [r.announced, r.filing])) : null;
  const hi = drawn.length ? Math.max(...drawn.flatMap((r) => [r.announced, r.filing])) : null;
  return `Price in US dollars, log scale (${lo != null && hi != null ? fmtRange(lo, hi) : 'no prices'}): the price announced at signing, hollow, against the amount the filing records for the completed deal, filled.`;
}

const LEFT_OUT_TEXT: Record<LeftOutReason, string> = {
  ceiling: 'the announced figure is a ceiling or a floor, not a price',
  open: 'the deal has not closed, so its filing figure is not from a completed-deal note',
  no_price_at_signing: 'the announcement stated no price, which first appears at completion',
  filer_is_target: 'a take-private: the only filing is the target’s own',
  other_currency: 'the price is not in US dollars',
};

/** Everything the subtitle leaves out, as plain text for the "How to read this" disclosure (paragraphs separated by a blank line). */
export function eventDumbbellNotes(rows: readonly EventRow[], opts: EventsOpts = {}): string {
  const sel = eventDumbbellSelection(rows, opts);
  const drawn = sel.rows.filter((r) => r.differs).length;
  const same = sel.rows.length - drawn;
  const out = sel.leftOut.length
    ? `Left out although both figures exist: ${joinAnd(sel.leftOut.map((l) => `${l.company} (${l.reasons.map((r) => LEFT_OUT_TEXT[r]).join('; ')})`))}.`
    : 'No deal with both figures is left out.';
  return [
    `Like with like: a deal is compared only if it has closed, both figures are in US dollars, the announced price was stated at signing as an exact or approximate figure, and the filing figure is the amount the buyer records for the completed deal. ${upFirst(numWord(sel.rows.length))} ${sel.rows.length === 1 ? 'deal meets' : 'deals meet'} that test; ${same ? `${numWord(drawn)} ${drawn === 1 ? 'is' : 'are'} drawn and ${numWord(same)} ${same === 1 ? 'carries' : 'carry'} the same number in both, within rounding` : `${drawn === 1 ? 'it is' : 'all are'} drawn`}.`,
    `Why the two differ: adjustments at closing, a share-price move for a deal paid in stock (the announced figure uses the share price at announcement, the filing the price at closing), an earn-out the filing excludes, and cash acquired. The label is the filing against the announcement; the median is over the deals where the filing records less.`,
    `${out} This is a selected set, the deals where the ledger holds both figures; a price recorded once is in the timeline.`,
  ].join('\n\n');
}

/** One row per compared deal, plain text (the table and a CSV can share the file). */
export interface DumbbellTableRow {
  company: string; buyer: string; announced: string; filing: string; change: string; reading: string; announced_on: string; closed_on: string;
}
export function dumbbellTableRows(rows: readonly EventRow[], opts: EventsOpts = {}): DumbbellTableRow[] {
  return eventDumbbellRows(rows, opts).map((r) => ({
    company: r.company, buyer: r.buyer, announced: fmtValue(r.announced, r.unit, r.announcedQualifier), filing: fmtValue(r.filing, r.unit, r.filingQualifier),
    change: r.differs ? fmtPct(r.change, 1) : '0%', reading: !r.differs ? 'same within rounding' : r.change < 0 ? 'filing lower' : 'filing higher',
    announced_on: fmtDay(r.start), closed_on: r.closed ? fmtDay(r.closed) : '—',
  }));
}
export const DUMBBELL_TABLE_COLUMNS = [
  { key: 'company', label: 'Target' },
  { key: 'buyer', label: 'Buyer' },
  { key: 'announced', label: 'Announced price' },
  { key: 'filing', label: 'In the filing' },
  { key: 'change', label: 'Filing against announced', align: 'right' as const },
  { key: 'reading', label: 'Reading' },
  { key: 'announced_on', label: 'Announced' },
  { key: 'closed_on', label: 'Closed' },
];
export const DUMBBELL_TABLE_ROW_HEADER = 'company';

/** The price kinds a deal's mark can carry, for a check that the Kind control offers no kind the data lack (and none the control omits). */
export function priceKindsPresent(rows: readonly EventRow[], opts: EventsOpts = {}): string[] {
  const d = eventsData(rows, opts);
  const present = new Set(d.rows.filter((r) => r.row_type === 'mark').map((r) => r.metric_kind as string).filter(Boolean));
  return (EVENT_PRICE_KINDS as readonly string[]).filter((k) => present.has(k)).concat([...present].filter((k) => !(EVENT_PRICE_KINDS as readonly string[]).includes(k)));
}

