from __future__ import annotations

from .judgments import JevAssessment
from .policy import CLOUD_POINTS, CONFIDENCE_THRESHOLD, SIZE_POINTS
from .results import FitResult
from .submissions import ClaimVerdict, EmployeeBand


def calculate_fit(assessment: JevAssessment | None) -> FitResult:
    if assessment is None:
        return FitResult(
            employee_points=None,
            cloud_points=None,
            score=None,
            score_status="provisional",
            rationale="Assessment failed, so both fit components are unknown.",
        )
    employee_points: int | None = None
    if (
        assessment.employee_count_confidence >= CONFIDENCE_THRESHOLD
        and assessment.employee_count_category != "conflicting"
        and assessment.employee_count_category != EmployeeBand.UNKNOWN
        and assessment.claim_support.get("employee_count_band") == ClaimVerdict.SUPPORTED
        and assessment.claim_confidence.get("employee_count_band", 0) >= CONFIDENCE_THRESHOLD
    ):
        employee_points = SIZE_POINTS.get(EmployeeBand(assessment.employee_count_category))
    cloud_points: int | None = None
    if (
        assessment.cloud_confidence >= CONFIDENCE_THRESHOLD
        and assessment.claim_support.get("proposed_cloud_category") == ClaimVerdict.SUPPORTED
        and assessment.claim_confidence.get("proposed_cloud_category", 0) >= CONFIDENCE_THRESHOLD
    ):
        cloud_points = CLOUD_POINTS.get(assessment.cloud_category)
    known = [value for value in (employee_points, cloud_points) if value is not None]
    score = sum(known) if known else None
    status = "complete" if len(known) == 2 else "provisional"
    components = [
        f"employee count: {employee_points} points"
        if employee_points is not None
        else "employee count: unknown",
        f"cloud demand: {cloud_points} points"
        if cloud_points is not None
        else "cloud demand: unknown",
    ]
    return FitResult(
        employee_points=employee_points,
        cloud_points=cloud_points,
        score=score,
        score_status=status,
        rationale="; ".join(components) + ".",
    )
