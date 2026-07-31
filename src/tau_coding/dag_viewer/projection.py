"""Build browser-neutral manifests and live snapshots from durable replay."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.correction import reduce_correction_projections
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.replay import (
    DagReplayState,
    HistoricalReplayResult,
    replay_dag_run_at_sequence,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunReader
from tau_coding.dag_viewer.causal import DagCausalModel, build_causal_model
from tau_coding.dag_viewer.receipt_index import ReceiptIndex
from tau_coding.dag_viewer.redaction import redact_for_viewer

PROOF_SCOPE = {
    "proves": [
        "Tau projected verified SQLite journal state in authoritative sequence order.",
        "Scheduler, runtime, and receipt-admission state remain distinct.",
        "Causal, route, join, and attention state derives from the selected journal prefix.",
    ],
    "does_not_prove": [
        "Runtime text proves node completion.",
        "Agent or reviewer claims are semantically correct.",
        "The source DAG may be edited from the viewer.",
        "A causal explanation proves semantic correctness or future route behavior.",
    ],
}

OPTIONAL_INSPECTOR_SCHEMAS = {
    "completion_boundary": "tau.node_completion_boundary.v1",
    "review_scope": "tau.review_scope.v1",
    "workspace_freshness": "tau.workspace_freshness.v1",
    "worker": "tau.worker_assignment.v1",
    "execution_profile": "tau.execution_profile_resolution.v1",
    "gap_expansion": "tau.gap_candidate.v1",
}


def load_dag_replay(
    *, run_dir: Path, run_id: str | None = None, at_sequence: int | None = None
) -> tuple[DagReplayState, tuple[dict[str, Any], ...]]:
    result = load_dag_replay_result(run_dir=run_dir, run_id=run_id, at_sequence=at_sequence)
    return result.replay, result.events


def load_dag_replay_result(
    *, run_dir: Path, run_id: str | None = None, at_sequence: int | None = None
) -> HistoricalReplayResult:
    database = run_dir.expanduser().resolve() / "dag-run.sqlite3"
    with SqliteDagRunReader(database) as reader, reader.snapshot():
        run_ids = reader.run_ids()
        if run_id is None:
            if len(run_ids) != 1:
                raise RuntimeError("dag_viewer_run_id_ambiguous")
            run_id = run_ids[0]
        return replay_dag_run_at_sequence(reader, run_id, at_sequence)


def build_dag_view_manifest(*, replay: DagReplayState, run_dir: Path) -> dict[str, Any]:
    source_path = run_dir / "source-dag.json"
    source_available = source_path.is_file()
    source: dict[str, Any] | None = None
    if source_available:
        try:
            loaded_source = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("dag_source_artifact_invalid") from exc
        if not isinstance(loaded_source, dict):
            raise RuntimeError("dag_source_artifact_invalid")
        if canonical_sha256(loaded_source) != replay.plan.source_payload_sha256:
            raise RuntimeError("dag_source_artifact_hash_mismatch")
        source = loaded_source
    graph = {
        "nodes": [node.to_payload() for node in replay.plan.nodes],
        "edges": [edge.to_payload() for edge in replay.plan.control_edges],
        "terminals": [item.to_payload() for item in replay.plan.terminal_endpoints],
        "routes": [item.to_value() for item in replay.plan.route_contracts],
        "joins": [item.to_value() for item in replay.plan.join_contracts],
    }
    goal = replay.plan.goal_binding.to_value()
    source_extensions = replay.plan.source_extensions.to_value()
    workflow = source_extensions.get("workflow") if isinstance(source_extensions, dict) else None
    payload: dict[str, Any] = {
        "schema": "tau.dag_view_manifest.v1",
        "run_id": replay.run_id,
        "plan_id": replay.plan.plan_id,
        "plan_sha256": replay.plan.plan_sha256,
        "source_schema": replay.plan.source_schema,
        "source_sha256": replay.plan.source_payload_sha256,
        "source_available": source_available,
        "source_redacted": False,
        "source_dag": source,
        "source_status": "AVAILABLE" if source_available else "SOURCE_DAG_NOT_RETAINED",
        "dag_plan": replay.plan.to_payload(),
        "goal": goal,
        "workflow": workflow,
        "graph": graph,
        "receipt_index": [],
        "proof_scope": PROOF_SCOPE,
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["source_redacted"] = any(
        path.startswith("$.source_dag") for path in redacted.redacted_paths
    )
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def build_dag_live_snapshot(
    *,
    replay: DagReplayState,
    recent_events: tuple[dict[str, Any], ...],
    view_mode: str = "LIVE",
    selected_event_created_at: str | None = None,
    receipt_index: ReceiptIndex | None = None,
) -> dict[str, Any]:
    snapshot, _ = build_dag_view_state(
        replay=replay,
        recent_events=recent_events,
        view_mode=view_mode,
        selected_event_created_at=selected_event_created_at,
        receipt_index=receipt_index,
    )
    return snapshot


def build_dag_view_state(
    *,
    replay: DagReplayState,
    recent_events: tuple[dict[str, Any], ...],
    view_mode: str = "LIVE",
    selected_event_created_at: str | None = None,
    receipt_index: ReceiptIndex | None = None,
) -> tuple[dict[str, Any], DagCausalModel]:
    corrections = reduce_correction_projections(recent_events)
    correction_by_node = {
        str(correction.incident.get("node_id")): correction for correction in corrections
    }
    latest_attempt = {
        node_id: max(
            (item for item in replay.attempts if item.node_id == node_id),
            key=lambda item: item.attempt,
            default=None,
        )
        for node_id, _ in replay.node_states
    }
    nodes: list[dict[str, Any]] = []
    runtime_by_endpoint = {item.endpoint_lease_sha256: item for item in replay.runtime_projections}
    for node_id, state in replay.node_states:
        accepted = state == "success"
        attempt = latest_attempt[node_id]
        scheduler_state, admission_state = _project_node_state(
            committed_state=state,
            attempt_state=attempt.state if attempt is not None else None,
        )
        plan_node = next(node for node in replay.plan.nodes if node.node_id == node_id)
        replay_result = next(
            (item for item in reversed(replay.results) if item.node_id == node_id), None
        )
        result_payload = replay_result.payload if replay_result is not None else {}
        attempt_started_at = (
            _attempt_event_created_at(
                recent_events,
                attempt_id=attempt.attempt_id,
                event_type="attempt_dispatched",
            )
            if attempt is not None
            else None
        )
        accepted_output = result_payload.get("accepted_output")
        errors = result_payload.get("errors")
        endpoint_hash = _find_endpoint_lease_sha256(result_payload)
        runtime = runtime_by_endpoint.get(endpoint_hash or "")
        nodes.append(
            {
                "node_id": node_id,
                "node_kind": plan_node.adapter_kind,
                "scheduler": {
                    "state": scheduler_state,
                    "attempt": attempt.attempt if attempt is not None else 0,
                    "max_attempts": plan_node.max_attempts,
                },
                "runtime": {
                    "state": runtime.state if runtime else "UNKNOWN",
                    "liveness": runtime.liveness if runtime else "UNKNOWN",
                    "confidence": runtime.confidence if runtime else "UNKNOWN",
                    "last_event_id": runtime.last_event_id if runtime else None,
                },
                "admission": {
                    "state": admission_state,
                    "accepted": accepted,
                    "receipt_refs": [],
                },
                "result": {
                    "summary": (
                        accepted_output.get("summary")
                        if accepted and isinstance(accepted_output, dict)
                        else None
                    ),
                    "accepted_output": (
                        accepted_output if accepted and isinstance(accepted_output, dict) else None
                    ),
                    "blocker_codes": (
                        [str(item) for item in errors]
                        if state in {"blocked", "failed", "timed_out"} and isinstance(errors, list)
                        else []
                    ),
                    "started_at": _result_timestamp(result_payload, "started_at")
                    or attempt_started_at,
                    "finished_at": (
                        result_payload.get("finished_at")
                        if isinstance(result_payload.get("finished_at"), str)
                        else None
                    ),
                    "duration_seconds": (
                        result_payload.get("duration_seconds")
                        if isinstance(result_payload.get("duration_seconds"), (int, float))
                        else None
                    ),
                    "cost_accounting": (
                        result_payload.get("cost_accounting")
                        if isinstance(result_payload.get("cost_accounting"), dict)
                        else _empty_cost_accounting()
                    ),
                    "budget_blocker": (
                        result_payload.get("budget_blocker")
                        if isinstance(result_payload.get("budget_blocker"), dict)
                        else None
                    ),
                },
                "transaction": _transaction_projection(
                    plan_node=plan_node,
                    replay_result=replay_result.payload if replay_result else None,
                    recent_events=recent_events,
                    scheduler_attempt=attempt.attempt if attempt is not None else None,
                    accepted=accepted,
                    committed_state=state,
                ),
                "correction": _correction_payload(correction_by_node.get(node_id)),
                "updated_sequence": replay.journal_sequence,
            }
        )
    if view_mode not in {"LIVE", "HISTORICAL"}:
        raise RuntimeError("dag_viewer_view_mode_invalid")
    reference_time_ms = time.time_ns() // 1_000_000
    if view_mode == "HISTORICAL":
        if selected_event_created_at is None:
            raise RuntimeError("dag_viewer_sequence_timestamp_missing")
        try:
            selected_time = datetime.fromisoformat(selected_event_created_at)
            reference_time_ms = int(selected_time.timestamp() * 1000)
        except ValueError as exc:
            raise RuntimeError("dag_viewer_sequence_timestamp_invalid") from exc
    lease_stale = (
        replay.run_status == "RUNNING"
        and replay.lease_expires_at_ms is not None
        and replay.lease_expires_at_ms < reference_time_ms
    )
    projection_state = (
        "RECONCILIATION_REQUIRED"
        if replay.run_status == "RECONCILIATION_REQUIRED"
        else "STALE"
        if lease_stale
        else "COMPLETE"
        if replay.run_status in {"PASS", "BLOCKED"}
        else "LIVE"
    )
    edges = [{"edge_id": key, "state": value} for key, value in replay.edge_states]
    terminals = [{"terminal_id": key, "state": value} for key, value in replay.terminal_states]
    correction_payloads = [
        payload for item in corrections if (payload := _correction_payload(item)) is not None
    ]
    causal = build_causal_model(
        replay=replay,
        events=recent_events,
        receipts=receipt_index or ReceiptIndex(Path(".").resolve(), ()),
        node_projections=nodes,
        edge_projections=edges,
        terminal_projections=terminals,
        corrections=correction_payloads,
        projection_state=projection_state,
    )
    source_extensions = replay.plan.source_extensions.to_value()
    workflow = source_extensions.get("workflow") if isinstance(source_extensions, dict) else None
    workflow_result_node_id = (
        workflow.get("result_node_id")
        if isinstance(workflow, dict) and isinstance(workflow.get("result_node_id"), str)
        else None
    )
    active_states = {
        "ready",
        "running",
        "validating",
        "committing",
        "retry_pending",
        "reconciliation_required",
    }
    active_node_ids = [
        node["node_id"] for node in nodes if node["scheduler"]["state"] in active_states
    ]
    blocked_nodes = [
        node for node in nodes if node["scheduler"]["state"] in {"blocked", "failed", "timed_out"}
    ]
    result_node = next(
        (node for node in nodes if node["node_id"] == workflow_result_node_id),
        None,
    )
    payload = {
        "schema": "tau.dag_view_snapshot.v2",
        "run_id": replay.run_id,
        "plan_sha256": replay.plan.plan_sha256,
        "journal_sequence": replay.journal_sequence,
        "view": {
            "mode": view_mode,
            "sequence": replay.journal_sequence,
            "sequence_created_at": selected_event_created_at,
        },
        "run_status": replay.run_status,
        "run_verdict": replay.run_verdict,
        "projection_state": projection_state,
        "nodes": nodes,
        "edges": edges,
        "terminals": terminals,
        "routes": list(causal.routes),
        "joins": list(causal.joins),
        "corrections": correction_payloads,
        "attention_items": list(causal.attention_items),
        "highest_priority_attention_id": (
            causal.attention_items[0]["attention_id"] if causal.attention_items else None
        ),
        "run_summary": {
            "active_node_ids": active_node_ids,
            "accepted_node_ids": [
                node["node_id"] for node in nodes if node["admission"]["accepted"] is True
            ],
            "highest_priority_blocker": (
                {
                    "node_id": blocked_nodes[0]["node_id"],
                    "codes": blocked_nodes[0]["result"]["blocker_codes"],
                }
                if blocked_nodes
                else None
            ),
            "final_result": (
                result_node["result"]["accepted_output"]
                if result_node is not None and result_node["admission"]["accepted"] is True
                else None
            ),
            "cost_accounting": _run_cost_accounting(nodes),
        },
        "recent_events": _browser_event_projections(
            recent_events[-200:], receipt_index=receipt_index
        ),
        "proof_scope": PROOF_SCOPE,
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["snapshot_sha256"] = canonical_sha256(result)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result, causal


def _run_cost_accounting(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    node_costs = [
        node["result"]["cost_accounting"]
        for node in nodes
        if isinstance(node.get("result"), dict)
        and isinstance(node["result"].get("cost_accounting"), dict)
    ]
    estimated_values = [
        value
        for item in node_costs
        if isinstance((value := item.get("estimated_cost_usd")), (int, float))
    ]
    return {
        "schema": "tau.generic_dag_cost_accounting.v1",
        "source": (
            "provider_reported_estimate"
            if any(item.get("source") == "provider_reported_estimate" for item in node_costs)
            else "not_reported"
        ),
        "input_tokens": sum(_int_or_zero(item.get("input_tokens")) for item in node_costs),
        "output_tokens": sum(_int_or_zero(item.get("output_tokens")) for item in node_costs),
        "cache_read_tokens": sum(
            _int_or_zero(item.get("cache_read_tokens")) for item in node_costs
        ),
        "cache_write_tokens": sum(
            _int_or_zero(item.get("cache_write_tokens")) for item in node_costs
        ),
        "total_tokens": sum(_int_or_zero(item.get("total_tokens")) for item in node_costs),
        "estimated_cost_usd": round(sum(estimated_values), 12) if estimated_values else None,
        "estimated_cost_is_billing_truth": False,
    }


def _empty_cost_accounting() -> dict[str, Any]:
    return {
        "schema": "tau.generic_dag_cost_accounting.v1",
        "source": "not_reported",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": None,
        "estimated_cost_is_billing_truth": False,
    }


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _result_timestamp(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _attempt_event_created_at(
    events: tuple[dict[str, Any], ...], *, attempt_id: str, event_type: str
) -> str | None:
    for event in events:
        if event.get("event_type") != event_type or event.get("attempt_id") != attempt_id:
            continue
        created_at = event.get("created_at")
        if isinstance(created_at, str):
            return created_at
    return None


def _correction_payload(correction: Any) -> dict[str, Any] | None:
    if correction is None:
        return None
    return {
        "incident_id": correction.incident_id,
        "state": correction.state,
        "journal_sequence": correction.journal_sequence,
        "incident": correction.incident,
        "intent": correction.intent,
        "action_receipt": correction.action_receipt,
        "verification": correction.verification,
    }


def build_dag_live_events(
    *,
    replay: DagReplayState,
    events: tuple[dict[str, Any], ...],
    after_sequence: int,
    limit: int,
) -> dict[str, Any]:
    payload = {
        "schema": "tau.dag_live_event.v1",
        "run_id": replay.run_id,
        "after_sequence": after_sequence,
        "events": [event for event in events if int(event["seq"]) > after_sequence][:limit],
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def build_selected_node_inspector(
    *,
    replay: DagReplayState,
    recent_events: tuple[dict[str, Any], ...],
    node_id: str,
    attempt: int | None = None,
    view_mode: str = "LIVE",
    selected_event_created_at: str | None = None,
    receipt_index: ReceiptIndex | None = None,
) -> dict[str, Any]:
    if view_mode not in {"LIVE", "HISTORICAL"}:
        raise RuntimeError("dag_viewer_view_mode_invalid")
    plan_node = next((node for node in replay.plan.nodes if node.node_id == node_id), None)
    if plan_node is None:
        raise RuntimeError("dag_viewer_node_inspector_not_found")
    attempts = [item for item in replay.attempts if item.node_id == node_id]
    selected_attempt = (
        next((item for item in attempts if item.attempt == attempt), None)
        if attempt is not None
        else max(attempts, key=lambda item: item.attempt, default=None)
    )
    if attempt is not None and selected_attempt is None:
        raise RuntimeError("dag_viewer_node_inspector_not_found")
    selected_attempt_number = selected_attempt.attempt if selected_attempt is not None else 0
    selected_attempt_id = selected_attempt.attempt_id if selected_attempt is not None else None
    replay_result = next(
        (
            item
            for item in reversed(replay.results)
            if item.node_id == node_id
            and (selected_attempt is None or item.attempt == selected_attempt.attempt)
        ),
        None,
    )
    result_payload = replay_result.payload if replay_result is not None else {}
    accepted_output = result_payload.get("accepted_output")
    input_manifest = _input_manifest_section(
        replay=replay,
        plan_node=plan_node,
        selected_attempt_id=selected_attempt_id,
    )
    completion_boundary = _optional_section(
        result_payload,
        keys=("node_completion_boundary", "completion_boundary"),
        schema=OPTIONAL_INSPECTOR_SCHEMAS["completion_boundary"],
        absent_status="not_available",
    )
    review_scope = _optional_section(
        result_payload,
        keys=("review_scope", "review", "review_receipt"),
        schema=OPTIONAL_INSPECTOR_SCHEMAS["review_scope"],
        absent_status="not_enforced",
    )
    workspace_freshness = _optional_section(
        result_payload,
        keys=("workspace_freshness", "workspace_freshness_receipt", "stale_read"),
        schema=OPTIONAL_INSPECTOR_SCHEMAS["workspace_freshness"],
        absent_status="not_enforced",
    )
    worker = _worker_section(
        replay=replay,
        result_payload=result_payload,
        endpoint_hash=_find_endpoint_lease_sha256(result_payload),
    )
    diagnostics = _diagnostics_section(
        events=recent_events,
        node_id=node_id,
        attempt_id=selected_attempt_id,
        result_payload=result_payload,
    )
    accepted_evidence = _accepted_evidence_section(
        plan_node=plan_node,
        result_payload=result_payload,
        accepted_output=accepted_output,
        receipt_index=receipt_index,
    )
    payload = {
        "schema": "tau.selected_node_inspector_projection.v1",
        "run_id": replay.run_id,
        "plan_id": replay.plan.plan_id,
        "plan_sha256": replay.plan.plan_sha256,
        "node_id": node_id,
        "attempt": selected_attempt_number,
        "attempt_id": selected_attempt_id,
        "journal_sequence": replay.journal_sequence,
        "view": {
            "mode": view_mode,
            "sequence": replay.journal_sequence,
            "sequence_created_at": selected_event_created_at,
        },
        "projection_key": canonical_sha256(
            {
                "run_id": replay.run_id,
                "plan_sha256": replay.plan.plan_sha256,
                "node_id": node_id,
                "attempt": selected_attempt_number,
                "journal_sequence": replay.journal_sequence,
            }
        ),
        "contract": _contract_section(replay=replay, plan_node=plan_node),
        "accepted_inputs": input_manifest,
        "completion_boundary": completion_boundary,
        "review_scope": review_scope,
        "workspace_freshness": workspace_freshness,
        "worker": worker,
        "accepted_evidence_and_artifacts": accepted_evidence,
        "diagnostics": diagnostics,
        "attention": _inspector_attention(
            input_manifest=input_manifest,
            completion_boundary=completion_boundary,
            review_scope=review_scope,
            workspace_freshness=workspace_freshness,
            worker=worker,
            accepted_evidence=accepted_evidence,
        ),
        "read_only": True,
        "mutation_controls": [],
        "proof_scope": {
            "proves": [
                "The browser received one backend-projected selected-node view.",
                "Accepted evidence, optional enforcement receipts, and diagnostics are separated.",
                "The projection is keyed by run, plan, node, attempt, and journal sequence.",
            ],
            "does_not_prove": [
                "Diagnostics are authoritative for node settlement.",
                "Optional receipt absence is a pass.",
                "Provider or reviewer semantic claims are correct.",
            ],
        },
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["projection_sha256"] = canonical_sha256(result)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def _contract_section(*, replay: DagReplayState, plan_node: DagPlanNode) -> dict[str, Any]:
    routes = [
        route.to_value()
        for route in replay.plan.route_contracts
        if isinstance(route.to_value(), dict)
        and route.to_value().get("source_node_id") == plan_node.node_id
    ]
    joins = [
        join.to_value()
        for join in replay.plan.join_contracts
        if isinstance(join.to_value(), dict)
        and join.to_value().get("join_node_id") == plan_node.node_id
    ]
    return {
        "status": "available",
        "role": plan_node.role,
        "adapter": {"kind": plan_node.adapter_kind, "executor": plan_node.executor},
        "profile": plan_node.runtime_requirement.to_value(),
        "routes": routes,
        "joins": joins,
        "required_evidence": list(plan_node.required_evidence),
        "limits": {
            "max_attempts": plan_node.max_attempts,
            "timeout": {"kind": plan_node.timeout_kind, "seconds": plan_node.timeout_seconds},
            "execution": replay.plan.execution_limits.to_value(),
        },
        "side_effects": replay.plan.security_declarations.to_value(),
        "source_bindings": [binding.to_value() for binding in plan_node.source_bindings],
        "requested_capabilities": [item.to_value() for item in plan_node.requested_capabilities],
    }


def _input_manifest_section(
    *,
    replay: DagReplayState,
    plan_node: DagPlanNode,
    selected_attempt_id: str | None,
) -> dict[str, Any]:
    bindings = [
        binding
        for binding in replay.plan.context_bindings
        if binding.target_node_id == plan_node.node_id
    ]
    if not bindings:
        return {
            "status": "not_available",
            "schema": "tau.node_input_manifest.v1",
            "reason": "no_input_bindings_declared",
            "attempt_id": selected_attempt_id,
            "bindings": [],
            "omissions": [],
        }
    results_by_node = {
        item.node_id: item for item in replay.results if item.terminal_state == "success"
    }
    projected_bindings: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for binding in bindings:
        source = results_by_node.get(binding.source_node_id)
        if source is None:
            omissions.append(
                {
                    "binding_id": binding.binding_id,
                    "source_node_id": binding.source_node_id,
                    "reason": "source_not_accepted",
                    "on_missing": binding.on_missing,
                }
            )
            continue
        accepted_output = source.payload.get("accepted_output")
        projected_bindings.append(
            {
                "binding_id": binding.binding_id,
                "source_node_id": binding.source_node_id,
                "source_attempt": source.attempt,
                "source_schema": (
                    accepted_output.get("schema") if isinstance(accepted_output, dict) else None
                ),
                "source_sha256": canonical_sha256(accepted_output)
                if accepted_output is not None
                else None,
                "projection": binding.projection,
                "selector_kind": binding.selector_kind,
                "materialization_mode": binding.materialization_mode,
                "by_reference": _references_in(accepted_output),
            }
        )
    return {
        "status": "available" if projected_bindings else "blocked",
        "schema": "tau.node_input_manifest.v1",
        "attempt_id": selected_attempt_id,
        "bindings": projected_bindings,
        "omissions": omissions,
    }


def _optional_section(
    payload: dict[str, Any],
    *,
    keys: tuple[str, ...],
    schema: str,
    absent_status: str,
) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return {"status": "available", "schema": value.get("schema") or schema, "value": value}
    return {"status": absent_status, "schema": schema, "reason": "receipt_absent"}


def _worker_section(
    *,
    replay: DagReplayState,
    result_payload: dict[str, Any],
    endpoint_hash: str | None,
) -> dict[str, Any]:
    for key in ("worker", "worker_assignment", "worker_receipt"):
        value = result_payload.get(key)
        if isinstance(value, dict):
            return {
                "status": "available",
                "schema": value.get("schema") or "tau.worker_assignment.v1",
                "value": value,
            }
    runtime = next(
        (
            item
            for item in replay.runtime_projections
            if item.endpoint_lease_sha256 == endpoint_hash
        ),
        None,
    )
    if runtime is None:
        return {
            "status": "not_enforced",
            "schema": OPTIONAL_INSPECTOR_SCHEMAS["worker"],
            "reason": "worker_receipt_absent",
        }
    return {
        "status": "available",
        "schema": "tau.worker_assignment.v1",
        "value": {
            "endpoint_lease_sha256": runtime.endpoint_lease_sha256,
            "state": runtime.state,
            "liveness": runtime.liveness,
            "confidence": runtime.confidence,
            "last_event_id": runtime.last_event_id,
        },
    }


def _accepted_evidence_section(
    *,
    plan_node: DagPlanNode,
    result_payload: dict[str, Any],
    accepted_output: Any,
    receipt_index: ReceiptIndex | None,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    if isinstance(accepted_output, dict):
        evidence.append(
            {
                "kind": "accepted_output",
                "schema": accepted_output.get("schema"),
                "sha256": canonical_sha256(accepted_output),
                "lineage": accepted_output.get("source_gap_lineage")
                or accepted_output.get("gap_lineage"),
            }
        )
    for key in ("accepted_evidence", "artifacts"):
        value = result_payload.get(key)
        if isinstance(value, list):
            evidence.extend(item for item in value if isinstance(item, dict))
    receipts = [
        item.to_payload() for item in (receipt_index.entries if receipt_index is not None else ())
    ]
    missing = [
        required
        for required in plan_node.required_evidence
        if not any(item.get("schema") == required for item in evidence)
    ]
    return {
        "status": "available" if evidence or receipts else "not_available",
        "items": evidence,
        "receipts": receipts,
        "missing_required_evidence": missing,
    }


def _diagnostics_section(
    *,
    events: tuple[dict[str, Any], ...],
    node_id: str,
    attempt_id: str | None,
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    diagnostic_events = [
        event
        for event in events
        if event.get("entity_type") == "node"
        and event.get("entity_id") == node_id
        and event.get("event_type") == "dag_diagnostic_event_appended"
        and (attempt_id is None or event.get("attempt_id") in {None, attempt_id})
    ][-25:]
    return {
        "status": "available" if diagnostic_events else "not_available",
        "authority": "diagnostic_only",
        "can_settle_node": False,
        "events": diagnostic_events,
        "stdout": _bounded_text(result_payload.get("stdout")),
        "stderr": _bounded_text(result_payload.get("stderr")),
        "timing": {
            "started_at": _result_timestamp(result_payload, "started_at"),
            "finished_at": _result_timestamp(result_payload, "finished_at"),
            "duration_seconds": result_payload.get("duration_seconds")
            if isinstance(result_payload.get("duration_seconds"), (int, float))
            else None,
        },
        "tool_activity": result_payload.get("tool_activity")
        if isinstance(result_payload.get("tool_activity"), list)
        else [],
        "token_activity": result_payload.get("cost_accounting")
        if isinstance(result_payload.get("cost_accounting"), dict)
        else _empty_cost_accounting(),
    }


def _inspector_attention(
    *,
    input_manifest: dict[str, Any],
    completion_boundary: dict[str, Any],
    review_scope: dict[str, Any],
    workspace_freshness: dict[str, Any],
    worker: dict[str, Any],
    accepted_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if input_manifest["status"] == "blocked":
        items.append({"severity": "BLOCKER", "code": "blocked_input", "section": "accepted_inputs"})
    if accepted_evidence.get("missing_required_evidence"):
        items.append(
            {
                "severity": "BLOCKER",
                "code": "missing_required_evidence",
                "section": "accepted_evidence_and_artifacts",
                "items": accepted_evidence["missing_required_evidence"],
            }
        )
    _append_state_attention(items, review_scope, "review_scope", "stale_review")
    _append_state_attention(
        items, workspace_freshness, "workspace_freshness", "unresolved_stale_read"
    )
    _append_state_attention(items, worker, "worker", "quarantined_worker")
    if completion_boundary["status"] == "not_available":
        items.append(
            {
                "severity": "WARNING",
                "code": "completion_boundary_not_available",
                "section": "completion_boundary",
            }
        )
    return items


def _append_state_attention(
    items: list[dict[str, Any]], section: dict[str, Any], section_name: str, code: str
) -> None:
    value = section.get("value")
    if not isinstance(value, dict):
        return
    state_values = {
        str(value.get("state") or "").lower(),
        str(value.get("disposition") or "").lower(),
        str(value.get("status") or "").lower(),
    }
    if code == "stale_review" and {"stale", "outdated"} & state_values:
        items.append({"severity": "ACTION_REQUIRED", "code": code, "section": section_name})
    elif (
        code == "unresolved_stale_read" and {"unresolved", "stale", "blocked"} & state_values
    ) or (code == "quarantined_worker" and {"quarantined", "blocked"} & state_values):
        items.append({"severity": "BLOCKER", "code": code, "section": section_name})


def _references_in(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    references: list[dict[str, Any]] = []
    for key in ("path", "artifact_id", "sha256", "uri", "receipt_id"):
        item = value.get(key)
        if isinstance(item, str):
            references.append({"field": key, "value": item})
    return references


def _bounded_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:4096] + ("[TRUNCATED]" if len(value) > 4096 else "")


def _project_node_state(*, committed_state: str, attempt_state: str | None) -> tuple[str, str]:
    if committed_state == "success":
        return "settled", "accepted"
    if committed_state == "superseded":
        return "superseded", "not_applicable"
    if committed_state in {"skipped", "cancelled"}:
        return committed_state, "not_applicable"
    if committed_state in {"blocked", "failed", "timed_out"}:
        return committed_state, "rejected"
    if committed_state != "pending":
        return committed_state, "awaiting_receipt"
    attempt_projection = {
        "RESERVED": ("ready", "not_started"),
        "DISPATCHED": ("running", "awaiting_receipt"),
        "STAGED": ("validating", "validating"),
        "VALIDATED": ("committing", "validating"),
        "OUTPUT_COMMITTED": ("committing", "validating"),
        "RETRY_SCHEDULED": ("retry_pending", "rejected"),
        "UNCERTAIN": ("reconciliation_required", "rejected"),
    }
    if attempt_state is None:
        return "pending", "not_started"
    return attempt_projection.get(attempt_state, ("pending", "not_started"))


def _find_endpoint_lease_sha256(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("endpoint_lease_sha256")
        if isinstance(candidate, str) and candidate.startswith("sha256:"):
            return candidate
        for item in value.values():
            found = _find_endpoint_lease_sha256(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_endpoint_lease_sha256(item)
            if found:
                return found
    return None


def _browser_event_projections(
    events: tuple[dict[str, Any], ...], *, receipt_index: ReceiptIndex | None
) -> list[dict[str, Any]]:
    receipt_ids = (
        {str(item.path): item.receipt_id for item in receipt_index.entries}
        if receipt_index is not None
        else {}
    )

    def project(value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {item_key: project(item, key=item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [project(item, key=key) for item in value]
        if isinstance(value, str) and key in {
            "path",
            "receipt",
            "route_decision_receipt",
            "join_decision_receipt",
        }:
            if value in receipt_ids:
                return receipt_ids[value]
            if Path(value).is_absolute():
                return "ABSOLUTE_PATH_OMITTED"
        return value

    return [project(event) for event in events]


def _transaction_projection(
    *,
    plan_node: DagPlanNode,
    replay_result: dict[str, Any] | None,
    recent_events: tuple[dict[str, Any], ...],
    scheduler_attempt: int | None,
    accepted: bool,
    committed_state: str,
) -> dict[str, Any] | None:
    if plan_node.adapter_kind != "generic_artifact_transaction":
        return None
    config = plan_node.adapter_config.to_value()
    transaction_config = config.get("transaction")
    transaction_id = (
        transaction_config.get("transaction_id") if isinstance(transaction_config, dict) else None
    )
    attempts: dict[int, dict[str, Any]] = {}
    accepted_manifest_sha256: str | None = None
    for event in recent_events:
        if (
            event.get("event_type") != "dag_diagnostic_event_appended"
            or event.get("entity_id") != plan_node.node_id
        ):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("authority") != "diagnostic_only":
            continue
        if payload.get("scheduler_attempt") != scheduler_attempt:
            continue
        attempt_number = payload.get("attempt")
        phase = payload.get("phase")
        evidence = payload.get("evidence")
        if not isinstance(attempt_number, int) or attempt_number < 1 or not isinstance(phase, str):
            continue
        evidence = evidence if isinstance(evidence, dict) else {}
        attempt = attempts.setdefault(attempt_number, {"attempt": attempt_number})
        if phase == "producer_started":
            attempt["producer_state"] = "RUNNING"
        elif phase == "producer_completed":
            attempt["producer_state"] = "PASS"
            attempt["candidate_manifest_sha256"] = evidence.get("candidate_manifest_sha256")
        elif phase == "validator_completed":
            attempt["validator_status"] = evidence.get("status")
        elif phase == "reviewer_started":
            attempt["reviewer_verdict"] = "RUNNING"
        elif phase == "reviewer_completed":
            attempt["reviewer_verdict"] = evidence.get("verdict")
            attempt["review_feedback_sha256"] = evidence.get("review_feedback_sha256")
        elif phase == "revision_committed":
            attempt["revision_instruction"] = evidence.get("instruction")
        elif phase == "accepted_manifest_written":
            candidate = evidence.get("accepted_manifest_sha256")
            if isinstance(candidate, str):
                accepted_manifest_sha256 = candidate

    if replay_result is not None:
        result_attempts = replay_result.get("attempts")
        if isinstance(result_attempts, list):
            for item in result_attempts:
                if not isinstance(item, dict) or not isinstance(item.get("attempt"), int):
                    continue
                attempt_number = int(item["attempt"])
                projected = attempts.setdefault(attempt_number, {"attempt": attempt_number})
                if isinstance(item.get("candidate_manifest_sha256"), str):
                    projected["producer_state"] = "PASS"
                if (
                    isinstance(transaction_config, dict)
                    and isinstance(transaction_config.get("validator"), dict)
                    and isinstance(item.get("validation_receipt_path"), str)
                ):
                    projected["validator_status"] = "PASS"
                for source_key, target_key in (
                    ("candidate_manifest_sha256", "candidate_manifest_sha256"),
                    ("review_verdict", "reviewer_verdict"),
                    ("review_feedback_sha256", "review_feedback_sha256"),
                ):
                    if item.get(source_key) is not None:
                        projected[target_key] = item[source_key]
        result_sha256 = replay_result.get("accepted_manifest_sha256")
        if isinstance(result_sha256, str):
            accepted_manifest_sha256 = result_sha256
    ordered_attempts = [attempts[key] for key in sorted(attempts)]
    if accepted:
        transaction_state = "ACCEPTED"
    elif committed_state == "blocked":
        transaction_state = "BLOCKED"
    elif committed_state in {"failed", "timed_out"}:
        transaction_state = "REJECTED"
    else:
        transaction_state = "AWAITING_RECEIPT"
    return {
        "transaction_id": transaction_id,
        "current_attempt": max(attempts, default=0),
        "max_attempts": int(config.get("transaction_max_attempts", plan_node.max_attempts)),
        "state": transaction_state,
        "accepted_manifest_sha256": accepted_manifest_sha256,
        "attempts": ordered_attempts,
    }
