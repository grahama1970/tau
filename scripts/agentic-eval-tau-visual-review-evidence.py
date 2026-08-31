#!/usr/bin/env python3
"""Issue #336 proof: visual reviewer PASS must be screenshot-bound."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from tau_coding.project_dag import run_project_dag_contract  # noqa: E402


PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGA"
    "WjR9awAAAABJRU5ErkJggg=="
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _handoff(previous_subagent: str, next_agent: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "issue-336"},
        "goal": {
            "goal_id": "issue-336-visual-review-evidence",
            "goal_version": 1,
            "goal_hash": "sha256:issue-336-goal",
        },
        "previous_subagent": previous_subagent,
        "context": {"summary": f"{previous_subagent} node response.", "artifacts": []},
        "result": {
            "status": "PASS",
            "summary": f"{previous_subagent} completed.",
            "evidence": evidence,
        },
        "rationale": "The DAG contract controls acceptance.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def _write_response_spec(root: Path, agent: str, response: dict[str, Any]) -> None:
    spec_path = root / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"import json; print({json.dumps(json.dumps(response, sort_keys=True))})"
    spec_path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(root)}),
        encoding="utf-8",
    )


def _write_contract(root: Path, dag_id: str) -> Path:
    root = root.resolve()
    spec_root = root / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": dag_id,
        "goal": {
            "goal_id": "issue-336-visual-review-evidence",
            "goal_version": 1,
            "goal_hash": "sha256:issue-336-goal",
        },
        "target": {"repo": "grahama1970/tau", "target": "issue-336"},
        "entry_node": "creator",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 3},
        "nodes": [
            {
                "id": "creator",
                "agent": "creator",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "creator" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {"reviews_node": "creator", "requires_goal_hash": True},
            },
        ],
        "edges": [{"from": "creator", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "reviewer_visual_evidence_missing",
            "reviewer_visual_evidence_unreadable",
            "reviewer_visual_evidence_hash_mismatch",
            "reviewer_visual_evidence_not_png",
            "reviewer_visual_boundary_missing",
        ],
    }
    path = root / f"{dag_id}.json"
    path.write_text(json.dumps(contract, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _run_case(root: Path, *, name: str, reviewer_evidence: dict[str, Any]) -> dict[str, Any]:
    case_root = (root / name).resolve()
    (case_root / "agents").mkdir(parents=True, exist_ok=True)
    contract_path = _write_contract(case_root, f"issue-336-{name}")
    creator_artifact = case_root / "creator.svg"
    creator_artifact.write_text("<svg><text>visual artifact</text></svg>\n", encoding="utf-8")
    _write_response_spec(
        case_root,
        "creator",
        _handoff(
            "creator",
            "reviewer",
            [
                {
                    "kind": "creator_artifact",
                    "path": str(creator_artifact),
                    "sha256": _sha256(creator_artifact),
                    "goal_hash": "sha256:issue-336-goal",
                }
            ],
        ),
    )
    _write_response_spec(case_root, "reviewer", _handoff("reviewer", "human", [reviewer_evidence]))
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case_root / "run",
        agents_root=case_root / "agents",
    )
    receipt_path = case_root / "run" / "dag-receipt.json"
    if not receipt_path.exists():
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "name": name,
        "contract_path": str(contract_path),
        "receipt_path": str(receipt_path),
        "receipt_status": receipt.get("status"),
        "receipt_ok": receipt.get("ok"),
        "dag_error": receipt.get("dag_error"),
        "reviewer_verdicts": receipt.get("reviewer_verdicts", []),
        "alerts": receipt.get("alerts", []),
    }


def run_proof(work: Path, out: Path) -> dict[str, Any]:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    visual_screenshot = work / "positive" / "rendered-screenshot.png"
    visual_screenshot.parent.mkdir(parents=True, exist_ok=True)
    visual_screenshot.write_bytes(PNG_1X1_BYTES)
    fake_visual = _run_case(
        work,
        name="fake-visual-reviewer",
        reviewer_evidence={
            "kind": "reviewer_verdict",
            "reviewed_node_id": "creator",
            "goal_hash": "sha256:issue-336-goal",
            "verdict": "PASS",
            "represents_goal": True,
            "attractive": True,
        },
    )
    screenshot_bound = _run_case(
        work,
        name="screenshot-bound-reviewer",
        reviewer_evidence={
            "kind": "reviewer_verdict",
            "reviewed_node_id": "creator",
            "goal_hash": "sha256:issue-336-goal",
            "verdict": "PASS",
            "represents_goal": True,
            "attractive": True,
            "screenshot": {"path": str(visual_screenshot), "sha256": _sha256(visual_screenshot)},
            "mocked": False,
            "live": True,
        },
    )
    fake_failure_code = (fake_visual.get("dag_error") or {}).get("failure_code")
    positive_verdict = (screenshot_bound.get("reviewer_verdicts") or [{}])[0]
    positive_screenshot = positive_verdict.get("screenshot") if isinstance(positive_verdict, dict) else None
    errors = []
    if fake_visual.get("receipt_status") != "BLOCKED" or fake_failure_code != "reviewer_visual_evidence_missing":
        errors.append("fake_visual_reviewer_not_blocked")
    if screenshot_bound.get("receipt_status") != "PASS" or screenshot_bound.get("receipt_ok") is not True:
        errors.append("screenshot_bound_visual_reviewer_not_accepted")
    if not isinstance(positive_screenshot, dict) or positive_screenshot.get("sha256") != _sha256(visual_screenshot):
        errors.append("positive_screenshot_hash_not_bound")
    if positive_verdict.get("mocked") is not False or positive_verdict.get("live") is not True:
        errors.append("positive_visual_boundary_missing")
    payload = {
        "schema": "tau.visual_reviewer_evidence_proof.v1",
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "errors": errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "fake_visual_reviewer": fake_visual,
        "screenshot_bound_reviewer": screenshot_bound,
        "proof_boundary": {
            "proves": "Tau blocks visual-quality reviewer PASS claims unless the reviewer verdict carries readable screenshot evidence with an exact sha256 and mocked/live boundaries.",
            "does_not_prove": "Human aesthetic acceptance, browser rendering quality, provider semantic quality, or GOAL.md completion.",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=REPO_ROOT / "local/agentic-evals/tau-visual-review-evidence/work")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "local/agentic-evals/tau-visual-review-evidence-proof.json")
    args = parser.parse_args()
    payload = run_proof(args.work, args.out)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
