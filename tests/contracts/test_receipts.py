from docktape_lead_agent.delivery.receipts import load_delivery, message_hash, save_intent
from docktape_lead_agent.domain.submissions import NotificationStatus


def test_payload_hash_includes_blocks_and_intent_is_uncertain(tmp_path):
    path = tmp_path / "delivery.json"
    first = load_delivery(path, "lead", "same text", [{"type": "section", "text": "one"}])
    assert first.message_sha256 != message_hash("same text", [{"type": "section", "text": "two"}])
    save_intent(path, first)
    restored = load_delivery(path, "lead", "same text", [{"type": "section", "text": "one"}])
    assert restored.status == NotificationStatus.UNKNOWN


def test_receipt_changes_with_slack_destination(tmp_path):
    from docktape_lead_agent.storage.files import atomic_write_json

    path = tmp_path / "delivery.json"
    old = load_delivery(path, "lead", "text", [], destination="https://hooks.example/old")
    old.status = NotificationStatus.SENT
    atomic_write_json(path, old)
    new = load_delivery(path, "lead", "text", [], destination="https://hooks.example/new")
    assert new.status == NotificationStatus.NOT_REQUESTED
    assert new.destination_sha256 != old.destination_sha256
