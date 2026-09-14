import textwrap
from pathlib import Path

import pytest

from scholarship_watchdog.config import (
    ConfigError,
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


def test_a_malformed_private_file_raises_without_quoting_its_contents(tmp_path):
    """SPEC 5: a traceback is a public Actions log from P3.

    PyYAML's MarkedYAMLError embeds the offending line verbatim, so letting it
    escape publishes a line of the user's private registry. The id, the URL and
    the quoting that broke the parse must all be absent from what is raised.
    """
    _write(tmp_path, "sources.yaml", PUBLIC)
    _write(
        tmp_path,
        "sources.local.yaml",
        "sources:\n  - id: ambassade-de-tunisie-berlin\n    name: Embassy\n"
        '    url: "https://tn-embassy.example/bourses\n    role: watch\n',
    )
    with pytest.raises(ConfigError) as caught:
        load_sources(tmp_path)

    rendered = f"{caught.value}{caught.value.__cause__ or ''}"
    assert "tn-embassy" not in rendered
    assert "ambassade-de-tunisie-berlin" not in rendered
    assert "sources.local.yaml" in rendered, "naming the file is safe and useful"


def test_a_duplicate_id_between_two_private_files_withholds_the_id(tmp_path):
    """An id that appears only in private files is itself private.

    The public-first case still reports the id, because a public id is public.
    """
    _write(tmp_path, "sources.yaml", PUBLIC)
    private_entry = (
        "sources:\n  - id: fulbright-xx-commission\n    name: Commission\n"
        "    role: watch\n    url: https://example.org/c\n"
    )
    _write(tmp_path, "sources.local.yaml", private_entry)
    _write(tmp_path, "watched.local.yaml", private_entry)

    with pytest.raises(DuplicateSourceIdError) as caught:
        load_sources(tmp_path)

    assert "fulbright-xx-commission" not in str(caught.value)
    assert "sources.local.yaml" in str(caught.value)
    assert "watched.local.yaml" in str(caught.value)


def test_an_invalid_private_entry_reports_no_field_names_or_values(tmp_path):
    """safe_errors strips the rejected value; loc still names the field.

    A typo'd key in a private file names something about the private registry,
    so the private branch reports a count and nothing else.
    """
    _write(tmp_path, "sources.yaml", PUBLIC)
    _write(
        tmp_path,
        "sources.local.yaml",
        "sources:\n  - id: c\n    name: C\n    role: watch\n"
        "    url: https://example.org/c\n    citizenship_hint: TN\n",
    )
    with pytest.raises(ConfigError) as caught:
        load_sources(tmp_path)

    rendered = f"{caught.value}{caught.value.__cause__ or ''}"
    assert "citizenship_hint" not in rendered
    assert "TN" not in rendered


def test_an_invalid_public_entry_still_reports_which_field_failed(tmp_path):
    """The public registry is public, so its detail stays debuggable."""
    _write(
        tmp_path,
        "sources.yaml",
        "sources:\n  - id: x\n    name: X\n    role: watch\n"
        "    url: https://example.org/x\n    fetchr: firecrawl\n",
    )
    with pytest.raises(ConfigError, match="fetchr"):
        load_sources(tmp_path)


def test_an_invalid_profile_reports_no_field_names_or_values(tmp_path):
    """profile.yaml is the most sensitive file in the project.

    A citizenship code reaching a traceback is the one unrecoverable mistake
    SPEC 5 names, so neither the rejected value nor the field name escapes.
    """
    _write(tmp_path, "profile.yaml", "citizenship: TN\nresidency: DE\nbogus_field: yes\n")
    with pytest.raises(ConfigError) as caught:
        load_profile(tmp_path)

    rendered = f"{caught.value}{caught.value.__cause__ or ''}"
    assert "TN" not in rendered
    assert "bogus_field" not in rendered


def test_a_malformed_profile_does_not_quote_its_contents(tmp_path):
    _write(tmp_path, "profile.yaml", 'citizenship: "TN\nresidency: DE\n')
    with pytest.raises(ConfigError) as caught:
        load_profile(tmp_path)

    rendered = f"{caught.value}{caught.value.__cause__ or ''}"
    assert "TN" not in rendered
    assert "citizenship" not in rendered
