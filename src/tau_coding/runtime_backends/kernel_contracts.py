"""Attempt-scoped Python workspace contracts for the Jupyter kernel backend."""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease
from tau_coding.schema_registry import require_schema_compatible

PYTHON_WORKSPACE_REQUEST_SCHEMA = "tau.python_workspace_request.v1"
PYTHON_WORKSPACE_RECEIPT_SCHEMA = "tau.python_workspace_receipt.v1"
PYTHON_EXECUTION_REQUEST_SCHEMA = "tau.python_execution_request.v1"
PYTHON_EXECUTION_RECEIPT_SCHEMA = "tau.python_execution_receipt.v1"
PYTHON_KERNEL_CONTROL_RECEIPT_SCHEMA = "tau.python_kernel_control_receipt.v1"
PYTHON_PACKAGE_MANIFEST_SCHEMA = "tau.python_package_manifest.v1"

PYTHON_KERNEL_BACKEND = "python-kernel"
PYTHON_KERNEL_VERSION = "1"
PYTHON_KERNEL_FEATURES = frozenset(
    {
        "attempt_scoped_namespace",
        "serialized_execution",
        "jupyter_request_correlation",
        "structured_output_artifacts",
        "interrupt",
        "quarantine",
        "bounded_startup",
        "environment_identity",
        "restart_safe_process_ownership",
        "kernel_success_is_not_tau_acceptance",
    }
)

PythonWorkspaceStatus = Literal["READY", "UNAVAILABLE", "QUARANTINED"]
PythonExecutionStatus = Literal["OK", "ERROR", "INTERRUPTED", "BLOCKED"]


class PythonKernelContractError(ValueError):
    """Raised when a Python workspace request violates the Tau kernel contract."""


@dataclass(frozen=True, slots=True)
class PythonPackageManifest:
    python_executable: str
    python_version: str
    platform: str
    packages: FrozenJson
    security_profile: FrozenJson
    available: bool
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.python_executable or not self.python_version or not self.platform:
            raise PythonKernelContractError("python package manifest identity is incomplete")
        if any(not isinstance(error, str) or not error for error in self.errors):
            raise PythonKernelContractError("python package manifest errors must be strings")
        if self.available and self.errors:
            raise PythonKernelContractError("available package manifest must not contain errors")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_PACKAGE_MANIFEST_SCHEMA,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "platform": self.platform,
            "packages": self.packages.to_value(),
            "security_profile": self.security_profile.to_value(),
            "available": self.available,
            "errors": list(self.errors),
            "sha256": self.sha256,
        }

    @property
    def sha256(self) -> str:
        payload = {
            "schema": PYTHON_PACKAGE_MANIFEST_SCHEMA,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "platform": self.platform,
            "packages": self.packages.to_value(),
            "security_profile": self.security_profile.to_value(),
            "available": self.available,
            "errors": list(self.errors),
        }
        return canonical_sha256(payload)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PythonPackageManifest:
        _require_schema(payload, PYTHON_PACKAGE_MANIFEST_SCHEMA, {"sha256"})
        manifest = cls(
            python_executable=_required_string(payload, "python_executable"),
            python_version=_required_string(payload, "python_version"),
            platform=_required_string(payload, "platform"),
            packages=_frozen_object(payload, "packages"),
            security_profile=_frozen_object(payload, "security_profile"),
            available=_required_bool(payload, "available"),
            errors=_string_tuple(payload, "errors"),
        )
        if payload.get("sha256") != manifest.sha256:
            raise PythonKernelContractError("python package manifest sha256 mismatch")
        return manifest


