"""Project DAG route policy for the canonical DagPlan scheduler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau_coding.dag_route_decision import (
    RouteDecisionError,
    build_route_contract,
    evaluate_route_decision,
    write_route_decision_receipt,
)
from tau_coding.dag_runtime.model import DagPlan, canonical_sha256
from tau_coding.dag_runtime.transition import (
    DagEdgeSettlement,
    DagNodeCompletion,
    DagRunBlock,
    DagTransitionBatch,
    DagTransitionView,
)


class ProjectDagTransitionPolicy:
    """Interpret project route contracts without owning an execution loop."""

    def __init__(self, *, receipt_dir: Path, dag_id: str, goal_hash: str) -> None:
        self._receipt_dir = receipt_dir
        self._dag_id = dag_id
        self._goal_hash = goal_hash

    def validate_plan(self, plan: DagPlan) -> None:
        if plan.join_contracts:
            raise RuntimeError("dag_join_transition_policy_required")
        edge_ids = {edge.edge_id for edge in plan.control_edges}
        for frozen in plan.route_contracts:
            contract = frozen.to_value()
            if not isinstance(contract, dict):
                raise RuntimeError("invalid_dag_plan_route_contract")
            claimed = contract.get("contract_sha256")
            unsigned = {key: value for key, value in contract.items() if key != "contract_sha256"}
            if claimed != canonical_sha256(unsigned):
                raise RuntimeError("dag_plan_route_contract_hash_mismatch")
            ordered = contract.get("ordered_edge_ids")
            if not isinstance(ordered, list) or not all(item in edge_ids for item in ordered):
                raise RuntimeError("dag_plan_route_edge_missing")

    def after_node_terminal(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
    ) -> DagTransitionBatch:
        outgoing = [
            edge for edge in view.plan.control_edges if edge.source_node_id == completion.node_id
        ]
        if completion.terminal_state == "skipped":
            return DagTransitionBatch(
                edge_settlements=tuple(
                    DagEdgeSettlement(edge.edge_id, "skipped", "source_skipped")
                    for edge in outgoing
                )
            )
        if completion.status != "PASS" or completion.verdict != "PASS":
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    completion.verdict,
                    "Project node did not reach a routeable terminal result.",
                    {"node_id": completion.node_id, "attempt": completion.attempt},
                )
            )
        route_wrapper = next(
            (
                item.to_value()
                for item in view.plan.route_contracts
                if item.to_value().get("source_node_id") == completion.node_id
            ),
            None,
        )
        if route_wrapper is None:
            return DagTransitionBatch(
                edge_settlements=tuple(
                    DagEdgeSettlement(edge.edge_id, "success", "source_passed")
                    for edge in outgoing
                )
            )
        return self._evaluate_route(view, completion, route_wrapper)

    def _evaluate_route(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
        wrapper: dict[str, Any],
    ) -> DagTransitionBatch:
        edges_by_id = {edge.edge_id: edge for edge in view.plan.control_edges}
        ordered_ids = wrapper["ordered_edge_ids"]
        route_edges = [edges_by_id[edge_id] for edge_id in ordered_ids]
        accepted = completion.raw_result.get("accepted_output")
        source_result = accepted.get("result") if isinstance(accepted, dict) else None
        if not isinstance(source_result, dict):
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    "route_source_result_missing",
                    "Conditional source response must contain an object result.",
                    {"node_id": completion.node_id},
                )
            )
        try:
            route_contract = build_route_contract(
                source_node_id=completion.node_id,
                mode=wrapper.get("mode"),
                edges=[
                    {
                        "edge_index": edge.source_ordinal,
                        "target": edge.target_id,
                        "condition": edge.condition.to_value() if edge.condition else None,
                    }
                    for edge in route_edges
                ],
            )
            decision = evaluate_route_decision(
                dag_id=self._dag_id,
                goal_hash=self._goal_hash,
                source_node_id=completion.node_id,
                attempt=completion.attempt,
                source_result=source_result,
                route_contract=route_contract,
            )
        except RouteDecisionError as exc:
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    exc.code,
                    "Typed route evaluation rejected the source result.",
                    {"node_id": completion.node_id, "errors": [str(exc)]},
                )
            )
        path = (
            self._receipt_dir
            / "route-decisions"
            / completion.node_id
            / f"attempt-{completion.attempt:03d}.json"
        )
        try:
            write_route_decision_receipt(path, decision)
        except OSError as exc:
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    "route_decision_receipt_write_failed",
                    "Tau could not persist the route decision receipt.",
                    {"node_id": completion.node_id, "errors": [str(exc)]},
                )
            )
        if decision["status"] != "PASS":
            return DagTransitionBatch(
                receipt_paths=(str(path),),
                events=(
                    {
                        "event": "route_decided",
                        "node_id": completion.node_id,
                        "attempt": completion.attempt,
                        "status": decision["status"],
                        "selected_targets": [],
                        "route_decision_receipt": str(path),
                    },
                ),
                block_run=DagRunBlock(
                    str(decision["failure_code"]),
                    "Typed route decision blocked successor activation.",
                    {
                        "node_id": completion.node_id,
                        "route_decision_receipt": str(path),
                        "selected_targets": [],
                    },
                ),
            )
        selected = set(decision["selected_targets"])
        return DagTransitionBatch(
            edge_settlements=tuple(
                DagEdgeSettlement(
                    edge.edge_id,
                    "success" if edge.target_id in selected else "skipped",
                    "route_selected" if edge.target_id in selected else "route_unselected",
                )
                for edge in route_edges
            ),
            receipt_paths=(str(path),),
            events=(
                {
                    "event": "route_decided",
                    "node_id": completion.node_id,
                    "attempt": completion.attempt,
                    "status": decision["status"],
                    "selected_targets": list(decision["selected_targets"]),
                    "route_decision_receipt": str(path),
                },
            ),
        )
