#!/usr/bin/env python3
"""Agentic eval proof for Tau's pipeline-self-repair failure overlay."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.project_receipt_projection import ProjectReceiptProjection

GOAL_HASH = "sha256:tau-pipeline-self-repair-agentic-eval"


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
        else Path(tempfile.mkdtemp(prefix="tau-pipeline-self-repair-eval-"))
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
    _write_dag(dag, command_spec, discord=False)

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
    dag_payload = _parse_json(dag_run["stdout"])
    projection = ProjectReceiptProjection.load(receipt_dir).snapshot()
    repair = (
        (dag_payload.get("pipeline_self_repair") or [{}])[0]
        if isinstance(dag_payload, dict)
        else {}
    )
    repair_ledger = Path(str(repair.get("ledger") or "")) if isinstance(repair, dict) else Path()
    inspect = _run(
        [
            str(
                Path.home()
                / ".pi"
                / "agent"
                / "skills"
                / "pipeline-self-repair"
                / "run.sh"
            ),
            "inspect",
            "--ledger",
            str(repair_ledger),
            "--json",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "repair-inspect.stdout.json",
        stderr_path=logs_dir / "repair-inspect.stderr.txt",
    )
    inspect_payload = _parse_json(inspect["stdout"])
    errors: list[str] = []
    if dag_run["exit_code"] == 0:
        errors.append("dag_run_unexpected_pass")
    if not isinstance(dag_payload, dict) or dag_payload.get("status") != "BLOCKED":
        errors.append("dag_not_blocked")
    if not isinstance(repair, dict) or repair.get("repair_state") != "NEEDS_TRIAGE":
        errors.append("repair_state_not_needs_triage")
    if not repair_ledger.is_file():
        errors.append("repair_ledger_missing")
    if projection["run_summary"].get("repair", {}).get("open_category_count") != 1:
        errors.append("viewer_repair_summary_missing")
    if not isinstance(inspect_payload, dict) or inspect_payload.get("open_failure_count") != 1:
        errors.append("repair_ledger_inspect_failed")
    if not projection["nodes"] or projection["nodes"][0].get("correction") is None:
        errors.append("viewer_node_correction_missing")
    if "reviewer" in (dag_payload.get("node_attempts") or {}):
        errors.append("downstream_node_ran")

    receipt = {
        "schema": "tau.pipeline_self_repair_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "dag_status": dag_payload.get("status") if isinstance(dag_payload, dict) else None,
        "dag_verdict": dag_payload.get("verdict") if isinstance(dag_payload, dict) else None,
        "repair_state": repair.get("repair_state") if isinstance(repair, dict) else None,
        "category_key": repair.get("category_key") if isinstance(repair, dict) else None,
        "failure_category_id": repair.get("failure_category_id")
        if isinstance(repair, dict)
        else None,
        "repair_ledger": str(repair_ledger),
        "viewer_repair_summary": projection["run_summary"].get("repair"),
        "repair_ledger_inspect": inspect_payload,
        "errors": errors,
        "proof_boundary": (
            "Live local Tau CLI plus pipeline-self-repair safe mode; no ticket publication "
            "or watchdog dispatch."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _write_dag(path: Path, command_spec: Path, *, discord: bool) -> None:
    repair_policy: dict[str, Any] = {
        "enabled": True,
        "handler": "pipeline-self-repair",
        "goal_project": "tau",
        "pipeline": "tau",
        "repo": "grahama1970/tau",
        "skip_memory": True,
        "skip_github": True,
        "no_ticket": True,
        "timeout_seconds": 60,
    }
    if discord:
        repair_policy["discord"] = {"enabled": True, "question_id": "tau-repair-eval-question"}
    path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "tau-pipeline-self-repair-agentic-eval",
                "goal": {
                    "goal_id": "tau-pipeline-self-repair-agentic-eval",
                    "goal_version": 1,
                    "goal_hash": GOAL_HASH,
                },
                "target": {
                    "repo": "grahama1970/tau",
                    "target": "agentic-eval:pipeline-self-repair",
                },
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"resume": False, "default_timeout_seconds": 30, "max_total_attempts": 2},
                "repair_policy": repair_policy,
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
        "github": {"repo": "grahama1970/tau", "target": "agentic-eval:pipeline-self-repair"},
        "goal": {
            "goal_id": "tau-pipeline-self-repair-agentic-eval",
            "goal_version": 1,
            "goal_hash": GOAL_HASH,
        },
        "previous_subagent": "coder",
        "context": {"summary": "intentionally missing required evidence", "artifacts": []},
        "result": {
            "status": "PASS",
            "summary": "missing creator_artifact evidence",
            "evidence": [],
        },
        "rationale": "Fault-injected required evidence failure to exercise self-repair.",
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


def _resolve_out(repo: Path, out: Path) -> Path:
    expanded = out.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (repo / expanded).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
