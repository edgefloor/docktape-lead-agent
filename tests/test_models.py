from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import ChoiceAnswer, LeadSubmission, ScoreAnswer


def test_stable_id_ignores_scheme_www_and_url_details() -> None:
    first = LeadSubmission(name="A", email="USER@Example.com", company_name="  Acme   Cloud ", website="https://www.acme.com/a?q=1", employee_count_band="unknown")
    second = LeadSubmission(name="B", email="user@example.com", company_name="acme cloud", website="http://acme.com/other#x", employee_count_band="unknown")
    assert first.lead_id == second.lead_id


def test_changed_identity_creates_new_id() -> None:
    base = LeadSubmission(name="A", email="one@example.com", company_name="Acme", website="https://acme.com", employee_count_band="unknown")
    changed = LeadSubmission(name="A", email="two@example.com", company_name="Acme", website="https://acme.com", employee_count_band="unknown")
    assert base.lead_id != changed.lead_id


@pytest.mark.parametrize(
    "website",
    ["ftp://example.com", "https://user:pass@example.com", "http://127.0.0.1", "http://10.0.0.1", "http://localhost"],
)
def test_rejects_non_public_or_credentialed_urls(website: str) -> None:
    with pytest.raises(ValidationError):
        LeadSubmission(name="A", email="a@example.com", company_name="Acme", website=website, employee_count_band="unknown")


@pytest.mark.parametrize("band", ["1-10", "11-50", "51-200", "201+", "unknown"])
def test_accepts_every_employee_band_form_option(band: str) -> None:
    lead = LeadSubmission(
        name="A",
        email="a@example.com",
        company_name="Acme",
        website="https://acme.com",
        employee_count_band=band,
    )
    assert lead.employee_count_band.value == band


def test_employee_band_is_required() -> None:
    with pytest.raises(ValidationError):
        LeadSubmission(name="A", email="a@example.com", company_name="Acme", website="https://acme.com")


@pytest.mark.parametrize(
    "probabilities",
    [
        {"yes": -1.0, "no": 2.0},
        {"yes": 0.4, "no": 0.4},
        {"yes": float("nan"), "no": 0.0},
    ],
)
def test_choice_answer_rejects_invalid_probability_distributions(probabilities: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ChoiceAnswer(type="choice", choice="yes", probabilities=probabilities, confidence=0.9)


def test_choice_answer_requires_selected_highest_probability() -> None:
    with pytest.raises(ValidationError):
        ChoiceAnswer(type="choice", choice="no", probabilities={"yes": 0.8, "no": 0.2}, confidence=0.9)


def test_score_answer_requires_probability_weighted_score() -> None:
    with pytest.raises(ValidationError):
        ScoreAnswer(
            type="score",
            score=3.0,
            probabilities={"0": 0.0, "1": 1.0},
            confidence=1.0,
            legend={"0": "none", "1": "direct"},
        )


def test_score_answer_accepts_fractional_position() -> None:
    answer = ScoreAnswer(
        type="score",
        score=1.6,
        probabilities={"0": 0.0, "1": 0.4, "2": 0.6},
        confidence=0.5,
        legend={"0": "none", "1": "adjacent", "2": "direct"},
    )
    assert answer.score == 1.6
