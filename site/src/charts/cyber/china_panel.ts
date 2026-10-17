import { VL_SCHEMA, series, tok } from '../theme';
import type { ChinaRow, Listing } from './types';
import {
  BUILD_LISTINGS, FILL_PART, FILL_WHOLE, MARKET_SHAPE, SHORT_HISTORY_SHARE, andList, axisName, count, exposureLabel, fmtShare, isWholeBusiness, median,
  shortHistoryBelow,
} from './targets_data';

/**
 * Figure 8: the China panel. For each A-share listing of rings A and B, East Money's PS_TTM (market capitalisation over
 * trailing-twelve-month revenue) placed in the listing's own daily history, beside its year-on-year revenue growth, in the same order.
 *
 * Definitional constants, mirrored from `pipelines/cyber/valuation_history_em.py` (they define the method, never a result):
 * the percentile is the share of the listing's own trading days with a PS_TTM at or below the latest; a percentile at or above
 * the top quartile with falling revenue is what `flags.ps_percentile_while_revenue_falling` marks; fewer trading days than the
 * minimum and the percentile is not published.
 */
export const TOP_QUARTILE = 0.75;
export const BOTTOM_QUARTILE = 1 - TOP_QUARTILE;
export const MIN_HISTORY_DAYS = 250;
/** A listing this close below the top-quartile cut (in percentile points, as a fraction) with falling revenue is named as a near miss. A display constant. */
export const NEAR_CUT = 0.02;
export const FLAG = 'ps_percentile_while_revenue_falling';

export interface ChinaPoint {
  ticker: string;
  name: string;
  name_cn: string | null;
  /** The axis label: a short name, with ` *` after it when the history is short. */
  label: string;
  ring: string;
  /** The listing's purity from the listings mart (null when the mart is not passed). */
  purity: string | null;
  /** The exposure as a legend prints it. */
  exposure: string;
  /** Security is the whole business (drawn filled); every smaller exposure is drawn hollow. */
  whole: boolean;
  ps_ttm: number | null;
  /** 0 to 1: share of the listing's own days with a PS_TTM at or below the latest. */
  ps_percentile: number | null;
  revenue_yoy: number | null;
  revenue_period: string | null;
  n_history_days: number | null;
  history_start: string | null;
  /** The history is shorter than `SHORT_HISTORY_SHARE` of the longest one (marked `*`): the percentile is taken over a shorter window. */
  short_history: boolean;
  as_of: string | null;
  /** `flags.ps_percentile_while_revenue_falling`: true, false, or null when either input is missing. */
  flag: boolean | null;
}

export interface ChinaRows {
  /** Every row of the mart. */
  all: ChinaPoint[];
  /** Drawn, in order: the listings with a percentile, highest first. */
  drawn: ChinaPoint[];
  /** Not drawn: no percentile (too few days of history, or no value). */
  noPercentile: ChinaPoint[];
  /** Drawn on the left only: a percentile but no revenue growth. */
  noRevenue: ChinaPoint[];
  /** Listings whose flag is a boolean (both inputs present). */
  assessable: ChinaPoint[];
  flagged: ChinaPoint[];
  /** Listings whose revenue fell year on year. */
  falling: ChinaPoint[];
  history_start_min: string | null;
  history_start_max: string | null;
  /** Trading days below which a history is marked short (`SHORT_HISTORY_SHARE` of the longest). */
  shortBelow: number;
  /** The reporting period of the growth figure, or null when listings differ. */
  period: string | null;
  as_of: string | null;
}

const maxOf = (xs: (string | null)[]) => xs.reduce<string | null>((m, x) => (x && (!m || x > m) ? x : m), null);
const minOf = (xs: (string | null)[]) => xs.reduce<string | null>((m, x) => (x && (!m || x < m) ? x : m), null);

/**
 * Data preparation: the mart's rows as points, ordered by percentile, with the counts the title and subtitle quote. Each point
 * carries its exposure from the listings mart (the build's own copy when the caller passes none).
 */
