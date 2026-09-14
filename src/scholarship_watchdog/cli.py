"""Manual entry point. `scholarship-watchdog fetch`.

P1 ships the fetch stage alone, so this runs one pass over the registry, writes
snapshots, writes a public run report and prints a summary. The Actions
workflow that calls it on a weekly cron arrives in P3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import ConfigError, DuplicateSourceIdError, load_sources
from .fetch import FetchRun, build_run_report, fetch_all
from .fetch.firecrawl_fetcher import FirecrawlFetcher
from .fetch.httpx_fetcher import HttpxFetcher
from .paths import run_report_path


def _private_ids(run: FetchRun) -> set[str]:
    return {p.source.id for p in run.pages if p.source.private}


def render_summary(run: FetchRun) -> list[str]:
    """One line per public page. Private sources are omitted entirely.

    Stdout is a public artifact from P3 onward: the Actions log of a public
    repository is readable by anyone. Withholding the id is not enough, because
    the line itself is the signal. One line per private source publishes how
    many private sources exist, and a reader comparing two weekly runs learns
    the week the auto-grown watch list gained an entry, plus each private page's
    role and whether it changed. SPEC.md section 5 settled this argument once
    already, for the GitHub Issues notifier: wording the body carefully does not
    help when the existence of the entry is the signal.

    The private side reports through the email digest in P3, which is the
    channel SPEC.md section 5 designates for it.
    """
    lines = []
    for outcome in run.pages:
        if outcome.source.private:
            continue
        state = outcome.result.status
        if outcome.change is not None:
            state = "changed" if outcome.change.changed else "unchanged"
        lines.append(f"{outcome.source.id:<32} {outcome.source.role:<9} {state}")
    return lines


def render_alerts(run: FetchRun) -> list[str]:
    """Alert lines for public sources only, for the same reason.

    The detail text is written by this project, but it is not content-free: a
    content_collapse detail carries character counts of the page it fired on,
    and an error_signature detail names the pattern a private page matched. A
    redacted id in front of that still reports that some private page broke this
    week, which is itself profile-shaped.
    """
    private = _private_ids(run)
    return [
        f"ALERT {a.source_id}: {a.check} - {a.detail}"
        for a in run.alerts
        if a.source_id not in private
    ]


def _build_fetchers() -> dict[str, object]:
    """Firecrawl is built with whatever key is present, including none.

    The fetcher itself decides to skip when the key is absent, so the run still
    reports the source as skipped rather than as missing a fetcher, and the
    consecutive-skip counter still sees it.
    """
    return {
        "httpx": HttpxFetcher(),
        "firecrawl": FirecrawlFetcher(api_key=os.environ.get("FIRECRAWL_API_KEY")),
    }


def _fetch(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    try:
        sources = load_sources(repo_root / "config")
    except (ConfigError, DuplicateSourceIdError) as exc:
        # Both are built to be safe to print; nothing else from the loader is.
        # See config.ConfigError for why the exception chain is suppressed.
        print(f"config: {exc}", file=sys.stderr)
        return 2
    if args.only:
        sources = [s for s in sources if s.id in set(args.only)]
        if not sources:
            print(f"no source matched {args.only}", file=sys.stderr)
            return 2

    started = datetime.now(UTC)
    run = fetch_all(sources, fetchers=_build_fetchers(), repo_root=repo_root)
    finished = datetime.now(UTC)

    report = build_run_report(run, started_at=started, finished_at=finished, repo_root=repo_root)
    path = run_report_path(finished, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    for line in render_summary(run):
        print(line)
    for line in render_alerts(run):
        print(line, file=sys.stderr)

    print(f"\nrun report: {path}")
    return 1 if run.alerts else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scholarship-watchdog")
    parser.add_argument("--repo-root", default=".", help="checkout root; default cwd")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_cmd = sub.add_parser("fetch", help="fetch every registered page once")
    fetch_cmd.add_argument("--only", nargs="+", metavar="SOURCE_ID")
    fetch_cmd.set_defaults(handler=_fetch)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
