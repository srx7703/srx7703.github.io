// Copy pipeline outputs (data/marts/**/*.json, data/facts/*.json) into public/data so charts can load
// them by URL. Runs automatically before `astro dev` / `astro build` (npm pre-hooks).
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const root = resolve(process.cwd(), '..', 'data');
const out = resolve(process.cwd(), 'public', 'data');
rmSync(out, { recursive: true, force: true });
let n = 0;
const walk = (dir, rel) => {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, join(rel, name));
    else if (name.endsWith('.json')) {
      mkdirSync(join(out, rel), { recursive: true });
      cpSync(p, join(out, rel, name));
      n++;
    }
  }
};
for (const sub of ['marts', 'facts']) if (existsSync(join(root, sub))) walk(join(root, sub), sub);
console.log(`[sync-data] copied ${n} json files to public/data`);
