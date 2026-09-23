from __future__ import annotations

from .judgments import JevAssessment
from .policy import CONFIDENCE_THRESHOLD
from .profiles import CompanyProfile, MaterialClaim
from .submissions import ClaimSupport, ClaimVerdict


def build_accepted_profile(profile: CompanyProfile, assessment: JevAssessment) -> CompanyProfile:
    accepted = profile.model_copy(deep=True)

    def claim_is_accepted(claim_id: str) -> bool:
        return (
            assessment.claim_support.get(claim_id) == ClaimVerdict.SUPPORTED
            and assessment.claim_confidence.get(claim_id, 0) >= CONFIDENCE_THRESHOLD
        )

    def unknown_claim() -> MaterialClaim:
        return MaterialClaim(value="unknown", support_type=ClaimSupport.UNKNOWN, evidence_ids=[])

    for claim_id in (
        "identity",
        "business_description",
        "employee_count_band",
        "proposed_cloud_category",
        "competitive_overlap",
    ):
        if not claim_is_accepted(claim_id):
            setattr(accepted, claim_id, unknown_claim())

    accepted.jurisdictions = [
        fact
        for index, fact in enumerate(accepted.jurisdictions)
        if claim_is_accepted(f"jurisdiction_{index}")
    ]
    accepted.cloud_signals = [
        claim
        for index, claim in enumerate(accepted.cloud_signals)
        if claim_is_accepted(f"cloud_signal_{index}")
    ]
    accepted.observed_aliases = []
    accepted.sales_summary = [
        sentence
        for sentence, verdict, confidence in zip(
            accepted.sales_summary,
            assessment.summary_support,
            assessment.summary_confidence,
            strict=False,
        )
        if verdict == ClaimVerdict.SUPPORTED and confidence >= CONFIDENCE_THRESHOLD
    ]
    return accepted


def render_summary(profile: CompanyProfile | None, assessment: JevAssessment | None) -> str:
    if profile is None or assessment is None:
        return "The automated assessment did not complete. Review the saved evidence and errors."
    accepted: list[str] = []
    rejected = 0
    for sentence, verdict, confidence in zip(
        profile.sales_summary,
        assessment.summary_support,
        assessment.summary_confidence,
        strict=False,
    ):
        if verdict == ClaimVerdict.SUPPORTED and confidence >= CONFIDENCE_THRESHOLD:
            accepted.append(sentence.text.strip())
        else:
            rejected += 1
    if rejected:
        accepted.append(
            f"{rejected} proposed summary statement(s) lacked accepted evidence support."
        )
    return " ".join(accepted) if accepted else "The evidence does not support a sales summary."
