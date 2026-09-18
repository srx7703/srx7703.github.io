/**
 * How recently each live project's data actually ran, for the note in the home page margin.
 *
 * The note used to be a hand-written list of three projects. Two live projects were added after it
 * and nobody remembered to add them, so the home page quietly claimed the site had three live feeds
 * when it had five. A registry is still a list someone has to keep, so `tests/test_home_freshness.py`
 * fails the build if a project whose status is `live` is missing an entry here.
 *
 * Each entry names the facts file and, in order of preference, the keys that hold the moment the data
 * was captured — not the moment the page was rendered. `generated_at` is the last resort because a
 * rebuild triggered by something else would refresh it without any new data behind it.
 */

import fomc from '@data/facts/fomc.json';
import midterms from '@data/facts/midterms.json';
import saas from '@data/facts/saas.json';
import power from '@data/facts/power_demand.json';
import optical from '@data/facts/valuation_optical.json';
import ssb from '@data/facts/valuation_ssb.json';

type Facts = Record<string, unknown>;

export type Feed = {
  /** the project id, which is also its page slug — used by the test that keeps this list complete */
  id: string;
  label: string;
  cadence: string;
  facts: Facts;
  /** timestamp keys in order of preference */
  keys: string[];
};

export const FEEDS: Feed[] = [
  { id: 'midterms-2026', label: 'Midterms', cadence: 'twice daily', facts: midterms, keys: ['last_snapshot_ts'] },
  { id: 'fomc-markets', label: 'FOMC', cadence: 'twice daily', facts: fomc, keys: ['last_snapshot_ts'] },
  { id: 'saas-benchmark', label: 'SaaS', cadence: 'weekly', facts: saas, keys: ['last_refresh', 'generated_at'] },
  {
    id: 'optical-modules-valuation',
    label: 'Optical',
    cadence: 'daily; consensus weekly',
    facts: optical,
    keys: ['as_of_price', 'generated_at'],
  },
  {
    id: 'datacenter-power',
    label: 'Data-center power',
    cadence: 'weekly',
    // The generator inventory is monthly and the retail sales file is monthly, so the honest stamp
    // is when the pipeline last ran rather than a snapshot time that would imply daily data.
    facts: power,
    keys: ['generated_at'],
  },
  {
    id: 'solid-state-battery-valuation',
    label: 'Solid-state',
    cadence: 'daily; consensus weekly',
    facts: ssb,
    keys: ['as_of_price', 'generated_at'],
  },
];

/** The first key that actually holds a timestamp, or null if the facts file has none of them. */
export function stampOf(feed: Feed): string | null {
  for (const k of feed.keys) {
    const v = feed.facts[k];
    if (typeof v === 'string' && v) return v;
  }
  return null;
}

/** Feeds that have a timestamp, newest first, so a stalled pipeline sinks rather than hides. */
export function feedStamps(): { label: string; cadence: string; ts: string }[] {
  return FEEDS.map((f) => ({ label: f.label, cadence: f.cadence, ts: stampOf(f) }))
    .filter((f): f is { label: string; cadence: string; ts: string } => f.ts != null)
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
}
