from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langsmith import tracing_context

from .delivery.receipts import load_delivery, save_intent
from .delivery.slack import send_slack
from .domain.policy import load_policy
from .domain.results import FinalResult
from .domain.submissions import LeadSubmission, NotificationStatus
from .inference.jev_review import assess_with_jev
from .inference.profile import extract_openai_profile
from .observability import HostedTrace
from .reporting.notification import build_notification, build_slack_blocks
from .reporting.workbook import update_workbook
from .research.clients import DiscoveryClient
from .research.urls import assert_public_url
from .settings import Settings
from .storage.checkpoints import open_checkpointer
from .storage.files import atomic_write_json, atomic_write_text, file_lock
from .storage.runs import WORKFLOW_VERSION, RunStore, digest
from .workflow.graph import build_graph
from .workflow.nodes import WorkflowNodes

PROFILE_VERSION = "profile-v3"
JEV_SCHEMA_VERSION = "jev-v2"


class Application:
    def __init__(
        self,
        settings: Settings,
        output_dir: Path,
        *,
        profile_adapter=extract_openai_profile,
        review_adapter=assess_with_jev,
        research_client: Any = None,
        slack_sender=send_slack,
    ):
        self.settings = settings
        self.output_dir = output_dir.resolve()
        self.profile_adapter = profile_adapter
        self.review_adapter = review_adapter
        self.research_client = research_client
        self.slack_sender = slack_sender

    def fingerprint(
        self,
        lead: LeadSubmission,
        policy: dict[str, Any],
        synthetic: bool,
        evidence_file: Path | None,
    ) -> str:
        return digest(
            {
                "company": lead.company_name.casefold(),
                "website": lead.website,
                "employee_count_band": lead.employee_count_band.value,
                "policy": policy,
                "openai_model": self.settings.openai_model,
                "jev_model": "jev-latest",
                "profile_version": PROFILE_VERSION,
                "profile_prompt_version": PROFILE_VERSION,
                "jev_schema_version": JEV_SCHEMA_VERSION,
                "workflow_version": WORKFLOW_VERSION,
                "synthetic": synthetic,
                "fixture": digest(evidence_file.read_text(encoding="utf-8"))
                if evidence_file
                else None,
            }
        )

    def run(
        self,
        original: dict[str, Any],
        *,
        evidence_file: Path | None = None,
        refresh: bool = False,
        send: bool = False,
        policy_path: Path | None = None,
    ) -> FinalResult:
        lead = LeadSubmission.model_validate(original)
        assert_public_url(lead.website, resolve=False)
        policy = load_policy(policy_path)
        fingerprint = self.fingerprint(lead, policy, evidence_file is not None, evidence_file)
        reusable = (
            None if refresh else RunStore.reusable(self.output_dir, lead.lead_id, fingerprint)
        )
        if reusable:
            store, result = reusable
            store.event("reuse", "selected", reason="fingerprint_and_freshness_match")
            self.publish(store, result, send=send)
            return result
        self.settings.require_assessment(synthetic=evidence_file is not None)
        store = RunStore.create(
            self.output_dir,
            lead.lead_id,
            fingerprint,
            configuration={
                "policy_version": policy["version"],
                "policy_effective_date": policy["effective_date"],
                "policy_sha256": digest(policy),
                "profile_version": PROFILE_VERSION,
                "jev_schema_version": JEV_SCHEMA_VERSION,
                "openai_requested_model": self.settings.openai_model,
                "jev_requested_model": "jev-latest",
            },
        )
        store.trace = HostedTrace(lead.lead_id, store.execution_id)
        initial = {
            "lead": lead.model_dump(mode="json"),
            "original": original,
            "policy": policy,
            "execution_id": store.execution_id,
            "errors": [],
            "branch_reasons": [],
            "evidence_file": str(evidence_file.resolve()) if evidence_file else None,
        }
        store.write("input.json", initial)
        graph_input = {"execution_id": store.execution_id, "errors": [], "branch_reasons": []}
        store.event("reuse", "bypassed", reason="refresh" if refresh else "no_eligible_result")
        owned_client = None
        research_client = self.research_client
        if not evidence_file and research_client is None:
            owned_client = DiscoveryClient(
                searxng_base_url=self.settings.searxng_base_url,
                firecrawl_base_url=self.settings.firecrawl_base_url,
                firecrawl_api_key=self.settings.firecrawl_api_key,
                on_attempt=lambda provider, attempt, outcome, elapsed: store.event(
                    "research_http",
                    outcome,
                    provider=provider,
                    retry_count=attempt,
                    duration_ms=round(elapsed * 1000, 2),
                ),
            )
            research_client = owned_client
        try:
            nodes = WorkflowNodes(
                store,
                settings=self.settings,
                evidence_file=evidence_file,
                research_client=research_client,
                profile_adapter=self.profile_adapter,
                review_adapter=self.review_adapter,
            )
            with tracing_context(enabled=False), open_checkpointer(self.output_dir) as checkpoint:
                graph = build_graph(nodes, checkpoint)
                output = graph.invoke(
                    graph_input, config={"configurable": {"thread_id": store.execution_id}}
                )
            result = FinalResult.model_validate(store.read(output["result_ref"]))
        finally:
            if owned_client:
                owned_client.close()
            if "result" not in locals():
                store.trace.close("failed")
        self.publish(store, result, send=send)
        store.trace.close(result.execution_outcome)
        return result

    def resume(self, lead_id: str, execution_id: str, *, send: bool = False) -> FinalResult:
        pointer_path = self.output_dir / "runs" / lead_id / "current.json"
        if pointer_path.exists():
            current = json.loads(pointer_path.read_text(encoding="utf-8"))
            if current.get("execution_id") != execution_id:
                raise ValueError("cannot resume a superseded execution")
        store = RunStore(self.output_dir, lead_id, execution_id)
        store.trace = HostedTrace(lead_id, execution_id)
        initial = store.read("input.json")
        if store.exists("result.json"):
            result = FinalResult.model_validate(store.read("result.json"))
            self.publish(store, result, send=send)
            return result
        evidence_file = Path(initial["evidence_file"]) if initial.get("evidence_file") else None
        self.settings.require_assessment(synthetic=evidence_file is not None)
        client = self.research_client or (
            None
            if evidence_file
            else DiscoveryClient(
                searxng_base_url=self.settings.searxng_base_url,
                firecrawl_base_url=self.settings.firecrawl_base_url,
                firecrawl_api_key=self.settings.firecrawl_api_key,
                on_attempt=lambda provider, attempt, outcome, elapsed: store.event(
                    "research_http",
                    outcome,
                    provider=provider,
                    retry_count=attempt,
                    duration_ms=round(elapsed * 1000, 2),
                ),
            )
        )
        try:
            nodes = WorkflowNodes(
                store,
                settings=self.settings,
                research_client=client,
                evidence_file=evidence_file,
                profile_adapter=self.profile_adapter,
                review_adapter=self.review_adapter,
            )
            with tracing_context(enabled=False), open_checkpointer(self.output_dir) as checkpoint:
                graph = build_graph(nodes, checkpoint)
                output = graph.invoke(None, config={"configurable": {"thread_id": execution_id}})
            result = FinalResult.model_validate(store.read(output["result_ref"]))
        finally:
            if self.research_client is None and client is not None:
                client.close()
        self.publish(store, result, send=send)
        store.trace.close(result.execution_outcome)
        return result

    def publish(
        self,
        store: RunStore,
        result: FinalResult,
        *,
        send: bool = False,
        explicit_retry: bool = False,
    ) -> NotificationStatus:
        message = build_notification(result)
        blocks = build_slack_blocks(result)
        atomic_write_text(store.path / "notification.txt", message)
        with file_lock(self.output_dir / ".tracker.lock"):
            update_workbook(self.output_dir / "lead-tracker.xlsx", result)
        delivery_path = store.lead_dir / "delivery.json"
        with file_lock(store.lead_dir / ".delivery.lock"):
            record = load_delivery(
                delivery_path,
                result.lead_id,
                message,
                blocks,
                destination=self.settings.slack_webhook_url if send else None,
            )
            if send and record.status != NotificationStatus.SENT:
                self.settings.require_slack()
                if record.status == NotificationStatus.NOT_REQUESTED or explicit_retry:
                    save_intent(delivery_path, record)
                    store.write("delivery-intent.json", json.loads(delivery_path.read_text()))
                    store.event(
                        "slack",
                        "intent_saved",
                        reason="explicit_retry" if explicit_retry else "initial_send",
                    )
                    record = self.slack_sender(
                        self.settings.slack_webhook_url,
                        message,
                        record,
                        explicit_retry=explicit_retry,
                        blocks=blocks,
                    )
                    store.event("slack", record.status.value, reason="transport_returned")
            atomic_write_json(delivery_path, record.model_dump(mode="json"))
            store.write("delivery.json", record.model_dump(mode="json"))
            result.notification_status = record.status
            atomic_write_json(store.path / "result.json", result.model_dump(mode="json"))
            atomic_write_json(store.lead_dir / "result.json", result.model_dump(mode="json"))
        with file_lock(self.output_dir / ".tracker.lock"):
            update_workbook(self.output_dir / "lead-tracker.xlsx", result)
        return record.status

    def retry_notification(self, lead_id: str) -> NotificationStatus:
        pointer = json.loads((self.output_dir / "runs" / lead_id / "current.json").read_text())
        store = RunStore(self.output_dir, lead_id, pointer["execution_id"])
        result = FinalResult.model_validate(store.read("result.json"))
        return self.publish(store, result, send=True, explicit_retry=True)

    def inspect(self, lead_id: str, execution_id: str | None = None) -> dict[str, Any]:
        if execution_id is None:
            pointer = json.loads((self.output_dir / "runs" / lead_id / "current.json").read_text())
            execution_id = pointer["execution_id"]
        store = RunStore(self.output_dir, lead_id, execution_id)
        events = [
            json.loads(line) for line in (store.path / "events.jsonl").read_text().splitlines()
        ]
        return {
            "manifest": store.read("manifest.json"),
            "result": store.read("result.json") if store.exists("result.json") else None,
            "events": events,
        }
