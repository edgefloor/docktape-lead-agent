from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    return urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, parsed.fragment)
    )


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
        identity = "\n".join(
            (self.normalized_email, normalize_text(self.company_name), self.hostname)
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def normalized_state(self) -> dict[str, Any]:
        return {
            "name": normalize_text(self.name),
            "email": self.normalized_email,
            "company_name": normalize_text(self.company_name),
            "website_hostname": self.hostname,
            "employee_count_band": self.employee_count_band,
        }
