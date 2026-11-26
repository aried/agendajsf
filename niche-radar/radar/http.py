"""HTTP client with per-host pacing, retries and Retry-After support.

Standard library only. Proxies and CA bundles come from the usual environment
variables (HTTPS_PROXY, SSL_CERT_FILE), which urllib honours on its own.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import urlsplit

from . import __version__

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Minimum seconds between two requests to the same host.
HOST_INTERVALS = {
    "wikimedia.org": 2.5,
    "wikipedia.org": 2.5,
    "api.github.com": 6.5,
}
DEFAULT_INTERVAL = 0.5


class SourceError(Exception):
    """A source could not be read. Carries the HTTP status when there is one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Http:
    def __init__(
        self,
        contact: str = "",
        timeout: float = 25.0,
        max_retries: int = 2,
        max_wait: float = 45.0,
        sleep=time.sleep,
        clock=time.monotonic,
        opener=None,
    ):
        suffix = f"; {contact}" if contact else ""
        self.user_agent = f"niche-radar/{__version__} (personal research script{suffix})"
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._open = opener or urllib.request.urlopen
        self._last_call: dict[str, float] = {}
        self.requests: Counter[str] = Counter()

    def _pace(self, host: str) -> None:
        interval = next(
            (v for k, v in HOST_INTERVALS.items() if host == k or host.endswith("." + k)),
            DEFAULT_INTERVAL,
        )
        last = self._last_call.get(host)
        if last is not None:
            wait = interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)

    def get(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        host = urlsplit(url).hostname or ""
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, **(headers or {})}
        )
        last_error: SourceError | None = None
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            self._pace(host)
            self.requests[host] += 1
            wait = min(2.0 * 2**attempt, self.max_wait)
            try:
                with self._open(request, timeout=self.timeout) as response:
                    body = response.read()
                self._last_call[host] = self._clock()
                return body
            except urllib.error.HTTPError as error:
                self._last_call[host] = self._clock()
                snippet = error.read()[:300].decode("utf-8", "replace").strip()
                last_error = SourceError(f"{host}: HTTP {error.code}: {snippet}", error.code)
                if error.code not in RETRY_STATUSES:
                    raise last_error from None
                if error.code == 429:
                    # One patient retry; a second refusal means the limit is real.
                    if rate_limited:
                        raise last_error from None
                    rate_limited = True
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                if retry_after.strip().isdigit():
                    asked = float(retry_after)
                    if asked > self.max_wait:
                        raise last_error from None
                    wait = asked + 1.0
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                self._last_call[host] = self._clock()
                last_error = SourceError(f"{host}: {error}")
            if attempt < self.max_retries:
                self._sleep(wait)
        raise last_error or SourceError(f"{host}: request failed")

    def get_json(self, url: str, headers: dict[str, str] | None = None):
        body = self.get(url, {"Accept": "application/json", **(headers or {})})
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            host = urlsplit(url).hostname
            snippet = body[:200].decode("utf-8", "replace").strip()
            raise SourceError(f"{host}: resposta não é JSON: {snippet}") from None
