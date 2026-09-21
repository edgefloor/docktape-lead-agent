from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from assessment import (
    AssessmentError,
    build_accepted_profile,
    build_jev_request,
    calculate_fit,
    evaluate_compliance,
    evaluate_country_compliance,
    extract_openai_profile,
    load_policy,
    parse_jev_response,
    render_summary,
    route_status,
    validate_profile_evidence,
)
from models import (
    ClaimSupport,
    ClaimVerdict,
    CloudCategory,
    CompetitorBasis,
    ComplianceOutcome,
    CompetitiveOverlap,
    FinalStatus,
    JurisdictionFact,
    JurisdictionRole,
)


def test_openai_request_uses_strict_schema_and_store_false(lead, research, profile) -> None:
    captured = {}

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="completed", output=[], output_text=profile.model_dump_json(), model="gpt-test-1")

    client = SimpleNamespace(responses=Responses())
    policy = load_policy(Path("data/policy.json"))
    parsed, returned = extract_openai_profile(
        lead,
        research,
        api_key="unused",
        model="gpt-test",
        policy=policy,
        client=client,
    )
    assert parsed.identity.value == "Example Cloud"
    assert returned == "gpt-test-1"
    assert captured["store"] is False
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["strict"] is True
    schema = captured["text"]["format"]["schema"]
    assert schema["properties"]["employee_count_band"]["properties"]["value"]["enum"] == [
        "1-10",
        "11-50",
        "51-200",
        "201+",
        "unknown",
    ]
    assert schema["properties"]["proposed_cloud_category"]["properties"]["value"]["enum"] == [
        "minimal",
        "digital_product",
        "production_cloud",
        "substantial_cloud",
        "unknown",
        "conflicting",
    ]
    assert schema["properties"]["competitive_overlap"]["properties"]["value"]["enum"] == [
        "competing_service",
        "adjacent_service",
        "no_overlap",
        "unknown",
    ]
    assert schema["$defs"]["JurisdictionFact"]["properties"]["country"]["pattern"] == "^(?:[A-Z]{2}|unknown)$"


def test_jev_questions_are_choice_and_independent(lead, research, profile) -> None:
    policy = load_policy(Path("data/policy.json"))
    request = build_jev_request(lead, research, profile, policy)
    assert request["model"] == "jev-latest"
    assert all(
        question["type"] == "choice"
        for key, question in request["questions"].items()
        if key != "unlisted_competitor_overlap"
    )
    assert "proposed_profile_not_evidence" in request["state"]


def test_jev_rates_unlisted_competitor_overlap_on_an_ordered_score(lead, research, profile) -> None:
    policy = load_policy(Path("data/policy.json"))
    question = build_jev_request(lead, research, profile, policy)["questions"]["unlisted_competitor_overlap"]

    assert question["type"] == "score"
    assert len(question["criteria"]) == 4
    assert "No material overlap" in question["criteria"][0]
    assert "Direct competitor" in question["criteria"][3]


def test_profile_requires_citations_for_claims_and_summary(research, profile) -> None:
    profile.identity.evidence_ids = []
    profile.sales_summary[0].evidence_ids = []
    with pytest.raises(AssessmentError, match="must cite evidence"):
        validate_profile_evidence(profile, research)


def test_unknown_claim_may_cite_conflicting_evidence(research, profile) -> None:
    profile.jurisdictions[0].country = "unknown"
    profile.jurisdictions[0].support_type = "unknown"
    profile.jurisdictions[0].evidence_ids = [research.evidence[0].id]

    validate_profile_evidence(profile, research)


def test_scoring_and_direct_cloud_requirement(assessment) -> None:
    fit = calculate_fit(assessment)
    assert fit.score == 70
    assert route_status(fit, ComplianceOutcome.CLEAR) == FinalStatus.SALES_READY

    assessment.cloud_category = CloudCategory.DIGITAL_PRODUCT
    fit = calculate_fit(assessment)
    assert fit.score == 50
    assert route_status(fit, ComplianceOutcome.CLEAR) == FinalStatus.LOWER_PRIORITY


def test_unknown_component_routes_to_review(assessment) -> None:
    assessment.cloud_confidence = 0.79
    fit = calculate_fit(assessment)
    assert fit.score == 30
    assert fit.score_status == "provisional"
    assert route_status(fit, ComplianceOutcome.CLEAR) == FinalStatus.REVIEW_REQUIRED