export function chinaPanelRows(rows: ChinaRow[], listings: Listing[] = BUILD_LISTINGS): ChinaRows {
  const purityOf = new Map(listings.map((l) => [l.ticker, l.purity]));
  const shortBelow = shortHistoryBelow(rows.map((r) => r.n_history_days));
  const all: ChinaPoint[] = rows.filter((r) => r.ticker).map((r) => {
    const flag = r.flags && typeof r.flags[FLAG] === 'boolean' ? (r.flags[FLAG] as boolean) : null;
    const purity = purityOf.get(r.ticker as string) ?? null;
    const short = r.n_history_days != null && r.n_history_days < shortBelow;
    return {
      ticker: r.ticker as string, name: r.name ?? (r.ticker as string), name_cn: r.name_cn, label: `${axisName(r.name ?? r.ticker)}${short ? ' *' : ''}`,
      ring: r.ring ?? '', purity, exposure: exposureLabel(purity), whole: isWholeBusiness(purity),
      ps_ttm: r.ps_ttm, ps_percentile: r.ps_percentile, revenue_yoy: r.revenue_yoy, revenue_period: r.revenue_period,
      n_history_days: r.n_history_days, history_start: r.history_start, short_history: short, as_of: r.as_of, flag,
    };
  });
  const seen = new Map<string, number>();
  all.forEach((p) => seen.set(p.label, (seen.get(p.label) ?? 0) + 1));
  all.forEach((p) => { if ((seen.get(p.label) ?? 0) > 1) p.label = `${p.label} (${p.ticker})`; });
  const drawn = all.filter((p) => p.ps_percentile != null && p.ps_percentile > 0)
    .sort((a, b) => (b.ps_percentile as number) - (a.ps_percentile as number) || a.ticker.localeCompare(b.ticker));
  const periods = Array.from(new Set(all.map((p) => p.revenue_period).filter((x): x is string => !!x)));
  return {
    all, drawn,
    noPercentile: all.filter((p) => !(p.ps_percentile != null && p.ps_percentile > 0)),
    noRevenue: drawn.filter((p) => p.revenue_yoy == null),
    assessable: all.filter((p) => p.flag != null),
    flagged: all.filter((p) => p.flag === true),
    falling: all.filter((p) => p.revenue_yoy != null && p.revenue_yoy < 0),
    history_start_min: minOf(drawn.map((p) => p.history_start)),
    history_start_max: maxOf(drawn.map((p) => p.history_start)),
    shortBelow,
    period: periods.length === 1 ? periods[0] : null,
    as_of: maxOf(all.map((p) => p.as_of)),
  };
}

// ---- numbers the title and the page quote ------------------------------------------------------------------------------

/** log of n choose k */
const lnChoose = (n: number, k: number): number => {
  let s = 0;
  for (let i = 1; i <= k; i += 1) s += Math.log((n - k + i) / i);
  return s;
};
/** P(X >= k) for X hypergeometric: `draws` drawn from `total`, of which `marked` are marked. */
export function hypergeometricAtLeast(k: number, total: number, marked: number, draws: number): number {
  let p = 0;
  for (let i = k; i <= Math.min(marked, draws); i += 1) {
    if (draws - i > total - marked) continue;
    p += Math.exp(lnChoose(marked, i) + lnChoose(total - marked, draws - i) - lnChoose(total, draws));
  }
  return Math.min(1, p);
}

/**
 * Everything the title, the notes and the page's prose say about the distribution, computed from the rows. `topFalling` is the flag
 * (top quartile of its own history while revenue falls); `expected` is how many of the `top` listings would have falling revenue if
 * growth had no link to the percentile (the falling share of the assessable listings times `top`), and `pAtLeast` the chance of
 * `topFalling` or more under that same assumption (hypergeometric).
 */
