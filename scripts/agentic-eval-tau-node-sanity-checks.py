#!/usr/bin/env python3
"""Adversarial Tau project-DAG node sanity-check proof matrix."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from tau_coding.project_dag import run_project_dag_contract  # noqa: E402

GOAL_HASH = "sha256:node-sanity-goal"
PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGA"
    "WjR9awAAAABJRU5ErkJggg=="
)


def _sha(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _handoff(previous: str, next_agent: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "node-sanity-checks"},
        "goal": {"goal_id": "node-sanity-checks", "goal_version": 1, "goal_hash": GOAL_HASH},
        "previous_subagent": previous,
        "context": {"summary": f"{previous} response", "artifacts": []},
        "result": {"status": "PASS", "summary": f"{previous} completed", "evidence": evidence},
        "rationale": "Tau node sanity checks own acceptance.",
        "next_agent": {"name": next_agent, "executor": "human" if next_agent == "human" else "local", "reason": "continue"},
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def _write_response_spec(root: Path, agent: str, response: dict[str, Any]) -> None:
    spec_path = root / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"import json; print({json.dumps(json.dumps(response, sort_keys=True))})"
    spec_path.write_text(json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(root)}), encoding="utf-8")


def _sanity_checks(*types: str) -> list[dict[str, Any]]:
    return [
        {
            "schema": "tau.node_sanity_check.v1",
            "id": f"check-{index}-{check_type}",
            "check_type": check_type,
            "severity": "BLOCK",
        }
        for index, check_type in enumerate(types, start=1)
    ]


def _base_contract(root: Path, case_name: str) -> dict[str, Any]:
    spec_root = root.resolve() / "specs"
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": case_name.replace("_", "-")[:60],
        "goal": {"goal_id": "node-sanity-checks", "goal_version": 1, "goal_hash": GOAL_HASH},
        "target": {"repo": "grahama1970/tau", "target": "node-sanity-checks"},
        "entry_node": "creator",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 4, "max_concurrency": 2},
        "nodes": [
            {
                "id": "creator",
                "agent": "creator",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "creator" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
                "sanity_checks": _sanity_checks("required_evidence_exact_kinds", "no_mocked_proof"),
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {"reviews_node": "creator", "requires_goal_hash": True},
                "sanity_checks": _sanity_checks(
                    "required_evidence_exact_kinds",
                    "reviewer_verdict_schema",
                    "reviewer_verdict_bound_to_artifact",
                    "reviewer_verdict_bound_to_attempt",
                    "no_mocked_proof",
                ),
            },
        ],
        "edges": [{"from": "creator", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "missing_required_evidence",
            "reviewer_verdict_schema_invalid",
            "reviewer_verdict_invalid",
            "reviewer_artifact_binding_missing",
            "reviewer_artifact_hash_mismatch",
            "reviewer_attempt_binding_mismatch",
            "node_sanity_check_contract_invalid",
            "node_sanity_check_failed",
            "reviewer_topology_bypass",
            "required_reviewer_join_policy_bypass",
        ],
    }


def _creator_evidence(root: Path, *, mocked: bool = False, attempt_id: str = "creator-attempt-001") -> list[dict[str, Any]]:
    artifact = root.resolve() / "creator-artifact.svg"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("<svg xmlns='http://www.w3.org/2000/svg'><title>Tau</title></svg>\n", encoding="utf-8")
    return [
        {
            "kind": "creator_artifact",
            "path": str(artifact),
            "sha256": _sha(artifact),
            "attempt_id": attempt_id,
            "goal_hash": GOAL_HASH,
            "mocked": mocked,
            "live": True,
        }
    ]


def _valid_verdict(root: Path, *, verdict: str = "PASS") -> dict[str, Any]:
    artifact = root.resolve() / "creator-artifact.svg"
    if not artifact.exists():
        _creator_evidence(root)
    return {
        "schema": "tau.reviewer_verdict.v1",
        "kind": "reviewer_verdict",
        "reviewed_node_id": "creator",
        "reviewer_node_id": "reviewer",
        "goal_hash": GOAL_HASH,
        "verdict": verdict,
        "reviewed_artifact": {"path": str(artifact), "sha256": _sha(artifact)},
        "reviewed_attempt_id": "creator-attempt-001",
        "mocked": False,
        "live": True,
    }


def _write_contract(root: Path, case_name: str, contract: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(exist_ok=True)
    path = root / f"{case_name}.json"
    path.write_text(json.dumps(contract, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _run_contract(root: Path, case_name: str, contract: dict[str, Any], creator: dict[str, Any] | None, reviewer: dict[str, Any] | None) -> dict[str, Any]:
    case_root = root / case_name
    if creator is None:
        creator = _handoff("creator", "reviewer", _creator_evidence(case_root))
    if reviewer is None:
        reviewer = _handoff("reviewer", "human", [_valid_verdict(case_root)])
    contract_path = _write_contract(case_root, case_name, contract)
    _write_response_spec(case_root, "creator", creator)
    _write_response_spec(case_root, "reviewer", reviewer)
    try:
        receipt = run_project_dag_contract(contract_path=contract_path, receipt_dir=case_root / "run", agents_root=case_root / "agents")
    except RuntimeError as exc:
        error = str(exc)
        if "sanity_checks" in error:
            code = "node_sanity_check_contract_invalid"
        elif "bypassed on paths" in error:
            code = "reviewer_topology_bypass"
        else:
            code = "contract_runtime_error"
        return {"status": "BLOCKED", "ok": False, "failure_code": code, "error": error}
    return {
        "status": receipt.get("status"),
        "ok": receipt.get("ok"),
        "verdict": receipt.get("verdict"),
        "failure_code": (receipt.get("dag_error") or {}).get("failure_code"),
        "alerts": receipt.get("alerts", []),
        "receipt_path": str(case_root / "run" / "dag-receipt.json"),
    }


def _default_case(root: Path, case_name: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    case_root = root / case_name
    contract = _base_contract(case_root, case_name)
    return contract, None, None


def _mutate_verdict(root: Path, case_name: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    case_root = root / case_name
    contract = _base_contract(case_root, case_name)
    creator = _handoff("creator", "reviewer", _creator_evidence(case_root))
    verdict = _valid_verdict(case_root)
    mutate(verdict)
    reviewer = _handoff("reviewer", "human", [verdict])
    return _run_contract(root, case_name, contract, creator, reviewer)


def _visual_case(root: Path, case_name: str, mutate_verdict: Callable[[dict[str, Any], Path, Path], None], receipt_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    case_root = root / case_name
    contract = _base_contract(case_root, case_name)
    contract["nodes"][1]["sanity_checks"].append(
        {"schema": "tau.node_sanity_check.v1", "id": "visual-binding", "check_type": "visual_review_receipt_bound_to_screenshot", "severity": "BLOCK"}
    )
    creator = _handoff("creator", "reviewer", _creator_evidence(case_root))
    screenshot = case_root / "screenshot.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(PNG_1X1_BYTES)
    visual_receipt = case_root / "visual-review-receipt.json"
    if receipt_payload is None:
        receipt_payload = {
            "schema": "tau.visual_review_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "goal_hash": GOAL_HASH,
            "reviewed_node_id": "creator",
            "reviewer_node_id": "reviewer",
            "verification_method": "browser_screenshot_readback",
            "reviewed_screenshot": {"path": str(screenshot), "sha256": _sha(screenshot)},
            "mocked": False,
            "live": True,
        }
    visual_receipt.write_text(json.dumps(receipt_payload, sort_keys=True), encoding="utf-8")
    verdict = _valid_verdict(case_root)
    verdict.update(
        {
            "represents_goal": True,
            "attractive": True,
            "screenshot": {"path": str(screenshot), "sha256": _sha(screenshot), "mocked": False, "live": True},
            "visual_review_receipt": {"path": str(visual_receipt), "sha256": _sha(visual_receipt)},
            "visual_review_receipt_sha256": _sha(visual_receipt),
        }
    )
    mutate_verdict(verdict, screenshot, visual_receipt)
    reviewer = _handoff("reviewer", "human", [verdict])
    return _run_contract(root, case_name, contract, creator, reviewer)


def _join_bypass(root: Path, case_name: str, *, transitive: bool = False, policy: str = "any_success") -> dict[str, Any]:
    case_root = root / case_name
    contract = _base_contract(case_root, case_name)
    spec_root = case_root.resolve() / "specs"
    extra = []
    edges = [{"from": "creator", "to": "reviewer"}]
    if transitive:
        extra.append({"id": "adapter", "agent": "adapter", "executor": "local", "max_attempts": 1, "command_spec": str(spec_root / "adapter" / "tau-dispatch-command.json"), "required_evidence": ["reviewer_verdict"]})
        edges.append({"from": "reviewer", "to": "adapter"})
        edges.append({"from": "adapter", "to": "join"})
    else:
        edges.append({"from": "reviewer", "to": "join"})
    extra.extend(
        [
            {"id": "alternate", "agent": "alternate", "executor": "local", "max_attempts": 1, "command_spec": str(spec_root / "alternate" / "tau-dispatch-command.json"), "required_evidence": ["creator_artifact"]},
            {"id": "join", "agent": "join", "executor": "scheduler", "max_attempts": 1, "required_evidence": [], "join": {"schema": "tau.dag_join_policy.v1", "policy": policy, "timeout_seconds": 5}},
        ]
    )
    contract["nodes"].extend(extra)
    contract["edges"] = edges + [{"from": "alternate", "to": "join"}, {"from": "join", "to": "human"}]
    contract_path = _write_contract(case_root, case_name, contract)
    _write_response_spec(case_root, "creator", _handoff("creator", "reviewer", _creator_evidence(case_root)))
    _write_response_spec(case_root, "reviewer", _handoff("reviewer", "join", [_valid_verdict(case_root)]))
    _write_response_spec(case_root, "alternate", _handoff("alternate", "join", _creator_evidence(case_root)))
    if transitive:
        _write_response_spec(case_root, "adapter", _handoff("adapter", "join", [_valid_verdict(case_root)]))
    receipt = run_project_dag_contract(contract_path=contract_path, receipt_dir=case_root / "run", agents_root=case_root / "agents", scheduler="bounded-ready-queue")
    return {"status": receipt.get("status"), "ok": receipt.get("ok"), "verdict": receipt.get("verdict"), "failure_code": (receipt.get("dag_error") or {}).get("failure_code"), "alerts": receipt.get("alerts", [])}


def run_case(root: Path, case_name: str) -> dict[str, Any]:
    case_root = root / case_name
    contract, creator, reviewer = _default_case(root, case_name)
    if case_name == "valid_artifact_attempt_binding_positive":
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "prose_only_pass_missing_verdict":
        return _run_contract(root, case_name, contract, None, _handoff("reviewer", "human", [{"kind": "note", "text": "VERDICT: PASS", "goal_hash": GOAL_HASH}]))
    if case_name == "missing_json_schema":
        return _mutate_verdict(root, case_name, lambda v: v.pop("schema"))
    if case_name == "missing_kind":
        return _mutate_verdict(root, case_name, lambda v: v.pop("kind"))
    if case_name == "missing_reviewed_node_id":
        return _mutate_verdict(root, case_name, lambda v: v.pop("reviewed_node_id"))
    if case_name == "wrong_reviewed_node_id":
        return _mutate_verdict(root, case_name, lambda v: v.update(reviewed_node_id="other"))
    if case_name == "missing_reviewer_node_id":
        return _mutate_verdict(root, case_name, lambda v: v.pop("reviewer_node_id"))
    if case_name == "wrong_reviewer_node_id":
        return _mutate_verdict(root, case_name, lambda v: v.update(reviewer_node_id="creator"))
    if case_name == "invalid_verdict_token":
        return _mutate_verdict(root, case_name, lambda v: v.update(verdict="SHIP_IT"))
    if case_name == "fail_verdict_blocks":
        return _mutate_verdict(root, case_name, lambda v: v.update(verdict="FAIL"))
    if case_name == "blocked_verdict_blocks":
        return _mutate_verdict(root, case_name, lambda v: v.update(verdict="BLOCKED"))
    if case_name == "required_evidence_substring_rejected":
        creator = _handoff("creator", "reviewer", [{"kind": "note", "text": "creator_artifact", "goal_hash": GOAL_HASH}])
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "mocked_creator_proof_rejected":
        creator = _handoff("creator", "reviewer", _creator_evidence(case_root, mocked=True))
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "artifact_binding_missing":
        return _mutate_verdict(root, case_name, lambda v: v.pop("reviewed_artifact"))
    if case_name == "artifact_binding_wrong_path":
        return _mutate_verdict(root, case_name, lambda v: v["reviewed_artifact"].update(path=str(case_root / "other.svg")))
    if case_name == "artifact_binding_stale_hash":
        return _mutate_verdict(root, case_name, lambda v: v["reviewed_artifact"].update(sha256="sha256:" + "0" * 64))
    if case_name == "artifact_binding_missing_sha":
        return _mutate_verdict(root, case_name, lambda v: v["reviewed_artifact"].pop("sha256"))
    if case_name == "attempt_binding_missing":
        return _mutate_verdict(root, case_name, lambda v: v.pop("reviewed_attempt_id"))
    if case_name == "attempt_binding_wrong":
        return _mutate_verdict(root, case_name, lambda v: v.update(reviewed_attempt_id="creator-attempt-999"))
    if case_name == "visual_missing_screenshot":
        return _mutate_verdict(root, case_name, lambda v: v.update(represents_goal=True, attractive=True))
    if case_name == "visual_screenshot_bad_hash":
        return _visual_case(root, case_name, lambda v, s, r: v["screenshot"].update(sha256="sha256:" + "1" * 64))
    if case_name == "visual_screenshot_not_png":
        return _visual_case(root, case_name, lambda v, s, r: (s.write_text("not png", encoding="utf-8"), v["screenshot"].update(sha256=_sha(s))))
    if case_name == "visual_missing_boundary":
        return _visual_case(root, case_name, lambda v, s, r: (v.pop("mocked", None), v.pop("live", None), v["screenshot"].pop("mocked", None), v["screenshot"].pop("live", None)))
    if case_name == "visual_missing_receipt":
        return _visual_case(root, case_name, lambda v, s, r: v.pop("visual_review_receipt"))
    if case_name == "visual_receipt_wrong_goal":
        return _visual_case(root, case_name, lambda v, s, r: None, {"schema": "tau.visual_review_receipt.v1", "status": "PASS", "verdict": "PASS", "goal_hash": "sha256:wrong", "reviewed_node_id": "creator", "reviewer_node_id": "reviewer", "verification_method": "browser_screenshot_readback", "reviewed_screenshot": {"path": str(case_root / "screenshot.png"), "sha256": "sha256:pending"}, "mocked": False, "live": True})
    if case_name == "sanity_check_unknown_type":
        contract["nodes"][1]["sanity_checks"].append({"schema": "tau.node_sanity_check.v1", "id": "bad", "check_type": "trust_me_bro", "severity": "BLOCK"})
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "sanity_check_unknown_key":
        contract["nodes"][1]["sanity_checks"].append({"schema": "tau.node_sanity_check.v1", "id": "bad", "check_type": "reviewer_verdict_schema", "severity": "BLOCK", "regex": ".*PASS.*"})
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "direct_topology_bypass":
        contract["edges"].append({"from": "creator", "to": "human"})
        return _run_contract(root, case_name, contract, creator, reviewer)
    if case_name == "optional_join_bypass":
        return _join_bypass(root, case_name, transitive=False, policy="any_success")
    if case_name == "transitive_optional_join_bypass":
        return _join_bypass(root, case_name, transitive=True, policy="any_success")
    raise KeyError(f"unknown case: {case_name}")


EXPECTED_FAILURES = {
    "valid_artifact_attempt_binding_positive": None,
    "prose_only_pass_missing_verdict": "missing_required_evidence",
    "missing_json_schema": "reviewer_verdict_schema_invalid",
    "missing_kind": "missing_required_evidence",
    "missing_reviewed_node_id": "reviewer_verdict_schema_invalid",
    "wrong_reviewed_node_id": "reviewer_artifact_binding_missing",
    "missing_reviewer_node_id": "reviewer_verdict_schema_invalid",
    "wrong_reviewer_node_id": "reviewer_verdict_schema_invalid",
    "invalid_verdict_token": "reviewer_verdict_schema_invalid",
    "fail_verdict_blocks": "reviewer_verdict_invalid",
    "blocked_verdict_blocks": "reviewer_verdict_invalid",
    "required_evidence_substring_rejected": "missing_required_evidence",
    "mocked_creator_proof_rejected": "node_sanity_check_failed",
    "artifact_binding_missing": "reviewer_artifact_binding_missing",
    "artifact_binding_wrong_path": "reviewer_artifact_hash_mismatch",
    "artifact_binding_stale_hash": "reviewer_artifact_hash_mismatch",
    "artifact_binding_missing_sha": "reviewer_artifact_hash_mismatch",
    "attempt_binding_missing": "reviewer_attempt_binding_mismatch",
    "attempt_binding_wrong": "reviewer_attempt_binding_mismatch",
    "visual_missing_screenshot": "reviewer_visual_evidence_missing",
    "visual_screenshot_bad_hash": "reviewer_visual_evidence_hash_mismatch",
    "visual_screenshot_not_png": "reviewer_visual_evidence_not_png",
    "visual_missing_boundary": "reviewer_visual_boundary_missing",
    "visual_missing_receipt": "visual_review_receipt_missing",
    "visual_receipt_wrong_goal": "visual_review_receipt_invalid",
    "sanity_check_unknown_type": "node_sanity_check_contract_invalid",
    "sanity_check_unknown_key": "node_sanity_check_contract_invalid",
    "direct_topology_bypass": "reviewer_topology_bypass",
    "optional_join_bypass": "required_reviewer_join_policy_bypass",
    "transitive_optional_join_bypass": "required_reviewer_join_policy_bypass",
}


def _normalize_code(code: object) -> str | None:
    if code is None:
        return None
    text = str(code).lower()
    aliases = {"required_reviewer_join_policy_bypass": "required_reviewer_join_policy_bypass"}
    return aliases.get(text, text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(EXPECTED_FAILURES), required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    observed = run_case(args.work, args.case)
    expected = EXPECTED_FAILURES[args.case]
    observed_code = _normalize_code(observed.get("failure_code") or observed.get("verdict"))
    if expected is None:
        ok = observed.get("status") == "PASS" and observed.get("ok") is True
    else:
        ok = observed.get("status") == "BLOCKED" and observed_code == expected
    payload = {
        "schema": "tau.node_sanity_check_case_result.v1",
        "case": args.case,
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "expected_failure_code": expected,
        "observed_failure_code": observed_code,
        "observed": observed,
        "mocked": False,
        "live": True,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
