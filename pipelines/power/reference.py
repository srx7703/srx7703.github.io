"""The four curated layers of the power page, and the hygiene that decides what is fit to publish.

Most of this project is a parser: a public workbook goes in, a table comes out, and the number is
whatever EIA or PJM said it was. Four things on the page are not like that, because no machine-readable
public file holds them:

``deals``              what a data-center operator actually contracted for, one row per deal. The
                       contract answer to "where does the power come from", which is a different
                       answer from the physical one and is sourced one press release at a time.
``utility_pipelines``  what each utility discloses about its data-center load pipeline, per company
                       per quarter. Utilities describe this differently enough that a shared schema
                       is already an interpretation, so the row keeps the disclosure's own words.
``unit_economics``     the per-path comparison a reader wants and nobody publishes consistently.
``demand_forecasts``   the published estimates of US data-center electricity use, quoted unchanged.

A hand-curated row is not worse evidence than a parsed one, but it fails differently. A parser fails
loudly and in bulk; curation fails quietly and one row at a time — a figure typed without the
qualifier that made it true, a source read once in March and still shown as current in September, a
note to oneself left in a field that the site serves to the world. This module is where those failures
are caught, and it is the only place that decides whether a curated row is fit to publish:
``pipelines/power/publish.py`` copies whatever ``rows()`` returns straight into ``data/facts/``, which
is served at ``site/public/data/facts/``. Every field of a returned row is therefore public.

The rules, in the order they are defined below:

1. a row names a link and the date that link was published;
2. a row carries a ``caveat``, the qualifier that makes the figure honest, written for a reader;
3. ``stage`` and ``path`` come from the shared vocabulary — an unknown value is an error, not a
   warning, because a stage the page cannot place is worse than a row that is missing;
4. a row unchecked for more than ``REFERENCE_STALE_DAYS`` is returned and flagged ``stale``, so the
   page can grey it rather than show an old number as current;
5. ``mw`` is null or positive. A deal with no disclosed capacity is normal and stays null; zero is not
   a capacity and neither is a negative one;
6. ``company_confirmed=False`` rows are kept and flagged ``unconfirmed``, because "a newspaper said so"
   and "the company filed it" are different kinds of fact and the page marks which is which;
7. working notes never reach a reader. ``curator_notes`` exists for them and is stripped here, so it
   cannot be serialised into the facts file by a caller that copies the row wholesale.

Rules 1, 2, 3, 5 and 7 raise. Rules 4 and 6 flag, because the row is still true — it is only old, or
only a claim. ``validate_all()`` catches the raises one file at a time and reports them as checks, so
one malformed deal cannot take the other three files off the page.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import DATA_DIR
from pipelines.power.config import PATHS, REFERENCE_STALE_DAYS, STAGE_ORDER
from pipelines.power.schema import DEALS_SCHEMA, deals_frame

log = logging.getLogger("power.reference")

REFERENCE_DIR = DATA_DIR / "reference" / "power"

KINDS = ("deals", "utility_pipelines", "unit_economics", "demand_forecasts")

#: Required of every published row whatever the file holds: rules 1 and 2. `source_url`, `publish_date`
#: and `caveat` each have a rule of their own below; `source_name` only has to be there.
PROVENANCE = ("source_name", "source_url", "publish_date", "caveat")
_NAMED_SOURCE_ONLY = ("source_name",)

#: What identifies a row of each kind, beyond the provenance every row carries. Presence only — the
#: value has to be there, and the rules below say what a present value may be.
IDENTITY: dict[str, tuple[str, ...]] = {
    "deals": ("deal_id", "path", "buyer", "seller", "technology", "stage", "company_confirmed", "last_checked"),
    "utility_pipelines": ("ticker", "quarter"),
    "unit_economics": ("path", "metric", "value", "unit", "vendor_claim"),
    # `basis` is not in the reading list this file quotes from, and it has to be: LBNL's 176 TWh in
    # 2023 is a level and the IEA's "+240 TWh by 2030" is an increment. Put both in a column headed
    # TWh with nothing to tell them apart and the page will add them together sooner or later.
    "demand_forecasts": ("source", "scenario", "year", "basis"),
}

#: Fields the curator keeps for themselves. Stripped by `rows()` before anything can publish them.
PRIVATE_FIELDS = ("curator_notes",)

#: Phrases that address whoever maintains the file rather than whoever reads the page. Each of these
#: has shipped to a reader on some site at some point; none of them is a citation.
WORKING_NOTE_MARKERS = ("re-verify", "not verified", "todo", "check before publishing", "adversarial", "researcher")

#: Dates are recorded as the source printed them, to the day.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Every date field a curated row may carry, in any of the four files.
DATE_FIELDS = ("publish_date", "last_checked", "announced_date")


class CurationError(ValueError):
    """A curated row is not fit to publish. The message names the file, the row and every fault."""


def reference_path(kind: str) -> Path:
    if kind not in KINDS:
        raise CurationError(f"unknown reference kind {kind!r}; expected one of {', '.join(KINDS)}")
    return REFERENCE_DIR / f"{kind}.json"


def _blank(value: object) -> bool:
    """Missing, or a string with nothing in it. `False` and `0` are values, not blanks."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not _ISO_DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# The rules. Each takes a row and returns what is wrong with it, so they compose and test alone.
