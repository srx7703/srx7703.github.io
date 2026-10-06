// Write the payments page's URL-loaded data files: the large chart datasets and the metric dictionary
// rows, to public/data/payments/<file>.json (CLAUDE.md: large marts are loaded by URL).
//
// The rows come from src/charts/payments/external.ts, the same module the page uses to point each
// chart spec at its URL, so the page and its data files are always built from the same marts. The
// module is bundled with esbuild (already a dependency of Astro, via Vite) and run here, in prebuild,
// rather than served from an Astro endpoint: an endpoint would join the build's module graph and
// reorder the shared stylesheet chunks of every other page. Runs after sync-data.mjs, which clears
// public/data. Deterministic: fixed file names, no timestamps.
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const ENTRY = resolve(process.cwd(), 'src', 'charts', 'payments', 'external.ts');
const TMP = resolve(process.cwd(), 'node_modules', '.cache', 'payments-data.mjs');
const OUT = resolve(process.cwd(), 'public', 'data', 'payments');

mkdirSync(resolve(TMP, '..'), { recursive: true });
await build({ entryPoints: [ENTRY], bundle: true, platform: 'node', format: 'esm', outfile: TMP, logLevel: 'warning' });
const { dataFiles, DATA_BASE } = await import(pathToFileURL(TMP).href);
if (DATA_BASE !== '/data/payments') throw new Error(`DATA_BASE ${DATA_BASE} does not match ${OUT}`);
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const files = dataFiles();
for (const [name, rows] of Object.entries(files)) writeFileSync(resolve(OUT, `${name}.json`), JSON.stringify(rows));
rmSync(TMP, { force: true });
console.log(`[payments-data] wrote ${Object.keys(files).length} json files to public/data/payments`);
