from __future__ import annotations

from typing import Any

from .competitor import evaluate_unlisted_competitor
from .country import evaluate_country_compliance
from .evidence import ResearchAttempt
from .judgments import JevAssessment
from .policy import (
    CONFIDENCE_THRESHOLD,
)
from .profiles import CompanyProfile
from .results import ComplianceCheck, FitResult
from .submissions import (
    ClaimVerdict,
    CompetitorBasis,
    ComplianceOutcome,
    FinalStatus,
)


def _evidence_excerpt(research: ResearchAttempt, identifier: str) -> str:
    if identifier == "submitted_name":
        return "the submitted company name"
    for record in research.evidence:
        if record.id == identifier:
            return record.excerpt[:300]
    return "no supporting source"


def evaluate_compliance(
    profile: CompanyProfile | None,
    assessment: JevAssessment | None,
    research: ResearchAttempt,
    policy: dict[str, Any],
) -> tuple[list[ComplianceCheck], ComplianceOutcome]:
    if profile is None or assessment is None:
        check = ComplianceCheck(
            kind="assessment",
            policy_entry="assessment",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason="The assessment did not complete, so the engagement policy could not be cleared.",
        )
        return [check], ComplianceOutcome.REVIEW_REQUIRED
    checks: list[ComplianceCheck] = []
    for competitor in policy["competitors"]:
        identifier = competitor["id"]
        basis = assessment.competitor_basis[identifier]
        confidence = assessment.competitor_confidence[identifier]
        selected = assessment.competitor_evidence[identifier]
        basis_value = basis.value if isinstance(basis, CompetitorBasis) else str(basis)
        source = _evidence_excerpt(research, selected)
        evidence_inconsistent = (
            basis
            in {
                CompetitorBasis.LIKELY_ALIAS,
                CompetitorBasis.PLAUSIBLE_PARTIAL,
            }
            and selected == "none"
        )
        if basis in {CompetitorBasis.NO_INDICATION, CompetitorBasis.DISTINCT_ENTITY}:
            outcome = ComplianceOutcome.CLEAR
            reason = f"No supported competitor match to {competitor['name']}. Basis: {basis.value}."
        elif evidence_inconsistent:
            outcome = ComplianceOutcome.REVIEW_REQUIRED
            reason = f"The {basis.value} decision for {competitor['name']} has no supporting evidence basis."
        elif confidence < CONFIDENCE_THRESHOLD:
            outcome = ComplianceOutcome.REVIEW_REQUIRED
            reason = f"The possible match to {competitor['name']} has confidence {confidence:.2f}, below 0.80. Basis: {basis.value}."
        elif basis == CompetitorBasis.LIKELY_ALIAS:
            outcome = ComplianceOutcome.FLAGGED
            reason = (
                f"The submitted company is a likely alias of {competitor['name']}. Basis: {source}"
            )
        elif basis == CompetitorBasis.PLAUSIBLE_PARTIAL:
            outcome = ComplianceOutcome.REVIEW_REQUIRED
            reason = f"Possible match to {competitor['name']} remains unresolved. Basis: {source}"
        elif basis == CompetitorBasis.INSUFFICIENT_IDENTITY:
            outcome = ComplianceOutcome.REVIEW_REQUIRED
            reason = f"Possible match to {competitor['name']} remains unresolved. Basis: {source}"
        else:
            outcome = ComplianceOutcome.CLEAR
            reason = f"No supported competitor match to {competitor['name']}. Basis: {basis.value}."
        checks.append(
            ComplianceCheck(
                kind="competitor",
                policy_entry=competitor["name"],
                outcome=outcome,
                reason=reason,
                evidence_id=None if selected in {"submitted_name", "none"} else selected,
                details={
                    "basis": basis_value,
                    "confidence": confidence,
                    "evidence_selection": selected,
                },
            )
        )

    checks.append(evaluate_unlisted_competitor(profile, assessment, policy))
    accepted_jurisdictions = [
        fact
        for index, fact in enumerate(profile.jurisdictions)
        if assessment.claim_support.get(f"jurisdiction_{index}") == ClaimVerdict.SUPPORTED
        and assessment.claim_confidence.get(f"jurisdiction_{index}", 0) >= CONFIDENCE_THRESHOLD
    ]
    checks.append(evaluate_country_compliance(accepted_jurisdictions, policy))
    if assessment.validation_errors:
        checks.append(
            ComplianceCheck(
                kind="assessment",
                policy_entry="jev_response_validation",
                outcome=ComplianceOutcome.REVIEW_REQUIRED,
                reason="; ".join(assessment.validation_errors),
                details={"errors": assessment.validation_errors},
            )
        )
    if any(check.outcome == ComplianceOutcome.FLAGGED for check in checks):
        return checks, ComplianceOutcome.FLAGGED
    if any(check.outcome == ComplianceOutcome.REVIEW_REQUIRED for check in checks):
        return checks, ComplianceOutcome.REVIEW_REQUIRED
    return checks, ComplianceOutcome.CLEAR


def route_status(
    fit: FitResult,
    compliance: ComplianceOutcome,
    *,
    unresolved_conflict: bool = False,
    assessment_failed: bool = False,
) -> FinalStatus:
    if compliance == ComplianceOutcome.FLAGGED:
        return FinalStatus.DO_NOT_ENGAGE
    if (
        compliance == ComplianceOutcome.REVIEW_REQUIRED
        or unresolved_conflict
        or assessment_failed
        or fit.score_status != "complete"
    ):
        return FinalStatus.REVIEW_REQUIRED
    if (
        fit.score is not None
        and fit.score >= 60
        and fit.cloud_points is not None
        and fit.cloud_points >= 40
    ):
        return FinalStatus.SALES_READY
    return FinalStatus.LOWER_PRIORITY
