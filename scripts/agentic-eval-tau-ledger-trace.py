#!/usr/bin/env python3
"""Agentic eval proof for Tau's default replay ledger trace."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

GOAL_HASH = "sha256:tau-ledger-trace-agentic-eval"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-ledger-trace-eval-"))
    )
    receipt_dir = run_root / "run"
    logs_dir = run_root / "logs"
    specs_dir = run_root / "specs"
    agents_root = run_root / "agents"
    for path in (receipt_dir, logs_dir, specs_dir / "coder", agents_root):
        path.mkdir(parents=True, exist_ok=True)

    artifact = run_root / "creator-artifact.json"
    artifact.write_text(
        json.dumps(
            {
                "schema": "tau.ledger_trace_eval_artifact.v1",
                "goal_hash": GOAL_HASH,
                "created_at_unix_ns": time.time_ns(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_command_spec(
        specs_dir / "coder" / "tau-dispatch-command.json",
        cwd=run_root,
        payload=_handoff(
            "coder",
            "human",
            [{"kind": "creator_artifact", "path": str(artifact), "goal_hash": GOAL_HASH}],
        ),
    )
    dag = run_root / "dag.json"
    _write_dag(dag, specs_dir / "coder" / "tau-dispatch-command.json")

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
    ledger_path = receipt_dir / "run-ledger.json"
    verify = _run(
        [*tau, "ledger", "verify", "--ledger", str(ledger_path)],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "ledger-verify.stdout.json",
        stderr_path=logs_dir / "ledger-verify.stderr.txt",
    )
    dag_payload = _parse_json(dag_run["stdout"])
    ledger = _read_json(ledger_path)
    verify_payload = _parse_json(verify["stdout"])
    trace = ledger.get("trace") if isinstance(ledger.get("trace"), dict) else {}
    errors: list[str] = []
    if (
        dag_run["exit_code"] != 0
        or not isinstance(dag_payload, dict)
        or dag_payload.get("ok") is not True
    ):
        errors.append("dag_run_not_pass")
    if (
        verify["exit_code"] != 0
        or not isinstance(verify_payload, dict)
        or verify_payload.get("ok") is not True
    ):
        errors.append("ledger_verify_not_ok")
    if not isinstance(trace, dict) or trace.get("schema") != "tau.run_ledger_trace.v1":
        errors.append("trace_schema_missing")
    if not isinstance(trace.get("entry_kind_counts"), dict) or not trace["entry_kind_counts"]:
        errors.append("entry_kind_counts_missing")
    if int(trace.get("artifact_count") or 0) < 1:
        errors.append("artifact_digest_missing")

    receipt = {
        "schema": "tau.ledger_trace_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "dag_status": dag_payload.get("status") if isinstance(dag_payload, dict) else None,
        "ledger_verify_ok": verify_payload.get("ok") if isinstance(verify_payload, dict) else None,
        "trace_schema": trace.get("schema") if isinstance(trace, dict) else None,
        "artifact_count": trace.get("artifact_count") if isinstance(trace, dict) else None,
        "entry_kind_counts": trace.get("entry_kind_counts") if isinstance(trace, dict) else None,
        "errors": errors,
        "proof_boundary": "Live local CLI path; no provider/browser/GitHub mutation.",
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
                "dag_id": "tau-ledger-trace-agentic-eval",
                "goal": {
                    "goal_id": "tau-ledger-trace-agentic-eval",
                    "goal_version": 1,
                    "goal_hash": GOAL_HASH,
                },
                "target": {"repo": "grahama1970/tau", "target": "agentic-eval:ledger-trace"},
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"resume": False, "default_timeout_seconds": 30, "max_total_attempts": 1},
                "nodes": [
                    {
                        "id": "coder",
                        "agent": "coder",
                        "executor": "local",
                        "max_attempts": 1,
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
                    "malformed_handoff",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _handoff(agent: str, next_agent: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "agentic-eval:ledger-trace"},
        "goal": {
            "goal_id": "tau-ledger-trace-agentic-eval",
            "goal_version": 1,
            "goal_hash": GOAL_HASH,
        },
        "previous_subagent": agent,
        "context": {"summary": "ledger trace eval", "artifacts": []},
        "result": {"status": "PASS", "summary": "creator artifact exists", "evidence": evidence},
        "rationale": "Exercise default Tau ledger trace.",
        "next_agent": {"name": next_agent, "executor": "human", "reason": "complete"},
        "required_evidence": ["creator_artifact"],
        "stop_condition": "Done.",
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
