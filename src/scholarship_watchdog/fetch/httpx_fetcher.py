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
from urllib.parse import urljoin, urlsplit

import httpx

from ..models import Source
from .base import MAX_RESPONSE_BYTES, FetchResult, ResponseTooLargeError, read_capped
from .canonical import canonicalise_markdown_links
from .clean import clean

USER_AGENT = "scholarship-watchdog/0.1 (+https://github.com/rayssenz/scholarship-watchdog)"
TIMEOUT = 25.0
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_REDIRECTS = 5


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
        self._client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
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
        status, text, failure, final_url = self._get_with_retries(page_url)

        if status is None:
            return self._failed(source, page_url, reason=failure)

        if status >= 400 or text is None:
            return self._failed(source, page_url, reason=failure, http_status=status)

        # Relative links resolve against where the page ended up, not where the
        # registry pointed: `/start` redirecting to `/new/portal/` must turn
        # `award` into `/new/portal/award`, or discovery hands P4 a candidate
        # link to the wrong page. The snapshot stays keyed on the registered URL.
        markdown = clean(text, keep_links=source.is_discover)
        if source.is_discover and markdown:
            markdown = canonicalise_markdown_links(markdown, base=final_url)

        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=markdown,
            status="ok",
            fetched_at=datetime.now(UTC),
            http_status=status,
        )

    def _get_with_retries(self, page_url: str) -> tuple[int | None, str | None, str | None, str]:
        """Retry only what a retry can fix: transient status codes and timeouts.

        A 404 will not become a 200 on the third attempt, so retrying it only
        spends the run's time budget and hammers someone else's server.

        Returns (status, text, failure, final_url). Text is None on any failure.
        """
        failure: str | None = None
        final_url = page_url

        for attempt in range(1, self._max_attempts + 1):
            try:
                status, text, failure, final_url = self._get_following_redirects(page_url)
            except httpx.HTTPError as exc:
                status, text, failure = None, None, type(exc).__name__
            else:
                if status not in RETRY_STATUS or attempt == self._max_attempts:
                    return status, text, failure, final_url

            if attempt < self._max_attempts:
                self._sleep(float(2 ** (attempt - 1)))

        return None, None, failure, final_url

    def _get_following_redirects(self, page_url: str) -> tuple[int, str | None, str | None, str]:
        """One attempt: follow redirects by hand, reading only the final body.

        Redirects are not left to httpx, because httpx reads each intermediate
        response's body in full before following it, and the byte budget cannot
        see those reads. A 302 streaming gigabytes was drained completely and
        the fetch still returned ok. Here each redirect is closed unread.

        Every body is streamed rather than fetched whole, because the budget has
        to be enforced while reading: a buffering get() has already consumed the
        response by the time any check could run.
        """
        url = page_url
        headers = {"User-Agent": USER_AGENT}
        for _ in range(MAX_REDIRECTS + 1):
            with self._client.stream("GET", url, headers=headers, follow_redirects=False) as resp:
                # is_redirect is true for every 3xx; only a usable Location is
                # followed. A 300 page listing alternatives is read as a page.
                if resp.has_redirect_location:
                    status = resp.status_code
                    url = urljoin(str(resp.url), resp.headers["location"])
                    if urlsplit(url).scheme not in ("http", "https"):
                        return status, None, "redirect to a non-http URL", url
                    continue
                if resp.status_code >= 400:
                    return resp.status_code, None, f"HTTP {resp.status_code}", url
                try:
                    return resp.status_code, read_capped(resp, self._max_bytes), None, url
                except ResponseTooLargeError as exc:
                    return resp.status_code, None, str(exc), url
        return status, None, f"more than {MAX_REDIRECTS} redirects", url

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
