# Pre-registered test rules: Q1, Q2, Q5, Q7 (AI / data-center server makers)

Registered 2026-11-09. Append-only: later changes go in a dated amendment at the end. Two independent reviews of a
first draft (ambiguity, validity) were folded in before registration; the draft is not kept.

What had already been seen when this was written (honest disclosure): the September 2026 monthly revenue of the
Taiwan makers and its year-over-year change (Quanta +77%, Wiwynn +100%, Wistron +87%, Inventec +63%, Gigabyte +125%,
Hon Hai +38%); the October 2025 monthly revenue of the same makers; Foxconn Industrial Internet's share price and
trailing P/E on 2026-11-09; and, while checking that the filings parse, the last few quarterly values of Dell's
Infrastructure Solutions Group lines, HPE's Server segment, Celestica's segments and Super Micro's net sales. No
group growth rate, margin comparison, multiple cross-section or capex correlation below had been computed.

## Common definitions

- **Calendar placement.** Each fiscal quarter is placed in the calendar quarter that contains most of its days.
  Dell: Feb–Apr → Q1, May–Jul → Q2, Aug–Oct → Q3, Nov–Jan → Q4. HPE likewise. Super Micro, Lenovo, Celestica and
  the Taiwan and mainland makers end on calendar quarter ends.
- **Operating profit.** US and Hong Kong filers: reported operating income. Taiwan: 營業利益 (MOPS). Mainland:
  营业总收入 − 营业总成本 (cost of sales, taxes and surcharges, selling, administrative, R&D and finance expense);
  the reported 营业利润 is not used because it also holds investment income, fair-value changes and subsidies.
- **Gross profit.** Revenue less cost of revenue as reported (Taiwan: 營業毛利（毛損）淨額; mainland: 营业收入 −
  营业成本; US: GrossProfit, or revenue less cost of revenue where GrossProfit is not tagged).
- **Taiwan revenue** is the consolidated monthly revenue on MOPS as shown at grading, including corrections,
  converted to US dollars where stated at the monthly average of FRED series EXTAUS (Federal Reserve H.10).
- **Vintage.** For every figure, the latest filed value for the period is used (restatements and recast
  comparatives win over first prints).
- **Rounding.** Every threshold comparison uses unrounded values; the page displays one decimal.

## Q1. Are the ODMs taking share from the brand makers? (graded 2026-12-31)

- **OEM group**: Dell *Servers and networking* (Infrastructure Solutions Group revenue less Storage; from fiscal 2027
  Dell reports it as AI-optimized plus traditional servers and networking, which sum to the same line), HPE *Server*
  segment, Super Micro net sales, Lenovo *Infrastructure Solutions Group*. US dollars as reported. If a segment is
  redefined, both windows use the recast comparatives in the latest filing; if there are none, Q1 is undecided.
- **ODM group (primary)**: Quanta + Wistron + Inventec. Wiwynn is consolidated by Wistron, so it is inside Wistron
  and not added. **ODM group (robustness)**: Quanta + Inventec + Wiwynn. Wistron outside Wiwynn also makes GPU
  boards and trays that end up in OEM servers, so part of its growth is the OEMs' own; the robustness group removes
  that overlap.
- **Hon Hai is left out of both**: servers are a minority of its consolidated revenue, which is dominated by consumer
  electronics. Its September 2026 growth (+38%) was the lowest of the six seen; it is shown beside the groups,
  ungraded, so a reader can see what including it would do.
- Totals of the Taiwan makers include notebooks and other products, which dilutes their server growth (a bias
  against "holds"); the GPU-board overlap above runs the other way.
- **Windows.** OEM: for each member, the four fiscal quarters ending with its latest quarter that ends between
  2026-09-15 and 2026-11-15, against the four quarters ending a year earlier. ODM: October 2025 – September 2026
  against October 2024 – September 2025.
- **Measure.** g = year-over-year growth of the group's summed trailing-four-quarter US-dollar revenue. Relative gain
  R = (1 + g_ODM) / (1 + g_OEM).
- **Verdict.** For each ODM group: holds if R ≥ 1.08, refuted if R ≤ 1.00, undecided in between. Q1 **holds** or is
  **refuted** only if both ODM groups give that verdict; otherwise undecided. If any OEM has not reported its
  qualifying quarter by 2026-12-31, undecided. The same computation one year earlier is shown beside it, ungraded.

## Q2. Does the gross-margin squeeze of AI servers get paid for by operating leverage? (graded 2026-12-31)

