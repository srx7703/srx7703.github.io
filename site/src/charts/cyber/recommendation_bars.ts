import { VL_SCHEMA, series, tok } from '../theme';
import type { Listing, RecommendationRow } from './types';
import {
  type ControlName, type RatingBucket, type RatingGroup, type RatingListings, type RatingSource, type Side, type TargetsState,
  BUCKETS, LAYER_ORDER, MARKET_LABEL, SIDES, SIDE_LABEL, andList, bySide, buildRatingListings, count, fmtShare, isWholeBusiness,
  layerLabel, numWord, ratingGroups, roundShares, targetsParams, targetsState, thinBelowOf, titleMapFor, viewPhrase,
} from './targets_data';

/** Figure 5: the controls that drive it (the same names are unbound top-level params of the spec). */
export const RATINGS_KEYS: ControlName[] = ['layer', 'market', 'coverage', 'source'];

const ZWSP = '\u200b';
/** Characters per line for a sentence printed inside a panel: a phone's half-width panel holds about 26 characters of 10px type. */
const WRAP = 26;
/** Estimated width of one character of 10px Plex Sans, for the label-fit test. */
const CHAR_PX = 5.4;
/** Height of one bar's row: the side totals above the bar, the bar (14px), and the shares below it. */
const ROW_STEP = 42;
/** Lines of a sentence printed in one row (a 42px row holds three lines of 10px type at a 12px line height). */
const SENTENCE_LINES = 3;

/** Rows: one per listing and source, plus the sources' windows and the thin threshold. */
export type RecommendationRows = RatingListings;

/** Data preparation: per-listing counts of both vocabularies, read from the recommendations mart (never merged). */
export function recommendationBarsRows(recs: RecommendationRow[], listings: Listing[] = []): RecommendationRows {
  const thin = thinBelowOf(listings.filter((l) => l.ring === 'A' && l.market_group !== 'china'));
  return buildRatingListings(recs, thin, listings);
}

/** The first complete English window, e.g. `6 months` -> `six-month`, `current distribution` stays. */
const windowWord = (w: string | null): string => {
  const m = /^(\d+) months?$/.exec(w ?? '');
  return m ? `${numWord(Number(m[1]))}-month` : (w ?? '');
};

/** Panel header (two or three lines joined by `|`): the source, its window and, for East Money's single bar, its number of ratings. */
const panelLabel = (rows: RecommendationRows, source: RatingSource, ratings?: number): string =>
  (source === 'yahoo'
    ? `Yahoo Finance|${rows.windows.yahoo ?? 'current distribution'}`
    : `East Money|${rows.windows.eastmoney ?? 'six months'}${ratings != null ? `|n=${ratings}` : ''}`);

/**
 * The one sentence on East Money's blank buckets (the figure's note and the page's paragraph use it as written), from the buckets
 * no listing printed; null when it printed all five. Blank is read as not printed, never as zero.
 */
export function recommendationBarsNote(rows: RecommendationRows): string | null {
  const miss = rows.unprinted.eastmoney;
  const n = rows.eastmoney.length;
  if (!miss.length || !n) return null;
  const none = rows.empty.eastmoney ? ` (${count(rows.empty.eastmoney, 'more listing')} ${rows.empty.eastmoney === 1 ? 'has' : 'have'} no East Money rating at all)` : '';
  return `East Money's feed leaves ${andList(miss.map((b) => b.label))} blank for all ${n} A-share listings it covers${none}. We read blank as not printed, which may be a property of the feed and not of the brokers, so the bar draws only ${andList(BUCKETS.eastmoney.filter((b) => !miss.includes(b)).map((b) => b.label))}.`;
}

const wrapLines = (text: string, width = WRAP): string[] => {
  const out: string[] = [];
  let line = '';
  for (const w of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + w.length > width) { out.push(line); line = w; } else line = line ? `${line} ${w}` : w;
  }
  if (line) out.push(line);
  return out.length ? out : [''];
};

// ---- empty views -----------------------------------------------------------------------------------------------------

const marketPhrase = (st: TargetsState): string =>
  (st.market === 'us' ? 'US ' : st.market === 'other_overseas' ? 'other overseas ' : '');

