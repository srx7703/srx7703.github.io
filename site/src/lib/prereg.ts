import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Git blob SHA-1 of docs/EVALUATION_PLAN.md, computed at build time (matches `git hash-object`). */
export function evaluationPlan() {
  const path = resolve(process.cwd(), '..', 'docs', 'EVALUATION_PLAN.md');
  const buf = readFileSync(path);
  const sha = createHash('sha1').update(`blob ${buf.length}\0`).update(buf).digest('hex');
  return { sha, short: sha.slice(0, 12), url: 'https://github.com/srx7703/srx7703.github.io/blob/main/docs/EVALUATION_PLAN.md', history: 'https://github.com/srx7703/srx7703.github.io/commits/main/docs/EVALUATION_PLAN.md' };
}