# ---------------------------------------------------------------------------------------------


def identity_problems(kind: str, row: dict) -> list[str]:
    """The fields that say which deal, company or estimate this is. Presence only."""
    if kind not in KINDS:
        raise CurationError(f"unknown reference kind {kind!r}")
    return [f"no {field}" for field in IDENTITY[kind] if _blank(row.get(field))]


def sourcing_problems(row: dict) -> list[str]:
    """Rule 1: a link a reader can open, and the date the thing behind it was published."""
    out = []
    url = row.get("source_url")
    if not (isinstance(url, str) and url.strip().startswith("http")):
        out.append(f"source_url {url!r} is not a link")
    published = row.get("publish_date")
    if _blank(published):
        out.append("no publish_date")
    elif not _parse_date(str(published)):
        out.append(f"publish_date {published!r} is not a YYYY-MM-DD date")
    return out


def caveat_problems(row: dict) -> list[str]:
    """Rule 2: the qualifier that makes the figure honest travels with it or the row is not published.

    An announced capacity that is a maximum, a pipeline number that counts requests rather than
    signatures, a cost that excludes interconnection: the page renders this sentence beside the
    figure, so a row without one is a number with its meaning left behind in the source.
    """
    return [] if not _blank(row.get("caveat")) else ["no caveat"]


def vocabulary_problems(row: dict) -> list[str]:
    """Rule 3: `stage` and `path` come from the vocabulary the rest of the page is built on.

    The stage words are the ones `config.STATUS_STAGE` maps EIA's status codes onto, which is what
    lets the page put announced gas turbines beside announced nuclear agreements. A value outside
    either list is an error: bucketing it as "other" would hide a new category, and dropping the row
    silently would lose a deal.
    """
    out = []
    stage = row.get("stage")
    if not _blank(stage) and stage not in STAGE_ORDER:
        out.append(f"stage {stage!r} is not one of {', '.join(STAGE_ORDER)}")
    path = row.get("path")
    if not _blank(path) and path not in PATHS:
        out.append(f"path {path!r} is not one of {', '.join(PATHS)}")
    return out


def capacity_problems(row: dict) -> list[str]:
    """Rule 5: `mw` is null or a positive number.

    Undisclosed capacity is the normal case for a contract and stays null — a guess dressed as a
    figure is the one thing this table must never contain. Zero is not a capacity and neither is a
    negative one; both mean a parse or a typo, and both would total correctly and read wrongly.
    (Zero is a real answer in other columns: a utility disclosing a pipeline with nothing contracted
    yet is news, so this rule is about `mw` alone.)
    """
    if "mw" not in row or row.get("mw") is None:
        return []
    mw = row["mw"]
    if isinstance(mw, bool) or not isinstance(mw, (int, float)):
        return [f"mw {mw!r} is not a number"]
    return [] if mw > 0 else [f"mw {mw} is not a capacity; leave it null when none was disclosed"]


def working_note_problems(row: dict) -> list[str]:
    """Rule 7: nothing addressed to the pipeline's own authors may sit in a field the site serves."""
    out = []
    for field, value in row.items():
        if field in PRIVATE_FIELDS or not isinstance(value, str):
            continue
        low = value.lower()
        out += [f"{field} contains the working note {marker!r}" for marker in WORKING_NOTE_MARKERS if marker in low]
    return out


def date_problems(row: dict) -> list[str]:
    """Every date a row carries is a real day, so staleness and ordering mean something."""
    return [
        f"{field} {row[field]!r} is not a YYYY-MM-DD date"
        for field in DATE_FIELDS
        if field in row and not _blank(row[field]) and not _parse_date(str(row[field]))
    ]


def row_problems(kind: str, row: dict) -> list[str]:
    """Everything wrong with one row, not just the first thing."""
    return [
        *identity_problems(kind, row),
        *[f"no {f}" for f in _NAMED_SOURCE_ONLY if _blank(row.get(f))],
        *sourcing_problems(row),
        *caveat_problems(row),
        *vocabulary_problems(row),
        *capacity_problems(row),
        *working_note_problems(row),
        *date_problems(row),
    ]


# ---------------------------------------------------------------------------------------------
# The flags. A flagged row is published; the page renders it differently.
# ---------------------------------------------------------------------------------------------


def is_stale(last_checked: str | None, today: date | None = None) -> bool:
    """Rule 4: has nobody looked at this row's source in more than `REFERENCE_STALE_DAYS` days?

    A row with no `last_checked` at all counts as stale: the absence of a check is not evidence of
    freshness. The row is still returned — greying a number is honest, deleting it is not.
    """
    if _blank(last_checked):
        return True
    checked = _parse_date(str(last_checked))
    if checked is None:
        raise CurationError(f"last_checked {last_checked!r} is not a YYYY-MM-DD date")
    return ((today or date.today()) - checked).days > REFERENCE_STALE_DAYS