@dataclass(frozen=True, slots=True)
class PythonWorkspaceRequest:
    run_id: str
    plan_revision: str
    dag_id: str
    node_id: str
    attempt_id: str
    attempt_number: int
    worktree: str
    goal_hash: str
    policy_sha256: str
    data_boundary_sha256: str
    required_features: tuple[str, ...]
    startup_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        _require_nonempty(
            {
                "run_id": self.run_id,
                "plan_revision": self.plan_revision,
                "dag_id": self.dag_id,
                "node_id": self.node_id,
                "attempt_id": self.attempt_id,
                "worktree": self.worktree,
                "goal_hash": self.goal_hash,
                "policy_sha256": self.policy_sha256,
                "data_boundary_sha256": self.data_boundary_sha256,
            }
        )
        if self.attempt_number < 1:
            raise PythonKernelContractError("attempt_number must be at least 1")
        if self.startup_timeout_seconds <= 0:
            raise PythonKernelContractError("startup_timeout_seconds must be positive")
        _require_known_features(self.required_features)
        object.__setattr__(self, "required_features", tuple(sorted(set(self.required_features))))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_WORKSPACE_REQUEST_SCHEMA,
            "run_id": self.run_id,
            "plan_revision": self.plan_revision,
            "dag_id": self.dag_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "worktree": self.worktree,
            "goal_hash": self.goal_hash,
            "policy_sha256": self.policy_sha256,
            "data_boundary_sha256": self.data_boundary_sha256,
            "required_features": list(self.required_features),
            "startup_timeout_seconds": self.startup_timeout_seconds,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PythonWorkspaceRequest:
        _require_schema(payload, PYTHON_WORKSPACE_REQUEST_SCHEMA)
        return cls(
            run_id=_required_string(payload, "run_id"),
            plan_revision=_required_string(payload, "plan_revision"),
            dag_id=_required_string(payload, "dag_id"),
            node_id=_required_string(payload, "node_id"),
            attempt_id=_required_string(payload, "attempt_id"),
            attempt_number=_required_int(payload, "attempt_number"),
            worktree=_required_string(payload, "worktree"),
            goal_hash=_required_string(payload, "goal_hash"),
            policy_sha256=_required_string(payload, "policy_sha256"),
            data_boundary_sha256=_required_string(payload, "data_boundary_sha256"),
            required_features=_string_tuple(payload, "required_features"),
            startup_timeout_seconds=_required_number(payload, "startup_timeout_seconds"),
        )


