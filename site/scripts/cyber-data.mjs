// Write the cybersecurity page's URL-loaded data files: the large chart datasets, the table files (pre-formatted rows for
// the lazy <UrlTable>s), the title maps of the dashboards and the metric dictionary rows, to public/data/cyber/<file>.json
// (CLAUDE.md: large marts are loaded by URL).
//
// The rows come from src/charts/cyber/external.ts (the registry the page also uses to point each chart spec at its URL) and
// from src/charts/cyber/page.ts (the arrays and title maps the page itself prints; external.ts cannot import it, so it is
// passed in here), so the page and its data files are always built from the same marts. The two modules are bundled with
// esbuild (already a dependency of Astro, via Vite) and run here, in prebuild, rather than served from an Astro endpoint:
// an endpoint would join the build's module graph and reorder the shared stylesheet chunks of every other page.
// Runs after sync-data.mjs, which clears public/data (so public/data/cyber is rebuilt every time; it never
// collides with the copied marts under public/data/marts/cyber). Deterministic: fixed file names, no timestamps.
//
// Rights (owner decision D1): the raw fields of the Yahoo-derived marts never reach a served file. The files carry derived
// measures only (multiples, growth, upside, counts); a row that has one of the raw keys below fails the prebuild.
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const ENTRY_DIR = resolve(process.cwd(), 'src', 'charts', 'cyber');
const TMP = resolve(process.cwd(), 'node_modules', '.cache', 'cyber-data.mjs');
const OUT = resolve(process.cwd(), 'public', 'data', 'cyber');

/** Raw Yahoo (and East Money market-cap) fields: not served in any file, however the table is built. */
const RAW_KEYS = new Set(['price', 'price_ts', 'market_cap', 'total_market_cap', 'enterprise_value', 'enterprise_value_usd', 'target_mean', 'target_high', 'target_low', 'recommendation_mean', 'ntm_revenue']);
/** Derived from a raw field and served because a figure draws it; named in the log so that the owner can decide. */
const WATCH_KEYS = new Set(['ev_usd']);
const rawKeysIn = (rows, set = RAW_KEYS) => {
  const found = new Set();
  const visit = (v, depth) => {
    if (depth > 3 || v == null || typeof v !== 'object') return;
    if (Array.isArray(v)) { for (const x of v) visit(x, depth + 1); return; }
    // a raw field is a bare number; a key of the same name that holds text ("price": "$1.2B", a deal's price) is something else
    for (const [k, x] of Object.entries(v)) { if (set.has(k) && (typeof x === 'number' || (typeof x === 'string' && /^-?\d+(\.\d+)?$/.test(x)))) found.add(k); visit(x, depth + 1); }
  };
  visit(rows, 0);
  return [...found];
};

mkdirSync(resolve(TMP, '..'), { recursive: true });
await build({
  stdin: {
    contents: "export * from './external.ts'; import * as page from './page.ts'; export { page };",
    resolveDir: ENTRY_DIR,
    sourcefile: 'cyber-data-entry.ts',
    loader: 'ts',
  },
  bundle: true, platform: 'node', format: 'esm', outfile: TMP, logLevel: 'warning',
});
const { dataFiles, DATA_BASE, page } = await import(pathToFileURL(TMP).href);
if (DATA_BASE !== '/data/cyber') throw new Error(`DATA_BASE ${DATA_BASE} does not match ${OUT}`);
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const files = dataFiles(page);
const bad = Object.entries(files).map(([name, rows]) => [name, rawKeysIn(rows)]).filter(([, keys]) => keys.length);
if (bad.length) throw new Error(`[cyber-data] raw Yahoo fields in served files (owner decision D1): ${bad.map(([n, k]) => `${n}: ${k.join(', ')}`).join('; ')}`);
const watch = Object.entries(files).map(([name, rows]) => [name, rawKeysIn(rows, WATCH_KEYS)]).filter(([, keys]) => keys.length);
if (watch.length) console.warn(`[cyber-data] derived-from-raw fields served for a figure (see the rights note): ${watch.map(([n, k]) => `${n}: ${k.join(', ')}`).join('; ')}`);
let bytes = 0;
for (const [name, rows] of Object.entries(files)) {
  const text = JSON.stringify(rows);
  bytes += Buffer.byteLength(text);
  writeFileSync(resolve(OUT, `${name}.json`), text);
}
rmSync(TMP, { force: true });
console.log(`[cyber-data] wrote ${Object.keys(files).length} json files (${Math.round(bytes / 1024)} KB) to public/data/cyber`);
