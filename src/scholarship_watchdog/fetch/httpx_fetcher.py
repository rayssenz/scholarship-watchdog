"""The default fetcher: httpx plus trafilatura. See SPEC.md section 3.1.

Free and sufficient for DAAD and most static university pages. Politeness is
not optional here: this project reads a small number of institutional pages on
a weekly schedule, and an honest user agent plus rate limiting is what keeps
that welcome.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlsplit

import httpx

from ..models import Source
from .base import MAX_RESPONSE_BYTES, FetchResult, ResponseTooLargeError, read_capped
from .canonical import canonicalise_markdown_links
from .clean import clean

USER_AGENT = "scholarship-watchdog/0.1 (+https://github.com/rayssenz/scholarship-watchdog)"
TIMEOUT = 25.0
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpxFetcher:
    """Retrieve a static page over plain HTTP."""

    name: ClassVar[str] = "httpx"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        max_attempts: int = 3,
        max_bytes: int = MAX_RESPONSE_BYTES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._max_attempts = max_attempts
        self._max_bytes = max_bytes
        self._sleep = sleep
        self._last_request_at: dict[str, float] = {}

    def fetch(self, source: Source, page_url: str) -> FetchResult:
        # One client serves every source, private ones first, and it keeps a
        # cookie jar. Left alone, a cookie a private page set rides along on the
        # next public request, so a public snapshot could change with the private
        # registry (SPEC.md section 5). Every fetch starts with an empty jar;
        # cookies set during one fetch's redirect chain still apply within it.
        self._client.cookies.clear()
        self._wait_for_host(page_url, source.rate_limit_seconds)
        status, text, failure = self._get_with_retries(page_url)

        if status is None:
            return self._failed(source, page_url, reason=failure)

        if status >= 400 or text is None:
            return self._failed(source, page_url, reason=failure, http_status=status)

        markdown = clean(text, keep_links=source.is_discover)
        if source.is_discover and markdown:
            markdown = canonicalise_markdown_links(markdown, base=page_url)

        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=markdown,
            status="ok",
            fetched_at=datetime.now(UTC),
            http_status=status,
        )

    def _get_with_retries(self, page_url: str) -> tuple[int | None, str | None, str | None]:
        """Retry only what a retry can fix: transient status codes and timeouts.

        A 404 will not become a 200 on the third attempt, so retrying it only
        spends the run's time budget and hammers someone else's server.

        The body is streamed rather than fetched whole, because the byte budget
        has to be enforced while reading. A non-streaming get() has already
        buffered the entire response by the time any check could run, which is
        exactly the failure the budget exists to prevent, so the body is read
        inside the stream context and this returns text rather than a response.
        """
        failure: str | None = None
        headers = {"User-Agent": USER_AGENT}

        for attempt in range(1, self._max_attempts + 1):
            try:
                with self._client.stream(
                    "GET", page_url, headers=headers, follow_redirects=True
                ) as response:
                    if response.status_code not in RETRY_STATUS:
                        if response.status_code >= 400:
                            return response.status_code, None, f"HTTP {response.status_code}"
                        try:
                            return (
                                response.status_code,
                                read_capped(response, self._max_bytes),
                                None,
                            )
                        except ResponseTooLargeError as exc:
                            return response.status_code, None, str(exc)

                    failure = f"HTTP {response.status_code}"
                    if attempt == self._max_attempts:
                        return response.status_code, None, failure
            except httpx.HTTPError as exc:
                failure = type(exc).__name__

            if attempt < self._max_attempts:
                self._sleep(float(2 ** (attempt - 1)))

        return None, None, failure

    def _wait_for_host(self, page_url: str, rate_limit_seconds: float) -> None:
        host = urlsplit(page_url).netloc
        last = self._last_request_at.get(host)
        now = time.monotonic()
        if last is not None:
            remaining = rate_limit_seconds - (now - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    def _failed(
        self, source: Source, page_url: str, *, reason: str | None, http_status: int | None = None
    ) -> FetchResult:
        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=None,
            status="failed",
            fetched_at=datetime.now(UTC),
            reason=reason,
            http_status=http_status,
        )