@dataclass(frozen=True, slots=True)
class PythonWorkspaceReceipt:
    workspace_request_sha256: str
    status: PythonWorkspaceStatus
    endpoint_lease: FrozenJson | None
    package_manifest: FrozenJson
    generation_id: str
    process_identity: FrozenJson
    state_path: str
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"READY", "UNAVAILABLE", "QUARANTINED"}:
            raise PythonKernelContractError("python workspace status is invalid")
        if self.status == "READY" and self.endpoint_lease is None:
            raise PythonKernelContractError("ready workspace requires endpoint lease")
        if self.status != "READY" and not self.errors:
            raise PythonKernelContractError("non-ready workspace requires errors")
        _require_nonempty(
            {
                "workspace_request_sha256": self.workspace_request_sha256,
                "generation_id": self.generation_id,
                "state_path": self.state_path,
            }
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_WORKSPACE_RECEIPT_SCHEMA,
            "workspace_request_sha256": self.workspace_request_sha256,
            "status": self.status,
            "endpoint_lease": (
                self.endpoint_lease.to_value() if self.endpoint_lease is not None else None
            ),
            "package_manifest": self.package_manifest.to_value(),
            "generation_id": self.generation_id,
            "process_identity": self.process_identity.to_value(),
            "state_path": self.state_path,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class PythonExecutionRequest:
    workspace_request_sha256: str
    endpoint_lease_sha256: str
    generation_id: str
    execution_id: str
    goal_hash: str
    policy_sha256: str
    data_boundary_sha256: str
    code: str

    def __post_init__(self) -> None:
        _require_nonempty(
            {
                "workspace_request_sha256": self.workspace_request_sha256,
                "endpoint_lease_sha256": self.endpoint_lease_sha256,
                "generation_id": self.generation_id,
                "execution_id": self.execution_id,
                "goal_hash": self.goal_hash,
                "policy_sha256": self.policy_sha256,
                "data_boundary_sha256": self.data_boundary_sha256,
            }
        )
        if not isinstance(self.code, str):
            raise PythonKernelContractError("code must be a string")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_EXECUTION_REQUEST_SCHEMA,
            "workspace_request_sha256": self.workspace_request_sha256,
            "endpoint_lease_sha256": self.endpoint_lease_sha256,
            "generation_id": self.generation_id,
            "execution_id": self.execution_id,
            "goal_hash": self.goal_hash,
            "policy_sha256": self.policy_sha256,
            "data_boundary_sha256": self.data_boundary_sha256,
            "code": self.code,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class PythonExecutionReceipt:
    execution_request_sha256: str
    workspace_request_sha256: str
    endpoint_lease_sha256: str
    generation_id: str
    execution_id: str
    jupyter_msg_id: str
    status: PythonExecutionStatus
    started_at: str
    finished_at: str
    duration_ms: int
    code_sha256: str
    output_artifact: FrozenJson
    output_projection: FrozenJson
    late_async_outputs: tuple[FrozenJson, ...]
    tau_admission_status: str
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"OK", "ERROR", "INTERRUPTED", "BLOCKED"}:
            raise PythonKernelContractError("python execution status is invalid")
        if self.status == "OK" and self.errors:
            raise PythonKernelContractError("successful kernel receipt must not contain errors")
        _require_nonempty(
            {
                "execution_request_sha256": self.execution_request_sha256,
                "workspace_request_sha256": self.workspace_request_sha256,
                "endpoint_lease_sha256": self.endpoint_lease_sha256,
                "generation_id": self.generation_id,
                "execution_id": self.execution_id,
                "jupyter_msg_id": self.jupyter_msg_id,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "code_sha256": self.code_sha256,
                "tau_admission_status": self.tau_admission_status,
            }
        )
        if self.duration_ms < 0:
            raise PythonKernelContractError("duration_ms must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_EXECUTION_RECEIPT_SCHEMA,
            "execution_request_sha256": self.execution_request_sha256,
            "workspace_request_sha256": self.workspace_request_sha256,
            "endpoint_lease_sha256": self.endpoint_lease_sha256,
            "generation_id": self.generation_id,
            "execution_id": self.execution_id,
            "jupyter_msg_id": self.jupyter_msg_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "code_sha256": self.code_sha256,
            "output_artifact": self.output_artifact.to_value(),
            "output_projection": self.output_projection.to_value(),
            "late_async_outputs": [item.to_value() for item in self.late_async_outputs],
            "tau_admission_status": self.tau_admission_status,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class PythonKernelControlReceipt:
    endpoint_lease_sha256: str
    generation_id: str
    action: str
    status: str
    process_identity_before: FrozenJson
    process_identity_after: FrozenJson
    errors: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_KERNEL_CONTROL_RECEIPT_SCHEMA,
            "endpoint_lease_sha256": self.endpoint_lease_sha256,
            "generation_id": self.generation_id,
            "action": self.action,
            "status": self.status,
            "process_identity_before": self.process_identity_before.to_value(),
            "process_identity_after": self.process_identity_after.to_value(),
            "errors": list(self.errors),
        }


def build_python_package_manifest() -> PythonPackageManifest:
    packages: dict[str, dict[str, str | bool]] = {}
    errors: list[str] = []
    for package_name in ("jupyter_client", "ipykernel"):
        try:
            version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            version = ""
            errors.append(f"missing_package:{package_name}")
        packages[package_name] = {"version": version, "available": bool(version)}
    return PythonPackageManifest(
        python_executable=sys.executable,
        python_version=sys.version,
        platform=platform.platform(),
        packages=FrozenJson.from_value(packages),
        security_profile=FrozenJson.from_value(
            {
                "sandbox": "none",
                "process_user": "current-user",
                "note": "Jupyter kernel executes local code and is not Tau acceptance.",
            }
        ),
        available=not errors,
        errors=tuple(errors),
    )


