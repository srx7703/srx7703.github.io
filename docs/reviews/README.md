# Acceptance reviews

Independent, read-only reviewer sessions (Claude Code subagents) audit the repo and the live site
against the written plan after each milestone. Reports are stored verbatim except that machine-local
paths are shortened to repo-relative paths. Findings are numbered so the closure review can cite them.

| Date | Review | Scope | Findings |
|---|---|---|---|
| 2026-09-16 | `2026-09-16-pipelines-and-data.md` | pipelines, data layers, workflows, docs, facts, robustness | 17 (3 high, 7 medium, 7 low) |
| 2026-09-16 | `2026-09-16-site-and-pages.md` | IA, page skeleton, numbers-from-facts, chart rules, design system, a11y, How-I-work | 30 (7 high, 12 medium, 11 low) |
| 2026-09-16 | `2026-09-16-closure.md` | verification that the high/medium findings above are closed in HEAD | 7 residuals, all fixed in the following commit |
