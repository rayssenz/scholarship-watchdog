import textwrap
from pathlib import Path

import pytest

from scholarship_watchdog.config import (
    DuplicateSourceIdError,
    load_profile,
    load_sources,
)

PUBLIC = textwrap.dedent(
    """
    defaults:
      fetcher: httpx
      rate_limit_seconds: 2
      recurs: annual
    sources:
      - id: daad-study-scholarship
        name: DAAD Study Scholarship
        role: watch
        url: https://example.org/daad-detail
        country: DE
        institution: DAAD
      - id: csc-campuschina
        name: CampusChina
        role: discover
        url: https://example.org/campuschina
        country: CN
        fetcher: firecrawl
    """
)

LOCAL = textwrap.dedent(
    """
    sources:
      - id: fulbright-local-commission
        name: Fulbright, national commission
        role: watch
        url: https://example.org/commission
        country: XX
    """
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / name).write_text(body)
    return tmp_path


def test_defaults_are_applied_and_per_source_values_win(tmp_path):
    _write(tmp_path, "sources.yaml", PUBLIC)
    sources = {s.id: s for s in load_sources(tmp_path)}
    assert sources["daad-study-scholarship"].fetcher == "httpx"
    assert sources["daad-study-scholarship"].rate_limit_seconds == 2.0
    assert sources["csc-campuschina"].fetcher == "firecrawl"


def test_a_missing_local_file_is_not_an_error(tmp_path):
    """SPEC 5: without private config the system runs in report-only mode.

    This is what a stranger cloning the repository gets, and it must degrade
    rather than crash.
    """
    _write(tmp_path, "sources.yaml", PUBLIC)
    sources = load_sources(tmp_path)
    assert len(sources) == 2
    assert all(s.private is False for s in sources)


def test_local_sources_are_loaded_and_tagged_private(tmp_path):
    _write(tmp_path, "sources.yaml", PUBLIC)
    _write(tmp_path, "sources.local.yaml", LOCAL)
    sources = {s.id: s for s in load_sources(tmp_path)}
    assert sources["fulbright-local-commission"].private is True
    assert sources["daad-study-scholarship"].private is False


def test_promoted_sources_are_loaded_and_tagged_private(tmp_path):
    """watched.local.yaml is machine-owned; its entries are profile-derived."""
    _write(tmp_path, "sources.yaml", PUBLIC)
    _write(
        tmp_path,
        "watched.local.yaml",
        "sources:\n  - id: leaf-abc123\n    name: Promoted leaf\n"
        "    role: watch\n    url: https://example.org/leaf\n",
    )
    sources = {s.id: s for s in load_sources(tmp_path)}
    assert sources["leaf-abc123"].private is True


def test_a_duplicate_id_across_files_raises(tmp_path):
    """A local entry shadowing a public id would silently drop a watched page."""
    _write(tmp_path, "sources.yaml", PUBLIC)
    _write(
        tmp_path,
        "sources.local.yaml",
        "sources:\n  - id: daad-study-scholarship\n    name: Shadow\n"
        "    role: watch\n    url: https://example.org/shadow\n",
    )
    with pytest.raises(DuplicateSourceIdError, match="daad-study-scholarship"):
        load_sources(tmp_path)


def test_a_duplicate_id_within_one_file_raises(tmp_path):
    _write(
        tmp_path,
        "sources.yaml",
        PUBLIC + "\n  - id: daad-study-scholarship\n"
        "    name: Again\n    role: watch\n    url: https://example.org/again\n",
    )
    with pytest.raises(DuplicateSourceIdError):
        load_sources(tmp_path)


def test_an_unknown_source_key_raises(tmp_path):
    """A typo in a registry key must fail loudly, not be ignored."""
    _write(
        tmp_path,
        "sources.yaml",
        "sources:\n  - id: x\n    name: X\n    role: watch\n"
        "    url: https://example.org/x\n    fetchr: firecrawl\n",
    )
    with pytest.raises(Exception, match="fetchr"):
        load_sources(tmp_path)


def test_a_missing_profile_returns_none(tmp_path):
    """Report-only mode again: no profile means no scoring, not a crash."""
    assert load_profile(tmp_path) is None


def test_the_example_profile_loads(tmp_path):
    """The committed template must stay valid against the schema."""
    repo_config = Path(__file__).resolve().parent.parent / "config"
    body = (repo_config / "profile.example.yaml").read_text()
    (tmp_path / "profile.yaml").write_text(body)
    profile = load_profile(tmp_path)
    assert profile is not None
    assert profile.tiers.act_now == 70


def test_the_committed_public_registry_loads(tmp_path):
    """config/sources.yaml must stay loadable by the code that consumes it.

    Only the public file is copied into the temporary directory. The author is
    this project's production user and will have a real sources.local.yaml in
    their checkout, so reading config/ directly would make this test fail on
    the maintainer's machine every run, and a test its owner learns to ignore
    is a deleted test.
    """
    repo_config = Path(__file__).resolve().parent.parent / "config"
    (tmp_path / "sources.yaml").write_text((repo_config / "sources.yaml").read_text())

    sources = load_sources(tmp_path)
    assert len(sources) == 13
    assert sum(1 for s in sources if s.is_watch) == 4
    assert all(s.private is False for s in sources)
