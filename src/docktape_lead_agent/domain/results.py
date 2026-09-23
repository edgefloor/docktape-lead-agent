from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .evidence import utc_now
from .profiles import CompanyProfile
from .submissions import ComplianceOutcome, FinalStatus, LeadSubmission, NotificationStatus


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
    execution_id: str | None = None
    authoritative_attempt_id: str | None = None
    input_fingerprint: str | None = None
    execution_outcome: Literal["completed", "incomplete", "failed"] = "completed"
    branch_reasons: list[str] = Field(default_factory=list)
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
    destination_sha256: str | None = None
    status: NotificationStatus = NotificationStatus.NOT_REQUESTED
    sent_at: datetime | None = None
    message_identifier: str | None = None
    acknowledgment: str | None = None
    attempts: list[DeliveryAttempt] = Field(default_factory=list)