/** The sentence a source's panel shows when the view leaves it nothing, or null when it has rows. */
export function ratingReason(rows: RecommendationRows, source: RatingSource, st: TargetsState): string | null {
  if (ratingGroups(rows, source, st).length) return null;
  const all = rows[source];
  const inLayer = st.layer === 'all' ? '' : ` in ${layerLabel(st.layer)}`;
  const layerRows = all.filter((l) => st.layer === 'all' || l.layer === st.layer);
  if (source === 'eastmoney') {
    if (!all.length) return 'East Money printed no rating for any listing on this build.';
    if (!layerRows.length) return `No A-share listing${inLayer} has an East Money rating in the window.`;
    return `Every A-share listing${inLayer} with an East Money rating has fewer than ${numWord(rows.thinBelow)} rating analysts (${count(layerRows.length, 'listing')}); set coverage to all listings to see them.`;
  }
  if (!all.length) return 'Yahoo returned no ratings on this build.';
  if (!layerRows.length) {
    return st.layer !== 'all' && !(rows.listedByLayer[st.layer] ?? 0)
      ? `${layerLabel(st.layer)} has no company in the listed pool (the priced pool), so there are no ratings to show here.`
      : `No listing${inLayer} has a Yahoo rating.`;
  }
  const marketRows = layerRows.filter((l) => st.market === 'all' || l.market_group === st.market);
  if (!marketRows.length) return `No ${marketPhrase(st)}listing${inLayer} has a Yahoo rating.`;
  return `Every ${marketPhrase(st)}listing${inLayer} with a Yahoo rating has fewer than ${numWord(rows.thinBelow)} rating analysts (${count(marketRows.length, 'listing')}); set coverage to all listings to see them.`;
}

// ---- spec rows -------------------------------------------------------------------------------------------------------

const GROUP_NAME: Record<string, string> = { pooled: 'All outside China', us: 'US', other_overseas: 'Other overseas' };

type Seg = {
  k: 'seg';
  panel: RatingSource; panel_label: string; layer: string; market: string; coverage: string;
  group: string; group_label: string; go: number;
  bucket: string; bucket_label: string; side: Side; side_label: string;
  n: number; share: number; x0: number; x1: number; mid: number; lab: string; w_full: number; w_pct: number;
  listings: number; ratings: number;
  /** The sentence a screen reader gets for the segment. */
  aria: string;
};

const SIDE_DOT = ' \u00b7 ';
/** East Money's hold side is called neutral; Yahoo's is hold. */
const holdWord = (source: RatingSource): string => (source === 'eastmoney' ? 'neutral' : 'hold');

/**
 * The totals of a bar's three directions, written above the bar in the longest form that fits the panel: `buy side 61% · hold 35% ·
 * sell side 4%`, then `buy 61% · hold 35% · sell 4%`, then the sell side alone (or the largest side when there is none). The shares add to
 * exactly 100 (largest remainder), and a direction nobody rated is left out. One row per bar, same band as its segments.
 */
function sidesFor(rows: RecommendationRows, g: RatingGroup, st: TargetsState, market: string): Record<string, unknown> | null {
  const sides = bySide(g);
  const present = SIDES.filter((sd) => sides[sd] > 0);
  if (!present.length) return null;
  const pct = roundShares(present.map((sd) => sides[sd]));
  const word = (sd: Side, long: boolean) => (sd === 'hold' ? holdWord(g.source) : long ? `${sd} side` : sd);
  const long = present.map((sd, i) => `${word(sd, true)} ${pct[i]}%`).join(SIDE_DOT);
  const med = present.map((sd, i) => `${word(sd, false)} ${pct[i]}%`).join(SIDE_DOT);
  const lead: Side = present.includes('sell') ? 'sell' : present.reduce((a, b) => (sides[a] >= sides[b] ? a : b));
  const short = `${word(lead, true)} ${pct[present.indexOf(lead)]}%`;
  const plain = g.source === 'eastmoney' ? `${MARKET_LABEL.china} listings` : (GROUP_NAME[g.group === 'pooled' && st.market !== 'all' ? st.market : g.group] ?? g.label);
  return {
    k: 'sides', panel: g.source, panel_order: g.source === 'yahoo' ? 0 : 1,
    panel_label: panelLabel(rows, g.source, g.source === 'eastmoney' ? g.ratings : undefined), layer: st.layer, market, coverage: st.coverage,
    group: plain, group_label: g.source === 'eastmoney' ? ZWSP : `${g.label}|n=${g.ratings}`, go: g.order,
    side_long: long, side_med: med, side_short: short,
    w_long: Math.ceil(long.length * CHAR_PX) + 4, w_med: Math.ceil(med.length * CHAR_PX) + 4,
  };
}

