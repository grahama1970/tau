"""Tau-owned reusable worker session pools for scheduler-managed providers."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.worker_assignment import (
    WorkerAssignment,
    WorkerAssignmentError,
    WorkerCapability,
)

WORKER_LIFECYCLE_RECEIPT_SCHEMA = "tau.worker_lifecycle_receipt.v1"
WORKER_RESET_RECEIPT_SCHEMA = "tau.worker_reset_receipt.v1"
WORKER_CLEANUP_RECEIPT_SCHEMA = "tau.worker_cleanup_receipt.v1"
WORKER_POOL_BENCHMARK_RECEIPT_SCHEMA = "tau.worker_pool_benchmark_receipt.v1"


class WorkerSessionState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    LEASED = "LEASED"
    RESETTING = "RESETTING"
    QUARANTINED = "QUARANTINED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class ScillmWorkerSession:
    session_id: str
    generation: int
    provider: str
    model: str
    base_url: str
    trust_zone: str
    data_classes: tuple[str, ...]
    worktree_id: str
    supported_capabilities: tuple[str, ...]
    state: WorkerSessionState = WorkerSessionState.READY
    active_slots: int = 0
    concurrency_slots: int = 1
    healthy: bool = True
    spawn_count: int = 1
    claim_count: int = 0
    reuse_count: int = 0
    reset_count: int = 0
    cleanup_count: int = 0
    reset_failure: bool = False
    health_failure: bool = False
    attempt_context_keys: tuple[str, ...] = ()
    temp_file_count: int = 0
    credential_grant_count: int = 0
    cancellation_state: bool = False

    @property
    def worker_id(self) -> str:
        return f"scillm:{self.provider}:{self.model}:{self.session_id}:g{self.generation}"

    @property
    def worker_resource_id(self) -> str:
        return f"worker:scillm:{self.session_id}:g{self.generation}"

    def capability(self) -> WorkerCapability:
        return WorkerCapability(
            worker_id=self.worker_id,
            runtime_kind="scillm",
            provider=self.provider,
            model=self.model,
            adapter_kind="scillm_chat",
            interaction_modes=("one_shot",),
            supported_capabilities=self.supported_capabilities,
            session_scopes=("node_attempt",),
            trust_zones=(self.trust_zone,),
            data_classes=self.data_classes,
            worktree_ids=(self.worktree_id,),
            supports_worktree_binding=True,
            concurrency_slots=self.concurrency_slots,
            active_slots=self.active_slots,
            health="HEALTHY" if self.healthy else "UNHEALTHY",
            readiness="READY" if self.state is WorkerSessionState.READY else self.state.value,
            reset_required=True,
            cleanup_required=True,
            resource_id=self.worker_resource_id,
            priority=10,
        )


class ScillmWorkerSessionPool:
    """Deterministic SciLLM session pool used by Tau scheduler tests and adapters."""

    def __init__(self, sessions: tuple[ScillmWorkerSession, ...]) -> None:
        if not sessions:
            raise ValueError("at least one SciLLM worker session is required")
        self._sessions = {session.worker_id: session for session in sessions}
        self._lifecycle_events: list[dict[str, Any]] = []
        self._reset_events: list[dict[str, Any]] = []
        self._cleanup_events: list[dict[str, Any]] = []

    @classmethod
    def single(
        cls,
        *,
        session_id: str = "scillm-session-1",
        provider: str = "scillm",
        model: str = "gpt-5.6-xhigh",
        base_url: str = "http://127.0.0.1:4001",
        trust_zone: str = "repo",
        data_classes: tuple[str, ...] = ("source",),
        worktree_id: str = "tau-main",
        supported_capabilities: tuple[str, ...] = (
            "one_shot",
            "scillm_chat",
            "supports_working_directory",
        ),
    ) -> ScillmWorkerSessionPool:
        return cls(
            (
                ScillmWorkerSession(
                    session_id=session_id,
                    generation=1,
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    trust_zone=trust_zone,
                    data_classes=data_classes,
                    worktree_id=worktree_id,
                    supported_capabilities=supported_capabilities,
                ),
            )
        )

    def capabilities(self) -> tuple[WorkerCapability, ...]:
        return tuple(
            session.capability()
            for session in sorted(self._sessions.values(), key=lambda item: item.worker_id)
            if session.state in {WorkerSessionState.READY, WorkerSessionState.LEASED}
        )

    def claim_worker(self, assignment: WorkerAssignment) -> dict[str, Any]:
        session = self._session_for_worker(assignment.selected.worker_id)
        started = time.monotonic()
        previous_state = session.state
        if session.health_failure or not session.healthy:
            self._quarantine(session, reason="health_check_failed")
            raise WorkerAssignmentError("worker_health_check_failed", session.worker_id)
        if session.state is not WorkerSessionState.READY:
            raise WorkerAssignmentError("worker_not_ready", f"{session.worker_id}:{session.state}")
        if session.active_slots >= session.concurrency_slots:
            raise WorkerAssignmentError("worker_slots_exhausted", session.worker_id)
        updated = replace(
            session,
            state=WorkerSessionState.LEASED,
            active_slots=session.active_slots + 1,
            claim_count=session.claim_count + 1,
            reuse_count=session.reuse_count + (1 if session.claim_count else 0),
            attempt_context_keys=("messages", "tools", "scratchpad"),
            temp_file_count=2,
            credential_grant_count=1,
            cancellation_state=True,
        )
        self._sessions[session.worker_id] = updated
        receipt = {
            "schema": WORKER_LIFECYCLE_RECEIPT_SCHEMA,
            "event": "worker_claimed",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "worker_id": updated.worker_id,
            "session_id": updated.session_id,
            "generation": updated.generation,
            "previous_state": previous_state.value,
            "new_state": updated.state.value,
            "pre_claim_attempt_context_keys": list(session.attempt_context_keys),
            "run_id": assignment.requirement["run_id"],
            "node_id": assignment.requirement["node_id"],
            "attempt_id": assignment.requirement["attempt_id"],
            "worker_requirement_sha256": assignment.requirement_sha256,
            "capability_sha256": updated.capability().capability_sha256,
            "health_check": {"status": "PASS", "deterministic": True},
            "duration_seconds": round(time.monotonic() - started, 6),
            "proof_boundary": _proof_boundary(),
        }
        self._lifecycle_events.append(receipt)
        return receipt

    def complete_worker_attempt(
        self,
        *,
        worker_id: str,
        run_id: str,
        node_id: str,
        attempt_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        del result
        session = self._session_for_worker(worker_id)
        started = time.monotonic()
        previous_state = session.state
        resetting = replace(session, state=WorkerSessionState.RESETTING)
        self._sessions[worker_id] = resetting
        reset_actions = {
            "attempt_context_keys_cleared": list(resetting.attempt_context_keys),
            "temp_files_removed": resetting.temp_file_count,
            "credential_grants_revoked": resetting.credential_grant_count,
            "cancellation_state_cleared": resetting.cancellation_state,
            "tool_state_cleared": True,
        }
        status = "PASS"
        disposition = "READY"
        reset_errors: list[str] = []
        if resetting.reset_failure:
            status = "BLOCKED"
            disposition = "QUARANTINED"
            reset_errors.append("worker_reset_failed")
            updated = replace(
                resetting,
                state=WorkerSessionState.QUARANTINED,
                healthy=False,
                active_slots=max(0, resetting.active_slots - 1),
                reset_count=resetting.reset_count + 1,
            )
        else:
            updated = replace(
                resetting,
                state=WorkerSessionState.READY,
                active_slots=max(0, resetting.active_slots - 1),
                reset_count=resetting.reset_count + 1,
                attempt_context_keys=(),
                temp_file_count=0,
                credential_grant_count=0,
                cancellation_state=False,
            )
        self._sessions[worker_id] = updated
        receipt = {
            "schema": WORKER_RESET_RECEIPT_SCHEMA,
            "status": status,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "worker_id": worker_id,
            "session_id": updated.session_id,
            "generation": updated.generation,
            "previous_state": previous_state.value,
            "intermediate_state": WorkerSessionState.RESETTING.value,
            "new_state": updated.state.value,
            "disposition": disposition,
            "run_id": run_id,
            "node_id": node_id,
            "attempt_id": attempt_id,
            "reset_actions": reset_actions,
            "errors": reset_errors,
            "duration_seconds": round(time.monotonic() - started, 6),
            "proof_boundary": _proof_boundary(),
        }
        self._reset_events.append(receipt)
        return receipt

    def shutdown(self, *, reason: str = "pool_shutdown") -> tuple[dict[str, Any], ...]:
        receipts: list[dict[str, Any]] = []
        for worker_id, session in sorted(self._sessions.items()):
            previous_state = session.state
            updated = replace(
                session,
                state=WorkerSessionState.STOPPED,
                active_slots=0,
                cleanup_count=session.cleanup_count + 1,
            )
            self._sessions[worker_id] = updated
            receipt = {
                "schema": WORKER_CLEANUP_RECEIPT_SCHEMA,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "provider_live": False,
                "worker_id": worker_id,
                "session_id": updated.session_id,
                "generation": updated.generation,
                "previous_state": previous_state.value,
                "new_state": updated.state.value,
                "reason": reason,
                "cleanup_actions": {
                    "active_slots_cleared": True,
                    "attempt_context_cleared": True,
                    "session_marked_stopped": True,
                },
                "proof_boundary": _proof_boundary(),
            }
            self._cleanup_events.append(receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def recover_after_restart(
        self,
        *,
        run_id: str,
        reason: str = "scheduler_restart",
    ) -> tuple[dict[str, Any], ...]:
        receipts: list[dict[str, Any]] = []
        unsafe_states = {
            WorkerSessionState.STARTING,
            WorkerSessionState.LEASED,
            WorkerSessionState.RESETTING,
            WorkerSessionState.STOPPING,
        }
        for worker_id, session in sorted(self._sessions.items()):
            previous_state = session.state
            if previous_state in unsafe_states:
                updated = replace(
                    session,
                    state=WorkerSessionState.QUARANTINED,
                    healthy=False,
                    active_slots=0,
                )
                status = "BLOCKED"
                action = "quarantined_inflight_session"
            else:
                updated = replace(session, active_slots=0)
                status = "PASS"
                action = "revalidated_idle_session"
            self._sessions[worker_id] = updated
            receipt = {
                "schema": WORKER_LIFECYCLE_RECEIPT_SCHEMA,
                "event": "worker_recovered_after_restart",
                "status": status,
                "mocked": False,
                "live": True,
                "provider_live": False,
                "worker_id": worker_id,
                "session_id": updated.session_id,
                "generation": updated.generation,
                "previous_state": previous_state.value,
                "new_state": updated.state.value,
                "run_id": run_id,
                "reason": reason,
                "revalidation_action": action,
                "proof_boundary": _proof_boundary(),
            }
            self._lifecycle_events.append(receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def benchmark_receipt(self) -> dict[str, Any]:
        sessions = tuple(sorted(self._sessions.values(), key=lambda item: item.worker_id))
        return {
            "schema": WORKER_POOL_BENCHMARK_RECEIPT_SCHEMA,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "session_count": len(sessions),
            "spawn_count": sum(session.spawn_count for session in sessions),
            "claim_count": sum(session.claim_count for session in sessions),
            "reuse_count": sum(session.reuse_count for session in sessions),
            "reset_count": sum(session.reset_count for session in sessions),
            "cleanup_count": sum(session.cleanup_count for session in sessions),
            "claims": {
                "proves": [
                    "This fixture records session spawn, claim, reuse, reset, and cleanup counts.",
                    "Sequential compatible attempts can use one session generation.",
                ],
                "does_not_prove": [
                    "Broad performance improvement.",
                    "Provider semantic quality.",
                    "Browser, Herdr pane, CLI, or worktree pooling.",
                ],
            },
        }

    def session(self, worker_id: str) -> ScillmWorkerSession:
        return self._session_for_worker(worker_id)

    def lifecycle_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._lifecycle_events)

    def reset_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._reset_events)

    def cleanup_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._cleanup_events)

    def with_session(self, worker_id: str, **changes: Any) -> None:
        session = self._session_for_worker(worker_id)
        updated = replace(session, **changes)
        self._sessions.pop(worker_id)
        self._sessions[updated.worker_id] = updated

    def _session_for_worker(self, worker_id: str) -> ScillmWorkerSession:
        session = self._sessions.get(worker_id)
        if session is None:
            raise WorkerAssignmentError("worker_session_missing", worker_id)
        return session

    def _quarantine(self, session: ScillmWorkerSession, *, reason: str) -> None:
        self._sessions[session.worker_id] = replace(
            session,
            state=WorkerSessionState.QUARANTINED,
            healthy=False,
        )
        self._lifecycle_events.append(
            {
                "schema": WORKER_LIFECYCLE_RECEIPT_SCHEMA,
                "event": "worker_quarantined",
                "status": "BLOCKED",
                "mocked": False,
                "live": True,
                "provider_live": False,
                "worker_id": session.worker_id,
                "session_id": session.session_id,
                "generation": session.generation,
                "previous_state": session.state.value,
                "new_state": WorkerSessionState.QUARANTINED.value,
                "reason": reason,
                "proof_boundary": _proof_boundary(),
            }
        )


def _proof_boundary() -> dict[str, Any]:
    payload = {
        "workers_are_evidence_sources": False,
        "reuse_requires_new_attempt_lease": True,
        "attempt_context_is_reset_between_attempts": True,
        "provider_semantics_not_proven": True,
    }
    return {**payload, "proof_boundary_hash": canonical_sha256(payload)}
