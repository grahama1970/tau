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
from tau_coding.runtime_backends.tmux import (
    TMUX_CLEANUP_AUTHORIZATION_SCHEMA,
    TmuxRuntimeBackend,
    TmuxRuntimeScope,
    tmux_cleanup_authorization,
    tmux_runtime_scope_request,
    tmux_runtime_spawn_request,
    tmux_runtime_work_order,
)
from tau_coding.runtime_backends.worktrees import (
    GitWorktreeLeaseError,
    GitWorktreeLeaseManager,
    worktree_discard_authorization,
)

__all__ = [
    "GitWorktreeLease",
    "GitWorktreeLeaseError",
    "GitWorktreeLeaseManager",
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
    "TMUX_CLEANUP_AUTHORIZATION_SCHEMA",
    "TmuxRuntimeBackend",
    "TmuxRuntimeScope",
    "herdr_cleanup_authorization",
    "herdr_runtime_scope_request",
    "herdr_runtime_spawn_request",
    "herdr_runtime_work_order",
    "local_runtime_request",
    "tmux_cleanup_authorization",
    "tmux_runtime_scope_request",
    "tmux_runtime_spawn_request",
    "tmux_runtime_work_order",
    "worktree_discard_authorization",
]
