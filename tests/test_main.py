from __future__ import annotations

import hashlib
import json
from argparse import Namespace

import main
from delivery import atomic_write_json
from main import Settings, process_lead
from models import DeliveryRecord, NotificationStatus


def test_live_assessment_does_not_require_firecrawl_api_key() -> None:
    settings = Settings(
        searxng_base_url="https://search.example.test",
        firecrawl_base_url="https://firecrawl.example.test",
        firecrawl_api_key=None,
        typesafe_api_key="typesafe-test",
        openai_api_key="openai-test",
        openai_model="gpt-test",
        slack_webhook_url=None,
    )

    settings.require_assessment(synthetic=False)


def test_refresh_reuses_confirmed_delivery_for_unchanged_message(
    tmp_path, monkeypatch, lead, profile, assessment
) -> None:
    submission_path = tmp_path / "lead.json"
    submission_path.write_text(lead.model_dump_json(), encoding="utf-8")
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"synthetic": True, "evidence": []}), encoding="utf-8")
    output_dir = tmp_path / "output"
    run_dir = output_dir / "runs" / lead.lead_id
    run_dir.mkdir(parents=True)
    message = "unchanged notification"
    atomic_write_json(
        run_dir / "delivery.json",
        DeliveryRecord(
            lead_id=lead.lead_id,
            message_sha256=hashlib.sha256(message.encode()).hexdigest(),
            status=NotificationStatus.SENT,
        ),
    )

    monkeypatch.setattr(main, "assert_public_url", lambda _url: None)
    monkeypatch.setattr(main, "extract_openai_profile", lambda *_args, **_kwargs: (profile, "gpt-test"))
    monkeypatch.setattr(main, "assess_with_jev", lambda *_args, **_kwargs: assessment)
    monkeypatch.setattr(main, "build_notification", lambda _result: message)
    seen_statuses = []

    def capture_send(_url, _message, record, **_kwargs):
        seen_statuses.append(record.status)
        return record

    monkeypatch.setattr(main, "send_slack", capture_send)
    args = Namespace(
        submission=submission_path,
        output_dir=output_dir,
        send_slack=True,
        refresh=True,
        evidence_file=evidence_path,
    )
    settings = Settings(
        searxng_base_url=None,
        firecrawl_base_url=None,
        firecrawl_api_key=None,
        typesafe_api_key="typesafe-test",
        openai_api_key="openai-test",
        openai_model="gpt-test",
        slack_webhook_url="https://hooks.slack.test/x",
    )

    assert process_lead(args, settings) == 0
    assert seen_statuses == [NotificationStatus.SENT]
