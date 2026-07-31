"""Backend-neutral scheduler state machine for compiled Tau DAG plans."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any

from tau_coding.course_correction import write_course_correction_receipt
from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.correction import CorrectionStateProjection
from tau_coding.dag_runtime.model import (
    DagPlan,
    DagPlanContextBinding,
    DagPlanNode,
    FrozenJson,
    canonical_sha256,
)
from tau_coding.dag_runtime.node_input_manifest import (
    admit_node_input_manifest,
    resolve_node_input_manifest,
)
from tau_coding.dag_runtime.replay import apply_transition_state, replay_dag_run
from tau_coding.dag_runtime.resource_leases import (
    ResourceLeaseDenied,
    ResourceLeaseManager,
    ResourceLeaseToken,
)
from tau_coding.dag_runtime.run_store import (
    DagAttemptIdentity,
    DagRunLease,
    DagRunStoreError,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagNodeCompletion,
    DagPolicyReplayState,
    DagRunBlock,
    DagTransitionBatch,
    DagTransitionPolicy,
    DagTransitionView,
    transition_batch_to_payload,
)
from tau_coding.dag_runtime.worker_assignment import (
    WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
    WorkerAssignmentError,
    WorkerCapability,
    WorkerRegistry,
    compile_worker_requirement,
    normalize_worker_capabilities,
    select_worker,
    worker_resource_requirement,
)
from tau_coding.dag_runtime.workspace_reads import (
    initial_workspace_read_set,
    result_workspace_read_set,
    stale_read_policy,
    stale_read_reconciliations_from_result,
    workspace_changes_from_result,
)
from tau_coding.diagnostics import tau_logger
from tau_coding.node_completion_boundary import (
    NODE_COMPLETION_BOUNDARY_SCHEMA,
    requires_node_completion_boundary,
    validate_node_completion_boundary,
)


@dataclass(frozen=True, slots=True)
class DagNodeAttempt:
    attempt: int
    max_attempts: int
    cancel_event: Event
    run_id: str
    attempt_id: str
    idempotency_key: str
    recovered: bool = False
    input_manifest_path: str | None = None
    input_manifest_sha256: str | None = None
    input_manifest_admission_id: str | None = None
    worker_assignment_path: str | None = None
    worker_assignment_sha256: str | None = None
    worker_assignment_admission_id: str | None = None


NodeExecutor = Callable[
    [DagPlanNode, tuple[dict[str, Any], ...], DagNodeAttempt],
    dict[str, Any],
]
EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class DagCorrectionRequest:
    plan: DagPlan
    node: DagPlanNode
    result: Mapping[str, Any]
    attempt: DagNodeAttempt
    run_store: SqliteDagRunStore
    lease: DagRunLease


CorrectionHandler = Callable[[DagCorrectionRequest], CorrectionStateProjection]


@dataclass(frozen=True, slots=True)
class DagSchedulerResult:
    status: str
    verdict: str
    node_results: tuple[dict[str, Any], ...]
    completed_node_ids: tuple[str, ...]
    max_observed_concurrency: int
    edge_states: tuple[tuple[str, str], ...]
    terminal_states: tuple[tuple[str, str], ...]
    node_states: tuple[tuple[str, str], ...]
    transition_receipt_paths: tuple[str, ...]
    durable: bool = False
    run_id: str | None = None
    lease_epoch: int | None = None
    replayed_event_count: int = 0


def run_dag_plan(
    plan: DagPlan,
    *,
    execute_node: NodeExecutor,
    transition_policy: DagTransitionPolicy | None = None,
    max_concurrency: int = 1,
    event_sink: EventSink | None = None,
    run_store: SqliteDagRunStore | None = None,
    run_id: str | None = None,
    lease_owner: str | None = None,
    allow_lease_takeover: bool = False,
    lease_ttl_seconds: float = 15.0,
    fault_injector: Callable[[str, Mapping[str, Any]], None] | None = None,
    on_lease_acquired: Callable[[DagRunLease], None] | None = None,
    correction_handler: CorrectionHandler | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    resource_lease_manager: ResourceLeaseManager | None = None,
    resource_lease_ttl_seconds: float = 15.0,
    worker_registry: (
        WorkerRegistry | tuple[WorkerCapability | Mapping[str, Any], ...] | None
    ) = None,
) -> DagSchedulerResult:
    """Execute an all-success DagPlan through one bounded ready queue.

    Route and join policies other than the generic all-success policy remain
    fail closed until their project adapters are moved onto this state machine.
    """

    if max_concurrency < 1:
        raise RuntimeError("max_concurrency must be at least 1")
    if worker_registry is not None and (run_store is None or resource_lease_manager is None):
        raise RuntimeError("worker assignment requires run_store and resource_lease_manager")
    policy = transition_policy or AllSuccessTransitionPolicy()
    policy.validate_plan(plan)
    effective_run_id = run_id or plan.plan_id
    lease: DagRunLease | None = None
    replayed_event_count = 0
    persisted_outcome: tuple[str, str | None] | None = None
    lease_renewal_interval = max(0.001, lease_ttl_seconds / 3.0)
    next_lease_renewal = time.monotonic() + lease_renewal_interval
    if run_store is not None:
        persisted_outcome = run_store.run_outcome(effective_run_id)
        lease = run_store.acquire_run(
            plan=plan,
            run_id=effective_run_id,
            owner_id=lease_owner or f"tau-scheduler-{uuid.uuid4().hex}",
            ttl_seconds=lease_ttl_seconds,
            allow_takeover=allow_lease_takeover,
        )
        if on_lease_acquired is not None:
            try:
                on_lease_acquired(lease)
            except Exception as exc:
                _finish_interrupted_run(
                    run_store=run_store,
                    lease=lease,
                    exc=exc,
                    terminalize_exception=True,
                )
                raise

    declared_terminal_nodes = {
        terminal.terminal_id
        for terminal in plan.terminal_endpoints
        if terminal.kind == "declared_node"
    }
    nodes = {
        node.node_id: node for node in plan.nodes if node.node_id not in declared_terminal_nodes
    }
    incoming_edges = _incoming_edges(plan, node_ids=set(nodes))
    context_bindings = _context_bindings_by_target(plan)
    edge_states: dict[str, str] = {}
    terminal_states: dict[str, str] = {}
    node_states = {node_id: "pending" for node_id in nodes}
    completed: set[str] = set()
    resolved: set[str] = set()
    results: dict[str, dict[str, Any]] = {}
    result_order: list[str] = []
    scheduled: set[str] = set()
    cancel_events = {node_id: Event() for node_id in nodes}
    attempt_counts = {node_id: 0 for node_id in nodes}
    attempt_history: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    max_observed_concurrency = (
        run_store.max_observed_concurrency(effective_run_id) if run_store is not None else 0
    )
    blocked_result: dict[str, Any] | None = None
    transition_receipt_paths: list[str] = []
    deadlines: dict[str, float] = {}

    if run_store is not None and lease is not None:
        uncertain = run_store.mark_dispatched_attempts_uncertain(lease)
        if uncertain:
            first = uncertain[0]
            uncertain_result = DagSchedulerResult(
                status="BLOCKED",
                verdict="DAG_ATTEMPT_EFFECT_UNCERTAIN",
                node_results=(),
                completed_node_ids=(),
                max_observed_concurrency=0,
                edge_states=(),
                terminal_states=(),
                node_states=tuple(sorted(node_states.items())),
                transition_receipt_paths=(),
                durable=True,
                run_id=effective_run_id,
                lease_epoch=lease.epoch,
                replayed_event_count=len(run_store.load_events(effective_run_id)),
            )
            _emit(
                event_sink,
                {
                    "event": "scheduler_reconciliation_required",
                    "attempt_id": first.identity.attempt_id,
                    "node_id": first.identity.node_id,
                    "attempt": first.identity.attempt,
                    "idempotency_key": first.identity.idempotency_key,
                },
            )
            run_store.release_lease(lease)
            return uncertain_result
        try:
            replayed_event_count, replayed_block = _restore_durable_state(
                plan=plan,
                policy=policy,
                run_store=run_store,
                run_id=effective_run_id,
                nodes=nodes,
                node_states=node_states,
                edge_states=edge_states,
                terminal_states=terminal_states,
                deadlines=deadlines,
                completed=completed,
                resolved=resolved,
                results=results,
                result_order=result_order,
                scheduled=scheduled,
                cancel_events=cancel_events,
                attempt_counts=attempt_counts,
                attempt_history=attempt_history,
                transition_receipt_paths=transition_receipt_paths,
                event_sink=event_sink,
            )
        except RuntimeError as exc:
            failure_code = str(exc).split(":", 1)[0]
            verdict = failure_code.upper()
            if persisted_outcome is None or persisted_outcome[0] == "RUNNING":
                run_store.mark_run_finished(lease, status="BLOCKED", verdict=verdict)
            run_store.release_lease(lease)
            return DagSchedulerResult(
                status="BLOCKED",
                verdict=verdict,
                node_results=(),
                completed_node_ids=(),
                max_observed_concurrency=max_observed_concurrency,
                edge_states=tuple(sorted(edge_states.items())),
                terminal_states=tuple(sorted(terminal_states.items())),
                node_states=tuple(sorted(node_states.items())),
                transition_receipt_paths=tuple(transition_receipt_paths),
                durable=True,
                run_id=effective_run_id,
                lease_epoch=lease.epoch,
                replayed_event_count=len(run_store.load_events(effective_run_id)),
            )
        for stored in run_store.list_attempts(effective_run_id):
            observed_attempt = (
                stored.identity.attempt - 1
                if stored.state == "RESERVED"
                else stored.identity.attempt
            )
            attempt_counts[stored.identity.node_id] = max(
                attempt_counts.get(stored.identity.node_id, 0), observed_attempt
            )
        recovery_block = _recover_incomplete_attempts(
            plan=plan,
            policy=policy,
            run_store=run_store,
            lease=lease,
            nodes=nodes,
            node_states=node_states,
            edge_states=edge_states,
            terminal_states=terminal_states,
            deadlines=deadlines,
            completed=completed,
            resolved=resolved,
            results=results,
            result_order=result_order,
            scheduled=scheduled,
            cancel_events=cancel_events,
            attempt_counts=attempt_counts,
            attempt_history=attempt_history,
            transition_receipt_paths=transition_receipt_paths,
            event_sink=event_sink,
            fault_injector=fault_injector,
            correction_handler=correction_handler,
        )
        blocked_result = replayed_block or recovery_block

    _emit(event_sink, {"event": "scheduler_started", "plan_id": plan.plan_id})
    try:
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            futures: dict[Future[dict[str, Any]], str] = {}
            future_attempts: dict[Future[dict[str, Any]], DagAttemptIdentity] = {}
            future_resource_leases: dict[
                Future[dict[str, Any]], tuple[ResourceLeaseToken, ...]
            ] = {}
            while len(resolved) < len(nodes):
                if (
                    run_store is not None
                    and lease is not None
                    and time.monotonic() >= next_lease_renewal
                ):
                    lease = run_store.renew_lease(lease, ttl_seconds=lease_ttl_seconds)
                    next_lease_renewal = time.monotonic() + lease_renewal_interval
                if cancel_requested is not None and cancel_requested():
                    blocked_result = {
                        "status": "CANCELLED",
                        "verdict": "CANCELLED",
                        "errors": ["DAG run cancelled by operator request"],
                    }
                    lease = _cancel_and_collect_futures(
                        futures=futures,
                        future_attempts=future_attempts,
                        cancel_events=cancel_events,
                        results=results,
                        result_order=result_order,
                        node_states=node_states,
                        resolved=resolved,
                        event_sink=event_sink,
                        run_store=run_store,
                        lease=lease,
                        lease_ttl_seconds=lease_ttl_seconds,
                        lease_renewal_interval=lease_renewal_interval,
                    )
                    break
                if blocked_result is not None:
                    break
                settle_block = _settle_unrunnable_nodes(
                    plan=plan,
                    policy=policy,
                    nodes=nodes,
                    resolved=resolved,
                    scheduled=scheduled,
                    completed=completed,
                    results=results,
                    result_order=result_order,
                    node_states=node_states,
                    edge_states=edge_states,
                    terminal_states=terminal_states,
                    deadlines=deadlines,
                    cancel_events=cancel_events,
                    futures=futures,
                    transition_receipt_paths=transition_receipt_paths,
                    event_sink=event_sink,
                    run_store=run_store,
                    lease=lease,
                )
                if settle_block is not None:
                    blocked_result = {
                        "status": "BLOCKED",
                        "verdict": settle_block.failure_code,
                        "errors": [settle_block.message],
                        "transition_evidence": settle_block.evidence,
                    }
                    lease = _cancel_and_collect_futures(
                        futures=futures,
                        future_attempts=future_attempts,
                        cancel_events=cancel_events,
                        results=results,
                        result_order=result_order,
                        node_states=node_states,
                        resolved=resolved,
                        event_sink=event_sink,
                        run_store=run_store,
                        lease=lease,
                        lease_ttl_seconds=lease_ttl_seconds,
                        lease_renewal_interval=lease_renewal_interval,
                    )
                    break
                ready = [
                    node_id
                    for node_id in sorted(nodes)
                    if node_id not in resolved
                    and node_id not in scheduled
                    and _node_is_ready(
                        node_id,
                        incoming_edges=incoming_edges,
                        edge_states=edge_states,
                    )
                ]
                for node_id in ready:
                    if len(futures) >= max_concurrency:
                        break
                    attempt_counts[node_id] += 1
                    attempt = attempt_counts[node_id]
                    start_transition = policy.before_node_start(
                        _transition_view(
                            plan=plan,
                            node_states=node_states,
                            edge_states=edge_states,
                            terminal_states=terminal_states,
                            running_node_ids=set(futures.values()),
                            deadlines=deadlines,
                        ),
                        node_id,
                        attempt,
                    )
                    _persist_control_transition(
                        run_store=run_store,
                        lease=lease,
                        event_key=f"before-node:{node_id}:{attempt}",
                        batch=start_transition,
                    )
                    _apply_transition_batch(
                        plan=plan,
                        batch=start_transition,
                        edge_states=edge_states,
                        terminal_states=terminal_states,
                        deadlines=deadlines,
                    )
                    _apply_node_effects(
                        batch=start_transition,
                        nodes=nodes,
                        node_states=node_states,
                        resolved=resolved,
                        completed=completed,
                        results=results,
                        result_order=result_order,
                        scheduled=scheduled,
                        cancel_events=cancel_events,
                        futures=futures,
                        event_sink=event_sink,
                    )
                    transition_receipt_paths.extend(start_transition.receipt_paths)
                    for transition_event in start_transition.events:
                        _emit(event_sink, transition_event)
                    if start_transition.block_run is not None:
                        blocked_result = {
                            "status": "BLOCKED",
                            "verdict": start_transition.block_run.failure_code,
                            "errors": [start_transition.block_run.message],
                        }
                        break
                    if node_id in resolved or node_id in scheduled:
                        continue
                    worker_admission: dict[str, Any] | None = None
                    if run_store is not None and lease is not None:
                        identity = run_store.reserve_attempt(
                            lease,
                            plan_sha256=plan.plan_sha256,
                            node_id=node_id,
                            attempt=attempt,
                        )
                        _inject_fault(fault_injector, "after_attempt_reserved", identity)
                        initial_read_set = initial_workspace_read_set(
                            plan=plan,
                            node=nodes[node_id],
                            run_id=effective_run_id,
                            attempt_id=identity.attempt_id,
                            attempt=identity.attempt,
                        )
                        if initial_read_set["entry_count"]:
                            run_store.record_workspace_read_set(
                                lease,
                                identity.attempt_id,
                                initial_read_set,
                            )
                        input_resolution = resolve_node_input_manifest(
                            plan=plan,
                            node=nodes[node_id],
                            identity=identity,
                            bindings=context_bindings.get(node_id, ()),
                            edge_states=edge_states,
                            results=results,
                            run_store=run_store,
                        )
                        input_admission = admit_node_input_manifest(
                            run_store=run_store,
                            lease=lease,
                            identity=identity,
                            manifest=input_resolution.manifest,
                        )
                        _inject_fault(fault_injector, "after_input_manifest_admitted", identity)
                        accepted_inputs = input_resolution.accepted_inputs
                        if input_resolution.blocked_result is not None:
                            run_store.mark_dispatched(lease, identity.attempt_id)
                            future = pool.submit(_return_result, input_resolution.blocked_result)
                            futures[future] = node_id
                            future_attempts[future] = identity
                            future_resource_leases[future] = ()
                            scheduled.add(node_id)
                            node_states[node_id] = "running"
                            _emit(
                                event_sink,
                                {
                                    "event": "node_input_blocked",
                                    "node_id": node_id,
                                    "attempt": attempt,
                                    "verdict": input_resolution.blocked_result["verdict"],
                                    "input_manifest_sha256": input_admission["sha256"],
                                },
                            )
                            continue
                        try:
                            worker_tokens, worker_admission = _assign_worker_for_attempt(
                                plan=plan,
                                node=nodes[node_id],
                                run_id=effective_run_id,
                                identity=identity,
                                run_store=run_store,
                                lease=lease,
                                resource_lease_manager=resource_lease_manager,
                                resource_lease_ttl_seconds=resource_lease_ttl_seconds,
                                worker_registry=worker_registry,
                            )
                            resource_tokens = (
                                resource_lease_manager.acquire_for_attempt(
                                    node=nodes[node_id],
                                    run_id=effective_run_id,
                                    attempt_id=identity.attempt_id,
                                    ttl_seconds=resource_lease_ttl_seconds,
                                    run_store=run_store,
                                    scheduler_lease=lease,
                                )
                                if resource_lease_manager is not None
                                else ()
                            )
                            resource_tokens = (*worker_tokens, *resource_tokens)
                        except (ResourceLeaseDenied, WorkerAssignmentError) as exc:
                            blocked_result = {
                                "node_id": node_id,
                                "status": "BLOCKED",
                                "verdict": exc.code.upper(),
                                "errors": [str(exc)],
                            }
                            node_states[node_id] = "blocked"
                            resolved.add(node_id)
                            break
                        run_store.mark_dispatched(lease, identity.attempt_id)
                        _inject_fault(fault_injector, "after_attempt_dispatched", identity)
                    else:
                        identity = DagAttemptIdentity(
                            run_id=effective_run_id,
                            node_id=node_id,
                            attempt=attempt,
                            attempt_id=f"{effective_run_id}:{node_id}:{attempt}",
                            idempotency_key=f"{effective_run_id}:{node_id}:{attempt}:effect",
                        )
                        resource_tokens = ()
                        input_resolution = resolve_node_input_manifest(
                            plan=plan,
                            node=nodes[node_id],
                            identity=identity,
                            bindings=context_bindings.get(node_id, ()),
                            edge_states=edge_states,
                            results=results,
                            run_store=None,
                        )
                        accepted_inputs = input_resolution.accepted_inputs
                        input_admission = None
                        if input_resolution.blocked_result is not None:
                            future = pool.submit(_return_result, input_resolution.blocked_result)
                            futures[future] = node_id
                            future_attempts[future] = identity
                            future_resource_leases[future] = ()
                            scheduled.add(node_id)
                            node_states[node_id] = "running"
                            _emit(
                                event_sink,
                                {
                                    "event": "node_input_blocked",
                                    "node_id": node_id,
                                    "attempt": attempt,
                                    "verdict": input_resolution.blocked_result["verdict"],
                                },
                            )
                            continue
                    input_manifest_path = (
                        str(input_admission["path"]) if isinstance(input_admission, dict) else None
                    )
                    input_manifest_sha256 = (
                        str(input_admission["sha256"])
                        if isinstance(input_admission, dict)
                        else None
                    )
                    input_manifest_admission_id = (
                        str(input_admission["admission_id"])
                        if isinstance(input_admission, dict)
                        else None
                    )
                    worker_assignment_path = (
                        str(worker_admission["path"])
                        if isinstance(worker_admission, dict)
                        else None
                    )
                    worker_assignment_sha256 = (
                        str(worker_admission["sha256"])
                        if isinstance(worker_admission, dict)
                        else None
                    )
                    worker_assignment_admission_id = (
                        str(worker_admission["admission_id"])
                        if isinstance(worker_admission, dict)
                        else None
                    )
                    future = pool.submit(
                        execute_node,
                        nodes[node_id],
                        accepted_inputs,
                        DagNodeAttempt(
                            attempt=attempt,
                            max_attempts=nodes[node_id].max_attempts,
                            cancel_event=cancel_events[node_id],
                            run_id=effective_run_id,
                            attempt_id=identity.attempt_id,
                            idempotency_key=identity.idempotency_key,
                            recovered=identity.recovered,
                            input_manifest_path=input_manifest_path,
                            input_manifest_sha256=input_manifest_sha256,
                            input_manifest_admission_id=input_manifest_admission_id,
                            worker_assignment_path=worker_assignment_path,
                            worker_assignment_sha256=worker_assignment_sha256,
                            worker_assignment_admission_id=worker_assignment_admission_id,
                        ),
                    )
                    futures[future] = node_id
                    future_attempts[future] = identity
                    future_resource_leases[future] = resource_tokens
                    scheduled.add(node_id)
                    node_states[node_id] = "running"
                    _emit(
                        event_sink,
                        {"event": "node_started", "node_id": node_id, "attempt": attempt},
                    )
                observed_concurrency = len(futures)
                if observed_concurrency > max_observed_concurrency:
                    max_observed_concurrency = observed_concurrency
                    if run_store is not None and lease is not None:
                        run_store.record_observed_concurrency(lease, max_observed_concurrency)

                if blocked_result is not None:
                    lease = _cancel_and_collect_futures(
                        futures=futures,
                        future_attempts=future_attempts,
                        cancel_events=cancel_events,
                        results=results,
                        result_order=result_order,
                        node_states=node_states,
                        resolved=resolved,
                        event_sink=event_sink,
                        run_store=run_store,
                        lease=lease,
                        lease_ttl_seconds=lease_ttl_seconds,
                        lease_renewal_interval=lease_renewal_interval,
                    )
                    break
                if not futures:
                    if len(resolved) == len(nodes):
                        break
                    if deadlines:
                        next_deadline = min(deadlines.values())
                        remaining_seconds = next_deadline - time.monotonic()
                        if remaining_seconds > 0:
                            time.sleep(min(remaining_seconds, 0.05))
                            continue
                        for deadline_id in sorted(
                            key for key, value in deadlines.items() if value <= time.monotonic()
                        ):
                            transition = policy.on_deadline(
                                _transition_view(
                                    plan=plan,
                                    node_states=node_states,
                                    edge_states=edge_states,
                                    terminal_states=terminal_states,
                                    running_node_ids=set(),
                                    deadlines=deadlines,
                                ),
                                deadline_id,
                            )
                            _persist_control_transition(
                                run_store=run_store,
                                lease=lease,
                                event_key=f"deadline:{deadline_id}",
                                batch=transition,
                            )
                            _apply_transition_batch(
                                plan=plan,
                                batch=transition,
                                edge_states=edge_states,
                                terminal_states=terminal_states,
                                deadlines=deadlines,
                            )
                            _apply_node_effects(
                                batch=transition,
                                nodes=nodes,
                                node_states=node_states,
                                resolved=resolved,
                                completed=completed,
                                results=results,
                                result_order=result_order,
                                scheduled=scheduled,
                                cancel_events=cancel_events,
                                futures=futures,
                                event_sink=event_sink,
                            )
                            transition_receipt_paths.extend(transition.receipt_paths)
                            for event in transition.events:
                                _emit(event_sink, event)
                            if transition.block_run is not None:
                                blocked_result = {
                                    "status": "BLOCKED",
                                    "verdict": transition.block_run.failure_code,
                                    "errors": [transition.block_run.message],
                                }
                        if blocked_result is not None:
                            break
                        continue
                    remaining = sorted(set(nodes) - completed)
                    blocked_result = {
                        "status": "BLOCKED",
                        "verdict": "READY_QUEUE_STALLED",
                        "errors": [f"no node became ready: {', '.join(remaining)}"],
                    }
                    break

                wait_timeout = None
                if deadlines:
                    wait_timeout = max(0.0, min(deadlines.values()) - time.monotonic())
                if run_store is not None and lease is not None:
                    lease_wait = max(0.0, next_lease_renewal - time.monotonic())
                    wait_timeout = (
                        lease_wait if wait_timeout is None else min(wait_timeout, lease_wait)
                    )
                if cancel_requested is not None:
                    wait_timeout = 0.05 if wait_timeout is None else min(wait_timeout, 0.05)
                done, _ = wait(futures, timeout=wait_timeout, return_when=FIRST_COMPLETED)
                if not done:
                    for deadline_id in sorted(
                        key for key, value in deadlines.items() if value <= time.monotonic()
                    ):
                        transition = policy.on_deadline(
                            _transition_view(
                                plan=plan,
                                node_states=node_states,
                                edge_states=edge_states,
                                terminal_states=terminal_states,
                                running_node_ids=set(futures.values()),
                                deadlines=deadlines,
                            ),
                            deadline_id,
                        )
                        _persist_control_transition(
                            run_store=run_store,
                            lease=lease,
                            event_key=f"deadline:{deadline_id}",
                            batch=transition,
                        )
                        _apply_transition_batch(
                            plan=plan,
                            batch=transition,
                            edge_states=edge_states,
                            terminal_states=terminal_states,
                            deadlines=deadlines,
                        )
                        _apply_node_effects(
                            batch=transition,
                            nodes=nodes,
                            node_states=node_states,
                            resolved=resolved,
                            completed=completed,
                            results=results,
                            result_order=result_order,
                            scheduled=scheduled,
                            cancel_events=cancel_events,
                            futures=futures,
                            event_sink=event_sink,
                        )
                        transition_receipt_paths.extend(transition.receipt_paths)
                        for event in transition.events:
                            _emit(event_sink, event)
                        if transition.block_run is not None:
                            blocked_result = {
                                "status": "BLOCKED",
                                "verdict": transition.block_run.failure_code,
                                "errors": [transition.block_run.message],
                            }
                    if blocked_result is not None:
                        lease = _cancel_and_collect_futures(
                            futures=futures,
                            future_attempts=future_attempts,
                            cancel_events=cancel_events,
                            results=results,
                            result_order=result_order,
                            node_states=node_states,
                            resolved=resolved,
                            event_sink=event_sink,
                            run_store=run_store,
                            lease=lease,
                            lease_ttl_seconds=lease_ttl_seconds,
                            lease_renewal_interval=lease_renewal_interval,
                        )
                        break
                    continue
                if (
                    run_store is not None
                    and lease is not None
                    and (
                        time.monotonic() >= next_lease_renewal
                        or _lease_close_to_expiry(lease, lease_ttl_seconds)
                    )
                ):
                    lease = run_store.renew_lease(lease, ttl_seconds=lease_ttl_seconds)
                    next_lease_renewal = time.monotonic() + lease_renewal_interval
                completed_batch: list[tuple[str, DagAttemptIdentity, dict[str, Any]]] = []
                for future in done:
                    node_id = futures.pop(future)
                    identity = future_attempts.pop(future)
                    resource_tokens = future_resource_leases.pop(future, ())
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive adapter boundary.
                        tau_logger(
                            run_id=identity.run_id,
                            node_id=identity.node_id,
                            attempt=identity.attempt,
                            attempt_id=identity.attempt_id,
                            idempotency_key=identity.idempotency_key,
                        ).exception("dag_node_future_exception")
                        result = {
                            "node_id": node_id,
                            "status": "BLOCKED",
                            "verdict": "ADAPTER_EXECUTION_FAILED",
                            "errors": [str(exc)],
                        }
                    if resource_lease_manager is not None and resource_tokens:
                        resource_lease_manager.release(
                            resource_tokens,
                            run_store=run_store,
                            scheduler_lease=lease,
                        )
                    completed_batch.append((node_id, identity, result))

                batch_blocked = False
                for node_id, identity, result in sorted(completed_batch):
                    attempt = attempt_counts[node_id]
                    result = _enforce_node_completion_boundary(
                        plan=plan,
                        node=nodes[node_id],
                        identity=identity,
                        result=result,
                        run_store=run_store,
                        lease=lease,
                    )
                    result = _apply_workspace_stale_read_observations(
                        plan=plan,
                        node=nodes[node_id],
                        identity=identity,
                        result=result,
                        run_store=run_store,
                        lease=lease,
                    )
                    result = _enforce_workspace_stale_read_policy(
                        node=nodes[node_id],
                        identity=identity,
                        result=result,
                        run_store=run_store,
                    )
                    try:
                        validation = _validate_attempt_result(node_id=node_id, result=result)
                    except RuntimeError as exc:
                        result = {
                            "node_id": node_id,
                            "status": "BLOCKED",
                            "verdict": "DAG_ATTEMPT_RESULT_INVALID",
                            "errors": [str(exc)],
                            "retryable": False,
                        }
                        validation = _validate_attempt_result(node_id=node_id, result=result)
                    raw_attempt_result = result
                    repeated_signature = _repeated_failure_signature(
                        current=result,
                        prior_results=attempt_history[node_id],
                    )
                    if repeated_signature is not None and attempt < nodes[node_id].max_attempts:
                        result = _with_same_failure_course_correction(
                            result,
                            plan=plan,
                            node=nodes[node_id],
                            identity=identity,
                            attempt=attempt,
                            repeated_signature=repeated_signature,
                        )
                        raw_attempt_result = result
                    if run_store is not None and lease is not None:
                        result = run_store.stage_result(lease, identity.attempt_id, result)
                        _inject_fault(fault_injector, "after_result_staged", identity)
                        run_store.validate_result(lease, identity.attempt_id, validation)
                        _inject_fault(fault_injector, "after_result_validated", identity)
                    result = _with_attempt_history(
                        result,
                        attempt=attempt,
                        prior_results=attempt_history[node_id],
                    )
                    result.setdefault("scheduler_attempt_id", identity.attempt_id)
                    result.setdefault("scheduler_attempt", identity.attempt)
                    attempt_history[node_id].append(raw_attempt_result)
                    retryable = result.get("retryable") is not False
                    scheduler_cancelled = cancel_events[node_id].is_set()
                    failed_attempt = (
                        result.get("status") != "PASS" or result.get("verdict") != "PASS"
                    )
                    correction_allows_retry = _correction_allows_retry(
                        plan=plan,
                        node=nodes[node_id],
                        result=result,
                        attempt=DagNodeAttempt(
                            attempt=attempt,
                            max_attempts=nodes[node_id].max_attempts,
                            cancel_event=cancel_events[node_id],
                            run_id=effective_run_id,
                            attempt_id=identity.attempt_id,
                            idempotency_key=identity.idempotency_key,
                        ),
                        run_store=run_store,
                        lease=lease,
                        correction_handler=correction_handler,
                        event_sink=event_sink,
                    )
                    will_retry = (
                        retryable
                        and correction_allows_retry
                        and not scheduler_cancelled
                        and attempt < nodes[node_id].max_attempts
                    )
                    if failed_attempt:
                        _emit(
                            event_sink,
                            {
                                "event": "node_attempt_failed",
                                "node_id": node_id,
                                "attempt": attempt,
                                "retrying": will_retry,
                                "stop_reason": result.get("stop_reason")
                                or str(result.get("verdict") or "node_blocked").lower(),
                                "errors": result.get("errors", []),
                            },
                        )
                    if failed_attempt and will_retry:
                        if run_store is not None and lease is not None:
                            run_store.schedule_retry(
                                lease, identity.attempt_id, next_attempt=attempt + 1
                            )
                            _inject_fault(fault_injector, "after_retry_scheduled", identity)
                        scheduled.remove(node_id)
                        node_states[node_id] = "pending"
                        _emit(
                            event_sink,
                            {
                                "event": "node_retry_scheduled",
                                "node_id": node_id,
                                "attempt": attempt,
                                "next_attempt": attempt + 1,
                                "verdict": result.get("verdict"),
                            },
                        )
                        continue
                    if run_store is not None and lease is not None:
                        admitted = _admit_result_receipt(
                            run_store, lease, identity.attempt_id, result
                        )
                        if not admitted:
                            _classify_unadmitted_result(
                                run_store, lease, identity.attempt_id, result
                            )
                            # Enforcement (#207, contract section 4): a node
                            # that would settle accepted without an admitted
                            # receipt settles BLOCKED through the scheduler's
                            # trusted path instead. Observation mode measured
                            # zero remaining bypasses before this flip.
                            if (
                                result.get("status") == "PASS"
                                and result.get("verdict") == "PASS"
                                and isinstance(result.get("receipt_path"), str)
                            ):
                                result = _enforce_admission_block(
                                    run_store, lease, identity.attempt_id, result
                                )
                        run_store.commit_output(lease, identity.attempt_id)
                        _inject_fault(fault_injector, "after_output_committed", identity)
                    completion = DagNodeCompletion(
                        node_id=node_id,
                        attempt=attempt,
                        status=str(result.get("status") or "BLOCKED"),
                        verdict=str(result.get("verdict") or "NODE_BLOCKED"),
                        retryable=retryable,
                        raw_result=result,
                        terminal_state=(
                            "cancelled"
                            if cancel_events[node_id].is_set()
                            else "success"
                            if result.get("status") == "PASS" and result.get("verdict") == "PASS"
                            else "failed"
                        ),
                    )
                    transition = policy.after_node_terminal(
                        _transition_view(
                            plan=plan,
                            node_states=node_states,
                            edge_states=edge_states,
                            terminal_states=terminal_states,
                            running_node_ids=set(futures.values()),
                            deadlines=deadlines,
                        ),
                        completion,
                    )
                    if scheduler_cancelled and transition.block_run is not None:
                        transition = replace(transition, block_run=None)
                    if run_store is not None and lease is not None:
                        run_store.commit_transition(
                            lease,
                            identity.attempt_id,
                            completion=_completion_to_payload(completion),
                            result=result,
                            transition=transition_batch_to_payload(transition),
                        )
                        _inject_fault(fault_injector, "after_transition_committed", identity)
                    results[node_id] = result
                    result_order.append(node_id)
                    _apply_transition_batch(
                        plan=plan,
                        batch=transition,
                        edge_states=edge_states,
                        terminal_states=terminal_states,
                        deadlines=deadlines,
                    )
                    _apply_node_effects(
                        batch=transition,
                        nodes=nodes,
                        node_states=node_states,
                        resolved=resolved,
                        completed=completed,
                        results=results,
                        result_order=result_order,
                        scheduled=scheduled,
                        cancel_events=cancel_events,
                        futures=futures,
                        event_sink=event_sink,
                    )
                    transition_receipt_paths.extend(transition.receipt_paths)
                    for transition_event in transition.events:
                        _emit(event_sink, transition_event)
                    if transition.block_run is not None:
                        if blocked_result is None:
                            blocked_result = {
                                **result,
                                "status": "BLOCKED",
                                "verdict": transition.block_run.failure_code,
                                "errors": [transition.block_run.message],
                                "transition_evidence": transition.block_run.evidence,
                            }
                        node_states[node_id] = "blocked"
                        resolved.add(node_id)
                        _emit(
                            event_sink,
                            {
                                "event": "node_blocked",
                                "node_id": node_id,
                                "attempt": attempt,
                                "verdict": result.get("verdict"),
                            },
                        )
                        batch_blocked = True
                        continue
                    resolved.add(node_id)
                    node_states[node_id] = completion.terminal_state
                    if completion.terminal_state == "success":
                        completed.add(node_id)
                    _emit(
                        event_sink,
                        {"event": "node_completed", "node_id": node_id, "attempt": attempt},
                    )
                completion_transition = policy.after_completion_batch(
                    _transition_view(
                        plan=plan,
                        node_states=node_states,
                        edge_states=edge_states,
                        terminal_states=terminal_states,
                        running_node_ids=set(futures.values()),
                        deadlines=deadlines,
                    )
                )
                _persist_control_transition(
                    run_store=run_store,
                    lease=lease,
                    event_key="completion-batch",
                    batch=completion_transition,
                )
                _apply_transition_batch(
                    plan=plan,
                    batch=completion_transition,
                    edge_states=edge_states,
                    terminal_states=terminal_states,
                    deadlines=deadlines,
                )
                _apply_node_effects(
                    batch=completion_transition,
                    nodes=nodes,
                    node_states=node_states,
                    resolved=resolved,
                    completed=completed,
                    results=results,
                    result_order=result_order,
                    scheduled=scheduled,
                    cancel_events=cancel_events,
                    futures=futures,
                    event_sink=event_sink,
                )
                transition_receipt_paths.extend(completion_transition.receipt_paths)
                for transition_event in completion_transition.events:
                    _emit(event_sink, transition_event)
                if completion_transition.block_run is not None:
                    if blocked_result is None:
                        blocked_result = {
                            "status": "BLOCKED",
                            "verdict": completion_transition.block_run.failure_code,
                            "errors": [completion_transition.block_run.message],
                            "transition_evidence": completion_transition.block_run.evidence,
                        }
                    batch_blocked = True
                if batch_blocked:
                    lease = _cancel_and_collect_futures(
                        futures=futures,
                        future_attempts=future_attempts,
                        cancel_events=cancel_events,
                        results=results,
                        result_order=result_order,
                        node_states=node_states,
                        resolved=resolved,
                        event_sink=event_sink,
                        run_store=run_store,
                        lease=lease,
                        lease_ttl_seconds=lease_ttl_seconds,
                        lease_renewal_interval=lease_renewal_interval,
                    )
                    break

    except BaseException as exc:
        _finish_interrupted_run(
            run_store=run_store,
            lease=lease,
            exc=exc,
            terminalize_exception=False,
        )
        raise

    ordered_results = tuple(results[node_id] for node_id in result_order)
    if (
        blocked_result is None
        and persisted_outcome is not None
        and persisted_outcome[0] == "BLOCKED"
    ):
        blocked_result = {
            "status": "BLOCKED",
            "verdict": persisted_outcome[1] or "NODE_BLOCKED",
            "errors": ["blocked verdict restored from durable run state"],
        }
    if blocked_result is not None:
        verdict = str(blocked_result.get("verdict") or "NODE_BLOCKED")
        status = "CANCELLED" if blocked_result.get("status") == "CANCELLED" else "BLOCKED"
    else:
        verdict = "PASS"
        status = "PASS"
    _emit(
        event_sink,
        {
            "event": "scheduler_finished",
            "plan_id": plan.plan_id,
            "status": status,
            "verdict": verdict,
        },
    )
    scheduler_result = DagSchedulerResult(
        status=status,
        verdict=verdict,
        node_results=ordered_results,
        completed_node_ids=tuple(sorted(completed)),
        max_observed_concurrency=max_observed_concurrency,
        edge_states=tuple(sorted(edge_states.items())),
        terminal_states=tuple(sorted(terminal_states.items())),
        node_states=tuple(sorted(node_states.items())),
        transition_receipt_paths=tuple(transition_receipt_paths),
        durable=run_store is not None,
        run_id=effective_run_id if run_store is not None else None,
        lease_epoch=lease.epoch if lease is not None else None,
        replayed_event_count=replayed_event_count,
    )
    if run_store is not None and lease is not None:
        run_store.mark_run_finished(lease, status=status, verdict=verdict)
        _inject_fault(
            fault_injector,
            "after_run_finished",
            {"run_id": effective_run_id, "status": status, "verdict": verdict},
        )
        run_store.release_lease(lease)
    return scheduler_result


def _finish_interrupted_run(
    *,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
    exc: BaseException,
    terminalize_exception: bool,
) -> None:
    if run_store is None or lease is None:
        return
    if isinstance(exc, KeyboardInterrupt):
        status = "CANCELLED"
        verdict = "CANCELLED"
    elif terminalize_exception:
        status = "BLOCKED"
        verdict = "DAG_RUN_EXCEPTION"
    else:
        status = None
        verdict = None
    if status is not None and verdict is not None:
        with suppress(Exception):
            run_store.mark_run_finished(lease, status=status, verdict=verdict)
    with suppress(Exception):
        run_store.release_lease(lease)


def _inject_fault(
    injector: Callable[[str, Mapping[str, Any]], None] | None,
    point: str,
    context: DagAttemptIdentity | Mapping[str, Any],
) -> None:
    if injector is None:
        return
    if isinstance(context, DagAttemptIdentity):
        payload: Mapping[str, Any] = {
            "run_id": context.run_id,
            "node_id": context.node_id,
            "attempt": context.attempt,
            "attempt_id": context.attempt_id,
            "idempotency_key": context.idempotency_key,
        }
    else:
        payload = context
    try:
        injector(point, payload)
    except BaseException:
        tau_logger(
            **dict(payload),
            fault_point=point,
        ).exception("dag_fault_injector_exception")
        raise


def _correction_allows_retry(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    result: Mapping[str, Any],
    attempt: DagNodeAttempt,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
    correction_handler: CorrectionHandler | None,
    event_sink: EventSink | None,
) -> bool:
    """Require durable verification only for explicitly correction-gated failures."""

    if result.get("correction_required") is not True:
        return True
    if correction_handler is None or run_store is None or lease is None:
        _emit(
            event_sink,
            {
                "event": "correction_blocked",
                "node_id": node.node_id,
                "attempt": attempt.attempt,
                "reason": "durable_correction_handler_required",
            },
        )
        return False
    projection = correction_handler(
        DagCorrectionRequest(
            plan=plan,
            node=node,
            result=dict(result),
            attempt=attempt,
            run_store=run_store,
            lease=lease,
        )
    )
    _emit(
        event_sink,
        {
            "event": "correction_evaluated",
            "node_id": node.node_id,
            "attempt": attempt.attempt,
            "incident_id": projection.incident_id,
            "correction_state": projection.state,
            "journal_sequence": projection.journal_sequence,
            "retry_authorized": projection.state == "VERIFIED",
        },
    )
    return projection.state == "VERIFIED"


def _repeated_failure_signature(
    *,
    current: Mapping[str, Any],
    prior_results: list[dict[str, Any]],
) -> str | None:
    if not _failed_result(current) or not prior_results:
        return None
    if not _has_actionable_failure_evidence(current):
        return None
    current_signature = _failure_signature(current)
    previous = prior_results[-1]
    if (
        _failed_result(previous)
        and _has_actionable_failure_evidence(previous)
        and _failure_signature(previous) == current_signature
    ):
        return current_signature
    return None


def _failed_result(result: Mapping[str, Any]) -> bool:
    return result.get("status") != "PASS" or result.get("verdict") != "PASS"


def _failure_signature(result: Mapping[str, Any]) -> str:
    errors = result.get("errors")
    command_results = result.get("command_results")
    normalized_command_results: list[dict[str, Any]] = []
    if isinstance(command_results, list):
        for command_result in command_results:
            if not isinstance(command_result, dict):
                continue
            normalized_command_results.append(
                {
                    "returncode": command_result.get("returncode"),
                    "termination_cause": command_result.get("termination_cause"),
                    "stderr": command_result.get("stderr"),
                    "stdout": command_result.get("stdout"),
                }
            )
    return canonical_sha256(
        {
            "status": result.get("status"),
            "verdict": result.get("verdict"),
            "errors": errors if isinstance(errors, list) else [],
            "command_results": normalized_command_results,
        }
    )


def _has_actionable_failure_evidence(result: Mapping[str, Any]) -> bool:
    errors = result.get("errors")
    if isinstance(errors, list) and any(str(error).strip() for error in errors):
        return True
    command_results = result.get("command_results")
    if not isinstance(command_results, list):
        return False
    for command_result in command_results:
        if not isinstance(command_result, dict):
            continue
        if any(
            command_result.get(field) not in (None, "")
            for field in ("returncode", "termination_cause", "stderr", "stdout")
        ):
            return True
    return False


def _with_same_failure_course_correction(
    result: dict[str, Any],
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    attempt: int,
    repeated_signature: str,
) -> dict[str, Any]:
    receipt_path = result.get("receipt_path")
    if isinstance(receipt_path, str) and receipt_path.strip():
        correction_dir = Path(receipt_path).expanduser().resolve().parent
    else:
        correction_dir = Path.cwd()
    correction_dir.mkdir(parents=True, exist_ok=True)
    correction_path = (
        correction_dir / f"{node.node_id}-course-correction-attempt-{attempt:03d}.json"
    )
    course_correction = write_course_correction_receipt(
        correction_path,
        trigger="brave_search_required_after_two_attempts",
        run_id=identity.run_id,
        dag_id=plan.plan_id,
        goal_hash=plan.runtime_goal_hash,
        target={
            "kind": "dag_node_attempt",
            "node_id": node.node_id,
            "adapter_kind": node.adapter_kind,
            "executor": node.executor,
        },
        node_id=node.node_id,
        agent=node.role,
        attempt=attempt,
        observed_state={
            "attempt_count": attempt,
            "repeated_failure_signature": repeated_signature,
            "advisory_only": True,
            "search_ladder_budget": {
                "memory_recall": 1,
                "brave_search": 1,
                "github_search": 1,
                "dogpile": 1,
            },
            "searches_performed": [],
            "searches_not_performed": [
                "memory_recall",
                "registered_dependency_docs",
                "brave_search",
                "github_search",
                "dogpile",
            ],
        },
        errors=[
            (
                "two consecutive failed attempts had the same error signature; "
                "normal retry blocked before a third same-context attempt"
            )
        ],
        reason=(
            "The same node failure signature repeated across two attempts; "
            "advisory research or a new plan is required before another retry."
        ),
        required_action={
            "type": "advisory_escalation_required",
            "ladder": [
                "memory_recall",
                "registered_dependency_docs",
                "brave_search",
                "github_search",
                "dogpile",
                "human",
            ],
            "skill": "brave-search",
            "skill_reference": "$brave-search",
            "advisory_evidence_only": True,
            "does_not_satisfy_acceptance_gate": True,
        },
        blocked_report_required={
            "required": True,
            "fields": [
                "blocker_summary",
                "attempt_count",
                "repeated_failure_signature",
                "searches_performed",
                "searches_not_performed",
            ],
        },
        mocked=False,
        live=False,
        provider_live=False,
    )
    errors = list(result.get("errors")) if isinstance(result.get("errors"), list) else []
    return {
        **result,
        "status": "BLOCKED",
        "verdict": "COURSE_CORRECTION_REQUIRED",
        "retryable": False,
        "correction_required": True,
        "course_correction_receipt_path": str(correction_path),
        "course_correction_trigger": course_correction["trigger"],
        "course_correction_advisory_only": True,
        "errors": [
            *errors,
            "brave_search_required_after_two_attempts",
            f"course_correction_receipt_path:{correction_path}",
        ],
    }


def _recover_incomplete_attempts(
    *,
    plan: DagPlan,
    policy: DagTransitionPolicy,
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    nodes: Mapping[str, DagPlanNode],
    node_states: dict[str, str],
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    deadlines: dict[str, float],
    completed: set[str],
    resolved: set[str],
    results: dict[str, dict[str, Any]],
    result_order: list[str],
    scheduled: set[str],
    cancel_events: Mapping[str, Event],
    attempt_counts: dict[str, int],
    attempt_history: dict[str, list[dict[str, Any]]],
    transition_receipt_paths: list[str],
    event_sink: EventSink | None,
    fault_injector: Callable[[str, Mapping[str, Any]], None] | None,
    correction_handler: CorrectionHandler | None,
) -> dict[str, Any] | None:
    empty_futures: dict[Future[dict[str, Any]], str] = {}
    for stored in run_store.list_attempts(lease.run_id):
        if stored.state not in {"STAGED", "VALIDATED", "OUTPUT_COMMITTED"}:
            continue
        identity = stored.identity
        node_id = identity.node_id
        raw_result = stored.staged_result
        if raw_result is None:
            raise RuntimeError("dag_attempt_output_not_committed")
        if stored.state == "STAGED":
            validation = _validate_attempt_result(node_id=node_id, result=raw_result)
            run_store.validate_result(lease, identity.attempt_id, validation)
            _inject_fault(fault_injector, "after_result_validated", identity)
        result = _with_attempt_history(
            raw_result,
            attempt=identity.attempt,
            prior_results=attempt_history[node_id],
        )
        retryable = result.get("retryable") is not False
        failed = result.get("status") != "PASS" or result.get("verdict") != "PASS"
        correction_allows_retry = _correction_allows_retry(
            plan=plan,
            node=nodes[node_id],
            result=result,
            attempt=DagNodeAttempt(
                attempt=identity.attempt,
                max_attempts=nodes[node_id].max_attempts,
                cancel_event=cancel_events[node_id],
                run_id=lease.run_id,
                attempt_id=identity.attempt_id,
                idempotency_key=identity.idempotency_key,
                recovered=True,
            ),
            run_store=run_store,
            lease=lease,
            correction_handler=correction_handler,
            event_sink=event_sink,
        )
        will_retry = (
            retryable and correction_allows_retry and identity.attempt < nodes[node_id].max_attempts
        )
        if stored.state != "OUTPUT_COMMITTED" and failed and will_retry:
            run_store.schedule_retry(lease, identity.attempt_id, next_attempt=identity.attempt + 1)
            attempt_history[node_id].append(raw_result)
            attempt_counts[node_id] = max(attempt_counts[node_id], identity.attempt)
            node_states[node_id] = "pending"
            continue
        if stored.state != "OUTPUT_COMMITTED":
            run_store.commit_output(lease, identity.attempt_id)
            _inject_fault(fault_injector, "after_output_committed", identity)
        completion = DagNodeCompletion(
            node_id=node_id,
            attempt=identity.attempt,
            status=str(result.get("status") or "BLOCKED"),
            verdict=str(result.get("verdict") or "NODE_BLOCKED"),
            retryable=retryable,
            raw_result=result,
            terminal_state=(
                "success"
                if result.get("status") == "PASS" and result.get("verdict") == "PASS"
                else "failed"
            ),
        )
        transition = policy.after_node_terminal(
            _transition_view(
                plan=plan,
                node_states=node_states,
                edge_states=edge_states,
                terminal_states=terminal_states,
                running_node_ids=set(),
                deadlines=deadlines,
            ),
            completion,
        )
        run_store.commit_transition(
            lease,
            identity.attempt_id,
            completion=_completion_to_payload(completion),
            result=result,
            transition=transition_batch_to_payload(transition),
        )
        _inject_fault(fault_injector, "after_transition_committed", identity)
        results[node_id] = result
        if node_id not in result_order:
            result_order.append(node_id)
        _apply_transition_batch(
            plan=plan,
            batch=transition,
            edge_states=edge_states,
            terminal_states=terminal_states,
            deadlines=deadlines,
        )
        _apply_node_effects(
            batch=transition,
            nodes=nodes,
            node_states=node_states,
            resolved=resolved,
            completed=completed,
            results=results,
            result_order=result_order,
            scheduled=scheduled,
            cancel_events=cancel_events,
            futures=empty_futures,
            event_sink=event_sink,
        )
        transition_receipt_paths.extend(transition.receipt_paths)
        resolved.add(node_id)
        scheduled.add(node_id)
        node_states[node_id] = "blocked" if transition.block_run else completion.terminal_state
        if completion.terminal_state == "success" and transition.block_run is None:
            completed.add(node_id)
        if transition.block_run is not None:
            return {
                **result,
                "status": "BLOCKED",
                "verdict": transition.block_run.failure_code,
                "errors": [transition.block_run.message],
                "transition_evidence": transition.block_run.evidence,
            }
    completion_transition = policy.after_completion_batch(
        _transition_view(
            plan=plan,
            node_states=node_states,
            edge_states=edge_states,
            terminal_states=terminal_states,
            running_node_ids=set(),
            deadlines=deadlines,
        )
    )
    _persist_control_transition(
        run_store=run_store,
        lease=lease,
        event_key="replay-completion-batch",
        batch=completion_transition,
    )
    _apply_transition_batch(
        plan=plan,
        batch=completion_transition,
        edge_states=edge_states,
        terminal_states=terminal_states,
        deadlines=deadlines,
    )
    _apply_node_effects(
        batch=completion_transition,
        nodes=nodes,
        node_states=node_states,
        resolved=resolved,
        completed=completed,
        results=results,
        result_order=result_order,
        scheduled=scheduled,
        cancel_events=cancel_events,
        futures=empty_futures,
        event_sink=event_sink,
    )
    transition_receipt_paths.extend(completion_transition.receipt_paths)
    if completion_transition.block_run is not None:
        return {
            "status": "BLOCKED",
            "verdict": completion_transition.block_run.failure_code,
            "errors": [completion_transition.block_run.message],
            "transition_evidence": completion_transition.block_run.evidence,
        }
    return None


def _validate_attempt_result(*, node_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise RuntimeError("dag_attempt_result_invalid")
    claimed_node = result.get("node_id")
    if claimed_node is not None and claimed_node != node_id:
        raise RuntimeError("dag_attempt_result_invalid:node_id")
    for field in ("status", "verdict"):
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"dag_attempt_result_invalid:{field}")
    canonical_sha256(dict(result))
    return {
        "schema": "tau.dag_attempt_validation.v1",
        "status": "PASS",
        "node_id": node_id,
        "result_sha256": canonical_sha256(dict(result)),
    }


def _apply_workspace_stale_read_observations(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    result: Mapping[str, Any],
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
) -> dict[str, Any]:
    updated = dict(result)
    if run_store is None or lease is None:
        return updated
    try:
        read_set = result_workspace_read_set(
            plan=plan,
            node=node,
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
            attempt=identity.attempt,
            result=result,
        )
        if read_set is not None:
            updated["workspace_read_set_record"] = run_store.record_workspace_read_set(
                lease,
                identity.attempt_id,
                read_set,
            )
        reconciliations = stale_read_reconciliations_from_result(result)
        if reconciliations:
            updated["stale_read_reconciliation_records"] = [
                dict(item)
                for item in run_store.record_stale_read_reconciliations(
                    lease,
                    identity.attempt_id,
                    reconciliations,
                )
            ]
        changes = workspace_changes_from_result(result)
        if changes:
            updated["workspace_change_signals"] = [
                dict(item)
                for item in run_store.record_workspace_changes(
                    lease,
                    identity.attempt_id,
                    changes,
                )
            ]
    except (RuntimeError, DagRunStoreError) as exc:
        return _workspace_stale_read_blocked_result(
            updated,
            verdict="WORKSPACE_READ_EVIDENCE_INVALID",
            errors=(str(exc),),
            signals=(),
        )
    return updated


def _enforce_workspace_stale_read_policy(
    *,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    result: Mapping[str, Any],
    run_store: SqliteDagRunStore | None,
) -> dict[str, Any]:
    updated = dict(result)
    if run_store is None:
        return updated
    unresolved = run_store.unresolved_workspace_change_signals(
        identity.run_id,
        identity.attempt_id,
    )
    updated["workspace_stale_read_state"] = {
        "schema": "tau.workspace_stale_read_state.v1",
        "policy": stale_read_policy(node),
        "unresolved_signal_count": len(unresolved),
        "unresolved_signal_ids": [str(item["signal_id"]) for item in unresolved],
    }
    if not unresolved or result.get("status") != "PASS" or result.get("verdict") != "PASS":
        return updated
    policy = stale_read_policy(node)
    if policy == "observe":
        return updated
    verdict = (
        "STALE_WORKSPACE_READ_BLOCKED"
        if policy == "block"
        else "STALE_WORKSPACE_READ_RECONCILIATION_REQUIRED"
    )
    return _workspace_stale_read_blocked_result(
        updated,
        verdict=verdict,
        errors=(
            "workspace read set is stale relative to an admitted concurrent change; "
            "reread or supply deterministic stale-read reconciliation evidence"
        ),
        signals=unresolved,
    )


def _workspace_stale_read_blocked_result(
    result: Mapping[str, Any],
    *,
    verdict: str,
    errors: tuple[str, ...],
    signals: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    updated = dict(result)
    updated["status"] = "BLOCKED"
    updated["verdict"] = verdict
    updated["retryable"] = False
    updated["accepted_output"] = None
    updated["errors"] = [
        *(item for item in result.get("errors", []) if isinstance(item, str)),
        *errors,
    ]
    updated["stale_read_signals"] = [dict(item) for item in signals]
    return updated


def _enforce_node_completion_boundary(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    result: Mapping[str, Any],
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
) -> dict[str, Any]:
    if not requires_node_completion_boundary(node.required_evidence):
        return dict(result)
    source_extensions = node.source_extensions.to_value()
    policy = (
        source_extensions.get("node_completion_boundary_policy")
        if isinstance(source_extensions, dict)
        else None
    )
    validation = validate_node_completion_boundary(
        result.get("node_completion_boundary"),
        expected_goal_hash=plan.runtime_goal_hash,
        expected_plan_sha256=plan.plan_sha256,
        expected_node_id=node.node_id,
        expected_attempt_id=identity.attempt_id,
        policy=policy,
    )
    updated = {
        **dict(result),
        "node_completion_boundary_validation": validation.to_payload(),
    }
    if not validation.ok or validation.boundary is None:
        return _boundary_blocked_result(updated, validation.alert_codes, validation.errors)
    updated["node_completion_boundary"] = validation.boundary
    updated["node_completion_boundary_sha256"] = validation.boundary_sha256
    if run_store is None or lease is None:
        return _boundary_blocked_result(
            updated,
            ("node_completion_boundary_admission_unavailable",),
            ("node completion boundary requires a durable run store for admission",),
        )

    boundary_path = (
        run_store.path.parent / "node-completion-boundaries" / f"{identity.attempt_id}.json"
    )
    try:
        write_result = write_durable_json(boundary_path, validation.boundary)
        admission = run_store.admit_receipt(
            lease,
            identity.attempt_id,
            receipt_kind=NODE_COMPLETION_BOUNDARY_SCHEMA,
            sha256=write_result.sha256,
            path=str(write_result.path),
            size_bytes=write_result.size_bytes,
        )
    except (OSError, RuntimeError, ValueError, DagRunStoreError) as exc:
        return _boundary_blocked_result(
            updated,
            ("node_completion_boundary_admission_failed",),
            (str(exc),),
        )
    updated["node_completion_boundary_path"] = str(write_result.path)
    updated["node_completion_boundary_sha256"] = write_result.sha256
    updated["node_completion_boundary_admission"] = admission
    return updated


def _boundary_blocked_result(
    result: Mapping[str, Any],
    alert_codes: tuple[str, ...],
    errors: tuple[str, ...],
) -> dict[str, Any]:
    combined_errors = [
        *(item for item in result.get("errors", []) if isinstance(item, str)),
        *errors,
    ]
    combined_alerts = [
        *(item for item in result.get("alert_codes", []) if isinstance(item, str)),
        *alert_codes,
    ]
    updated = dict(result)
    updated["status"] = "BLOCKED"
    updated["verdict"] = "NODE_COMPLETION_BOUNDARY_INVALID"
    updated["retryable"] = False
    updated["accepted_output"] = None
    updated["errors"] = list(dict.fromkeys(combined_errors))
    updated["alert_codes"] = list(dict.fromkeys(combined_alerts))
    return updated


def _completion_to_payload(completion: DagNodeCompletion) -> dict[str, Any]:
    return {
        "node_id": completion.node_id,
        "attempt": completion.attempt,
        "status": completion.status,
        "verdict": completion.verdict,
        "retryable": completion.retryable,
        "terminal_state": completion.terminal_state,
    }


def _persist_control_transition(
    *,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
    event_key: str,
    batch: DagTransitionBatch,
) -> None:
    if run_store is None or lease is None:
        return
    payload = transition_batch_to_payload(batch)
    digest = canonical_sha256(payload).removeprefix("sha256:")[:16]
    run_store.commit_control_transition(
        lease,
        event_key=f"{event_key}:{digest}",
        transition=payload,
    )


def _restore_durable_state(
    *,
    plan: DagPlan,
    policy: DagTransitionPolicy,
    run_store: SqliteDagRunStore,
    run_id: str,
    nodes: Mapping[str, DagPlanNode],
    node_states: dict[str, str],
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    deadlines: dict[str, float],
    completed: set[str],
    resolved: set[str],
    results: dict[str, dict[str, Any]],
    result_order: list[str],
    scheduled: set[str],
    cancel_events: Mapping[str, Event],
    attempt_counts: dict[str, int],
    attempt_history: dict[str, list[dict[str, Any]]],
    transition_receipt_paths: list[str],
    event_sink: EventSink | None,
) -> tuple[int, dict[str, Any] | None]:
    events = run_store.load_events(run_id)
    attempts = run_store.list_attempts(run_id)
    runtime_endpoints = {
        str(event["entity_id"])
        for event in events
        if event["event_type"] == "runtime_event_appended"
    }
    runtime_projections = tuple(
        projection
        for endpoint in sorted(runtime_endpoints)
        if (projection := run_store.runtime_state_projection(run_id, endpoint)) is not None
    )
    replay = replay_dag_run(
        plan=plan,
        run_record=run_store.load_run_record(run_id),
        events=events,
        attempts=attempts,
        runtime_projections=runtime_projections,
    )
    policy.restore(
        plan,
        DagPolicyReplayState(
            committed_receipts=replay.transition_receipts,
            node_states=dict(node_states),
            edge_states=dict(edge_states),
            terminal_states=dict(terminal_states),
        ),
    )
    node_states.update(replay.node_states)
    edge_states.update(replay.edge_states)
    terminal_states.update(replay.terminal_states)
    deadlines.update(replay.deadline_monotonic)
    transition_receipt_paths.extend(item.path for item in replay.transition_receipts)
    for replay_event in replay.replay_events:
        _emit(event_sink, replay_event)
    replayed_node_states = dict(replay.node_states)
    for replayed in replay.results:
        node_id = replayed.node_id
        attempt_counts[node_id] = max(attempt_counts.get(node_id, 0), replayed.attempt)
        results[node_id] = replayed.payload
        if node_id not in result_order:
            result_order.append(node_id)
        resolved.add(node_id)
        scheduled.add(node_id)
        if replayed.terminal_state == "success" and replayed_node_states.get(node_id) == "success":
            completed.add(node_id)
    for stored in attempts:
        if stored.staged_result is None or stored.state not in {
            "RETRY_SCHEDULED",
            "SETTLED",
        }:
            continue
        if stored.state == "RETRY_SCHEDULED":
            attempt_history[stored.identity.node_id].append(stored.staged_result)
    return len(events), replay.block


def _cancel_and_collect_futures(
    *,
    futures: dict[Future[dict[str, Any]], str],
    future_attempts: dict[Future[dict[str, Any]], DagAttemptIdentity],
    cancel_events: Mapping[str, Event],
    results: dict[str, dict[str, Any]],
    result_order: list[str],
    node_states: dict[str, str],
    resolved: set[str],
    event_sink: EventSink | None,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
    lease_ttl_seconds: float,
    lease_renewal_interval: float,
) -> DagRunLease | None:
    if not futures:
        return lease
    _emit(
        event_sink,
        {
            "event": "scheduler_cancellation_signaled",
            "node_ids": sorted(futures.values()),
        },
    )
    for pending, pending_node_id in futures.items():
        cancel_events[pending_node_id].set()
        pending.cancel()
    for pending, pending_node_id in futures.items():
        identity = future_attempts.pop(pending)
        while not pending.done():
            wait((pending,), timeout=lease_renewal_interval)
            if run_store is not None and lease is not None and not pending.done():
                lease = run_store.renew_lease(lease, ttl_seconds=lease_ttl_seconds)
        try:
            cancelled_result = pending.result()
        except CancelledError:
            cancelled_result = {
                "node_id": pending_node_id,
                "status": "BLOCKED",
                "verdict": "CANCELLED",
                "errors": ["cancelled before adapter execution"],
            }
        except Exception as exc:  # pragma: no cover - defensive boundary.
            cancelled_result = {
                "node_id": pending_node_id,
                "status": "BLOCKED",
                "verdict": "CANCELLED",
                "errors": [str(exc)],
            }
        if run_store is not None and lease is not None:
            cancelled_result = run_store.stage_result(lease, identity.attempt_id, cancelled_result)
            run_store.validate_result(
                lease,
                identity.attempt_id,
                _validate_attempt_result(node_id=pending_node_id, result=cancelled_result),
            )
            run_store.commit_output(lease, identity.attempt_id)
            run_store.commit_transition(
                lease,
                identity.attempt_id,
                completion={
                    "node_id": pending_node_id,
                    "attempt": identity.attempt,
                    "status": str(cancelled_result.get("status") or "BLOCKED"),
                    "verdict": str(cancelled_result.get("verdict") or "CANCELLED"),
                    "retryable": False,
                    "terminal_state": "cancelled",
                },
                result=cancelled_result,
                transition=transition_batch_to_payload(DagTransitionBatch()),
            )
        results[pending_node_id] = cancelled_result
        result_order.append(pending_node_id)
        node_states[pending_node_id] = "cancelled"
        resolved.add(pending_node_id)
        _emit(event_sink, {"event": "node_cancelled", "node_id": pending_node_id})
    futures.clear()
    return lease


def _lease_close_to_expiry(lease: DagRunLease, lease_ttl_seconds: float) -> bool:
    ttl_ms = max(1, int(lease_ttl_seconds * 1000))
    renew_margin_ms = max(100, ttl_ms // 2)
    return lease.expires_at_ms <= int(time.time() * 1000) + renew_margin_ms


def _incoming_edges(plan: DagPlan, *, node_ids: set[str]) -> dict[str, tuple[str, ...]]:
    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in plan.control_edges:
        if edge.target_kind == "node" and edge.target_id in incoming:
            incoming[edge.target_id].append(edge.edge_id)
    return {node_id: tuple(sorted(edge_ids)) for node_id, edge_ids in incoming.items()}


def _context_bindings_by_target(
    plan: DagPlan,
) -> Mapping[str, tuple[DagPlanContextBinding, ...]]:
    values: dict[str, list[DagPlanContextBinding]] = {}
    for binding in plan.context_bindings:
        values.setdefault(binding.target_node_id, []).append(binding)
    return {target: tuple(bindings) for target, bindings in values.items()}


def _return_result(result: dict[str, Any]) -> dict[str, Any]:
    return result


def _assign_worker_for_attempt(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    identity: DagAttemptIdentity,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
    resource_lease_manager: ResourceLeaseManager | None,
    resource_lease_ttl_seconds: float,
    worker_registry: WorkerRegistry | tuple[WorkerCapability | Mapping[str, Any], ...] | None,
) -> tuple[tuple[ResourceLeaseToken, ...], dict[str, Any] | None]:
    if worker_registry is None and resource_lease_manager is None:
        return (), None
    if run_store is None or lease is None:
        raise WorkerAssignmentError("worker_assignment_run_store_required", node.node_id)
    if resource_lease_manager is None:
        raise WorkerAssignmentError("worker_resource_lease_manager_missing", node.node_id)
    requirement = compile_worker_requirement(
        plan=plan,
        node=node,
        run_id=run_id,
        attempt_id=identity.attempt_id,
        attempt=identity.attempt,
    )
    candidates = normalize_worker_capabilities(
        worker_registry,
        plan=plan,
        node=node,
        run_id=run_id,
        attempt_id=identity.attempt_id,
        attempt=identity.attempt,
    )
    assignment = select_worker(requirement=requirement, candidates=candidates)
    worker_node = replace(
        node,
        runtime_requirement=FrozenJson.from_value(
            {"resource_leases": [worker_resource_requirement(assignment)]}
        ),
    )
    worker_tokens = resource_lease_manager.acquire_for_attempt(
        node=worker_node,
        run_id=run_id,
        attempt_id=identity.attempt_id,
        ttl_seconds=resource_lease_ttl_seconds,
        run_store=run_store,
        scheduler_lease=lease,
    )
    if not worker_tokens:
        raise WorkerAssignmentError("worker_resource_lease_missing", node.node_id)
    try:
        receipt = assignment.receipt_payload(resource_lease_token=worker_tokens[0].to_payload())
        written = write_durable_json(
            run_store.path.parent
            / "worker-assignments"
            / node.node_id
            / f"{identity.attempt_id}.json",
            receipt,
        )
        admission = run_store.admit_receipt(
            lease,
            identity.attempt_id,
            receipt_kind=WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
            sha256=written.sha256,
            path=str(written.path),
            size_bytes=written.size_bytes,
        )
    except Exception:
        resource_lease_manager.release(
            worker_tokens,
            run_store=run_store,
            scheduler_lease=lease,
            reason="worker_assignment_receipt_failed",
        )
        raise
    return worker_tokens, admission


def _node_is_ready(
    node_id: str,
    *,
    incoming_edges: Mapping[str, tuple[str, ...]],
    edge_states: Mapping[str, str],
) -> bool:
    edge_ids = incoming_edges[node_id]
    return not edge_ids or all(edge_states.get(edge_id) == "success" for edge_id in edge_ids)


def _transition_view(
    *,
    plan: DagPlan,
    node_states: dict[str, str],
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    running_node_ids: set[str],
    deadlines: dict[str, float],
) -> DagTransitionView:
    return DagTransitionView(
        plan=plan,
        node_states=dict(node_states),
        edge_states=dict(edge_states),
        terminal_states=dict(terminal_states),
        running_node_ids=frozenset(running_node_ids),
        deadline_monotonic=dict(deadlines),
        now_monotonic=time.monotonic(),
    )


def _apply_transition_batch(
    *,
    plan: DagPlan,
    batch: DagTransitionBatch,
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    deadlines: dict[str, float],
) -> None:
    apply_transition_state(
        plan=plan,
        batch=batch,
        node_states={},
        edge_states=edge_states,
        terminal_states=terminal_states,
        deadlines=deadlines,
    )


def _apply_node_effects(
    *,
    batch: DagTransitionBatch,
    nodes: Mapping[str, DagPlanNode],
    node_states: dict[str, str],
    resolved: set[str],
    completed: set[str],
    results: dict[str, dict[str, Any]],
    result_order: list[str],
    scheduled: set[str],
    cancel_events: Mapping[str, Event],
    futures: Mapping[Future[dict[str, Any]], str],
    event_sink: EventSink | None,
) -> None:
    running = set(futures.values())
    cancelled_running: list[str] = []
    for cancellation in batch.node_cancellations:
        node_id = cancellation.node_id
        if node_id not in nodes or node_id in resolved:
            continue
        cancel_events[node_id].set()
        for future, future_node_id in futures.items():
            if future_node_id == node_id:
                future.cancel()
        if node_id in running:
            node_states[node_id] = "cancelled"
            cancelled_running.append(node_id)
            continue
        result = {
            "node_id": node_id,
            "status": "CANCELLED",
            "verdict": "CANCELLED",
            "attempt_count": 0,
            "accepted_output": None,
            "errors": [],
        }
        results[node_id] = result
        result_order.append(node_id)
        node_states[node_id] = "cancelled"
        resolved.add(node_id)
        scheduled.add(node_id)
        _emit(
            event_sink,
            {
                "event": "unstarted_join_source_suppressed",
                "node_id": node_id,
                "state": "cancelled",
                "reason_code": cancellation.reason_code,
            },
        )
    if cancelled_running:
        _emit(
            event_sink,
            {
                "event": "join_source_cancellation_signaled",
                "node_ids": sorted(cancelled_running),
            },
        )
    for settlement in batch.node_settlements:
        node_id = settlement.node_id
        if node_id not in nodes or node_id in resolved:
            continue
        result = {
            "node_id": node_id,
            "status": "PASS" if settlement.state == "success" else settlement.state.upper(),
            "verdict": "PASS" if settlement.state == "success" else settlement.state.upper(),
            "attempt_count": 0,
            "accepted_output": None,
            "errors": [],
        }
        results[node_id] = result
        result_order.append(node_id)
        node_states[node_id] = settlement.state
        resolved.add(node_id)
        scheduled.add(node_id)
        if settlement.state == "success":
            completed.add(node_id)


def _admit_result_receipt(
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    attempt_id: str,
    result: Mapping[str, Any],
) -> bool:
    """Parent-side S6-S7 (#203): hash the durable receipt and admit it.

    The digest is computed from the bytes on disk, never from worker-reported
    values, so the admission row binds what actually exists. A missing or
    unreadable receipt is not an error here - it stays visible as a bypass
    ledger entry until enforcement (#207) flips on.
    """

    raw_path = result.get("receipt_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    path = Path(raw_path)
    try:
        blob = path.read_bytes()
    except OSError:
        return False
    # S6 validation: never admit bytes that do not parse as a JSON object -
    # a child SIGKILLed mid-write leaves a torn file, and admitting its hash
    # would make torn evidence authoritative. Torn/invalid receipts stay
    # unadmitted and visible on the bypass ledger until enforcement settles
    # them via system_settlement.
    try:
        parsed = json.loads(blob.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    digest = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    try:
        run_store.admit_receipt(
            lease,
            attempt_id,
            receipt_kind="node_receipt",
            sha256=digest,
            path=str(path),
            size_bytes=len(blob),
        )
        return True
    except DagRunStoreError:
        # dag_admission_conflict or lease loss: surfaced by the store's own
        # event trail; settlement still records the gap via the observer.
        return False


def _classify_unadmitted_result(
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    attempt_id: str,
    result: Mapping[str, Any],
) -> None:
    """Absence classification for the subprocess family (#205).

    The durable attempt row is the attempt witness: a DISPATCHED attempt whose
    child terminated by signal or timeout and left no admissible receipt is
    (b) attempted-and-swallowed; a child that exited on its own without a
    receipt is (a)-shaped at this layer (control flow never reached a write).
    Recorded as a diagnostic event so #215's load campaign reads
    classifications instead of re-deriving them.
    """

    command_results = result.get("command_results")
    returncodes = (
        [r.get("returncode") for r in command_results if isinstance(r, dict)]
        if isinstance(command_results, list)
        else []
    )
    if not returncodes:
        return
    terminated = any(rc in (-9, -15, 124, 130) for rc in returncodes)
    classification = "attempted_and_swallowed" if terminated else "never_attempted_write"
    try:
        run_store.append_diagnostic_event(
            lease,
            event_key=f"absence-classified:{attempt_id}",
            node_id=str(result.get("node_id") or ""),
            attempt_id=attempt_id,
            payload={
                "schema": "tau.dag_diagnostic_event.v1",
                "event_type": "receipt_absence_classified",
                "classification": classification,
                "returncodes": returncodes,
                "receipt_path": result.get("receipt_path"),
            },
        )
    except DagRunStoreError:
        return


def _enforce_admission_block(
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    attempt_id: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert an unadmittable accepted result into a system-settled BLOCKED.

    The worker claimed PASS but its receipt could not be admitted (missing,
    torn, or conflicting): the claim is not evidence. The scheduler authors a
    system_settlement receipt through its trusted path; if even that fails the
    run store enters RUN_STORE_FAILURE and the raise propagates.
    """

    from tau_coding.dag_runtime.system_settlement import settle_with_system_receipt

    receipt_path = Path(str(result.get("receipt_path")))
    settle_with_system_receipt(
        run_store,
        lease,
        attempt_id,
        receipts_root=receipt_path.parent.parent
        if receipt_path.parent.name
        else receipt_path.parent,
        node_id=str(result.get("node_id") or ""),
        reason_code="expected_receipt_not_admitted",
        expected_receipt_kind="node_receipt",
        classification="attempted_and_swallowed",
        run_dir=run_store.path.parent,
    )
    blocked = dict(result)
    blocked["status"] = "BLOCKED"
    blocked["verdict"] = "RECEIPT_NOT_ADMITTED"
    errors = list(blocked.get("errors") or [])
    errors.append("accepted terminal state refused: receipt was not admitted")
    blocked["errors"] = errors
    return blocked


def _settle_unrunnable_nodes(
    *,
    plan: DagPlan,
    policy: DagTransitionPolicy,
    nodes: dict[str, DagPlanNode],
    resolved: set[str],
    scheduled: set[str],
    completed: set[str],
    results: dict[str, dict[str, Any]],
    result_order: list[str],
    node_states: dict[str, str],
    edge_states: dict[str, str],
    terminal_states: dict[str, str],
    deadlines: dict[str, float],
    cancel_events: Mapping[str, Event],
    futures: Mapping[Future[dict[str, Any]], str],
    transition_receipt_paths: list[str],
    event_sink: EventSink | None,
    run_store: SqliteDagRunStore | None,
    lease: DagRunLease | None,
) -> DagRunBlock | None:
    incoming = _incoming_edges(plan, node_ids=set(nodes))
    changed = True
    while changed:
        changed = False
        for node_id in sorted(nodes):
            if node_id in resolved or node_id in scheduled:
                continue
            edge_ids = incoming[node_id]
            if not edge_ids or not all(edge_id in edge_states for edge_id in edge_ids):
                continue
            if all(edge_states[edge_id] == "success" for edge_id in edge_ids):
                continue
            state = (
                "skipped"
                if all(edge_states[edge_id] == "skipped" for edge_id in edge_ids)
                else "blocked"
            )
            result = {
                "node_id": node_id,
                "status": state.upper(),
                "verdict": state.upper(),
                "attempt_count": 0,
                "accepted_output": None,
                "errors": [],
            }
            transition = policy.after_node_terminal(
                _transition_view(
                    plan=plan,
                    node_states=node_states,
                    edge_states=edge_states,
                    terminal_states=terminal_states,
                    running_node_ids=set(),
                    deadlines=deadlines,
                ),
                DagNodeCompletion(
                    node_id=node_id,
                    attempt=0,
                    status=state.upper(),
                    verdict=state.upper(),
                    retryable=False,
                    raw_result=result,
                    terminal_state=state,
                ),
            )
            _persist_control_transition(
                run_store=run_store,
                lease=lease,
                event_key=f"virtual-terminal:{node_id}:{state}",
                batch=transition,
            )
            _apply_transition_batch(
                plan=plan,
                batch=transition,
                edge_states=edge_states,
                terminal_states=terminal_states,
                deadlines=deadlines,
            )
            _apply_node_effects(
                batch=transition,
                nodes=nodes,
                node_states=node_states,
                resolved=resolved,
                completed=completed,
                results=results,
                result_order=result_order,
                scheduled=scheduled,
                cancel_events=cancel_events,
                futures=futures,
                event_sink=event_sink,
            )
            transition_receipt_paths.extend(transition.receipt_paths)
            for transition_event in transition.events:
                _emit(event_sink, transition_event)
            if transition.block_run is not None:
                return transition.block_run
            completion_transition = policy.after_completion_batch(
                _transition_view(
                    plan=plan,
                    node_states=node_states,
                    edge_states=edge_states,
                    terminal_states=terminal_states,
                    running_node_ids=set(futures.values()),
                    deadlines=deadlines,
                )
            )
            _persist_control_transition(
                run_store=run_store,
                lease=lease,
                event_key=f"virtual-completion-batch:{node_id}:{state}",
                batch=completion_transition,
            )
            _apply_transition_batch(
                plan=plan,
                batch=completion_transition,
                edge_states=edge_states,
                terminal_states=terminal_states,
                deadlines=deadlines,
            )
            _apply_node_effects(
                batch=completion_transition,
                nodes=nodes,
                node_states=node_states,
                resolved=resolved,
                completed=completed,
                results=results,
                result_order=result_order,
                scheduled=scheduled,
                cancel_events=cancel_events,
                futures=futures,
                event_sink=event_sink,
            )
            transition_receipt_paths.extend(completion_transition.receipt_paths)
            for transition_event in completion_transition.events:
                _emit(event_sink, transition_event)
            if completion_transition.block_run is not None:
                return completion_transition.block_run
            if node_id in resolved:
                changed = True
                continue
            results[node_id] = result
            result_order.append(node_id)
            node_states[node_id] = state
            resolved.add(node_id)
            _emit(
                event_sink,
                {"event": f"node_{state}", "node_id": node_id, "attempt": 0},
            )
            changed = True
    return None


def _emit(sink: EventSink | None, event: dict[str, Any]) -> None:
    if sink is not None:
        sink(event)


def _with_attempt_history(
    result: dict[str, Any],
    *,
    attempt: int,
    prior_results: list[dict[str, Any]],
) -> dict[str, Any]:
    combined = dict(result)
    adapter_attempt_count = result.get("attempt_count")
    combined["attempt_count"] = (
        adapter_attempt_count if isinstance(adapter_attempt_count, int) else attempt
    )
    command_results: list[Any] = []
    for item in (*prior_results, result):
        values = item.get("command_results")
        if isinstance(values, list):
            command_results.extend(values)
    if command_results:
        combined["command_results"] = command_results
    combined["scheduler_attempts"] = [
        {
            "attempt": index,
            "status": item.get("status"),
            "verdict": item.get("verdict"),
            "errors": list(item.get("errors", [])) if isinstance(item.get("errors"), list) else [],
        }
        for index, item in enumerate((*prior_results, result), start=1)
    ]
    return combined
