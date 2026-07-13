"""Closed typed route evaluation for project DAG results."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROUTE_CONDITION_SCHEMA = "tau.route_condition.v1"
ROUTE_DECISION_SCHEMA = "tau.dag_route_decision.v1"
ROUTE_MODES = frozenset({"exclusive", "first_match", "fanout", "all_matching"})
RESERVED_FIELDS = frozenset(
    {
        "artifacts",
        "commands_run",
        "errors",
        "evidence",
        "policy_exceptions",
        "proof_scope",
        "summary",
    }
)
FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_DEPTH = 8
MAX_CONDITIONS = 64
MAX_LIST_LENGTH = 128
MAX_SCALAR_STRING_LENGTH = 4096
MAX_SOURCE_RESULT_DEPTH = 32
MAX_SOURCE_RESULT_NODES = 4096
MAX_SOURCE_CONTAINER_ITEMS = 1024
MAX_SOURCE_STRING_BYTES = 4 * 1024 * 1024
MAX_SOURCE_RESULT_BYTES = 8 * 1024 * 1024
ROUTE_DECISION_PROOF_SCOPE = {
    "proves": [
        "Tau evaluated the normalized closed route contract against recorded "
        "typed source fields.",
        "Tau selected only the recorded targets under the declared route mode.",
    ],
    "does_not_prove": [
        "The source result is semantically true.",
        "The selected branch will complete successfully.",
        "Provider or model quality.",
        "Join or terminal contribution semantics.",
    ],
}
ROUTE_DECISION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "failure_code",
        "dag_id",
        "goal_hash",
        "source_node_id",
        "attempt",
        "mode",
        "source_result_sha256",
        "source_fields",
        "source_fields_sha256",
        "route_contract",
        "route_contract_sha256",
        "evaluations",
        "selected_targets",
        "proof_scope",
        "decision_sha256",
    }
)
ROUTE_DECISION_VALIDATION_CODES = (
    "route_decision_schema_mismatch",
    "route_decision_proof_scope_mismatch",
    "route_decision_fields_mismatch",
    "route_contract_hash_mismatch",
    "route_decision_hash_mismatch",
    "route_source_fields_hash_mismatch",
    "route_decision_replay_mismatch",
    "route_dag_id_mismatch",
    "route_goal_hash_mismatch",
    "route_source_node_mismatch",
    "route_attempt_mismatch",
    "route_receipt_path_mismatch",
    "route_source_result_hash_mismatch",
)


class RouteDecisionError(ValueError):
    """A fail-closed route contract or evaluation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def normalize_route_condition(condition: object) -> dict[str, Any]:
    counter = [0]
    return _normalize_condition(condition, depth=1, counter=counter)


def validate_route_condition(condition: object) -> list[str]:
    try:
        normalize_route_condition(condition)
    except RouteDecisionError as exc:
        return [f"{exc.code}: {exc}"]
    return []


def collect_referenced_fields(condition: Mapping[str, Any]) -> set[str]:
    op = condition["op"]
    if op in {"eq", "neq", "in", "not_in", "exists"}:
        return {str(condition["field"])}
    if op in {"all", "any"}:
        return {
            field
            for child in condition["conditions"]
            for field in collect_referenced_fields(child)
        }
    return collect_referenced_fields(condition["condition"])


