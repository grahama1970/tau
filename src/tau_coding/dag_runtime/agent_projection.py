"""Tau-agent run projections and operator-action contracts (tau#309).

``tau.run_projection.v1`` / ``tau.agent_projection.v1`` are read-only views
derived exclusively from the authoritative agent-node journal and receipts —
never from panes, provider session status, or transport side channels.

``tau.operator_action_request.v1`` / ``tau.operator_action_receipt.v1`` carry
bounded, stale-safe operator actions. An action succeeds only when it changes
Tau's authoritative journal; a provider/session accepting input proves
nothing. Clients (Herdr included) are renderers and submitters, never state
owners.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tau_coding.dag_runtime.agent_node import (
    OPAQUE_COMPAT_HARNESS_MODE,
    AgentNodeError,
    AgentNodeRun,
)
from tau_coding.dag_runtime.model import canonical_sha256

RUN_PROJECTION_SCHEMA = "tau.run_projection.v1"
AGENT_PROJECTION_SCHEMA = "tau.agent_projection.v1"
OPERATOR_ACTION_REQUEST_SCHEMA = "tau.operator_action_request.v1"
OPERATOR_ACTION_RECEIPT_SCHEMA = "tau.operator_action_receipt.v1"

OPERATOR_ACTIONS = (
    "cancel",
    "add_next_turn_instruction",
    "retry_requested",
    "request_independent_review",
    "request_human_approval",
    "pause",
    "resume",
)
AUTHORIZED_ACTORS = ("human_operator", "project_watchdog")

LIFECYCLE_FROM_EVENT = {
    "agent_node_started": "model_turn_running",
    "tool_request_admitted": "tool_running",
    "tool_request_rejected": "model_turn_running",
    "tool_effect_recorded": "model_turn_running",
    "agent_turn_recorded": "model_turn_running",
    "steering_queued": "model_turn_running",
    "evidence_recorded": "verifying",
    "agent_error": "repair_requested",
    "agent_cancelled": "cancelled",
    "agent_loop_finished": "verifying",
}
TERMINAL_LIFECYCLES = ("completed", "failed", "cancelled", "blocked")


class OperatorActionError(AgentNodeError):
    """Fail-closed operator action rejection with a stable code."""


def project_agent_node(
    run: AgentNodeRun, *, settlement: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Derive a ``tau.agent_projection.v1`` record from the journal alone."""
    run.journal.verify_chain()
    entries = run.journal.entries
    lifecycle = "selected"
    for entry in entries:
        lifecycle = LIFECYCLE_FROM_EVENT.get(entry["event_type"], lifecycle)
        if entry["event_type"] == "agent_node_settled":
            lifecycle = entry["payload"]["state"]
    blocker = None
    if settlement is not None and settlement.get("blockers"):
        blocker = "|".join(settlement["blockers"])
    body = {
        "schema": AGENT_PROJECTION_SCHEMA,
        "run_id": run.work_order["run_id"],
        "node_id": run.work_order["node_id"],
        "attempt_id": run.work_order["attempt_id"],
        "attempt": run.work_order["attempt"],
        "goal_hash": run.work_order["goal_hash"],
        "plan_sha256": run.work_order["plan_sha256"],
        "journal_seq": entries[-1]["seq"] if entries else 0,
        "journal_head_sha256": entries[-1]["sha256"] if entries else "",
        "role": run.work_order.get("role"),
        "harness": run.work_order.get("harness", "tau_native_agent_loop"),
        "transport_profile": _selected_profile(run.work_order),
        "lifecycle": lifecycle,
        "turns": len(run.turn_receipts),
        "turn_receipt_sha256s": [item["sha256"] for item in run.turn_receipts],
        "tool_effect_receipt_sha256s": [item["sha256"] for item in run.tool_effect_receipts],
        "evidence_kinds": sorted(run.evidence),
        "current_blocker": blocker,
        "permitted_operator_actions": permitted_actions(lifecycle, run.work_order),
        "proof_boundary": {
            "derived_from_journal_only": True,
            "panes_and_transport_status_not_authoritative": True,
            "projection_is_not_semantic_quality_proof": True,
        },
    }
    return {**body, "sha256": canonical_sha256(body)}


