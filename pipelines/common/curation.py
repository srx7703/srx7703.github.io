"""Shared rules for hand-curated reference files.

Some facts on this site cannot be fetched. A contracted deal, a system count buried in a Hong Kong interim
report, a hospital tender award: these exist only in prose, on pages that no API serves, and somebody has to read
them and type them in. That is the least-checked path into the site, so it gets the most checking.

Every curated row obeys the same rules whatever it describes:

1. **A link a reader can open, and a publication date.** Not a citation, a URL.
2. **A caveat, always.** The qualifier that makes a figure honest travels with it or the row does not publish.
   A number whose meaning was left behind in the source is worse than no number.
3. **Nothing addressed to the pipeline's authors.** Working notes go in ``curator_notes``, which is stripped
   before anything reaches a page; a note to self in any other field rejects the whole file.
4. **A re-check date.** After ``stale_days`` the page greys the row rather than dropping it, because a figure
   nobody has looked at in three months is still a figure, just an older one.
5. **A whole file fails together.** ``rows()`` reports every fault in every row at once and publishes none of
   them, because a curated file is short enough to fix in one pass and a half-published table is worse than a
   missing one.

A track supplies a :class:`CurationSpec` naming its directory, its file kinds, the identity fields each kind
needs, and any extra validators of its own. Everything else lives here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from pipelines.common.checks import check
from pipelines.common.storage import DATA_DIR

log = logging.getLogger("common.curation")

#: Required of every published row, whatever the file holds.
PROVENANCE = ("source_name", "source_url", "publish_date", "caveat")

#: Fields the curator keeps. Stripped by `rows()` before anything can publish them.
PRIVATE_FIELDS = ("curator_notes",)

#: Phrases that address whoever maintains the file rather than whoever reads the page. Each of these has shipped
#: to a reader on some site at some point; none of them is a citation.
WORKING_NOTE_MARKERS = ("re-verify", "not verified", "todo", "check before publishing", "adversarial",
                        "researcher", "fixme", "placeholder", "tbd")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_FIELDS = ("publish_date", "last_checked", "announced_date", "award_date", "decision_date")

DEFAULT_STALE_DAYS = 90


class CurationError(ValueError):
    """A curated row is not fit to publish. The message names the file, the row and every fault."""


Validator = Callable[[dict], list[str]]


@dataclass(frozen=True)
class CurationSpec:
    """What one track's curated layer looks like."""

    track: str
    kinds: tuple[str, ...]
    #: Fields that say *which* thing a row is about, beyond the provenance every row carries. Presence only.
    identity: dict[str, tuple[str, ...]]
    stale_days: int = DEFAULT_STALE_DAYS
    #: Track-specific rules, run after the generic ones. Keyed by kind; "*" applies to every kind.
    extra: dict[str, tuple[Validator, ...]] = field(default_factory=dict)

    @property
    def directory(self) -> Path:
        return DATA_DIR / "reference" / self.track

    def path(self, kind: str) -> Path:
        if kind not in self.kinds:
            raise CurationError(f"unknown reference kind {kind!r} for track {self.track!r}")
        return self.directory / f"{kind}.json"


# ---------------------------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------------------------


