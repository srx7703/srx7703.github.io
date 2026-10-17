/**
 * Figure 2, EV / NTM revenue against expected growth: does the market pay more for faster-growing security companies, and
 * which companies sit far from the page's own regression.
 *
 * Marks (the legend is one block written by hand, `keyLayer`; the notes say the same in words):
 *  - one dot per listing with both a multiple and a growth estimate (ring A today; ring B carries neither);
 *  - shape is the listing market (United States circle, other overseas triangle, China A-share diamond), never colour;
 *  - fill is exposure: filled blue is a company whose whole business is security, hollow grey one whose main line of business
 *    is (a `main` listing is outside the page's regression);
 *  - a ring round a dot marks the companies the regression was fitted on (`regression.latest.population`, 23 today), so the n and
 *    the R-squared in the notes describe marks a reader can find;
 *  - size is enterprise value in US dollars: the mart's `enterprise_value_usd`, which is the quoted value of a dollar listing or the
 *    quoted value converted at the FRED rate of its currency (`facts.coverage.fx_as_of`). A listing whose currency has no rate on file
 *    is drawn at the smallest size and named in the notes. Multiples are ratios inside one currency and are never converted;
 *  - fewer than three analysts behind the multiple is a dashed outline and a lighter fill, the outline at full opacity;
 *  - the five largest residuals of the regression are named. Each is measured against that company's OWN fitted multiple from the full
 *    model (the percent is exp(residual) - 1), and a short rule runs from the dot to that fitted value, so the label and the geometry
 *    agree. The line is NOT that fit.
 *
 * The line is the regression's growth slope, drawn through the means of the sample it was fitted on
 * (ln fitted multiple = c + slope x growth, c from the residuals mart: the mean of ln(fitted) minus slope x growth). The model also
 * holds free-cash-flow margin, listing market and layer groups, so the line is a slice of the multivariate fit with the other terms at
 * their mean ("growth slope, other terms at their mean"), not a separate one-variable regression and not each company's fit; it is drawn
 * whatever the filters say and only across the growth range of the fitted sample. Drawn only if `regression.latest.fitted`. The model is
 * the page's own; it is not one of the registered readings (facts.regression.note).
 *
 * Growth axis: linear, ending at the 92nd percentile of growth rounded up to a whole ten points; a listing beyond it is outside the
 * plotted range, counted on the plot ("n more beyond 50%") and named in the notes with its growth. The multiple axis is a log scale.
 *
 * Narrow charts (a plot narrower than NARROW_PLOT pixels) name the five dots by ticker and percent; wide charts by name and percent, both on
 * one line (the legend says the percent is the gap along the rule to the company's own model fit). Where each label goes (left or right of its dot, nudged up or down) is chosen here, once, by a small
 * search that keeps labels off the dots, off each other and inside the plot at a range of widths.
 */
import { VL_SCHEMA, series, tok } from '../theme';
import type { Listing, RegressionMart } from './types';
import {
  type KeyEntry, type MissingMultiples, type Spec, type ValuationPoint, type ValuationState,
  COV_LABEL_EXPR, COV_THIN_LABEL, EXPOSURE_MAIN, EXPOSURE_WHOLE, FILTER_EXPR, KEY_SCALES, MARKETS, MARKET_LABEL, MARKET_SHAPE, MIN_ANALYSTS, THIN_DASH, THIN_OPACITY,
  USD_LABEL_EXPR, USD_TEXT_EXPR, VALUATION_DEFAULTS,
  GROWTH_AXIS_HI_Q, andList, baselineReading, clipDomain, emptySpec, emptyViewLayer, fmtDay, fmtPct0, fmtSigned, fmtTimes, fxAsOf, keyLayer, logTicks, mean, median,
  missingMultipleNote, missingMultiples, numberWord, plural, shortName, snapshotDate, valuationParams, valuationPoints, viewOf,
} from './valuation_data';

/** The unit of the slope's translation into a ratio: ten points of growth. A method constant, not a result. */
export const GROWTH_STEP = 0.1;
/**
 * Fewest listings in a view for a faster-half / slower-half comparison to be stated in the title. Below it the title says the view is too
 * small to compare: the median of three companies cannot carry that sentence.
 */
export const MIN_COMPARE_N = 12;
/** A plot narrower than this many pixels (a phone) gets one-line labels and fewer ticks. */
export const NARROW_PLOT = 330;
/** Height of the plot in pixels (the spec's `height`). */
const PLOT_H = 420;
/** Size range of the dots in square pixels, and the gap between a dot's edge and its ring in pixels. */
const SIZE_RANGE: [number, number] = [52, 560];
const RING_GAP = 3.4;

export type FitSummary = {
  n: number | null; nUs: number | null; nCn: number | null; r2: number | null; adjR2: number | null; adjR2NoLayer: number | null;
  slope: number | null; slopeCi: [number, number] | null; asOf: string | null;
};

/** Where a label sits against its dot: on its left or right side, the pixel gap from the dot's centre to the text, and the vertical nudge in pixels. */
export type Placement = { side: 'left' | 'right'; dx: number; dy: number };

