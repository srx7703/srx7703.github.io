"""The cyber track's Yahoo payloads are read by the run and never committed.

Yahoo's terms forbid building a database or archive of its data. The four earlier tracks commit their
raw payloads (a decision that predates this rule and would mean rewriting git history to undo); the
cyber track must not add to that, and these tests are what keeps a later edit from quietly doing so.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pipelines.valuation import config
from pipelines.valuation.config import LOCAL_RAW_TRACKS, Company, raw_source

REPO = Path(__file__).resolve().parents[3]
# Every directory under data/raw/valuation that holds a Yahoo payload: prices (info), estimates, fundamentals.
YAHOO_SOURCES = ("yahoo_info", "yahoo", "yahoo_fin")


def _company(track: str) -> Company:
    return Company("X", "X", track, "high", "us", 12)


def test_only_the_cyber_track_archives_locally():
    assert LOCAL_RAW_TRACKS == {"cyber"}


def test_raw_source_routes_by_track():
    assert raw_source(_company("cyber"), "yahoo_info") == "yahoo_info_local"
    assert raw_source(_company("cyber"), "yahoo") == "yahoo_local"
    for track in ("optical", "ssb", "power", "surgical"):
        assert raw_source(_company(track), "yahoo_info") == "yahoo_info"


def test_every_local_directory_is_gitignored():
    """`git check-ignore` is the authority, not a grep of .gitignore: a rule that is shadowed or
    mis-patterned would pass the grep and still let the payload into a commit."""
    for source in YAHOO_SOURCES:
        path = f"data/raw/valuation/{raw_source(_company('cyber'), source)}/2026-10-21/CRWD.json.gz"
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO, check=False)
        assert result.returncode == 0, f"{path} would be committed"


def test_the_earlier_tracks_are_not_silently_ignored():
    """Ignoring their payloads would hide the 3,000 committed files' continuation, not stop it."""
    for source in YAHOO_SOURCES:
        path = f"data/raw/valuation/{source}/2026-10-21/CRWD.json.gz"
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO, check=False).returncode == 1


def test_every_track_in_a_company_pool_is_a_known_track():
    for c in config.COMPANIES:
        assert c.track in config.TRACKS


def test_no_module_archives_a_yahoo_payload_without_routing_it():
    """A new call site that names a Yahoo directory directly would bypass the rule above."""
    pattern = re.compile(r"""archive[_a-z]*\(\s*["']yahoo""")
    offenders = [
        f"{path.name}:{n}"
        for path in (REPO / "pipelines" / "valuation").glob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"Yahoo payload archived without raw_source(): {offenders}"
