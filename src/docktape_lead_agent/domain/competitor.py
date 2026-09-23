from __future__ import annotations

from typing import Any

from .judgments import InvalidAnswer, JevAssessment
from .policy import COMPETITOR_DECISION_PROBABILITY, COMPETITOR_SCORE_THRESHOLD
from .profiles import CompanyProfile
from .results import ComplianceCheck
from .submissions import ClaimSupport, CompetitiveOverlap, ComplianceOutcome


def evaluate_unlisted_competitor(
    profile: CompanyProfile,
    assessment: JevAssessment,
    policy: dict[str, Any],
) -> ComplianceCheck:
    if isinstance(assessment.raw_answers.get("unlisted_competitor_overlap"), InvalidAnswer):
        return ComplianceCheck(
            kind="competitor",
            policy_entry="competitive_scope",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason="Jev overlap judgment is unavailable or invalid.",
            details={"judgment_status": "invalid"},
        )
    proposed = CompetitiveOverlap(profile.competitive_overlap.value)
    reviewed_score = assessment.unlisted_competitor_score
    proposal_evidenced = profile.competitive_overlap.support_type in {
        ClaimSupport.DIRECT,
        ClaimSupport.INFERRED,
    } and bool(profile.competitive_overlap.evidence_ids)
    overlap_probability = sum(
        assessment.unlisted_competitor_probabilities.get(level, 0) for level in ("2", "3")
    )
    non_overlap_probability = sum(
        assessment.unlisted_competitor_probabilities.get(level, 0) for level in ("0", "1")
    )
    reviewed_as_competing = (
        reviewed_score >= COMPETITOR_SCORE_THRESHOLD
        and overlap_probability >= COMPETITOR_DECISION_PROBABILITY
    )
    reviewed_as_non_competing = (
        reviewed_score < COMPETITOR_SCORE_THRESHOLD
        and non_overlap_probability >= COMPETITOR_DECISION_PROBABILITY
    )
    scope_name = policy["competitive_scope"]["name"]
    evidence_id = (
        profile.competitive_overlap.evidence_ids[0]
        if profile.competitive_overlap.evidence_ids
        else None
    )
    overlap_details = {
        "openai_classification": proposed.value,
        "jev_score": reviewed_score,
        "jev_confidence": assessment.unlisted_competitor_confidence,
        "material_overlap_probability": overlap_probability,
        "non_material_overlap_probability": non_overlap_probability,
        "score_threshold": COMPETITOR_SCORE_THRESHOLD,
        "decision_probability_threshold": COMPETITOR_DECISION_PROBABILITY,
    }

    if (
        proposed == CompetitiveOverlap.COMPETING_SERVICE
        and reviewed_as_competing
        and proposal_evidenced
    ):
        return ComplianceCheck(
            kind="competitor",
            policy_entry="competitive_scope",
            outcome=ComplianceOutcome.FLAGGED,
            reason=(
                f"OpenAI identified an unlisted competing service in {scope_name}; Jev independently rated "
                f"capability overlap {reviewed_score:.2f}/3.00 at confidence "
                f"{assessment.unlisted_competitor_confidence:.2f} (material-overlap probability "
                f"{overlap_probability:.2f})."
            ),
            evidence_id=evidence_id,
            details=overlap_details,
        )

    if not proposal_evidenced or proposed == CompetitiveOverlap.UNKNOWN:
        return ComplianceCheck(
            kind="competitor",
            policy_entry="competitive_scope",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason=(
                f"Unlisted competitor screening is unresolved: OpenAI proposed {proposed.value}; Jev rated "
                f"overlap {reviewed_score:.2f}/3.00 with material-overlap probability "
                f"{overlap_probability:.2f}."
            ),
            evidence_id=evidence_id,
            details=overlap_details,
        )

    if proposed != CompetitiveOverlap.COMPETING_SERVICE and reviewed_as_non_competing:
        return ComplianceCheck(
            kind="competitor",
            policy_entry="competitive_scope",
            outcome=ComplianceOutcome.CLEAR,
            reason=(
                f"Both reviewers classified the company outside the {scope_name} competitive scope "
                f"(OpenAI: {proposed.value}; Jev overlap: {reviewed_score:.2f}/3.00; "
                f"non-material-overlap probability: {non_overlap_probability:.2f})."
            ),
            evidence_id=evidence_id,
            details=overlap_details,
        )

    if (proposed == CompetitiveOverlap.COMPETING_SERVICE) != reviewed_as_competing:
        return ComplianceCheck(
            kind="competitor",
            policy_entry="competitive_scope",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason=(
                "The reviewers disagreed about material competitive overlap: "
                f"OpenAI proposed {proposed.value}; Jev rated overlap {reviewed_score:.2f}/3.00 with "
                f"material-overlap probability {overlap_probability:.2f}."
            ),
            evidence_id=evidence_id,
            details=overlap_details,
        )

    return ComplianceCheck(
        kind="competitor",
        policy_entry="competitive_scope",
        outcome=ComplianceOutcome.REVIEW_REQUIRED,
        reason=(
            f"Jev's overlap distribution crosses the policy boundary: score {reviewed_score:.2f}/3.00, "
            f"material-overlap probability {overlap_probability:.2f}, and confidence "
            f"{assessment.unlisted_competitor_confidence:.2f}."
        ),
        evidence_id=evidence_id,
        details=overlap_details,
    )