function segsFor(rows: RecommendationRows, g: RatingGroup, st: TargetsState, market: string): Seg[] {
  const buckets = BUCKETS[g.source].filter((b) => g.counts[b.key] != null);
  const total = buckets.reduce((s, b) => s + (g.counts[b.key] ?? 0), 0);
  if (!total) return [];
  let acc = 0;
  const plain = g.source === 'eastmoney' ? `${MARKET_LABEL.china} listings` : (GROUP_NAME[g.group === 'pooled' && st.market !== 'all' ? st.market : g.group] ?? g.label);
  return buckets.filter((b) => (g.counts[b.key] ?? 0) > 0).map((b: RatingBucket) => {
    const n = g.counts[b.key] as number;
    const share = n / total;
    const x0 = acc;
    acc += share;
    const pct = fmtShare(share);
    const lab = `${b.label} ${pct}`;
    return {
      k: 'seg', panel: g.source, panel_order: g.source === 'yahoo' ? 0 : 1,
      panel_label: panelLabel(rows, g.source, g.source === 'eastmoney' ? g.ratings : undefined), layer: st.layer, market, coverage: st.coverage,
      group: plain, group_label: g.source === 'eastmoney' ? ZWSP : `${g.label}|n=${g.ratings}`, go: g.order,
      bucket: b.label, bucket_label: b.label, side: b.side, side_label: SIDE_LABEL[b.side],
      n, share, x0, x1: acc, mid: (x0 + acc) / 2, lab, w_full: Math.ceil(lab.length * CHAR_PX) + 6, w_pct: Math.ceil(pct.length * CHAR_PX) + 6,
      listings: g.listings, ratings: g.ratings,
      aria: `${plain}, ${g.source === 'yahoo' ? 'Yahoo Finance' : 'East Money'}: ${b.label} ${pct} (${n} of ${g.ratings.toLocaleString('en-US')} ratings, ${SIDE_LABEL[b.side].toLowerCase()})`,
    };
  });
}

/**
 * Everything the spec draws, for every combination of the four controls: stacked segments per group, and the sentences
 * (a panel's reason when the view leaves it empty, East Money's note under its bar). Shares are computed here from the
 * printed counts, so the chart and the finding read the same numbers.
 */
