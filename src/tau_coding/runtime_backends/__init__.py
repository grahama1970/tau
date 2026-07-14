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
from tau_coding.runtime_backends.registry import RuntimeBackendRegistry

__all__ = [
    "GitWorktreeLease",
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
]
