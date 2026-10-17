// Copy pipeline outputs (data/marts/**/*.json, data/facts/*.json) into public/data so charts can load
// them by URL. Runs automatically before `astro dev` / `astro build` (npm pre-hooks).
//
// Rights (cybersecurity page, owner decision D1): the cyber marts that carry Yahoo's parsed fields company by company
// (listings.json: price, market cap, enterprise value, target prices, ratings; targets.json; recommendations.json) are
// NOT served. They stay in the repository (data/marts/cyber/), and the build imports them from there
// (src/charts/cyber/external.ts); the deployed site offers no download of them. A directory listed in SERVED below is
// served by whitelist: a new mart in it stays private until it is added here on purpose. Every other mart directory is
// copied as before. What the page prints about "what is served" must match this list exactly.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';

/** Directory (relative to data/, with '/') -> the JSON files (without extension) served from it. */
export const SERVED = {
  'marts/cyber': new Set([
    'ledger_timeline', // the charted private ledger: curated rows with a public link (CSV download of the page)
    'reported_unconfirmed', // curated rows that are never charted
    'events', // curated deal events
    'excluded', // pool exclusions with reasons
    'metric_dictionary', // definitions
    'etf_crosscheck', // CIBR holdings against the pool (tickers and names)
  ]),
};
/** The three Yahoo-derived marts a deployed site must never serve (checked after the copy). */
const NEVER_SERVED = ['marts/cyber/listings.json', 'marts/cyber/targets.json', 'marts/cyber/recommendations.json'];

const root = resolve(process.cwd(), '..', 'data');
const out = resolve(process.cwd(), 'public', 'data');
rmSync(out, { recursive: true, force: true });
let n = 0;
const withheld = [];
const walk = (dir, rel) => {
  const allow = SERVED[rel.split(sep).join('/')];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, join(rel, name));
    else if (name.endsWith('.json')) {
      if (allow && !allow.has(name.slice(0, -'.json'.length))) { withheld.push(join(rel, name).split(sep).join('/')); continue; }
      mkdirSync(join(out, rel), { recursive: true });
      cpSync(p, join(out, rel, name));
      n++;
    }
  }
};
for (const sub of ['marts', 'facts']) if (existsSync(join(root, sub))) walk(join(root, sub), sub);
for (const f of NEVER_SERVED) if (existsSync(join(out, f))) throw new Error(`[sync-data] ${f} must not be served (Yahoo-derived, owner decision D1)`);
console.log(`[sync-data] copied ${n} json files to public/data${withheld.length ? `; withheld ${withheld.length} (not on the serve list): ${withheld.join(', ')}` : ''}`);
