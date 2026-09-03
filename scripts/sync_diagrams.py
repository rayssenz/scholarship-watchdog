"""Copy diagram sources into the documents that embed them.

The `.mmd` files under diagrams/ are the single source of truth. SPEC.md embeds
the same mermaid inline so it renders natively on GitHub, which means the text
lives in two places and drifts the moment one is edited alone. README.md embeds
the rendered SVGs instead, so it displays in any viewer and cannot drift textually.

This script does two things, in the order listed in DIAGRAMS. It rewrites the
fenced mermaid blocks in SPEC.md from the `.mmd` files, and it gives each
rendered SVG a white page background so README's <img> embeds stay readable on
GitHub dark mode. Both are idempotent. Run it after regenerating a diagram, and
in CI with --check to fail a build where either has drifted.

    python scripts/sync_diagrams.py            # rewrite the documents
    python scripts/sync_diagrams.py --check    # report drift, change nothing
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Order matters: it maps positionally onto the mermaid blocks in each document.
DIAGRAMS = ["weekly-run", "finding-new-scholarships"]
DOCUMENTS = ["SPEC.md"]

FENCE = re.compile(r"```mermaid\n.*?\n```", re.S)


def sources() -> list[str]:
    return [(ROOT / "diagrams" / f"{name}.mmd").read_text().rstrip() for name in DIAGRAMS]


def sync(doc: Path, srcs: list[str], check: bool) -> bool:
    """Rewrite doc's mermaid blocks. Returns True when doc is already in sync."""
    text = doc.read_text()
    blocks = FENCE.findall(text)

    if len(blocks) != len(srcs):
        print(f"{doc.name}: has {len(blocks)} mermaid blocks, expected {len(srcs)}")
        return False

    updated = text
    for block, src in zip(blocks, srcs):
        updated = updated.replace(block, f"```mermaid\n{src}\n```")

    if updated == text:
        print(f"{doc.name}: in sync")
        return True

    if check:
        print(f"{doc.name}: DRIFTED from diagrams/*.mmd")
        return False

    doc.write_text(updated)
    print(f"{doc.name}: updated")
    return True


SVG_OPEN = re.compile(r"<svg\b[^>]*>")
VIEWBOX = re.compile(r'viewBox="0 0 ([\d.]+) ([\d.]+)"')
BG_MARK = 'data-purpose="page-background"'
OLD_BG = re.compile(r'<rect[^>]*data-purpose="page-background"[^>]*/>')


def prepare_svg(svg: Path, check: bool) -> bool:
    """Make the rendered SVG embeddable as an <img> and readable on dark pages.

    Two fixes, both required, and both invisible when the SVG is rendered by
    pasting its markup into a page rather than loading it through <img src>.

    Mermaid emits `width="100%"` with no `height` and a `max-width` style. Inside
    a host page that resolves against the parent element. As a standalone
    document behind <img src> there is no parent, so the browser cannot derive an
    intrinsic size and paints nothing. VS Code's preview shows an empty box;
    other viewers fall back to the viewBox and appear to work. Concrete width and
    height from the viewBox fix it everywhere.

    Mermaid also emits a transparent background with #333 text, unreadable on
    GitHub dark mode. A white rect is inserted as the first child so it paints
    beneath every edge and node. It must be the first child and must use viewBox
    units, not percentages: filling mermaid's own background rect instead covers
    the edges, because that rect sits in a group painted after them.
    """
    text = svg.read_text()
    m = SVG_OPEN.search(text)
    vb = VIEWBOX.search(text)
    if not m or not vb:
        print(f"{svg.name}: no <svg> root or viewBox found")
        return False
    w, h = vb.group(1), vb.group(2)

    root = m.group(0)
    fixed = re.sub(r'\s(?:width|height|style)="[^"]*"', "", root)
    fixed = fixed[:-1] + f' width="{w}" height="{h}">'
    bg = f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff" {BG_MARK}/>'

    body = OLD_BG.sub("", text[m.end():], count=1)
    rebuilt = fixed + bg + body

    if rebuilt == text:
        print(f"{svg.name}: embeddable")
        return True
    if check:
        print(f"{svg.name}: NOT embeddable (missing size or background)")
        return False
    svg.write_text(rebuilt)
    print(f"{svg.name}: sized {w}x{h}, background inserted")
    return True


def main() -> int:
    check = "--check" in sys.argv
    srcs = sources()
    results = [sync(ROOT / name, srcs, check) for name in DOCUMENTS]
    results += [prepare_svg(ROOT / "diagrams" / f"{n}.svg", check) for n in DIAGRAMS]

    if not all(results):
        if check:
            print("\nRun `python scripts/sync_diagrams.py` to fix.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