def evaluate_route_condition(
    condition: Mapping[str, Any],
    source_result: Mapping[str, Any],
) -> bool:
    op = condition["op"]
    if op == "exists":
        field = str(condition["field"])
        if field in source_result:
            _validate_runtime_value(source_result[field], label=f"result.{field}")
            return True
        return False
    if op in {"all", "any"}:
        if op == "all":
            for child in condition["conditions"]:
                if not evaluate_route_condition(child, source_result):
                    return False
            return True
        for child in condition["conditions"]:
            if evaluate_route_condition(child, source_result):
                return True
        return False
    if op == "not":
        return not evaluate_route_condition(condition["condition"], source_result)

    field = str(condition["field"])
    if field not in source_result:
        raise RouteDecisionError("route_field_missing", f"route field {field!r} is missing")
    actual = source_result[field]
    expected = condition["value"]
    _validate_runtime_value(actual, label=f"result.{field}")

    if op in {"in", "not_in"}:
        if not _is_scalar(actual):
            raise RouteDecisionError(
                "route_field_type_invalid",
                f"route field {field!r} must be a scalar for {op}",
            )
        expected_type = _scalar_type(expected[0])
        if _scalar_type(actual) is not expected_type:
            raise RouteDecisionError(
                "route_comparison_type_mismatch",
                f"route field {field!r} does not match the condition operand type",
            )
        matched = actual in expected
        return matched if op == "in" else not matched

    if _value_type(actual) != _value_type(expected):
        raise RouteDecisionError(
            "route_comparison_type_mismatch",
            f"route field {field!r} does not match the condition operand type",
        )
    matched = actual == expected
    return matched if op == "eq" else not matched


