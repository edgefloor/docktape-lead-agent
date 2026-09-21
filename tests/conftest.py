from __future__ import annotations

import pytest

from models import (
    CertificateContext,
    ClaimVerdict,
    CloudCategory,
    CompanyProfile,
    CompetitorBasis,
    CompetitiveOverlap,
    EmployeeBand,
    EvidenceRecord,
    JevAssessment,
    JurisdictionFact,
    JurisdictionRole,
    LeadSubmission,
    MaterialClaim,
    ResearchAttempt,
    SummarySentence,
)


@pytest.fixture
def lead() -> LeadSubmission:
    return LeadSubmission(
        name="Ada Example",
        email="ada@example.com",
        company_name="Example Cloud",
        website="https://www.example.com/product?ref=demo",
        employee_count_band="51-200",
    )


@pytest.fixture
def research() -> ResearchAttempt:
    evidence = EvidenceRecord(
        id="evidence_page",
        source_type="page",
        source_url="https://example.com/about",
        excerpt="Example Cloud has 100 employees and runs customer workloads on AWS.",
        company_association="submitted_domain",
    )
    return ResearchAttempt(
        evidence=[evidence],
        certificate_context=CertificateContext(apex="example.com"),
    )


@pytest.fixture
def profile() -> CompanyProfile:
    def claim(value: str) -> MaterialClaim:
        return MaterialClaim(value=value, support_type="direct", evidence_ids=["evidence_page"])

    return CompanyProfile(
        identity=claim("Example Cloud"),
        observed_aliases=[],
        business_description=claim("Cloud software company"),
        jurisdictions=[
            JurisdictionFact(
                entity_name="Example Cloud Inc",
                country="US",
                role=JurisdictionRole.REGISTERED_JURISDICTION,
                support_type="direct",
                evidence_ids=["evidence_page"],
            )
        ],
        employee_count_band=claim("51-200"),
        cloud_signals=[claim("Runs customer workloads on AWS")],
        proposed_cloud_category=claim("production_cloud"),
        competitive_overlap=claim("no_overlap"),
        missing_facts=[],
        conflicts=[],
        sales_summary=[SummarySentence(text="Example Cloud runs customer workloads on AWS.", evidence_ids=["evidence_page"])],
    )


@pytest.fixture
def assessment() -> JevAssessment:
    return JevAssessment(
        resolved_model="jev-1.13.0",
        raw_answers={},
        claim_support={
            "identity": ClaimVerdict.SUPPORTED,
            "business_description": ClaimVerdict.SUPPORTED,
            "jurisdiction_0": ClaimVerdict.SUPPORTED,
            "employee_count_band": ClaimVerdict.SUPPORTED,
            "proposed_cloud_category": ClaimVerdict.SUPPORTED,
            "competitive_overlap": ClaimVerdict.SUPPORTED,
            "cloud_signal_0": ClaimVerdict.SUPPORTED,
        },
        claim_confidence={
            "identity": 0.95,
            "business_description": 0.95,
            "jurisdiction_0": 0.95,
            "employee_count_band": 0.95,
            "proposed_cloud_category": 0.95,
            "competitive_overlap": 0.95,
            "cloud_signal_0": 0.95,
        },
        employee_count_category=EmployeeBand.FIFTY_ONE_TO_TWO_HUNDRED,
        employee_count_confidence=0.95,
        cloud_category=CloudCategory.PRODUCTION_CLOUD,
        cloud_confidence=0.95,
        competitor_basis={
            "cloudtrim_inc": CompetitorBasis.NO_INDICATION,
            "spendwise_cloud": CompetitorBasis.NO_INDICATION,
            "rightsize_cloud_co": CompetitorBasis.NO_INDICATION,
        },
        competitor_evidence={
            "cloudtrim_inc": "submitted_name",
            "spendwise_cloud": "submitted_name",
            "rightsize_cloud_co": "submitted_name",
        },
        competitor_confidence={
            "cloudtrim_inc": 0.95,
            "spendwise_cloud": 0.95,
            "rightsize_cloud_co": 0.95,
        },
        unlisted_competitor_score=0.0,
        unlisted_competitor_probabilities={"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0},
        unlisted_competitor_confidence=0.95,
        compliance_follow_up="none",
        follow_up_confidence=0.9,
        summary_support=[ClaimVerdict.SUPPORTED],
        summary_confidence=[0.95],
    )
