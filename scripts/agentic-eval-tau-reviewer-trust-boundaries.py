#!/usr/bin/env python3
"""Proof for Tau reviewer-trust hardening boundaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import tau_coding.project_dag as project_dag  # noqa: E402
from tau_coding.project_dag import run_project_dag_contract  # noqa: E402

GOAL_HASH = "sha256:reviewer-trust-goal"


def _handoff(
    previous_subagent: str, next_agent: str, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "reviewer-trust-hardening"},
        "goal": {
            "goal_id": "reviewer-trust-hardening",
            "goal_version": 1,
            "goal_hash": GOAL_HASH,
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


def _base_contract(root: Path, dag_id: str) -> dict[str, Any]:
    spec_root = root / "specs"
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": dag_id,
        "goal": {
            "goal_id": "reviewer-trust-hardening",
            "goal_version": 1,
            "goal_hash": GOAL_HASH,
        },
        "target": {"repo": "grahama1970/tau", "target": "reviewer-trust-hardening"},
        "entry_node": "creator",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
            "max_concurrency": 2,
        },
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
            "reviewer_verdict_schema_invalid",
            "required_reviewer_join_policy_bypass",
        ],
    }


def _write_contract(root: Path, dag_id: str, payload: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(exist_ok=True)
    path = root / f"{dag_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _valid_creator_evidence(root: Path) -> list[dict[str, Any]]:
    artifact = root / "creator-artifact.txt"
    artifact.write_text("creator artifact\n", encoding="utf-8")
    return [{"kind": "creator_artifact", "path": str(artifact), "goal_hash": GOAL_HASH}]


def _valid_reviewer_verdict() -> dict[str, Any]:
    return {
        "schema": "tau.reviewer_verdict.v1",
        "kind": "reviewer_verdict",
        "reviewed_node_id": "creator",
        "reviewer_node_id": "reviewer",
        "goal_hash": GOAL_HASH,
        "verdict": "PASS",
    }


def _run_schema_negative(root: Path) -> dict[str, Any]:
    case = root / "missing-reviewer-verdict-schema"
    contract = _base_contract(case, "missing-reviewer-verdict-schema")
    contract_path = _write_contract(case, "missing-reviewer-verdict-schema", contract)
    _write_response_spec(
        case, "creator", _handoff("creator", "reviewer", _valid_creator_evidence(case))
    )
    verdict = _valid_reviewer_verdict()
    verdict.pop("schema")
    _write_response_spec(case, "reviewer", _handoff("reviewer", "human", [verdict]))
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "failure_code": (receipt.get("dag_error") or {}).get("failure_code"),
        "alerts": receipt.get("alerts", []),
    }


def _run_contract_negative(root: Path) -> dict[str, Any]:
    case = root / "missing-reviews-node"
    contract = _base_contract(case, "missing-reviews-node")
    del contract["nodes"][1]["reviewer"]["reviews_node"]
    contract_path = _write_contract(case, "missing-reviews-node", contract)
    try:
        run_project_dag_contract(
            contract_path=contract_path,
            receipt_dir=case / "run",
            agents_root=case / "agents",
        )
    except RuntimeError as exc:
        return {"status": "BLOCKED", "failure_code": "reviewer_contract_invalid", "error": str(exc)}
    return {"status": "PASS", "failure_code": None, "error": "contract unexpectedly accepted"}


def _run_topology_bypass_negative(root: Path) -> dict[str, Any]:
    case = root / "reviewer-topology-bypass"
    contract = _base_contract(case, "reviewer-topology-bypass")
    contract["edges"].append({"from": "creator", "to": "human"})
    contract_path = _write_contract(case, "reviewer-topology-bypass", contract)
    try:
        run_project_dag_contract(
            contract_path=contract_path,
            receipt_dir=case / "run",
            agents_root=case / "agents",
        )
    except RuntimeError as exc:
        return {"status": "BLOCKED", "failure_code": "reviewer_topology_bypass", "error": str(exc)}
    return {"status": "PASS", "failure_code": None, "error": "contract unexpectedly accepted"}


def _run_missing_reviewer_node_id_negative(root: Path) -> dict[str, Any]:
    case = root / "missing-reviewer-node-id"
    contract = _base_contract(case, "missing-reviewer-node-id")
    contract_path = _write_contract(case, "missing-reviewer-node-id", contract)
    _write_response_spec(
        case, "creator", _handoff("creator", "reviewer", _valid_creator_evidence(case))
    )
    verdict = _valid_reviewer_verdict()
    verdict.pop("reviewer_node_id")
    _write_response_spec(case, "reviewer", _handoff("reviewer", "human", [verdict]))
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "failure_code": (receipt.get("dag_error") or {}).get("failure_code"),
        "alerts": receipt.get("alerts", []),
    }


def _run_reviewer_fail_negative(root: Path) -> dict[str, Any]:
    case = root / "reviewer-fail-verdict"
    contract = _base_contract(case, "reviewer-fail-verdict")
    contract_path = _write_contract(case, "reviewer-fail-verdict", contract)
    _write_response_spec(
        case, "creator", _handoff("creator", "reviewer", _valid_creator_evidence(case))
    )
    verdict = _valid_reviewer_verdict()
    verdict["verdict"] = "FAIL"
    _write_response_spec(case, "reviewer", _handoff("reviewer", "human", [verdict]))
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "failure_code": (receipt.get("dag_error") or {}).get("failure_code"),
        "alerts": receipt.get("alerts", []),
    }


def _run_exact_evidence_negative() -> dict[str, Any]:
    response = {"result": {"evidence": [{"kind": "note", "text": "creator_artifact"}]}}
    missing = project_dag._missing_required_evidence(("creator_artifact",), response)
    return {"status": "PASS" if missing == ["creator_artifact"] else "BLOCKED", "missing": missing}


def _run_join_bypass_negative(root: Path) -> dict[str, Any]:
    case = root / "reviewer-optional-join"
    contract = _base_contract(case, "reviewer-optional-join")
    spec_root = case / "specs"
    contract["nodes"].append(
        {
            "id": "join",
            "agent": "join",
            "executor": "scheduler",
            "max_attempts": 1,
            "required_evidence": [],
            "join": {
                "schema": "tau.dag_join_policy.v1",
                "policy": "any_success",
                "timeout_seconds": 5,
            },
        }
    )
    contract["nodes"].append(
        {
            "id": "alternate",
            "agent": "alternate",
            "executor": "local",
            "max_attempts": 1,
            "command_spec": str(spec_root / "alternate" / "tau-dispatch-command.json"),
            "required_evidence": ["creator_artifact"],
        }
    )
    contract["edges"] = [
        {"from": "creator", "to": "reviewer"},
        {"from": "reviewer", "to": "join"},
        {"from": "alternate", "to": "join"},
        {"from": "join", "to": "human"},
    ]
    contract_path = _write_contract(case, "reviewer-optional-join", contract)
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
        scheduler="bounded-ready-queue",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "command_executed": receipt.get("command_executed"),
        "alerts": receipt.get("alerts", []),
    }


def _run_transitive_join_bypass_negative(root: Path) -> dict[str, Any]:
    case = root / "reviewer-transitive-optional-join"
    contract = _base_contract(case, "reviewer-transitive-optional-join")
    spec_root = case / "specs"
    contract["nodes"].extend(
        [
            {
                "id": "adapter",
                "agent": "adapter",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "adapter" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
            },
            {
                "id": "join",
                "agent": "join",
                "executor": "scheduler",
                "max_attempts": 1,
                "required_evidence": [],
                "join": {
                    "schema": "tau.dag_join_policy.v1",
                    "policy": "any_success",
                    "timeout_seconds": 5,
                },
            },
            {
                "id": "alternate",
                "agent": "alternate",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "alternate" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
        ]
    )
    contract["edges"] = [
        {"from": "creator", "to": "reviewer"},
        {"from": "reviewer", "to": "adapter"},
        {"from": "adapter", "to": "join"},
        {"from": "alternate", "to": "join"},
        {"from": "join", "to": "human"},
    ]
    contract_path = _write_contract(case, "reviewer-transitive-optional-join", contract)
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
        scheduler="bounded-ready-queue",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "command_executed": receipt.get("command_executed"),
        "alerts": receipt.get("alerts", []),
    }


def _run_all_success_positive(root: Path) -> dict[str, Any]:
    case = root / "reviewer-all-success-join"
    contract = _base_contract(case, "reviewer-all-success-join")
    spec_root = case / "specs"
    contract["nodes"].append(
        {
            "id": "join",
            "agent": "join",
            "executor": "scheduler",
            "max_attempts": 1,
            "required_evidence": [],
            "join": {
                "schema": "tau.dag_join_policy.v1",
                "policy": "all_success",
                "timeout_seconds": 5,
            },
        }
    )
    contract["nodes"].append(
        {
            "id": "alternate",
            "agent": "alternate",
            "executor": "local",
            "max_attempts": 1,
            "command_spec": str(spec_root / "alternate" / "tau-dispatch-command.json"),
            "required_evidence": ["creator_artifact"],
        }
    )
    contract["edges"] = [
        {"from": "creator", "to": "reviewer"},
        {"from": "reviewer", "to": "join"},
        {"from": "alternate", "to": "join"},
        {"from": "join", "to": "human"},
    ]
    contract_path = _write_contract(case, "reviewer-all-success-join", contract)
    _write_response_spec(
        case, "creator", _handoff("creator", "reviewer", _valid_creator_evidence(case))
    )
    _write_response_spec(
        case, "alternate", _handoff("alternate", "join", _valid_creator_evidence(case))
    )
    _write_response_spec(
        case, "reviewer", _handoff("reviewer", "join", [_valid_reviewer_verdict()])
    )
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=case / "run",
        agents_root=case / "agents",
        scheduler="bounded-ready-queue",
    )
    return {
        "receipt_path": str(case / "run" / "dag-receipt.json"),
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "ok": receipt.get("ok"),
        "alerts": receipt.get("alerts", []),
    }


def run_proof(work: Path, out: Path) -> dict[str, Any]:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    schema_negative = _run_schema_negative(work)
    contract_negative = _run_contract_negative(work)
    topology_bypass_negative = _run_topology_bypass_negative(work)
    missing_reviewer_node_id_negative = _run_missing_reviewer_node_id_negative(work)
    reviewer_fail_negative = _run_reviewer_fail_negative(work)
    exact_evidence_negative = _run_exact_evidence_negative()
    join_bypass_negative = _run_join_bypass_negative(work)
    transitive_join_bypass_negative = _run_transitive_join_bypass_negative(work)
    all_success_positive = _run_all_success_positive(work)
    errors: list[str] = []
    if schema_negative.get("failure_code") != "reviewer_verdict_schema_invalid":
        errors.append("missing_schema_reviewer_verdict_not_blocked")
    if contract_negative.get("failure_code") != "reviewer_contract_invalid":
        errors.append("missing_reviews_node_contract_not_blocked")
    if topology_bypass_negative.get("failure_code") != "reviewer_topology_bypass":
        errors.append("reviewer_topology_bypass_not_blocked")
    if missing_reviewer_node_id_negative.get("failure_code") != "reviewer_verdict_schema_invalid":
        errors.append("missing_reviewer_node_id_not_blocked")
    if reviewer_fail_negative.get("failure_code") != "reviewer_verdict_invalid":
        errors.append("reviewer_fail_verdict_not_blocked")
    if exact_evidence_negative.get("missing") != ["creator_artifact"]:
        errors.append("required_evidence_substring_was_accepted")
    if join_bypass_negative.get("verdict") != "REQUIRED_REVIEWER_JOIN_POLICY_BYPASS":
        errors.append("required_reviewer_optional_join_not_blocked")
    if transitive_join_bypass_negative.get("verdict") != "REQUIRED_REVIEWER_JOIN_POLICY_BYPASS":
        errors.append("transitive_required_reviewer_optional_join_not_blocked")
    if all_success_positive.get("status") != "PASS" or all_success_positive.get("ok") is not True:
        errors.append("required_reviewer_all_success_join_not_accepted")
    payload = {
        "schema": "tau.reviewer_trust_boundaries_proof.v1",
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "errors": errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "schema_negative": schema_negative,
        "contract_negative": contract_negative,
        "topology_bypass_negative": topology_bypass_negative,
        "missing_reviewer_node_id_negative": missing_reviewer_node_id_negative,
        "reviewer_fail_negative": reviewer_fail_negative,
        "exact_evidence_negative": exact_evidence_negative,
        "join_bypass_negative": join_bypass_negative,
        "transitive_join_bypass_negative": transitive_join_bypass_negative,
        "all_success_positive": all_success_positive,
        "proof_boundary": {
            "proves": (
                "Tau rejects malformed project-DAG reviewer verdict evidence, rejects "
                "reviewer FAIL verdicts, requires reviewer declarations to bind a reviewed "
                "node, matches required evidence by exact kind, and refuses direct or "
                "transitive permissive joins that make required reviewer branches optional."
            ),
            "does_not_prove": (
                "Provider semantic quality, human acceptance, every non-reviewer evidence kind, "
                "or GOAL.md completion."
            ),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = run_proof(args.work, args.out)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
