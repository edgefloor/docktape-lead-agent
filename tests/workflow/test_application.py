from __future__ import annotations

import json

from docktape_lead_agent.application import Application
from docktape_lead_agent.domain.submissions import NotificationStatus
from docktape_lead_agent.settings import Settings


def settings() -> Settings:
    return Settings(None, None, None, "jev-test", "openai-test", "gpt-test", None)


def test_run_reuse_and_changed_band(tmp_path, lead, profile, assessment):
    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    calls = {"profile": 0, "review": 0}

    def extract(*args, **kwargs):
        calls["profile"] += 1
        return profile, "gpt-test-resolved"

    def review(*args, **kwargs):
        calls["review"] += 1
        return assessment

    app = Application(
        settings(), tmp_path / "output", profile_adapter=extract, review_adapter=review
    )
    first = app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    assert first.execution_outcome == "completed"
    assert first.authoritative_attempt_id == "initial"
    assert calls == {"profile": 1, "review": 1}
    second = app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    assert second.execution_id == first.execution_id
    assert calls == {"profile": 1, "review": 1}
    changed = lead.model_copy(
        update={"employee_count_band": lead.employee_count_band.__class__.ONE_TO_TEN}
    )
    third = app.run(changed.model_dump(mode="json"), evidence_file=fixture)
    assert third.lead_id == first.lead_id
    assert third.execution_id != first.execution_id
    assert calls == {"profile": 2, "review": 2}
    report = app.inspect(lead.lead_id)
    assert report["manifest"]["authoritative_attempt_id"] == "initial"
    assert report["manifest"]["configuration"]["profile_version"] == "profile-v3"
    assert any(event["stage"] == "jev" for event in report["events"])
    assert third.notification_status == NotificationStatus.NOT_REQUESTED


