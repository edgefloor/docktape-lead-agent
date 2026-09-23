from __future__ import annotations

import json

import httpx
from langchain_openai import ChatOpenAI
from openai import InternalServerError

from docktape_lead_agent.domain.policy import load_policy
from docktape_lead_agent.inference.profile import extract_openai_profile


def test_langchain_profile_outbound_contract(lead, research, profile):
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "gpt-test-resolved",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": profile.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        chat = ChatOpenAI(
            model="gpt-test",
            api_key="test",
            base_url="https://api.example/v1",
            http_client=http,
            max_retries=0,
            timeout=1,
            use_responses_api=True,
            store=False,
        )
        parsed, returned, usage = extract_openai_profile(
            lead,
            research,
            api_key="test",
            model="gpt-test",
            policy=load_policy(),
            client=chat,
        )
    assert parsed.identity.value == profile.identity.value
    assert returned == "gpt-test-resolved"
    assert usage["usage"]["input_tokens"] == 10
    assert len(captured) == 1
    assert captured[0]["store"] is False
    assert captured[0]["text"]["format"]["type"] == "json_schema"
    assert captured[0]["text"]["format"]["strict"] is True
    schema = captured[0]["text"]["format"]["schema"]
    assert schema["properties"]["employee_count_band"]["properties"]["value"]["enum"] == [
        "1-10",
        "11-50",
        "51-200",
        "201+",
        "unknown",
    ]
    assert (
        schema["$defs"]["JurisdictionFact"]["properties"]["country"]["pattern"]
        == "^(?:[A-Z]{2}|unknown)$"
    )
    expected_ids = sorted(item.id for item in research.evidence)
    for definition in ("MaterialClaim", "JurisdictionFact", "SummarySentence"):
        assert (
            schema["$defs"][definition]["properties"]["evidence_ids"]["items"]["enum"]
            == expected_ids
        )


def test_profile_refusal_and_incomplete_are_rejected(lead, research):
    import pytest

    from docktape_lead_agent.inference.shared import AssessmentError

    for status, content in (
        ("completed", [{"type": "refusal", "refusal": "cannot comply"}]),
        ("incomplete", []),
    ):

        def handler(request: httpx.Request, status=status, content=content) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-test",
                    "status": status,
                    "incomplete_details": {"reason": "max_output_tokens"}
                    if status == "incomplete"
                    else None,
                    "output": [
                        {
                            "id": "msg_test",
                            "type": "message",
                            "status": status,
                            "role": "assistant",
                            "content": content,
                        }
                    ],
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            chat = ChatOpenAI(
                model="gpt-test",
                api_key="test",
                base_url="https://api.example/v1",
                http_client=http,
                max_retries=0,
                timeout=1,
                use_responses_api=True,
                store=False,
            )
            with pytest.raises((AssessmentError, ValueError)):
                extract_openai_profile(
                    lead,
                    research,
                    api_key="test",
                    model="gpt-test",
                    policy=load_policy(),
                    client=chat,
                )


def test_profile_transport_retry_is_bounded(lead, research):
    import pytest

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503, json={"error": {"message": "unavailable", "type": "server_error"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        chat = ChatOpenAI(
            model="gpt-test",
            api_key="test",
            base_url="https://api.example/v1",
            http_client=http,
            max_retries=0,
            timeout=1,
            use_responses_api=True,
            store=False,
        )
        with pytest.raises(InternalServerError):
            extract_openai_profile(
                lead, research, api_key="test", model="gpt-test", policy=load_policy(), client=chat
            )
    assert calls == 2


def test_invalid_jurisdiction_citation_gets_one_bounded_correction(lead, research, profile) -> None:
    from docktape_lead_agent.domain.evidence import EvidenceRecord

    research.evidence.append(
        EvidenceRecord(
            id="snippet_only",
            source_type="search_snippet",
            source_url="https://example.com/search",
            excerpt="Example Cloud may be registered in the US.",
            company_association="unverified",
        )
    )
    invalid = profile.model_copy(deep=True)
    invalid.jurisdictions[0].evidence_ids = ["snippet_only"]
    calls = 0
    outcomes = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        candidate = invalid if calls == 1 else profile
        return httpx.Response(
            200,
            json={
                "id": f"resp_{calls}",
                "object": "response",
                "created_at": 1,
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {
                        "id": f"msg_{calls}",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": candidate.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        chat = ChatOpenAI(
            model="gpt-test",
            api_key="test",
            base_url="https://api.example/v1",
            http_client=http,
            max_retries=0,
            timeout=1,
            use_responses_api=True,
            store=False,
        )
        parsed, _, _ = extract_openai_profile(
            lead,
            research,
            api_key="test",
            model="gpt-test",
            policy=load_policy(),
            client=chat,
            on_attempt=lambda attempt, outcome, elapsed: outcomes.append((attempt, outcome)),
        )

    assert calls == 2
    assert outcomes == [(0, "invalid_citations"), (1, "completed")]
    assert parsed.jurisdictions[0].evidence_ids == ["evidence_page"]


def test_optional_direct_signal_without_page_is_discarded(lead, research, profile):
    from docktape_lead_agent.domain.evidence import EvidenceRecord
    from docktape_lead_agent.domain.profiles import MaterialClaim

    research.evidence.append(
        EvidenceRecord(
            id="snippet_only",
            source_type="search_snippet",
            source_url="https://example.com/search",
            excerpt="AWS mentioned",
            company_association="unverified",
        )
    )
    profile.cloud_signals.append(
        MaterialClaim(value="AWS mentioned", support_type="direct", evidence_ids=["snippet_only"])
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": profile.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        chat = ChatOpenAI(
            model="gpt-test",
            api_key="test",
            base_url="https://api.example/v1",
            http_client=http,
            max_retries=0,
            timeout=1,
            use_responses_api=True,
            store=False,
        )
        clean, _, metadata = extract_openai_profile(
            lead, research, api_key="test", model="gpt-test", policy=load_policy(), client=chat
        )
    assert len(clean.cloud_signals) == 1
    assert metadata["discarded_claims"][0]["claim_id"] == "cloud_signal_1"
    assert metadata["discarded_claims"][0]["claim"]["evidence_ids"] == ["snippet_only"]