export function ratingSpecRows(rows: RecommendationRows) {
  const out: Record<string, unknown>[] = [];
  const layers = ['all', ...LAYER_ORDER];
  const note = recommendationBarsNote(rows);
  // Sentences are printed three lines to a row, because a row of the panel is tall enough for three lines of 10px type.
  const sentences = (panel: RatingSource, st: TargetsState, market: string, text: string, first: number, kind: 'state' | 'note' | 'key', ratings?: number) => {
    const lines = wrapLines(text);
    for (let i = 0; i < lines.length; i += SENTENCE_LINES) {
      const n = i / SENTENCE_LINES + 1;
      out.push({
        k: kind, panel, panel_order: panel === 'yahoo' ? 0 : 1, panel_label: panelLabel(rows, panel, ratings), layer: st.layer, market, coverage: st.coverage,
        group: ZWSP, group_label: ZWSP.repeat(first + n), go: first + n, line: lines.slice(i, i + SENTENCE_LINES), aria: lines.slice(i, i + SENTENCE_LINES).join(' '),
      });
    }
  };
  for (const layer of layers) {
    for (const coverage of ['all', 'three']) {
      for (const market of ['all', 'us', 'other_overseas']) {
        const st = targetsState({ layer, market, coverage });
        const groups = ratingGroups(rows, 'yahoo', st);
        const reason = ratingReason(rows, 'yahoo', st);
        if (reason) sentences('yahoo', st, market, reason, 0, 'state');
        groups.forEach((g) => { out.push(...segsFor(rows, g, st, market)); const sd = sidesFor(rows, g, st, market); if (sd) out.push(sd); });
        if (groups.length) sentences('yahoo', st, market, `Left to right: ${BUCKETS.yahoo.map((b) => b.label).join(', ')}.`, groups.length, 'key');
      }
      // East Money ignores the market control: its rows match every market.
      const st = targetsState({ layer, market: 'all', coverage });
      const groups = ratingGroups(rows, 'eastmoney', st);
      const reason = ratingReason(rows, 'eastmoney', st);
      if (reason) sentences('eastmoney', st, '*', reason, 0, 'state');
      groups.forEach((g) => { out.push(...segsFor(rows, g, st, '*')); const sd = sidesFor(rows, g, st, '*'); if (sd) out.push(sd); });
      if (groups.length && note) {
        const printed = BUCKETS.eastmoney.filter((b) => !rows.unprinted.eastmoney.includes(b)).map((b) => b.label);
        sentences('eastmoney', st, '*', `Left to right: ${printed.join(', ')}. Blank in the feed: ${rows.unprinted.eastmoney.map((b) => b.label).join(', ')}.`, groups.length, 'note', groups[0].ratings);
      }
    }
  }
  return out;
}

/** What a row must satisfy at the current params; the East Money rows (`market == '*'`) match every market. */
export const RATINGS_FILTER = "datum.layer == layer && (datum.market == '*' || datum.market == market) && datum.coverage == coverage";

const SIDE_RANGE = [series.a, series.neutral, series.b];

/**
 * Figure 5. Two panels side by side, each with its own scale from 0 to 100%: Yahoo's five buckets (strong buy to strong sell,
 * the current distribution) pooled over listings outside China and by market group, and East Money's brokerage ratings of the
 * last six months (buy, add, neutral, reduce, sell) pooled over the A-share listings. The vocabularies are never merged,
 * added or averaged. Colour has three slots only, the direction of a rating (buy side, hold or neutral, sell side), so the two
 * ends of a vocabulary share a slot and a thin background gap tells their segments apart; the shares are written under the
 * segments wide enough to hold them, the bucket's name too when it fits. Above each bar its three direction totals are written
 * (in the longest form that fits the panel), and a line under the Yahoo bars gives the order of the buckets. Each bar's label
 * carries its number of ratings.
 *
 * Both panels are always drawn at full strength: the `source` control changes the title and the table, not the panels (a faded panel
 * cannot keep its text and its bars readable on both grounds). `layer`, `market` and `coverage` filter both (East Money ignores
 * `market`). A view that leaves a panel nothing prints one sentence there, never an empty panel.
 *
 * Dashboard group `targets`, four unbound top-level params. All shares are precomputed by `ratingSpecRows`; Vega only filters.
 */
