from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class WorkflowState(TypedDict, total=False):
    execution_id: str
    initial_evidence_ref: str
    initial_profile_ref: str
    initial_review_ref: str
    follow_evidence_ref: str
    follow_profile_ref: str
    follow_review_ref: str
    follow_target: str
    authoritative_attempt_id: str | None
    branch_reasons: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    follow_errors: Annotated[list[str], operator.add]
    result_ref: str
