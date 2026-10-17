"""The curated private-company layer for the cybersecurity page (ring C).

Three files under ``data/reference/cyber/``: ``private_directory`` (who is in ring C, one row per company),
``private_metrics`` (one public fact per row: a round, a valuation, an ARR figure, an acquisition price) and
``events`` (IPOs, acquisitions, take-privates, delistings, priced rounds). The generic rules (a reachable link, a
publication date, a caveat, a re-check date, no working notes, whole-file-or-nothing) live in
:mod:`pipelines.common.curation`. What is here is what is specific to private-company facts, and each rule exists
because of a way this table can lie.

``tag``           how the figure was read: ``V`` on the primary page, ``S`` only in a search extract, ``C`` computed
                  from ``V`` inputs, ``U`` unverified. Only ``V`` and ``C`` rows may be chartable.
``metric_kind``   the closed vocabulary pinned in :mod:`pipelines.cyber.config`. A ``reported_talks``,
                  ``third_party_estimate`` or ``secondary_mark`` row is never chartable, whatever its tag: a number a
                  journalist heard, or a secondary-market price, is not a number the company or a filing stated.
``basis``         ``disclosed`` (the company or a filing states it), ``reported`` (a publication says so, the caveat
                  says which and when) or ``undisclosed`` (only the round amount is known, no valuation).
``qualifier``     ``=``, ``>``, ``<`` or ``~``. "More than $1.4 trillion" stored without its ``>`` would chart as a
                  point estimate.
``source_url``    never a paid database, a terminal, or an employer document. See ``BLOCKED_SOURCES``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from urllib.parse import urlparse

from pipelines.common.curation import CurationSpec, Validator, blank, one_of, parse_date
from pipelines.common.curation import rows as _rows
from pipelines.common.curation import validate_all as _validate_all
from pipelines.common.log import setup_logging
from pipelines.cyber.config import BASIS, LAYERS, METRIC_KINDS, NEVER_CHARTED_KINDS, STATUS

log = logging.getLogger("cyber.reference")

TAGS = ("V", "S", "C", "U")
CHARTABLE_TAGS = ("V", "C")
QUALIFIERS = ("=", ">", "<", "~")
MARKETS = ("overseas", "china")
METRIC_STATUSES = ("current", "superseded", "disputed")
EVENT_KINDS = ("ipo", "acquisition_announced", "acquisition_closed", "take_private", "delisting", "round")
BUYER_TYPES = ("platform_security", "non_security_software", "pe", "insurance", "state", "strategic", "other", "none")
DIRECTORY_STATUSES = ("independent", "pe_owned", "pending_acquisition", "acquired")

#: Hosts whose pages are paid databases or terminals. A fact read there is a fact nobody can re-open for free, and
#: their terms forbid republishing it. A link to one is rejected, however the number is labelled.
BLOCKED_SOURCES = (
    "pitchbook.com", "crunchbase.com", "cbinsights.com", "similarweb.com", "tracxn.com", "dealroom.co", "caplight.com",
    "preqin.com", "bloomberg.com", "owler.com", "zoominfo.com", "sensor-tower", "sensortower.com", "qichacha.com",
    "tianyancha.com", "itjuzi.com", "ft.com", "wsj.com",
)  # fmt: skip

KINDS = ("private_directory", "private_metrics", "events")

IDENTITY: dict[str, tuple[str, ...]] = {
    "private_directory": ("company", "layer", "market", "status"),
    "private_metrics": ("company", "metric", "period", "value", "unit", "status"),
    "events": ("company", "event_kind", "event_date"),
}


def _source_allowed(row: dict) -> list[str]:
    host = (urlparse(str(row.get("source_url", ""))).hostname or "").lower()
    # a blocked domain or any of its subdomains; never a substring (ft.com must not match microsoft.com)
    bad = [d for d in BLOCKED_SOURCES if host == d or host.endswith("." + d) or (d == "sensor-tower" and d in host)]
    return [f"source_url host {host!r} is a paid or restricted source ({bad[0]})"] if bad else []


def _chartable_needs_verified(row: dict) -> list[str]:
    """Only a figure read on its primary page, or computed from such figures, may reach a chart."""
    out = []
    if "chartable" in row and not isinstance(row["chartable"], bool):
        out.append("chartable must be true or false")
    if row.get("chartable") is True:
        if row.get("tag") not in CHARTABLE_TAGS:
            out.append(f"chartable row has tag {row.get('tag')!r}; only V or C may be charted")
        if row.get("metric_kind") in NEVER_CHARTED_KINDS:
            out.append(f"metric_kind {row.get('metric_kind')!r} is never charted")
        if row.get("basis") == "undisclosed" and row.get("metric_kind") == "primary_round_valuation":
            out.append("an undisclosed valuation cannot be charted")
    return out


def _dates_present(row: dict) -> list[str]:
    """``as_of_date`` is a calendar date. For a fact about a period it is the period's last day (2025 -> 2025-12-31,
    2024-05 -> 2024-05-31); the period label itself lives in ``period``."""
    out = [f"no {f}" for f in ("as_of_date", "last_checked") if blank(row.get(f))]
    as_of = row.get("as_of_date")
    if not blank(as_of) and parse_date(str(as_of)) is None:
        out.append(f"as_of_date {as_of!r} is not a YYYY-MM-DD date")
    return out


def _confirmed_is_bool(row: dict) -> list[str]:
    return [] if isinstance(row.get("company_confirmed"), bool) else ["company_confirmed must be true or false"]


def _numeric_value(row: dict) -> list[str]:
    v = row.get("value")
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return [f"value {v!r} is not a number"]
    return [] if v > 0 else [f"value {v} is not positive; leave the row out when the figure is undisclosed"]


def _definition_present(row: dict) -> list[str]:
    return [] if not blank(row.get("definition")) else ["no definition"]


def _layer_known(row: dict) -> list[str]:
    layer = row.get("layer")
    return [] if layer in LAYERS else [f"layer {layer!r} is not one of the twelve layers"]


def _reported_says_so(row: dict) -> list[str]:
    """A figure a publication reported, not one the company stated, has to say so where the reader sees it."""
    if row.get("basis") == "reported" and "report" not in str(row.get("caveat", "")).lower():
        return ["basis is 'reported' but the caveat does not say who reported it"]
    return []


def _sec_price_has_a_filing(row: dict) -> list[str]:
    if row.get("metric_kind") == "acquisition_price_sec" and "sec.gov" not in str(row.get("source_url", "")):
        return ["acquisition_price_sec must link to the SEC filing that records it"]
    return []


def _valuation_vintage(row: dict) -> list[str]:
    """The old-valuation trap: a headline from 2021 stands until a newer round replaces it, so its year travels."""
    if row.get("metric_kind") != "primary_round_valuation":
        return []
    year = row.get("vintage_year")
    if isinstance(year, bool) or not isinstance(year, int) or not 2015 <= year <= 2030:
        return [f"vintage_year {year!r} is not a year"]
    if not isinstance(row.get("stale_mark"), bool):
        return ["stale_mark must be true or false on a valuation row"]
    return []


def _china_amounts_are_ranges(row: dict) -> list[str]:
    """China's round amounts are mostly 'hundreds of millions of yuan': stored with ~ or >, never as a point."""
    exact = row.get("market") == "china" and row.get("metric_kind") == "round_amount" and row.get("qualifier") == "="
    if exact and row.get("basis") != "disclosed":
        return ["a China round amount that is not disclosed exactly needs a ~ or > qualifier"]
    return []


