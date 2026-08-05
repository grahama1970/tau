"""Deterministic Tau agent-requirement validation and SciLLM transport-profile selection.

Implements the fixture-first slice of tau#308: validate a versioned semantic
``tau.agent_requirement.v1`` node requirement, compute eligible SciLLM
transport profiles deterministically from a frozen discovery payload
(scillm#27 shape), and freeze the outcome into a
``tau.transport_profile_selection_receipt.v1``.

Tau is not a provider/model registry: profiles are SciLLM-owned; Tau only
authorizes and records selection. Selection fails closed when no eligible
live-ready profile exists, when fallback would remove a required transport
capability, or when a profile's mode is incompatible with the node's Tau
harness mode. ``opaque_agent_compat`` profiles are eligible only when the
node explicitly permits that compatibility mode, and never transfer
completion, evidence, retry, or workspace authority out of Tau.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256

AGENT_REQUIREMENT_SCHEMA = "tau.agent_requirement.v1"
# Migration alias retained per tau#308; new plans must use the agent schema.
AGENT_REQUIREMENT_COMPAT_SCHEMAS = ("tau.worker_requirement.v2-agent",)
SELECTION_RECEIPT_SCHEMA = "tau.transport_profile_selection_receipt.v1"
SCILLM_PROFILE_SCHEMA = "scillm.transport_profile.v1"

TAU_NATIVE_HARNESS = "tau_native_agent_loop"
OPAQUE_COMPAT_HARNESS = "opaque_agent_compat"
HARNESS_MODES = (TAU_NATIVE_HARNESS, OPAQUE_COMPAT_HARNESS)

# Mirror of scillm.proxy.transport_profiles contract (scillm#27). Frozen here
# on purpose: Tau validates against the published contract, not a live import.
TRANSPORT_CAPABILITIES = frozenset(
    {
        "streaming",
        "tool_calling",
        "structured_output",
        "files",
        "vision",
        "cancellation",
        "session_resume",
        "structured_events",
        "reasoning_effort",
    }
)
TAU_OWNED_CAPABILITIES = frozenset(
    {
        "agent_loop",
        "tool_execution",
        "tool_authorization",
        "worktree_policy",
        "semantic_retry",
        "evidence_acceptance",
        "node_completion",
        "dag_completion",
    }
)
TRANSPORT_LIVE_READY = "transport_live_ready"
WORKSPACE_MODES = ("isolated_worktree", "shared_repo", "read_only")


class AgentRequirementError(RuntimeError):
    """Fail-closed agent requirement / selection error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def validate_agent_requirement(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a ``tau.agent_requirement.v1`` payload."""
    schema = payload.get("schema")
    if schema != AGENT_REQUIREMENT_SCHEMA and schema not in AGENT_REQUIREMENT_COMPAT_SCHEMAS:
        raise AgentRequirementError("agent_requirement_schema_invalid", str(schema))
    role = _required_string(payload, "role")
    harness = payload.get("harness", TAU_NATIVE_HARNESS)
    if harness not in HARNESS_MODES:
        raise AgentRequirementError("agent_requirement_harness_invalid", str(harness))
    preferences = _string_list(payload.get("profile_preferences"))
    if not preferences:
        raise AgentRequirementError("agent_requirement_no_profile_preferences", role)
    transport_caps = _string_list(payload.get("required_transport_capabilities"))
    for cap in transport_caps:
        if cap in TAU_OWNED_CAPABILITIES:
            raise AgentRequirementError("agent_requirement_tau_owned_capability", cap)
        if cap not in TRANSPORT_CAPABILITIES:
            raise AgentRequirementError("agent_requirement_unknown_capability", cap)
    workspace = _mapping(payload.get("workspace"))
    workspace_mode = workspace.get("mode", "isolated_worktree")
    if workspace_mode not in WORKSPACE_MODES:
        raise AgentRequirementError("agent_requirement_workspace_mode_invalid", str(workspace_mode))
    fallback = _mapping(payload.get("fallback_policy"))
    fallback_allowed = _bool(fallback.get("allowed"), default=True)
    prohibit_downgrade = _bool(fallback.get("prohibit_capability_downgrade"), default=True)
    if not prohibit_downgrade:
        # Downgrading a *required* capability silently is never admissible.
        raise AgentRequirementError("agent_requirement_downgrade_not_permitted", role)
    return {
        "schema": AGENT_REQUIREMENT_SCHEMA,
        "role": role,
        "harness": harness,
        "profile_preferences": list(preferences),
        "required_transport_capabilities": sorted(set(transport_caps)),
        "domain_capabilities": sorted(set(_string_list(payload.get("domain_capabilities")))),
        "workspace": {
            "mode": workspace_mode,
            "allowed_paths": sorted(set(_string_list(workspace.get("allowed_paths")))),
        },
        "required_evidence": sorted(set(_string_list(payload.get("required_evidence")))),
        "fallback_policy": {
            "allowed": fallback_allowed,
            "prohibit_capability_downgrade": True,
        },
    }


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    requirement: dict[str, Any]
    requirement_sha256: str
    discovery_sha256: str
    fallback_order: tuple[str, ...]
    eligible: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]
    selected: dict[str, Any]

    def receipt_payload(
        self,
        *,
        run_id: str,
        node_id: str,
        attempt_id: str,
        attempt: int,
        plan_sha256: str,
        goal_hash: str,
        policy_hash: str,
        data_boundary_hash: str,
    ) -> dict[str, Any]:
        proof_boundary = {
            "selection_is_model_free": True,
            "profiles_are_scillm_owned": True,
            "selection_is_not_semantic_quality_proof": True,
            "tau_retains_agent_loop_and_settlement": True,
            "opaque_compat_reduced_observability": self.selected["mode"] == OPAQUE_COMPAT_HARNESS,
        }
        return {
            "schema": SELECTION_RECEIPT_SCHEMA,
            "run_id": run_id,
            "node_id": node_id,
            "attempt_id": attempt_id,
            "attempt": attempt,
            "plan_sha256": plan_sha256,
            "goal_hash": goal_hash,
            "agent_requirement": self.requirement,
            "agent_requirement_sha256": self.requirement_sha256,
            "scillm_discovery_sha256": self.discovery_sha256,
            "fallback_order": list(self.fallback_order),
            "eligible_candidates": list(self.eligible),
            "rejected_candidates": list(self.rejected),
            "selected_profile": dict(self.selected),
            "policy_hash": policy_hash,
            "data_boundary_hash": data_boundary_hash,
            "proof_boundary_hash": canonical_sha256(proof_boundary),
            "proof_boundary": proof_boundary,
        }


def select_transport_profile(
    *,
    requirement: Mapping[str, Any],
    discovery: Mapping[str, Any],
    policy_denied_profiles: Sequence[str] = (),
) -> ProfileSelection:
    """Deterministically select a SciLLM transport profile for a Tau agent node.

    ``discovery`` is the frozen scillm#27 payload:
    ``{"profiles": [scillm.transport_profile.v1, ...],
       "readiness": {profile_id: readiness_state}}``.
    """
    normalized = validate_agent_requirement(requirement)
    profiles = _profiles_by_id(discovery)
    readiness = _mapping(discovery.get("readiness"))
    fallback_order = _fallback_order(normalized, profiles)
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for profile_id in fallback_order:
        profile = profiles.get(profile_id)
        if profile is None:
            rejected.append({"profile_id": profile_id, "rejection_codes": ["PROFILE_UNKNOWN"]})
            continue
        codes = _rejection_codes(
            normalized,
            profile,
            readiness_state=str(readiness.get(profile_id, "unavailable")),
            policy_denied=profile_id in set(policy_denied_profiles),
        )
        entry = {"profile_id": profile_id, "profile_sha256": canonical_sha256(profile)}
        if codes:
            rejected.append({**entry, "rejection_codes": codes})
        else:
            eligible.append(entry)
    if not eligible:
        raise AgentRequirementError(
            "no_eligible_transport_profile",
            ",".join(
                f"{item['profile_id']}:{'|'.join(item['rejection_codes'])}" for item in rejected
            ),
        )
    selected_id = eligible[0]["profile_id"]
    selected_profile = profiles[selected_id]
    requirement_payload = dict(normalized)
    return ProfileSelection(
        requirement=requirement_payload,
        requirement_sha256=canonical_sha256(requirement_payload),
        discovery_sha256=canonical_sha256(dict(discovery)),
        fallback_order=tuple(fallback_order),
        eligible=tuple(eligible),
        rejected=tuple(rejected),
        selected={
            "profile_id": selected_id,
            "profile_sha256": canonical_sha256(selected_profile),
            "provider": selected_profile.get("provider"),
            "model": selected_profile.get("model"),
            "mode": selected_profile.get("mode"),
            "capabilities": sorted(_string_list(selected_profile.get("capabilities"))),
            "readiness": str(readiness.get(selected_id, "unavailable")),
        },
    )


def validate_selection_receipt(payload: Mapping[str, Any]) -> None:
    """Fail closed on a malformed transport-profile selection receipt."""
    if payload.get("schema") != SELECTION_RECEIPT_SCHEMA:
        raise AgentRequirementError("selection_receipt_schema_invalid", str(payload.get("schema")))
    for key in (
        "run_id",
        "node_id",
        "attempt_id",
        "plan_sha256",
        "goal_hash",
        "agent_requirement_sha256",
        "scillm_discovery_sha256",
        "policy_hash",
        "data_boundary_hash",
        "proof_boundary_hash",
    ):
        _required_string(payload, key)
    requirement = _mapping(payload.get("agent_requirement"))
    normalized = validate_agent_requirement(requirement)
    if canonical_sha256(normalized) != payload["agent_requirement_sha256"]:
        raise AgentRequirementError("selection_receipt_requirement_hash_mismatch")
    eligible = payload.get("eligible_candidates")
    rejected = payload.get("rejected_candidates")
    if not isinstance(eligible, list) or not eligible:
        raise AgentRequirementError("selection_receipt_missing_eligible_candidates")
    if not isinstance(rejected, list):
        raise AgentRequirementError("selection_receipt_missing_rejected_candidates")
    for item in rejected:
        entry = _mapping(item)
        if not _string_list(entry.get("rejection_codes")):
            raise AgentRequirementError(
                "selection_receipt_rejection_reason_missing", str(entry.get("profile_id"))
            )
    selected = _mapping(payload.get("selected_profile"))
    for key in ("profile_id", "profile_sha256", "provider", "model", "mode", "readiness"):
        if not _string(selected.get(key)):
            raise AgentRequirementError("selection_receipt_selected_profile_incomplete", key)
    if selected["readiness"] != TRANSPORT_LIVE_READY:
        raise AgentRequirementError(
            "selection_receipt_missing_live_readiness", str(selected["profile_id"])
        )
    if selected["mode"] == OPAQUE_COMPAT_HARNESS and normalized["harness"] != OPAQUE_COMPAT_HARNESS:
        raise AgentRequirementError(
            "selection_receipt_mode_incompatible_with_node", str(selected["profile_id"])
        )
    if selected["mode"] != OPAQUE_COMPAT_HARNESS and normalized["harness"] == OPAQUE_COMPAT_HARNESS:
        raise AgentRequirementError(
            "selection_receipt_mode_incompatible_with_node", str(selected["profile_id"])
        )


def _fallback_order(
    requirement: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    order: list[str] = []
    for preference in requirement["profile_preferences"]:
        if preference not in order:
            order.append(preference)
        if not requirement["fallback_policy"]["allowed"]:
            continue
        for fallback_id in _string_list(_mapping(profiles.get(preference)).get("fallbacks")):
            if fallback_id not in order:
                order.append(fallback_id)
    return order


def _rejection_codes(
    requirement: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    readiness_state: str,
    policy_denied: bool,
) -> list[str]:
    codes: list[str] = []
    if profile.get("schema") not in {None, SCILLM_PROFILE_SCHEMA}:
        codes.append("PROFILE_SCHEMA_INVALID")
    if policy_denied:
        codes.append("POLICY_DATA_BOUNDARY_REJECTED")
    mode = _string(profile.get("mode")) or ""
    harness = requirement["harness"]
    if harness == TAU_NATIVE_HARNESS and mode == OPAQUE_COMPAT_HARNESS:
        codes.append("PROFILE_MODE_INCOMPATIBLE_WITH_TAU_NATIVE")
    if harness == OPAQUE_COMPAT_HARNESS and mode != OPAQUE_COMPAT_HARNESS:
        codes.append("PROFILE_MODE_NOT_OPAQUE_COMPAT")
    capabilities = set(_string_list(profile.get("capabilities")))
    missing = sorted(set(requirement["required_transport_capabilities"]) - capabilities)
    if missing:
        codes.append("TRANSPORT_CAPABILITY_MISSING:" + "|".join(missing))
    domain = set(requirement["domain_capabilities"])
    tags = set(_string_list(profile.get("tags")))
    if requirement["role"] not in tags:
        codes.append("PROFILE_ROLE_TAG_MISSING")
    missing_domain = sorted(domain - tags - capabilities)
    if missing_domain:
        codes.append("DOMAIN_CAPABILITY_MISSING:" + "|".join(missing_domain))
    if readiness_state != TRANSPORT_LIVE_READY:
        codes.append(f"PROFILE_NOT_LIVE_READY:{readiness_state}")
    return codes


def _profiles_by_id(discovery: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    profiles = discovery.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise AgentRequirementError("scillm_discovery_missing_profiles")
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in profiles:
        entry = _mapping(item)
        profile_id = _string(entry.get("id"))
        if profile_id is None:
            raise AgentRequirementError("scillm_discovery_profile_id_missing")
        if profile_id in by_id:
            raise AgentRequirementError("scillm_discovery_profile_id_duplicate", profile_id)
        by_id[profile_id] = entry
    return by_id


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _string(payload.get(key))
    if value is None:
        raise AgentRequirementError("agent_requirement_field_missing", key)
    return value


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise AgentRequirementError("agent_requirement_boolean_invalid", str(value))
    return value
