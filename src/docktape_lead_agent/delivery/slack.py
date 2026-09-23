from __future__ import annotations

import time
from typing import Any

import httpx

from ..domain.evidence import utc_now
from ..domain.results import DeliveryAttempt, DeliveryRecord
from ..domain.submissions import NotificationStatus
from .receipts import message_hash


def send_slack(
    webhook_url: str,
    message: str,
    record: DeliveryRecord,
    *,
    explicit_retry: bool = False,
    blocks: list[dict[str, Any]] | None = None,
    client: httpx.Client | None = None,
) -> DeliveryRecord:
    if record.status == NotificationStatus.SENT and record.message_sha256 == message_hash(
        message, blocks
    ):
        return record
    if (
        record.status in {NotificationStatus.FAILED, NotificationStatus.UNKNOWN}
        and not explicit_retry
    ):
        return record
    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(connect=10, read=15, write=15, pool=10))
    duplicate_possible = explicit_retry and record.status == NotificationStatus.UNKNOWN
    payload: dict[str, Any] = {"text": message}
    if blocks:
        payload["blocks"] = blocks
    try:
        for attempt_number in range(2):
            try:
                response = http.post(webhook_url, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt_number == 0:
                    time.sleep(0.5)
                    continue
                attempt = DeliveryAttempt(
                    status=NotificationStatus.FAILED,
                    error=f"{type(exc).__name__}: connection was not established",
                    duplicate_possible=duplicate_possible,
                )
                record.attempts.append(attempt)
                record.status = attempt.status
                return record
            except (
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.WriteError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ) as exc:
                attempt = DeliveryAttempt(
                    status=NotificationStatus.UNKNOWN,
                    error=f"{type(exc).__name__}: Slack acceptance could not be confirmed",
                    duplicate_possible=True,
                )
                record.attempts.append(attempt)
                record.status = attempt.status
                return record
            if response.status_code == 429 and attempt_number == 0:
                try:
                    delay = min(max(float(response.headers.get("Retry-After", "0.5")), 0), 3)
                except ValueError:
                    delay = 0.5
                time.sleep(delay)
                continue
            acknowledgment = response.text[:500]
            if response.status_code == 200 and acknowledgment.strip() == "ok":
                attempt = DeliveryAttempt(
                    status=NotificationStatus.SENT,
                    acknowledgment=acknowledgment,
                    duplicate_possible=duplicate_possible,
                )
                record.attempts.append(attempt)
                record.status = NotificationStatus.SENT
                record.sent_at = utc_now()
                record.acknowledgment = acknowledgment
                record.message_identifier = None
                return record
            if response.status_code >= 500:
                status = NotificationStatus.UNKNOWN
                error = f"Slack returned HTTP {response.status_code}; acceptance is uncertain"
            else:
                status = NotificationStatus.FAILED
                error = f"Slack returned HTTP {response.status_code}: {acknowledgment}"
            attempt = DeliveryAttempt(
                status=status,
                acknowledgment=acknowledgment,
                error=error,
                duplicate_possible=duplicate_possible or status == NotificationStatus.UNKNOWN,
            )
            record.attempts.append(attempt)
            record.status = status
            return record
    finally:
        if owns_client:
            http.close()
    return record
