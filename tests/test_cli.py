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


class AccessDeniedPage:
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


def test_the_printed_summary_omits_private_sources_entirely(tmp_path):
    """SPEC 5, applied to stdout.

    From P3 this output is a GitHub Actions log on a public repository.
    Withholding the id is not enough: one line per private source publishes how
    many exist, and therefore the week the auto-grown watch list gained one.
    SPEC 5 settled that argument for GitHub Issues, where wording the body
    carefully did not help because the existence of the entry was the signal.
    The line count must depend on the public registry alone.
    """
    from scholarship_watchdog.cli import render_alerts, render_summary

    run = fetch_all(
        [PUBLIC_WATCH, PRIVATE_WATCH], fetchers={"httpx": AccessDeniedPage()}, repo_root=tmp_path
    )
    summary = render_summary(run)
    printed = "\n".join(summary + render_alerts(run))

    assert len(summary) == 1, "one line per public source, and nothing per private source"
    assert "daad-study" in printed, "public ids are still reported"
    assert "local-commission" not in printed
    assert "(private source)" not in printed, "a redacted line still counts the private sources"


def test_the_printed_output_is_identical_with_and_without_private_sources(tmp_path):
    """The invariant behind the line count, stated so it cannot regress.

    SPEC 5: anything shaped by the profile is private, including every artifact
    produced by acting on it. Two runs differing only in their private registry
    must be indistinguishable on stdout and stderr.
    """
    from scholarship_watchdog.cli import render_alerts, render_summary

    def printed(sources):
        run = fetch_all(
            sources, fetchers={"httpx": AccessDeniedPage()}, repo_root=tmp_path / str(len(sources))
        )
        return "\n".join(render_summary(run) + render_alerts(run))

    assert printed([PUBLIC_WATCH]) == printed([PUBLIC_WATCH, PRIVATE_WATCH])


def test_a_source_whose_fetcher_is_unavailable_is_skipped_not_crashed(tmp_path):
    """A firecrawl source with no key when no firecrawl fetcher was built."""
    firecrawl_source = Source(
        id="nus", name="NUS", role="watch", url="https://d.example/n", fetcher="firecrawl"
    )
    run = fetch_all([firecrawl_source], fetchers={"httpx": RecordingFetcher()}, repo_root=tmp_path)
    assert run.pages[0].result.status == "skipped"


def test_a_malformed_private_config_exits_cleanly_without_a_traceback(tmp_path, capsys):
    """SPEC 5: from P3 this stderr is a public Actions log.

    The end-to-end guard behind the loader's own. Exit 2, one line, and no
    fragment of the gitignored file anywhere in the output.
    """
    from scholarship_watchdog.cli import main

    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text(
        "sources:\n  - id: daad\n    name: DAAD\n    role: watch\n    url: https://a.example/d\n"
    )
    (config / "sources.local.yaml").write_text(
        "sources:\n  - id: mext-local-embassy\n    name: Embassy\n"
        '    url: "https://REPLACE-ME.embassy.example/scholarship\n    role: watch\n'
    )

    code = main(["--repo-root", str(tmp_path), "fetch"])
    captured = capsys.readouterr()

    assert code == 2
    assert "Traceback" not in captured.err
    assert "REPLACE-ME" not in captured.err
    assert "mext-local-embassy" not in captured.err
    assert "sources.local.yaml" in captured.err


def test_an_error_page_with_a_rotating_token_is_not_reported_as_a_change(tmp_path):
    """Measured live on 2026-09-14, docs/p1-acceptance.md.

    An Imperva block regenerates its incident ID on every request, so the
    cleaned markdown differs every run and the hash never settles. The alert is
    correct and fires every run by SPEC 3.1's design. Calling it a content
    change is not: under the P3 cron it would extract and notify every week
    about a page that is permanently broken.

    An error page is not a content change. The snapshot still advances, because
    SPEC 3.1 says a 200 succeeded at the transport level, but nothing
    downstream should act on it.
    """

    class RotatingBlock:
        name = "httpx"

        def __init__(self):
            self.n = 0

        def fetch(self, source, page_url):
            self.n += 1
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown=f"Request unsuccessful. Incapsula incident ID: 99900{self.n}-4477{self.n}",
                status="ok",
                fetched_at=datetime.now(UTC),
                http_status=200,
            )

    fetcher = RotatingBlock()
    for run_number in (1, 2, 3):
        run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
        assert any(a.check == "error_signature" for a in run.alerts), (
            f"run {run_number}: the block must alert every run"
        )
        assert run.pages[0].broken is True, (
            f"run {run_number}: the page must be marked broken, not treated as new content"
        )
        assert run.pages[0].change.changed is True, (
            f"run {run_number}: the hash genuinely differs; `changed` must stay truthful "
            "so the ordering guard's precondition keeps its meaning"
        )


def test_a_real_content_change_is_still_reported_as_changed(tmp_path):
    """The other side of the guard above: suppressing error pages must not
    suppress genuine edits."""

    class Editing:
        name = "httpx"

        def __init__(self):
            self.n = 0

        def fetch(self, source, page_url):
            self.n += 1
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown=f"Application deadline: {self.n} October 2027. Apply through the portal.",
                status="ok",
                fetched_at=datetime.now(UTC),
                http_status=200,
            )

    fetcher = Editing()
    first = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    second = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    assert first.pages[0].change.changed is True
    assert second.pages[0].change.changed is True, "a genuine edit is still a change"
    assert first.pages[0].broken is False
    assert second.pages[0].broken is False, "a good page is never marked broken"


def test_the_run_report_marks_a_broken_page(tmp_path):
    """SPEC 3.1: the report distinguishes a changed page from a broken one."""
    run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": AccessDeniedPage()}, repo_root=tmp_path)
    started = datetime.now(UTC)
    row = build_run_report(run, started_at=started, finished_at=started, repo_root=tmp_path)[
        "sources"
    ][0]
    assert row["broken"] is True
    assert row["changed"] is True, "the hash statement stays truthful"


def test_the_run_report_does_not_mark_a_healthy_page_broken(tmp_path):
    run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": RecordingFetcher()}, repo_root=tmp_path)
    started = datetime.now(UTC)
    row = build_run_report(run, started_at=started, finished_at=started, repo_root=tmp_path)[
        "sources"
    ][0]
    assert row["broken"] is False


def test_a_watch_page_that_404s_every_week_alerts_through_the_real_pipeline(tmp_path):
    """The failure the review constructed, end to end with a real fetcher.

    Three weekly runs of a moved page used to produce three empty alert lists.
    """
    import httpx

    from scholarship_watchdog.fetch.httpx_fetcher import HttpxFetcher

    moved = HttpxFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        sleep=lambda s: None,
    )
    checks = []
    for _ in range(3):
        run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": moved}, repo_root=tmp_path)
        checks.append([a.check for a in run.alerts])
    assert checks == [[], ["skipped_twice"], ["skipped_twice"]]
