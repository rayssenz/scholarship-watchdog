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


def test_an_oversized_response_is_failed_rather_than_read_into_memory():
    """SPEC 4: this runs unattended on a 7 GB Actions runner with no swap.

    A hostile or merely broken source can answer with a body far larger than
    any scholarship page. Reading it whole OOM-kills the job, which reports no
    diagnostics, writes no run report and leaves the skip ledger unwritten.
    """
    body = b"x" * 5000

    def handler(request):
        return httpx.Response(200, content=body)

    fetcher, _ = _fetcher(handler, max_bytes=1000)
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "failed"
    assert result.markdown is None
    assert "too large" in result.reason


def test_a_lying_content_length_does_not_defeat_the_cap():
    """Content-Length is optional and attacker-controlled, so it is not the
    enforcement. The budget is counted over the bytes actually read."""

    def handler(request):
        return httpx.Response(200, content=b"x" * 5000, headers={"Content-Length": "12"})

    fetcher, _ = _fetcher(handler, max_bytes=1000)
    assert fetcher.fetch(WATCH, WATCH.url).status == "failed"


def test_a_body_inside_the_cap_is_still_fetched_normally():
    html = (FIXTURES / "daad_watch_with_deadline.html").read_text()
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, html=html), max_bytes=10_000_000)
    assert fetcher.fetch(WATCH, WATCH.url).status == "ok"


def test_an_oversized_body_is_abandoned_rather_than_drained():
    """The memory bound, as opposed to the label on it.

    Classifying an oversize response as failed is worth nothing if the bytes
    were already buffered before the count ran, which is what a non-streaming
    get() does. This counts what the server was actually asked to produce: the
    generator must stop being pulled shortly after the budget is passed, not
    run to completion.
    """
    produced = []

    def chunks():
        for i in range(1000):
            produced.append(i)
            yield b"x" * 1000

    def handler(request):
        return httpx.Response(200, content=chunks())

    fetcher, _ = _fetcher(handler, max_bytes=5000)
    result = fetcher.fetch(WATCH, WATCH.url)

    assert result.status == "failed"
    assert len(produced) < 20, f"drained {len(produced)} chunks of a 1000-chunk body"


def test_no_cookie_set_while_fetching_one_source_reaches_the_next():
    """One client serves every source, private ones included, and they are
    fetched first. A cookie a private page set was sent with the next public
    request, so a public snapshot could change with the private registry, which
    is exactly what SPEC 5's invariant forbids."""
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("cookie")))
        if request.url.path == "/private":
            return httpx.Response(200, html="ok", headers={"Set-Cookie": "country=QQ; Path=/"})
        return httpx.Response(200, html="ok")

    fetcher, _ = _fetcher(handler)
    private = Source(
        id="p", name="P", role="watch", url="https://host.example/private", private=True
    )
    public = Source(id="q", name="Q", role="watch", url="https://host.example/public")
    fetcher.fetch(private, private.url)
    fetcher.fetch(public, public.url)
    assert seen == [("/private", None), ("/public", None)]


def _portal(links):
    """A discover page trafilatura keeps links for: a <main> region and enough
    entries (SPEC 3.1's measured limit)."""
    items = "".join(
        f'<li><a href="{h}">Programme {i} Scholarship</a></li>' for i, h in enumerate(links)
    )
    return f"<html><body><main><h1>Scholarships</h1><ul>{items}</ul></main></body></html>"


def test_a_redirect_body_is_abandoned_rather_than_drained():
    """httpx reads each intermediate redirect's
    body in full before yielding the final response, so the byte cap never saw
    it: a 302 streaming a large body was drained completely and the fetch
    returned ok. The drain test only ever exercised a final 200."""
    pulled = []

    def big():
        for i in range(1000):
            pulled.append(i)
            yield b"x" * 1000

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/end"}, content=big())
        html = (FIXTURES / "daad_watch_with_deadline.html").read_text()
        return httpx.Response(200, html=html)

    fetcher, _ = _fetcher(handler, max_bytes=5000)
    src = Source(id="s", name="S", role="watch", url="https://a.example/start")
    result = fetcher.fetch(src, src.url)
    assert result.status == "ok"
    assert len(pulled) < 20, f"drained {len(pulled)} chunks of a 1000-chunk redirect body"


def test_a_redirect_loop_fails_instead_of_running_forever():
    fetcher, _ = _fetcher(lambda r: httpx.Response(302, headers={"Location": r.url.path}))
    src = Source(id="s", name="S", role="watch", url="https://a.example/loop")
    result = fetcher.fetch(src, src.url)
    assert result.status == "failed"
    assert "redirect" in result.reason.lower()


def test_a_redirect_to_a_non_http_url_is_not_followed():
    fetcher, _ = _fetcher(lambda r: httpx.Response(302, headers={"Location": "file:///etc/passwd"}))
    src = Source(id="s", name="S", role="watch", url="https://a.example/p")
    assert fetcher.fetch(src, src.url).status == "failed"


def test_links_on_a_redirected_page_resolve_against_where_the_page_ended_up():
    """Canonicalisation used the requested URL
    as its base, so `/start` redirecting to `/new/portal/` turned a relative
    link `p0` into `/p0` instead of `/new/portal/p0`: a candidate link to the
    wrong page, which P4 would then try to verify and promote."""

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/new/portal/"})
        return httpx.Response(200, html=_portal([f"p{i}" for i in range(20)]))

    fetcher, _ = _fetcher(handler)
    src = Source(id="d", name="D", role="discover", url="https://a.example/start")
    result = fetcher.fetch(src, src.url)
    assert "https://a.example/new/portal/p0" in result.markdown
    assert result.page_url == src.url, "the snapshot stays keyed on the registered URL"


def test_a_3xx_page_without_a_location_is_read_as_a_page():
    """`is_redirect` is true for every 3xx. A `300 Multiple Choices` page listing
    alternatives has no Location to follow, and the manual redirect loop turned
    it into failed/KeyError where httpx used to return its body."""
    html = (FIXTURES / "daad_watch_with_deadline.html").read_text()
    fetcher, _ = _fetcher(lambda r: httpx.Response(300, html=html))
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "ok"
    assert "1 October 2027" in result.markdown


def test_an_unknown_charset_falls_back_rather_than_failing_the_page():
    """A server declaring `charset=utf8mb4` made the capped reader raise
    LookupError on every run. httpx's own decoding falls back for the same
    header, so moving to the capped reader had quietly regressed it."""
    html = (FIXTURES / "daad_watch_with_deadline.html").read_bytes()
    fetcher, _ = _fetcher(
        lambda r: httpx.Response(
            200, content=html, headers={"Content-Type": "text/html; charset=utf8mb4"}
        )
    )
    result = fetcher.fetch(WATCH, WATCH.url)
    assert result.status == "ok"
    assert "1 October 2027" in result.markdown
