/**
 * Build-time mini charts for the project rows: one small glyph per project, computed from the same marts and
 * facts the pages use, so they refresh with every data run. Rendered as inline SVG by MiniChart.astro.
 */
import midtermsSeries from '@data/marts/predmarkets/midterms_headline.json';
import fomcHistory from '@data/marts/predmarkets/fomc_history.json';
import fomcFacts from '@data/facts/fomc.json';
import saasLatest from '@data/marts/sec/saas_latest.json';
import tcRows from '@data/marts/statarb/tc_sensitivity.json';
import finllm from '@data/facts/finllm.json';
import opticalCompanies from '@data/marts/valuation/optical_companies.json';
import ssbCompanies from '@data/marts/valuation/ssb_companies.json';
import memIndex from '@data/marts/memcycle/price_index.json';
import takeRates from '@data/marts/payments/take_rates.json';
import cyberListings from '@data/marts/cyber/listings.json';
import adShare from '@data/marts/admarket/video_share.json';
import type { Listing } from '@charts/cyber/types';

export type Pt = [number, number];
export type MiniSpec =
  | { kind: 'lines'; series: { name: string; color: string; points: Pt[] }[]; y: [number, number]; label: string }
  | { kind: 'scatter'; points: { x: number; y: number; hit: boolean }[]; x: [number, number]; y: [number, number]; line?: { a: number; b: number }; label: string }
  | { kind: 'bars'; bars: { v: number; color: string }[]; y: [number, number]; label: string };

type Row = { snapshot_ts: string; key: string; platform: string; prob: number | null };
type Hist = { meeting: string; platform: string; date: string; side: string; prob: number };

const byTime = (a: Pt, b: Pt) => a[0] - b[0];

function midterms(): MiniSpec {
  const rows = (midtermsSeries as Row[]).filter((r) => r.key === 'house_dem' && r.prob != null);
  const series = ['polymarket', 'kalshi'].map((p) => ({
    name: p, color: `--series-${p}`,
    points: rows.filter((r) => r.platform === p).map((r): Pt => [Date.parse(r.snapshot_ts), r.prob as number]).sort(byTime),
  }));
  const n = new Set(rows.map((r) => r.snapshot_ts)).size;
  return { kind: 'lines', series, y: [0, 1], label: `Probability that Democrats win the House, Polymarket and Kalshi, ${n} snapshots` };
}

function fomc(): MiniSpec {
  const meeting = fomcFacts.next_meeting?.meeting;
  const rows = (fomcHistory as Hist[]).filter((r) => r.meeting === meeting && r.side === 'hike');
  const series = ['polymarket', 'kalshi'].map((p) => ({
    name: p, color: `--series-${p}`,
    points: rows.filter((r) => r.platform === p).map((r): Pt => [Date.parse(r.date), r.prob]).sort(byTime),
  }));
  return { kind: 'lines', series, y: [0, 1], label: `Probability of a hike at the ${meeting} FOMC meeting over time, Polymarket and Kalshi` };
}

function saas(): MiniSpec {
  const pts = (saasLatest as { rev_growth: number | null; fcf_margin: number | null; rule_of_40: number | null }[])
    .filter((r) => r.rev_growth != null && r.fcf_margin != null)
    .map((r) => ({ x: r.rev_growth as number, y: r.fcf_margin as number, hit: (r.rule_of_40 ?? -Infinity) >= 40 }));
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const pad = (lo: number, hi: number): [number, number] => { const d = (hi - lo) * 0.08; return [lo - d, hi + d]; };
  return {
    kind: 'scatter', points: pts, x: pad(Math.min(...xs), Math.max(...xs)), y: pad(Math.min(...ys), Math.max(...ys)),
    line: { a: 0.4, b: -1 }, // Rule of 40: growth + FCF margin = 40%
    label: `Revenue growth versus free-cash-flow margin for ${pts.length} software companies, with the Rule-of-40 line`,
  };
}

