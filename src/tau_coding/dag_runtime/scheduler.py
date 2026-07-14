"""Backend-neutral scheduler state machine for compiled Tau DAG plans."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from threading import Event
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import (
    DagAttemptIdentity,
    DagRunLease,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagCommittedReceipt,
    DagNodeCompletion,
    DagPolicyReplayState,
    DagRunBlock,
    DagTransitionBatch,
    DagTransitionPolicy,
    DagTransitionView,
    transition_batch_from_payload,
    transition_batch_to_payload,
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


NodeExecutor = Callable[
    [DagPlanNode, tuple[dict[str, Any], ...], DagNodeAttempt],
    dict[str, Any],
]
EventSink = Callable[[dict[str, Any]], None]


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
) -> DagSchedulerResult:
    """Execute an all-success DagPlan through one bounded ready queue.

    Route and join policies other than the generic all-success policy remain
    fail closed until their project adapters are moved onto this state machine.
    """

    if max_concurrency < 1:
        raise RuntimeError("max_concurrency must be at least 1")
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
            except Exception:
                run_store.release_lease(lease)
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
    context_edges = _context_edges(plan)
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
        )
        blocked_result = replayed_block or recovery_block

    _emit(event_sink, {"event": "scheduler_started", "plan_id": plan.plan_id})
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures: dict[Future[dict[str, Any]], str] = {}
        future_attempts: dict[Future[dict[str, Any]], DagAttemptIdentity] = {}
        while len(resolved) < len(nodes):
            if (
                run_store is not None
                and lease is not None
                and time.monotonic() >= next_lease_renewal
            ):
                lease = run_store.renew_lease(lease, ttl_seconds=lease_ttl_seconds)
                next_lease_renewal = time.monotonic() + lease_renewal_interval
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
                and _node_is_ready(node_id, incoming_edges=incoming_edges, edge_states=edge_states)
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
                if run_store is not None and lease is not None:
                    identity = run_store.reserve_attempt(
                        lease,
                        plan_sha256=plan.plan_sha256,
                        node_id=node_id,
                        attempt=attempt,
                    )
                    _inject_fault(fault_injector, "after_attempt_reserved", identity)
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
                accepted_inputs = tuple(
                    results[source]["accepted_output"]
                    for source, edge_id in context_edges.get(node_id, ())
                    if edge_states.get(edge_id) == "success"
                    and isinstance(results.get(source, {}).get("accepted_output"), dict)
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
                    ),
                )
                futures[future] = node_id
                future_attempts[future] = identity
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
            completed_batch: list[tuple[str, DagAttemptIdentity, dict[str, Any]]] = []
            for future in done:
                node_id = futures.pop(future)
                identity = future_attempts.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive adapter boundary.
                    result = {
                        "node_id": node_id,
                        "status": "BLOCKED",
                        "verdict": "ADAPTER_EXECUTION_FAILED",
                        "errors": [str(exc)],
                    }
                completed_batch.append((node_id, identity, result))

            batch_blocked = False
            for node_id, identity, result in sorted(completed_batch):
                attempt = attempt_counts[node_id]
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
                attempt_history[node_id].append(raw_attempt_result)
                retryable = result.get("retryable") is not False
                scheduler_cancelled = cancel_events[node_id].is_set()
                failed_attempt = result.get("status") != "PASS" or result.get("verdict") != "PASS"
                will_retry = (
                    retryable
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
        status = "BLOCKED"
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
    injector(point, payload)


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
        will_retry = retryable and identity.attempt < nodes[node_id].max_attempts
        if stored.state != "OUTPUT_COMMITTED" and failed and will_retry:
            run_store.schedule_retry(
                lease, identity.attempt_id, next_attempt=identity.attempt + 1
            )
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
    committed_receipts: dict[str, DagCommittedReceipt] = {}
    replayed_block: dict[str, Any] | None = None
    for event in events:
        if event["event_type"] not in {
            "scheduler_transition_committed",
            "scheduler_control_transition_committed",
        }:
            continue
        transition_payload = event["payload"].get("transition")
        if not isinstance(transition_payload, Mapping):
            raise RuntimeError("dag_transition_replay_mismatch")
        for receipt in transition_payload.get("receipt_refs", []):
            if not isinstance(receipt, Mapping):
                raise RuntimeError("dag_transition_replay_mismatch")
            path = str(receipt["path"])
            committed_receipts[path] = DagCommittedReceipt(
                path=path,
                file_sha256=str(receipt["file_sha256"]),
            )
    policy.restore(
        plan,
        DagPolicyReplayState(
            committed_receipts=tuple(committed_receipts.values()),
            node_states=dict(node_states),
            edge_states=dict(edge_states),
            terminal_states=dict(terminal_states),
        ),
    )
    empty_futures: dict[Future[dict[str, Any]], str] = {}
    for event in events:
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
        _apply_transition_batch(
            plan=plan,
            batch=batch,
            edge_states=edge_states,
            terminal_states=terminal_states,
            deadlines=deadlines,
        )
        _apply_node_effects(
            batch=batch,
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
        transition_receipt_paths.extend(batch.receipt_paths)
        for transition_event in batch.events:
            _emit(event_sink, {**transition_event, "durably_replayed": True})
        if batch.block_run is not None and replayed_block is None:
            replayed_block = {
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
        result = dict(result)
        if "resumed" in result:
            result["resumed"] = True
        result["durably_replayed"] = True
        node_id = str(completion["node_id"])
        attempt = int(completion["attempt"])
        attempt_counts[node_id] = max(attempt_counts.get(node_id, 0), attempt)
        results[node_id] = result
        if node_id not in result_order:
            result_order.append(node_id)
        terminal_state = str(completion["terminal_state"])
        node_states[node_id] = "blocked" if batch.block_run is not None else terminal_state
        resolved.add(node_id)
        scheduled.add(node_id)
        if terminal_state == "success" and batch.block_run is None:
            completed.add(node_id)
        _emit(
            event_sink,
            {
                "event": "node_replayed",
                "node_id": node_id,
                "attempt": attempt,
                "terminal_state": node_states[node_id],
            },
        )
    for stored in run_store.list_attempts(run_id):
        if stored.staged_result is None or stored.state not in {
            "RETRY_SCHEDULED",
            "SETTLED",
        }:
            continue
        if stored.state == "RETRY_SCHEDULED":
            attempt_history[stored.identity.node_id].append(stored.staged_result)
    return len(events), replayed_block


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
            cancelled_result = run_store.stage_result(
                lease, identity.attempt_id, cancelled_result
            )
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


def _incoming_edges(plan: DagPlan, *, node_ids: set[str]) -> dict[str, tuple[str, ...]]:
    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in plan.control_edges:
        if edge.target_kind == "node" and edge.target_id in incoming:
            incoming[edge.target_id].append(edge.edge_id)
    return {node_id: tuple(sorted(edge_ids)) for node_id, edge_ids in incoming.items()}


def _context_edges(plan: DagPlan) -> Mapping[str, tuple[tuple[str, str], ...]]:
    values: dict[str, list[tuple[str, str]]] = {}
    for binding in plan.context_bindings:
        values.setdefault(binding.target_node_id, []).append(
            (binding.source_node_id, binding.control_edge_id)
        )
    return {target: tuple(sources) for target, sources in values.items()}


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
    edges = {edge.edge_id: edge for edge in plan.control_edges}
    terminal_ids = {terminal.terminal_id for terminal in plan.terminal_endpoints}
    for settlement in batch.edge_settlements:
        if settlement.edge_id not in edges:
            raise RuntimeError(f"dag_transition_unknown_edge:{settlement.edge_id}")
        prior = edge_states.get(settlement.edge_id)
        if prior is not None and prior != settlement.state:
            raise RuntimeError(f"dag_transition_effect_conflict:{settlement.edge_id}")
        edge_states[settlement.edge_id] = settlement.state
        edge = edges[settlement.edge_id]
        if edge.target_kind == "terminal" or edge.target_id in terminal_ids:
            terminal_states[edge.target_id] = settlement.state
    for arm in batch.deadline_arms:
        deadline_prior = deadlines.get(arm.deadline_id)
        if deadline_prior is not None and deadline_prior != arm.deadline_monotonic:
            raise RuntimeError(f"dag_transition_deadline_conflict:{arm.deadline_id}")
        deadlines[arm.deadline_id] = arm.deadline_monotonic
    for deadline_id in batch.deadline_cancellations:
        deadlines.pop(deadline_id, None)


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
