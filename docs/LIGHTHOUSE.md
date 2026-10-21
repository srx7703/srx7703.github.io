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
| 2026-09-29 | /projects/memory-cycles/ | 99 | 100 | 100 | 100 | cloud session, Chromium 1194, served gzip-compressed with `serve`; 85 on an uncompressed server (the surgical page scores the same there) after moving two CSV downloads to `src` URLs and compacting the calendar spec (590 KB → 336 KB of HTML) |
| 2026-10-17 (before, gzip-served) | /projects/cybersecurity-landscape/ | 96 | 100 | n/m | n/m | HTML 1,221,587 B (118 KB gzipped); LCP 2.6 s, the `p.dek`. The performance review's measurement, Lighthouse 13.5, `--only-categories=performance,accessibility` |
| 2026-10-17 (before, plain server) | /projects/cybersecurity-landscape/ | 58 | 100 | n/m | n/m | the same build on `python3 -m http.server`, which does not compress: FCP 7.4 s, LCP 8.1 s. The performance review's own measurement |
| 2026-10-17 (components and data files, gzip-served) | /projects/cybersecurity-landscape/ | 96 | 100 | n/m | n/m | the build of this branch with the page's tables still inline; title maps are files fetched when a control first moves, the interval figure's dead duplicate file is gone, the Yahoo-derived marts are no longer served. HTML 1,055,575 B |
| 2026-10-17 (components and data files, plain server) | /projects/cybersecurity-landscape/ | 60 | 100 | n/m | n/m | same build: FCP 6.6 s, LCP 7.3 s, 1,223 KiB transferred |
| 2026-10-17 (page on lazy tables, gzip-served) | /projects/cybersecurity-landscape/ | 98 | 100 | n/m | n/m | a copy of the page that uses the new options: the reported, ETF, China, listing, targets, ratings and ledger tables lazy `<UrlTable>`s over `public/data/cyber/*-table.json`, specs in `<script type="application/json">`. HTML 418,249 B; 241 KiB transferred |
| 2026-10-17 (page on lazy tables, plain server) | /projects/cybersecurity-landscape/ | 81 | 100 | n/m | n/m | same copy: FCP 3.3 s, LCP 4.0 s, 592 KiB transferred. What is left on a plain server is the three fonts (169 KB), `Base.css` (17.5 KB) and the interval figure's data, which sits inline (about 140 KB as JSON) because its empty-view sentence reads the dataset by name |
| 2026-10-21 | /projects/ad-market/ | 99 | 100 | 100 | 100 | local build served by `astro preview` (uncompressed); LCP 2.1 s, FCP 1.4 s; HTML 184,875 B |

Remaining accessibility deductions were link colour contrast (4.2:1); the light-theme accent was
darkened to 6.3:1 in the following commit. The 2026-09-16 restyle (paper ground, serif text, ink links)
brought accessibility to 100 on all three pages. Re-run after design changes and append a row.

**Two numbers for every page of this size.** GitHub Pages serves text gzip-compressed; the repo's own static server
(`python3 -m http.server`) does not. A page of a megabyte reads 96 on the first and about 58 on the
second, so each row for the cybersecurity page says which one it is, and a run on the plain server is not a regression of the page.
To measure the compressed case locally, serve `site/dist` with any server that gzips text (a few lines of Python's
`http.server` with `gzip.compress` do it) and run the same command against it. The remaining weight on a plain server is the
three fonts and `Base.css`, which are site-wide.

**Lighthouse does not scroll, so it does not see the charts.** Charts render when they near the viewport, and an audit that
never scrolls reports 100 for accessibility whatever the drawn charts contain. After changing a chart, scroll every `.vega`
into view in a browser and run axe-core on the page (`axe.run(document)`); on 2026-10-17 that found 34 unlabelled `<svg>` marks in
two charts that Lighthouse had passed.
