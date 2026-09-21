from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from assessment import (
    CONFIDENCE_THRESHOLD,
    AssessmentError,
    assess_with_jev,
    build_accepted_profile,
    calculate_fit,
    evaluate_compliance,
    extract_openai_profile,
    load_policy,
    render_summary,
    route_status,
)
from delivery import (
    DeliveryError,
    atomic_write_json,
    atomic_write_text,
    build_notification,
    build_slack_blocks,
    load_delivery,
    send_slack,
    update_workbook,
)
from discovery import (
    DiscoveryClient,
    DiscoveryError,
    assert_public_url,
    collect_follow_up,
    collect_initial,
    load_evidence_fixture,
)
from models import (
    CertificateContext,
    ComplianceOutcome,
    FinalResult,
    FinalStatus,
    LeadSubmission,
    NotificationStatus,
    ResearchAttempt,
)

EXIT_SUCCESS = 0
EXIT_INPUT_OR_CONFIG = 2
EXIT_INCOMPLETE_ASSESSMENT = 3
EXIT_OUTPUT_OR_DELIVERY = 4
ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "data" / "policy.json"


@dataclass(frozen=True)
class Settings:
    searxng_base_url: str | None
    firecrawl_base_url: str | None
    firecrawl_api_key: str | None
    typesafe_api_key: str | None
    openai_api_key: str | None
    openai_model: str | None
    slack_webhook_url: str | None

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            searxng_base_url=os.getenv("SEARXNG_BASE_URL"),
            firecrawl_base_url=os.getenv("FIRECRAWL_BASE_URL"),
            firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY"),
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL"),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        )

    def require_assessment(self, *, synthetic: bool) -> None:
        names = ["TYPESAFE_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL"]
        if not synthetic:
            names.extend(("SEARXNG_BASE_URL", "FIRECRAWL_BASE_URL"))
        values = {
            "SEARXNG_BASE_URL": self.searxng_base_url,
            "FIRECRAWL_BASE_URL": self.firecrawl_base_url,
            "FIRECRAWL_API_KEY": self.firecrawl_api_key,
            "TYPESAFE_API_KEY": self.typesafe_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENAI_MODEL": self.openai_model,
        }
        missing = [name for name in names if not values[name]]
        if missing:
            raise ValueError("missing configuration: " + ", ".join(missing))

    def require_slack(self) -> None:
        if not self.slack_webhook_url:
            raise ValueError("missing configuration: SLACK_WEBHOOK_URL")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Research, screen, and score one sales lead.")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("submission", nargs="?", type=Path, help="JSON lead submission")
    mode.add_argument("--retry-notification", metavar="LEAD_ID", help="retry a saved Slack notification")
    result.add_argument("--output-dir", type=Path, default=Path("output"))
    result.add_argument("--env-file", type=Path, default=Path(".env"))
    result.add_argument("--send-slack", action="store_true")
    result.add_argument("--refresh", action="store_true", help="repeat discovery and assessment")
    result.add_argument("--evidence-file", type=Path, help="explicitly synthetic evidence fixture")
    return result


def sanitize_error(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, ValueError) and text.startswith("missing configuration:"):
        return f"ValueError: {text}"
    lowered = text.casefold()
    if any(word in lowered for word in ("bearer ", "api_key", "apikey", "webhook")):
        return f"{type(exc).__name__}: request failed; credentials redacted"
    return f"{type(exc).__name__}: {text[:1000]}"


def load_submission(path: Path) -> tuple[dict[str, Any], LeadSubmission]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"submission file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"submission is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError("submission must be a JSON object")
    return payload, LeadSubmission.model_validate(payload)


def archive_current_run(run_dir: Path) -> None:
    files = [path for path in run_dir.iterdir() if path.is_file()] if run_dir.exists() else []
    if not files:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = run_dir / "archive" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for path in files:
        shutil.copy2(path, destination / path.name)


def save_attempts(path: Path, attempts: list[dict[str, Any]], *, errors: list[str] | None = None) -> None:
    payload: dict[str, Any] = {"attempts": attempts}
    if errors:
        payload["errors"] = errors
    atomic_write_json(path, payload)


def _evidence_links(research: ResearchAttempt) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for item in research.evidence:
        if item.source_url and item.source_url not in seen:
            seen.add(item.source_url)
            links.append(item.source_url)
    return links


