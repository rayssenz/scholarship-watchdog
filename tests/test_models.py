from datetime import date

import pytest
from pydantic import ValidationError

from scholarship_watchdog.models import Profile, ScholarshipRecord, Source, safe_errors


def _minimal_record(**overrides: object) -> dict:
    base = {"program": "DAAD Study Scholarship", "source_url": "https://example.org/a"}
    return base | overrides


def test_a_record_needs_only_a_program_and_a_source_url():
    """Every other field is optional-typed: absence is recorded as absence."""
    record = ScholarshipRecord(**_minimal_record())
    assert record.program == "DAAD Study Scholarship"
    assert record.deadline is None
    assert record.institution is None


def test_a_record_without_a_program_is_rejected():
    """program is the identity field, so it cannot be absent."""
    with pytest.raises(ValidationError):
        ScholarshipRecord(source_url="https://example.org/a")


def test_a_record_with_an_empty_program_is_rejected():
    """SPEC 3.2: program is the only model-produced component of the identity
    hash. An empty string would hash to a real, stable identity, silently
    occupying an identity slot at Task 8's dual hashing."""
    with pytest.raises(ValidationError):
        ScholarshipRecord(**_minimal_record(program=""))


def test_an_unknown_field_is_rejected():
    """SPEC 3.2: a prompt change inventing a field must fail, not be dropped.

    Pydantic's default is to ignore extras, which would let a model emit
    `application_fee` and have it vanish with no error anywhere.
    """
    with pytest.raises(ValidationError):
        ScholarshipRecord(**_minimal_record(application_fee="200 EUR"))


def test_a_parsed_deadline_requires_the_raw_string_it_came_from():
    """SPEC 3.2: deadline_raw preserves the original so a bad parse is auditable.

    A record with deadline=2027-03-04 and no raw string cannot be checked for
    the DD.MM transposition that section 3.2 calls the highest-risk failure.
    """
    with pytest.raises(ValidationError, match="deadline_raw"):
        ScholarshipRecord(**_minimal_record(deadline=date(2027, 3, 4)))


def test_a_whitespace_only_raw_string_does_not_satisfy_the_audit_guard():
    """A raw string of spaces satisfies `not deadline_raw` without carrying
    anything to audit, which defeats the whole point of the field."""
    with pytest.raises(ValidationError, match="deadline_raw"):
        ScholarshipRecord(**_minimal_record(deadline=date(2027, 3, 4), deadline_raw="   "))


def test_a_deadline_with_its_raw_string_is_accepted():
    record = ScholarshipRecord(
        **_minimal_record(deadline=date(2027, 3, 4), deadline_raw="04.03.2027")
    )
    assert record.deadline_raw == "04.03.2027"


def test_a_raw_string_with_no_parsed_deadline_is_accepted():
    """The fuzzy-date case: 'mid-October' is preserved and never parsed."""
    record = ScholarshipRecord(**_minimal_record(deadline_raw="mid-October"))
    assert record.deadline is None


def test_an_invalid_degree_level_is_rejected():
    with pytest.raises(ValidationError):
        ScholarshipRecord(**_minimal_record(degree_level="highschool"))


def test_a_source_defaults_to_httpx_annual_and_public():
    source = Source(
        id="daad-study-scholarship",
        name="DAAD Study Scholarship",
        role="watch",
        url="https://example.org/daad",
    )
    assert source.fetcher == "httpx"
    assert source.recurs == "annual"
    assert source.private is False
    assert source.is_watch is True
    assert source.is_discover is False


def test_a_source_country_may_be_null():
    """SPEC 3.2: a multi-country portal must not inherit one country's date
    convention. Fulbright's international site links ~160 commission pages."""
    source = Source(
        id="fulbright-foreign-student",
        name="Fulbright Foreign Student Program",
        role="discover",
        url="https://example.org/fulbright",
        country=None,
    )
    assert source.country is None


def test_an_invalid_role_is_rejected():
    with pytest.raises(ValidationError):
        Source(id="x", name="X", role="crawl", url="https://example.org/x")


def test_a_profile_never_renders_its_contents():
    """A profile in a traceback or an assertion diff is the unrecoverable leak."""
    profile = Profile(
        citizenship="XX",
        residency="XX",
        date_of_birth=date(1990, 1, 1),
        degree={"held": "bachelor", "seeking": "master"},
    )
    assert repr(profile) == "Profile(<redacted>)"
    assert "XX" not in f"{profile}"
    assert "1990" not in f"{profile}"


def test_a_rejected_profile_value_is_not_echoed_in_the_error():
    """The redacted __repr__ does not cover Pydantic's own error messages.

    A ValidationError renders the input it rejected by default, so a malformed
    profile leaks the value through a traceback rather than through a log line
    someone wrote. In P3 that traceback is a public Actions log.

    `hide_input_in_errors` only covers the string form. `exc.errors()` and
    `exc.json()` are a separate leak path that the same flag does not close;
    this pins that boundary as a tested fact so a pydantic release that
    changed it would fail this suite rather than silently changing exposure.
    `safe_errors()` is the accessor that closes it.
    """
    with pytest.raises(ValidationError) as caught:
        Profile(
            citizenship="XX",
            residency="XX",
            date_of_birth="not-a-date",
            degree={"held": "bachelor", "seeking": "master"},
        )
    exc = caught.value
    assert "not-a-date" not in str(exc)
    assert "not-a-date" in str(exc.errors())
    assert "not-a-date" in exc.json()
    assert "not-a-date" not in str(safe_errors(exc))


def test_a_rejected_source_value_is_not_echoed_in_the_error():
    """The Source-side equivalent: sources.local.yaml is private config too,
    and there was previously no test covering this leak path for Source."""
    with pytest.raises(ValidationError) as caught:
        Source(
            id="x",
            name="X",
            role="watch",
            url="https://example.org/x",
            rate_limit_seconds="ZZ-SECRET-COMMISSION",
        )
    exc = caught.value
    assert "ZZ-SECRET-COMMISSION" not in str(exc)
    assert "ZZ-SECRET-COMMISSION" in str(exc.errors())
    assert "ZZ-SECRET-COMMISSION" in exc.json()
    assert "ZZ-SECRET-COMMISSION" not in str(safe_errors(exc))


@pytest.mark.parametrize(
    "url",
    [
        "http://[broken/p",
        "ftp://example.org/x",
        "file:///etc/passwd",
        "example.org/no-scheme",
        "https:///no-host",
    ],
)
def test_a_source_url_must_be_an_http_url_with_a_host(url):
    with pytest.raises(ValidationError):
        Source(id="s", name="S", role="watch", url=url)
