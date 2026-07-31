"""Live filesystem conformance receipt for Tau SciLLM worker session reuse."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, FrozenJson
from tau_coding.dag_runtime.resource_leases import ResourceLeaseManager
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.worker_assignment import WORKER_ASSIGNMENT_RECEIPT_SCHEMA
from tau_coding.dag_runtime.worker_session_pool import (
    WORKER_CLEANUP_RECEIPT_SCHEMA,
    WORKER_LIFECYCLE_RECEIPT_SCHEMA,
    WORKER_RESET_RECEIPT_SCHEMA,
    ScillmWorkerSession,
    ScillmWorkerSessionPool,
    WorkerSessionState,
)

SCILLM_WORKER_POOL_CONFORMANCE_SCHEMA = "tau.scillm_worker_pool_conformance.v1"


def write_scillm_worker_pool_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Run the live scheduler/store/lease conformance workload for SciLLM pool reuse."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    run_dir = proof_dir / "run"
    proof_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = _scillm_plan(proof_dir, ["first", "second"])
    pool = ScillmWorkerSessionPool.single(worktree_id="tau-main")
    store_path = run_dir / "dag-run.sqlite3"
    lease_path = run_dir / "resource-leases.sqlite3"
    observed_attempts: list[dict[str, Any]] = []

    def execute(
        node: DagPlanNode,
        _inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if not attempt.worker_assignment_path:
            raise RuntimeError("worker_assignment_missing_before_dispatch")
        observed_attempts.append(
            {
                "node_id": node.node_id,
                "attempt_id": attempt.attempt_id,
                "worker_assignment_path": attempt.worker_assignment_path,
                "worker_assignment_sha256": attempt.worker_assignment_sha256,
            }
        )
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "attempt_id": attempt.attempt_id,
                "worker_assignment_path": attempt.worker_assignment_path,
            },
        }

    with (
        SqliteDagRunStore(store_path) as store,
        ResourceLeaseManager(
            lease_path,
            owner_id="scillm-worker-pool-conformance",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="scillm-worker-pool-conformance",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )
        assignment_admissions = store.list_admissions(
            "scillm-worker-pool-conformance",
            receipt_kind=WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
        )
        lifecycle_admissions = store.list_admissions(
            "scillm-worker-pool-conformance",
            receipt_kind=WORKER_LIFECYCLE_RECEIPT_SCHEMA,
        )
        reset_admissions = store.list_admissions(
            "scillm-worker-pool-conformance",
            receipt_kind=WORKER_RESET_RECEIPT_SCHEMA,
        )
        lease_events = leases.events()
        integrity = store.integrity_check()

    cleanup_receipts = pool.shutdown(reason="conformance_shutdown")
    benchmark = pool.benchmark_receipt()
    slot_full_pool = ScillmWorkerSessionPool(
        (
            ScillmWorkerSession(
                session_id="slot-full",
                generation=1,
                provider="scillm",
                model="gpt-5.6-xhigh",
                base_url="http://127.0.0.1:4001",
                trust_zone="repo",
                data_classes=("source",),
                worktree_id="tau-main",
                supported_capabilities=(
                    "one_shot",
                    "scillm_chat",
                    "supports_working_directory",
                ),
                active_slots=1,
                concurrency_slots=1,
            ),
        )
    )
    slot_dispatch_count = 0

    def execute_slot_full(
        node: DagPlanNode,
        _inputs: tuple[dict[str, Any], ...],
        _attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del node
        nonlocal slot_dispatch_count
        slot_dispatch_count += 1
        return {"node_id": "slot", "status": "PASS", "verdict": "PASS"}

    with (
        SqliteDagRunStore(run_dir / "slot-full.sqlite3") as slot_store,
        ResourceLeaseManager(
            run_dir / "slot-full-leases.sqlite3",
            owner_id="scillm-worker-pool-conformance",
        ) as slot_leases,
    ):
        slot_result = run_dag_plan(
            _scillm_plan(proof_dir / "slot-full", ["slot"]),
            execute_node=execute_slot_full,
            run_store=slot_store,
            run_id="scillm-worker-slot-full",
            lease_owner="scheduler",
            resource_lease_manager=slot_leases,
            worker_registry=slot_full_pool,
        )

    restart_pool = ScillmWorkerSessionPool(
        (
            ScillmWorkerSession(
                session_id="restart-inflight",
                generation=7,
                provider="scillm",
                model="gpt-5.6-xhigh",
                base_url="http://127.0.0.1:4001",
                trust_zone="repo",
                data_classes=("source",),
                worktree_id="tau-main",
                supported_capabilities=(
                    "one_shot",
                    "scillm_chat",
                    "supports_working_directory",
                ),
                state=WorkerSessionState.LEASED,
                active_slots=1,
                attempt_context_keys=("messages",),
                credential_grant_count=1,
                cancellation_state=True,
            ),
        )
    )
    restart_receipts = restart_pool.recover_after_restart(
        run_id="scillm-worker-restart-recovery",
        reason="conformance_restart_revalidation",
    )
    cleanup_path = proof_dir / "worker-cleanup-receipt.json"
    benchmark_path = proof_dir / "worker-pool-benchmark.json"
    restart_path = proof_dir / "worker-restart-recovery-receipt.json"
    slot_full_path = proof_dir / "worker-slot-full-result.json"
    _write_json(
        cleanup_path,
        {
            "schema": "tau.worker_cleanup_receipts.v1",
            "receipts": list(cleanup_receipts),
        },
    )
    _write_json(benchmark_path, benchmark)
    _write_json(
        restart_path,
        {
            "schema": "tau.worker_restart_recovery_receipts.v1",
            "receipts": list(restart_receipts),
        },
    )
    _write_json(
        slot_full_path,
        {
            "schema": "tau.worker_slot_full_result.v1",
            "status": slot_result.status,
            "verdict": slot_result.verdict,
            "dispatch_count": slot_dispatch_count,
        },
    )
    checks = {
        "scheduler_status_pass": result.status == "PASS",
        "two_attempts_observed": len(observed_attempts) == 2,
        "distinct_attempt_ids": len({item["attempt_id"] for item in observed_attempts}) == 2,
        "assignment_receipts_admitted": len(assignment_admissions) == 2,
        "lifecycle_receipts_admitted": len(lifecycle_admissions) == 2,
        "reset_receipts_admitted": len(reset_admissions) == 2,
        "same_generation_reused": benchmark["spawn_count"] == 1 and benchmark["reuse_count"] == 1,
        "second_pre_claim_context_empty": (
            len(pool.lifecycle_events()) >= 2
            and pool.lifecycle_events()[1]["pre_claim_attempt_context_keys"] == []
        ),
        "worker_leases_acquired": (
            _event_count(lease_events, "resource_lease_acquired", "worker") == 2
        ),
        "worker_leases_released": (
            _event_count(lease_events, "resource_lease_released", "worker") == 2
        ),
        "cleanup_receipt_written": (
            cleanup_path.exists() and cleanup_receipts[0]["schema"] == WORKER_CLEANUP_RECEIPT_SCHEMA
        ),
        "benchmark_receipt_written": benchmark_path.exists(),
        "full_slot_blocked_before_dispatch": (
            slot_result.status == "BLOCKED"
            and slot_result.verdict == "WORKER_NO_ELIGIBLE_CANDIDATE"
            and slot_dispatch_count == 0
        ),
        "restart_recovery_quarantined_inflight_session": (
            restart_receipts[0]["status"] == "BLOCKED"
            and restart_receipts[0]["new_state"] == "QUARANTINED"
            and restart_pool.session(restart_receipts[0]["worker_id"]).state
            is WorkerSessionState.QUARANTINED
        ),
        "run_store_integrity_pass": integrity.get("ok") is True,
    }
    failed = [name for name, ok in checks.items() if ok is not True]
    payload = {
        "schema": SCILLM_WORKER_POOL_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "failed_checks": failed,
        "checks": checks,
        "run_dir": str(run_dir),
        "store_path": str(store_path),
        "resource_lease_store": str(lease_path),
        "observed_attempts": observed_attempts,
        "assignment_admission_count": len(assignment_admissions),
        "lifecycle_admission_count": len(lifecycle_admissions),
        "reset_admission_count": len(reset_admissions),
        "cleanup_receipt_path": str(cleanup_path),
        "benchmark_receipt_path": str(benchmark_path),
        "restart_recovery_receipt_path": str(restart_path),
        "slot_full_result_path": str(slot_full_path),
        "benchmark": benchmark,
        "scheduler_result": {
            "status": result.status,
            "verdict": result.verdict,
            "completed_node_ids": list(result.completed_node_ids),
            "run_id": result.run_id,
        },
        "slot_full_result": {
            "status": slot_result.status,
            "verdict": slot_result.verdict,
            "dispatch_count": slot_dispatch_count,
        },
        "restart_recovery_result": {
            "receipt_count": len(restart_receipts),
            "status": restart_receipts[0]["status"],
            "new_state": restart_receipts[0]["new_state"],
        },
        "proof_scope": {
            "proves": [
                (
                    "Tau reused one SciLLM worker generation for two sequential "
                    "compatible DAG attempts."
                ),
                (
                    "Each attempt received a distinct attempt id, assignment receipt, "
                    "lifecycle claim receipt, reset receipt, and worker resource lease."
                ),
                "The second claim observed no attempt-scoped context from the first attempt.",
                "Cleanup and benchmark receipts were written with explicit non-proof claims.",
                "A full worker slot blocks before node dispatch.",
                "Restart recovery quarantines an in-flight worker session fail-closed.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Live SciLLM network service availability.",
                "Browser, Herdr pane, CLI, or worktree pooling.",
                "Broad performance improvement outside this fixture.",
            ],
        },
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_output, payload)
    return payload


def _scillm_plan(root: Path, node_ids: list[str]) -> DagPlan:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "scillm-worker-pool-conformance",
            "run_dir": str(root / "generic-run"),
            "nodes": [
                {
                    "node_id": node_id,
                    "role": node_id,
                    "command": ["true"],
                    "depends_on": [node_ids[index - 1]] if index else [],
                    "accepted_context_from": [node_ids[index - 1]] if index else [],
                    "receipt_path": str(root / "receipts" / f"{node_id}.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
                for index, node_id in enumerate(node_ids)
            ],
        },
        source_path=root / "scillm-worker-pool-dag.json",
    )
    nodes: list[DagPlanNode] = []
    for node in plan.nodes:
        runtime = node.runtime_requirement.to_value()
        runtime["worker_requirement"] = {
            "runtime_kind": "scillm",
            "provider": "scillm",
            "model": "gpt-5.6-xhigh",
            "adapter_kind": "scillm_chat",
            "trust_zone": "repo",
            "data_classes": ["source"],
            "worktree_id": "tau-main",
            "required_capabilities": ["scillm_chat"],
        }
        nodes.append(replace(node, runtime_requirement=FrozenJson.from_value(runtime)))
    return replace(plan, nodes=tuple(nodes)).with_computed_hash()


def _event_count(events: tuple[dict[str, Any], ...], event_type: str, resource_kind: str) -> int:
    return sum(
        1
        for event in events
        if event.get("event_type") == event_type
        and event.get("payload", {}).get("payload", {}).get("resource_kind") == resource_kind
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-live-filesystem", action="store_true")
    args = parser.parse_args()
    payload = write_scillm_worker_pool_conformance(
        Path(args.output),
        allow_live_filesystem=args.allow_live_filesystem,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
