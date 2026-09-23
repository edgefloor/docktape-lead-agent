from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..domain.results import FinalResult
from ..observability import safe_error
from .files import atomic_write_json

SCHEMA_VERSION = 2
WORKFLOW_VERSION = "2.1"


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class RunStore:
    def __init__(self, output_dir: Path, lead_id: str, execution_id: str):
        self.output_dir = output_dir
        self.lead_id = lead_id
        self.execution_id = execution_id
        self.lead_dir = output_dir / "runs" / lead_id
        self.path = self.lead_dir / "executions" / execution_id
        self.path.mkdir(parents=True, exist_ok=True)
        self.trace = None

    @classmethod
    def create(
        cls,
        output_dir: Path,
        lead_id: str,
        fingerprint: str,
        configuration: dict[str, Any] | None = None,
    ) -> RunStore:
        store = cls(output_dir, lead_id, uuid4().hex)
        store.write(
            "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "workflow_version": WORKFLOW_VERSION,
                "lead_id": lead_id,
                "execution_id": store.execution_id,
                "input_fingerprint": fingerprint,
                "configuration": configuration or {},
                "started_at": datetime.now(UTC).isoformat(),
                "execution_outcome": "running",
                "authoritative_attempt_id": None,
                "stages": {},
            },
        )
        return store

    def write(self, name: str, value: Any) -> None:
        atomic_write_json(self.path / name, value)

    def read(self, name: str) -> Any:
        return json.loads((self.path / name).read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return (self.path / name).exists()

    def event(self, stage: str, outcome: str, **fields: Any) -> None:
        allow = {
            "attempt_id",
            "evidence_snapshot",
            "reason",
            "duration_ms",
            "provider",
            "retry_count",
            "requested_model",
            "resolved_model",
            "error",
            "budget",
        }
        record = {
            "execution_id": self.execution_id,
            "lead_id": self.lead_id,
            "stage": stage,
            "outcome": outcome,
            "at": datetime.now(UTC).isoformat(),
            **{key: value for key, value in fields.items() if key in allow},
        }
        self.path.mkdir(parents=True, exist_ok=True)
        if self.trace and stage in {
            "research_http",
            "openai_http",
            "jev_http",
            "openai",
            "jev",
            "slack",
        }:
            self.trace.operation(
                stage,
                outcome,
                provider=fields.get("provider"),
                retry_count=fields.get("retry_count"),
                duration_ms=fields.get("duration_ms"),
            )
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def stage(self, name: str, operation: Any) -> dict[str, Any]:
        artifact = f"stage-{name}.json"
        if self.exists(artifact):
            return self.read(artifact)["updates"]
        started = datetime.now(UTC)
        self.event(name, "started")
        span = self.trace.start(name) if self.trace else None
        try:
            updates = operation()
            outcome = "completed"
        except Exception as exc:
            ended = datetime.now(UTC)
            duration = round((ended - started).total_seconds() * 1000, 2)
            manifest = self.read("manifest.json")
            manifest["stages"][name] = {"outcome": "failed", "duration_ms": duration}
            manifest["execution_outcome"] = "failed"
            self.write("manifest.json", manifest)
            self.event(name, "failed", duration_ms=duration, error=safe_error(exc))
            if self.trace:
                self.trace.finish(span, "failed", duration)
            raise
        ended = datetime.now(UTC)
        duration = round((ended - started).total_seconds() * 1000, 2)
        self.write(
            artifact,
            {
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "outcome": outcome,
                "updates": updates,
            },
        )
        manifest = self.read("manifest.json")
        manifest["stages"][name] = {"outcome": outcome, "duration_ms": duration}
        self.write("manifest.json", manifest)
        self.event(
            name, outcome, duration_ms=duration, error=next(iter(updates.get("errors", [])), None)
        )
        if self.trace:
            self.trace.finish(span, outcome, duration)
        return updates

    def attempt(self, attempt_id: str, **fields: Any) -> None:
        name = f"attempt-{attempt_id}.json"
        value = (
            self.read(name)
            if self.exists(name)
            else {
                "schema_version": SCHEMA_VERSION,
                "attempt_id": attempt_id,
            }
        )
        value.update(fields)
        self.write(name, value)

    def finalize(self, result: FinalResult, authoritative_attempt_id: str | None) -> None:
        self.write("result.json", result.model_dump(mode="json"))
        manifest = self.read("manifest.json")
        manifest["authoritative_attempt_id"] = authoritative_attempt_id
        manifest["execution_outcome"] = "completed" if not result.errors else "incomplete"
        manifest["completed_at"] = datetime.now(UTC).isoformat()
        self.write("manifest.json", manifest)
        atomic_write_json(
            self.lead_dir / "current.json",
            {
                "schema_version": SCHEMA_VERSION,
                "execution_id": self.execution_id,
                "input_fingerprint": manifest["input_fingerprint"],
                "selected_at": datetime.now(UTC).isoformat(),
            },
        )
        atomic_write_json(self.lead_dir / "result.json", result.model_dump(mode="json"))

    @classmethod
    def reusable(
        cls, output_dir: Path, lead_id: str, fingerprint: str
    ) -> tuple[RunStore, FinalResult] | None:
        pointer = output_dir / "runs" / lead_id / "current.json"
        if not pointer.exists():
            return None
        data = json.loads(pointer.read_text(encoding="utf-8"))
        if (
            data.get("schema_version") != SCHEMA_VERSION
            or data.get("input_fingerprint") != fingerprint
        ):
            return None
        selected = datetime.fromisoformat(data["selected_at"])
        if datetime.now(UTC) - selected > timedelta(hours=24):
            return None
        store = cls(output_dir, lead_id, data["execution_id"])
        manifest = store.read("manifest.json")
        if manifest["execution_outcome"] != "completed":
            return None
        return store, FinalResult.model_validate(store.read("result.json"))
