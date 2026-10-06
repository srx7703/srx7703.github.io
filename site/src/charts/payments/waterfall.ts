import { VL_SCHEMA, series, tok } from '../theme';

export type WaterfallLine = {
  waterfall: string; segment: string; company: string; period: string; as_of: string;
  layer: string; line: string; role: string; order: number; usd_per_100: number | null;
  qualifier: string | null; formula: string | null; inputs: string | null; source_url: string | null;
  tag: string; chartable: boolean; stale: boolean; note: string | null;
};

/** The books in page order, with a short name for facet headers and selects. */
export const WATERFALL_LABEL: Record<string, string> = {
  card_acquiring_us: 'A US card payment through Stripe',
  bnpl_affirm: 'An Affirm buy-now-pay-later purchase',
  corporate_card_bill: 'A BILL corporate card swipe',
  cross_border_wise: 'A Wise cross-border transfer',
  cross_border_fsb: 'The industry benchmark for cross-border B2B',
};
export const WATERFALL_ORDER = Object.keys(WATERFALL_LABEL);

export const LINE_LABEL: Record<string, string> = {
  merchant_network: 'Merchant fees', card_network: 'Card network fees', interest_income: 'Interest income',
  gain_on_sales_of_loans: 'Gain on loan sales', servicing_income: 'Servicing income', total_revenue: 'Total revenue',
  loss_on_loan_purchase_commitment: 'Loss on loan commitments', provision_for_credit_losses: 'Credit loss provision',
  funding: 'Funding costs', processing_and_servicing: 'Processing, servicing',
  revenue_less_transaction_costs: 'Kept: revenue less costs',
  merchant_price_stripe_list: 'Merchant pays (list price)',
  interchange_visa_traditional_rewards_cnp: 'Issuer interchange',
  network_fees: 'Network fees',
  acquirer_and_network_residual: 'Kept: acquirer + network',
  transaction_fees: 'Card fees', rewards: 'Cardholder rewards',
  net_interchange_after_rewards: 'Kept: net interchange',
  visa_commercial_cnp_list: 'Visa list rate (reference)',
  avg_cost_b2b_msme: 'Average cost', avg_cost_b2b_msme_fx_component: 'of which FX margin',
  avg_cost_b2b_msme_fee_component: 'of which fees',
  cross_border_take_rate: 'All customers', cross_border_take_rate_business: 'Business customers',
};
export const lineLabel = (l: string) => LINE_LABEL[l] ?? l.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Role -> how the step is drawn. Colours are design tokens only. */
const ROLE_STYLE: Record<string, { color: string; opacity: number; hollow?: boolean }> = {
  paid: { color: series.neutral, opacity: 0.9 },
  subtotal: { color: series.neutral, opacity: 0.9 },
  take: { color: series.a, opacity: 1 },
  component: { color: series.a, opacity: 0.55 },
  cost: { color: series.b, opacity: 1 },
  residual: { color: series.c, opacity: 1 },
  benchmark: { color: series.neutral, opacity: 0.9 },
  reference: { color: series.neutral, opacity: 1, hollow: true },
  gap: { color: series.neutral, opacity: 0.25, hollow: true },
};
export const ROLE_LABEL: Record<string, string> = {
  paid: 'Paid', subtotal: 'Subtotal', take: 'Revenue line', component: 'Component', cost: 'Cost',
  residual: 'Kept', benchmark: 'Benchmark', reference: 'Reference rate (not in the chain)', gap: 'Undisclosed',
};

export type WaterfallStep = {
  waterfall: string; book: string; company: string; period: string; as_of: string; layer: string;
  line: string; label: string; role: string; role_label: string; order: number; row: string;
  value: number | null; x0: number; x1: number; qualifier: string; color: string; opacity: number;
  hollow: boolean; value_label: string; note: string | null; formula: string | null; source_url: string | null;
};

