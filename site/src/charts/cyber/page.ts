/**
 * Page-only helpers for site/src/content/projects/cybersecurity-landscape.mdx.
 *
 * The page may not hand-type a number (docs/cyber/SITE_SPEC.md), so everything it prints that is computed rather than read
 * straight from `data/facts/cyber.json` is computed here, from the facts and the published marts, and the MDX only
 * interpolates it. Nothing in this file does I/O: it imports the same JSON the page imports and the figure registry of
 * `external.ts`, so a table, a title and a sentence can never disagree with the chart beside them. Where a count exists both
 * in the facts and as something this file can recompute from a mart, `agree()` compares the two and stops the build when they
 * differ, so a figure title and a sentence cannot drift apart.
 *
 * Sections: formatting, the lead, the scoreboard, titles of the three dashboards, table rows, sentence facts, data quality.
 */
import factsJson from '@data/facts/cyber.json';
import excludedJson from '@data/marts/cyber/excluded.json';
import etfJson from '@data/marts/cyber/etf_crosscheck.json';
import reportedJson from '@data/marts/cyber/reported_unconfirmed.json';
import dictionaryJson from '@data/marts/cyber/metric_dictionary.json';
import { changelog } from '@lib/changelog';
import { figureRows, marts } from './external';
import { layerMapFinding, privateOutnumber, type LayerMapData } from './layer_map';
import { evGrowthFinding, type EvGrowthData } from './ev_growth_scatter';
import { rule40Finding, type Rule40Data } from './rule40_ranked';
import { EXPOSURE_LABEL, LISTING_TABLE_COLUMNS, MIN_ANALYSTS, layerShort, listingTableRows, missingMultiples, ringBNote, valuationTitleMap } from './valuation_data';
import { targetDumbbellTitles, targetStats, type DumbbellRows } from './target_dumbbell';
import { ratingStats, recommendationBarsNote, recommendationBarsTitles, type RecommendationRows } from './recommendation_bars';
import { SUSPECT_RATIO, andList, chinaTableRows, fmtUpside, numWord, ratingsTableRows, shortName, targetsTableRows } from './targets_data';
import { chinaPanelStats, periodWords, type ChinaRows } from './china_panel';
import {
  intervalTableRows, intervalTableSelects, ledgerIntervalNotes, ledgerIntervalStats, ledgerIntervalSubline, ledgerIntervalSubtitle, ledgerIntervalTitleMap,
} from './ledger_intervals';
import {
  dumbbellTableRows, eventDumbbellFinding, eventDumbbellNotes, eventDumbbellSubtitle, eventsCounted, eventsNotes, eventsStats, eventsSubtitle,
  eventsTableRows, eventsTableSelects, eventsTitleMap, latestEventClause,
} from './events';
import { fmtDay, fmtMonth, fmtValue, kindLabel, layerName, ledgerTableRows, ledgerTableSelects } from './ledger_data';
import type { DictionaryRow, EtfRow, ExcludedRow, ReportedRow } from './types';

// ---- the facts, typed for the parts the page reads -------------------------------------------------------------------------

/** A published number of the facts file. `lo` and `hi` are the ends of its interval where it has one. */
export interface Fact { value: number | null; qualifier?: string | null; unit?: string; period?: string; as_of?: string; source_url?: string; lo?: number | null; hi?: number | null; level?: string; n?: number }
/** A median with the number of companies behind it and its quartiles. */
export type Stat = Fact & { n: number; p25?: Fact; p75?: Fact };
export interface EvidenceItem { label: string; value: number | null; unit: string | null; note: string | null }
export interface ScoreItem { id: string; title: string; status: string; reason: string; graded: string; next_grading: string | null; already_seen: string; evidence: EvidenceItem[] }
export interface KpiFact { id: string; label: string; value: number; qualifier: string; unit: string; period: string; as_of: string; note: string }
export interface Check { name: string; status: 'pass' | 'warn' | 'fail'; detail: string }
export interface Q6Company { company: string; layer: string; scored: boolean; above: boolean | null; n_comparables: number; thin: boolean; arr_as_of: string | null; arr_months_old: number | null; arr_old: boolean }
export interface CyberFacts {
  generated_at: string; as_of: string; registered: string;
  scope: { n_layers: number; n_listed: number; n_private: number; n_layers_without_ring_a: number; n_layers_private_outnumber_listed: number; by_ring: Record<string, { n: number }> };
  coverage: { fx_as_of: string | null };
  regression: {
    n: number; n_us: number; n_cn: number; n_params: number; draws: number; ci_draws: number; series_from: string; n_series: number; model: string; fitted_on: string; note: string;
    r2: Fact; adj_r2: Fact; adj_r2_no_layer: Fact; layer_gain: Fact; slope: Fact; fcf_coef: Fact; china_dummy: Fact;
    premium: { ai_trio_median: Fact; rest_median: Fact; ratio: Fact; n_trio: number; n_rest: number };
  };
  ev_sales: { n_ring_a: number; n_with: number; n_refused: number; median: Stat; pure_play_median: Stat; us_pure_play_median: Stat; us_population_median: Stat; cn_median: Stat; cn_us_ratio: Fact; as_of: string };
  rule_of_40: { n_scored: number; n_pass: number; cut: number; median_score: Fact; leaders: { name: string; value: number; growth: Fact; fcf_margin: Fact }[] };
  targets: {
    as_of: string; n_ring_a_outside_china: number; n_with_target: number; n_above_price: number; n_thin: number; thin_below: number; median_upside: Fact;
    covered: { n: number; median_upside: Fact }; q4_q5_list: { n: number; median_upside: Fact };
    most_upside: Fact & { name: string; ticker: string }; least_upside: Fact & { name: string; ticker: string };
    thin_extremes: (Fact & { name: string; ticker: string; n_opinions: number })[];
  };
  recommendations: {
    as_of: string;
    yahoo: { n_ratings: number; counts: Record<string, number>; shares: Record<string, Fact> };
    eastmoney: { n_listings: number; n_covered: number; n_wholly_null: number; n_ratings: number; counts: Record<string, number | null>; window: string };
  };
  cn: {
    n_listed: number; n_ring_a: number; n_ring_b: number; n_flagged: number; n_top_quartile: number; n_revenue_falling: number; flagged: string[]; as_of: string; n_opinions_min: number; n_opinions_max: number;
    median_ps_ttm: Fact; median_ps_percentile: Fact; median_revenue_yoy: Fact;
  };
  private: {
    n_directory: number; n_ledger_rows: number; n_chartable: number; n_reported_unconfirmed: number; n_stale: number; n_with_arr: number; n_with_headline: number;
    n_q6_scored: number; n_q6_above: number; multiples_as_of: string; n_china_rows: number; n_reported_duplicates: number;
    n_reported_by_group: Record<string, number>;
    latest_valuation_max: { company: string; value: number; unit: string; qualifier: string; period: string; basis: string };
    q6: { thin_below: number; old_arr_months: number; companies: Q6Company[]; thin_layers: string[] };
  };
  events: {
    n: number; n_unconfirmed: number; acquisitions_12m: number; acquisitions_announced_12m: number; acquisitions_first_recorded_at_completion_12m: number;
    acquisitions_closed_12m: number; acquisitions_closed_12m_announced_earlier: number; acquisitions_not_charted_12m: number;
    acquisitions_12m_by_buyer_type: Record<string, number>; n_sec_priced: number; n_sec_priced_deals: number; n_sec_priced_deals_12m: number;
    window_from: string; window_to: string; by_kind: Record<string, number>;
    latest: { company: string; event_kind: string; event_date: string; counterparty: string; chart_value: number; chart_qualifier: string; unit: string; status_after: string | null };
  };
  universe: { etfs: { fund: string; as_of: string; source_url: string; counts: Record<string, number> }[] };
  kpis: KpiFact[];
  scoreboard: ScoreItem[];
  counts: {
    scoreboard: Record<string, number>;
    checks: { n: number; n_pass: number; n_warn: number; n_fail: number; n_warn_waiting: number; n_warn_data: number; warn_waiting: string[]; warn_data: string[] };
  };
  marts: Record<string, string>;
  sources: { name: string; url: string }[];
  checks: Check[];
}
export const facts = factsJson as unknown as CyberFacts;