export function chinaPanelStats(rows: ChinaRows) {
  const pct = rows.drawn.filter((p) => p.ps_percentile != null);
  const top = pct.filter((p) => (p.ps_percentile as number) >= TOP_QUARTILE);
  const bottom = pct.filter((p) => (p.ps_percentile as number) <= BOTTOM_QUARTILE);
  const assess = rows.assessable;
  const fallingAssess = assess.filter((p) => (p.revenue_yoy as number) < 0).length;
  const topAssess = top.filter((p) => p.flag != null);
  const topFalling = topAssess.filter((p) => (p.revenue_yoy as number) < 0);
  const expected = assess.length ? (topAssess.length * fallingAssess) / assess.length : 0;
  const nearMiss = pct.filter((p) => p.flag === false && (p.ps_percentile as number) < TOP_QUARTILE && (p.ps_percentile as number) >= TOP_QUARTILE - NEAR_CUT && (p.revenue_yoy as number) < 0);
  const partial = rows.all.filter((p) => !p.whole && p.purity != null);
  return {
    n: rows.all.length, nDrawn: pct.length, nAssessable: assess.length,
    top: top.length, bottom: bottom.length, belowMedianHalf: pct.filter((p) => (p.ps_percentile as number) < 0.5).length,
    topFalling: topFalling.length, topFallingList: topFalling,
    falling: rows.falling.length, fallingAssessable: fallingAssess,
    expected, pAtLeast: topAssess.length ? hypergeometricAtLeast(topFalling.length, assess.length, fallingAssess, topAssess.length) : null,
    medianPercentile: median(pct.map((p) => p.ps_percentile as number)),
    nPartial: partial.filter((p) => p.purity === 'partial').length, nNotWhole: partial.length,
    flaggedPartial: rows.flagged.filter((p) => p.purity === 'partial'),
    shortHistory: rows.all.filter((p) => p.short_history), flaggedShort: rows.flagged.filter((p) => p.short_history),
    nearMiss,
  };
}

// ---- spec ------------------------------------------------------------------------------------------------------------

const ZWSP = '​';
const year = (d: string | null) => (d ? d.slice(0, 4) : '');
export const PANEL_PCT = 'pct';
export const PANEL_YOY = 'yoy';

const floorTo = (x: number, step: number) => Math.floor(x / step + 1e-9) * step;
const ceilTo = (x: number, step: number) => Math.ceil(x / step - 1e-9) * step;

/** `2026H1` as `first half 2026` (`H2`: second half); any other period is returned as served. */
export function periodWords(period: string | null | undefined): string {
  const m = /^(\d{4})H([12])$/.exec(period ?? '');
  return m ? `${m[2] === '1' ? 'first' : 'second'} half ${m[1]}` : (period ?? '');
}

/** Panel headers (lines joined by `|`; a line is at most about 17 characters so two panels of a phone do not touch). */
export function chinaPanelHeaders(rows: ChinaRows): Record<string, string> {
  return {
    [PANEL_PCT]: `PS_TTM percentile|own history, from|${year(rows.history_start_min) || 'listing'} or listing|* = short history`,
    [PANEL_YOY]: `Revenue growth|year on year,|${periodWords(rows.period) || 'latest report'}`,
  };
}

export const FLAG_LABEL = 'Top quartile and revenue falling';
export const OTHER_LABEL = 'Other listings';

/** Label of the legend's three entries: the two fills (exposure) and the colour of a flagged listing. */
const LEGEND_KEYS = [FILL_WHOLE, FILL_PART, FLAG_LABEL];

/**
 * Figure 8. Two panels of one row of listings, in the same order (highest percentile first), each with its own axis: left, the
 * percentile of the latest PS_TTM among the listing's own days (dashed line, labelled: top quartile); right, revenue growth
 * year on year for the same listings (solid line: no growth). Both are shown in percent. A listing that sits in the top quartile
 * of its own history while revenue falls is drawn in orange with both numbers written beside it. Exposure is the fill (filled:
 * security is the whole business; hollow: its main line or only part of it) and the shape is the market's (every listing here is
 * an A-share, so a diamond). A name followed by `*` has a history shorter than three quarters of the longest one. A listing with
 * no percentile is not drawn in either panel and is counted in the notes. An empty mart prints one sentence.
 *
 * Static figure (no dashboard controls).
 */
