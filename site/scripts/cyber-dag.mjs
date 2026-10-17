// Generate the "How the data is built" flow diagram for the cybersecurity-landscape page as an inline SVG.
//
// The diagram is declared below as columns of nodes at explicit grid rows and a list of edges (published sources ->
// raw read -> weekly tables -> metrics -> regression and scoreboard -> marts -> gate, facts and page) and drawn by
// code, never by hand. Output: site/src/assets/cyber-dag.svg, imported by the MDX page with `?raw` and inlined, so its
// colours are the page's CSS custom properties (light and dark).
//
// Rows are chosen so that each data lane (Yahoo, East Money history, CIBR, private companies) runs straight across
// the columns it does not touch; a lane that skips a stage is a straight line through an empty cell, never a
// pass-through box. The script checks its own drawing and fails the build on any of: an edge naming an undeclared node,
// an edge passing through a box that is not one of its ends, two boxes overlapping, a label or sub-line too long for its
// box. It also warns when a mart in data/facts/cyber.json `marts`, or a source in its `sources`, is not drawn, so the
// picture cannot silently fall behind the pipeline.
//
// Deterministic (no timestamps, fixed order) and dependency-free, so it is cheap enough to run on every prebuild.
// Phone width: the drawing keeps its own pixel size below its width (`min-width`), so the text stays at its drawn size
// and sits in a keyboard-focusable region that scrolls sideways inside the figure; the page itself never scrolls sideways.
// Below 480 px the drawing is replaced by the same boxes as an ordered list (each box with what feeds it), because a 1,000 px
// drawing read through a 320 px window is not a figure a reader can follow. The output is therefore an HTML fragment
// (region, drawing, list, and the rules that switch between them) although the file keeps the name cyber-dag.svg; the page
// imports it with `?raw` and inlines it, and nothing else reads it.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const OUT = resolve(process.cwd(), 'src', 'assets', 'cyber-dag.svg');
const FACTS = resolve(process.cwd(), '..', 'data', 'facts', 'cyber.json');

