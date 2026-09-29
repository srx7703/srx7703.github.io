"""ECOS 402Y016 raw responses -> normalised snapshot.

v1 makes no network call. The raw layer was written once, by the registration pull on 2026-09-28
(``data/raw/memcycle/ecos/2026-09-28/1316/``), and is append-only; this module only parses it.

The pull used ECOS's public ``sample`` key, which returns at most ten rows per request, so each series arrives as
a probe of its first and last row followed by ten-month pages. A month can therefore appear twice. A repeated
month with the same value is collapsed; a repeated month with a *different* value stops the run, because it
means the source revised a number between two pages of the same pull and neither can be preferred silently.

Run: ``python -m pipelines.memcycle.ingest``
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from pathlib import Path

import polars as pl

from pipelines.common.log import setup_logging
from pipelines.common.storage import write_parquet
from pipelines.memcycle import config as cfg
from pipelines.memcycle import schema

log = logging.getLogger("memcycle.ingest")

SERIES = [(item, basis) for item in (cfg.DRAM_ITEM, cfg.FLASH_ITEM) for basis in (cfg.MAIN_BASIS, *cfg.SENS_BASES)]
COLUMNS = ["item_code", "item_name", "basis", "month", "value", "unit", "weight", "retrieved_at", "preliminary"]


class EcosError(ValueError):
    """A response that is not a usable StatisticSearch payload."""


def parse_line(line: str) -> dict:
    """One JSONL record of the raw file: ``{url, fetched_at, response}``."""
    rec = json.loads(line)
    resp = rec.get("response")
    if isinstance(resp, str):
        head = resp.lstrip()[:15].lower()
        if head.startswith("<!doctype") or head.startswith("<html") or head.startswith("<"):
            raise EcosError(f"HTML instead of JSON from {rec.get('url')}")
        raise EcosError(f"non-JSON response from {rec.get('url')}")
    if not isinstance(resp, dict):
        raise EcosError(f"no response object for {rec.get('url')}")
    return rec


def search_rows(rec: dict) -> list[dict]:
    """Rows of a StatisticSearch response, or [] for other endpoints. An ECOS error payload raises."""
    resp = rec["response"]
    if "RESULT" in resp:
        r = resp["RESULT"]
        raise EcosError(f"ECOS error {r.get('CODE')}: {r.get('MESSAGE')} ({rec.get('url')})")
    if "StatisticSearch" not in resp:
        return []
    out = []
    for row in resp["StatisticSearch"].get("row", []):
        t = row["TIME"]
        if len(t) != 6 or not t.isdigit():
            raise EcosError(f"not a monthly TIME value: {t!r}")
        out.append({
            "item_code": row["ITEM_CODE1"],
            "item_name": row["ITEM_NAME1"],
            "basis": row["ITEM_CODE2"],
            "month": f"{t[:4]}-{t[4:]}",
            "value": float(row["DATA_VALUE"]),
            "unit": row["UNIT_NAME"],
            "weight": float(row["WGT"]) if row.get("WGT") not in (None, "") else None,
            "retrieved_at": rec["fetched_at"],
        })
    return out


def _is_probe(url: str) -> bool:
    """The single-row probes (first and last row over the whole span) that precede each series' pages."""
    # https://ecos.bok.or.kr/api/StatisticSearch/<key>/json/kr/<start>/<end>/<table>/M/<from>/<to>/<item>/<basis>
    parts = url.rstrip("/").split("/")
    return parts[8] == parts[9] and parts[12][:4] != parts[13][:4]


def parse_raw(lines: list[str]) -> list[dict]:
    """Normalised rows for the six registered series; paged rows win over the probes' timestamps."""
    rows: dict[tuple[str, str, str], dict] = {}
    for line in lines:
        if not line.strip():
            continue
        rec = parse_line(line)
        probe = _is_probe(rec["url"]) if "StatisticSearch" in rec["url"] else False
        for r in search_rows(rec):
            if (r["item_code"], r["basis"]) not in SERIES:
                continue
            key = (r["item_code"], r["basis"], r["month"])
            prev = rows.get(key)
            if prev is not None:
                if prev["value"] != r["value"]:
                    raise EcosError(f"conflicting values for {key}: {prev['value']} vs {r['value']}")
                if probe or not prev.get("_probe"):
                    continue
            rows[key] = {**r, "_probe": probe}
    out = sorted(rows.values(), key=lambda r: (r["item_code"], r["basis"], r["month"]))
    last = {}
    for r in out:
        last[(r["item_code"], r["basis"])] = max(last.get((r["item_code"], r["basis"]), ""), r["month"])
    for r in out:
        r.pop("_probe")
        # ECOS publishes the latest month of the export price index as preliminary and revises it with the
        # next release
        r["preliminary"] = r["month"] == last[(r["item_code"], r["basis"])]
    return out


def read_raw(run_dir: Path) -> list[str]:
    with gzip.open(run_dir / "ecos_raw_responses.jsonl.gz", "rt", encoding="utf-8") as f:
        return f.read().splitlines()


def snapshot(run_dir: Path | None = None) -> pl.DataFrame:
    run_dir = run_dir or cfg.RAW_ECOS_DIR / cfg.REGISTRATION_ECOS_RUN
    df = pl.DataFrame(parse_raw(read_raw(run_dir)), schema_overrides={"weight": pl.Float64}).select(COLUMNS)
    return schema.ECOS_SCHEMA.validate(df)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=cfg.REGISTRATION_ECOS_RUN, help="raw partition <date>/<HHMM>")
    args = ap.parse_args(argv)
    setup_logging()
    df = snapshot(cfg.RAW_ECOS_DIR / args.run)
    write_parquet(df, cfg.SNAP_PATH)
    log.info("wrote %s rows to %s", df.height, cfg.SNAP_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