def test_contradicted_component_claim_cannot_score(assessment) -> None:
    assessment.claim_support["employee_count_band"] = "contradicted"
    assessment.claim_support["proposed_cloud_category"] = "insufficient"
    fit = calculate_fit(assessment)
    assert fit.employee_points is None
    assert fit.cloud_points is None
    assert fit.score is None
    assert fit.score_status == "provisional"


def test_inconsistent_supported_profile_and_categories_cannot_score(lead, research, profile) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.employee_count_band.value = "1-10"
    profile.proposed_cloud_category.value = "digital_product"
    parsed = parse_jev_response(_jev_payload(lead, research, profile, policy), profile, research, policy)

    assert parsed.employee_count_category == "unknown"
    assert parsed.cloud_category == CloudCategory.UNKNOWN
    assert len(parsed.validation_errors) == 2
    assert calculate_fit(parsed).score is None


def test_accepted_profile_replaces_rejected_facts_with_uncertainty(profile, assessment) -> None:
    assessment.claim_support["jurisdiction_0"] = ClaimVerdict.CONTRADICTED
    assessment.claim_support["employee_count_band"] = ClaimVerdict.INSUFFICIENT
    assessment.claim_support["cloud_signal_0"] = ClaimVerdict.INSUFFICIENT

    accepted = build_accepted_profile(profile, assessment)

    assert accepted.jurisdictions == []
    assert accepted.employee_count_band.value == "unknown"
    assert accepted.cloud_signals == []
    assert accepted.business_description.value == profile.business_description.value


def test_compliance_flag_wins_and_summary_rejects_unsupported(assessment, profile, research, lead) -> None:
    assessment.competitor_basis["cloudtrim_inc"] = "likely_alias"
    assessment.competitor_evidence["cloudtrim_inc"] = "evidence_page"
    policy = load_policy(Path("data/policy.json"))
    checks, outcome = evaluate_compliance(profile, assessment, research, policy)
    assert outcome == ComplianceOutcome.FLAGGED
    assert any(check.outcome == ComplianceOutcome.FLAGGED for check in checks)
    fit = calculate_fit(assessment)
    assert route_status(fit, outcome) == FinalStatus.DO_NOT_ENGAGE

    assessment.summary_confidence = [0.5]
    assert "lacked accepted evidence support" in render_summary(profile, assessment)


def test_jurisdiction_claim_must_pass_claim_verification(assessment, profile, research) -> None:
    assessment.claim_support["jurisdiction_0"] = "contradicted"
    policy = load_policy(Path("data/policy.json"))
    checks, outcome = evaluate_compliance(profile, assessment, research, policy)
    country = next(check for check in checks if check.kind == "country")
    assert country.outcome == ComplianceOutcome.REVIEW_REQUIRED
    assert outcome == ComplianceOutcome.REVIEW_REQUIRED


def _jev_payload(lead, research, profile, policy) -> dict:
    request = build_jev_request(lead, research, profile, policy)
    choices = {
        "employee_count_category": "51-200",
        "cloud_category": "production_cloud",
        "compliance_follow_up": "none",
        "competitor_basis__cloudtrim_inc": "likely_alias",
        "competitor_evidence__cloudtrim_inc": "evidence_page",
    }
    answers = {}
    for key, question in request["questions"].items():
        options = list(question["criteria"])
        if question["type"] == "score":
            answers[key] = {
                "type": "score",
                "score": 0.0,
                "probabilities": {str(index): 1.0 if index == 0 else 0.0 for index in range(len(options))},
                "confidence": 0.99,
                "legend": {str(index): option for index, option in enumerate(options)},
            }
            continue
        if key.startswith(("claim__", "summary__")):
            choice = "supported"
        elif key.startswith("competitor_basis__"):
            choice = choices.get(key, "no_indication")
        elif key.startswith("competitor_evidence__"):
            choice = choices.get(key, "submitted_name")
        else:
            choice = choices[key]
        answers[key] = {
            "type": "choice",
            "choice": choice,
            "probabilities": {option: 1.0 if option == choice else 0.0 for option in options},
            "confidence": 0.99,
        }
    return {"model": "jev-1.13.0", "answers": answers}


def test_invalid_jev_component_preserves_independent_flag(lead, research, profile) -> None:
    policy = load_policy(Path("data/policy.json"))
    payload = _jev_payload(lead, research, profile, policy)
    payload["answers"]["employee_count_category"] = {
        "type": "choice",
        "choice": "invalid",
        "probabilities": {"invalid": 1.0},
        "confidence": 0.99,
    }
    parsed = parse_jev_response(payload, profile, research, policy)
    assert parsed.employee_count_category == "unknown"
    assert parsed.validation_errors
    _, outcome = evaluate_compliance(profile, parsed, research, policy)
    assert outcome == ComplianceOutcome.FLAGGED


