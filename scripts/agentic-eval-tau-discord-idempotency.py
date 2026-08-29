#!/usr/bin/env python3
"""Prove Tau does not send duplicate ops-discord notifications for the same repair category."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tau_coding.project_dag import (
    OPS_DISCORD_NOTIFICATION_SCHEMA,
    _notify_ops_discord_for_human_adjudication,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args()
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-discord-idempotency-"))
    )
    receipt_dir = run_root / "run"
    repair_root = receipt_dir / "pipeline-self-repair"
    repair_root.mkdir(parents=True, exist_ok=True)
    out = (
        args.out.expanduser().resolve()
        if args.out.is_absolute()
        else (Path.cwd() / args.out).resolve()
    )

    question_id = "tau-idempotency-question"
    category_key = "tau/coder/missing-required-evidence/idempotency/v1"
    failure_category_id = "tau:missing-required-evidence"
    seed_path = repair_root / "coder-attempt-001-ops-discord-notification.json"
    seed = {
        "schema": OPS_DISCORD_NOTIFICATION_SCHEMA,
        "status": "SENT",
        "ok": True,
        "transport": "discord_bot",
        "question_id": question_id,
        "category_key": category_key,
        "failure_category_id": failure_category_id,
        "discord_message_id": "existing-message-1",
        "discord_channel_id": "existing-channel-1",
        "message_url": "https://discord.com/channels/example/existing-channel-1/existing-message-1",
        "external_effects": True,
    }
    seed_path.write_text(json.dumps(seed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    contract = SimpleNamespace(
        dag_id="tau-discord-idempotency-agentic-eval",
        goal={"goal_hash": "sha256:tau-discord-idempotency-agentic-eval"},
        repair_policy={
            "discord": {
                "require_human_adjudication": True,
                "run_sh": "/bin/false",
                "discord_bot": True,
                "channel_name": "horus",
                "timeout_seconds": 1,
            }
        },
    )
    node = SimpleNamespace(node_id="coder")
    projection = {
        "repair_state": "NEEDS_TRIAGE",
        "category_key": category_key,
        "failure_category_id": failure_category_id,
        "discord": {"question_id": question_id},
    }
    updated = (
        _notify_ops_discord_for_human_adjudication(
            contract=contract,
            receipt_dir=receipt_dir,
            node=node,
            attempt=2,
            result={"verdict": "HUMAN_ADJUDICATION_REQUIRED"},
            projection=projection,
        )
        or {}
    )
    notification_path = Path(updated.get("ops_discord_notification_receipt", ""))
    notification = _read_json(notification_path)
    routing = _read_json(Path(str(updated.get("adjudication_routing_manifest") or "")))
    all_notifications = sorted(repair_root.glob("*-ops-discord-notification.json"))
    errors: list[str] = []
    if notification.get("status") != "DEDUPED":
        errors.append("notification_not_deduped")
    if notification.get("source_notification_receipt") != str(seed_path):
        errors.append("source_notification_receipt_missing")
    if notification.get("external_effects") is not False:
        errors.append("deduped_notification_claimed_external_effect")
    if len(all_notifications) != 2:
        errors.append("unexpected_notification_receipt_count")
    if routing.get("unknown_category_policy") != "FAIL_CLOSED":
        errors.append("routing_manifest_fail_closed_policy_missing")
    receipt = {
        "schema": "tau.discord_notification_idempotency_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "seed_notification_receipt": str(seed_path),
        "deduped_notification_receipt": str(notification_path),
        "notification": notification,
        "routing_manifest": routing,
        "notification_receipt_count": len(all_notifications),
        "errors": errors,
        "proof_boundary": (
            "Direct Tau repair notification path with /bin/false as a no-send sentinel; proves "
            "duplicate category detection before external notification execution."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