/**
 * Lay out one book's lines as waterfall steps, in dollars per $100 of volume.
 *
 * A chain (a book with a `paid`, `subtotal` or `residual` line) runs left to right: revenue lines
 * add from zero until a `paid` line sets the starting price, after which a `take` is a slice of what
 * was paid and steps down; `cost` lines step down; `paid`, `subtotal` and `residual` are drawn from zero
 * as totals; a `gap` (a step nobody publishes) is a pale dashed bar over whatever is left, labelled
 * "not published", never a number. A book without a chain (Wise, the FSB benchmark) is a set of separate bars from zero, one per
 * period, because take rates in different periods or customer groups do not add up; `component`
 * lines stack from zero to show what a benchmark is made of. Nothing is summed across books.
 */
export function waterfallSteps(rows: WaterfallLine[], waterfall: string): WaterfallStep[] {
  const lines = rows
    .filter((r) => r.waterfall === waterfall && r.chartable && !r.stale)
    .sort((a, b) => a.period.localeCompare(b.period) || a.order - b.order);
  const periods = [...new Set(lines.map((r) => r.period))];
  const multi = periods.length > 1;
  const out: WaterfallStep[] = [];
  for (const period of periods) {
    const here = lines.filter((r) => r.period === period);
    const chain = here.some((r) => ['paid', 'subtotal', 'residual'].includes(r.role));
    let run = 0;
    let paid = false;
    let comp = 0;
    for (const r of here) {
      const v = r.usd_per_100;
      let x0 = 0; let x1 = v ?? 0;
      if (r.role === 'gap') { x0 = 0; x1 = run; }
      else if (v == null) continue;
      else if (r.role === 'component') { x0 = comp; x1 = comp + v; comp = x1; }
      else if (!chain || ['benchmark', 'reference'].includes(r.role)) { x0 = 0; x1 = v; }
      else if (r.role === 'paid') { x0 = 0; x1 = v; run = v; paid = true; }
      else if (r.role === 'subtotal' || r.role === 'residual') { x0 = 0; x1 = v; run = v; }
      else if (r.role === 'take' && paid) { x0 = run - v; x1 = run; run -= v; }
      else if (r.role === 'take') { x0 = run; x1 = run + v; run += v; }
      else if (r.role === 'cost') { x0 = run + v; x1 = run; run += v; }
      const style = ROLE_STYLE[r.role] ?? { color: series.neutral, opacity: 0.9 };
      const label = lineLabel(r.line);
      const q = r.qualifier && r.qualifier !== '=' ? `${r.qualifier} ` : '';
      out.push({
        waterfall, book: WATERFALL_LABEL[waterfall] ?? waterfall, company: r.company, period, as_of: r.as_of,
        layer: r.layer, line: r.line, label, role: r.role, role_label: ROLE_LABEL[r.role] ?? r.role, order: r.order,
        row: multi ? `${label} · ${period}` : label,
        value: v, x0: Math.min(x0, x1), x1: Math.max(x0, x1), qualifier: r.qualifier ?? '',
        color: style.color, opacity: style.opacity, hollow: !!style.hollow,
        value_label: v == null ? 'not published' : `${q}${v < 0 ? '−' : ''}$${Math.abs(v).toFixed(2)}`,
        note: r.note, formula: r.formula, source_url: r.source_url,
      });
    }
  }
  return out;
}

/**
 * Layout. A single (unfaceted) view, so VegaChart sizes it to the container with autosize fit-x
 * like the other payments charts. There is no y-axis label column: each step's name is printed
 * just above its bar, left-aligned at the zero edge, and each book opens with a header row. The
 * only fixed horizontal space is the value label after the longest bar, so at phone width the bars
 * shrink instead of the whole figure being scaled down. Text limits come from the container width.
 */
const NARROW = '(containerSize()[0] || 640) < 480';
// VegaChart draws an unfaceted chart at (container - 78)px; keep a few px spare.
const TEXT_LIMIT = { expr: 'max(150, (containerSize()[0] || 640) - 92)' };

const Y = {
  field: 'key', type: 'nominal', title: null, sort: { field: 'seq', op: 'min' },
  axis: null, scale: { paddingInner: 0, paddingOuter: 0.1 },
};
const X = {
  field: 'x0', type: 'quantitative', title: 'US$ per $100 of volume',
  axis: { titleAnchor: 'start', format: '$.2~f', tickCount: { expr: `${NARROW} ? 3 : 5` }, grid: true, labelFlush: true },
  scale: { zero: true, nice: true },
};
const X2 = { field: 'x1' };
const BAR = { type: 'bar', height: 11, yOffset: 6 } as const;

