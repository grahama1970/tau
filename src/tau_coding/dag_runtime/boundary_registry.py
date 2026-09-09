"""Scheduler authority-boundary failure registry (#349)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

BOUNDARY_REGISTRY_SCHEMA = "tau.scheduler_boundary_registry.v1"
FALLBACK_FAILURE_SCHEMA = "tau.scheduler_boundary_fallback_failure.v1"


@dataclass(frozen=True, slots=True)
class SchedulerBoundary:
    boundary_id: str
    family: str
    repair_category: str
    retryable: bool
    description: str


_BOUNDARIES = (
    SchedulerBoundary(
        "adapter_future_execution",
        "runtime_backend_dispatch",
        "repairable_infrastructure_failure",
        True,
        "adapter/future execution and runtime backend collection",
    ),
    SchedulerBoundary(
        "worker_assignment_completion",
        "worker_lifecycle",
        "repairable_infrastructure_failure",
        False,
        "worker assignment, completion, and session-pool failures",
    ),
    SchedulerBoundary(
        "scheduler_run_lease",
        "scheduler_lease_authority",
        "terminal_authority_violation",
        False,
        "scheduler run lease acquire, renew, and release",
    ),
    SchedulerBoundary(
        "resource_lease",
        "resource_lease_authority",
        "repairable_infrastructure_failure",
        False,
        "resource lease acquire, renew, and release",
    ),
    SchedulerBoundary(
        "workspace_stale_read",
        "workspace_consistency",
        "reconciliation_required",
        False,
        "stale workspace observation and enforcement",
    ),
    SchedulerBoundary(
        "node_completion_boundary",
        "node_completion_evidence",
        "terminal_policy_violation",
        False,
        "node completion boundary and evidence admission",
    ),
    SchedulerBoundary(
        "attempt_result_admission",
        "attempt_result_contract",
        "repairable_contract_failure",
        True,
        "attempt-result admission and schema validation",
    ),
    SchedulerBoundary(
        "result_store_commit",
        "run_store_commit",
        "reconciliation_required",
        False,
        "result staging, validation, commit, and run-store operations",
    ),
    SchedulerBoundary(
        "route_join_transition",
        "transition_policy",
        "repairable_contract_failure",
        False,
        "route/join evaluation and transition persistence",
    ),
    SchedulerBoundary(
        "effect_outbox_reconciliation",
        "effect_reconciliation",
        "reconciliation_required",
        False,
        "effect, outbox, and reconciliation boundaries",
    ),
    SchedulerBoundary(
        "checkpoint_replay_recovery",
        "checkpoint_replay_recovery",
        "reconciliation_required",
        False,
        "checkpoint, replay, and incomplete-attempt recovery",
    ),
    SchedulerBoundary(
        "cleanup_retention",
        "cleanup_retention_authority",
        "terminal_policy_violation",
        False,
        "cleanup and retention paths that can affect run authority",
    ),
    SchedulerBoundary(
        "recursive_failure_object_admission",
        "recursive_failure_containment",
        "terminal_policy_violation",
        False,
        "classification or synthesized failure-result admission failure",
    ),
)

_BOUNDARY_BY_ID = {boundary.boundary_id: boundary for boundary in _BOUNDARIES}
_ORIGINAL_CODE_BOUNDARIES = {
    "ADAPTER_EXECUTION_FAILED": "adapter_future_execution",
    "WORKER_ASSIGNMENT_FAILED": "worker_assignment_completion",
    "WORKER_COMPLETION_FAILED": "worker_assignment_completion",
    "SCHEDULER_RUN_LEASE_FAILED": "scheduler_run_lease",
    "RESOURCE_LEASE_DENIED": "resource_lease",
    "RESOURCE_LEASE_RELEASE_FAILED": "resource_lease",
    "WORKSPACE_READ_EVIDENCE_INVALID": "workspace_stale_read",
    "STALE_WORKSPACE_READ_BLOCKED": "workspace_stale_read",
    "STALE_WORKSPACE_READ_RECONCILIATION_REQUIRED": "workspace_stale_read",
    "NODE_COMPLETION_BOUNDARY_FAILED": "node_completion_boundary",
    "RESULT_STORE_COMMIT_FAILED": "result_store_commit",
    "ROUTE_JOIN_TRANSITION_FAILED": "route_join_transition",
    "DAG_ATTEMPT_EFFECT_UNCERTAIN": "effect_outbox_reconciliation",
    "CHECKPOINT_REPLAY_RECOVERY_FAILED": "checkpoint_replay_recovery",
    "CLEANUP_RETENTION_FAILED": "cleanup_retention",
}


def scheduler_boundary_registry_payload() -> dict[str, Any]:
    return {
        "schema": BOUNDARY_REGISTRY_SCHEMA,
        "status": "PASS",
        "boundaries": [asdict(boundary) for boundary in _BOUNDARIES],
        "boundary_ids": [boundary.boundary_id for boundary in _BOUNDARIES],
    }


def boundary_for_original_code(original_code: str) -> SchedulerBoundary:
    if original_code.startswith("dag_attempt_result_"):
        return _BOUNDARY_BY_ID["attempt_result_admission"]
    return _BOUNDARY_BY_ID[
        _ORIGINAL_CODE_BOUNDARIES.get(original_code, "recursive_failure_object_admission")
    ]


def attach_boundary_failure(
    result: dict[str, Any],
    *,
    boundary_id: str,
    original_exception: BaseException | None = None,
) -> dict[str, Any]:
    boundary = _BOUNDARY_BY_ID[boundary_id]
    result["boundary_id"] = boundary.boundary_id
    result["repair_category"] = boundary.repair_category
    result["failure_family"] = boundary.family
    if result.get("retryable") is None:
        result["retryable"] = boundary.retryable
    failure = result.setdefault("failure", {})
    if isinstance(failure, dict):
        failure["boundary_id"] = boundary.boundary_id
        failure["repair_category"] = boundary.repair_category
        failure["failure_family"] = boundary.family
        if original_exception is not None:
            failure["exception_class"] = type(original_exception).__name__
            failure["exception_message"] = str(original_exception)
    return result


def fallback_boundary_failure(
    *,
    node_id: str,
    boundary_id: str,
    original_code: str,
    error: str,
) -> dict[str, Any]:
    boundary = _BOUNDARY_BY_ID[boundary_id]
    return {
        "schema": FALLBACK_FAILURE_SCHEMA,
        "node_id": node_id,
        "status": "BLOCKED",
        "verdict": "SCHEDULER_BOUNDARY_CONTAINMENT_FAILED",
        "retryable": False,
        "boundary_id": boundary.boundary_id,
        "repair_category": boundary.repair_category,
        "failure_family": boundary.family,
        "errors": [error],
        "failure": {
            "schema": "tau.internal_failure.v1",
            "original_code": original_code,
            "classification_code": "tau_scheduler_boundary_fallback",
            "boundary_id": boundary.boundary_id,
            "repair_category": boundary.repair_category,
            "failure_family": boundary.family,
        },
    }
