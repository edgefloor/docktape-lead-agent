from __future__ import annotations

from typing import Any

from ..domain.results import FinalResult
from ..domain.submissions import ComplianceOutcome


def _escape_slack(value: str) -> str:
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return value.replace("@", "＠")


def _display_label(value: Any) -> str:
    return str(value).replace("_", " ").capitalize()


def _primary_compliance_reason(result: FinalResult) -> str:
    if result.compliance_outcome == ComplianceOutcome.CLEAR:
        return "All competitor and country checks cleared under the exercise policy."
    matching = [
        check for check in result.compliance_checks if check.outcome == result.compliance_outcome
    ]
    business_reasons = [check for check in matching if check.kind != "assessment"]
    selected = (
        (business_reasons or matching or result.compliance_checks)[0]
        if (business_reasons or matching or result.compliance_checks)
        else None
    )
    if selected is None:
        return f"Compliance is {_display_label(result.compliance_outcome).casefold()}."
    if (
        selected.kind == "competitor"
        and selected.policy_entry == "competitive_scope"
        and selected.outcome == ComplianceOutcome.FLAGGED
    ):
        return "This company offers cloud cost management services that directly compete with us."
    return " ".join(selected.reason.split())


def build_notification(result: FinalResult) -> str:
    score = "Unknown" if result.fit.score is None else str(result.fit.score)
    prefix = "[SYNTHETIC TEST] " if result.synthetic else ""
    status = _display_label(result.final_status)
    score_status = _display_label(result.fit.score_status)
    compliance = _display_label(result.compliance_outcome)
    company = _escape_slack(result.submission.company_name)
    summary = _escape_slack(result.summary)
    reason = _escape_slack(_primary_compliance_reason(result))
    message = (
        f"{prefix}Lead assessment\n"
        f"\nStatus: {status}\n"
        f"Company: {company}\n"
        f"Fit: {score} ({score_status})\n"
        f"\nSummary\n{summary}\n"
        f"\nCompliance\n{compliance}\n{reason}"
    )
    return message


def build_slack_blocks(result: FinalResult) -> list[dict[str, Any]]:
    score = "Unknown" if result.fit.score is None else str(result.fit.score)
    title = "Lead assessment"
    if result.synthetic:
        title = "[SYNTHETIC TEST] Lead assessment"
    reason = _escape_slack(_primary_compliance_reason(result))
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status*\n{_display_label(result.final_status)}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Company*\n{_escape_slack(result.submission.company_name)}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Fit*\n{score} ({_display_label(result.fit.score_status)})",
                },
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary*\n{_escape_slack(result.summary)}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Compliance*\n{_display_label(result.compliance_outcome)}\n{reason}",
            },
        },
    ]
