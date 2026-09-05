"""Reduce HTML to the text a reader sees. See SPEC.md section 3.1.

Cleaning is what makes change detection meaningful. Raw HTML carries render
timestamps, session tokens, visitor counters and rotating banners, all of which
change on every fetch without the content changing. Hashing post-extraction
text removes that noise, so a detected change is far more likely to be real.

`keep_links` is the discover-role case. Default cleaning discards href
attributes along with the navigation, which would leave the extractor reading
programme names as text with nothing to follow, and promotion would have no
candidate_url to verify.
"""

from __future__ import annotations

import trafilatura


def clean(html: str, *, keep_links: bool) -> str:
    """Return clean markdown, or an empty string when there is no content."""
    if not html.strip():
        return ""
    extracted = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=keep_links,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    return (extracted or "").strip()