function statarb(): MiniSpec {
  const rows = tcRows as { strategy: string; tc_bps: number; sharpe: number }[];
  const colors: Record<string, string> = { OLS: '--series-a', PCA: '--series-b', LASSO: '--series-c' };
  const series = ['OLS', 'PCA', 'LASSO'].map((s) => ({
    name: s, color: colors[s], points: rows.filter((r) => r.strategy === s).map((r): Pt => [r.tc_bps, r.sharpe]).sort(byTime),
  }));
  const ys = rows.map((r) => r.sharpe);
  return { kind: 'lines', series, y: [Math.min(...ys, 0), Math.max(...ys, 0)], label: 'Out-of-sample Sharpe ratio versus one-way transaction cost for OLS, PCA and LASSO' };
}

function finllmBars(): MiniSpec {
  const byKey = Object.fromEntries(finllm.summary.map((s) => [s.key, s.f1]));
  const bars = [
    { v: byKey.base, color: '--series-neutral' }, { v: byKey.v2, color: '--series-a' },
    { v: byKey.base_gemma4, color: '--series-neutral' }, { v: byKey.v2_gemma4, color: '--series-a' },
  ];
  return { kind: 'bars', bars, y: [0, 1], label: 'BERTScore F1 for base and fine-tuned Gemma 2 27B and Gemma 4 31B' };
}

type ValRow = { ticker: string; purity: string; fwd_pe_2026: number | null; growth: number | null };

/** Forward PE against expected growth, the same view the page leads with, shrunk to a glyph. */
function valuationScatter(rows: ValRow[], label: string): MiniSpec {
  const pts = rows
    .filter((r) => r.fwd_pe_2026 != null && r.growth != null)
    .map((r) => ({ x: r.growth as number, y: Math.log10(r.fwd_pe_2026 as number), hit: r.purity === 'high' }));
  if (!pts.length) return { kind: 'scatter', points: [], x: [0, 1], y: [0, 1], label };
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const pad = (lo: number, hi: number): [number, number] => { const d = (hi - lo) * 0.1 || 1; return [lo - d, hi + d]; };
  return {
    kind: 'scatter', points: pts, x: pad(Math.min(...xs), Math.max(...xs)), y: pad(Math.min(...ys), Math.max(...ys)),
    label,
  };
}

/** The DRAM contract-price index on a log scale, the calendar the page dates. */
function memcycle(): MiniSpec {
  const rows = (memIndex as { product: string; month: string; value: number }[]).filter((r) => r.product === 'DRAM');
  const points = rows.map((r): Pt => [Date.parse(`${r.month}-01`), Math.log10(r.value)]);
  const ys = points.map((p) => p[1]);
  return {
    kind: 'lines', series: [{ name: 'DRAM', color: '--series-a', points }], y: [Math.min(...ys), Math.max(...ys)],
    label: 'DRAM export contract-price index on a log scale, monthly since the series start the rule uses',
  };
}

type TakeRow = { company: string; period: string; ratio_kind: string; as_of: string; take_rate_pct: number | null; qualifier: string; chartable: boolean; stale: boolean };

/**
 * Gross take rate (revenue over the company's own volume) per reported period for one acquirer, one BNPL lender
 * and one B2B payments company, one per colour. Quarterly rows only (an annual row would sit among the quarters), chartable, current and exact (=); never summed or compared
 * across definitions, a glyph of how differently each layer prices.
 */
