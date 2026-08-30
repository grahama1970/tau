#!/usr/bin/env python3
"""Live proof for bounded model-facing child-agent requests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.child_agent_requests import (  # noqa: E402
    CHILD_AGENT_PROOF_SCHEMA,
    CHILD_AGENT_REQUEST_SCHEMA,
    ChildAgentRegistry,
    ChildAgentRequestError,
    child_instruction_operator_action,
)


def _request(idx: int) -> dict[str, Any]:
    name = ["alpha", "bravo", "charlie"][idx]
    role = ["scout", "coder", "reviewer"][idx]
    model = ["local-fast", "local-careful", "local-reviewer"][idx]
    return {
        "schema": CHILD_AGENT_REQUEST_SCHEMA,
        "request_id": f"issue316-{name}",
        "idempotency_key": f"issue316-{name}",
        "parent": {
            "run_id": "issue316-parent",
            "node_id": "planner",
            "depth": 0,
            "goal_hash": "sha256:issue316-child-agent-requests",
        },
        "role": role,
        "task": {
            "summary": f"bounded child {name}",
            "prompt": f"Execute bounded child request {name} and write a receipt.",
        },
        "requested": {
            "tools": ["read"],
            "paths": ["src/tau_coding"],
            "skills": ["tau"],
            "data_classes": ["public"],
            "models": [model],
        },
        "budgets": {
            "max_depth": 1,
            "max_turns": 1,
            "timeout_seconds": 30,
            "max_attempts": 1,
            "max_concurrency": 3,
            "max_tokens": 16000,
            "max_cost_usd": 0.0,
        },
        "policy": {
            "allowed_tools": ["read"],
            "allowed_paths": ["src/tau_coding"],
            "allowed_skills": ["tau"],
            "allowed_data_classes": ["public"],
            "allowed_models": [model],
            "allow_network": False,
            "require_receipt": True,
        },
        "join": {"join_id": "issue316-join", "policy": "all_pass"},
        "fanout_index": idx,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    registry = ChildAgentRegistry(parent_run_id="issue316-parent", max_children=3)
    handles = []
    for idx in range(3):
        handles.append(registry.admit(_request(idx), run_root=work / "children"))

    duplicate = registry.admit(_request(0), run_root=work / "children")
    byte_idempotent_duplicate = duplicate.handle_id == handles[0].handle_id
    try:
        conflict = _request(0)
        conflict["task"] = {"summary": "mutated", "prompt": "mutated request"}
        registry.admit(conflict, run_root=work / "children")
        conflict_rejected = False
    except ChildAgentRequestError as exc:
        conflict_rejected = exc.code == "child_agent_idempotency_conflict"
    if not byte_idempotent_duplicate:
        errors.append("duplicate_request_not_byte_idempotent")
    if not conflict_rejected:
        errors.append("idempotency_conflict_not_rejected")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) if not env.get("PYTHONPATH") else f"{SRC}{os.pathsep}{env['PYTHONPATH']}"
    pre_settlement_results = registry.accepted_results()
    processes = []
    for handle in handles:
        processes.append(
            (
                handle,
                subprocess.Popen(
                    ["uv", "run", "tau", "dag-run", handle.dag_spec_path, "--no-resume"],
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                ),
            )
        )

    child_runs = []
    for handle, process in processes:
        stdout, stderr = process.communicate(timeout=90)
        stdout_path = work / f"{handle.handle_id}.stdout"
        stderr_path = work / f"{handle.handle_id}.stderr"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        receipt_path = Path(handle.result_receipt_path)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
        registry.record_terminal(handle.handle_id, receipt=receipt)
        child_runs.append(
            {
                "handle_id": handle.handle_id,
                "child_run_id": handle.child_run_id,
                "role": handle.role,
                "exit_code": process.returncode,
                "receipt_path": str(receipt_path),
                "receipt_status": receipt.get("status"),
                "dag_spec_path": handle.dag_spec_path,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )
        if process.returncode != 0 or receipt.get("status") != "PASS":
            errors.append(f"child_failed:{handle.handle_id}:{process.returncode}:{receipt.get('status')}")

    instruction_action = child_instruction_operator_action(
        handles[0],
        action_request_id="issue316-instruction-1",
        instruction="Continue the admitted child DAG only; do not ask a human.",
        journal_seq=1,
        journal_head_sha256="sha256:issue316-journal-head",
    )
    if instruction_action.get("requires_human_input") is not False:
        errors.append("operator_action_requires_human_input")

    restarted_registry = ChildAgentRegistry.from_payload(registry.to_payload())
    reconstructed_same_handles = [h.handle_id for h in restarted_registry.handles] == [
        h.handle_id for h in registry.handles
    ]
    if pre_settlement_results:
        errors.append("results_available_before_settlement")
    if not reconstructed_same_handles:
        errors.append("registry_restart_handle_mismatch")

    accepted = restarted_registry.accepted_results()
    if len(accepted) != 3:
        errors.append(f"accepted_result_count:{len(accepted)}")

    follow_up_registry = ChildAgentRegistry(parent_run_id=handles[0].child_run_id, max_children=1)
    follow_up_request = _request(0)
    follow_up_request["request_id"] = "issue316-alpha-followup"
    follow_up_request["idempotency_key"] = "issue316-alpha-followup"
    follow_up_request["parent"] = {
        "run_id": handles[0].child_run_id,
        "node_id": "child-agent",
        "depth": 1,
        "goal_hash": "sha256:issue316-child-agent-requests",
        "attempt_id": "attempt-followup",
        "plan_sha256": handles[0].request_sha256,
        "journal_seq": 2,
        "journal_head_sha256": "sha256:issue316-followup-journal-head",
    }
    follow_up_request["budgets"] = {"max_depth": 2, "max_turns": 1, "timeout_seconds": 30}
    follow_up = follow_up_registry.admit(follow_up_request, run_root=work / "followup")
    follow_up_completed = subprocess.run(
        ["uv", "run", "tau", "dag-run", follow_up.dag_spec_path, "--no-resume"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    follow_up_receipt = (
        json.loads(Path(follow_up.result_receipt_path).read_text(encoding="utf-8"))
        if Path(follow_up.result_receipt_path).is_file()
        else {}
    )
    if follow_up_completed.returncode != 0 or follow_up_receipt.get("status") != "PASS":
        errors.append("followup_child_failed")

    payload = {
        "schema": CHILD_AGENT_PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "proof_boundary": {
            "proves": "model-facing child requests are admitted idempotently, compiled into bounded Tau child DAG specs, executed through the installed tau dag-run path, and joined by durable receipt handles",
            "does_not_prove": "semantic quality of external provider answers, UI pane rendering, or a real paid-provider child session",
        },
        "registry": registry.to_payload(),
        "byte_idempotent_duplicate": byte_idempotent_duplicate,
        "idempotency_conflict_rejected": conflict_rejected,
        "launched_child_dags_before_waiting": True,
        "pre_settlement_result_count": len(pre_settlement_results),
        "reconstructed_same_handles_after_restart": reconstructed_same_handles,
        "context_compaction_handle_continuity": reconstructed_same_handles,
        "child_runs": child_runs,
        "accepted_results": accepted,
        "follow_up": {
            "handle_id": follow_up.handle_id,
            "parent_child_run_id": handles[0].child_run_id,
            "lineage_request_sha256": follow_up.request_sha256,
            "exit_code": follow_up_completed.returncode,
            "receipt_status": follow_up_receipt.get("status"),
        },
        "operator_action": instruction_action,
        "errors": errors,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
