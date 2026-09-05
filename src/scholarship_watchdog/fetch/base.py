"""The retrieval contract. See SPEC.md section 3.1.

Two implementations, selected per source in the registry, because the paid one
costs credits and must stay opt-in. The protocol also keeps the paid provider
swappable and keeps the core runnable with no Firecrawl key at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

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
