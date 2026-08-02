"""Typed node-level completion boundary validation for high-assurance DAG nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.public_dag_contracts import immutable_json

NODE_COMPLETION_BOUNDARY_SCHEMA = "tau.node_completion_boundary.v1"
NODE_COMPLETION_BOUNDARY_POLICY_SCHEMA = "tau.node_completion_boundary_policy.v1"

BOUNDARY_SECTIONS: tuple[str, ...] = (
    "checked_scope",
    "not_checked",
    "assumptions",
    "known_unknowns",
    "evidence_gaps",
    "recommended_followups",
    "proves",
    "does_not_prove",
)

BOUNDARY_ITEM_DECLARED_FACT_KEYS: tuple[str, ...] = (
    "proposed_node",
    "requested_paths",
    "requested_capabilities",
    "requested_resources",
    "data_classes",
    "side_effect_class",
    "budget",
    "scope_claim",
    "requires_human_approval",
)


@dataclass(frozen=True, slots=True)
class NodeCompletionBoundaryValidation:
    ok: bool
    boundary: Mapping[str, Any] | None
    boundary_sha256: str | None
    alert_codes: tuple[str, ...]
    errors: tuple[str, ...]
    required_sections: tuple[str, ...]
    non_empty_sections: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.boundary is not None:
            object.__setattr__(self, "boundary", immutable_json(self.boundary))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "tau.node_completion_boundary_validation.v1",
            "status": "PASS" if self.ok else "BLOCKED",
            "boundary_schema": NODE_COMPLETION_BOUNDARY_SCHEMA,
            "boundary_sha256": self.boundary_sha256,
            "required_sections": list(self.required_sections),
            "non_empty_sections": list(self.non_empty_sections),
            "alert_codes": list(self.alert_codes),
            "errors": list(self.errors),
            "self_reported_coverage_is_evidence": False,
            "proves": [
                "Boundary identity and required typed sections matched scheduler-owned node scope."
            ]
            if self.ok
            else [],
            "does_not_prove": [
                "Self-reported checked/not-checked coverage does not prove completeness.",
                "Self-reported assumptions and evidence gaps do not prove correctness.",
            ],
        }


def requires_node_completion_boundary(required_evidence: tuple[str, ...]) -> bool:
    return NODE_COMPLETION_BOUNDARY_SCHEMA in required_evidence


def validate_node_completion_boundary(
    raw_boundary: object,
    *,
    expected_goal_hash: str,
    expected_plan_sha256: str,
    expected_node_id: str,
    expected_attempt_id: str,
    policy: object = None,
) -> NodeCompletionBoundaryValidation:
    policy_result = _normalize_policy(policy)
    required_sections = policy_result["required_sections"]
    non_empty_sections = policy_result["non_empty_sections"]
    alerts: list[str] = list(policy_result["alert_codes"])
    errors: list[str] = list(policy_result["errors"])
    if not isinstance(raw_boundary, Mapping):
        return NodeCompletionBoundaryValidation(
            ok=False,
            boundary=None,
            boundary_sha256=None,
            alert_codes=tuple([*alerts, "node_completion_boundary_missing"]),
            errors=tuple([*errors, "node_completion_boundary must be an object"]),
            required_sections=required_sections,
            non_empty_sections=non_empty_sections,
        )

    if raw_boundary.get("schema") != NODE_COMPLETION_BOUNDARY_SCHEMA:
        alerts.append("node_completion_boundary_malformed")
        errors.append(f"node_completion_boundary.schema must be {NODE_COMPLETION_BOUNDARY_SCHEMA}")

    identity_checks = (
        ("goal_hash", expected_goal_hash, "node_completion_boundary_goal_mismatch"),
        ("plan_sha256", expected_plan_sha256, "node_completion_boundary_plan_mismatch"),
        ("node_id", expected_node_id, "node_completion_boundary_node_mismatch"),
        ("attempt_id", expected_attempt_id, "node_completion_boundary_attempt_mismatch"),
    )
    for field, expected, code in identity_checks:
        observed = raw_boundary.get(field)
        if observed != expected:
            alerts.append(code)
            errors.append(f"{field} mismatch: expected {expected}, observed {observed!r}")

    normalized_sections: dict[str, list[dict[str, Any]]] = {}
    for section in BOUNDARY_SECTIONS:
        value = raw_boundary.get(section)
        if section in required_sections and section not in raw_boundary:
            alerts.append("node_completion_boundary_missing_required_section")
            errors.append(f"{section} is required")
            normalized_sections[section] = []
            continue
        if value is None and section not in raw_boundary:
            normalized_sections[section] = []
            continue
        items, section_errors = _normalize_typed_items(value, section=section)
        if section_errors:
            alerts.append("node_completion_boundary_invalid_item")
            errors.extend(section_errors)
        if section in non_empty_sections and not items:
            alerts.append("node_completion_boundary_empty_required_section")
            errors.append(f"{section} must be non-empty by policy")
        normalized_sections[section] = items

    boundary = {
        "schema": NODE_COMPLETION_BOUNDARY_SCHEMA,
        "goal_hash": str(raw_boundary.get("goal_hash") or ""),
        "plan_sha256": str(raw_boundary.get("plan_sha256") or ""),
        "node_id": str(raw_boundary.get("node_id") or ""),
        "attempt_id": str(raw_boundary.get("attempt_id") or ""),
        **normalized_sections,
        "self_reported_coverage_is_evidence": False,
        "does_not_prove_completeness_or_correctness": True,
    }
    return NodeCompletionBoundaryValidation(
        ok=not alerts and not errors,
        boundary=boundary,
        boundary_sha256=canonical_sha256(boundary),
        alert_codes=tuple(dict.fromkeys(alerts)),
        errors=tuple(errors),
        required_sections=required_sections,
        non_empty_sections=non_empty_sections,
    )


def _normalize_policy(policy: object) -> dict[str, Any]:
    required_sections = BOUNDARY_SECTIONS
    non_empty_sections: tuple[str, ...] = ()
    alerts: list[str] = []
    errors: list[str] = []
    if policy is None:
        return {
            "required_sections": required_sections,
            "non_empty_sections": non_empty_sections,
            "alert_codes": alerts,
            "errors": errors,
        }
    if not isinstance(policy, Mapping):
        return {
            "required_sections": required_sections,
            "non_empty_sections": non_empty_sections,
            "alert_codes": ["node_completion_boundary_policy_invalid"],
            "errors": ["node_completion_boundary_policy must be an object"],
        }
    if policy.get("schema") not in (None, NODE_COMPLETION_BOUNDARY_POLICY_SCHEMA):
        alerts.append("node_completion_boundary_policy_invalid")
        errors.append(
            "node_completion_boundary_policy.schema must be "
            f"{NODE_COMPLETION_BOUNDARY_POLICY_SCHEMA}"
        )
    required_raw = policy.get("required_sections", BOUNDARY_SECTIONS)
    required_sections, required_errors = _section_tuple(
        required_raw,
        field="node_completion_boundary_policy.required_sections",
    )
    if required_errors:
        alerts.append("node_completion_boundary_policy_invalid")
        errors.extend(required_errors)
        required_sections = BOUNDARY_SECTIONS
    non_empty_raw = policy.get("non_empty_sections", ())
    non_empty_sections, non_empty_errors = _section_tuple(
        non_empty_raw,
        field="node_completion_boundary_policy.non_empty_sections",
    )
    if non_empty_errors:
        alerts.append("node_completion_boundary_policy_invalid")
        errors.extend(non_empty_errors)
        non_empty_sections = ()
    return {
        "required_sections": required_sections,
        "non_empty_sections": non_empty_sections,
        "alert_codes": alerts,
        "errors": errors,
    }


def _section_tuple(value: object, *, field: str) -> tuple[tuple[str, ...], list[str]]:
    if not isinstance(value, (list, tuple)):
        return (), [f"{field} must be a list"]
    errors: list[str] = []
    sections: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in BOUNDARY_SECTIONS:
            errors.append(f"{field}[{index}] is not a known boundary section")
            continue
        sections.append(item)
    return tuple(dict.fromkeys(sections)), errors


def _normalize_typed_items(
    value: object, *, section: str
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, list):
        return [], [f"{section} must be a list of typed items"]
    errors: list[str] = []
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            errors.append(f"{section}[{index}] must be an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{section}[{index}].id must be a non-empty string")
            continue
        if item_id in seen:
            errors.append(f"{section}[{index}].id is duplicated: {item_id}")
            continue
        statement = item.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            errors.append(f"{section}[{index}].statement must be a non-empty string")
            continue
        evidence_refs = item.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            errors.append(f"{section}[{index}].evidence_refs must be a list when provided")
            continue
        refs, ref_errors = _normalize_evidence_refs(evidence_refs, section=section, index=index)
        if ref_errors:
            errors.extend(ref_errors)
            continue
        normalized_item = {
            "id": item_id,
            "statement": statement,
            "evidence_refs": refs,
        }
        if isinstance(item.get("severity"), str) and item["severity"].strip():
            normalized_item["severity"] = item["severity"]
        if isinstance(item.get("followup"), str) and item["followup"].strip():
            normalized_item["followup"] = item["followup"]
        for fact_key in BOUNDARY_ITEM_DECLARED_FACT_KEYS:
            if fact_key in item:
                normalized_item[fact_key] = _canonical_json_value(item[fact_key])
        seen.add(item_id)
        normalized.append(normalized_item)
    return sorted(normalized, key=lambda entry: entry["id"]), errors


def _canonical_json_value(value: object) -> object:
    """Keep producer-declared facts JSON-safe and hash-stable."""

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_evidence_refs(
    value: list[Any],
    *,
    section: str,
    index: int,
) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    refs: list[dict[str, str]] = []
    for ref_index, ref in enumerate(value):
        if not isinstance(ref, Mapping):
            errors.append(f"{section}[{index}].evidence_refs[{ref_index}] must be an object")
            continue
        kind = ref.get("kind")
        ref_id = ref.get("id")
        if not isinstance(kind, str) or not kind.strip():
            errors.append(f"{section}[{index}].evidence_refs[{ref_index}].kind must be a string")
            continue
        if not isinstance(ref_id, str) or not ref_id.strip():
            errors.append(f"{section}[{index}].evidence_refs[{ref_index}].id must be a string")
            continue
        normalized = {"kind": kind, "id": ref_id}
        if isinstance(ref.get("sha256"), str):
            normalized["sha256"] = ref["sha256"]
        if isinstance(ref.get("path"), str):
            normalized["path"] = ref["path"]
        refs.append(normalized)
    return sorted(refs, key=lambda entry: (entry["kind"], entry["id"])), errors