def _is_unresolved(profile: Any, assessment: Any) -> bool:
    if profile is None or assessment is None:
        return True
    identity = assessment.claim_support.get("identity")
    identity_confidence = assessment.claim_confidence.get("identity", 0)
    accepted_contradiction = any(
        verdict.value == "contradicted" and assessment.claim_confidence.get(claim_id, 0) >= CONFIDENCE_THRESHOLD
        for claim_id, verdict in assessment.claim_support.items()
    )
    return (
        identity is None
        or identity.value != "supported"
        or identity_confidence < CONFIDENCE_THRESHOLD
        or accepted_contradiction
        or bool(assessment.validation_errors)
    )


def _existing_run(run_dir: Path) -> FinalResult | None:
    path = run_dir / "result.json"
    if not path.exists():
        return None
    try:
        return FinalResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt saved run must not prevent a fresh assessment
        return None


def repair_saved_outputs(result: FinalResult, output_dir: Path, *, send: bool, settings: Settings) -> int:
    run_dir = output_dir / "runs" / result.lead_id
    message = build_notification(result)
    blocks = build_slack_blocks(result)
    atomic_write_text(run_dir / "notification.txt", message)
    delivery_path = run_dir / "delivery.json"
    record = load_delivery(delivery_path, result.lead_id, message)
    result.notification_status = record.status
    atomic_write_json(run_dir / "result.json", result)
    update_workbook(output_dir / "lead-tracker.xlsx", result)
    if not send:
        atomic_write_json(delivery_path, record)
        return EXIT_INCOMPLETE_ASSESSMENT if result.final_status == FinalStatus.REVIEW_REQUIRED and result.errors else EXIT_SUCCESS
    settings.require_slack()
    assert settings.slack_webhook_url
    record = send_slack(settings.slack_webhook_url, message, record, blocks=blocks)
    atomic_write_json(delivery_path, record)
    result.notification_status = record.status
    atomic_write_json(run_dir / "result.json", result)
    update_workbook(output_dir / "lead-tracker.xlsx", result)
    return EXIT_SUCCESS if record.status == NotificationStatus.SENT else EXIT_OUTPUT_OR_DELIVERY


