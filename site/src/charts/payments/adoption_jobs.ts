import { VL_SCHEMA, series, tok } from '../theme';
import {
  type Source, type JobsRow, ADOPTION_PARAMS, BOARD_COMPANY,
  namedData, reasonLayer, lookupExpr, emptyExpr, emptyReason, lit, rowsOf,
} from './adoption_shared';

const NAME = 'jobs_src';

/** Function order as classified in pipelines/payments/jobs.py (FUNCTIONS); "other" last. */
export const JOB_FUNCTIONS = ['engineering', 'sales/gtm', 'risk/compliance', 'product/design',
  'operations/support', 'finance/legal', 'other'];
export const JOB_FUNCTION_LABEL: Record<string, string> = {
  engineering: 'Engineering', 'sales/gtm': 'Sales and go-to-market', 'risk/compliance': 'Risk and compliance',
  'product/design': 'Product and design', 'operations/support': 'Operations and support',
  'finance/legal': 'Finance and legal', other: 'Other',
};

const COMPANY_BOARD: Record<string, string> = Object.fromEntries(Object.entries(BOARD_COMPANY).map(([b, c]) => [c, b]));

/**
 * Hiring by function: open postings on each company's public Greenhouse or Ashby board, classified
 * by title into seven functions (`jobs_by_function.json`; counts only, never job text). Bars are each
 * board's latest weekly snapshot; the grey tick is its first snapshot, so a bar that has moved since
 * shows how far. `company=all` adds the boards together, each at its own latest snapshot.
 *
 * Dashboard group `adoption`, param `company` (it also declares `source`, which this chart ignores).
 */
export function hiringSpec(source: Source<JobsRow>, opts: { source?: string; company?: string } = {}) {
  const board = lookupExpr(COMPANY_BOARD, 'company');
  const companyOf = lookupExpr(BOARD_COMPANY, 'datum.board');
  const y = {
    field: 'function_label', type: 'nominal', title: null,
    sort: JOB_FUNCTIONS.map((f) => JOB_FUNCTION_LABEL[f]),
    scale: { domain: JOB_FUNCTIONS.map((f) => JOB_FUNCTION_LABEL[f]) },
    axis: { labelLimit: 160, ticks: false, domain: false },
  };
  const x = { field: 'postings', type: 'quantitative', title: 'Open postings', axis: { format: ',d', tickCount: 5 } };
  const funcLabel = lookupExpr(JOB_FUNCTION_LABEL, 'datum.function');
  const base = [
    { calculate: companyOf, as: 'company' },
    { filter: "company == 'all' || datum.company == company" },
    { joinaggregate: [{ op: 'max', field: 'snapshot_date', as: 'last' }, { op: 'min', field: 'snapshot_date', as: 'first' }], groupby: ['board'] },
  ];
  const reason = [
    `${emptyExpr(NAME)} ? ${lit(emptyReason('the job-board file'))}`,
    `company != 'all' && !isValid(${board}) ? company + ' has no pinned\\nGreenhouse or Ashby job board.'`,
    `company != 'all' && !indata(${lit(NAME)}, 'board', ${board}) ? 'No snapshot of ' + company + '\\'s\\njob board in the current file.'`,
    "''",
  ].join(' : ');
  return {
    $schema: VL_SCHEMA,
    height: { step: 26 },
    params: ADOPTION_PARAMS(opts),
    data: namedData(source, NAME),
    layer: [
      {
        transform: [
          ...base,
          { filter: 'datum.snapshot_date == datum.last' },
          { aggregate: [{ op: 'sum', field: 'postings', as: 'postings' }, { op: 'sum', field: 'openings', as: 'openings' },
            { op: 'max', field: 'snapshot_date', as: 'snapshot' }, { op: 'distinct', field: 'board', as: 'boards' }], groupby: ['function'] },
          { calculate: funcLabel, as: 'function_label' },
          { joinaggregate: [{ op: 'sum', field: 'postings', as: 'total' }] },
          { calculate: 'datum.total > 0 ? datum.postings / datum.total : null', as: 'share' },
        ],
        layer: [
          {
            mark: { type: 'bar', height: 14, cornerRadiusEnd: 2, color: series.a },
            encoding: {
              y, x,
              tooltip: [
                { field: 'function_label', type: 'nominal', title: 'Function' },
                { field: 'postings', type: 'quantitative', title: 'Postings', format: ',' },
                { field: 'openings', type: 'quantitative', title: 'Openings', format: ',' },
                { field: 'share', type: 'quantitative', title: 'Of all postings', format: '.0%' },
                { field: 'boards', type: 'quantitative', title: 'Boards' },
                { field: 'snapshot', type: 'nominal', title: 'Latest snapshot' },
              ],
            },
          },
          {
            mark: { type: 'text', align: 'left', dx: 4, fontSize: 11, color: tok('ink-2') },
            encoding: { y, x, text: { field: 'postings', type: 'quantitative', format: ',' } },
          },
        ],
      },
      {
        // First snapshot, drawn only where it differs from the latest.
        transform: [
          ...base,
          { filter: 'datum.snapshot_date == datum.first && datum.first != datum.last' },
          { aggregate: [{ op: 'sum', field: 'postings', as: 'postings' }, { op: 'min', field: 'snapshot_date', as: 'snapshot' }], groupby: ['function'] },
          { calculate: funcLabel, as: 'function_label' },
        ],
        mark: { type: 'tick', thickness: 2, size: 20, color: series.neutral },
        encoding: {
          y, x,
          tooltip: [
            { field: 'function_label', type: 'nominal', title: 'Function' },
            { field: 'postings', type: 'quantitative', title: 'Postings at first snapshot', format: ',' },
            { field: 'snapshot', type: 'nominal', title: 'First snapshot' },
          ],
        },
      },
      reasonLayer(reason),
    ],
  };
}

/** Finding for the default view (Stripe): the largest function's share of its latest postings. */
export function hiringFinding(source?: Source<JobsRow>, company = 'Stripe') {
  const token = COMPANY_BOARD[company];
  const rows = rowsOf(source).filter((r) => r.board === token);
  const last = rows.reduce((m, r) => (r.snapshot_date > m ? r.snapshot_date : m), '');
  const latest = rows.filter((r) => r.snapshot_date === last);
  const total = latest.reduce((s, r) => s + r.postings, 0);
  const top = [...latest].sort((a, b) => b.postings - a.postings)[0];
  if (!top || !total) {
    return { title: `What ${company} is hiring for, by function`, ready: false, last: null, top: null, share: null };
  }
  const share = top.postings / total;
  return {
    title: `${JOB_FUNCTION_LABEL[top.function] ?? top.function} is ${Math.round(share * 100)}% of ${company}'s open postings`,
    ready: true, last, top, share,
  };
}

export function hiringSubtitle(): string {
  return 'Open postings on each company\'s public Greenhouse or Ashby board, by function classified from the job title; bar = latest weekly snapshot, grey tick = first snapshot. '
    + 'Counts only; companies on other applicant systems are not tracked.';
}