// ---- declaration ---------------------------------------------------------------------------------------------------
// `row` is the grid row of the box's top edge (a unit is ROW px; a negative row is a lane above the first one). `drop: true`
// marks a read whose raw payload is not kept (drawn dashed); `cur: true` marks a curated file, written once from public
// pages by a curator agent and re-opened by a second agent, with a source link, a date and a qualifier on every row (a bar
// on the left edge).
// `fact` is a prefix of the matching entry in data/facts/cyber.json `sources`; `marts` lists the marts a node stands for.
const COLUMNS = [
  {
    title: 'Published by others',
    nodes: [
      { id: 's_fred', row: -1.2, label: 'FRED exchange rates', sub: ['dollars per currency,', 'newest rate on file'], fact: 'FRED' },
      { id: 's_plan', row: 0.0, label: 'Evaluation plan', sub: ['section I, registered', 'definitions and pool'], fact: 'Section I' },
      { id: 's_yh', row: 1.2, label: 'Yahoo Finance', sub: ['price, EV, targets,', 'ratings, revenue est.'], fact: 'Yahoo Finance' },
      { id: 's_emf', row: 2.4, label: 'East Money forecast', sub: ['A-share consensus,', 'six-month ratings'], fact: 'East Money profit forecast' },
      { id: 's_emv', row: 3.6, label: 'East Money valuation', sub: ['daily PS_TTM and PB'], fact: 'East Money valuation analysis' },
      { id: 's_emr', row: 4.6, label: 'East Money reports', sub: ['A-share revenue,', 'by report date'], fact: 'East Money financial reports' },
      { id: 's_cibr', row: 5.9, label: 'CIBR holdings', sub: ['First Trust ETF page'], fact: 'First Trust NASDAQ' },
      { id: 's_pub', row: 7.1, label: 'Public statements', sub: ['filings, releases,', 'priced rounds, deals'], fact: 'Company filings' },
    ],
  },
  {
    title: 'Raw read',
    nodes: [
      { id: 'b_fx', row: -1.2, label: 'FX rates', sub: ['read at publish;', 'newest per currency'] },
      { id: 'b_pool', row: 0.0, label: 'Pool', sub: ['pool.py, as registered', 'layer, ring, purity'] },
      { id: 'b_yh', row: 1.2, label: 'Yahoo responses', sub: ['read in memory;', 'parsed fields kept'], drop: true },
      { id: 'b_emf', row: 2.4, label: 'East Money rows', sub: ['read in memory;', 'parsed fields kept'], drop: true },
      { id: 'b_emh', row: 4.1, label: 'East Money tables', sub: ['read in memory;', 'parsed fields kept'], drop: true },
      { id: 'b_cibr', row: 5.9, label: 'CIBR file', sub: ['cibr_holdings_*.json,', 'tickers and names'] },
      { id: 'b_ref', row: 7.1, label: 'Curated files', sub: ['private_*.json, events,', 'link, date, qualifier'], cur: true },
    ],
  },
  {
    title: 'Weekly tables',
    nodes: [
      { id: 'c_week', row: 1.8, label: 'Consensus table', sub: ['consensus/*.parquet;', 'last good run, Mondays'] },
      { id: 'c_hist', row: 4.1, label: 'Valuation history', sub: ['valuation_history_em/', 'daily PS_TTM, revenue'] },
      { id: 'c_val', row: 7.1, label: 'Validated rows', sub: ['reference.rows():', 'checked, notes stripped'], cur: true },
    ],
  },
  {
    title: 'Metrics',
    nodes: [
      { id: 'm_metrics', row: 1.2, label: 'metrics.py', sub: ['EV/NTM, growth, FCF,', 'Rule of 40, upside'] },
      { id: 'm_china', row: 4.1, label: 'China panel', sub: ['PS_TTM percentile,', 'revenue growth y/y'] },
      { id: 'm_etf', row: 5.9, label: 'etf.py', sub: ['CIBR against the pool;', 'a gap fails the check'] },
    ],
  },
  {
    title: 'Regression, scoreboard',
    nodes: [
      { id: 'g_reg', row: 0.0, label: 'regression.py', sub: ['EV/NTM on growth,', 'layers, residuals'] },
      { id: 'g_eval', row: 2.5, label: 'evaluate.py', sub: ['Q1-Q10 as registered;', 'collecting until graded'] },
      { id: 'g_ledger', row: 7.1, label: 'ledger.py', sub: ['private intervals,', 'events, unconfirmed'] },
    ],
  },
  {
    title: 'Marts (data/marts/cyber)',
    nodes: [
      { id: 'k_reg', row: 0.0, label: 'regression', sub: ['latest, series,', 'residuals, premium'], marts: ['regression'] },
      { id: 'k_listings', row: 1.2, label: 'listings', sub: ['+ layer_summary,', 'targets, recommendations'], marts: ['listings', 'layer_summary', 'targets', 'recommendations'] },
      { id: 'k_score', row: 2.5, label: 'scoreboard', sub: ['+ evidence, kpis'], marts: ['scoreboard', 'scoreboard_evidence', 'kpis'] },
      { id: 'k_china', row: 4.1, label: 'china_panel', sub: ['A-share listings'], marts: ['china_panel'] },
      { id: 'k_etf', row: 5.9, label: 'etf_crosscheck', sub: ['+ excluded,', 'metric_dictionary'], marts: ['etf_crosscheck', 'excluded', 'metric_dictionary'] },
      { id: 'k_private', row: 7.1, label: 'private_intervals', sub: ['+ ledger_*, events,', 'reported_unconfirmed'], marts: ['private_intervals', 'ledger_timeline', 'ledger_latest', 'events', 'reported_unconfirmed'] },
    ],
  },
  {
    title: 'Gate, facts, page',
    nodes: [
      { id: 'p_gate', row: 2.2, label: 'pandera gate', sub: ['a schema failure', 'blocks the write'] },
      { id: 'p_guard', row: 3.6, label: 'regression guard', sub: ['a thin run keeps the', 'last good facts'] },
      { id: 'p_facts', row: 5.0, label: 'facts/cyber.json', sub: ['every number the', 'page prints'] },
      { id: 'p_page', row: 6.4, label: 'this page', sub: ['prose templated;', 'charts read the marts'] },
    ],
  },
];

const EDGES = [
  // published -> raw read
  ['s_plan', 'b_pool'], ['s_yh', 'b_yh'], ['s_emf', 'b_emf'], ['s_emv', 'b_emh'], ['s_emr', 'b_emh'],
  ['s_cibr', 'b_cibr'], ['s_pub', 'b_ref'], ['s_fred', 'b_fx'],
  // raw read -> weekly tables
  ['b_yh', 'c_week'], ['b_emf', 'c_week'], ['b_emh', 'c_hist'], ['b_ref', 'c_val'],
  // tables and lanes -> metrics
  ['c_week', 'm_metrics'], ['b_pool', 'm_metrics'], ['b_fx', 'm_metrics'], ['c_hist', 'm_china'], ['b_cibr', 'm_etf'],
  // metrics -> regression and scoreboard
  ['m_metrics', 'g_reg'], ['m_metrics', 'g_eval'], ['m_metrics', 'g_ledger'], ['c_val', 'g_ledger'],
  ['g_reg', 'g_eval'], ['g_ledger', 'g_eval'],
  // -> marts
  ['m_metrics', 'k_listings'], ['g_reg', 'k_reg'], ['g_eval', 'k_score'], ['g_ledger', 'k_private'],
  ['m_china', 'k_china'], ['m_etf', 'k_etf'],
  // marts -> gate -> facts -> page
  ['k_reg', 'p_gate'], ['k_listings', 'p_gate'], ['k_score', 'p_gate'], ['k_china', 'p_gate'], ['k_etf', 'p_gate'],
  ['k_private', 'p_gate'],
  ['p_gate', 'p_guard'], ['p_guard', 'p_facts'], ['p_facts', 'p_page'],
];

