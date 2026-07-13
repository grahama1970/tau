"""Typed transition effects consumed by the canonical DagPlan scheduler."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
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
    terminal_state: str = "success"


@dataclass(frozen=True, slots=True)
class DagTransitionView:
    plan: DagPlan
    node_states: dict[str, str]
    edge_states: dict[str, str]
    terminal_states: dict[str, str]
    running_node_ids: frozenset[str]
    deadline_monotonic: dict[str, float]
    now_monotonic: float


@dataclass(frozen=True, slots=True)
class DagEdgeSettlement:
    edge_id: str
    state: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DagNodeSettlement:
    node_id: str
    state: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DagNodeCancellation:
    node_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DagDeadlineArm:
    deadline_id: str
    deadline_monotonic: float
    reason_code: str


@dataclass(frozen=True, slots=True)
class DagRunBlock:
    failure_code: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DagTransitionBatch:
    edge_settlements: tuple[DagEdgeSettlement, ...] = ()
    node_settlements: tuple[DagNodeSettlement, ...] = ()
    node_cancellations: tuple[DagNodeCancellation, ...] = ()
    deadline_arms: tuple[DagDeadlineArm, ...] = ()
    deadline_cancellations: tuple[str, ...] = ()
    receipt_paths: tuple[str, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    block_run: DagRunBlock | None = None


@dataclass(frozen=True, slots=True)
class DagCommittedReceipt:
    path: str
    file_sha256: str


@dataclass(frozen=True, slots=True)
class DagPolicyReplayState:
    committed_receipts: tuple[DagCommittedReceipt, ...]
    node_states: dict[str, str]
    edge_states: dict[str, str]
    terminal_states: dict[str, str]


def transition_batch_to_payload(batch: DagTransitionBatch) -> dict[str, Any]:
    """Serialize a committed transition without persisting process-local clocks."""

    now_monotonic = time.monotonic()
    now_wall_ms = time.time_ns() // 1_000_000
    receipt_refs: list[dict[str, str]] = []
    for raw_path in batch.receipt_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise RuntimeError(f"dag_transition_receipt_missing:{path}")
        receipt_refs.append(
            {
                "path": str(path.resolve()),
                "file_sha256": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            }
        )
    return {
        "schema": "tau.dag_transition_batch.v1",
        "edge_settlements": [asdict(item) for item in batch.edge_settlements],
        "node_settlements": [asdict(item) for item in batch.node_settlements],
        "node_cancellations": [asdict(item) for item in batch.node_cancellations],
        "deadline_arms": [
            {
                "deadline_id": item.deadline_id,
                "deadline_due_at_ms": now_wall_ms
                + max(0, int((item.deadline_monotonic - now_monotonic) * 1000)),
                "reason_code": item.reason_code,
            }
            for item in batch.deadline_arms
        ],
        "deadline_cancellations": list(batch.deadline_cancellations),
        "receipt_refs": receipt_refs,
        "events": list(batch.events),
        "block_run": (
            {
                "failure_code": batch.block_run.failure_code,
                "message": batch.block_run.message,
                "evidence": batch.block_run.evidence,
            }
            if batch.block_run
            else None
        ),
    }


def transition_batch_from_payload(payload: Mapping[str, Any]) -> DagTransitionBatch:
    """Restore a transition and translate durable wall deadlines to monotonic time."""

    if payload.get("schema") != "tau.dag_transition_batch.v1":
        raise RuntimeError("dag_transition_replay_mismatch")
    now_monotonic = time.monotonic()
    now_wall_ms = time.time_ns() // 1_000_000
    block = payload.get("block_run")
    return DagTransitionBatch(
        edge_settlements=tuple(DagEdgeSettlement(**item) for item in payload["edge_settlements"]),
        node_settlements=tuple(DagNodeSettlement(**item) for item in payload["node_settlements"]),
        node_cancellations=tuple(
            DagNodeCancellation(**item) for item in payload["node_cancellations"]
        ),
        deadline_arms=tuple(
            DagDeadlineArm(
                deadline_id=str(item["deadline_id"]),
                deadline_monotonic=now_monotonic
                + max(0.0, (int(item["deadline_due_at_ms"]) - now_wall_ms) / 1000),
                reason_code=str(item["reason_code"]),
            )
            for item in payload["deadline_arms"]
        ),
        deadline_cancellations=tuple(str(item) for item in payload["deadline_cancellations"]),
        receipt_paths=tuple(str(item["path"]) for item in payload["receipt_refs"]),
        events=tuple(dict(item) for item in payload["events"]),
        block_run=(
            DagRunBlock(
                failure_code=str(block["failure_code"]),
                message=str(block["message"]),
                evidence=dict(block["evidence"]),
            )
            if isinstance(block, Mapping)
            else None
        ),
    )


class DagTransitionPolicy(Protocol):
    def validate_plan(self, plan: DagPlan) -> None: ...

    def restore(self, plan: DagPlan, replay: DagPolicyReplayState) -> None: ...

    def after_node_terminal(
        self,
        view: DagTransitionView,
        completion: DagNodeCompletion,
    ) -> DagTransitionBatch: ...

    def before_node_start(
        self,
        view: DagTransitionView,
        node_id: str,
        attempt: int,
    ) -> DagTransitionBatch: ...

    def on_deadline(
        self,
        view: DagTransitionView,
        deadline_id: str,
    ) -> DagTransitionBatch: ...

    def after_completion_batch(self, view: DagTransitionView) -> DagTransitionBatch: ...


class AllSuccessTransitionPolicy:
    """Settle every outgoing edge only after a successful final node result."""

    def validate_plan(self, plan: DagPlan) -> None:
        if plan.route_contracts or plan.join_contracts:
            raise RuntimeError("dag_transition_policy_required")

    def restore(self, plan: DagPlan, replay: DagPolicyReplayState) -> None:
        del plan, replay

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

    def before_node_start(
        self,
        view: DagTransitionView,
        node_id: str,
        attempt: int,
    ) -> DagTransitionBatch:
        return DagTransitionBatch()

    def on_deadline(
        self,
        view: DagTransitionView,
        deadline_id: str,
    ) -> DagTransitionBatch:
        raise RuntimeError(f"dag_transition_unknown_deadline:{deadline_id}")

    def after_completion_batch(self, view: DagTransitionView) -> DagTransitionBatch:
        return DagTransitionBatch()