def _event_buyer(row: dict) -> list[str]:
    kind = row.get("event_kind")
    if kind in ("acquisition_announced", "acquisition_closed", "take_private") and blank(row.get("buyer_type")):
        return [f"{kind} needs a buyer_type"]
    return []


def _event_value(row: dict) -> list[str]:
    out: list[str] = []
    if row.get("value") is not None:
        out += [*_numeric_value(row), *one_of("qualifier", QUALIFIERS)(row), *one_of("metric_kind", METRIC_KINDS)(row)]
    if row.get("sec_value") is not None:
        sec = row["sec_value"]
        if isinstance(sec, bool) or not isinstance(sec, (int, float)) or sec <= 0:
            out.append(f"sec_value {sec!r} is not a positive number")
        if blank(row.get("unit")):
            out.append("sec_value needs the unit it is stated in")
        if "sec.gov" not in str(row.get("sec_source_url") or row.get("source_url", "")):
            out.append("sec_value needs an sec.gov link (sec_source_url, or the row's own source_url)")
    return out


_COMMON: tuple[Validator, ...] = (
    one_of("tag", TAGS),
    one_of("market", MARKETS),
    _chartable_needs_verified,
    _dates_present,
    _source_allowed,
)
_NUMERIC: tuple[Validator, ...] = (
    one_of("metric_kind", METRIC_KINDS),
    one_of("qualifier", QUALIFIERS),
    one_of("basis", BASIS),
    _numeric_value,
    _definition_present,
    _confirmed_is_bool,
    _layer_known,
    _reported_says_so,
    _sec_price_has_a_filing,
    _valuation_vintage,
    _china_amounts_are_ranges,
)

SPEC = CurationSpec(
    track="cyber",
    kinds=KINDS,
    identity=IDENTITY,
    stale_days=90,
    extra={
        "*": _COMMON,
        "private_directory": (one_of("status", DIRECTORY_STATUSES), _layer_known),
        "private_metrics": (*_NUMERIC, one_of("status", METRIC_STATUSES)),
        "events": (one_of("event_kind", EVENT_KINDS), one_of("buyer_type", BUYER_TYPES, required=False), _event_buyer,
                   _event_value, _layer_known, _confirmed_is_bool),
    },
)  # fmt: skip

assert set(DIRECTORY_STATUSES) <= set(STATUS), "directory statuses are a subset of the one status vocabulary"


def rows(kind: str, today: date | None = None) -> list[dict]:
    return _rows(SPEC, kind, today)


def validate_all(today: date | None = None) -> list[dict]:
    return _validate_all(SPEC, today)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="Validate the curated cybersecurity reference files.")
    p.add_argument("kinds", nargs="*", default=list(KINDS))
    args = p.parse_args(argv)
    bad = 0
    for kind in args.kinds:
        try:
            log.info("%s: %d row(s) OK", kind, len(rows(kind)))
        except Exception as exc:  # report every file, then fail
            log.error("%s: %s", kind, exc)
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