const layers = () => [
  {
    transform: [{ filter: '!datum.header && !datum.hollow' }],
    mark: { ...BAR },
    encoding: {
      x: X, x2: X2,
      color: { field: 'color', type: 'nominal', scale: null, legend: null },
      opacity: { field: 'opacity', type: 'quantitative', scale: null, legend: null },
      tooltip: [
        { field: 'book', type: 'nominal', title: 'Book' },
        { field: 'label', type: 'nominal', title: 'Line' },
        { field: 'role_label', type: 'nominal', title: 'Role' },
        { field: 'value_label', type: 'nominal', title: 'Per $100' },
        { field: 'period', type: 'nominal', title: 'Period' },
        { field: 'layer', type: 'nominal', title: 'Who' },
        { field: 'formula', type: 'nominal', title: 'Computed as' },
        { field: 'note', type: 'nominal', title: 'Note' },
      ],
    },
  },
  {
    transform: [{ filter: '!datum.header && datum.hollow' }],
    mark: { ...BAR, fill: tok('series-neutral'), fillOpacity: 0.12, strokeDash: [4, 3], strokeWidth: 1.2 },
    encoding: {
      x: X, x2: X2,
      stroke: { field: 'color', type: 'nominal', scale: null, legend: null },
      tooltip: [
        { field: 'book', type: 'nominal', title: 'Book' },
        { field: 'label', type: 'nominal', title: 'Line' },
        { field: 'role_label', type: 'nominal', title: 'Role' },
        { field: 'value_label', type: 'nominal', title: 'Per $100' },
        { field: 'note', type: 'nominal', title: 'Note' },
      ],
    },
  },
  {
    // Step name above its bar, from the left edge of the plot.
    transform: [{ filter: '!datum.header' }],
    mark: { type: 'text', fontSize: 11, color: tok('ink'), baseline: 'middle', align: 'left', dy: -6, limit: TEXT_LIMIT },
    encoding: { x: { value: 0 }, text: { field: 'row', type: 'nominal' } },
  },
  {
    // Book header row.
    transform: [{ filter: 'datum.header' }],
    mark: { type: 'text', fontSize: { expr: `${NARROW} ? 11 : 12` }, fontWeight: 600, color: tok('ink'), baseline: 'middle', align: 'left', dy: 2, limit: TEXT_LIMIT },
    encoding: { x: { value: 0 }, text: { field: 'book', type: 'nominal' } },
  },
  {
    // Value at the far end of each bar.
    transform: [{ filter: "!datum.header && datum.role != 'gap'" }],
    mark: { type: 'text', fontSize: 10.5, color: tok('ink-2'), baseline: 'middle', align: 'left', dx: 4, dy: 6 },
    encoding: { x: { field: 'x1', type: 'quantitative' }, text: { field: 'value_label', type: 'nominal' } },
  },
  {
    // An undisclosed step says so, in italics, where the others print their value.
    transform: [{ filter: "!datum.header && datum.role == 'gap'" }],
    mark: { type: 'text', fontSize: 10.5, color: tok('ink-2'), baseline: 'middle', align: 'left', dx: 4, dy: 6, fontStyle: 'italic' },
    encoding: { x: { field: 'x1', type: 'quantitative' }, text: { field: 'value_label', type: 'nominal' } },
  },
];

type Row = Partial<WaterfallStep> & { key: string; seq: number; header: boolean; book: string };

/** Header row plus step rows for each book, in order. */
function waterfallRows(rows: WaterfallLine[], books: string[], headers: boolean): Row[] {
  const out: Row[] = [];
  books.forEach((b, bi) => {
    const steps = waterfallSteps(rows, b);
    if (!steps.length) return;
    const book = WATERFALL_LABEL[b] ?? b;
    if (headers) out.push({ key: `${b}|__header`, seq: bi * 1000, header: true, book, waterfall: b });
    steps.forEach((s, i) => out.push({ ...s, key: `${b}|${i}`, seq: bi * 1000 + i + 1, header: false }));
  });
  return out;
}