/** Stop the build when a count the page prints from one place differs from the same count held in the facts. */
function agree(what: string, page: number | string, fromFacts: number | string): void {
  if (page !== fromFacts) throw new Error(`[cyber page] ${what}: the page computes ${page}, the facts say ${fromFacts}`);
}

// ---- formatting -----------------------------------------------------------------------------------------------------------

/** A multiple: `7.3x`. */
export const x1 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : `${v.toFixed(1)}x`);
/** A plain number with one decimal: `4.8`. */
export const dec1 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(1));
/** A share as a whole percent: `61%`. */
export const pctWhole = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : `${Math.round(v * 100)}%`.replace('-', '−'));
/** A share as a percent with one decimal and a true minus: `114.3%`, `−7.6%`. */
export const pct1 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`.replace('-', '−'));
/** Adjusted R squared and the like, two decimals. */
export const dec2 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(2));
/** A signed percent with a true minus: `+13%`, `−3%`. */
export const signed = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : fmtUpside(v).replace('-', '−'));
/** A signed percent with one decimal, for small changes: `−2.5%`. */
export const signed1 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(1)}%`);
/** A hyphen-minus in front of a digit becomes the true minus the charts use; dates (`2026-10-26`) and `t-1` are left alone. */
export const minusify = (t: string): string => t.replace(/(^|[\s(,;])-(?=\d)/g, '$1−');
/** `2027-01-11` as `11 Jan 2027`: the one date format of the page. */
export const day = fmtDay;
/** Every ISO date inside a sentence (`2026-10-26`) in the page's date format; other digits are left alone. */
export const prettyDates = (t: string): string => t.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (d) => fmtDay(d));
export const joinList = andList;
const joinAnd = andList;
export const published = fmtDay(facts.generated_at.slice(0, 10));
/** The number words the page needs for counts, so prose can say `three` where a count equals three, and digits otherwise. */
export const words = (n: number): string => ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'][n] ?? String(n);
export const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
export const lcFirst = (s: string): string => (s ? s[0].toLowerCase() + s.slice(1) : s);
export const plural = (n: number, one: string, many = `${one}s`): string => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const uniqSorted = (xs: (string | null | undefined)[]): string[] => [...new Set(xs.filter((x): x is string => !!x))].sort((a, b) => a.localeCompare(b));

/** The window the multiples are taken over, as the registered method names it. It is a name, kept in one place. */
export const NTM = 'next-twelve-month';
/** The Rule of 40 name, with its cut read from the facts. */
export const R40 = `Rule of ${facts.rule_of_40.cut}`;
/** A step of growth for reading the slope: ten points, the unit the figure's subtitle uses. */
export const GROWTH_STEP_POINTS = 10;
/** Fewest analysts behind a multiple or a target for it to count as covered (the registered minimum, `MIN_ANALYSTS`), as a word. */
export const MIN_ANALYSTS_WORD = words(MIN_ANALYSTS);

/** The sources the page cites, found by name in the facts so a re-ordered list cannot send a figure to the wrong one. */
export const sources = (() => {
  const find = (re: RegExp) => {
    const s = facts.sources.find((x) => re.test(x.name));
    if (!s) throw new Error(`[cyber page] no source in the facts matches ${re}`);
    return s;
  };
  return {
    yahoo: find(/^Yahoo/), eastmoney: find(/profit forecast/i), emValuation: find(/valuation analysis/i), emReports: find(/financial reports/i),
    cibr: find(/CIBR/), filings: find(/filings and announcements/i), plan: find(/evaluation plan/i), fred: find(/^FRED/),
  };
})();

// ---- the lead: what the first cross-section says ----------------------------------------------------------------------------

const reg = facts.regression;
const scoreItem = (id: string): ScoreItem | undefined => facts.scoreboard.find((q) => q.id === id);

/** The multiplier on the fitted multiple for `GROWTH_STEP_POINTS` more points of expected growth, with its 95% interval. */
export function growthMultiple(points = GROWTH_STEP_POINTS): { mult: number; lo: number | null; hi: number | null } {
  const f = (b: number | null | undefined): number | null => (b == null ? null : Math.exp((b * points) / 100));
  return { mult: f(reg.slope.value) as number, lo: f(reg.slope.lo), hi: f(reg.slope.hi) };
}

/**
 * What the first cross-section says, in words the registered baseline supports (owner decision D4): faster expected growth goes with a
 * higher multiple, the layers add no visible fit in this sample, and Q1, the registered test of the layer question, is read off the
 * "already seen" text of the scoreboard, so the sentence follows the reading and cannot claim more than it. A gain above zero is not
 * called "adds some": the sentence says what the model shows and leaves the question to Q1.
 * `claim` opens the page, `support` gives the numbers, `step` reads the slope, `q1` says what the registered test reads.
 */