export function recommendationBarsSpec(rows: RecommendationRows, opts: { state?: Partial<TargetsState> } = {}) {
  const values = ratingSpecRows(rows);
  const y = {
    field: 'group_label', type: 'nominal', title: null,
    sort: { field: 'go', op: 'min', order: 'ascending' },
    axis: { labelLimit: 36, labelFontSize: 10, ticks: false, domain: false, labelPadding: 3, labelExpr: "split(datum.label, '|')" },
  };
  // no gridlines: the sentences under the bars would run across them on a phone, and the shares are written on the bars
  const x = (field: string) => ({
    field, type: 'quantitative', title: null,
    scale: { domain: [0, 1], nice: false },
    axis: { format: '.0%', values: [0, 0.5, 1], labelFontSize: 10, grid: false, offset: 6 },
  });
  const tooltip = [
    { field: 'panel_label', type: 'nominal', title: 'Source' },
    { field: 'group', type: 'nominal', title: 'Group' },
    { field: 'bucket_label', type: 'nominal', title: 'Rating' },
    { field: 'n', type: 'quantitative', title: 'Ratings' },
    { field: 'share', type: 'quantitative', title: 'Share', format: '.1%' },
    { field: 'ratings', type: 'quantitative', title: 'Ratings in the bar' },
    { field: 'listings', type: 'quantitative', title: 'Listings' },
  ];
  return {
    $schema: VL_SCHEMA,
    params: targetsParams(RATINGS_KEYS, opts.state),
    data: { values },
    transform: [{ filter: RATINGS_FILTER }],
    facet: {
      column: {
        field: 'panel_label', type: 'nominal', title: null, sort: { field: 'panel_order', op: 'min', order: 'ascending' },
        header: {
          labelOrient: 'top', labelAnchor: 'start', labelAngle: 0, labelAlign: 'left', labelFontSize: 12, labelPadding: 8, labelLimit: 0,
          labelColor: tok('ink'), labelExpr: "split(datum.label, '|')", labelLineHeight: 14,
        },
      },
    },
    _facetColumns: 2,
    // re-measure the panels when a control changes the number of rows (the default only measures once)
    autosize: { type: 'pad', resize: true },
    spacing: 10,
    padding: { left: 0, right: 0, top: 0, bottom: 2 },
    spec: {
      width: 200,
      height: { step: ROW_STEP },
      layer: [
        {
          transform: [{ filter: "datum.k == 'seg'" }],
          mark: { type: 'bar', height: 14, stroke: tok('bg'), strokeWidth: 1.5, opacity: 1 },
          encoding: {
            y, x: x('x0'), x2: { field: 'x1' },
            color: {
              field: 'side', type: 'nominal',
              scale: { domain: SIDES, range: SIDE_RANGE },
              legend: {
                title: null, orient: 'top', direction: 'horizontal', symbolType: 'square', symbolSize: 90, labelFontSize: 11, columnPadding: 12,
                labelExpr: `{${SIDES.map((s) => `'${s}': '${SIDE_LABEL[s]}'`).join(', ')}}[datum.label]`,
              },
            },
            tooltip, description: { field: 'aria', type: 'nominal' },
          },
        },
        {
          // shares under the segments that can hold them: name and share when both fit, the share alone otherwise
          transform: [
            { filter: "datum.k == 'seg'" },
            { calculate: '(datum.x1 - datum.x0) * child_width', as: 'px' },
            { calculate: "datum.px >= datum.w_full ? datum.lab : (datum.px >= datum.w_pct ? format(datum.share, '.0%') : '')", as: 'shown' },
          ],
          mark: { type: 'text', baseline: 'top', dy: 10, fontSize: 10, color: tok('ink-2'), aria: false },
          encoding: { y, x: x('mid'), text: { field: 'shown', type: 'nominal' } },
        },
        {
          // the totals of the three directions above each bar, in the longest form that fits the panel
          transform: [
            { filter: "datum.k == 'sides'" },
            { calculate: 'datum.w_long <= child_width ? datum.side_long : (datum.w_med <= child_width ? datum.side_med : datum.side_short)', as: 'side_text' },
          ],
          mark: { type: 'text', align: 'left', baseline: 'bottom', dy: -9, fontSize: 10, color: tok('ink-2'), aria: false },
          encoding: { y, x: { value: 0 }, text: { field: 'side_text', type: 'nominal' } },
        },
        {
          // a panel's reason when the view leaves it empty, the bucket order under the Yahoo bars, and East Money's note under its bar
          transform: [{ filter: "datum.k == 'state' || datum.k == 'note' || datum.k == 'key'" }],
          mark: { type: 'text', align: 'left', baseline: 'top', dy: -17, lineHeight: 12, fontSize: 10, fontStyle: 'italic', color: tok('ink-2') },
          encoding: { y, x: { value: 2 }, text: { field: 'line', type: 'nominal' }, description: { field: 'aria', type: 'nominal' } },
        },
      ],
    },
    resolve: { scale: { x: 'independent', y: 'independent' } },
  };
}

// ---- finding and subtitle ----------------------------------------------------------------------------------------------

/**
 * Finding for the view `state` (default view when omitted), from the printed counts alone. `source` picks the vocabulary the
 * sentence is about; the other vocabulary is never mentioned in it. A view with nothing in it says why. The three Yahoo shares are
 * rounded together so they add to 100%.
 */
