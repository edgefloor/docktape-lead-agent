from docktape_lead_agent.domain.judgments import InvalidAnswer
from docktape_lead_agent.domain.policy import load_policy
from docktape_lead_agent.inference.jev_questions import build_jev_request
from docktape_lead_agent.inference.jev_review import parse_jev_response


def test_missing_jev_judgments_are_explicit(lead, research, profile):
    request = build_jev_request(lead, research, profile, load_policy())
    parsed = parse_jev_response(
        {"model": "jev-test", "answers": {}}, profile, research, load_policy()
    )
    assert request["questions"]
    assert isinstance(parsed.raw_answers["unlisted_competitor_overlap"], InvalidAnswer)
    assert parsed.raw_answers["unlisted_competitor_overlap"].probabilities == {}
    assert parsed.received_answers == {}
    assert parsed.validation_errors