export function chinaPanelSpec(rows: ChinaRows) {
  const headers = chinaPanelHeaders(rows);
  const order = rows.drawn.map((p) => p.label);
  const hasData = rows.drawn.length > 0;
  const yoys = rows.drawn.map((p) => p.revenue_yoy).filter((v): v is number => v != null).map((v) => v * 100);
  const yLo = floorTo(Math.min(0, ...(yoys.length ? yoys : [0])), 20);
  const yHi = ceilTo(Math.max(0, ...(yoys.length ? yoys : [0])), 20);
  const when = rows.period ? periodWords(rows.period) : 'the latest report';
  const pt = (p: ChinaPoint, panel: string, value: number) => ({
    k: 'pt', panel, panel_label: headers[panel], label: p.label, name: p.name, name_cn: p.name_cn, ticker: p.ticker, ring: p.ring,
    exposure: p.exposure, whole: p.whole, value, flagged: p.flag === true,
    ps_ttm: p.ps_ttm, percentile: p.ps_percentile == null ? null : p.ps_percentile * 100, revenue_yoy: p.revenue_yoy == null ? null : p.revenue_yoy * 100,
    revenue_period: p.revenue_period, n_history_days: p.n_history_days, history_start: p.history_start,
    history_note: p.short_history ? 'shorter than three quarters of the longest history' : null,
    aria: chinaAria(p, panel, when),
    lab: p.flag === true ? `${Math.round(value)}%` : '',
    // A label goes on the side of its marker with room, so it never runs past the panel's edge.
    lab_align: (value - (panel === PANEL_PCT ? 0 : yLo)) / ((panel === PANEL_PCT ? 100 : yHi) - (panel === PANEL_PCT ? 0 : yLo)) > 0.7 ? 'right' : 'left',
  });
  const values: Record<string, unknown>[] = [];
  if (hasData) {
    rows.drawn.forEach((p) => {
      values.push(pt(p, PANEL_PCT, (p.ps_percentile as number) * 100));
      if (p.revenue_yoy != null) values.push(pt(p, PANEL_YOY, p.revenue_yoy * 100));
    });
    // Invisible anchors fix each panel's own axis: the percentile runs 0 to 100, growth from the lowest to the highest, to fifths.
    [0, 100].forEach((v) => values.push({ k: 'anchor', panel: PANEL_PCT, panel_label: headers[PANEL_PCT], label: order[0], value: v }));
    [yLo, yHi].forEach((v) => values.push({ k: 'anchor', panel: PANEL_YOY, panel_label: headers[PANEL_YOY], label: order[0], value: v }));
  } else {
    values.push({ k: 'state', panel: PANEL_PCT, panel_label: headers[PANEL_PCT], label: ZWSP, line: ['No A-share valuation history', 'is published on this build.'], aria: 'No A-share valuation history is published on this build.' });
  }
  const y = {
    field: 'label', type: 'nominal', title: null, sort: order.length ? order : undefined,
    axis: { labelLimit: 84, labelFontSize: 10, ticks: false, domain: false, grid: true, gridColor: tok('grid'), gridOpacity: 0.5, labelPadding: 4 },
  };
  const x = {
    field: 'value', type: 'quantitative', title: null,
    scale: { nice: false, zero: false },
    axis: { tickCount: 6, tickMinStep: 25, labelExpr: "datum.value + '%'", grid: true, gridColor: tok('grid'), labelFontSize: 10.5, offset: 4, labelFlush: true },
  };
  const tooltip = [
    { field: 'name', type: 'nominal', title: 'Company' },
    { field: 'name_cn', type: 'nominal', title: 'Name in Chinese' },
    { field: 'ticker', type: 'nominal', title: 'Listing' },
    { field: 'exposure', type: 'nominal', title: 'Security exposure' },
    { field: 'ring', type: 'nominal', title: 'Ring (A: priced pool, B: classified only)' },
    { field: 'ps_ttm', type: 'quantitative', title: 'PS_TTM, latest (times revenue)', format: '.1f' },
    { field: 'percentile', type: 'quantitative', title: 'Percentile of own days (%)', format: '.0f' },
    { field: 'n_history_days', type: 'quantitative', title: 'Trading days in history' },
    { field: 'history_start', type: 'nominal', title: 'History starts' },
    { field: 'history_note', type: 'nominal', title: 'History' },
    { field: 'revenue_yoy', type: 'quantitative', title: 'Revenue growth, year on year (%)', format: '+.1f' },
    { field: 'revenue_period', type: 'nominal', title: 'Revenue period' },
  ];
  const wholeTest = 'datum.whole';
  const legend = { title: null, orient: 'top', direction: 'vertical', columns: 1, labelFontSize: 11, labelLimit: 220, offset: 8, symbolType: 'diamond', symbolStrokeWidth: 1.7, symbolSize: 72 };
  return {
    $schema: VL_SCHEMA,
    data: { values },
    facet: {
      column: {
        field: 'panel_label', type: 'nominal', title: null, sort: [headers[PANEL_PCT], headers[PANEL_YOY]],
        header: {
          labelOrient: 'top', labelAnchor: 'start', labelAngle: 0, labelAlign: 'left', labelFontSize: 12, labelPadding: 16, labelLimit: 0,
          labelColor: tok('ink'), labelExpr: "split(datum.label, '|')", labelLineHeight: 14,
        },
      },
    },
    _facetColumns: 2,
    spacing: 4,
    padding: { left: 0, right: 0, top: 0, bottom: 2 },
    spec: {
      width: 200,
      height: { step: 15 },
      layer: [
        {
          // the top-quartile line, on the percentile panel only; a reference line with no text of its own, so the accessibility tree skips it
          transform: [{ filter: `datum.k == 'pt' && datum.panel == '${PANEL_PCT}'` }, { aggregate: [{ op: 'count', as: 'n' }] }, { calculate: String(TOP_QUARTILE * 100), as: 'value' }],
          mark: { type: 'rule', color: tok('ink-3'), strokeWidth: 1, strokeDash: [4, 3], aria: false },
          encoding: { x },
        },
        {
          // its name, written once at the top of the panel, to the left of the line
          transform: [{ filter: `datum.k == 'pt' && datum.panel == '${PANEL_PCT}'` }, { aggregate: [{ op: 'count', as: 'n' }] }, { calculate: String(TOP_QUARTILE * 100), as: 'value' }, { calculate: "'top quartile'", as: 'txt' }],
          mark: { type: 'text', align: 'right', baseline: 'bottom', dx: -3, fontSize: 10, fontStyle: 'italic', color: tok('ink-2'), aria: false },
          encoding: { x, y: { value: -3 }, text: { field: 'txt', type: 'nominal' } },
        },
        {
          // no growth, on the growth panel only
          transform: [{ filter: `datum.k == 'pt' && datum.panel == '${PANEL_YOY}'` }, { aggregate: [{ op: 'count', as: 'n' }] }, { calculate: '0', as: 'value' }],
          mark: { type: 'rule', color: tok('ink-3'), strokeWidth: 1, aria: false },
          encoding: { x },
        },
        {
          // invisible anchors that fix each panel's axis: nothing to read, so hidden from the accessibility tree
          transform: [{ filter: "datum.k == 'anchor'" }],
          mark: { type: 'point', opacity: 0, size: 1, aria: false },
          encoding: { x, y },
        },
        {
          // the listings: a diamond (the market), filled when security is the whole business and hollow otherwise, orange when flagged
          transform: [{ filter: "datum.k == 'pt'" }],
          mark: { type: 'point', shape: MARKET_SHAPE.china, filled: true, size: 72, strokeWidth: 1.7, opacity: 1 },
          encoding: {
            x, y, tooltip, description: { field: 'aria', type: 'nominal' },
            stroke: { condition: { test: 'datum.flagged', value: series.b }, value: series.a },
            fill: {
              condition: [
                { test: `${wholeTest} && datum.flagged`, value: series.b },
                { test: wholeTest, value: series.a },
              ],
              value: tok('bg'),
            },
          },
        },
        {
          // the legend: this layer plots nothing (its filter passes no row); its scales have explicit domains, which is all a legend needs
          transform: [{ filter: 'false' }],
          mark: { type: 'point', shape: MARKET_SHAPE.china, filled: true, size: 72, strokeWidth: 1.7, opacity: 1, aria: false },
          encoding: {
            fill: { field: 'legend_key', type: 'nominal', scale: { type: 'ordinal', domain: LEGEND_KEYS, range: [series.a, tok('bg'), series.b] }, legend },
            stroke: { field: 'legend_key', type: 'nominal', scale: { type: 'ordinal', domain: LEGEND_KEYS, range: [series.a, series.a, series.b] }, legend },
          },
        },
        ...(['left', 'right'] as const).map((align) => ({
          // the two numbers of a flagged listing, written beside it on the side with room
          transform: [{ filter: `datum.k == 'pt' && datum.flagged && datum.lab_align == '${align}'` }],
          mark: { type: 'text', align, dx: align === 'left' ? 9 : -9, baseline: 'middle', fontSize: 10, color: tok('ink-2'), aria: false },
          encoding: { x, y, text: { field: 'lab', type: 'nominal' } },
        })),
        {
          transform: [{ filter: "datum.k == 'state'" }],
          mark: { type: 'text', align: 'left', baseline: 'top', dy: -7, lineHeight: 12, fontSize: 10.5, fontStyle: 'italic', color: tok('ink-2') },
          encoding: { x: { value: 2 }, y, text: { field: 'line', type: 'nominal' }, description: { field: 'aria', type: 'nominal' } },
        },
      ],
    },
    resolve: { scale: { x: 'independent' }, axis: { x: 'independent' } },
  };
}