export function layerVerdict(): { adds: boolean; claim: string; support: string; short: string; step: string; q1: string } {
  const base = reg.adj_r2_no_layer.value as number;
  const full = reg.adj_r2.value as number;
  const gain = reg.layer_gain.value ?? full - base;
  const slopePositive = reg.slope.lo != null && reg.slope.lo > 0;
  const same = dec2(base) === dec2(full);
  const move = gain < 0 && !same ? `lowers that from ${dec2(base)} to ${dec2(full)}` : gain > 0 && !same ? `raises that from ${dec2(base)} to ${dec2(full)}` : `leaves that at ${dec2(base)}`;
  const adds = gain > 0 && !same;
  const short = adds ? 'the layers raise the fit in this sample' : 'the layers add no visible fit in this sample';
  const grows = 'listed cybersecurity companies with faster expected growth trade at higher multiples of revenue';
  const claim = slopePositive
    ? (adds
      ? `In the first snapshot, ${grows}, and adding the layer of security a company sells raises the fit in this sample. Whether that is more than chance is what Q1, the registered test, is for.`
      : `In the first snapshot, ${grows}, and the layer of security a company sells adds no visible fit on top of growth. That describes this small sample; it does not show that layers are unpriced.`)
    : 'In the first snapshot, expected growth does not separate the multiples of listed cybersecurity companies with confidence.';
  const support = `In the page's own model, growth, free-cash-flow margin and a China indicator explain ${dec2(base)} of the variation in EV/NTM revenue across the ${reg.n} listed companies it is fitted on (adjusted R²); adding the layer groups ${move}.`;
  const step = growthMultiple();
  const stepSentence = `With cash margin, listing market and layer held fixed, each additional ${GROWTH_STEP_POINTS} points of expected growth goes with a multiple ${dec1(step.mult)} times as high (${reg.slope.level} interval ${dec1(step.lo)} to ${dec1(step.hi)}).`;
  return { adds, claim, support, short, step: stepSentence, q1: q1Sentence() };
}

/** What Q1 reads on the baseline, in a sentence that claims no more than the reading does. */
function q1Sentence(): string {
  const q1 = scoreItem('Q1');
  if (!q1) return '';
  const read = baselineOf('Q1');
  const uplift = /changes it by ([+\-−]?\d+(?:\.\d+)?)/.exec(q1.already_seen);
  const noGain = uplift ? Number(uplift[1].replace('−', '-')) <= 0 : false;
  const head = `Q1 is the registered test of the layer question, on the ${reg.n_us} US companies alone and with a simpler model.`;
  const when = q1.next_grading ? ` It is graded on ${fmtDay(q1.next_grading)}.` : '';
  if (read === 'undecidable') {
    const why = noGain ? ': the layers show no gain, but the interval is wide enough to include the gain the claim asks for' : '';
    return `${head} On the baseline it reads undecidable, which means the data neither support nor rule out a layer effect${why}.${when}`;
  }
  if (read) return `${head} On the baseline it reads ${read}.${when}`;
  return `${head} It has no reading on the baseline.${when}`;
}

/** The two medians of the lead: the registered group and every listed company with a multiple. */
export const headline = {
  registered: { median: facts.ev_sales.us_population_median.value as number, n: facts.ev_sales.us_population_median.n },
  all: { median: facts.ev_sales.median.value as number, n: facts.ev_sales.median.n, p25: facts.ev_sales.median.p25?.value as number, p75: facts.ev_sales.median.p75?.value as number },
  usPurePlays: { median: facts.ev_sales.us_pure_play_median.value as number, n: facts.ev_sales.us_pure_play_median.n },
};

// ---- the scoreboard ---------------------------------------------------------------------------------------------------------

/** `config.QSTATUS` in words. */
export const STATUS_LABEL: Record<string, string> = { collecting: 'Collecting', holds: 'Holds', falsified: 'Falsified', undecidable: 'Undecidable' };

/** Claims whose "already seen" text carries a reading on the baseline ("the rule reads holds"), grouped by the word. */
export function baselineReads(): { status: string; ids: string[] }[] {
  const by = new Map<string, string[]>();
  for (const q of facts.scoreboard) {
    const m = /\breads (holds|falsified|undecidable)\b/i.exec(q.already_seen);
    if (m) by.set(m[1].toLowerCase(), [...(by.get(m[1].toLowerCase()) ?? []), q.id]);
  }
  return [...by].map(([status, ids]) => ({ status, ids }));
}
/** The reading of one claim on the baseline snapshot (`holds`, `falsified`, `undecidable`), or null when its "already seen" text has none. */
export const baselineOf = (id: string): string | null => baselineReads().find((r) => r.ids.includes(id))?.status ?? null;
/** One sentence on the baseline readings: which claims read what, and that none is a result. */
export function baselineSentence(): string {
  const reads = baselineReads();
  const n = reads.reduce((s, r) => s + r.ids.length, 0);
  if (!n) return 'No claim has a reading on the baseline snapshot.';
  const parts = reads.map((r) => `${andList(r.ids)} read ${r.status}`);
  return `${cap(words(n))} ${n === 1 ? 'has' : 'have'} a reading on the baseline snapshot, taken while the claims were being written: ${andList(parts)}. They are labelled "already seen" and are not results.`;
}
/** Claims that assert nothing happens: the sample supports them most easily, so a hold on them is weak evidence (section I, power). */
export const noEffectClaims = facts.scoreboard.filter((q) => /does not (predict|widen)/i.test(q.title)).map((q) => q.id);
/** Claims that already read holds on the baseline: their thresholds were set with that reading in view, so a later hold confirms the first look and nothing more. */
export const holdsAlready = facts.scoreboard.filter((q) => baselineOf(q.id) === 'holds').map((q) => q.id);
export const collecting = facts.scoreboard.filter((q) => q.status === 'collecting').length;
/** The earliest date any claim is graded or read. */
export const firstGrading = facts.scoreboard.map((q) => q.next_grading).filter((d): d is string => !!d).sort()[0] ?? null;

/** The trio of the Q9 claim, by ticker: a registered list (names, not numbers). */
const AI_TRIO = ['PANW', 'CRWD', 'NET'];

/** What Q3 read on the baseline, parsed from its "already seen" text: the two medians and their ratio. */
export function q3Split(): { high: number; low: number; ratio: number; nHigh: number; nLow: number } | null {
  const q3 = scoreItem('Q3');
  const m = q3 ? /(\d+) companies at or above \d+ with a median of ([\d.]+)x and (\d+) below with a median of ([\d.]+)x, a ratio of (\d+(?:\.\d+)?)/.exec(q3.already_seen) : null;
  return m ? { nHigh: Number(m[1]), high: Number(m[2]), nLow: Number(m[3]), low: Number(m[4]), ratio: Number(m[5]) } : null;
}

/**
 * The first look of a claim in plain words, for the scoreboard. The numbers are read from the "already seen" text the pipeline publishes
 * (never typed here), and a text that does not match falls back to the reading alone.
 */
