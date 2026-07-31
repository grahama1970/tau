"""Deterministic worker capability matching for DAG scheduler dispatch."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256

WORKER_CAPABILITY_SCHEMA = "tau.worker_capability.v1"
WORKER_REQUIREMENT_SCHEMA = "tau.worker_requirement.v1"
WORKER_ASSIGNMENT_RECEIPT_SCHEMA = "tau.worker_assignment_receipt.v1"


class WorkerAssignmentError(RuntimeError):
    """Fail-closed worker assignment error with a stable verdict code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class WorkerRegistry(Protocol):
    """Capability source used by the scheduler before adapter dispatch."""

    def capabilities(self) -> Iterable[WorkerCapability | Mapping[str, Any]]:
        """Return current worker capability descriptors."""


@dataclass(frozen=True, slots=True)
class WorkerCapability:
    worker_id: str
    runtime_kind: str
    interaction_modes: tuple[str, ...]
    supported_capabilities: tuple[str, ...]
    session_scopes: tuple[str, ...]
    provider: str | None = None
    model: str | None = None
    adapter_kind: str | None = None
    trust_zones: tuple[str, ...] = ("local",)
    data_classes: tuple[str, ...] = ("public",)
    worktree_ids: tuple[str, ...] = ()
    supports_worktree_binding: bool = False
    concurrency_slots: int = 1
    active_slots: int = 0
    health: str = "HEALTHY"
    readiness: str = "READY"
    reset_required: bool = False
    cleanup_required: bool = True
    resource_id: str | None = None
    priority: int = 100

    def __post_init__(self) -> None:
        if not self.worker_id.strip() or not self.runtime_kind.strip():
            raise ValueError("worker_id and runtime_kind must not be empty")
        if not self.interaction_modes:
            raise ValueError("worker interaction_modes must not be empty")
        if not self.session_scopes:
            raise ValueError("worker session_scopes must not be empty")
        if self.concurrency_slots < 1:
            raise ValueError("worker concurrency_slots must be at least 1")
        if self.active_slots < 0:
            raise ValueError("worker active_slots must be non-negative")
        object.__setattr__(self, "interaction_modes", _sorted_unique(self.interaction_modes))
        object.__setattr__(
            self, "supported_capabilities", _sorted_unique(self.supported_capabilities)
        )
        object.__setattr__(self, "session_scopes", _sorted_unique(self.session_scopes))
        object.__setattr__(self, "trust_zones", _sorted_unique(self.trust_zones))
        object.__setattr__(self, "data_classes", _sorted_unique(self.data_classes))
        object.__setattr__(self, "worktree_ids", _sorted_unique(self.worktree_ids))

    @property
    def capability_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @property
    def worker_resource_id(self) -> str:
        return self.resource_id or f"worker:{self.worker_id}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKER_CAPABILITY_SCHEMA,
            "worker_id": self.worker_id,
            "runtime_kind": self.runtime_kind,
            "provider": self.provider,
            "model": self.model,
            "adapter_kind": self.adapter_kind,
            "interaction_modes": list(self.interaction_modes),
            "supported_capabilities": list(self.supported_capabilities),
            "session_scopes": list(self.session_scopes),
            "trust_zones": list(self.trust_zones),
            "data_classes": list(self.data_classes),
            "worktree_ids": list(self.worktree_ids),
            "supports_worktree_binding": self.supports_worktree_binding,
            "concurrency_slots": self.concurrency_slots,
            "active_slots": self.active_slots,
            "health": self.health,
            "readiness": self.readiness,
            "reset_required": self.reset_required,
            "cleanup_required": self.cleanup_required,
            "resource_id": self.worker_resource_id,
            "priority": self.priority,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> WorkerCapability:
        if payload.get("schema") not in {None, WORKER_CAPABILITY_SCHEMA}:
            raise ValueError("worker capability schema is invalid")
        return cls(
            worker_id=_required_string(payload, "worker_id"),
            runtime_kind=_required_string(payload, "runtime_kind"),
            provider=_optional_string(payload.get("provider")),
            model=_optional_string(payload.get("model")),
            adapter_kind=_optional_string(payload.get("adapter_kind")),
            interaction_modes=_string_tuple(payload, "interaction_modes"),
            supported_capabilities=_string_tuple(payload, "supported_capabilities"),
            session_scopes=_string_tuple(payload, "session_scopes"),
            trust_zones=_string_tuple(payload, "trust_zones", default=("local",)),
            data_classes=_string_tuple(payload, "data_classes", default=("public",)),
            worktree_ids=_string_tuple(payload, "worktree_ids", default=()),
            supports_worktree_binding=_bool(payload.get("supports_worktree_binding")),
            concurrency_slots=_int(payload.get("concurrency_slots"), default=1),
            active_slots=_int(payload.get("active_slots"), default=0),
            health=_optional_string(payload.get("health")) or "HEALTHY",
            readiness=_optional_string(payload.get("readiness")) or "READY",
            reset_required=_bool(payload.get("reset_required")),
            cleanup_required=_bool(payload.get("cleanup_required"), default=True),
            resource_id=_optional_string(payload.get("resource_id")),
            priority=_int(payload.get("priority"), default=100),
        )


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    requirement: dict[str, Any]
    requirement_sha256: str
    candidates: tuple[WorkerCapability, ...]
    eligible: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]
    selected: WorkerCapability

    def receipt_payload(
        self,
        *,
        resource_lease_token: Mapping[str, Any],
    ) -> dict[str, Any]:
        proof_boundary = {
            "workers_are_evidence_sources": False,
            "selection_is_model_free": True,
            "adapter_dispatch_requires_attempt_bound_resource_lease": True,
        }
        return {
            "schema": WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
            "run_id": self.requirement["run_id"],
            "node_id": self.requirement["node_id"],
            "attempt_id": self.requirement["attempt_id"],
            "attempt": self.requirement["attempt"],
            "plan_sha256": self.requirement["plan_sha256"],
            "goal_hash": self.requirement["goal_hash"],
            "worker_requirement": self.requirement,
            "worker_requirement_sha256": self.requirement_sha256,
            "candidate_capability_hashes": [
                {
                    "worker_id": candidate.worker_id,
                    "capability_sha256": candidate.capability_sha256,
                }
                for candidate in self.candidates
            ],
            "eligible_candidates": list(self.eligible),
            "rejected_candidates": list(self.rejected),
            "selected_worker": {
                "worker_id": self.selected.worker_id,
                "capability_sha256": self.selected.capability_sha256,
                "resource_id": self.selected.worker_resource_id,
                "selection_key": _selection_key(self.selected),
            },
            "resource_lease_token": dict(resource_lease_token),
            "policy_hash": self.requirement["policy_hash"],
            "data_boundary_hash": self.requirement["data_boundary_hash"],
            "proof_boundary_hash": canonical_sha256(proof_boundary),
            "proof_boundary": proof_boundary,
        }


