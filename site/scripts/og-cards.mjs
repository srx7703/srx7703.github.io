// Draws the link-preview cards (public/og/*.png, 1200x630): one for the site and one per project page.
// They are typographic on purpose: no chart and no number, so a card can never go stale while the data refreshes.
//
// Dev tool, not part of the build. It needs a headless Chrome and puppeteer-core, neither of which is a dependency of
// this site:   CHROME=/path/to/chrome PUPPETEER_CORE=/path/to/puppeteer-core.js node scripts/og-cards.mjs
// Re-run it when a project page is added or its title changes, then commit the PNGs.
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, join } from 'node:path';

const root = resolve(process.cwd());
const puppeteer = (await import(process.env.PUPPETEER_CORE ?? 'puppeteer-core')).default;
const chrome = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const fontUrl = (pkg, file) =>
  `data:font/woff2;base64,${readFileSync(join(root, 'node_modules/@fontsource-variable', pkg, 'files', file)).toString('base64')}`;
const FONTS = `
@font-face{font-family:"Newsreader Variable";font-style:normal;font-weight:200 800;src:url(${fontUrl('newsreader', 'newsreader-latin-wght-normal.woff2')}) format("woff2-variations")}
@font-face{font-family:"Newsreader Variable";font-style:italic;font-weight:200 800;src:url(${fontUrl('newsreader', 'newsreader-latin-wght-italic.woff2')}) format("woff2-variations")}
@font-face{font-family:"IBM Plex Sans Variable";font-style:normal;font-weight:100 700;src:url(${fontUrl('ibm-plex-sans', 'ibm-plex-sans-latin-wght-normal.woff2')}) format("woff2-variations")}`;

const AUTHOR = 'Song Ruoxuan';
const STATUS = { live: 'Live project, auto-refreshed', 'case-study': 'Case study', 'in-progress': 'In progress' };

function card({ eyebrow, title, size, note }) {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return `<!doctype html><meta charset="utf-8"><style>${FONTS}
*{box-sizing:border-box}html,body{margin:0}
body{width:1200px;height:630px;background:#f2f1ec;color:#171717;font-family:"Newsreader Variable",Georgia,serif}
.f{height:630px;padding:60px 76px 52px;display:flex;flex-direction:column}
.e{font:500 22px/1 "IBM Plex Sans Variable",Helvetica,Arial,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#6b6a63}
.t{flex:1;display:flex;align-items:center}
h1{margin:0;font-weight:500;font-size:${size}px;line-height:1.1;letter-spacing:-.005em;text-wrap:balance;max-width:1020px}
.b{border-top:1.5px solid #171717;padding-top:22px;display:flex;justify-content:space-between;gap:40px;font:400 24px/1.3 "IBM Plex Sans Variable",Helvetica,Arial,sans-serif;color:#454440}
.b b{font-weight:500;color:#171717;white-space:nowrap}
</style><div class="f"><div class="e">${esc(eyebrow)}</div><div class="t"><h1>${esc(title)}</h1></div><div class="b"><span>${esc(note)}</span><b>srx7703.github.io</b></div></div>`;
}

const sizeFor = (t) => (t.length <= 52 ? 76 : t.length <= 80 ? 64 : 54);
const NOTE = 'Charts, data and written methodology, built with Claude Code';

const cards = [{
  file: 'site',
  eyebrow: AUTHOR,
  title: 'Data analysis and dashboards',
  size: 92,
  note: 'Interactive charts and written methodology, built with Claude Code',
}];
const dir = join(root, 'src/content/projects');
for (const f of readdirSync(dir).filter((n) => n.endsWith('.mdx')).sort()) {
  const fm = readFileSync(join(dir, f), 'utf8').split('---')[1];
  const field = (k) => JSON.parse(fm.match(new RegExp(`^${k}:\\s*(.+)$`, 'm'))[1]);
  const title = field('title');
  cards.push({ file: f.replace(/\.mdx$/, ''), eyebrow: `${AUTHOR} · ${STATUS[fm.match(/^status:\s*(\S+)/m)[1]]}`, title, size: sizeFor(title), note: NOTE });
}

mkdirSync(join(root, 'public/og'), { recursive: true });
const browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1200, height: 630, deviceScaleFactor: 1 });
for (const c of cards) {
  await page.setContent(card(c), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  const png = await page.screenshot({ type: 'png', clip: { x: 0, y: 0, width: 1200, height: 630 } });
  writeFileSync(join(root, 'public/og', `${c.file}.png`), png);
  console.log(c.file.padEnd(34), `${(png.length / 1024).toFixed(0)} KB`);
}
await browser.close();
