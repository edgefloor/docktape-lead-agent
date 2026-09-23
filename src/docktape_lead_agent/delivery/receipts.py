from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..domain.results import DeliveryRecord
from ..domain.submissions import NotificationStatus
from ..storage.files import DeliveryError, atomic_write_json

PAYLOAD_VERSION = 1


def notification_payload(message: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": PAYLOAD_VERSION, "text": message, "blocks": blocks}


def message_hash(message: str, blocks: list[dict[str, Any]] | None = None) -> str:
    payload = notification_payload(message, blocks or [])
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_delivery(
    path: Path,
    lead_id: str,
    message: str,
    blocks: list[dict[str, Any]] | None = None,
    destination: str | None = None,
) -> DeliveryRecord:
    digest = message_hash(message, blocks)
    destination_digest = hashlib.sha256(destination.encode()).hexdigest() if destination else None
    if not path.exists():
        return DeliveryRecord(
            lead_id=lead_id, message_sha256=digest, destination_sha256=destination_digest
        )
    try:
        record = DeliveryRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeliveryError("saved delivery record is invalid") from exc
    if (
        record.lead_id != lead_id
        or record.message_sha256 != digest
        or (destination_digest is not None and record.destination_sha256 != destination_digest)
    ):
        return DeliveryRecord(
            lead_id=lead_id, message_sha256=digest, destination_sha256=destination_digest
        )
    return record


def save_intent(path: Path, record: DeliveryRecord) -> None:
    pending = record.model_copy(deep=True)
    pending.status = NotificationStatus.UNKNOWN
    atomic_write_json(path, pending.model_dump(mode="json"))