def normalize_worker_capabilities(
    registry: WorkerRegistry | Iterable[WorkerCapability | Mapping[str, Any]] | None,
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    attempt_id: str,
    attempt: int,
) -> tuple[WorkerCapability, ...]:
    if registry is None:
        requirement = compile_worker_requirement(
            plan=plan,
            node=node,
            run_id=run_id,
            attempt_id=attempt_id,
            attempt=attempt,
        )
        return (compatibility_worker_capability(requirement, node=node),)
    source = registry.capabilities() if hasattr(registry, "capabilities") else registry
    return tuple(_coerce_capability(item) for item in source)


def compile_worker_requirement(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    attempt_id: str,
    attempt: int,
) -> dict[str, Any]:
    runtime = _mapping(node.runtime_requirement.to_value())
    adapter = _mapping(node.adapter_config.to_value())
    extensions = _mapping(node.source_extensions.to_value())
    override = {
        **_mapping(adapter.get("worker_requirement")),
        **_mapping(runtime.get("worker_requirement")),
        **_mapping(extensions.get("worker_requirement")),
    }
    runtime_kind = (
        _string(override.get("runtime_kind")) or _string(runtime.get("backend")) or "none"
    )
    interaction_mode = (
        _string(override.get("interaction_mode"))
        or _string(runtime.get("interaction_mode"))
        or "none"
    )
    required_capabilities = set(_string_list(runtime.get("required_capabilities")))
    required_capabilities.update(_string_list(override.get("required_capabilities")))
    for capability in node.requested_capabilities:
        value = capability.to_value()
        if isinstance(value, str):
            required_capabilities.add(value)
        elif isinstance(value, Mapping):
            name = _string(value.get("name")) or _string(value.get("capability"))
            if name:
                required_capabilities.add(name)
    if interaction_mode != "none":
        required_capabilities.add(interaction_mode)
    trust_zone = (
        _string(override.get("trust_zone")) or _string(runtime.get("trust_zone")) or "local"
    )
    data_classes = (
        _string_list(override.get("data_classes"))
        or _string_list(runtime.get("data_classes"))
        or _string_list(extensions.get("data_classes"))
        or ("public",)
    )
    worktree_id = (
        _string(override.get("worktree_id"))
        or _string(runtime.get("worktree_id"))
        or _string(extensions.get("worktree_id"))
    )
    requirement = {
        "schema": WORKER_REQUIREMENT_SCHEMA,
        "run_id": run_id,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "attempt": attempt,
        "plan_sha256": plan.plan_sha256,
        "goal_hash": plan.runtime_goal_hash,
        "runtime_kind": runtime_kind,
        "provider": _string(override.get("provider")) or _string(runtime.get("provider")),
        "model": _string(override.get("model")) or _string(runtime.get("model")),
        "adapter_kind": _string(override.get("adapter_kind")) or node.adapter_kind,
        "interaction_mode": interaction_mode,
        "required_capabilities": sorted(required_capabilities),
        "session_scope": (
            _string(override.get("session_scope"))
            or _string(runtime.get("session_scope"))
            or "node_attempt"
        ),
        "observation_requirements": sorted(_string_list(runtime.get("observation_requirements"))),
        "trust_zone": trust_zone,
        "data_classes": sorted(data_classes),
        "worktree_id": worktree_id,
        "policy_hash": canonical_sha256(
            {
                "fail_closed_on": list(plan.fail_closed_on),
                "node_required_evidence": list(node.required_evidence),
                "runtime_requirement": runtime,
            }
        ),
        "data_boundary_hash": canonical_sha256(
            {
                "source_bindings": [item.to_value() for item in node.source_bindings],
                "source_extensions": extensions,
                "trust_zone": trust_zone,
                "data_classes": sorted(data_classes),
                "worktree_id": worktree_id,
            }
        ),
    }
    return requirement


