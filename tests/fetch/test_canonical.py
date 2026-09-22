from pathlib import Path

import pytest

from scholarship_watchdog.fetch.canonical import canonical_url, canonicalise_markdown_links
from scholarship_watchdog.fetch.clean import clean

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://www.ntu.edu.sg/admissions/graduate/financialmatters/scholarships"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.ORG/a/b", "https://example.org/a/b"),
        ("https://example.org/a/b#section", "https://example.org/a/b"),
        ("https://example.org/a/b?utm_source=nav", "https://example.org/a/b"),
        ("https://example.org/a/b?gclid=x&id=7", "https://example.org/a/b?id=7"),
        ("https://example.org/a/b?b=2&a=1", "https://example.org/a/b?a=1&b=2"),
        ("https://example.org/a/b/", "https://example.org/a/b"),
        ("https://example.org/", "https://example.org/"),
    ],
)
def test_canonicalisation_removes_noise_and_orders_what_remains(raw, expected):
    assert canonical_url(raw) == expected


def test_a_meaningful_query_parameter_survives():
    """The DAAD watch sources are distinguished only by ?detail=, so stripping
    the query wholesale would collapse two watched programmes into one."""
    url = "https://www2.daad.de/db/en/21148-scholarship-database/?detail=50026200"
    assert "detail=50026200" in canonical_url(url)


def test_a_relative_href_resolves_against_the_page_it_came_from():
    assert canonical_url("scholarships/rss", base=BASE) == (
        "https://www.ntu.edu.sg/admissions/graduate/financialmatters/scholarships/rss"
    )


def test_a_root_relative_href_resolves_against_the_host():
    assert canonical_url("/admissions/x", base=BASE) == "https://www.ntu.edu.sg/admissions/x"


def test_tracking_parameters_do_not_make_a_portal_look_changed():
    """SPEC 3.1: hrefs are canonicalised before hashing, so a portal rotating
    tracking parameters does not register as changed.

    This is the whole cost control. A portal that looks changed every week is
    an extraction call every week for content that never moved.
    """
    plain = canonicalise_markdown_links(
        clean((FIXTURES / "ntu_discover_links.html").read_text(), keep_links=True),
        base=BASE,
    )
    tracked = canonicalise_markdown_links(
        clean((FIXTURES / "ntu_discover_links_utm.html").read_text(), keep_links=True),
        base=BASE,
    )
    assert plain == tracked


def test_canonicalising_markdown_leaves_the_link_text_alone():
    markdown = "See [Nanyang President's Graduate Scholarship](/x/npgs?utm_source=nav)."
    result = canonicalise_markdown_links(markdown, base=BASE)
    assert "Nanyang President's Graduate Scholarship" in result
    assert "utm_source" not in result
    assert "https://www.ntu.edu.sg/x/npgs" in result


def test_a_malformed_link_is_left_alone_rather_than_failing_the_page():
    """urlsplit raises on a target such as `http://[broken/x`. Raised inside a
    fetch, that failed the whole discover page every week, taking every good
    link on it down with the one bad one."""
    markdown = "- [Good](/p1?utm_source=x)\n- [Bad](http://[broken/x)"
    out = canonicalise_markdown_links(markdown, base="https://a.example/portal")
    assert "https://a.example/p1" in out
    assert "http://[broken/x" in out
