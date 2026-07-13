"""Project DAG route and join policy for the canonical DagPlan scheduler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau_coding.dag_join_decision import (
    JoinDecisionError,
    build_terminal_contribution,
    evaluate_join_decision,
    write_immutable_json,
)
from tau_coding.dag_route_decision import (
    RouteDecisionError,
    build_route_contract,
    evaluate_route_decision,
    write_route_decision_receipt,
)
from tau_coding.dag_runtime.model import DagPlan, DagPlanEdge, canonical_sha256
from tau_coding.dag_runtime.transition import (
    DagDeadlineArm,
    DagEdgeSettlement,
    DagNodeCancellation,
    DagNodeCompletion,
    DagNodeSettlement,
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
        self._contributions: dict[str, dict[str, Any]] = {}
        self._finalized_joins: set[str] = set()
        self._dirty_joins: set[str] = set()

    def validate_plan(self, plan: DagPlan) -> None:
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
        for frozen in plan.join_contracts:
            contract = frozen.to_value()
            if not isinstance(contract, dict):
                raise RuntimeError("invalid_dag_plan_join_contract")
            incoming = contract.get("incoming_edge_ids")
            if not isinstance(incoming, list) or not all(item in edge_ids for item in incoming):
                raise RuntimeError("dag_plan_join_edge_missing")
            if contract.get("policy_sha256") != canonical_sha256(contract.get("policy")):
                raise RuntimeError("dag_plan_join_policy_hash_mismatch")

    def before_node_start(
        self,
        view: DagTransitionView,
        node_id: str,
        attempt: int,
    ) -> DagTransitionBatch:
        arms: list[DagDeadlineArm] = []
        events: list[dict[str, Any]] = []
        for join in self._joins_from_source(view.plan, node_id):
            join_id = str(join["join_node_id"])
            if join_id in view.deadline_monotonic or join_id in self._finalized_joins:
                continue
            timeout = float(join["policy"]["timeout_seconds"])
            deadline = view.now_monotonic + timeout
            arms.append(DagDeadlineArm(join_id, deadline, "join_source_started"))
            events.append(
                {
                    "event": "join_deadline_armed",
                    "join_node_id": join_id,
                    "source_node_id": node_id,
                    "origin": "source_start",
                    "timeout_seconds": timeout,
                }
            )
        return DagTransitionBatch(deadline_arms=tuple(arms), events=tuple(events))

    def on_deadline(
        self,
        view: DagTransitionView,
        deadline_id: str,
    ) -> DagTransitionBatch:
        join = self._join_by_id(view.plan, deadline_id)
        if join is None or deadline_id in self._finalized_joins:
            return DagTransitionBatch(deadline_cancellations=(deadline_id,))
        settlements: list[DagEdgeSettlement] = []
        cancellations: list[DagNodeCancellation] = []
        paths: list[str] = []
        events: list[dict[str, Any]] = []
        for edge_id in join["incoming_edge_ids"]:
            if edge_id in self._contributions:
                continue
            edge = self._edge_by_id(view.plan, edge_id)
            try:
                path = self._record_contribution(
                    view,
                    join,
                    edge_id=edge_id,
                    state="timed_out",
                    reason_code="join_timeout",
                    basis={
                        "kind": "join_timeout",
                        "join_node_id": deadline_id,
                        "timeout_seconds": join["policy"]["timeout_seconds"],
                    },
                )
            except (OSError, JoinDecisionError) as exc:
                return self._contribution_write_block(exc, join_id=deadline_id)
            paths.append(path)
            settlements.append(DagEdgeSettlement(edge_id, "timed_out", "join_timeout"))
            cancellations.append(DagNodeCancellation(edge.source_node_id, "join_timeout"))
            events.append(self._contribution_event(join, edge, "timed_out", path))
        final = self._evaluate_join(view, join)
        return self._combine(
            DagTransitionBatch(
                edge_settlements=tuple(settlements),
                node_cancellations=tuple(cancellations),
                deadline_cancellations=(deadline_id,),
                receipt_paths=tuple(paths),
                events=tuple(events)
                + (
                    {
                        "event": "join_timeout_expired",
                        "join_node_id": deadline_id,
                        "timeout_seconds": join["policy"]["timeout_seconds"],
                    },
                ),
            ),
            final,
        )

    def after_completion_batch(self, view: DagTransitionView) -> DagTransitionBatch:
        dirty = sorted(self._dirty_joins)
        self._dirty_joins.clear()
        return self._combine(
            *(
                self._evaluate_join(view, join)
                for join_id in dirty
                if (join := self._join_by_id(view.plan, join_id)) is not None
            )
        )

    def after_node_terminal(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
    ) -> DagTransitionBatch:
        outgoing = [
            edge for edge in view.plan.control_edges if edge.source_node_id == completion.node_id
        ]
        if completion.node_id in {
            str(item.to_value()["join_node_id"]) for item in view.plan.join_contracts
        }:
            return DagTransitionBatch()
        if completion.terminal_state == "skipped":
            base = DagTransitionBatch(
                edge_settlements=tuple(
                    DagEdgeSettlement(edge.edge_id, "skipped", "source_skipped")
                    for edge in outgoing
                )
            )
            return self._with_join_contributions(view, completion, base)
        if completion.status != "PASS" or completion.verdict != "PASS":
            if self._joins_from_source(view.plan, completion.node_id):
                return self._with_join_contributions(view, completion, DagTransitionBatch())
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
            base = DagTransitionBatch(
                edge_settlements=tuple(
                    DagEdgeSettlement(edge.edge_id, "success", "source_passed") for edge in outgoing
                )
            )
        else:
            base = self._evaluate_route(view, completion, route_wrapper)
        if base.block_run is not None:
            return base
        return self._with_join_contributions(view, completion, base)

    def _with_join_contributions(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
        base: DagTransitionBatch,
    ) -> DagTransitionBatch:
        base = DagTransitionBatch(
            edge_settlements=tuple(
                item for item in base.edge_settlements if item.edge_id not in self._contributions
            ),
            node_settlements=base.node_settlements,
            node_cancellations=base.node_cancellations,
            deadline_arms=base.deadline_arms,
            deadline_cancellations=base.deadline_cancellations,
            receipt_paths=base.receipt_paths,
            events=base.events,
            block_run=base.block_run,
        )
        batches = [base]
        for join in self._joins_from_source(view.plan, completion.node_id):
            for edge_id in join["incoming_edge_ids"]:
                edge = self._edge_by_id(view.plan, edge_id)
                if edge.source_node_id != completion.node_id:
                    continue
                state = (
                    completion.terminal_state
                    if completion.terminal_state != "success"
                    else "success"
                    if completion.status == "PASS" and completion.verdict == "PASS"
                    else "failed"
                )
                reason = {
                    "success": "source_passed",
                    "skipped": "source_skipped",
                    "failed": "source_failed",
                    "blocked": "source_blocked",
                    "cancelled": "source_cancelled",
                    "timed_out": "source_timed_out",
                }.get(state, "source_failed")
                if edge_id in self._contributions:
                    existing = str(self._contributions[edge_id]["state"])
                    if existing != state:
                        batches.append(
                            DagTransitionBatch(
                                events=(
                                    {
                                        "event": "late_terminal_contribution_ignored",
                                        "edge_index": edge.source_ordinal,
                                        "existing_state": existing,
                                        "late_state": state,
                                    },
                                )
                            )
                        )
                    continue
                if completion.attempt == 0:
                    basis_kind = f"upstream_{state}"
                elif completion.raw_result.get("live") is False:
                    basis_kind = "virtual_node_completed"
                else:
                    basis_kind = "source_terminal"
                try:
                    path = self._record_contribution(
                        view,
                        join,
                        edge_id=edge_id,
                        state=state,
                        reason_code=reason,
                        basis={"kind": basis_kind},
                        source_binding=(
                            {
                                "source_node_id": completion.node_id,
                                "attempt": completion.attempt,
                            }
                            if basis_kind == "source_terminal"
                            else {}
                        ),
                    )
                except (OSError, JoinDecisionError) as exc:
                    batches.append(
                        self._contribution_write_block(
                            exc,
                            join_id=str(join["join_node_id"]),
                        )
                    )
                    return self._combine(*batches)
                contribution_events = [self._contribution_event(join, edge, state, path)]
                if state in {"failed", "blocked"}:
                    contribution_events.append(
                        {
                            "event": "branch_failure_contributed_to_join",
                            "node_id": completion.node_id,
                            "join_node_id": join["join_node_id"],
                            "state": state,
                        }
                    )
                batches.append(
                    DagTransitionBatch(
                        edge_settlements=(DagEdgeSettlement(edge_id, state, reason),),
                        receipt_paths=(path,),
                        events=tuple(contribution_events),
                    )
                )
            self._dirty_joins.add(str(join["join_node_id"]))
        return self._combine(*batches)

    def _evaluate_join(
        self,
        view: DagTransitionView,
        join: dict[str, Any],
    ) -> DagTransitionBatch:
        join_id = str(join["join_node_id"])
        if join_id in self._finalized_joins:
            return DagTransitionBatch()
        incoming_ids = list(join["incoming_edge_ids"])
        incoming = [self._edge_contract(view.plan, edge_id) for edge_id in incoming_ids]
        try:
            decision = evaluate_join_decision(
                dag_id=self._dag_id,
                goal_hash=self._goal_hash,
                join_node_id=join_id,
                join_policy=join["policy"],
                incoming_edges=incoming,
                contributions=[
                    self._contributions[edge_id]
                    for edge_id in incoming_ids
                    if edge_id in self._contributions
                ],
            )
        except JoinDecisionError as exc:
            return DagTransitionBatch(
                block_run=DagRunBlock(exc.code, "Tau rejected the join contribution set.", {})
            )
        if decision["status"] == "WAIT":
            return DagTransitionBatch()
        if decision["status"] == "TERMINAL_INTENT":
            settlements: list[DagEdgeSettlement] = []
            cancellations: list[DagNodeCancellation] = []
            paths: list[str] = []
            events: list[dict[str, Any]] = []
            for edge_id in decision["pending_edge_indexes"]:
                # The pure evaluator reports source ordinals; resolve them back to plan edge IDs.
                plan_edge = next(
                    edge
                    for edge in view.plan.control_edges
                    if edge.source_ordinal == edge_id and edge.target_id == join_id
                )
                try:
                    path = self._record_contribution(
                        view,
                        join,
                        edge_id=plan_edge.edge_id,
                        state="cancelled",
                        reason_code="join_short_circuit",
                        basis={
                            "kind": "join_short_circuit",
                            "join_node_id": join_id,
                            "terminal_intent": decision["decision"],
                        },
                    )
                except (OSError, JoinDecisionError) as exc:
                    return self._contribution_write_block(exc, join_id=join_id)
                settlements.append(
                    DagEdgeSettlement(plan_edge.edge_id, "cancelled", "join_short_circuit")
                )
                cancellations.append(
                    DagNodeCancellation(plan_edge.source_node_id, "join_short_circuit")
                )
                paths.append(path)
                events.append(self._contribution_event(join, plan_edge, "cancelled", path))
            return self._combine(
                DagTransitionBatch(
                    edge_settlements=tuple(settlements),
                    node_cancellations=tuple(cancellations),
                    receipt_paths=tuple(paths),
                    events=tuple(events),
                ),
                self._evaluate_join(view, join),
            )
        decision_path = self._receipt_dir / "join-decisions" / f"{join_id}.json"
        try:
            write_immutable_json(
                decision_path,
                decision,
                conflict_code="join_decision_receipt_write_failed",
            )
        except (OSError, JoinDecisionError) as exc:
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    getattr(exc, "code", "join_decision_receipt_write_failed"),
                    "Tau could not persist the join decision receipt.",
                    {"join_node_id": join_id},
                )
            )
        self._finalized_joins.add(join_id)
        terminal_state = {"release": "success", "skip": "skipped", "block": "blocked"}[
            decision["decision"]
        ]
        outgoing = tuple(
            DagEdgeSettlement(edge.edge_id, terminal_state, decision["reason_code"])
            for edge in view.plan.control_edges
            if edge.source_node_id == join_id
        )
        block = None
        if decision["decision"] == "block":
            block = DagRunBlock(
                decision["reason_code"],
                "Declared DAG join policy blocked continuation.",
                {"join_node_id": join_id, "join_decision_receipt": str(decision_path)},
            )
        return DagTransitionBatch(
            edge_settlements=outgoing,
            node_settlements=(DagNodeSettlement(join_id, terminal_state, decision["reason_code"]),),
            deadline_cancellations=(join_id,),
            receipt_paths=(str(decision_path),),
            events=(
                {
                    "event": "join_decided",
                    "join_node_id": join_id,
                    "decision": decision["decision"],
                    "reason_code": decision["reason_code"],
                    "join_decision_receipt": str(decision_path),
                },
            ),
            block_run=block,
        )

    def _record_contribution(
        self,
        view: DagTransitionView,
        join: dict[str, Any],
        *,
        edge_id: str,
        state: str,
        reason_code: str,
        basis: dict[str, Any],
        source_binding: dict[str, Any] | None = None,
    ) -> str:
        edge = self._edge_by_id(view.plan, edge_id)
        payload = build_terminal_contribution(
            dag_id=self._dag_id,
            goal_hash=self._goal_hash,
            join_node_id=str(join["join_node_id"]),
            edge_contract=self._edge_contract(view.plan, edge_id),
            state=state,
            reason_code=reason_code,
            basis=basis,
            join_policy=join["policy"],
            incoming_count=len(join["incoming_edge_ids"]),
            source_binding=source_binding,
        )
        path = (
            self._receipt_dir
            / "terminal-contributions"
            / str(join["join_node_id"])
            / f"edge-{int(edge.source_ordinal or 0):04d}.json"
        )
        write_immutable_json(path, payload, conflict_code="terminal_contribution_conflict")
        self._contributions[edge_id] = payload
        return str(path)

    @staticmethod
    def _contribution_write_block(
        exc: OSError | JoinDecisionError,
        *,
        join_id: str,
    ) -> DagTransitionBatch:
        return DagTransitionBatch(
            block_run=DagRunBlock(
                getattr(exc, "code", "terminal_contribution_write_failed"),
                "Tau could not persist an immutable terminal contribution.",
                {"join_node_id": join_id, "error": str(exc)},
            )
        )

    @staticmethod
    def _combine(*batches: DagTransitionBatch) -> DagTransitionBatch:
        block = next((item.block_run for item in batches if item.block_run is not None), None)
        return DagTransitionBatch(
            edge_settlements=tuple(x for item in batches for x in item.edge_settlements),
            node_settlements=tuple(x for item in batches for x in item.node_settlements),
            node_cancellations=tuple(x for item in batches for x in item.node_cancellations),
            deadline_arms=tuple(x for item in batches for x in item.deadline_arms),
            deadline_cancellations=tuple(
                x for item in batches for x in item.deadline_cancellations
            ),
            receipt_paths=tuple(x for item in batches for x in item.receipt_paths),
            events=tuple(x for item in batches for x in item.events),
            block_run=block,
        )

    @staticmethod
    def _edge_by_id(plan: DagPlan, edge_id: str) -> DagPlanEdge:
        return next(edge for edge in plan.control_edges if edge.edge_id == edge_id)

    def _edge_contract(self, plan: DagPlan, edge_id: str) -> dict[str, Any]:
        edge = self._edge_by_id(plan, edge_id)
        return {
            "edge_index": edge.source_ordinal,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_id,
            "condition": edge.condition.to_value() if edge.condition else None,
        }

    @staticmethod
    def _join_by_id(plan: DagPlan, join_id: str) -> dict[str, Any] | None:
        return next(
            (
                item.to_value()
                for item in plan.join_contracts
                if item.to_value()["join_node_id"] == join_id
            ),
            None,
        )

    def _joins_from_source(self, plan: DagPlan, node_id: str) -> list[dict[str, Any]]:
        outgoing = {edge.edge_id for edge in plan.control_edges if edge.source_node_id == node_id}
        return [
            item.to_value()
            for item in plan.join_contracts
            if outgoing.intersection(item.to_value()["incoming_edge_ids"])
        ]

    @staticmethod
    def _contribution_event(
        join: dict[str, Any], edge: Any, state: str, path: str
    ) -> dict[str, Any]:
        return {
            "event": "terminal_contribution_recorded",
            "join_node_id": join["join_node_id"],
            "edge_index": edge.source_ordinal,
            "source_node_id": edge.source_node_id,
            "state": state,
            "receipt": path,
        }

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
