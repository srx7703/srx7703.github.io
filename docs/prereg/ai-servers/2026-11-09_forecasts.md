# Pre-registered forecasts: Taiwan November 2026 revenue, Dell and HPE server quarters, Q4 records

Registered 2026-11-09, before any of the figures below is published. Append-only; scoring is added in an amendment
section and never by editing a forecast.

## F1. November 2026 monthly revenue of eight Taiwan makers (published on MOPS by 2026-12-10)

Method (code: `pipelines/servers/forecast.py`, at the commit that adds this file): point = the company's November
2025 revenue × (1 + the mean year-over-year growth of its three most recent published months). The 80% interval
multiplies the point by the 10th and 90th percentiles of the same formula's one-month-ahead errors (actual /
forecast) for that company over 2019-01 to the month before the latest one used. MiTAC had not published October when
this was registered, so its three months end in September. Revenue in thousands of New Taiwan dollars, consolidated,
as first shown on MOPS.

| Company | Months used | Nov 2025 | Mean YoY used | Point | 80% interval | Backtest months | Backtest median abs error |
|---|---|---|---|---|---|---|---|
| 2382 Quanta Computer | 2026-08, 2026-09, 2026-10 | 192,946,811 | +119.4% | 423,262,795 | 316,753,764 – 557,613,014 | 93 | 12.8% |
| 6669 Wiwynn | 2026-08, 2026-09, 2026-10 | 96,885,487 | +77.1% | 171,578,212 | 118,339,509 – 234,938,892 | 93 | 15.7% |
| 3231 Wistron | 2026-08, 2026-09, 2026-10 | 280,624,451 | +123.7% | 627,876,301 | 532,724,212 – 780,993,776 | 93 | 10.0% |
| 2356 Inventec | 2026-08, 2026-09, 2026-10 | 52,156,348 | +48.2% | 77,315,358 | 62,168,581 – 97,797,821 | 93 | 8.7% |
| 2317 Hon Hai (Foxconn) | 2026-08, 2026-09, 2026-10 | 844,275,843 | +45.4% | 1,227,363,942 | 1,017,541,605 – 1,489,634,639 | 93 | 10.8% |
| 2376 Gigabyte | 2026-08, 2026-09, 2026-10 | 30,729,748 | +120.2% | 67,666,982 | 48,662,665 – 94,356,227 | 93 | 18.1% |
| 2357 ASUS | 2026-08, 2026-09, 2026-10 | 66,884,631 | +43.7% | 96,124,320 | 79,062,390 – 113,164,414 | 93 | 9.9% |
| 3706 MiTAC Holdings | 2026-07, 2026-08, 2026-09 | 7,821,659 | +86.1% | 14,555,414 | 10,706,238 – 21,154,329 | 92 | 18.6% |

Scoring: (a) how many of the eight actuals fall inside their 80% intervals (about six expected if the intervals are
honest); (b) median absolute percentage error of the points, against the naive forecast "November = October" (for
MiTAC, "= September"), which is what a reader would guess with no model.

## F2. Dell, fiscal 2027 third quarter (Aug–Oct 2026, expected late November)

Servers and networking revenue (AI-optimized plus traditional) grows more than 50% year over year (base: fiscal 2026
third quarter, US$10,125m).

## F3. HPE, fiscal 2026 fourth quarter (Aug–Oct 2026, expected early December)

Server segment revenue grows more than 20% year over year, measured against the recast fiscal 2025 fourth quarter in
the latest filing (US$4,182m as recast in the fiscal 2026 filings).

## F4. Record quarters

Quanta's and Wiwynn's 2026Q4 revenue (October + November + December monthly revenue) each exceed every earlier
calendar quarter (records at registration: Quanta 2026Q3, NT$1,115,544,521 thousand; Wiwynn 2026Q3,
NT$434,617,690 thousand). Scored when December revenue is published (by 2027-01-10).

## Amendments

**A1 (2026-11-09, same day, before HPE reports the quarter).** The F3 base quoted above, US$4,182m, is not a recast
figure. A code review found it mixes HPE's fiscal-2025 annual Server total on the old segment definition with nine
months on the recast fiscal-2026 definition, so it is a quarter on neither basis. F3 is therefore scored against the
prior-year fourth-quarter Server segment revenue as printed in HPE's fiscal-2026 fourth-quarter earnings release
(the recast comparative). The forecast itself, growth of more than 20%, is unchanged. Nothing about the actual
quarter was known when this was written.