def project_run(
    *,
    run_id: str,
    dag_id: str,
    goal_hash: str,
    node_projections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    for projection in node_projections:
        if projection.get("schema") != AGENT_PROJECTION_SCHEMA:
            raise AgentNodeError("run_projection_node_schema_invalid")
        if projection.get("run_id") != run_id:
            raise AgentNodeError("run_projection_run_id_mismatch", str(projection.get("node_id")))
        if projection.get("goal_hash") != goal_hash:
            raise AgentNodeError("run_projection_goal_mismatch", str(projection.get("node_id")))
    body = {
        "schema": RUN_PROJECTION_SCHEMA,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "nodes": [dict(projection) for projection in node_projections],
        "journal_seq_total": sum(int(p["journal_seq"]) for p in node_projections),
    }
    return {**body, "sha256": canonical_sha256(body)}


def validate_projection_readback(
    projection: Mapping[str, Any], *, run: AgentNodeRun
) -> None:
    """Reject stale or mismatched projections against the live journal."""
    if projection.get("schema") != AGENT_PROJECTION_SCHEMA:
        raise AgentNodeError("projection_schema_invalid", str(projection.get("schema")))
    if projection.get("run_id") != run.work_order["run_id"] or projection.get(
        "node_id"
    ) != run.work_order["node_id"]:
        raise AgentNodeError("projection_identity_mismatch")
    if not projection.get("goal_hash"):
        raise AgentNodeError("projection_goal_binding_missing")
    if projection["goal_hash"] != run.work_order["goal_hash"]:
        raise AgentNodeError("projection_goal_binding_missing")
    head_seq = run.journal.entries[-1]["seq"] if run.journal.entries else 0
    if projection.get("journal_seq") != head_seq:
        raise AgentNodeError(
            "projection_stale_journal_seq", f"{projection.get('journal_seq')}!={head_seq}"
        )
    head_sha = run.journal.entries[-1]["sha256"] if run.journal.entries else ""
    if projection.get("journal_head_sha256") != head_sha:
        raise AgentNodeError("projection_stale_journal_head")


def permitted_actions(lifecycle: str, work_order: Mapping[str, Any]) -> list[str]:
    if lifecycle in TERMINAL_LIFECYCLES:
        return ["retry_requested"] if lifecycle in ("failed", "cancelled") else []
    actions = [
        "cancel",
        "add_next_turn_instruction",
        "request_independent_review",
        "request_human_approval",
    ]
    if work_order.get("harness") == OPAQUE_COMPAT_HARNESS_MODE:
        actions.remove("add_next_turn_instruction")
    return actions


def apply_operator_action(
    *,
    run: AgentNodeRun,
    request: Mapping[str, Any],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Validate and apply one bounded operator action against the journal.

    Returns a ``tau.operator_action_receipt.v1``. The receipt's
    ``journal_transition`` proves the action changed authoritative state;
    typed outcomes ``unsupported`` / ``queued_for_next_turn`` /
    ``fork_required`` cover structured control that the current harness or
    transport cannot honor immediately.
    """
    _validate_action_request(run=run, request=request, max_attempts=max_attempts)
    observed_seq = int(request["observed_journal_seq"])
    action = request["action"]
    harness = run.work_order.get("harness", "tau_native_agent_loop")
    outcome = "applied"
    if action == "cancel":
        run.cancel(f"operator:{request['actor']}")
    elif action == "add_next_turn_instruction":
        if harness == OPAQUE_COMPAT_HARNESS_MODE:
            outcome = "fork_required"
            run.journal.append(
                "operator_action_deferred", {"action": action, "outcome": outcome}
            )
        else:
            run.steer(str(request.get("instruction", "")))
            outcome = "queued_for_next_turn"
    elif action in ("pause", "resume"):
        outcome = "unsupported"
        run.journal.append("operator_action_deferred", {"action": action, "outcome": outcome})
    elif action == "retry_requested":
        run.journal.append(
            "retry_authorized",
            {"actor": request["actor"], "next_attempt": run.work_order["attempt"] + 1},
        )
    else:
        run.journal.append("operator_request_recorded", {"action": action})
    head = run.journal.entries[-1]
    body = {
        "schema": OPERATOR_ACTION_RECEIPT_SCHEMA,
        "request": dict(request),
        "request_sha256": canonical_sha256(dict(request)),
        "run_id": run.work_order["run_id"],
        "node_id": run.work_order["node_id"],
        "attempt": run.work_order["attempt"],
        "goal_hash": run.work_order["goal_hash"],
        "outcome": outcome,
        "journal_transition": {
            "observed_seq": observed_seq,
            "resulting_seq": head["seq"],
            "resulting_head_sha256": head["sha256"],
            "journal_changed": head["seq"] > observed_seq,
        },
    }
    return {**body, "sha256": canonical_sha256(body)}


def _validate_action_request(
    *, run: AgentNodeRun, request: Mapping[str, Any], max_attempts: int
) -> None:
    if request.get("schema") != OPERATOR_ACTION_REQUEST_SCHEMA:
        raise OperatorActionError("operator_action_schema_invalid", str(request.get("schema")))
    action = request.get("action")
    if action not in OPERATOR_ACTIONS:
        raise OperatorActionError("operator_action_unknown", str(action))
    actor = request.get("actor")
    if actor not in AUTHORIZED_ACTORS:
        raise OperatorActionError("operator_action_unauthorized_actor", str(actor))
    if request.get("run_id") != run.work_order["run_id"] or request.get(
        "node_id"
    ) != run.work_order["node_id"]:
        raise OperatorActionError("operator_action_identity_mismatch")
    if request.get("goal_hash") != run.work_order["goal_hash"]:
        raise OperatorActionError("operator_action_goal_mismatch")
    observed = request.get("observed_journal_seq")
    head_seq = run.journal.entries[-1]["seq"] if run.journal.entries else 0
    if type(observed) is not int or observed != head_seq:
        raise OperatorActionError(
            "operator_action_stale_journal_seq", f"{observed}!={head_seq}"
        )
    lifecycle = _current_lifecycle(run)
    if lifecycle in TERMINAL_LIFECYCLES and action != "retry_requested":
        raise OperatorActionError("operator_action_node_terminal", lifecycle)
    if action == "retry_requested":
        if lifecycle not in ("failed", "cancelled"):
            raise OperatorActionError("operator_action_retry_not_applicable", lifecycle)
        if run.work_order["attempt"] >= max_attempts:
            raise OperatorActionError(
                "operator_action_retry_exhausted",
                f"attempt={run.work_order['attempt']} max={max_attempts}",
            )


def _current_lifecycle(run: AgentNodeRun) -> str:
    lifecycle = "selected"
    for entry in run.journal.entries:
        lifecycle = LIFECYCLE_FROM_EVENT.get(entry["event_type"], lifecycle)
        if entry["event_type"] == "agent_node_settled":
            lifecycle = entry["payload"]["state"]
    return lifecycle


def _selected_profile(work_order: Mapping[str, Any]) -> dict[str, Any] | None:
    selection = work_order.get("transport_profile_selection")
    if not isinstance(selection, Mapping):
        return None
    selected = selection.get("selected_profile")
    return dict(selected) if isinstance(selected, Mapping) else None