// What happens in each gap between columns, printed above it.
const STEPS = ['fetched, parsed', 'stored, validated', 'computed', 'fitted, graded', 'written', 'gated, templated'];

// ---- layout --------------------------------------------------------------------------------------------------------
const NODE_W = 134, NODE_H = 42, ROW = 50, COL_GAP = 22, TOP = 58, PAD = 6;
const W = PAD * 2 + COLUMNS.length * NODE_W + (COLUMNS.length - 1) * COL_GAP;
const maxRow = Math.max(...COLUMNS.flatMap((c) => c.nodes.map((n) => n.row)));
const minRow = Math.min(0, ...COLUMNS.flatMap((c) => c.nodes.map((n) => n.row)));
const LEGEND_H = 34;
const H = Math.ceil(TOP + (maxRow - minRow) * ROW + NODE_H + LEGEND_H);
const LABEL_PX = 10.5, SUB_PX = 9, TITLE_PX = 11, CHAR_EM = 0.55; // conservative average advance of the sans face, in em

const pos = new Map();
COLUMNS.forEach((col, ci) => {
  const x = PAD + ci * (NODE_W + COL_GAP);
  col.nodes.forEach((n) => {
    if (pos.has(n.id)) throw new Error(`cyber-dag: node ${n.id} declared twice`);
    pos.set(n.id, { x, y: TOP + (n.row - minRow) * ROW, node: n, col: ci });
  });
});

