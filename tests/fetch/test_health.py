from datetime import UTC, datetime
from pathlib import Path

from scholarship_watchdog.fetch.base import FetchResult
from scholarship_watchdog.fetch.clean import clean
from scholarship_watchdog.fetch.health import SkipLedger, check_page
from scholarship_watchdog.fetch.snapshots import PageState, advance, read_previous
from scholarship_watchdog.models import Source

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WATCH = Source(id="daad-study", name="DAAD", role="watch", url="https://example.org/d")
DISCOVER = Source(id="mext", name="MEXT", role="discover", url="https://example.org/m")
FIRECRAWL = Source(
    id="nus", name="NUS", role="watch", url="https://example.org/n", fetcher="firecrawl"
)


def _result(source, markdown, status="ok", reason=None):
    return FetchResult(
        source_id=source.id,
        page_url=source.url,
        markdown=markdown,
        status=status,
        fetched_at=datetime.now(UTC),
        reason=reason,
    )


def _no_previous():
    return PageState(previous_markdown=None, previous_hash=None)


def _previous(markdown):
    return PageState(previous_markdown=markdown, previous_hash="unused")


# --- error signature -------------------------------------------------------


def test_an_access_denied_body_served_with_http_200_alerts():
    markdown = clean((FIXTURES / "access_denied.html").read_text(), keep_links=False)
    alerts = check_page(WATCH, _result(WATCH, markdown), _no_previous())
    assert [a.check for a in alerts] == ["error_signature"]


def test_an_empty_extraction_alerts_and_is_the_real_javascript_shell_signal():
    """A JavaScript-only shell with no fallback text cleans to nothing at all.

    trafilatura finds no content and returns None, so the markdown is empty,
    and that empty extraction is the signal. It is what the registry's
    verified note for the NUS source records: it "returns 0 characters to a
    plain fetch".

    Measured on 2026-09-05 with trafilatura 1.12.2: a shell whose only body
    text is a noscript notice keeps that notice through cleaning (60
    characters), because the baseline fallback reads uncleaned body text. That
    shape is caught by the "enable JavaScript" signature in SPEC 3.1's table
    instead. This fixture carries no noscript, so the empty branch is the one
    under test here.
    """
    markdown = clean((FIXTURES / "js_shell.html").read_text(), keep_links=False)
    assert markdown == "", "precondition: cleaning yields nothing for a shell"

    alerts = check_page(FIRECRAWL, _result(FIRECRAWL, markdown), _no_previous())
    assert [a.check for a in alerts] == ["error_signature"]
    assert "empty" in alerts[0].detail


def test_an_empty_extraction_does_not_also_report_a_missing_deadline():
    """One cause, one alert. An empty page has no deadline by construction, and
    reporting both buries the actual problem, which is that nothing arrived."""
    alerts = check_page(FIRECRAWL, _result(FIRECRAWL, ""), _no_previous())
    assert [a.check for a in alerts] == ["error_signature"]


def test_a_stored_error_page_still_alerts_on_the_next_unchanged_run(tmp_path):
    """check_page reads the fetch result, not the store.

    That is half of SPEC 3.1's ordering argument: the check cannot be fooled by
    an error page already sitting in the snapshot. The other half, that
    fetch_all calls this before the skip gate, is tested in tests/test_cli.py,
    because the call order lives there and this test cannot see it.
    """
    markdown = clean((FIXTURES / "access_denied.html").read_text(), keep_links=False)
    advance(WATCH, WATCH.url, markdown, repo_root=tmp_path)

    previous = read_previous(WATCH, WATCH.url, repo_root=tmp_path)
    change_would_be_unchanged = previous.previous_markdown == markdown
    assert change_would_be_unchanged, "precondition: run two sees no change"

    alerts = check_page(WATCH, _result(WATCH, markdown), previous)
    assert any(a.check == "error_signature" for a in alerts)


def test_healthy_content_raises_nothing():
    markdown = clean((FIXTURES / "daad_watch_with_deadline.html").read_text(), keep_links=False)
    assert check_page(WATCH, _result(WATCH, markdown), _no_previous()) == []


# --- content collapse ------------------------------------------------------


def test_content_dropping_below_forty_percent_of_the_previous_snapshot_alerts():
    alerts = check_page(WATCH, _result(WATCH, "x" * 30), _previous("y" * 100))
    assert any(a.check == "content_collapse" for a in alerts)


