"""Does this page state a deadline? See SPEC.md section 3.1.

A deterministic heuristic, deliberately not an LLM call. It answers one
question for the fetch-stage health check: has a `watch` source stopped
carrying a date? Section 3.1 makes that condition an alert, because a watch
source that stops yielding a deadline is broken by definition.

The patterns came from `scripts/probe_sources.py`, which the source registry
was rebuilt against in September 2026. They live here now so the probe and the
health check cannot drift into disagreeing about what a deadline looks like.
Precision matters more than recall: a false positive keeps a broken page quiet,
which is the failure this check exists to prevent.
"""

from __future__ import annotations

import re

DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|\d{4}-\d{2}-\d{2})\b",
    re.I,
)

DEADLINE_PATTERN = re.compile(
    r"\b(?:deadline|closing date|applications? close|apply by|due date"
    r"|frist|bewerbungsschluss)\b",
    re.I,
)


def find_dates(text: str) -> list[str]:
    """Every date-shaped string, verbatim, in order of appearance."""
    return DATE_PATTERN.findall(text)


def find_deadline_keywords(text: str) -> list[str]:
    """Every deadline-signalling word, verbatim."""
    return DEADLINE_PATTERN.findall(text)


def has_deadline(text: str) -> bool:
    """True when the text carries both a date and a word that frames it.

    Both are required. A date alone is usually a news timestamp: the PKU page
    dropped from the registry carried twelve dates and no deadline keyword. A
    keyword alone is usually a page saying deadlines exist elsewhere, which is
    exactly what the MEXT and Fulbright portals say.
    """
    return bool(find_dates(text) and find_deadline_keywords(text))
