from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SPACE_RE = re.compile(r"\s+")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value.strip()).casefold()


def normalize_hostname(value: str) -> str:
    host = value.rstrip(".").casefold()
    return host.removeprefix("www.")


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return bool(address.is_global)


def normalize_public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("website must use http or https")
    if not parsed.hostname:
        raise ValueError("website must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("website must not contain credentials")
    host = normalize_hostname(parsed.hostname)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("website must use a public hostname")
    if not is_public_ip(host):
        raise ValueError("website must use a public address")
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{host}{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, parsed.fragment))


class EmployeeBand(StrEnum):
    ONE_TO_TEN = "1-10"
    ELEVEN_TO_FIFTY = "11-50"
    FIFTY_ONE_TO_TWO_HUNDRED = "51-200"
    TWO_HUNDRED_ONE_PLUS = "201+"
    UNKNOWN = "unknown"


class ClaimSupport(StrEnum):
    SELF_REPORTED = "self_reported"
    DIRECT = "direct"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class CloudCategory(StrEnum):
    MINIMAL = "minimal"
    DIGITAL_PRODUCT = "digital_product"
    PRODUCTION_CLOUD = "production_cloud"
    SUBSTANTIAL_CLOUD = "substantial_cloud"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class CompetitorBasis(StrEnum):
    LIKELY_ALIAS = "likely_alias"
    PLAUSIBLE_PARTIAL = "plausible_partial"
    DISTINCT_ENTITY = "distinct_entity"
    NO_INDICATION = "no_indication"
    INSUFFICIENT_IDENTITY = "insufficient_identity"


class CompetitiveOverlap(StrEnum):
    COMPETING_SERVICE = "competing_service"
    ADJACENT_SERVICE = "adjacent_service"
    NO_OVERLAP = "no_overlap"
    UNKNOWN = "unknown"


class JurisdictionRole(StrEnum):
    REGISTERED_JURISDICTION = "registered_jurisdiction"
    CONTRACTING_ENTITY = "contracting_entity"
    ULTIMATE_PARENT = "ultimate_parent"
    OPERATIONAL_HEADQUARTERS = "operational_headquarters"
    OFFICE = "office"


class ComplianceOutcome(StrEnum):
    CLEAR = "clear"
    REVIEW_REQUIRED = "review_required"
    FLAGGED = "flagged"


class FinalStatus(StrEnum):
    DO_NOT_ENGAGE = "do_not_engage"
    REVIEW_REQUIRED = "review_required"
    SALES_READY = "sales_ready"
    LOWER_PRIORITY = "lower_priority"


class NotificationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"


class LeadSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    company_name: str = Field(min_length=1)
    website: str
    employee_count_band: EmployeeBand

    @field_validator("name", "company_name")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL_RE.fullmatch(value):
            raise ValueError("email has invalid syntax")
        return value

    @field_validator("website")
    @classmethod
    def valid_website(cls, value: str) -> str:
        normalize_public_url(value)
        return value.strip()

    @property
    def normalized_email(self) -> str:
        return self.email.casefold()

    @property
    def hostname(self) -> str:
        hostname = urlsplit(self.website).hostname
        assert hostname is not None
        return normalize_hostname(hostname)

    @property
    def lead_id(self) -> str:
        identity = "\n".join((self.normalized_email, normalize_text(self.company_name), self.hostname))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def normalized_state(self) -> dict[str, Any]:
        return {
            "name": normalize_text(self.name),
            "email": self.normalized_email,
            "company_name": normalize_text(self.company_name),
            "website_hostname": self.hostname,
            "employee_count_band": self.employee_count_band,
        }


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_type: Literal["page", "search_snippet", "submitted_field"]
    source_url: str | None = None
    excerpt: str
    retrieved_at: datetime = Field(default_factory=utc_now)
    company_association: str
    truncated: bool = False
    synthetic: bool = False
    search_query: str | None = None
    result_title: str | None = None
    result_position: int | None = None


class CertificateContext(BaseModel):
    apex: str
    hostnames: list[str] = Field(default_factory=list)
    malformed_count: int = 0
    wildcard_count: int = 0
    duplicate_count: int = 0
    out_of_scope_count: int = 0
    omitted_count: int = 0
    response_truncated: bool = False
    unavailable: bool = False
    warning: str | None = None


class ResearchAttempt(BaseModel):
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    certificate_context: CertificateContext
    queries: list[str] = Field(default_factory=list)
    page_requests: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    synthetic: bool = False
    follow_up_failed: bool = False


class MaterialClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    support_type: ClaimSupport
    evidence_ids: list[str]


class JurisdictionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_name: str
    country: str
    role: JurisdictionRole
    support_type: ClaimSupport
    evidence_ids: list[str]

    @field_validator("entity_name")
    @classmethod
    def nonempty_entity_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entity_name must not be blank")
        return value.strip()

    @field_validator("country")
    @classmethod
    def valid_country(cls, value: str) -> str:
        if value != "unknown" and not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("country must be an ISO two-letter code or unknown")
        return value


class SummarySentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: MaterialClaim
    observed_aliases: list[str]
    business_description: MaterialClaim
    jurisdictions: list[JurisdictionFact]
    employee_count_band: MaterialClaim
    cloud_signals: list[MaterialClaim]
    proposed_cloud_category: MaterialClaim
    competitive_overlap: MaterialClaim
    missing_facts: list[str]
    conflicts: list[str]
    sales_summary: list[SummarySentence]

    @model_validator(mode="after")
    def allowed_values(self) -> CompanyProfile:
        if self.employee_count_band.value not in {item.value for item in EmployeeBand}:
            raise ValueError("invalid employee_count_band value")
        if self.proposed_cloud_category.value not in {item.value for item in CloudCategory}:
            raise ValueError("invalid proposed_cloud_category value")
        if self.competitive_overlap.value not in {item.value for item in CompetitiveOverlap}:
            raise ValueError("invalid competitive_overlap value")
        return self

    def material_claims(self) -> dict[str, MaterialClaim | JurisdictionFact]:
        claims = {
            "identity": self.identity,
            "business_description": self.business_description,
            "employee_count_band": self.employee_count_band,
            "proposed_cloud_category": self.proposed_cloud_category,
            "competitive_overlap": self.competitive_overlap,
        }
        claims.update({f"jurisdiction_{index}": claim for index, claim in enumerate(self.jurisdictions)})
        claims.update({f"cloud_signal_{index}": claim for index, claim in enumerate(self.cloud_signals)})
        return claims


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_distribution(self) -> ChoiceAnswer:
        if not self.probabilities:
            raise ValueError("choice probabilities must not be empty")
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in self.probabilities.values()):
            raise ValueError("choice probabilities must be finite values from 0 to 1")
        if abs(sum(self.probabilities.values()) - 1) > 0.01:
            raise ValueError("choice probabilities must sum to 1")
        if self.choice not in self.probabilities:
            raise ValueError("selected choice is absent from probabilities")
        highest = max(self.probabilities.values())
        if self.probabilities[self.choice] < highest:
            raise ValueError("selected choice is not a highest-probability option")
        return self


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(ge=0)
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    legend: dict[str, Any]

    @model_validator(mode="after")
    def valid_distribution(self) -> ScoreAnswer:
        if not self.probabilities:
            raise ValueError("score probabilities must not be empty")
        expected_keys = {str(index) for index in range(len(self.probabilities))}
        if set(self.probabilities) != expected_keys or set(self.legend) != expected_keys:
            raise ValueError("score probabilities and legend must use consecutive level keys")
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in self.probabilities.values()):
            raise ValueError("score probabilities must be finite values from 0 to 1")
        if abs(sum(self.probabilities.values()) - 1) > 0.01:
            raise ValueError("score probabilities must sum to 1")
        weighted_score = sum(int(level) * probability for level, probability in self.probabilities.items())
        if abs(self.score - weighted_score) > 0.02:
            raise ValueError("score must equal the probability-weighted level position")
        return self


class JevAssessment(BaseModel):
    requested_model: str = "jev-latest"
    resolved_model: str
    raw_answers: dict[str, ChoiceAnswer | ScoreAnswer]
    claim_support: dict[str, ClaimVerdict]
    claim_confidence: dict[str, float]
    employee_count_category: EmployeeBand | Literal["conflicting"]
    employee_count_confidence: float
    cloud_category: CloudCategory
    cloud_confidence: float
    competitor_basis: dict[str, CompetitorBasis]
    competitor_evidence: dict[str, str]
    competitor_confidence: dict[str, float]
    unlisted_competitor_score: float
    unlisted_competitor_probabilities: dict[str, float]
    unlisted_competitor_confidence: float
    compliance_follow_up: str
    follow_up_confidence: float
    summary_support: list[ClaimVerdict]
    summary_confidence: list[float]
    validation_errors: list[str] = Field(default_factory=list)


class FitResult(BaseModel):
    employee_points: int | None
    cloud_points: int | None
    score: int | None
    score_status: Literal["complete", "provisional"]
    rationale: str


class ComplianceCheck(BaseModel):
    kind: Literal["competitor", "country", "assessment"]
    policy_entry: str
    outcome: ComplianceOutcome
    reason: str
    evidence_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class FinalResult(BaseModel):
    lead_id: str
    submission: LeadSubmission
    original_submission: dict[str, Any] = Field(default_factory=dict)
    accepted_profile: CompanyProfile | None
    fit: FitResult
    compliance_checks: list[ComplianceCheck]
    compliance_outcome: ComplianceOutcome
    final_status: FinalStatus
    summary: str
    evidence_references: list[str]
    policy_version: str
    openai_requested_model: str | None = None
    openai_returned_model: str | None = None
    jev_requested_model: str | None = None
    jev_resolved_model: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    synthetic: bool = False
    processed_at: datetime = Field(default_factory=utc_now)
    processing_seconds: float = 0
    notification_status: NotificationStatus = NotificationStatus.NOT_REQUESTED


class DeliveryAttempt(BaseModel):
    attempted_at: datetime = Field(default_factory=utc_now)
    status: NotificationStatus
    acknowledgment: str | None = None
    error: str | None = None
    duplicate_possible: bool = False


class DeliveryRecord(BaseModel):
    lead_id: str
    message_sha256: str
    status: NotificationStatus = NotificationStatus.NOT_REQUESTED
    sent_at: datetime | None = None
    message_identifier: str | None = None
    acknowledgment: str | None = None
    attempts: list[DeliveryAttempt] = Field(default_factory=list)