def select_worker(
    *,
    requirement: Mapping[str, Any],
    candidates: Iterable[WorkerCapability],
) -> WorkerAssignment:
    candidate_tuple = tuple(
        sorted(candidates, key=lambda item: (item.worker_id, item.capability_sha256))
    )
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidate_tuple:
        codes = _rejection_codes(requirement, candidate)
        item = {
            "worker_id": candidate.worker_id,
            "capability_sha256": candidate.capability_sha256,
        }
        if codes:
            rejected.append({**item, "rejection_codes": codes})
        else:
            eligible.append({**item, "selection_key": _selection_key(candidate)})
    if not eligible:
        raise WorkerAssignmentError(
            "worker_no_eligible_candidate",
            ",".join(
                f"{item['worker_id']}:{'|'.join(item['rejection_codes'])}" for item in rejected
            ),
        )
    by_id = {candidate.worker_id: candidate for candidate in candidate_tuple}
    selected_id = sorted(
        (item["worker_id"] for item in eligible),
        key=lambda worker_id: _selection_key(by_id[worker_id]),
    )[0]
    requirement_payload = dict(requirement)
    return WorkerAssignment(
        requirement=requirement_payload,
        requirement_sha256=canonical_sha256(requirement_payload),
        candidates=candidate_tuple,
        eligible=tuple(eligible),
        rejected=tuple(rejected),
        selected=by_id[selected_id],
    )


def compatibility_worker_capability(
    requirement: Mapping[str, Any],
    *,
    node: DagPlanNode,
) -> WorkerCapability:
    runtime_kind = _required_mapping_string(requirement, "runtime_kind")
    provider = _optional_string(requirement.get("provider"))
    model = _optional_string(requirement.get("model"))
    worktree_id = _optional_string(requirement.get("worktree_id"))
    return WorkerCapability(
        worker_id=f"compat:{runtime_kind}:{node.adapter_kind}",
        runtime_kind=runtime_kind,
        provider=provider,
        model=model,
        adapter_kind=node.adapter_kind,
        interaction_modes=(_required_mapping_string(requirement, "interaction_mode"),),
        supported_capabilities=tuple(_string_list(requirement.get("required_capabilities"))),
        session_scopes=(_required_mapping_string(requirement, "session_scope"),),
        trust_zones=(_required_mapping_string(requirement, "trust_zone"),),
        data_classes=tuple(_string_list(requirement.get("data_classes"))),
        worktree_ids=(worktree_id,) if worktree_id else (),
        supports_worktree_binding=worktree_id is not None,
        resource_id=f"worker:compat:{runtime_kind}:{node.adapter_kind}:{node.node_id}",
        priority=1000,
    )


