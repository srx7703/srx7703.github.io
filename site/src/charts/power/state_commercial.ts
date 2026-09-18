import { VL_SCHEMA, series } from '../theme';

export type StateYear = { state: string; year: number; twh: number; months: number; complete_year: boolean };

const STATE_LABEL: Record<string, string> = {
  VA: 'Virginia', TX: 'Texas', GA: 'Georgia', OH: 'Ohio',
  LA: 'Louisiana', AZ: 'Arizona', IL: 'Illinois',
};

export const stateLabel = (s: string) => STATE_LABEL[s] ?? s;

/**
 * Part-years are dropped from the plot, not annualised.
 *
 * EIA's monthly file runs a few months behind, so the current year is always incomplete. Plotting
 * six months beside twelve draws a collapse that did not happen; scaling it by two invents a number.
 * The page states which year the lines stop at and prints the partial year in the table instead.
 */
export function completeYears(rows: StateYear[]): StateYear[] {
  return rows.filter((r) => r.complete_year);
}

export function partialYears(rows: StateYear[]): StateYear[] {
  return rows.filter((r) => !r.complete_year);
}

/**
 * Commercial-sector electricity sales, indexed so states of very different size share one axis.
 *
 * This is the closest public trace of the data-center build-out, and it is a trace rather than a
 * measurement: the commercial sector also holds every office, hospital and supermarket in the
 * state. What makes it readable is that those grow with population, a few percent a decade, so a
 * state pulling away from the others is showing something else. The page names what.
 */
export function stateCommercialSpec(rows: StateYear[], highlight = 'VA') {
  const complete = completeYears(rows);
  const years = [...new Set(complete.map((r) => r.year))].sort((a, b) => a - b);
  const first = years[0];
  const firstByState = new Map<string, number>();
  complete.filter((r) => r.year === first).forEach((r) => firstByState.set(r.state, r.twh));
  const values = complete
    .filter((r) => firstByState.get(r.state))
    .map((r) => ({
      ...r,
      label: stateLabel(r.state),
      indexed: r.twh / (firstByState.get(r.state) as number),
      focus: r.state === highlight ? stateLabel(highlight) : 'Other states',
    }));
  const last = years[years.length - 1];
  const endLabels = values.filter((v) => v.year === last);
  return {
    $schema: VL_SCHEMA,
    height: 280,
    data: { values },
    layer: [
      {
        mark: { type: 'line', strokeWidth: 2, point: { size: 22, filled: true } },
        encoding: {
          x: { field: 'year', type: 'ordinal', title: null, axis: { labelAngle: 0 } },
          y: {
            field: 'indexed', type: 'quantitative',
            title: `Commercial sales, ${first} = 1.0`,
            scale: { zero: false }, axis: { format: '.2f', tickCount: 5 },
          },
          color: {
            field: 'focus', type: 'nominal',
            scale: { domain: [stateLabel(highlight), 'Other states'], range: [series.a, series.neutral] },
            legend: { title: null, orient: 'top', direction: 'horizontal', symbolType: 'stroke' },
          },
          detail: { field: 'state', type: 'nominal' },
          opacity: { condition: { test: `datum.state === '${highlight}'`, value: 1 }, value: 0.45 },
          tooltip: [
            { field: 'label', type: 'nominal', title: 'State' },
            { field: 'year', type: 'ordinal', title: 'Year' },
            { field: 'twh', type: 'quantitative', title: 'TWh', format: '.1f' },
            { field: 'indexed', type: 'quantitative', title: `vs ${first}`, format: '.2f' },
          ],
        },
      },
      {
        data: { values: endLabels },
        mark: { type: 'text', align: 'left', dx: 7, fontSize: 10 },
        encoding: {
          x: { field: 'year', type: 'ordinal' },
          y: { field: 'indexed', type: 'quantitative' },
          text: { field: 'label', type: 'nominal' },
          // A condition rather than a second scale: two scales over the same field, one with a
          // legend and one without, make Vega resolve a conflicting "disable" and warn. The end
          // labels need the colour, not a legend entry, so they never declare a scale at all.
          color: {
            condition: { test: `datum.state === '${highlight}'`, value: series.a },
            value: series.neutral,
          },
        },
      },
    ],
  };
}
