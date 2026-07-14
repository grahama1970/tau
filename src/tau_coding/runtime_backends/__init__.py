"""Backend-neutral runtime contracts and registry."""

from tau_coding.runtime_backends.base import RuntimeBackend
from tau_coding.runtime_backends.contracts import (
    GitWorktreeLease,
    RuntimeCapabilities,
    RuntimeCapabilityDecision,
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeReconciliationReceipt,
    RuntimeRequirement,
    RuntimeStateProjection,
    RuntimeSubmitReceipt,
)
from tau_coding.runtime_backends.herdr import (
    HERDR_CLEANUP_AUTHORIZATION_SCHEMA,
    HerdrRuntimeBackend,
    HerdrRuntimeScope,
    herdr_cleanup_authorization,
    herdr_runtime_scope_request,
    herdr_runtime_spawn_request,
    herdr_runtime_work_order,
)
from tau_coding.runtime_backends.local import (
    LocalRuntimeBackend,
    LocalRuntimeExecutionRequest,
    LocalRuntimeExecutionResult,
    local_runtime_request,
)
from tau_coding.runtime_backends.registry import RuntimeBackendRegistry

__all__ = [
    "GitWorktreeLease",
    "HERDR_CLEANUP_AUTHORIZATION_SCHEMA",
    "HerdrRuntimeBackend",
    "HerdrRuntimeScope",
    "LocalRuntimeBackend",
    "LocalRuntimeExecutionRequest",
    "LocalRuntimeExecutionResult",
    "RuntimeBackend",
    "RuntimeBackendRegistry",
    "RuntimeCapabilities",
    "RuntimeCapabilityDecision",
    "RuntimeEndpointLease",
    "RuntimeEvent",
    "RuntimeReconciliationReceipt",
    "RuntimeRequirement",
    "RuntimeStateProjection",
    "RuntimeSubmitReceipt",
    "herdr_cleanup_authorization",
    "herdr_runtime_scope_request",
    "herdr_runtime_spawn_request",
    "herdr_runtime_work_order",
    "local_runtime_request",
]
