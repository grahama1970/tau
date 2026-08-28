#!/usr/bin/env python3
"""Live local Tau proof for the mandatory agentic-evals gate.

This script is intentionally not a pytest. It exercises Tau through the CLI,
then independently reads back the artifacts that prove the effect occurred.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.core_agentic_eval_proof.v1"
GOAL_HASH = "sha256:tau-core-agentic-eval"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = args.out.expanduser()
    if not out.is_absolute():
        out = (repo / out).resolve()
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-core-agentic-eval-"))
    )
    uv_bin = shutil.which(args.uv_bin) or args.uv_bin
    logs_dir = run_root / "logs"
    specs_dir = run_root / "specs"
    receipt_dir = run_root / "run"
    agents_root = run_root / "agents"
    for directory in (logs_dir, specs_dir, receipt_dir, agents_root):
        directory.mkdir(parents=True, exist_ok=True)

    creator_artifact = run_root / "creator-artifact.json"
    creator_artifact.write_text(
        json.dumps(
            {
                "schema": "tau.core_agentic_eval_creator_artifact.v1",
                "goal_hash": GOAL_HASH,
                "mocked": False,
                "live": True,
                "observed_at_unix_ns": time.time_ns(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    coder_spec = specs_dir / "coder-command.json"
    reviewer_spec = specs_dir / "reviewer-command.json"
    dag_spec = run_root / "tau-core-agentic-eval-dag.json"
    _write_command_spec(
        coder_spec,
        cwd=run_root,
        payload={
            "schema": "tau.agent_handoff.v1",
            "github": {"repo": "grahama1970/tau", "target": "agentic-eval:tau-core"},
            "goal": {"goal_id": "tau-core-agentic-eval", "goal_version": 1, "goal_hash": GOAL_HASH},
            "previous_subagent": "coder",
            "context": {"summary": "live Tau core proof creator", "artifacts": []},
            "result": {
                "status": "PASS",
                "summary": "creator produced read-back artifact",
                "evidence": [
                    {
                        "kind": "creator_artifact",
                        "path": str(creator_artifact),
                        "goal_hash": GOAL_HASH,
                    }
                ],
            },
            "rationale": "Exercise Tau DAG dispatch through the real CLI for agentic-evals.",
            "next_agent": {"name": "reviewer", "executor": "local", "reason": "continue"},
            "required_evidence": ["creator_artifact", "reviewer_verdict"],
            "stop_condition": "Stop at human after reviewer PASS.",
        },
    )
    _write_command_spec(
        reviewer_spec,
        cwd=run_root,
        payload={
            "schema": "tau.agent_handoff.v1",
            "github": {"repo": "grahama1970/tau", "target": "agentic-eval:tau-core"},
            "goal": {"goal_id": "tau-core-agentic-eval", "goal_version": 1, "goal_hash": GOAL_HASH},
            "previous_subagent": "reviewer",
            "context": {"summary": "live Tau core proof reviewer", "artifacts": []},
            "result": {
                "status": "PASS",
                "summary": "reviewer accepted goal-bound creator artifact",
                "evidence": [
                    {
                        "kind": "reviewer_verdict",
                        "reviewed_node_id": "coder",
                        "goal_hash": GOAL_HASH,
                        "verdict": "PASS",
                    }
                ],
            },
            "rationale": "Reviewer confirms the goal hash and evidence class expected by the DAG.",
            "next_agent": {"name": "human", "executor": "human", "reason": "complete"},
            "required_evidence": ["creator_artifact", "reviewer_verdict"],
            "stop_condition": "Done.",
        },
    )
    dag_spec.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "tau-core-agentic-eval",
                "goal": {
                    "goal_id": "tau-core-agentic-eval",
                    "goal_version": 1,
                    "goal_hash": GOAL_HASH,
                },
                "target": {"repo": "grahama1970/tau", "target": "agentic-eval:tau-core"},
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 3},
                "nodes": [
                    {
                        "id": "coder",
                        "agent": "coder",
                        "executor": "local",
                        "max_attempts": 1,
                        "command_spec": str(coder_spec),
                        "required_evidence": ["creator_artifact"],
                    },
                    {
                        "id": "reviewer",
                        "agent": "reviewer",
                        "executor": "local",
                        "max_attempts": 1,
                        "command_spec": str(reviewer_spec),
                        "required_evidence": ["reviewer_verdict"],
                        "reviewer": {"reviews_node": "coder", "requires_goal_hash": True},
                    },
                ],
                "edges": [{"from": "coder", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
                "required_evidence": ["creator_artifact", "reviewer_verdict"],
                "fail_closed_on": [
                    "goal_hash_mismatch",
                    "target_changed",
                    "unexpected_node",
                    "unexpected_edge",
                    "missing_required_evidence",
                    "max_attempts_exceeded",
                    "malformed_handoff",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    tau = [uv_bin, "run", "--project", str(repo), "tau"]
    dag_run = _run(
        [
            *tau,
            "dag-run",
            str(dag_spec),
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
    ledger_path = receipt_dir / "run-ledger.json"
    ledger_verify = _run(
        [*tau, "ledger", "verify", "--ledger", str(ledger_path)],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "ledger-verify.stdout.json",
        stderr_path=logs_dir / "ledger-verify.stderr.txt",
    )

    tampered_path = receipt_dir / "run-ledger-tampered.json"
    if ledger_path.exists():
        tampered = json.loads(ledger_path.read_text(encoding="utf-8"))
        if isinstance(tampered.get("entries"), list) and len(tampered["entries"]) > 1:
            tampered["entries"][1]["payload"] = copy.deepcopy(tampered["entries"][1]["payload"])
            tampered["entries"][1]["payload"]["tampered"] = True
        tampered_path.write_text(
            json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    tamper_verify = _run(
        [*tau, "ledger", "verify", "--ledger", str(tampered_path)],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "ledger-tamper-verify.stdout.json",
        stderr_path=logs_dir / "ledger-tamper-verify.stderr.txt",
    )

    dag_payload = _parse_json(dag_run["stdout"])
    verify_payload = _parse_json(ledger_verify["stdout"])
    tamper_payload = _parse_json(tamper_verify["stdout"])
    errors: list[str] = []
    if dag_run["exit_code"] != 0:
        errors.append(f"dag_run_exit:{dag_run['exit_code']}")
    if not isinstance(dag_payload, dict) or dag_payload.get("ok") is not True:
        errors.append("dag_run_not_ok")
    if not isinstance(dag_payload, dict) or dag_payload.get("status") != "PASS":
        errors.append("dag_run_not_pass")
    if not ledger_path.is_file() or ledger_path.stat().st_size == 0:
        errors.append("ledger_missing")
    if ledger_verify["exit_code"] != 0:
        errors.append(f"ledger_verify_exit:{ledger_verify['exit_code']}")
    if not isinstance(verify_payload, dict) or verify_payload.get("ok") is not True:
        errors.append("ledger_verify_not_ok")
    if tamper_verify["exit_code"] == 0:
        errors.append("tamper_verify_unexpected_success")
    if not isinstance(tamper_payload, dict) or tamper_payload.get("ok") is not False:
        errors.append("tamper_verify_not_fail_closed")
    if isinstance(tamper_payload, dict) and tamper_payload.get("reason") != "entry_hash_mismatch":
        errors.append(f"tamper_reason:{tamper_payload.get('reason')!r}")

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "run_root": str(run_root),
        "dag_run": _without_stdout(dag_run),
        "ledger_verify": _without_stdout(ledger_verify),
        "tamper_verify": _without_stdout(tamper_verify),
        "dag_status": dag_payload.get("status") if isinstance(dag_payload, dict) else None,
        "ledger_ok": verify_payload.get("ok") if isinstance(verify_payload, dict) else None,
        "tamper_ok": tamper_payload.get("ok") if isinstance(tamper_payload, dict) else None,
        "tamper_reason": tamper_payload.get("reason") if isinstance(tamper_payload, dict) else None,
        "errors": errors,
        "proof_boundary": {
            "proves": [
                "Tau can execute a local creator/reviewer DAG through the CLI.",
                "Tau writes a non-empty run-ledger.json for that run.",
                "tau ledger verify accepts the intact ledger and rejects a tampered ledger.",
            ],
            "does_not_prove": [
                "provider semantic quality",
                "browser rendering quality",
                "GitHub mutation behavior",
            ],
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


def _write_command_spec(path: Path, *, cwd: Path, payload: dict[str, Any]) -> None:
    code = "import json; print(json.dumps(" + repr(payload) + "))"
    path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(cwd)})
        + "\n",
        encoding="utf-8",
    )


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if stripped[index + end :].strip():
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _without_stdout(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "stdout"}


if __name__ == "__main__":
    raise SystemExit(main())