def worker_resource_requirement(assignment: WorkerAssignment) -> dict[str, str]:
    return {
        "resource_kind": "worker",
        "resource_id": assignment.selected.worker_resource_id,
        "mode": "exclusive",
        "mutation_key": f"worker:{assignment.selected.worker_resource_id}",
    }


def _rejection_codes(requirement: Mapping[str, Any], candidate: WorkerCapability) -> list[str]:
    codes: list[str] = []
    if candidate.runtime_kind != requirement.get("runtime_kind"):
        codes.append("WORKER_RUNTIME_KIND_MISMATCH")
    provider = _optional_string(requirement.get("provider"))
    if provider is not None and candidate.provider != provider:
        codes.append("WORKER_PROVIDER_MISMATCH")
    model = _optional_string(requirement.get("model"))
    if model is not None and candidate.model != model:
        codes.append("WORKER_MODEL_MISMATCH")
    adapter_kind = _optional_string(requirement.get("adapter_kind"))
    if candidate.adapter_kind is not None and adapter_kind != candidate.adapter_kind:
        codes.append("WORKER_ADAPTER_KIND_MISMATCH")
    interaction_mode = _required_mapping_string(requirement, "interaction_mode")
    if interaction_mode not in candidate.interaction_modes:
        codes.append("WORKER_INTERACTION_MODE_MISSING")
    missing_capabilities = sorted(
        set(_string_list(requirement.get("required_capabilities")))
        - set(candidate.supported_capabilities)
    )
    if missing_capabilities:
        codes.append("WORKER_CAPABILITY_MISSING")
    if _required_mapping_string(requirement, "session_scope") not in candidate.session_scopes:
        codes.append("WORKER_SESSION_SCOPE_MISMATCH")
    if _required_mapping_string(requirement, "trust_zone") not in candidate.trust_zones:
        codes.append("WORKER_TRUST_ZONE_MISMATCH")
    if not set(_string_list(requirement.get("data_classes"))).issubset(candidate.data_classes):
        codes.append("WORKER_DATA_CLASS_DENIED")
    worktree_id = _optional_string(requirement.get("worktree_id"))
    if worktree_id is not None and (
        not candidate.supports_worktree_binding or worktree_id not in candidate.worktree_ids
    ):
        codes.append("WORKER_WORKTREE_MISMATCH")
    if candidate.health != "HEALTHY":
        codes.append("WORKER_UNHEALTHY")
    if candidate.readiness != "READY":
        codes.append("WORKER_NOT_READY")
    if candidate.active_slots >= candidate.concurrency_slots:
        codes.append("WORKER_SLOTS_EXHAUSTED")
    return codes


def _selection_key(candidate: WorkerCapability) -> list[str | int]:
    return [candidate.priority, candidate.worker_id, candidate.capability_sha256]


def _coerce_capability(item: WorkerCapability | Mapping[str, Any]) -> WorkerCapability:
    if isinstance(item, WorkerCapability):
        return item
    return WorkerCapability.from_payload(item)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_string(value: object) -> str | None:
    return _string(value)


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _string(payload.get(key))
    if value is None:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_mapping_string(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_string(payload.get(key))
    if value is None:
        raise WorkerAssignmentError("worker_requirement_invalid", key)
    return value


def _string_tuple(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None and default is not None:
        return default
    result = _string_list(value)
    if not result and default is None:
        raise ValueError(f"{key} must contain at least one string")
    return result


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return _sorted_unique(str(item) for item in value if isinstance(item, str) and item)


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(values)))


def _bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise ValueError("worker boolean fields must be bool")
    return value


def _int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if type(value) is not int:
        raise ValueError("worker integer fields must be int")
    return value
