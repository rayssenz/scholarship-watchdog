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
from pydantic import ValidationError

from .models import Profile, Source, safe_errors

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


class ConfigError(ValueError):
    """A config file could not be read. Carries no content from the file.

    The underlying libraries are not safe to let escape. PyYAML's
    MarkedYAMLError embeds the offending line verbatim in its message, and a
    pydantic ValidationError names the field that failed. Over a gitignored
    file, either one publishes a piece of the user's private registry into what
    SPEC.md section 5 says becomes a public Actions log in P3.

    Every raise site chains with `from None` rather than `from exc`, so even an
    uncaught ConfigError prints nothing from the file. That is deliberate
    belt-and-braces: the CLI catches this, and the suppressed chain is what
    protects a future caller that forgets to.
    """


def _safe_detail(exc: ValidationError, *, private: bool) -> str:
    """What may be said about a rejected entry, given which file it came from.

    `safe_errors` strips the rejected value, which is the fix Ruling R11 added
    after `hide_input_in_errors` turned out to cover only the string form. It
    does not strip `loc`, and over a private file a field name is itself a fact
    about the private registry, so the private branch reports a count alone.
    """
    errors = safe_errors(exc)
    if private:
        return f"{len(errors)} invalid field(s)"
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        raise ConfigError(f"{path.name} could not be parsed ({type(exc).__name__})") from None


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
            try:
                source = Source(**merged)
            except ValidationError as exc:
                raise ConfigError(
                    f"{filename} has an invalid entry: {_safe_detail(exc, private=private)}"
                ) from None
            if source.id in seen:
                # An id seen first in the public file is public and may be named.
                # One that appears only in private files is itself private.
                first_file = seen[source.id]
                names_it = first_file == PUBLIC_SOURCES
                subject = f"source id {source.id!r}" if names_it else "a source id"
                raise DuplicateSourceIdError(
                    f"{subject} appears in both {first_file} and {filename}"
                )
            seen[source.id] = filename
            sources.append(source)

    return sources


def load_profile(config_dir: Path) -> Profile | None:
    """Load the private profile, or None when it is absent (report-only mode).

    profile.yaml is the most sensitive file in the project, so it gets the same
    treatment as the private registry files and for the same reason: a raw
    parse error or a raw ValidationError over this file would put a citizenship
    code or a residency rule into a traceback.
    """
    path = config_dir / PROFILE
    if not path.exists():
        return None
    try:
        return Profile(**_read(path))
    except ValidationError as exc:
        raise ConfigError(
            f"{PROFILE} has an invalid entry: {_safe_detail(exc, private=True)}"
        ) from None
    except TypeError:
        raise ConfigError(f"{PROFILE} is not a mapping") from None
