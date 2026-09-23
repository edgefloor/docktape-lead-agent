from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langsmith import Client


def safe_error(exc: BaseException) -> str:
    message = str(exc)[:400]
    if any(
        marker in message.casefold()
        for marker in ("bearer ", "authorization", "api_key", "apikey", "webhook")
    ):
        return f"{type(exc).__name__}: request failed; credentials redacted"
    return f"{type(exc).__name__}: {message}"


class HostedTrace:
    """Optional operational spans. Raw submissions and evidence stay in local artifacts."""

    def __init__(self, lead_id: str, execution_id: str):
        self.client: Client | None = None
        self.parent_id = uuid4()
        self.project = os.getenv("LANGSMITH_PROJECT", "docktape-lead-agent")
        if os.getenv("DOCKTAPE_LANGSMITH_TRACING", "").lower() not in {"1", "true", "yes"}:
            return
        if not os.getenv("LANGSMITH_API_KEY"):
            return
        try:
            self.client = Client(auto_batch_tracing=False)
            self.client.create_run(
                "lead_qualification",
                {"lead_id": lead_id, "execution_id": execution_id},
                "chain",
                id=self.parent_id,
                start_time=datetime.now(UTC),
                project_name=self.project,
            )
        except Exception:
            self.client = None

    def start(self, name: str) -> Any:
        if self.client is None:
            return None
        identifier = uuid4()
        try:
            self.client.create_run(
                name,
                {},
                "tool",
                id=identifier,
                parent_run_id=self.parent_id,
                start_time=datetime.now(UTC),
                project_name=self.project,
            )
            return identifier
        except Exception:
            return None

    def finish(self, identifier: Any, outcome: str, duration_ms: float) -> None:
        if self.client is None or identifier is None:
            return
        try:
            self.client.update_run(
                identifier,
                end_time=datetime.now(UTC),
                outputs={"outcome": outcome, "duration_ms": duration_ms},
            )
        except Exception:
            pass

    def operation(
        self,
        name: str,
        outcome: str,
        *,
        provider: str | None = None,
        retry_count: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self.client is None:
            return
        identifier = self.start(name)
        if identifier is None:
            return
        try:
            self.client.update_run(
                identifier,
                end_time=datetime.now(UTC),
                outputs={
                    "outcome": outcome,
                    "provider": provider,
                    "retry_count": retry_count,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            pass

    def close(self, outcome: str) -> None:
        if self.client is None:
            return
        try:
            self.client.update_run(
                self.parent_id, end_time=datetime.now(UTC), outputs={"outcome": outcome}
            )
            self.client.flush(timeout=5)
        except Exception:
            pass