export type ResidualLabel = {
  ticker: string; name: string; growth: number; ev_ntm: number; residual: number;
  /** The company's own fitted multiple from the full model, in the same unit as `ev_ntm`. */
  fitted: number;
  /** exp(residual) - 1: how far the dot is above (positive) or below its own fitted multiple, as a fraction. */
  gap: number;
  /** Two lines: the name, then how far the dot is from its own model-fitted multiple. */
  text: [string, string];
  /** One line for a wide chart: the name and the percent (the legend says the percent is the gap to the company's own model fit). */
  line: string;
  /** One line for a narrow chart: the ticker and the percent. */
  short: string;
  layer: string; ring: string; market: string; purity: string; cov3: boolean;
  /** Where the label goes on a wide and on a narrow chart. */
  wide: Placement; narrow: Placement;
};

export type EvGrowthData = {
  /** Every listing with a multiple, growth, FCF margin or score: the dataset figures 2 and 3 share. */
  points: ValuationPoint[];
  /** Listings with both a multiple and a growth estimate, before any filter. */
  plotted: number;
  /** True when none has both: the spec is then one sentence. */
  empty: boolean;
  x: { domain: [number, number] };
  y: { domain: [number, number]; ticks: number[] };
  /** Listings with growth outside the plotted range of the axis, named in the notes. */
  offAxis: { ticker: string; name: string; growth: number }[];
  line: { growth: number; ev_ntm: number }[] | null;
  /** null when the mart has no `latest`; `fitted` false means the regression did not run this week. */
  fit: FitSummary | null;
  fitted: boolean;
  fitReason: string | null;
  /** Tickers of the companies the regression was fitted on (`regression.latest.population`), ringed on the plot. */
  fitTickers: string[];
  labels: ResidualLabel[];
  missing: MissingMultiples;
  /** Ring A listings with a multiple and no growth estimate. */
  noGrowth: number;
  /** Day of the newest price (`YYYY-MM-DD`), for the figure's as-of stamp. */
  asOf: string | null;
  /** Plotted dots with an enterprise value in US dollars (the size scale), and how many of them were converted from another currency. */
  nUsd: number;
  nConverted: number;
  /** Plotted dots with no dollar value because their currency has no exchange rate on file: drawn at the smallest size. */
  noRate: { ticker: string; name: string; ccy: string }[];
  /** The day of the exchange rates (`facts.coverage.fx_as_of`), null when facts carry none. */
  fxAsOf: string | null;
  size: { domain: [number, number]; legend: number[] } | null;
  defaults: ValuationState;
};

const asNum = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const asStr = (v: unknown): string | null => (typeof v === 'string' ? v : null);

// ---- where the labels go ----------------------------------------------------------------------------------------------------

type Box = { x0: number; x1: number; y0: number; y1: number };
const overlap = (a: Box, b: Box): number => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
const CHAR_W = 5.9;
const LINE_H = 13;
const WIDE_PLOT_WIDTHS = [380, 450, 530, 620, 720, 860, 980];
const NARROW_PLOT_WIDTHS = [125, 150, 175, 200, 230, 280];

type Geometry = {
  x: [number, number]; y: [number, number];
  /** Dots to keep clear: every plotted listing, with its enterprise value in dollars (or null). */
  dots: { ticker: string; growth: number; ev: number; usd: number | null }[];
  sizeDomain: [number, number] | null;
  line: { growth: number; ev_ntm: number }[] | null;
};

/** The area in square pixels the size scale gives a dot (the same square-root scale as the spec). */
function dotArea(usd: number | null, domain: [number, number] | null): number {
  if (!domain || usd == null) return SIZE_RANGE[0];
  const [lo, hi] = domain.map(Math.sqrt);
  return hi > lo ? SIZE_RANGE[0] + (SIZE_RANGE[1] - SIZE_RANGE[0]) * ((Math.sqrt(usd) - lo) / (hi - lo)) : SIZE_RANGE[0];
}

/**
 * Greedy search for the label positions of one variant. Each label tries left and right of its dot, centred and nudged up and down by half
 * a line, and takes the cheapest: overlap with other dots, with labels already placed and with their rules, with the line, and any part
 * outside the plot, summed over several plot widths so the choice holds from a narrow to a wide chart.
 */
