import { VL_SCHEMA, series, tok } from '../theme';
import { SEGMENT_LABEL } from './share_data';

export type ProductRow = {
  company: string; segment: string; product_line: string; product_status: string;
  launch_date: string | null; exit_date: string | null; as_of: string; last_checked: string;
  stale: boolean; tag: string; source_name: string; source_url: string; caveat: string | null;
};

/** The nine product lines, in the order the layers converge (acquiring outward). */
export const PRODUCT_LINES = ['online_acquiring', 'in_person_pos', 'bnpl', 'corporate_card', 'ap_bill_pay',
  'banking_accounts', 'card_issuing_platform', 'cross_border_accounts', 'stablecoin_payments'];
export const PRODUCT_LABEL: Record<string, string> = {
  online_acquiring: 'Online acquiring', in_person_pos: 'In-person POS', bnpl: 'Buy now, pay later',
  corporate_card: 'Corporate card', ap_bill_pay: 'Bill pay (AP)', banking_accounts: 'Business accounts',
  card_issuing_platform: 'Card issuing for platforms', cross_border_accounts: 'Cross-border accounts',
  stablecoin_payments: 'Stablecoin payments',
};
export const STATUS_LABEL: Record<string, string> = {
  live: 'Live', announced: 'Announced', discontinued: 'Discontinued', none: 'Checked: not offered',
};
const STATUSES = ['live', 'announced', 'discontinued', 'none'];

export type ProductCell = ProductRow & {
  line_label: string; status_label: string; segment_label: string; live_lines: number; launched: string;
};

/** Mark-ready cells: one per company and product line that has a curated row (stale rows kept, faded). */
export function productCells(rows: ProductRow[]): ProductCell[] {
  const live = new Map<string, number>();
  rows.forEach((r) => { if (r.product_status === 'live') live.set(r.company, (live.get(r.company) ?? 0) + 1); });
  return rows
    .filter((r) => PRODUCT_LINES.includes(r.product_line))
    .map((r) => ({
      ...r,
      line_label: PRODUCT_LABEL[r.product_line] ?? r.product_line,
      status_label: STATUS_LABEL[r.product_status] ?? r.product_status,
      segment_label: `${r.segment} · ${SEGMENT_LABEL[r.segment] ?? r.segment}`,
      live_lines: live.get(r.company) ?? 0,
      launched: r.launch_date ?? 'not stated',
    }));
}

/**
 * Product convergence: which companies sell which of the nine product lines, grouped by layer
 * (the company's largest revenue source), each group opened by a header row. A filled circle is
 * live, a hollow circle announced, a cross discontinued, a small grey dot means the line was checked
 * and is not offered; an empty cell has no curated row. One hue throughout: status is carried by
 * shape and fill, not colour. Stale rows (overdue for a re-check) are faded. Static: no dashboard group.
 *
 * A single (unfaceted) view, so VegaChart sizes it to the container with autosize fit-x: column
 * headers stand vertical at the top and the nine columns share whatever width is left after the
 * company names, so the matrix fits a 320px phone column without being scaled down.
 */
