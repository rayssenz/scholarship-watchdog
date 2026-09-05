"""Catch a source that broke without saying so. See SPEC.md section 3.1.

These checks run *before* the change-detection skip gate, and the ordering is
the point rather than a detail. A broken source is by construction unchanged
after its first broken fetch: the error page becomes the stored snapshot, the
next run sees no change, and any alarm placed after the gate is unreachable
forever. Checking the fetch result rather than the extraction count is what
makes breakage detectable at all.

Four checks, three of them on the freshly fetched page and one across runs:

  error_signature        an error page served as content, or nothing at all:
                         a JavaScript-only shell with no fallback text cleans
                         to an empty extraction, which is what the registry
                         recorded for NUS; a shell whose only text is a
                         noscript notice keeps it through cleaning (measured,
                         trafilatura 1.12.2) and matches the "enable
                         JavaScript" pattern instead
  content_collapse       cleaned markdown under 40% of the previous snapshot
  watch_without_deadline a watch source that stopped carrying a date
  skipped_twice          a source skipped on two consecutive runs

Staleness is deliberately not a check. Scholarship pages legitimately go six to
twelve months unchanged, so any cadence would either never fire or cry wolf,
and there is no data to tune thirteen of them against. It is reported in the
run report instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..deadlines import has_deadline
from ..models import Source
from ..paths import health_path
from .base import FetchResult
from .snapshots import PageState

CheckName = Literal[
    "content_collapse", "error_signature", "skipped_twice", "watch_without_deadline"
]

COLLAPSE_RATIO = 0.4

ERROR_SIGNATURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"enable javascript", re.I),
    re.compile(r"access denied", re.I),
    re.compile(r"\b403 forbidden\b", re.I),
    re.compile(r"\b404 not found\b", re.I),
    re.compile(r"\b(500|502|503) (internal server error|bad gateway|service unavailable)\b", re.I),
    re.compile(r"you do not have permission", re.I),
    re.compile(r"request blocked", re.I),
)


@dataclass(frozen=True)
class Alert:
    source_id: str
    check: CheckName
    detail: str


def check_page(source: Source, result: FetchResult, previous: PageState) -> list[Alert]:
    """Every content alert this page raises, read off the fetch result.

    One cause, one alert. A page that did not arrive raises nothing here, and a
    page that arrived broken raises the alert naming why it is broken and
    stops. An error page has no deadline and is also a content collapse, so
    reporting all three would bury the one fact that matters: the page is not
    the page any more.
    """
    if not result.is_ok or result.markdown is None:
        return []

    markdown = result.markdown

    if not markdown.strip():
        return [
            Alert(
                source.id,
                "error_signature",
                "empty content after cleaning: the page returned no readable "
                "text, which is what a JavaScript-only shell looks like",
            )
        ]

    for pattern in ERROR_SIGNATURES:
        if pattern.search(markdown):
            return [
                Alert(source.id, "error_signature", f"fetched text matches {pattern.pattern!r}")
            ]

    alerts: list[Alert] = []

    previous_length = len(previous.previous_markdown or "")
    if previous_length and len(markdown) < previous_length * COLLAPSE_RATIO:
        alerts.append(
            Alert(
                source.id,
                "content_collapse",
                f"{len(markdown)} chars against {previous_length} previously",
            )
        )

    if source.is_watch and not has_deadline(markdown):
        alerts.append(
            Alert(
                source.id,
                "watch_without_deadline",
                "a watch source must carry a date and a deadline keyword",
            )
        )

    return alerts


class SkipLedger:
    """Consecutive-skip counters, public sources only.

    Cross-run state, so it needs somewhere to live. Until P3 builds the
    encrypted bundle, public counters go to `data/health.json`; a private
    source is never recorded, because SPEC.md section 5 keeps even aggregate
    counts about private sources out of the committed tree. Private counters
    join the bundle in P3.
    """

    def __init__(self, path: Path, counts: dict[str, int]) -> None:
        self._path = path
        self._counts = counts

    @classmethod
    def load(cls, repo_root: Path | None = None) -> SkipLedger:
        path = health_path(repo_root=repo_root)
        counts: dict[str, int] = {}
        if path.exists():
            try:
                counts = json.loads(path.read_text()).get("consecutive_skips", {})
            except (json.JSONDecodeError, AttributeError):
                counts = {}
        return cls(path, counts)

    def record(self, source: Source, result: FetchResult) -> Alert | None:
        """Count this outcome and return an alert on the second skip running."""
        if source.private:
            return None

        if result.status != "skipped":
            self._counts.pop(source.id, None)
            return None

        self._counts[source.id] = self._counts.get(source.id, 0) + 1
        if self._counts[source.id] < 2:
            return None
        return Alert(
            source.id,
            "skipped_twice",
            f"skipped {self._counts[source.id]} runs running: {result.reason}",
        )

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"consecutive_skips": self._counts}, indent=2, sort_keys=True) + "\n"
        )
