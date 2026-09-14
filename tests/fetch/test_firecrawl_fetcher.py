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


def test_the_endpoint_targets_the_documented_api_version():
    """Verified live on 2026-09-14 against docs.firecrawl.dev.

    v1 and v2 both still answer and take the same request and response shapes,
    but the v1 reference now redirects to the v2 introduction, so v1 is
    undocumented and on an unannounced retirement path. This runs unattended
    for months at a time, so it targets the version that is documented.
    """
    assert FIRECRAWL_ENDPOINT == "https://api.firecrawl.dev/v2/scrape"


def test_a_200_carrying_html_is_failed_not_an_exception():
    """A captive portal or proxy interstitial answers 200 with HTML.

    An unguarded response.json() raises JSONDecodeError out of the fetcher, and
    nothing between here and the CLI catches it, so one bad response would end
    the whole weekly run before the skip ledger or the report were written.
    """
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, text="<html>Access Denied</html>"))
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert result.markdown is None


def test_a_200_with_an_empty_body_is_failed():
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, text=""))
    assert fetcher.fetch(NUS, NUS.url).status == "failed"


def test_a_200_whose_json_is_not_an_object_is_failed():
    """`payload.get` on a list raises AttributeError, same blast radius."""
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, json=[1, 2, 3]))
    assert fetcher.fetch(NUS, NUS.url).status == "failed"


def test_a_successful_payload_carrying_no_markdown_is_failed():
    """SPEC 3.1: a page that stores an empty snapshot looks unchanged forever.

    `{"success": true, "data": null}` previously returned ok with empty
    markdown, which advances an empty snapshot. The content-collapse check
    fires once on that run and never again, because the empty snapshot becomes
    the baseline it compares against.
    """
    for payload in ({"success": True, "data": None}, {"success": True, "data": {"markdown": ""}}):
        fetcher, _ = _fetcher(lambda request, p=payload: httpx.Response(200, json=p))
        result = fetcher.fetch(NUS, NUS.url)
        assert result.status == "failed", payload
        assert result.markdown is None, payload


def test_an_oversized_firecrawl_response_is_failed():
    """Same budget as the httpx fetcher, and for the same runner.

    A proxy or captive portal in front of the API answers with whatever it
    likes, so "it is a paid API" is not a memory bound.
    """
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, content=b"x" * 5000), max_bytes=1000)
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert "too large" in result.reason