function spec(values: Row[]) {
  return {
    $schema: VL_SCHEMA,
    height: { step: 30 },
    data: { values },
    encoding: { y: Y },
    layer: layers(),
    padding: { right: 52 },
  };
}

/** One book as a horizontal waterfall. */
export function waterfallSpec(rows: WaterfallLine[], waterfall: string) {
  return spec(waterfallRows(rows, [waterfall], false));
}

/**
 * Several books as one figure (a header row per book, one shared dollar axis so the books are
 * comparable). Static: no dashboard group.
 */
export function waterfallFacetSpec(rows: WaterfallLine[], books: string[] = WATERFALL_ORDER) {
  return spec(waterfallRows(rows, books, true));
}

/**
 * How each book's finding is worded. Only words live here; every number is read from the book's lines.
 * `kept` books compare the residual with the gross (paid / subtotal / single take); `trend` books
 * compare the first and last periods of their first layer.
 */
const FINDING_WORDS: Record<string, { kind: 'kept'; gross: string; keeper: string; after: string } | { kind: 'trend'; subject: string }> = {
  card_acquiring_us: { kind: 'kept', gross: 'the merchant pays', keeper: 'the acquirer and network keep', after: 'after the issuer\'s interchange' },
  bnpl_affirm: { kind: 'kept', gross: 'of revenue', keeper: 'Affirm keeps', after: 'after transaction costs' },
  corporate_card_bill: { kind: 'kept', gross: 'of card fees', keeper: 'BILL keeps', after: 'after rewards' },
  cross_border_wise: { kind: 'trend', subject: 'Wise\'s cross-border take rate (all customers)' },
  cross_border_fsb: { kind: 'trend', subject: 'The G20 average cost of a small-business cross-border payment' },
};

/** Finding for a book's figure title, computed from its lines. */
export function waterfallFinding(rows: WaterfallLine[], waterfall: string) {
  const steps = waterfallSteps(rows, waterfall);
  const money = (v: number) => `$${v.toFixed(2)}`;
  const words = FINDING_WORDS[waterfall];
  const who = steps[0]?.company ?? waterfall;
  const kept = steps.find((s) => s.role === 'residual');
  const takes = steps.filter((s) => s.role === 'take');
  const gross = steps.find((s) => s.role === 'subtotal' || s.role === 'paid') ?? (takes.length === 1 ? takes[0] : undefined);
  if (kept?.value != null && gross?.value != null) {
    const w = words?.kind === 'kept' ? words : { gross: '', keeper: `${who} keeps`, after: '' };
    return {
      title: `Of ${money(gross.value)} ${w.gross} per $100, ${w.keeper} ${money(kept.value)}${w.after ? ` ${w.after}` : ''}`.replace(/\s+/g, ' '),
      kept: kept.value, gross: gross.value,
    };
  }
  const firstLayer = steps[0]?.layer;
  const series = steps.filter((s) => s.value != null && (s.role === 'take' || s.role === 'benchmark') && s.layer === firstLayer);
  const subject = words?.kind === 'trend' ? words.subject : `${who}'s ${series[0]?.label.toLowerCase() ?? 'rate'}`;
  if (series.length >= 2) {
    const a = series[0]; const b = series[series.length - 1];
    const va = a.value as number; const vb = b.value as number;
    const dir = vb < va ? 'fell' : vb > va ? 'rose' : 'held';
    return { title: `${subject} ${dir} from ${money(va)} to ${money(vb)} per $100 (${a.period} to ${b.period})`, kept: null, gross: null };
  }
  const one = series[0];
  return { title: one ? `${subject} was ${money(one.value as number)} per $100 (${one.period})` : `${who}: no chartable lines`, kept: null, gross: null };
}

export function waterfallSubtitle(): string {
  return 'Dollars per $100 of payment volume, from filed revenue and cost lines or published rate sheets. Blue: revenue lines; orange: costs; green: what the company keeps; '
    + 'grey: totals and benchmarks; dashed outline: a step nobody publishes, or a reference rate outside the chain. Books are not added together.';
}