function placeLabels(
  labels: { ticker: string; growth: number; ev_ntm: number; fitted: number; w: number; h: number }[],
  g: Geometry,
  widths: number[],
): Placement[] {
  const order = labels.map((_, i) => i);
  const out: Placement[] = labels.map(() => ({ side: 'right', dx: 9, dy: 0 }));
  const ly = (v: number, y: [number, number]) => ((Math.log(y[1]) - Math.log(v)) / (Math.log(y[1]) - Math.log(y[0]))) * PLOT_H;
  const lx = (v: number, W: number) => ((v - g.x[0]) / (g.x[1] - g.x[0])) * W;
  const placed: { i: number; boxes: Box[] }[] = [];
  for (const i of order) {
    const l = labels[i];
    // the gap from the dot's centre to the text clears its ring (the dot's radius, the ring's gap and a little air)
    const r0 = Math.sqrt(dotArea(g.dots.find((d) => d.ticker === l.ticker)?.usd ?? null, g.sizeDomain) / Math.PI);
    const dx = Math.round(r0 + RING_GAP + 4);
    const cands: Placement[] = [];
    for (const side of ['right', 'left'] as const) for (const dy of [0, -l.h / 2 - 1, l.h / 2 + 1, -l.h - 3, l.h + 3, -1.5 * l.h - 5, 1.5 * l.h + 5]) cands.push({ side, dx, dy: Math.round(dy) });
    let best = cands[0];
    let bestCost = Infinity;
    for (const c of cands) {
      let cost = (c.side === 'right' ? 0 : 1.5) + Math.abs(c.dy) * 0.05;
      const boxes: Box[] = [];
      for (const W of widths) {
        const px = lx(l.growth, W);
        const py = ly(l.ev_ntm, g.y);
        const x0 = c.side === 'right' ? px + c.dx : px - c.dx - l.w;
        const box: Box = { x0, x1: x0 + l.w, y0: py + c.dy - l.h / 2, y1: py + c.dy + l.h / 2 };
        boxes.push(box);
        for (const d of g.dots) {
          if (d.ticker === l.ticker) continue;
          const dr = Math.sqrt(dotArea(d.usd, g.sizeDomain) / Math.PI) + 1.5;
          const dx = lx(d.growth, W);
          const dyy = ly(d.ev, g.y);
          cost += overlap(box, { x0: dx - dr, x1: dx + dr, y0: dyy - dr, y1: dyy + dr });
        }
        for (const p of placed) {
          const pb = p.boxes[boxes.length - 1];
          cost += 4 * overlap(box, pb);
          const o = labels[p.i];
          const rx = lx(o.growth, W);
          cost += 3 * overlap(box, { x0: rx - 1, x1: rx + 1, y0: Math.min(ly(o.ev_ntm, g.y), ly(o.fitted, g.y)), y1: Math.max(ly(o.ev_ntm, g.y), ly(o.fitted, g.y)) });
        }
        if (g.line && g.line.length === 2) {
          const [a, b] = g.line;
          for (let k = 0; k <= 40; k++) {
            const gr = a.growth + ((b.growth - a.growth) * k) / 40;
            const e = Math.exp(Math.log(a.ev_ntm) + ((Math.log(b.ev_ntm) - Math.log(a.ev_ntm)) * k) / 40);
            const x = lx(gr, W);
            const y = ly(e, g.y);
            if (x >= box.x0 && x <= box.x1 && y >= box.y0 && y <= box.y1) cost += 6;
          }
        }
        const inside: Box = { x0: 0, x1: W, y0: 0, y1: PLOT_H };
        cost += 20 * (l.w * l.h - overlap(box, inside));
      }
      if (cost < bestCost - 1e-9) { bestCost = cost; best = c; }
    }
    out[i] = best;
    const boxes: Box[] = widths.map((W) => {
      const px = lx(l.growth, W);
      const py = ly(l.ev_ntm, g.y);
      const x0 = best.side === 'right' ? px + best.dx : px - best.dx - l.w;
      return { x0, x1: x0 + l.w, y0: py + best.dy - l.h / 2, y1: py + best.dy + l.h / 2 };
    });
    placed.push({ i, boxes });
  }
  return out;
}

