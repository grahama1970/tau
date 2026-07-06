"""Validation-only adaptive DAG expansion proposals."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

try:
    import yaml
except ImportError:  # pragma: no cover - only used in stripped environments.
    yaml = None  # type: ignore[assignment]


DAG_EXPANSION_PROPOSAL_SCHEMA = "tau.dag_expansion_proposal.v1"
DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA = "tau.dag_expansion_validation_receipt.v1"
DAG_EXPANSION_POLICY_RECEIPT_SCHEMA = "tau.dag_expansion_policy_receipt.v1"
DAG_EXPANSION_APPLY_RECEIPT_SCHEMA = "tau.dag_expansion_apply_receipt.v1"

EXPANSION_LIMITS = {
    "max_new_nodes": 2,
    "max_depth_delta": 1,
    "max_new_edges": 4,
    "allow_new_executors": False,
    "allow_target_change": False,
    "allow_goal_change": False,
    "allow_terminal_node_change": False,
    "allow_command_spec_change": False,
}

ALLOWED_AUTHORS = {"reviewer", "goal-guardian", "validator"}
PRE_RUN_AUTHOR = "planner"
ALLOWED_NEW_AGENTS = {"reviewer", "validator", "goal-guardian", "research-auditor"}
DISALLOWED_NEW_AGENTS = {
    "coder",
    "creator",
    "worker",
    "provider",
    "script-writer",
    "contact-sheet-builder",
    "contact-sheet-writer",
}


def write_dag_expansion_validation_receipt(
    *,
    dag_contract_path: Path,
    proposal_path: Path,
    receipt_path: Path,
    preview_path: Path | None = None,
) -> dict[str, Any]:
    """Validate an expansion proposal and optionally write a preview DAG."""

    resolved_contract_path = dag_contract_path.expanduser().resolve()
    resolved_proposal_path = proposal_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    resolved_preview_path = preview_path.expanduser().resolve() if preview_path else None

    contract_payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(contract_payload)
    proposal = _load_object(resolved_proposal_path, label="DAG expansion proposal")
    alerts = _validate_proposal(
        contract_payload=contract_payload,
        proposal=proposal,
    )
    expanded_preview = None if alerts else _expanded_contract(contract_payload, proposal)
    if expanded_preview is not None:
        validate_dag_contract(expanded_preview)
    status = "PASS" if not alerts else "BLOCKED"
    if status == "PASS" and resolved_preview_path is not None and expanded_preview is not None:
        _write_json(resolved_preview_path, expanded_preview)
    receipt = {
        "schema": DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "dag_id": contract.dag_id,
        "goal_hash": contract.goal["goal_hash"],
        "dag_contract": str(resolved_contract_path),
        "dag_contract_sha256": f"sha256:{_sha256(resolved_contract_path)}",
        "proposal": str(resolved_proposal_path),
        "proposal_sha256": f"sha256:{_sha256(resolved_proposal_path)}",
        "receipt_path": str(resolved_receipt_path),
        "preview_path": str(resolved_preview_path) if resolved_preview_path and status == "PASS" else None,
        "preview_sha256": (
            f"sha256:{_sha256(resolved_preview_path)}"
            if resolved_preview_path and status == "PASS" and resolved_preview_path.exists()
            else None
        ),
        "limits": EXPANSION_LIMITS,
        "proposal_summary": _proposal_summary(proposal),
        "alerts": alerts,
        "applied": False,
        "mutated_source_dag": False,
        "memory_sync": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "DAG expansion proposal was inspected deterministically.",
                "Immutable goal, target, terminal node, executor, command-spec, depth, node, and edge limits were checked.",
                "No expansion was applied to a running DAG.",
                "No source DAG contract mutation, route mutation, Memory write, GitHub mutation, provider call, or command dispatch occurred.",
            ],
            "does_not_prove": [
                "Adaptive DAG expansion application.",
                "Runtime route mutation.",
                "Provider/model semantic quality.",
                "Memory route learning.",
                "Mutating branch safety.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def write_dag_expansion_policy_receipt(
    *,
    validation_receipt_path: Path,
    receipt_path: Path,
    signal_receipt_path: Path | None = None,
    require_clean_signal: bool = False,
) -> dict[str, Any]:
    """Decide whether a validated expansion is allowed to become a new DAG artifact."""

    resolved_validation_path = validation_receipt_path.expanduser().resolve()
    resolved_signal_path = signal_receipt_path.expanduser().resolve() if signal_receipt_path else None
    resolved_receipt_path = receipt_path.expanduser().resolve()
    validation = _load_object(resolved_validation_path, label="DAG expansion validation receipt")
    alerts = _policy_alerts(
        validation=validation,
        validation_receipt_path=resolved_validation_path,
        signal_receipt_path=resolved_signal_path,
        require_clean_signal=require_clean_signal,
    )
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": DAG_EXPANSION_POLICY_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "validation_receipt": str(resolved_validation_path),
        "validation_receipt_sha256": f"sha256:{_sha256(resolved_validation_path)}",
        "signal_receipt": str(resolved_signal_path) if resolved_signal_path else None,
        "signal_receipt_sha256": f"sha256:{_sha256(resolved_signal_path)}" if resolved_signal_path else None,
        "receipt_path": str(resolved_receipt_path),
        "dag_id": validation.get("dag_id"),
        "goal_hash": validation.get("goal_hash"),
        "require_clean_signal": require_clean_signal,
        "apply_allowed": status == "PASS",
        "recommended_next_command": (
            "tau dag-expansion-apply --validation-receipt <receipt> "
            "--policy-receipt <policy-receipt.json> "
            "--out <expanded-dag.json> --receipt <apply-receipt.json>"
            if status == "PASS"
            else None
        ),
        "alerts": alerts,
        "applied": False,
        "route_mutation": False,
        "dag_mutation": False,
        "memory_sync": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "Expansion validation receipt was checked for policy eligibility.",
                "Policy decision was emitted as a receipt.",
                "No DAG artifact was written by policy validation.",
            ],
            "does_not_prove": [
                "Expansion application.",
                "Runtime route mutation.",
                "Provider/model semantic quality.",
                "Memory route learning.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def write_dag_expansion_apply_receipt(
    *,
    validation_receipt_path: Path,
    out_path: Path,
    receipt_path: Path,
    policy_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Materialize a validated preview as a new DAG contract artifact."""

    resolved_validation_path = validation_receipt_path.expanduser().resolve()
    resolved_policy_path = policy_receipt_path.expanduser().resolve() if policy_receipt_path else None
    resolved_out_path = out_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    validation = _load_object(resolved_validation_path, label="DAG expansion validation receipt")
    alerts = _apply_alerts(
        validation=validation,
        validation_receipt_path=resolved_validation_path,
        policy_receipt_path=resolved_policy_path,
    )
    expanded: dict[str, Any] | None = None
    if not alerts:
        preview_path = Path(str(validation["preview_path"])).expanduser().resolve()
        expanded = _load_object(preview_path, label="expanded DAG preview")
        try:
            validate_dag_contract(expanded)
        except RuntimeError as exc:
            alerts.append(
                _alert(
                    "BLOCK",
                    "expanded_dag_invalid",
                    "Expanded DAG preview does not satisfy tau.dag_contract.v1.",
                    {"error": str(exc)},
                )
            )
    status = "PASS" if not alerts else "BLOCKED"
    if status == "PASS" and expanded is not None:
        _write_json(resolved_out_path, expanded)
    resume_supported = bool(expanded.get("limits", {}).get("resume")) if expanded else None
    receipt = {
        "schema": DAG_EXPANSION_APPLY_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "validation_receipt": str(resolved_validation_path),
        "validation_receipt_sha256": f"sha256:{_sha256(resolved_validation_path)}",
        "policy_receipt": str(resolved_policy_path) if resolved_policy_path else None,
        "policy_receipt_sha256": f"sha256:{_sha256(resolved_policy_path)}" if resolved_policy_path else None,
        "receipt_path": str(resolved_receipt_path),
        "source_dag_contract": validation.get("dag_contract"),
        "expanded_dag": str(resolved_out_path) if status == "PASS" else None,
        "expanded_dag_sha256": f"sha256:{_sha256(resolved_out_path)}" if status == "PASS" else None,
        "dag_id": validation.get("dag_id"),
        "goal_hash": validation.get("goal_hash"),
        "alerts": alerts,
        "applied": status == "PASS",
        "mutated_source_dag": False,
        "runtime_route_mutation": False,
        "memory_sync": False,
        "provider_calls": False,
        "resume_supported": resume_supported,
        "rerun_command": (
            ["tau", "dag-run", str(resolved_out_path), "--scheduler", "bounded-ready-queue"]
            if status == "PASS"
            else None
        ),
        "proof_scope": {
            "proves": [
                "Validated expansion preview was materialized as a new DAG contract artifact.",
                "Source DAG contract was not mutated.",
                "No running DAG route was mutated.",
            ],
            "does_not_prove": [
                "Expanded DAG execution.",
                "Provider/model semantic quality.",
                "Memory route learning.",
                "Mutating branch safety.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def _validate_proposal(
    *,
    contract_payload: dict[str, Any],
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if proposal.get("schema") != DAG_EXPANSION_PROPOSAL_SCHEMA:
        alerts.append(_alert("BLOCK", "invalid_schema", "Proposal schema is not supported.", {}))
        return alerts
    contract = validate_dag_contract(contract_payload)
    if proposal.get("parent_dag_id") != contract.dag_id:
        alerts.append(
            _alert(
                "BLOCK",
                "parent_dag_mismatch",
                "Proposal parent_dag_id does not match the DAG contract.",
                {"expected": contract.dag_id, "observed": proposal.get("parent_dag_id")},
            )
        )
    proposal_goal = _mapping(proposal.get("goal"))
    if proposal_goal and proposal_goal != contract.goal:
        alerts.append(
            _alert(
                "BLOCK",
                "goal_change_not_allowed",
                "Expansion proposals may not change the immutable goal.",
                {"expected": contract.goal, "observed": proposal_goal},
            )
        )
    elif not proposal_goal:
        goal_hash = proposal.get("goal_hash")
        if goal_hash != contract.goal["goal_hash"]:
            alerts.append(
                _alert(
                    "BLOCK",
                    "goal_hash_mismatch",
                    "Proposal goal_hash does not match the immutable goal hash.",
                    {"expected": contract.goal["goal_hash"], "observed": goal_hash},
                )
            )
    target = _mapping(proposal.get("target"))
    if target and target != contract.target:
        alerts.append(
            _alert(
                "BLOCK",
                "target_change_not_allowed",
                "Expansion proposals may not change the DAG target.",
                {"expected": contract.target, "observed": target},
            )
        )
    if "terminal_nodes" in proposal or "terminal_nodes_delta" in proposal:
        alerts.append(
            _alert(
                "BLOCK",
                "terminal_node_change_not_allowed",
                "Expansion proposals may not change terminal nodes in this slice.",
                {},
            )
        )
    author = _author(proposal)
    phase = str(proposal.get("phase") or proposal.get("run_state") or "")
    if author == PRE_RUN_AUTHOR and phase != "pre_run":
        alerts.append(
            _alert(
                "BLOCK",
                "planner_expansion_not_pre_run",
                "Planner may draft expansion only before a run.",
                {"author": author, "phase": phase},
            )
        )
    elif author not in ALLOWED_AUTHORS and author != PRE_RUN_AUTHOR:
        alerts.append(
            _alert(
                "BLOCK",
                "unauthorized_expansion_author",
                "Only reviewer, validator, goal-guardian, or pre-run planner may propose expansion.",
                {"author": author},
            )
        )
    new_nodes = _dict_list(proposal.get("new_nodes"))
    new_edges = _dict_list(proposal.get("new_edges"))
    if len(new_nodes) > EXPANSION_LIMITS["max_new_nodes"]:
        alerts.append(
            _alert(
                "BLOCK",
                "max_new_nodes_exceeded",
                "Proposal adds too many nodes.",
                {"max_new_nodes": EXPANSION_LIMITS["max_new_nodes"], "observed": len(new_nodes)},
            )
        )
    if len(new_edges) > EXPANSION_LIMITS["max_new_edges"]:
        alerts.append(
            _alert(
                "BLOCK",
                "max_new_edges_exceeded",
                "Proposal adds too many edges.",
                {"max_new_edges": EXPANSION_LIMITS["max_new_edges"], "observed": len(new_edges)},
            )
        )
    existing_node_ids = {str(item.get("id")) for item in _dict_list(contract_payload.get("nodes"))}
    existing_executors = {
        str(item.get("executor"))
        for item in _dict_list(contract_payload.get("nodes"))
        if isinstance(item.get("executor"), str)
    }
    for node in new_nodes:
        alerts.extend(_validate_new_node(node, existing_node_ids, existing_executors))
    for edge in new_edges:
        alerts.extend(_validate_new_edge(edge, existing_node_ids, {str(node.get("id")) for node in new_nodes}))
    if not alerts:
        expanded = _expanded_contract(contract_payload, proposal)
        depth_delta = _dag_depth(expanded) - _dag_depth(contract_payload)
        if depth_delta > EXPANSION_LIMITS["max_depth_delta"]:
            alerts.append(
                _alert(
                    "BLOCK",
                    "max_depth_delta_exceeded",
                    "Proposal increases DAG depth beyond the first-slice limit.",
                    {
                        "max_depth_delta": EXPANSION_LIMITS["max_depth_delta"],
                        "observed_depth_delta": depth_delta,
                    },
                )
            )
        try:
            validate_dag_contract(expanded)
        except RuntimeError as exc:
            alerts.append(
                _alert(
                    "BLOCK",
                    "expanded_dag_invalid",
                    "Expanded DAG preview does not satisfy tau.dag_contract.v1.",
                    {"error": str(exc)},
                )
            )
        if _cycle_detected(expanded):
            alerts.append(
                _alert(
                    "BLOCK",
                    "cycle_detected",
                    "Expansion proposal would make the DAG cyclic.",
                    {},
                )
            )
    return alerts


def _policy_alerts(
    *,
    validation: dict[str, Any],
    validation_receipt_path: Path,
    signal_receipt_path: Path | None,
    require_clean_signal: bool,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if validation.get("schema") != DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_validation_schema",
                "Expansion policy requires a tau.dag_expansion_validation_receipt.v1 input.",
                {"path": str(validation_receipt_path), "schema": validation.get("schema")},
            )
        )
    if validation.get("ok") is not True or validation.get("status") != "PASS":
        alerts.append(
            _alert(
                "BLOCK",
                "validation_not_pass",
                "Expansion policy requires a passing validation receipt.",
                {"ok": validation.get("ok"), "status": validation.get("status")},
            )
        )
    preview_path = validation.get("preview_path")
    if not isinstance(preview_path, str) or not preview_path:
        alerts.append(_alert("BLOCK", "missing_preview", "Expansion validation receipt has no preview path.", {}))
    elif not Path(preview_path).expanduser().is_file():
        alerts.append(
            _alert(
                "BLOCK",
                "preview_missing",
                "Expansion preview path does not exist.",
                {"preview_path": preview_path},
            )
        )
    if validation.get("applied") is not False:
        alerts.append(
            _alert(
                "BLOCK",
                "validation_receipt_not_pure",
                "Expansion policy requires a validation-only receipt with applied=false.",
                {"applied": validation.get("applied")},
            )
        )
    if require_clean_signal:
        if signal_receipt_path is None:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_signal_receipt",
                    "--require-clean-signal requires --signal-receipt.",
                    {},
                )
            )
        else:
            alerts.extend(_clean_signal_alerts(signal_receipt_path))
    return alerts


