"""Typed, backend-neutral contracts for Tau runtime endpoints."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal, cast

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256

RUNTIME_BACKEND_CAPABILITIES_SCHEMA = "tau.runtime_backend_capabilities.v1"
RUNTIME_ENDPOINT_LEASE_SCHEMA = "tau.runtime_endpoint_lease.v1"
RUNTIME_SUBMIT_RECEIPT_SCHEMA = "tau.runtime_submit_receipt.v1"
RUNTIME_EVENT_SCHEMA = "tau.runtime_event.v1"
RUNTIME_STATE_PROJECTION_SCHEMA = "tau.runtime_state_projection.v1"
RUNTIME_RECONCILIATION_RECEIPT_SCHEMA = "tau.runtime_reconciliation_receipt.v1"
LEGACY_GIT_WORKTREE_LEASE_SCHEMA = "tau.git_worktree_lease.v1"
GIT_WORKTREE_LEASE_SCHEMA = "tau.git_worktree_lease.v2"
RUNTIME_CAPABILITY_DECISION_SCHEMA = "tau.runtime_capability_decision.v1"
RUNTIME_REQUIREMENT_SCHEMA = "tau.runtime_requirement.v1"

RuntimeState = Literal[
    "STARTING",
    "READY",
    "RUNNING",
    "WAITING_ON_INPUT",
    "WAITING_ON_APPROVAL",
    "AUTH_REQUIRED",
    "INTERSTITIAL",
    "BLOCKED",
    "EXITED",
    "CRASHED",
    "UNKNOWN",
]
RuntimeLiveness = Literal["ALIVE", "DEAD", "UNKNOWN"]
ObservationConfidence = Literal["NATIVE", "PROCESS", "HEURISTIC", "UNKNOWN"]

BOOLEAN_RUNTIME_CAPABILITIES = (
    "interactive",
    "one_shot",
    "native_events",
    "native_agent_state",
    "foreground_process_state",
    "structured_composer_state",
    "stable_endpoint_id",
    "human_attach",
    "supports_working_directory",
    "supports_owned_inventory",
    "supports_terminate",
)
OBSERVATION_CONFIDENCE_LEVELS = frozenset({"NATIVE", "PROCESS", "HEURISTIC", "UNKNOWN"})


def _validate_observation_confidence(values: tuple[str, ...], label: str) -> None:
    invalid = sorted(set(values) - OBSERVATION_CONFIDENCE_LEVELS)
    if invalid:
        raise ValueError(f"{label} contains invalid values: {', '.join(invalid)}")


@dataclass(frozen=True, slots=True)
class RuntimeRequirement:
    backend: str
    interaction_mode: Literal["none", "one_shot", "interactive"]
    required_capabilities: tuple[str, ...]
    session_scope: str
    observation_requirements: tuple[ObservationConfidence, ...]

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise ValueError("runtime backend must not be empty")
        if self.interaction_mode not in {"none", "one_shot", "interactive"}:
            raise ValueError("runtime interaction_mode is invalid")
        unknown = sorted(set(self.required_capabilities) - set(BOOLEAN_RUNTIME_CAPABILITIES))
        if unknown:
            raise ValueError(f"unknown runtime capabilities: {', '.join(unknown)}")
        if not self.session_scope.strip():
            raise ValueError("runtime session_scope must not be empty")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("runtime required_capabilities must be unique")
        object.__setattr__(self, "required_capabilities", tuple(sorted(self.required_capabilities)))
        if self.interaction_mode != "none" and self.interaction_mode not in (
            self.required_capabilities
        ):
            raise ValueError("runtime interaction_mode must be included in required_capabilities")
        if self.interaction_mode == "none" and self.required_capabilities:
            raise ValueError("non-runtime nodes must not request runtime capabilities")
        if self.interaction_mode == "none" and self.backend != "none":
            raise ValueError("non-runtime requirement must use backend none")
        if self.backend == "none" and self.interaction_mode != "none":
            raise ValueError("backend none cannot request runtime interaction")
        if self.interaction_mode == "none" and self.observation_requirements:
            raise ValueError("non-runtime nodes must not request runtime observations")
        if len(self.observation_requirements) != len(set(self.observation_requirements)):
            raise ValueError("observation_requirements must be unique")
        object.__setattr__(
            self, "observation_requirements", tuple(sorted(self.observation_requirements))
        )
        _validate_observation_confidence(
            self.observation_requirements, "observation_requirements"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_REQUIREMENT_SCHEMA,
            "backend": self.backend,
            "interaction_mode": self.interaction_mode,
            "required_capabilities": list(self.required_capabilities),
            "session_scope": self.session_scope,
            "observation_requirements": list(self.observation_requirements),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeRequirement:
        _require_schema(payload, RUNTIME_REQUIREMENT_SCHEMA, cls)
        return cls(
            backend=_required_string(payload, "backend"),
            interaction_mode=_interaction_mode(payload.get("interaction_mode")),
            required_capabilities=_string_tuple(payload, "required_capabilities"),
            session_scope=_required_string(payload, "session_scope"),
            observation_requirements=_observation_tuple(payload),
        )


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    backend: str
    version: str
    interactive: bool
    one_shot: bool
    native_events: bool
    native_agent_state: bool
    foreground_process_state: bool
    structured_composer_state: bool
    stable_endpoint_id: bool
    human_attach: bool
    supports_working_directory: bool
    supports_owned_inventory: bool
    supports_terminate: bool
    observation_confidence_levels: tuple[ObservationConfidence, ...]
    supported_session_scopes: tuple[str, ...]
    unsupported_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.backend.strip() or not self.version.strip():
            raise ValueError("runtime backend and version must not be empty")
        invalid_booleans = [
            name
            for name in BOOLEAN_RUNTIME_CAPABILITIES
            if type(getattr(self, name)) is not bool
        ]
        if invalid_booleans:
            raise ValueError(
                "runtime capability values must be booleans: "
                + ", ".join(invalid_booleans)
            )
        if len(self.observation_confidence_levels) != len(set(self.observation_confidence_levels)):
            raise ValueError("observation_confidence_levels must be unique")
        if any(not isinstance(value, str) for value in self.unsupported_requirements):
            raise ValueError("unsupported_requirements must contain strings")
        if len(self.unsupported_requirements) != len(set(self.unsupported_requirements)):
            raise ValueError("unsupported_requirements must be unique")
        if not self.supported_session_scopes or any(
            not isinstance(value, str) or not value.strip()
            for value in self.supported_session_scopes
        ):
            raise ValueError("supported_session_scopes must contain non-empty values")
        if len(self.supported_session_scopes) != len(set(self.supported_session_scopes)):
            raise ValueError("supported_session_scopes must be unique")
        object.__setattr__(
            self,
            "observation_confidence_levels",
            tuple(sorted(self.observation_confidence_levels)),
        )
        object.__setattr__(
            self, "unsupported_requirements", tuple(sorted(self.unsupported_requirements))
        )
        object.__setattr__(
            self, "supported_session_scopes", tuple(sorted(self.supported_session_scopes))
        )
        _validate_observation_confidence(
            self.observation_confidence_levels, "observation_confidence_levels"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_BACKEND_CAPABILITIES_SCHEMA,
            "backend": self.backend,
            "version": self.version,
            **{name: getattr(self, name) for name in BOOLEAN_RUNTIME_CAPABILITIES},
            "observation_confidence_levels": list(self.observation_confidence_levels),
            "supported_session_scopes": list(self.supported_session_scopes),
            "unsupported_requirements": list(self.unsupported_requirements),
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeCapabilities:
        _require_schema(payload, RUNTIME_BACKEND_CAPABILITIES_SCHEMA, cls)
        return cls(
            backend=_required_string(payload, "backend"),
            version=_required_string(payload, "version"),
            **{name: _required_bool(payload, name) for name in BOOLEAN_RUNTIME_CAPABILITIES},
            observation_confidence_levels=_observation_tuple(payload),
            supported_session_scopes=_string_tuple(payload, "supported_session_scopes"),
            unsupported_requirements=_string_tuple(payload, "unsupported_requirements"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeEndpointLease:
    run_id: str
    plan_revision: str
    dag_id: str
    node_id: str
    attempt_id: str
    attempt_number: int
    execution_token: str
    backend: str
    backend_session_id: str | None
    scope_id: str
    endpoint_id: str
    work_order_sha256: str
    goal_hash: str
    owner: str
    created_at: str
    expires_at: str
    heartbeat_policy: FrozenJson
    cleanup_policy: FrozenJson
    capabilities_sha256: str
    backend_ids: FrozenJson

    def __post_init__(self) -> None:
        _require_nonempty_attributes(
            self,
            (
                "run_id", "plan_revision", "dag_id", "node_id", "attempt_id",
                "execution_token", "backend", "scope_id", "endpoint_id", "owner",
                "created_at", "expires_at",
            ),
        )
        if self.backend_session_id is not None and (
            not isinstance(self.backend_session_id, str) or not self.backend_session_id
        ):
            raise ValueError("backend_session_id must be a non-empty string or null")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        _require_sha256(self.work_order_sha256, "work_order_sha256")
        _require_sha256(self.goal_hash, "goal_hash")
        _require_sha256(self.capabilities_sha256, "capabilities_sha256")
        _require_frozen_object(self.heartbeat_policy, "heartbeat_policy")
        _require_frozen_object(self.cleanup_policy, "cleanup_policy")
        _require_frozen_object(self.backend_ids, "backend_ids")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": RUNTIME_ENDPOINT_LEASE_SCHEMA, **_dataclass_payload(self)}

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeEndpointLease:
        _require_schema(payload, RUNTIME_ENDPOINT_LEASE_SCHEMA, cls)
        return cls(
            run_id=_required_string(payload, "run_id"),
            plan_revision=_required_string(payload, "plan_revision"),
            dag_id=_required_string(payload, "dag_id"),
            node_id=_required_string(payload, "node_id"),
            attempt_id=_required_string(payload, "attempt_id"),
            attempt_number=_required_int(payload, "attempt_number", minimum=1),
            execution_token=_required_string(payload, "execution_token"),
            backend=_required_string(payload, "backend"),
            backend_session_id=_nullable_string(payload, "backend_session_id"),
            scope_id=_required_string(payload, "scope_id"),
            endpoint_id=_required_string(payload, "endpoint_id"),
            work_order_sha256=_required_string(payload, "work_order_sha256"),
            goal_hash=_required_string(payload, "goal_hash"),
            owner=_required_string(payload, "owner"),
            created_at=_required_string(payload, "created_at"),
            expires_at=_required_string(payload, "expires_at"),
            heartbeat_policy=_frozen_object(payload, "heartbeat_policy"),
            cleanup_policy=_frozen_object(payload, "cleanup_policy"),
            capabilities_sha256=_required_string(payload, "capabilities_sha256"),
            backend_ids=_frozen_object(payload, "backend_ids"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeSubmitReceipt:
    endpoint_lease_sha256: str
    work_order_sha256: str
    composer_state_before: str
    text_delivery_count: int
    submit_attempt_count: int
    composer_state_after: str
    delivery_status: str
    backend_acknowledgement: FrozenJson
    provider_execution_status: str
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string_entries(self.errors, "errors")
        _require_nonempty_attributes(
            self,
            (
                "composer_state_before",
                "composer_state_after",
                "delivery_status",
                "provider_execution_status",
            ),
        )
        if (
            type(self.text_delivery_count) is not int
            or type(self.submit_attempt_count) is not int
            or self.text_delivery_count < 0
            or self.submit_attempt_count < 0
        ):
            raise ValueError("runtime submit counts must be non-negative")
        _require_sha256(self.endpoint_lease_sha256, "endpoint_lease_sha256")
        _require_sha256(self.work_order_sha256, "work_order_sha256")
        _require_frozen_object(self.backend_acknowledgement, "backend_acknowledgement")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": RUNTIME_SUBMIT_RECEIPT_SCHEMA, **_dataclass_payload(self)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeSubmitReceipt:
        _require_schema(payload, RUNTIME_SUBMIT_RECEIPT_SCHEMA, cls)
        return cls(
            endpoint_lease_sha256=_required_string(payload, "endpoint_lease_sha256"),
            work_order_sha256=_required_string(payload, "work_order_sha256"),
            composer_state_before=_required_string(payload, "composer_state_before"),
            text_delivery_count=_required_int(payload, "text_delivery_count", minimum=0),
            submit_attempt_count=_required_int(payload, "submit_attempt_count", minimum=0),
            composer_state_after=_required_string(payload, "composer_state_after"),
            delivery_status=_required_string(payload, "delivery_status"),
            backend_acknowledgement=_frozen_object(payload, "backend_acknowledgement"),
            provider_execution_status=_required_string(payload, "provider_execution_status"),
            errors=_string_tuple(payload, "errors"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    run_id: str
    endpoint_lease_sha256: str
    event_type: str
    observed_at: str
    state: RuntimeState
    liveness: RuntimeLiveness
    confidence: ObservationConfidence
    source: str
    observation: FrozenJson

    def __post_init__(self) -> None:
        _require_nonempty_attributes(
            self, ("event_id", "run_id", "event_type", "observed_at", "source")
        )
        _require_sha256(self.endpoint_lease_sha256, "endpoint_lease_sha256")
        _validate_runtime_state(self.state)
        _validate_runtime_liveness(self.liveness)
        _validate_observation_confidence((self.confidence,), "confidence")
        _require_frozen_object(self.observation, "observation")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": RUNTIME_EVENT_SCHEMA, **_dataclass_payload(self)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeEvent:
        _require_schema(payload, RUNTIME_EVENT_SCHEMA, cls)
        return cls(
            event_id=_required_string(payload, "event_id"),
            run_id=_required_string(payload, "run_id"),
            endpoint_lease_sha256=_required_string(payload, "endpoint_lease_sha256"),
            event_type=_required_string(payload, "event_type"),
            observed_at=_required_string(payload, "observed_at"),
            state=cast(RuntimeState, _required_string(payload, "state")),
            liveness=cast(RuntimeLiveness, _required_string(payload, "liveness")),
            confidence=cast(ObservationConfidence, _required_string(payload, "confidence")),
            source=_required_string(payload, "source"),
            observation=_frozen_object(payload, "observation"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeStateProjection:
    run_id: str
    endpoint_lease_sha256: str
    state: RuntimeState
    liveness: RuntimeLiveness
    confidence: ObservationConfidence
    last_event_id: str
    event_count: int

    def __post_init__(self) -> None:
        _require_nonempty_attributes(self, ("run_id", "last_event_id"))
        _require_sha256(self.endpoint_lease_sha256, "endpoint_lease_sha256")
        if type(self.event_count) is not int or self.event_count < 0:
            raise ValueError("event_count must be non-negative")
        _validate_runtime_state(self.state)
        _validate_runtime_liveness(self.liveness)
        _validate_observation_confidence((self.confidence,), "confidence")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": RUNTIME_STATE_PROJECTION_SCHEMA, **_dataclass_payload(self)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeStateProjection:
        _require_schema(payload, RUNTIME_STATE_PROJECTION_SCHEMA, cls)
        return cls(
            run_id=_required_string(payload, "run_id"),
            endpoint_lease_sha256=_required_string(payload, "endpoint_lease_sha256"),
            state=cast(RuntimeState, _required_string(payload, "state")),
            liveness=cast(RuntimeLiveness, _required_string(payload, "liveness")),
            confidence=cast(ObservationConfidence, _required_string(payload, "confidence")),
            last_event_id=_required_string(payload, "last_event_id"),
            event_count=_required_int(payload, "event_count", minimum=0),
        )


@dataclass(frozen=True, slots=True)
class RuntimeReconciliationReceipt:
    run_id: str
    endpoint_lease_sha256: str
    status: Literal["PASS", "BLOCKED"]
    action: str
    evidence: FrozenJson
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string_entries(self.errors, "errors")
        _require_nonempty_attributes(self, ("run_id", "action"))
        _require_sha256(self.endpoint_lease_sha256, "endpoint_lease_sha256")
        if self.status not in {"PASS", "BLOCKED"}:
            raise ValueError("runtime reconciliation status is invalid")
        if self.status == "PASS" and self.errors:
            raise ValueError("passing reconciliation receipt must not contain errors")
        if self.status == "BLOCKED" and not self.errors:
            raise ValueError("blocked reconciliation receipt requires errors")
        _require_frozen_object(self.evidence, "evidence")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": RUNTIME_RECONCILIATION_RECEIPT_SCHEMA, **_dataclass_payload(self)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeReconciliationReceipt:
        _require_schema(payload, RUNTIME_RECONCILIATION_RECEIPT_SCHEMA, cls)
        return cls(
            run_id=_required_string(payload, "run_id"),
            endpoint_lease_sha256=_required_string(payload, "endpoint_lease_sha256"),
            status=cast(Literal["PASS", "BLOCKED"], _required_string(payload, "status")),
            action=_required_string(payload, "action"),
            evidence=_frozen_object(payload, "evidence"),
            errors=_string_tuple(payload, "errors"),
        )


@dataclass(frozen=True, slots=True)
class GitWorktreeLease:
    run_id: str
    plan_revision: str
    node_id: str
    attempt_id: str
    repository: str
    worktree_path: str
    base_commit: str
    head_commit: str
    branch: str | None
    detached: bool
    allowed_paths: tuple[str, ...]
    owner: str
    created_at: str
    expires_at: str
    pre_status_sha256: str
    cleanup_policy: FrozenJson

    def __post_init__(self) -> None:
        _require_nonempty_attributes(
            self,
            (
                "run_id",
                "plan_revision",
                "node_id",
                "attempt_id",
                "repository",
                "worktree_path",
                "base_commit",
                "head_commit",
                "owner",
                "created_at",
                "expires_at",
            ),
        )
        if len(self.allowed_paths) != len(set(self.allowed_paths)):
            raise ValueError("allowed_paths must be unique")
        if any(not isinstance(path, str) or not path for path in self.allowed_paths):
            raise ValueError("allowed_paths must contain non-empty strings")
        if self.branch is not None and (not isinstance(self.branch, str) or not self.branch):
            raise ValueError("branch must be a non-empty string or null")
        if type(self.detached) is not bool:
            raise ValueError("detached must be a boolean")
        if self.detached == (self.branch is not None):
            raise ValueError("detached and branch identity are inconsistent")
        _require_sha256(self.pre_status_sha256, "pre_status_sha256")
        _require_frozen_object(self.cleanup_policy, "cleanup_policy")

    def to_payload(self) -> dict[str, Any]:
        return {"schema": GIT_WORKTREE_LEASE_SCHEMA, **_dataclass_payload(self)}

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GitWorktreeLease:
        schema = payload.get("schema")
        if schema not in {LEGACY_GIT_WORKTREE_LEASE_SCHEMA, GIT_WORKTREE_LEASE_SCHEMA}:
            raise ValueError(f"schema must be {GIT_WORKTREE_LEASE_SCHEMA}")
        _require_schema(payload, cast(str, schema), cls)
        legacy = schema == LEGACY_GIT_WORKTREE_LEASE_SCHEMA
        base_commit = _required_string(payload, "base_commit")
        return cls(
            run_id=_required_string(payload, "run_id"),
            plan_revision=_required_string(payload, "plan_revision"),
            node_id=_required_string(payload, "node_id"),
            attempt_id=_required_string(payload, "attempt_id"),
            repository=_required_string(payload, "repository"),
            worktree_path=_required_string(payload, "worktree_path"),
            base_commit=base_commit,
            head_commit=base_commit if legacy else _required_string(payload, "head_commit"),
            branch=None if legacy else _nullable_string(payload, "branch"),
            detached=True if legacy else _required_bool(payload, "detached"),
            allowed_paths=_string_tuple(payload, "allowed_paths"),
            owner=_required_string(payload, "owner"),
            created_at=_required_string(payload, "created_at"),
            expires_at=_required_string(payload, "expires_at"),
            pre_status_sha256=(
                canonical_sha256("")
                if legacy
                else _required_string(payload, "pre_status_sha256")
            ),
            cleanup_policy=_frozen_object(payload, "cleanup_policy"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityDecision:
    status: Literal["PASS", "BLOCKED"]
    backend: str
    requirement_sha256: str
    capabilities_sha256: str | None
    missing_capabilities: tuple[str, ...]
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string_entries(self.missing_capabilities, "missing_capabilities")
        _require_string_entries(self.errors, "errors")
        _require_nonempty_attributes(self, ("backend",))
        if self.status not in {"PASS", "BLOCKED"}:
            raise ValueError("runtime capability decision status is invalid")
        if self.status == "PASS" and (self.missing_capabilities or self.errors):
            raise ValueError("passing capability decision must not contain failures")
        if self.status == "PASS" and self.backend != "none" and not self.capabilities_sha256:
            raise ValueError("passing runtime backend decision requires capabilities_sha256")
        if self.status == "BLOCKED" and not self.errors:
            raise ValueError("blocked capability decision requires errors")
        _require_sha256(self.requirement_sha256, "requirement_sha256")
        if self.capabilities_sha256 is not None:
            _require_sha256(self.capabilities_sha256, "capabilities_sha256")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_CAPABILITY_DECISION_SCHEMA,
            "ok": self.status == "PASS",
            **_dataclass_payload(self),
            "proof_scope": {
                "proves": [
                    "Tau compared a compiled runtime requirement with registered "
                    "backend capabilities."
                ],
                "does_not_prove": [
                    "The backend enforces every declared capability.",
                    "The backend is secure or available at execution time.",
                    "A node executed or produced a truthful result.",
                ],
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeCapabilityDecision:
        _require_schema(
            payload,
            RUNTIME_CAPABILITY_DECISION_SCHEMA,
            cls,
            extras={"ok", "proof_scope"},
        )
        status = cast(Literal["PASS", "BLOCKED"], _required_string(payload, "status"))
        if status not in {"PASS", "BLOCKED"}:
            raise ValueError("runtime capability decision status is invalid")
        if _required_bool(payload, "ok") is not (status == "PASS"):
            raise ValueError("runtime capability decision ok/status mismatch")
        _frozen_object(payload, "proof_scope")
        capabilities_sha256 = payload.get("capabilities_sha256")
        if capabilities_sha256 is not None and not isinstance(capabilities_sha256, str):
            raise ValueError("capabilities_sha256 must be a string or null")
        return cls(
            status=status,
            backend=_required_string(payload, "backend"),
            requirement_sha256=_required_string(payload, "requirement_sha256"),
            capabilities_sha256=capabilities_sha256,
            missing_capabilities=_string_tuple(payload, "missing_capabilities"),
            errors=_string_tuple(payload, "errors"),
        )


def _dataclass_payload(value: object) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(cast(Any, value)):
        item = getattr(value, field.name)
        if isinstance(item, FrozenJson):
            payload[field.name] = item.to_value()
        elif isinstance(item, tuple):
            payload[field.name] = list(item)
        else:
            payload[field.name] = item
    return payload


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_schema(
    payload: dict[str, Any],
    schema: str,
    model: type[Any],
    *,
    extras: set[str] | None = None,
) -> None:
    if payload.get("schema") != schema:
        raise ValueError(f"schema must be {schema}")
    allowed = {
        "schema",
        *(field.name for field in fields(cast(Any, model))),
        *(extras or set()),
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected contract properties: {', '.join(unexpected)}")


def _required_int(payload: dict[str, Any], key: str, *, minimum: int) -> int:
    value = payload.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{key} must be an integer >= {minimum}")
    return value


def _nullable_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _frozen_object(payload: dict[str, Any], key: str) -> FrozenJson:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return FrozenJson.from_value(value)


def _require_frozen_object(value: FrozenJson, label: str) -> None:
    if not isinstance(value.to_value(), dict):
        raise ValueError(f"{label} must be an object")


def _require_nonempty_attributes(value: object, names: tuple[str, ...]) -> None:
    invalid = [
        name
        for name in names
        if not isinstance(getattr(value, name), str) or not getattr(value, name)
    ]
    if invalid:
        raise ValueError(
            "required runtime identity fields must be non-empty: " + ", ".join(invalid)
        )


def _validate_runtime_state(value: str) -> None:
    if value not in {
        "STARTING", "READY", "RUNNING", "WAITING_ON_INPUT",
        "WAITING_ON_APPROVAL", "AUTH_REQUIRED", "INTERSTITIAL",
        "BLOCKED", "EXITED", "CRASHED", "UNKNOWN",
    }:
        raise ValueError("runtime state is invalid")


def _validate_runtime_liveness(value: str) -> None:
    if value not in {"ALIVE", "DEAD", "UNKNOWN"}:
        raise ValueError("runtime liveness is invalid")


def _require_sha256(value: str, label: str) -> None:
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"{label} must be a complete lowercase SHA-256 digest")


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string array")
    return tuple(value)


def _require_string_entries(values: tuple[object, ...], label: str) -> None:
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"{label} must contain strings")


def _observation_tuple(payload: dict[str, Any]) -> tuple[ObservationConfidence, ...]:
    values = (
        _string_tuple(payload, "observation_confidence_levels")
        if ("observation_confidence_levels" in payload)
        else _string_tuple(payload, "observation_requirements")
    )
    allowed = {"NATIVE", "PROCESS", "HEURISTIC", "UNKNOWN"}
    if any(value not in allowed for value in values):
        raise ValueError("runtime observation confidence is invalid")
    return cast(tuple[ObservationConfidence, ...], values)


def _interaction_mode(value: object) -> Literal["none", "one_shot", "interactive"]:
    if value not in {"none", "one_shot", "interactive"}:
        raise ValueError("runtime interaction_mode is invalid")
    return cast(Literal["none", "one_shot", "interactive"], value)