def process_lead(args: argparse.Namespace, settings: Settings) -> int:
    assert args.submission is not None
    original_submission, lead = load_submission(args.submission)
    output_dir: Path = args.output_dir.resolve()
    run_dir = output_dir / "runs" / lead.lead_id
    existing = _existing_run(run_dir)
    if existing and not args.refresh:
        return repair_saved_outputs(existing, output_dir, send=args.send_slack, settings=settings)

    synthetic = args.evidence_file is not None
    settings.require_assessment(synthetic=synthetic)
    assert_public_url(lead.website)
    if args.refresh:
        archive_current_run(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    atomic_write_json(
        run_dir / "input.json",
        {
            "original": original_submission,
            "validated": lead.model_dump(mode="json"),
            "normalized": lead.normalized_state(),
            "lead_id": lead.lead_id,
        },
    )

    discovery_error: str | None = None
    discovery_client: DiscoveryClient | None = None
    if args.evidence_file:
        research = load_evidence_fixture(args.evidence_file, lead)
    else:
        assert settings.searxng_base_url and settings.firecrawl_base_url
        discovery_client = DiscoveryClient(
            searxng_base_url=settings.searxng_base_url,
            firecrawl_base_url=settings.firecrawl_base_url,
            firecrawl_api_key=settings.firecrawl_api_key,
        )
        try:
            research = collect_initial(lead, discovery_client)
        except Exception as exc:  # noqa: BLE001 - preserve a review result for any discovery failure
            discovery_error = sanitize_error(exc)
            research = ResearchAttempt(
                certificate_context=CertificateContext(apex=lead.hostname, unavailable=True, warning=discovery_error),
                warnings=[discovery_error],
            )
    evidence_attempts = [research.model_dump(mode="json")]
    atomic_write_json(run_dir / "evidence.json", {"attempts": evidence_attempts})

    policy = load_policy(POLICY_PATH)
    profile = None
    assessment = None
    openai_returned_model = None
    profile_attempts: list[dict[str, Any]] = []
    assessment_attempts: list[dict[str, Any]] = []
    profile_errors: list[str] = []
    assessment_errors: list[str] = []
    errors: list[str] = [discovery_error] if discovery_error else []

    try:
        assert settings.openai_api_key and settings.openai_model
        profile, openai_returned_model = extract_openai_profile(
            lead,
            research,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            policy=policy,
        )
        profile_attempts.append(
            {
                "requested_model": settings.openai_model,
                "returned_model": openai_returned_model,
                "profile": profile.model_dump(mode="json"),
            }
        )
    except Exception as exc:  # noqa: BLE001 - SDK failures become sanitized assessment failures
        message = sanitize_error(exc)
        errors.append(message)
        profile_errors.append(message)

    if profile is not None:
        try:
            assert settings.typesafe_api_key
            assessment = assess_with_jev(lead, research, profile, policy, api_key=settings.typesafe_api_key)
            assessment_attempts.append(assessment.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - API failures become sanitized assessment failures
            message = sanitize_error(exc)
            errors.append(message)
            assessment_errors.append(message)

    if profile is not None and assessment is not None and discovery_client is not None:
        _, initial_outcome = evaluate_compliance(profile, assessment, research, policy)
        target = assessment.compliance_follow_up
        if (
            initial_outcome == ComplianceOutcome.REVIEW_REQUIRED
            and target != "none"
            and assessment.follow_up_confidence >= CONFIDENCE_THRESHOLD
        ):
            research = collect_follow_up(lead, discovery_client, target, research)
            evidence_attempts.append(research.model_dump(mode="json"))
            atomic_write_json(run_dir / "evidence.json", {"attempts": evidence_attempts})
            if research.follow_up_failed:
                message = "Compliance follow-up failed; the initial assessment remains authoritative."
                errors.append(message)
                assessment_errors.append(message)
            else:
                try:
                    candidate_profile, candidate_openai_model = extract_openai_profile(
                        lead,
                        research,
                        api_key=settings.openai_api_key,
                        model=settings.openai_model,
                        policy=policy,
                    )
                    profile_attempts.append(
                        {
                            "requested_model": settings.openai_model,
                            "returned_model": candidate_openai_model,
                            "profile": candidate_profile.model_dump(mode="json"),
                        }
                    )
                    candidate_assessment = assess_with_jev(
                        lead,
                        research,
                        candidate_profile,
                        policy,
                        api_key=settings.typesafe_api_key,
                    )
                    assessment_attempts.append(candidate_assessment.model_dump(mode="json"))
                    profile = candidate_profile
                    assessment = candidate_assessment
                    openai_returned_model = candidate_openai_model
                except Exception as exc:  # noqa: BLE001 - preserve the first assessment after follow-up failure
                    message = f"Follow-up reassessment failed: {sanitize_error(exc)}"
                    errors.append(message)
                    profile_errors.append(message)
                    assessment_errors.append(message)
    elif profile is not None and assessment is not None and research.synthetic:
        _, initial_outcome = evaluate_compliance(profile, assessment, research, policy)
        if (
            initial_outcome == ComplianceOutcome.REVIEW_REQUIRED
            and assessment.compliance_follow_up != "none"
            and assessment.follow_up_confidence >= CONFIDENCE_THRESHOLD
        ):
            research.warnings.append("Synthetic fixture cannot perform a network compliance follow-up; review remains required.")
    if discovery_client is not None:
        discovery_client.close()

    save_attempts(run_dir / "openai-profile.json", profile_attempts, errors=profile_errors)
    save_attempts(run_dir / "jev-assessment.json", assessment_attempts, errors=assessment_errors)

    fit = calculate_fit(assessment)
    checks, compliance = evaluate_compliance(profile, assessment, research, policy)
    summary = render_summary(profile, assessment)
    final_status = route_status(
        fit,
        compliance,
        unresolved_conflict=_is_unresolved(profile, assessment),
        assessment_failed=profile is None or assessment is None or research.follow_up_failed or bool(assessment_errors),
    )
    result = FinalResult(
        lead_id=lead.lead_id,
        submission=lead,
        original_submission=original_submission,
        accepted_profile=build_accepted_profile(profile, assessment) if profile is not None and assessment is not None else None,
        fit=fit,
        compliance_checks=checks,
        compliance_outcome=compliance,
        final_status=final_status,
        summary=summary,
        evidence_references=_evidence_links(research),
        policy_version=policy["version"],
        openai_requested_model=settings.openai_model,
        openai_returned_model=openai_returned_model,
        jev_requested_model="jev-latest",
        jev_resolved_model=assessment.resolved_model if assessment else None,
        warnings=research.warnings,
        errors=errors,
        synthetic=research.synthetic,
        processing_seconds=round(time.monotonic() - started, 3),
    )
    atomic_write_json(run_dir / "result.json", result)
    message = build_notification(result)
    blocks = build_slack_blocks(result)
    atomic_write_text(run_dir / "notification.txt", message)
    delivery = load_delivery(run_dir / "delivery.json", lead.lead_id, message)
    atomic_write_json(run_dir / "delivery.json", delivery)

    try:
        update_workbook(output_dir / "lead-tracker.xlsx", result)
    except Exception as exc:  # noqa: BLE001 - persistence implementations can raise several exception types
        print(f"output error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_OUTPUT_OR_DELIVERY

    if args.send_slack:
        try:
            settings.require_slack()
            assert settings.slack_webhook_url
            delivery = send_slack(settings.slack_webhook_url, message, delivery, blocks=blocks)
            atomic_write_json(run_dir / "delivery.json", delivery)
            result.notification_status = delivery.status
            atomic_write_json(run_dir / "result.json", result)
            update_workbook(output_dir / "lead-tracker.xlsx", result)
        except Exception as exc:  # noqa: BLE001 - delivery and workbook failures share an exit category
            print(f"delivery error: {sanitize_error(exc)}", file=sys.stderr)
            return EXIT_OUTPUT_OR_DELIVERY
        if delivery.status != NotificationStatus.SENT:
            return EXIT_OUTPUT_OR_DELIVERY

    print(json.dumps({"lead_id": result.lead_id, "status": result.final_status, "score": result.fit.score}))
    if errors:
        return EXIT_INCOMPLETE_ASSESSMENT
    return EXIT_SUCCESS


def retry_notification(lead_id: str, output_dir: Path, settings: Settings) -> int:
    settings.require_slack()
    run_dir = output_dir.resolve() / "runs" / lead_id
    result_path = run_dir / "result.json"
    notification_path = run_dir / "notification.txt"
    if not result_path.exists() or not notification_path.exists():
        raise ValueError(f"saved result or notification does not exist for lead {lead_id}")
    result = FinalResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    message = notification_path.read_text(encoding="utf-8")
    blocks = build_slack_blocks(result)
    update_workbook(output_dir.resolve() / "lead-tracker.xlsx", result)
    delivery_path = run_dir / "delivery.json"
    record = load_delivery(delivery_path, lead_id, message)
    assert settings.slack_webhook_url
    record = send_slack(
        settings.slack_webhook_url,
        message,
        record,
        explicit_retry=True,
        blocks=blocks,
    )
    atomic_write_json(delivery_path, record)
    result.notification_status = record.status
    atomic_write_json(result_path, result)
    try:
        update_workbook(output_dir / "lead-tracker.xlsx", result)
    except Exception as exc:  # noqa: BLE001 - delivery is already durable before this best-effort refresh
        print(f"tracker refresh failed after delivery: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_OUTPUT_OR_DELIVERY
    return EXIT_SUCCESS if record.status == NotificationStatus.SENT else EXIT_OUTPUT_OR_DELIVERY


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.retry_notification and (args.refresh or args.evidence_file or args.send_slack):
        print("--retry-notification cannot be combined with --refresh, --evidence-file, or --send-slack", file=sys.stderr)
        return EXIT_INPUT_OR_CONFIG
    load_dotenv(args.env_file, override=False)
    settings = Settings.from_environment()
    try:
        if args.retry_notification:
            return retry_notification(args.retry_notification, args.output_dir, settings)
        return process_lead(args, settings)
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError, DiscoveryError, AssessmentError) as exc:
        print(f"input or configuration error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_INPUT_OR_CONFIG
    except DeliveryError as exc:
        print(f"output error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_OUTPUT_OR_DELIVERY


if __name__ == "__main__":
    raise SystemExit(main())
