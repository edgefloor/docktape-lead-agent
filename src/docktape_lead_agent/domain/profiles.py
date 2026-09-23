from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .submissions import (
    ClaimSupport,
    CloudCategory,
    CompetitiveOverlap,
    EmployeeBand,
    JurisdictionRole,
)


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
        claims.update(
            {f"jurisdiction_{index}": claim for index, claim in enumerate(self.jurisdictions)}
        )
        claims.update(
            {f"cloud_signal_{index}": claim for index, claim in enumerate(self.cloud_signals)}
        )
        return claims
