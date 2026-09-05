import httpx

from scholarship_watchdog.fetch.firecrawl_fetcher import FIRECRAWL_ENDPOINT, FirecrawlFetcher
from scholarship_watchdog.models import Source

NUS = Source(
    id="nus-research-scholarship",
    name="NUS Research Scholarship",
    role="watch",
    url="https://example.org/nus",
    fetcher="firecrawl",
)
CSC = Source(
    id="csc-campuschina",
    name="CampusChina",
    role="discover",
    url="https://example.org/csc",
    fetcher="firecrawl",
)


def _ok(markdown: str):
    def handler(request):
        assert str(request.url) == FIRECRAWL_ENDPOINT
        return httpx.Response(200, json={"success": True, "data": {"markdown": markdown}})

    return handler


def _fetcher(handler, api_key="fc-test-key", **kwargs):
    recorded: list[float] = []
    return FirecrawlFetcher(
        api_key=api_key,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=recorded.append,
        **kwargs,
    ), recorded


def test_a_rendered_page_returns_the_markdown_firecrawl_produced():
    fetcher, _ = _fetcher(_ok("# NUS Research Scholarship\n\nDeadline: 1 January 2026"))
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "ok"
    assert "1 January 2026" in result.markdown


def test_the_api_key_is_sent_as_a_bearer_token_and_never_in_the_url():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"success": True, "data": {"markdown": "text"}})

    fetcher, _ = _fetcher(handler)
    fetcher.fetch(NUS, NUS.url)
    assert seen["auth"] == "Bearer fc-test-key"
    assert "fc-test-key" not in seen["url"]


def test_no_key_means_skipped_with_a_reason_not_failed():
    """SPEC 3.1: the project stays runnable without a Firecrawl key.

    `skipped` rather than `failed` matters downstream: the skip counter alerts
    after two consecutive skips, so a flagship source cannot vanish quietly,
    while a failure would be reported as breakage the user cannot fix.
    """
    fetcher, _ = _fetcher(_ok("unused"), api_key=None)
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "skipped"
    assert result.markdown is None
    assert "no firecrawl api key" in result.reason.lower()


def test_no_key_makes_no_request_at_all():
    """A skip that still spends a request would spend credits it does not have
    and leak the URL to the provider for nothing."""

    def handler(request):
        raise AssertionError("no request should be made without a key")

    fetcher, _ = _fetcher(handler, api_key=None)
    assert fetcher.fetch(NUS, NUS.url).status == "skipped"


def test_a_discover_source_gets_canonical_hrefs():
    markdown = "- [NPGS](/x/npgs?utm_source=nav)"
    fetcher, _ = _fetcher(_ok(markdown))
    result = fetcher.fetch(CSC, "https://www.example.org/portal")
    assert "utm_source" not in result.markdown
    assert "https://www.example.org/x/npgs" in result.markdown


def test_a_rate_limit_response_is_retried_then_reported_failed():
    fetcher, slept = _fetcher(lambda r: httpx.Response(429), max_attempts=3)
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert len(slept) == 2


def test_an_unsuccessful_payload_is_failed_not_ok():
    """Firecrawl answers HTTP 200 with success:false. Treating that as ok would
    store an empty snapshot and make the source look permanently unchanged."""
    fetcher, _ = _fetcher(
        lambda r: httpx.Response(200, json={"success": False, "error": "render timeout"})
    )
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert "render timeout" in result.reason