def test_a_modest_shrink_does_not_alert():
    alerts = check_page(WATCH, _result(WATCH, "x" * 70), _previous("y" * 100))
    assert not any(a.check == "content_collapse" for a in alerts)


def test_the_first_fetch_cannot_collapse():
    """There is no baseline on a first fetch, so the ratio is undefined.
    Alerting here would fire on every newly registered source."""
    alerts = check_page(WATCH, _result(WATCH, "short"), _no_previous())
    assert not any(a.check == "content_collapse" for a in alerts)


# --- role-aware deadline ---------------------------------------------------


def test_a_watch_source_that_stops_yielding_a_deadline_alerts():
    """SPEC 3.1: a watch source that stops yielding a deadline is broken by
    definition. This is the guard the original registry lacked, and the reason
    the registry was rebuilt."""
    markdown = clean((FIXTURES / "daad_watch_no_deadline.html").read_text(), keep_links=False)
    alerts = check_page(WATCH, _result(WATCH, markdown), _no_previous())
    assert any(a.check == "watch_without_deadline" for a in alerts)


def test_a_discover_source_without_a_deadline_never_alerts():
    """Missing dates are expected on a portal. MEXT deadlines are set per
    embassy and never appear on the studyinjapan domain at all."""
    alerts = check_page(
        DISCOVER, _result(DISCOVER, "Scholarship types: type A, type B"), _no_previous()
    )
    assert not any(a.check == "watch_without_deadline" for a in alerts)


def test_a_watch_source_with_a_deadline_does_not_alert():
    markdown = clean((FIXTURES / "daad_watch_with_deadline.html").read_text(), keep_links=False)
    alerts = check_page(WATCH, _result(WATCH, markdown), _no_previous())
    assert not any(a.check == "watch_without_deadline" for a in alerts)


def test_a_failed_fetch_is_not_also_reported_as_a_missing_deadline():
    """One cause, one alert. A dead host reported as three separate failures
    buries the actual problem."""
    alerts = check_page(
        WATCH, _result(WATCH, None, status="failed", reason="HTTP 503"), _no_previous()
    )
    assert [a.check for a in alerts] == []


# --- skip ledger -----------------------------------------------------------


def test_one_skip_is_quiet_and_two_consecutive_skips_alert(tmp_path):
    """SPEC 3.1: a source skipped twice running alerts, so a flagship source
    cannot vanish quietly behind a missing key."""
    skipped = _result(FIRECRAWL, None, status="skipped", reason="no key")

    ledger = SkipLedger.load(tmp_path)
    assert ledger.record(FIRECRAWL, skipped) is None
    ledger.save()

    reloaded = SkipLedger.load(tmp_path)
    alert = reloaded.record(FIRECRAWL, skipped)
    assert alert is not None
    assert alert.check == "skipped_twice"


def test_a_successful_fetch_resets_the_counter(tmp_path):
    skipped = _result(FIRECRAWL, None, status="skipped", reason="no key")
    ledger = SkipLedger.load(tmp_path)
    ledger.record(FIRECRAWL, skipped)
    ledger.record(FIRECRAWL, _result(FIRECRAWL, "content"))
    assert ledger.record(FIRECRAWL, skipped) is None


def test_the_ledger_persists_to_the_public_health_file(tmp_path):
    ledger = SkipLedger.load(tmp_path)
    ledger.record(FIRECRAWL, _result(FIRECRAWL, None, status="skipped", reason="no key"))
    ledger.save()
    assert (tmp_path / "data" / "health.json").exists()


def test_the_ledger_never_records_a_private_source(tmp_path):
    """SPEC 5: nothing about private sources reaches the committed tree, not
    even a counter. Private counters join the encrypted bundle in P3."""
    private = Source(
        id="local-commission",
        name="Commission",
        role="watch",
        url="https://example.org/c",
        private=True,
    )
    ledger = SkipLedger.load(tmp_path)
    ledger.record(private, _result(private, None, status="skipped", reason="no key"))
    ledger.save()

    health = tmp_path / "data" / "health.json"
    assert not health.exists() or "local-commission" not in health.read_text()


def test_a_corrupt_health_file_does_not_stop_the_run(tmp_path):
    """A counter is a convenience; losing it costs one late alert. Aborting a
    deadline watch over it would cost a missed scholarship."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "health.json").write_text("{not json")
    ledger = SkipLedger.load(tmp_path)
    assert ledger.record(FIRECRAWL, _result(FIRECRAWL, "content")) is None
