"""openFDA: the competitive set, from the only machine-readable registry in this project.

What this gives and does not give, because the distinction shapes the whole page: an FDA clearance record says
**who may sell a device in the United States**. Not one field in it relates to volume, price or installed base.
So this module produces a competitive set and an approval timeline, never a share.

There is a structural quirk worth the code that exploits it. Intuitive's da Vinci sits in the legacy endoscopic
product code, which it has occupied for two decades and which holds a few hundred records, most of them its own.
Between 2024 and 2026 the FDA wrote **new regulations** for the modern soft-tissue entrants, so Symani, Versius,
Dexter and Ottava each arrived under a fresh product code with almost nothing else in it. The consequence is
convenient: the non-Intuitive field can be enumerated exactly, in one query, instead of being sifted out of the
incumbent's twenty-year filing history.

openFDA needs no key. It allows 240 requests a minute and 1,000 a day per IP, which is far more than this needs —
the whole registry is a few hundred records — so the client is deliberately slow and single-threaded.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, run_stamp, utc_now, write_json, write_parquet
from pipelines.surgical import config as cfg
from pipelines.surgical.schema import CLEARANCES_SCHEMA, clearances_frame

log = logging.getLogger("surgical.registry")

OUT_DIR = SNAP_DIR / "surgical"
PAGE = 100
UA = "portfolio-pipelines/0.1 surgical.registry (+https://github.com/srx7703/srx7703.github.io)"

#: Applicant-name fragments mapped to a config.MAKERS ticker. Applicant names in FDA records are legal entities,
#: not brands: Hugo is filed by Covidien, not by "Medtronic". Matching on the brand alone would lose it.
APPLICANT_TO_MAKER = {
    "intuitive": "ISRG",
    "procept": "PRCT",
    "covidien": "MDT",
    "medtronic": "MDT",
    "ethicon": "JNJ",
    "auris": "JNJ",
    "johnson": "JNJ",
    "cmr surgical": "private:cmr",
    "distalmotion": "private:distalmotion",
    "medical microinstruments": "private:mmi",
    "momentis": "private:momentis",
    "moon surgical": "private:moon",
    "virtual incision": "private:virtualincision",
    "asensus": "private:karlstorz",
    "transenterix": "private:karlstorz",
    "karl storz": "private:karlstorz",
    "medicaroid": "private:medicaroid",
    "meere": "058110.KQ",
    "noah medical": "",
}


class UnknownEndpoint(RuntimeError):
    """The openFDA path does not exist. Distinct from a query that matched nothing."""


def _get(path: str, params: dict) -> dict:
    """One openFDA call.

    openFDA answers 404 for two completely different situations and they must not be conflated. A query that
    matched nothing returns JSON with ``error.code == "NOT_FOUND"``, which is a real answer. A path that does not
    exist returns an HTML page saying ``Cannot GET``, which means the code is asking for something that is not
    there — and swallowing it as "no results" is how a whole registry silently reports zero for every query.
    That happened here: ``/device/denovo.json`` does not exist, and the first version of this module recorded
    four empty De Novo fetches as successes.
    """
    r = httpx.get(f"{cfg.OPENFDA_BASE}{path}", params=params, timeout=60.0,
                  headers={"User-Agent": UA}, follow_redirects=True)
    if r.status_code == 404:
        try:
            body = r.json()
        except ValueError:
            raise UnknownEndpoint(
                f"{cfg.OPENFDA_BASE}{path} does not exist (openFDA returned HTML, not JSON). "
                "This is a wrong path, not an empty result."
            ) from None
        if (body.get("error") or {}).get("code") == "NOT_FOUND":
            return {"results": [], "meta": {"results": {"total": 0}}}
        raise UnknownEndpoint(f"{cfg.OPENFDA_BASE}{path}: unexpected 404 body {body!r}")
    r.raise_for_status()
    return r.json()


def _maker_for(applicant: str | None) -> str | None:
    if not applicant:
        return None
    low = applicant.lower()
    for fragment, ticker in APPLICANT_TO_MAKER.items():
        if fragment in low:
            return ticker or None
    return None


#: openFDA exposes 510(k) and not De Novo — /device/denovo.json is not a path. The De Novo grants that created
#: the new regulations for Ottava, Versius, Dexter and Symani therefore cannot be fetched here and live in the
#: curated layer instead, each with its grant number and the FDA page it was read from.
REGISTRY_ENDPOINT = {"fda_510k": "/510k.json"}


def fetch_code(code: str, registry: str = "fda_510k", *, pause: float = 0.3) -> list[dict]:
    """Every record under one product code, paged. Small by construction: hundreds, not millions."""
    if registry not in REGISTRY_ENDPOINT:
        raise UnknownEndpoint(f"openFDA has no endpoint for {registry}; see REGISTRY_ENDPOINT")
    endpoint = REGISTRY_ENDPOINT[registry]
    out: list[dict] = []
    skip = 0
    while True:
        payload = _get(endpoint, {"search": f"product_code:{code}", "limit": PAGE, "skip": skip})
        results = payload.get("results", [])
        out.extend(results)
        total = payload.get("meta", {}).get("results", {}).get("total", len(out))
        skip += len(results)
        if not results or skip >= total or skip >= 1000:
            break
        time.sleep(pause)
    log.info("%s %s: %d records", registry, code, len(out))
    return out


def normalise(raw: list[dict], registry: str, code: str, snapshot_ts: str) -> list[dict]:
    rows = []
    for r in raw:
        cid = r.get("k_number") or r.get("denovo_number") or r.get("decision_number")
        if not cid:
            continue
        applicant = r.get("applicant") or r.get("company_name")
        raw_date = r.get("decision_date") or r.get("date_received") or ""
        # openFDA dates arrive either as YYYYMMDD or already hyphenated, depending on the endpoint.
        date = raw_date if "-" in raw_date else (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else None
        )
        rows.append({
            "snapshot_ts": snapshot_ts,
            "registry": registry,
            "clearance_id": cid,
            "applicant": applicant,
            "device_name": r.get("device_name") or r.get("trade_name"),
            "product_code": r.get("product_code") or code,
            "decision_date": date,
            "decision": r.get("decision_description") or r.get("decision_code"),
            "regulation_number": r.get("regulation_number"),
            "maker": _maker_for(applicant),
            # Every code queried here was chosen because it is soft tissue; the flag exists so a later widening
            # of the code list does not silently pull orthopaedic devices onto the page.
            "in_scope": code in cfg.FDA_PRODUCT_CODES,
        })
    return rows


def run(*, codes: tuple[str, ...] | None = None) -> dict:
    ts = utc_now()
    snapshot_ts = ts.isoformat()
    run_date, _ = run_stamp(ts)
    codes = codes or tuple(cfg.FDA_PRODUCT_CODES)

    rows: list[dict] = []
    errors: list[str] = []
    per_code: dict[str, int] = {}
    for code in codes:
        for registry in REGISTRY_ENDPOINT:
            try:
                got = normalise(fetch_code(code, registry), registry, code, snapshot_ts)
            except Exception as exc:                      # one dead code must not lose the others
                errors.append(f"{registry}:{code}: {exc}")
                log.warning("%s %s failed: %s", registry, code, exc)
                continue
            per_code[f"{registry}:{code}"] = len(got)
            rows.extend(got)

    df = clearances_frame(rows)
    path = None
    if df.height:
        CLEARANCES_SCHEMA.validate(df)
        path = write_parquet(df, OUT_DIR / "clearances" / f"{run_date}.parquet")

    matched = df.filter(df["maker"].is_not_null()).height if df.height else 0
    incumbent = df.filter(df["maker"] == "ISRG").height if df.height else 0
    checks = [
        check("Registry fetched", df.height > 0,
              f"{df.height} 510(k) records across {len(per_code)} product codes"
              + (f"; failed: {'; '.join(errors)}" if errors else "")),
        check("Applicants resolved", df.height > 0 and matched / max(df.height, 1) > 0.5,
              f"{matched} of {df.height} records mapped to a maker in the pool; the rest are applicants outside "
              "it, which is expected because a product code is not exclusive to one company", warn=True),
        check("Incumbent concentration", True,
              f"{incumbent} of {df.height} records belong to Intuitive, which is why the legacy code cannot be "
              "used as a competitor list on its own"),
        # openFDA covers 510(k) only, so the grants that created the new codes are not in this table.
        check("De Novo grants", False,
              "openFDA has no De Novo endpoint, so the grants that created the SAQ/SCV/SDD/SIR regulations are "
              "curated by hand rather than fetched; this table holds the follow-on 510(k)s only", warn=True),
        # Said on every run so nobody later mistakes this table for a market measure.
        check("No volume in this source", False,
              "an FDA clearance says who may sell, never how many were sold; every unit figure on the page comes "
              "from the curated layer instead", warn=True),
    ]
    return {"rows": df.height, "path": str(path) if path else None, "per_code": per_code,
            "errors": errors, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Fetch FDA clearances for soft-tissue surgical robots")
    ap.add_argument("--codes", default="", help="comma-separated product codes (default: config.FDA_PRODUCT_CODES)")
    a = ap.parse_args(argv)
    res = run(codes=tuple(c.strip() for c in a.codes.split(",") if c.strip()) or None)
    for c in res["checks"]:
        log.info("check %-26s %-5s %s", c["name"], c["status"], c["detail"])
    write_json(res["per_code"], OUT_DIR / "clearances" / "last_run_counts.json")
    failed = [c["name"] for c in res["checks"] if c["status"] == "fail"]
    return 1 if (res["errors"] or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