export function firstLook(q: ScoreItem): string {
  const t = minusify(q.already_seen);
  const read = baselineOf(q.id);
  const generic = read ? `Reads ${read} on the baseline snapshot.` : 'No reading on the baseline snapshot.';
  const num = (s: string) => Number(s.replace('−', '-'));
  const level = reg.slope.level ?? '95%';
  switch (q.id) {
    case 'Q1': {
      const u = /changes it by ([+\-−]?\d+(?:\.\d+)?)/.exec(t);
      if (read === 'undecidable' && u && num(u[1]) <= 0) return 'The layers show no gain on the first look, but the interval is wide enough that a gain cannot be ruled out.';
      return generic;
    }
    case 'Q2': {
      const m = /coefficients are (\d+(?:\.\d+)?) for growth and (\d+(?:\.\d+)?) for FCF margin.*?interval of ([+\-−]?\d+(?:\.\d+)?) to ([+\-−]?\d+(?:\.\d+)?)/.exec(t);
      if (!m) return generic;
      const [g, f, lo, hi] = [num(m[1]), num(m[2]), num(m[3]), num(m[4])];
      return `${g > f ? 'Growth' : 'Free-cash-flow margin'} carries the larger standardised coefficient (${m[1]} against ${m[2]}), but the ${level} interval of the difference, ${m[3]} to ${m[4]}, ${lo < 0 && hi > 0 ? 'takes in zero' : 'does not take in zero'}.`;
    }
    case 'Q3': {
      const s = q3Split();
      return s ? `Companies at or above the cut trade at ${dec1(s.ratio)} times the multiple of those below (${x1(s.high)} against ${x1(s.low)}).` : generic;
    }
    case 'Q4': {
      const m = /median upside across the (\d+) companies is ([+\-−]?\d+(?:\.\d+)?)%/.exec(t);
      return m ? `The median upside across the ${m[1]} companies of the registered list is ${m[2].startsWith('−') ? m[2] : `+${m[2].replace(/^\+/, '')}`}%; there is no return to compare it with yet.` : generic;
    }
    case 'Q5':
      return /has not been run/i.test(t) ? 'Not run yet: the test needs the daily price history, fetched when the claim is graded.' : generic;
    case 'Q6': {
      const w = /about (\d+) to (\d+) times ARR/.exec(t);
      const a = /about (\d+(?:\.\d+)?) times a reported ARR/.exec(t);
      return w && a
        ? `Wiz was priced at about ${w[1]} to ${w[2]} times ARR and Armis at no more than about ${a[1]} times. The sources of those ARR figures are named in the plan's amendment.`
        : generic;
    }
    case 'Q7': {
      const r = /a ratio of (\d+(?:\.\d+)?)/.exec(t);
      const c = /China coefficient is ([+\-−]?\d+(?:\.\d+)?)/.exec(t);
      return r && c && num(c[1]) < 0 ? `The ${words(reg.n_cn)} A-share companies trade at ${r[1]} of the multiple of the registered group, and the China coefficient in a growth regression is negative (${c[1]}).` : generic;
    }
    case 'Q8': {
      const v = /^(.+?) meets the same definition.*?signed a merger agreement with (.+?) on/.exec(t);
      const sales = /sale processes at ([A-Z0-9.]+) and at ([A-Z0-9.]+)/.exec(t);
      const others = sales ? ` Press reports of sale processes at ${words(sales.length - 1)} other listed companies have produced no deal.` : '';
      return v ? `${v[1]} meets the same definition and signed a merger agreement with ${v[2]} in the registration week.${others}` : generic;
    }
    case 'Q9': {
      const p = facts.regression.premium;
      return p.ratio.value != null
        ? `The trio trades at ${dec1(p.ratio.value)} times the median multiple of the rest. It was picked because it sits at the top of the cross-section.` : generic;
    }
    case 'Q10': return /^Nothing/i.test(t) ? 'Nothing to read yet: the first snapshot is the start of the first pair.' : generic;
    default: return generic;
  }
}

/** The claim as the table prints it: the registered title, with the one phrase that needs a gloss explained. */
const claimText = (title: string): string => title.replace(/\(a calibration item\)/i, '(a calibration item: a count of announcements, which can always be scored)');

export interface ScoreRow { id: string; claim: string; status: string; graded: string; first: string; seen: string; reason: string }
export const scoreRows: ScoreRow[] = facts.scoreboard.map((q) => ({
  id: q.id, claim: claimText(q.title), status: STATUS_LABEL[q.status] ?? q.status, graded: prettyDates(q.graded), first: prettyDates(firstLook(q)),
  seen: prettyDates(minusify(q.already_seen)), reason: prettyDates(minusify(q.reason)),
}));

// ---- dashboard 1: valuation (titles for every combination of controls) -----------------------------------------------------

const layerData = figureRows<LayerMapData>('layer-map');
const evData = figureRows<EvGrowthData>('ev-growth');
const r40Data = figureRows<Rule40Data>('rule40-scatter');

/** `titles` and `titleKeys` for the three valuation figures (each value is `{ keys, map }`). */
export const valuationTitles = {
  layerMap: valuationTitleMap(layerData.points, (s) => layerMapFinding(layerData, s)),
  evGrowth: valuationTitleMap(evData.points, (s) => evGrowthFinding(evData, s)),
  rule40: valuationTitleMap(r40Data.points, (s) => rule40Finding(r40Data, s)),
};

/** Layers with more private than listed companies, and layers with no company in the priced pool. */
export const layerFacts = (() => {
  const noPriced = marts.layerSummary.filter((s) => (s.n_a ?? 0) === 0).map((s) => s.name ?? '');
  const outnumber = privateOutnumber(layerData).length;
  agree('layers with no company in the priced pool', noPriced.length, facts.scope.n_layers_without_ring_a);
  agree('layers where the ledger holds more private companies than the pool holds listed ones', outnumber, facts.scope.n_layers_private_outnumber_listed);
  const missing = missingMultiples(marts.listings);
  return {
    layers: layerData.panels.length,
    privateOutnumber: outnumber,
    noPriced,
    missing: { n: missing.n, text: missing.groups.map((g) => `${g.label} ${g.n}`).join(', ') },
    unverified: marts.listings.filter((l) => l.placement_basis === 'anchor_product').length,
    listings: marts.listings.length,
    live: facts.scope.n_listed,
    delisted: facts.scope.by_ring.F.n,
  };
})();

/** How the controls of the valuation dashboard are described to a reader: which figures and which table they drive. */
export const valuationNote = (() => {
  const figures = Object.keys(valuationTitles).length;
  const ring = ringBNote(marts.listings);
  return `These controls drive the ${words(figures)} figures below and the table of every listing under Tables, which says how many rows are in view.${ring ? ` ${ring}` : ''}`;
})();

/** The five largest gaps of the page's own model, as the distance of each dot from that company's own fitted multiple. */
export const topResiduals = marts.regression.residuals
  .filter((r) => r.top5 === true && typeof r.residual === 'number')
  .map((r) => ({ name: String(r.name), ticker: String(r.ticker), ratio: Math.exp(r.residual as number) }))
  .sort((a, b) => Math.abs(Math.log(b.ratio)) - Math.abs(Math.log(a.ratio)))
  .map((r) => `${shortName(r.name)} ${r.ratio < 1 ? `${Math.round((1 - r.ratio) * 100)}% below` : `${Math.round((r.ratio - 1) * 100)}% above`} its own fitted multiple`);