def _apply_alerts(
    *,
    validation: dict[str, Any],
    validation_receipt_path: Path,
    policy_receipt_path: Path | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if validation.get("schema") != DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_validation_schema",
                "Expansion apply requires a tau.dag_expansion_validation_receipt.v1 input.",
                {"path": str(validation_receipt_path), "schema": validation.get("schema")},
            )
        )
    if validation.get("ok") is not True or validation.get("status") != "PASS":
        alerts.append(
            _alert(
                "BLOCK",
                "validation_not_pass",
                "Expansion apply requires a passing validation receipt.",
                {"ok": validation.get("ok"), "status": validation.get("status")},
            )
        )
    preview_path = validation.get("preview_path")
    if not isinstance(preview_path, str) or not preview_path:
        alerts.append(_alert("BLOCK", "missing_preview", "Expansion validation receipt has no preview path.", {}))
    elif not Path(preview_path).expanduser().is_file():
        alerts.append(
            _alert(
                "BLOCK",
                "preview_missing",
                "Expansion preview path does not exist.",
                {"preview_path": preview_path},
            )
        )
    if validation.get("applied") is not False:
        alerts.append(
            _alert(
                "BLOCK",
                "validation_receipt_not_pure",
                "Expansion apply requires a validation-only receipt with applied=false.",
                {"applied": validation.get("applied")},
            )
        )
    source_dag_contract = validation.get("dag_contract")
    expected_source_sha = validation.get("dag_contract_sha256")
    if not isinstance(source_dag_contract, str) or not source_dag_contract:
        alerts.append(
            _alert(
                "BLOCK",
                "missing_source_dag_contract",
                "Expansion apply requires validation receipt dag_contract.",
                {},
            )
        )
    else:
        resolved_source_path = Path(source_dag_contract).expanduser().resolve()
        if not resolved_source_path.is_file():
            alerts.append(
                _alert(
                    "BLOCK",
                    "source_dag_contract_missing",
                    "Source DAG contract from validation receipt no longer exists.",
                    {"dag_contract": source_dag_contract},
                )
            )
        else:
            observed_source_sha = f"sha256:{_sha256(resolved_source_path)}"
            if not isinstance(expected_source_sha, str) or not expected_source_sha:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "missing_source_dag_contract_hash",
                        "Expansion apply requires validation receipt dag_contract_sha256.",
                        {"dag_contract": source_dag_contract},
                    )
                )
            elif observed_source_sha != expected_source_sha:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "source_dag_contract_hash_mismatch",
                        "Source DAG contract hash does not match the validation receipt.",
                        {
                            "dag_contract": source_dag_contract,
                            "expected": expected_source_sha,
                            "observed": observed_source_sha,
                        },
                    )
                )
    if policy_receipt_path is not None:
        policy = _load_object(policy_receipt_path, label="DAG expansion policy receipt")
        if policy.get("schema") != DAG_EXPANSION_POLICY_RECEIPT_SCHEMA:
            alerts.append(
                _alert(
                    "BLOCK",
                    "invalid_policy_schema",
                    "Expansion apply policy receipt has an unsupported schema.",
                    {"schema": policy.get("schema"), "path": str(policy_receipt_path)},
                )
            )
        if policy.get("ok") is not True or policy.get("apply_allowed") is not True:
            alerts.append(
                _alert(
                    "BLOCK",
                    "policy_not_allowing_apply",
                    "Expansion policy receipt does not allow apply.",
                    {
                        "ok": policy.get("ok"),
                        "status": policy.get("status"),
                        "apply_allowed": policy.get("apply_allowed"),
                    },
                )
            )
        expected_validation_path = policy.get("validation_receipt")
        if isinstance(expected_validation_path, str):
            resolved_expected_validation_path = (
                Path(expected_validation_path).expanduser().resolve()
            )
            if resolved_expected_validation_path != validation_receipt_path:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "policy_validation_receipt_mismatch",
                        "Expansion policy receipt references a different validation receipt.",
                        {
                            "expected": str(validation_receipt_path),
                            "observed": str(resolved_expected_validation_path),
                        },
                    )
                )
        expected_validation_sha = policy.get("validation_receipt_sha256")
        observed_validation_sha = f"sha256:{_sha256(validation_receipt_path)}"
        if expected_validation_sha != observed_validation_sha:
            alerts.append(
                _alert(
                    "BLOCK",
                    "policy_validation_hash_mismatch",
                    "Expansion policy receipt is stale for the supplied validation receipt.",
                    {"expected": expected_validation_sha, "observed": observed_validation_sha},
                )
            )
    else:
        alerts.append(
            _alert(
                "BLOCK",
                "missing_policy_receipt",
                "Expansion apply requires a policy receipt so orchestration policy remains explicit.",
                {},
            )
        )
    expected_preview_sha = validation.get("preview_sha256")
    preview_path = validation.get("preview_path")
    if isinstance(preview_path, str) and preview_path:
        resolved_preview_path = Path(preview_path).expanduser().resolve()
        observed_preview_sha = (
            f"sha256:{_sha256(resolved_preview_path)}"
            if resolved_preview_path.is_file()
            else None
        )
        if not isinstance(expected_preview_sha, str) or not expected_preview_sha:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_preview_hash",
                    "Expansion apply requires validation receipt preview_sha256.",
                    {"preview_path": preview_path},
                )
            )
        elif observed_preview_sha != expected_preview_sha:
            alerts.append(
                _alert(
                    "BLOCK",
                    "preview_hash_mismatch",
                    "Expanded DAG preview hash does not match the validation receipt.",
                    {
                        "preview_path": preview_path,
                        "expected": expected_preview_sha,
                        "observed": observed_preview_sha,
                    },
                )
            )
    return alerts


