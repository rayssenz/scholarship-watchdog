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

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import ClassVar

import httpx

from ..models import Source
from .base import MAX_RESPONSE_BYTES, FetchResult, ResponseTooLargeError, read_capped
from .canonical import canonicalise_markdown_links

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
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
        max_bytes: int = MAX_RESPONSE_BYTES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._max_attempts = max_attempts
        self._max_bytes = max_bytes
        self._sleep = sleep

    def fetch(self, source: Source, page_url: str) -> FetchResult:
        if not self._api_key:
            return self._result(source, page_url, None, "skipped", reason=NO_KEY)

        status, text, failure = self._post_with_retries(page_url)
        if status is None:
            return self._result(source, page_url, None, "failed", reason=failure)

        failed = partial(self._result, source, page_url, None, "failed", http_status=status)

        if status >= 400 or text is None:
            return failed(reason=failure)

        # A 200 is not a promise of JSON. A captive portal, a proxy interstitial
        # or a misrouted request answers 200 with HTML, and an unguarded
        # response.json() raises out of this method. Nothing between here and
        # the CLI catches it, so that one response would end the whole weekly
        # run before the skip ledger and the run report were written.
        try:
            payload = json.loads(text)
        except ValueError:
            return failed(reason="response body was not JSON")
        if not isinstance(payload, dict):
            return failed(reason="response body was not a JSON object")

        if not payload.get("success"):
            return failed(reason=str(payload.get("error", "firecrawl reported failure")))

        data = payload.get("data")
        markdown = (data.get("markdown") if isinstance(data, dict) else None) or ""
        markdown = markdown.strip()
        if not markdown:
            # Not ok-with-nothing: an empty snapshot becomes the baseline every
            # later run compares against, so the page reads as unchanged forever
            # and the content-collapse check can never fire again. SPEC 3.1.
            return failed(reason="firecrawl returned no markdown")

        if source.is_discover:
            markdown = canonicalise_markdown_links(markdown, base=page_url)

        return self._result(source, page_url, markdown, "ok", http_status=status)

    def _post_with_retries(self, page_url: str) -> tuple[int | None, str | None, str | None]:
        """Streamed for the same reason as the httpx fetcher.

        Firecrawl is a paid API rather than an arbitrary site, so a hostile body
        is less likely here, but "less likely" is not a memory bound on an
        unattended runner, and a proxy or captive portal in front of the API
        answers with whatever it likes.
        """
        body = {"url": page_url, "formats": ["markdown"], "onlyMainContent": True}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        failure: str | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                with self._client.stream(
                    "POST", FIRECRAWL_ENDPOINT, json=body, headers=headers
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
