// Generate the "How the data is built" flow diagram for the payments-landscape page as an inline SVG.
//
// The diagram is declared below as columns of nodes and a list of edges (sources -> what is stored ->
// transform modules -> marts -> grading and the pandera gate -> facts and the page) and laid out by
// code, never drawn by hand. Output: site/src/assets/payments-dag.svg, imported by the MDX page with
// `?raw` and inlined, so its colours are the page's CSS custom properties (light and dark).
//
// Deterministic (no timestamps, fixed order) and dependency-free, so it is cheap enough to run on every
// prebuild. It also checks the declared marts against data/facts/payments.json `marts` and warns about a
// mart that the diagram does not show, so the drawing cannot silently fall behind the pipeline.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const OUT = resolve(process.cwd(), 'src', 'assets', 'payments-dag.svg');
const FACTS = resolve(process.cwd(), '..', 'data', 'facts', 'payments.json');

// ---- declaration ---------------------------------------------------------------------------------
// `ci: true` marks what exists only after the scheduled CI run (drawn dashed).
const COLUMNS = [
  {
    title: 'Published by others',
    nodes: [
      { id: 's_filings', label: 'SEC filings, IR releases', sub: '8-K, 10-Q, 20-F, letters' },
      { id: 's_private', label: 'Company statements', sub: 'newsrooms, round releases' },
      { id: 's_official', label: 'Official totals', sub: 'Fed FEDS Note, Census, FRED' },
      { id: 's_rates', label: 'Rate sheets, benchmarks', sub: 'Visa interchange, FSB KPIs' },
      { id: 's_products', label: 'Product and deal pages', sub: 'launches, acquisitions' },
      { id: 's_http', label: 'HTTP Archive', sub: 'Tech Report API, monthly', ci: true },
      { id: 's_npm', label: 'npm and PyPI', sub: 'download counts', ci: true },
      { id: 's_jobs', label: 'Greenhouse, Ashby', sub: 'public job boards', ci: true },
      { id: 's_formd', label: 'SEC EDGAR APIs', sub: 'Form D, XBRL facts', ci: true },
    ],
  },
  {
    title: 'Stored as read',
    nodes: [
      { id: 'r_kpi', label: 'kpi_disclosures_*.json', sub: 'reference, hand-verified' },
      { id: 'r_private', label: 'private_metrics.json', sub: 'reference, with qualifier' },
      { id: 'r_denom', label: 'denominators.json', sub: 'reference' },
      { id: 'r_waterfall', label: 'waterfall_inputs.json', sub: 'reference' },
      { id: 'r_prod', label: 'products, events .json', sub: 'reference' },
      { id: 'r_raw', label: 'data/raw/payments/', sub: 'json.gz, append-only', ci: true },
      { id: 'r_counts', label: 'job counts only', sub: 'no posting text kept', ci: true },
    ],
  },
  {
    title: 'Transform',
    nodes: [
      { id: 't_economics', label: 'economics.py', sub: 'per-$100 books, bands' },
      { id: 't_ledger', label: 'ledger.py', sub: 'take rates, private ledger' },
      { id: 't_share', label: 'share.py', sub: 'lenses, never summed' },
      { id: 't_curated', label: 'publish.py (curated)', sub: 'matrix, timeline, dictionary' },
      { id: 't_webtech', label: 'webtech.py', sub: 'coverage, 3-crawl mean', ci: true },
      { id: 't_devstats', label: 'devstats.py', sub: 'pinned SDK whitelist', ci: true },
      { id: 't_jobs', label: 'jobs.py', sub: 'title to function', ci: true },
      { id: 't_formd', label: 'formd.py', sub: 'raise notices', ci: true },
    ],
  },
  {
    title: 'Marts (data/marts/payments)',
    nodes: [
      { id: 'm_water', label: 'waterfall_lines', sub: '+ sensitivity', marts: ['waterfall_lines', 'sensitivity'] },
      { id: 'm_take', label: 'take_rates', sub: '+ refusals with reasons', marts: ['take_rates', 'take_rates_refused', 'private_take_rates_refused'] },
      { id: 'm_ledger', label: 'ledger_timeline', sub: '+ latest, private_intervals', marts: ['ledger_timeline', 'ledger_latest', 'private_intervals'] },
      { id: 'm_share', label: 'share_lenses', sub: '+ share_pool_hhi', marts: ['share_lenses', 'share_pool_hhi'] },
      { id: 'm_static', label: 'products, events', sub: 'chartable rows only', marts: ['products', 'events'] },
      { id: 'm_dict', label: 'metric_dictionary', sub: '+ reported_unconfirmed', marts: ['metric_dictionary', 'reported_unconfirmed'] },
      { id: 'm_ci', label: 'CI views', sub: 'webtech, devstats, jobs, formd', ci: true, marts: ['webtech_q2', 'devstats_q1', 'devstats_monthly', 'jobs_by_function', 'formd_filings', 'sec_latest'] },
    ],
  },
  {
    title: 'Grade and gate',
    nodes: [
      { id: 'g_evaluate', label: 'evaluate.py', sub: 'Q1-Q9 as registered', marts: ['scoreboard', 'scoreboard_evidence'] },
      { id: 'g_gate', label: 'pandera gate', sub: 'a failure blocks the write', marts: ['kpis'] },
      { id: 'g_guard', label: 'regression guard', sub: 'keeps last good run' },
    ],
  },
  {
    title: 'Published',
    nodes: [
      { id: 'p_facts', label: 'facts/payments.json', sub: 'KPIs, values, checks' },
      { id: 'p_page', label: 'this page', sub: 'prose templated, charts' },
    ],
  },
];

