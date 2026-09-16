import paired from '@data/marts/finllm/paired.json';
import { VL_SCHEMA, series, tok } from '../theme';

/** Per-item BERTScore F1, base vs adapter, one dumbbell per held-out item, sorted by gain. */
export function finllmPairedSpec(family: 'gemma4' | 'gemma2' = 'gemma4') {
  const rows = (paired as Record<string, { item: number; base: number; lora: number; delta: number }[]>)[family]
    .slice()
    .sort((a, b) => b.delta - a.delta)
    .map((r, i) => ({ ...r, rank: i + 1, label: `item ${r.item}` }));
  const long = rows.flatMap((r) => [
    { ...r, variant: 'base', f1: r.base },
    { ...r, variant: '+ SEC LoRA', f1: r.lora },
  ]);
  return {
    $schema: VL_SCHEMA,
    height: rows.length * 16,
    encoding: { y: { field: 'label', type: 'nominal', sort: rows.map((r) => r.label), title: null, axis: { labelFontSize: 10 } } },
    layer: [
      {
        data: { values: rows },
        mark: { type: 'rule', strokeWidth: 2, color: series.neutral },
        encoding: { x: { field: 'base', type: 'quantitative', title: 'BERTScore F1 per item', scale: { domain: [0.6, 1.0] }, axis: { format: '.2f', tickCount: 5 } }, x2: { field: 'lora' } },
      },
      {
        data: { values: long },
        mark: { type: 'point', filled: true, size: 60, stroke: tok('surface'), strokeWidth: 1.5 },
        encoding: {
          x: { field: 'f1', type: 'quantitative' },
          color: { field: 'variant', type: 'nominal', scale: { domain: ['base', '+ SEC LoRA'], range: [series.neutral, series.a] }, legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'circle' } },
          tooltip: [
            { field: 'label', type: 'nominal', title: 'Item' },
            { field: 'variant', type: 'nominal', title: 'Model' },
            { field: 'f1', type: 'quantitative', title: 'F1', format: '.4f' },
            { field: 'delta', type: 'quantitative', title: 'Gain', format: '+.4f' },
          ],
        },
      },
    ],
  };
}
