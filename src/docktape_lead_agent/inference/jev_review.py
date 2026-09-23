from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from ..domain.evidence import ResearchAttempt
from ..domain.judgments import ChoiceAnswer, InvalidAnswer, JevAssessment, ScoreAnswer
from ..domain.policy import (
    COMPETITOR_SCORE_LEVELS,
    CONFIDENCE_THRESHOLD,
)
from ..domain.profiles import CompanyProfile
from ..domain.submissions import (
    ClaimVerdict,
    CloudCategory,
    CompetitorBasis,
    EmployeeBand,
    LeadSubmission,
)
from ..research.http import request_with_retry
from .jev_questions import build_jev_request
from .shared import JEV_ENDPOINT, AssessmentError


def _answer(
    raw: dict[str, Any], key: str, allowed: set[str], errors: list[str]
) -> ChoiceAnswer | InvalidAnswer:
    try:
        answer = ChoiceAnswer.model_validate(raw[key])
    except (KeyError, ValidationError):
        errors.append(f"missing or malformed Jev answer: {key}")
        return InvalidAnswer(reason="missing_or_malformed", received=raw.get(key))
    if answer.choice not in allowed:
        errors.append(f"invalid Jev option for {key}: {answer.choice}")
    if set(answer.probabilities) != allowed:
        errors.append(f"Jev probability options do not match {key}")
    if answer.choice not in allowed or set(answer.probabilities) != allowed:
        return InvalidAnswer(reason="invalid_options", received=raw.get(key))
    return answer


def _score_answer(
    raw: dict[str, Any], key: str, levels: list[Any], errors: list[str]
) -> ScoreAnswer | InvalidAnswer:
    try:
        answer = ScoreAnswer.model_validate(raw[key])
    except (KeyError, ValidationError):
        errors.append(f"missing or malformed Jev answer: {key}")
        return InvalidAnswer(reason="missing_or_malformed", received=raw.get(key))
    expected_legend = {str(index): level for index, level in enumerate(levels)}
    if answer.legend != expected_legend:
        errors.append(f"Jev score legend does not match {key}")
        return InvalidAnswer(reason="invalid_legend", received=raw.get(key))
    return answer


