from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .submissions import ClaimVerdict, CloudCategory, CompetitorBasis, EmployeeBand


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_distribution(self) -> ChoiceAnswer:
        if not self.probabilities:
            raise ValueError("choice probabilities must not be empty")
        if any(
            not math.isfinite(value) or value < 0 or value > 1
            for value in self.probabilities.values()
        ):
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
        if any(
            not math.isfinite(value) or value < 0 or value > 1
            for value in self.probabilities.values()
        ):
            raise ValueError("score probabilities must be finite values from 0 to 1")
        if abs(sum(self.probabilities.values()) - 1) > 0.01:
            raise ValueError("score probabilities must sum to 1")
        weighted_score = sum(
            int(level) * probability for level, probability in self.probabilities.items()
        )
        if abs(self.score - weighted_score) > 0.02:
            raise ValueError("score must equal the probability-weighted level position")
        return self


class InvalidAnswer(BaseModel):
    type: Literal["invalid"] = "invalid"
    reason: str
    received: Any = None
    choice: str = ""
    confidence: float = 0
    score: float = 0
    probabilities: dict[str, float] = Field(default_factory=dict)


class JevAssessment(BaseModel):
    requested_model: str = "jev-latest"
    resolved_model: str
    raw_answers: dict[str, ChoiceAnswer | ScoreAnswer | InvalidAnswer]
    received_answers: dict[str, Any] = Field(default_factory=dict)
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
