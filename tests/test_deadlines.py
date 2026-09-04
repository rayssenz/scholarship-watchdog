import pytest

from scholarship_watchdog.deadlines import find_dates, has_deadline


@pytest.mark.parametrize(
    "text",
    [
        "Application deadline: 1 October 2027",
        "Closing date is October 6, 2026 at 23:59",
        "Bewerbungsschluss / deadline 04.03.2027",
        "applications close 2026-01-01",
        "Apply by 15/11/2026",
    ],
)
def test_a_date_beside_a_deadline_keyword_counts_as_a_deadline(text):
    assert has_deadline(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Last updated 12 March 2026. Our programmes are taught in English.",
        "The deadline varies by embassy; contact your local mission.",
        "Published 2026-02-01 in the news archive.",
        "",
    ],
)
def test_a_date_without_a_keyword_or_a_keyword_without_a_date_does_not(text):
    """The PKU case: twelve dates and zero deadline keywords, all news
    timestamps. Registry note in config/sources.yaml records why it was
    dropped."""
    assert has_deadline(text) is False


def test_the_german_date_format_is_recognised_verbatim():
    """SPEC 3.2 calls DD.MM.YYYY the highest-risk field. It must be found
    before it can be preserved in deadline_raw."""
    assert find_dates("Frist: 04.03.2027") == ["04.03.2027"]


def test_a_fuzzy_date_yields_no_parsed_date():
    """'mid-October' is a deadline_raw value with no deadline. It is not a
    date, and claiming otherwise is how a wrong deadline gets invented."""
    assert find_dates("Applications close in mid-October") == []
