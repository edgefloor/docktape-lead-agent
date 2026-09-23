from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


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
