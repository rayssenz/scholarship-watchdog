"""Catch a source that broke without saying so. See SPEC.md section 3.1.

These checks run *before* the change-detection skip gate, and the ordering is
the point rather than a detail. A page they call broken is never stored, but a
snapshot can already be broken before anything flagged it: written before a new
signature existed, or before that rule did. Such a page is unchanged every
week, and any alarm placed after the gate would be unreachable for it forever.
Checking the fetch result rather than the extraction count is what makes
breakage detectable at all.

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
  skipped_twice          a source skipped or failed on two consecutive runs

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
    # Bot walls, which are the common case for a university portal and are all
    # served with HTTP 200. Added after the P1 acceptance run measured an
    # Imperva block getting through every pattern above it: see
    # docs/p1-acceptance.md. Each is matched on a phrase the vendor's own
    # interstitial prints, not on a generic word, because a false positive here
    # freezes the page's snapshot until the pattern is corrected.
    re.compile(r"incapsula incident", re.I),
    re.compile(r"request unsuccessful", re.I),
    re.compile(r"attention required.{0,3}\| cloudflare", re.I),
    re.compile(r"checking your browser before accessing", re.I),
    re.compile(r"verify you are a human", re.I),
    re.compile(r"pardon our interruption", re.I),
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
    """Consecutive-unsuccessful-run counters, public sources only.

    SPEC.md section 3.1 files two causes under one check: a source skipped for
    want of a Firecrawl key, and a source whose fetch keeps failing. Both count
    here, and only a page that actually arrived resets the counter. Counting
    skips alone left the second cause unfireable: a watch page that moved (404)
    or a host that went away failed every week in silence, because a failure
    reset the counter and `check_page` raises nothing for a page that did not
    arrive.

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
        """Load the counters, or start empty if the file is missing or malformed.

        A counter is a convenience; losing it costs one late alert. Aborting a
        deadline watch over it would cost a missed scholarship, so anything that
        is not a mapping of ids to non-negative integers is discarded rather
        than trusted, including well-formed JSON of the wrong shape.
        """
        path = health_path(repo_root=repo_root)
        if not path.exists():
            return cls(path, {})
        try:
            counts = json.loads(path.read_text()).get("consecutive_skips")
        except (json.JSONDecodeError, AttributeError):
            return cls(path, {})
        valid = isinstance(counts, dict) and all(
            isinstance(k, str) and type(v) is int and v >= 0 for k, v in counts.items()
        )
        return cls(path, counts if valid else {})

    def record(self, source: Source, result: FetchResult) -> Alert | None:
        """Count this outcome; alert on the second unsuccessful run running."""
        if source.private:
            return None

        if result.status not in ("skipped", "failed"):
            self._counts.pop(source.id, None)
            return None

        self._counts[source.id] = self._counts.get(source.id, 0) + 1
        if self._counts[source.id] < 2:
            return None
        return Alert(
            source.id,
            "skipped_twice",
            f"not fetched {self._counts[source.id]} runs running: {result.reason}",
        )

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"consecutive_skips": self._counts}, indent=2, sort_keys=True) + "\n"
        )