for (const [a, b] of EDGES) {
  if (!pos.has(a) || !pos.has(b)) throw new Error(`cyber-dag: edge ${a} -> ${b} names an undeclared node`);
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const f = (n) => Number(n.toFixed(1));

// ---- edges ---------------------------------------------------------------------------------------------------------
// Returns the path and a polyline sampling it (for the collision check).
const edgeGeom = (a, b) => {
  const p = pos.get(a), q = pos.get(b);
  if (p.col === q.col) {
    // Same column: a short vertical arrow down (or up) the left side, between the two boxes.
    const x = p.x + 14;
    const down = q.y > p.y;
    const y1 = down ? p.y + NODE_H : p.y, y2 = down ? q.y - 2 : q.y + NODE_H + 2;
    return { d: `M${f(x)},${f(y1)} L${f(x)},${f(y2)}`, pts: [[x, y1], [x, y2]] };
  }
  const x1 = p.x + NODE_W, y1 = p.y + NODE_H / 2, x2 = q.x - 3, y2 = q.y + NODE_H / 2;
  if (Math.abs(y1 - y2) < 0.5) return { d: `M${f(x1)},${f(y1)} L${f(x2)},${f(y2)}`, pts: [[x1, y1], [x2, y2]] };
  const dx = (x2 - x1) / 2;
  const pts = [];
  for (let i = 0; i <= 48; i++) {
    const t = i / 48, u = 1 - t;
    const cx1 = x1 + dx, cx2 = x2 - dx;
    pts.push([
      u ** 3 * x1 + 3 * u * u * t * cx1 + 3 * u * t * t * cx2 + t ** 3 * x2,
      u ** 3 * y1 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y2,
    ]);
  }
  return { d: `M${f(x1)},${f(y1)} C${f(x1 + dx)},${f(y1)} ${f(x2 - dx)},${f(y2)} ${f(x2)},${f(y2)}`, pts };
};

// ---- self-checks ---------------------------------------------------------------------------------------------------
const problems = [];
const inside = (pt, r, m = 2) => pt[0] > r.x - m && pt[0] < r.x + NODE_W + m && pt[1] > r.y - m && pt[1] < r.y + NODE_H + m;
const geoms = new Map();
for (const [a, b] of EDGES) {
  const g = edgeGeom(a, b);
  geoms.set(`${a}>${b}`, g);
  for (const [id, r] of pos) {
    if (id === a || id === b) continue;
    if (g.pts.some((pt) => inside(pt, r))) problems.push(`edge ${a} -> ${b} passes through box ${id}`);
  }
}
const all = [...pos.entries()];
for (let i = 0; i < all.length; i++) {
  for (let j = i + 1; j < all.length; j++) {
    const [ia, a] = all[i], [ib, b] = all[j];
    if (a.col === b.col && a.y < b.y + NODE_H + 4 && b.y < a.y + NODE_H + 4) problems.push(`boxes ${ia} and ${ib} overlap`);
  }
}
const innerW = NODE_W - 14; // 7 px padding each side
for (const { node } of pos.values()) {
  if (node.label.length * LABEL_PX * CHAR_EM > innerW) problems.push(`label of ${node.id} is too long for its box: ${node.label}`);
  if (node.sub.length > 2) problems.push(`${node.id} has more than two sub-lines`);
  for (const s of node.sub) if (s.length * SUB_PX * CHAR_EM > innerW) problems.push(`sub-line of ${node.id} is too long for its box: ${s}`);
}
COLUMNS.forEach((c, i) => {
  if (c.title.length * TITLE_PX * CHAR_EM > NODE_W + COL_GAP - 4) problems.push(`column title ${i} is too long: ${c.title}`);
});
STEPS.forEach((s, i) => {
  if (s.length * 9.5 * CHAR_EM > 2 * (NODE_W + COL_GAP) - 20) problems.push(`step label ${i} is too long: ${s}`);
});
if (STEPS.length !== COLUMNS.length - 1) problems.push('STEPS must have one entry per gap between columns');
if (problems.length) throw new Error(`cyber-dag: layout problems:\n  ${problems.join('\n  ')}`);

// ---- drawing -------------------------------------------------------------------------------------------------------
const parts = [];
parts.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" class="dag" role="img" aria-labelledby="cyber-dag-title cyber-dag-desc">`);
parts.push('<title id="cyber-dag-title">How the cybersecurity data is built</title>');
const desc = COLUMNS.map((c) => `${c.title}: ${c.nodes.map((n) => n.label).join(', ')}`).join('. ');
parts.push(`<desc id="cyber-dag-desc">${esc(`Left to right. ${desc}. Dashed boxes are read in memory and their raw payload is not kept, only the parsed fields. A bar on the left edge marks a curated file: every row has a source link, a date and a qualifier.`)}</desc>`);
parts.push(`<style>
.dag{font-family:var(--font-sans,system-ui,sans-serif);width:100%;min-width:${W}px;height:auto;display:block}
.dag .col{font-size:${TITLE_PX}px;font-weight:600;fill:var(--ink)}
.dag .step{font-size:9.5px;font-style:italic;fill:var(--ink-3)}
.dag .box{fill:var(--bg);stroke:var(--border-strong);stroke-width:1}
.dag .box.drop{stroke-dasharray:4 3;stroke:var(--ink-3)}
.dag .bar{fill:var(--ink-3)}
.dag .lbl{font-size:${LABEL_PX}px;fill:var(--ink)}
.dag .sub{font-size:${SUB_PX}px;fill:var(--ink-3)}
.dag .edge{fill:none;stroke:var(--ink-3);stroke-width:0.9;opacity:0.75}
.dag .head{fill:var(--ink-3)}
.dag .legend{font-size:9.5px;fill:var(--ink-3)}
</style>`);
parts.push('<defs><marker id="cyber-dag-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L6,3 L0,6 z" class="head"/></marker></defs>');

COLUMNS.forEach((col, ci) => {
  const x = PAD + ci * (NODE_W + COL_GAP);
  parts.push(`<text class="col" x="${x}" y="18">${esc(col.title)}</text>`);
});
STEPS.forEach((s, i) => {
  const x = PAD + (i + 1) * (NODE_W + COL_GAP) - COL_GAP / 2;
  parts.push(`<text class="step" x="${f(x)}" y="36" text-anchor="middle">${esc(s)}</text>`);
});

parts.push('<g>');
for (const [a, b] of EDGES) parts.push(`<path class="edge" d="${geoms.get(`${a}>${b}`).d}" marker-end="url(#cyber-dag-arrow)"/>`);
parts.push('</g>');

for (const { x, y, node } of pos.values()) {
  const tx = x + (node.cur ? 10 : 7);
  const subs = node.sub.map((s, i) => `<text class="sub" x="${tx}" y="${y + 25 + i * 10}">${esc(s)}</text>`).join('');
  parts.push(`<g><rect class="box${node.drop ? ' drop' : ''}" x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}"/>`
    + (node.cur ? `<rect class="bar" x="${x}" y="${y}" width="3" height="${NODE_H}"/>` : '')
    + `<text class="lbl" x="${tx}" y="${y + 13}">${esc(node.label)}</text>${subs}</g>`);
}

const ly = H - 14;
parts.push(`<rect class="box drop" x="${PAD}" y="${ly - 9}" width="22" height="11"/>`);
parts.push(`<text class="legend" x="${PAD + 30}" y="${ly}">read in memory; the raw payload is not kept, only the parsed fields</text>`);
const lx = PAD + 30 + 360;
parts.push(`<rect class="box" x="${lx}" y="${ly - 9}" width="22" height="11"/><rect class="bar" x="${lx}" y="${ly - 9}" width="3" height="11"/>`);
parts.push(`<text class="legend" x="${lx + 30}" y="${ly}">curated, with a source link, date and qualifier for every row</text>`);
parts.push('</svg>');

// ---- the same boxes as a list (below 480 px the list replaces the drawing) ---------------------------------------------
const incoming = new Map();
for (const [a, b] of EDGES) incoming.set(b, [...(incoming.get(b) ?? []), a]);
const labelOf = (id) => pos.get(id).node.label;
const listItems = COLUMNS.map((col, ci) => {
  const boxes = col.nodes.map((n) => {
    const from = incoming.get(n.id);
    const what = n.sub.join(' ').replace(/[.;,\s]+$/, '');
    return `<li><strong>${esc(n.label)}</strong>: ${esc(what)}.${n.cur ? ' Curated: a source link, a date and a qualifier on every row.' : ''}`
      + `${from ? ` <span class="from">From ${esc(from.map(labelOf).join(', '))}.</span>` : ''}</li>`;
  }).join('');
  return `<li><strong>${esc(col.title)}</strong>${ci > 0 ? ` <span class="from">(${esc(STEPS[ci - 1])})</span>` : ''}<ul>${boxes}</ul></li>`;
});
const REGION = 'Pipeline diagram; use the arrow keys to scroll it sideways';
const css = `<style>
.dag-fig .dag-scroll{overflow-x:auto}
.dag-fig .dag-list{display:none;font-family:var(--font-sans,system-ui,sans-serif);font-size:.86rem;line-height:1.45;color:var(--ink-2);margin:0;padding-left:1.1rem}
.dag-fig .dag-list>li{margin:0 0 .7rem}
.dag-fig .dag-list ul{margin:.25rem 0 0;padding-left:1rem;list-style:disc}
.dag-fig .dag-list .from{color:var(--ink-3)}
@media (max-width:480px){.dag-fig .dag-scroll{display:none}.dag-fig .dag-list{display:block}}
</style>`;
const svg = `<div class="dag-fig">\n${css}\n<div class="dag-scroll" tabindex="0" role="region" aria-label="${REGION}">\n${parts.join('\n')}\n</div>\n`
  + `<ol class="dag-list" aria-label="How the cybersecurity data is built, as a list">${listItems.join('')}</ol>\n</div>\n`;

// ---- drift check against the published facts -----------------------------------------------------------------------
if (existsSync(FACTS)) {
  const facts = JSON.parse(readFileSync(FACTS, 'utf8'));
  const drawn = new Set(COLUMNS.flatMap((c) => c.nodes.flatMap((n) => n.marts ?? [])));
  const missing = Object.keys(facts.marts ?? {}).filter((m) => !drawn.has(m));
  if (missing.length) console.warn(`[cyber-dag] marts not shown in the diagram: ${missing.join(', ')}`);
  const extra = [...drawn].filter((m) => !(m in (facts.marts ?? {})));
  if (extra.length) console.warn(`[cyber-dag] diagram shows marts the facts do not list: ${extra.join(', ')}`);
  const prefixes = COLUMNS.flatMap((c) => c.nodes.map((n) => n.fact).filter(Boolean));
  const unshown = (facts.sources ?? []).map((s) => s.name).filter((name) => !prefixes.some((p) => name.startsWith(p)));
  if (unshown.length) console.warn(`[cyber-dag] sources not shown in the diagram: ${unshown.join('; ')}`);
}

mkdirSync(dirname(OUT), { recursive: true });
const prev = existsSync(OUT) ? readFileSync(OUT, 'utf8') : null;
if (prev !== svg) writeFileSync(OUT, svg);
console.log(`[cyber-dag] ${prev === svg ? 'unchanged' : 'wrote'} ${pos.size} nodes, ${EDGES.length} edges`);
