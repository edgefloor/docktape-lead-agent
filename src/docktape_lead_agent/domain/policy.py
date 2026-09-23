from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from .submissions import CloudCategory, EmployeeBand

CONFIDENCE_THRESHOLD = 0.80
COMPETITOR_SCORE_THRESHOLD = 2.0
COMPETITOR_DECISION_PROBABILITY = 0.80
COMPETITOR_SCORE_LEVELS = [
    "No material overlap: evidence establishes a different product category and no core competitive capability is sold.",
    "Adjacent only: the company sells related cloud or infrastructure capabilities, but no core competitive capability.",
    "Material overlap: the company sells at least one core competitive capability, but it is limited or ancillary.",
    "Direct competitor: cloud cost optimization or FinOps is a core offering with multiple competitive capabilities.",
]
SIZE_POINTS = {
    EmployeeBand.ONE_TO_TEN: 5,
    EmployeeBand.ELEVEN_TO_FIFTY: 15,
    EmployeeBand.FIFTY_ONE_TO_TWO_HUNDRED: 30,
    EmployeeBand.TWO_HUNDRED_ONE_PLUS: 40,
}
CLOUD_POINTS = {
    CloudCategory.MINIMAL: 0,
    CloudCategory.DIGITAL_PRODUCT: 20,
    CloudCategory.PRODUCTION_CLOUD: 40,
    CloudCategory.SUBSTANTIAL_CLOUD: 60,
}


class PolicyError(ValueError):
    pass


def load_policy(path: Path | None = None) -> dict[str, Any]:
    source = (
        path.read_text(encoding="utf-8")
        if path
        else (
            files("docktape_lead_agent")
            .joinpath("resources/policy.json")
            .read_text(encoding="utf-8")
        )
    )
    payload = json.loads(source)
    required = {"version", "effective_date", "competitors", "country_codes"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise PolicyError("policy file is missing required fields")
    return payload