/** The sentence a screen reader gets for one listing's mark in one panel. */
export function chinaAria(p: ChinaPoint, panel: string, when: string): string {
  const who = `${p.name}, ${p.ticker}, ${p.exposure}, ring ${p.ring}`;
  if (panel === PANEL_PCT) {
    const days = p.n_history_days != null ? `, over ${p.n_history_days.toLocaleString('en-US')} trading days from ${p.history_start ?? 'the start of its history'}${p.short_history ? ' (a short history)' : ''}` : '';
    return `${who}: PS_TTM is at the ${fmtShare(p.ps_percentile as number)} percentile of its own history${days}${p.flag === true ? '; top quartile while revenue falls' : ''}`;
  }
  return `${who}: revenue growth ${p.revenue_yoy == null ? 'not available' : `${p.revenue_yoy >= 0 ? '+' : ''}${(p.revenue_yoy * 100).toFixed(1)}%`} year on year, ${when}`;
}

// ---- finding and subtitle ----------------------------------------------------------------------------------------------

/**
 * Finding: where the A-share listings sit in their own PS_TTM history (how many in the bottom quarter, how many in the top) and how
 * many of those in the top quarter have falling revenue, against how many would be expected if growth had no link to the percentile.
 * Counted from the rows; the flag is the mart's `flags.ps_percentile_while_revenue_falling`.
 */