export function recommendationBarsFinding(rows: RecommendationRows, state: Partial<TargetsState> = {}): string {
  const st = targetsState(state);
  const source: RatingSource = st.source === 'eastmoney' ? 'eastmoney' : 'yahoo';
  const reason = ratingReason(rows, source, st);
  if (reason) return reason;
  const g = ratingGroups(rows, source, st)[0];
  const sides = bySide(g);
  const printed = BUCKETS[source].filter((b) => g.counts[b.key] != null);
  const where = viewPhrase(st, g.listings, source === 'eastmoney' ? { where: 'A-share listing', analysts: 'rating analysts' } : { analysts: 'rating analysts' });
  if (source === 'yahoo') {
    const [buy, hold, sell] = roundShares([sides.buy, sides.hold, sides.sell]);
    const tail = [
      sides.hold ? `${hold}% are hold` : '',
      sides.sell ? `${sell}% are sell or strong sell` : 'none is sell or strong sell',
    ].filter(Boolean);
    return `${buy}% of ${g.ratings.toLocaleString('en-US')} Yahoo ratings on ${where} are buy or strong buy, ${andList(tail)}`;
  }
  const everyone = sides.buy === g.ratings;
  const all = g.ratings === 1 ? 'the one' : g.ratings === 2 ? 'both' : `all ${g.ratings}`;
  const lead = `${everyone ? all : `${sides.buy} of ${g.ratings}`} East Money ${windowWord(rows.windows.eastmoney) || 'six-month'} ${g.ratings === 1 ? 'rating' : 'ratings'} on ${where} ${everyone && g.ratings === 1 ? 'is' : 'are'} buy or add`;
  const missing = rows.unprinted.eastmoney.filter((b) => !printed.includes(b));
  return missing.length
    ? `${lead}; ${andList(missing.map((b) => b.label))} ${missing.length === 1 ? 'is' : 'are'} blank in East Money's feed`
    : `${lead}; ${fmtShare((sides.hold + sides.sell) / g.ratings)} are neutral, reduce or sell`;
}

/** `titles` prop for `<VegaChart group="targets" titleKeys={RATINGS_KEYS}>`: the finding for every combination of the four controls. */
export const recommendationBarsTitles = (rows: RecommendationRows): Record<string, string> =>
  titleMapFor(RATINGS_KEYS, (st) => recommendationBarsFinding(rows, st));

/** Subtitle: the metric, unit and window in one sentence. Everything else is in `recommendationBarsNotes`. */
export function recommendationBarsSubtitle(rows: RecommendationRows): string {
  return `Share of ratings in each bucket, in percent, each panel on its own 0 to 100% scale: Yahoo Finance's ${rows.windows.yahoo ?? 'current distribution'} beside East Money's ${windowWord(rows.windows.eastmoney) || 'six-month'} brokerage ratings.`;
}

/**
 * Buy-side share of the Yahoo ratings in each layer, for the page's prose. A layer is compared only when at least `thinBelow` of its
 * listings have at least `thinBelow` rating analysts, and only those listings are pooled, so a layer of one thin rating cannot set the
 * extreme. `listings` and `ratings` say what stands behind each share.
 */
export function buyShareByLayer(rows: RecommendationRows): { layer: string; label: string; share: number; ratings: number; listings: number }[] {
  const out: { layer: string; label: string; share: number; ratings: number; listings: number }[] = [];
  for (const layer of LAYER_ORDER) {
    const ls = rows.yahoo.filter((l) => l.layer === layer && !l.thin);
    if (ls.length < rows.thinBelow) continue;
    const ratings = ls.reduce((n, l) => n + l.n_ratings, 0);
    const buy = ls.reduce((n, l) => n + (l.counts.strong_buy ?? 0) + (l.counts.buy ?? 0), 0);
    out.push({ layer, label: layerLabel(layer), share: buy / ratings, ratings, listings: ls.length });
  }
  return out;
}

