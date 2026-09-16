import rows from '@data/marts/finllm/summary.json';
import { VL_SCHEMA, series, tok } from '../theme';

/** BERTScore F1 for base vs SEC-LoRA on both base models (emphasis: adapter in blue, base in grey). */
export function finllmSummarySpec() {
  return {
    $schema: VL_SCHEMA,
    data: { values: rows },
    height: 220,
    encoding: {
      x: { field: 'family', type: 'nominal', title: null, axis: { labelAngle: 0 } },
      xOffset: { field: 'variant', type: 'nominal', sort: ['base', '+ SEC LoRA'] },
      y: { field: 'f1', type: 'quantitative', title: 'BERTScore F1 (n = 20 held-out items)', scale: { domain: [0, 1] }, axis: { format: '.1f', tickCount: 6 } },
      color: { field: 'variant', type: 'nominal', sort: ['base', '+ SEC LoRA'], scale: { domain: ['base', '+ SEC LoRA'], range: [series.neutral, series.a] }, legend: { title: null, orient: 'top', direction: 'horizontal' } },
      tooltip: [
        { field: 'label', type: 'nominal', title: 'Model' },
        { field: 'f1', type: 'quantitative', title: 'F1', format: '.4f' },
        { field: 'p', type: 'quantitative', title: 'Precision', format: '.4f' },
        { field: 'r', type: 'quantitative', title: 'Recall', format: '.4f' },
      ],
    },
    layer: [
      { mark: { type: 'bar', size: 24, cornerRadiusEnd: 4 } },
      { mark: { type: 'text', dy: -6, fontSize: 11, color: tok('ink-2') }, encoding: { text: { field: 'f1', type: 'quantitative', format: '.3f' } } },
    ],
  };
}
