"""Pure replay of the durable Tau DAG journal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, canonical_sha256
from tau_coding.dag_runtime.run_store import (
    RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA,
    DagAttemptIdentity,
    DagJournalEvent,
    DagRunRecord,
    DagRunStoreError,
    SqliteDagRunReader,
    StoredAttempt,
)
from tau_coding.dag_runtime.transition import (
    DagCommittedReceipt,
    DagTransitionBatch,
    transition_batch_from_payload,
)
from tau_coding.runtime_backends.contracts import RuntimeEvent, RuntimeStateProjection


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


@dataclass(frozen=True, slots=True)
class HistoricalReplayResult:
    replay: DagReplayState
    events: tuple[dict[str, Any], ...]
    selected_sequence: int
    selected_event_created_at: str
    head_sequence: int
    view_mode: str


def replay_dag_run_at_sequence(
    reader: SqliteDagRunReader,
    run_id: str,
    at_sequence: int | None,
) -> HistoricalReplayResult:
    """Replay one exact verified run-local journal prefix.

    Mutable projection tables are cross-checked only at the journal head. They
    are never inputs to historical state.
    """

    head_sequence = reader.latest_sequence(run_id)
    if head_sequence < 1:
        raise DagRunStoreError("dag_viewer_journal_empty", run_id)
    selected_sequence = head_sequence if at_sequence is None else at_sequence
    if not reader.event_sequence_belongs_to_run(run_id, selected_sequence):
        raise DagRunStoreError("dag_viewer_sequence_not_in_run", str(selected_sequence))

    journal_events: list[DagJournalEvent] = []
    cursor = 0
    while cursor < selected_sequence:
        page = reader.load_events(
            run_id,
            after_sequence=cursor,
            through_sequence=selected_sequence,
            limit=5000,
        )
        if not page:
            raise DagRunStoreError("dag_viewer_journal_sequence_gap", str(cursor))
        journal_events.extend(page)
        cursor = page[-1].sequence
    if cursor != selected_sequence:
        raise DagRunStoreError("dag_viewer_sequence_not_in_run", str(selected_sequence))

    events = tuple(event.to_mapping() for event in journal_events)
    plan = reader.load_plan(run_id)
    run_record = _run_record_from_prefix(plan=plan, run_id=run_id, events=events)
    attempts = _attempts_from_prefix(run_id=run_id, events=events)
    runtime_projections = _runtime_projections_from_prefix(run_id=run_id, events=events)
    replay = replay_dag_run(
        plan=plan,
        run_record=run_record,
        events=events,
        attempts=attempts,
        runtime_projections=runtime_projections,
    )

    if selected_sequence == head_sequence:
        _cross_check_head(
            reader=reader,
            derived_run=run_record,
            derived_attempts=attempts,
            derived_runtime=runtime_projections,
        )
    return HistoricalReplayResult(
        replay=replay,
        events=events,
        selected_sequence=selected_sequence,
        selected_event_created_at=journal_events[-1].created_at,
        head_sequence=head_sequence,
        view_mode="LIVE" if at_sequence is None else "HISTORICAL",
    )


def _run_record_from_prefix(
    *, plan: DagPlan, run_id: str, events: tuple[dict[str, Any], ...]
) -> DagRunRecord:
    status = "RUNNING"
    verdict: str | None = None
    lease_owner: str | None = None
    lease_epoch = 0
    lease_expires_at_ms: int | None = None
    saw_created = False
    for event in events:
        event_type = str(event["event_type"])
        payload = event["payload"]
        if not isinstance(payload, Mapping):
            raise DagRunStoreError("dag_run_event_invalid", str(event["seq"]))
        if event_type == "run_created":
            if saw_created or payload.get("plan_sha256") != plan.plan_sha256:
                raise DagRunStoreError("dag_run_plan_mismatch", run_id)
            saw_created = True
        elif event_type in {"run_lease_acquired", "run_lease_renewed", "run_lease_taken_over"}:
            lease_owner = str(payload["owner_id"])
            lease_epoch = int(event["lease_epoch"])
            lease_expires_at_ms = int(payload["expires_at_ms"])
        elif event_type == "run_lease_released":
            lease_owner = None
            lease_epoch = int(event["lease_epoch"])
            lease_expires_at_ms = None
        elif event_type == "attempt_effect_uncertain":
            status = "RECONCILIATION_REQUIRED"
            verdict = "DAG_ATTEMPT_EFFECT_UNCERTAIN"
        elif event_type in {"run_completed", "run_blocked"}:
            candidate_status = str(payload.get("status", ""))
            if candidate_status not in {"PASS", "BLOCKED"}:
                raise DagRunStoreError("dag_run_replay_invalid", candidate_status)
            status = candidate_status
            verdict = str(payload["verdict"])
    if not saw_created:
        raise DagRunStoreError("dag_run_replay_invalid", "missing_run_created")
    return DagRunRecord(
        run_id=run_id,
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        status=status,
        verdict=verdict,
        lease_owner=lease_owner,
        lease_epoch=lease_epoch,
        lease_expires_at_ms=lease_expires_at_ms,
    )


def _attempts_from_prefix(
    *, run_id: str, events: tuple[dict[str, Any], ...]
) -> tuple[StoredAttempt, ...]:
    attempts: dict[str, dict[str, Any]] = {}
    states = {
        "attempt_dispatched": "DISPATCHED",
        "attempt_result_staged": "STAGED",
        "attempt_result_validated": "VALIDATED",
        "attempt_retry_scheduled": "RETRY_SCHEDULED",
        "attempt_output_committed": "OUTPUT_COMMITTED",
        "scheduler_transition_committed": "SETTLED",
        "attempt_effect_uncertain": "UNCERTAIN",
    }
    for event in events:
        event_type = str(event["event_type"])
        attempt_id = event.get("attempt_id")
        payload = event["payload"]
        if event_type == "attempt_reserved":
            if not isinstance(payload, Mapping) or not isinstance(attempt_id, str):
                raise DagRunStoreError("dag_attempt_identity_conflict", str(attempt_id))
            if attempt_id in attempts:
                raise DagRunStoreError("dag_attempt_identity_conflict", attempt_id)
            attempts[attempt_id] = {
                "identity": DagAttemptIdentity(
                    run_id=run_id,
                    node_id=str(payload["node_id"]),
                    attempt=int(payload["attempt"]),
                    attempt_id=attempt_id,
                    idempotency_key=str(payload["idempotency_key"]),
                    recovered=True,
                ),
                "state": "RESERVED",
                "effect_state": "NONE",
                "staged_result": None,
                "committed_result": None,
            }
            continue
        if event_type not in states:
            continue
        if not isinstance(attempt_id, str) or attempt_id not in attempts:
            raise DagRunStoreError("dag_attempt_identity_conflict", str(attempt_id))
        attempt = attempts[attempt_id]
        attempt["state"] = states[event_type]
        if event_type == "attempt_result_staged":
            if not isinstance(payload, Mapping) or not isinstance(payload.get("result"), dict):
                raise DagRunStoreError("dag_attempt_result_conflict", attempt_id)
            result = dict(payload["result"])
            if canonical_sha256(result) != payload.get("result_sha256"):
                raise DagRunStoreError("dag_attempt_result_conflict", attempt_id)
            attempt["staged_result"] = result
        elif event_type == "attempt_output_committed":
            staged = attempt["staged_result"]
            if not isinstance(staged, dict) or canonical_sha256(staged) != payload.get(
                "result_sha256"
            ):
                raise DagRunStoreError("dag_attempt_output_hash_mismatch", attempt_id)
            attempt["committed_result"] = staged
        elif event_type == "attempt_effect_uncertain":
            attempt["effect_state"] = "UNCERTAIN"
    return tuple(
        StoredAttempt(**attempt)
        for attempt in sorted(
            attempts.values(), key=lambda item: (item["identity"].attempt, item["identity"].node_id)
        )
    )


def _runtime_projections_from_prefix(
    *, run_id: str, events: tuple[dict[str, Any], ...]
) -> tuple[RuntimeStateProjection, ...]:
    grouped: dict[str, list[RuntimeEvent]] = {}
    for event in events:
        if event["event_type"] != "runtime_event_appended":
            continue
        payload = event["payload"]
        if not isinstance(payload, Mapping) or payload.get(
            "schema"
        ) != RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA:
            raise DagRunStoreError("runtime_event_journal_schema_invalid", str(event["seq"]))
        runtime_payload = payload.get("runtime_event")
        if not isinstance(runtime_payload, dict):
            raise DagRunStoreError("runtime_event_hash_mismatch", str(event["seq"]))
        if canonical_sha256(runtime_payload) != payload.get("runtime_event_sha256"):
            raise DagRunStoreError("runtime_event_hash_mismatch", str(event["seq"]))
        identity_payload = dict(runtime_payload)
        identity_payload.pop("observed_at", None)
        if canonical_sha256(identity_payload) != payload.get("runtime_event_identity_sha256"):
            raise DagRunStoreError("runtime_event_identity_hash_mismatch", str(event["seq"]))
        try:
            runtime_event = RuntimeEvent.from_payload(runtime_payload)
        except (TypeError, ValueError) as exc:
            raise DagRunStoreError("runtime_event_schema_invalid", str(event["seq"])) from exc
        if runtime_event.run_id != run_id:
            raise DagRunStoreError("runtime_event_run_mismatch", runtime_event.event_id)
        if payload.get("endpoint_lease_sha256") != runtime_event.endpoint_lease_sha256:
            raise DagRunStoreError("runtime_event_endpoint_mismatch", runtime_event.event_id)
        if event.get("entity_id") != runtime_event.endpoint_lease_sha256:
            raise DagRunStoreError("runtime_event_endpoint_mismatch", runtime_event.event_id)
        expected_key = f"runtime:{runtime_event.endpoint_lease_sha256}:{runtime_event.event_id}"
        if event.get("event_key") != expected_key:
            raise DagRunStoreError("runtime_event_key_mismatch", runtime_event.event_id)
        transport = runtime_event.observation.to_value().get("transport")
        transport_mode = transport.get("mode") if isinstance(transport, dict) else "unknown"
        if payload.get("transport_mode") != transport_mode:
            raise DagRunStoreError(
                "runtime_event_transport_mode_mismatch", runtime_event.event_id
            )
        grouped.setdefault(runtime_event.endpoint_lease_sha256, []).append(runtime_event)
    return tuple(
        RuntimeStateProjection(
            run_id=run_id,
            endpoint_lease_sha256=endpoint,
            state=values[-1].state,
            liveness=values[-1].liveness,
            confidence=values[-1].confidence,
            last_event_id=values[-1].event_id,
            event_count=len(values),
        )
        for endpoint, values in sorted(grouped.items())
    )


def _cross_check_head(
    *,
    reader: SqliteDagRunReader,
    derived_run: DagRunRecord,
    derived_attempts: tuple[StoredAttempt, ...],
    derived_runtime: tuple[RuntimeStateProjection, ...],
) -> None:
    if reader.load_run_record(derived_run.run_id) != derived_run:
        raise DagRunStoreError("dag_viewer_head_projection_mismatch", "run")
    if reader.load_attempts(derived_run.run_id) != derived_attempts:
        raise DagRunStoreError("dag_viewer_head_projection_mismatch", "attempts")
    if reader.runtime_projections(derived_run.run_id) != derived_runtime:
        raise DagRunStoreError("dag_viewer_head_projection_mismatch", "runtime")


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
