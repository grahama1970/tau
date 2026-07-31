"""Hash-bound execution profile resolution for canonical Tau DAG plans."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from enum import StrEnum
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256

EXECUTION_PROFILE_SCHEMA = "tau.execution_profile.v1"
EXECUTION_PROFILE_RESOLUTION_SCHEMA = "tau.execution_profile_resolution.v1"
EXECUTION_PROFILE_REVISION_SCHEMA = "tau.execution_profile_revision.v1"
EXECUTION_PROFILE_IDS = ("interactive", "standard", "assurance")
_PROFILE_RANK = {profile: rank for rank, profile in enumerate(EXECUTION_PROFILE_IDS)}
_OPTIONAL_CAPABILITIES = {
    "scoped_review_evidence": "tau.scoped_review_receipt.v1",
    "node_completion_boundary": "tau.node_completion_boundary_receipt.v1",
    "input_manifest": "tau.node_input_manifest.v1",
    "stale_read_reconciliation": "tau.workspace_stale_read_reconciliation.v1",
}


class ExecutionProfileError(RuntimeError):
    """Fail-closed profile resolution error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class ProfileRevisionDisposition(StrEnum):
    UNCHANGED = "UNCHANGED"
    STRENGTHENED = "STRENGTHENED"
    REJECTED = "REJECTED"


def resolve_execution_profile(
    *,
    payload: Mapping[str, Any],
    source_family: str,
    source_schema: str,
    source_limits: Mapping[str, Any],
    source_required_evidence: tuple[str, ...],
    source_fail_closed_on: tuple[str, ...],
    node_count: int,
    edge_count: int,
    max_concurrency: int | None,
) -> dict[str, Any]:
    raw = payload.get("execution_profile")
    selected_profile, selection_source, explicit_overrides = _parse_profile(raw)
    parent_policy = _mapping_or_empty(payload.get("execution_profile_policy"))
    data_boundary = payload.get("data_boundary")
    _reject_model_authored_profile(payload, raw)
    selected_profile = _apply_parent_policy(
        selected_profile,
        parent_policy=parent_policy,
        data_boundary=data_boundary,
    )
    defaults = _profile_defaults(selected_profile)
    controls = deepcopy(defaults)
    source_overrides = _source_overrides(
        source_limits=source_limits,
        source_required_evidence=source_required_evidence,
        source_fail_closed_on=source_fail_closed_on,
        max_concurrency=max_concurrency,
    )
    accepted_overrides: list[dict[str, Any]] = []
    rejected_overrides: list[dict[str, Any]] = []
    _apply_overrides(
        controls,
        source_overrides,
        origin="source_contract",
        accepted=accepted_overrides,
        rejected=rejected_overrides,
    )
    _apply_overrides(
        controls,
        explicit_overrides,
        origin="execution_profile",
        accepted=accepted_overrides,
        rejected=rejected_overrides,
    )
    if rejected_overrides:
        detail = ",".join(item["field"] for item in rejected_overrides)
        raise ExecutionProfileError("execution_profile_override_broadens_policy", detail)
    compatibility_default = raw is None
    optional = _optional_capability_status()
    controls["required_evidence_schemas"] = sorted(
        set(controls["required_evidence_schemas"])
        | set(source_required_evidence)
        | _profile_required_optional_evidence(selected_profile, optional)
    )
    controls["effective_node_count"] = node_count
    controls["effective_edge_count"] = edge_count
    controls_hash = canonical_sha256(controls)
    resolution = {
        "schema": EXECUTION_PROFILE_RESOLUTION_SCHEMA,
        "profile_schema": EXECUTION_PROFILE_SCHEMA,
        "profile_id": selected_profile,
        "profile_version": 1,
        "selection_source": selection_source,
        "compatibility_default": compatibility_default,
        "historical_profile_omitted": compatibility_default,
        "source_family": source_family,
        "source_schema": source_schema,
        "defaults_applied": defaults,
        "source_overrides_accepted": accepted_overrides,
        "source_overrides_rejected": rejected_overrides,
        "parent_policy": dict(parent_policy),
        "policy_data_boundary_compatibility": _policy_data_boundary_compatibility(
            selected_profile,
            data_boundary=data_boundary,
        ),
        "optional_capabilities": optional,
        "resolved_controls": controls,
        "resolved_controls_sha256": controls_hash,
        "proof_boundary": {
            "model_confidence_used": False,
            "profile_alters_scheduler_engine": False,
            "provider_calls_performed": False,
            "historical_runs_rewritten": False,
        },
    }
    return {**resolution, "resolution_sha256": canonical_sha256(resolution)}


