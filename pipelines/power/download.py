"""Binary downloads for the power workbooks, with the one check that actually matters.

``pipelines.common.http.HttpClient`` speaks JSON. These sources are Excel files, and EIA has a habit
that a plain ``raise_for_status`` cannot catch: a month that has not been published yet answers
**HTTP 200 with the section's HTML index page**, about 55 KB of markup, rather than a 404. Handing
that to a parser gives a confusing error a long way from the cause, and worse, a retry loop treats it
as a permanent success. So every download that should be a workbook is checked for the ZIP magic
number that begins every xlsx file, and anything else is treated as "not published".
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import httpx

from pipelines.common.http import DEFAULT_UA

log = logging.getLogger("power.download")

ZIP_MAGIC = b"PK"
MIN_WORKBOOK_BYTES = 10_000


class NotPublished(Exception):
    """The URL answered, but with something that is not a workbook."""


def get_bytes(url: str, *, timeout: float = 120.0, max_retries: int = 3) -> bytes:
    """Fetch a URL, retrying transport errors and 5xx. 404 raises immediately."""
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": DEFAULT_UA})
            if r.status_code == 404:
                raise NotPublished(f"404 for {url}")
            if r.status_code >= 500 or r.status_code == 429:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.content
        except NotPublished:
            raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            if attempt == max_retries:
                raise
            sleep = min(30.0, 2**attempt + random.uniform(0, 0.5))
            log.warning("GET %s failed (%s); retry %d/%d in %.1fs", url, exc, attempt + 1, max_retries, sleep)
            time.sleep(sleep)
    raise RuntimeError(f"unreachable; last error: {last!r}")


def get_workbook(url: str, **kw) -> bytes:
    """Fetch a URL that must be an xlsx, or raise ``NotPublished``.

    The size floor catches a truncated download; the magic-number check catches EIA answering 200
    with its own directory listing for a month it has not released.
    """
    body = get_bytes(url, **kw)
    if len(body) < MIN_WORKBOOK_BYTES:
        raise NotPublished(f"{url} returned {len(body)} bytes, too small to be a workbook")
    if not body.startswith(ZIP_MAGIC):
        head = body[:80].decode("utf-8", "replace").replace("\n", " ")
        raise NotPublished(f"{url} returned {len(body)} bytes that are not a zip: {head!r}")
    return body


def cache_to(body: bytes, path: Path) -> Path:
    """Write a fetched workbook beside the run so a parse failure can be reproduced offline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path