/** Numbers the page's prose quotes about the ratings, all computed from the rows. */
export function ratingStats(rows: RecommendationRows) {
  const all = ratingGroups(rows, 'yahoo', targetsState())[0];
  const sides = all ? bySide(all) : null;
  const byLayer = buyShareByLayer(rows).sort((a, b) => a.share - b.share);
  const em = ratingGroups(rows, 'eastmoney', targetsState())[0];
  const emSides = em ? bySide(em) : null;
  const covered = rows.yahoo.filter((l) => !l.thin);
  const buyShares = covered.map((l) => (l.counts.strong_buy ?? 0) + (l.counts.buy ?? 0)).map((b, i) => b / covered[i].n_ratings);
  return {
    yahoo: all && sides ? { ratings: all.ratings, listings: all.listings, buy: sides.buy / all.ratings, hold: sides.hold / all.ratings, sell: sides.sell / all.ratings, shares: roundShares([sides.buy, sides.hold, sides.sell]) } : null,
    lowestLayer: byLayer[0] ?? null, highestLayer: byLayer[byLayer.length - 1] ?? null,
    nCovered: covered.length, coveredBuyMin: buyShares.length ? Math.min(...buyShares) : null, coveredBuyMax: buyShares.length ? Math.max(...buyShares) : null,
    eastmoney: em && emSides ? { ratings: em.ratings, listings: em.listings, buy: emSides.buy, hold: emSides.hold, sell: emSides.sell, counts: em.counts } : null,
    mainLine: rows.yahoo.filter((l) => l.purity != null && !isWholeBusiness(l.purity)).length,
  };
}

/**
 * How to read the figure: plain text, short paragraphs separated by a blank line (the page puts it in a "How to read this" disclosure).
 * Counts are for the full set; a control changes the bars drawn, not these sentences.
 */
export function recommendationBarsNotes(rows: RecommendationRows): string {
  const yb = BUCKETS.yahoo.map((b) => b.label);
  const eb = BUCKETS.eastmoney.map((b) => b.label);
  const paras: string[] = [];
  paras.push(
    `Left: Yahoo Finance's ${numWord(yb.length)} buckets (${andList(yb)}), ${rows.windows.yahoo ?? 'current distribution'}, pooled over ${count(rows.yahoo.length, 'listing')} outside China and by market.`
    + ` Right: East Money's brokerage ratings (${andList(eb)}), ${windowWord(rows.windows.eastmoney) || 'six-month'} window, pooled over ${count(rows.eastmoney.length, 'A-share listing')}; one count is one institution rating one company.`
    + ` The two vocabularies are never merged or added.`,
  );
  paras.push(
    `Colours group each source's own labels by direction: blue for the buy side, grey for hold or neutral, orange for the sell side. The two ends of a vocabulary share a colour and are told apart by a thin gap and by their order,`
    + ` which is written under the Yahoo bars. The totals of the three directions are written above each bar, and the share of each segment under it where it fits. n is the number of ratings in the bar.`,
  );
  const note = recommendationBarsNote(rows);
  if (note) paras.push(note);
  const empty = rows.empty.yahoo + rows.empty.eastmoney;
  if (empty) paras.push(`Not drawn: ${count(rows.empty.yahoo, 'Yahoo listing')} and ${count(rows.empty.eastmoney, 'East Money listing')} with no rating behind them (no count printed, or counts of zero).`);
  const main = rows.yahoo.filter((l) => l.purity != null && !isWholeBusiness(l.purity)).length;
  paras.push(
    `Coverage counts rating analysts: at least ${numWord(rows.thinBelow)} per listing. The market control applies to the Yahoo pools only; the East Money panel ignores it.`
    + ` The ratings source control chooses which source the title and the table follow; both panels are always drawn in full.`
    + `${main ? ` The Yahoo pools include ${count(main, 'company', 'companies')} whose main line of business, not the whole business, is security.` : ''}`,
  );
  return paras.join('\n\n');
}

/** Shorter aliases of the figure's exports, for callers that name the figure `recommendation`. */
export {
  recommendationBarsRows as recommendationRows, recommendationBarsSpec as recommendationSpec,
  recommendationBarsFinding as recommendationFinding, recommendationBarsSubtitle as recommendationSubtitle,
  recommendationBarsNotes as recommendationNotes,
};
export { SIDES, SIDE_LABEL };