const EDGES = [
  ['s_filings', 'r_kpi'], ['s_filings', 'r_waterfall'], ['s_private', 'r_private'], ['s_official', 'r_denom'],
  ['s_rates', 'r_waterfall'], ['s_products', 'r_prod'], ['s_http', 'r_raw'], ['s_npm', 'r_raw'],
  ['s_formd', 'r_raw'], ['s_jobs', 'r_counts'],
  ['r_kpi', 't_ledger'], ['r_kpi', 't_share'], ['r_private', 't_ledger'], ['r_denom', 't_share'],
  ['r_waterfall', 't_economics'], ['r_prod', 't_curated'], ['r_raw', 't_webtech'], ['r_raw', 't_devstats'],
  ['r_raw', 't_formd'], ['r_counts', 't_jobs'],
  ['t_economics', 'm_water'], ['t_ledger', 'm_take'], ['t_ledger', 'm_ledger'], ['t_share', 'm_share'],
  ['t_curated', 'm_static'], ['t_curated', 'm_dict'], ['t_webtech', 'm_ci'], ['t_devstats', 'm_ci'],
  ['t_jobs', 'm_ci'], ['t_formd', 'm_ci'],
  ['m_water', 'g_evaluate'], ['m_take', 'g_evaluate'], ['m_ledger', 'g_evaluate'], ['m_share', 'g_evaluate'],
  ['m_ci', 'g_evaluate'],
  ['m_water', 'g_gate'], ['m_take', 'g_gate'], ['m_ledger', 'g_gate'], ['m_share', 'g_gate'], ['m_static', 'g_gate'],
  ['m_dict', 'g_gate'], ['m_ci', 'g_gate'], ['g_evaluate', 'g_gate'],
  ['g_gate', 'g_guard'], ['g_guard', 'p_facts'], ['g_guard', 'p_page'], ['p_facts', 'p_page'],
];

// What happens in each gap between columns, printed above it.
const STEPS = ['read, dated, tagged', 'validated, qualifier kept', 'computed', 'graded, checked', 'written, templated'];

// ---- layout --------------------------------------------------------------------------------------
const NODE_W = 150, NODE_H = 34, ROW = 44, COL_GAP = 34, TOP = 58, PAD = 10;
const W = PAD * 2 + COLUMNS.length * NODE_W + (COLUMNS.length - 1) * COL_GAP;
const maxRows = Math.max(...COLUMNS.map((c) => c.nodes.length));
const LEGEND_H = 30;
const H = TOP + maxRows * ROW + LEGEND_H;

const pos = new Map();
COLUMNS.forEach((col, ci) => {
  const x = PAD + ci * (NODE_W + COL_GAP);
  const offset = ((maxRows - col.nodes.length) * ROW) / 2;
  col.nodes.forEach((n, ri) => pos.set(n.id, { x, y: TOP + offset + ri * ROW, node: n, col: ci }));
});

