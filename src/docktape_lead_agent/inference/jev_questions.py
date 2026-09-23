from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from ..domain.evidence import ResearchAttempt
from ..domain.policy import COMPETITOR_SCORE_LEVELS, CONFIDENCE_THRESHOLD
from ..domain.profiles import CompanyProfile
from ..domain.submissions import (
    EmployeeBand,
    LeadSubmission,
    normalize_text,
)


def normalize_company_name(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    suffixes = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "co",
        "company",
        "corp",
        "corporation",
    }
    return " ".join(part for part in value.split() if part not in suffixes)


def competitor_similarity(
    lead: LeadSubmission, policy: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    submitted = normalize_company_name(lead.company_name)
    result: dict[str, dict[str, Any]] = {}
    for competitor in policy["competitors"]:
        names = [competitor["name"], *competitor.get("aliases", [])]
        scores = {name: fuzz.WRatio(submitted, normalize_company_name(name)) for name in names}
        result[competitor["id"]] = {
            "policy_name": competitor["name"],
            "submitted_normalized": submitted,
            "candidate_normalized": [normalize_company_name(name) for name in names],
            "scores": scores,
        }
    return result


def _choice(instructions: Any, criteria: dict[str, Any]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _score(instructions: Any, criteria: list[Any]) -> dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def build_jev_request(
    lead: LeadSubmission,
    research: ResearchAttempt,
    profile: CompanyProfile,
    policy: dict[str, Any],
) -> dict[str, Any]:
    evidence_ids = [item.id for item in research.evidence]
    state = {
        "company": {
            "submitted_name": lead.company_name,
            "submitted_hostname": lead.hostname,
            "employee_count_band": lead.employee_count_band.value,
        },
        "evidence_index": [
            {
                "id": item.id,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "excerpt": item.excerpt,
                "retrieved_at": item.retrieved_at.isoformat(),
                "company_association": item.company_association,
                "synthetic": item.synthetic,
            }
            for item in research.evidence
        ],
        "certificate_context": research.certificate_context.model_dump(mode="json"),
        "proposed_profile_not_evidence": profile.model_dump(mode="json"),
        "engagement_policy": policy,
        "name_similarity_signals_not_decisions": competitor_similarity(lead, policy),
        "acceptance_threshold": CONFIDENCE_THRESHOLD,
    }
    questions: dict[str, Any] = {}
    claim_criteria = {
        "supported": "The cited raw evidence supports the proposed claim.",
        "contradicted": "Raw evidence conflicts with the proposed claim.",
        "insufficient": "The supplied raw evidence does not establish the proposed claim.",
    }
    for claim_id in profile.material_claims():
        claim = profile.material_claims()[claim_id]
        questions[f"claim__{claim_id}"] = _choice(
            {
                "question": "Does the raw evidence support this proposed claim?",
                "claim_id": claim_id,
                "proposed_claim": claim.model_dump(mode="json"),
            },
            claim_criteria,
        )
    for index in range(len(profile.sales_summary)):
        questions[f"summary__{index}"] = _choice(
            {
                "question": "Does the raw evidence support this proposed summary sentence?",
                "summary_index": index,
                "proposed_sentence": profile.sales_summary[index].model_dump(mode="json"),
            },
            claim_criteria,
        )
    questions["employee_count_category"] = _choice(
        "Which employee-count category has the strongest support in raw evidence? Preserve conflicts.",
        {
            **{item.value: None for item in EmployeeBand},
            "conflicting": "Credible sources disagree.",
        },
    )
    questions["cloud_category"] = _choice(
        "Which single cloud-demand category has the strongest support? GPU or processing without cloud hosting is not substantial_cloud.",
        {
            "minimal": "Direct evidence confirms minimal infrastructure needs.",
            "digital_product": "It is a digital product without direct production-cloud evidence.",
            "production_cloud": "Fetched page evidence states production cloud infrastructure.",
            "substantial_cloud": "Fetched page evidence states substantial cloud-hosted workloads.",
            "unknown": "Evidence does not establish a category.",
            "conflicting": "Credible evidence conflicts.",
        },
    )
    questions["unlisted_competitor_overlap"] = _score(
        {
            "question": (
                "How much do the company's sold product capabilities overlap with competitive_scope? Rate only "
                "capabilities supported by raw evidence. Ignore company-name similarity, generic cloud usage, and the "
                "proposed profile."
            ),
            "competitive_scope": policy["competitive_scope"],
        },
        COMPETITOR_SCORE_LEVELS,
    )
    basis_criteria = {
        "likely_alias": "Evidence supports that the submitted company is this listed competitor.",
        "plausible_partial": "The identity is a plausible partial match to this named competitor and needs context.",
        "distinct_entity": "Evidence establishes a different entity.",
        "no_indication": "There is no indication of a match.",
        "insufficient_identity": "Evidence cannot establish the submitted company's identity.",
    }
    evidence_criteria = {
        item: "Select this evidence ID as the strongest basis." for item in evidence_ids
    }
    evidence_criteria.update(
        {
            "submitted_name": "The submitted name itself is the strongest basis.",
            "none": "No supplied item supports the decision.",
        }
    )
    for competitor in policy["competitors"]:
        identifier = competitor["id"]
        questions[f"competitor_basis__{identifier}"] = _choice(
            f"What is the evidence-based relationship between the submitted company and policy competitor {competitor['name']}?",
            basis_criteria,
        )
        questions[f"competitor_evidence__{identifier}"] = _choice(
            f"Which supplied evidence item is the strongest basis for the decision about {competitor['name']}?",
            evidence_criteria,
        )
    questions["compliance_follow_up"] = _choice(
        "If compliance is ambiguous and no competitor or country flag is already supported, which one search is useful?",
        {
            "none": "No single search is useful or a flag is already supported.",
            "jurisdiction": "One legal-entity or jurisdiction query could resolve the uncertainty.",
            **{
                item[
                    "id"
                ]: f"One identity query about {item['name']} could resolve the uncertainty."
                for item in policy["competitors"]
            },
        },
    )
    return {"state": state, "model": "jev-latest", "questions": questions}