def parse_jev_response(
    payload: dict[str, Any],
    profile: CompanyProfile,
    research: ResearchAttempt,
    policy: dict[str, Any],
) -> JevAssessment:
    raw = payload.get("answers")
    if not isinstance(raw, dict) or not isinstance(payload.get("model"), str):
        raise AssessmentError("Jev response is missing model or answers")
    errors: list[str] = []
    parsed: dict[str, ChoiceAnswer | ScoreAnswer | InvalidAnswer] = {}

    def read(key: str, allowed: set[str]) -> ChoiceAnswer | InvalidAnswer:
        value = _answer(raw, key, allowed, errors)
        parsed[key] = value
        return value

    def read_score(key: str, levels: list[Any]) -> ScoreAnswer | InvalidAnswer:
        value = _score_answer(raw, key, levels, errors)
        parsed[key] = value
        return value

    verdicts: dict[str, ClaimVerdict] = {}
    claim_confidence: dict[str, float] = {}
    for claim_id in profile.material_claims():
        answer = read(f"claim__{claim_id}", {item.value for item in ClaimVerdict})
        verdicts[claim_id] = (
            ClaimVerdict(answer.choice)
            if answer.choice in ClaimVerdict._value2member_map_
            else ClaimVerdict.INSUFFICIENT
        )
        claim_confidence[claim_id] = answer.confidence

    summary_support: list[ClaimVerdict] = []
    summary_confidence: list[float] = []
    for index in range(len(profile.sales_summary)):
        answer = read(f"summary__{index}", {item.value for item in ClaimVerdict})
        summary_support.append(
            ClaimVerdict(answer.choice)
            if answer.choice in ClaimVerdict._value2member_map_
            else ClaimVerdict.INSUFFICIENT
        )
        summary_confidence.append(answer.confidence)

    employee = read(
        "employee_count_category", {item.value for item in EmployeeBand} | {"conflicting"}
    )
    cloud = read("cloud_category", {item.value for item in CloudCategory})
    unlisted_overlap = read_score(
        "unlisted_competitor_overlap",
        COMPETITOR_SCORE_LEVELS,
    )
    follow_options = {"none", "jurisdiction"} | {item["id"] for item in policy["competitors"]}
    follow_up = read("compliance_follow_up", follow_options)

    basis: dict[str, CompetitorBasis] = {}
    basis_evidence: dict[str, str] = {}
    basis_confidence: dict[str, float] = {}
    evidence_options = {item.id for item in research.evidence} | {"submitted_name", "none"}
    for competitor in policy["competitors"]:
        identifier = competitor["id"]
        decision = read(f"competitor_basis__{identifier}", {item.value for item in CompetitorBasis})
        selected = read(f"competitor_evidence__{identifier}", evidence_options)
        basis[identifier] = (
            CompetitorBasis(decision.choice)
            if decision.choice in CompetitorBasis._value2member_map_
            else CompetitorBasis.INSUFFICIENT_IDENTITY
        )
        basis_evidence[identifier] = (
            selected.choice if selected.choice in evidence_options else "none"
        )
        basis_confidence[identifier] = min(decision.confidence, selected.confidence)
        if (
            basis[identifier] in {CompetitorBasis.LIKELY_ALIAS, CompetitorBasis.PLAUSIBLE_PARTIAL}
            and selected.choice == "none"
        ):
            errors.append(f"competitor decision lacks a consistent evidence basis: {identifier}")

    employee_category: EmployeeBand | str = (
        employee.choice
        if employee.choice == "conflicting"
        else EmployeeBand(employee.choice)
        if employee.choice in EmployeeBand._value2member_map_
        else EmployeeBand.UNKNOWN
    )
    employee_claim_accepted = (
        verdicts.get("employee_count_band") == ClaimVerdict.SUPPORTED
        and claim_confidence.get("employee_count_band", 0) >= CONFIDENCE_THRESHOLD
    )
    if (
        employee_claim_accepted
        and employee.confidence >= CONFIDENCE_THRESHOLD
        and profile.employee_count_band.value != EmployeeBand.UNKNOWN
        and employee_category != profile.employee_count_band.value
    ):
        errors.append(
            "employee-count category conflicts with the accepted profile claim: "
            f"{employee_category} != {profile.employee_count_band.value}"
        )
        employee_category = EmployeeBand.UNKNOWN

    cloud_category = (
        CloudCategory(cloud.choice)
        if cloud.choice in CloudCategory._value2member_map_
        else CloudCategory.UNKNOWN
    )
    cloud_claim_accepted = (
        verdicts.get("proposed_cloud_category") == ClaimVerdict.SUPPORTED
        and claim_confidence.get("proposed_cloud_category", 0) >= CONFIDENCE_THRESHOLD
    )
    if (
        cloud_claim_accepted
        and cloud.confidence >= CONFIDENCE_THRESHOLD
        and profile.proposed_cloud_category.value != CloudCategory.UNKNOWN
        and cloud_category != profile.proposed_cloud_category.value
    ):
        errors.append(
            "cloud category conflicts with the accepted profile claim: "
            f"{cloud_category.value} != {profile.proposed_cloud_category.value}"
        )
        cloud_category = CloudCategory.UNKNOWN

    return JevAssessment(
        resolved_model=payload["model"],
        raw_answers=parsed,
        received_answers=raw,
        claim_support=verdicts,
        claim_confidence=claim_confidence,
        employee_count_category=employee_category,
        employee_count_confidence=employee.confidence,
        cloud_category=cloud_category,
        cloud_confidence=cloud.confidence,
        competitor_basis=basis,
        competitor_evidence=basis_evidence,
        competitor_confidence=basis_confidence,
        unlisted_competitor_score=unlisted_overlap.score,
        unlisted_competitor_probabilities=unlisted_overlap.probabilities,
        unlisted_competitor_confidence=unlisted_overlap.confidence,
        compliance_follow_up=follow_up.choice if follow_up.choice in follow_options else "none",
        follow_up_confidence=follow_up.confidence,
        summary_support=summary_support,
        summary_confidence=summary_confidence,
        validation_errors=errors,
    )


def assess_with_jev(
    lead: LeadSubmission,
    research: ResearchAttempt,
    profile: CompanyProfile,
    policy: dict[str, Any],
    *,
    api_key: str,
    client: httpx.Client | None = None,
    on_attempt: Callable[[int, str, float], None] | None = None,
) -> JevAssessment:
    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10))
    try:
        _, body, _ = request_with_retry(
            http,
            "POST",
            JEV_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json=build_jev_request(lead, research, profile, policy),
            on_attempt=on_attempt,
        )
    except httpx.HTTPError as exc:
        raise AssessmentError(f"Jev request failed: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            http.close()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AssessmentError("Jev returned invalid JSON") from exc
    return parse_jev_response(payload, profile, research, policy)
