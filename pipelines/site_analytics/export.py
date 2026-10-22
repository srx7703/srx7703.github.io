"""Download raw events from Mixpanel's Export API into data/private/site_analytics/ (gitignored, never committed).

Needs a Mixpanel service account in the environment (a local .env, never the repository):
MIXPANEL_PROJECT_ID, MIXPANEL_SERVICE_ACCOUNT, MIXPANEL_SERVICE_SECRET.

    uv run --env-file .env python -m pipelines.site_analytics.export 2026-10-22 2026-10-29
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

EXPORT_URL = "https://data.mixpanel.com/api/2.0/export"
OUT_DIR = Path("data/private/site_analytics")


def export(from_date: str, to_date: str, out_dir: Path = OUT_DIR) -> Path:
    """Stream the raw events between two dates (inclusive, project time zone) to a JSONL file and return its path."""
    auth = (os.environ["MIXPANEL_SERVICE_ACCOUNT"], os.environ["MIXPANEL_SERVICE_SECRET"])
    params = {"project_id": os.environ["MIXPANEL_PROJECT_ID"], "from_date": from_date, "to_date": to_date}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"events_{from_date}_{to_date}.jsonl"
    with httpx.stream("GET", EXPORT_URL, params=params, auth=auth, timeout=300) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    log.info("wrote %s", out)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(export(argv[0], argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