export function chinaPanelFinding(rows: ChinaRows): string {
  if (!rows.all.length) return 'No A-share valuation history is published on this build';
  const s = chinaPanelStats(rows);
  if (!s.nAssessable) return `The top-quartile flag is not available for any of the ${count(rows.all.length, 'A-share listing')}: each lacks a PS_TTM percentile or a revenue growth figure`;
  const partly = s.nPartial ? ` (${s.nPartial} only partly security)` : '';
  const lead = `Of ${s.n} A-share listings${partly}, ${s.bottom} sit in the bottom quarter of their own PS_TTM history and ${s.top} in the top`;
  if (!s.top) return lead;
  return `${lead}; ${s.topFalling} of those ${s.top} ${s.topFalling === 1 ? 'has' : 'have'} falling revenue, ${s.expected.toFixed(1)} expected if the two were unrelated`;
}

/** Subtitle: the metric, unit and window of each panel in one sentence. Everything else is in `chinaPanelNotes`. */
export function chinaPanelSubtitle(rows: ChinaRows): string {
  const first = rows.history_start_min;
  return `Left: PS_TTM (market capitalisation over trailing-twelve-month revenue) as a percentile of the listing's own daily history since ${first ?? '2018'}, in percent; right: year-on-year revenue growth, ${rows.period ? `${periodWords(rows.period)}, ` : ''}in percent; East Money, as of ${rows.as_of ?? 'the snapshot'}.`;
}

/**
 * How to read the figure: plain text, short paragraphs separated by a blank line (the page puts it in a "How to read this"
 * disclosure).
 */
