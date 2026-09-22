"""The curated layer for the soft-tissue surgical robot page.

Four files, and between them they carry almost every number the page prints, because almost nothing in this
market is fetchable. The generic rules — a reachable link, a publication date, a caveat, a re-check date, no
working notes, whole-file-or-nothing — live in :mod:`pipelines.common.curation`. What is here is the handful of
rules specific to counting surgical robots, and each exists because of a way this particular table can lie.

``units``    a unit figure without its **basis** is uncomparable, so basis is required and closed. Intuitive
             *places* systems including under lease, Edge Medical reports systems "installed or delivered",
             Surgerii's prospectus reports production and sales as separate rows, and MedBot reports orders
             apart from installs. Four companies, four meanings of "units".
``tenders``  a **contract_kind** is required, because a leasing award names the leasing company as winner,
             leaves the brand blank, and prints a multi-year lease fee in the unit-price column. Without the
             kind, that fee walks straight into an average selling price.
``quota``    licences permitted, licences awarded and machines installed are three different numbers that every
             secondary source conflates. The file stores only what its source actually states.
``denovo``   the FDA grants that created the new regulations, which openFDA does not serve.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from pipelines.common.curation import (
    CurationError,
    CurationSpec,
    Validator,
    blank,
    one_of,
    positive_number,
)
from pipelines.common.curation import load as _load
from pipelines.common.curation import rows as _rows
from pipelines.common.curation import validate_all as _validate_all
from pipelines.common.log import setup_logging
from pipelines.surgical.config import (
    PLACEMENT_MODEL,
    REFERENCE_STALE_DAYS,
    TIER_ORDER,
    UNIT_BASIS,
)

log = logging.getLogger("surgical.reference")

GEOGRAPHIES = ("global", "us", "cn", "ex-cn", "eu", "jp", "kr")
CONTRACT_KINDS = ("purchase", "lease", "maintenance")


def _geography_required_for_unit(row: dict) -> list[str]:
    """A unit figure without a geography is the Procept trap.

    Procept's quarterly installed base and procedure counts are **United States only**, and a global figure
    circulates that its own release does not contain. Stored without a geography, the US number would be
    compared against Intuitive's worldwide installed base and understate Procept by whatever its ex-US fleet
    happens to be. So every unit row says which world it counts.
    """
    return [] if not blank(row.get("geography")) else ["no geography; a unit count with no world is uncomparable"]


def _lease_is_not_a_price(row: dict) -> list[str]:
    """A lease award's unit price is a multi-year fee, not what a machine costs.

    Chinese tender notices for leased systems name a leasing company as the winner, leave the brand column
    empty, and put the lease fee where a unit price would go. Letting that row claim a brand would attribute a
    financing arrangement to a manufacturer, and letting it into an average selling price would drag the average
    toward a number that is not a price at all.
    """
    out = []
    if row.get("contract_kind") != "lease":
        return out
    if not blank(row.get("brand")):
        out.append("a lease award names the leasing company, not the manufacturer, so brand must be empty")
    caveat = str(row.get("caveat") or "").lower()
    if "lease" not in caveat and "租" not in str(row.get("caveat") or ""):
        out.append("a lease row's caveat must say the price is a lease fee rather than a machine price")
    return out


def _maintenance_has_no_units(row: dict) -> list[str]:
    """A service contract is not a shipment. It is kept, because its price is informative, but it carries no
    quantity that may be counted as a machine."""
    if row.get("contract_kind") != "maintenance":
        return []
    return [] if row.get("quantity") is None else ["a maintenance contract must not carry a machine quantity"]


def _partial_purchase_says_so(row: dict) -> list[str]:
    """An award that is not a complete clinical system must say what it actually bought.

    The panel contains a research and teaching robotic arm at CNY 1.09m and a training simulator. Both are
    real awards with real prices, and neither is what a hospital pays for a surgical robot. They stay in the
    table because their existence is informative, they are excluded from every price statistic, and their
    caveat has to name what the money bought so a reader scanning the table is not misled by the row itself.
    """
    if row.get("complete_system") is not False:
        return []
    caveat = str(row.get("caveat") or "")
    return [] if len(caveat) > 40 else [
        "an award that is not a complete system must say in its caveat what it actually bought"]


def _quota_numbers_agree(row: dict) -> list[str]:
    """Newly added licences cannot exceed the total permitted."""
    total, new = row.get("permitted_total"), row.get("newly_added")
    if total is None or new is None:
        return []
    return [] if new <= total else [f"newly_added {new} exceeds permitted_total {total}"]


UNIT_RULES: tuple[Validator, ...] = (
    one_of("metric", UNIT_BASIS),
    one_of("basis", UNIT_BASIS),
    one_of("geography", GEOGRAPHIES),
    one_of("tier", TIER_ORDER),
    one_of("source_kind", ("company", "third_party")),
    one_of("placement_model", PLACEMENT_MODEL, required=False),
    positive_number("value", allow_null=False),
    _geography_required_for_unit,
)

TENDER_RULES: tuple[Validator, ...] = (
    one_of("contract_kind", CONTRACT_KINDS),
    positive_number("quantity"),
    positive_number("unit_price_cny"),
    positive_number("total_price_cny"),
    _lease_is_not_a_price,
    _maintenance_has_no_units,
    _partial_purchase_says_so,
)

QUOTA_RULES: tuple[Validator, ...] = (_quota_numbers_agree,)

SPEC = CurationSpec(
    track="surgical",
    kinds=("units", "tenders", "quota", "denovo"),
    identity={
        "units": ("maker", "metric", "basis", "period", "value", "geography", "tier", "source_kind",
                  "last_checked"),
        "tenders": ("notice_id", "hospital", "contract_kind", "award_date", "complete_system",
                    "last_checked"),
        "quota": ("province", "plan", "permitted_total", "newly_added"),
        "denovo": ("grant_id", "applicant", "device_name", "decision_date"),
    },
    stale_days=REFERENCE_STALE_DAYS,
    extra={"units": UNIT_RULES, "tenders": TENDER_RULES, "quota": QUOTA_RULES},
)

KINDS = SPEC.kinds


def load(kind: str) -> dict:
    return _load(SPEC, kind)


def rows(kind: str, today: date | None = None) -> list[dict]:
    return _rows(SPEC, kind, today)


def validate_all(today: date | None = None) -> list[dict]:
    return _validate_all(SPEC, today)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argparse.ArgumentParser(description="Validate the curated surgical reference files").parse_args(argv)
    try:
        checks = validate_all()
    except CurationError as exc:
        log.error("%s", exc)
        return 1
    for c in checks:
        log.info("check %-28s %-5s %s", c["name"], c["status"], c["detail"])
    return 1 if any(c["status"] == "fail" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