def test_malformed_jev_answer_becomes_validation_error_instead_of_aborting(lead, research, profile) -> None:
    policy = load_policy(Path("data/policy.json"))
    payload = _jev_payload(lead, research, profile, policy)
    payload["answers"]["competitor_evidence__spendwise_cloud"]["probabilities"] = {}

    parsed = parse_jev_response(payload, profile, research, policy)

    assert "missing or malformed Jev answer: competitor_evidence__spendwise_cloud" in parsed.validation_errors
    assert parsed.competitor_evidence["spendwise_cloud"] in {"none", "submitted_name", "evidence_page"}


def test_inconsistent_likely_alias_requires_review(lead, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    request = build_jev_request(lead, research, profile, policy)
    payload = _jev_payload(lead, research, profile, policy)
    payload["answers"]["competitor_basis__cloudtrim_inc"]["choice"] = "likely_alias"
    payload["answers"]["competitor_basis__cloudtrim_inc"]["probabilities"] = {
        option: 1.0 if option == "likely_alias" else 0.0
        for option in request["questions"]["competitor_basis__cloudtrim_inc"]["criteria"]
    }
    payload["answers"]["competitor_evidence__cloudtrim_inc"]["choice"] = "none"
    payload["answers"]["competitor_evidence__cloudtrim_inc"]["probabilities"] = {
        option: 1.0 if option == "none" else 0.0
        for option in request["questions"]["competitor_evidence__cloudtrim_inc"]["criteria"]
    }
    parsed = parse_jev_response(payload, profile, research, policy)
    checks, outcome = evaluate_compliance(profile, parsed, research, policy)
    cloudtrim = next(check for check in checks if check.policy_entry == "CloudTrim Inc")
    assert cloudtrim.outcome == ComplianceOutcome.REVIEW_REQUIRED
    assert outcome == ComplianceOutcome.REVIEW_REQUIRED


def test_dual_agent_unlisted_competitor_agreement_is_flagged(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.competitive_overlap.value = CompetitiveOverlap.COMPETING_SERVICE
    profile.competitive_overlap.support_type = ClaimSupport.INFERRED
    assessment.unlisted_competitor_score = 3.0
    assessment.unlisted_competitor_probabilities = {"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0}
    assessment.unlisted_competitor_confidence = 0.91

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    overlap = next(check for check in checks if check.policy_entry == "competitive_scope")
    assert overlap.outcome == ComplianceOutcome.FLAGGED
    assert "unlisted competing service" in overlap.reason.casefold()
    assert outcome == ComplianceOutcome.FLAGGED


def test_dual_overlap_agreement_does_not_require_a_second_jev_claim_vote(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.competitive_overlap.value = CompetitiveOverlap.COMPETING_SERVICE
    profile.competitive_overlap.support_type = ClaimSupport.DIRECT
    assessment.claim_support["competitive_overlap"] = ClaimVerdict.INSUFFICIENT
    assessment.claim_confidence["competitive_overlap"] = 0.21
    assessment.unlisted_competitor_score = 3.0
    assessment.unlisted_competitor_probabilities = {"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0}
    assessment.unlisted_competitor_confidence = 1.0

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    overlap = next(check for check in checks if check.policy_entry == "competitive_scope")
    assert overlap.outcome == ComplianceOutcome.FLAGGED
    assert outcome == ComplianceOutcome.FLAGGED


def test_unlisted_competitor_disagreement_stays_in_review(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.competitive_overlap.value = CompetitiveOverlap.COMPETING_SERVICE
    profile.competitive_overlap.support_type = ClaimSupport.INFERRED
    assessment.unlisted_competitor_score = 1.0
    assessment.unlisted_competitor_probabilities = {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0}
    assessment.unlisted_competitor_confidence = 0.95

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    overlap = next(check for check in checks if check.policy_entry == "competitive_scope")
    assert overlap.outcome == ComplianceOutcome.REVIEW_REQUIRED
    assert outcome == ComplianceOutcome.REVIEW_REQUIRED


def test_score_uncertainty_within_non_competing_levels_still_clears(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.competitive_overlap.value = CompetitiveOverlap.ADJACENT_SERVICE
    assessment.unlisted_competitor_score = 0.69
    assessment.unlisted_competitor_probabilities = {"0": 0.31, "1": 0.69, "2": 0.0, "3": 0.0}
    assessment.unlisted_competitor_confidence = 0.69

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    overlap = next(check for check in checks if check.policy_entry == "competitive_scope")
    assert overlap.outcome == ComplianceOutcome.CLEAR
    assert outcome == ComplianceOutcome.CLEAR


def test_score_uncertainty_within_competing_levels_still_flags(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    profile.competitive_overlap.value = CompetitiveOverlap.COMPETING_SERVICE
    assessment.unlisted_competitor_score = 2.5
    assessment.unlisted_competitor_probabilities = {"0": 0.0, "1": 0.0, "2": 0.5, "3": 0.5}
    assessment.unlisted_competitor_confidence = 0.5

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    overlap = next(check for check in checks if check.policy_entry == "competitive_scope")
    assert overlap.outcome == ComplianceOutcome.FLAGGED
    assert outcome == ComplianceOutcome.FLAGGED


def jurisdiction(entity: str, country: str, role: JurisdictionRole) -> JurisdictionFact:
    return JurisdictionFact(
        entity_name=entity,
        country=country,
        role=role,
        support_type=ClaimSupport.DIRECT,
        evidence_ids=["evidence_page"],
    )


def test_multiple_global_offices_do_not_create_a_country_conflict() -> None:
    policy = load_policy(Path("data/policy.json"))
    facts = [
        jurisdiction("Example Cloud Ltd", "GB", JurisdictionRole.REGISTERED_JURISDICTION),
        jurisdiction("Example Cloud Inc", "US", JurisdictionRole.CONTRACTING_ENTITY),
        jurisdiction("Example Cloud", "HU", JurisdictionRole.OFFICE),
        jurisdiction("Example Cloud", "DE", JurisdictionRole.OFFICE),
    ]

    check = evaluate_country_compliance(facts, policy)

    assert check.outcome == ComplianceOutcome.CLEAR
    assert "multiple" in check.reason.casefold()


def test_relevant_entity_in_listed_country_is_flagged() -> None:
    policy = load_policy(Path("data/policy.json"))
    facts = [jurisdiction("Example Cloud Ltd", "RU", JurisdictionRole.REGISTERED_JURISDICTION)]

    check = evaluate_country_compliance(facts, policy)

    assert check.outcome == ComplianceOutcome.FLAGGED
    assert "RU" in check.reason


def test_listed_country_office_requires_review_but_is_not_flagged() -> None:
    policy = load_policy(Path("data/policy.json"))
    facts = [
        jurisdiction("Example Cloud Ltd", "GB", JurisdictionRole.REGISTERED_JURISDICTION),
        jurisdiction("Example Cloud", "RU", JurisdictionRole.OFFICE),
    ]

    check = evaluate_country_compliance(facts, policy)

    assert check.outcome == ComplianceOutcome.REVIEW_REQUIRED
    assert "office" in check.reason.casefold()


def test_conflict_only_exists_for_same_entity_and_role() -> None:
    policy = load_policy(Path("data/policy.json"))
    facts = [
        jurisdiction("Example Cloud Ltd", "GB", JurisdictionRole.REGISTERED_JURISDICTION),
        jurisdiction("Example Cloud Ltd", "US", JurisdictionRole.REGISTERED_JURISDICTION),
    ]

    check = evaluate_country_compliance(facts, policy)

    assert check.outcome == ComplianceOutcome.REVIEW_REQUIRED
    assert "conflicting" in check.reason.casefold()


def test_no_indication_competitor_is_clear_even_with_low_confidence(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    assessment.competitor_confidence = {key: 0.2 for key in assessment.competitor_confidence}

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    competitors = [check for check in checks if check.kind == "competitor"]
    assert all(check.outcome == ComplianceOutcome.CLEAR for check in competitors)
    assert outcome == ComplianceOutcome.CLEAR


def test_distinct_entity_is_clear_even_with_low_confidence(assessment, profile, research) -> None:
    policy = load_policy(Path("data/policy.json"))
    assessment.competitor_basis["cloudtrim_inc"] = CompetitorBasis.DISTINCT_ENTITY
    assessment.competitor_evidence["cloudtrim_inc"] = "none"
    assessment.competitor_confidence["cloudtrim_inc"] = 0.2

    checks, outcome = evaluate_compliance(profile, assessment, research, policy)

    cloudtrim = next(check for check in checks if check.policy_entry == "CloudTrim Inc")
    assert cloudtrim.outcome == ComplianceOutcome.CLEAR
    assert outcome == ComplianceOutcome.CLEAR
