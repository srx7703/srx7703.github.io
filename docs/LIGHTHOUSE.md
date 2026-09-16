# Lighthouse scores

Run locally with `npx lighthouse@latest <url> --chrome-flags="--headless=new" --only-categories=performance,accessibility,best-practices,seo`
against the built site served from `site/dist` (mobile emulation, simulated throttling). PageSpeed
Insights was rate-limited from the build machine on 2026-09-16, so these are local numbers.

| Date | Page | Performance | Accessibility | Best practices | SEO | Notes |
|---|---|---|---|---|---|---|
| 2026-09-16 (before fixes) | /projects/saas-benchmark/ | 64 | 91 | 100 | 100 | vega-embed loaded eagerly (LCP 5.9 s); heading order; aria-sort on role=button |
| 2026-09-16 (after fixes) | /projects/saas-benchmark/ | 98 | 97 | 100 | 100 | chart library lazy-loaded on scroll; link contrast raised afterwards |
| 2026-09-16 (after fixes) | / | 100 | 95 | 100 | 100 | |
| 2026-09-16 (after fixes) | /projects/fomc-markets/ | 100 | 96 | 100 | 100 | |

Remaining accessibility deductions were link colour contrast (4.2:1); the light-theme accent was
darkened to 6.3:1 in the following commit. Re-run after design changes and append a row.
