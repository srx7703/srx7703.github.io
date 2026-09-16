import midterms from '@data/facts/midterms.json';
import fomc from '@data/facts/fomc.json';
import saas from '@data/facts/saas.json';
import statarb from '@data/facts/statarb.json';
import finllm from '@data/facts/finllm.json';
import { pct } from './fmt';

/** One headline number per project for the cards, always read from facts. */
export const cardKpis: Record<string, { label: string; value: string }> = {
  'midterms-2026': { label: 'Democrats win the House, Polymarket', value: pct(midterms.headline.house_dem.polymarket, 1) },
  'fomc-markets': fomc.next_meeting?.platforms?.polymarket?.modal_prob != null
    ? { label: `modal outcome at the next decision (${fomc.next_meeting.platforms.polymarket.modal_side}), Polymarket`, value: pct(fomc.next_meeting.platforms.polymarket.modal_prob, 1) }
    : { label: 'decision markets tracked', value: String(fomc.decision_markets) },
  'saas-benchmark': { label: 'median revenue growth, TTM', value: pct(saas.median_rev_growth, 1) },
  'statarb-2019-2020': { label: `best stat-arb Sharpe (${statarb.best_statarb.strategy}) vs ${statarb.buy_and_hold.sharpe.toFixed(2)} buy-and-hold`, value: statarb.best_statarb.sharpe.toFixed(2) },
  'financial-llm-sec': { label: 'BERTScore F1 gain on Gemma 4 with the SEC adapter', value: `+${finllm.deltas_pct.gemma4_base_to_v2}%` },
};
