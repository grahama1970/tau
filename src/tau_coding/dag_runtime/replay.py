"""Pure replay of the durable Tau DAG journal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import DagPlan
from tau_coding.dag_runtime.run_store import DagRunRecord, StoredAttempt
from tau_coding.dag_runtime.transition import (
    DagCommittedReceipt,
    DagTransitionBatch,
    transition_batch_from_payload,
)
from tau_coding.runtime_backends.contracts import RuntimeStateProjection


@dataclass(frozen=True, slots=True)
class DagReplayAttempt:
    node_id: str
    attempt: int
    attempt_id: str
    state: str
    effect_state: str


@dataclass(frozen=True, slots=True)
class DagReplayResult:
    node_id: str
    attempt: int
    terminal_state: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DagReplayState:
    run_id: str
    plan: DagPlan
    journal_sequence: int
    run_status: str
    run_verdict: str | None
    node_states: tuple[tuple[str, str], ...]
    edge_states: tuple[tuple[str, str], ...]
    terminal_states: tuple[tuple[str, str], ...]
    attempts: tuple[DagReplayAttempt, ...]
    results: tuple[DagReplayResult, ...]
    runtime_projections: tuple[RuntimeStateProjection, ...]
    transition_receipts: tuple[DagCommittedReceipt, ...]
    replay_events: tuple[dict[str, Any], ...]
    deadline_monotonic: tuple[tuple[str, float], ...]
    lease_owner: str | None
    lease_epoch: int | None
    lease_expires_at_ms: int | None
    block: dict[str, Any] | None


def apply_transition_state(
    *,
    plan: DagPlan,
    batch: DagTransitionBatch,
    node_states: dict[str, str],
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    deadlines: dict[str, float],
) -> None:
    """Apply durable transition effects without process-local side effects."""

    edges = {edge.edge_id: edge for edge in plan.control_edges}
    terminal_ids = {terminal.terminal_id for terminal in plan.terminal_endpoints}
    node_ids = {node.node_id for node in plan.nodes}
    for edge_settlement in batch.edge_settlements:
        if edge_settlement.edge_id not in edges:
            raise RuntimeError(f"dag_transition_unknown_edge:{edge_settlement.edge_id}")
        edge_prior = edge_states.get(edge_settlement.edge_id)
        if edge_prior is not None and edge_prior != edge_settlement.state:
            raise RuntimeError(f"dag_transition_effect_conflict:{edge_settlement.edge_id}")
        edge_states[edge_settlement.edge_id] = edge_settlement.state
        edge = edges[edge_settlement.edge_id]
        if edge.target_kind == "terminal" or edge.target_id in terminal_ids:
            terminal_states[edge.target_id] = edge_settlement.state
    for node_settlement in batch.node_settlements:
        if (
            node_settlement.node_id in node_ids
            and node_states.get(node_settlement.node_id) == "pending"
        ):
            node_states[node_settlement.node_id] = node_settlement.state
    for cancellation in batch.node_cancellations:
        if cancellation.node_id in node_ids and node_states.get(cancellation.node_id) == "pending":
            node_states[cancellation.node_id] = "cancelled"
    for arm in batch.deadline_arms:
        deadline_prior = deadlines.get(arm.deadline_id)
        if deadline_prior is not None and deadline_prior != arm.deadline_monotonic:
            raise RuntimeError(f"dag_transition_deadline_conflict:{arm.deadline_id}")
        deadlines[arm.deadline_id] = arm.deadline_monotonic
    for deadline_id in batch.deadline_cancellations:
        deadlines.pop(deadline_id, None)


def replay_dag_run(
    *,
    plan: DagPlan,
    run_record: DagRunRecord,
    events: tuple[dict[str, Any], ...],
    attempts: tuple[StoredAttempt, ...],
    runtime_projections: tuple[RuntimeStateProjection, ...],
) -> DagReplayState:
    """Reduce verified journal inputs into the authoritative read model."""

    if run_record.plan_sha256 != plan.plan_sha256:
        raise RuntimeError("dag_run_plan_mismatch")
    declared_terminal_nodes = {
        terminal.terminal_id
        for terminal in plan.terminal_endpoints
        if terminal.kind == "declared_node"
    }
    node_states = {
        node.node_id: "pending"
        for node in plan.nodes
        if node.node_id not in declared_terminal_nodes
    }
    edge_states: dict[str, str] = {}
    terminal_states: dict[str, str] = {}
    deadlines: dict[str, float] = {}
    results: list[DagReplayResult] = []
    receipts: dict[str, DagCommittedReceipt] = {}
    replay_events: list[dict[str, Any]] = []
    block: dict[str, Any] | None = None
    last_sequence = 0
    for event in events:
        sequence = int(event["seq"])
        if sequence <= last_sequence:
            raise RuntimeError("dag_journal_sequence_invalid")
        last_sequence = sequence
        event_type = event["event_type"]
        if event_type not in {
            "scheduler_transition_committed",
            "scheduler_control_transition_committed",
        }:
            continue
        transition_payload = event["payload"].get("transition")
        if not isinstance(transition_payload, Mapping):
            raise RuntimeError("dag_transition_replay_mismatch")
        batch = transition_batch_from_payload(transition_payload)
        replay_events.extend({**dict(item), "durably_replayed": True} for item in batch.events)
        for cancellation in batch.node_cancellations:
            if node_states.get(cancellation.node_id) != "pending":
                continue
            results.append(
                DagReplayResult(
                    node_id=cancellation.node_id,
                    attempt=0,
                    terminal_state="cancelled",
                    payload={
                        "node_id": cancellation.node_id,
                        "status": "CANCELLED",
                        "verdict": "CANCELLED",
                        "attempt_count": 0,
                        "accepted_output": None,
                        "errors": [],
                    },
                )
            )
        for node_settlement in batch.node_settlements:
            if node_states.get(node_settlement.node_id) != "pending":
                continue
            status = "PASS" if node_settlement.state == "success" else node_settlement.state.upper()
            results.append(
                DagReplayResult(
                    node_id=node_settlement.node_id,
                    attempt=0,
                    terminal_state=node_settlement.state,
                    payload={
                        "node_id": node_settlement.node_id,
                        "status": status,
                        "verdict": status,
                        "attempt_count": 0,
                        "accepted_output": None,
                        "errors": [],
                    },
                )
            )
        apply_transition_state(
            plan=plan,
            batch=batch,
            node_states=node_states,
            edge_states=edge_states,
            terminal_states=terminal_states,
            deadlines=deadlines,
        )
        for receipt in transition_payload.get("receipt_refs", []):
            if not isinstance(receipt, Mapping):
                raise RuntimeError("dag_transition_replay_mismatch")
            path = str(receipt["path"])
            receipts[path] = DagCommittedReceipt(path, str(receipt["file_sha256"]))
        if batch.block_run is not None and block is None:
            block = {
                "status": "BLOCKED",
                "verdict": batch.block_run.failure_code,
                "errors": [batch.block_run.message],
                "transition_evidence": batch.block_run.evidence,
            }
        if event_type != "scheduler_transition_committed":
            continue
        completion = event["payload"].get("completion")
        result = event["payload"].get("result")
        if not isinstance(completion, Mapping) or not isinstance(result, dict):
            raise RuntimeError("dag_transition_replay_mismatch")
        node_id = str(completion["node_id"])
        terminal_state = str(completion["terminal_state"])
        replayed = dict(result)
        if "resumed" in replayed:
            replayed["resumed"] = True
        replayed["durably_replayed"] = True
        node_states[node_id] = "blocked" if batch.block_run is not None else terminal_state
        results.append(
            DagReplayResult(node_id, int(completion["attempt"]), terminal_state, replayed)
        )
        replay_events.append(
            {
                "event": "node_replayed",
                "node_id": node_id,
                "attempt": int(completion["attempt"]),
                "terminal_state": node_states[node_id],
            }
        )
    replay_attempts = tuple(
        DagReplayAttempt(
            stored.identity.node_id,
            stored.identity.attempt,
            stored.identity.attempt_id,
            stored.state,
            stored.effect_state,
        )
        for stored in attempts
    )
    return DagReplayState(
        run_id=run_record.run_id,
        plan=plan,
        journal_sequence=last_sequence,
        run_status=run_record.status,
        run_verdict=run_record.verdict,
        node_states=tuple(sorted(node_states.items())),
        edge_states=tuple(sorted(edge_states.items())),
        terminal_states=tuple(sorted(terminal_states.items())),
        attempts=replay_attempts,
        results=tuple(results),
        runtime_projections=runtime_projections,
        transition_receipts=tuple(receipts.values()),
        replay_events=tuple(replay_events),
        deadline_monotonic=tuple(sorted(deadlines.items())),
        lease_owner=run_record.lease_owner,
        lease_epoch=run_record.lease_epoch,
        lease_expires_at_ms=run_record.lease_expires_at_ms,
        block=block,
    )
