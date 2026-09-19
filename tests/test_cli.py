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

    SPEC 3.1 requires health checks to precede the skip gate. Since ruling R14 a
    page the check calls broken is never stored, so the gate can only see an
    unchanged broken page when the stored snapshot was already broken before
    anything flagged it: written before a new signature was added, or before
    this rule existed. The acceptance run left exactly such a snapshot behind.
    That page is unchanged every week, and an alarm behind the gate would never
    fire for it.

    So the error page is seeded into the store directly, as history would have
    left it, and one run over the same page must still alert.
    """
    from scholarship_watchdog.fetch.snapshots import advance

    body = (
        "# Access Denied\n\nYou do not have permission to access "
        "this resource on this server. Reference #18.7c2d1502."
    )
    advance(PUBLIC_WATCH, PUBLIC_WATCH.url, body, repo_root=tmp_path)

    class ErrorPage:
        name = "httpx"

        def fetch(self, source, page_url):
            return FetchResult(
                source_id=source.id,
                page_url=page_url,
                markdown=body,
                status="ok",
                fetched_at=datetime.now(UTC),
                http_status=200,
            )

    run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": ErrorPage()}, repo_root=tmp_path)
    assert run.pages[0].change.changed is False, "precondition: the gate sees no change"
    assert any(a.check == "error_signature" for a in run.alerts), (
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


def test_an_error_page_with_a_rotating_token_is_reported_changed_and_broken(tmp_path):
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


class Sequence:
    """Serves the given bodies in order, one per run, as HTTP 200."""

    name = "httpx"

    def __init__(self, *bodies):
        self._bodies = list(bodies)

    def fetch(self, source, page_url):
        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=self._bodies.pop(0),
            status="ok",
            fetched_at=datetime.now(UTC),
            http_status=200,
        )


GOOD = "Application deadline: 1 October 2027. " + "The programme funds graduate study. " * 100


def _stored(tmp_path, source):
    from scholarship_watchdog.fetch.snapshots import read_previous

    return read_previous(source, source.url, repo_root=tmp_path).previous_markdown


def test_a_broken_page_keeps_the_last_good_snapshot_and_alerts_every_run(tmp_path):
    """Ruling R14: a page the health check calls broken is treated like a failed
    fetch. Storing it overwrote the last real content, which P2 needs to
    re-extract from once the site recovers."""
    fetcher = Sequence(GOOD, "Access Denied.", "Access Denied.")
    fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    for week in (2, 3):
        run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
        assert [a.check for a in run.alerts] == ["error_signature"], f"week {week}"
        assert run.pages[0].broken is True
        assert run.pages[0].advanced is False
        assert _stored(tmp_path, PUBLIC_WATCH) == GOOD, f"week {week}: good copy kept"


def test_content_collapse_alerts_every_week_rather_than_once(tmp_path):
    """The collapsed page used to become the new baseline, so the check fired in
    week two and was silent from week three onwards. An unrecognised bot wall
    on a discover source got exactly one digest line."""
    wall = "Please wait while we check your connection. Ref 12345."
    fetcher = Sequence(GOOD, wall, wall, wall)
    fetch_all([PUBLIC_DISCOVER], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    for week in (2, 3, 4):
        run = fetch_all([PUBLIC_DISCOVER], fetchers={"httpx": fetcher}, repo_root=tmp_path)
        assert [a.check for a in run.alerts] == ["content_collapse"], f"week {week}"
        assert run.pages[0].broken is True, "a collapsed page is not content either"
        assert _stored(tmp_path, PUBLIC_DISCOVER) == GOOD


def test_a_page_that_recovers_advances_normally(tmp_path):
    fetcher = Sequence(GOOD, "Access Denied.", GOOD + " Updated.")
    for _ in range(3):
        run = fetch_all([PUBLIC_WATCH], fetchers={"httpx": fetcher}, repo_root=tmp_path)
    assert run.alerts == []
    assert run.pages[0].advanced is True
    assert _stored(tmp_path, PUBLIC_WATCH) == GOOD + " Updated."


def test_an_empty_firecrawl_render_alerts_every_run_like_an_empty_httpx_page(tmp_path):
    """Both fetchers must treat the same symptom the same way. Firecrawl used
    to return failed for an empty render, which raised nothing at all, while
    the identical shell over httpx raised error_signature."""
    import httpx

    from scholarship_watchdog.fetch.firecrawl_fetcher import FirecrawlFetcher

    source = Source(
        id="nus", name="NUS", role="discover", url="https://n.example/", fetcher="firecrawl"
    )
    empty = FirecrawlFetcher(
        api_key="fc-test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"success": True, "data": {"markdown": "  "}})
            )
        ),
        sleep=lambda s: None,
    )
    for week in (1, 2):
        run = fetch_all([source], fetchers={"firecrawl": empty}, repo_root=tmp_path)
        assert "error_signature" in [a.check for a in run.alerts], f"week {week}"
        assert run.pages[0].advanced is False


def test_an_exception_inside_one_fetch_does_not_end_the_run(tmp_path):
    """Any exception a fetcher raises becomes a failed result for that source.

    The reviews constructed several: a malformed URL (`urlsplit` raises
    ValueError, httpx raises InvalidURL, neither an HTTPError), an unknown
    charset (LookupError carrying the charset name), a malformed link in
    canonicalisation. Each one used to abort the loop before the ledger and the
    report were written. The message is dropped, because it can quote the page
    or the private registry entry that caused it; the type name is kept.
    """

    class Exploding:
        name = "httpx"

        def fetch(self, source, page_url):
            if source.id == PUBLIC_WATCH.id:
                raise LookupError("unknown encoding: private-QQ")
            return RecordingFetcher().fetch(source, page_url)

    run = fetch_all(
        [PUBLIC_WATCH, PUBLIC_DISCOVER], fetchers={"httpx": Exploding()}, repo_root=tmp_path
    )
    by_id = {p.source.id: p for p in run.pages}
    assert by_id["daad-study"].result.status == "failed"
    assert by_id["daad-study"].result.reason == "LookupError"
    assert by_id["mext"].result.status == "ok", "the next source is still fetched"
    assert (tmp_path / "data" / "health.json").exists(), "the ledger is still saved"


def test_a_url_httpx_rejects_fails_one_source_not_the_run(tmp_path):
    import httpx

    from scholarship_watchdog.fetch.httpx_fetcher import HttpxFetcher

    # Parses as a URL, so it survives to the fetch, where httpx rejects it with
    # InvalidURL, which is not an HTTPError and escaped the fetcher's own handling.
    broken = Source(id="broken", name="B", role="watch", url="https://not-a-host.example/p\x00q")
    real = HttpxFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        sleep=lambda s: None,
    )
    run = fetch_all([broken, PUBLIC_WATCH], fetchers={"httpx": real}, repo_root=tmp_path)
    assert [p.result.status for p in run.pages] == ["failed", "failed"]
    assert "not-a-host" not in (run.pages[0].result.reason or "")


def test_the_exit_code_depends_on_public_sources_only(tmp_path):
    """From P3 the job's red or green status is public. A non-zero exit caused
    by a private page is a weekly "some private page broke" signal, the same
    one render_alerts was fixed to withhold. Found by two reviewers."""
    from scholarship_watchdog.cli import exit_code

    healthy_public_broken_private = fetch_all(
        [PUBLIC_WATCH, PRIVATE_WATCH],
        fetchers={"httpx": Selective(broken={PRIVATE_WATCH.id})},
        repo_root=tmp_path / "a",
    )
    assert healthy_public_broken_private.alerts, "precondition: the private page did alert"
    assert exit_code(healthy_public_broken_private) == 0

    broken_public = fetch_all(
        [PUBLIC_WATCH],
        fetchers={"httpx": Selective(broken={PUBLIC_WATCH.id})},
        repo_root=tmp_path / "b",
    )
    assert exit_code(broken_public) == 1


def test_the_run_report_carries_no_private_alert(tmp_path):
    """The page filter had a test; the alert filter beside it did not, and
    replacing it with `if True` left all 142 tests green."""
    run = fetch_all(
        [PUBLIC_WATCH, PRIVATE_WATCH],
        fetchers={"httpx": Selective(broken={PRIVATE_WATCH.id})},
        repo_root=tmp_path,
    )
    assert any(a.source_id == PRIVATE_WATCH.id for a in run.alerts), "precondition"
    started = datetime.now(UTC)
    report = build_run_report(run, started_at=started, finished_at=started, repo_root=tmp_path)
    assert report["alerts"] == []
    assert PRIVATE_WATCH.id not in json.dumps(report)


class Selective:
    """Serves a healthy watch page, or Access Denied for the ids named broken."""

    name = "httpx"

    def __init__(self, broken):
        self._broken = broken

    def fetch(self, source, page_url):
        body = "Access Denied." if source.id in self._broken else GOOD
        return FetchResult(
            source_id=source.id,
            page_url=page_url,
            markdown=body,
            status="ok",
            fetched_at=datetime.now(UTC),
            http_status=200,
        )
