"""Project-agent DAG contract runner for Tau handoff loops."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.battle_scillm import (
    preflight_battle_scillm_auth,
    resolve_active_scillm_proxy_key,
)
from tau_coding.course_correction import build_course_correction_receipt
from tau_coding.dag_route_decision import (
    ROUTE_CONDITION_SCHEMA,
    ROUTE_DECISION_VALIDATION_CODES,
    ROUTE_MODES,
    RouteDecisionError,
    build_route_contract,
    evaluate_route_decision,
    normalize_route_condition,
    write_route_decision_receipt,
)
from tau_coding.evidence_manifest import write_evidence_validation_receipt
from tau_coding.handoff_dispatch import (
    dispatch_agent_handoff_command_once,
    load_agent_dispatch_command_spec,
    write_agent_handoff_command_loop_receipt,
)
from tau_coding.memory_evidence_gate import (
    read_gate_payload,
    write_evidence_case_gate_receipt,
    write_memory_intent_gate_receipt,
)
from tau_coding.policy_profile import zero_trust_preflight_receipt
from tau_coding.security_capability import (
    compile_capability_decision,
    validate_capability_declaration,
)
from tau_coding.security_context import resolve_security_context

try:  # YAML is available in the project lock through docs tooling, but keep JSON first.
    import yaml
except ImportError:  # pragma: no cover - exercised only in stripped runtime environments.
    yaml = None  # type: ignore[assignment]


DAG_CONTRACT_SCHEMA = "tau.dag_contract.v1"
DAG_RECEIPT_SCHEMA = "tau.dag_receipt.v1"
DAG_ERROR_SCHEMA = "tau.dag_error.v1"
DAG_PROGRESS_SCHEMA = "tau.dag_progress.v1"
FAIL_CLOSED_REGISTRY_SCHEMA = "tau.fail_closed_registry.v1"
DAG_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
PERSISTENT_SUBAGENT_SCHEMA = "tau.persistent_subagent.v1"
PROVIDER_COMMAND_TIMEOUT_SECONDS = 900.0

FAIL_CLOSED_REGISTRY: dict[str, dict[str, str]] = {
    "branch_goal_hash_divergence": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.branch_goal_hash_divergence",
    },
    "branch_target_divergence": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.branch_target_divergence",
    },
    "cleanup_apply_without_absence_proof": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_cleanup.absence_proof",
    },
    "goal_hash_mismatch": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.handoff.active_goal_hash",
    },
    "invalid_provider_receipt": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_receipt.schema_and_binding",
    },
    "malformed_handoff": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.handoff.schema",
    },
    "max_attempts_exceeded": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.node_attempts",
    },
    "missing_required_evidence": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.required_evidence",
    },
    "missing_required_join": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.required_join",
    },
    "missing_work_order_sha256": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_work_order.sha256",
    },
    "pointless_unit_test_drift": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.monitor_alerts.pointless_unit_test_drift",
    },
    "provider_auth_required": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_auth.failure_classifier",
    },
    "brave_search_required_after_two_attempts": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.retry_policy.brave_search_course_correction",
    },
    "evidence_case_boundary_mismatch": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.evidence_case_boundary",
    },
    "evidence_case_hash_missing": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.evidence_case_hash",
    },
    "evidence_case_policy_mismatch": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.evidence_case_policy",
    },
    "intent_clarify_required": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.intent_route",
    },
    "intent_contains_inline_evidence": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.no_inline_evidence",
    },
    "intent_deflected": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.intent_route",
    },
    "intent_not_planner_only": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.planner_only",
    },
    "memory_first_not_true": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.memory_first",
    },
    "missing_evidence_case": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.evidence_case_required",
    },
    "missing_memory_intent": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.memory_evidence_gate.intent_required",
    },
    "reviewer_goal_hash_mismatch": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.reviewer_goal_hash",
    },
    "target_changed": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.handoff.github_target",
    },
    "unexpected_edge": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.observed_edges",
    },
    "unexpected_node": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.observed_nodes",
    },
    "unresolved_block_alert": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.monitor_alerts.unresolved_block",
    },
    "unsupported_ready_queue_condition": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.ready_queue_conditions",
    },
    **{
        code: {
            "severity": "BLOCK",
            "implemented_by": "tau.validators.dag.typed_route_decision",
        }
        for code in (
            "invalid_route_condition",
            "invalid_route_mode",
            "route_mode_without_conditional_edges",
            "mixed_conditional_unconditional_routes",
            "conditional_route_source_requires_response",
            "conditional_target_multiple_predecessors",
            "typed_route_requires_bounded_ready_queue",
            "route_source_result_missing",
            "route_field_missing",
            "route_field_type_invalid",
            "route_comparison_type_mismatch",
            "route_no_match",
            "route_ambiguous_exclusive",
            "route_all_matching_incomplete",
            "route_decision_receipt_write_failed",
            "route_activation_invariant_violation",
            "route_source_result_invalid",
            "route_source_binding_mismatch",
            *ROUTE_DECISION_VALIDATION_CODES,
        )
    },
    "secure_mode_requires_handoff_loop": {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.secure_scheduler_boundary",
    },
}


@dataclass(frozen=True, slots=True)
class ProjectDagNode:
    node_id: str
    agent: str
    executor: str
    max_attempts: int
    command_spec: str | None
    required_evidence: tuple[str, ...]
    reviewer: dict[str, Any] | None
    context: dict[str, Any]
    requested_capabilities: tuple[dict[str, Any], ...]
    route_mode: str | None


@dataclass(frozen=True, slots=True)
class ProjectDagEdge:
    edge_index: int
    source: str
    target: str
    condition: object | None = None


@dataclass(frozen=True, slots=True)
class ProjectDagContract:
    payload: dict[str, Any]
    dag_id: str
    goal: dict[str, Any]
    target: dict[str, Any]
    entry_node: str
    terminal_nodes: tuple[str, ...]
    nodes: dict[str, ProjectDagNode]
    edges: tuple[ProjectDagEdge, ...]
    limits: dict[str, Any]
    context: dict[str, Any]
    required_evidence: tuple[str, ...]
    fail_closed_on: tuple[str, ...]
    evidence_manifest: str | None
    command_policy: str | None
    policy_profile: str | dict[str, Any] | None
    data_boundary: str | dict[str, Any] | None
    security_mode: str | None
    actor_access_manifest: str | dict[str, Any] | None
    environment_manifest: str | dict[str, Any] | None
    memory_intent: str | dict[str, Any] | None
    evidence_case: str | dict[str, Any] | None
    research_query_safety_receipt: str | None
    itar_access_preflight_receipt: str | None
    sandbox_run_receipt: str | None
    compliance_package_validation_receipt: str | None


def run_project_dag_contract(
    *,
    contract_path: Path,
    receipt_dir: Path | None = None,
    agents_root: Path,
    command_spec_root: Path | None = None,
    scheduler: str = "handoff-loop",
    security_mode: str | None = None,
) -> dict[str, Any]:
    """Run a project-agent DAG contract through the existing handoff command loop."""

    resolved_contract_path = contract_path.expanduser().resolve()
    payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(payload)
    resolved_receipt_dir = _resolve_receipt_dir(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=receipt_dir,
    )
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)

    if scheduler not in {"handoff-loop", "bounded-ready-queue"}:
        raise RuntimeError(f"unknown project DAG scheduler: {scheduler}")
    security_context_result = resolve_security_context(
        dag_contract=contract.payload,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
        requested_mode=security_mode or contract.security_mode,
    )
    if security_context_result.alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(security_context_result.alerts[0]["code"]).upper(),
            alerts=security_context_result.alerts,
            memory_intent_gate_receipt=None,
            evidence_case_gate_receipt=None,
            evidence_validation_receipt=None,
            zero_trust_preflight_receipt=None,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    capability_decision_receipt: dict[str, Any] | None = None
    provider_policy_alerts = _provider_policy_preflight(contract)
    if provider_policy_alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(provider_policy_alerts[0]["code"]).upper(),
            alerts=provider_policy_alerts,
            memory_intent_gate_receipt=None,
            evidence_case_gate_receipt=None,
            evidence_validation_receipt=None,
            zero_trust_preflight_receipt=None,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    zero_trust_alerts, zero_trust_receipt = _zero_trust_preflight(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
    )
    if zero_trust_alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(zero_trust_alerts[0]["code"]).upper(),
            alerts=zero_trust_alerts,
            memory_intent_gate_receipt=None,
            evidence_case_gate_receipt=None,
            evidence_validation_receipt=None,
            zero_trust_preflight_receipt=zero_trust_receipt,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    memory_alerts, memory_intent_receipt, evidence_case_receipt = _memory_evidence_preflight(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
    )
    if memory_alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(memory_alerts[0]["code"]).upper(),
            alerts=memory_alerts,
            evidence_validation_receipt=None,
            zero_trust_preflight_receipt=zero_trust_receipt,
            memory_intent_gate_receipt=memory_intent_receipt,
            evidence_case_gate_receipt=evidence_case_receipt,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    containment_alerts, containment_receipts = _containment_gate_preflight(
        contract=contract,
        contract_path=resolved_contract_path,
    )
    if containment_alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(containment_alerts[0]["code"]).upper(),
            alerts=containment_alerts,
            memory_intent_gate_receipt=memory_intent_receipt,
            evidence_case_gate_receipt=evidence_case_receipt,
            evidence_validation_receipt=None,
            zero_trust_preflight_receipt=zero_trust_receipt,
            containment_gate_receipts=containment_receipts,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    evidence_manifest_alerts, evidence_validation_receipt = _evidence_manifest_preflight(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
    )
    if evidence_manifest_alerts:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(evidence_manifest_alerts[0]["code"]).upper(),
            alerts=evidence_manifest_alerts,
            evidence_validation_receipt=evidence_validation_receipt,
            zero_trust_preflight_receipt=zero_trust_receipt,
            memory_intent_gate_receipt=memory_intent_receipt,
            evidence_case_gate_receipt=evidence_case_receipt,
            containment_gate_receipts=containment_receipts,
            security_context_receipt=security_context_result.receipt,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    scheduler_alert: dict[str, Any] | None = None
    if scheduler != "bounded-ready-queue" and _has_typed_routes(contract):
        scheduler_alert = _alert(
            "BLOCK",
            "typed_route_requires_bounded_ready_queue",
            "Typed route conditions require the bounded-ready-queue scheduler.",
            {"requested_scheduler": scheduler},
        )
    elif (
        scheduler == "bounded-ready-queue"
        and security_context_result.context.get("security_mode") == "secure"
    ):
        scheduler_alert = _alert(
            "BLOCK",
            "secure_mode_requires_handoff_loop",
            "Secure execution is currently authoritative only through the "
            "handoff-loop scheduler.",
            {"requested_scheduler": scheduler, "supported_scheduler": "handoff-loop"},
        )
    if scheduler_alert is not None:
        receipt = _pre_dispatch_blocked_receipt(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            verdict=str(scheduler_alert["code"]).upper(),
            alerts=[scheduler_alert],
            memory_intent_gate_receipt=memory_intent_receipt,
            evidence_case_gate_receipt=evidence_case_receipt,
            evidence_validation_receipt=evidence_validation_receipt,
            zero_trust_preflight_receipt=zero_trust_receipt,
            containment_gate_receipts=containment_receipts,
            security_context_receipt=security_context_result.receipt,
            capability_decision_receipt=None,
        )
        _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
        return receipt
    if security_context_result.context.get("security_mode") == "secure":
        command_policy = security_context_result.resolved_artifacts.get("command_policy")
        if not isinstance(command_policy, dict):
            raise RuntimeError("resolved secure command policy is unavailable")
        capability_decision_receipt = compile_capability_decision(
            dag_id=contract.dag_id,
            run_id=str(contract.payload.get("run_id") or contract.dag_id),
            goal_hash=str(contract.goal["goal_hash"]),
            security_context=security_context_result.context,
            command_policy=command_policy,
            nodes=[
                {
                    "node_id": node.node_id,
                    "executor": node.executor,
                    "attempt": 1,
                    "requested_capabilities": list(node.requested_capabilities),
                }
                for node in contract.nodes.values()
            ],
            receipt_dir=resolved_receipt_dir,
        )
        if capability_decision_receipt.get("status") != "PASS":
            receipt = _pre_dispatch_blocked_receipt(
                contract=contract,
                contract_path=resolved_contract_path,
                receipt_dir=resolved_receipt_dir,
                scheduler=scheduler,
                verdict="CAPABILITY_REQUEST_DENIED",
                alerts=list(capability_decision_receipt.get("alerts", [])),
                memory_intent_gate_receipt=memory_intent_receipt,
                evidence_case_gate_receipt=evidence_case_receipt,
                evidence_validation_receipt=evidence_validation_receipt,
                zero_trust_preflight_receipt=zero_trust_receipt,
                containment_gate_receipts=containment_receipts,
                security_context_receipt=security_context_result.receipt,
                capability_decision_receipt=capability_decision_receipt,
            )
            _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
            return receipt
    if scheduler == "bounded-ready-queue":
        return _run_bounded_ready_queue_project_dag(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            agents_root=agents_root.expanduser().resolve(),
            command_spec_root=command_spec_root,
        )

    compiled_spec_root = _compile_command_specs(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
        fallback_root=command_spec_root,
    )
    dag_agent_registry = _write_dag_agent_registry(
        contract=contract,
        receipt_dir=resolved_receipt_dir,
    )

    start_handoff = _start_handoff(contract, contract_path=resolved_contract_path)
    start_path = resolved_receipt_dir / "start-handoff.json"
    _write_json(start_path, start_handoff)

    max_steps = _max_steps(contract)
    loop_dir = resolved_receipt_dir / "command-loop"
    progress_events: list[dict[str, Any]] = []

    def record_progress(event: dict[str, Any]) -> None:
        progress_events.append(event)
        _write_project_dag_progress(
            contract=contract,
            receipt_dir=resolved_receipt_dir,
            scheduler=scheduler,
            events=progress_events,
            node_attempts=_command_loop_progress_attempts(progress_events),
            status="RUNNING",
        )

    loop = write_agent_handoff_command_loop_receipt(
        start_handoff,
        loop_dir,
        agent_registry_root=dag_agent_registry,
        command_spec_root=compiled_spec_root,
        active_goal_hash=str(contract.goal["goal_hash"]),
        max_steps=max_steps,
        command_policy_path=_contract_relative_path(
            contract.command_policy,
            resolved_contract_path,
        ),
        progress_callback=record_progress,
        secure_execution=(
            _secure_execution_configuration(
                contract=contract,
                receipt_dir=resolved_receipt_dir,
                security_context=security_context_result.context,
                capability_decision_receipt=capability_decision_receipt,
            )
            if security_context_result.context.get("security_mode") == "secure"
            else None
        ),
    )
    loop_payload = loop.as_dict()
    loop_receipt_path = loop_dir / "command-loop-receipt.json"
    if loop_receipt_path.exists():
        loop_payload = _read_json_object(loop_receipt_path, label="command-loop receipt")

    alerts = _evaluate_loop_against_contract(contract, loop_payload)
    status = "PASS" if not alerts and loop_payload.get("ok") is True else "BLOCKED"
    verdict = "PASS" if status == "PASS" else _blocked_verdict(alerts, loop_payload)
    receipt = {
        "schema": DAG_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": scheduler,
        "security_mode": security_context_result.receipt.get("security_mode"),
        "execution": "project_agent_dag_via_handoff_command_loop",
        "dag_id": contract.dag_id,
        "contract_path": str(resolved_contract_path),
        "contract_sha256": f"sha256:{_sha256(resolved_contract_path)}",
        "run_dir": str(resolved_receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "edge_count": len(contract.edges),
        "max_steps": max_steps,
        "command_loop_receipt": str(loop_receipt_path),
        "security_context_receipt": security_context_result.receipt.get("receipt_path"),
        "security_context_sha256": security_context_result.receipt.get(
            "security_context_sha256"
        ),
        "capability_decision_receipt": (
            capability_decision_receipt.get("receipt_path")
            if isinstance(capability_decision_receipt, dict)
            else None
        ),
        "selected_agents": [
            dispatch.get("selected_agent")
            for dispatch in _dispatches(loop_payload)
            if dispatch.get("selected_agent")
        ],
        "observed_edges": _observed_edges(contract, loop_payload),
        "node_attempts": _node_attempts(contract, loop_payload),
        "reviewer_verdicts": _reviewer_verdicts(contract, loop_payload),
        "alerts": alerts,
        "evidence_validation_receipt": (
            evidence_validation_receipt.get("receipt_path")
            if isinstance(evidence_validation_receipt, dict)
            else None
        ),
        "zero_trust_preflight_receipt": (
            zero_trust_receipt.get("receipt_path") if isinstance(zero_trust_receipt, dict) else None
        ),
        "memory_intent_gate_receipt": (
            memory_intent_receipt.get("receipt_path")
            if isinstance(memory_intent_receipt, dict)
            else None
        ),
        "evidence_case_gate_receipt": (
            evidence_case_receipt.get("receipt_path")
            if isinstance(evidence_case_receipt, dict)
            else None
        ),
        "containment_gate_receipts": {
            gate_name: gate_receipt.get("receipt_path")
            for gate_name, gate_receipt in containment_receipts.items()
            if isinstance(gate_receipt, dict)
        },
        "artifacts": [
            str(security_context_result.receipt_path),
            *(
                [str(capability_decision_receipt["receipt_path"])]
                if isinstance(capability_decision_receipt, dict)
                and isinstance(capability_decision_receipt.get("receipt_path"), str)
                else []
            ),
            str(start_path),
            str(loop_receipt_path),
            *loop.artifacts,
            *(
                [str(zero_trust_receipt["receipt_path"])]
                if isinstance(zero_trust_receipt, dict)
                and isinstance(zero_trust_receipt.get("receipt_path"), str)
                else []
            ),
            *(
                [str(memory_intent_receipt["receipt_path"])]
                if isinstance(memory_intent_receipt, dict)
                and isinstance(memory_intent_receipt.get("receipt_path"), str)
                else []
            ),
            *(
                [str(evidence_case_receipt["receipt_path"])]
                if isinstance(evidence_case_receipt, dict)
                and isinstance(evidence_case_receipt.get("receipt_path"), str)
                else []
            ),
            *(
                [str(evidence_validation_receipt["receipt_path"])]
                if isinstance(evidence_validation_receipt, dict)
                and isinstance(evidence_validation_receipt.get("receipt_path"), str)
                else []
            ),
            *[
                str(gate_receipt["receipt_path"])
                for gate_receipt in containment_receipts.values()
                if isinstance(gate_receipt, dict)
                and isinstance(gate_receipt.get("receipt_path"), str)
            ],
            *[
                str(path)
                for path in sorted((resolved_receipt_dir / "compiled-command-specs").rglob("*"))
                if path.is_file()
            ],
        ],
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": [
                "DAG contract parsed and validated.",
                "Entry node was compiled into a tau.agent_handoff.v1 start handoff.",
                (
                    "Secure node routing used the grant-bound Bubblewrap executor."
                    if security_context_result.context.get("security_mode") == "secure"
                    else "Node routing used the development command-loop subprocess runner."
                ),
                "Observed edges and retry counts were checked against the DAG contract.",
                "Reviewer verdict evidence was checked against the immutable goal hash.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Parallel DAG scheduling.",
                "GitHub mutation or ticket closure.",
                "Unbounded autonomous operation.",
                "Successful secure execution when the selected sandbox backend is unavailable.",
            ],
        },
        "errors": list(loop_payload.get("errors", [])) if isinstance(loop_payload, dict) else [],
        "timestamp": _utc_stamp(),
    }
    dag_error = _dag_error(
        contract=contract,
        receipt_dir=resolved_receipt_dir,
        scheduler=scheduler,
        status=status,
        verdict=verdict,
        alerts=alerts,
        errors=receipt["errors"],
        node_attempts=receipt["node_attempts"],
    )
    if dag_error is not None:
        receipt["dag_error"] = dag_error
    _write_project_dag_progress(
        contract=contract,
        receipt_dir=resolved_receipt_dir,
        scheduler=scheduler,
        events=progress_events,
        node_attempts=receipt["node_attempts"],
        status=status,
        verdict=verdict,
    )
    receipt["progress_path"] = str(resolved_receipt_dir / "dag-progress.json")
    _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
    return receipt


def dag_contract_error_payload(
    *,
    contract_path: Path,
    receipt_dir: Path | None,
    error: str,
    scheduler: str,
) -> dict[str, Any]:
    """Project-agent-readable error for DAGs that fail before execution starts."""

    resolved_contract_path = contract_path.expanduser().resolve()
    resolved_receipt_dir = (
        receipt_dir.expanduser().resolve()
        if receipt_dir is not None
        else resolved_contract_path.parent / ".tau-dag-run"
    )
    payload: dict[str, Any] = {
        "schema": DAG_ERROR_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "severity": "BLOCK",
        "failure_code": "dag_contract_invalid",
        "verdict": "DAG_CONTRACT_INVALID",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "message": error,
        "dag_id": None,
        "scheduler": scheduler,
        "contract_path": str(resolved_contract_path),
        "run_dir": str(resolved_receipt_dir),
        "receipt_path": str(resolved_receipt_dir / "dag-receipt.json"),
        "recommended_action": {
            "type": "repair_then_retry_or_reroute",
            "next_agent": "goal-guardian",
            "reason": (
                "Repair the DAG contract so it satisfies tau.dag_contract.v1 before dispatch."
            ),
        },
        "evidence": {
            "primary_alert": {
                "severity": "BLOCK",
                "code": "dag_contract_invalid",
                "message": error,
                "evidence": {
                    "contract_path": str(resolved_contract_path),
                    "scheduler": scheduler,
                },
            },
            "alert_count": 1,
            "alert_codes": ["dag_contract_invalid"],
            "errors": [error],
        },
        "proof_scope": {
            "proves": [
                "Tau rejected a malformed or incomplete DAG contract before dispatch.",
                "Tau packaged the contract failure as a project-agent course-correction payload.",
                "No DAG route, goal, target, command, or handoff was executed.",
            ],
            "does_not_prove": [
                "The repaired DAG contract will pass.",
                "Any subagent or provider command was executed.",
                "Provider/model semantic quality.",
            ],
        },
    }
    return payload


def _secure_execution_configuration(
    *,
    contract: ProjectDagContract,
    receipt_dir: Path,
    security_context: dict[str, Any],
    capability_decision_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the immutable inputs consumed by the secure handoff executor."""

    if not isinstance(capability_decision_receipt, dict):
        raise RuntimeError("secure execution requires a capability decision receipt")
    policy_ref = security_context.get("policy_profile")
    boundary_ref = security_context.get("data_boundary")
    if not isinstance(policy_ref, dict) or not isinstance(boundary_ref, dict):
        raise RuntimeError("secure execution requires resolved policy and boundary references")
    policy_path = policy_ref.get("path")
    boundary_path = boundary_ref.get("path")
    policy_sha256 = policy_ref.get("sha256")
    boundary_sha256 = boundary_ref.get("sha256")
    if not all(
        isinstance(value, str) and value
        for value in (policy_path, boundary_path, policy_sha256, boundary_sha256)
    ):
        raise RuntimeError("secure execution references must be path- and hash-bound")

    grants_by_node: dict[str, list[dict[str, Any]]] = {
        node_id: [] for node_id in contract.nodes
    }
    for grant in capability_decision_receipt.get("grants", []):
        if not isinstance(grant, dict):
            continue
        node_id = grant.get("node_id")
        if isinstance(node_id, str) and node_id in grants_by_node:
            grants_by_node[node_id].append(grant)

    return {
        "backend": "bwrap",
        "receipt_root": str(receipt_dir / "secure-execution"),
        "run_id": str(contract.payload.get("run_id") or contract.dag_id),
        "dag_id": contract.dag_id,
        "goal_hash": str(contract.goal["goal_hash"]),
        "security_context_sha256": str(security_context["security_context_sha256"]),
        "policy_profile_path": policy_path,
        "policy_profile_sha256": policy_sha256,
        "data_boundary_path": boundary_path,
        "data_boundary_sha256": boundary_sha256,
        "grants_by_node": grants_by_node,
    }


