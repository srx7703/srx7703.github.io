# payments-landscape — build status

Updated by the orchestrator after every wave (see EXECUTION.md). Cloud session works on branch `payments-landscape-gy0bq3`.

| wave | status | date | notes |
|---|---|---|---|
| W0 setup + pre-registration | done | 2026-10-02 | Branch `payments-landscape` from origin/main. Section H appended to docs/EVALUATION_PLAN.md; pinned constants in pipelines/payments/config.py; test_plan.py green. Owner chose: all PLAN §8 defaults, push the branch, run in a cloud session, and set SEC_USER_AGENT (a project-specific address) in the cloud environment. Scaffold of facts/marts/charts/MDX deferred to W2/W3 so the branch stays green. |
| W1 reference data | done | 2026-10-04 | 7 files, 701 rows (676 V, 19 C, 6 S; 685 chartable, all V/C), 60 recorded gaps. 6 curators + adversarial verifiers; every file ended with 0 blocking findings after 1-2 fix rounds. Pages read via Exa (container has no direct internet). Known gaps: Fiserv quarterly Clover revenue not disclosed in dollars; Global Payments has no volume series and a 2026 resegmentation break; Adyen half-yearly only. |
| W2 pipeline | done | 2026-10-04 | economics (per-$100 waterfalls + Q4/Q7/Q8 sensitivity), ledger (private timeline, intervals, take rates; qualifier algebra tested), share (4 lenses, never summed; pool HHI), schema/evaluate/publish -> data/marts/payments + data/facts/payments.json (3 KPIs, Q1-Q9 scoreboard, checks, regression guard). Full suite + ruff green; publish idempotent except generated_at; SaaS marts byte-identical. Open: no curated EUR/USD 2025 average yet (Adyen unconverted); Q1 undecidable until Airwallex/Checkout.com calendar-year volume exists and PayPal merchant split is defined (section H amendment candidate); Q6/Stripe bands live as labelled assumptions in ledger.py; CI-only sources (SEC payments, webtech, npm, jobs, Form D) fill on first Actions run. |
| W3 site | | | |
| W4 review | | | |
| W5 ship prep | | | |

## Notes for the cloud session

- **SEC.** Calls need `SEC_USER_AGENT` from the environment. If it is unset, stop the SEC parts and ask the owner. Never put a personal address in code, commits or logs.
- **Scouting material.** The raw scouting files (landscape, data_sources, methods, portfolio_fit, pilots) live only on the owner's Mac. Their content is summarised in PLAN.md. Everything a curator needs must be re-verified from primary pages anyway, because only [V] rows reach facts.
- **Known stray write.** `pipelines/valuation/tests/test_site_pages.py` rewrites `data/marts/valuation/evaluation.json`. Restore it before every commit.
