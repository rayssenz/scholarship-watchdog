"""The schemas every stage agrees on. See SPEC.md section 3.2.

Every extracted field except `program` is optional-typed. That is deliberate:
absence is recorded as absence, because a hallucinated deadline is worse than a
missing one. Section 3.3 defines what each absence means to scoring, so the
optionality does not silently push the decision into whatever the code happens
to do.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Role = Literal["watch", "discover"]
FetcherName = Literal["httpx", "firecrawl"]
Recurs = Literal["annual", "one-off"]
DegreeLevel = Literal["bachelor", "master", "phd", "postdoc"]
FundingType = Literal["full", "partial", "tuition-only", "none"]
ApplicationRoute = Literal["direct", "agency", "embassy"]
StipendPeriod = Literal["month", "year", "total"]


class Strict(BaseModel):
    """Reject unknown fields, and keep rejected values out of `str()`/`repr()`.

    `extra="forbid"` means a model that invents a field, or a config file with
    a typo in a key, fails at the boundary instead of being silently discarded.

    `hide_input_in_errors` closes a leak the redacted `Profile.__repr__` below
    does not reach: Pydantic's default ValidationError *string* message embeds
    the offending input, so one malformed line in `profile.yaml` or
    `sources.local.yaml` would otherwise produce a traceback containing a
    citizenship code or a commission URL. In P3 that traceback lands in a
    GitHub Actions log, which is public on a public repository.

    This flag only covers the string form (`str(exc)`, `repr(exc)`). The
    *structured* form still carries the raw input by default: both
    `exc.errors()` and `exc.json()` embed it regardless of this setting. Any
    code that reports a ValidationError over `Profile` or `Source` input must
    call `safe_errors(exc)` below instead of `exc.errors()` directly.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def safe_errors(exc: ValidationError) -> list[dict]:
    """The structured error list, with the rejected input stripped out.

    `hide_input_in_errors` on `Strict` only suppresses the input from the
    *string* rendering of a ValidationError; `exc.errors()` and `exc.json()`
    both embed the raw value that failed validation regardless of that
    setting. For `Profile` and `Source`, that value can be a citizenship code
    or a private commission URL, so any code that reports a validation
    failure over their input (Task 3's config loader, for one) must call this
    instead of `exc.errors()` to avoid putting it in a log or a public Actions
    run.
    """
    return exc.errors(include_input=False)


class Stipend(Strict):
    amount: float | None = None
    currency: str | None = None
    period: StipendPeriod | None = None


class LanguageCertificate(Strict):
    certificate: str | None = None
    minimum_score: float | None = None


class NationalityRestrictions(Strict):
    eligible: list[str] | None = None
    excluded: list[str] | None = None


class ScholarshipRecord(Strict):
    """One normalized opportunity, as extracted from one page."""

    program: str = Field(
        min_length=1,
        description=(
            "The only model-produced component of the identity hash (SPEC "
            "3.2). An empty program would hash to a real, stable identity, "
            "silently occupying an identity slot."
        ),
    )
    institution: str | None = None
    degree_level: DegreeLevel | None = None
    language_of_instruction: str | None = None
    funding_type: FundingType | None = None
    stipend: Stipend | None = None
    deadline: date | None = None
    deadline_raw: str | None = None
    nationality_restrictions: NationalityRestrictions | None = None
    language_certificate: LanguageCertificate | None = None
    experience_requirement: str | None = None
    application_route: ApplicationRoute | None = None
    source_url: str
    candidate_url: str | None = None

    @model_validator(mode="after")
    def _a_parsed_deadline_keeps_its_source_string(self) -> Self:
        if self.deadline is not None and not (self.deadline_raw or "").strip():
            raise ValueError(
                "deadline_raw is required whenever deadline is set: without the "
                "original string a DD.MM transposition is invisible"
            )
        return self


class Source(Strict):
    """One registered page and what the run is allowed to demand of it."""

    id: str
    name: str
    role: Role
    url: str
    country: str | None = None
    institution: str | None = None
    fetcher: FetcherName = "httpx"
    recurs: Recurs = "annual"
    rate_limit_seconds: float = 2.0
    candidate_pattern: str | None = None
    verified: str | None = None
    private: bool = Field(
        default=False,
        description=(
            "True when this source came from a gitignored config file. Every "
            "artifact produced from it is private; see SPEC.md section 5."
        ),
    )

    @field_validator("url")
    @classmethod
    def _http_url_with_a_host(cls, url: str) -> str:
        """Reject a URL at load time rather than mid-run.

        A URL that will not parse used to crash the run when its snapshot path
        was built, long after loading, and outside any handler. Refusing non-http
        schemes also keeps `file://` and friends out of the fetchers, which
        matters from P4, when promoted URLs come from pages the project scraped.
        """
        try:
            url.encode("utf-8")
            parts = urlsplit(url)
        except ValueError:
            # UnicodeEncodeError is a ValueError. A lone surrogate passed the
            # check below and crashed the run later, hashing the snapshot path.
            raise ValueError("url does not parse as text") from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be http or https and name a host")
        return url

    @property
    def is_watch(self) -> bool:
        return self.role == "watch"

    @property
    def is_discover(self) -> bool:
        return self.role == "discover"


class DegreeProfile(Strict):
    held: DegreeLevel
    seeking: DegreeLevel


class LanguageSkill(Strict):
    code: str
    certificate: str | None = None
    score: float | None = None


class Blockers(Strict):
    degree_level_mismatch: bool = True
    nationality_excluded: bool = True


class Weights(Strict):
    funding_full: int = 30
    funding_partial: int = 10
    stipend_above_threshold: int = 15
    stipend_threshold_eur_month: int = 800
    language_match: int = 15
    no_certificate_gap: int = 10
    application_route_direct: int = 5


class Tiers(Strict):
    act_now: int = 70
    shortlist: int = 40


class Profile(Strict):
    """The private eligibility profile. Loaded, never logged, never committed."""

    citizenship: str
    residency: str
    date_of_birth: date
    degree: DegreeProfile
    languages: list[LanguageSkill] = Field(default_factory=list)
    experience_years: int = 0
    blockers: Blockers = Field(default_factory=Blockers)
    weights: Weights = Field(default_factory=Weights)
    tiers: Tiers = Field(default_factory=Tiers)
    promotion_min_fit: int = 55

    def __repr__(self) -> str:
        """Never render profile contents.

        A profile in a traceback, a log line or a pytest assertion diff is the
        leak this project exists to avoid. Section 5 calls it the one
        unrecoverable mistake.
        """
        return "Profile(<redacted>)"

    __str__ = __repr__