def test_interrupted_profile_resumes_from_saved_research(tmp_path, lead, profile, assessment):
    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    calls = 0

    def extract(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return profile, "gpt-test-resolved"

    app = Application(
        settings(),
        tmp_path / "output",
        profile_adapter=extract,
        review_adapter=lambda *a, **k: assessment,
    )
    import pytest

    with pytest.raises(KeyboardInterrupt):
        app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    execution_id = next((tmp_path / "output" / "runs" / lead.lead_id / "executions").iterdir()).name
    result = app.resume(lead.lead_id, execution_id)
    assert result.authoritative_attempt_id == "initial"
    assert calls == 2
    stages = app.inspect(lead.lead_id)["manifest"]["stages"]
    assert stages["initial_research"]["outcome"] == "completed"


def test_failed_follow_candidate_retains_initial_attempt(
    tmp_path, monkeypatch, lead, research, profile, assessment
):
    import docktape_lead_agent.workflow.followup as followup_module
    import docktape_lead_agent.workflow.nodes as node_module
    from docktape_lead_agent.domain.submissions import CompetitorBasis

    initial = assessment.model_copy(deep=True)
    initial.competitor_basis["spendwise_cloud"] = CompetitorBasis.PLAUSIBLE_PARTIAL
    initial.compliance_follow_up = "jurisdiction"
    initial.follow_up_confidence = 0.95
    monkeypatch.setattr(node_module, "collect_initial", lambda lead, client: research)
    follow = research.model_copy(deep=True)
    follow.queries = ["one additional query"]
    monkeypatch.setattr(
        followup_module, "collect_follow_up", lambda lead, client, target, prior: follow
    )
    reviews = 0

    def review(*args, **kwargs):
        nonlocal reviews
        reviews += 1
        if reviews == 2:
            raise ValueError("candidate failed")
        return initial

    live = Settings(
        "https://search.example",
        "https://fetch.example",
        None,
        "jev-test",
        "openai-test",
        "gpt-test",
        None,
    )
    app = Application(
        live,
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=review,
        research_client=object(),
    )
    result = app.run(lead.model_dump(mode="json"))
    assert reviews == 2
    assert result.authoritative_attempt_id == "initial"
    assert result.execution_outcome == "completed"
    assert any("candidate failed" in warning for warning in result.warnings)
    assert "follow_candidate_incomplete" in result.branch_reasons
    report = app.inspect(lead.lead_id)
    assert report["manifest"]["authoritative_attempt_id"] == "initial"
    assert report["manifest"]["stages"]["follow_review"]["outcome"] == "completed"
    assert report["result"]["final_status"] == "review_required"


def test_uncertain_delivery_never_resends_automatically(tmp_path, lead, profile, assessment):
    import pytest

    from docktape_lead_agent.domain.submissions import NotificationStatus

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    attempts = 0

    def sender(url, message, record, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("crash after external acceptance")
        record.status = NotificationStatus.SENT
        return record

    configured = Settings(
        None, None, None, "jev-test", "openai-test", "gpt-test", "https://hooks.example.test/x"
    )
    app = Application(
        configured,
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
        slack_sender=sender,
    )
    with pytest.raises(RuntimeError):
        app.run(lead.model_dump(mode="json"), evidence_file=fixture, send=True)
    reused = app.run(lead.model_dump(mode="json"), evidence_file=fixture, send=True)
    assert reused.notification_status == NotificationStatus.UNKNOWN
    assert attempts == 1
    assert app.retry_notification(lead.lead_id) == NotificationStatus.SENT
    assert attempts == 2


def test_workbook_failure_prevents_delivery(tmp_path, monkeypatch, lead, profile, assessment):
    import pytest

    import docktape_lead_agent.application as app_module

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    sent = []
    app = Application(
        Settings(
            None, None, None, "jev-test", "openai-test", "gpt-test", "https://hooks.example.test/x"
        ),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
        slack_sender=lambda *a, **k: sent.append(True),
    )
    monkeypatch.setattr(
        app_module, "update_workbook", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        app.run(lead.model_dump(mode="json"), evidence_file=fixture, send=True)
    assert not sent


def test_failed_initial_profile_saves_review_result(tmp_path, lead):
    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    app = Application(
        settings(),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (_ for _ in ()).throw(ValueError("bad profile")),
    )
    result = app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    assert result.final_status.value == "review_required"
    assert result.execution_outcome == "incomplete"
    assert result.authoritative_attempt_id is None
    assert app.inspect(lead.lead_id)["manifest"]["execution_outcome"] == "incomplete"


def test_policy_change_invalidates_result(tmp_path, lead, profile, assessment):
    from docktape_lead_agent.domain.policy import load_policy

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy = load_policy()
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    app = Application(
        settings(),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
    )
    first = app.run(lead.model_dump(mode="json"), evidence_file=fixture, policy_path=policy_path)
    policy["version"] = "changed-policy"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    second = app.run(lead.model_dump(mode="json"), evidence_file=fixture, policy_path=policy_path)
    assert first.execution_id != second.execution_id
    assert second.policy_version == "changed-policy"


def test_unresolved_follow_up_does_not_promote(
    tmp_path, monkeypatch, lead, research, profile, assessment
):
    import docktape_lead_agent.workflow.followup as followup_module
    import docktape_lead_agent.workflow.nodes as node_module
    from docktape_lead_agent.domain.submissions import CompetitorBasis

    review = assessment.model_copy(deep=True)
    review.competitor_basis["spendwise_cloud"] = CompetitorBasis.PLAUSIBLE_PARTIAL
    review.compliance_follow_up = "jurisdiction"
    review.follow_up_confidence = 0.95
    monkeypatch.setattr(node_module, "collect_initial", lambda *a: research)
    monkeypatch.setattr(followup_module, "collect_follow_up", lambda *a: research)
    app = Application(
        Settings(
            "https://search.example",
            "https://fetch.example",
            None,
            "jev-test",
            "openai-test",
            "gpt-test",
            None,
        ),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: review,
        research_client=object(),
    )
    result = app.run(lead.model_dump(mode="json"))
    assert result.authoritative_attempt_id == "initial"
    assert "follow_up_unresolved" in result.branch_reasons


def test_old_execution_cannot_republish_over_current(tmp_path, lead, profile, assessment):
    import pytest

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    app = Application(
        settings(),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
    )
    first = app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    second = app.run(lead.model_dump(mode="json"), evidence_file=fixture, refresh=True)
    with pytest.raises(ValueError, match="superseded"):
        app.resume(lead.lead_id, first.execution_id)
    assert app.inspect(lead.lead_id)["manifest"]["execution_id"] == second.execution_id


def test_concurrent_publish_sends_once(tmp_path, lead, profile, assessment):
    from concurrent.futures import ThreadPoolExecutor

    from docktape_lead_agent.domain.submissions import NotificationStatus

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    calls = 0

    def sender(url, message, record, **kwargs):
        nonlocal calls
        calls += 1
        record.status = NotificationStatus.SENT
        return record

    app = Application(
        Settings(
            None, None, None, "jev-test", "openai-test", "gpt-test", "https://hooks.example.test/x"
        ),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
        slack_sender=sender,
    )
    result = app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    from docktape_lead_agent.storage.runs import RunStore

    store = RunStore(tmp_path / "output", lead.lead_id, result.execution_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _: app.publish(store, result.model_copy(deep=True), send=True), range(2)
            )
        )
    assert outcomes == [NotificationStatus.SENT, NotificationStatus.SENT]
    assert calls == 1


def test_standard_langsmith_tracing_does_not_capture_submission(
    tmp_path, monkeypatch, lead, profile, assessment
):
    from langchain_core.tracers.context import collect_runs

    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("DOCKTAPE_LANGSMITH_TRACING", raising=False)
    app = Application(
        settings(),
        tmp_path / "output",
        profile_adapter=lambda *a, **k: (profile, "gpt-test"),
        review_adapter=lambda *a, **k: assessment,
    )
    with collect_runs() as collector:
        app.run(lead.model_dump(mode="json"), evidence_file=fixture)
    assert lead.email not in str([run.inputs for run in collector.traced_runs])