/** The AI-tailwind trio of Q9: their names, the company whose multiple is the trio's median, and the count of the rest. */
export const trioFacts = (() => {
  const p = facts.regression.premium;
  const rows = AI_TRIO.map((t) => marts.listings.find((l) => l.ticker === t)).filter((l): l is NonNullable<typeof l> => !!l && l.ev_ntm != null)
    .sort((a, b) => (a.ev_ntm as number) - (b.ev_ntm as number));
  const mid = rows.length % 2 === 1 ? rows[(rows.length - 1) / 2] : null;
  const medianIsOne = !!mid && p.ai_trio_median.value != null && Math.abs((mid.ev_ntm as number) - p.ai_trio_median.value) / p.ai_trio_median.value < 1e-3;
  return {
    names: andList(AI_TRIO.map((t) => shortName(marts.listings.find((l) => l.ticker === t)?.name ?? t))),
    medianOf: medianIsOne ? shortName(mid!.name ?? mid!.ticker ?? '') : null,
    n: p.n_trio, nRest: p.n_rest, trio: p.ai_trio_median.value as number, rest: p.rest_median.value as number, ratio: p.ratio.value as number,
  };
})();

/** The Rule of 40 leaders and the registered group's split, for the sentence under the third figure. */
export const r40Facts = (() => {
  const leaders = facts.rule_of_40.leaders;
  const ly = leaders.map((l) => `${l.name} (${dec1(l.value)}, with expected growth of ${pct1(l.growth.value)} and a free-cash-flow margin of ${pct1(l.fcf_margin.value)})`);
  return { leaders, list: joinList(ly), first: leaders[0], split: q3Split() };
})();

/** The Q1 groups that the model merges into "other", named: layer placement is blunt where a group holds few companies. */
export const q1Groups = (() => {
  const q1 = scoreItem('Q1');
  // Q1's growth-only model and the page's own model can round to the same adjusted R squared: say so, as they are different models
  const g = q1 ? /Growth alone has an adjusted R² of (\d+(?:\.\d+)?)/.exec(q1.already_seen) : null;
  const sameFigure = !!g && g[1] === dec2(reg.adj_r2_no_layer.value);
  const growthOnly = g ? g[1] : '';
  const m = q1 ? /; other is ([A-Z0-9.,\s]+?)\./.exec(q1.already_seen) : null;
  if (!m) return { text: '', growthOnly, sameFigure };
  const tickers = m[1].split(',').map((s) => s.trim()).filter(Boolean);
  const listings = tickers.map((t) => marts.listings.find((l) => l.ticker === t)).filter((l): l is NonNullable<typeof l> => !!l);
  if (!listings.length) return { text: '', growthOnly, sameFigure };
  const layers = uniqSorted(listings.map((l) => layerShort(layerName(l.layer))));
  return { text: `${joinList(listings.map((l) => shortName(l.name ?? l.ticker ?? '')))} (${layers.join('; ')})`, growthOnly, sameFigure };
})();

// ---- dashboard 2: targets and ratings ----------------------------------------------------------------------------------------

const dumbData = figureRows<DumbbellRows>('target-dumbbell');
const ratingData = figureRows<RecommendationRows>('recommendation-bars');
export const targetTitles = targetDumbbellTitles(dumbData);
export const ratingTitles = recommendationBarsTitles(ratingData);
/** The sentence on East Money's blank buckets, or null when it printed all of them (the one wording of the page). */
export const ratingNote = recommendationBarsNote(ratingData);
/**
 * Filter attributes of a linked table, in the `<control>-any` form the valuation table uses (`valuation_data.listingTableRows`): a
 * space-separated list of every value of that control under which the row is in view. A row appears once, whatever the control,
 * where exact-match attributes would need a copy per option (`tableVariants` multiplied these tables about six times).
 */
function withAny<T extends { layer: string; market: string; coverage: string; source: string }>(rows: T[], marketFree: (r: T) => boolean = () => false) {
  return rows.map((r) => ({
    ...r,
    'layer-any': `all ${r.layer}`,
    // a row the figure draws whatever the market control says (East Money's panel) is in view under every market
    'market-any': marketFree(r) ? 'all us other_overseas china' : `all ${r.market}`,
    'coverage-any': r.coverage === 'three' ? 'all three' : 'all',
    'source-any': r.source,
  }));
}
/** The keys to pass as `attrKeys` of the targets table and of the ratings table. */
export const TARGET_FILTER_KEYS = ['layer-any', 'market-any', 'coverage-any'];
export const RATING_FILTER_KEYS = ['layer-any', 'market-any', 'coverage-any', 'source-any'];
export const targetTableRows = withAny(targetsTableRows(dumbData));
export const ratingTableRows = withAny(ratingsTableRows(ratingData, dumbData), (r) => r.source === 'eastmoney');

/** The note under the controls of the targets and ratings dashboard: which control drives what. */
export const targetsNote = 'Layer, market and coverage change both figures and both tables; the ratings source chooses which source the ratings title and table follow, and both panels are always drawn in full. Market applies to listings outside China; the A-share and East Money panels ignore it.';

export const targetFacts = (() => {
  const t = facts.targets;
  const st = targetStats(dumbData);
  const thinExtreme = t.thin_extremes[0];
  const bothEnds = !!(st.thinMost && st.coveredMost && st.thinLeast && st.coveredLeast && st.thinMost.value > st.coveredMost.value && st.thinLeast.value < st.coveredLeast.value);
  agree('listings with a mean target above the price', marts.targets.filter((r) => (r.upside ?? 0) > 0).length, t.n_above_price);
  return {
    n: t.n_with_target,
    above: t.n_above_price,
    median: t.median_upside.value as number,
    coveredN: t.covered.n,
    coveredMedian: t.covered.median_upside.value as number,
    q4N: t.q4_q5_list.n,
    nThin: t.n_thin,
    thinBelow: t.thin_below,
    most: st.coveredMost ?? t.most_upside,
    least: st.coveredLeast ?? t.least_upside,
    thinExtreme,
    bothEnds,
    nMainLine: st.nMainLine,
    suspects: st.suspects,
    suspectTimes: numWord(SUSPECT_RATIO),
    notQ4: st.nNotQ4,
  };
})();

/** `6 months` as the adjective `6-month`, for a count window; other windows are returned as served. */
const windowAdjective = (w: string): string => w.replace(/^(\d+) months?$/, '$1-month');

export const ratingFacts = (() => {
  const y = facts.recommendations.yahoo;
  const st = ratingStats(ratingData);
  const em = facts.recommendations.eastmoney;
  const shares = st.yahoo?.shares ?? [0, 0, 0];
  return {
    n: y.n_ratings, listings: ratingData.yahoo.length, buy: shares[0], hold: shares[1], sell: shares[2],
    lowest: st.lowestLayer, highest: st.highestLayer,
    em: { ratings: em.n_ratings, listings: em.n_covered, buy: em.counts.buy ?? 0, add: em.counts.add ?? 0, nullListings: em.n_wholly_null, window: windowAdjective(em.window) },
  };
})();

// ---- dashboard 3: private ledger and events --------------------------------------------------------------------------------

const intervalMap = ledgerIntervalTitleMap(marts.privateIntervals);
/** The events figures take the window end from the facts, as the module asks, so the title and the facts agree. */
export const EVENTS_OPTS = { asOf: facts.events.window_to } as const;
const eventsMap = eventsTitleMap(marts.events, EVENTS_OPTS);