function payments(): MiniSpec {
  const picks: [string, string][] = [['PayPal', '--series-a'], ['Affirm', '--series-b'], ['BILL', '--series-c']];
  const rows = (takeRates as TakeRow[]).filter(
    (r) => r.ratio_kind === 'gross_take_rate' && /Q\d$/.test(r.period) && r.chartable && !r.stale && r.qualifier === '=' && r.take_rate_pct != null,
  );
  const series = picks.map(([name, color]) => ({
    name, color,
    points: rows.filter((r) => r.company === name).map((r): Pt => [Date.parse(r.as_of), r.take_rate_pct as number]).sort(byTime),
  }));
  const ys = series.flatMap((s) => s.points.map((p) => p[1]));
  const y: [number, number] = ys.length ? [0, Math.max(...ys) * 1.08] : [0, 1];
  return {
    kind: 'lines', series, y,
    label: `Gross take rate, revenue as a percentage of each company's own volume, by reported quarter for ${picks.map((p) => p[0]).join(', ')}`,
  };
}

/**
 * The page's second figure shrunk to a glyph: EV over next-twelve-month revenue (log scale) against expected growth for
 * the registered listings that have both, pure plays in the series colour and the rest grey. A listing without a
 * positive multiple or a growth figure is left out (the page lists each with its reason), and growth beyond its 95th
 * percentile is drawn on the right edge so one outlier does not flatten the rest. Empty-state safe: no row, no points.
 */
function cyber(): MiniSpec {
  const rows = (cyberListings as Listing[]).filter(
    (r) => r.ring === 'A' && r.ev_ntm != null && r.ev_ntm > 0 && r.growth != null && Number.isFinite(r.growth),
  );
  if (!rows.length) {
    return { kind: 'scatter', points: [], x: [0, 1], y: [0, 1], label: 'EV over next-twelve-month revenue against expected growth for listed security companies; no listing has both yet' };
  }
  const growth = rows.map((r) => r.growth as number).sort((a, b) => a - b);
  const cap = growth[Math.min(growth.length - 1, Math.floor(0.95 * growth.length))];
  const pts = rows.map((r) => ({ x: Math.min(r.growth as number, cap), y: Math.log10(r.ev_ntm as number), hit: r.purity === 'high' }));
  const ys = pts.map((p) => p.y);
  const pad = (lo: number, hi: number): [number, number] => { const d = (hi - lo) * 0.1 || 1; return [lo - d, hi + d]; };
  return {
    kind: 'scatter', points: pts, x: pad(growth[0], cap), y: pad(Math.min(...ys), Math.max(...ys)),
    label: `EV over next-twelve-month revenue (log scale) against expected revenue growth for ${pts.length} of the priced listed security companies, pure plays highlighted; growth beyond its 95th percentile is drawn at the right edge`,
  };
}

/** Google's and Meta's quarterly share of the nine-company advertising total, the page's hero shrunk to a glyph. */
function admarket(): MiniSpec {
  const rows = adShare as { quarter: string; company: string; share: number }[];
  const date = (q: string) => Date.UTC(Number(q.slice(0, 4)), Number(q[5]) * 3 - 1, 28);
  const series = [['Google', '--series-a'], ['Meta', '--series-b']].map(([name, color]) => ({
    name, color, points: rows.filter((r) => r.company === name).map((r): Pt => [date(r.quarter), r.share]).sort(byTime),
  }));
  return { kind: 'lines', series, y: [0, 0.6], label: "Google's and Meta's quarterly share of advertising revenue among the nine listed companies" };
}

export const miniCharts: Record<string, MiniSpec> = {
  'midterms-2026': midterms(),
  'fomc-markets': fomc(),
  'saas-benchmark': saas(),
  'statarb-2019-2020': statarb(),
  'financial-llm-sec': finllmBars(),
  'optical-modules-valuation': valuationScatter(
    opticalCompanies as ValRow[],
    'Forward PE against expected earnings growth for each listed optical-module company',
  ),
  'solid-state-battery-valuation': valuationScatter(
    ssbCompanies as ValRow[],
    'Forward PE against expected earnings growth for each listed solid-state battery company',
  ),
  'memory-cycles': memcycle(),
  'payments-landscape': payments(),
  'cybersecurity-landscape': cyber(),
  'ad-market': admarket(),
};
