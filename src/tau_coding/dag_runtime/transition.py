"""Typed transition effects consumed by the canonical DagPlan scheduler."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from tau_coding.dag_runtime.model import DagPlan, canonical_sha256

TRANSITION_BATCH_SCHEMA = "tau.dag_transition_batch.v1"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_TRANSITION_STATES = frozenset(
    {"success", "failed", "blocked", "skipped", "cancelled", "timed_out"}
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "edge_settlements",
        "node_settlements",
        "node_cancellations",
        "deadline_arms",
        "deadline_cancellations",
        "receipt_refs",
        "events",
        "block_run",
    }
)


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
        "schema": TRANSITION_BATCH_SCHEMA,
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


def transition_batch_from_payload(
    payload: Mapping[str, Any],
    *,
    plan: DagPlan | None = None,
    verify_receipts: bool = False,
    active_deadlines: Mapping[str, float] | None = None,
) -> DagTransitionBatch:
    """Restore a transition and translate durable wall deadlines to monotonic time."""

    _validate_top_level(payload)
    now_monotonic = time.monotonic()
    now_wall_ms = time.time_ns() // 1_000_000
    edge_settlements = tuple(
        DagEdgeSettlement(
            edge_id=_required_string(item, "edge_id", "dag_transition_edge_id_invalid"),
            state=_required_state(item, "state", "dag_transition_edge_state_invalid"),
            reason_code=_required_reason(item, "reason_code", "dag_transition_reason_invalid"),
        )
        for item in _objects(
            payload,
            "edge_settlements",
            frozenset({"edge_id", "state", "reason_code"}),
            "dag_transition_edge_invalid",
        )
    )
    node_settlements = tuple(
        DagNodeSettlement(
            node_id=_required_string(item, "node_id", "dag_transition_node_id_invalid"),
            state=_required_state(item, "state", "dag_transition_node_state_invalid"),
            reason_code=_required_reason(item, "reason_code", "dag_transition_reason_invalid"),
        )
        for item in _objects(
            payload,
            "node_settlements",
            frozenset({"node_id", "state", "reason_code"}),
            "dag_transition_node_invalid",
        )
    )
    node_cancellations = tuple(
        DagNodeCancellation(
            node_id=_required_string(item, "node_id", "dag_transition_node_id_invalid"),
            reason_code=_required_reason(item, "reason_code", "dag_transition_reason_invalid"),
        )
        for item in _objects(
            payload,
            "node_cancellations",
            frozenset({"node_id", "reason_code"}),
            "dag_transition_cancellation_invalid",
        )
    )
    deadline_arms = tuple(
        _deadline_arm_from_payload(item, now_monotonic=now_monotonic, now_wall_ms=now_wall_ms)
        for item in _objects(
            payload,
            "deadline_arms",
            frozenset({"deadline_id", "deadline_due_at_ms", "reason_code"}),
            "dag_transition_deadline_invalid",
        )
    )
    deadline_cancellations = tuple(
        _required_plain_string(item, "dag_transition_deadline_id_invalid")
        for item in _list(payload, "deadline_cancellations", "dag_transition_deadline_invalid")
    )
    receipt_paths = tuple(
        _receipt_path_from_payload(item, verify_receipts=verify_receipts)
        for item in _objects(
            payload,
            "receipt_refs",
            frozenset({"path", "file_sha256"}),
            "dag_transition_receipt_invalid",
        )
    )
    events = tuple(
        _event_from_payload(item)
        for item in _list(payload, "events", "dag_transition_event_invalid")
    )
    block = _block_from_payload(payload.get("block_run"))
    batch = DagTransitionBatch(
        edge_settlements=edge_settlements,
        node_settlements=node_settlements,
        node_cancellations=node_cancellations,
        deadline_arms=deadline_arms,
        deadline_cancellations=deadline_cancellations,
        receipt_paths=receipt_paths,
        events=events,
        block_run=block,
    )
    validate_transition_batch(plan=plan, batch=batch, active_deadlines=active_deadlines)
    return batch


def validate_transition_batch(
    *,
    plan: DagPlan | None,
    batch: DagTransitionBatch,
    active_deadlines: Mapping[str, float] | None = None,
) -> None:
    """Fail closed before a transition effect becomes authoritative state."""

    edge_ids = {edge.edge_id for edge in plan.control_edges} if plan is not None else None
    node_ids = {node.node_id for node in plan.nodes} if plan is not None else None
    terminal_ids = (
        {terminal.terminal_id for terminal in plan.terminal_endpoints}
        if plan is not None
        else None
    )
    seen_edges: dict[str, str] = {}
    for edge_settlement in batch.edge_settlements:
        if edge_ids is not None and edge_settlement.edge_id not in edge_ids:
            raise RuntimeError(f"dag_transition_unknown_edge:{edge_settlement.edge_id}")
        _remember_effect(
            seen_edges,
            edge_settlement.edge_id,
            edge_settlement.state,
            "dag_transition_edge",
        )
        _validate_state(edge_settlement.state, "dag_transition_edge_state_invalid")
        _validate_reason(edge_settlement.reason_code, "dag_transition_reason_invalid")
    seen_nodes: dict[str, str] = {}
    for node_settlement in batch.node_settlements:
        if (
            node_ids is not None
            and terminal_ids is not None
            and node_settlement.node_id not in node_ids
            and node_settlement.node_id not in terminal_ids
        ):
            raise RuntimeError(f"dag_transition_unknown_node:{node_settlement.node_id}")
        _remember_effect(
            seen_nodes,
            node_settlement.node_id,
            node_settlement.state,
            "dag_transition_node",
        )
        _validate_state(node_settlement.state, "dag_transition_node_state_invalid")
        _validate_reason(node_settlement.reason_code, "dag_transition_reason_invalid")
    seen_cancellations: set[str] = set()
    for cancellation in batch.node_cancellations:
        if node_ids is not None and cancellation.node_id not in node_ids:
            raise RuntimeError(f"dag_transition_unknown_cancellation:{cancellation.node_id}")
        if cancellation.node_id in seen_cancellations:
            raise RuntimeError(f"dag_transition_duplicate_cancellation:{cancellation.node_id}")
        seen_cancellations.add(cancellation.node_id)
        _validate_reason(cancellation.reason_code, "dag_transition_reason_invalid")
    seen_deadline_arms: set[str] = set()
    for arm in batch.deadline_arms:
        if not arm.deadline_id:
            raise RuntimeError("dag_transition_deadline_id_invalid")
        if arm.deadline_id in seen_deadline_arms:
            raise RuntimeError(f"dag_transition_duplicate_deadline:{arm.deadline_id}")
        seen_deadline_arms.add(arm.deadline_id)
        if not math.isfinite(arm.deadline_monotonic):
            raise RuntimeError(f"dag_transition_deadline_due_invalid:{arm.deadline_id}")
        _validate_reason(arm.reason_code, "dag_transition_reason_invalid")
    seen_deadline_cancellations: set[str] = set()
    for deadline_id in batch.deadline_cancellations:
        if not deadline_id:
            raise RuntimeError("dag_transition_deadline_id_invalid")
        if deadline_id in seen_deadline_cancellations:
            raise RuntimeError(f"dag_transition_duplicate_deadline_cancellation:{deadline_id}")
        seen_deadline_cancellations.add(deadline_id)
        if active_deadlines is not None and deadline_id not in active_deadlines:
            raise RuntimeError(f"dag_transition_unknown_deadline:{deadline_id}")
    for path in batch.receipt_paths:
        if not path:
            raise RuntimeError("dag_transition_receipt_invalid")
    for event in batch.events:
        _ensure_canonical(event, "dag_transition_event_non_canonical")
    if batch.block_run is not None:
        _validate_reason(batch.block_run.failure_code, "dag_transition_block_code_invalid")
        if not isinstance(batch.block_run.message, str) or not batch.block_run.message:
            raise RuntimeError("dag_transition_block_message_invalid")
        _ensure_canonical(batch.block_run.evidence, "dag_transition_block_evidence_invalid")


def _validate_top_level(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != TRANSITION_BATCH_SCHEMA:
        raise RuntimeError("dag_transition_replay_mismatch")
    extra = set(payload) - _TOP_LEVEL_KEYS
    missing = _TOP_LEVEL_KEYS - set(payload)
    if extra or missing:
        raise RuntimeError("dag_transition_schema_invalid")


def _list(payload: Mapping[str, Any], key: str, code: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(code)
    return value


def _objects(
    payload: Mapping[str, Any],
    key: str,
    expected_keys: frozenset[str],
    code: str,
) -> tuple[Mapping[str, Any], ...]:
    objects: list[Mapping[str, Any]] = []
    for item in _list(payload, key, code):
        if not isinstance(item, Mapping) or set(item) != expected_keys:
            raise RuntimeError(code)
        objects.append(item)
    return tuple(objects)


def _required_plain_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(code)
    return value


def _required_string(payload: Mapping[str, Any], key: str, code: str) -> str:
    return _required_plain_string(payload.get(key), code)


def _required_state(payload: Mapping[str, Any], key: str, code: str) -> str:
    state = _required_string(payload, key, code)
    _validate_state(state, code)
    return state


def _required_reason(payload: Mapping[str, Any], key: str, code: str) -> str:
    reason = _required_string(payload, key, code)
    _validate_reason(reason, code)
    return reason


def _validate_state(state: str, code: str) -> None:
    if state not in _TRANSITION_STATES:
        raise RuntimeError(code)


def _validate_reason(reason: str, code: str) -> None:
    if not _REASON_RE.fullmatch(reason):
        raise RuntimeError(code)


def _deadline_arm_from_payload(
    item: Mapping[str, Any],
    *,
    now_monotonic: float,
    now_wall_ms: int,
) -> DagDeadlineArm:
    due_at = item.get("deadline_due_at_ms")
    if not isinstance(due_at, int) or isinstance(due_at, bool):
        raise RuntimeError("dag_transition_deadline_due_invalid")
    deadline_id = _required_string(item, "deadline_id", "dag_transition_deadline_id_invalid")
    return DagDeadlineArm(
        deadline_id=deadline_id,
        deadline_monotonic=now_monotonic + max(0.0, (due_at - now_wall_ms) / 1000),
        reason_code=_required_reason(item, "reason_code", "dag_transition_reason_invalid"),
    )


def _receipt_path_from_payload(item: Mapping[str, Any], *, verify_receipts: bool) -> str:
    path = _required_string(item, "path", "dag_transition_receipt_invalid")
    digest = _required_string(item, "file_sha256", "dag_transition_receipt_hash_invalid")
    if _SHA256_RE.fullmatch(digest) is None:
        raise RuntimeError("dag_transition_receipt_hash_invalid")
    if verify_receipts:
        receipt_path = Path(path)
        if not receipt_path.is_file():
            raise RuntimeError(f"dag_transition_receipt_missing:{path}")
        observed = f"sha256:{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}"
        if observed != digest:
            raise RuntimeError(f"dag_transition_receipt_hash_mismatch:{path}")
    return path


def _event_from_payload(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise RuntimeError("dag_transition_event_invalid")
    event = dict(item)
    _ensure_canonical(event, "dag_transition_event_non_canonical")
    schema = event.get("schema")
    if schema is not None and not isinstance(schema, str):
        raise RuntimeError("dag_transition_event_invalid")
    return event


def _block_from_payload(block: Any) -> DagRunBlock | None:
    if block is None:
        return None
    if not isinstance(block, Mapping) or set(block) != {"failure_code", "message", "evidence"}:
        raise RuntimeError("dag_transition_block_invalid")
    evidence = block.get("evidence")
    if not isinstance(evidence, Mapping):
        raise RuntimeError("dag_transition_block_evidence_invalid")
    return DagRunBlock(
        failure_code=_required_reason(block, "failure_code", "dag_transition_block_code_invalid"),
        message=_required_string(block, "message", "dag_transition_block_message_invalid"),
        evidence=dict(evidence),
    )


def _ensure_canonical(value: Mapping[str, Any], code: str) -> None:
    try:
        canonical_sha256(dict(value))
    except (RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(code) from exc


def _remember_effect(seen: dict[str, str], effect_id: str, state: str, prefix: str) -> None:
    existing = seen.get(effect_id)
    if existing is None:
        seen[effect_id] = state
        return
    if existing == state:
        raise RuntimeError(f"{prefix}_duplicate:{effect_id}")
    raise RuntimeError(f"{prefix}_conflict:{effect_id}")


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