export const privateFigures = {
  intervals: {
    title: intervalMap.initial, subtitle: ledgerIntervalSubtitle(marts.privateIntervals), notes: ledgerIntervalNotes(marts.privateIntervals),
    titles: intervalMap.titles, keys: intervalMap.keys,
  },
  events: {
    title: eventsMap.initial, subtitle: eventsSubtitle(marts.events, EVENTS_OPTS), notes: eventsNotes(marts.events, EVENTS_OPTS),
    titles: eventsMap.titles, keys: eventsMap.keys,
  },
  dumbbell: { title: eventDumbbellFinding(marts.events, EVENTS_OPTS), subtitle: eventDumbbellSubtitle(marts.events, EVENTS_OPTS), notes: eventDumbbellNotes(marts.events, EVENTS_OPTS) },
};

export const privateFacts = (() => {
  const p = facts.private;
  const s = ledgerIntervalStats(marts.privateIntervals);
  const chinaRounds = marts.privateIntervals.filter((r) => r.market === 'china' && r.headline_value != null).length;
  const chinaRows = marts.ledgerTimeline.filter((r) => r.market === 'china');
  const chinaArr = marts.privateIntervals.filter((r) => r.market === 'china' && r.arr_value != null).length;
  const chinaKinds = uniqSorted(chinaRows.map((r) => kindLabel(r.metric_kind).toLowerCase()));
  const chinaCurrencies = uniqSorted(chinaRows.map((r) => r.unit));
  const chinaGap = chinaRounds === 0 && chinaArr === 0
    ? `No Chinese private company has a priced round or ARR in the ledger, which holds ${p.n_china_rows} rows for them, ${chinaRows.length} of them charted (${joinAnd(chinaKinds)}, in ${joinAnd(chinaCurrencies)})`
    : `${chinaRounds} Chinese private companies have a priced round and ${chinaArr} state ARR; the ledger holds ${p.n_china_rows} rows for them, ${chinaRows.length} of them charted`;
  const comparableLayers = uniqSorted(marts.privateIntervals.filter((r) => !!r.comparable_tickers).map((r) => layerName(r.layer)));
  const top = p.latest_valuation_max;
  const lastChecked = marts.ledgerTimeline.map((r) => r.last_checked).filter((d): d is string => !!d).sort().pop() ?? null;
  // a valuation a publication reported is drawn hollow only when the curator marked it chartable: the companies where that happens
  const reportedDrawn = marts.privateIntervals.filter((r) => r.headline_basis === 'reported' && r.headline_value != null).map((r) => r.company).filter((c): c is string => !!c);
  // the companies sitting wholly above their interval, with what their comparison rests on (facts.private.q6)
  const q6 = p.q6.companies.filter((c) => c.scored && c.above === true).map((c) => {
    const bits: string[] = [];
    if (c.thin) bits.push(`its comparable set has ${words(c.n_comparables)} ${c.n_comparables === 1 ? 'company' : 'companies'}`);
    if (c.arr_old) bits.push(`its ARR is dated ${fmtMonth(c.arr_as_of)}, ${c.arr_months_old} months before the multiples it is set against`);
    return bits.length ? `${c.company}: ${bits.join(' and ')}` : '';
  }).filter(Boolean);
  agree('Q6 companies above their interval', s.above, p.n_q6_above);
  return {
    directory: p.n_directory, withHeadline: p.n_with_headline, withArr: p.n_with_arr,
    scored: s.scored, above: s.above, aboveNames: s.aboveNames, drawn: s.drawn,
    q6Scored: p.n_q6_scored, q6Above: p.n_q6_above, q6Basis: q6,
    chinaRounds, chinaArr, chinaLedgerRows: chinaRows.length, chinaCurrencies, chinaGap,
    comparableLayers, comparableLayersText: comparableLayers.join('; '), thin: s.thin, thinLayers: s.thinLayers, thinLayersText: s.thinLayers.join('; '),
    subline: ledgerIntervalSubline(marts.privateIntervals, {}),
    ledgerRows: p.n_ledger_rows, chartable: p.n_chartable, unconfirmed: p.n_reported_unconfirmed, stale: p.n_stale,
    reportedDrawn, reportedDrawnText: reportedDrawn.length ? joinList(reportedDrawn) : 'none',
    top: { company: top.company, text: fmtValue(top.value, top.unit, top.qualifier), basis: top.basis, date: fmtDay(top.period) },
    lastChecked,
  };
})();

export const eventFacts = (() => {
  const e = facts.events;
  const s = eventsStats(marts.events, EVENTS_OPTS);
  // the deal counts of the figure's title and of these sentences are the pipeline's own: a mismatch stops the build
  agree('deals with a chartable record in the twelve months', s.deals, e.acquisitions_12m);
  agree('of them dated by announcement', s.announced, e.acquisitions_announced_12m);
  agree('of them first recorded at completion', s.completionDated, e.acquisitions_first_recorded_at_completion_12m);
  agree('deals closed in the window', s.closed, e.acquisitions_closed_12m);
  agree('deals bought by a security vendor', s.platform, e.acquisitions_12m_by_buyer_type.platform_security);
  agree('deals priced from a filing in the window', s.filingPriced, e.n_sec_priced_deals_12m);
  agree('deals priced from a filing', s.filingPricedAll, e.n_sec_priced_deals);
  const l = e.latest;
  const price = fmtValue(l.chart_value, l.unit, l.chart_qualifier);
  const clause = latestEventClause({ company: l.company, event_kind: l.event_kind, counterparty: l.counterparty, status_after: l.status_after, price });
  return {
    deals: s.deals, announced: s.announced, completionDated: s.completionDated, counted: eventsCounted(s), platform: s.platform, share: s.share,
    closed: s.closed, closedEarlier: e.acquisitions_closed_12m_announced_earlier, notCharted: e.acquisitions_not_charted_12m,
    filingPriced: s.filingPriced, filingPricedAll: s.filingPricedAll,
    windowTo: e.window_to,
    latest: { kind: clause.kind, clause: clause.clause, pending: clause.pending, date: fmtDay(l.event_date) },
  };
})();

// ---- the China section ----------------------------------------------------------------------------------------------------

const chinaData = figureRows<ChinaRows>('china-panel');

