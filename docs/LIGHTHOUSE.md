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
| 2026-09-16 (restyle, opsz fonts) | / | 91 | 100 | 100 | 100 | Newsreader with the opsz axis: 129 + 143 KB latin files behind the stylesheet |
| 2026-09-16 (restyle, opsz fonts) | /projects/fomc-markets/ | 89 | 100 | 100 | 100 | |
| 2026-09-16 (restyle, opsz fonts) | /projects/saas-benchmark/ | 82 | 100 | 100 | 100 | LCP 3.6 s |
| 2026-09-16 (restyle, final) | / | 99 | 100 | 100 | 100 | weight-only Newsreader (58 KB) + IBM Plex Sans (45 KB), both preloaded |
| 2026-09-16 (restyle, final) | /projects/fomc-markets/ | 99 | 100 | 100 | 100 | |
| 2026-09-16 (restyle, final) | /projects/saas-benchmark/ | 94 | 100 | 100 | 100 | LCP 2.7 s; the 151 KB document (inlined benchmark table) is the next lever |

Remaining accessibility deductions were link colour contrast (4.2:1); the light-theme accent was
darkened to 6.3:1 in the following commit. The 2026-09-16 restyle (paper ground, serif text, ink links)
brought accessibility to 100 on all three pages. Re-run after design changes and append a row.