export function productsSpec(rows: ProductRow[]) {
  const cells = productCells(rows);
  const segments = [...new Set(cells.map((v) => v.segment))].sort();
  const values: Record<string, unknown>[] = [];
  segments.forEach((seg, si) => {
    const here = cells.filter((c) => c.segment === seg);
    const companies = [...new Set(here.map((c) => c.company))]
      .sort((a, b) => (here.find((c) => c.company === b)!.live_lines - here.find((c) => c.company === a)!.live_lines) || a.localeCompare(b));
    const label = `${seg} · ${SEGMENT_LABEL[seg] ?? seg}`;
    values.push({ key: `${seg}|`, seq: si * 1000, header: true, segment_label: label });
    companies.forEach((co, ci) => {
      here.filter((c) => c.company === co).forEach((c) => values.push({ ...c, key: `${seg}|${co}`, seq: si * 1000 + ci + 1, header: false }));
    });
  });
  const narrow = '(containerSize()[0] || 640) < 480';
  // Fixed-width company column, so the group headers can start at its left edge.
  const extent = `(${narrow} ? 104 : 148)`;
  const isHeader = "indexof(datum.value, '|') == length(datum.value) - 1";
  return {
    $schema: VL_SCHEMA,
    height: { step: 20 },
    data: { values },
    encoding: {
      y: {
        field: 'key', type: 'nominal', title: null, sort: { field: 'seq', op: 'min' },
        axis: {
          labelExpr: "split(datum.value, '|')[1]", labelLimit: { expr: `${extent} - 8` }, minExtent: { expr: extent }, maxExtent: { expr: extent },
          grid: true, gridOpacity: { condition: { test: isHeader, value: 0 }, value: 1 },
          ticks: false, domain: false, labelFontSize: { expr: `${narrow} ? 10.5 : 12` },
        },
      },
    },
    layer: [
      {
        transform: [{ filter: '!datum.header' }],
        mark: { type: 'point', strokeWidth: 1.6 },
        encoding: {
          x: {
            field: 'line_label', type: 'nominal', title: null,
            scale: { domain: PRODUCT_LINES.map((l) => PRODUCT_LABEL[l]) },
            axis: {
              orient: 'top', labelAngle: -90, labelAlign: 'left', labelBaseline: 'middle',
              labelLimit: 140, labelFontSize: { expr: `${narrow} ? 10.5 : 11.5` }, grid: true, ticks: false, domain: false,
            },
          },
          shape: {
            field: 'product_status', type: 'nominal',
            scale: { domain: STATUSES, range: ['circle', 'circle', 'cross', 'circle'] },
            legend: null,
          },
          size: {
            condition: [
              { test: "datum.product_status == 'none'", value: 12 },
              { test: "datum.product_status == 'discontinued'", value: 55 },
            ],
            value: 70,
          },
          stroke: {
            condition: { test: "datum.product_status == 'live' || datum.product_status == 'announced'", value: series.a },
            value: series.neutral,
          },
          fill: {
            condition: [
              { test: "datum.product_status == 'live'", value: series.a },
              { test: "datum.product_status == 'none'", value: series.neutral },
            ],
            value: 'transparent',
          },
          opacity: { condition: { test: 'datum.stale', value: 0.4 }, value: 1 },
          tooltip: [
            { field: 'company', type: 'nominal', title: 'Company' },
            { field: 'line_label', type: 'nominal', title: 'Product line' },
            { field: 'status_label', type: 'nominal', title: 'Status' },
            { field: 'launched', type: 'nominal', title: 'Launched' },
            { field: 'last_checked', type: 'nominal', title: 'Last checked' },
            { field: 'source_name', type: 'nominal', title: 'Source' },
            { field: 'caveat', type: 'nominal', title: 'Caveat' },
          ],
        },
      },
      {
        // Group header: the layer name on its own row, starting under the company names.
        transform: [{ filter: 'datum.header' }],
        mark: { type: 'text', fontSize: 11.5, fontWeight: 600, color: tok('ink'), align: 'left', baseline: 'middle', dy: 2, dx: { expr: `-${extent}` }, limit: { expr: 'max(150, (containerSize()[0] || 640) - 92)' } },
        encoding: { x: { value: 0 }, text: { field: 'segment_label', type: 'nominal' } },
      },
    ],
  };
}

/** Finding for the figure title, computed: the broadest company and how many sell four lines or more. */
export function productsFinding(rows: ProductRow[], threshold = 4) {
  const live = new Map<string, number>();
  rows.forEach((r) => { if (r.product_status === 'live' && !r.stale) live.set(r.company, (live.get(r.company) ?? 0) + 1); });
  const ranked = [...live].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const top = ranked[0];
  const broad = ranked.filter(([, n]) => n >= threshold).length;
  const companies = new Set(rows.map((r) => r.company)).size;
  if (!top) return { title: 'Product lines by company', top: null, broad: 0, companies };
  return {
    title: `${top[0]} sells ${top[1]} of the ${PRODUCT_LINES.length} product lines; ${broad} of ${companies} companies sell ${threshold} or more`,
    top: { company: top[0], lines: top[1] }, broad, companies,
  };
}

export function productsSubtitle(): string {
  return 'Product lines each company sells, grouped by its largest revenue source. Filled circle: live; hollow: announced; cross: discontinued; '
    + 'small grey dot: checked and not offered; empty: no curated row. Faded: overdue for a re-check. From company product pages and releases.';
}
