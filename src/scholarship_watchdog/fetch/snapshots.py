"""Store one page's cleaned markdown and decide whether it moved.

See SPEC.md sections 3.1 and 3.6. Two rules do the work:

Snapshots are keyed by (source_id, page_url), so each page hashes and advances
independently. A run that fetches twenty of twenty-five pages before a budget
alarm halts must advance exactly those twenty and leave the rest pending.

An unchanged page terminates the pipeline for that page. This is the primary
cost control: the expensive stage is extraction, and a source whose content has
not moved is skipped before reaching it.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..models import Source
from ..paths import snapshot_path


def content_hash(markdown: str) -> str:
    """SHA-256 of the utf-8 bytes.

    Not Python's hash(), which is salted per process and would report every
    page changed on every run.
    """
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PageState:
    """What the last successful run left behind for one page."""

    previous_markdown: str | None
    previous_hash: str | None


@dataclass(frozen=True)
class Change:
    changed: bool
    current_hash: str
    previous_hash: str | None
    diff: str | None


def read_previous(source: Source, page_url: str, *, repo_root: Path | None = None) -> PageState:
    """Load the stored snapshot, or an empty state on a first run."""
    path = snapshot_path(source, page_url, repo_root=repo_root)
    if not path.exists():
        return PageState(previous_markdown=None, previous_hash=None)
    markdown = path.read_text(encoding="utf-8")
    return PageState(previous_markdown=markdown, previous_hash=content_hash(markdown))


def detect_change(previous: PageState, markdown: str) -> Change:
    """Compare hashes and, when they differ, keep a diff for the run report."""
    current = content_hash(markdown)
    if previous.previous_hash == current:
        return Change(
            changed=False, current_hash=current, previous_hash=previous.previous_hash, diff=None
        )

    diff = "\n".join(
        difflib.unified_diff(
            (previous.previous_markdown or "").splitlines(),
            markdown.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=2,
        )
    )
    return Change(
        changed=True, current_hash=current, previous_hash=previous.previous_hash, diff=diff
    )


def advance(source: Source, page_url: str, markdown: str, *, repo_root: Path | None = None) -> Path:
    """Store this page's snapshot. Called only after that page succeeded."""
    path = snapshot_path(source, page_url, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path