export const chinaFacts = (() => {
  const c = facts.cn;
  const st = chinaPanelStats(chinaData);
  const em = facts.recommendations.eastmoney;
  agree('A-share listings flagged', chinaData.flagged.length, c.n_flagged);
  const flagged = chinaData.flagged.map((p) => ({ name: p.name.replace(/\s*\([^)]*\)/g, ''), ring: p.ring, exposure: p.exposure.toLowerCase() }));
  return {
    n: c.n_listed, flagged, nFlagged: c.n_flagged, falling: c.n_revenue_falling, topQuartile: c.n_top_quartile,
    top: st.top, bottom: st.bottom, topFalling: st.topFalling, expected: st.expected, nPartial: st.nPartial, partialFlagged: st.flaggedPartial.length,
    flaggedList: joinList(flagged.map((f) => `${f.name} (ring ${f.ring}, ${f.exposure})`)),
    period: periodWords(chinaData.period),
    medianPs: c.median_ps_ttm.value as number, medianPercentile: c.median_ps_percentile.value as number, medianYoy: c.median_revenue_yoy.value as number,
    cnMedian: facts.ev_sales.cn_median.value as number, usMedian: facts.ev_sales.us_population_median.value as number, usN: facts.ev_sales.us_population_median.n, ratio: facts.ev_sales.cn_us_ratio.value as number,
    ownDummy: { ratio: Math.exp(reg.china_dummy.value as number), lo: Math.exp(reg.china_dummy.lo as number), hi: Math.exp(reg.china_dummy.hi as number) },
    nAShareFit: reg.n_cn,
    emRatings: em.n_ratings, emBuy: em.counts.buy ?? 0, emAdd: em.counts.add ?? 0, emWindow: windowAdjective(em.window),
    nOpinionsMin: facts.cn.n_opinions_min, nOpinionsMax: facts.cn.n_opinions_max,
  };
})();

// ---- the size of the dots and the exchange rates -----------------------------------------------------------------------------

/** What the growth figure's dot size rests on: how many dots are sized, in dollars or converted at which day's FRED rate, and who has no rate. */
export const sizeFacts = (() => {
  const converted = evData.nConverted;
  return {
    sized: evData.nUsd, converted, quoted: evData.nUsd - converted, fxDay: fmtDay(evData.fxAsOf ?? facts.coverage.fx_as_of),
    noRate: evData.noRate.map((n) => `${shortName(n.name)} (${n.ccy})`),
  };
})();

/**
 * Ring A listings that another page of the repository also tracks: that page's valuation job commits Yahoo responses for them, which the
 * cyber capture itself never does. Named on the page, because a statement that "no Yahoo response is committed" would otherwise be false for them.
 * tests/test_cyber_page_numbers.py recomputes the list from the valuation config and fails if it changes.
 */
export const OVERLAP_TICKERS = ['CSCO'];
export const overlapNames = joinList(OVERLAP_TICKERS.map((t) => shortName(marts.listings.find((l) => l.ticker === t)?.name ?? t)));

// ---- what the deployed site serves ---------------------------------------------------------------------------------------------

/**
 * The marts the deployed site serves, by the whitelist of site/scripts/sync-data.mjs (owner decision D1). The page prints this list, and
 * tests/test_cyber_page_numbers.py fails if it differs from the script's. The three Yahoo-derived marts are not served.
 */
export const SERVED_MARTS = ['ledger_timeline', 'reported_unconfirmed', 'events', 'excluded', 'metric_dictionary', 'etf_crosscheck'] as const;
const SERVED_WORDS: Record<(typeof SERVED_MARTS)[number], string> = {
  ledger_timeline: 'the charted private ledger', reported_unconfirmed: 'the figures never charted', events: 'the deal events', excluded: 'the list of companies left out of the pool',
  metric_dictionary: 'the metric dictionary', etf_crosscheck: 'the ETF cross-check',
};
export const servedList = joinList(SERVED_MARTS.map((m) => SERVED_WORDS[m]));

// ---- tables ---------------------------------------------------------------------------------------------------------------------

const PLACEMENT_LABEL: Record<string, string> = { anchor_product: 'Anchor product, unverified', segment_disclosed: 'Segment table', company_description: 'Company description', etf_classification: 'ETF classification' };
const placementOf = new Map(marts.listings.map((l) => [l.ticker, l.placement_basis]));
/** Every listing of the pool with the reason when it has no multiple, and the basis of its layer placement. */
export const listingRows = listingTableRows(marts.listings, marts.layerSummary).map((r) => ({ ...r, layer_basis: PLACEMENT_LABEL[placementOf.get(String(r.ticker)) ?? ''] ?? '—' }));
export const LISTING_COLUMNS = [...LISTING_TABLE_COLUMNS, { key: 'layer_basis', label: 'Layer placement' }];

export const SCORE_COLUMNS = [
  { key: 'id', label: '#' },
  { key: 'claim', label: 'Claim' },
  { key: 'status', label: 'Status' },
  { key: 'first', label: 'First look (already seen, not a result)' },
  { key: 'graded', label: 'Graded on' },
];
export const SCORE_APPENDIX_COLUMNS = [
  { key: 'id', label: '#' },
  { key: 'seen', label: 'Already seen at registration, as registered' },
];

export const TARGET_COLUMNS = [
  { key: 'name', label: 'Company' },
  { key: 'ticker', label: 'Ticker' },
  { key: 'layer_label', label: 'Layer' },
  { key: 'market_label', label: 'Market' },
  { key: 'exposure', label: 'Exposure' },
  { key: 'coverage_label', label: 'Coverage' },
  { key: 'analysts', label: 'Target analysts', format: 'num' as const },
  { key: 'upside', label: 'Upside', format: 'pct' as const, align: 'right' as const },
  { key: 'upside_low', label: 'Lowest target', format: 'pct' as const, align: 'right' as const },
  { key: 'upside_high', label: 'Highest target', format: 'pct' as const, align: 'right' as const },
  { key: 'currency', label: 'Quote currency' },
  { key: 'registered', label: 'In the registered Q4 list' },
  { key: 'note', label: 'Note' },
];
export const RATING_COLUMNS = [
  { key: 'name', label: 'Company' },
  { key: 'ticker', label: 'Ticker' },
  { key: 'layer_label', label: 'Layer' },
  { key: 'market_label', label: 'Market' },
  { key: 'source_label', label: 'Source' },
  { key: 'ratings', label: 'Ratings printed' },
  { key: 'analysts', label: 'Ratings', format: 'num' as const },
  { key: 'coverage_label', label: 'Coverage' },
  { key: 'note', label: 'Window' },
];

const KIND_LABEL: Record<string, string> = { left_out: 'Left out of the pool', ring_f: 'Delisted or acquired (ring F)' };
export const excludedRows = (excludedJson as unknown as ExcludedRow[]).map((r) => ({ ticker: r.ticker ?? '', name: r.name ?? '', kind: KIND_LABEL[r.kind ?? ''] ?? r.kind ?? '', reason: r.reason ?? '' }));
export const EXCLUDED_COLUMNS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'name', label: 'Company' },
  { key: 'kind', label: 'Why not in the pool' },
  { key: 'reason', label: 'Reason' },
];

const ETF_STATUS: Record<string, string> = { ring_a: 'Ring A, priced', ring_b: 'Ring B, classified', excluded: 'Left out' };
export const etfRows = (etfJson as unknown as EtfRow[]).map((r) => ({
  ticker: r.ticker ?? '', name: r.name ?? '', status: ETF_STATUS[r.status ?? ''] ?? r.status ?? '', layer: r.layer ? layerName(r.layer) : '—', purity: r.purity ? (EXPOSURE_LABEL[r.purity] ?? r.purity) : '—', reason: r.excluded_reason ?? '',
}));
export const ETF_COLUMNS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'name', label: 'Holding' },
  { key: 'status', label: 'In the pool as' },
  { key: 'layer', label: 'Layer' },
  { key: 'purity', label: 'Exposure' },
  { key: 'reason', label: 'Why left out' },
];
export const etfCounts = facts.universe.etfs[0]?.counts ?? {};