def execution_limits_with_profile(
    source_limits: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(source_limits),
        "execution_profile": {
            "profile_id": resolution["profile_id"],
            "resolution_sha256": resolution["resolution_sha256"],
            "resolved_controls": dict(resolution["resolved_controls"]),
        },
    }


def source_extensions_with_profile(
    source_extensions: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(source_extensions),
        "execution_profile_resolution": dict(resolution),
    }


def evaluate_profile_revision(
    previous_resolution: Mapping[str, Any],
    requested_profile: str,
    *,
    approved_strengthening: bool,
) -> dict[str, Any]:
    previous = str(previous_resolution.get("profile_id") or "")
    if previous not in _PROFILE_RANK:
        raise ExecutionProfileError("execution_profile_previous_invalid", previous)
    if requested_profile not in _PROFILE_RANK:
        raise ExecutionProfileError("execution_profile_unknown", requested_profile)
    previous_rank = _PROFILE_RANK[previous]
    requested_rank = _PROFILE_RANK[requested_profile]
    if requested_rank < previous_rank:
        disposition = ProfileRevisionDisposition.REJECTED
        verdict = "PROFILE_DOWNGRADE_REJECTED"
    elif requested_rank > previous_rank and not approved_strengthening:
        disposition = ProfileRevisionDisposition.REJECTED
        verdict = "PROFILE_STRENGTHENING_REQUIRES_APPROVAL"
    elif requested_rank > previous_rank:
        disposition = ProfileRevisionDisposition.STRENGTHENED
        verdict = "PASS"
    else:
        disposition = ProfileRevisionDisposition.UNCHANGED
        verdict = "PASS"
    payload = {
        "schema": EXECUTION_PROFILE_REVISION_SCHEMA,
        "previous_profile_id": previous,
        "requested_profile_id": requested_profile,
        "approved_strengthening": approved_strengthening,
        "disposition": disposition.value,
        "status": "PASS" if verdict == "PASS" else "BLOCKED",
        "verdict": verdict,
        "new_plan_required": disposition is ProfileRevisionDisposition.STRENGTHENED,
        "proof_boundary": {
            "provider_calls_performed": False,
            "model_confidence_used": False,
        },
    }
    return {**payload, "revision_sha256": canonical_sha256(payload)}


def _parse_profile(raw: Any) -> tuple[str, str, Mapping[str, Any]]:
    if raw is None:
        return "standard", "compatibility_default_profileless_current_contract", {}
    if isinstance(raw, str):
        _require_known_profile(raw)
        return raw, "source_declared", {}
    if not isinstance(raw, Mapping):
        raise ExecutionProfileError("execution_profile_invalid", "must be string or object")
    if raw.get("schema") not in {None, EXECUTION_PROFILE_SCHEMA}:
        raise ExecutionProfileError("execution_profile_schema_invalid", str(raw.get("schema")))
    if raw.get("authored_by") == "model" or raw.get("source") == "model":
        raise ExecutionProfileError("execution_profile_model_authored", "model source is refused")
    profile_id = str(raw.get("profile_id") or raw.get("id") or "")
    _require_known_profile(profile_id)
    overrides = raw.get("overrides") or {}
    if not isinstance(overrides, Mapping):
        raise ExecutionProfileError("execution_profile_overrides_invalid", profile_id)
    return profile_id, "source_declared", overrides


def _reject_model_authored_profile(payload: Mapping[str, Any], raw: Any) -> None:
    del raw
    for key in ("model_authored_execution_profile", "model_profile_override"):
        if key in payload:
            raise ExecutionProfileError("execution_profile_model_authored", key)


