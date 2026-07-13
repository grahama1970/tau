"""Immutable, backend-neutral representation of a validated Tau DAG."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

DAG_PLAN_SCHEMA = "tau.dag_plan.v1"


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"DAG plan value is not canonical JSON: {exc}") from exc


def canonical_sha256(payload: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class FrozenJson:
    """Canonical JSON held as text so nested caller mutation cannot alter a plan."""

    canonical: str

    @classmethod
    def from_value(cls, value: object) -> FrozenJson:
        return cls(canonical=canonical_json(value))

    def to_value(self) -> Any:
        return json.loads(self.canonical)


@dataclass(frozen=True, slots=True)
class DagPlanTerminal:
    terminal_id: str
    kind: str
    origin: str

    def to_payload(self) -> dict[str, str]:
        return {
            "terminal_id": self.terminal_id,
            "kind": self.kind,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class DagPlanEdge:
    edge_id: str
    source_node_id: str
    target_id: str
    target_kind: str
    condition: FrozenJson | None
    source_ordinal: int | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_node_id": self.source_node_id,
            "target": {"kind": self.target_kind, "id": self.target_id},
            "condition": self.condition.to_value() if self.condition else None,
            "source_ordinal": self.source_ordinal,
        }


@dataclass(frozen=True, slots=True)
class DagPlanContextBinding:
    binding_id: str
    source_node_id: str
    target_node_id: str
    control_edge_id: str
    projection: str
    activation: str
    origin: str

    def to_payload(self) -> dict[str, str]:
        return {
            "binding_id": self.binding_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "control_edge_id": self.control_edge_id,
            "projection": self.projection,
            "activation": self.activation,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class DagPlanNode:
    node_id: str
    role: str
    executor: str
    adapter_kind: str
    adapter_config: FrozenJson
    max_attempts: int
    timeout_kind: str
    timeout_seconds: float | None
    required_evidence: tuple[str, ...]
    static_context: FrozenJson
    requested_capabilities: tuple[FrozenJson, ...]
    source_bindings: tuple[FrozenJson, ...]
    source_extensions: FrozenJson

    def to_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "executor": self.executor,
            "adapter": {
                "kind": self.adapter_kind,
                "config": self.adapter_config.to_value(),
            },
            "retry_policy": {"max_attempts": self.max_attempts},
            "timeout_policy": {
                "kind": self.timeout_kind,
                "seconds": self.timeout_seconds,
            },
            "required_evidence": list(self.required_evidence),
            "static_context": self.static_context.to_value(),
            "requested_capabilities": [item.to_value() for item in self.requested_capabilities],
            "source_bindings": [item.to_value() for item in self.source_bindings],
            "source_extensions": self.source_extensions.to_value(),
        }


@dataclass(frozen=True, slots=True)
class DagPlan:
    schema: str
    plan_id: str
    source_family: str
    source_schema: str
    source_logical_id: str
    source_payload_sha256: str
    goal_binding: FrozenJson
    target_binding: FrozenJson
    entry_node_ids: tuple[str, ...]
    terminal_endpoints: tuple[DagPlanTerminal, ...]
    completion_policy: str
    nodes: tuple[DagPlanNode, ...]
    control_edges: tuple[DagPlanEdge, ...]
    context_bindings: tuple[DagPlanContextBinding, ...]
    route_contracts: tuple[FrozenJson, ...]
    join_contracts: tuple[FrozenJson, ...]
    required_evidence: tuple[str, ...]
    fail_closed_on: tuple[str, ...]
    security_declarations: FrozenJson
    execution_limits: FrozenJson
    source_extensions: FrozenJson
    plan_sha256: str = ""

    def to_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "plan_id": self.plan_id,
            "source": {
                "family": self.source_family,
                "schema": self.source_schema,
                "logical_id": self.source_logical_id,
                "canonical_source_sha256": self.source_payload_sha256,
            },
            "goal_binding": self.goal_binding.to_value(),
            "target_binding": self.target_binding.to_value(),
            "entry_node_ids": list(self.entry_node_ids),
            "terminal_endpoints": [item.to_payload() for item in self.terminal_endpoints],
            "completion_policy": {"kind": self.completion_policy},
            "nodes": [item.to_payload() for item in self.nodes],
            "control_edges": [item.to_payload() for item in self.control_edges],
            "context_bindings": [item.to_payload() for item in self.context_bindings],
            "route_contracts": [item.to_value() for item in self.route_contracts],
            "join_contracts": [item.to_value() for item in self.join_contracts],
            "required_evidence": list(self.required_evidence),
            "fail_closed_on": list(self.fail_closed_on),
            "security_declarations": self.security_declarations.to_value(),
            "execution_limits": self.execution_limits.to_value(),
            "source_extensions": self.source_extensions.to_value(),
        }
        if include_hash:
            payload["plan_sha256"] = self.plan_sha256
        return payload

    def with_computed_hash(self) -> DagPlan:
        return replace(self, plan_sha256=canonical_sha256(self.to_payload(include_hash=False)))