/** Data preparation: the dataset, the axis ranges, the line, the ring, the dollar sizing and the residual labels. */
export function evGrowthRows(listings: Listing[], regression: RegressionMart | null | undefined, defaults: ValuationState = VALUATION_DEFAULTS): EvGrowthData {
  const points = valuationPoints(listings);
  const both = points.filter((p) => p.ev_ntm != null && p.growth != null);
  const byTicker = new Map(points.map((p) => [p.ticker, p]));

  const gs = both.map((p) => p.growth as number);
  const xDomain = clipDomain(gs, GROWTH_STEP, 0, GROWTH_AXIS_HI_Q);
  const evs = both.map((p) => p.ev_ntm as number);
  const yDomain: [number, number] = evs.length ? [Math.min(...evs) / 1.25, Math.max(...evs) * 1.25] : [0.3, 40];
  const offAxis = both.filter((p) => (p.growth as number) > xDomain[1] || (p.growth as number) < xDomain[0])
    .map((p) => ({ ticker: p.ticker, name: p.name, growth: p.growth as number })).sort((a, b) => b.growth - a.growth);

  const latest = regression?.latest ?? null;
  const fitted = !!latest && (latest as Record<string, unknown>).fitted === true;
  const slope = latest ? asNum((latest as Record<string, unknown>).slope) : null;
  const ci = latest ? ((latest as Record<string, unknown>).slope_ci as unknown) : null;
  const slopeCi: [number, number] | null = Array.isArray(ci) && ci.length === 2 && asNum(ci[0]) != null && asNum(ci[1]) != null ? [ci[0] as number, ci[1] as number] : null;
  const fit: FitSummary | null = latest ? {
    n: latest.n ?? null, nUs: asNum((latest as Record<string, unknown>).n_us), nCn: asNum((latest as Record<string, unknown>).n_cn), r2: latest.r2 ?? null, adjR2: latest.adj_r2 ?? null, adjR2NoLayer: latest.adj_r2_no_layer ?? null,
    slope, slopeCi, asOf: asStr((latest as Record<string, unknown>).as_of),
  } : null;

  // the residuals joined to their listing: the fitted multiple gives the line's intercept, the top five give the labels
  const residuals = (regression?.residuals ?? []).flatMap((r) => {
    const ticker = asStr(r.ticker);
    const p = ticker ? byTicker.get(ticker) : undefined;
    const fittedEv = asNum(r.fitted);
    const residual = asNum(r.residual);
    if (!ticker || !p || p.growth == null || p.ev_ntm == null || fittedEv == null || fittedEv <= 0 || residual == null) return [];
    return [{ p, fittedEv, residual, top5: r.top5 === true }];
  });
  const population = latest ? (latest as Record<string, unknown>).population : null;
  const fitTickers = (Array.isArray(population) ? population.filter((t): t is string => typeof t === 'string') : residuals.map((r) => r.p.ticker))
    .filter((t) => both.some((p) => p.ticker === t));
  let line: EvGrowthData['line'] = null;
  if (fitted && slope != null && residuals.length >= 3) {
    const c = mean(residuals.map((r) => Math.log(r.fittedEv) - slope * (r.p.growth as number)));
    const g = residuals.map((r) => r.p.growth as number);
    const [g0, g1] = [Math.min(...g), Math.max(...g)];
    line = [g0, g1].map((growth) => ({ growth: Math.round(growth * 1e4) / 1e4, ev_ntm: Math.round(Math.exp(c + slope * growth) * 1e4) / 1e4 }));
  }

  const usd = both.map((p) => p.ev_usd).filter((v): v is number => v != null);
  let size: EvGrowthData['size'] = null;
  if (usd.length) {
    const lo = Math.min(...usd);
    const hi = Math.max(...usd);
    // legend entries: every second power of ten counting down from the largest, at most three, none below the smallest dot
    const legend: number[] = [];
    for (let e = Math.floor(Math.log10(hi)); e >= Math.ceil(Math.log10(lo)) && legend.length < 3; e -= 2) legend.unshift(10 ** e);
    size = { domain: [lo, hi], legend };
  }

  // the labels, with the cheapest positions for a wide and for a narrow chart
  const top = residuals.filter((r) => r.top5);
  const base = top.map((r) => {
    const gap = Math.exp(r.residual) - 1;
    const second = `${fmtSigned(gap)} vs its model fit`;
    const short = `${r.p.ticker.replace(/\..*$/, '')} ${fmtSigned(gap)}`;
    return { r, gap, text: [shortName(r.p.name), second] as [string, string], line: `${shortName(r.p.name)} ${fmtSigned(gap)}`, short };
  });
  const geometry: Geometry = {
    x: xDomain, y: yDomain, sizeDomain: size ? size.domain : null, line,
    dots: both.map((p) => ({ ticker: p.ticker, growth: p.growth as number, ev: p.ev_ntm as number, usd: p.ev_usd })),
  };
  const wide = placeLabels(base.map((b) => ({ ticker: b.r.p.ticker, growth: b.r.p.growth as number, ev_ntm: b.r.p.ev_ntm as number, fitted: b.r.fittedEv, w: b.line.length * CHAR_W, h: LINE_H })), geometry, WIDE_PLOT_WIDTHS);
  const narrow = placeLabels(base.map((b) => ({ ticker: b.r.p.ticker, growth: b.r.p.growth as number, ev_ntm: b.r.p.ev_ntm as number, fitted: b.r.fittedEv, w: b.short.length * CHAR_W, h: LINE_H })), geometry, NARROW_PLOT_WIDTHS);
  const labels: ResidualLabel[] = base.map((b, i) => ({
    ticker: b.r.p.ticker, name: b.r.p.name, growth: b.r.p.growth as number, ev_ntm: b.r.p.ev_ntm as number, residual: b.r.residual,
    fitted: Math.round(b.r.fittedEv * 1e4) / 1e4, gap: b.gap, text: b.text, line: b.line, short: b.short,
    layer: b.r.p.layer, ring: b.r.p.ring, market: b.r.p.market, purity: b.r.p.purity, cov3: b.r.p.cov3,
    wide: wide[i], narrow: narrow[i],
  }));

  return {
    points,
    plotted: both.length,
    empty: both.length === 0,
    x: { domain: xDomain },
    y: { domain: yDomain, ticks: logTicks(yDomain[0], yDomain[1]) },
    offAxis,
    line,
    fit,
    fitted,
    fitReason: latest ? asStr((latest as Record<string, unknown>).reason) : 'the regression mart has no latest week',
    fitTickers,
    labels,
    missing: missingMultiples(listings),
    noGrowth: points.filter((p) => p.ring === 'A' && p.ev_ntm != null && p.growth == null).length,
    asOf: snapshotDate(listings),
    nUsd: usd.length,
    nConverted: both.filter((p) => p.ev_usd != null && p.ccy !== 'USD').length,
    noRate: both.filter((p) => p.ev_usd == null).map((p) => ({ ticker: p.ticker, name: p.name, ccy: p.ccy })),
    fxAsOf: fxAsOf(),
    size,
    defaults,
  };
}

