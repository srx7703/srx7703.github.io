/**
 * Chart theme. Colours are emitted as `token:--name` strings and resolved against the CSS
 * tokens at render time (VegaChart.astro), so one spec follows the light/dark theme and the
 * palette is defined once in tokens.css. Series order is fixed site-wide (docs/CHART_RULES.md).
 */
export const tok = (name: string): string => `token:--${name}`;

export const series = {
  polymarket: tok('series-polymarket'),
  kalshi: tok('series-kalshi'),
  dem: tok('series-dem'),
  rep: tok('series-rep'),
  neutral: tok('series-neutral'),
  a: tok('series-a'),
  b: tok('series-b'),
  c: tok('series-c'),
  cut: tok('series-cut'),
  hold: tok('series-hold'),
  hike: tok('series-hike'),
};

export const SIDE_DOMAIN = ['cut', 'hold', 'hike'];
export const SIDE_RANGE = [series.cut, series.hold, series.hike];
export const SIDE_LABEL_EXPR = "datum.label == 'cut' ? 'Cut' : datum.label == 'hike' ? 'Hike' : 'Hold'";

export const PLATFORM_DOMAIN = ['polymarket', 'kalshi'];
export const PLATFORM_RANGE = [series.polymarket, series.kalshi];
export const PLATFORM_LABEL_EXPR = "datum.label == 'polymarket' ? 'Polymarket' : 'Kalshi'";

export const VL_SCHEMA = 'https://vega.github.io/schema/vega-lite/v6.json';