def build_route_contract(
    *,
    source_node_id: str,
    mode: str | None,
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(source_node_id, str) or not source_node_id:
        raise RouteDecisionError("invalid_route_condition", "source node id is required")
    resolved_mode = mode or "exclusive"
    if resolved_mode not in ROUTE_MODES:
        raise RouteDecisionError("invalid_route_mode", f"unsupported route mode: {resolved_mode}")
    normalized_edges: list[dict[str, Any]] = []
    edge_indexes: set[int] = set()
    targets: set[str] = set()
    for edge in edges:
        if set(edge) != {"edge_index", "target", "condition"}:
            raise RouteDecisionError(
                "invalid_route_condition", "route edge has unsupported properties"
            )
        edge_index = edge["edge_index"]
        if isinstance(edge_index, bool) or not isinstance(edge_index, int) or edge_index < 0:
            raise RouteDecisionError(
                "invalid_route_condition", "route edge_index must be a non-negative integer"
            )
        target = edge["target"]
        if not isinstance(target, str) or not target:
            raise RouteDecisionError("invalid_route_condition", "route target is required")
        if edge_index in edge_indexes:
            raise RouteDecisionError("invalid_route_condition", "route edge indexes must be unique")
        if target in targets:
            raise RouteDecisionError("invalid_route_condition", "route targets must be unique")
        edge_indexes.add(edge_index)
        targets.add(target)
        normalized_edges.append(
            {
                "edge_index": edge_index,
                "target": target,
                "condition": normalize_route_condition(edge["condition"]),
            }
        )
    if not normalized_edges:
        raise RouteDecisionError("invalid_route_condition", "conditional route has no edges")
    normalized_edges.sort(key=lambda edge: edge["edge_index"])
    return {
        "source_node_id": source_node_id,
        "mode": resolved_mode,
        "edges": normalized_edges,
    }


def evaluate_route_decision(
    *,
    dag_id: str,
    goal_hash: str,
    source_node_id: str,
    attempt: int,
    source_result: Mapping[str, Any],
    route_contract: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(dag_id, str) or not dag_id:
        raise RouteDecisionError("route_source_binding_mismatch", "dag_id is required")
    if not isinstance(goal_hash, str) or not goal_hash:
        raise RouteDecisionError("route_source_binding_mismatch", "goal_hash is required")
    if not isinstance(source_node_id, str) or not source_node_id:
        raise RouteDecisionError(
            "route_source_binding_mismatch", "source_node_id is required"
        )
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise RouteDecisionError(
            "route_source_binding_mismatch", "attempt must be a positive integer"
        )
    try:
        _validate_source_result_shape(source_result)
        source_result_bytes = canonical_json_bytes(source_result)
        if len(source_result_bytes) > MAX_SOURCE_RESULT_BYTES:
            raise RouteDecisionError(
                "route_source_result_invalid", "source result exceeds max serialized bytes"
            )
        source_result_snapshot = json.loads(source_result_bytes)
    except RouteDecisionError:
        raise
    except (RuntimeError, TypeError, ValueError) as exc:
        raise RouteDecisionError(
            "route_source_result_invalid", "source result is not canonical JSON"
        ) from exc
    if set(route_contract) != {"source_node_id", "mode", "edges"}:
        raise RouteDecisionError("invalid_route_condition", "route contract shape is invalid")
    raw_edges = route_contract.get("edges")
    if not isinstance(raw_edges, list):
        raise RouteDecisionError("invalid_route_condition", "route contract edges are invalid")
    normalized_contract = build_route_contract(
        source_node_id=route_contract.get("source_node_id"),
        mode=route_contract.get("mode"),
        edges=raw_edges,
    )
    if normalized_contract != route_contract:
        raise RouteDecisionError("invalid_route_condition", "route contract is not canonical")
    if normalized_contract["source_node_id"] != source_node_id:
        raise RouteDecisionError(
            "route_source_binding_mismatch", "route contract source does not match decision source"
        )
    route_contract = normalized_contract
    fields = sorted(
        {
            field
            for edge in route_contract["edges"]
            for field in collect_referenced_fields(edge["condition"])
        }
    )
    source_fields = [
        (
            {"field": field, "present": True, "value": source_result_snapshot[field]}
            if field in source_result_snapshot
            else {"field": field, "present": False}
        )
        for field in fields
    ]
    for item in source_fields:
        if item["present"]:
            _validate_runtime_value(item["value"], label=f"result.{item['field']}")
    evaluations: list[dict[str, Any]] = []
    matched_edges: list[Mapping[str, Any]] = []
    failure_code: str | None = None
    mode = str(route_contract["mode"])

    for edge in route_contract["edges"]:
        if mode == "first_match" and matched_edges:
            evaluations.append(
                {
                    "edge_index": edge["edge_index"],
                    "target": edge["target"],
                    "evaluated": False,
                    "matched": False,
                    "error_code": None,
                }
            )
            continue
        try:
            matched = evaluate_route_condition(edge["condition"], source_result_snapshot)
            error_code = None
        except RouteDecisionError as exc:
            matched = False
            error_code = exc.code
            if failure_code is None:
                failure_code = exc.code
        evaluations.append(
            {
                "edge_index": edge["edge_index"],
                "target": edge["target"],
                "evaluated": True,
                "matched": matched,
                "error_code": error_code,
            }
        )
        if matched:
            matched_edges.append(edge)

    if failure_code is None:
        if mode == "exclusive":
            if not matched_edges:
                failure_code = "route_no_match"
            elif len(matched_edges) > 1:
                failure_code = "route_ambiguous_exclusive"
        elif mode in {"first_match", "fanout"} and not matched_edges:
            failure_code = "route_no_match"
        elif mode == "all_matching" and len(matched_edges) != len(route_contract["edges"]):
            failure_code = "route_all_matching_incomplete"

    selected_targets = [] if failure_code else [str(edge["target"]) for edge in matched_edges]
    status = "BLOCKED" if failure_code else "PASS"
    decision: dict[str, Any] = {
        "schema": ROUTE_DECISION_SCHEMA,
        "status": status,
        "failure_code": failure_code,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "source_node_id": source_node_id,
        "attempt": attempt,
        "mode": mode,
        "source_result_sha256": sha256_json(source_result_snapshot),
        "source_fields": source_fields,
        "source_fields_sha256": sha256_json(source_fields),
        "route_contract": json.loads(canonical_json_bytes(route_contract)),
        "route_contract_sha256": sha256_json(route_contract),
        "evaluations": evaluations,
        "selected_targets": selected_targets,
        "proof_scope": json.loads(canonical_json_bytes(ROUTE_DECISION_PROOF_SCOPE)),
    }
    decision["decision_sha256"] = sha256_json(decision)
    return decision


def validate_route_decision_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_dag_id: str,
    expected_goal_hash: str,
    expected_source_node_id: str,
    expected_attempt: int,
    source_result: Mapping[str, Any],
    expected_receipt_path: Path | str | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        _validate_source_result_shape(receipt)
        receipt_bytes = canonical_json_bytes(receipt)
        if len(receipt_bytes) > MAX_SOURCE_RESULT_BYTES:
            raise RouteDecisionError(
                "route_decision_hash_mismatch", "route receipt exceeds max serialized bytes"
            )
    except (RecursionError, RuntimeError, RouteDecisionError, TypeError, ValueError):
        return ["route_decision_hash_mismatch"]
    try:
        _validate_source_result_shape(source_result)
        source_result_bytes = canonical_json_bytes(source_result)
        if len(source_result_bytes) > MAX_SOURCE_RESULT_BYTES:
            raise RouteDecisionError(
                "route_source_result_hash_mismatch",
                "source result exceeds max serialized bytes",
            )
        expected_source_result_hash: str | None = (
            f"sha256:{hashlib.sha256(source_result_bytes).hexdigest()}"
        )
        source_result_hash_valid = True
    except (RecursionError, RuntimeError, RouteDecisionError, TypeError, ValueError):
        expected_source_result_hash = None
        source_result_hash_valid = False
    unexpected_fields = set(receipt) - (ROUTE_DECISION_FIELDS | {"receipt_path"})
    missing_fields = ROUTE_DECISION_FIELDS - set(receipt)
    if unexpected_fields or missing_fields:
        errors.append("route_decision_fields_mismatch")
    if receipt.get("schema") != ROUTE_DECISION_SCHEMA:
        errors.append("route_decision_schema_mismatch")
    if receipt.get("proof_scope") != ROUTE_DECISION_PROOF_SCOPE:
        errors.append("route_decision_proof_scope_mismatch")
    route_contract = receipt.get("route_contract")
    normalized_contract: dict[str, Any] | None = None
    try:
        normalized_contract = _normalize_embedded_route_contract(route_contract)
        if sha256_json(route_contract) != receipt.get("route_contract_sha256"):
            raise RouteDecisionError("route_contract_hash_mismatch", "route contract hash differs")
    except (RecursionError, RuntimeError, RouteDecisionError, TypeError, ValueError):
        errors.append("route_contract_hash_mismatch")
    projection = {
        key: receipt.get(key)
        for key in (
            "schema",
            "status",
            "failure_code",
            "dag_id",
            "goal_hash",
            "source_node_id",
            "attempt",
            "mode",
            "source_result_sha256",
            "source_fields",
            "source_fields_sha256",
            "route_contract",
            "route_contract_sha256",
            "evaluations",
            "selected_targets",
            "proof_scope",
        )
    }
    try:
        decision_hash_matches = sha256_json(projection) == receipt.get("decision_sha256")
    except (RecursionError, RuntimeError, TypeError, ValueError):
        decision_hash_matches = False
    if not decision_hash_matches:
        errors.append("route_decision_hash_mismatch")
    try:
        if sha256_json(receipt.get("source_fields")) != receipt.get("source_fields_sha256"):
            errors.append("route_source_fields_hash_mismatch")
    except (RecursionError, RuntimeError, TypeError, ValueError):
        errors.append("route_source_fields_hash_mismatch")
    if normalized_contract is None:
        errors.append("route_decision_replay_mismatch")
    else:
        try:
            replay = evaluate_route_decision(
                dag_id=expected_dag_id,
                goal_hash=expected_goal_hash,
                source_node_id=expected_source_node_id,
                attempt=expected_attempt,
                source_result=source_result,
                route_contract=normalized_contract,
            )
        except (
            RecursionError,
            RuntimeError,
            RouteDecisionError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):
            errors.append("route_decision_replay_mismatch")
        else:
            replay_fields = ROUTE_DECISION_FIELDS - {"decision_sha256"}
            if any(receipt.get(key) != replay.get(key) for key in replay_fields):
                errors.append("route_decision_replay_mismatch")
    expected_bindings = {
        "dag_id": (expected_dag_id, "route_dag_id_mismatch"),
        "goal_hash": (expected_goal_hash, "route_goal_hash_mismatch"),
        "source_node_id": (expected_source_node_id, "route_source_node_mismatch"),
        "attempt": (expected_attempt, "route_attempt_mismatch"),
    }
    for field, (expected, error_code) in expected_bindings.items():
        actual = receipt.get(field)
        if type(actual) is not type(expected) or actual != expected:
            errors.append(error_code)
    receipt_path = receipt.get("receipt_path")
    normalized_expected_path = (
        str(Path(expected_receipt_path)) if expected_receipt_path is not None else None
    )
    if (receipt_path is not None or normalized_expected_path is not None) and (
        not isinstance(receipt_path, str) or receipt_path != normalized_expected_path
    ):
        errors.append("route_receipt_path_mismatch")
    source_hash_matches = (
        source_result_hash_valid
        and expected_source_result_hash == receipt.get("source_result_sha256")
    )
    if not source_hash_matches:
        errors.append("route_source_result_hash_mismatch")
    return errors


def _normalize_embedded_route_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"source_node_id", "mode", "edges"}:
        raise RouteDecisionError("route_contract_hash_mismatch", "invalid route contract")
    edges = value.get("edges")
    if not isinstance(edges, list):
        raise RouteDecisionError("route_contract_hash_mismatch", "route edges are missing")
    normalized = build_route_contract(
        source_node_id=value.get("source_node_id"),
        mode=value.get("mode"),
        edges=edges,
    )
    if normalized != value:
        raise RouteDecisionError("route_contract_hash_mismatch", "route contract is not canonical")
    return normalized


def write_route_decision_receipt(path: Path, decision: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(decision)
    payload["receipt_path"] = str(path)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def _normalize_condition(
    condition: object,
    *,
    depth: int,
    counter: list[int],
) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise RouteDecisionError("invalid_route_condition", "route condition exceeds max depth")
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise RouteDecisionError("invalid_route_condition", "route condition exceeds max objects")
    if not isinstance(condition, dict):
        raise RouteDecisionError("unsupported_ready_queue_condition", "condition must be an object")
    if condition.get("schema") != ROUTE_CONDITION_SCHEMA:
        raise RouteDecisionError(
            "unsupported_ready_queue_condition",
            f"condition schema must be {ROUTE_CONDITION_SCHEMA}",
        )
    op = condition.get("op")
    if op not in {"eq", "neq", "in", "not_in", "exists", "all", "any", "not"}:
        raise RouteDecisionError("invalid_route_condition", f"unsupported route operator: {op}")

    if op in {"eq", "neq", "in", "not_in"}:
        _require_exact_keys(condition, {"schema", "op", "field", "value"})
        field = _normalize_field(condition.get("field"))
        value = condition.get("value")
        _validate_condition_value(value, membership=op in {"in", "not_in"})
        return {"schema": ROUTE_CONDITION_SCHEMA, "op": op, "field": field, "value": value}
    if op == "exists":
        _require_exact_keys(condition, {"schema", "op", "field"})
        return {
            "schema": ROUTE_CONDITION_SCHEMA,
            "op": op,
            "field": _normalize_field(condition.get("field")),
        }
    if op in {"all", "any"}:
        _require_exact_keys(condition, {"schema", "op", "conditions"})
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            raise RouteDecisionError(
                "invalid_route_condition", f"{op}.conditions must be a non-empty list"
            )
        return {
            "schema": ROUTE_CONDITION_SCHEMA,
            "op": op,
            "conditions": [
                _normalize_condition(child, depth=depth + 1, counter=counter) for child in children
            ],
        }
    _require_exact_keys(condition, {"schema", "op", "condition"})
    return {
        "schema": ROUTE_CONDITION_SCHEMA,
        "op": op,
        "condition": _normalize_condition(
            condition.get("condition"), depth=depth + 1, counter=counter
        ),
    }


def _validate_source_result_shape(value: object) -> None:
    counter = [0]
    string_bytes = [0]

    def visit(item: object, depth: int) -> None:
        if depth > MAX_SOURCE_RESULT_DEPTH:
            raise RouteDecisionError(
                "route_source_result_invalid", "source result exceeds max depth"
            )
        counter[0] += 1
        if counter[0] > MAX_SOURCE_RESULT_NODES:
            raise RouteDecisionError(
                "route_source_result_invalid", "source result exceeds max node count"
            )
        if isinstance(item, str):
            string_bytes[0] += len(item.encode("utf-8"))
            if string_bytes[0] > MAX_SOURCE_STRING_BYTES:
                raise RouteDecisionError(
                    "route_source_result_invalid", "source result exceeds max string bytes"
                )
            return
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(item, float) and not math.isfinite(item):
                raise RouteDecisionError(
                    "route_source_result_invalid", "source result contains non-finite number"
                )
            return
        if isinstance(item, dict):
            if len(item) > MAX_SOURCE_CONTAINER_ITEMS:
                raise RouteDecisionError(
                    "route_source_result_invalid", "source result object is too large"
                )
            for key, child in item.items():
                if not isinstance(key, str):
                    raise RouteDecisionError(
                        "route_source_result_invalid", "source result keys must be strings"
                    )
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        if isinstance(item, list):
            if len(item) > MAX_SOURCE_CONTAINER_ITEMS:
                raise RouteDecisionError(
                    "route_source_result_invalid", "source result list is too large"
                )
            for child in item:
                visit(child, depth + 1)
            return
        raise RouteDecisionError(
            "route_source_result_invalid", "source result contains unsupported JSON value"
        )

    visit(value, 1)


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    actual = set(value)
    if actual != expected:
        raise RouteDecisionError(
            "invalid_route_condition",
            f"route condition keys must be {sorted(expected)}; got {sorted(actual)}",
        )


def _normalize_field(value: object) -> str:
    if not isinstance(value, str) or not FIELD_PATTERN.fullmatch(value):
        raise RouteDecisionError("invalid_route_condition", "route field has invalid grammar")
    if value in RESERVED_FIELDS:
        raise RouteDecisionError("invalid_route_condition", f"route field {value!r} is reserved")
    return value


def _validate_condition_value(value: object, *, membership: bool) -> None:
    if membership:
        if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
            raise RouteDecisionError(
                "invalid_route_condition", "membership value must be a bounded non-empty list"
            )
        if not all(_is_scalar(item) for item in value):
            raise RouteDecisionError("invalid_route_condition", "membership values must be scalars")
        value_types = {_scalar_type(item) for item in value}
        if len(value_types) != 1:
            raise RouteDecisionError(
                "invalid_route_condition", "membership values must have one scalar type"
            )
        for item in value:
            _validate_scalar(item, code="invalid_route_condition")
        return
    if _is_scalar(value):
        _validate_scalar(value, code="invalid_route_condition")
        return
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
        raise RouteDecisionError(
            "invalid_route_condition",
            "comparison value must be a scalar or bounded non-empty scalar list",
        )
    if not all(_is_scalar(item) for item in value):
        raise RouteDecisionError("invalid_route_condition", "comparison list must be scalar")
    if len({_scalar_type(item) for item in value}) != 1:
        raise RouteDecisionError(
            "invalid_route_condition", "comparison list must have one scalar type"
        )
    for item in value:
        _validate_scalar(item, code="invalid_route_condition")


def _validate_runtime_value(value: object, *, label: str) -> None:
    if _is_scalar(value):
        _validate_scalar(value, code="route_field_type_invalid")
        return
    if isinstance(value, list) and len(value) <= MAX_LIST_LENGTH:
        if not all(_is_scalar(item) for item in value):
            raise RouteDecisionError("route_field_type_invalid", f"{label} has a non-scalar list")
        if len({_scalar_type(item) for item in value}) > 1:
            raise RouteDecisionError("route_field_type_invalid", f"{label} has mixed scalar types")
        for item in value:
            _validate_scalar(item, code="route_field_type_invalid")
        return
    raise RouteDecisionError("route_field_type_invalid", f"{label} has an unsupported type")


def _validate_scalar(value: object, *, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise RouteDecisionError(code, "route numbers must be finite")
    if isinstance(value, str) and len(value) > MAX_SCALAR_STRING_LENGTH:
        raise RouteDecisionError(code, "route strings exceed the maximum length")


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


def _scalar_type(value: object) -> type[object]:
    if value is None:
        return type(None)
    if isinstance(value, bool):
        return bool
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    return str


def _value_type(value: object) -> object:
    if isinstance(value, list):
        return (list, _scalar_type(value[0]) if value else None)
    return _scalar_type(value)
