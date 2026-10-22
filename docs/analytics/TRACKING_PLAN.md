# Product analytics: tracking plan

The site is treated as a small product. Its "users" are visitors (recruiters, hiring managers, other analysts), and the
questions are the ones a product analyst asks of any product: where do people arrive, do they reach the projects, do they
engage with the data, and do they come back. This file is the contract between the site code
(`site/src/components/Analytics.astro`) and the analysis code (`pipelines/site_analytics/`). Change both together.

Tool: **Mixpanel** (free plan), loaded only when the build has `PUBLIC_MIXPANEL_TOKEN` set. Without it no page loads any
tracking code.

## Privacy

- No personal data is sent: no names, emails, form input or free text. Visitors are identified only by Mixpanel's random
  device id (`distinct_id`) kept in `localStorage`.
- IP-based geolocation is off (`ip: false`), and the browser's Do Not Track setting is respected.
- No autocapture and no session replay: the site loads Mixpanel's core SDK, which does not contain the recorder.
- The site footer says that anonymous usage is measured with Mixpanel.
- Raw event exports never enter this public repository; they are written to `data/private/` (gitignored), and only
  aggregated numbers may be published.

## Events

Every event carries the page context below, so any event can be split by page.

| Property | Values | Meaning |
|---|---|---|
| `page_type` | `home`, `projects_index`, `project`, `how_i_work`, `about`, `changelog`, `other` | which kind of page |
| `project_slug` | e.g. `payments-landscape` (project pages only) | which project |
| `device_type` | `mobile`, `desktop` | viewport width below / at least 768 px |
| `theme` | `light`, `dark` | colour theme in use |

| Event | Fired when | Extra properties |
|---|---|---|
| `Page Viewed` | once per page load | `referrer_domain`, `utm_source`, `utm_medium`, `utm_campaign` |
| `Project Opened` | a project card (home or Projects page) is clicked | `target_slug`, `project_status`, `card_position` |
| `Chart Interacted` | first click, or first 1.5 s hover, on a chart; once per chart per page view | `chart_id`, `interaction` (`hover`, `click`) |
| `Dashboard Filter Changed` | a dashboard control changes | `dashboard_group`, `control`, `value` |
| `CSV Downloaded` | a "Download CSV" button is clicked | `filename` |
| `Outbound Link Clicked` | a link to another site is clicked | `destination_domain`, `link_type` (`linkedin`, `github`, `source`, `other`) |
| `Scroll Depth Reached` | the reader passes 25 / 50 / 75 / 100 % of the page; once per mark per page view | `depth` |
| `Page Engaged` | the page has been visible for 30 s in total | `seconds` |

Naming: events are `Object Verb` in Title Case (Mixpanel convention); properties are `snake_case`.

Delivery: events wait in a `localStorage` queue and are sent in batches every few seconds. `Project Opened` and
`Outbound Link Clicked` happen as the visitor leaves the page, so they skip the queue and go out at once by
`sendBeacon`, which the browser completes after navigation. Mixpanel also adds its default properties (browser, OS,
screen size, current URL, referrer) and keeps the latest UTM values seen on the device, so a visitor's later events
carry the campaign that brought them (last-touch attribution).

## Metrics built on these events

Each one is computed from the raw export by `pipelines/site_analytics/metrics.py` (DuckDB SQL, unit-tested on a small
synthetic log) and can be cross-checked in Mixpanel's Funnels, Retention and Insights reports.

**Activation funnel** (per visitor, steps in order, within 1 day):
`Page Viewed` → `Page Viewed` with `page_type = project` → `Chart Interacted` → `CSV Downloaded` or `Outbound Link Clicked`.
The conversion between adjacent steps shows where readers drop off.

**Engagement**: share of project-page views that reach `Page Engaged`, and share that reach `Scroll Depth Reached` 75
(two separate rates; both events fire at most once per page view).

**Retention**: weekly cohorts by first `Page Viewed`; a visitor is retained in week *k* if they have any `Page Viewed` in
that week.

**Feature adoption**: for each project page with dashboards, share of visitor-days on it that include at least one
`Dashboard Filter Changed` (events carry no page-view id, so a visitor-day stands in for a visit).

**High-intent visitor** (the product-qualified-lead idea from product-led growth): within 7 days, at least 2 distinct
project pages viewed **and** at least one `Chart Interacted`, `CSV Downloaded` or `Outbound Link Clicked` to LinkedIn or
GitHub. Counted weekly, it is the "signal" a sales or customer-success team would receive in a SaaS company.

## PLG hand-off (phase 2)

Visitors to this site are anonymous, so there is no real account to hand to a CRM. Phase 2 practises the hand-off on a
clearly labelled synthetic B2B dataset instead: score accounts with the high-intent rule above, then push the qualified
ones to a free HubSpot CRM as company records with a `pql_score` property and an owner task. Nothing synthetic is ever
mixed with the site's real numbers.

## Setup

1. Create a free Mixpanel project (US data residency) and copy its **project token** (public by design: it is sent by
   every browser).
2. In the GitHub repository settings add a repository **variable** (not a secret) `MIXPANEL_PROJECT_TOKEN` with that value.
   The deploy workflow passes it to the build as `PUBLIC_MIXPANEL_TOKEN`.
3. For analysis, create a Mixpanel **service account** and set `MIXPANEL_PROJECT_ID`, `MIXPANEL_SERVICE_ACCOUNT` and
   `MIXPANEL_SERVICE_SECRET` in a local `.env`; never commit them.
4. Local check: `PUBLIC_MIXPANEL_TOKEN=<token> PUBLIC_MIXPANEL_DEBUG=1 npm run build` in `site/`, serve `site/dist`, and
   watch the browser console: every event is logged before it is sent.
5. Analysis: `uv run --env-file .env python -m pipelines.site_analytics.export <from> <to>` writes the raw events to
   `data/private/site_analytics/`; `uv run python -m pipelines.site_analytics.metrics <that file>` prints the tables.
