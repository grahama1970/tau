#!/usr/bin/env python3
"""Retained eval for Tau command-spec DAG resume preserving accepted creator output."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from tau_coding.project_dag import resume_project_dag_command_spec_nodes, run_project_dag_contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tau-command-spec-resume-") as tmp:
        root = Path(tmp)
        contract_path = write_contract(root)
        write_response_spec(root, "coder", handoff("coder", "reviewer", creator_evidence()))
        write_response_spec(
            root,
            "reviewer",
            {
                "status": "BLOCKED",
                "verdict": "SCILLM_PROVIDER_ROUTE_FAILED",
                "errors": ["HTTP 429"],
            },
        )
        first = run_project_dag_contract(
            contract_path=contract_path,
            receipt_dir=root / "run",
            agents_root=root / "agents",
            command_spec_root=root / "specs",
            scheduler="bounded-ready-queue",
        )
        write_response_spec(root, "reviewer", reviewer_handoff())
        resumed = resume_project_dag_command_spec_nodes(
            contract_path=contract_path,
            receipt_dir=root / "run",
            agents_root=root / "agents",
            command_spec_root=root / "specs",
            preserve_nodes=("coder",),
            rerun_nodes=("reviewer",),
            execute=True,
        )
        coder_count = int((root / "coder-count.txt").read_text())
        reviewer_count = int((root / "reviewer-count.txt").read_text())
        dag_receipt = json.loads((root / "run" / "dag-receipt.json").read_text())
        proof = {
            "schema": "tau.command_spec_resume_agentic_proof.v1",
            "ok": resumed.get("status") == "PASS"
            and coder_count == 1
            and reviewer_count == 2
            and Path(str(resumed.get("archive_path"))).is_file(),
            "mocked": False,
            "live": True,
            "provider_live": False,
            "first_status": first.get("status"),
            "first_verdict": first.get("verdict"),
            "final_status": resumed.get("status"),
            "final_verdict": dag_receipt.get("verdict"),
            "creator_not_relaunched": coder_count == 1,
            "reviewer_rerun_only": reviewer_count == 2,
            "run_store_archived": Path(str(resumed.get("archive_path"))).is_file(),
            "resume_contract_path": resumed.get("resume_contract_path"),
            "safe_admission": resumed.get("safe_admission"),
            "node_attempts": dag_receipt.get("node_attempts"),
        }
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


def write_contract(root: Path) -> Path:
    (root / "agents").mkdir()
    spec_root = root / "specs"
    payload: dict[str, Any] = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "ask-tau-repair-test",
        "goal": {
            "goal_id": "ask-tau-repair-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {"repo": "grahama1970/tau", "target": "same-run-resume"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 3},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
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
    }
    path = root / "dag.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_response_spec(root: Path, agent: str, response: dict[str, Any]) -> None:
    spec_path = root / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path = root / f"{agent}-count.txt"
    code = "\n".join(
        [
            "import json",
            "from pathlib import Path",
            f"counter = Path({str(counter_path)!r})",
            "count = int(counter.read_text()) if counter.exists() else 0",
            "counter.write_text(str(count + 1))",
            f"print(json.dumps({json.dumps(response)}))",
        ]
    )
    spec_path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(root)}),
        encoding="utf-8",
    )


def handoff(agent: str, next_agent: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "same-run-resume"},
        "goal": {
            "goal_id": "ask-tau-repair-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": agent,
        "context": {"summary": f"{agent} node response.", "artifacts": []},
        "result": {"status": "PASS", "summary": f"{agent} completed.", "evidence": evidence},
        "rationale": "The DAG contract controls the next route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def creator_evidence() -> list[dict[str, Any]]:
    return [
        {
            "kind": "creator_artifact",
            "path": "artifact.txt",
            "sha256": "sha256:creator",
            "summary": "created",
            "goal_hash": "sha256:active-goal",
        }
    ]


def reviewer_handoff() -> dict[str, Any]:
    return handoff(
        "reviewer",
        "human",
        [
            {
                "kind": "reviewer_verdict",
                "schema": "tau.reviewer_verdict.v1",
                "reviewer_node_id": "reviewer",
                "reviewed_node_id": "coder",
                "verdict": "PASS",
                "summary": "reviewed",
                "goal_hash": "sha256:active-goal",
            }
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
