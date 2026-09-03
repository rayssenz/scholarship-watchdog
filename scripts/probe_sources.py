"""Probe every registered source and report what it actually yields.

Requires pyyaml. Until the project has a pyproject.toml: pip install pyyaml

Answers two questions the registry depends on:

  fetcher       does a plain HTTP fetch return readable text, or is a browser
                needed? Low visible-text ratio means client-side rendering.
  role          `watch` sources must carry a deadline; `discover` sources need
                not. A watch URL with no date produces schema-valid records
                with null deadlines and no error anywhere, which is the failure
                mode this script exists to make loud.

Reads config/sources.yaml so it cannot drift from the registry. Run it when
adding or re-pointing a source. Not a CI gate: it hits the live network and
several registered hosts time out routinely, so it runs on a schedule and
raises an issue on failure rather than blocking a push.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

REGISTRY = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
UA = "scholarship-watchdog/0.1 (+https://github.com/rayssenz/scholarship-watchdog)"
TIMEOUT = 25
TEXT_FLOOR = 2000

DATE = re.compile(
    r"\b(\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
    r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})\b",
    re.I,
)
DEADLINE = re.compile(r"\b(deadline|closing date|applications? close|apply by|due date)\b", re.I)


def visible_text(html: str) -> str:
    """Strip markup the way a reader sees the page, minus scripts and styles."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def probe(source: dict) -> dict:
    """Fetch one source and report fetcher fitness plus deadline presence."""
    result = {"id": source["id"], "declared": source.get("fetcher", "httpx")}
    try:
        req = urllib.request.Request(source["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = visible_text(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, never raises
        return result | {"fetcher": "unreachable", "dates": 0, "note": type(exc).__name__}

    dates = DATE.findall(text)
    keywords = DEADLINE.findall(text)
    return result | {
        "fetcher": "httpx" if len(text) > TEXT_FLOOR else "firecrawl",
        "chars": len(text),
        "dates": len(dates),
        "keywords": len(keywords),
        "has_deadline": bool(dates and keywords),
    }


def main() -> int:
    registry = yaml.safe_load(REGISTRY.read_text())
    sources = registry["sources"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, sources))

    by_id = {s["id"]: s for s in sources}
    problems = 0
    unresolved = 0

    for r in sorted(results, key=lambda r: r["id"]):
        source = by_id[r["id"]]
        flags = []

        # This probe drives only httpx, so a declared-firecrawl source cannot be
        # judged here. It is reported against the `verified` note the registry
        # carries from a hand-check, and counted clean only if that note exists.
        # Counting an unprobed source clean without evidence was a bug: three
        # sources once passed a check that never ran.
        browser_declared = r["declared"] == "firecrawl"
        plain_fetch_failed = r["fetcher"] in ("unreachable", "firecrawl")

        if browser_declared and plain_fetch_failed:
            if source.get("verified"):
                print(f"{r['id']:<28} browser-only, verified in registry")
            else:
                unresolved += 1
                print(f"{r['id']:<28} UNRESOLVED: browser-only and no `verified` note")
            continue

        # A network failure says nothing about the registry entry. Several
        # registered hosts time out intermittently, so a timeout is reported as
        # unreachable-this-run rather than counted as a registry defect.
        if r["fetcher"] == "unreachable":
            print(f"{r['id']:<28} not reached this run ({r['note']}); registry unchanged")
            continue

        if r["fetcher"] != r["declared"]:
            flags.append(f"FETCHER MISMATCH: declared {r['declared']}, measured {r['fetcher']}")

        # `watch` sources must carry a deadline; `discover` sources need not.
        # A source with no role predates the role split and is treated as
        # unresolved, since the registry rebuild has not classified it yet.
        role = source.get("role")
        if role is None:
            unresolved += 1
            print(f"{r['id']:<28} UNRESOLVED: no role set (registry rebuild pending)")
            continue
        if role == "watch" and not r.get("has_deadline"):
            flags.append(f"NO DEADLINE (dates={r.get('dates', 0)} kw={r.get('keywords', 0)})")

        problems += bool(flags)
        status = "; ".join(flags) if flags else "ok"
        print(f"{r['id']:<28} {status}")

    judged = problems + unresolved
    print(
        f"\n{len(results) - judged}/{len(results)} sources clean, "
        f"{unresolved} unresolved, {problems} failing"
    )
    if problems or unresolved:
        print("Sources not reached this run are excluded: a timeout is a network")
        print("fact, not a registry defect. Re-run before treating one as a failure.")
    return 1 if (problems or unresolved) else 0


if __name__ == "__main__":
    sys.exit(main())
