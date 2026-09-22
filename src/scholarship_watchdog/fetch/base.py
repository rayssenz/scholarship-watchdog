"""The retrieval contract. See SPEC.md section 3.1.

Two implementations, selected per source in the registry, because the paid one
costs credits and must stay opt-in. The protocol also keeps the paid provider
swappable and keeps the core runnable with no Firecrawl key at all.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

import httpx

from ..models import Source

FetchStatus = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class FetchResult:
    """What one attempt at one page produced.

    `status` is about transport, not content quality:

      ok       the page was retrieved and cleaned; the markdown may still be an
               error page served with HTTP 200, which is the health check's
               problem rather than the fetcher's
      skipped  deliberately not attempted, e.g. a firecrawl source with no key
      failed   an exception, a timeout, or HTTP >= 400

    A `failed` page never advances its stored snapshot (SPEC.md section 4), so
    the change stays pending and the next weekly run retries it.
    """

    source_id: str
    page_url: str
    markdown: str | None
    status: FetchStatus
    fetched_at: datetime
    reason: str | None = None
    http_status: int | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


class Fetcher(Protocol):
    """Retrieve one page and return cleaned markdown."""

    name: ClassVar[str]

    def fetch(self, source: Source, page_url: str) -> FetchResult: ...


MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class ResponseTooLargeError(Exception):
    """A response body exceeded the byte budget and was abandoned part-read."""


def read_capped(response: httpx.Response, max_bytes: int) -> str:
    """Read a streaming response into text, abandoning it past `max_bytes`.

    SPEC 4 puts this on an unattended Actions runner with finite memory and no
    swap, so an unbounded read is an OOM kill: the job reports no diagnostics,
    writes no run report, and leaves the skip ledger unwritten, which is a
    silent failure rather than a loud one.

    `Content-Length` is not the enforcement. It is optional, absent under
    chunked transfer encoding, and set by the same server the budget exists to
    defend against, so the count is taken over the bytes actually read.

    Decoding is explicit rather than left to charset detection, because the
    decoded text is what gets hashed for change detection and a detector that
    guesses differently between two runs would report a change that is not one.
    """
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > max_bytes:
            raise ResponseTooLargeError(f"response too large (over {max_bytes} bytes)")
    encoding = response.charset_encoding or "utf-8"
    try:
        codecs.lookup(encoding)
    except LookupError:
        # A server declaring `charset=utf8mb4` or similar used to fail the page
        # on every run. httpx's own decoding falls back here too.
        encoding = "utf-8"
    return bytes(body).decode(encoding, errors="replace")
