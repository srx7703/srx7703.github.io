import { VL_SCHEMA, series } from '../theme';

export type TenderRow = {
  notice_id: string; hospital: string; province: string; brand: string; model: string;
  maker: string; quantity: number | null; unit_price_cny: number | null;
  award_date: string; contract_kind: string; caveat: string; source_url: string;
};

const MAKER_NAME: Record<string, string> = {
  ISRG: 'Intuitive (imported and JV)',
  '2675.HK': 'Edge Medical',
  '2252.HK': 'MicroPort MedBot',
  'private:sizhirui': 'Sizhirui',
  'private:surgerii': 'Surgerii',
  'private:cornerstone': 'Cornerstone',
  'private:toodo': 'Toodo',
  'private:ronovo': 'Ronovo',
};

export const makerName = (m: string, brand: string) => MAKER_NAME[m] ?? brand ?? 'Not attributed';

/** Purchases only. A lease fee and a service contract are not what a machine costs. */
export function pricedPurchases(rows: TenderRow[]): TenderRow[] {
  return rows.filter((r) => r.contract_kind === 'purchase' && r.unit_price_cny);
}

/**
 * Every award as its own dot, by manufacturer.
 *
 * A dot per award rather than a bar per median, because the spread is the finding and a median would hide it.
 * The page says beside this chart what the spread is NOT: no notice discloses arm count, console count,
 * instrument package or warranty term, so a cheap award may simply be a smaller configuration. Read it as a
 * range of what hospitals paid, never as a vendor's price list.
 */
export function tenderPriceSpec(rows: TenderRow[]) {
  const values = pricedPurchases(rows).map((r) => ({
    ...r,
    maker_label: makerName(r.maker, r.brand),
    price_m: (r.unit_price_cny as number) / 1e6,
    year: r.award_date.slice(0, 4),
  }));
  const order = [...new Set(values.map((v) => v.maker_label))].sort(
    (a, b) => values.filter((v) => v.maker_label === b).length - values.filter((v) => v.maker_label === a).length,
  );
  return {
    $schema: VL_SCHEMA,
    height: Math.max(200, order.length * 34),
    data: { values },
    layer: [
      {
        mark: { type: 'tick', thickness: 2, size: 18, opacity: 0.55 },
        encoding: {
          y: { field: 'maker_label', type: 'nominal', sort: order, title: null,
               axis: { labelFontSize: 11, labelLimit: 190 } },
          x: { field: 'price_m', type: 'quantitative', title: 'Award price, CNY million per system',
               scale: { zero: true }, axis: { tickCount: 6 } },
          color: { value: series.a },
          tooltip: [
            { field: 'hospital', type: 'nominal', title: 'Hospital' },
            { field: 'brand', type: 'nominal', title: 'Brand' },
            { field: 'model', type: 'nominal', title: 'Model' },
            { field: 'price_m', type: 'quantitative', title: 'CNY m', format: '.2f' },
            { field: 'award_date', type: 'nominal', title: 'Awarded' },
            { field: 'province', type: 'nominal', title: 'Province' },
          ],
        },
      },
      {
        mark: { type: 'point', shape: 'diamond', size: 70, filled: true, opacity: 1 },
        transform: [{ aggregate: [{ op: 'median', field: 'price_m', as: 'med' }], groupby: ['maker_label'] }],
        encoding: {
          y: { field: 'maker_label', type: 'nominal', sort: order },
          x: { field: 'med', type: 'quantitative' },
          color: { value: series.b },
          tooltip: [{ field: 'med', type: 'quantitative', title: 'Median, CNY m', format: '.2f' }],
        },
      },
    ],
  };
}