/** A reported row as the mart serves it: the closed reason code (`reason`) and its reader sentence (`reason_label`). */
type ReportedMartRow = ReportedRow & { reason_label?: string | null; reason_group?: string | null };
const WHY: Record<string, string> = {
  never_charted_kind: 'A report of talks, a third-party estimate or a secondary mark: never charted',
  unverified_tag: 'Not read on the primary page',
  reported_basis: 'A publication reported it; the company has not stated it',
};
const reportedSeen = new Set<string>();
/** One row per (company, date, kind of figure, value, source): the mart de-duplicates already, and the table keeps the same key. */
export const reportedRows = (reportedJson as unknown as ReportedMartRow[])
  .filter((r) => {
    const key = [r.company, r.date ?? r.period, r.metric_kind ?? r.metric, r.value, r.source_url].join('|');
    if (reportedSeen.has(key)) return false;
    reportedSeen.add(key);
    return true;
  })
  .map((r) => ({
    company: esc(r.company ?? ''),
    layer: layerName(r.layer),
    figure: r.metric_kind ? kindLabel(r.metric_kind) : (r.metric ?? '').replace(/_/g, ' '),
    reported: fmtValue(r.value, r.unit ?? 'USD', r.qualifier ?? '='),
    date: r.date ?? r.period ?? '—',
    why: r.reason_label ?? WHY[r.reason ?? ''] ?? (r.reason ?? '').replace(/_/g, ' '),
    caveat: esc(r.caveat ?? ''),
    source: r.source_url ? `<a href="${esc(r.source_url)}" rel="noopener">${esc(r.source_name ?? 'source')}</a>` : esc(r.source_name ?? '—'),
  }));
agree('rows of the table of figures never charted', reportedRows.length, facts.private.n_reported_unconfirmed);
export const REPORTED_COLUMNS = [
  { key: 'company', label: 'Company' },
  { key: 'layer', label: 'Layer' },
  { key: 'figure', label: 'Figure' },
  { key: 'reported', label: 'Value as stated' },
  { key: 'date', label: 'Date' },
  { key: 'why', label: 'Why it is not charted' },
  { key: 'caveat', label: 'Caveat' },
  { key: 'source', label: 'Source' },
];
/** The rows of the reported table by group, for the sentence above it (facts: the rows of the mart in each group). */
export const reportedFacts = (() => {
  const g = facts.private.n_reported_by_group;
  return {
    n: reportedRows.length, duplicates: facts.private.n_reported_duplicates,
    confirmed: g.company_confirmed_not_charted ?? 0, publication: g.reported_by_a_publication ?? 0, never: g.never_charted_kind ?? 0,
  };
})();

/**
 * The long private ledger, the interval table, the deal-event table and the announced-against-filed table: the same builders as
 * `external.ts` hands the prebuild (TABLE_FILES, PAGE_TABLES), so a row count printed here is the count of the file the table loads.
 */
const ledgerBuilt = ledgerTableRows(marts.ledgerTimeline);
const intervalBuilt = intervalTableRows(marts.privateIntervals);
const eventsBuilt = eventsTableRows(marts.events, EVENTS_OPTS);
export const ledgerSelects = ledgerTableSelects(ledgerBuilt);
export const intervalSelects = intervalTableSelects(intervalBuilt);
export const eventsSelects = eventsTableSelects(eventsBuilt);
export const ledgerRowCount = ledgerBuilt.length;
export const intervalRowCount = intervalBuilt.length;
export const eventRowCount = eventsBuilt.length;
export const dumbbellRowCount = dumbbellTableRows(marts.events, EVENTS_OPTS).length;
agree('rows of the private ledger table', ledgerRowCount, facts.private.n_chartable);

export const chinaRows = chinaTableRows(marts.chinaPanel);
export const CHINA_COLUMNS = [
  { key: 'name', label: 'Company' },
  { key: 'name_cn', label: 'Chinese name' },
  { key: 'ticker', label: 'Ticker' },
  { key: 'ring', label: 'Ring' },
  { key: 'exposure', label: 'Exposure' },
  { key: 'ps_ttm', label: 'PS_TTM (x)', format: 'fixed1' as const },
  { key: 'ps_percentile', label: 'Percentile of own history', format: 'pct' as const },
  { key: 'revenue_yoy', label: 'Revenue growth, year on year', format: 'pct' as const },
  { key: 'revenue_period', label: 'Period' },
  { key: 'top_quartile_while_falling', label: 'Top quartile while revenue falls' },
  { key: 'history_days', label: 'Days of history', format: 'num' as const },
  { key: 'short_history', label: 'Short history (percentile over a shorter window)' },
  { key: 'history_start', label: 'History from' },
];

export const dictionaryRows = dictionaryJson as unknown as DictionaryRow[];
export const dictionaryMetrics = uniqSorted(dictionaryRows.map((r) => r.metric));
/** One row of the metric dictionary by metric name, for the Method section, so a definition is read and never retyped. */
export const dictRow = (metric: string): DictionaryRow | undefined => dictionaryRows.find((r) => r.metric === metric);

// ---- data quality, the diagram and the page's changelog --------------------------------------------------------------------------

export const quality = (() => {
  const by = (s: Check['status']) => facts.checks.filter((c) => c.status === s);
  const c = facts.counts.checks;
  return {
    pass: by('pass').length, warn: by('warn'), fail: by('fail'), total: facts.checks.length,
    referenceFiles: facts.checks.filter((x) => / file$/.test(x.name)).length, martCount: Object.keys(facts.marts).length,
    waiting: c.n_warn_waiting, data: c.n_warn_data, waitingNames: c.warn_waiting, dataNames: c.warn_data,
  };
})();

/** Title of the pipeline diagram: what feeds the page and what stands between the sources and the marts. */
export const dagTitle = `${facts.sources.length} sources, ${quality.referenceFiles} curated files and ${quality.martCount} marts, with one validation gate before anything is written`;

export const pageLog = changelog.filter((e) => e.project === 'cybersecurity-landscape');

/** The small print under a KPI: its period and note, with the dates in the page's format. */
export const kpiSub = (k: KpiFact): string => prettyDates(`${k.period}; ${k.note}`);

/** A KPI value for its tile, by unit. */
export function kpiValue(k: KpiFact): string {
  if (k.unit.startsWith('times')) return x1(k.value);
  if (k.unit.startsWith('fraction')) return signed(k.value);
  if (k.unit === 'USD' || k.unit.length === 3) return fmtValue(k.value, k.unit, k.qualifier);
  return String(k.value);
}
