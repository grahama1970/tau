"""Immutable, backend-neutral representation of a validated Tau DAG."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

DAG_PLAN_SCHEMA = "tau.dag_plan.v1"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTEXT_BINDING_SELECTOR_KINDS = frozenset(
    {"accepted_output", "artifact_by_schema", "receipt_by_schema"}
)
CONTEXT_BINDING_MATERIALIZATION_MODES = frozenset({"by_value", "by_reference"})
CONTEXT_BINDING_ON_MISSING = frozenset({"omit", "block", "fail"})
CONTEXT_BINDING_ON_INVALID = frozenset({"omit", "block", "fail"})
DAG_PLAN_TERMINAL_KINDS = frozenset({"declared_node", "external", "derived_leaf"})
DAG_PLAN_TARGET_KINDS = frozenset({"node", "terminal"})
DAG_PLAN_TIMEOUT_KINDS = frozenset({"explicit", "source_default", "adapter_defined"})
DAG_PLAN_ADAPTER_KINDS = frozenset(
    {
        "generic_artifact_transaction",
        "generic_command",
        "generic_skill",
        "project_handoff_command",
        "project_human",
        "project_persistent_declaration",
        "project_provider",
        "project_provider_handoff_command",
        "project_virtual",
        "tau_native_agent_loop",
    }
)
DAG_PLAN_COMPLETION_POLICIES = frozenset(
    {"all_nodes_pass_fail_fast", "declared_terminal_settlement"}
)


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
class DagPlanValidationIssue:
    code: str
    path: str
    detail: str = ""

    def to_payload(self) -> dict[str, str]:
        payload = {"code": self.code, "path": self.path}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class DagPlanValidation:
    ok: bool
    issues: tuple[DagPlanValidationIssue, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "tau.dag_plan_validation.v1",
            "ok": self.ok,
            "codes": list(self.codes),
            "issues": [issue.to_payload() for issue in self.issues],
        }


class DagPlanValidationError(RuntimeError):
    """Raised when a canonical DagPlan fails fail-closed admission."""

    def __init__(self, validation: DagPlanValidation) -> None:
        self.validation = validation
        first = validation.issues[0] if validation.issues else None
        message = first.code if first is not None else "dag_plan_invalid"
        if first is not None and first.path:
            message = f"{message}:{first.path}"
        super().__init__(message)


def require_valid_dag_plan(plan: DagPlan) -> DagPlanValidation:
    """Validate a DagPlan and raise a typed error on the first invalid boundary."""

    validation = validate_dag_plan(plan)
    if not validation.ok:
        raise DagPlanValidationError(validation)
    return validation


def validate_dag_plan(plan: DagPlan) -> DagPlanValidation:
    """Return structural and hash validation for the canonical DAG execution boundary."""

    issues: list[DagPlanValidationIssue] = []
    _append_if_empty(issues, plan.schema, "$.schema", "dag_plan_schema_invalid")
    if plan.schema != DAG_PLAN_SCHEMA:
        issues.append(
            DagPlanValidationIssue(
                "dag_plan_schema_invalid",
                "$.schema",
                f"expected {DAG_PLAN_SCHEMA}",
            )
        )
    _append_if_empty(issues, plan.plan_id, "$.plan_id", "dag_plan_id_empty")
    _append_if_empty(issues, plan.source_family, "$.source.family", "source_family_empty")
    _append_if_empty(issues, plan.source_schema, "$.source.schema", "source_schema_empty")
    _append_if_empty(issues, plan.source_logical_id, "$.source.logical_id", "source_id_empty")
    if not _is_complete_sha256(plan.source_payload_sha256):
        issues.append(
            DagPlanValidationIssue(
                "source_payload_hash_invalid",
                "$.source.canonical_source_sha256",
            )
        )
    if not plan.plan_sha256:
        issues.append(DagPlanValidationIssue("dag_plan_hash_missing", "$.plan_sha256"))
    elif not _is_complete_sha256(plan.plan_sha256):
        issues.append(DagPlanValidationIssue("dag_plan_hash_invalid", "$.plan_sha256"))

    if plan.completion_policy not in DAG_PLAN_COMPLETION_POLICIES:
        issues.append(
            DagPlanValidationIssue(
                "completion_policy_invalid",
                "$.completion_policy.kind",
                plan.completion_policy,
            )
        )

    node_ids = _unique_values(
        issues,
        [node.node_id for node in plan.nodes],
        "$.nodes",
        "node_id",
        "duplicate_node_id",
        "node_id_empty",
    )
    terminal_ids = _unique_values(
        issues,
        [terminal.terminal_id for terminal in plan.terminal_endpoints],
        "$.terminal_endpoints",
        "terminal_id",
        "duplicate_terminal_id",
        "terminal_id_empty",
    )
    edge_ids = _unique_values(
        issues,
        [edge.edge_id for edge in plan.control_edges],
        "$.control_edges",
        "edge_id",
        "duplicate_edge_id",
        "edge_id_empty",
    )
    binding_ids = _unique_values(
        issues,
        [binding.binding_id for binding in plan.context_bindings],
        "$.context_bindings",
        "binding_id",
        "duplicate_binding_id",
        "binding_id_empty",
    )
    del binding_ids
    _validate_runtime_bindings(issues, plan.runtime_bindings)

    declared_terminal_nodes = {
        terminal.terminal_id
        for terminal in plan.terminal_endpoints
        if terminal.kind == "declared_node"
    }
    executable_node_ids = node_ids - declared_terminal_nodes
    for index, terminal in enumerate(plan.terminal_endpoints):
        if terminal.kind not in DAG_PLAN_TERMINAL_KINDS:
            issues.append(
                DagPlanValidationIssue(
                    "terminal_kind_invalid",
                    f"$.terminal_endpoints[{index}].kind",
                    terminal.kind,
                )
            )
        if (
            terminal.kind in {"declared_node", "derived_leaf"}
            and terminal.terminal_id not in node_ids
        ):
            issues.append(
                DagPlanValidationIssue(
                    "terminal_missing",
                    f"$.terminal_endpoints[{index}].terminal_id",
                    terminal.terminal_id,
                )
            )

    for index, node in enumerate(plan.nodes):
        path = f"$.nodes[{index}]"
        if node.adapter_kind not in DAG_PLAN_ADAPTER_KINDS:
            issues.append(
                DagPlanValidationIssue(
                    "adapter_kind_invalid", f"{path}.adapter.kind", node.adapter_kind
                )
            )
        if node.timeout_kind not in DAG_PLAN_TIMEOUT_KINDS:
            issues.append(
                DagPlanValidationIssue(
                    "timeout_kind_invalid", f"{path}.timeout_policy.kind", node.timeout_kind
                )
            )
        _validate_attempts(issues, node.max_attempts, f"{path}.retry_policy.max_attempts")
        _validate_timeout(
            issues,
            node.timeout_kind,
            node.timeout_seconds,
            f"{path}.timeout_policy.seconds",
        )
        _validate_unique_tuple(
            issues,
            node.required_evidence,
            f"{path}.required_evidence",
            "required_evidence_duplicate",
            "required_evidence_empty",
        )
        _validate_json_objects(
            issues,
            node.requested_capabilities,
            f"{path}.requested_capabilities",
            "requested_capability_invalid",
        )
        _validate_json_objects(
            issues,
            node.source_bindings,
            f"{path}.source_bindings",
            "source_binding_invalid",
        )

    _validate_unique_tuple(
        issues,
        plan.entry_node_ids,
        "$.entry_node_ids",
        "duplicate_entry_node",
        "entry_node_empty",
    )
    for index, entry_id in enumerate(plan.entry_node_ids):
        if entry_id not in node_ids:
            issues.append(
                DagPlanValidationIssue("entry_missing", f"$.entry_node_ids[{index}]", entry_id)
            )
        elif entry_id in declared_terminal_nodes:
            issues.append(
                DagPlanValidationIssue(
                    "entry_terminal_invalid", f"$.entry_node_ids[{index}]", entry_id
                )
            )
    if not plan.entry_node_ids:
        issues.append(DagPlanValidationIssue("entry_missing", "$.entry_node_ids"))
    if not plan.terminal_endpoints:
        issues.append(DagPlanValidationIssue("terminal_missing", "$.terminal_endpoints"))

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    logical_edges: set[tuple[str, str, str]] = set()
    edges_by_id = {edge.edge_id: edge for edge in plan.control_edges if edge.edge_id in edge_ids}
    incoming_edges: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for index, edge in enumerate(plan.control_edges):
        path = f"$.control_edges[{index}]"
        if edge.target_kind not in DAG_PLAN_TARGET_KINDS:
            issues.append(
                DagPlanValidationIssue(
                    "edge_target_kind_invalid", f"{path}.target.kind", edge.target_kind
                )
            )
        if edge.source_node_id not in node_ids:
            issues.append(
                DagPlanValidationIssue("edge_source_missing", f"{path}.source_node_id")
            )
        if edge.target_kind == "node":
            if edge.target_id not in node_ids:
                issues.append(DagPlanValidationIssue("edge_target_missing", f"{path}.target.id"))
            elif edge.source_node_id in node_ids:
                adjacency[edge.source_node_id].add(edge.target_id)
                incoming_edges[edge.target_id].add(edge.edge_id)
        elif edge.target_kind == "terminal" and edge.target_id not in terminal_ids:
            issues.append(DagPlanValidationIssue("edge_target_missing", f"{path}.target.id"))
        if edge.target_kind == "node" and edge.source_node_id == edge.target_id:
            issues.append(DagPlanValidationIssue("edge_self_loop", path, edge.edge_id))
        logical = (edge.source_node_id, edge.target_kind, edge.target_id)
        if logical in logical_edges:
            issues.append(DagPlanValidationIssue("duplicate_logical_edge", path, edge.edge_id))
        logical_edges.add(logical)

    _validate_context_bindings(issues, plan.context_bindings, node_ids, edges_by_id)
    _validate_route_contracts(issues, plan.route_contracts, node_ids, edge_ids)
    _validate_join_contracts(issues, plan.join_contracts, node_ids, edge_ids, incoming_edges)
    _validate_graph_shape(
        issues,
        plan=plan,
        adjacency=adjacency,
        executable_node_ids=executable_node_ids,
        terminal_ids=terminal_ids,
    )

    if plan.plan_sha256 and _is_complete_sha256(plan.plan_sha256):
        try:
            computed = canonical_sha256(plan.to_payload(include_hash=False))
        except RuntimeError as exc:
            issues.append(DagPlanValidationIssue("dag_plan_canonical_json_invalid", "$", str(exc)))
        else:
            if computed != plan.plan_sha256:
                issues.append(
                    DagPlanValidationIssue(
                        "dag_plan_hash_mismatch",
                        "$.plan_sha256",
                        f"expected {computed}",
                    )
                )
    return DagPlanValidation(ok=not issues, issues=tuple(issues))


def _append_if_empty(
    issues: list[DagPlanValidationIssue],
    value: object,
    path: str,
    code: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(DagPlanValidationIssue(code, path))


def _is_complete_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _unique_values(
    issues: list[DagPlanValidationIssue],
    values: list[str],
    root_path: str,
    field_name: str,
    duplicate_code: str,
    empty_code: str,
) -> set[str]:
    seen: set[str] = set()
    unique: set[str] = set()
    for index, value in enumerate(values):
        path = f"{root_path}[{index}].{field_name}"
        if not isinstance(value, str) or not value.strip():
            issues.append(DagPlanValidationIssue(empty_code, path))
            continue
        if value in seen:
            issues.append(DagPlanValidationIssue(duplicate_code, path, value))
        seen.add(value)
        unique.add(value)
    return unique


def _validate_unique_tuple(
    issues: list[DagPlanValidationIssue],
    values: tuple[str, ...],
    root_path: str,
    duplicate_code: str,
    empty_code: str,
) -> None:
    seen: set[str] = set()
    for index, value in enumerate(values):
        path = f"{root_path}[{index}]"
        if not isinstance(value, str) or not value.strip():
            issues.append(DagPlanValidationIssue(empty_code, path))
            continue
        if value in seen:
            issues.append(DagPlanValidationIssue(duplicate_code, path, value))
        seen.add(value)


def _validate_attempts(
    issues: list[DagPlanValidationIssue],
    value: object,
    path: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        issues.append(DagPlanValidationIssue("max_attempts_invalid", path, repr(value)))


def _validate_timeout(
    issues: list[DagPlanValidationIssue],
    kind: str,
    seconds: object,
    path: str,
) -> None:
    if kind == "adapter_defined" and seconds is None:
        return
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        issues.append(DagPlanValidationIssue("timeout_seconds_invalid", path, repr(seconds)))
        return
    if not math.isfinite(float(seconds)) or float(seconds) <= 0:
        issues.append(DagPlanValidationIssue("timeout_seconds_invalid", path, repr(seconds)))


def _validate_json_objects(
    issues: list[DagPlanValidationIssue],
    values: tuple[FrozenJson, ...],
    root_path: str,
    code: str,
) -> None:
    for index, value in enumerate(values):
        decoded = value.to_value()
        if not isinstance(decoded, Mapping):
            issues.append(DagPlanValidationIssue(code, f"{root_path}[{index}]"))


def _validate_runtime_bindings(
    issues: list[DagPlanValidationIssue],
    bindings: tuple[FrozenJson, ...],
) -> None:
    seen: set[str] = set()
    for index, binding in enumerate(bindings):
        path = f"$.runtime_bindings[{index}]"
        value = binding.to_value()
        if not isinstance(value, Mapping):
            issues.append(DagPlanValidationIssue("runtime_binding_invalid", path))
            continue
        binding_id = value.get("binding_id")
        if not isinstance(binding_id, str) or not binding_id.strip():
            issues.append(DagPlanValidationIssue("runtime_binding_id_empty", f"{path}.binding_id"))
            continue
        if binding_id in seen:
            issues.append(
                DagPlanValidationIssue("duplicate_runtime_binding_id", f"{path}.binding_id")
            )
        seen.add(binding_id)


def _validate_context_bindings(
    issues: list[DagPlanValidationIssue],
    bindings: tuple[DagPlanContextBinding, ...],
    node_ids: set[str],
    edges_by_id: Mapping[str, DagPlanEdge],
) -> None:
    for index, binding in enumerate(bindings):
        path = f"$.context_bindings[{index}]"
        if binding.source_node_id not in node_ids:
            issues.append(
                DagPlanValidationIssue(
                    "dag_context_binding_source_missing",
                    f"{path}.source_node_id",
                )
            )
        if binding.target_node_id not in node_ids:
            issues.append(
                DagPlanValidationIssue(
                    "dag_context_binding_target_missing",
                    f"{path}.target_node_id",
                )
            )
        edge = edges_by_id.get(binding.control_edge_id)
        if edge is None:
            issues.append(
                DagPlanValidationIssue(
                    "dag_context_binding_control_edge_missing",
                    f"{path}.control_edge_id",
                )
            )
            continue
        if edge.target_kind != "node":
            issues.append(
                DagPlanValidationIssue(
                    "dag_context_binding_target_not_node",
                    f"{path}.control_edge_id",
                    edge.edge_id,
                )
            )
        if (
            edge.source_node_id != binding.source_node_id
            or edge.target_id != binding.target_node_id
        ):
            issues.append(
                DagPlanValidationIssue(
                    "dag_context_binding_edge_mismatch",
                    f"{path}.control_edge_id",
                    edge.edge_id,
                )
            )
        if binding.selector_kind not in CONTEXT_BINDING_SELECTOR_KINDS:
            issues.append(DagPlanValidationIssue("binding_selector_kind_invalid", path))
        if binding.materialization_mode not in CONTEXT_BINDING_MATERIALIZATION_MODES:
            issues.append(DagPlanValidationIssue("binding_materialization_mode_invalid", path))
        if binding.on_missing not in CONTEXT_BINDING_ON_MISSING:
            issues.append(DagPlanValidationIssue("binding_on_missing_invalid", path))
        if binding.on_invalid not in CONTEXT_BINDING_ON_INVALID:
            issues.append(DagPlanValidationIssue("binding_on_invalid_invalid", path))
        if (
            binding.max_reference_bytes is not None
            and (
                isinstance(binding.max_reference_bytes, bool)
                or not isinstance(binding.max_reference_bytes, int)
                or binding.max_reference_bytes < 1
            )
        ):
            issues.append(DagPlanValidationIssue("binding_max_reference_bytes_invalid", path))


def _validate_route_contracts(
    issues: list[DagPlanValidationIssue],
    contracts: tuple[FrozenJson, ...],
    node_ids: set[str],
    edge_ids: set[str],
) -> None:
    seen: set[str] = set()
    for index, contract in enumerate(contracts):
        path = f"$.route_contracts[{index}]"
        payload = contract.to_value()
        if not isinstance(payload, Mapping):
            issues.append(DagPlanValidationIssue("route_contract_invalid", path))
            continue
        source_id = payload.get("source_node_id")
        if not isinstance(source_id, str) or source_id not in node_ids:
            issues.append(DagPlanValidationIssue("route_source_missing", f"{path}.source_node_id"))
        elif source_id in seen:
            issues.append(DagPlanValidationIssue("duplicate_route_contract", path, source_id))
        seen.add(str(source_id))
        ordered = payload.get("ordered_edge_ids")
        if not isinstance(ordered, list) or not ordered:
            issues.append(DagPlanValidationIssue("route_edge_membership_invalid", path))
        else:
            for edge_id in ordered:
                if not isinstance(edge_id, str) or edge_id not in edge_ids:
                    issues.append(
                        DagPlanValidationIssue("route_edge_membership_invalid", path)
                    )
                    break
        expected_hash = payload.get("contract_sha256")
        if isinstance(expected_hash, str):
            without_hash = dict(payload)
            without_hash.pop("contract_sha256", None)
            if canonical_sha256(without_hash) != expected_hash:
                issues.append(DagPlanValidationIssue("route_contract_hash_mismatch", path))


def _validate_join_contracts(
    issues: list[DagPlanValidationIssue],
    contracts: tuple[FrozenJson, ...],
    node_ids: set[str],
    edge_ids: set[str],
    incoming_edges: Mapping[str, set[str]],
) -> None:
    seen: set[str] = set()
    for index, contract in enumerate(contracts):
        path = f"$.join_contracts[{index}]"
        payload = contract.to_value()
        if not isinstance(payload, Mapping):
            issues.append(DagPlanValidationIssue("join_contract_invalid", path))
            continue
        join_id = payload.get("join_node_id")
        if not isinstance(join_id, str) or join_id not in node_ids:
            issues.append(DagPlanValidationIssue("join_node_missing", f"{path}.join_node_id"))
        elif join_id in seen:
            issues.append(DagPlanValidationIssue("duplicate_join_contract", path, join_id))
        seen.add(str(join_id))
        incoming = payload.get("incoming_edge_ids")
        expected = incoming_edges.get(str(join_id), set())
        if (
            not isinstance(incoming, list)
            or set(incoming) != expected
            or not set(incoming) <= edge_ids
        ):
            issues.append(DagPlanValidationIssue("join_edge_membership_invalid", path))
        policy = payload.get("policy")
        policy_hash = payload.get("policy_sha256")
        if isinstance(policy_hash, str) and canonical_sha256(policy) != policy_hash:
            issues.append(DagPlanValidationIssue("join_policy_hash_mismatch", path))


def _validate_graph_shape(
    issues: list[DagPlanValidationIssue],
    *,
    plan: DagPlan,
    adjacency: Mapping[str, set[str]],
    executable_node_ids: set[str],
    terminal_ids: set[str],
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, trail: tuple[str, ...]) -> None:
        if node_id in visiting:
            issues.append(DagPlanValidationIssue("cycle_detected", "$.control_edges", node_id))
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency.get(node_id, set()):
            visit(target, (*trail, target))
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in adjacency:
        visit(node_id, (node_id,))

    reachable: set[str] = set()
    stack = [entry for entry in plan.entry_node_ids if entry in adjacency]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(sorted(adjacency.get(node_id, set()) - reachable))
    for node_id in sorted(executable_node_ids - reachable):
        issues.append(DagPlanValidationIssue("node_unreachable", "$.nodes", node_id))

    terminal_edge_sources = {
        edge.source_node_id
        for edge in plan.control_edges
        if edge.target_kind == "terminal" and edge.target_id in terminal_ids
    }
    terminal_node_ids = terminal_ids & set(adjacency)

    def can_reach_terminal(node_id: str, seen: set[str]) -> bool:
        if node_id in terminal_node_ids or node_id in terminal_edge_sources:
            return True
        if node_id in seen:
            return False
        seen.add(node_id)
        return any(can_reach_terminal(target, seen) for target in adjacency.get(node_id, set()))

    for node_id in sorted(executable_node_ids):
        node = next((item for item in plan.nodes if item.node_id == node_id), None)
        if node is not None and node.adapter_kind == "project_persistent_declaration":
            continue
        if node_id in terminal_edge_sources:
            continue
        if not can_reach_terminal(node_id, set()):
            issues.append(DagPlanValidationIssue("node_dead_end", "$.nodes", node_id))


def _raise_local_validation(code: str, path: str, detail: str = "") -> None:
    raise DagPlanValidationError(
        DagPlanValidation(False, (DagPlanValidationIssue(code, path, detail),))
    )


@dataclass(frozen=True, slots=True)
class FrozenJson:
    """Canonical JSON held as text so nested caller mutation cannot alter a plan."""

    canonical: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, str):
            _raise_local_validation("frozen_json_invalid", "$.canonical")
        try:
            json.loads(self.canonical)
        except json.JSONDecodeError as exc:
            _raise_local_validation("frozen_json_invalid", "$.canonical", str(exc))

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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.terminal_id, "terminal_id", "$.terminal_id")
        _require_non_empty_string(self.kind, "kind", "$.kind")
        _require_non_empty_string(self.origin, "origin", "$.origin")

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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.edge_id, "edge_id", "$.edge_id")
        _require_non_empty_string(self.source_node_id, "source_node_id", "$.source_node_id")
        _require_non_empty_string(self.target_id, "target_id", "$.target.id")
        _require_non_empty_string(self.target_kind, "target_kind", "$.target.kind")
        if self.condition is not None and not isinstance(self.condition, FrozenJson):
            _raise_local_validation("edge_condition_invalid", "$.condition")
        if self.source_ordinal is not None and (
            isinstance(self.source_ordinal, bool)
            or not isinstance(self.source_ordinal, int)
            or self.source_ordinal < 0
        ):
            _raise_local_validation("edge_source_ordinal_invalid", "$.source_ordinal")

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
    accepted_source_schemas: tuple[str, ...] = ("*",)
    selector_kind: str = "accepted_output"
    materialization_mode: str = "by_value"
    on_missing: str = "omit"
    on_invalid: str = "omit"
    max_reference_bytes: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.binding_id, "binding_id", "$.binding_id")
        _require_non_empty_string(self.source_node_id, "source_node_id", "$.source_node_id")
        _require_non_empty_string(self.target_node_id, "target_node_id", "$.target_node_id")
        _require_non_empty_string(self.control_edge_id, "control_edge_id", "$.control_edge_id")
        _require_non_empty_string(self.projection, "projection", "$.projection")
        _require_non_empty_string(self.activation, "activation", "$.activation")
        _require_non_empty_string(self.origin, "origin", "$.origin")
        if not isinstance(self.accepted_source_schemas, tuple) or not self.accepted_source_schemas:
            _raise_local_validation("binding_accepted_schemas_invalid", "$.accepted_source_schemas")
        if self.max_reference_bytes is not None and (
            isinstance(self.max_reference_bytes, bool)
            or not isinstance(self.max_reference_bytes, int)
            or self.max_reference_bytes < 1
        ):
            _raise_local_validation("binding_max_reference_bytes_invalid", "$.max_reference_bytes")

    def to_payload(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "control_edge_id": self.control_edge_id,
            "projection": self.projection,
            "activation": self.activation,
            "origin": self.origin,
            "accepted_source_schemas": list(self.accepted_source_schemas),
            "selector_kind": self.selector_kind,
            "materialization_mode": self.materialization_mode,
            "on_missing": self.on_missing,
            "on_invalid": self.on_invalid,
            "max_reference_bytes": self.max_reference_bytes,
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
    runtime_requirement: FrozenJson

    def __post_init__(self) -> None:
        _require_non_empty_string(self.node_id, "node_id", "$.node_id")
        _require_non_empty_string(self.role, "role", "$.role")
        _require_non_empty_string(self.executor, "executor", "$.executor")
        _require_non_empty_string(self.adapter_kind, "adapter_kind", "$.adapter.kind")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            _raise_local_validation("max_attempts_invalid", "$.retry_policy.max_attempts")
        _require_non_empty_string(self.timeout_kind, "timeout_kind", "$.timeout_policy.kind")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            _raise_local_validation("timeout_seconds_invalid", "$.timeout_policy.seconds")

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
            "runtime_requirement": self.runtime_requirement.to_value(),
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
    runtime_bindings: tuple[FrozenJson, ...]
    route_contracts: tuple[FrozenJson, ...]
    join_contracts: tuple[FrozenJson, ...]
    required_evidence: tuple[str, ...]
    fail_closed_on: tuple[str, ...]
    security_declarations: FrozenJson
    execution_limits: FrozenJson
    source_extensions: FrozenJson
    plan_sha256: str = ""

    def __post_init__(self) -> None:
        _require_non_empty_string(self.schema, "schema", "$.schema")
        _require_non_empty_string(self.plan_id, "plan_id", "$.plan_id")
        _require_non_empty_string(self.source_family, "source_family", "$.source.family")
        _require_non_empty_string(self.source_schema, "source_schema", "$.source.schema")
        _require_non_empty_string(
            self.source_logical_id, "source_logical_id", "$.source.logical_id"
        )
        if not isinstance(self.entry_node_ids, tuple):
            _raise_local_validation("entry_nodes_invalid", "$.entry_node_ids")
        if not isinstance(self.nodes, tuple):
            _raise_local_validation("nodes_invalid", "$.nodes")
        if not isinstance(self.control_edges, tuple):
            _raise_local_validation("control_edges_invalid", "$.control_edges")

    @property
    def runtime_goal_hash(self) -> str:
        """Return the complete digest runtime leases use for this goal binding."""

        return canonical_sha256(self.goal_binding.to_value())

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
            "runtime_bindings": [item.to_value() for item in self.runtime_bindings],
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


def _require_non_empty_string(value: object, label: str, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _raise_local_validation(f"{label}_empty", path)
