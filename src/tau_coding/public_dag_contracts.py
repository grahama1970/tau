"""Strict validation helpers for public Tau DAG source contracts."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

PROJECT_DAG_ROOT_KEYS = frozenset(
    {
        "schema",
        "dag_id",
        "run_id",
        "goal",
        "target",
        "entry_node",
        "terminal_nodes",
        "limits",
        "nodes",
        "edges",
        "context",
        "required_evidence",
        "fail_closed_on",
        "evidence_manifest",
        "command_policy",
        "policy_profile",
        "data_boundary",
        "security_mode",
        "actor_access_manifest",
        "environment_manifest",
        "memory_intent",
        "evidence_case",
        "research_query_safety_receipt",
        "itar_access_preflight_receipt",
        "sandbox_run_receipt",
        "compliance_package_validation_receipt",
        "requires_itar_access_preflight",
        "requires_external_research",
        "external_research",
        "requires_sandbox",
        "requires_compliance_package_validation",
        "requires_review_ready_package",
        "provider_sensitive",
        "requires_provider_route",
        "requires_knowledge_freshness",
        "knowledge_freshness",
        "mutating",
        "scheduler",
        "command_specs",
        "execution_profile",
        "execution_profile_policy",
        "extensions",
        "repair_policy",
    }
)

PROJECT_DAG_GOAL_KEYS = frozenset(
    {
        "goal_id",
        "goal_version",
        "goal_hash",
        "summary",
        "completion_criteria",
        "immutable_goal",
        "extensions",
    }
)
PROJECT_DAG_TARGET_KEYS = frozenset({"repo", "branch", "target", "allowed_paths", "extensions"})
PROJECT_DAG_LIMIT_KEYS = frozenset(
    {
        "resume",
        "default_timeout_seconds",
        "max_total_attempts",
        "max_concurrency",
        "max_steps",
        "provider_command_timeout_seconds",
        "provider_command_timeout_s",
        "extensions",
    }
)
PROJECT_DAG_NODE_KEYS = frozenset(
    {
        "id",
        "agent",
        "executor",
        "runtime_backend",
        "max_attempts",
        "timeout_seconds",
        "command_spec",
        "required_evidence",
        "depends_on",
        "reviewer",
        "context",
        "requested_capabilities",
        "persistent_subagent",
        "route",
        "join",
        "provider",
        "provider_route",
        "requires_provider_route",
        "mutates",
        "model_policy",
        "prompt_contract",
        "skill",
        "extensions",
    }
)
PROJECT_DAG_EDGE_KEYS = frozenset({"from", "to", "condition", "extensions"})

GENERIC_DAG_ROOT_KEYS = frozenset(
    {
        "schema",
        "run_id",
        "run_dir",
        "events_jsonl",
        "nodes",
        "goal",
        "goal_hash",
        "workflow",
        "max_concurrency",
        "limits",
        "budget",
        "cost_budget",
        "execution_profile",
        "execution_profile_policy",
        "data_boundary",
        "extensions",
    }
)
GENERIC_DAG_GOAL_KEYS = frozenset(
    {
        "goal_id",
        "goal_version",
        "goal_hash",
        "summary",
        "completion_criteria",
        "version",
        "sha256",
        "statement",
        "extensions",
    }
)
GENERIC_DAG_BUDGET_KEYS = frozenset(
    {"estimated_cost_usd", "max_estimated_cost_usd", "extensions"}
)
GENERIC_DAG_NODE_KEYS = frozenset(
    {
        "node_id",
        "role",
        "command",
        "depends_on",
        "accepted_context_from",
        "receipt_path",
        "timeout_seconds",
        "max_attempts",
        "work_order_path",
        "transaction",
        "skill",
        "browser",
        "tau_agent",
        "extensions",
    }
)


class ImmutableJsonDict(dict[str, Any]):
    """Dict-compatible immutable JSON object for validated public contracts."""

    _locked: bool

    def __init__(self, value: Mapping[str, Any]) -> None:
        dict.__init__(self)
        for key, nested in value.items():
            if not isinstance(key, str):
                raise RuntimeError("JSON object keys must be strings")
            dict.__setitem__(self, key, immutable_json(nested))
        self._locked = True

    def __setitem__(self, key: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise TypeError("validated Tau contract JSON is immutable")
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: str) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def clear(self) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("validated Tau contract JSON is immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("validated Tau contract JSON is immutable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("validated Tau contract JSON is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def __ior__(self, other: object) -> ImmutableJsonDict:  # type: ignore[override]
        raise TypeError("validated Tau contract JSON is immutable")

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        copied = thaw_json(self)
        memo[id(self)] = copied
        return cast(dict[str, Any], copied)

    def copy(self) -> dict[str, Any]:
        return cast(dict[str, Any], thaw_json(self))


class ImmutableJsonList(list[Any]):
    """List-compatible immutable JSON array for validated public contracts."""

    _locked: bool

    def __init__(self, value: Sequence[Any]) -> None:
        list.__init__(self)
        for nested in value:
            list.append(self, immutable_json(nested))
        self._locked = True

    def __setitem__(self, index: Any, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise TypeError("validated Tau contract JSON is immutable")
        list.__setitem__(self, index, value)

    def __delitem__(self, index: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def append(self, item: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def clear(self) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def extend(self, other: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def insert(self, index: int, item: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def pop(self, index: int = -1) -> Any:
        raise TypeError("validated Tau contract JSON is immutable")

    def remove(self, item: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def reverse(self) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def sort(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("validated Tau contract JSON is immutable")

    def __iadd__(self, other: Any) -> ImmutableJsonList:
        raise TypeError("validated Tau contract JSON is immutable")

    def __imul__(self, other: Any) -> ImmutableJsonList:
        raise TypeError("validated Tau contract JSON is immutable")

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        copied = thaw_json(self)
        memo[id(self)] = copied
        return cast(list[Any], copied)

    def copy(self) -> list[Any]:
        return cast(list[Any], thaw_json(self))


def immutable_json(value: Any) -> Any:
    """Return an immutable, JSON-only clone with no aliases to caller-owned data."""

    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return ImmutableJsonDict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return ImmutableJsonList(value)
    return ImmutableJsonDict(
        {
            "unsupported_json_type": type(value).__name__,
            "value": str(value),
        }
    )


def thaw_json(value: Any) -> Any:
    """Return fresh mutable JSON values from immutable contract data."""

    if isinstance(value, Mapping):
        return {str(key): thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [thaw_json(item) for item in value]
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return copy.deepcopy(value)


def validate_project_dag_public_boundary(payload: Mapping[str, Any], errors: list[str]) -> None:
    """Apply common strict source-contract rules for tau.dag_contract.v1."""

    _validate_no_unknown_keys(payload, "$", PROJECT_DAG_ROOT_KEYS, errors)
    _validate_extensions(payload, "$", errors)
    _validate_no_non_finite(payload, "$", errors)
    goal = payload.get("goal")
    if isinstance(goal, Mapping):
        _validate_no_unknown_keys(goal, "goal", PROJECT_DAG_GOAL_KEYS, errors)
        _validate_extensions(goal, "goal", errors)
    target = payload.get("target")
    if isinstance(target, Mapping):
        _validate_no_unknown_keys(target, "target", PROJECT_DAG_TARGET_KEYS, errors)
        _validate_extensions(target, "target", errors)
    limits = payload.get("limits")
    if isinstance(limits, Mapping):
        _validate_no_unknown_keys(limits, "limits", PROJECT_DAG_LIMIT_KEYS, errors)
        _validate_extensions(limits, "limits", errors)
        for key in ("max_concurrency", "max_steps"):
            if key in limits:
                _validate_strict_positive_int(limits[key], f"limits.{key}", errors)
        for key in (
            "default_timeout_seconds",
            "provider_command_timeout_seconds",
            "provider_command_timeout_s",
        ):
            if key in limits:
                _validate_strict_positive_number(limits[key], f"limits.{key}", errors)
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        for index, node in enumerate(nodes):
            if isinstance(node, Mapping):
                label = f"nodes[{index}]"
                _validate_no_unknown_keys(node, label, PROJECT_DAG_NODE_KEYS, errors)
                _validate_extensions(node, label, errors)
                if "timeout_seconds" in node:
                    _validate_strict_positive_number(
                        node["timeout_seconds"],
                        f"{label}.timeout_seconds",
                        errors,
                    )
    edges = payload.get("edges")
    if isinstance(edges, list):
        for index, edge in enumerate(edges):
            if isinstance(edge, Mapping):
                label = f"edges[{index}]"
                _validate_no_unknown_keys(edge, label, PROJECT_DAG_EDGE_KEYS, errors)
                _validate_extensions(edge, label, errors)


def validate_generic_dag_public_boundary(payload: Mapping[str, Any]) -> None:
    """Apply common strict source-contract rules for tau.generic_dag_spec.v1."""

    errors: list[str] = []
    _validate_no_unknown_keys(payload, "$", GENERIC_DAG_ROOT_KEYS, errors)
    _validate_extensions(payload, "$", errors)
    _validate_no_non_finite(payload, "$", errors)
    goal = payload.get("goal")
    if isinstance(goal, Mapping):
        _validate_no_unknown_keys(goal, "goal", GENERIC_DAG_GOAL_KEYS, errors)
        _validate_extensions(goal, "goal", errors)
    for key in ("budget", "cost_budget"):
        budget = payload.get(key)
        if isinstance(budget, Mapping):
            _validate_no_unknown_keys(budget, key, GENERIC_DAG_BUDGET_KEYS, errors)
            _validate_extensions(budget, key, errors)
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        for index, node in enumerate(nodes):
            if isinstance(node, Mapping):
                label = f"nodes[{index}]"
                _validate_no_unknown_keys(node, label, GENERIC_DAG_NODE_KEYS, errors)
                _validate_extensions(node, label, errors)
    if errors:
        raise RuntimeError("; ".join(errors))


def strict_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must be a non-empty string")
    return value


def strict_positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeError(f"{label} must be a positive integer")
    return value


def strict_positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be a positive finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise RuntimeError(f"{label} must be a positive finite number")
    return parsed


def _validate_strict_positive_number(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} must be a positive finite number")
        return
    if not math.isfinite(float(value)) or float(value) <= 0:
        errors.append(f"{label} must be a positive finite number")


def _validate_strict_positive_int(value: Any, label: str, errors: list[str]) -> None:
    if type(value) is not int or value < 1:
        errors.append(f"{label} must be a positive integer")


def explicit_extensions(
    payload: Mapping[str, Any],
    *,
    promoted_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return the declared public extension object after boundary validation."""

    extensions = payload.get("extensions")
    result = dict(extensions) if isinstance(extensions, Mapping) else {}
    for key in sorted(promoted_keys):
        if key in payload:
            result[key] = payload[key]
    return result


def strict_optional_path(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must be a non-empty string when provided")
    return value


def _validate_no_unknown_keys(
    payload: Mapping[str, Any],
    label: str,
    allowed: frozenset[str],
    errors: list[str],
) -> None:
    for key in sorted(payload):
        if key not in allowed:
            field = f"{label}.{key}" if label != "$" else key
            errors.append(f"{field} is not allowed outside extensions")


def _validate_extensions(payload: Mapping[str, Any], label: str, errors: list[str]) -> None:
    if "extensions" not in payload:
        return
    value = payload["extensions"]
    field = f"{label}.extensions" if label != "$" else "extensions"
    if not isinstance(value, Mapping):
        errors.append(f"{field} must be an object")
        return
    _validate_no_non_finite(value, field, errors)


def _validate_no_non_finite(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            errors.append(f"{label} must not be NaN or Infinity")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            child = f"{label}.{key}" if label != "$" else str(key)
            _validate_no_non_finite(nested, child, errors)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, nested in enumerate(value):
            _validate_no_non_finite(nested, f"{label}[{index}]", errors)
