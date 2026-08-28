#!/usr/bin/env python3
"""Agentic eval proof for Tau Discord human-decision receipts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.project_receipt_projection import ProjectReceiptProjection

GOAL_HASH = "sha256:tau-discord-unblock-agentic-eval"
QUESTION_ID = "tau-discord-unblock-eval-question"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-discord-unblock-eval-"))
    )
    receipt_dir = run_root / "run"
    logs_dir = run_root / "logs"
    specs_dir = run_root / "specs" / "coder"
    agents_root = run_root / "agents"
    for path in (receipt_dir, logs_dir, specs_dir, agents_root):
        path.mkdir(parents=True, exist_ok=True)

    command_spec = specs_dir / "tau-dispatch-command.json"
    _write_command_spec(command_spec, cwd=run_root, payload=_failing_handoff())
    dag = run_root / "dag.json"
    _write_dag(dag, command_spec)
    os.environ.setdefault(
        "OPS_DISCORD_WEBHOOK_TAU_REPAIR_EVAL_URL",
        "https://discord.com/api/webhooks/1234567890/redacted-eval-token",
    )

    tau = [shutil.which(args.uv_bin) or args.uv_bin, "run", "--project", str(repo), "tau"]
    dag_run = _run(
        [
            *tau,
            "dag-run",
            str(dag),
            "--receipt-dir",
            str(receipt_dir),
            "--agents-root",
            str(agents_root),
            "--scheduler",
            "bounded-ready-queue",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "dag-run.stdout.json",
        stderr_path=logs_dir / "dag-run.stderr.txt",
    )
    question_path = run_root / "discord" / "question.json"
    status_path = run_root / "discord" / "status.json"
    answer_path = run_root / "discord" / "answer.json"
    validation_path = run_root / "discord" / "answer-validation.json"
    _run(
        [
            *tau,
            "discord-receipt",
            "question",
            "--output",
            str(question_path),
            "--question-id",
            QUESTION_ID,
            "--run-id",
            "tau-discord-unblock-agentic-eval",
            "--node-id",
            "coder",
            "--goal-hash",
            GOAL_HASH,
            "--question",
            "Approve rerunning the repaired node?",
            "--allowed-answers",
            "approve,hold",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "question.stdout.json",
        stderr_path=logs_dir / "question.stderr.txt",
    )
    _run(
        [
            *tau,
            "discord-receipt",
            "status",
            "--output",
            str(status_path),
            "--question-id",
            QUESTION_ID,
            "--run-id",
            "tau-discord-unblock-agentic-eval",
            "--node-id",
            "coder",
            "--goal-hash",
            GOAL_HASH,
            "--message",
            "Repair category is waiting for human choice.",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "status.stdout.json",
        stderr_path=logs_dir / "status.stderr.txt",
    )
    _run(
        [
            *tau,
            "discord-receipt",
            "answer",
            "--output",
            str(answer_path),
            "--question-id",
            QUESTION_ID,
            "--run-id",
            "tau-discord-unblock-agentic-eval",
            "--node-id",
            "coder",
            "--goal-hash",
            GOAL_HASH,
            "--answer",
            "approve",
            "--answered-by",
            "human",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "answer.stdout.json",
        stderr_path=logs_dir / "answer.stderr.txt",
    )
    validation = _run(
        [
            *tau,
            "discord-receipt",
            "validate-answer",
            "--question",
            str(question_path),
            "--answer",
            str(answer_path),
            "--output",
            str(validation_path),
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "validation.stdout.json",
        stderr_path=logs_dir / "validation.stderr.txt",
    )

    dag_payload = _parse_json(dag_run["stdout"])
    snapshot = ProjectReceiptProjection.load(receipt_dir).snapshot()
    repair = (
        (dag_payload.get("pipeline_self_repair") or [{}])[0]
        if isinstance(dag_payload, dict)
        else {}
    )
    discord = (
        repair.get("discord")
        if isinstance(repair, dict) and isinstance(repair.get("discord"), dict)
        else {}
    )
    status_receipt = _read_json(status_path)
    validation_receipt = _read_json(validation_path)
    ops_discord_receipt = _read_json(
        Path(str(discord.get("ops_discord_notification_receipt") or ""))
    )
    errors: list[str] = []
    if (
        dag_run["exit_code"] == 0
        or not isinstance(dag_payload, dict)
        or dag_payload.get("status") != "BLOCKED"
    ):
        errors.append("dag_repair_question_not_blocked")
    if (
        discord.get("schema") != "tau.discord_human_question.v1"
        or discord.get("question_id") != QUESTION_ID
    ):
        errors.append("viewer_discord_question_missing")
    if status_receipt.get("unblocks_decision") is not False:
        errors.append("status_receipt_unblocked_decision")
    if discord.get("state") != "QUESTION_SENT":
        errors.append("ops_discord_question_not_sent")
    if ops_discord_receipt.get("schema") != "ops_discord.notification_receipt.v1":
        errors.append("ops_discord_notification_receipt_missing")
    if ops_discord_receipt.get("status") != "DRY_RUN":
        errors.append("ops_discord_notification_not_dispatched")
    if ops_discord_receipt.get("human_adjudication_required") is not True:
        errors.append("ops_discord_human_adjudication_flag_missing")
    if validation["exit_code"] != 0 or validation_receipt.get("unblocks_decision") is not True:
        errors.append("answer_validation_failed")
    if (
        not snapshot["corrections"]
        or snapshot["corrections"][0]["incident"].get("discord", {}).get("question_id")
        != QUESTION_ID
    ):
        errors.append("snapshot_discord_state_missing")

    receipt = {
        "schema": "tau.discord_unblock_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "dag_status": dag_payload.get("status") if isinstance(dag_payload, dict) else None,
        "discord_question_state": discord,
        "ops_discord_notification_receipt": ops_discord_receipt,
        "status_unblocks_decision": status_receipt.get("unblocks_decision"),
        "answer_unblocks_decision": validation_receipt.get("unblocks_decision"),
        "errors": errors,
        "proof_boundary": (
            "Live Tau CLI plus ops-discord dry-run notification receipt validation; "
            "no Discord network delivery and no repair completion proof."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _write_dag(path: Path, command_spec: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "tau-discord-unblock-agentic-eval",
                "goal": {
                    "goal_id": "tau-discord-unblock-agentic-eval",
                    "goal_version": 1,
                    "goal_hash": GOAL_HASH,
                },
                "target": {"repo": "grahama1970/tau", "target": "agentic-eval:discord-unblock"},
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"resume": False, "default_timeout_seconds": 30, "max_total_attempts": 2},
                "repair_policy": {
                    "enabled": True,
                    "handler": "pipeline-self-repair",
                    "goal_project": "tau",
                    "pipeline": "tau",
                    "repo": "grahama1970/tau",
                    "skip_memory": True,
                    "skip_github": True,
                    "no_ticket": True,
                    "timeout_seconds": 60,
                    "discord": {
                        "enabled": True,
                        "question_id": QUESTION_ID,
                        "webhook": "tau-repair-eval",
                        "dry_run": True,
                        "require_human_adjudication": True,
                    },
                },
                "nodes": [
                    {
                        "id": "coder",
                        "agent": "coder",
                        "executor": "local",
                        "max_attempts": 2,
                        "command_spec": str(command_spec),
                        "required_evidence": ["creator_artifact"],
                    }
                ],
                "edges": [{"from": "coder", "to": "human"}],
                "required_evidence": ["creator_artifact"],
                "fail_closed_on": [
                    "goal_hash_mismatch",
                    "target_changed",
                    "unexpected_node",
                    "unexpected_edge",
                    "missing_required_evidence",
                    "max_attempts_exceeded",
                    "malformed_handoff",
                    "pipeline_self_repair_required",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _failing_handoff() -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "agentic-eval:discord-unblock"},
        "goal": {
            "goal_id": "tau-discord-unblock-agentic-eval",
            "goal_version": 1,
            "goal_hash": GOAL_HASH,
        },
        "previous_subagent": "coder",
        "context": {"summary": "intentionally blocked for human decision", "artifacts": []},
        "result": {
            "status": "PASS",
            "summary": "missing creator_artifact evidence",
            "evidence": [],
        },
        "rationale": "Fault-injected failure to expose Discord human-decision receipts.",
        "next_agent": {"name": "human", "executor": "human", "reason": "blocked"},
        "required_evidence": ["creator_artifact"],
        "stop_condition": "Blocked.",
    }


def _write_command_spec(path: Path, *, cwd: Path, payload: dict[str, Any]) -> None:
    code = f"import json; print(json.dumps({payload!r}))"
    path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(cwd)}),
        encoding="utf-8",
    )


def _run(
    command: list[str], *, cwd: Path, timeout: int, stdout_path: Path, stderr_path: Path
) -> dict[str, Any]:
    proc = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _resolve_out(repo: Path, out: Path) -> Path:
    expanded = out.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (repo / expanded).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