/** The pair of halves a finding compares: companies growing faster than the view's median growth, and the rest. */
export function growthHalves(points: ValuationPoint[]) {
  const rows = points.filter((p) => p.ev_ntm != null && p.growth != null);
  const cut = median(rows.map((p) => p.growth as number));
  if (cut == null) return null;
  const fast = rows.filter((p) => (p.growth as number) > cut).map((p) => p.ev_ntm as number);
  const slow = rows.filter((p) => (p.growth as number) <= cut).map((p) => p.ev_ntm as number);
  const mf = median(fast);
  const ms = median(slow);
  return mf == null || ms == null ? null : { n: rows.length, cut, fast: mf, slow: ms, nFast: fast.length, nSlow: slow.length };
}

/**
 * Title: what the multiples of the faster-growing half and the slower-growing half of the view say, in words the numbers support. It says
 * nothing about layers (the layer reading is in the notes). Computed from the rows in the view (`state`); the direction word is read off
 * the two medians, never typed. A view with fewer than MIN_COMPARE_N listings is said to be too small to compare.
 */
export function evGrowthFinding(data: EvGrowthData, state: ValuationState = data.defaults): string {
  const view = viewOf(data.points, state).filter((p) => p.ev_ntm != null && p.growth != null);
  if (!view.length) return 'No listed company in this view has both a multiple and a growth estimate';
  if (view.length < MIN_COMPARE_N) return `Too few to compare: ${plural(view.length, 'listed company', 'listed companies')} in this view ${view.length === 1 ? 'has' : 'have'} both a multiple and a growth estimate`;
  const h = growthHalves(view);
  if (!h) return 'No listed company in this view has both a multiple and a growth estimate';
  const f = h.fast.toFixed(1);
  const s = h.slow.toFixed(1);
  const lead = `Of ${h.n} listed companies with a security business, the faster-growing half (expected growth above ${fmtPct0(h.cut)})`;
  if (f === s) return `${lead} trades at the same median ${fmtTimes(h.fast)} revenue as the slower half`;
  return `${lead} trades at a median ${fmtTimes(h.fast)} revenue, ${h.fast > h.slow ? 'above' : 'below'} the ${fmtTimes(h.slow)} of the slower half`;
}

/** What the layer groups do to adjusted R-squared in the page's own model, in words that follow the sign of the number and claim no more than the sample shows. */
export function layerGainSentence(fit: FitSummary): string | null {
  if (fit.adjR2 == null || fit.adjR2NoLayer == null) return null;
  const withL = fit.adjR2.toFixed(2);
  const without = fit.adjR2NoLayer.toFixed(2);
  if (withL === without) return `Adding the layer groups leaves adjusted R² unchanged at ${withL}, so the layers add no visible fit in this sample.`;
  return fit.adjR2 > fit.adjR2NoLayer
    ? `Adding the layer groups raises adjusted R² from ${without} to ${withL}, so the layers add some visible fit in this sample.`
    : `Adding the layer groups lowers adjusted R² from ${without} to ${withL}, so the layers add no visible fit in this sample.`;
}

/** The slope as a ratio per ten points of growth, with its interval. */
export function slopeRatio(fit: FitSummary): { ratio: number; lo: number | null; hi: number | null } | null {
  if (fit.slope == null) return null;
  const f = (b: number) => Math.exp(b * GROWTH_STEP);
  return { ratio: f(fit.slope), lo: fit.slopeCi ? f(fit.slopeCi[0]) : null, hi: fit.slopeCi ? f(fit.slopeCi[1]) : null };
}

/** Subtitle: metric, unit, scale and what the size is, in one sentence. Marks, the line, the sizing and what is left out are in `evGrowthNotes`. */
export function evGrowthSubtitle(data: EvGrowthData): string {
  if (data.empty) return 'Enterprise value over next-twelve-month revenue (log scale) against expected growth. No listing has both on this snapshot.';
  return `Enterprise value over next-twelve-month revenue (log scale) against expected growth, next-twelve-month revenue over trailing-twelve-month revenue minus one${data.size ? '; dot area follows enterprise value in US dollars' : ''}.`;
}

/**
 * What a reader needs beyond the subtitle, as plain text (no markup): paragraphs separated by a blank line. The page puts it in the figure's
 * "How to read this" block. Every number is computed from the marts and the facts.
 */