def _apply_parent_policy(
    profile_id: str,
    *,
    parent_policy: Mapping[str, Any],
    data_boundary: Any,
) -> str:
    max_profile = parent_policy.get("max_profile")
    if (
        isinstance(max_profile, str)
        and max_profile in _PROFILE_RANK
        and _PROFILE_RANK[profile_id] > _PROFILE_RANK[max_profile]
    ):
        raise ExecutionProfileError(
            "execution_profile_parent_policy_rejects_broadening",
            f"{profile_id}>{max_profile}",
        )
    min_profile = parent_policy.get("min_profile")
    if (
        isinstance(min_profile, str)
        and min_profile in _PROFILE_RANK
        and _PROFILE_RANK[profile_id] < _PROFILE_RANK[min_profile]
    ):
        profile_id = min_profile
    if _data_boundary_requires_assurance(data_boundary) and profile_id != "assurance":
        profile_id = "assurance"
    return profile_id


def _profile_defaults(profile_id: str) -> dict[str, Any]:
    _require_known_profile(profile_id)
    base = {
        "canonical_scheduler_required": True,
        "durable_run_store_required": True,
        "required_evidence_schemas": [],
        "review_requirements": "node_declared",
        "approval_requirements": "exact_human_gate_for_irreversible_effects",
        "stale_read_behavior": "observe",
        "input_manifest_enforcement": "when_declared",
        "adaptive_expansion": {"max_nodes": 0, "authorization": "none"},
        "side_effect_defaults": {
            "external_side_effects": "deny_without_exact_human_gate",
            "irreversible_effects": "deny_without_exact_human_gate",
        },
        "security_defaults": {
            "network": "deny_by_default",
            "filesystem_mutation": "node_declared_only",
        },
    }
    if profile_id == "interactive":
        return {
            **base,
            "durable_run_store_required": False,
            "max_concurrency": 1,
            "max_nodes": 5,
            "max_depth": 3,
            "max_attempts_per_node": 1,
            "max_total_attempts": 5,
            "max_run_seconds": 600,
            "required_evidence_schemas": [],
            "review_requirements": "none_unless_declared",
            "stale_read_behavior": "observe",
            "input_manifest_enforcement": "when_declared",
            "adaptive_expansion": {"max_nodes": 1, "authorization": "human_gate"},
            "side_effect_defaults": {
                "external_side_effects": "deny_without_exact_human_gate",
                "irreversible_effects": "deny_without_exact_human_gate",
                "local_mutation": "bounded_reversible_only",
            },
        }
    if profile_id == "standard":
        return {
            **base,
            "max_concurrency": 4,
            "max_nodes": 50,
            "max_depth": 10,
            "max_attempts_per_node": 3,
            "max_total_attempts": 150,
            "max_run_seconds": 3600,
            "required_evidence_schemas": ["tau.generic_dag_node_receipt.v1"],
            "adaptive_expansion": {"max_nodes": 3, "authorization": "policy_bounded"},
        }
    return {
        **base,
        "max_concurrency": 2,
        "max_nodes": 100,
        "max_depth": 20,
        "max_attempts_per_node": 3,
        "max_total_attempts": 300,
        "max_run_seconds": 7200,
        "required_evidence_schemas": [
            "tau.generic_dag_node_receipt.v1",
            "tau.execution_profile_resolution.v1",
        ],
        "review_requirements": "scoped_review_when_declared_or_applicable",
        "stale_read_behavior": "fail_closed_when_available",
        "input_manifest_enforcement": "required_when_available",
        "adaptive_expansion": {"max_nodes": 5, "authorization": "exact_human_gate"},
        "side_effect_defaults": {
            "external_side_effects": "deny_without_exact_human_gate",
            "irreversible_effects": "deny_without_exact_human_gate",
            "local_mutation": "receipt_and_boundary_required",
        },
        "security_defaults": {
            "network": "deny_by_default",
            "filesystem_mutation": "receipt_and_boundary_required",
            "high_risk_effects": "exact_approval_required",
        },
    }