for (const [a, b] of EDGES) {
  if (!pos.has(a) || !pos.has(b)) throw new Error(`payments-dag: edge ${a} -> ${b} names an undeclared node`);
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const f = (n) => Number(n.toFixed(1));

const edgePath = (a, b) => {
  const p = pos.get(a), q = pos.get(b);
  if (p.col === q.col) {
    // Same column (the gate chain): a short vertical arrow down the left edge.
    const x = p.x + 14;
    return `M${f(x)},${f(p.y + NODE_H)} L${f(x)},${f(q.y - 2)}`;
  }
  const x1 = p.x + NODE_W, y1 = p.y + NODE_H / 2, x2 = q.x - 3, y2 = q.y + NODE_H / 2;
  const dx = (x2 - x1) / 2;
  return `M${f(x1)},${f(y1)} C${f(x1 + dx)},${f(y1)} ${f(x2 - dx)},${f(y2)} ${f(x2)},${f(y2)}`;
};

const ciNode = (id) => Boolean(pos.get(id).node.ci);

const parts = [];
parts.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" class="dag" role="img" aria-labelledby="dag-title dag-desc">`);
parts.push('<title id="dag-title">How the payments data is built</title>');
const desc = COLUMNS.map((c) => `${c.title}: ${c.nodes.map((n) => n.label).join(', ')}`).join('. ');
parts.push(`<desc id="dag-desc">${esc(`Left to right. ${desc}. Dashed boxes exist only after the scheduled CI run.`)}</desc>`);
parts.push(`<style>
.dag{font-family:var(--font-sans,system-ui,sans-serif);width:100%;min-width:760px;height:auto;display:block}
.dag .col{font-size:11px;font-weight:600;fill:var(--ink)}
.dag .step{font-size:9.5px;font-style:italic;fill:var(--ink-3)}
.dag .box{fill:var(--bg);stroke:var(--border-strong);stroke-width:1}
.dag .box.ci{stroke-dasharray:4 3;stroke:var(--ink-3)}
.dag .lbl{font-size:10.5px;fill:var(--ink)}
.dag .sub{font-size:9px;fill:var(--ink-3)}
.dag .edge{fill:none;stroke:var(--ink-3);stroke-width:0.9;opacity:0.75}
.dag .edge.ci{stroke-dasharray:3 3}
.dag .head{fill:var(--ink-3)}
.dag .legend{font-size:9.5px;fill:var(--ink-3)}
</style>`);
parts.push('<defs><marker id="dag-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L6,3 L0,6 z" class="head"/></marker></defs>');

COLUMNS.forEach((col, ci) => {
  const x = PAD + ci * (NODE_W + COL_GAP);
  parts.push(`<text class="col" x="${x}" y="18">${esc(col.title)}</text>`);
});
STEPS.forEach((s, i) => {
  const x = PAD + (i + 1) * (NODE_W + COL_GAP) - COL_GAP / 2;
  parts.push(`<text class="step" x="${f(x)}" y="36" text-anchor="middle">${esc(s)}</text>`);
});

parts.push('<g>');
for (const [a, b] of EDGES) {
  const cls = ciNode(a) || ciNode(b) ? 'edge ci' : 'edge';
  parts.push(`<path class="${cls}" d="${edgePath(a, b)}" marker-end="url(#dag-arrow)"/>`);
}
parts.push('</g>');

for (const { x, y, node } of pos.values()) {
  parts.push(`<g><rect class="box${node.ci ? ' ci' : ''}" x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}"/>`
    + `<text class="lbl" x="${x + 7}" y="${y + 14}">${esc(node.label)}</text>`
    + `<text class="sub" x="${x + 7}" y="${y + 27}">${esc(node.sub)}</text></g>`);
}

const ly = H - 12;
parts.push(`<rect class="box ci" x="${PAD}" y="${ly - 9}" width="22" height="11"/>`);
parts.push(`<text class="legend" x="${PAD + 30}" y="${ly}">written only by the scheduled CI run; absent from a local build, and the page says so where it is missing</text>`);
parts.push('</svg>');

const svg = `${parts.join('\n')}\n`;

// ---- drift check against the published facts -----------------------------------------------------
if (existsSync(FACTS)) {
  const facts = JSON.parse(readFileSync(FACTS, 'utf8'));
  const drawn = new Set(COLUMNS.flatMap((c) => c.nodes.flatMap((n) => n.marts ?? [])));
  const missing = Object.keys(facts.marts ?? {}).filter((m) => !drawn.has(m));
  if (missing.length) console.warn(`[payments-dag] marts not shown in the diagram: ${missing.join(', ')}`);
}

mkdirSync(dirname(OUT), { recursive: true });
const prev = existsSync(OUT) ? readFileSync(OUT, 'utf8') : null;
if (prev !== svg) writeFileSync(OUT, svg);
console.log(`[payments-dag] ${prev === svg ? 'unchanged' : 'wrote'} ${COLUMNS.reduce((s, c) => s + c.nodes.length, 0)} nodes, ${EDGES.length} edges`);
