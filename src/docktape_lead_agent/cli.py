from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from .application import Application
from .settings import Settings


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Research, screen, and score one sales lead.")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("submission", nargs="?", type=Path, help="JSON lead submission")
    mode.add_argument("--retry-notification", metavar="LEAD_ID")
    mode.add_argument("--inspect", metavar="LEAD_ID")
    mode.add_argument("--resume", nargs=2, metavar=("LEAD_ID", "EXECUTION_ID"))
    result.add_argument("--output-dir", type=Path, default=Path("output"))
    result.add_argument("--env-file", type=Path, default=Path(".env"))
    result.add_argument("--policy", type=Path, help="override the packaged dated policy")
    result.add_argument("--send-slack", action="store_true")
    result.add_argument("--refresh", action="store_true")
    result.add_argument("--evidence-file", type=Path, help="explicit synthetic evidence fixture")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_dotenv(args.env_file, override=False)
    application = Application(Settings.from_environment(), args.output_dir)
    try:
        if args.inspect:
            print(json.dumps(application.inspect(args.inspect), indent=2))
            return 0
        if args.retry_notification:
            status = application.retry_notification(args.retry_notification)
            print(json.dumps({"notification_status": status.value}))
            return 0 if status.value == "sent" else 4
        if args.resume:
            result = application.resume(*args.resume, send=args.send_slack)
        else:
            original = json.loads(args.submission.read_text(encoding="utf-8"))
            result = application.run(
                original,
                evidence_file=args.evidence_file,
                refresh=args.refresh,
                send=args.send_slack,
                policy_path=args.policy,
            )
        print(
            json.dumps(
                {
                    "lead_id": result.lead_id,
                    "execution_id": result.execution_id,
                    "status": result.final_status.value,
                    "score": result.fit.score,
                    "execution_outcome": result.execution_outcome,
                    "notification_status": result.notification_status.value,
                }
            )
        )
        return 3 if result.execution_outcome != "completed" else 0
    except (ValueError, TypeError, ValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(
            f"input or configuration error: {type(exc).__name__}: {str(exc)[:500]}", file=sys.stderr
        )
        return 2
    except Exception as exc:
        print(f"execution error: {type(exc).__name__}: {str(exc)[:500]}", file=sys.stderr)
        return 4