def _run_bounded_ready_queue_project_dag(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None,
) -> dict[str, Any]:
    """Run acyclic project DAG nodes when dependencies are ready."""

    graph_alerts = _ready_queue_contract_alerts(contract)
    if graph_alerts:
        receipt = _ready_queue_receipt(
            contract=contract,
            contract_path=contract_path,
            receipt_dir=receipt_dir,
            command_spec_root=command_spec_root,
            status="BLOCKED",
            verdict=str(graph_alerts[0]["code"]).upper(),
            alerts=graph_alerts,
            dispatches=[],
            events=[],
            node_attempts={},
            reviewer_verdicts=[],
            observed_edges=[],
            execution_seconds=0.0,
            max_observed_concurrency=0,
            errors=[],
        )
        _write_json(receipt_dir / "dag-receipt.json", receipt)
        return receipt

    command_spec_root = _compile_command_specs(
        contract=contract,
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        fallback_root=command_spec_root,
    )
    dag_agent_registry = _write_dag_agent_registry(contract=contract, receipt_dir=receipt_dir)

    max_concurrency = _max_concurrency(contract)
    runnable_nodes = {
        node_id
        for node_id, node in contract.nodes.items()
        if node.executor != "human" and node_id not in contract.terminal_nodes
    }
    incoming_edges = _incoming_edge_objects(contract)
    outgoing_edges = _outgoing_edge_objects(contract)
    completed: set[str] = set()
    resolved_sources: set[str] = set()
    activated_edges: set[int] = set()
    activated_terminals: set[str] = set()
    active_nodes: set[str] = {
        node_id for node_id in runnable_nodes if not incoming_edges.get(node_id)
    }
    failed = False
    dispatches: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    responses: dict[str, dict[str, Any]] = {}
    node_artifacts: dict[str, list[str]] = {}
    node_attempts: dict[str, int] = {}
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    course_correction_artifacts: list[str] = []
    route_decision_artifacts: list[str] = []
    intervals: list[tuple[float, float]] = []
    started_at = time.monotonic()

    def activate_edges(edges: list[ProjectDagEdge]) -> None:
        for edge in edges:
            activated_edges.add(edge.edge_index)
            if edge.target in contract.terminal_nodes:
                activated_terminals.add(edge.target)
            elif edge.target in runnable_nodes:
                active_nodes.add(edge.target)

    def resolve_unconditional_source(node_id: str) -> None:
        activate_edges(outgoing_edges.get(node_id, []))
        resolved_sources.add(node_id)
        completed.add(node_id)

    def node_ready(node_id: str) -> bool:
        if node_id not in active_nodes:
            return False
        incoming = incoming_edges.get(node_id, [])
        if not incoming:
            return True
        if any(_edge_is_conditional(edge) for edge in incoming):
            return (
                len(incoming) == 1
                and incoming[0].source in resolved_sources
                and incoming[0].edge_index in activated_edges
            )
        return all(
            edge.source in resolved_sources and edge.edge_index in activated_edges
            for edge in incoming
        )

    def mark_virtual_ready_nodes() -> None:
        changed = True
        while changed:
            changed = False
            for node_id in sorted(active_nodes - completed):
                node = contract.nodes[node_id]
                if node.command_spec:
                    continue
                if not node_ready(node_id):
                    continue
                resolve_unconditional_source(node_id)
                events.append(
                    {
                        "event": "virtual_node_completed",
                        "node_id": node_id,
                        "agent": node.agent,
                        "ts": _utc_stamp(),
                    }
                )
                _write_project_dag_progress(
                    contract=contract,
                    receipt_dir=receipt_dir,
                    scheduler="bounded-ready-queue",
                    events=events,
                    node_attempts=node_attempts,
                    status="RUNNING",
                )
                changed = True

    def ready_nodes(running: set[str]) -> list[str]:
        return [
            node_id
            for node_id in sorted(active_nodes - completed - running)
            if contract.nodes[node_id].command_spec
            and node_ready(node_id)
        ]

    mark_virtual_ready_nodes()
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures: dict[Future[dict[str, Any]], str] = {}
        while True:
            if failed:
                break
            running_node_ids = set(futures.values())
            for node_id in ready_nodes(running_node_ids):
                if len(futures) >= max_concurrency:
                    break
                node = contract.nodes[node_id]
                node_attempts[node_id] = node_attempts.get(node_id, 0) + 1
                if node_attempts[node_id] > node.max_attempts:
                    alerts.append(
                        _alert(
                            "BLOCK",
                            "max_attempts_exceeded",
                            "Node exceeded its DAG max_attempts.",
                            {
                                "node_id": node_id,
                                "attempts": node_attempts[node_id],
                                "max_attempts": node.max_attempts,
                            },
                        )
                    )
                    failed = True
                    break
                start_payload = _node_start_handoff(
                    contract,
                    node,
                    contract_path=contract_path,
                    predecessor_responses=[
                        responses[edge.source]
                        for edge in incoming_edges.get(node_id, [])
                        if edge.edge_index in activated_edges and edge.source in responses
                    ],
                )
                artifact_dir = (
                    receipt_dir / "ready-queue" / node_id / f"attempt-{node_attempts[node_id]:03d}"
                )
                future = executor.submit(
                    _dispatch_ready_node,
                    node=node,
                    start_payload=start_payload,
                    agents_root=dag_agent_registry,
                    command_spec_root=command_spec_root,
                    artifact_dir=artifact_dir,
                    command_policy_path=_contract_relative_path(
                        contract.command_policy,
                        contract_path,
                    ),
                )
                futures[future] = node_id
                events.append(
                    {
                        "event": "node_started",
                        "node_id": node_id,
                        "agent": node.agent,
                        "attempt": node_attempts[node_id],
                        "ts": _utc_stamp(),
                    }
                )
                _write_project_dag_progress(
                    contract=contract,
                    receipt_dir=receipt_dir,
                    scheduler="bounded-ready-queue",
                    events=events,
                    node_attempts=node_attempts,
                    status="RUNNING",
                )
            if failed:
                break
            if not futures:
                remaining = sorted(active_nodes - completed)
                if remaining:
                    alerts.append(
                        _alert(
                            "BLOCK",
                            "ready_queue_stalled",
                            "No active DAG node had satisfied dependencies.",
                            {"remaining_nodes": remaining, "completed_nodes": sorted(completed)},
                        )
                    )
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                node_id = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive executor boundary.
                    result = {
                        "ok": False,
                        "dispatch": None,
                        "response": None,
                        "started_monotonic": time.monotonic(),
                        "completed_monotonic": time.monotonic(),
                        "errors": [str(exc)],
                    }
                intervals.append(
                    (
                        float(result["started_monotonic"]),
                        float(result["completed_monotonic"]),
                    )
                )
                dispatch = result.get("dispatch")
                if isinstance(dispatch, dict):
                    dispatches.append(dispatch)
                    dispatch_path = (
                        receipt_dir
                        / "ready-queue"
                        / node_id
                        / f"attempt-{node_attempts[node_id]:03d}"
                        / "dispatch-receipt.json"
                    )
                    _write_json(dispatch_path, dispatch)
                node_artifacts[node_id] = [
                    str(path)
                    for path in sorted((receipt_dir / "ready-queue" / node_id).rglob("*"))
                    if path.is_file()
                ]
                events.append(
                    {
                        "event": "node_completed",
                        "node_id": node_id,
                        "agent": contract.nodes[node_id].agent,
                        "attempt": node_attempts[node_id],
                        "ok": result.get("ok") is True,
                        "ts": _utc_stamp(),
                    }
                )
                _write_project_dag_progress(
                    contract=contract,
                    receipt_dir=receipt_dir,
                    scheduler="bounded-ready-queue",
                    events=events,
                    node_attempts=node_attempts,
                    status="RUNNING",
                )
                if result.get("ok") is not True:
                    stop_reason = "node_dispatch_failed"
                    if isinstance(dispatch, dict):
                        stop_reason = str(dispatch.get("stop_reason") or stop_reason)
                    elif result.get("errors"):
                        stop_reason = _command_spec_load_stop_reason(
                            "\n".join(str(item) for item in result.get("errors", []))
                        )
                    can_retry = node_attempts[node_id] < contract.nodes[node_id].max_attempts
                    events.append(
                        {
                            "event": "node_attempt_failed",
                            "node_id": node_id,
                            "agent": contract.nodes[node_id].agent,
                            "attempt": node_attempts[node_id],
                            "retrying": can_retry,
                            "stop_reason": stop_reason,
                            "errors": result.get("errors", []),
                            "ts": _utc_stamp(),
                        }
                    )
                    _write_project_dag_progress(
                        contract=contract,
                        receipt_dir=receipt_dir,
                        scheduler="bounded-ready-queue",
                        events=events,
                        node_attempts=node_attempts,
                        status="RUNNING",
                    )
                    drift_alert = _pointless_unit_test_drift_alert(
                        node_id=node_id,
                        node=contract.nodes[node_id],
                        attempt=node_attempts[node_id],
                        stop_reason=stop_reason,
                        result=result,
                    )
                    if drift_alert is not None:
                        artifact = _write_course_correction_receipt(
                            contract=contract,
                            receipt_dir=receipt_dir,
                            node_id=node_id,
                            node=contract.nodes[node_id],
                            attempt=node_attempts[node_id],
                            code="pointless_unit_test_drift",
                            reason=drift_alert["message"],
                            stop_reason=stop_reason,
                            errors=[str(item) for item in result.get("errors", [])],
                        )
                        course_correction_artifacts.append(str(artifact))
                        alerts.append(drift_alert)
                        errors.extend(str(item) for item in result.get("errors", []))
                        failed = True
                        continue
                    if can_retry:
                        if node_attempts[node_id] >= 2:
                            artifact = _write_course_correction_receipt(
                                contract=contract,
                                receipt_dir=receipt_dir,
                                node_id=node_id,
                                node=contract.nodes[node_id],
                                attempt=node_attempts[node_id],
                                code="brave_search_required_after_two_attempts",
                                reason=(
                                    "Node has failed two attempts; require $brave-search "
                                    "research before another retry."
                                ),
                                stop_reason=stop_reason,
                                errors=[str(item) for item in result.get("errors", [])],
                            )
                            course_correction_artifacts.append(str(artifact))
                            alerts.append(
                                _alert(
                                    "BLOCK",
                                    "brave_search_required_after_two_attempts",
                                    (
                                        "Node reached two failed attempts and must use "
                                        "$brave-search before retry."
                                    ),
                                    {
                                        "node_id": node_id,
                                        "agent": contract.nodes[node_id].agent,
                                        "attempts": node_attempts[node_id],
                                        "max_attempts": contract.nodes[node_id].max_attempts,
                                        "stop_reason": stop_reason,
                                        "course_correction_receipt": str(artifact),
                                    },
                                )
                            )
                            errors.extend(str(item) for item in result.get("errors", []))
                            failed = True
                            continue
                        continue
                    alerts.append(
                        _alert(
                            "BLOCK",
                            stop_reason,
                            "Ready-queue node dispatch did not pass after max_attempts.",
                            {
                                "node_id": node_id,
                                "attempts": node_attempts[node_id],
                                "max_attempts": contract.nodes[node_id].max_attempts,
                                "errors": result.get("errors", []),
                            },
                        )
                    )
                    errors.extend(str(item) for item in result.get("errors", []))
                    failed = True
                    continue
                response = result.get("response")
                if isinstance(response, dict):
                    responses[node_id] = response
                    node_alerts = _node_response_alerts(contract, contract.nodes[node_id], response)
                    if node_alerts:
                        auth_alert = next(
                            (
                                item
                                for item in node_alerts
                                if item.get("code") == "provider_auth_required"
                            ),
                            None,
                        )
                        if auth_alert is not None:
                            can_retry = (
                                node_attempts[node_id] < contract.nodes[node_id].max_attempts
                            )
                            auth_evidence = (
                                auth_alert.get("evidence")
                                if isinstance(auth_alert.get("evidence"), dict)
                                else {}
                            )
                            auth_errors = [
                                str(item)
                                for item in auth_evidence.get("auth_errors", [])
                                if isinstance(item, str)
                            ]
                            repair_path = _write_provider_auth_repair_receipt(
                                receipt_dir=receipt_dir,
                                node_id=node_id,
                                node=contract.nodes[node_id],
                                attempt=node_attempts[node_id],
                                response=response,
                            )
                            repair_payload = json.loads(repair_path.read_text(encoding="utf-8"))
                            repair_ok = _provider_auth_repair_ready_for_retry(repair_payload)
                            auth_evidence["provider_auth_repair_receipt"] = str(repair_path)
                            auth_evidence["provider_auth_repair_status"] = repair_payload.get(
                                "status"
                            )
                            auth_evidence["provider_auth_repair_ok"] = repair_ok
                            events.append(
                                {
                                    "event": "provider_auth_repair_attempted",
                                    "node_id": node_id,
                                    "agent": contract.nodes[node_id].agent,
                                    "attempt": node_attempts[node_id],
                                    "repair_status": repair_payload.get("status"),
                                    "repair_ok": repair_ok,
                                    "retrying": (repair_ok and can_retry),
                                    "provider_auth_repair_receipt": str(repair_path),
                                    "ts": _utc_stamp(),
                                }
                            )
                            _write_project_dag_progress(
                                contract=contract,
                                receipt_dir=receipt_dir,
                                scheduler="bounded-ready-queue",
                                events=events,
                                node_attempts=node_attempts,
                                status="RUNNING",
                            )
                            if repair_ok and can_retry:
                                responses.pop(node_id, None)
                                continue
                            if repair_ok and not can_retry:
                                auth_errors.append(
                                    "provider auth repair passed but node retry budget is exhausted"
                                )
                            else:
                                auth_errors.extend(
                                    str(item)
                                    for item in repair_payload.get("errors", [])
                                    if isinstance(item, str)
                                )
                                env_refresh = _provider_auth_repair_env_refresh(repair_payload)
                                if isinstance(env_refresh, dict):
                                    auth_errors.extend(
                                        str(item)
                                        for item in env_refresh.get("errors", [])
                                        if isinstance(item, str)
                                    )
                            artifact = _write_course_correction_receipt(
                                contract=contract,
                                receipt_dir=receipt_dir,
                                node_id=node_id,
                                node=contract.nodes[node_id],
                                attempt=node_attempts[node_id],
                                code="provider_auth_required",
                                reason=auth_alert["message"],
                                stop_reason="provider_auth_required",
                                errors=auth_errors,
                            )
                            course_correction_artifacts.append(str(artifact))
                            auth_evidence["course_correction_receipt"] = str(artifact)
                            auth_alert["evidence"] = auth_evidence
                            errors.extend(auth_errors)
                        alerts.extend(node_alerts)
                        failed = True
                    else:
                        source_edges = outgoing_edges.get(node_id, [])
                        conditional_edges = [
                            edge for edge in source_edges if _edge_is_conditional(edge)
                        ]
                        if conditional_edges:
                            source_result = response.get("result")
                            if not isinstance(source_result, dict):
                                alerts.append(
                                    _alert(
                                        "BLOCK",
                                        "route_source_result_missing",
                                        "Conditional source response must contain an object "
                                        "result.",
                                        {"node_id": node_id},
                                    )
                                )
                                failed = True
                                continue
                            try:
                                route_contract = build_route_contract(
                                    source_node_id=node_id,
                                    mode=contract.nodes[node_id].route_mode,
                                    edges=[
                                        {
                                            "edge_index": edge.edge_index,
                                            "target": edge.target,
                                            "condition": edge.condition,
                                        }
                                        for edge in conditional_edges
                                    ],
                                )
                                decision = evaluate_route_decision(
                                    dag_id=contract.dag_id,
                                    goal_hash=str(contract.goal["goal_hash"]),
                                    source_node_id=node_id,
                                    attempt=node_attempts[node_id],
                                    source_result=source_result,
                                    route_contract=route_contract,
                                )
                            except RouteDecisionError as exc:
                                alerts.append(
                                    _alert(
                                        "BLOCK",
                                        exc.code,
                                        "Typed route evaluation rejected the source result.",
                                        {"node_id": node_id, "errors": [str(exc)]},
                                    )
                                )
                                errors.append(str(exc))
                                failed = True
                                continue
                            decision_path = (
                                receipt_dir
                                / "route-decisions"
                                / node_id
                                / f"attempt-{node_attempts[node_id]:03d}.json"
                            )
                            try:
                                write_route_decision_receipt(decision_path, decision)
                            except OSError as exc:
                                alerts.append(
                                    _alert(
                                        "BLOCK",
                                        "route_decision_receipt_write_failed",
                                        "Tau could not persist the route decision receipt.",
                                        {"node_id": node_id, "errors": [str(exc)]},
                                    )
                                )
                                errors.append(str(exc))
                                failed = True
                                continue
                            route_decision_artifacts.append(str(decision_path))
                            events.append(
                                {
                                    "event": "route_decided",
                                    "node_id": node_id,
                                    "attempt": node_attempts[node_id],
                                    "status": decision["status"],
                                    "selected_targets": decision["selected_targets"],
                                    "route_decision_receipt": str(decision_path),
                                    "ts": _utc_stamp(),
                                }
                            )
                            if decision["status"] != "PASS":
                                failure_code = str(decision["failure_code"])
                                alerts.append(
                                    _alert(
                                        "BLOCK",
                                        failure_code,
                                        "Typed route decision blocked successor activation.",
                                        {
                                            "node_id": node_id,
                                            "route_decision_receipt": str(decision_path),
                                            "selected_targets": [],
                                        },
                                    )
                                )
                                failed = True
                                continue
                            selected_targets = set(decision["selected_targets"])
                            activate_edges(
                                [
                                    edge
                                    for edge in conditional_edges
                                    if edge.target in selected_targets
                                ]
                            )
                            resolved_sources.add(node_id)
                            completed.add(node_id)
                        else:
                            resolve_unconditional_source(node_id)
                        mark_virtual_ready_nodes()
                else:
                    alerts.append(
                        _alert(
                            "BLOCK",
                            "missing_node_response",
                            "Ready-queue node did not return a JSON handoff response.",
                            {"node_id": node_id},
                        )
                    )
                    failed = True

    execution_seconds = round(time.monotonic() - started_at, 6)
    if not alerts and not activated_terminals:
        alerts.append(
            _alert(
                "BLOCK",
                "missing_terminal_route",
                "Completed DAG nodes do not reach a declared terminal node.",
                {
                    "completed_nodes": sorted(completed),
                    "terminal_nodes": list(contract.terminal_nodes),
                },
            )
        )
    observed_edges = _ready_queue_observed_edges(contract, activated_edges)
    reviewer_verdicts = [
        verdict
        for node_id, response in responses.items()
        if contract.nodes[node_id].reviewer is not None
        for verdict in _reviewer_verdict_evidence(response)
    ]
    status = "PASS" if not alerts else "BLOCKED"
    verdict = "PASS" if status == "PASS" else str(alerts[0]["code"]).upper()
    receipt = _ready_queue_receipt(
        contract=contract,
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        command_spec_root=command_spec_root,
        status=status,
        verdict=verdict,
        alerts=alerts,
        dispatches=dispatches,
        events=events,
        node_attempts=node_attempts,
        reviewer_verdicts=reviewer_verdicts,
        observed_edges=observed_edges,
        execution_seconds=execution_seconds,
        max_observed_concurrency=_max_observed_concurrency(intervals),
        errors=errors,
        node_artifacts=node_artifacts,
        course_correction_artifacts=course_correction_artifacts,
        route_decision_artifacts=route_decision_artifacts,
        resolved_sources=resolved_sources,
        activated_edges=activated_edges,
        activated_terminals=activated_terminals,
    )
    _write_project_dag_progress(
        contract=contract,
        receipt_dir=receipt_dir,
        scheduler="bounded-ready-queue",
        events=events,
        node_attempts=node_attempts,
        status=status,
        verdict=verdict,
    )
    receipt["progress_path"] = str(receipt_dir / "dag-progress.json")
    _write_json(receipt_dir / "dag-receipt.json", receipt)
    return receipt