def blank(value: object) -> bool:
    """Empty for curation purposes. Zero and False are values, not blanks."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def parse_date(value: str) -> date | None:
    if not _ISO_DATE.match(value.strip()):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# The generic rules
# ---------------------------------------------------------------------------------------------


def identity_problems(spec: CurationSpec, kind: str, row: dict) -> list[str]:
    if kind not in spec.identity:
        raise CurationError(f"no identity fields declared for {kind!r} in track {spec.track!r}")
    return [f"no {f}" for f in spec.identity[kind] if blank(row.get(f))]


def sourcing_problems(row: dict) -> list[str]:
    out = []
    url = row.get("source_url")
    if not (isinstance(url, str) and url.strip().startswith("http")):
        out.append(f"source_url {url!r} is not a link")
    published = row.get("publish_date")
    if blank(published):
        out.append("no publish_date")
    elif not parse_date(str(published)):
        out.append(f"publish_date {published!r} is not a YYYY-MM-DD date")
    if blank(row.get("source_name")):
        out.append("no source_name")
    return out


def caveat_problems(row: dict) -> list[str]:
    return [] if not blank(row.get("caveat")) else ["no caveat"]


def working_note_problems(row: dict) -> list[str]:
    out = []
    for field_name, value in row.items():
        if field_name in PRIVATE_FIELDS or not isinstance(value, str):
            continue
        low = value.lower()
        out += [f"{field_name} contains the working note {m!r}" for m in WORKING_NOTE_MARKERS if m in low]
    return out


def date_problems(row: dict) -> list[str]:
    return [
        f"{f} {row[f]!r} is not a YYYY-MM-DD date"
        for f in DATE_FIELDS
        if f in row and not blank(row[f]) and not parse_date(str(row[f]))
    ]


def positive_number(field_name: str, *, allow_null: bool = True) -> Validator:
    """A reusable rule: this field is null or strictly positive.

    Zero is the trap. A zero quantity or a zero price means a parse failure or a typo, and both would total
    correctly and read wrongly. Where an undisclosed value is the normal case it stays null; a guess dressed as
    a figure is the one thing a curated table must never contain.
    """

    def rule(row: dict) -> list[str]:
        if field_name not in row or row.get(field_name) is None:
            return [] if allow_null else [f"no {field_name}"]
        v = row[field_name]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return [f"{field_name} {v!r} is not a number"]
        return [] if v > 0 else [f"{field_name} {v} is not a positive value; leave it null when undisclosed"]

    return rule


def one_of(field_name: str, allowed, *, required: bool = True) -> Validator:
    """A reusable rule: this field comes from a closed vocabulary.

    Closed rather than open because an unrecognised value is news. Bucketing it as "other" hides a new category
    and dropping the row silently loses a fact.
    """
    allowed = tuple(allowed)

    def rule(row: dict) -> list[str]:
        v = row.get(field_name)
        if blank(v):
            return [f"no {field_name}"] if required else []
        return [] if v in allowed else [f"{field_name} {v!r} is not one of {', '.join(map(str, allowed))}"]

    return rule


def row_problems(spec: CurationSpec, kind: str, row: dict) -> list[str]:
    """Everything wrong with one row, not just the first thing."""
    problems = [
        *identity_problems(spec, kind, row),
        *sourcing_problems(row),
        *caveat_problems(row),
        *working_note_problems(row),
        *date_problems(row),
    ]
    for rule in (*spec.extra.get("*", ()), *spec.extra.get(kind, ())):
        problems += rule(row)
    return problems


# ---------------------------------------------------------------------------------------------
# Flags. A flagged row is published; the page renders it differently.
# ---------------------------------------------------------------------------------------------


def is_stale(last_checked: str | None, spec: CurationSpec, today: date | None = None) -> bool:
    if blank(last_checked):
        return True
    when = parse_date(str(last_checked))
    if when is None:
        return True
    return ((today or date.today()) - when).days > spec.stale_days


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def load(spec: CurationSpec, kind: str) -> dict:
    path = spec.path(kind)
    if not path.exists():
        raise CurationError(f"{path} does not exist; the curated {kind} file is part of the repo")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CurationError(f"{path.name}: expected an object, found {type(data).__name__}")
    if not isinstance(data.get("rows"), list):
        raise CurationError(f"{path.name}: expected a 'rows' list, found {type(data.get('rows')).__name__}")
    return data


def rows(spec: CurationSpec, kind: str, today: date | None = None) -> list[dict]:
    """The publishable rows of one file: validated, stripped of private fields, flagged."""
    data = load(spec, kind)
    faults: list[str] = []
    out: list[dict] = []
    for i, row in enumerate(data["rows"]):
        if not isinstance(row, dict):
            faults.append(f"{kind}[{i}]: expected an object, found {type(row).__name__}")
            continue
        problems = row_problems(spec, kind, row)
        if problems:
            label = next((row[k] for k in ("id", "notice_id", "maker", "ticker", "entity", "province")
                          if row.get(k)), i)
            faults.append(f"{kind}[{label}]: " + "; ".join(problems))
            continue
        published = {k: v for k, v in row.items() if k not in PRIVATE_FIELDS}
        published["stale"] = is_stale(row.get("last_checked"), spec, today)
        out.append(published)
    if faults:
        raise CurationError(
            f"{len(faults)} unpublishable row(s) in {spec.path(kind).name} — " + " | ".join(faults))
    return out


def validate_all(spec: CurationSpec, today: date | None = None) -> list[dict]:
    """One `file`, `rows` and `freshness` check per curated file.

    A file that will not parse fails on its own line and the others are still checked, so the page can say which
    curated layer is broken instead of losing all of them.
    """
    checks: list[dict] = []
    for kind in spec.kinds:
        try:
            data = load(spec, kind)
        except Exception as exc:
            checks.append(check(f"{kind} file", False, str(exc)))
            continue
        checks.append(check(f"{kind} file", True,
                            f"{spec.path(kind).name} parsed, {len(data['rows'])} row(s), "
                            f"updated {data.get('updated', 'never')}"))
        try:
            published = rows(spec, kind, today)
        except CurationError as exc:
            checks.append(check(f"{kind} rows", False, str(exc)))
            continue
        checks.append(check(f"{kind} rows", True,
                            f"{len(published)} row(s) sourced, dated and caveated"
                            if published else "the file is empty, nothing to publish yet"))
        stale = [r for r in published if r.get("stale")]
        checks.append(check(f"{kind} freshness", not stale,
                            f"{len(stale)} of {len(published)} row(s) unchecked for more than "
                            f"{spec.stale_days} days" if published
                            else f"no rows to re-check (limit {spec.stale_days} days)",
                            warn=True))
    return checks
