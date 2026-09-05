"""Render JavaScript-heavy sources in a real browser. See SPEC.md section 3.1.

CampusChina and several university portals return an empty application shell to
a plain HTTP client, so httpx retrieves markup with no content in it and
trafilatura has nothing to extract. Firecrawl renders the page and returns
finished markdown, so there is no cleaning step here.

It costs credits, so it is opt-in per source rather than the default, and a
source configured for it with no key present is skipped with a warning rather
than failing the run. That keeps the repository runnable for someone who clones
it with no paid account, which SPEC.md section 5 requires.

The HTTP API is called directly rather than through the vendor SDK: one fewer
dependency, and httpx.MockTransport makes the whole fetcher testable with no
network, which the CI constraint requires.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

import httpx

from ..models import Source
from .base import FetchResult
from .canonical import canonicalise_markdown_links

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
TIMEOUT = 90.0
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
NO_KEY = "no Firecrawl API key configured; set FIRECRAWL_API_KEY"


class FirecrawlFetcher:
    """Retrieve a client-rendered page through Firecrawl."""

    name: ClassVar[str] = "firecrawl"

    def __init__(
        self,
        api_key: str | None,
        client: httpx.Client | None = None,
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._max_attempts = max_attempts
        self._sleep = sleep

    def fetch(self, source: Source, page_url: str) -> FetchResult:
        if not self._api_key:
            return self._result(source, page_url, None, "skipped", reason=NO_KEY)

        response, failure = self._post_with_retries(page_url)
        if response is None:
            return self._result(source, page_url, None, "failed", reason=failure)

        if response.status_code >= 400:
            return self._result(
                source,
                page_url,
                None,
                "failed",
                reason=f"HTTP {response.status_code}",
                http_status=response.status_code,
            )

        payload = response.json()
        if not payload.get("success"):
            return self._result(
                source,
                page_url,
                None,
                "failed",
                reason=str(payload.get("error", "firecrawl reported failure")),
                http_status=response.status_code,
            )

        markdown = (payload.get("data") or {}).get("markdown") or ""
        if source.is_discover and markdown:
            markdown = canonicalise_markdown_links(markdown, base=page_url)

        return self._result(
            source, page_url, markdown.strip(), "ok", http_status=response.status_code
        )

    def _post_with_retries(self, page_url: str) -> tuple[httpx.Response | None, str | None]:
        body = {"url": page_url, "formats": ["markdown"], "onlyMainContent": True}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        failure: str | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(FIRECRAWL_ENDPOINT, json=body, headers=headers)
            except httpx.HTTPError as exc:
                failure = type(exc).__name__
            else:
                if response.status_code not in RETRY_STATUS:
                    return response, None
                failure = f"HTTP {response.status_code}"
                if attempt == self._max_attempts:
                    return response, failure

            if attempt < self._max_attempts:
                self._sleep(float(2 ** (attempt - 1)))

        return None, failure

    def _result(
        self,
        source: Source,
        page_url: str,
        markdown: str | None,
        status: str,
        *,
        reason: str | None = None,
        http_status: int | None = None,
    ) -> FetchResult:
        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=markdown,
            status=status,  # type: ignore[arg-type]
            fetched_at=datetime.now(UTC),
            reason=reason,
            http_status=http_status,
        )
