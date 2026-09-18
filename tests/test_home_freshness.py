"""The home page's freshness note must cover every live project.

The note is the only place a reader learns whether a pipeline is still running. It began as a
hand-written list of three projects; two more live projects were added months later and the list was
not, so the page understated the site by two feeds and nobody noticed. This test is what makes the
list impossible to forget: add a project with ``status: live`` and it fails until the project appears
in ``site/src/lib/freshness.ts``.

It also checks the other direction — an entry for a project that no longer exists, or that has stopped
being live — because a note that names a dead feed is worse than one that omits a live one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "site/src/content/projects"
FRESHNESS = ROOT / "site/src/lib/freshness.ts"
FACTS = ROOT / "data/facts"


def live_projects() -> set[str]:
    out = set()
    for page in PROJECTS.glob("*.mdx"):
        head = page.read_text(encoding="utf-8").split("---")[1] if "---" in page.read_text(encoding="utf-8") else ""
        if re.search(r"^status:\s*live\s*$", head, re.M):
            out.add(page.stem)
    return out


def registered() -> dict[str, list[str]]:
    """{project id: timestamp keys} as declared in the registry."""
    src = FRESHNESS.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for entry in re.finditer(r"\{[^{}]*?id:\s*'([^']+)'[^{}]*?keys:\s*\[([^\]]*)\][^{}]*?\}", src, re.S):
        out[entry.group(1)] = re.findall(r"'([^']+)'", entry.group(2))
    return out


def test_every_live_project_is_in_the_freshness_note():
    missing = sorted(live_projects() - set(registered()))
    assert not missing, (
        "these live projects are missing from site/src/lib/freshness.ts, so the home page would not "
        f"show whether their pipelines are still running: {', '.join(missing)}"
    )


def test_the_note_names_no_project_that_is_not_live():
    stale = sorted(set(registered()) - live_projects())
    assert not stale, (
        "site/src/lib/freshness.ts names projects that are not live (or no longer exist): "
        f"{', '.join(stale)}"
    )


def test_each_registered_key_exists_in_its_facts_file():
    """A key that no pipeline writes would silently fall through to the next one, or to nothing."""
    problems = []
    for pid, keys in registered().items():
        facts_name = {
            "midterms-2026": "midterms.json",
            "fomc-markets": "fomc.json",
            "saas-benchmark": "saas.json",
            "optical-modules-valuation": "valuation_optical.json",
            "solid-state-battery-valuation": "valuation_ssb.json",
        }.get(pid)
        if facts_name is None:
            problems.append(f"{pid}: no facts file mapped in this test")
            continue
        path = FACTS / facts_name
        if not path.exists():
            problems.append(f"{pid}: {facts_name} does not exist")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not any(isinstance(data.get(k), str) and data.get(k) for k in keys):
            problems.append(f"{pid}: none of {keys} holds a timestamp in {facts_name}")
    assert not problems, "freshness registry does not match the facts files:\n" + "\n".join(problems)
