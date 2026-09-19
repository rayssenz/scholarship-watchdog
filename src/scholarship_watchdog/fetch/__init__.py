"""The fetch stage. See SPEC.md sections 3.1 and 3.6.

Ordering is load-bearing, not stylistic. Watch sources are fetched before
discover sources because all stages draw on one budget, and "deadline safety
first" is a principle in section 2 that has to be enforced by ordering: a long
discovery pass running first could exhaust the budget before the watched
deadline pages are fetched at all, starving the guarantee the system exists to
provide.

Per page, the order is fetch, then health check, then the change gate, then
advance. The health check precedes the gate for the reason section 3.1 gives,
and a page it calls broken is not advanced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..models import Source
from ..paths import snapshot_path
from .base import Fetcher, FetchResult, FetchStatus
from .health import Alert, SkipLedger, check_page
from .snapshots import Change, advance, detect_change, read_previous

__all__ = [
    "Alert",
    "Change",
    "FetchResult",
    "FetchRun",
    "FetchStatus",
    "Fetcher",
    "PageOutcome",
    "build_run_report",
    "fetch_all",
]


@dataclass(frozen=True)
class PageOutcome:
    source: Source
    result: FetchResult
    change: Change | None
    advanced: bool
    broken: bool = False
    """The health check called this page an error or a collapse, whatever its
    hash says. A broken page is never stored as a snapshot.

    Kept separate from `change.changed` because the two answer different
    questions and a bot wall answers them oppositely: an Imperva block carries a
    fresh incident ID on every request, so it is changed every run and is never
    new content. Downstream stages read this, not the hash, when deciding
    whether a page is worth extracting or notifying on. SPEC.md section 3.1.
    """


BROKEN_CHECKS = frozenset({"error_signature", "content_collapse"})
"""The checks that say the fetched bytes are not the page. `watch_without_deadline`
is deliberately absent: a page with no deadline is still real content."""


@dataclass
class FetchRun:
    pages: list[PageOutcome] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


def _fetch_order(sources: list[Source]) -> list[Source]:
    """Watch first, then discover; stable within each role."""
    return [s for s in sources if s.is_watch] + [s for s in sources if s.is_discover]


def _fetch_contained(fetcher: Fetcher, source: Source) -> FetchResult:
    """One source's fetch, with any exception turned into a failed result.

    The fetchers classify the failures they expect. This catches the rest: a
    malformed registry URL, an unknown charset, a link that will not parse. Any
    of them used to end the loop before the skip ledger was saved and before the
    run report was written, so one bad page cost the whole week.

    Only the exception's type survives. Its message can quote the page, or the
    private registry entry that caused it, and a traceback on stderr is a public
    Actions log from P3 (SPEC.md section 5).
    """
    try:
        return fetcher.fetch(source, source.url)
    except Exception as exc:  # noqa: BLE001 - containment is the point, see docstring
        return FetchResult(
            source_id=source.id,
            page_url=source.url,
            markdown=None,
            status="failed",
            fetched_at=datetime.now(UTC),
            reason=type(exc).__name__,
        )


def fetch_all(
    sources: list[Source],
    *,
    fetchers: dict[str, Fetcher],
    repo_root: Path | None = None,
) -> FetchRun:
    """Fetch every source once, in role order, and advance what succeeded."""
    run = FetchRun()
    ledger = SkipLedger.load(repo_root)

    for source in _fetch_order(sources):
        fetcher = fetchers.get(source.fetcher)
        if fetcher is None:
            result = FetchResult(
                source_id=source.id,
                page_url=source.url,
                markdown=None,
                status="skipped",
                fetched_at=datetime.now(UTC),
                reason=f"no {source.fetcher} fetcher configured",
            )
        else:
            result = _fetch_contained(fetcher, source)

        skip_alert = ledger.record(source, result)
        if skip_alert is not None:
            run.alerts.append(skip_alert)

        previous = read_previous(source, source.url, repo_root=repo_root)
        page_alerts = check_page(source, result, previous)
        run.alerts.extend(page_alerts)

        if not result.is_ok or result.markdown is None:
            run.pages.append(PageOutcome(source, result, None, advanced=False))
            continue

        # A page the health check calls broken is stored like a failed fetch:
        # not at all. Ruling R14, SPEC 3.1. Storing it overwrote the last real
        # content, which later stages need once the site recovers, and made the
        # broken page its own baseline, so content_collapse fired once and then
        # compared the wall against itself forever.
        #
        # `changed` stays a truthful statement about the hash and `broken`
        # carries the judgement. They answer different questions: a bot wall
        # regenerates an incident ID on every request, so it is genuinely
        # changed every run and equally genuinely not new content.
        broken = any(a.check in BROKEN_CHECKS for a in page_alerts)
        change = detect_change(previous, result.markdown)
        advanced = change.changed and not broken
        if advanced:
            advance(source, source.url, result.markdown, repo_root=repo_root)

        run.pages.append(PageOutcome(source, result, change, advanced=advanced, broken=broken))

    ledger.save()
    return run


def build_run_report(
    run: FetchRun,
    *,
    started_at: datetime,
    finished_at: datetime,
    repo_root: Path | None = None,
) -> dict:
    """The committed run report. Public sources only. See SPEC.md section 5.

    Private sources are excluded entirely, including from the counts. The
    report already says which public portals changed this week; a line saying
    the private watch list also moved lets an observer correlate the two and
    infer that something on a named portal cleared the profile's blockers.
    """
    public = [p for p in run.pages if not p.source.private]
    public_ids = {p.source.id for p in public}

    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "pages_fetched": sum(1 for p in public if p.result.is_ok),
        "pages_changed": sum(1 for p in public if p.change and p.change.changed),
        "pages_unchanged": sum(1 for p in public if p.change and not p.change.changed),
        "pages_skipped": sum(1 for p in public if p.result.status == "skipped"),
        "pages_failed": sum(1 for p in public if p.result.status == "failed"),
        "alerts": [
            {"source_id": a.source_id, "check": a.check, "detail": a.detail}
            for a in run.alerts
            if a.source_id in public_ids
        ],
        "sources": [
            {
                "id": p.source.id,
                "role": p.source.role,
                "fetcher": p.source.fetcher,
                "status": p.result.status,
                "changed": bool(p.change and p.change.changed),
                "broken": p.broken,
                "chars": len(p.result.markdown or ""),
                "days_since_change": _days_since_change(p, finished_at, repo_root),
                "reason": p.result.reason,
            }
            for p in public
        ],
    }


def _days_since_change(outcome: PageOutcome, now: datetime, repo_root: Path | None) -> int | None:
    """Reported, never alerted on. SPEC 3.1 explains why: scholarship pages
    legitimately go six to twelve months unchanged."""
    path = snapshot_path(outcome.source, outcome.source.url, repo_root=repo_root)
    if not path.exists():
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    return (now - modified).days
