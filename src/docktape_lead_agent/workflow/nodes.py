from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..domain.compliance import evaluate_compliance
from ..domain.evidence import CertificateContext, ResearchAttempt
from ..domain.judgments import JevAssessment
from ..domain.policy import CONFIDENCE_THRESHOLD
from ..domain.profiles import CompanyProfile
from ..domain.submissions import ComplianceOutcome, LeadSubmission
from ..inference.jev_review import assess_with_jev
from ..inference.profile import extract_openai_profile
from ..observability import safe_error
from ..research.collection import collect_initial, load_evidence_fixture
from ..storage.runs import RunStore, digest
from .finalization import FinalizationStage
from .followup import FollowupStages
from .state import WorkflowState

ProfileAdapter = Callable[..., tuple[CompanyProfile, str]]
ReviewAdapter = Callable[..., JevAssessment]


class WorkflowNodes(FollowupStages, FinalizationStage):
    def __init__(
        self,
        store: RunStore,
        *,
        settings: Any,
        evidence_file: Any = None,
        research_client: Any = None,
        profile_adapter: ProfileAdapter = extract_openai_profile,
        review_adapter: ReviewAdapter = assess_with_jev,
    ) -> None:
        self.store = store
        self.input = store.read("input.json")
        self.settings = settings
        self.evidence_file = evidence_file
        self.research_client = research_client
        self.profile_adapter = profile_adapter
        self.review_adapter = review_adapter
        self.started = time.monotonic()

    def initial_research(self, state: WorkflowState) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            lead = LeadSubmission.model_validate(self.input["lead"])
            try:
                if self.evidence_file:
                    research = load_evidence_fixture(self.evidence_file, lead)
                else:
                    research = collect_initial(lead, self.research_client)
                if len(research.queries) > 3 or len(set(research.page_requests)) > 4:
                    raise ValueError("initial research budget exceeded")
                errors: list[str] = []
            except Exception as exc:
                message = f"Research failed: {safe_error(exc)}"
                research = ResearchAttempt(
                    certificate_context=CertificateContext(
                        apex=lead.hostname,
                        unavailable=True,
                        warning=message,
                    ),
                    warnings=[message],
                )
                errors = [message]
            self.store.write("evidence-initial.json", research.model_dump(mode="json"))
            snapshot = digest(research.model_dump(mode="json"))
            self.store.attempt(
                "initial",
                evidence_snapshot=snapshot,
                evidence_ref="evidence-initial.json",
                status="research_complete",
            )
            self.store.event(
                "research",
                "completed",
                attempt_id="initial",
                evidence_snapshot=snapshot,
                budget={"queries": len(research.queries), "pages": len(research.page_requests)},
            )
            return {"initial_evidence_ref": "evidence-initial.json", "errors": errors}

        return self.store.stage("initial_research", operation)

    def _extract(self, state: WorkflowState, phase: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            ref = state[f"{phase}_evidence_ref"]
            research = ResearchAttempt.model_validate(self.store.read(ref))
            if phase == "follow" and research.follow_up_failed:
                return {"branch_reasons": ["follow_up_research_failed"]}
            lead = LeadSubmission.model_validate(self.input["lead"])
            try:
                extra = {}
                if self.profile_adapter is extract_openai_profile:
                    extra["on_attempt"] = lambda attempt, outcome, elapsed: self.store.event(
                        "openai_http",
                        outcome,
                        attempt_id=phase,
                        provider="openai",
                        retry_count=attempt,
                        duration_ms=round(elapsed * 1000, 2),
                    )
                extracted = self.profile_adapter(
                    lead,
                    research,
                    api_key=self.settings.openai_api_key,
                    model=self.settings.openai_model,
                    policy=self.input["policy"],
                    **extra,
                )
                profile, returned_model = extracted[:2]
                provider_metadata = extracted[2] if len(extracted) > 2 else {}
            except Exception as exc:
                message = f"OpenAI profile failed: {safe_error(exc)}"
                self.store.attempt(phase, status="profile_failed", profile_error=message)
                return {"follow_errors" if phase == "follow" else "errors": [message]}
            name = f"profile-{phase}.json"
            self.store.write(
                name,
                {
                    "profile": profile.model_dump(mode="json"),
                    "requested_model": self.settings.openai_model,
                    "returned_model": returned_model,
                    "provider_metadata": provider_metadata,
                    "evidence_snapshot": digest(research.model_dump(mode="json")),
                },
            )
            self.store.attempt(phase, profile_ref=name, status="profile_complete")
            self.store.event(
                "openai",
                "completed",
                attempt_id=phase,
                provider="openai",
                evidence_snapshot=digest(research.model_dump(mode="json")),
                requested_model=self.settings.openai_model,
                resolved_model=returned_model,
                reason="discarded_optional_claims"
                if provider_metadata.get("discarded_claims")
                else None,
            )
            return {f"{phase}_profile_ref": name}

        return self.store.stage(f"{phase}_profile", operation)

    def initial_profile(self, state: WorkflowState) -> dict[str, Any]:
        return self._extract(state, "initial")

    def follow_profile(self, state: WorkflowState) -> dict[str, Any]:
        return self._extract(state, "follow")

    def _review(self, state: WorkflowState, phase: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            profile_ref = state.get(f"{phase}_profile_ref")
            if not profile_ref:
                return {}
            profile_data = self.store.read(profile_ref)
            profile = CompanyProfile.model_validate(profile_data["profile"])
            research = ResearchAttempt.model_validate(
                self.store.read(state[f"{phase}_evidence_ref"])
            )
            try:
                extra = {}
                if self.review_adapter is assess_with_jev:
                    extra["on_attempt"] = lambda attempt, outcome, elapsed: self.store.event(
                        "jev_http",
                        outcome,
                        attempt_id=phase,
                        provider="jev",
                        retry_count=attempt,
                        duration_ms=round(elapsed * 1000, 2),
                    )
                review = self.review_adapter(
                    LeadSubmission.model_validate(self.input["lead"]),
                    research,
                    profile,
                    self.input["policy"],
                    api_key=self.settings.typesafe_api_key,
                    **extra,
                )
            except Exception as exc:
                message = f"Jev review failed: {safe_error(exc)}"
                self.store.attempt(phase, status="review_failed", review_error=message)
                return {"follow_errors" if phase == "follow" else "errors": [message]}
            name = f"review-{phase}.json"
            self.store.write(
                name,
                {
                    "review": review.model_dump(mode="json"),
                    "profile_ref": profile_ref,
                    "evidence_snapshot": profile_data["evidence_snapshot"],
                },
            )
            self.store.attempt(
                phase,
                review_ref=name,
                status="review_complete",
                validation_errors=review.validation_errors,
            )
            self.store.event(
                "jev",
                "completed",
                attempt_id=phase,
                provider="jev",
                evidence_snapshot=profile_data["evidence_snapshot"],
                requested_model=review.requested_model,
                resolved_model=review.resolved_model,
            )
            return {f"{phase}_review_ref": name}

        return self.store.stage(f"{phase}_review", operation)

    def initial_review(self, state: WorkflowState) -> dict[str, Any]:
        return self._review(state, "initial")

    def follow_review(self, state: WorkflowState) -> dict[str, Any]:
        return self._review(state, "follow")

    def initial_decision(self, state: WorkflowState) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            if not state.get("initial_profile_ref") or not state.get("initial_review_ref"):
                return {
                    "follow_target": "none",
                    "branch_reasons": ["initial_assessment_incomplete"],
                }
            profile = CompanyProfile.model_validate(
                self.store.read(state["initial_profile_ref"])["profile"]
            )
            review = JevAssessment.model_validate(
                self.store.read(state["initial_review_ref"])["review"]
            )
            research = ResearchAttempt.model_validate(
                self.store.read(state["initial_evidence_ref"])
            )
            _, outcome = evaluate_compliance(profile, review, research, self.input["policy"])
            target = review.compliance_follow_up
            reasons: list[str] = []
            if outcome == ComplianceOutcome.FLAGGED:
                reasons.append("compliance_flag_accepted")
                target = "none"
            elif outcome != ComplianceOutcome.REVIEW_REQUIRED or target == "none":
                reasons.append("follow_up_not_useful")
                target = "none"
            elif review.follow_up_confidence < CONFIDENCE_THRESHOLD:
                reasons.append("follow_up_low_confidence")
                target = "none"
            elif research.synthetic:
                reasons.append("synthetic_follow_up_unavailable")
                target = "none"
            else:
                reasons.append(f"follow_up_requested:{target}")
            self.store.attempt("initial", status="assessed", decision=outcome.value)
            self.store.event("branch", "selected", attempt_id="initial", reason=reasons[0])
            return {
                "authoritative_attempt_id": "initial",
                "follow_target": target,
                "branch_reasons": reasons,
            }

        return self.store.stage("initial_decision", operation)
