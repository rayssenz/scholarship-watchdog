"""Where a run is allowed to write. See SPEC.md section 5.

This module exists because the destination decision is the privacy boundary,
and section 5 records that the same leak was rediscovered four times in four
different places. Spread across callers, the rule is a convention that each new
output path may forget. Here it is one function with one test suite.

The rule: anything shaped by the eligibility profile is private, including
every artifact produced by acting on it. A public source's snapshot is public
because the page exists independently of the user. A private source's snapshot
is private, and so is its path, because a directory named for the user's
embassy names their country.

Until P3 builds the encrypted bundle, private artifacts go to `.private/`,
which is gitignored. P3 moves that directory into `data/private.age`; the
callers do not change, because they only ever ask this module.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .models import Source

PUBLIC_ROOT = Path("data")
PRIVATE_ROOT = Path(".private")

_SLUG_BODY = 24
_UNSAFE = re.compile(r"[^a-z0-9]+")


def _repo_root(repo_root: Path | None) -> Path:
    return Path.cwd() if repo_root is None else repo_root


def page_slug(page_url: str) -> str:
    """A readable, stable, collision-resistant filename for one page.

    Readable so a human can find a snapshot; stable so the same URL always maps
    to the same file across runs; hashed suffix because the DAAD watch sources
    differ only in a query parameter, and a slug that dropped the query would
    collapse two watched programmes into one file and stop guarding one of them.
    """
    parts = urlsplit(page_url)
    body = _UNSAFE.sub("-", f"{parts.path}".lower()).strip("-") or "index"
    body = body[:_SLUG_BODY].strip("-")
    digest = hashlib.sha256(page_url.encode()).hexdigest()[:10]
    return f"{body}-{digest}"


def snapshot_root(source: Source, *, repo_root: Path | None = None) -> Path:
    """The directory this source's snapshots belong in, public or private."""
    base = _repo_root(repo_root)
    if source.private:
        return base / PRIVATE_ROOT / "snapshots" / source.id
    return base / PUBLIC_ROOT / "snapshots" / source.id


def snapshot_path(source: Source, page_url: str, *, repo_root: Path | None = None) -> Path:
    """The markdown snapshot for one (source_id, page_url) pair."""
    return snapshot_root(source, repo_root=repo_root) / f"{page_slug(page_url)}.md"


def run_report_path(timestamp: datetime, *, repo_root: Path | None = None) -> Path:
    """Run reports are public and cover public sources only (section 5)."""
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return _repo_root(repo_root) / PUBLIC_ROOT / "runs" / f"{stamp}.json"


def health_path(*, repo_root: Path | None = None) -> Path:
    """Public skip counters. Private counters wait for the P3 bundle."""
    return _repo_root(repo_root) / PUBLIC_ROOT / "health.json"
