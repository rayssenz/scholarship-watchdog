import json
from datetime import UTC, datetime

from scholarship_watchdog.fetch import build_run_report, fetch_all
from scholarship_watchdog.fetch.base import FetchResult
from scholarship_watchdog.models import Source

PUBLIC_WATCH = Source(id="daad-study", name="DAAD", role="watch", url="https://a.example/d")
PUBLIC_DISCOVER = Source(id="mext", name="MEXT", role="discover", url="https://b.example/m")
PRIVATE_WATCH = Source(
    id="local-commission",
    name="Commission",
    role="watch",
    url="https://c.example/x",
    private=True,
)


class RecordingFetcher:
    name = "httpx"

    def __init__(self, markdown="Application deadline: 1 October 2027"):
        self.calls: list[str] = []
        self._markdown = markdown

    def fetch(self, source, page_url):
        self.calls.append(source.id)
        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=self._markdown,
            status="ok",
            fetched_at=datetime.now(UTC),
            http_status=200,
        )


def test_watch_sources_are_fetched_before_discover_sources(tmp_path):
    """SPEC 3.6: deadline safety first is a principle that has to be enforced
    by ordering. A verification queue running first could exhaust the budget
    before the watched deadline pages are fetched at all."""
    fetcher = RecordingFetcher()
    fetch_all(
        [PUBLIC_DISCOVER, PUBLIC_WATCH],
        fetchers={"httpx": fetcher},
        repo_root=tmp_path,
    )
    assert fetcher.calls == ["daad-study", "mext"]


def test_a_second_run_over_unchanged_content_reports_every_page_unchanged(tmp_path):
    """P1 acceptance bullet two, end to end."""
    fetcher = RecordingFetcher()
    first = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    assert all(p.change.changed for p in first.pages)

    second = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    assert all(not p.change.changed for p in second.pages)


def test_a_failed_page_does_not_advance_its_snapshot(tmp_path):
    """SPEC 4: a detected change stays pending and the next run retries it, so
    a lost week self-heals rather than silently dropping an opportunity."""

    class Failing:
        name = "httpx"

        def fetch(self, source, page_url):
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown=None,
                status="failed",
                fetched_at=datetime.now(UTC),
                reason="HTTP 503",
            )

    run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": Failing()}, repo_root=tmp_path)
    assert run.pages[0].advanced is False
    assert not list((tmp_path / "data" / "snapshots").rglob("*.md"))


def test_the_run_report_never_mentions_a_private_source(tmp_path):
    """SPEC 5: nothing about private sources reaches the committed run report,
    not even aggregate counts.

    The report already says which public portals changed. A line saying the
    private watch list grew in the same week lets an observer correlate the two
    and conclude that a programme on that portal cleared the profile's blockers.
    """
    fetcher = RecordingFetcher()
    run = fetch_all([PUBLIC_WATCH, PRIVATE_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    started = datetime.now(UTC)
    report = build_run_report(run, started_at=started, finished_at=started)

    blob = json.dumps(report)
    assert "local-commission" not in blob
    assert "c.example" not in blob
    assert report["pages_fetched"] == 1, "private pages must not be counted either"


def test_a_private_source_is_still_fetched_and_snapshotted_privately(tmp_path):
    """Excluded from the report, not from the run. The user's commission page
    is the only real watch source for Fulbright."""
    fetcher = RecordingFetcher()
    fetch_all([PRIVATE_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    assert fetcher.calls == ["local-commission"]
    assert list((tmp_path / ".private" / "snapshots").rglob("*.md"))
    assert not (tmp_path / "data" / "snapshots").exists()


def test_the_report_carries_a_staleness_figure_without_alerting(tmp_path):
    """SPEC 3.1: staleness is reported and never alerts."""
    fetcher = RecordingFetcher()
    run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    started = datetime.now(UTC)
    report = build_run_report(run, started_at=started, finished_at=started, repo_root=tmp_path)
    assert report["sources"][0]["days_since_change"] == 0
    assert not any(a.check == "staleness" for a in run.alerts)


def test_a_stored_error_page_alerts_on_the_second_run_through_fetch_all(tmp_path):
    """The ordering guard, tested where the ordering actually lives.

    SPEC 3.1 argues health checks must precede the skip gate because a broken
    source is unchanged after its first broken fetch. That argument is about
    fetch_all: check_page is stateless, so testing it alone proves only that
    it ignores the gate, which is trivially true. What can regress is the order
    of the calls in this function, and only a run through fetch_all catches it.

    Run one stores the error page. Run two fetches the same error page, so the
    change gate reports unchanged and would end the page's pipeline. The alert
    must still be there.
    """

    class ErrorPage:
        name = "httpx"

        def fetch(self, source, page_url):
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown=(
                    "# Access Denied\n\nYou do not have permission to access "
                    "this resource on this server. Reference #18.7c2d1502."
                ),
                status="ok",
                fetched_at=datetime.now(UTC),
                http_status=200,
            )

    first = fetch_all([PUBLIC_WATCH], fetchers={"httpx": ErrorPage()}, repo_root=tmp_path)
    assert any(a.check == "error_signature" for a in first.alerts)
    assert first.pages[0].change.changed is True

    second = fetch_all([PUBLIC_WATCH], fetchers={"httpx": ErrorPage()}, repo_root=tmp_path)
    assert second.pages[0].change.changed is False, "precondition: run two sees no change"
    assert any(a.check == "error_signature" for a in second.alerts), (
        "the health check ran after the skip gate and is now unfireable"
    )


def test_the_printed_summary_withholds_private_source_ids(tmp_path):
    """SPEC 5, applied to stdout.

    From P3 this output is a GitHub Actions log on a public repository, so a
    printed id names the user's commission and therefore their country. The
    outcome is reported; the identity is not.
    """
    from scholarship_watchdog.cli import render_alerts, render_summary

    class ErrorPage:
        name = "httpx"

        def fetch(self, source, page_url):
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown="Access Denied. You do not have permission.",
                status="ok",
                fetched_at=datetime.now(UTC),
                http_status=200,
            )

    run = fetch_all(
        [PUBLIC_WATCH, PRIVATE_WATCH], fetchers={"httpx": ErrorPage()}, repo_root=tmp_path
    )
    printed = "\n".join(render_summary(run) + render_alerts(run))

    assert "local-commission" not in printed
    assert "(private source)" in printed
    assert "daad-study" in printed, "public ids are still reported"


def test_a_source_whose_fetcher_is_unavailable_is_skipped_not_crashed(tmp_path):
    """A firecrawl source with no key when no firecrawl fetcher was built."""
    firecrawl_source = Source(
        id="nus", name="NUS", role="watch", url="https://d.example/n", fetcher="firecrawl"
    )
    run = fetch_all([firecrawl_source], fetchers={"httpx": RecordingFetcher()}, repo_root=tmp_path)
    assert run.pages[0].result.status == "skipped"
