"""Unified read-only Tau run status surface."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_STATUS_SCHEMA = "tau.run_status.v1"


def build_run_status(run_dir: Path) -> dict[str, Any]:
    """Summarize known Tau run artifacts without mutating them."""

    resolved = run_dir.expanduser().resolve()
    artifacts = _artifact_paths(resolved)
    run_receipt = _read_optional_json(artifacts["run_receipt"])
    if not run_receipt:
        run_receipt = _read_optional_json(artifacts["real_world_sanity_receipt"])
    project_dag_receipt = _read_optional_json(artifacts["project_dag_receipt"])
    evidence_validation = _read_optional_json(artifacts["evidence_validation"])
    runtime_manifest = _read_optional_json(artifacts["runtime_manifest"])
    checkpoint = _read_optional_json(artifacts["checkpoint"])
    current_state = _read_optional_json(artifacts["current_state"])
    cleanup = _read_optional_json(artifacts["cleanup"])
    herdr_gc = _read_optional_json(artifacts["herdr_gc"])
    approval_gate = _read_optional_json(artifacts["approval_gate"])
    orchestration_evidence = _read_optional_json(artifacts["orchestration_evidence"])
    planner_receipt = _read_optional_json(artifacts["planner_receipt"])
    browser_cdp_proof = _read_optional_json(artifacts["browser_cdp_proof"])
    dag_stress_suite = _read_optional_json(artifacts["dag_stress_suite"])
    dag_stress_campaign = _read_optional_json(artifacts["dag_stress_campaign"])
    route_memory_candidate = _read_optional_json(artifacts["route_memory_candidate"])
    route_memory_sync = _read_optional_json(artifacts["route_memory_sync"])
    memory_readback = _read_optional_json(artifacts["memory_readback"])
    dag_expansion_validation = _read_optional_json(
        artifacts["dag_expansion_validation"]
    ) or _read_optional_json(artifacts["dag_expansion_validation_short"])
    dag_expansion_policy = _read_optional_json(
        artifacts["dag_expansion_policy"]
    ) or _read_optional_json(artifacts["dag_expansion_policy_short"])
    dag_expansion_apply = _read_optional_json(
        artifacts["dag_expansion_apply"]
    ) or _read_optional_json(artifacts["dag_expansion_apply_short"])
    github_apply_policy = _read_optional_json(artifacts["github_apply_policy"])
    github_handoff_transport = _read_optional_json(artifacts["github_handoff_transport"])
    lifecycle_states = _load_lifecycle_states(resolved, runtime_manifest, run_receipt)
    readiness_records = _load_readiness_records(resolved, runtime_manifest, run_receipt)
    events_path = _event_path(resolved, run_receipt, runtime_manifest)
    events_count = _events_count(events_path)
    detected_type = _detected_type(
        run_receipt,
        project_dag_receipt,
        evidence_validation,
        runtime_manifest,
        approval_gate=approval_gate,
        cleanup=cleanup,
        herdr_gc=herdr_gc,
        orchestration_evidence=orchestration_evidence,
        planner_receipt=planner_receipt,
        browser_cdp_proof=browser_cdp_proof,
        dag_stress_suite=dag_stress_suite,
        dag_stress_campaign=dag_stress_campaign,
        route_memory_candidate=route_memory_candidate,
        route_memory_sync=route_memory_sync,
        memory_readback=memory_readback,
        dag_expansion_validation=dag_expansion_validation,
        dag_expansion_policy=dag_expansion_policy,
        dag_expansion_apply=dag_expansion_apply,
        github_apply_policy=github_apply_policy,
        github_handoff_transport=github_handoff_transport,
    )
    status = _overall_status(
        run_receipt,
        project_dag_receipt,
        evidence_validation,
        checkpoint,
        approval_gate,
        cleanup,
        herdr_gc,
        orchestration_evidence,
        planner_receipt,
        browser_cdp_proof,
        dag_stress_suite,
        dag_stress_campaign,
        route_memory_sync,
        route_memory_candidate,
        memory_readback,
        dag_expansion_apply,
        dag_expansion_policy,
        dag_expansion_validation,
        github_apply_policy,
        github_handoff_transport,
    )
    missing = [
        name
        for name, path in artifacts.items()
        if name in {"run_receipt", "runtime_manifest"} and _missing_required_artifact(
            name,
            path,
            artifacts=artifacts,
            detected_type=detected_type,
        )
    ]
    receipt = {
        "schema": RUN_STATUS_SCHEMA,
        "ok": status not in {"BLOCKED", "FAIL", "FAILED", "MISSING"},
        "status": status,
        "mocked": False,
        "live": _live_value(
            run_receipt,
            project_dag_receipt,
            evidence_validation,
            cleanup,
            herdr_gc,
            approval_gate,
            orchestration_evidence,
            planner_receipt,
            browser_cdp_proof,
            dag_stress_suite,
            dag_stress_campaign,
            route_memory_sync,
            route_memory_candidate,
            memory_readback,
            dag_expansion_apply,
            dag_expansion_policy,
            dag_expansion_validation,
            github_apply_policy,
            github_handoff_transport,
        ),
        "run_dir": str(resolved),
        "detected_type": detected_type,
        "artifacts": {name: str(path) for name, path in artifacts.items() if path.exists()},
        "missing_required_artifacts": missing,
        "run_receipt": _receipt_summary(run_receipt),
        "project_dag": _project_dag_summary(project_dag_receipt),
        "evidence_validation": _evidence_validation_summary(evidence_validation),
        "runtime_manifest": _manifest_summary(runtime_manifest),
        "checkpoint": _checkpoint_summary(checkpoint or current_state),
        "generic_dag": _generic_dag_summary(run_receipt),
        "provider_pane": _provider_pane_summary(run_receipt, runtime_manifest),
        "provider_readiness": _provider_readiness_summary(
            run_receipt,
            runtime_manifest,
            readiness_records,
            lifecycle_states,
        ),
        "provider_dag": _provider_dag_summary(run_receipt),
        "provider_dag_planner": _provider_dag_planner_summary(planner_receipt),
        "events": {
            "path": str(events_path) if events_path else None,
            "count": events_count,
        },
        "provider_session_states": [_provider_state_summary(state) for state in lifecycle_states],
        "cleanup": _cleanup_summary(cleanup),
        "herdr_gc": _herdr_gc_summary(herdr_gc),
        "real_world_sanity": _real_world_sanity_summary(run_receipt),
        "approval_gate": _approval_summary(approval_gate),
        "orchestration_evidence": _orchestration_summary(orchestration_evidence),
        "browser_cdp_proof": _browser_cdp_proof_summary(browser_cdp_proof),
        "dag_stress": _dag_stress_summary(dag_stress_suite),
        "dag_stress_campaign": _dag_stress_campaign_summary(dag_stress_campaign),
        "route_memory": _route_memory_summary(
            route_memory_candidate,
            route_memory_sync,
            memory_readback,
        ),
        "dag_expansion": _dag_expansion_summary(
            dag_expansion_validation,
            dag_expansion_policy,
            dag_expansion_apply,
        ),
        "github_apply_policy": _github_apply_policy_summary(github_apply_policy),
        "github_handoff_transport": _github_handoff_transport_summary(github_handoff_transport),
        "proof_scope": {
            "proves": [
                "Tau can summarize known run artifacts from one run directory",
                "Tau can expose checkpoint/current-state, lifecycle, cleanup, approval, and evidence status without mutation",
                "Tau can expose project DAG dag-receipt.json failures without requiring provider runtime manifests",
            ],
            "does_not_prove": [
                "new provider execution",
                "Herdr workspace cleanup unless a cleanup receipt is present",
                "GitHub ticket closure",
                "production repository mutation",
                "production browser/chat UI rendering",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    return receipt


def _artifact_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "run_receipt": run_dir / "run-receipt.json",
        "project_dag_receipt": run_dir / "dag-receipt.json",
        "evidence_validation": run_dir / "evidence-validation-receipt.json",
        "runtime_manifest": run_dir / "runtime-manifest.json",
        "checkpoint": run_dir / "checkpoint.json",
        "current_state": run_dir / "current-state.json",
        "cleanup": run_dir / "herdr-cleanup-receipt.json",
        "herdr_gc": run_dir / "herdr-gc-receipt.json",
        "approval_gate": run_dir / "approval-gate-receipt.json",
        "orchestration_evidence": run_dir / "orchestration-evidence-receipt.json",
        "planner_receipt": run_dir / "planner-receipt.json",
        "real_world_sanity_receipt": run_dir / "real-world-sanity-receipt.json",
        "browser_cdp_proof": run_dir / "browser-cdp-proof" / "browser-cdp-proof-receipt.json",
        "dag_stress_suite": run_dir / "suite-receipt.json",
        "dag_stress_campaign": run_dir / "campaign-receipt.json",
        "route_memory_candidate": run_dir / "dag-route-memory-candidate-receipt.json",
        "route_memory_sync": run_dir / "dag-route-memory-sync-receipt.json",
        "memory_readback": run_dir / "memory-readback.json",
        "dag_expansion_validation": run_dir / "dag-expansion-validation-receipt.json",
        "dag_expansion_validation_short": run_dir / "validation-receipt.json",
        "dag_expansion_policy": run_dir / "dag-expansion-policy-receipt.json",
        "dag_expansion_policy_short": run_dir / "policy-receipt.json",
        "dag_expansion_apply": run_dir / "dag-expansion-apply-receipt.json",
        "dag_expansion_apply_short": run_dir / "apply-receipt.json",
        "github_apply_policy": run_dir / "github-apply-policy-receipt.json",
        "github_handoff_transport": run_dir / "github-transport-missing-policy-receipt.json",
    }


def _missing_required_artifact(
    name: str,
    path: Path,
    *,
    artifacts: dict[str, Path],
    detected_type: str,
) -> bool:
    if path.exists():
        return False
    if name == "run_receipt" and artifacts["real_world_sanity_receipt"].exists():
        return False
    if name == "run_receipt" and artifacts["project_dag_receipt"].exists():
        return False
    if name == "run_receipt" and detected_type in {
        "approval_gate",
        "dag_stress",
        "dag_stress_campaign",
        "herdr_gc",
        "route_memory",
        "dag_expansion",
        "herdr_cleanup",
        "orchestration_evidence",
        "provider_dag_planner",
        "real_world_sanity",
        "browser_cdp_proof",
        "github_apply_policy",
        "github_handoff_transport",
        "project_dag",
    }:
        return False
    if name == "runtime_manifest" and detected_type in {
        "approval_gate",
        "dag_stress",
        "dag_stress_campaign",
        "herdr_gc",
        "route_memory",
        "dag_expansion",
        "generic_dag",
        "herdr_cleanup",
        "orchestration_evidence",
        "provider_dag_planner",
        "real_world_sanity",
        "browser_cdp_proof",
        "github_apply_policy",
        "github_handoff_transport",
        "project_dag",
    }:
        return False
    return True


def _detected_type(
    run_receipt: dict[str, Any],
    project_dag_receipt: dict[str, Any],
    evidence_validation: dict[str, Any],
    runtime_manifest: dict[str, Any],
    *,
    approval_gate: dict[str, Any],
    cleanup: dict[str, Any],
    herdr_gc: dict[str, Any],
    orchestration_evidence: dict[str, Any],
    planner_receipt: dict[str, Any],
    browser_cdp_proof: dict[str, Any],
    dag_stress_suite: dict[str, Any],
    dag_stress_campaign: dict[str, Any],
    route_memory_candidate: dict[str, Any],
    route_memory_sync: dict[str, Any],
    memory_readback: dict[str, Any],
    dag_expansion_validation: dict[str, Any],
    dag_expansion_policy: dict[str, Any],
    dag_expansion_apply: dict[str, Any],
    github_apply_policy: dict[str, Any],
    github_handoff_transport: dict[str, Any],
) -> str:
    schema = str(
        run_receipt.get("schema")
        or project_dag_receipt.get("schema")
        or evidence_validation.get("schema")
        or herdr_gc.get("schema")
        or cleanup.get("schema")
        or route_memory_sync.get("schema")
        or route_memory_candidate.get("schema")
        or memory_readback.get("schema")
        or dag_expansion_apply.get("schema")
        or dag_expansion_policy.get("schema")
        or dag_expansion_validation.get("schema")
        or approval_gate.get("schema")
        or orchestration_evidence.get("schema")
        or planner_receipt.get("schema")
        or browser_cdp_proof.get("schema")
        or dag_stress_suite.get("schema")
        or dag_stress_campaign.get("schema")
        or github_apply_policy.get("schema")
        or github_handoff_transport.get("schema")
        or runtime_manifest.get("schema")
        or ""
    )
    if schema == "tau.generic_dag_run_receipt.v1":
        return "generic_dag"
    if schema in {"tau.dag_receipt.v1", "tau.evidence_validation_receipt.v1"}:
        return "project_dag"
    if schema == "tau.provider_pane_run_receipt.v1":
        return "provider_pane"
    if schema == "tau.provider_readiness_run_receipt.v1":
        return "provider_readiness"
    if schema == "tau.dag_run_receipt.v1":
        return "provider_dag"
    if schema == "tau.real_world_sanity_suite_receipt.v1":
        return "real_world_sanity"
    if schema == "tau.approval_gate_receipt.v1":
        return "approval_gate"
    if schema == "tau.herdr_cleanup_receipt.v1":
        return "herdr_cleanup"
    if schema == "tau.herdr_gc_receipt.v1":
        return "herdr_gc"
    if schema == "tau.orchestration_evidence_receipt.v1":
        return "orchestration_evidence"
    if schema == "tau.dag_planner_receipt.v1":
        return "provider_dag_planner"
    if schema == "tau.browser_cdp_proof.v1":
        return "browser_cdp_proof"
    if schema == "tau.dag_stress_suite_receipt.v1":
        return "dag_stress"
    if schema == "tau.dag_stress_campaign_receipt.v1":
        return "dag_stress_campaign"
    if schema in {
        "tau.dag_route_memory_candidate_receipt.v1",
        "tau.dag_route_memory_sync_receipt.v1",
        "tau.memory_readback_proof.v1",
    }:
        return "route_memory"
    if schema in {
        "tau.dag_expansion_validation_receipt.v1",
        "tau.dag_expansion_policy_receipt.v1",
        "tau.dag_expansion_apply_receipt.v1",
    }:
        return "dag_expansion"
    if schema == "tau.github_apply_policy_receipt.v1":
        return "github_apply_policy"
    if schema == "tau.github_handoff_transport_receipt.v1":
        return "github_handoff_transport"
    if schema:
        return schema.removeprefix("tau.").removesuffix(".v1")
    return "unknown"


def _overall_status(
    run_receipt: dict[str, Any],
    project_dag_receipt: dict[str, Any],
    evidence_validation: dict[str, Any],
    checkpoint: dict[str, Any],
    approval_gate: dict[str, Any],
    cleanup: dict[str, Any],
    herdr_gc: dict[str, Any],
    orchestration_evidence: dict[str, Any],
    planner_receipt: dict[str, Any],
    browser_cdp_proof: dict[str, Any],
    dag_stress_suite: dict[str, Any],
    dag_stress_campaign: dict[str, Any],
    route_memory_sync: dict[str, Any],
    route_memory_candidate: dict[str, Any],
    memory_readback: dict[str, Any],
    dag_expansion_apply: dict[str, Any],
    dag_expansion_policy: dict[str, Any],
    dag_expansion_validation: dict[str, Any],
    github_apply_policy: dict[str, Any],
    github_handoff_transport: dict[str, Any],
) -> str:
    for payload in (
        run_receipt,
        project_dag_receipt,
        evidence_validation,
        checkpoint,
        herdr_gc,
        cleanup,
        route_memory_sync,
        route_memory_candidate,
        memory_readback,
        dag_expansion_apply,
        dag_expansion_policy,
        dag_expansion_validation,
        approval_gate,
        orchestration_evidence,
        planner_receipt,
        browser_cdp_proof,
        dag_stress_suite,
        dag_stress_campaign,
        github_apply_policy,
        github_handoff_transport,
    ):
        status = payload.get("status")
        if isinstance(status, str) and status:
            return status
    return "MISSING"


def _live_value(*payloads: dict[str, Any]) -> Any:
    values = [payload.get("live") for payload in payloads if "live" in payload]
    if any(value == "mixed" for value in values):
        return "mixed"
    if any(value is True for value in values):
        return True
    if values:
        return False
    return False


def _receipt_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    keys = (
        "schema",
        "ok",
        "status",
        "verdict",
        "mocked",
        "live",
        "run_id",
        "attempt_count",
        "max_attempts",
        "node_count",
        "completed_node_count",
        "all_provider_structured_ready",
        "check_count",
        "failed_check_count",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _manifest_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "run_id": payload.get("run_id"),
        "events_jsonl": payload.get("events_jsonl"),
        "provider_session_state_count": _count(payload.get("provider_session_states")),
        "readiness_record_count": _count(payload.get("readiness_records")),
    }


def _checkpoint_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "verdict": payload.get("verdict"),
        "active_node_id": payload.get("active_node_id"),
        "completed_nodes": payload.get("completed_nodes"),
        "ready_nodes": payload.get("ready_nodes"),
        "blocked_nodes": payload.get("blocked_nodes"),
    }


def _generic_dag_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.generic_dag_run_receipt.v1":
        return None
    raw_nodes = payload.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "verdict": payload.get("verdict"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "spec_path": payload.get("spec_path"),
        "resume_requested": payload.get("resume_requested"),
        "resume_source": payload.get("resume_source"),
        "node_count": payload.get("node_count"),
        "completed_node_count": payload.get("completed_node_count"),
        "resumed_node_count": len(
            [node for node in nodes if isinstance(node, dict) and node.get("resumed") is True]
        ),
        "dispatched_node_count": len(
            [
                node
                for node in nodes
                if isinstance(node, dict) and int(node.get("attempt_count") or 0) > 0
            ]
        ),
        "blocked_node_count": len(
            [
                node
                for node in nodes
                if isinstance(node, dict) and str(node.get("status") or "").upper() == "BLOCKED"
            ]
        ),
        "nodes": [
            _generic_dag_node_summary(node)
            for node in nodes
            if isinstance(node, dict)
        ],
    }


def _project_dag_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.dag_receipt.v1":
        return None
    node_attempts = payload.get("node_attempts")
    observed_edges = payload.get("observed_edges")
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "verdict": payload.get("verdict"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "dag_id": payload.get("dag_id"),
        "goal_hash": payload.get("goal_hash"),
        "target": payload.get("target"),
        "entry_node": payload.get("entry_node"),
        "terminal_nodes": payload.get("terminal_nodes"),
        "observed_edge_count": _count(observed_edges),
        "observed_edges": observed_edges if isinstance(observed_edges, list) else [],
        "node_attempt_count": _count(node_attempts),
        "node_attempts": node_attempts if isinstance(node_attempts, dict) else {},
        "missing_required_evidence": payload.get("missing_required_evidence"),
        "unexpected_edges": payload.get("unexpected_edges"),
        "course_correction_path": payload.get("course_correction_path"),
        "error_count": _count(payload.get("errors")),
        "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
    }


def _evidence_validation_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.evidence_validation_receipt.v1":
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "dag_id": payload.get("dag_id"),
        "manifest_path": payload.get("manifest_path"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "item_count": payload.get("item_count"),
        "valid_item_count": payload.get("valid_item_count"),
        "invalid_item_count": payload.get("invalid_item_count"),
        "error_count": _count(payload.get("errors")),
        "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
    }


def _generic_dag_node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "role": node.get("role"),
        "status": node.get("status"),
        "verdict": node.get("verdict"),
        "attempt_count": node.get("attempt_count"),
        "resumed": node.get("resumed"),
        "live": node.get("live"),
        "provider_live": node.get("provider_live"),
        "provider_status": node.get("provider_status"),
        "provider_verdict": node.get("provider_verdict"),
        "started_at": node.get("started_at"),
        "finished_at": node.get("finished_at"),
        "duration_seconds": node.get("duration_seconds"),
        "receipt_path": node.get("receipt_path"),
        "work_order_path": node.get("work_order_path"),
        "work_order_sha256": node.get("work_order_sha256"),
        "artifact_count": _count(node.get("artifacts")),
        "artifacts": _artifact_summary_map(node.get("artifacts")),
        "error_count": _count(node.get("errors")),
        "errors": node.get("errors") if isinstance(node.get("errors"), list) else [],
    }


def _provider_dag_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.dag_run_receipt.v1":
        return None
    attempts = payload.get("attempts")
    attempt_records = attempts if isinstance(attempts, list) else []
    provider_sessions = payload.get("provider_sessions")
    visible_subagents = payload.get("visible_subagents")
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "verdict": payload.get("verdict"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "run_id": payload.get("run_id"),
        "scratch_worktree": payload.get("scratch_worktree"),
        "attempt_count": payload.get("attempt_count"),
        "max_attempts": payload.get("max_attempts"),
        "provider_session_count": _count(provider_sessions),
        "visible_subagent_count": _count(visible_subagents),
        "visible_subagents": _role_summary_map(visible_subagents),
        "provider_sessions": _role_summary_map(provider_sessions),
        "attempts": [
            _provider_dag_attempt_summary(attempt)
            for attempt in attempt_records
            if isinstance(attempt, dict)
        ],
        "herdr_cleanup_receipt": payload.get("herdr_cleanup_receipt"),
        "herdr_cleanup": _embedded_cleanup_summary(payload.get("herdr_cleanup")),
        "orchestration_evidence_receipt": payload.get("orchestration_evidence_receipt"),
        "orchestration_evidence": _embedded_orchestration_summary(
            payload.get("orchestration_evidence")
        ),
    }


def _provider_pane_summary(
    run_receipt: dict[str, Any],
    runtime_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    if run_receipt.get("schema") != "tau.provider_pane_run_receipt.v1":
        return None
    providers_raw = runtime_manifest.get("providers")
    providers = providers_raw if isinstance(providers_raw, list) else []
    ready_prompt_count = len(
        [
            provider
            for provider in providers
            if isinstance(provider, dict) and provider.get("ready_prompt_observed") is True
        ]
    )
    return {
        "schema": run_receipt.get("schema"),
        "status": run_receipt.get("status"),
        "ok": run_receipt.get("ok"),
        "mocked": run_receipt.get("mocked"),
        "live": run_receipt.get("live"),
        "run_id": runtime_manifest.get("run_id") or run_receipt.get("run_id"),
        "provider_count": len([provider for provider in providers if isinstance(provider, dict)]),
        "ready_prompt_observed_count": ready_prompt_count,
        "visible_prompt_is_gate": True,
        "workstation_manifest": runtime_manifest.get("workstation_manifest"),
        "inspect_path": runtime_manifest.get("inspect_path"),
        "providers": [
            _provider_pane_record_summary(provider)
            for provider in providers
            if isinstance(provider, dict)
        ],
    }


def _provider_pane_record_summary(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": provider.get("provider_id"),
        "role": provider.get("role"),
        "pane_id": provider.get("pane_id"),
        "terminal_id": provider.get("terminal_id"),
        "work_order_path": provider.get("work_order_path"),
        "ready_prompt_observed": provider.get("ready_prompt_observed"),
        "readiness_actions": provider.get("readiness_actions"),
        "visible_log": provider.get("visible_log"),
        "read_returncode": provider.get("read_returncode"),
    }


def _provider_readiness_summary(
    run_receipt: dict[str, Any],
    runtime_manifest: dict[str, Any],
    readiness_records: list[dict[str, Any]],
    lifecycle_states: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if run_receipt.get("schema") != "tau.provider_readiness_run_receipt.v1":
        return None
    state_values = [
        str(record.get("state") or "unknown")
        for record in readiness_records
        if isinstance(record, dict)
    ]
    if not state_values:
        state_values = [
            str(record.get("state") or "unknown")
            for record in lifecycle_states
            if isinstance(record, dict)
        ]
    return {
        "schema": run_receipt.get("schema"),
        "status": run_receipt.get("status"),
        "ok": run_receipt.get("ok"),
        "mocked": run_receipt.get("mocked"),
        "live": run_receipt.get("live"),
        "provider_live": run_receipt.get("provider_live"),
        "run_id": runtime_manifest.get("run_id") or run_receipt.get("run_id"),
        "all_provider_structured_ready": run_receipt.get("all_provider_structured_ready"),
        "readiness_record_count": len(readiness_records),
        "provider_session_state_count": len(lifecycle_states),
        "ready_count": len(
            [
                record
                for record in readiness_records
                if isinstance(record, dict) and record.get("ready") is True
            ]
        ),
        "state_counts": _value_counts(state_values),
        "workstation_manifest": runtime_manifest.get("workstation_manifest"),
        "inspect_path": runtime_manifest.get("inspect_path"),
        "readiness": [
            _provider_readiness_record_summary(record)
            for record in readiness_records
            if isinstance(record, dict)
        ],
    }


def _provider_readiness_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    diagnostics = record.get("diagnostics") if isinstance(record.get("diagnostics"), dict) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {
        "provider_id": record.get("provider_id"),
        "state": record.get("state"),
        "ready": record.get("ready"),
        "source": record.get("source"),
        "workspace_id": record.get("workspace_id"),
        "pane_id": record.get("pane_id"),
        "terminal_id": record.get("terminal_id"),
        "visible_prompt_observed": diagnostics.get("visible_prompt_observed"),
        "visible_prompt_is_gate": diagnostics.get("visible_prompt_is_gate"),
        "provider_readiness_path": evidence.get("provider_readiness_path"),
        "provider_readiness_sha256": record.get("_source_sha256")
        or _path_sha256(evidence.get("provider_readiness_path")),
        "provider_session_state_path": evidence.get("provider_session_state_path"),
        "provider_session_state_sha256": _path_sha256(evidence.get("provider_session_state_path")),
    }


def _provider_dag_planner_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.dag_planner_receipt.v1":
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "run_id": payload.get("run_id"),
        "repo": payload.get("repo"),
        "dag_spec": payload.get("dag_spec"),
        "events_jsonl": payload.get("events_jsonl"),
        "scratch_worktree": payload.get("scratch_worktree"),
        "target_file": payload.get("target_file"),
        "max_attempts": payload.get("max_attempts"),
        "proof_controls": payload.get("proof_controls"),
    }


def _provider_dag_attempt_summary(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt": attempt.get("attempt"),
        "coder_status": attempt.get("coder_status"),
        "coder_verdict": attempt.get("coder_verdict"),
        "reviewer_status": attempt.get("reviewer_status"),
        "reviewer_verdict": attempt.get("reviewer_verdict"),
        "errors": attempt.get("errors"),
    }


def _role_summary_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, raw in value.items():
        if not isinstance(raw, dict):
            continue
        result[str(key)] = {
            "role": raw.get("role"),
            "provider_id": raw.get("provider_id"),
            "workspace_id": raw.get("workspace_id"),
            "pane_id": raw.get("pane_id"),
            "terminal_id": raw.get("terminal_id"),
            "visible": raw.get("visible"),
            "ready": raw.get("ready"),
            "state": raw.get("state"),
        }
    return result


def _artifact_summary_map(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    artifacts: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        path = item.get("path")
        if isinstance(kind, str) and kind and isinstance(path, str) and path:
            artifacts[kind] = path
    return artifacts


def _embedded_cleanup_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    applied_action_count = value.get("applied_action_count")
    if applied_action_count is None:
        applied_action_count = _count(value.get("applied_actions"))
    post_verified_absent_count = value.get("post_verified_absent_count")
    if post_verified_absent_count is None:
        post_verified_absent_count = _post_verified_absent_count(value.get("applied_actions"))
    return {
        "status": value.get("status"),
        "mocked": value.get("mocked"),
        "live": value.get("live"),
        "mode": value.get("mode"),
        "ok": value.get("ok"),
        "resource_count": value.get("resource_count"),
        "candidate_count": value.get("candidate_count"),
        "applied_action_count": applied_action_count,
        "post_verified_absent_count": post_verified_absent_count,
    }


def _embedded_orchestration_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": value.get("status"),
        "mocked": value.get("mocked"),
        "live": value.get("live"),
        "provider_live": value.get("provider_live"),
        "feature_counts": value.get("feature_counts"),
    }


def _provider_state_summary(payload: dict[str, Any]) -> dict[str, Any]:
    process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
    interstitial = (
        payload.get("interstitial") if isinstance(payload.get("interstitial"), dict) else {}
    )
    provider_api = (
        payload.get("provider_api") if isinstance(payload.get("provider_api"), dict) else {}
    )
    return {
        "schema": payload.get("schema"),
        "provider_id": payload.get("provider_id"),
        "workspace_id": payload.get("workspace_id"),
        "pane_id": payload.get("pane_id"),
        "terminal_id": payload.get("terminal_id"),
        "state": payload.get("state"),
        "ready": payload.get("ready"),
        "source": payload.get("source"),
        "observed_at": payload.get("observed_at"),
        "process_alive": process.get("alive"),
        "foreground_command": process.get("command"),
        "auth_status": auth.get("status"),
        "interstitial_present": interstitial.get("present"),
        "interstitial_kind": interstitial.get("kind"),
        "provider_api_available": provider_api.get("available"),
        "visible_log_path": evidence.get("visible_log_path"),
        "provider_readiness_path": evidence.get("provider_readiness_path"),
        "provider_readiness_sha256": _path_sha256(evidence.get("provider_readiness_path")),
        "provider_session_state_path": payload.get("_source_path"),
        "provider_session_state_sha256": payload.get("_source_sha256"),
        "provider_event_log_path": evidence.get("provider_event_log_path"),
    }


def _cleanup_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "mode": payload.get("mode"),
        "runtime_manifest": payload.get("runtime_manifest"),
        "runtime_manifest_sha256": payload.get("runtime_manifest_sha256"),
        "resource_count": payload.get("resource_count"),
        "candidate_count": payload.get("candidate_count"),
        "applied_action_count": _count(payload.get("applied_actions")),
        "post_verified_absent_count": _post_verified_absent_count(payload.get("applied_actions")),
    }


def _herdr_gc_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.herdr_gc_receipt.v1":
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "mode": payload.get("mode"),
        "run_dir": payload.get("run_dir"),
        "herdr_bin": payload.get("herdr_bin"),
        "approval_required": payload.get("approval_required"),
        "approval_receipt": payload.get("approval_receipt"),
        "approval_receipt_sha256": payload.get("approval_receipt_sha256"),
        "workspace_count": payload.get("workspace_count"),
        "candidate_count": payload.get("candidate_count"),
        "skipped_count": payload.get("skipped_count"),
        "applied_action_count": payload.get("applied_action_count"),
        "post_verified_absent_count": payload.get("post_verified_absent_count"),
        "command_result_count": _count(payload.get("command_results")),
        "alerts": payload.get("alerts"),
    }


def _post_verified_absent_count(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    return sum(1 for item in value if isinstance(item, dict) and item.get("post_verified_absent") is True)


def _real_world_sanity_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.real_world_sanity_suite_receipt.v1":
        return None
    checks = payload.get("checks")
    check_records = checks if isinstance(checks, list) else []
    post_cleanup_records = [
        check.get("post_cleanup")
        for check in check_records
        if isinstance(check, dict) and isinstance(check.get("post_cleanup"), dict)
    ]
    live_cleanup_records = [
        cleanup
        for cleanup in post_cleanup_records
        if isinstance(cleanup, dict) and cleanup.get("live") is True
    ]
    failed_checks = [
        check.get("check_id")
        for check in check_records
        if isinstance(check, dict) and check.get("status") != "PASS"
    ]
    receipt_summaries = [
        check.get("receipt_summary")
        for check in check_records
        if isinstance(check, dict) and isinstance(check.get("receipt_summary"), dict)
    ]
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "check_count": payload.get("check_count"),
        "failed_check_count": payload.get("failed_check_count"),
        "completed_at": payload.get("completed_at"),
        "checks": [
            _real_world_sanity_check_summary(check)
            for check in check_records
            if isinstance(check, dict)
        ],
        "post_cleanup_count": len(post_cleanup_records),
        "live_post_cleanup_count": len(live_cleanup_records),
        "failed_checks": [check_id for check_id in failed_checks if isinstance(check_id, str)],
        "generic_dag_node_totals": _real_world_sanity_generic_node_totals(receipt_summaries),
    }


def _real_world_sanity_check_summary(check: dict[str, Any]) -> dict[str, Any]:
    cleanup = check.get("post_cleanup")
    return {
        "check_id": check.get("check_id"),
        "level": check.get("level"),
        "status": check.get("status"),
        "ok": check.get("ok"),
        "mocked": check.get("mocked"),
        "live": check.get("live"),
        "provider_live": check.get("provider_live"),
        "attempt_count": check.get("attempt_count"),
        "receipt_summary": check.get("receipt_summary"),
        "post_cleanup": _post_cleanup_summary(cleanup) if isinstance(cleanup, dict) else None,
    }


def _real_world_sanity_generic_node_totals(
    receipt_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    totals = {
        "node_count": 0,
        "completed_node_count": 0,
        "resumed_node_count": 0,
        "dispatched_node_count": 0,
        "blocked_node_count": 0,
        "timed_node_count": 0,
        "node_error_count": 0,
        "checks_with_blocked_nodes": [],
        "checks_with_errors": [],
    }
    for summary in receipt_summaries:
        schema = summary.get("schema")
        if schema != "tau.generic_dag_run_receipt.v1":
            continue
        totals["node_count"] += _int_value(summary.get("node_count"))
        totals["completed_node_count"] += _int_value(summary.get("completed_node_count"))
        totals["resumed_node_count"] += _int_value(summary.get("resumed_node_count"))
        totals["dispatched_node_count"] += _int_value(summary.get("dispatched_node_count"))
        totals["blocked_node_count"] += _int_value(summary.get("blocked_node_count"))
        totals["timed_node_count"] += _int_value(summary.get("timed_node_count"))
        node_error_counts = summary.get("node_error_counts")
        if isinstance(node_error_counts, dict):
            error_total = sum(_int_value(value) for value in node_error_counts.values())
            totals["node_error_count"] += error_total
        else:
            error_total = 0
        if _int_value(summary.get("blocked_node_count")) > 0:
            totals["checks_with_blocked_nodes"].append(summary.get("spec_path"))
        if error_total > 0:
            totals["checks_with_errors"].append(summary.get("spec_path"))
    totals["checks_with_blocked_nodes"] = [
        value for value in totals["checks_with_blocked_nodes"] if isinstance(value, str)
    ]
    totals["checks_with_errors"] = [
        value for value in totals["checks_with_errors"] if isinstance(value, str)
    ]
    return totals


def _post_cleanup_summary(payload: dict[str, Any]) -> dict[str, Any]:
    receipt_summary = payload.get("receipt_summary")
    receipt_summary = receipt_summary if isinstance(receipt_summary, dict) else {}
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "mode": payload.get("mode"),
        "run_dir": payload.get("run_dir"),
        "receipt_path": payload.get("receipt_path"),
        "receipt_summary": payload.get("receipt_summary"),
        "cleanup_applied_action_count": receipt_summary.get("applied_action_count"),
        "cleanup_post_verified_absent_count": receipt_summary.get("post_verified_absent_count"),
        "errors": payload.get("errors"),
    }


def _approval_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "approved": payload.get("approved"),
        "requested_action": payload.get("requested_action"),
        "approval_packet": payload.get("approval_packet"),
        "approval_packet_sha256": payload.get("approval_packet_sha256"),
        "packet_summary": payload.get("packet_summary"),
        "errors": payload.get("errors"),
    }


def _orchestration_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "feature_counts": payload.get("feature_counts"),
    }


def _browser_cdp_proof_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.browser_cdp_proof.v1":
        return None
    screenshot = payload.get("screenshot")
    screenshot = screenshot if isinstance(screenshot, dict) else {}
    assertions = payload.get("visible_assertions")
    assertions = assertions if isinstance(assertions, dict) else {}
    artifacts = payload.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "verdict": payload.get("verdict"),
        "surface": payload.get("surface"),
        "transport": payload.get("transport"),
        "screenshot_path": screenshot.get("path"),
        "screenshot_sha256": screenshot.get("sha256"),
        "screenshot_width": screenshot.get("width"),
        "screenshot_height": screenshot.get("height"),
        "screenshot_size_bytes": screenshot.get("size_bytes"),
        "visible_assertions": assertions,
        "visible_assertion_count": len(assertions),
        "visible_assertion_pass_count": sum(1 for value in assertions.values() if value is True),
        "html_artifact": artifacts.get("html"),
        "receipt_artifact": artifacts.get("receipt"),
        "screenshot_artifact": artifacts.get("screenshot_png"),
        "errors": payload.get("errors"),
    }


def _dag_stress_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.dag_stress_suite_receipt.v1":
        return None
    rungs = payload.get("rungs")
    rung_records = rungs if isinstance(rungs, list) else []
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "execution": payload.get("execution"),
        "rung_count": payload.get("rung_count"),
        "passed_rungs": payload.get("passed_rungs"),
        "expected_blocked_rungs": payload.get("expected_blocked_rungs"),
        "unexpected_rungs": payload.get("unexpected_rungs"),
        "blocked_rung_count": len(
            [
                rung
                for rung in rung_records
                if isinstance(rung, dict) and str(rung.get("status") or "").upper() == "BLOCKED"
            ]
        ),
        "rungs": [
            _dag_stress_rung_summary(rung)
            for rung in rung_records
            if isinstance(rung, dict)
        ],
    }


def _dag_stress_rung_summary(rung: dict[str, Any]) -> dict[str, Any]:
    return {
        "rung_id": rung.get("rung_id"),
        "status": rung.get("status"),
        "expected_status": rung.get("expected_status"),
        "verdict": rung.get("verdict"),
        "attempt_count": rung.get("attempt_count"),
        "event_count": rung.get("event_count"),
    }


def _dag_stress_campaign_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.dag_stress_campaign_receipt.v1":
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "execution": payload.get("execution"),
        "max_budget": payload.get("max_budget"),
        "repetitions": payload.get("repetitions"),
        "suite_count": payload.get("suite_count"),
        "total_rungs": payload.get("total_rungs"),
        "failed_suite_count": payload.get("failed_suite_count"),
        "status_counts": payload.get("status_counts"),
        "verdict_counts": payload.get("verdict_counts"),
        "grading_dimensions": payload.get("grading_dimensions"),
    }


def _route_memory_summary(
    candidate: dict[str, Any],
    sync: dict[str, Any],
    readback: dict[str, Any],
) -> dict[str, Any] | None:
    if not any((candidate, sync, readback)):
        return None
    candidate_ok = candidate.get("schema") == "tau.dag_route_memory_candidate_receipt.v1"
    sync_ok = sync.get("schema") == "tau.dag_route_memory_sync_receipt.v1"
    readback_ok = readback.get("schema") == "tau.memory_readback_proof.v1"
    memory_response = sync.get("memory_response")
    memory_response = memory_response if isinstance(memory_response, dict) else {}
    return {
        "candidate": (
            {
                "schema": candidate.get("schema"),
                "status": candidate.get("status"),
                "ok": candidate.get("ok"),
                "dag_id": candidate.get("dag_id"),
                "goal_hash": candidate.get("goal_hash"),
                "accepted_candidate_count": candidate.get("accepted_candidate_count"),
                "rejected_candidate_count": candidate.get("rejected_candidate_count"),
                "sync_status": candidate.get("sync_status"),
                "memory_sync": candidate.get("memory_sync"),
                "route_mutation": candidate.get("route_mutation"),
                "dag_mutation": candidate.get("dag_mutation"),
                "provider_calls": candidate.get("provider_calls"),
                "alert_count": _count(candidate.get("alerts")),
            }
            if candidate_ok
            else None
        ),
        "sync": (
            {
                "schema": sync.get("schema"),
                "status": sync.get("status"),
                "ok": sync.get("ok"),
                "dag_id": sync.get("dag_id"),
                "goal_hash": sync.get("goal_hash"),
                "collection": sync.get("collection"),
                "memory_url": sync.get("memory_url"),
                "apply": sync.get("apply"),
                "memory_sync": sync.get("memory_sync"),
                "sync_status": sync.get("sync_status"),
                "projected_document_count": sync.get("projected_document_count"),
                "memory_response": {
                    "collection": memory_response.get("collection"),
                    "inserted": memory_response.get("inserted"),
                    "updated": memory_response.get("updated"),
                    "total": memory_response.get("total"),
                    "error_count": _count(memory_response.get("errors")),
                },
                "approval_receipt": sync.get("approval_receipt"),
                "approval_receipt_sha256": sync.get("approval_receipt_sha256"),
                "alert_count": _count(sync.get("alerts")),
                "route_mutation": sync.get("route_mutation"),
                "dag_mutation": sync.get("dag_mutation"),
                "provider_calls": sync.get("provider_calls"),
            }
            if sync_ok
            else None
        ),
        "readback": (
            {
                "schema": readback.get("schema"),
                "status": readback.get("status"),
                "ok": readback.get("ok"),
                "collection": readback.get("collection"),
                "memory_url": readback.get("memory_url"),
                "endpoint": readback.get("endpoint"),
                "document_count_returned": readback.get("document_count_returned"),
                "found_count": readback.get("found_count"),
                "missing_keys": readback.get("missing_keys"),
            }
            if readback_ok
            else None
        ),
    }


def _dag_expansion_summary(
    validation: dict[str, Any],
    policy: dict[str, Any],
    apply: dict[str, Any],
) -> dict[str, Any] | None:
    if not any((validation, policy, apply)):
        return None
    return {
        "validation": _dag_expansion_receipt_summary(
            validation,
            expected_schema="tau.dag_expansion_validation_receipt.v1",
        ),
        "policy": _dag_expansion_receipt_summary(
            policy,
            expected_schema="tau.dag_expansion_policy_receipt.v1",
        ),
        "apply": _dag_expansion_receipt_summary(
            apply,
            expected_schema="tau.dag_expansion_apply_receipt.v1",
        ),
    }


def _dag_expansion_receipt_summary(
    payload: dict[str, Any],
    *,
    expected_schema: str,
) -> dict[str, Any] | None:
    if payload.get("schema") != expected_schema:
        return None
    alerts = payload.get("alerts")
    alert_records = alerts if isinstance(alerts, list) else []
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "verdict": payload.get("verdict"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "dag_id": payload.get("dag_id"),
        "goal_hash": payload.get("goal_hash"),
        "proposal": payload.get("proposal"),
        "proposal_sha256": payload.get("proposal_sha256"),
        "validation_receipt": payload.get("validation_receipt"),
        "validation_receipt_sha256": payload.get("validation_receipt_sha256"),
        "policy_receipt": payload.get("policy_receipt"),
        "policy_receipt_sha256": payload.get("policy_receipt_sha256"),
        "preview_path": payload.get("preview_path"),
        "preview_sha256": payload.get("preview_sha256"),
        "expanded_dag": payload.get("expanded_dag"),
        "expanded_dag_sha256": payload.get("expanded_dag_sha256"),
        "alert_count": len(alert_records),
        "alert_codes": [
            alert.get("code")
            for alert in alert_records
            if isinstance(alert, dict) and isinstance(alert.get("code"), str)
        ],
        "errors": payload.get("errors"),
    }


def _github_apply_policy_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.github_apply_policy_receipt.v1":
        return None
    checks = payload.get("checks")
    check_records = checks if isinstance(checks, list) else []
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "target": payload.get("target"),
        "actions": payload.get("actions"),
        "requirements": payload.get("requirements"),
        "preflight_ready": payload.get("preflight_ready"),
        "approval_receipt": payload.get("approval_receipt"),
        "redaction_receipt": payload.get("redaction_receipt"),
        "check_count": len(check_records),
        "failed_checks": [
            check.get("code")
            for check in check_records
            if isinstance(check, dict) and check.get("ok") is not True
        ],
        "errors": payload.get("errors"),
    }


def _github_handoff_transport_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") != "tau.github_handoff_transport_receipt.v1":
        return None
    command_results = payload.get("command_results")
    command_result_records = command_results if isinstance(command_results, list) else []
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "dry_run": payload.get("dry_run"),
        "applied": payload.get("applied"),
        "target": payload.get("target"),
        "command_count": _count(payload.get("commands")),
        "command_result_count": len(command_result_records),
        "preflight_result_count": _count(payload.get("preflight_results")),
        "errors": payload.get("errors"),
    }


def _load_lifecycle_states(
    run_dir: Path,
    runtime_manifest: dict[str, Any],
    run_receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for item in runtime_manifest.get("provider_session_states", []):
        loaded = _load_path_or_object(item, base=run_dir)
        if loaded:
            states.append(loaded)
    if not states:
        for item in run_receipt.get("provider_session_states", []):
            if isinstance(item, dict):
                states.append(item)
    return states


def _load_readiness_records(
    run_dir: Path,
    runtime_manifest: dict[str, Any],
    run_receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in runtime_manifest.get("readiness_records", []):
        loaded = _load_path_or_object(item, base=run_dir)
        if loaded:
            records.append(loaded)
    if not records:
        for item in run_receipt.get("readiness_records", []):
            if isinstance(item, dict):
                records.append(item)
    return records


def _load_path_or_object(value: Any, *, base: Path) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    payload = _read_optional_json(path)
    if payload:
        payload["_source_path"] = str(path)
        payload["_source_sha256"] = _file_sha256(path)
    return payload


def _event_path(
    run_dir: Path,
    run_receipt: dict[str, Any],
    runtime_manifest: dict[str, Any],
) -> Path | None:
    value = run_receipt.get("events_jsonl") or runtime_manifest.get("events_jsonl")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = run_dir / path
    return path


def _events_count(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (dict, list)) else 0


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _path_sha256(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _file_sha256(Path(value).expanduser())


def _file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _value_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
