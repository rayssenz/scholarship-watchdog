from pathlib import Path

from scholarship_watchdog.fetch.clean import clean

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_cleaning_keeps_the_content_and_drops_the_chrome():
    markdown = clean(_fixture("daad_watch_with_deadline.html"), keep_links=False)
    assert "1 October 2027" in markdown
    assert "992 euros" in markdown
    assert "visitors" not in markdown
    assert "Session 8fb2c1" not in markdown


def test_cleaning_removes_the_render_timestamp_that_would_break_hashing():
    """SPEC 3.1: change detection runs on cleaned markdown, not raw HTML.

    Raw HTML carries render timestamps and session tokens that change on every
    fetch without the content changing, so hashing it would report every page
    changed every week and defeat the primary cost control.
    """
    markdown = clean(_fixture("daad_watch_with_deadline.html"), keep_links=False)
    assert "2026-09-03T14:02:11Z" not in markdown


def test_discover_cleaning_preserves_hyperlink_targets():
    """SPEC 3.1: default cleaning discards hrefs with the navigation, which
    would leave the extractor reading programme names with no link to follow,
    and promotion would have nothing to verify."""
    markdown = clean(_fixture("ntu_discover_links.html"), keep_links=True)
    assert "npgs" in markdown
    assert "Nanyang President's Graduate Scholarship" in markdown


def test_watch_cleaning_does_not_preserve_hyperlink_targets():
    markdown = clean(_fixture("ntu_discover_links.html"), keep_links=False)
    assert "npgs" not in markdown


def test_cleaning_empty_or_unparseable_html_returns_empty_string():
    """An empty application shell must produce empty text, not raise.

    SPEC 3.1: CampusChina returns a shell to a plain client, and this is the
    condition the error-signature health check reads.
    """
    assert clean("", keep_links=False) == ""
    assert clean("<html><body><div id='app'></div></body></html>", keep_links=False) == ""
