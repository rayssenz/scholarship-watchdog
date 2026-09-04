"""Load the registry and the profile from YAML. See SPEC.md section 5.

Three source files, and which file an entry came from decides whether its
artifacts are public:

  sources.yaml          public: what a stranger clones and runs
  sources.local.yaml    private, human-owned: the local copy is authoritative
  watched.local.yaml    private, machine-owned: written by promotion in P4

Both local files are gitignored, so both may be absent. Absent is the normal
case for a fresh clone and is never an error: SPEC.md section 5 requires the
system to degrade to report-only mode rather than crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Profile, Source

PUBLIC_SOURCES = "sources.yaml"
LOCAL_SOURCES = "sources.local.yaml"
PROMOTED_SOURCES = "watched.local.yaml"
PROFILE = "profile.yaml"

_PRIVATE_FILES = (LOCAL_SOURCES, PROMOTED_SOURCES)


class DuplicateSourceIdError(ValueError):
    """Two registry entries claim the same id.

    Raised rather than resolved by precedence: a local entry shadowing a public
    one would drop a watched deadline page with no error anywhere, which is the
    class of silent failure this project keeps finding.
    """


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_sources(config_dir: Path) -> list[Source]:
    """Merge the three registry files into one list, tagging privacy."""
    sources: list[Source] = []
    seen: dict[str, str] = {}

    for filename in (PUBLIC_SOURCES, *_PRIVATE_FILES):
        document = _read(config_dir / filename)
        defaults = document.get("defaults") or {}
        private = filename in _PRIVATE_FILES

        for entry in document.get("sources") or []:
            merged = {**defaults, **entry, "private": private}
            source = Source(**merged)
            if source.id in seen:
                raise DuplicateSourceIdError(
                    f"source id {source.id!r} appears in both {seen[source.id]} and {filename}"
                )
            seen[source.id] = filename
            sources.append(source)

    return sources


def load_profile(config_dir: Path) -> Profile | None:
    """Load the private profile, or None when it is absent (report-only mode)."""
    path = config_dir / PROFILE
    if not path.exists():
        return None
    return Profile(**yaml.safe_load(path.read_text()))
