# payments-landscape — build status

Updated by the orchestrator after every wave (see EXECUTION.md).

| wave | status | date | notes |
|---|---|---|---|
| W0 setup + pre-registration | done | 2026-10-02 | Branch `payments-landscape` from origin/main. Section H appended to docs/EVALUATION_PLAN.md; pinned constants in pipelines/payments/config.py; test_plan.py green. Owner chose: all PLAN §8 defaults, push the branch, run in a cloud session, and set SEC_USER_AGENT (a project-specific address) in the cloud environment. Scaffold of facts/marts/charts/MDX deferred to W2/W3 so the branch stays green. |
| W1 reference data | next | | |
| W2 pipeline | | | |
| W3 site | | | |
| W4 review | | | |
| W5 ship prep | | | |

## Notes for the cloud session

- **SEC.** Calls need `SEC_USER_AGENT` from the environment. If it is unset, stop the SEC parts and ask the owner. Never put a personal address in code, commits or logs.
- **Scouting material.** The raw scouting files (landscape, data_sources, methods, portfolio_fit, pilots) live only on the owner's Mac. Their content is summarised in PLAN.md. Everything a curator needs must be re-verified from primary pages anyway, because only [V] rows reach facts.
- **Known stray write.** `pipelines/valuation/tests/test_site_pages.py` rewrites `data/marts/valuation/evaluation.json`. Restore it before every commit.