- **Graded members**: Dell, Super Micro, Celestica, Quanta, Wiwynn, Wistron, Inventec, Foxconn Industrial Internet,
  Inspur Information. **Reported, not graded**: Lenovo (mostly PCs), HPE (acquired Juniper in 2025, which lifts its
  gross margin), Gigabyte (consumer boards and graphics cards). **Placebo**: Cisco, Pegatron, China Greatwall, run
  the same way.
- Company-level figures in reporting currency. Before = trailing four quarters placed in 2022Q3–2023Q2. After =
  trailing four quarters placed in 2025Q4–2026Q3. Members missing either window are dropped.
- A member **shows the pattern** if its gross margin is at least 0.5 percentage points lower **and** its operating
  margin is equal or higher in the after window.
- **Verdict.** With n graded members remaining: holds if at least ceil(2n/3) show the pattern; refuted if fewer than
  n/2 do; undecided in between. If two or more of the three placebo companies also show the pattern, the verdict is
  capped at undecided. Shown beside it, ungraded: the count with Wistron dropped (Wiwynn is inside it), and the
  weaker version "gross margin lower and operating profit amount higher".

## Q5. Does the market price these makers on growth rather than margin? (cross-section not yet computed)

- **Members (fixed)**: DELL, HPE, SMCI, 0992.HK, PENG, 2382.TW, 6669.TW, 3231.TW, 2356.TW, 2317.TW, 2376.TW,
  2357.TW, 3706.TW, 601138.SS, CLS, JBL, FLEX, SANM, 000977.SZ, 000938.SZ, 603019.SS, 603296.SS. Later pool changes
  do not alter Q5.
- **Snapshot**: the first valuation pipeline run after this file is committed. A member qualifies if it has a
  positive calendar-2027 consensus EPS from at least three analysts (the valuation pipeline's `fwd_pe_2027`,
  calendarised for fiscal-year companies; at registration 2027 is the next twelve months to within seven weeks) and
  trailing-four-quarter fundamentals as of the latest quarter reported on or before the snapshot date.
- **Market adjustment.** A-shares, Taiwan and US listings trade at different P/E levels, so each member's forward P/E
  is divided by the median forward P/E of qualifying members in its market group (US and Hong Kong / Taiwan /
  mainland) before ranking.
- **Measures.** Spearman rank correlation (average ranks for ties) of the adjusted forward P/E with (a) trailing
  four-quarter revenue growth, year over year, and (b) trailing four-quarter operating margin, company-level.
- **Verdict.** Holds if ρ(growth) ≥ 0.30 and ρ(growth) − ρ(margin) ≥ 0.20. Refuted if ρ(margin) > 0 and
  ρ(margin) ≥ ρ(growth). Undecided otherwise, and undecided if fewer than 15 members qualify.

## Q7. Do the Taiwan makers' monthly revenues tell you the hyperscalers' capex before it is reported?

- **Signal** for calendar quarter q: year-over-year growth of the summed US-dollar revenue of Quanta, Wistron and
  Inventec in the first two months of q (known around the 10th of the quarter's third month, about seven weeks before
  the cloud companies report q). **Variant**: the same with Wiwynn alone.
- **Target**: year-over-year growth of the summed cash capex of Microsoft, Alphabet, Amazon and Meta in q. Capex =
  XBRL PaymentsToAcquirePropertyPlantAndEquipment (Amazon: PaymentsToAcquireProductiveAssets, gross of proceeds and
  incentives), excluding finance-lease principal, so it is below the headline figures that include finance leases.
  Quarters by differencing year-to-date figures within each fiscal year (Microsoft's year ends in June). Oracle is
  excluded because its quarters end a month off.
- **Window**: correlation over 2019Q1–2026Q3; same-direction share over 2019Q2–2026Q3 (30 quarters).
- **Measures.** Pearson correlation of signal and target; and the share of quarters in which the change in
  year-over-year growth from q-1 to q has the same sign in both series (a zero change in either counts as a
  mismatch). The same-direction share is the binding test, because it removes the shared cycle that can make two
  growth series correlate.
- **Verdict, per signal.** Holds if the correlation is at least 0.5 and the same-direction share is at least 65%.
  Refuted if the correlation is below 0.3 or the same-direction share is below 55%. Undecided otherwise (a passing
  correlation with a failing direction share is undecided, not holds). Q7 holds or is refuted only if the primary
  signal and the Wiwynn variant agree; otherwise undecided. The signal leading by one quarter (signal q, target q+1)
  is reported as exploratory, not graded.

## Amendments

(none)
