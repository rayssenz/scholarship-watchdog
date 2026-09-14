import subprocess
from pathlib import Path

import pytest

from scholarship_watchdog.config import load_sources
from scholarship_watchdog.models import Source
from scholarship_watchdog.paths import (
    PRIVATE_ROOT,
    PUBLIC_ROOT,
    page_slug,
    snapshot_path,
)

REPO = Path(__file__).resolve().parent.parent


def _git_ignores(path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=REPO,
            check=False,
        ).returncode
        == 0
    )


PUBLIC_SOURCE = Source(
    id="daad-study-scholarship", name="DAAD", role="watch", url="https://example.org/d"
)
PRIVATE_SOURCE = Source(
    id="fulbright-local-commission",
    name="Fulbright commission",
    role="watch",
    url="https://example.org/c",
    private=True,
)


def test_a_public_snapshot_goes_under_data():
    path = snapshot_path(PUBLIC_SOURCE, PUBLIC_SOURCE.url, repo_root=REPO)
    assert path.is_relative_to(REPO / PUBLIC_ROOT / "snapshots")


def test_a_private_snapshot_goes_under_the_private_root():
    path = snapshot_path(PRIVATE_SOURCE, PRIVATE_SOURCE.url, repo_root=REPO)
    assert path.is_relative_to(REPO / PRIVATE_ROOT)
    assert not path.is_relative_to(REPO / PUBLIC_ROOT)


def test_git_refuses_to_track_a_private_snapshot_path():
    """SPEC 5's testable invariant, checked against git rather than a prefix.

    A path can satisfy every naming rule this module enforces and still be
    committable if .gitignore regresses. This asserts on the thing that
    actually protects the profile.
    """
    path = snapshot_path(PRIVATE_SOURCE, PRIVATE_SOURCE.url, repo_root=REPO)
    assert _git_ignores(path), f"{path} is committable"


def test_git_also_refuses_to_track_public_snapshot_paths_on_main():
    """main is code-only; data/ lives on the orphan data branch.

    Not a privacy rule, a branch-hygiene rule from docs/git-conventions.md, and
    it fails the same way if the ignore file regresses.
    """
    path = snapshot_path(PUBLIC_SOURCE, PUBLIC_SOURCE.url, repo_root=REPO)
    assert _git_ignores(path), f"{path} is committable"


def test_the_private_source_id_never_appears_in_a_public_path():
    """SPEC 5: the path itself is the leak.

    snapshots/fulbright-xx-commission/ publishes the country in its name, so no
    public path may carry a private source's id or a slug derived from its URL.
    The id does key the directory, privately, which is what the second
    assertion pins down.
    """
    private = snapshot_path(PRIVATE_SOURCE, PRIVATE_SOURCE.url, repo_root=REPO)
    assert not str(private).startswith(str(REPO / PUBLIC_ROOT))
    assert PRIVATE_SOURCE.id in str(private), "the id keys the directory, privately"


@pytest.mark.parametrize(
    ("url", "expected_prefix"),
    [
        ("https://example.org/a/b/detail?id=50026200", "a-b-detail"),
        ("https://example.org/", "index"),
        ("https://example.org/very/deep/path/that/keeps/going/on/and/on", "very-deep"),
    ],
)
def test_a_slug_is_readable_stable_and_bounded(url, expected_prefix):
    slug = page_slug(url)
    assert slug.startswith(expected_prefix)
    assert len(slug) <= 60
    assert slug == page_slug(url), "slugs must be deterministic"
    assert all(c.isalnum() or c in "-" for c in slug)


def test_two_urls_differing_only_in_query_get_different_slugs():
    """SPEC 3.1 keys snapshots by (source_id, page_url).

    The DAAD watch sources differ only by ?detail=, so a slug that dropped the
    query would collapse two watched programmes into one snapshot and silently
    stop guarding one of them.
    """
    a = page_slug("https://example.org/db/?detail=50026200")
    b = page_slug("https://example.org/db/?detail=57742130")
    assert a != b


def test_every_committed_registry_source_lands_in_an_ignored_path():
    """Sweep the real public registry rather than one hand-built source."""
    for source in load_sources(REPO / "config"):
        path = snapshot_path(source, source.url, repo_root=REPO)
        assert _git_ignores(path), f"{source.id} -> {path} is committable"
