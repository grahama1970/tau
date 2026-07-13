"""Backend-neutral scheduler state machine for compiled Tau DAG plans."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode

NodeExecutor = Callable[[DagPlanNode, tuple[dict[str, Any], ...]], dict[str, Any]]
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

    nodes = {node.node_id: node for node in plan.nodes}
    predecessors = _predecessors(plan, node_ids=set(nodes))
    context_sources = _context_sources(plan)
    completed: set[str] = set()
    results: dict[str, dict[str, Any]] = {}
    result_order: list[str] = []
    scheduled: set[str] = set()
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
                accepted_inputs = tuple(
                    results[source]["accepted_output"]
                    for source in context_sources.get(node_id, ())
                    if isinstance(results.get(source, {}).get("accepted_output"), dict)
                )
                future = pool.submit(execute_node, nodes[node_id], accepted_inputs)
                futures[future] = node_id
                scheduled.add(node_id)
                _emit(event_sink, {"event": "node_started", "node_id": node_id})
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
                results[node_id] = result
                result_order.append(node_id)
                if result.get("status") != "PASS" or result.get("verdict") != "PASS":
                    blocked_result = result
                    _emit(
                        event_sink,
                        {
                            "event": "node_blocked",
                            "node_id": node_id,
                            "verdict": result.get("verdict"),
                        },
                    )
                    for pending in futures:
                        pending.cancel()
                    futures.clear()
                    break
                completed.add(node_id)
                _emit(event_sink, {"event": "node_completed", "node_id": node_id})
            if blocked_result is not None:
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
        if edge.target_kind == "node":
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
