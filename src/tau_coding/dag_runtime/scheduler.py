"""Backend-neutral scheduler state machine for compiled Tau DAG plans."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode


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


def run_dag_plan(
    plan: DagPlan,
    *,
    execute_node: NodeExecutor,
    max_concurrency: int = 1,
    event_sink: EventSink | None = None,
) -> DagSchedulerResult:
    """Execute an all-success DagPlan through one bounded ready queue.

    Route and join policies other than the generic all-success policy remain
    fail closed until their project adapters are moved onto this state machine.
    """

    if max_concurrency < 1:
        raise RuntimeError("max_concurrency must be at least 1")
    if plan.route_contracts or plan.join_contracts:
        raise RuntimeError("dag_plan_route_join_adapter_required")

    declared_terminal_nodes = {
        terminal.terminal_id
        for terminal in plan.terminal_endpoints
        if terminal.kind == "declared_node"
    }
    nodes = {
        node.node_id: node
        for node in plan.nodes
        if node.node_id not in declared_terminal_nodes
    }
    predecessors = _predecessors(plan, node_ids=set(nodes))
    context_sources = _context_sources(plan)
    completed: set[str] = set()
    results: dict[str, dict[str, Any]] = {}
    result_order: list[str] = []
    scheduled: set[str] = set()
    cancel_events = {node_id: Event() for node_id in nodes}
    attempt_counts = {node_id: 0 for node_id in nodes}
    attempt_history: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    max_observed_concurrency = 0
    blocked_result: dict[str, Any] | None = None

    _emit(event_sink, {"event": "scheduler_started", "plan_id": plan.plan_id})
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures: dict[Future[dict[str, Any]], str] = {}
        while len(completed) < len(nodes):
            ready = [
                node_id
                for node_id in sorted(nodes)
                if node_id not in completed
                and node_id not in scheduled
                and predecessors[node_id].issubset(completed)
            ]
            for node_id in ready:
                if len(futures) >= max_concurrency:
                    break
                attempt_counts[node_id] += 1
                attempt = attempt_counts[node_id]
                accepted_inputs = tuple(
                    results[source]["accepted_output"]
                    for source in context_sources.get(node_id, ())
                    if isinstance(results.get(source, {}).get("accepted_output"), dict)
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
                _emit(
                    event_sink,
                    {"event": "node_started", "node_id": node_id, "attempt": attempt},
                )
            max_observed_concurrency = max(max_observed_concurrency, len(futures))

            if not futures:
                remaining = sorted(set(nodes) - completed)
                blocked_result = {
                    "status": "BLOCKED",
                    "verdict": "READY_QUEUE_STALLED",
                    "errors": [f"no node became ready: {', '.join(remaining)}"],
                }
                break

            done, _ = wait(futures, return_when=FIRST_COMPLETED)
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
                failed_attempt = (
                    result.get("status") != "PASS" or result.get("verdict") != "PASS"
                )
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
                if result.get("status") != "PASS" or result.get("verdict") != "PASS":
                    if blocked_result is None:
                        blocked_result = result
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
                completed.add(node_id)
                _emit(
                    event_sink,
                    {"event": "node_completed", "node_id": node_id, "attempt": attempt},
                )
            if batch_blocked:
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
    )


def _predecessors(plan: DagPlan, *, node_ids: set[str]) -> dict[str, set[str]]:
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in plan.control_edges:
        if edge.target_kind == "node" and edge.target_id in predecessors:
            predecessors[edge.target_id].add(edge.source_node_id)
    return predecessors


def _context_sources(plan: DagPlan) -> Mapping[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for binding in plan.context_bindings:
        values.setdefault(binding.target_node_id, []).append(binding.source_node_id)
    return {target: tuple(sorted(sources)) for target, sources in values.items()}


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
            "errors": list(item.get("errors", []))
            if isinstance(item.get("errors"), list)
            else [],
        }
        for index, item in enumerate((*prior_results, result), start=1)
    ]
    return combined
