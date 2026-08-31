#!/usr/bin/env python3
"""Retained Tau project-DAG eval for create-svg artifact-origin gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from tau_coding.project_dag import run_project_dag_contract  # noqa: E402

GOAL_HASH = "sha256:create-svg-origin-gates"


def _sha(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_spec(root: Path, node: str, response: dict[str, Any]) -> None:
    spec = root / "specs" / node / "tau-dispatch-command.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    code = f"import json; print({json.dumps(json.dumps(response, sort_keys=True))})"
    spec.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(root)}),
        encoding="utf-8",
    )


def _handoff(node: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "create-svg-origin-gates"},
        "goal": {"goal_id": "create-svg-origin-gates", "goal_version": 1, "goal_hash": GOAL_HASH},
        "previous_subagent": node,
        "context": {"summary": f"{node} response", "artifacts": []},
        "result": {"status": "PASS", "summary": f"{node} completed", "evidence": evidence},
        "rationale": "Tau owns artifact-origin acceptance.",
        "next_agent": {"name": "human", "executor": "human", "reason": "terminal"},
        "required_evidence": ["create_svg_variant_candidate"],
        "stop_condition": "Stop at human.",
    }


def _contract(
    root: Path,
    case_name: str,
    checks: list[str],
    *,
    handlers: list[str] | None = None,
) -> dict[str, Any]:
    node_id = "handler-webgpt" if handlers else "creator"
    context: dict[str, Any] = {}
    if handlers is not None:
        context["handlers"] = handlers
    spec_root = root.resolve() / "specs"
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": case_name,
        "goal": {"goal_id": "create-svg-origin-gates", "goal_version": 1, "goal_hash": GOAL_HASH},
        "target": {"repo": "grahama1970/tau", "target": "create-svg-origin-gates"},
        "entry_node": node_id,
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 2},
        "context": context,
        "nodes": [
            {
                "id": node_id,
                "agent": node_id,
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / node_id / "tau-dispatch-command.json"),
                "required_evidence": ["create_svg_variant_candidate"],
                "sanity_checks": [
                    {
                        "schema": "tau.node_sanity_check.v1",
                        "id": f"check-{index}-{check}",
                        "check_type": check,
                        "severity": "BLOCK",
                    }
                    for index, check in enumerate(checks, start=1)
                ],
            }
        ],
        "edges": [{"from": node_id, "to": "human"}],
        "required_evidence": ["create_svg_variant_candidate"],
        "fail_closed_on": [
            "tau_dag_requested_handler_substituted",
            "create_svg_required_payload_missing",
            "create_svg_candidate_receipt_invalid",
            "create_svg_artifact_origin_invalid",
            "node_sanity_check_failed",
            "missing_required_evidence",
        ],
    }


def _candidate(
    path: Path,
    *,
    mocked: bool = False,
    live: bool = True,
    origin: bool = True,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><title>Tau</title></svg>\n",
        encoding="utf-8",
    )
    candidate: dict[str, Any] = {
        "schema": "create_svg.variant_candidate.v1",
        "kind": "create_svg_variant_candidate",
        "svg_path": str(path),
        "svg_sha256": _sha(path),
        "mocked": mocked,
        "live": live,
        "goal_hash": GOAL_HASH,
    }
    if origin:
        candidate["origin"] = "tau_node_artifact"
        candidate["origin_node_id"] = "creator"
    return candidate


def _run_case(work: Path, case_name: str) -> dict[str, Any]:
    root = work / case_name
    root.mkdir(parents=True, exist_ok=True)
    node_id = "creator"
    expected_failure: str | None

    if case_name == "valid_receipt_bound_svg_candidate_accepted":
        contract = _contract(
            root,
            case_name,
            [
                "create_svg_required_svg_artifact",
                "create_svg_variant_candidate_live",
                "create_svg_artifact_origin",
            ],
        )
        evidence = [_candidate(root / "produced" / "creator.svg", origin=True)]
        expected_failure = None
    elif case_name == "requested_handler_substitution_blocked":
        contract = _contract(root, case_name, [], handlers=["webgpt", "webgemini", "webkimi"])
        contract["nodes"][0]["id"] = "handler-claude-opus-5-high"
        contract["nodes"][0]["agent"] = "handler-claude-opus-5-high"
        contract["nodes"][0]["command_spec"] = str(
            root.resolve() / "specs" / "handler-claude-opus-5-high" / "tau-dispatch-command.json"
        )
        contract["entry_node"] = "handler-claude-opus-5-high"
        contract["edges"] = [{"from": "handler-claude-opus-5-high", "to": "human"}]
        node_id = "handler-claude-opus-5-high"
        evidence = [_candidate(root / "produced" / "handler.svg", origin=False)]
        expected_failure = "tau_dag_requested_handler_substituted"
    elif case_name == "mocked_candidate_receipt_blocked":
        contract = _contract(root, case_name, ["create_svg_variant_candidate_live"])
        evidence = [
            _candidate(root / "produced" / "mocked.svg", mocked=True, live=False, origin=True)
        ]
        evidence[0]["failure_code"] = "create_svg_tau_compile_or_execute_failed"
        expected_failure = "create_svg_candidate_receipt_invalid"
    elif case_name == "prose_only_without_svg_payload_blocked":
        contract = _contract(root, case_name, ["create_svg_required_svg_artifact"])
        contract["nodes"][0]["required_evidence"] = []
        contract["required_evidence"] = []
        evidence = []
        expected_failure = "create_svg_required_payload_missing"
    elif case_name == "local_preview_origin_blocked":
        contract = _contract(root, case_name, ["create_svg_artifact_origin"])
        evidence = [_candidate(root / "local-preview.svg", origin=False)]
        expected_failure = "create_svg_artifact_origin_invalid"
    else:
        raise SystemExit(f"unknown case: {case_name}")

    contract_path = root / "dag.json"
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True), encoding="utf-8")
    _write_spec(root, node_id, _handoff(node_id, evidence))
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=root / "run",
        agents_root=root / "agents",
        scheduler="bounded-ready-queue",
    )
    observed_failure = (receipt.get("dag_error") or {}).get("failure_code")
    observed_status = receipt.get("status")
    passed = (
        observed_status == "PASS"
        if expected_failure is None
        else observed_failure == expected_failure
    )
    return {
        "schema": "tau.agentic_eval_case_receipt.v1",
        "case": case_name,
        "status": "PASS" if passed else "FAIL",
        "expected_failure_code": expected_failure,
        "observed": {"status": observed_status, "failure_code": observed_failure},
        "dag_receipt_path": str(root / "run" / "dag-receipt.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = _run_case(args.work, args.case)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
