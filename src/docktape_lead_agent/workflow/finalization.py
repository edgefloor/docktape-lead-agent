from __future__ import annotations

import time
from typing import Any

from ..domain.acceptance import build_accepted_profile, render_summary
from ..domain.compliance import evaluate_compliance, route_status
from ..domain.evidence import ResearchAttempt
from ..domain.judgments import JevAssessment
from ..domain.policy import CONFIDENCE_THRESHOLD
from ..domain.profiles import CompanyProfile
from ..domain.results import FinalResult
from ..domain.scoring import calculate_fit
from ..domain.submissions import ClaimVerdict, LeadSubmission
from .state import WorkflowState


class FinalizationStage:
    def finalize(self, state: WorkflowState) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            lead = LeadSubmission.model_validate(self.input["lead"])
            phase = state.get("authoritative_attempt_id")
            research_ref = (
                state.get(f"{phase}_evidence_ref") if phase else state["initial_evidence_ref"]
            )
            research = ResearchAttempt.model_validate(self.store.read(research_ref))
            profile = (
                CompanyProfile.model_validate(
                    self.store.read(state[f"{phase}_profile_ref"])["profile"]
                )
                if phase
                else None
            )
            review = (
                JevAssessment.model_validate(
                    self.store.read(state[f"{phase}_review_ref"])["review"]
                )
                if phase
                else None
            )
            fit = calculate_fit(review)
            checks, compliance = evaluate_compliance(
                profile, review, research, self.input["policy"]
            )
            identity_ok = bool(
                review
                and review.claim_support.get("identity") == ClaimVerdict.SUPPORTED
                and review.claim_confidence.get("identity", 0) >= CONFIDENCE_THRESHOLD
            )
            contradiction = bool(
                review
                and any(
                    verdict == ClaimVerdict.CONTRADICTED
                    and review.claim_confidence.get(claim_id, 0) >= CONFIDENCE_THRESHOLD
                    for claim_id, verdict in review.claim_support.items()
                )
            )
            unresolved = (
                not identity_ok or contradiction or bool(review and review.validation_errors)
            )
            errors = state.get("errors", [])
            status = route_status(
                fit,
                compliance,
                unresolved_conflict=unresolved,
                assessment_failed=not phase or bool(errors),
            )
            links = list(
                dict.fromkeys(item.source_url for item in research.evidence if item.source_url)
            )
            profile_data = self.store.read(state[f"{phase}_profile_ref"]) if phase else {}
            result = FinalResult(
                lead_id=lead.lead_id,
                execution_id=self.store.execution_id,
                authoritative_attempt_id=phase,
                input_fingerprint=self.store.read("manifest.json")["input_fingerprint"],
                execution_outcome="incomplete" if errors else "completed",
                branch_reasons=state.get("branch_reasons", []),
                submission=lead,
                original_submission=self.input["original"],
                accepted_profile=build_accepted_profile(profile, review)
                if profile and review
                else None,
                fit=fit,
                compliance_checks=checks,
                compliance_outcome=compliance,
                final_status=status,
                summary=render_summary(profile, review),
                evidence_references=links,
                policy_version=self.input["policy"]["version"],
                openai_requested_model=self.settings.openai_model,
                openai_returned_model=profile_data.get("returned_model"),
                jev_requested_model="jev-latest",
                jev_resolved_model=review.resolved_model if review else None,
                warnings=research.warnings + state.get("follow_errors", []),
                errors=errors,
                synthetic=research.synthetic,
                processing_seconds=round(time.monotonic() - self.started, 3),
            )
            self.store.finalize(result, phase)
            return {"result_ref": "result.json"}

        return self.store.stage("finalize", operation)