def load_dag_contract_payload(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML DAG contract object."""

    text = path.expanduser().resolve().read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("YAML DAG contracts require PyYAML")
        payload = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"DAG contract root must be an object: {path}")
    return payload


def validate_dag_contract(payload: dict[str, Any]) -> ProjectDagContract:
    """Validate the strict project-agent DAG contract used by `tau dag-run`."""

    errors: list[str] = []
    if payload.get("schema") != DAG_CONTRACT_SCHEMA:
        errors.append(f"schema must be {DAG_CONTRACT_SCHEMA}")
    dag_id = _required_string(payload, "dag_id", errors)
    goal = _required_mapping(payload, "goal", errors)
    for key in ("goal_id", "goal_hash"):
        _required_string(goal, key, errors)
    if not isinstance(goal.get("goal_version"), (int, str)) or isinstance(
        goal.get("goal_version"), bool
    ):
        errors.append("goal.goal_version must be an integer or string")
    target = _required_mapping(payload, "target", errors)
    for key in ("repo", "target"):
        _required_string(target, key, errors)
    entry_node = _required_string(payload, "entry_node", errors)
    terminal_nodes = _string_list(payload.get("terminal_nodes"), "terminal_nodes", errors)
    _validate_dag_identifier(entry_node, "entry_node", errors)
    for index, terminal_node in enumerate(terminal_nodes):
        _validate_dag_identifier(terminal_node, f"terminal_nodes[{index}]", errors)
    limits = _required_mapping(payload, "limits", errors)
    context = _optional_context_mapping(payload.get("context"), "context", errors)
    if _int_value(limits.get("max_total_attempts"), "limits.max_total_attempts", errors) < 1:
        errors.append("limits.max_total_attempts must be at least 1")
    required_evidence = _string_list(
        payload.get("required_evidence"),
        "required_evidence",
        errors,
    )
    fail_closed_on = _string_list(payload.get("fail_closed_on"), "fail_closed_on", errors)
    _validate_fail_closed_on_registry(fail_closed_on, errors)
    evidence_manifest = payload.get("evidence_manifest")
    if evidence_manifest is not None and not isinstance(evidence_manifest, str):
        errors.append("evidence_manifest must be a string path when provided")
        evidence_manifest = None
    command_policy = payload.get("command_policy")
    if command_policy is not None and not isinstance(command_policy, str):
        errors.append("command_policy must be a string path when provided")
        command_policy = None
    policy_profile = payload.get("policy_profile")
    if policy_profile is not None and not isinstance(policy_profile, (str, dict)):
        errors.append("policy_profile must be a string path or object when provided")
        policy_profile = None
    data_boundary = payload.get("data_boundary")
    if data_boundary is not None and not isinstance(data_boundary, (str, dict)):
        errors.append("data_boundary must be a string path or object when provided")
        data_boundary = None
    security_mode = payload.get("security_mode")
    if security_mode is not None and security_mode not in {"development", "secure"}:
        errors.append("security_mode must be development or secure when provided")
        security_mode = None
    actor_access_manifest = payload.get("actor_access_manifest")
    if actor_access_manifest is not None and not isinstance(actor_access_manifest, (str, dict)):
        errors.append("actor_access_manifest must be a string path or object when provided")
        actor_access_manifest = None
    environment_manifest = payload.get("environment_manifest")
    if environment_manifest is not None and not isinstance(environment_manifest, (str, dict)):
        errors.append("environment_manifest must be a string path or object when provided")
        environment_manifest = None
    memory_intent = payload.get("memory_intent")
    if memory_intent is not None and not isinstance(memory_intent, (str, dict)):
        errors.append("memory_intent must be a string path or object when provided")
        memory_intent = None
    evidence_case = payload.get("evidence_case")
    if evidence_case is not None and not isinstance(evidence_case, (str, dict)):
        errors.append("evidence_case must be a string path or object when provided")
        evidence_case = None
    research_query_safety_receipt = payload.get("research_query_safety_receipt")
    if research_query_safety_receipt is not None and not isinstance(
        research_query_safety_receipt, str
    ):
        errors.append("research_query_safety_receipt must be a string path when provided")
        research_query_safety_receipt = None
    itar_access_preflight_receipt = payload.get("itar_access_preflight_receipt")
    if itar_access_preflight_receipt is not None and not isinstance(
        itar_access_preflight_receipt, str
    ):
        errors.append("itar_access_preflight_receipt must be a string path when provided")
        itar_access_preflight_receipt = None
    sandbox_run_receipt = payload.get("sandbox_run_receipt")
    if sandbox_run_receipt is not None and not isinstance(sandbox_run_receipt, str):
        errors.append("sandbox_run_receipt must be a string path when provided")
        sandbox_run_receipt = None
    compliance_package_validation_receipt = payload.get("compliance_package_validation_receipt")
    if compliance_package_validation_receipt is not None and not isinstance(
        compliance_package_validation_receipt, str
    ):
        errors.append("compliance_package_validation_receipt must be a string path when provided")
        compliance_package_validation_receipt = None
    nodes = _parse_nodes(payload.get("nodes"), errors)
    edges = _parse_edges(payload.get("edges"), errors)
    node_ids = set(nodes)
    if entry_node and entry_node not in node_ids:
        errors.append(f"entry_node is not a declared node: {entry_node}")
    for edge in edges:
        if edge.source not in node_ids:
            errors.append(f"edge.from is not a declared node: {edge.source}")
        if edge.target not in node_ids and edge.target not in terminal_nodes:
            errors.append(f"edge.to is not a declared node or terminal node: {edge.target}")
    if terminal_nodes and not any(edge.target in terminal_nodes for edge in edges):
        errors.append("at least one edge must route to a terminal node")
    if errors:
        raise RuntimeError("; ".join(errors))
    return ProjectDagContract(
        payload=payload,
        dag_id=dag_id,
        goal=goal,
        target=target,
        entry_node=entry_node,
        terminal_nodes=tuple(terminal_nodes),
        nodes=nodes,
        edges=tuple(edges),
        limits=limits,
        context=context,
        required_evidence=tuple(required_evidence),
        fail_closed_on=tuple(fail_closed_on),
        evidence_manifest=evidence_manifest,
        command_policy=command_policy,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        security_mode=security_mode,
        actor_access_manifest=actor_access_manifest,
        environment_manifest=environment_manifest,
        memory_intent=memory_intent,
        evidence_case=evidence_case,
        research_query_safety_receipt=research_query_safety_receipt,
        itar_access_preflight_receipt=itar_access_preflight_receipt,
        sandbox_run_receipt=sandbox_run_receipt,
        compliance_package_validation_receipt=compliance_package_validation_receipt,
    )


def fail_closed_registry_payload() -> dict[str, Any]:
    """Return the executable fail-closed invariant registry for DAG authors."""

    return {
        "schema": FAIL_CLOSED_REGISTRY_SCHEMA,
        "ok": True,
        "status": "ACTIVE",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "invariant_count": len(FAIL_CLOSED_REGISTRY),
        "invariants": {
            code: {
                "severity": meta["severity"],
                "implemented_by": meta["implemented_by"],
            }
            for code, meta in sorted(FAIL_CLOSED_REGISTRY.items())
        },
        "proof_scope": {
            "proves": [
                "Tau exposes the fail_closed_on invariant codes accepted by tau.dag_contract.v1.",
                "Each listed invariant has an implemented_by validator binding.",
                "Unknown fail_closed_on codes fail closed during DAG contract validation.",
            ],
            "does_not_prove": [
                "A particular DAG contract was executed.",
                "Every possible future invariant is implemented.",
                "Provider/model semantic quality.",
            ],
        },
    }


def write_fail_closed_registry_receipt(output_path: Path | None = None) -> dict[str, Any]:
    """Return and optionally write the fail-closed invariant registry receipt."""

    payload = fail_closed_registry_payload()
    if output_path is not None:
        resolved_output = output_path.expanduser().resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        payload["receipt_path"] = str(resolved_output)
        _write_json(resolved_output, payload)
    else:
        payload["receipt_path"] = None
    return payload


def _validate_fail_closed_on_registry(values: list[str], errors: list[str]) -> None:
    unknown = sorted({value for value in values if value not in FAIL_CLOSED_REGISTRY})
    if unknown:
        known = ", ".join(sorted(FAIL_CLOSED_REGISTRY))
        errors.append(
            "fail_closed_on contains unknown invariant code(s): "
            f"{', '.join(unknown)}. Known codes: {known}"
        )


def _evidence_manifest_preflight(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if contract.evidence_manifest is None:
        return [], None
    manifest_path = Path(contract.evidence_manifest)
    if not manifest_path.is_absolute():
        manifest_path = contract_path.parent / manifest_path
    try:
        receipt = write_evidence_validation_receipt(
            manifest_path=manifest_path,
            receipt_path=receipt_dir / "evidence-validation-receipt.json",
        )
    except RuntimeError as exc:
        return [
            _alert(
                "BLOCK",
                "evidence_manifest_invalid",
                "DAG evidence_manifest could not be validated.",
                {
                    "evidence_manifest": str(manifest_path.expanduser().resolve()),
                    "error": str(exc),
                },
            )
        ], None

    alerts: list[dict[str, Any]] = []
    if receipt.get("ok") is not True:
        alerts.append(
            _alert(
                "BLOCK",
                "evidence_manifest_invalid",
                "DAG evidence_manifest did not pass typed evidence validation.",
                {
                    "evidence_manifest": str(manifest_path.expanduser().resolve()),
                    "evidence_validation_receipt": receipt.get("receipt_path"),
                    "errors": receipt.get("errors", []),
                },
            )
        )
    covered_kinds = {
        item.get("kind")
        for item in receipt.get("checked_items", [])
        if isinstance(item, dict)
        and item.get("valid") is True
        and isinstance(item.get("kind"), str)
    }
    missing = [item for item in contract.required_evidence if item not in covered_kinds]
    if missing:
        alerts.append(
            _alert(
                "BLOCK",
                "evidence_manifest_missing_required_evidence",
                "DAG evidence_manifest does not cover all DAG-level required_evidence kinds.",
                {
                    "evidence_manifest": str(manifest_path.expanduser().resolve()),
                    "evidence_validation_receipt": receipt.get("receipt_path"),
                    "missing": missing,
                    "covered_kinds": sorted(covered_kinds),
                },
            )
        )
    return alerts, receipt


def _containment_gate_preflight(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    alerts: list[dict[str, Any]] = []
    receipts: dict[str, dict[str, Any]] = {}
    requirements = {
        "itar_access_preflight": (
            _requires_itar_access_preflight(contract),
            contract.itar_access_preflight_receipt,
            "tau.itar_access_preflight_receipt.v1",
            "missing_itar_access_preflight",
            "ITAR-classified DAGs require a PASS actor/access preflight receipt.",
        ),
        "research_query_safety": (
            _requires_research_query_safety(contract),
            contract.research_query_safety_receipt,
            "tau.research_query_safety_receipt.v1",
            "missing_research_query_safety",
            "External research DAGs require a PASS research-query safety receipt.",
        ),
        "sandbox_run": (
            _requires_sandbox_run(contract),
            contract.sandbox_run_receipt,
            "tau.sandbox_run_receipt.v1",
            "missing_sandbox_run",
            "Sandbox-required DAGs require a PASS sandbox run receipt.",
        ),
        "compliance_package_validation": (
            _requires_compliance_package_validation(contract),
            contract.compliance_package_validation_receipt,
            "tau.compliance_package_validation_receipt.v1",
            "missing_compliance_package_validation",
            "Review-package DAGs require a PASS compliance package validation receipt.",
        ),
    }
    for gate_name, (
        required,
        receipt_value,
        expected_schema,
        missing_code,
        missing_message,
    ) in requirements.items():
        if not required and receipt_value is None:
            continue
        if receipt_value is None:
            alerts.append(
                _alert(
                    "BLOCK",
                    missing_code,
                    missing_message,
                    {
                        "gate": gate_name,
                        "required_field": f"{gate_name}_receipt",
                    },
                )
            )
            continue
        path = _contract_relative_path(receipt_value, contract_path)
        assert path is not None
        try:
            receipt = _read_json_object(path.expanduser().resolve(), label=gate_name)
        except RuntimeError as exc:
            alerts.append(
                _alert(
                    "BLOCK",
                    f"{gate_name}_unreadable",
                    f"{gate_name} receipt could not be read.",
                    {"gate": gate_name, "path": str(path), "errors": [str(exc)]},
                )
            )
            continue
        receipts[gate_name] = {
            **receipt,
            "receipt_path": str(path.expanduser().resolve()),
        }
        alerts.extend(
            _containment_receipt_alerts(
                gate_name=gate_name,
                receipt=receipt,
                path=path.expanduser().resolve(),
                expected_schema=expected_schema,
                contract=contract,
            )
        )
    return alerts, receipts


def _containment_receipt_alerts(
    *,
    gate_name: str,
    receipt: dict[str, Any],
    path: Path,
    expected_schema: str,
    contract: ProjectDagContract,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if receipt.get("schema") != expected_schema:
        alerts.append(
            _alert(
                "BLOCK",
                f"{gate_name}_schema_mismatch",
                f"{gate_name} receipt schema mismatch.",
                {
                    "gate": gate_name,
                    "path": str(path),
                    "expected_schema": expected_schema,
                    "actual_schema": receipt.get("schema"),
                },
            )
        )
    if receipt.get("ok") is not True or receipt.get("status") != "PASS":
        alerts.append(
            _alert(
                "BLOCK",
                f"{gate_name}_not_passed",
                f"{gate_name} receipt did not PASS.",
                {
                    "gate": gate_name,
                    "path": str(path),
                    "status": receipt.get("status"),
                    "ok": receipt.get("ok"),
                    "alert_codes": receipt.get("alert_codes", []),
                },
            )
        )
    goal_hash = _receipt_goal_hash(receipt)
    if goal_hash is not None and goal_hash != contract.goal["goal_hash"]:
        alerts.append(
            _alert(
                "BLOCK",
                f"{gate_name}_goal_hash_mismatch",
                f"{gate_name} receipt goal hash does not match the DAG goal.",
                {
                    "gate": gate_name,
                    "path": str(path),
                    "expected_goal_hash": contract.goal["goal_hash"],
                    "actual_goal_hash": goal_hash,
                },
            )
        )
    if gate_name == "compliance_package_validation" and receipt.get("review_ready") is not True:
        alerts.append(
            _alert(
                "BLOCK",
                "compliance_package_not_review_ready",
                "Compliance package validation receipt is not review-ready.",
                {"gate": gate_name, "path": str(path)},
            )
        )
    return alerts


def _requires_itar_access_preflight(contract: ProjectDagContract) -> bool:
    if contract.payload.get("requires_itar_access_preflight") is True:
        return True
    boundary = _embedded_data_boundary(contract)
    if not boundary:
        return False
    classification = str(boundary.get("classification") or "").upper()
    return classification == "ITAR" or boundary.get("itar") is True


def _requires_research_query_safety(contract: ProjectDagContract) -> bool:
    if contract.payload.get("requires_external_research") is True:
        return True
    for node in contract.nodes.values():
        payload = _node_payload(contract, node.node_id)
        if payload.get("requires_external_research") is True:
            return True
        if payload.get("external_research") is True:
            return True
    return False


def _requires_sandbox_run(contract: ProjectDagContract) -> bool:
    if contract.payload.get("requires_sandbox") is True:
        return True
    for node in contract.nodes.values():
        payload = _node_payload(contract, node.node_id)
        if payload.get("sandbox_required") is True or payload.get("requires_sandbox") is True:
            return True
    return False


def _requires_compliance_package_validation(contract: ProjectDagContract) -> bool:
    if contract.payload.get("requires_compliance_package_validation") is True:
        return True
    return contract.payload.get("requires_review_ready_package") is True


def _embedded_data_boundary(contract: ProjectDagContract) -> dict[str, Any] | None:
    if isinstance(contract.data_boundary, dict):
        return contract.data_boundary
    return None


def _receipt_goal_hash(receipt: dict[str, Any]) -> str | None:
    goal_hash = receipt.get("goal_hash")
    if isinstance(goal_hash, str) and goal_hash:
        return goal_hash
    for key in ("data_boundary", "policy_profile", "actor_manifest"):
        value = receipt.get(key)
        if isinstance(value, dict):
            payload = value.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("goal_hash"), str):
                return payload["goal_hash"]
    return None


def _provider_policy_preflight(contract: ProjectDagContract) -> list[dict[str, Any]]:
    if not _provider_sensitive_contract(contract):
        return []
    alerts: list[dict[str, Any]] = []
    for node in contract.nodes.values():
        if node.executor == "human":
            continue
        node_payload = _node_payload(contract, node.node_id)
        model_policy = node_payload.get("model_policy")
        if not isinstance(model_policy, dict):
            alerts.append(
                _alert(
                    "BLOCK",
                    "provider_policy_missing",
                    "Provider-sensitive DAG node is missing model_policy.",
                    {"node_id": node.node_id, "agent": node.agent},
                )
            )
            alerts.append(
                _alert(
                    "BLOCK",
                    "model_unspecified",
                    "Provider-sensitive DAG node does not specify an explicit model.",
                    {"node_id": node.node_id, "agent": node.agent},
                )
            )
        else:
            missing_policy_fields = [
                field
                for field in ("provider", "auth")
                if not isinstance(model_policy.get(field), str) or not model_policy.get(field)
            ]
            if missing_policy_fields:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "provider_policy_missing",
                        "Provider-sensitive DAG node model_policy is missing provider/auth fields.",
                        {
                            "node_id": node.node_id,
                            "agent": node.agent,
                            "missing": missing_policy_fields,
                        },
                    )
                )
            if not isinstance(model_policy.get("model"), str) or not model_policy.get("model"):
                alerts.append(
                    _alert(
                        "BLOCK",
                        "model_unspecified",
                        "Provider-sensitive DAG node does not specify an explicit model.",
                        {"node_id": node.node_id, "agent": node.agent},
                    )
                )
        prompt_contract = node_payload.get("prompt_contract")
        if not _valid_prompt_contract(prompt_contract):
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_prompt_contract",
                    "Provider-sensitive DAG node is missing an explicit prompt contract.",
                    {"node_id": node.node_id, "agent": node.agent},
                )
            )
        if not _has_provider_route_evidence(node.required_evidence):
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_required_evidence",
                    "Provider-sensitive DAG node is missing required provider-route evidence.",
                    {
                        "node_id": node.node_id,
                        "agent": node.agent,
                        "missing": ["provider_route_receipt"],
                        "required_evidence": list(node.required_evidence),
                    },
                )
            )
    return alerts


def _provider_sensitive_contract(contract: ProjectDagContract) -> bool:
    if contract.payload.get("provider_sensitive") is True:
        return True
    if contract.payload.get("requires_provider_route") is True:
        return True
    context_text = json.dumps(contract.context, sort_keys=True)
    provider_markers = (
        "scillm_image_model",
        "scillm_image_auth",
        "provider_route",
        "provider_model",
        "oauth",
    )
    if any(marker in context_text for marker in provider_markers):
        return True
    for node_id in contract.nodes:
        node_payload = _node_payload(contract, node_id)
        if any(
            key in node_payload
            for key in (
                "model_policy",
                "prompt_contract",
                "provider_route",
                "requires_provider_route",
            )
        ):
            return True
    return False


def _provider_sensitive_node(contract: ProjectDagContract, node: ProjectDagNode) -> bool:
    if node.executor == "human":
        return False
    node_payload = _node_payload(contract, node.node_id)
    if isinstance(node_payload.get("model_policy"), dict):
        return True
    if isinstance(node_payload.get("provider"), dict):
        return True
    if node_payload.get("requires_provider_route") is True:
        return True
    if "provider_route" in node_payload:
        return True
    return _provider_sensitive_contract(contract) and _has_provider_route_evidence(
        node.required_evidence
    )


def _provider_command_timeout_policy(
    contract: ProjectDagContract,
    node: ProjectDagNode,
) -> dict[str, Any] | None:
    if not _provider_sensitive_node(contract, node):
        return None
    raw_timeout = (
        contract.limits.get("provider_command_timeout_seconds")
        if "provider_command_timeout_seconds" in contract.limits
        else contract.limits.get("provider_command_timeout_s", PROVIDER_COMMAND_TIMEOUT_SECONDS)
    )
    try:
        timeout_s = float(raw_timeout)
    except (TypeError, ValueError):
        timeout_s = PROVIDER_COMMAND_TIMEOUT_SECONDS
    if timeout_s <= 0:
        timeout_s = PROVIDER_COMMAND_TIMEOUT_SECONDS
    return {
        "schema": "tau.provider_command_timeout_policy.v1",
        "source": "tau_provider_command_timeout_policy",
        "timeout_s": timeout_s,
        "default_timeout_s": PROVIDER_COMMAND_TIMEOUT_SECONDS,
        "reason": (
            "Provider-sensitive command-backed DAG nodes require a Tau-owned timeout "
            "when the command spec omits timeout_s."
        ),
    }


def _valid_prompt_contract(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    path = value.get("path")
    if isinstance(path, str) and path:
        return True
    schema = value.get("schema")
    if isinstance(schema, str) and schema:
        return True
    system = value.get("system_prompt") or value.get("system")
    user = value.get("user_template") or value.get("user_prompt") or value.get("user")
    return isinstance(system, str) and bool(system) and isinstance(user, str) and bool(user)


def _has_provider_route_evidence(required_evidence: tuple[str, ...]) -> bool:
    markers = (
        "provider_route",
        "model_route",
        "model_policy",
        "oauth",
        "auth_route",
        "provider_receipt",
    )
    return any(any(marker in item for marker in markers) for item in required_evidence)


def _node_dispatch_metadata(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    *,
    include_context: bool,
) -> dict[str, Any]:
    node_payload = _node_payload(contract, node.node_id)
    metadata: dict[str, Any] = {
        "dag_id": contract.dag_id,
        "node_id": node.node_id,
        "agent": node.agent,
        "executor": node.executor,
        "goal": contract.goal,
        "target": contract.target,
        "required_evidence": list(node.required_evidence),
    }
    for key in (
        "provider",
        "model_policy",
        "prompt_contract",
        "provider_route",
        "persistent_subagent",
    ):
        value = node_payload.get(key)
        if value is not None:
            metadata[key] = value
    timeout_policy = _provider_command_timeout_policy(contract, node)
    if timeout_policy is not None:
        metadata["timeout_policy"] = timeout_policy
    if node_payload.get("requires_provider_route") is True:
        metadata["requires_provider_route"] = True
    if include_context:
        metadata["context"] = node.context
    return metadata


def _attach_node_dispatch_context(
    context: dict[str, Any],
    contract: ProjectDagContract,
    node: ProjectDagNode,
) -> None:
    metadata = _node_dispatch_metadata(contract, node, include_context=True)
    context["dag_node_id"] = node.node_id
    context["dag_agent_role"] = node.agent
    context["tau_dag_node"] = metadata
    for key in (
        "provider",
        "model_policy",
        "prompt_contract",
        "provider_route",
        "persistent_subagent",
    ):
        if key in metadata:
            context[key] = metadata[key]
    if metadata.get("requires_provider_route") is True:
        context["requires_provider_route"] = True


def _memory_evidence_preflight(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    memory_policy: dict[str, Any] = {}
    if contract.memory_intent is None and contract.evidence_case is None:
        if contract.policy_profile is None:
            return [], None, None
        policy_profile, _, policy_alerts = _resolve_policy_object(
            contract.policy_profile,
            contract_path=contract_path,
            field_name="policy_profile",
        )
        if policy_alerts:
            return policy_alerts, None, None
        memory_policy = policy_profile.get("memory") if isinstance(policy_profile, dict) else None
        if not isinstance(memory_policy, dict) or memory_policy.get("intent_required") is not True:
            return [], None, None
    elif contract.policy_profile is not None:
        policy_profile, _, policy_alerts = _resolve_policy_object(
            contract.policy_profile,
            contract_path=contract_path,
            field_name="policy_profile",
        )
        if policy_alerts:
            return policy_alerts, None, None
        candidate_memory_policy = (
            policy_profile.get("memory") if isinstance(policy_profile, dict) else None
        )
        if isinstance(candidate_memory_policy, dict):
            memory_policy = candidate_memory_policy
    min_confidence = _policy_min_intent_confidence(memory_policy)
    memory_intent, memory_path, memory_read_alerts = read_gate_payload(
        contract.memory_intent,
        contract_path=contract_path,
        label="memory_intent",
    )
    memory_receipt = write_memory_intent_gate_receipt(
        memory_intent=memory_intent,
        memory_intent_path=memory_path,
        dag_contract=contract.payload,
        min_confidence=min_confidence,
        receipt_path=receipt_dir / "memory-intent-gate-receipt.json",
    )
    if contract.policy_profile is not None:
        _rewrite_alert_code(
            memory_receipt,
            old="inline_memory_evidence_rejected",
            new="intent_contains_inline_evidence",
        )
    evidence_case, evidence_case_path, evidence_read_alerts = read_gate_payload(
        contract.evidence_case,
        contract_path=contract_path,
        label="evidence_case",
    )
    evidence_case_receipt = write_evidence_case_gate_receipt(
        evidence_case=evidence_case,
        evidence_case_path=evidence_case_path,
        dag_contract=contract.payload,
        memory_intent_receipt=memory_receipt,
        receipt_path=receipt_dir / "evidence-case-gate-receipt.json",
    )
    alerts = [
        *memory_read_alerts,
        *memory_receipt.get("alerts", []),
        *evidence_read_alerts,
        *evidence_case_receipt.get("alerts", []),
    ]
    return alerts, memory_receipt, evidence_case_receipt


def _policy_min_intent_confidence(memory_policy: dict[str, Any]) -> float:
    value = memory_policy.get("min_intent_confidence")
    if isinstance(value, bool):
        return 0.5
    if isinstance(value, (int, float)) and 0 <= float(value) <= 1:
        return float(value)
    return 0.5


def _contract_relative_path(value: str | None, contract_path: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return contract_path.parent / path


def _zero_trust_preflight(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if contract.policy_profile is None:
        return [], None
    policy_profile, policy_path, policy_alerts = _resolve_policy_object(
        contract.policy_profile,
        contract_path=contract_path,
        field_name="policy_profile",
    )
    data_boundary, boundary_path, boundary_alerts = _resolve_policy_object(
        contract.data_boundary,
        contract_path=contract_path,
        field_name="data_boundary",
    )
    receipt = zero_trust_preflight_receipt(
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        dag_contract=contract.payload,
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        dag_contract_path=contract_path,
    )
    alerts = [*policy_alerts, *boundary_alerts, *receipt["alerts"]]
    if alerts != receipt["alerts"]:
        receipt = {**receipt, "ok": False, "status": "BLOCKED", "alerts": alerts}
        receipt["alert_codes"] = [alert["code"] for alert in alerts]
    receipt_path = receipt_dir / "zero-trust-preflight-receipt.json"
    receipt["receipt_path"] = str(receipt_path)
    _write_json(receipt_path, receipt)
    return alerts, receipt


def _resolve_policy_object(
    value: str | dict[str, Any] | None,
    *,
    contract_path: Path,
    field_name: str,
) -> tuple[dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    if value is None:
        return None, None, []
    if isinstance(value, dict):
        return value, None, []
    path = _contract_relative_path(value, contract_path)
    assert path is not None
    try:
        payload = _read_json_object(path.expanduser().resolve(), label=field_name)
    except RuntimeError as exc:
        return (
            None,
            path,
            [
                _alert(
                    "BLOCK",
                    f"invalid_{field_name}",
                    f"Zero-trust {field_name} could not be read.",
                    {"path": str(path), "errors": [str(exc)]},
                )
            ],
        )
    if not isinstance(payload, dict):
        return None, path, []
    return payload, path.expanduser().resolve(), []


def _pre_dispatch_blocked_receipt(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    scheduler: str,
    verdict: str,
    alerts: list[dict[str, Any]],
    memory_intent_gate_receipt: dict[str, Any] | None,
    evidence_case_gate_receipt: dict[str, Any] | None,
    evidence_validation_receipt: dict[str, Any] | None,
    zero_trust_preflight_receipt: dict[str, Any] | None = None,
    containment_gate_receipts: dict[str, dict[str, Any]] | None = None,
    security_context_receipt: dict[str, Any] | None = None,
    capability_decision_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = [str(contract_path)]
    containment_gate_receipts = containment_gate_receipts or {}
    if isinstance(security_context_receipt, dict) and isinstance(
        security_context_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(security_context_receipt["receipt_path"]))
    if isinstance(capability_decision_receipt, dict) and isinstance(
        capability_decision_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(capability_decision_receipt["receipt_path"]))
    if isinstance(zero_trust_preflight_receipt, dict) and isinstance(
        zero_trust_preflight_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(zero_trust_preflight_receipt["receipt_path"]))
    if isinstance(evidence_validation_receipt, dict) and isinstance(
        evidence_validation_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(evidence_validation_receipt["receipt_path"]))
    if isinstance(memory_intent_gate_receipt, dict) and isinstance(
        memory_intent_gate_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(memory_intent_gate_receipt["receipt_path"]))
    if isinstance(evidence_case_gate_receipt, dict) and isinstance(
        evidence_case_gate_receipt.get("receipt_path"),
        str,
    ):
        artifacts.append(str(evidence_case_gate_receipt["receipt_path"]))
    for gate_receipt in containment_gate_receipts.values():
        if isinstance(gate_receipt.get("receipt_path"), str):
            artifacts.append(str(gate_receipt["receipt_path"]))
    receipt = {
        "schema": DAG_RECEIPT_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": scheduler,
        "execution": "project_agent_dag_pre_dispatch_validation",
        "dag_id": contract.dag_id,
        "contract_path": str(contract_path),
        "contract_sha256": f"sha256:{_sha256(contract_path)}",
        "run_dir": str(receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "edge_count": len(contract.edges),
        "selected_agents": [],
        "observed_edges": [],
        "node_attempts": {},
        "reviewer_verdicts": [],
        "alerts": alerts,
        "security_mode": (
            security_context_receipt.get("security_mode")
            if isinstance(security_context_receipt, dict)
            else contract.security_mode or "development"
        ),
        "security_context_receipt": (
            security_context_receipt.get("receipt_path")
            if isinstance(security_context_receipt, dict)
            else None
        ),
        "security_context_sha256": (
            security_context_receipt.get("security_context_sha256")
            if isinstance(security_context_receipt, dict)
            else None
        ),
        "capability_decision_receipt": (
            capability_decision_receipt.get("receipt_path")
            if isinstance(capability_decision_receipt, dict)
            else None
        ),
        "command_executed": False,
        "provider_invoked": False,
        "filesystem_mutation_performed": False,
        "evidence_validation_receipt": (
            evidence_validation_receipt.get("receipt_path")
            if isinstance(evidence_validation_receipt, dict)
            else None
        ),
        "memory_intent_gate_receipt": (
            memory_intent_gate_receipt.get("receipt_path")
            if isinstance(memory_intent_gate_receipt, dict)
            else None
        ),
        "evidence_case_gate_receipt": (
            evidence_case_gate_receipt.get("receipt_path")
            if isinstance(evidence_case_gate_receipt, dict)
            else None
        ),
        "zero_trust_preflight_receipt": (
            zero_trust_preflight_receipt.get("receipt_path")
            if isinstance(zero_trust_preflight_receipt, dict)
            else None
        ),
        "containment_gate_receipts": {
            gate_name: gate_receipt.get("receipt_path")
            for gate_name, gate_receipt in containment_gate_receipts.items()
            if isinstance(gate_receipt, dict)
        },
        "artifacts": artifacts,
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": [
                "DAG contract parsed and validated.",
                "Tau inspected pre-dispatch gates before dispatch.",
                "No node command, provider, GitHub, Memory, or browser action was executed.",
            ],
            "does_not_prove": [
                "The repaired DAG contract will pass.",
                "Provider/model semantic quality.",
                "Runtime route mutation or GitHub side effects.",
            ],
        },
        "errors": [
            str(error)
            for alert in alerts
            for error in (
                alert.get("evidence", {}).get("errors", [])
                if isinstance(alert.get("evidence"), dict)
                else []
            )
        ],
        "timestamp": _utc_stamp(),
    }
    dag_error = _dag_error(
        contract=contract,
        receipt_dir=receipt_dir,
        scheduler=scheduler,
        status="BLOCKED",
        verdict=verdict,
        alerts=alerts,
        errors=receipt["errors"],
        node_attempts={},
    )
    if dag_error is not None:
        receipt["dag_error"] = dag_error
    return receipt


def _parse_nodes(value: object, errors: list[str]) -> dict[str, ProjectDagNode]:
    if not isinstance(value, list) or not value:
        errors.append("nodes must be a non-empty list")
        return {}
    nodes: dict[str, ProjectDagNode] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"nodes[{index}] must be an object")
            continue
        node_id = _required_string(item, "id", errors, prefix=f"nodes[{index}]")
        _validate_dag_identifier(node_id, f"nodes[{index}].id", errors)
        agent = _required_string(item, "agent", errors, prefix=f"nodes[{index}]")
        executor = str(item.get("executor") or "local")
        max_attempts = int(item.get("max_attempts", 1))
        if max_attempts < 1:
            errors.append(f"nodes[{index}].max_attempts must be at least 1")
        required_evidence = _string_list(
            item.get("required_evidence", []),
            f"nodes[{index}].required_evidence",
            errors,
        )
        command_spec = item.get("command_spec")
        reviewer = item.get("reviewer")
        if reviewer is not None and not isinstance(reviewer, dict):
            errors.append(f"nodes[{index}].reviewer must be an object")
            reviewer = None
        context = _optional_context_mapping(
            item.get("context"),
            f"nodes[{index}].context",
            errors,
        )
        requested_capabilities_value = item.get("requested_capabilities", [])
        requested_capabilities: list[dict[str, Any]] = []
        if not isinstance(requested_capabilities_value, list):
            errors.append(f"nodes[{index}].requested_capabilities must be a list")
        else:
            for capability_index, declaration in enumerate(requested_capabilities_value):
                errors.extend(
                    validate_capability_declaration(
                        declaration,
                        label=(
                            f"nodes[{index}].requested_capabilities[{capability_index}]"
                        ),
                    )
                )
                if isinstance(declaration, dict):
                    requested_capabilities.append(dict(declaration))
        _validate_persistent_subagent_declaration(
            item.get("persistent_subagent"),
            node_label=f"nodes[{index}]",
            required_evidence=required_evidence,
            errors=errors,
        )
        if node_id in nodes:
            errors.append(f"duplicate node id: {node_id}")
            continue
        nodes[node_id] = ProjectDagNode(
            node_id=node_id,
            agent=agent,
            executor=executor,
            max_attempts=max_attempts,
            command_spec=str(command_spec) if isinstance(command_spec, str) else None,
            required_evidence=tuple(required_evidence),
            reviewer=reviewer,
            context=context,
            requested_capabilities=tuple(requested_capabilities),
            route_mode=(
                str(item["route"]["mode"])
                if isinstance(item.get("route"), dict)
                and isinstance(item["route"].get("mode"), str)
                else None
            ),
        )
    return nodes


def _validate_persistent_subagent_declaration(
    value: object,
    *,
    node_label: str,
    required_evidence: list[str],
    errors: list[str],
) -> None:
    if value is None:
        return
    label = f"{node_label}.persistent_subagent"
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    if value.get("schema") != PERSISTENT_SUBAGENT_SCHEMA:
        errors.append(f"{label}.schema must be {PERSISTENT_SUBAGENT_SCHEMA}")
    for key in (
        "surface_id",
        "surface_url",
        "session_mode",
        "tau_control",
        "dag_parameter",
    ):
        if not isinstance(value.get(key), str) or not value.get(key):
            errors.append(f"{label}.{key} must be a non-empty string")
    surface_url = value.get("surface_url")
    if (
        isinstance(surface_url, str)
        and surface_url
        and not (
            surface_url.startswith("http://localhost:")
            or surface_url.startswith("http://127.0.0.1:")
        )
    ):
        errors.append(
            f"{label}.surface_url must use a local UX route "
            "(http://localhost:<port>/... or http://127.0.0.1:<port>/...)"
        )
    if value.get("session_mode") != "persistent":
        errors.append(
            f"{label}.session_mode must be persistent for a declared persistent subagent"
        )
    if value.get("tau_control") != "bounded_receipt_gated_ticks":
        errors.append(
            f"{label}.tau_control must be bounded_receipt_gated_ticks so Tau remains "
            "the DAG authority"
        )
    if value.get("unbounded_autonomy_allowed") is not False:
        errors.append(f"{label}.unbounded_autonomy_allowed must be false")
    required_receipts = _string_list(
        value.get("required_receipts"),
        f"{label}.required_receipts",
        errors,
    )
    if not required_receipts:
        errors.append(f"{label}.required_receipts must name at least one receipt schema")
    if "persistent_subagent_receipt" not in required_evidence:
        errors.append(
            f"{node_label}.required_evidence must include persistent_subagent_receipt "
            "when persistent_subagent is declared"
        )


def _parse_edges(value: object, errors: list[str]) -> list[ProjectDagEdge]:
    if not isinstance(value, list) or not value:
        errors.append("edges must be a non-empty list")
        return []
    edges: list[ProjectDagEdge] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"edges[{index}] must be an object")
            continue
        source = _required_string(item, "from", errors, prefix=f"edges[{index}]")
        target = _required_string(item, "to", errors, prefix=f"edges[{index}]")
        _validate_dag_identifier(source, f"edges[{index}].from", errors)
        _validate_dag_identifier(target, f"edges[{index}].to", errors)
        condition = item.get("condition")
        key = (source, target)
        if key in seen:
            errors.append(f"duplicate edge: {source}->{target}")
            continue
        seen.add(key)
        edges.append(
            ProjectDagEdge(
                edge_index=index,
                source=source,
                target=target,
                condition=condition,
            )
        )
    return edges


def _compile_command_specs(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    fallback_root: Path | None,
) -> Path | None:
    nodes_with_specs = [node for node in contract.nodes.values() if node.command_spec]
    if not nodes_with_specs:
        return fallback_root.expanduser().resolve() if fallback_root is not None else None
    compiled_root = receipt_dir / "compiled-command-specs"
    for node in nodes_with_specs:
        source = Path(str(node.command_spec))
        if not source.is_absolute():
            source = contract_path.parent / source
        if source.is_dir():
            source = source / "tau-dispatch-command.json"
        if not source.is_file():
            raise RuntimeError(f"command_spec for node {node.node_id} does not exist: {source}")
        target = compiled_root / node.node_id / "tau-dispatch-command.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_compiled_command_spec(
            source=source,
            target=target,
            contract=contract,
            node=node,
        )
    if fallback_root is not None:
        for node in contract.nodes.values():
            if node.command_spec:
                continue
            resolved_fallback_root = fallback_root.expanduser().resolve()
            source = resolved_fallback_root / node.node_id / "tau-dispatch-command.json"
            if not source.is_file():
                source = resolved_fallback_root / node.agent / "tau-dispatch-command.json"
            if source.is_file():
                target = compiled_root / node.node_id / "tau-dispatch-command.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_compiled_command_spec(
                    source=source,
                    target=target,
                    contract=contract,
                    node=node,
                )
    return compiled_root


def _write_compiled_command_spec(
    *,
    source: Path,
    target: Path,
    contract: ProjectDagContract,
    node: ProjectDagNode,
) -> None:
    payload = _read_json_object(source, label=f"command_spec:{node.node_id}")
    payload["tau_dag_node"] = _node_dispatch_metadata(
        contract,
        node,
        include_context=False,
    )
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_dag_agent_registry(*, contract: ProjectDagContract, receipt_dir: Path) -> Path:
    registry_root = receipt_dir / "dag-agent-registry"
    for node in contract.nodes.values():
        node_dir = registry_root / node.node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        agents_md = node_dir / "AGENTS.md"
        agents_md.write_text(
            "\n".join(
                [
                    "---",
                    f"id: {node.node_id}",
                    "active: true",
                    f"tau_role: {node.agent}",
                    f"tau_executor: {node.executor}",
                    "---",
                    "",
                    f"# DAG node {node.node_id}",
                    "",
                    f"Role: `{node.agent}`.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return registry_root


def _start_handoff(contract: ProjectDagContract, *, contract_path: Path) -> dict[str, Any]:
    entry = contract.nodes[contract.entry_node]
    context = _handoff_context(
        summary=f"Dispatch DAG contract {contract.dag_id}.",
        artifacts=[str(contract_path)],
        contract_context=contract.context,
        node_context=entry.context,
    )
    _attach_node_dispatch_context(context, contract, entry)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": contract.target["repo"],
            "target": contract.target["target"],
        },
        "goal": contract.goal,
        "previous_subagent": "human",
        "context": context,
        "result": {
            "status": "DAG_DISPATCH_REQUESTED",
            "summary": f"Tau is dispatching entry node {contract.entry_node}.",
            "evidence": [
                {
                    "kind": "dag_contract",
                    "schema": DAG_CONTRACT_SCHEMA,
                    "path": str(contract_path),
                    "sha256": f"sha256:{_sha256(contract_path)}",
                }
            ],
        },
        "rationale": "The DAG contract is the authoritative workflow and immutable goal boundary.",
        "next_agent": {
            "name": entry.node_id,
            "executor": entry.executor,
            "reason": f"Entry node for DAG {contract.dag_id} using role {entry.agent}.",
        },
        "required_evidence": list(contract.required_evidence),
        "stop_condition": "Stop at a terminal DAG node or any fail-closed invariant violation.",
    }


def _evaluate_loop_against_contract(
    contract: ProjectDagContract,
    loop_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if loop_payload.get("ok") is not True:
        stop_reason = str(loop_payload.get("stop_reason") or "command_loop_blocked")
        alerts.append(
            _alert(
                "BLOCK",
                stop_reason,
                "Underlying handoff command loop did not pass.",
                {"errors": loop_payload.get("errors", [])},
            )
        )
    dispatches = _dispatches(loop_payload)
    if not dispatches:
        alerts.append(_alert("BLOCK", "missing_dispatch", "DAG did not dispatch any node.", {}))
        return alerts

    selected = [str(dispatch.get("selected_agent")) for dispatch in dispatches]
    expected_entry_node = contract.nodes[contract.entry_node].node_id
    if selected[0] != expected_entry_node:
        alerts.append(
            _alert(
                "BLOCK",
                "entry_node_mismatch",
                "First selected agent does not match DAG entry node.",
                {"expected": expected_entry_node, "observed": selected[0]},
            )
        )
    for edge in _observed_edges(contract, loop_payload):
        if not _edge_allowed(contract, str(edge["from_node"]), str(edge["to_node"])):
            alerts.append(
                _alert(
                    "BLOCK",
                    "unexpected_edge",
                    "Observed handoff route is not allowed by DAG contract.",
                    edge,
                )
            )
    attempts = _node_attempts(contract, loop_payload)
    for node_id, count in attempts.items():
        max_attempts = contract.nodes[node_id].max_attempts
        if count > max_attempts:
            alerts.append(
                _alert(
                    "BLOCK",
                    "max_attempts_exceeded",
                    "Node exceeded its DAG max_attempts.",
                    {"node_id": node_id, "attempts": count, "max_attempts": max_attempts},
                )
            )
    for dispatch in dispatches:
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is None:
            alerts.append(
                _alert(
                    "BLOCK",
                    "unexpected_node",
                    "Selected agent is not declared in the DAG contract.",
                    {"selected_agent": dispatch.get("selected_agent")},
                )
            )
            continue
        response = _response_payload(dispatch)
        if response is None:
            continue
        missing = _missing_required_evidence(node.required_evidence, response)
        if missing:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_required_evidence",
                    "Node response did not include required evidence.",
                    {"node_id": node.node_id, "missing": missing},
                )
            )
        if node.reviewer is not None:
            reviewer_alerts = _reviewer_alerts(contract, node, response)
            alerts.extend(reviewer_alerts)
    return alerts


def _reviewer_alerts(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    verdicts = _reviewer_verdict_evidence(response)
    if not verdicts:
        return [
            _alert(
                "BLOCK",
                "missing_reviewer_verdict",
                "Reviewer node did not emit reviewer_verdict evidence.",
                {"node_id": node.node_id},
            )
        ]
    alerts: list[dict[str, Any]] = []
    expected_reviewed = (
        node.reviewer.get("reviews_node") if isinstance(node.reviewer, dict) else None
    )
    for verdict in verdicts:
        if verdict.get("goal_hash") != contract.goal["goal_hash"]:
            alerts.append(
                _alert(
                    "BLOCK",
                    "reviewer_goal_hash_mismatch",
                    "Reviewer verdict does not cite the immutable goal hash.",
                    {
                        "node_id": node.node_id,
                        "expected_goal_hash": contract.goal["goal_hash"],
                        "observed_goal_hash": verdict.get("goal_hash"),
                    },
                )
            )
        if expected_reviewed and verdict.get("reviewed_node_id") != expected_reviewed:
            alerts.append(
                _alert(
                    "BLOCK",
                    "reviewer_target_mismatch",
                    "Reviewer verdict did not review the expected creator node.",
                    {
                        "node_id": node.node_id,
                        "expected_reviewed_node": expected_reviewed,
                        "observed_reviewed_node": verdict.get("reviewed_node_id"),
                    },
                )
            )
    return alerts


def _node_response_alerts(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    auth_alert = _provider_auth_failure_alert(node, response)
    if auth_alert is not None:
        alerts.append(auth_alert)
        return alerts
    missing = _missing_required_evidence(node.required_evidence, response)
    if missing:
        alerts.append(
            _alert(
                "BLOCK",
                "missing_required_evidence",
                "Node response did not include required evidence.",
                {"node_id": node.node_id, "missing": missing},
            )
        )
    if node.reviewer is not None:
        alerts.extend(_reviewer_alerts(contract, node, response))
    return alerts


def _provider_auth_failure_alert(
    node: ProjectDagNode,
    response: dict[str, Any],
) -> dict[str, Any] | None:
    failures = _provider_auth_failures(response)
    if not failures:
        return None
    return _alert(
        "BLOCK",
        "provider_auth_required",
        (
            "Provider authentication failed before required evidence could be produced. "
            "Repair or refresh OAuth/readiness before retrying this DAG node."
        ),
        {
            "node_id": node.node_id,
            "agent": node.agent,
            "auth_errors": failures,
            "required_evidence": list(node.required_evidence),
        },
    )


def _provider_auth_failures(value: Any) -> list[str]:
    failures: list[str] = []
    _collect_provider_auth_failures(value, failures=failures)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in failures:
        normalized = " ".join(item.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _collect_provider_auth_failures(value: Any, *, failures: list[str]) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _collect_provider_auth_failures(child, failures=failures)
        return
    if isinstance(value, list):
        for child in value:
            _collect_provider_auth_failures(child, failures=failures)
        return
    if not isinstance(value, str):
        return
    lower = value.lower()
    has_auth_error = (
        "401" in lower
        or ("403" in lower and "permission_denied" in lower)
        or "permission denied" in lower
        or "unauthorized" in lower
        or "leaked api key" in lower
        or "api key leaked" in lower
        or "oauth" in lower
        and any(marker in lower for marker in ("expired", "stale", "invalid", "auth"))
    )
    if not has_auth_error:
        return
    if any(
        marker in lower
        for marker in (
            "http error 401",
            "http error 403",
            "401 unauthorized",
            "403 permission_denied",
            "permission_denied",
            "permission denied",
            "unauthorized",
            "leaked api key",
            "api key leaked",
            "oauth",
            "auth failed",
            "authentication failed",
        )
    ):
        failures.append(value)


def _ready_queue_contract_alerts(contract: ProjectDagContract) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    outgoing = _outgoing_edge_objects(contract)
    incoming = _incoming_edge_objects(contract)
    for source, edges in outgoing.items():
        conditional = [edge for edge in edges if _edge_is_conditional(edge)]
        unconditional = [edge for edge in edges if not _edge_is_conditional(edge)]
        node_payload = _node_payload(contract, source)
        route = node_payload.get("route")
        if route is not None:
            if (
                not isinstance(route, dict)
                or set(route) != {"mode"}
                or route.get("mode") not in ROUTE_MODES
            ):
                alerts.append(
                    _alert(
                        "BLOCK",
                        "invalid_route_mode",
                        "Node route must contain only a supported mode.",
                        {"node_id": source, "route": _json_safe_alert_value(route)},
                    )
                )
            elif not conditional:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "route_mode_without_conditional_edges",
                        "A route mode requires conditional outgoing edges.",
                        {"node_id": source, "mode": route["mode"]},
                    )
                )
        invalid_condition = False
        for edge in conditional:
            try:
                normalize_route_condition(edge.condition)
            except RouteDecisionError as exc:
                invalid_condition = True
                message = str(exc)
                if exc.code == "unsupported_ready_queue_condition":
                    message = (
                        "Bounded ready-queue does not evaluate edge conditions. Remove the "
                        "conditions or use a future typed route evaluator before dispatch."
                    )
                alerts.append(
                    _alert(
                        "BLOCK",
                        exc.code,
                        message,
                        {
                            "edges": [
                                {
                                    "from": edge.source,
                                    "to": edge.target,
                                    "condition": _json_safe_alert_value(edge.condition),
                                }
                            ]
                        },
                    )
                )
        if invalid_condition:
            continue
        if conditional and unconditional:
            alerts.append(
                _alert(
                    "BLOCK",
                    "mixed_conditional_unconditional_routes",
                    "A source may not mix conditional and unconditional outgoing edges.",
                    {"node_id": source},
                )
            )
        if conditional and not contract.nodes[source].command_spec:
            alerts.append(
                _alert(
                    "BLOCK",
                    "conditional_route_source_requires_response",
                    "A conditional route source must produce a command response.",
                    {"node_id": source},
                )
            )
    for target, edges in incoming.items():
        if len(edges) > 1 and any(_edge_is_conditional(edge) for edge in edges):
            alerts.append(
                _alert(
                    "BLOCK",
                    "conditional_target_multiple_predecessors",
                    "A conditional target may not have multiple predecessors before "
                    "join policy exists.",
                    {"target": target, "edge_indexes": [edge.edge_index for edge in edges]},
                )
            )
    if _cycle_detected(contract):
        alerts.append(
            _alert(
                "BLOCK",
                "cycle_detected",
                "Bounded ready-queue scheduler requires an acyclic DAG contract.",
                {},
            )
        )
    non_local_nodes = [
        node.node_id
        for node in contract.nodes.values()
        if node.command_spec and node.executor != "local"
    ]
    if non_local_nodes:
        alerts.append(
            _alert(
                "BLOCK",
                "non_local_ready_queue_node_not_allowed",
                "Bounded ready-queue scheduler only dispatches local command nodes in this slice.",
                {"node_ids": non_local_nodes},
            )
        )
    provider_nodes = [
        node.node_id
        for node in contract.nodes.values()
        if isinstance(_node_payload(contract, node.node_id).get("provider"), dict)
        or node.executor == "provider"
    ]
    if provider_nodes:
        alerts.append(
            _alert(
                "BLOCK",
                "provider_node_not_allowed",
                "Bounded ready-queue scheduler does not accept provider branches "
                "until branch locks exist.",
                {"node_ids": provider_nodes},
            )
        )
    mutating_nodes = [
        node.node_id
        for node in contract.nodes.values()
        if bool(contract.payload.get("mutating"))
        or bool(_node_payload(contract, node.node_id).get("mutates"))
    ]
    if mutating_nodes:
        alerts.append(
            _alert(
                "BLOCK",
                "mutating_node_not_allowed",
                "Bounded ready-queue scheduler only accepts non-mutating local nodes "
                "in this slice.",
                {"node_ids": mutating_nodes},
            )
        )
    return alerts


def _edge_is_conditional(edge: ProjectDagEdge) -> bool:
    return edge.condition is not None and edge.condition != ""


def _outgoing_edge_objects(contract: ProjectDagContract) -> dict[str, list[ProjectDagEdge]]:
    outgoing = {node_id: [] for node_id in contract.nodes}
    for edge in contract.edges:
        outgoing.setdefault(edge.source, []).append(edge)
    return outgoing


def _incoming_edge_objects(contract: ProjectDagContract) -> dict[str, list[ProjectDagEdge]]:
    incoming = {node_id: [] for node_id in contract.nodes}
    for terminal in contract.terminal_nodes:
        incoming.setdefault(terminal, [])
    for edge in contract.edges:
        incoming.setdefault(edge.target, []).append(edge)
    return incoming


def _has_typed_routes(contract: ProjectDagContract) -> bool:
    return any(
        isinstance(edge.condition, dict)
        and edge.condition.get("schema") == ROUTE_CONDITION_SCHEMA
        for edge in contract.edges
    )


def _node_payload(contract: ProjectDagContract, node_id: str) -> dict[str, Any]:
    raw_nodes = contract.payload.get("nodes")
    if not isinstance(raw_nodes, list):
        return {}
    for item in raw_nodes:
        if isinstance(item, dict) and item.get("id") == node_id:
            return item
    return {}


def _cycle_detected(contract: ProjectDagContract) -> bool:
    graph = _successors(contract, include_terminals=False)
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in permanent:
            return False
        if node_id in temporary:
            return True
        temporary.add(node_id)
        for child in graph.get(node_id, set()):
            if visit(child):
                return True
        temporary.remove(node_id)
        permanent.add(node_id)
        return False

    return any(visit(node_id) for node_id in contract.nodes)


def _predecessors(contract: ProjectDagContract) -> dict[str, set[str]]:
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in contract.nodes}
    for edge in contract.edges:
        if edge.target in predecessors and edge.source in contract.nodes:
            predecessors[edge.target].add(edge.source)
    return predecessors


def _successors(
    contract: ProjectDagContract,
    *,
    include_terminals: bool = True,
) -> dict[str, set[str]]:
    successors: dict[str, set[str]] = {node_id: set() for node_id in contract.nodes}
    for edge in contract.edges:
        if edge.source not in successors:
            continue
        if edge.target in contract.nodes or include_terminals:
            successors[edge.source].add(edge.target)
    return successors


def _node_dependencies_satisfied(
    node_id: str,
    predecessors: dict[str, set[str]],
    completed: set[str],
) -> bool:
    return predecessors.get(node_id, set()).issubset(completed)


def _terminal_reachable_from_completed(
    contract: ProjectDagContract,
    completed: set[str],
    successors: dict[str, set[str]],
) -> bool:
    return any(
        node_id in completed
        and any(target in contract.terminal_nodes for target in successors[node_id])
        for node_id in completed
    )


def _ready_queue_observed_edges(
    contract: ProjectDagContract,
    activated_edges: set[int],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in contract.edges:
        if edge.edge_index not in activated_edges:
            continue
        source_node = contract.nodes[edge.source]
        target_node = contract.nodes.get(edge.target)
        edges.append(
            {
                "from_node": edge.source,
                "from_agent": source_node.agent,
                "to_node": edge.target,
                "to_agent": target_node.agent if target_node else edge.target,
            }
        )
    return edges


def _node_start_handoff(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    *,
    contract_path: Path,
    predecessor_responses: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence: list[Any] = [
        {
            "kind": "dag_contract",
            "schema": DAG_CONTRACT_SCHEMA,
            "path": str(contract_path),
            "sha256": f"sha256:{_sha256(contract_path)}",
        }
    ]
    artifacts: list[str] = [str(contract_path)]
    for response in predecessor_responses:
        evidence.extend(_result_evidence(response))
        context = response.get("context")
        if isinstance(context, dict):
            artifacts.extend(
                str(item) for item in context.get("artifacts", []) if isinstance(item, str)
            )
    context = _handoff_context(
        summary=f"Dispatch ready DAG node {node.node_id}.",
        artifacts=artifacts,
        contract_context=contract.context,
        node_context=node.context,
    )
    _attach_node_dispatch_context(context, contract, node)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": contract.target["repo"],
            "target": contract.target["target"],
        },
        "goal": contract.goal,
        "previous_subagent": "human",
        "context": context,
        "result": {
            "status": "DAG_NODE_READY",
            "summary": f"Dependencies are satisfied for DAG node {node.node_id}.",
            "evidence": evidence,
        },
        "rationale": "The DAG contract is the authoritative workflow and immutable goal boundary.",
        "next_agent": {
            "name": node.agent,
            "executor": node.executor,
            "reason": f"Ready DAG node {node.node_id} in {contract.dag_id}.",
        },
        "required_evidence": list(node.required_evidence),
        "stop_condition": "Stop at a terminal DAG node or any fail-closed invariant violation.",
    }


def _dispatch_ready_node(
    *,
    node: ProjectDagNode,
    start_payload: dict[str, Any],
    agents_root: Path,
    command_spec_root: Path | None,
    artifact_dir: Path,
    command_policy_path: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        spec = load_agent_dispatch_command_spec(
            agents_root,
            node.node_id,
            command_spec_root=command_spec_root,
            command_policy_path=command_policy_path,
        )
        spec = _apply_provider_command_timeout_policy(spec, start_payload)
        dispatch = dispatch_agent_handoff_command_once(
            start_payload,
            list(spec["command"]),
            timeout_s=float(spec["timeout_s"]),
            cwd=spec.get("cwd") if isinstance(spec.get("cwd"), Path) else None,
            active_goal_hash=str(start_payload["goal"]["goal_hash"]),
            agent_registry_root=agents_root,
            artifact_dir=artifact_dir,
            command_spec_metadata=spec,
        )
        dispatch_payload = dispatch.as_dict()
        response = _response_payload(dispatch_payload)
        return {
            "ok": dispatch.ok,
            "dispatch": dispatch_payload,
            "response": response,
            "started_monotonic": started,
            "completed_monotonic": time.monotonic(),
            "errors": list(dispatch.errors),
        }
    except Exception as exc:
        return {
            "ok": False,
            "dispatch": None,
            "response": None,
            "started_monotonic": started,
            "completed_monotonic": time.monotonic(),
            "errors": [str(exc)],
        }


def _apply_provider_command_timeout_policy(
    spec: dict[str, object],
    start_payload: dict[str, Any],
) -> dict[str, object]:
    """Apply Tau-owned provider timeout only when command spec omitted timeout_s."""

    context = start_payload.get("context")
    tau_dag_node = context.get("tau_dag_node") if isinstance(context, dict) else None
    timeout_policy = tau_dag_node.get("timeout_policy") if isinstance(tau_dag_node, dict) else None
    if not isinstance(timeout_policy, dict):
        return spec
    if spec.get("timeout_s_source") == "command_spec":
        return spec
    timeout_s = timeout_policy.get("timeout_s")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        return spec
    updated = dict(spec)
    updated["timeout_s"] = float(timeout_s)
    updated["timeout_s_source"] = str(
        timeout_policy.get("source") or "tau_provider_command_timeout_policy"
    )
    updated["timeout_policy"] = dict(timeout_policy)
    return updated


def _max_concurrency(contract: ProjectDagContract) -> int:
    raw = contract.limits.get("max_concurrency", 2)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return 2
    return raw


def _max_observed_concurrency(intervals: list[tuple[float, float]]) -> int:
    points: list[tuple[float, int]] = []
    for start, end in intervals:
        points.append((start, 1))
        points.append((end, -1))
    active = 0
    max_active = 0
    for _, delta in sorted(points, key=lambda item: (item[0], -item[1])):
        active += delta
        max_active = max(max_active, active)
    return max_active


def _ready_queue_receipt(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    command_spec_root: Path | None,
    status: str,
    verdict: str,
    alerts: list[dict[str, Any]],
    dispatches: list[dict[str, Any]],
    events: list[dict[str, Any]],
    node_attempts: dict[str, int],
    reviewer_verdicts: list[dict[str, Any]],
    observed_edges: list[dict[str, Any]],
    execution_seconds: float,
    max_observed_concurrency: int,
    errors: list[str],
    node_artifacts: dict[str, list[str]] | None = None,
    course_correction_artifacts: list[str] | None = None,
    route_decision_artifacts: list[str] | None = None,
    resolved_sources: set[str] | None = None,
    activated_edges: set[int] | None = None,
    activated_terminals: set[str] | None = None,
) -> dict[str, Any]:
    proves = [
        "DAG contract parsed and validated.",
        "Bounded ready-queue scheduler preflight checked unsupported edge conditions, "
        "acyclicity, local-only execution, provider branches, and mutating branches.",
    ]
    if dispatches:
        proves.extend(
            [
                "Ready nodes were dispatched by the bounded ready-queue scheduler.",
                "Independent ready nodes can run concurrently when dependencies are satisfied.",
                "Each dispatched node used the real local command subprocess runner.",
                "Node evidence and reviewer verdicts were checked against the immutable goal hash.",
            ]
        )
    receipt = {
        "schema": DAG_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": "bounded-ready-queue",
        "execution": "project_agent_dag_bounded_ready_queue",
        "dag_id": contract.dag_id,
        "contract_path": str(contract_path),
        "contract_sha256": f"sha256:{_sha256(contract_path)}",
        "run_dir": str(receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "edge_count": len(contract.edges),
        "max_steps": _max_steps(contract),
        "max_concurrency": _max_concurrency(contract),
        "max_observed_concurrency": max_observed_concurrency,
        "execution_seconds": execution_seconds,
        "command_spec_root": str(command_spec_root) if command_spec_root else None,
        "selected_agents": [
            dispatch.get("selected_agent")
            for dispatch in dispatches
            if dispatch.get("selected_agent")
        ],
        "command_executed": bool(dispatches),
        "observed_edges": observed_edges,
        "node_attempts": node_attempts,
        "reviewer_verdicts": reviewer_verdicts,
        "scheduler_events": events,
        "dispatches": dispatches,
        "alerts": alerts,
        "artifacts": [
            str(path)
            for path in sorted(receipt_dir.rglob("*"))
            if path.is_file() and path.name != "dag-receipt.json"
        ],
        "node_artifacts": node_artifacts or {},
        "course_correction_artifacts": course_correction_artifacts or [],
        "route_decision_receipts": route_decision_artifacts or [],
        "resolved_sources": sorted(resolved_sources or set()),
        "activated_edges": sorted(activated_edges or set()),
        "activated_terminals": sorted(activated_terminals or set()),
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": proves,
            "does_not_prove": [
                "Provider/model semantic quality.",
                "GitHub mutation or ticket closure.",
                "Mutating branch safety.",
                "Unbounded autonomous operation.",
            ],
        },
        "errors": errors,
        "timestamp": _utc_stamp(),
    }
    dag_error = _dag_error(
        contract=contract,
        receipt_dir=receipt_dir,
        scheduler="bounded-ready-queue",
        status=status,
        verdict=verdict,
        alerts=alerts,
        errors=errors,
        node_attempts=node_attempts,
    )
    if dag_error is not None:
        receipt["dag_error"] = dag_error
    return receipt


def _command_loop_progress_attempts(events: list[dict[str, Any]]) -> dict[str, int]:
    attempts: dict[str, int] = {}
    for event in events:
        if event.get("event") != "step_started":
            continue
        selected_agent = event.get("selected_agent")
        if isinstance(selected_agent, str) and selected_agent:
            attempts[selected_agent] = attempts.get(selected_agent, 0) + 1
    return attempts


def _write_project_dag_progress(
    *,
    contract: ProjectDagContract,
    receipt_dir: Path,
    scheduler: str,
    events: list[dict[str, Any]],
    node_attempts: dict[str, int],
    status: str,
    verdict: str | None = None,
) -> None:
    node_progress = _project_dag_node_progress(contract, events, node_attempts)
    active_subagents = [
        {
            "node_id": item["node_id"],
            "agent": item["agent"],
            "attempt": item["attempt"],
        }
        for item in node_progress
        if item["status"] == "RUNNING"
    ]
    completed_subagents = [
        {"node_id": item["node_id"], "agent": item["agent"]}
        for item in node_progress
        if item["status"] == "COMPLETED"
    ]
    payload = {
        "schema": DAG_PROGRESS_SCHEMA,
        "ok": status not in {"BLOCKED", "FAIL", "FAILED"},
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": scheduler,
        "dag_id": contract.dag_id,
        "run_dir": str(receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "node_progress": node_progress,
        "active_subagents": active_subagents,
        "completed_subagents": completed_subagents,
        "event_count": len(events),
        "last_event": events[-1] if events else None,
        "events": events,
        "proof_scope": {
            "proves": [
                "Tau has written a durable local progress artifact for this DAG run.",
                (
                    "Operators can inspect active and completed subagents before the final "
                    "DAG receipt exists."
                ),
            ],
            "does_not_prove": [
                "The active subprocess will finish successfully.",
                "Provider/model semantic quality.",
                "Final DAG acceptance before dag-receipt.json is written.",
            ],
        },
        "updated_at": _utc_stamp(),
    }
    _write_json(receipt_dir / "dag-progress.json", payload)


def _project_dag_node_progress(
    contract: ProjectDagContract,
    events: list[dict[str, Any]],
    node_attempts: dict[str, int],
) -> list[dict[str, Any]]:
    statuses: dict[str, str] = {
        node_id: "PENDING"
        for node_id, node in contract.nodes.items()
        if node.executor != "human" and node_id not in contract.terminal_nodes
    }
    last_seen: dict[str, str] = {}
    for event in events:
        name = str(event.get("event") or "")
        node_id = event.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            selected_agent = event.get("selected_agent")
            node_id = selected_agent if isinstance(selected_agent, str) else None
        if not node_id or node_id == "human":
            continue
        last_seen[node_id] = str(event.get("ts") or "")
        if name in {"node_started", "step_started"}:
            statuses[node_id] = "RUNNING"
        elif name in {"virtual_node_completed", "node_completed"}:
            statuses[node_id] = "COMPLETED" if event.get("ok", True) is True else "BLOCKED"
        elif name == "step_completed":
            statuses[node_id] = "COMPLETED" if event.get("ok") is True else "BLOCKED"
        elif name in {"node_attempt_failed", "step_blocked", "loop_blocked"}:
            statuses[node_id] = "BLOCKED"
    progress: list[dict[str, Any]] = []
    for node_id in sorted(statuses):
        node = contract.nodes.get(node_id)
        progress.append(
            {
                "node_id": node_id,
                "agent": node.agent if node else node_id,
                "status": statuses[node_id],
                "attempt": node_attempts.get(node_id, 0),
                "last_event_at": last_seen.get(node_id),
            }
        )
    return progress


def _dag_error(
    *,
    contract: ProjectDagContract,
    receipt_dir: Path,
    scheduler: str,
    status: str,
    verdict: str,
    alerts: list[dict[str, Any]],
    errors: list[str],
    node_attempts: dict[str, int],
) -> dict[str, Any] | None:
    if status == "PASS":
        return None
    primary = alerts[0] if alerts else {}
    evidence = primary.get("evidence") if isinstance(primary.get("evidence"), dict) else {}
    failure_code = str(primary.get("code") or verdict or "dag_blocked")
    failed_node = _dag_error_node_id(contract, evidence)
    failed_agent = contract.nodes[failed_node].agent if failed_node in contract.nodes else None
    action = _dag_error_recommended_action(failure_code)
    payload: dict[str, Any] = {
        "schema": DAG_ERROR_SCHEMA,
        "status": "BLOCKED",
        "severity": str(primary.get("severity") or "BLOCK"),
        "failure_code": failure_code,
        "verdict": verdict,
        "message": str(primary.get("message") or "DAG execution blocked."),
        "dag_id": contract.dag_id,
        "scheduler": scheduler,
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "receipt_path": str(receipt_dir / "dag-receipt.json"),
        "run_dir": str(receipt_dir),
        "recommended_action": action,
        "evidence": {
            "primary_alert": primary,
            "alert_count": len(alerts),
            "alert_codes": [
                item.get("code") for item in alerts if isinstance(item, dict) and item.get("code")
            ],
            "errors": errors,
        },
        "proof_scope": {
            "proves": [
                "Tau detected a blocked DAG execution.",
                "Tau packaged the primary failure as a project-agent course-correction payload.",
                "No DAG route, goal, target, or handoff was mutated by this error contract.",
            ],
            "does_not_prove": [
                "The recommended action has been executed.",
                "The project agent accepted or applied the course correction.",
                "Provider/model semantic quality.",
            ],
        },
    }
    if failed_node is not None:
        payload["failed_node"] = failed_node
        payload["attempts"] = node_attempts.get(failed_node, 0)
        payload["max_attempts"] = contract.nodes[failed_node].max_attempts
    if failed_agent is not None:
        payload["failed_agent"] = failed_agent
    return payload


def _dag_error_node_id(contract: ProjectDagContract, evidence: dict[str, Any]) -> str | None:
    node_id = evidence.get("node_id")
    if isinstance(node_id, str) and node_id in contract.nodes:
        return node_id
    from_node = evidence.get("from_node")
    if isinstance(from_node, str) and from_node in contract.nodes:
        return from_node
    selected_agent = evidence.get("selected_agent")
    if isinstance(selected_agent, str):
        node = _node_for_agent(contract, selected_agent)
        if node is not None:
            return node.node_id
    node_ids = evidence.get("node_ids")
    if isinstance(node_ids, list):
        for item in node_ids:
            if isinstance(item, str) and item in contract.nodes:
                return item
    return None


def _dag_error_recommended_action(failure_code: str) -> dict[str, str]:
    normalized = failure_code.lower()
    route_failures = {
        "unsupported_ready_queue_condition",
        "invalid_route_condition",
        "invalid_route_mode",
        "route_mode_without_conditional_edges",
        "mixed_conditional_unconditional_routes",
        "conditional_route_source_requires_response",
        "conditional_target_multiple_predecessors",
        "typed_route_requires_bounded_ready_queue",
        "route_source_result_missing",
        "route_field_missing",
        "route_field_type_invalid",
        "route_comparison_type_mismatch",
        "route_no_match",
        "route_ambiguous_exclusive",
        "route_all_matching_incomplete",
        "route_decision_receipt_write_failed",
        "route_activation_invariant_violation",
    }
    if normalized in route_failures:
        return {
            "type": "repair_dag_route_contract",
            "next_agent": "goal-guardian",
            "reason": "Repair the typed route contract or source result before continuing.",
        }
    if normalized in {
        "reviewer_goal_hash_mismatch",
        "target_changed",
        "goal_changed",
        "goal_hash_mismatch",
        "unexpected_edge",
        "unexpected_node",
        "entry_node_mismatch",
        "cycle_detected",
    }:
        return {
            "type": "reroute",
            "next_agent": "goal-guardian",
            "reason": "Reconcile DAG route, goal, or target drift before continuing.",
        }
    if normalized in {
        "evidence_manifest_invalid",
        "evidence_manifest_missing_required_evidence",
    }:
        return {
            "type": "repair_evidence_manifest",
            "next_agent": "reviewer",
            "reason": (
                "Repair or regenerate the typed evidence manifest before normal continuation."
            ),
        }
    if normalized == "memory_route_not_dispatchable":
        return {
            "type": "request_memory_clarification",
            "next_agent": "human",
            "reason": (
                "Memory routed to clarification or deflection; resolve that "
                "route before DAG dispatch."
            ),
        }
    if normalized in {
        "invalid_memory_intent_schema",
        "memory_first_required",
        "missing_memory_route",
        "memory_intent_low_confidence",
        "memory_intent_goal_hash_mismatch",
        "memory_intent_target_mismatch",
        "inline_memory_evidence_rejected",
        "memory_intent_unreadable",
    }:
        return {
            "type": "repair_memory_intent",
            "next_agent": "goal-guardian",
            "reason": "Repair or regenerate the Memory intent gate before DAG dispatch.",
        }
    if normalized in {
        "missing_evidence_case",
        "invalid_evidence_case_schema",
        "missing_evidence_case_id",
        "missing_evidence_case_hash",
        "evidence_case_goal_hash_mismatch",
        "evidence_case_target_mismatch",
        "missing_evidence_case_data_boundary_hash",
        "missing_evidence_case_policy_hash",
        "invalid_evidence_case_support_artifacts",
        "memory_intent_gate_not_passed",
        "evidence_case_unreadable",
    }:
        return {
            "type": "repair_evidence_case",
            "next_agent": "reviewer",
            "reason": "Repair or regenerate the evidence case before DAG dispatch.",
        }
    if normalized in {
        "provider_policy_missing",
        "model_unspecified",
        "missing_prompt_contract",
    }:
        return {
            "type": "repair_provider_policy",
            "next_agent": "goal-guardian",
            "reason": (
                "Add explicit model_policy, prompt_contract, provider/auth/model route, "
                "and provider-route evidence before dispatch."
            ),
        }
    if normalized == "provider_auth_required":
        return {
            "type": "repair_provider_auth",
            "next_agent": "goal-guardian",
            "reason": "Refresh provider OAuth/readiness before retrying the DAG node.",
        }
    if normalized in {
        "missing_required_evidence",
        "missing_reviewer_verdict",
        "reviewer_target_mismatch",
        "missing_terminal_route",
        "pointless_unit_test_drift",
    }:
        return {
            "type": "reroute",
            "next_agent": "reviewer",
            "reason": "Inspect missing or inconsistent evidence before normal continuation.",
        }
    if normalized == "brave_search_required_after_two_attempts":
        return {
            "type": "run_brave_search_then_retry",
            "next_agent": "goal-guardian",
            "reason": "Require $brave-search research before another attempt.",
        }
    if normalized in {
        "command_timeout",
        "invalid_command_json",
        "command_failed",
        "max_attempts_exceeded",
        "missing_node_response",
        "missing_dispatch",
        "ready_queue_stalled",
        "command_loop_blocked",
    }:
        return {
            "type": "repair_then_retry_or_reroute",
            "next_agent": "goal-guardian",
            "reason": "Repair the node command or subagent response contract before retrying.",
        }
    if normalized == "command_policy_rejected":
        return {
            "type": "repair_command_policy",
            "next_agent": "goal-guardian",
            "reason": "Repair the command spec or trust policy before retrying the DAG.",
        }
    if normalized in {
        "missing_policy_profile",
        "invalid_policy_profile_schema",
        "unsupported_default_decision",
        "invalid_policy_profile",
        "missing_data_boundary",
        "invalid_data_boundary_schema",
        "missing_classification",
        "invalid_data_boundary",
        "classified_not_allowed",
        "external_provider_denied",
        "external_research_denied",
        "public_repo_denied",
        "memory_write_requires_approval",
    }:
        return {
            "type": "repair_then_retry_or_reroute",
            "next_agent": "goal-guardian",
            "reason": "Repair zero-trust policy/data-boundary gates before DAG dispatch.",
        }
    if normalized in {
        "missing_memory_intent",
        "invalid_memory_intent_schema",
        "memory_first_not_true",
        "intent_not_planner_only",
        "intent_confidence_missing",
        "intent_confidence_too_low",
        "intent_clarify_required",
        "intent_deflected",
        "intent_contains_inline_evidence",
        "missing_evidence_case",
        "invalid_evidence_case_schema",
        "evidence_case_hash_missing",
        "evidence_case_boundary_mismatch",
        "evidence_case_policy_mismatch",
        "invalid_memory_intent",
        "invalid_evidence_case",
    }:
        return {
            "type": "repair_memory_evidence_gate",
            "next_agent": "goal-guardian",
            "reason": (
                "Repair Graph Memory intent and create-evidence-case artifacts before "
                "zero-trust DAG dispatch."
            ),
        }
    if normalized in {
        "missing_itar_access_preflight",
        "itar_access_preflight_unreadable",
        "itar_access_preflight_schema_mismatch",
        "itar_access_preflight_not_passed",
        "itar_access_preflight_goal_hash_mismatch",
    }:
        return {
            "type": "repair_actor_access_gate",
            "next_agent": "goal-guardian",
            "reason": (
                "Run or repair the ITAR actor/access preflight receipt before DAG dispatch."
            ),
        }
    if normalized in {
        "missing_research_query_safety",
        "research_query_safety_unreadable",
        "research_query_safety_schema_mismatch",
        "research_query_safety_not_passed",
        "research_query_safety_goal_hash_mismatch",
    }:
        return {
            "type": "repair_research_query_gate",
            "next_agent": "research-auditor",
            "reason": ("Run or repair the research-query safety receipt before external research."),
        }
    if normalized in {
        "missing_sandbox_run",
        "sandbox_run_unreadable",
        "sandbox_run_schema_mismatch",
        "sandbox_run_not_passed",
        "sandbox_run_goal_hash_mismatch",
    }:
        return {
            "type": "repair_sandbox_gate",
            "next_agent": "goal-guardian",
            "reason": "Run or repair the sandbox policy receipt before dispatch.",
        }
    if normalized in {
        "missing_compliance_package_validation",
        "compliance_package_validation_unreadable",
        "compliance_package_validation_schema_mismatch",
        "compliance_package_validation_not_passed",
        "compliance_package_validation_goal_hash_mismatch",
        "compliance_package_not_review_ready",
    }:
        return {
            "type": "repair_review_package",
            "next_agent": "reviewer",
            "reason": (
                "Validate the compliance package as review-ready before normal continuation."
            ),
        }
    if normalized in {
        "non_local_ready_queue_node_not_allowed",
        "provider_node_not_allowed",
        "mutating_node_not_allowed",
    }:
        return {
            "type": "request_policy_gate",
            "next_agent": "goal-guardian",
            "reason": (
                "This branch requires an explicit policy or branch-lock gate before execution."
            ),
        }
    return {
        "type": "wait_for_human",
        "next_agent": "human",
        "reason": "Unhandled DAG failure requires human or orchestrator review.",
    }


def _command_spec_load_stop_reason(error: str) -> str:
    lower = error.lower()
    if any(
        marker in lower
        for marker in (
            "command policy",
            "command spec policy",
            "requires network",
            "mutates state",
            "clean worktree",
        )
    ):
        return "command_policy_rejected"
    return "node_dispatch_failed"


def _pointless_unit_test_drift_alert(
    *,
    node_id: str,
    node: ProjectDagNode,
    attempt: int,
    stop_reason: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if stop_reason not in {"command_failed", "invalid_command_json", "node_dispatch_failed"}:
        return None
    text = _result_text(result).lower()
    if not text:
        return None
    test_markers = (
        "pytest",
        "unittest",
        "test session starts",
        "collected ",
        " tests/",
        " test_",
        "ruff check",
        "mypy",
    )
    task_markers = (
        "creator_artifact",
        "reviewer_verdict",
        "tau.agent_handoff.v1",
        "wrote_receipt",
        "changed_files",
        "implementation",
        "patch",
    )
    if not any(marker in text for marker in test_markers):
        return None
    if any(marker in text for marker in task_markers):
        return None
    return _alert(
        "BLOCK",
        "pointless_unit_test_drift",
        "Node appears blocked but is producing test-only churn instead of task evidence.",
        {
            "node_id": node_id,
            "agent": node.agent,
            "attempt": attempt,
            "stop_reason": stop_reason,
            "detected_markers": [marker for marker in test_markers if marker in text],
            "required_course_correction": "stop_test_churn_report_blocker_and_replan",
        },
    )


def _write_course_correction_receipt(
    *,
    contract: ProjectDagContract,
    receipt_dir: Path,
    node_id: str,
    node: ProjectDagNode,
    attempt: int,
    code: str,
    reason: str,
    stop_reason: str,
    errors: list[str],
) -> Path:
    path = receipt_dir / "course-corrections" / f"{node_id}-attempt-{attempt:03d}-{code}.json"
    required_action = _course_correction_required_action(
        contract=contract,
        node_id=node_id,
        node=node,
        code=code,
        stop_reason=stop_reason,
        errors=errors,
    )
    blocked_report_required = {
        "required": True,
        "fields": [
            "blocker_summary",
            "attempted_fix",
            "why_test_churn_is_not_progress",
            "next_non_test_action",
            "brave_search_receipt_path",
        ],
        "reason": (
            "Blocked subagents must report the blocker and course correction "
            "instead of continuing non-essential deterministic unit tests."
        ),
    }
    payload = build_course_correction_receipt(
        trigger=code,
        dag_id=contract.dag_id,
        goal_hash=contract.goal["goal_hash"],
        target=contract.target,
        node_id=node_id,
        agent=node.agent,
        attempt=attempt,
        observed_state={
            "stop_reason": stop_reason,
            "errors": errors,
        },
        errors=errors,
        reason=reason,
        stop_reason=stop_reason,
        required_action=required_action,
        blocked_report_required=blocked_report_required,
        mocked=False,
        live=False,
        provider_live=False,
    )
    _write_json(path, payload)
    return path


def _write_provider_auth_repair_receipt(
    *,
    receipt_dir: Path,
    node_id: str,
    node: ProjectDagNode,
    attempt: int,
    response: dict[str, Any],
) -> Path:
    path = (
        receipt_dir
        / "provider-auth-repair"
        / f"{node_id}-attempt-{attempt:03d}-scillm-auth-preflight.json"
    )
    model = _provider_auth_repair_model(response) or _provider_auth_repair_model(
        _node_payload_from_response_context(response)
    )
    if not model:
        model = "gpt-5.5"
    base_url = os.environ.get("SCILLM_BASE_URL", "http://127.0.0.1:4001")
    try:
        payload = preflight_battle_scillm_auth(
            scillm_base_url=base_url,
            model=model,
            allow_repair=True,
        )
    except Exception as exc:  # pragma: no cover - defensive repair boundary.
        payload = {
            "schema": "tau.battle_scillm_auth_preflight.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": True,
            "surface": "scillm.chat_completions",
            "model": model,
            "base_url": base_url.rstrip("/"),
            "endpoint": "/v1/scillm/auth",
            "caller_skill": "tau-project-dag-provider-auth-repair",
            "repair_allowed": True,
            "repair_attempted": False,
            "repair_status": "exception",
            "errors": [str(exc)],
        }
    payload = dict(payload)
    env_refresh = _refresh_scillm_proxy_env_for_retry() if payload.get("ok") is True else None
    payload["tau_auto_repair"] = {
        "schema": "tau.provider_auth_auto_repair.v1",
        "trigger": "provider_auth_required",
        "node_id": node_id,
        "agent": node.agent,
        "attempt": attempt,
        "retry_after_pass": True,
        "env_refresh": env_refresh,
        "proof_scope": {
            "proves": [
                "Tau attempted the configured SciLLM auth preflight/repair before "
                "another DAG node retry.",
            ],
            "does_not_prove": [
                "The retried node will pass.",
                "Provider/model semantic quality.",
                "Host OAuth identity or legal authorization.",
            ],
        },
    }
    _write_json(path, payload)
    return path


def _refresh_scillm_proxy_env_for_retry() -> dict[str, Any]:
    key, source, errors = resolve_active_scillm_proxy_key()
    if not key:
        return {
            "status": "BLOCKED",
            "ok": False,
            "source": source,
            "updated_env": [],
            "errors": errors or ["active SciLLM proxy key could not be resolved"],
        }
    os.environ["SCILLM_PROXY_KEY"] = key
    os.environ["LITELLM_MASTER_KEY"] = key
    return {
        "status": "PASS",
        "ok": True,
        "source": source,
        "updated_env": ["SCILLM_PROXY_KEY", "LITELLM_MASTER_KEY"],
        "errors": [],
    }


def _provider_auth_repair_ready_for_retry(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    env_refresh = _provider_auth_repair_env_refresh(payload)
    return isinstance(env_refresh, dict) and env_refresh.get("ok") is True


def _provider_auth_repair_env_refresh(payload: dict[str, Any]) -> dict[str, Any] | None:
    auto_repair = payload.get("tau_auto_repair")
    if not isinstance(auto_repair, dict):
        return None
    env_refresh = auto_repair.get("env_refresh")
    return env_refresh if isinstance(env_refresh, dict) else None


def _provider_auth_repair_model(value: Any) -> str | None:
    if isinstance(value, dict):
        model_policy = value.get("model_policy")
        if isinstance(model_policy, dict):
            model = model_policy.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
        model = value.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        for child in value.values():
            found = _provider_auth_repair_model(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _provider_auth_repair_model(child)
            if found:
                return found
    return None


def _node_payload_from_response_context(response: dict[str, Any]) -> dict[str, Any]:
    context = response.get("context")
    if not isinstance(context, dict):
        return {}
    tau_dag_node = context.get("tau_dag_node")
    return tau_dag_node if isinstance(tau_dag_node, dict) else {}


def _course_correction_required_action(
    *,
    contract: ProjectDagContract,
    node_id: str,
    node: ProjectDagNode,
    code: str,
    stop_reason: str,
    errors: list[str],
) -> dict[str, Any]:
    if code == "provider_auth_required":
        return {
            "skill": "tau",
            "skill_reference": "$tau",
            "type": "repair_provider_auth",
            "description": (
                "Run the existing SciLLM/Codex OAuth auth preflight with stale OAuth "
                "repair enabled, then rerun provider readiness before retrying this node."
            ),
            "repair_function": "tau_coding.battle_scillm.preflight_battle_scillm_auth",
            "repair_default": (
                "attempt stale OAuth proxy recreation unless TAU_DISABLE_STALE_AUTH_REPAIR is set"
            ),
            "receipt_required": True,
            "required_receipt_schemas": [
                "tau.battle_scillm_auth_preflight.v1",
                "tau.provider_readiness_run_receipt.v1",
            ],
        }
    query = _brave_search_query(contract, node_id, node, stop_reason, errors)
    return {
        "skill": "brave-search",
        "skill_reference": "$brave-search",
        "query": query,
        "command": [
            "python",
            ".pi/skills/brave-search/brave_search.py",
            "web",
            query,
            "--count",
            "5",
        ],
        "receipt_required": True,
    }


def _brave_search_query(
    contract: ProjectDagContract,
    node_id: str,
    node: ProjectDagNode,
    stop_reason: str,
    errors: list[str],
) -> str:
    target = str(contract.target.get("target") or contract.dag_id)
    error_text = " ".join(errors)[:160]
    parts = [
        str(contract.target.get("repo") or "tau"),
        target,
        node_id,
        node.agent,
        stop_reason,
        error_text,
    ]
    return " ".join(part for part in parts if part).strip()


def _result_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in result.get("errors", []):
        chunks.append(str(item))
    dispatch = result.get("dispatch")
    if isinstance(dispatch, dict):
        for command_result in dispatch.get("command_results", []):
            if not isinstance(command_result, dict):
                continue
            for key in ("stdout", "stderr"):
                value = command_result.get(key)
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks)


def _observed_edges(
    contract: ProjectDagContract,
    loop_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for dispatch in _dispatches(loop_payload):
        from_node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        response_projection = dispatch.get("response_projection")
        if from_node is None or not isinstance(response_projection, dict):
            continue
        to_agent = response_projection.get("next_agent")
        to_node = _node_id_for_agent_or_terminal(contract, str(to_agent))
        target_node = contract.nodes.get(to_node)
        edges.append(
            {
                "from_node": from_node.node_id,
                "from_agent": from_node.agent,
                "to_node": to_node,
                "to_agent": target_node.agent if target_node else to_agent,
            }
        )
    return edges


def _node_attempts(contract: ProjectDagContract, loop_payload: dict[str, Any]) -> dict[str, int]:
    attempts: dict[str, int] = {}
    for dispatch in _dispatches(loop_payload):
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is not None:
            attempts[node.node_id] = attempts.get(node.node_id, 0) + 1
    return attempts


def _reviewer_verdicts(
    contract: ProjectDagContract,
    loop_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    for dispatch in _dispatches(loop_payload):
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is None or node.reviewer is None:
            continue
        response = _response_payload(dispatch)
        if response is not None:
            verdicts.extend(_reviewer_verdict_evidence(response))
    return verdicts


def _reviewer_verdict_evidence(response: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = _result_evidence(response)
    return [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("kind") == "reviewer_verdict"
    ]


def _missing_required_evidence(required: tuple[str, ...], response: dict[str, Any]) -> list[str]:
    if not required:
        return []
    haystack = json.dumps(_result_evidence(response), sort_keys=True)
    return [item for item in required if item not in haystack]


def _result_evidence(response: dict[str, Any]) -> list[Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        return []
    evidence = result.get("evidence")
    return evidence if isinstance(evidence, list) else []


def _response_payload(dispatch: dict[str, Any]) -> dict[str, Any] | None:
    command_results = dispatch.get("command_results")
    if not isinstance(command_results, list) or not command_results:
        return None
    first = command_results[0]
    if not isinstance(first, dict) or not isinstance(first.get("stdout"), str):
        return None
    try:
        payload = json.loads(first["stdout"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _dispatches(loop_payload: dict[str, Any]) -> list[dict[str, Any]]:
    dispatches = loop_payload.get("dispatches")
    if not isinstance(dispatches, list):
        return []
    return [item for item in dispatches if isinstance(item, dict)]


def _edge_allowed(contract: ProjectDagContract, source: str, target: str) -> bool:
    return any(edge.source == source and edge.target == target for edge in contract.edges)


def _node_for_agent(contract: ProjectDagContract, agent: str) -> ProjectDagNode | None:
    for node in contract.nodes.values():
        if node.agent == agent or node.node_id == agent:
            return node
    return None


def _node_id_for_agent_or_terminal(contract: ProjectDagContract, value: str) -> str:
    node = _node_for_agent(contract, value)
    if node is not None:
        return node.node_id
    return value


def _resolve_receipt_dir(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path | None,
) -> Path:
    if receipt_dir is not None:
        return receipt_dir.expanduser().resolve()
    raw_run_dir = contract.payload.get("run_dir")
    if isinstance(raw_run_dir, str) and raw_run_dir.strip():
        run_dir = Path(raw_run_dir)
        if not run_dir.is_absolute():
            run_dir = contract_path.parent / run_dir
        return run_dir.expanduser().resolve()
    return (contract_path.parent / f"{contract.dag_id}-run").resolve()


def _max_steps(contract: ProjectDagContract) -> int:
    raw = contract.limits.get("max_total_attempts")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return max(sum(node.max_attempts for node in contract.nodes.values()), 1)


def _blocked_verdict(alerts: list[dict[str, Any]], loop_payload: dict[str, Any]) -> str:
    if alerts:
        return str(alerts[0]["code"]).upper()
    return str(loop_payload.get("stop_reason") or "COMMAND_LOOP_BLOCKED").upper()


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


def _rewrite_alert_code(receipt: dict[str, Any], *, old: str, new: str) -> None:
    for alert in receipt.get("alerts", []):
        if isinstance(alert, dict) and alert.get("code") == old:
            alert["code"] = new
    receipt["alert_codes"] = [
        new if code == old else code for code in receipt.get("alert_codes", [])
    ]


def _required_mapping(value: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        errors.append(f"{key} must be an object")
        return {}
    return item


def _optional_context_mapping(value: object, label: str, errors: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        errors.append(f"{label} must be JSON-serializable: {exc}")
        return {}


def _json_safe_alert_value(value: object) -> object:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"type": type(value).__name__, "value": str(value)}
    return value


def _handoff_context(
    *,
    summary: str,
    artifacts: list[str],
    contract_context: dict[str, Any],
    node_context: dict[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    merged_artifacts = list(artifacts)
    for source in (contract_context, node_context):
        for key, value in source.items():
            if key == "summary":
                context["dag_context_summary"] = value
                continue
            if key == "artifacts":
                if isinstance(value, list):
                    merged_artifacts.extend(str(item) for item in value if isinstance(item, str))
                continue
            context[key] = value
    context["summary"] = summary
    context["artifacts"] = merged_artifacts
    return context


def _required_string(
    value: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    prefix: str | None = None,
) -> str:
    item = value.get(key)
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(item, str) or not item.strip():
        errors.append(f"{label} must be a non-empty string")
        return ""
    return item


def _validate_dag_identifier(value: str, label: str, errors: list[str]) -> None:
    if value and not DAG_IDENTIFIER_PATTERN.fullmatch(value):
        errors.append(
            f"{label} must match ^[A-Za-z][A-Za-z0-9_-]{{0,63}}$"
        )


def _string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{label} must be a string list")
        return []
    return list(value)


def _int_value(value: object, label: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return 0
    return value


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