def _clean_signal_alerts(signal_receipt_path: Path) -> list[dict[str, Any]]:
    signal = _load_object(signal_receipt_path, label="DAG signal receipt")
    alerts: list[dict[str, Any]] = []
    if signal.get("schema") != "tau.dag_signal_receipt.v1":
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_signal_schema",
                "Expansion policy clean-signal gate requires tau.dag_signal_receipt.v1.",
                {"schema": signal.get("schema"), "path": str(signal_receipt_path)},
            )
        )
    if signal.get("ok") is not True or signal.get("source_ok") is not True:
        alerts.append(
            _alert(
                "BLOCK",
                "signal_not_clean",
                "Expansion policy clean-signal gate requires passing signal and source DAG receipts.",
                {
                    "ok": signal.get("ok"),
                    "status": signal.get("status"),
                    "source_ok": signal.get("source_ok"),
                    "source_status": signal.get("source_status"),
                },
            )
        )
    negative_signals = _dict_list(signal.get("negative_signals"))
    if negative_signals:
        alerts.append(
            _alert(
                "BLOCK",
                "negative_signals_present",
                "Expansion policy clean-signal gate requires zero negative DAG signals.",
                {"negative_signal_count": len(negative_signals)},
            )
        )
    return alerts


def _validate_new_node(
    node: dict[str, Any],
    existing_node_ids: set[str],
    existing_executors: set[str],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    node_id = node.get("id")
    agent = node.get("agent")
    executor = node.get("executor")
    if not isinstance(node_id, str) or not node_id:
        alerts.append(_alert("BLOCK", "invalid_new_node", "New node id is required.", {"node": node}))
    elif node_id in existing_node_ids:
        alerts.append(
            _alert(
                "BLOCK",
                "new_node_id_collision",
                "Expansion node id already exists in the DAG contract.",
                {"node_id": node_id},
            )
        )
    if not isinstance(agent, str) or not agent:
        alerts.append(_alert("BLOCK", "invalid_new_node", "New node agent is required.", {"node": node}))
    elif agent in DISALLOWED_NEW_AGENTS or agent not in ALLOWED_NEW_AGENTS:
        alerts.append(
            _alert(
                "BLOCK",
                "disallowed_new_node_agent",
                "First expansion slice allows only reviewer, validator, goal-guardian, or already-routable non-mutating research-auditor nodes.",
                {"node_id": node_id, "agent": agent},
            )
        )
    if not isinstance(executor, str) or not executor:
        alerts.append(_alert("BLOCK", "invalid_new_node", "New node executor is required.", {"node": node}))
    elif executor not in existing_executors:
        alerts.append(
            _alert(
                "BLOCK",
                "new_executor_not_allowed",
                "Expansion proposals may not introduce new executors in this slice.",
                {"node_id": node_id, "executor": executor, "existing_executors": sorted(existing_executors)},
            )
        )
    if "command_spec" in node:
        alerts.append(
            _alert(
                "BLOCK",
                "command_spec_change_not_allowed",
                "Expansion proposals may not add or change command specs in this slice.",
                {"node_id": node_id},
            )
        )
    if "provider" in node:
        alerts.append(
            _alert(
                "BLOCK",
                "provider_branch_not_allowed",
                "Provider branches are not allowed in the first expansion validation slice.",
                {"node_id": node_id},
            )
        )
    if bool(node.get("mutates")):
        alerts.append(
            _alert(
                "BLOCK",
                "mutating_expansion_not_allowed",
                "Mutating expansion nodes are not allowed in this slice.",
                {"node_id": node_id},
            )
        )
    return alerts


def _validate_new_edge(
    edge: dict[str, Any],
    existing_node_ids: set[str],
    new_node_ids: set[str],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    source = edge.get("from")
    target = edge.get("to")
    allowed = existing_node_ids | new_node_ids
    if not isinstance(source, str) or not source:
        alerts.append(_alert("BLOCK", "invalid_new_edge", "New edge source is required.", {"edge": edge}))
    elif source not in allowed:
        alerts.append(
            _alert(
                "BLOCK",
                "new_edge_source_unknown",
                "New edge source must be an existing or proposed node.",
                {"from": source},
            )
        )
    if not isinstance(target, str) or not target:
        alerts.append(_alert("BLOCK", "invalid_new_edge", "New edge target is required.", {"edge": edge}))
    elif target not in allowed:
        alerts.append(
            _alert(
                "BLOCK",
                "new_edge_target_unknown",
                "New edge target must be an existing or proposed node.",
                {"to": target},
            )
        )
    return alerts


def _expanded_contract(contract_payload: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    expanded = deepcopy(contract_payload)
    expanded["nodes"] = [*_dict_list(expanded.get("nodes")), *_dict_list(proposal.get("new_nodes"))]
    expanded["edges"] = [*_dict_list(expanded.get("edges")), *_dict_list(proposal.get("new_edges"))]
    return expanded


def _proposal_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "proposed_by": _author(proposal),
        "phase": proposal.get("phase") or proposal.get("run_state"),
        "new_node_count": len(_dict_list(proposal.get("new_nodes"))),
        "new_edge_count": len(_dict_list(proposal.get("new_edges"))),
    }


def _author(proposal: dict[str, Any]) -> str:
    proposed_by = proposal.get("proposed_by")
    if isinstance(proposed_by, str):
        return proposed_by
    if isinstance(proposed_by, dict):
        for key in ("agent", "name", "role"):
            value = proposed_by.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _dag_depth(payload: dict[str, Any]) -> int:
    entry = payload.get("entry_node")
    if not isinstance(entry, str):
        return 0
    edges = _dict_list(payload.get("edges"))
    successors: dict[str, list[str]] = {}
    for edge in edges:
        source = edge.get("from")
        target = edge.get("to")
        if isinstance(source, str) and isinstance(target, str):
            successors.setdefault(source, []).append(target)

    seen: set[str] = set()

    def depth(node_id: str) -> int:
        if node_id in seen:
            return 0
        seen.add(node_id)
        children = successors.get(node_id, [])
        if not children:
            seen.remove(node_id)
            return 0
        result = 1 + max(depth(child) for child in children)
        seen.remove(node_id)
        return result

    return depth(entry)


def _cycle_detected(payload: dict[str, Any]) -> bool:
    nodes = {
        str(item.get("id"))
        for item in _dict_list(payload.get("nodes"))
        if isinstance(item.get("id"), str)
    }
    successors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in _dict_list(payload.get("edges")):
        source = edge.get("from")
        target = edge.get("to")
        if isinstance(source, str) and isinstance(target, str) and target in nodes:
            successors.setdefault(source, []).append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visited:
            return False
        if node_id in visiting:
            return True
        visiting.add(node_id)
        for child in successors.get(node_id, []):
            if visit(child):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in sorted(nodes))


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("YAML expansion proposals require PyYAML")
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _alert(
    severity: str,
    code: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
