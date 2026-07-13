"""Backend-neutral scheduler state machine for compiled Tau DAG plans."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagNodeCompletion,
    DagRunBlock,
    DagTransitionBatch,
    DagTransitionPolicy,
    DagTransitionView,
)


@dataclass(frozen=True, slots=True)
class DagNodeAttempt:
    attempt: int
    max_attempts: int
    cancel_event: Event


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


def run_dag_plan(
    plan: DagPlan,
    *,
    execute_node: NodeExecutor,
    transition_policy: DagTransitionPolicy | None = None,
    max_concurrency: int = 1,
    event_sink: EventSink | None = None,
) -> DagSchedulerResult:
    """Execute an all-success DagPlan through one bounded ready queue.

    Route and join policies other than the generic all-success policy remain
    fail closed until their project adapters are moved onto this state machine.
    """

    if max_concurrency < 1:
        raise RuntimeError("max_concurrency must be at least 1")
    policy = transition_policy or AllSuccessTransitionPolicy()
    policy.validate_plan(plan)

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
    max_observed_concurrency = 0
    blocked_result: dict[str, Any] | None = None
    transition_receipt_paths: list[str] = []
    deadlines: dict[str, float] = {}

    _emit(event_sink, {"event": "scheduler_started", "plan_id": plan.plan_id})
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures: dict[Future[dict[str, Any]], str] = {}
        while len(resolved) < len(nodes):
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
            )
            if settle_block is not None:
                blocked_result = {
                    "status": "BLOCKED",
                    "verdict": settle_block.failure_code,
                    "errors": [settle_block.message],
                    "transition_evidence": settle_block.evidence,
                }
                for pending_node_id in futures.values():
                    cancel_events[pending_node_id].set()
                for pending in futures:
                    pending.cancel()
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
                _apply_transition_batch(
                    plan=plan,
                    batch=start_transition,
                    edge_states=edge_states,
                    terminal_states=terminal_states,
                    deadlines=deadlines,
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
                    ),
                )
                futures[future] = node_id
                scheduled.add(node_id)
                node_states[node_id] = "running"
                _emit(
                    event_sink,
                    {"event": "node_started", "node_id": node_id, "attempt": attempt},
                )
            max_observed_concurrency = max(max_observed_concurrency, len(futures))

            if blocked_result is not None:
                break
            if not futures:
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
                    for pending_node_id in futures.values():
                        cancel_events[pending_node_id].set()
                    for pending in futures:
                        pending.cancel()
                    break
                continue
            completed_batch: list[tuple[str, dict[str, Any]]] = []
            for future in done:
                node_id = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive adapter boundary.
                    result = {
                        "node_id": node_id,
                        "status": "BLOCKED",
                        "verdict": "ADAPTER_EXECUTION_FAILED",
                        "errors": [str(exc)],
                    }
                completed_batch.append((node_id, result))

            batch_blocked = False
            for node_id, result in sorted(completed_batch):
                attempt = attempt_counts[node_id]
                result = _with_attempt_history(
                    result,
                    attempt=attempt,
                    prior_results=attempt_history[node_id],
                )
                attempt_history[node_id].append(result)
                retryable = result.get("retryable") is not False
                failed_attempt = result.get("status") != "PASS" or result.get("verdict") != "PASS"
                will_retry = retryable and attempt < nodes[node_id].max_attempts
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
                results[node_id] = result
                result_order.append(node_id)
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
                    results[pending_node_id] = cancelled_result
                    result_order.append(pending_node_id)
                    node_states[pending_node_id] = "cancelled"
                    resolved.add(pending_node_id)
                    _emit(
                        event_sink,
                        {"event": "node_cancelled", "node_id": pending_node_id},
                    )
                futures.clear()
                break

    ordered_results = tuple(results[node_id] for node_id in result_order)
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
    return DagSchedulerResult(
        status=status,
        verdict=verdict,
        node_results=ordered_results,
        completed_node_ids=tuple(sorted(completed)),
        max_observed_concurrency=max_observed_concurrency,
        edge_states=tuple(sorted(edge_states.items())),
        terminal_states=tuple(sorted(terminal_states.items())),
        node_states=tuple(sorted(node_states.items())),
        transition_receipt_paths=tuple(transition_receipt_paths),
    )


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
    return {target: tuple(sorted(sources)) for target, sources in values.items()}


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
