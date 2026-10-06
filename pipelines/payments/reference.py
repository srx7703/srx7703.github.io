"""The curated layer for the payments-landscape page.

Seven files under ``data/reference/payments/``, one per W1 curator. The generic rules (a reachable link, a
publication date, a caveat, a re-check date, no working notes, whole-file-or-nothing) live in
:mod:`pipelines.common.curation`. What is here is the handful of rules specific to payments numbers, and each
exists because of a way this particular table can lie.

``tag``             how the figure was read: ``V`` on the primary page, ``S`` only in a search extract, ``C``
                    computed from ``V`` inputs, ``U`` unverified. Only ``V`` and ``C`` rows may be chartable.
``metric_kind``     the closed vocabulary pinned in :mod:`pipelines.payments.config`. A ``reported_talks`` or
                    ``third_party_estimate`` row is never chartable, whatever its tag: a number a journalist
                    heard is not a number the company stated.
``qualifier``       ``=``, ``>``, ``<`` or ``~``. "More than $1.4 trillion" stored as ``1.4e12`` without its
                    ``>`` would chart as a point estimate.
``as_of_date``      the period or date the figure describes, distinct from when it was published.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from pipelines.common.curation import CurationSpec, Validator, blank, one_of
from pipelines.common.curation import rows as _rows
from pipelines.common.curation import validate_all as _validate_all
from pipelines.common.log import setup_logging
from pipelines.payments.config import METRIC_KINDS, NEVER_CHARTED_KINDS

log = logging.getLogger("payments.reference")

TAGS = ("V", "S", "C", "U")
CHARTABLE_TAGS = ("V", "C")
QUALIFIERS = ("=", ">", "<", "~")
SEGMENTS = ("A", "B", "C", "D", "E", "F")
PRODUCT_STATUSES = ("live", "beta", "announced", "discontinued", "none")
EVENT_KINDS = ("ipo", "acquisition", "priced_round", "tender", "direct_listing", "spac")

KINDS = ("kpi_disclosures_acceptance", "kpi_disclosures_other", "private_metrics", "waterfall_inputs",
         "products", "events", "denominators")

_METRIC_ROW = ("company", "metric", "period", "value", "unit")
IDENTITY: dict[str, tuple[str, ...]] = {
    "kpi_disclosures_acceptance": _METRIC_ROW,
    "kpi_disclosures_other": _METRIC_ROW,
    "private_metrics": (*_METRIC_ROW, "status"),
    "waterfall_inputs": ("waterfall", "line", "value", "unit"),
    "products": ("company", "product_line", "product_status"),
    "events": ("company", "event_kind", "event_date"),
    "denominators": ("series", "metric", "period", "value", "unit"),
}


def _chartable_needs_verified(row: dict) -> list[str]:
    """Only a figure read on its primary page, or computed from such figures, may reach a chart."""
    out = []
    if row.get("chartable") is True:
        if row.get("tag") not in CHARTABLE_TAGS:
            out.append(f"chartable row has tag {row.get('tag')!r}; only V or C may be charted")
        if row.get("metric_kind") in NEVER_CHARTED_KINDS:
            out.append(f"metric_kind {row.get('metric_kind')!r} is never charted")
    if "chartable" in row and not isinstance(row["chartable"], bool):
        out.append("chartable must be true or false")
    return out


def _dates_present(row: dict) -> list[str]:
    return [f"no {f}" for f in ("as_of_date", "last_checked") if blank(row.get(f))]


def _confirmed_is_bool(row: dict) -> list[str]:
    v = row.get("company_confirmed")
    return [] if isinstance(v, bool) else ["company_confirmed must be true or false"]


def _numeric_value(row: dict) -> list[str]:
    v = row.get("value")
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return [f"value {v!r} is not a number"]
    return []


def _definition_present(row: dict) -> list[str]:
    return [] if not blank(row.get("definition")) else ["no definition"]


_COMMON: tuple[Validator, ...] = (
    one_of("tag", TAGS),
    _chartable_needs_verified,
    _dates_present,
    _confirmed_is_bool,
)
_NUMERIC: tuple[Validator, ...] = (
    one_of("metric_kind", METRIC_KINDS),
    one_of("qualifier", QUALIFIERS),
    _numeric_value,
    _definition_present,
)

SPEC = CurationSpec(
    track="payments",
    kinds=KINDS,
    identity=IDENTITY,
    stale_days=120,
    extra={
        "*": _COMMON,
        "kpi_disclosures_acceptance": _NUMERIC,
        "kpi_disclosures_other": _NUMERIC,
        "private_metrics": (*_NUMERIC, one_of("status", ("current", "superseded", "disputed"))),
        "waterfall_inputs": (one_of("qualifier", QUALIFIERS), _numeric_value, _definition_present),
        "products": (one_of("product_status", PRODUCT_STATUSES),),
        "events": (one_of("event_kind", EVENT_KINDS), one_of("metric_kind", METRIC_KINDS, required=False)),
        "denominators": (one_of("qualifier", QUALIFIERS), _numeric_value, _definition_present),
    },
)


def rows(kind: str, today: date | None = None) -> list[dict]:
    return _rows(SPEC, kind, today)


def validate_all(today: date | None = None) -> list[dict]:
    return _validate_all(SPEC, today)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="Validate the curated payments reference files.")
    p.add_argument("kinds", nargs="*", default=list(KINDS))
    args = p.parse_args(argv)
    bad = 0
    for kind in args.kinds:
        try:
            n = len(rows(kind))
            log.info("%s: %d row(s) OK", kind, n)
        except Exception as exc:  # report every file, then fail
            log.error("%s: %s", kind, exc)
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
