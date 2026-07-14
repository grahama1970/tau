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
from tau_coding.runtime_backends.local import (
    LocalRuntimeBackend,
    LocalRuntimeExecutionRequest,
    LocalRuntimeExecutionResult,
    local_runtime_request,
)
from tau_coding.runtime_backends.registry import RuntimeBackendRegistry

__all__ = [
    "GitWorktreeLease",
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
    "local_runtime_request",
]
