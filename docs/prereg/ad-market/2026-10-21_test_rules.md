# Pre-registered test rules: Q1, Q2, Q3, Q5, Q6

Registered 2026-10-21. Append-only: later changes go in a dated amendment at the end.
Series definitions are those of `预注册_2026Q3Q4预测_20261021.md`. All figures come from SEC 10-Q/10-K XBRL.

What had already been seen when this was written (honest disclosure): the quarterly panel 2021Q1 to 2026Q2, including the
shares and the 2025Q3 and 2026Q2 year-over-year growth of every series, and Meta's advertising revenue falling year over
year in 2022Q2 to 2022Q4. Two-year growth figures (Q2) and group growth rates (Q3) had not been computed.

## Q1. Is Google's share of the video subset still falling? (confirmatory: future data)

Share = Google advertising / sum of the nine series of the video subset. Compare the mean of the four quarterly shares
2026Q3 to 2027Q2 with the mean of 2025Q3 to 2026Q2. **Holds** if the later mean is lower by at least 0.5 percentage
points. **Refuted** if the later mean is equal or higher. In between: undecided. Graded when all nine have reported 2027Q2.

## Q2. Did Apple's ATT hurt cross-app-tracking platforms more than search? (data exists, not yet computed)

- Growth measure: two-year growth, annualised, g2(q) = (R(q) / R(q-8))^(1/2) - 1, so that 2021 is compared with 2019 and
  the 2020 pandemic dip does not inflate it.
- Affected group: Meta advertising, Snap total revenue, Pinterest total revenue. Control group: Google Search & other,
  Microsoft search and news advertising. Group value = unweighted mean of its members.
- Before = mean g2 over 2021Q1 and 2021Q2 (ATT shipped with iOS 14.5 on 2021-04-26). After = mean g2 over 2022Q1 to 2022Q4.
- Drop = before - after, per group. **Holds** if the affected group's drop exceeds the control group's by 5 percentage
  points or more. **Refuted** if it exceeds it by less than 5 points or not at all.
- Known confound, stated now: 2022 also saw rate rises and an e-commerce slowdown, which hit performance advertisers;
  a positive result cannot be attributed to ATT alone.

## Q3. Is the open web losing ground to the walled gardens? (data exists, not yet computed)

- Open-web group: Google Network, Trade Desk, Magnite, PubMatic. Walled-garden group: Google Search & other, YouTube ads,
  Meta advertising, Amazon advertising, Microsoft search, Snap, Pinterest, Reddit advertising (Reddit from 2024Q1, so the
  group is also computed without Reddit as a check).
- Growth = compound annual growth of trailing-four-quarter revenue from 2022Q3-2023Q2 to 2025Q3-2026Q2 (three years).
- **Holds** if the open-web group's growth is lower than the walled-garden group's by 5 points or more. **Refuted** if it
  is equal or higher. In between: undecided.

## Q5. Is the AppLovin / Trade Desk growth gap structural? (confirmatory: future data)

Over the four quarters 2026Q3 to 2027Q2: **refuted** if in two or more of them Trade Desk's year-over-year revenue growth
is at least half of AppLovin's advertising growth. **Holds** otherwise.

## Q6. Does compute spending go with advertising growth? (exploratory, not confirmatory)

Capex as a share of revenue for Alphabet, Meta and Amazon is already known (2021 to 2025), and so is their 2026Q2 growth.
Q6 is therefore reported as a description only: the change in capex intensity 2023 to 2025 beside the advertising growth
of 2026 (four quarters once available). No verdict is drawn.

## Amendments

(none)