export function chinaPanelNotes(rows: ChinaRows): string {
  const first = rows.history_start_min;
  const last = rows.history_start_max;
  const s = chinaPanelStats(rows);
  const paras: string[] = [];
  paras.push(
    `Left: the percentile is the share of the listing's own trading days since ${first ?? '2018'}`
    + `${last && first && last > first ? ` (a later start, up to ${last}, where the listing is younger)` : ''} with a PS_TTM at or below the latest, East Money, as of ${rows.as_of ?? 'the snapshot'}. The dashed line is the top quartile.`
    + ` Right: cumulative revenue growth year on year, ${rows.period ? `${periodWords(rows.period)}, ` : 'latest report of each listing, '}East Money, in percent; the solid line is no growth. Same listings, same order.`
    + ` A half-year growth rate is lumpy for any company that books revenue in projects, so read a fall with care.`,
  );
  paras.push(
    `Marks: a diamond is an A-share listing. A filled diamond is a company whose whole business is security; a hollow one is a company whose main line of business is security, or only part of it`
    + `${s.nNotWhole ? ` (${count(s.nNotWhole, 'listing')}, of which ${s.nPartial} only partly)` : ''}. Orange marks a listing in the top quartile of its own history while revenue falls, with both numbers beside it.`
    + ` Ring, in the tooltip: A is the priced pool and B the classified list that is counted but not priced.`,
  );
  if (s.shortHistory.length) {
    paras.push(
      `A name followed by * has fewer than ${rows.shortBelow.toLocaleString('en-US')} trading days of history (${fmtShare(SHORT_HISTORY_SHARE)} of the longest, ${Math.max(...rows.all.map((p) => p.n_history_days ?? 0)).toLocaleString('en-US')}): its percentile is taken over a shorter window, which can miss earlier peaks and make a high percentile easier to reach.`
      + ` ${count(s.shortHistory.length, 'listing')} ${s.shortHistory.length === 1 ? 'is' : 'are'} marked${s.flaggedShort.length ? `, including ${andList(s.flaggedShort.map((p) => p.name))} among the flagged` : ''}. The percentile needs at least ${MIN_HISTORY_DAYS} trading days.`,
    );
  }
  if (s.nAssessable) {
    paras.push(
      `The flag is a count, not a test: ${s.topFalling} of the ${s.top} listings in the top quarter have falling revenue, and ${s.fallingAssessable} of all ${s.nAssessable} do, so about ${s.expected.toFixed(1)} would fall by chance`
      + `${s.pAtLeast != null ? `; the chance of ${s.topFalling} or more is ${fmtShare(s.pAtLeast)} if growth and the percentile were unrelated (hypergeometric)` : ''}.`
      + ` The median listing's latest PS_TTM is above ${fmtShare(s.medianPercentile)} of its own trading days, and ${s.bottom} of ${s.nDrawn} are in the bottom quarter.`
      + `${s.nearMiss.length ? ` The cut is sharp: ${andList(s.nearMiss.map((p) => `${p.name} (${fmtShare(p.ps_percentile as number, 1)}, revenue ${fmtShare(p.revenue_yoy as number)})`))} ${s.nearMiss.length === 1 ? 'sits' : 'sit'} just below it and ${s.nearMiss.length === 1 ? 'is' : 'are'} not flagged.` : ''}`
      + `${s.flaggedPartial.length ? ` ${count(s.flaggedPartial.length, 'flagged listing')} (${andList(s.flaggedPartial.map((p) => p.name))}) ${s.flaggedPartial.length === 1 ? 'has' : 'have'} only partial security exposure.` : ''}`,
    );
  }
  const gaps: string[] = [];
  if (rows.noPercentile.length) {
    gaps.push(`${count(rows.noPercentile.length, 'listing')} ${rows.noPercentile.length === 1 ? 'has' : 'have'} no percentile (fewer than ${MIN_HISTORY_DAYS} trading days of history, about one year) and ${rows.noPercentile.length === 1 ? 'is' : 'are'} not drawn`
      + `${rows.noPercentile.length <= 5 ? `: ${rows.noPercentile.map((p) => p.name).join(', ')}` : ''}`);
  }
  if (rows.noRevenue.length) gaps.push(`${count(rows.noRevenue.length, 'listing')} ${rows.noRevenue.length === 1 ? 'has' : 'have'} no revenue growth figure and appear${rows.noRevenue.length === 1 ? 's' : ''} on the left only`);
  if (!rows.all.length) paras.push('Nothing is drawn: the mart holds no A-share valuation history.');
  else paras.push(gaps.length ? `${gaps.join('; ')}.` : `Not drawn: none; all ${rows.all.length} listings have a percentile and a revenue growth figure.`);
  return paras.join('\n\n');
}
