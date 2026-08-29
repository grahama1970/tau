#!/usr/bin/env python3
"""Prove a repaired Tau required-node failure reruns the same semantic node then advances."""

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

GOAL_HASH = "sha256:tau-same-node-rerun-agentic-eval"
DAG_ID = "tau-same-node-rerun-agentic-eval"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-same-node-rerun-"))
    )
    receipt_dir = run_root / "run"
    specs = run_root / "specs"
    agents = run_root / "agents"
    logs = run_root / "logs"
    for path in (receipt_dir, specs / "coder", specs / "reviewer", agents, logs):
        path.mkdir(parents=True, exist_ok=True)

    coder_spec = specs / "coder" / "tau-dispatch-command.json"
    reviewer_spec = specs / "reviewer" / "tau-dispatch-command.json"
    dag = run_root / "dag.json"
    _write_command_spec(coder_spec, cwd=run_root, payload=_handoff("coder", evidence=[]))
    _write_command_spec(
        reviewer_spec,
        cwd=run_root,
        payload=_handoff("reviewer", evidence=[_evidence("reviewer_verdict")], next_agent="human"),
    )
    _write_dag(dag, coder_spec, reviewer_spec)

    tau = [shutil.which(args.uv_bin) or args.uv_bin, "run", "--project", str(repo), "tau"]
    first = _run(
        [
            *tau,
            "dag-run",
            str(dag),
            "--receipt-dir",
            str(receipt_dir),
            "--agents-root",
            str(agents),
            "--scheduler",
            "bounded-ready-queue",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs / "first.stdout.json",
        stderr_path=logs / "first.stderr.txt",
    )
    first_payload = _parse_json(first["stdout"])
    repair = ((first_payload or {}).get("pipeline_self_repair") or [{}])[0]
    ledger = Path(str(repair.get("ledger") or ""))
    category_key = str(repair.get("category_key") or "")
    proof_report = run_root / "agentic-evals-report.json"
    proof_report.write_text(
        json.dumps(
            {
                "schema": "agentic_evals.report.v2",
                "readiness": "READY",
                "status": "PASS",
                "cases": [{"name": "same-node-rerun", "status": "PASS"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    mark = _run(
        [
            str(Path.home() / ".pi" / "agent" / "skills" / "pipeline-self-repair" / "run.sh"),
            "mark-repaired",
            "--ledger",
            str(ledger),
            "--category-key",
            category_key,
            "--proof-report",
            str(proof_report),
            "--goal-project",
            "tau",
            "--json",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs / "mark-repaired.stdout.json",
        stderr_path=logs / "mark-repaired.stderr.txt",
    )
    _write_command_spec(
        coder_spec,
        cwd=run_root,
        payload=_handoff("coder", evidence=[_evidence("creator_artifact")], next_agent="reviewer"),
    )
    second = _run(
        [
            *tau,
            "dag-run",
            str(dag),
            "--receipt-dir",
            str(receipt_dir),
            "--agents-root",
            str(agents),
            "--scheduler",
            "bounded-ready-queue",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs / "second.stdout.json",
        stderr_path=logs / "second.stderr.txt",
    )
    second_payload = _parse_json(second["stdout"])
    snapshot = ProjectReceiptProjection.load(receipt_dir).snapshot()

    observed_edges = (second_payload or {}).get("observed_edges") or []
    errors: list[str] = []
    if first["exit_code"] == 0 or (first_payload or {}).get("status") != "BLOCKED":
        errors.append("first_run_not_blocked")
    if repair.get("node_id") != "coder" or not category_key:
        errors.append("repair_category_missing")
    if mark["exit_code"] != 0:
        errors.append("mark_repaired_failed")
    if second["exit_code"] != 0 or (second_payload or {}).get("status") != "PASS":
        errors.append("second_run_not_pass")
    if ((second_payload or {}).get("node_attempts") or {}).get("coder") != 2:
        errors.append("same_node_attempt_2_not_recorded")
    if not any(
        item.get("from_node") == "coder" and item.get("to_node") == "reviewer"
        for item in observed_edges
        if isinstance(item, dict)
    ):
        errors.append("downstream_reviewer_not_released")
    rerun = (second_payload or {}).get("pipeline_self_repair_rerun") or {}
    if rerun.get("authorized") is not True:
        errors.append("repair_rerun_not_authorized_by_ledger")
    if snapshot["run_summary"].get("repair", {}).get("open_category_count") != 0:
        errors.append("viewer_repair_category_not_closed")
    tau_triage = (
        (((rerun.get("closed_repair_records") or [{}])[0]).get("tau_triage") or {})
        if isinstance(rerun, dict)
        else {}
    )
    if tau_triage.get("code") != "tau_project_dag_missing_required_evidence" or not tau_triage.get(
        "next_command"
    ):
        errors.append("tau_specific_triage_code_missing")

    receipt = {
        "schema": "tau.same_node_rerun_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "receipt_dir": str(receipt_dir),
        "first_status": (first_payload or {}).get("status"),
        "repair_category_key": category_key,
        "mark_repaired_exit_code": mark["exit_code"],
        "second_status": (second_payload or {}).get("status"),
        "node_attempts": (second_payload or {}).get("node_attempts"),
        "observed_edges": observed_edges,
        "repair_rerun": rerun,
        "tau_triage": tau_triage,
        "viewer_repair_summary": snapshot["run_summary"].get("repair"),
        "errors": errors,
        "proof_boundary": (
            "Live local Tau CLI with safe command-spec fixtures; provider/model semantics "
            "are not exercised."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _write_dag(path: Path, coder_spec: Path, reviewer_spec: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": DAG_ID,
                "goal": {"goal_id": DAG_ID, "goal_version": 1, "goal_hash": GOAL_HASH},
                "target": {"repo": "grahama1970/tau", "target": "agentic-eval:same-node-rerun"},
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 4},
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
                },
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
                    },
                ],
                "edges": [{"from": "coder", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
                "required_evidence": ["creator_artifact", "reviewer_verdict"],
                "fail_closed_on": [
                    "missing_required_evidence",
                    "pipeline_self_repair_required",
                    "max_attempts_exceeded",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _evidence(kind: str) -> dict[str, str]:
    return {"kind": kind, "goal_hash": GOAL_HASH, "path": "fixture://same-node-rerun"}


def _handoff(
    agent: str, *, evidence: list[dict[str, str]], next_agent: str = "human"
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "agentic-eval:same-node-rerun"},
        "goal": {"goal_id": DAG_ID, "goal_version": 1, "goal_hash": GOAL_HASH},
        "previous_subagent": agent,
        "context": {"summary": f"{agent} fixture", "artifacts": []},
        "result": {"status": "PASS", "summary": f"{agent} fixture", "evidence": evidence},
        "rationale": "same-node rerun proof fixture",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "continue",
        },
        "required_evidence": [item["kind"] for item in evidence],
        "stop_condition": "done",
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
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "command": command,
    }


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
