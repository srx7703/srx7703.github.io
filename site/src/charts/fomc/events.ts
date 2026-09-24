import { VL_SCHEMA, series, tok } from '../theme';

/**
 * Release event study. The scatter puts each release's surprise (first print minus the Kalshi ladder's
 * mean ten minutes before) against the change in the expected move over the next two meetings from ten
 * minutes before to twenty minutes after, on the reaction platform. The grey band holds 90% of moves in
 * the same clock window on quiet days. Surprises are in each release's own unit, so each release type
 * gets its own panel; the y axis is shared so the panels compare.
 */
const UNITS: Record<string, { title: string; format: string }> = {
  cpi: { title: 'CPI surprise, pp (print − ladder mean)', format: '+.2f' },
  jobs: { title: 'Payrolls surprise, thousands', format: '+.0f' },
  pce: { title: 'Core PCE surprise, pp', format: '+.2f' },
};

export function fomcEventScatterSpec(
  release: 'cpi' | 'jobs' | 'pce',
  band: number | null,
  yMax: number,
  platform: 'polymarket' | 'kalshi' = 'polymarket',
) {
  const u = UNITS[release];
  const other = platform === 'polymarket' ? 'kalshi' : 'polymarket';
  const name = { polymarket: 'Polymarket', kalshi: 'Kalshi' };
  const tooltip = [
    { field: 'date', type: 'nominal', title: 'Release day' },
    { field: 'consensus', type: 'quantitative', title: 'Ladder mean' },
    { field: 'actual', type: 'quantitative', title: 'First print' },
    { field: 'surprise', type: 'quantitative', title: 'Surprise', format: u.format },
    { field: `${platform}.d_post`, type: 'quantitative', title: `${name[platform]}, 20 min (bps)`, format: '+.1f' },
    { field: `${platform}.d_close`, type: 'quantitative', title: `${name[platform]}, to 16:00 (bps)`, format: '+.1f' },
    { field: `${other}.d_post`, type: 'quantitative', title: `${name[other]}, 20 min (bps)`, format: '+.1f' },
  ];
  const y = {
    field: `${platform}.d_post`,
    type: 'quantitative',
    title: 'Move in expected bps',
    scale: { domain: [-yMax, yMax] },
    axis: { tickCount: 5, minExtent: 36, maxExtent: 36 },
  };
  return {
    $schema: VL_SCHEMA,
    data: { url: '/data/marts/predmarkets/fomc_event_study.json' },
    transform: [
      { filter: `datum.release == '${release}' && isValid(datum.surprise) && isValid(datum.${platform}.d_post)` },
    ],
    height: 220,
    layer: [
      ...(band != null
        ? [{
            data: { values: [{ lo: -band, hi: band }] },
            mark: { type: 'rect', opacity: 0.35 },
            encoding: {
              y: { field: 'lo', type: 'quantitative', scale: { domain: [-yMax, yMax] } },
              y2: { field: 'hi' },
              color: { value: tok('grid') },
            },
          }]
        : []),
      {
        data: { values: [{ v: 0 }] },
        mark: { type: 'rule', color: tok('border-strong'), opacity: 0.4 },
        encoding: { y: { field: 'v', type: 'quantitative' } },
      },
      {
        data: { values: [{ v: 0 }] },
        mark: { type: 'rule', color: tok('border-strong'), opacity: 0.4 },
        encoding: { x: { field: 'v', type: 'quantitative' } },
      },
      {
        mark: { type: 'point', filled: true, size: 70, stroke: tok('surface'), strokeWidth: 1.5 },
        encoding: {
          x: { field: 'surprise', type: 'quantitative', title: u.title, axis: { format: u.format.replace('+', ''), tickCount: 5 } },
          y,
          color: { value: tok('ink') },
          tooltip,
        },
      },
    ],
  };
}

/**
 * Mean path of the expected move around a release, split by the sign of the surprise (hawkish = a hotter
 * print or stronger payrolls). Colours follow the page's direction pair: hawkish takes the hike red,
 * dovish the cut blue.
 */
export function fomcEventPathSpec(release: string, pre: number, post: number, platform: 'polymarket' | 'kalshi' = 'polymarket') {
  const domain = ['hawkish', 'dovish'];
  return {
    $schema: VL_SCHEMA,
    data: { url: '/data/marts/predmarkets/fomc_event_paths.json' },
    transform: [{ filter: `datum.release == '${release}' && datum.platform == '${platform}' && datum.sign != 'in line'` }],
    height: 220,
    encoding: {
      x: { field: 'minute', type: 'quantitative', title: 'Minutes from the release', axis: { values: [-30, -10, 0, 20, 40, 60], grid: false } },
    },
    layer: [
      {
        data: { values: [{ m: -pre, what: 'Baseline read' }, { m: 0, what: 'Release' }, { m: post, what: 'Reaction read' }] },
        mark: { type: 'rule', strokeDash: [4, 4], opacity: 0.6 },
        encoding: { x: { field: 'm', type: 'quantitative' }, color: { value: tok('ink-3') }, tooltip: [{ field: 'what', title: 'Marker' }] },
      },
      {
        mark: { type: 'line', strokeWidth: 2, interpolate: 'step-after' },
        encoding: {
          y: { field: 'd_bps', type: 'quantitative', title: 'Mean change in expected bps', axis: { tickCount: 5 } },
          color: {
            field: 'sign', type: 'nominal',
            scale: { domain, range: [series.hike, series.cut] },
            legend: {
              title: null, orient: 'top', direction: 'horizontal', symbolType: 'stroke', columns: 1,
              labelExpr: "datum.label == 'hawkish' ? 'Hawkish surprise' : 'Dovish surprise'",
            },
          },
          tooltip: [
            { field: 'sign', title: 'Surprise' },
            { field: 'minute', title: 'Minute' },
            { field: 'd_bps', title: 'Mean change (bps)', format: '+.2f' },
            { field: 'n', title: 'Events' },
          ],
        },
      },
    ],
  };
}
