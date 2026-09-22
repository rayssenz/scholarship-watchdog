from scholarship_watchdog.fetch.snapshots import (
    advance,
    content_hash,
    detect_change,
    read_previous,
)
from scholarship_watchdog.models import Source

WATCH = Source(id="daad-study", name="DAAD", role="watch", url="https://example.org/d")
PRIVATE = Source(
    id="local-commission",
    name="Commission",
    role="watch",
    url="https://example.org/c",
    private=True,
)


def test_the_first_run_has_no_previous_snapshot(tmp_path):
    state = read_previous(WATCH, WATCH.url, repo_root=tmp_path)
    assert state.previous_markdown is None
    assert state.previous_hash is None


def test_a_first_fetch_is_a_change(tmp_path):
    state = read_previous(WATCH, WATCH.url, repo_root=tmp_path)
    change = detect_change(state, "Deadline: 1 October 2027")
    assert change.changed is True
    assert change.previous_hash is None


def test_a_second_run_over_identical_content_reports_unchanged(tmp_path):
    """P1 acceptance bullet two, and the primary cost control in SPEC 3.1."""
    markdown = "Deadline: 1 October 2027"
    advance(WATCH, WATCH.url, markdown, repo_root=tmp_path)
    change = detect_change(read_previous(WATCH, WATCH.url, repo_root=tmp_path), markdown)
    assert change.changed is False
    assert change.diff is None


def test_a_changed_page_reports_a_unified_diff(tmp_path):
    """SPEC 3.1: when hashes differ, a unified diff is retained for the run
    report."""
    advance(WATCH, WATCH.url, "Deadline: 1 October 2027", repo_root=tmp_path)
    change = detect_change(
        read_previous(WATCH, WATCH.url, repo_root=tmp_path), "Deadline: 15 November 2027"
    )
    assert change.changed is True
    assert "-Deadline: 1 October 2027" in change.diff
    assert "+Deadline: 15 November 2027" in change.diff


def test_advance_writes_under_the_public_root_for_a_public_source(tmp_path):
    path = advance(WATCH, WATCH.url, "text", repo_root=tmp_path)
    assert path.is_relative_to(tmp_path / "data" / "snapshots" / "daad-study")
    assert path.read_text() == "text"


def test_advance_writes_under_the_private_root_for_a_private_source(tmp_path):
    """SPEC 5, the third rediscovery: a snapshot directory named for the user's
    embassy names their country, so the path itself is the leak."""
    path = advance(PRIVATE, PRIVATE.url, "text", repo_root=tmp_path)
    assert path.is_relative_to(tmp_path / ".private" / "snapshots")
    assert not (tmp_path / "data").exists()


def test_each_page_of_a_source_advances_independently(tmp_path):
    """SPEC 3.6: snapshot advance is per page, not per run, so a budget halt
    after twenty of twenty-five pages advances exactly those twenty."""
    a, b = "https://example.org/db/?detail=1", "https://example.org/db/?detail=2"
    advance(WATCH, a, "page one", repo_root=tmp_path)
    assert detect_change(read_previous(WATCH, a, repo_root=tmp_path), "page one").changed is False
    assert detect_change(read_previous(WATCH, b, repo_root=tmp_path), "page two").changed is True


def test_hashing_is_stable_across_processes(tmp_path):
    """A hash that varied per interpreter run would report every page changed
    every week. sha256 of the utf-8 bytes, not Python's hash()."""
    assert content_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
