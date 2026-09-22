"""Make two URLs for the same page compare equal. See SPEC.md section 3.1.

Two jobs, both load-bearing:

  change detection   a discover page's markdown carries its hrefs, so a portal
                     that rotates utm parameters would hash differently every
                     week and buy an extraction call for content that never
                     moved. Canonicalising before hashing is the cost control.

  candidate identity P4 keys verification state on the canonical URL, so the
                     same programme reached through two tracked links must
                     resolve to one candidate rather than two.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
    }
)

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")


def canonical_url(url: str, *, base: str | None = None) -> str:
    """Resolve, lowercase the host, drop the fragment and tracking noise."""
    absolute = urljoin(base, url) if base else url
    parts = urlsplit(absolute)

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(kept))

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def canonicalise_markdown_links(markdown: str, *, base: str) -> str:
    """Rewrite every markdown link target in place, leaving link text alone."""

    def replace(match: re.Match[str]) -> str:
        text, target, _title = match.groups()
        try:
            return f"[{text}]({canonical_url(target, base=base)})"
        except ValueError:
            # One malformed href (`http://[broken/x`) is left as written. Raised
            # from here it failed the whole page, every good link with it.
            return match.group(0)

    return _MARKDOWN_LINK.sub(replace, markdown)