def verify_execution_binding(
    receipt: PythonExecutionReceipt | dict[str, Any],
    request: PythonExecutionRequest,
) -> dict[str, Any]:
    payload = receipt.to_payload() if isinstance(receipt, PythonExecutionReceipt) else receipt
    checks = {
        "execution_request_sha256": payload.get("execution_request_sha256") == request.sha256,
        "workspace_request_sha256": (
            payload.get("workspace_request_sha256") == request.workspace_request_sha256
        ),
        "endpoint_lease_sha256": (
            payload.get("endpoint_lease_sha256") == request.endpoint_lease_sha256
        ),
        "generation_id": payload.get("generation_id") == request.generation_id,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "schema": "tau.python_execution_binding_verification.v1",
        "status": "PASS" if not failed else "BLOCKED",
        "checks": checks,
        "errors": [f"binding_mismatch:{name}" for name in failed],
    }


def workspace_endpoint_lease(
    request: PythonWorkspaceRequest,
    *,
    endpoint_id: str,
    backend_session_id: str,
    generation_id: str,
    process_identity: dict[str, Any],
    created_at: str,
    expires_at: str,
) -> RuntimeEndpointLease:
    return RuntimeEndpointLease(
        run_id=request.run_id,
        plan_revision=request.plan_revision,
        dag_id=request.dag_id,
        node_id=request.node_id,
        attempt_id=request.attempt_id,
        attempt_number=request.attempt_number,
        execution_token=request.sha256.removeprefix("sha256:")[:16],
        backend=PYTHON_KERNEL_BACKEND,
        backend_session_id=backend_session_id,
        scope_id=f"{request.worktree}:{request.attempt_id}",
        endpoint_id=endpoint_id,
        work_order_sha256=request.sha256,
        goal_hash=request.goal_hash,
        owner="tau-python-workspace",
        created_at=created_at,
        expires_at=expires_at,
        heartbeat_policy=FrozenJson.from_value({"mode": "kernel-client-poll", "seconds": 5}),
        cleanup_policy=FrozenJson.from_value({"mode": "process-identity-match"}),
        capabilities_sha256=canonical_sha256(
            {
                "backend": PYTHON_KERNEL_BACKEND,
                "version": PYTHON_KERNEL_VERSION,
                "features": sorted(PYTHON_KERNEL_FEATURES),
            }
        ),
        backend_ids=FrozenJson.from_value(
            {
                "kernel_generation_id": generation_id,
                "process_identity": process_identity,
                "worktree": request.worktree,
            }
        ),
    )


def _require_known_features(features: Iterable[str]) -> None:
    unknown = sorted(set(features) - PYTHON_KERNEL_FEATURES)
    if unknown:
        raise PythonKernelContractError(f"unknown python kernel features: {', '.join(unknown)}")


def _require_schema(payload: dict[str, Any], schema: str, extras: set[str] | None = None) -> None:
    require_schema_compatible(payload.get("schema"), schema)
    allowed = {
        "schema",
        "python_executable",
        "python_version",
        "platform",
        "packages",
        "security_profile",
        "available",
        "errors",
        "sha256",
        "run_id",
        "plan_revision",
        "dag_id",
        "node_id",
        "attempt_id",
        "attempt_number",
        "worktree",
        "goal_hash",
        "policy_sha256",
        "data_boundary_sha256",
        "required_features",
        "startup_timeout_seconds",
        *(extras or set()),
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise PythonKernelContractError(f"unexpected python kernel properties: {unexpected}")


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PythonKernelContractError(f"{key} must be a non-empty string")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 1:
        raise PythonKernelContractError(f"{key} must be an integer >= 1")
    return value


def _required_number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or value <= 0:
        raise PythonKernelContractError(f"{key} must be a positive number")
    return float(value)


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if type(value) is not bool:
        raise PythonKernelContractError(f"{key} must be a boolean")
    return value


def _string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PythonKernelContractError(f"{key} must be an array of strings")
    return tuple(value)


def _frozen_object(payload: dict[str, Any], key: str) -> FrozenJson:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PythonKernelContractError(f"{key} must be an object")
    return FrozenJson.from_value(value)


def _require_nonempty(values: dict[str, str]) -> None:
    invalid = [name for name, value in values.items() if not isinstance(value, str) or not value]
    if invalid:
        raise PythonKernelContractError(
            "required python kernel fields are empty: " + ", ".join(invalid)
        )
