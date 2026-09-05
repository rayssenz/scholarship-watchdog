from pathlib import Path

import httpx
import pytest

from scholarship_watchdog.fetch.httpx_fetcher import USER_AGENT, HttpxFetcher
from scholarship_watchdog.models import Source

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WATCH = Source(id="daad-study", name="DAAD", role="watch", url="https://example.org/d")
DISCOVER = Source(
    id="ntu-postgrad",
    name="NTU",
    role="discover",
    url="https://www.ntu.edu.sg/admissions/graduate/financialmatters/scholarships",
)


def _fetcher(handler, **kwargs):
    recorded: list[float] = []
    fetcher = HttpxFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=recorded.append,
        **kwargs,
    )
    return fetcher, recorded


def test_a_successful_fetch_returns_cleaned_markdown():
    html = (FIXTURES / "daad_watch_with_deadline.html").read_text()
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, html=html))
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "ok"
    assert result.http_status == 200
    assert "1 October 2027" in result.markdown
    assert "Session 8fb2c1" not in result.markdown


def test_the_honest_user_agent_is_sent():
    """SPEC 4: polite rate limiting and an honest user agent identifying the
    project and its repository."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, html="<html><body><p>ok text here</p></body></html>")

    fetcher, _ = _fetcher(handler)
    fetcher.fetch(WATCH, WATCH.url)
    assert seen["ua"] == USER_AGENT
    assert "github.com/rayssenz/scholarship-watchdog" in USER_AGENT


def test_a_discover_source_gets_link_preserving_cleaning_and_canonical_hrefs():
    html = (FIXTURES / "ntu_discover_links_utm.html").read_text()
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, html=html))
    result = fetcher.fetch(DISCOVER, DISCOVER.url)
    assert "scholarships/npgs" in result.markdown
    assert "utm_source" not in result.markdown


def test_a_watch_source_gets_link_free_cleaning():
    html = (FIXTURES / "ntu_discover_links.html").read_text()
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, html=html))
    result = fetcher.fetch(WATCH, DISCOVER.url)
    assert "npgs" not in result.markdown


def test_a_transient_server_error_is_retried_with_exponential_backoff():
    """SPEC 4: bounded retries with exponential backoff."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, html="<html><body><p>recovered content</p></body></html>")

    fetcher, slept = _fetcher(handler)
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "ok"
    assert attempts["n"] == 3
    assert slept == [1.0, 2.0]


def test_retries_are_bounded_and_the_page_ends_as_failed():
    fetcher, slept = _fetcher(lambda request: httpx.Response(503), max_attempts=3)
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "failed"
    assert result.http_status == 503
    assert len(slept) == 2


def test_a_client_error_is_not_retried():
    """A 404 will not become a 200 on the third try; retrying it is impolite
    and wastes the run's time budget."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(404)

    fetcher, _ = _fetcher(handler)
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "failed"
    assert attempts["n"] == 1
    assert result.reason and "404" in result.reason


def test_a_timeout_is_reported_as_failed_not_raised():
    """A run must survive one dead host. SPEC 4 makes a failed page keep its
    old snapshot so the next weekly run retries it."""

    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    fetcher, _ = _fetcher(handler)
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "failed"
    assert result.markdown is None
    assert "ConnectTimeout" in result.reason


def test_rate_limiting_waits_between_requests_to_one_host():
    """SPEC 4: polite rate limiting. Three of the registry's watch sources are
    on one DAAD host, so a run would hit it three times in a row."""
    fetcher, slept = _fetcher(lambda r: httpx.Response(200, html="<p>content text</p>"))
    fetcher.fetch(WATCH, "https://example.org/one")
    fetcher.fetch(WATCH, "https://example.org/two")
    assert slept and slept[-1] == pytest.approx(WATCH.rate_limit_seconds, abs=0.5)
