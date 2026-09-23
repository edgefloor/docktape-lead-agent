from __future__ import annotations

from typing import Any

from ..domain.compliance import evaluate_compliance
from ..domain.evidence import ResearchAttempt
from ..domain.judgments import JevAssessment
from ..domain.profiles import CompanyProfile
from ..domain.submissions import ComplianceOutcome, LeadSubmission
from ..research.collection import collect_follow_up
from ..storage.runs import digest
from .state import WorkflowState


class FollowupStages:
    def follow_research(self, state: WorkflowState) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            lead = LeadSubmission.model_validate(self.input["lead"])
            prior = ResearchAttempt.model_validate(self.store.read(state["initial_evidence_ref"]))
            research = collect_follow_up(lead, self.research_client, state["follow_target"], prior)
            if (
                len(research.queries) > len(prior.queries) + 1
                or len(set(research.page_requests)) > len(set(prior.page_requests)) + 1
                or len(research.queries) > 4
                or len(set(research.page_requests)) > 5
            ):
                raise ValueError("follow-up research budget exceeded")
            self.store.write("evidence-follow.json", research.model_dump(mode="json"))
            snapshot = digest(research.model_dump(mode="json"))
            self.store.attempt(
                "follow",
                evidence_ref="evidence-follow.json",
                evidence_snapshot=snapshot,
                status="research_complete",
            )
            self.store.event(
                "research",
                "completed",
                attempt_id="follow",
                evidence_snapshot=snapshot,
                budget={"queries": len(research.queries), "pages": len(research.page_requests)},
            )
            return {
                "follow_evidence_ref": "evidence-follow.json",
                "follow_errors": ["Compliance follow-up failed"]
                if research.follow_up_failed
                else [],
            }

        return self.store.stage("follow_research", operation)

    def promote(self, state: WorkflowState) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            if not state.get("follow_profile_ref") or not state.get("follow_review_ref"):
                reason = "follow_candidate_incomplete"
            else:
                review = JevAssessment.model_validate(
                    self.store.read(state["follow_review_ref"])["review"]
                )
                research = ResearchAttempt.model_validate(
                    self.store.read(state["follow_evidence_ref"])
                )
                profile = CompanyProfile.model_validate(
                    self.store.read(state["follow_profile_ref"])["profile"]
                )
                _, outcome = evaluate_compliance(profile, review, research, self.input["policy"])
                if review.validation_errors or research.follow_up_failed:
                    reason = "follow_candidate_invalid"
                elif outcome == ComplianceOutcome.REVIEW_REQUIRED:
                    reason = "follow_up_unresolved"
                else:
                    reason = "follow_candidate_promoted"
            if reason == "follow_candidate_promoted":
                self.store.attempt("follow", status="promoted")
                attempt_id = "follow"
            else:
                self.store.attempt("follow", status="rejected", rejection_reason=reason)
                attempt_id = state.get("authoritative_attempt_id")
            self.store.event("promotion", "selected", attempt_id=attempt_id, reason=reason)
            return {"authoritative_attempt_id": attempt_id, "branch_reasons": [reason]}

        return self.store.stage("promotion", operation)
