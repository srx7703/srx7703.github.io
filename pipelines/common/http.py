"""Small JSON GET client with retries, exponential backoff and per-client throttling."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_UA = "portfolio-pipelines/0.1 (+https://github.com/srx7703/srx7703.github.io)"


class HttpClient:
    def __init__(
        self,
        base_url: str = "",
        *,
        min_interval: float = 0.0,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        max_retries: int = 4,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json", **(headers or {})},
        )
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last = 0.0
        self.calls = 0

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.get(path, params=params)
                self.calls += 1
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code} for {r.request.url}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                if attempt == self.max_retries:
                    raise
                sleep = min(30.0, (2**attempt) + random.uniform(0, 0.5))
                if status == 429:
                    sleep = max(sleep, 5.0)
                log.warning(
                    "GET %s failed (%s); retry %d/%d in %.1fs",
                    path,
                    exc,
                    attempt + 1,
                    self.max_retries,
                    sleep,
                )
                time.sleep(sleep)
        raise RuntimeError(f"unreachable; last error: {last_exc!r}")

    def _throttle(self) -> None:
        if self.min_interval:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last = time.monotonic()

    def close(self) -> None:
        self._client.close()
