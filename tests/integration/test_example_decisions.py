"""Historical demo evidence as offline policy examples, not current company facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docktape_lead_agent.domain.compliance import evaluate_compliance, route_status
from docktape_lead_agent.domain.evidence import ResearchAttempt
from docktape_lead_agent.domain.judgments import JevAssessment
from docktape_lead_agent.domain.policy import CONFIDENCE_THRESHOLD, load_policy
from docktape_lead_agent.domain.profiles import CompanyProfile
from docktape_lead_agent.domain.scoring import calculate_fit
from docktape_lead_agent.domain.submissions import ClaimVerdict, LeadSubmission
from docktape_lead_agent.inference.profile import (
    discard_unsupported_optional_signals,
    validate_profile_evidence,
)

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "example_cases"


@pytest.mark.parametrize("name", ["bitrise", "qovery", "holori"])
def test_reviewed_example_assessment_routes_from_evidence(name: str) -> None:
    case = json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))
    lead = LeadSubmission.model_validate(case["submission"])
    research = ResearchAttempt.model_validate(case["research"])
    profile = CompanyProfile.model_validate(case["profile"])
    review = JevAssessment.model_validate(case["review"])
    expected = case["expected"]

    assert lead.company_name.casefold() == name
    assert research.evidence
    assert review.validation_errors == []
    accepted_profile, discarded = discard_unsupported_optional_signals(profile, research)
    validate_profile_evidence(accepted_profile, research)
    if name == "bitrise":
        assert {item["claim_id"] for item in discarded} == {
            "cloud_signal_0",
            "cloud_signal_2",
        }
    else:
        assert discarded == []

    fit = calculate_fit(review)
    checks, compliance = evaluate_compliance(profile, review, research, load_policy())
    unresolved = (
        review.claim_support.get("identity") != ClaimVerdict.SUPPORTED
        or review.claim_confidence.get("identity", 0) < CONFIDENCE_THRESHOLD
        or any(
            verdict == ClaimVerdict.CONTRADICTED
            and review.claim_confidence.get(claim_id, 0) >= CONFIDENCE_THRESHOLD
            for claim_id, verdict in review.claim_support.items()
        )
    )
    status = route_status(fit, compliance, unresolved_conflict=unresolved)

    assert fit.score == expected["score"]
    assert fit.score_status == expected["score_status"]
    assert compliance.value == expected["compliance_outcome"]
    assert status.value == expected["final_status"]
    assert checks
