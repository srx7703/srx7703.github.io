import midterms from '@data/facts/midterms.json';
import fomc from '@data/facts/fomc.json';
import saas from '@data/facts/saas.json';
import statarb from '@data/facts/statarb.json';
import finllm from '@data/facts/finllm.json';
import optical from '@data/facts/valuation_optical.json';
import ssb from '@data/facts/valuation_ssb.json';
import memcycle from '@data/facts/memcycle.json';
import payments from '@data/facts/payments.json';
import { pct } from './fmt';

type PaymentsKpi = { id: string; label: string; value: number; qualifier: string; unit: string; period: string };

/** Payments facts carry a unit per KPI; format by unit and keep the qualifier (">", "<", "~") in front. */
function paymentsKpi(): { label: string; value: string } {
  const kpis = payments.kpis as PaymentsKpi[];
  const k = kpis.find((x) => x.id === 'affirm_kept_per_100') ?? kpis[0];
  if (!k) return { label: 'core companies covered', value: String(payments.counts.roster_core) };
  const q = k.qualifier === '=' ? '' : `${k.qualifier}\u202f`;
  let v: string;
  if (k.unit === 'usd_per_100') v = `$${k.value.toFixed(2)}`;
  else if (k.unit.startsWith('share')) v = pct(k.value, 1);
  else if (k.unit === 'USD') {
    const [d, s] = k.value >= 1e12 ? [1e12, 'tn'] : k.value >= 1e9 ? [1e9, 'bn'] : [1e6, 'm'];
    v = `$${(k.value / d).toFixed(1)}${s}`;
  } else v = String(k.value);
  return { label: `${k.label.charAt(0).toLowerCase()}${k.label.slice(1)}, ${k.period}`, value: `${q}${v}` };
}

/** One headline number per project for the cards, always read from facts. */
export const cardKpis: Record<string, { label: string; value: string }> = {
  'midterms-2026': { label: 'Democrats win the House, Polymarket', value: pct(midterms.headline.house_dem.polymarket, 1) },
  'fomc-markets': fomc.next_meeting?.platforms?.polymarket?.modal_prob != null
    ? { label: `modal outcome at the next decision (${fomc.next_meeting.platforms.polymarket.modal_side}), Polymarket`, value: pct(fomc.next_meeting.platforms.polymarket.modal_prob, 1) }
    : { label: 'decision markets tracked', value: String(fomc.decision_markets) },
  'saas-benchmark': { label: 'median revenue growth, TTM', value: pct(saas.median_rev_growth, 1) },
  'statarb-2019-2020': { label: `best stat-arb Sharpe (${statarb.best_statarb.strategy}) vs ${statarb.buy_and_hold.sharpe.toFixed(2)} buy-and-hold`, value: statarb.best_statarb.sharpe.toFixed(2) },
  'financial-llm-sec': { label: 'BERTScore F1 gain on Gemma 4 with the SEC adapter', value: `+${finllm.deltas_pct.gemma4_base_to_v2.toFixed(1)}%` },
  'optical-modules-valuation': optical.median_fwd_pe_this != null
    ? { label: `median forward PE, calendar ${optical.years[0]}`, value: `${optical.median_fwd_pe_this.toFixed(1)}x` }
    : { label: 'listings tracked', value: String(optical.n_listings) },
  'solid-state-battery-valuation': ssb.median_fwd_pe_this != null
    ? { label: `median forward PE, calendar ${ssb.years[0]}`, value: `${ssb.median_fwd_pe_this.toFixed(1)}x` }
    : { label: 'listings tracked', value: String(ssb.n_listings) },
  'memory-cycles': {
    label: `of pure-play stocks peaked before DRAM contract prices; registered test ${memcycle.q3.main_pure.verdict}`,
    value: pct(memcycle.q3.main_pure.share_lead_ge1, 1),
  },
  'payments-landscape': paymentsKpi(),
};
