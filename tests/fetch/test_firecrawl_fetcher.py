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


def test_a_malformed_success_payload_is_failed():
    """A success envelope with no data object, or with markdown that is not
    text, is a protocol error rather than a page. It is failed, not stored."""
    for payload in (
        {"success": True, "data": None},
        {"success": True},
        {"success": True, "data": {"markdown": 42}},
    ):
        fetcher, _ = _fetcher(lambda request, p=payload: httpx.Response(200, json=p))
        result = fetcher.fetch(NUS, NUS.url)
        assert result.status == "failed", payload
        assert result.markdown is None, payload


def test_an_empty_render_is_an_ok_page_with_no_text():
    """The same shape the httpx fetcher returns for a JavaScript shell, so one
    health check covers both fetchers. Ruling R14 keeps it out of the snapshot
    store; returning failed instead made it raise nothing at all."""
    for markdown in ("", "   "):
        payload = {"success": True, "data": {"markdown": markdown}}
        fetcher, _ = _fetcher(lambda request, p=payload: httpx.Response(200, json=p))
        result = fetcher.fetch(NUS, NUS.url)
        assert result.status == "ok"
        assert result.markdown == ""


def test_an_oversized_firecrawl_response_is_failed():
    """Same budget as the httpx fetcher, and for the same runner.

    A proxy or captive portal in front of the API answers with whatever it
    likes, so "it is a paid API" is not a memory bound.
    """
    fetcher, _ = _fetcher(lambda request: httpx.Response(200, content=b"x" * 5000), max_bytes=1000)
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert "too large" in result.reason


def _rendered(markdown, **metadata):
    payload = {"success": True, "data": {"markdown": markdown, "metadata": metadata}}
    return lambda request: httpx.Response(200, json=payload)


def test_an_origin_error_behind_a_successful_render_is_failed():
    """Firecrawl answers 200 for its own API call and reports the target page's
    status in `data.metadata.statusCode` (checked against docs.firecrawl.dev on
    2026-09-19). Reading only the API status stored a maintenance page served
    with 503 as a good snapshot, contrary to SPEC 3.1's rule that HTTP 400 or
    worse is a failure. Found by two reviewers."""
    fetcher, _ = _fetcher(_rendered("Down for maintenance.", statusCode=503))
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "failed"
    assert result.http_status == 503
    assert "503" in result.reason


def test_the_origin_status_is_recorded_when_the_page_is_fine():
    fetcher, _ = _fetcher(_rendered("# NUS\n\nDeadline: 1 January 2027", statusCode=200))
    result = fetcher.fetch(NUS, NUS.url)
    assert result.status == "ok"
    assert result.http_status == 200


def test_a_render_without_metadata_is_still_accepted():
    """The field is documented but not promised on every response."""
    fetcher, _ = _fetcher(_ok("# NUS\n\nDeadline: 1 January 2027"))
    assert fetcher.fetch(NUS, NUS.url).status == "ok"


def test_links_resolve_against_the_url_firecrawl_ended_up_on():
    """The same redirect question the httpx fetcher answers: a relative link
    resolves against where the page ended up, reported as `metadata.url`."""
    fetcher, _ = _fetcher(
        _rendered("- [Award](award)", statusCode=200, url="https://www.example.org/new/portal/")
    )
    result = fetcher.fetch(CSC, "https://www.example.org/start")
    assert "https://www.example.org/new/portal/award" in result.markdown


def test_no_cookie_from_one_scrape_reaches_the_next():
    """The httpx fetcher clears its jar per fetch; this one did not. A cookie
    the API set during a private scrape rode along on the next public one."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return httpx.Response(
            200,
            json={"success": True, "data": {"markdown": "text"}},
            headers={"Set-Cookie": "choice=QQ; Path=/"},
        )

    fetcher, _ = _fetcher(handler)
    fetcher.fetch(NUS, NUS.url)
    fetcher.fetch(CSC, CSC.url)
    assert seen == [None, None]


def test_a_long_provider_error_is_cut_short_in_the_reason():
    """The provider's error text goes into the committed run report and the
    skip alert. It is the provider's to write, so its length is ours to bound."""
    fetcher, _ = _fetcher(
        lambda r: httpx.Response(200, json={"success": False, "error": "x" * 5000})
    )
    assert len(fetcher.fetch(NUS, NUS.url).reason) <= 200