def is_unconfirmed(row: dict) -> bool:
    """Rule 6: did the company itself say this, or did somebody else say it about the company?

    Only rows that carry `company_confirmed` are making that claim at all; for the other files the
    question does not arise and the flag is False.
    """
    return "company_confirmed" in row and not row["company_confirmed"]


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def load(kind: str) -> dict:
    """The whole file: `updated`, `note`, `row_template` and the raw `rows`, unvalidated."""
    path = reference_path(kind)
    if not path.exists():
        raise CurationError(f"{path} does not exist; the curated {kind} file is part of the repo")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CurationError(f"{path.name}: expected an object, found {type(data).__name__}")
    if not isinstance(data.get("rows"), list):
        raise CurationError(f"{path.name}: expected a 'rows' list, found {type(data.get('rows')).__name__}")
    return data


def rows(kind: str, today: date | None = None) -> list[dict]:
    """The publishable rows of one file: validated, stripped of private fields, flagged.

    Raises on the first *file* that has anything wrong with it, with every fault in every row in the
    message, because a curated file is short enough to fix in one pass and a half-published table is
    worse than a missing one.
    """
    data = load(kind)
    faults: list[str] = []
    out: list[dict] = []
    for i, row in enumerate(data["rows"]):
        if not isinstance(row, dict):
            faults.append(f"{kind}[{i}]: expected an object, found {type(row).__name__}")
            continue
        problems = row_problems(kind, row)
        if problems:
            label = row.get("deal_id") or row.get("ticker") or row.get("source") or row.get("metric") or i
            faults.append(f"{kind}[{label}]: " + "; ".join(problems))
            continue
        published = {k: v for k, v in row.items() if k not in PRIVATE_FIELDS}
        published["stale"] = is_stale(row.get("last_checked"), today)
        published["unconfirmed"] = is_unconfirmed(row)
        out.append(published)
    if faults:
        raise CurationError(
            f"{len(faults)} unpublishable row(s) in {reference_path(kind).name} — " + " | ".join(faults)
        )
    return out


def deals_table(today: date | None = None) -> pl.DataFrame:
    """The contract table as the snapshot writes it: contract columns only, schema-validated."""
    df = deals_frame(rows("deals", today))
    DEALS_SCHEMA.validate(df)
    return df


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------


def validate_all(kinds: tuple[str, ...] = KINDS, today: date | None = None) -> list[dict]:
    """One `file`, `rows` and `freshness` check per curated file, plus the deal table's schema.

    A file that will not parse or will not validate fails on its own line and the other three are
    still checked, so the page can say which curated layer is broken instead of losing all of them.
    """
    checks: list[dict] = []
    for kind in kinds:
        try:
            data = load(kind)
        except Exception as exc:
            checks.append(check(f"{kind} file", False, str(exc)))
            continue
        updated = str(data.get("updated") or "")
        checks.append(check(
            f"{kind} file",
            bool(_parse_date(updated)),
            f"{reference_path(kind).name} parsed, {len(data['rows'])} row(s), "
            + (f"updated {updated}" if _parse_date(updated) else f"updated {updated!r} is not a date"),
        ))
        try:
            published = rows(kind, today)
        except Exception as exc:
            checks.append(check(f"{kind} rows", False, str(exc)))
            continue
        stale = [r for r in published if r["stale"]]
        unconfirmed = [r for r in published if r["unconfirmed"]]
        checks.append(check(
            f"{kind} rows",
            True,
            "the file is empty, nothing to publish yet" if not published else
            f"{len(published)} row(s) sourced, dated and caveated; {len(unconfirmed)} not company-confirmed",
        ))
        checks.append(check(
            f"{kind} freshness",
            not stale,
            f"{len(stale)} of {len(published)} row(s) unchecked for more than {REFERENCE_STALE_DAYS} days"
            if published else f"no rows to re-check (limit {REFERENCE_STALE_DAYS} days)",
            warn=True,
        ))
    if "deals" in kinds:
        try:
            df = deals_table(today)
            checks.append(check("deals table", True, f"{df.height} row(s) validated against DEALS_SCHEMA"))
        except Exception as exc:
            checks.append(check("deals table", False, f"{type(exc).__name__}: {exc}"))
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", action="append", default=[], choices=KINDS, help="check only these files")
    args = ap.parse_args(argv)
    setup_logging()

    checks = validate_all(tuple(args.kind) or KINDS)
    for c in checks:
        log.info("check %-26s %-4s %s", c["name"], c["status"], c["detail"])
    failed = [c for c in checks if c["status"] == "fail"]
    if failed:
        log.error("%s curated file(s) are not fit to publish: %s", len(failed), ", ".join(c["name"] for c in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
