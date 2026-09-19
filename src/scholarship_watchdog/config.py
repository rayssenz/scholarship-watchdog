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
    about the private registry. So is the number of rejected fields, which is
    why the private branch says nothing beyond "invalid".
    """
    errors = safe_errors(exc)
    if private:
        return "invalid"
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)


def _read(path: Path) -> dict[str, Any]:
    """Parse one YAML file into a mapping, or raise a ConfigError that quotes
    nothing from it.

    Every exception is caught, not a list of expected ones. The first version
    caught YAMLError, UnicodeDecodeError and OSError, and a `!!int` tag over a
    non-number still escaped: the conversion runs inside safe_load and raises
    ValueError, whose message is the raw value. A list of exceptions is the same
    mistake as a list of leaks.
    """
    if not path.exists():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - every type is caught on purpose, see docstring
        raise ConfigError(f"{path.name} could not be parsed ({type(exc).__name__})") from None
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return document


_REGISTRY_KEYS = frozenset({"defaults", "sources"})


def _registry_shape(document: dict[str, Any], filename: str, *, private: bool) -> None:
    """Reject a registry file whose envelope is wrong, before any entry is read.

    Only entries used to be validated. `source:` for `sources:` therefore loaded
    zero sources with no error and produced a successful, empty weekly run,
    which is every watch disabled in silence. An unknown key in a private file is
    not named, because the user typed it and it may say anything.
    """
    unknown = set(document) - _REGISTRY_KEYS
    if unknown:
        named = "" if private else f": {', '.join(sorted(unknown))}"
        raise ConfigError(f"{filename} has an unknown top-level key{named}")
    # Types are checked on the values as written, before any default applies.
    # `document.get("sources") or []` used to turn `sources: false` or
    # `sources: {}` into an empty list first, so both loaded zero sources.
    defaults = document.get("defaults")
    if defaults is not None and not _string_keyed(defaults):
        raise ConfigError(f"{filename}: defaults must be a mapping with text keys")
    entries = document.get("sources")
    if entries is not None and not (
        isinstance(entries, list) and all(_string_keyed(e) for e in entries)
    ):
        raise ConfigError(f"{filename}: sources must be a list of mappings with text keys")


def _string_keyed(value: object) -> bool:
    """A mapping whose keys can be keyword arguments. A YAML key such as `123`
    loads as an int and made Source(**entry) raise TypeError past every handler."""
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def load_sources(config_dir: Path) -> list[Source]:
    """Merge the three registry files into one list, tagging privacy."""
    sources: list[Source] = []
    seen: dict[str, str] = {}

    for filename in (PUBLIC_SOURCES, *_PRIVATE_FILES):
        document = _read(config_dir / filename)
        private = filename in _PRIVATE_FILES
        _registry_shape(document, filename, private=private)
        defaults = document.get("defaults") or {}

        for entry in document.get("sources") or []:
            merged = {**defaults, **entry, "private": private}
            try:
                source = Source(**merged)
            except ValidationError as exc:
                raise ConfigError(
                    f"{filename} has an invalid entry: {_safe_detail(exc, private=private)}"
                ) from None
            if source.id in seen:
                # Named only when nothing private is involved. A public id next
                # to a private file says "this public programme is in the user's
                # private registry", which from P4 means it was promoted, which
                # means it cleared the fitness gate: SPEC 5's third leak, rebuilt
                # out of an error message. Naming the private file alone still
                # says which private list grew.
                if private:
                    raise DuplicateSourceIdError(
                        "a source id is duplicated across the registry files; check the local files"
                    )
                raise DuplicateSourceIdError(f"source id {source.id!r} appears twice in {filename}")
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
