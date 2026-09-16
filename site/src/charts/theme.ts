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
};

export const PLATFORM_DOMAIN = ['polymarket', 'kalshi'];
export const PLATFORM_RANGE = [series.polymarket, series.kalshi];
export const PLATFORM_LABEL_EXPR = "datum.label == 'polymarket' ? 'Polymarket' : 'Kalshi'";

export const VL_SCHEMA = 'https://vega.github.io/schema/vega-lite/v6.json';
