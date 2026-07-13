"""Typed transition effects consumed by the canonical DagPlan scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tau_coding.dag_runtime.model import DagPlan


@dataclass(frozen=True, slots=True)
class DagNodeCompletion:
    node_id: str
    attempt: int
    status: str
    verdict: str
    retryable: bool
    raw_result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DagTransitionView:
    plan: DagPlan
    node_states: dict[str, str]
    edge_states: dict[str, str]
    terminal_states: dict[str, str]
    running_node_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class DagEdgeSettlement:
    edge_id: str
    state: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DagRunBlock:
    failure_code: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DagTransitionBatch:
    edge_settlements: tuple[DagEdgeSettlement, ...] = ()
    block_run: DagRunBlock | None = None


class DagTransitionPolicy(Protocol):
    def validate_plan(self, plan: DagPlan) -> None: ...

    def after_node_terminal(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
    ) -> DagTransitionBatch: ...


class AllSuccessTransitionPolicy:
    """Settle every outgoing edge only after a successful final node result."""

    def validate_plan(self, plan: DagPlan) -> None:
        if plan.route_contracts or plan.join_contracts:
            raise RuntimeError("dag_transition_policy_required")

    def after_node_terminal(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
    ) -> DagTransitionBatch:
        if completion.status != "PASS" or completion.verdict != "PASS":
            return DagTransitionBatch(
                block_run=DagRunBlock(
                    failure_code=completion.verdict or "NODE_BLOCKED",
                    message="A final node attempt did not pass.",
                    evidence={"node_id": completion.node_id, "attempt": completion.attempt},
                )
            )
        return DagTransitionBatch(
            edge_settlements=tuple(
                DagEdgeSettlement(
                    edge_id=edge.edge_id,
                    state="success",
                    reason_code="source_passed",
                )
                for edge in view.plan.control_edges
                if edge.source_node_id == completion.node_id
            )
        )
