import { VL_SCHEMA, series, tok } from '../theme';
import type { PhaseRow } from './calendar';

/**
 * Every rising phase of the registered calendar: how long it lasted against how far the index rose.
 * DRAM and flash are the two series; the running upswing is hollow because its end is the last month
 * published, not a confirmed peak. Linear axes: the rises span one order of magnitude, not several.
 */
export function uplegSpec(phases: PhaseRow[]) {
  const values = phases
    .filter((p) => p.from_kind === 'T')
    .map((p) => ({
      product: p.product === 'DRAM' ? 'DRAM' : 'Flash',
      from: p.from, to: p.to, months: p.months, change: p.change,
      status: p.to_confirmed ? 'Confirmed peak' : 'Running (last month published)',
      label: p.to.slice(0, 4),
    }));
  // year labels only where they cannot collide: the running upswings and rises of at least 100%
  const labelled = values.filter((v) => v.status !== 'Confirmed peak' || v.change >= 1);
  const color = { field: 'product', type: 'nominal', scale: { domain: ['DRAM', 'Flash'], range: [series.a, series.b] },
                  legend: { title: null, orient: 'top', direction: 'horizontal' } };
  const x = { field: 'months', type: 'quantitative', title: 'Months from trough to peak', scale: { zero: true } };
  const y = { field: 'change', type: 'quantitative', title: 'Rise of the contract-price index', axis: { format: '.0%', tickCount: 5 }, scale: { zero: true } };
  return {
    $schema: VL_SCHEMA,
    height: 280,
    data: { values },
    layer: [
      {
        transform: [{ filter: "datum.status === 'Confirmed peak'" }],
        mark: { type: 'point', filled: true, size: 70, opacity: 0.9 },
        encoding: {
          x, y, color,
          tooltip: [{ field: 'product', title: 'Product' }, { field: 'from', title: 'Trough' }, { field: 'to', title: 'Peak' },
                    { field: 'months', title: 'Months' }, { field: 'change', title: 'Rise', format: '+.0%' }],
        },
      },
      {
        transform: [{ filter: "datum.status !== 'Confirmed peak'" }],
        mark: { type: 'point', filled: false, size: 110, strokeWidth: 2 },
        encoding: {
          x, y, color,
          tooltip: [{ field: 'product', title: 'Product' }, { field: 'from', title: 'Trough' }, { field: 'to', title: 'Last month published' },
                    { field: 'months', title: 'Months so far' }, { field: 'change', title: 'Rise so far', format: '+.0%' }],
        },
      },
      {
        data: { values: labelled },
        mark: { type: 'text', align: 'left', dx: 7, dy: -2, fontSize: 10 },
        encoding: { x, y, text: { field: 'label' }, color: { value: tok('ink-3') } },
      },
    ],
  };
}