export function evGrowthNotes(data: EvGrowthData): string {
  if (data.empty) return 'No listing has both a multiple and a growth estimate on this snapshot, so there is nothing to plot.';
  const paras: string[] = [];
  const few = numberWord(MIN_ANALYSTS);
  paras.push([
    `Each dot is a listed company with both a multiple and a growth estimate (${data.plotted} in the full pool; the title counts the view the controls select). Shape is the listing market.`,
    `Filled blue is a company whose whole business is security, hollow grey one whose main line of business is.`,
    `A dashed outline and a lighter fill mean fewer than ${few} analysts behind the multiple: Yahoo's revenue analysts, or East Money's brokers for an A-share.`,
    data.fit && data.fitted ? `A ring marks the ${data.fitTickers.length} companies the regression was fitted on.` : '',
  ].filter(Boolean).join(' '));
  if (data.size) {
    const bits: string[] = [];
    const dollar = data.nUsd - data.nConverted;
    bits.push(`Dot area follows the square root of enterprise value in US dollars: ${data.nUsd} dots are sized, ${dollar} quoted in dollars${data.nConverted ? ` and ${data.nConverted} converted at the FRED exchange rate of their currency${data.fxAsOf ? `, read on ${fmtDay(data.fxAsOf)}` : ''}` : ''}.`);
    if (data.noRate.length) {
      const one = data.noRate.length === 1;
      bits.push(`${andList(data.noRate.map((n) => `${shortName(n.name)} (${n.ccy})`))} ${one ? 'has' : 'have'} no rate on file and ${one ? 'is' : 'are'} drawn at the smallest size.`);
    }
    bits.push('Multiples are ratios inside one currency and are never converted.');
    paras.push(bits.join(' '));
  } else {
    paras.push('No listing has an enterprise value in US dollars, so dots are one size. Multiples are ratios inside one currency and are never converted.');
  }
  if (data.fit && data.fitted) {
    const f = data.fit;
    const ratio = slopeRatio(f);
    const bits: string[] = [];
    bits.push(`The line is the growth slope of the page's own model${f.n != null ? ` (${f.n} companies${f.adjR2 != null ? `, adjusted R² ${f.adjR2.toFixed(2)}` : ''})` : ''}, drawn through the sample means with the model's other terms (free-cash-flow margin, listing market and layer groups) at their average${data.line ? '' : ' (not drawn: too few fitted points)'}. It is not one of the registered readings.${f.nUs != null && f.nCn != null ? ` The line holds the listing market at the sample's mix (${plural(f.nUs, 'US company', 'US companies')} and ${plural(f.nCn, 'A-share')}), so it is not the line of either market alone.` : ''}`);
    if (ratio) {
      const ci = ratio.lo != null && ratio.hi != null ? ` (95% interval ${ratio.lo.toFixed(1)} to ${ratio.hi.toFixed(1)})` : '';
      bits.push(`With those terms held fixed, each ${numberWord(Math.round(GROWTH_STEP * 100))} points of expected growth goes with a multiple ${ratio.ratio.toFixed(1)} times higher${ci}.`);
    }
    paras.push(bits.join(' '));
    const gain = layerGainSentence(f);
    const q1 = baselineReading('Q1');
    const q1Sentence = q1 === 'undecidable'
      ? 'Q1, the registered test of the layer question, reads undecidable on the baseline: this sample cannot rule a layer effect out.'
      : q1 ? `Q1, the registered test of the layer question, reads ${q1} on the baseline.` : '';
    if (gain || q1Sentence) paras.push([gain, q1Sentence].filter(Boolean).join(' '));
    if (data.labels.length) {
      paras.push(`The ${numberWord(data.labels.length)} largest gaps are named. Each is measured against that company's own fitted multiple from the full model, the end of the short rule beside its dot, and not against the line, which is the growth slope only. A gap measures distance from this sample's fit; it does not say the shares are cheap or dear, and the model has not been tested on new data.`);
    }
  } else {
    paras.push(`The page's own regression is not fitted on this snapshot${data.fitReason ? ` (${String(data.fitReason).replace(/_/g, ' ')})` : ''}, so no line is drawn.`);
  }
  const left: string[] = [];
  const miss = missingMultipleNote(data.missing);
  if (miss) left.push(miss);
  if (data.noGrowth) left.push(`${data.noGrowth} with a multiple but no growth estimate`);
  const tail: string[] = [];
  if (left.length) tail.push(`Not plotted: ${left.join('; ')}.`);
  if (data.offAxis.length) tail.push(`Beyond the growth axis (${fmtPct0(data.x.domain[1])}): ${data.offAxis.map((o) => `${o.name} ${fmtPct0(o.growth)}`).join(', ')}.`);
  if (tail.length) paras.push(tail.join(' '));
  return paras.join('\n\n');
}

const tooltip = [
  { field: 'name', type: 'nominal', title: 'Company' },
  { field: 'ticker', type: 'nominal', title: 'Listing' },
  { field: 'ev_ntm', type: 'quantitative', title: 'EV / NTM revenue', format: '.1f' },
  { field: 'growth', type: 'quantitative', title: 'Expected growth', format: '.1%' },
  { field: 'fcf', type: 'quantitative', title: 'FCF margin', format: '.1%' },
  { field: 'ev_text', type: 'nominal', title: 'Enterprise value (US$)' },
  { field: 'ccy', type: 'nominal', title: 'Quote currency' },
  { field: 'n', type: 'quantitative', title: 'Analysts behind the multiple' },
  { field: 'cov_label', type: 'nominal', title: 'Coverage' },
  { field: 'exposure', type: 'nominal', title: 'Exposure' },
  { field: 'status', type: 'nominal', title: 'Status' },
];

/** The Vega-Lite spec. Driven by the five params of the `valuation` group; `opts.defaults` sets their initial values. */
export function evGrowthSpec(data: EvGrowthData, opts: { defaults?: ValuationState } = {}): Spec {
  if (data.empty) return emptySpec('No listing has both a multiple and a growth estimate on this snapshot, so there is nothing to plot.');
  const defaults = opts.defaults ?? data.defaults;
  const exposures = [EXPOSURE_WHOLE, EXPOSURE_MAIN];
  const narrow = `width < ${NARROW_PLOT}`;
  const x = {
    field: 'growth', type: 'quantitative',
    scale: { domain: data.x.domain, nice: false },
    axis: { title: 'Expected revenue growth', labelExpr: "replace(format(datum.value, '.0%'), '-', '−')", tickCount: { expr: `${narrow} ? 4 : 6` }, grid: false },
  };
  const y = {
    field: 'ev_ntm', type: 'quantitative',
    scale: { type: 'log', domain: data.y.domain, nice: false },
    axis: { title: 'EV / NTM revenue (x, log scale)', values: data.y.ticks, labelExpr: "datum.value + 'x'", grid: true },
  };
  const shapeScale = { domain: [...MARKETS], range: MARKETS.map((m) => MARKET_SHAPE[m]) };
  const legend = { title: null, orient: 'top', direction: 'vertical', labelFontSize: 11, labelLimit: 0, rowPadding: 2 };
  // a listing whose currency has no rate has no dollar value: it takes the smallest size of the scale (a null would drop the dot)
  const sizeField = data.size
    ? {
        field: 'ev_size', type: 'quantitative',
        scale: { type: 'sqrt', domain: data.size.domain, range: SIZE_RANGE },
        legend: {
          ...legend, orient: 'bottom', direction: 'horizontal', columnPadding: 12, title: 'Enterprise value (US$)', titleLimit: 0, values: data.size.legend, labelExpr: USD_LABEL_EXPR,
          symbolFillColor: tok('ink-3'), symbolStrokeColor: tok('ink-3'), symbolOpacity: 0.6, titleFontSize: 11, titleColor: tok('ink-2'),
        },
      }
    : { value: 60 };

  const keyEntries: KeyEntry[] = [
    ...MARKETS.map((m) => ({ label: MARKET_LABEL[m], shape: MARKET_SHAPE[m], fill: tok('ink-3'), stroke: tok('ink-3') })),
    { label: EXPOSURE_WHOLE, fill: series.a, stroke: series.a },
    { label: EXPOSURE_MAIN, stroke: series.neutral },
    ...(data.fit && data.fitted && data.fitTickers.length ? [{ label: `In the page's own fit (${data.fitTickers.length} companies)`, fill: 'transparent', stroke: tok('ink'), strokeWidth: 1.3, size: 190 }] : []),
    { label: COV_THIN_LABEL, fill: tok('ink-3'), fillOpacity: THIN_OPACITY, stroke: tok('ink-3'), dash: [...THIN_DASH] },
    ...(data.line ? [{ label: 'Growth slope, other terms|at their mean', shape: 'stroke', stroke: tok('ink-2'), strokeWidth: 1.8, size: 420 }] : []),
    ...(data.labels.length ? [{ label: 'Rule and %: gap to own model fit', shape: 'M0,-1L0,1', stroke: tok('ink-2'), strokeWidth: 1.4, size: 200 }] : []),
  ];

  const layers: Spec[] = [
    {
      data: { values: [{ growth: 0 }] },
      mark: { type: 'rule', color: tok('border'), strokeDash: [3, 3], aria: false },
      encoding: { x },
    },
  ];
  if (data.line) {
    const end = data.line[data.line.length - 1];
    layers.push({
      data: { values: data.line },
      mark: { type: 'line', color: tok('ink-2'), strokeWidth: 1.8, clip: true, aria: false },
      encoding: { x, y },
    });
    layers.push({
      // the short name of the line, inside the plot: right-aligned at the end of the line and lifted above it, so it never runs past the edge
      data: { values: [end] },
      mark: { type: 'text', align: 'right', baseline: 'bottom', dx: -4, dy: -5, fontSize: 10, color: tok('ink-2') },
      encoding: { x, y, text: { value: 'growth slope' } },
    });
  }
  if (data.fit && data.fitted && data.fitTickers.length) {
    // the ring: the same shape as the dot, a little larger, so the 23 companies of the fit can be found; the size is computed with the dot's own square-root scale
    const [s0, s1] = SIZE_RANGE;
    const area = data.size
      ? `${s0} + (${s1 - s0}) * (sqrt(datum.ev_usd != null ? datum.ev_usd : ${data.size.domain[0]}) - ${Math.sqrt(data.size.domain[0])}) / ${Math.sqrt(data.size.domain[1]) - Math.sqrt(data.size.domain[0])}`
      : '60';
    layers.push({
      data: { values: data.points },
      transform: [
        { filter: 'datum.ev_ntm != null && datum.growth != null' },
        { filter: { field: 'ticker', oneOf: data.fitTickers } },
        { filter: FILTER_EXPR },
        { calculate: `PI * pow(sqrt((${area}) / PI) + ${RING_GAP}, 2)`, as: 'ring_size' },
      ],
      mark: { type: 'point', filled: false, strokeWidth: 1.2, color: tok('ink'), clip: true, aria: false },
      encoding: {
        x, y,
        shape: { field: 'market', type: 'nominal', scale: shapeScale, legend: null },
        size: { field: 'ring_size', type: 'quantitative', scale: null, legend: null },
      },
    });
  }
  if (data.labels.length) {
    // from each named dot to its own fitted multiple: a short rule and a tick at the fitted value
    layers.push({
      data: { values: data.labels },
      transform: [{ filter: FILTER_EXPR }],
      mark: { type: 'rule', strokeWidth: 1.2, color: tok('ink-2'), clip: true, aria: false },
      encoding: { x, y, y2: { field: 'fitted' } },
    });
    layers.push({
      data: { values: data.labels },
      transform: [{ filter: FILTER_EXPR }],
      mark: { type: 'point', shape: 'stroke', size: 130, strokeWidth: 1.6, filled: false, color: tok('ink-2'), clip: true, aria: false },
      encoding: { x, y: { ...y, field: 'fitted' } },
    });
  }
  layers.push({
    // the dataset is `data.points` untouched, so figure 3 can share it; exposure, the dollar size and the tooltip texts are derived here
    data: { values: data.points },
    transform: [
      { filter: 'datum.ev_ntm != null && datum.growth != null' },
      { filter: FILTER_EXPR },
      { calculate: `datum.purity == 'high' ? ${JSON.stringify(EXPOSURE_WHOLE)} : ${JSON.stringify(EXPOSURE_MAIN)}`, as: 'exposure' },
      { calculate: COV_LABEL_EXPR, as: 'cov_label' },
      { calculate: USD_TEXT_EXPR, as: 'ev_text' },
      ...(data.size ? [{ calculate: `datum.ev_usd != null ? datum.ev_usd : ${data.size.domain[0]}`, as: 'ev_size' }] : []),
    ],
    mark: { type: 'point', filled: true, strokeWidth: 1.7, clip: true },
    encoding: {
      x, y,
      shape: { field: 'market', type: 'nominal', scale: shapeScale, legend: null },
      size: sizeField,
      fill: { field: 'exposure', type: 'nominal', scale: { domain: exposures, range: [series.a, tok('bg')] }, legend: null },
      stroke: { field: 'exposure', type: 'nominal', scale: { domain: exposures, range: [series.a, series.neutral] }, legend: null },
      // thin coverage: a dashed outline and a lighter fill; the outline keeps full opacity so it keeps its contrast
      fillOpacity: { condition: { test: '!datum.cov3', value: THIN_OPACITY }, value: 1 },
      strokeDash: { condition: { test: '!datum.cov3', value: [...THIN_DASH] }, value: [1, 0] },
      tooltip,
    },
  });
  layers.push(emptyViewLayer(data.points, `datum.ev_ntm != null && datum.growth != null && ${FILTER_EXPR}`, 'No listed company in this view has both a multiple and a growth estimate.'));
  if (data.offAxis.length) {
    // companies beyond the growth axis are counted on the plot (the view's own count), and named in the notes
    layers.push({
      data: { values: data.points },
      transform: [
        { filter: 'datum.ev_ntm != null && datum.growth != null' },
        { filter: FILTER_EXPR },
        { filter: `datum.growth > ${data.x.domain[1]}` },
        { joinaggregate: [{ op: 'count', as: 'n_off' }] },
        { window: [{ op: 'row_number', as: 'rn_off' }] },
        { filter: 'datum.rn_off == 1' },
        { calculate: `'→ ' + datum.n_off + ' more beyond ${fmtPct0(data.x.domain[1])}' + (${narrow} ? '' : ' growth, named in the notes')`, as: 'off_text' },
      ],
      mark: { type: 'text', align: 'right', baseline: 'bottom', dx: -4, dy: -4, fontSize: 10, color: tok('ink-2') },
      encoding: { x: { value: { expr: 'width' } }, y: { value: { expr: 'height' } }, text: { field: 'off_text', type: 'nominal' } },
    });
  }
  // the five largest gaps, named. Wide charts: name and percent; narrow charts: ticker and percent. Each label sits where
  // `placeLabels` put it (left or right of its dot, nudged by `dy`); a layer for each label and variant, as a mark's alignment is not a field.
  const variants = [
    { key: 'wide' as const, width: `width >= ${NARROW_PLOT}`, field: 'line', size: 11 },
    { key: 'narrow' as const, width: narrow, field: 'short', size: 10 },
  ];
  for (const v of variants) {
    for (const l of data.labels) {
      const p = l[v.key];
      layers.push({
        data: { values: [l] },
        transform: [{ filter: v.width }, { filter: FILTER_EXPR }],
        mark: { type: 'text', align: p.side === 'right' ? 'left' : 'right', baseline: 'middle', dx: p.side === 'right' ? p.dx : -p.dx, dy: p.dy, fontSize: v.size, lineHeight: 13, color: tok('ink') },
        encoding: { x, y, text: { field: v.field, type: 'nominal' } },
      });
    }
  }
  layers.push(keyLayer(keyEntries));
  return {
    $schema: VL_SCHEMA,
    height: PLOT_H,
    params: valuationParams(defaults),
    resolve: { scale: KEY_SCALES },
    layer: layers,
  };
}
