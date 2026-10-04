/** Site and pipeline changes, newest first. Data refreshes are not listed; they show as "data as of" stamps. */
export type ChangelogEntry = { date: string; text: string; project?: string };

export const changelog: ChangelogEntry[] = [
  { date: '2026-10-04', project: 'payments-landscape', text: 'Payments landscape W4: review fixes (chart layout at phone width, finding titles on the adoption charts, fact-check corrections), method, evaluation, data-quality and build-notes sections in the appendix, and the large chart datasets and the metric dictionary loaded by URL instead of inlined in the page.' },
  { date: '2026-10-04', project: 'payments-landscape', text: 'Payments landscape W2–W3: economics, ledger, share-lens and evaluation modules with pandera-gated marts and the pre-registered scoreboard; the page with four linked dashboards (company explorer, share lens, adoption tracker, private ledger) sharing URL state and linked tables; the scheduled workflow drafted but not yet installed.' },
  { date: '2026-10-04', project: 'payments-landscape', text: 'Payments landscape W1: curated reference files (company KPIs, revenue lines, private statements, events, products, rate sheets, official denominators) read from the primary documents and re-opened by a separate verifier; clients for SEC, HTTP Archive, npm and the job boards.' },
  { date: '2026-10-02', project: 'payments-landscape', text: 'Payments landscape W0: questions Q1–Q9 pre-registered as section H of the evaluation plan, with the company universe, the SDK whitelist and the job-board tokens pinned.' },
  { date: '2026-09-16', text: 'Photos on the home and About pages and a masthead avatar; a build-time mini chart on every project row, drawn from the current data.' },
  { date: '2026-09-16', text: 'Restyle: paper ground and serif text, Tufte-style sidenotes, Distill-style article header and appendix; charts leave their cards.' },
  { date: '2026-09-16', text: 'Acceptance-review fixes: data-quality tables on every page, pre-registered evaluation plan with git hash, honest chart titles templated from facts, deploy after data refreshes, lazy-loaded chart library.' },
  { date: '2026-09-16', text: 'Financial LLM case study; sortable benchmark table with filters and CSV downloads; changelog page; prose-numbers guardrail test.' },
  { date: '2026-09-16', text: 'SaaS benchmark from SEC XBRL, FOMC decision-market page with backfilled history and scorecard, stat-arb case study; weekly refresh workflow.' },
  { date: '2026-09-16', text: 'Site v0 live on GitHub Pages: midterms tracker page, design tokens, Vega-Lite chart system.' },
  { date: '2026-09-15', text: 'Prediction-market snapshot pipeline (Polymarket + Kalshi) running twice daily on GitHub Actions.' },
];