def _source_overrides(
    *,
    source_limits: Mapping[str, Any],
    source_required_evidence: tuple[str, ...],
    source_fail_closed_on: tuple[str, ...],
    max_concurrency: int | None,
) -> dict[str, Any]:
    del source_required_evidence
    overrides: dict[str, Any] = {}
    numeric_map = {
        "max_total_attempts": "max_total_attempts",
        "max_nodes": "max_nodes",
        "max_depth": "max_depth",
        "default_timeout_seconds": "max_run_seconds",
        "max_run_seconds": "max_run_seconds",
    }
    for source_key, target_key in numeric_map.items():
        if source_key in source_limits:
            overrides[target_key] = source_limits[source_key]
    if max_concurrency is not None:
        overrides["max_concurrency"] = max_concurrency
    if source_fail_closed_on:
        overrides["fail_closed_on"] = list(source_fail_closed_on)
    return overrides


def _apply_overrides(
    controls: dict[str, Any],
    overrides: Mapping[str, Any],
    *,
    origin: str,
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> None:
    for field, raw_value in sorted(overrides.items()):
        if field in {
            "max_concurrency",
            "max_nodes",
            "max_depth",
            "max_attempts_per_node",
            "max_total_attempts",
            "max_run_seconds",
        }:
            value = _positive_int(raw_value, field)
            if value <= int(controls[field]):
                controls[field] = value
                accepted.append({"origin": origin, "field": field, "value": value})
            else:
                rejected.append(
                    {
                        "origin": origin,
                        "field": field,
                        "value": value,
                        "reason": "numeric_budget_broadening",
                    }
                )
        elif field == "required_evidence_schemas":
            values = _string_list(raw_value, field)
            current = set(controls["required_evidence_schemas"])
            if set(values).issuperset(current):
                controls[field] = sorted(set(values))
                accepted.append({"origin": origin, "field": field, "value": sorted(set(values))})
            else:
                rejected.append(
                    {
                        "origin": origin,
                        "field": field,
                        "value": sorted(set(values)),
                        "reason": "evidence_requirement_weakening",
                    }
                )
        elif field == "fail_closed_on":
            values = _string_list(raw_value, field)
            controls[field] = sorted(set(values))
            accepted.append({"origin": origin, "field": field, "value": sorted(set(values))})
        else:
            rejected.append(
                {
                    "origin": origin,
                    "field": str(field),
                    "value": raw_value,
                    "reason": "unsupported_override",
                }
            )


def _profile_required_optional_evidence(
    profile_id: str,
    optional: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    if profile_id != "assurance":
        return set()
    return {
        str(item["schema"])
        for item in optional.values()
        if item.get("status") == "AVAILABLE" and isinstance(item.get("schema"), str)
    }


def _optional_capability_status() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "AVAILABLE",
            "schema": schema,
            "degradation": None,
        }
        for name, schema in sorted(_OPTIONAL_CAPABILITIES.items())
    }


def _policy_data_boundary_compatibility(profile_id: str, *, data_boundary: Any) -> dict[str, Any]:
    return {
        "status": "PASS",
        "profile_id": profile_id,
        "data_boundary_requires_assurance": _data_boundary_requires_assurance(data_boundary),
        "policy_may_narrow": True,
        "source_may_narrow": True,
        "broadening_allowed": False,
    }


def _data_boundary_requires_assurance(data_boundary: Any) -> bool:
    if not isinstance(data_boundary, Mapping):
        return False
    values = {
        str(data_boundary.get("classification") or "").lower(),
        str(data_boundary.get("export_control") or "").lower(),
        str(data_boundary.get("foreign_person_access") or "").lower(),
    }
    return bool(values & {"itar", "controlled", "classified", "denied"})


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ExecutionProfileError("execution_profile_override_invalid", label)
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ExecutionProfileError("execution_profile_override_invalid", label)
    return list(value)


def _require_known_profile(profile_id: str) -> None:
    if profile_id not in _PROFILE_RANK:
        raise ExecutionProfileError("execution_profile_unknown", profile_id)
