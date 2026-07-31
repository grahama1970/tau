from __future__ import annotations

from dataclasses import replace
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


def test_sequential_scillm_attempts_reuse_one_generation_with_reset(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first", "second"])
    pool = ScillmWorkerSessionPool.single(worktree_id="tau-main")
    observed: list[dict[str, str]] = []

    def execute(
        node: DagPlanNode,
        _inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        assert attempt.worker_assignment_path
        observed.append(
            {
                "node_id": node.node_id,
                "attempt_id": attempt.attempt_id,
                "assignment": attempt.worker_assignment_path,
            }
        )
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"attempt_id": attempt.attempt_id},
        }

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="scillm-reuse",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )
        assignments = store.list_admissions(
            "scillm-reuse",
            receipt_kind=WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
        )
        lifecycle = store.list_admissions(
            "scillm-reuse",
            receipt_kind=WORKER_LIFECYCLE_RECEIPT_SCHEMA,
        )
        resets = store.list_admissions("scillm-reuse", receipt_kind=WORKER_RESET_RECEIPT_SCHEMA)

    claim_receipts = pool.lifecycle_events()
    reset_receipts = pool.reset_events()
    benchmark = pool.benchmark_receipt()

    assert result.status == "PASS"
    assert [item["node_id"] for item in observed] == ["first", "second"]
    assert observed[0]["attempt_id"] != observed[1]["attempt_id"]
    assert len(assignments) == 2
    assert len(lifecycle) == 2
    assert len(resets) == 2
    assert {item["generation"] for item in claim_receipts} == {1}
    assert claim_receipts[1]["pre_claim_attempt_context_keys"] == []
    assert all(item["status"] == "PASS" for item in reset_receipts)
    assert benchmark["spawn_count"] == 1
    assert benchmark["claim_count"] == 2
    assert benchmark["reuse_count"] == 1
    assert benchmark["reset_count"] == 2


def test_scillm_reuse_mismatch_blocks_before_second_dispatch(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first", "second"], second_model="claude-opus-4-8")
    pool = ScillmWorkerSessionPool.single(worktree_id="tau-main")
    called: list[str] = []

    def execute(node: DagPlanNode, _inputs: tuple[dict[str, Any], ...], _attempt: DagNodeAttempt):
        called.append(node.node_id)
        return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="scillm-mismatch",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )

    assert called == ["first"]
    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_NO_ELIGIBLE_CANDIDATE"


def test_scillm_reset_failure_quarantines_and_fails_closed(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first"])
    pool = ScillmWorkerSessionPool(
        (
            replace(
                ScillmWorkerSession(
                    session_id="bad-reset",
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
                ),
                reset_failure=True,
            ),
        )
    )

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=lambda node, _inputs, _attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
            run_store=store,
            run_id="scillm-reset-failure",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )
        reset_admissions = store.list_admissions(
            "scillm-reset-failure",
            receipt_kind=WORKER_RESET_RECEIPT_SCHEMA,
        )

    reset = pool.reset_events()[0]
    worker_id = reset["worker_id"]
    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_RESET_FAILED"
    assert reset["status"] == "BLOCKED"
    assert reset["new_state"] == "QUARANTINED"
    assert pool.session(worker_id).state is WorkerSessionState.QUARANTINED
    assert len(reset_admissions) == 1


def test_scillm_health_failure_quarantines_before_dispatch(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first"])
    session = ScillmWorkerSession(
        session_id="bad-health",
        generation=1,
        provider="scillm",
        model="gpt-5.6-xhigh",
        base_url="http://127.0.0.1:4001",
        trust_zone="repo",
        data_classes=("source",),
        worktree_id="tau-main",
        supported_capabilities=("one_shot", "scillm_chat", "supports_working_directory"),
        health_failure=True,
    )
    pool = ScillmWorkerSessionPool((session,))
    called = False

    def execute(node: DagPlanNode, _inputs: tuple[dict[str, Any], ...], _attempt: DagNodeAttempt):
        del node
        nonlocal called
        called = True
        return {"node_id": "first", "status": "PASS", "verdict": "PASS"}

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="scillm-health-failure",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )

    assert called is False
    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_HEALTH_CHECK_FAILED"
    assert pool.session(session.worker_id).state is WorkerSessionState.QUARANTINED


def test_scillm_full_slot_blocks_before_dispatch(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first"])
    session = ScillmWorkerSession(
        session_id="slot-full",
        generation=1,
        provider="scillm",
        model="gpt-5.6-xhigh",
        base_url="http://127.0.0.1:4001",
        trust_zone="repo",
        data_classes=("source",),
        worktree_id="tau-main",
        supported_capabilities=("one_shot", "scillm_chat", "supports_working_directory"),
        active_slots=1,
        concurrency_slots=1,
    )
    pool = ScillmWorkerSessionPool((session,))
    called = False

    def execute(node: DagPlanNode, _inputs: tuple[dict[str, Any], ...], _attempt: DagNodeAttempt):
        del node
        nonlocal called
        called = True
        return {"node_id": "first", "status": "PASS", "verdict": "PASS"}

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="scillm-full-slot",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=pool,
        )

    assert called is False
    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_NO_ELIGIBLE_CANDIDATE"


def test_scillm_restart_recovery_quarantines_inflight_session() -> None:
    session = ScillmWorkerSession(
        session_id="restart",
        generation=4,
        provider="scillm",
        model="gpt-5.6-xhigh",
        base_url="http://127.0.0.1:4001",
        trust_zone="repo",
        data_classes=("source",),
        worktree_id="tau-main",
        supported_capabilities=("one_shot", "scillm_chat", "supports_working_directory"),
        state=WorkerSessionState.LEASED,
        active_slots=1,
        attempt_context_keys=("messages",),
        credential_grant_count=1,
        cancellation_state=True,
    )
    pool = ScillmWorkerSessionPool((session,))

    receipts = pool.recover_after_restart(run_id="restart-proof")

    assert receipts[0]["status"] == "BLOCKED"
    assert receipts[0]["event"] == "worker_recovered_after_restart"
    assert receipts[0]["previous_state"] == "LEASED"
    assert receipts[0]["new_state"] == "QUARANTINED"
    assert pool.session(session.worker_id).state is WorkerSessionState.QUARANTINED
    assert pool.session(session.worker_id).active_slots == 0


def test_invalid_reset_receipt_fails_closed(tmp_path: Path) -> None:
    plan = _scillm_plan(tmp_path, ["first"])
    pool = ScillmWorkerSessionPool.single(worktree_id="tau-main")

    class BadResetReceiptPool:
        def capabilities(self):
            return pool.capabilities()

        def claim_worker(self, assignment):
            return pool.claim_worker(assignment)

        def complete_worker_attempt(self, **_kwargs):
            return "not-a-receipt"

    with (
        SqliteDagRunStore(tmp_path / "run.sqlite3") as store,
        ResourceLeaseManager(
            tmp_path / "leases.sqlite3",
            owner_id="test-scillm-pool",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=lambda node, _inputs, _attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
            run_store=store,
            run_id="bad-reset-receipt",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=BadResetReceiptPool(),
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_RESET_RECEIPT_INVALID"


def test_scillm_pool_shutdown_records_cleanup_and_benchmark(tmp_path: Path) -> None:
    del tmp_path
    pool = ScillmWorkerSessionPool.single(worktree_id="tau-main")

    cleanup = pool.shutdown(reason="test_shutdown")
    benchmark = pool.benchmark_receipt()

    assert cleanup[0]["schema"] == WORKER_CLEANUP_RECEIPT_SCHEMA
    assert cleanup[0]["status"] == "PASS"
    assert benchmark["cleanup_count"] == 1
    assert benchmark["claims"]["does_not_prove"]


def _scillm_plan(
    tmp_path: Path,
    node_ids: list[str],
    *,
    second_model: str = "gpt-5.6-xhigh",
) -> DagPlan:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "scillm-worker-pool",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": node_id,
                    "role": node_id,
                    "command": ["true"],
                    "depends_on": [node_ids[index - 1]] if index else [],
                    "accepted_context_from": [node_ids[index - 1]] if index else [],
                    "receipt_path": str(tmp_path / f"{node_id}.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
                for index, node_id in enumerate(node_ids)
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    nodes: list[DagPlanNode] = []
    for index, node in enumerate(plan.nodes):
        runtime = node.runtime_requirement.to_value()
        runtime["worker_requirement"] = {
            "runtime_kind": "scillm",
            "provider": "scillm",
            "model": second_model if index == 1 else "gpt-5.6-xhigh",
            "adapter_kind": "scillm_chat",
            "trust_zone": "repo",
            "data_classes": ["source"],
            "worktree_id": "tau-main",
            "required_capabilities": ["scillm_chat"],
        }
        nodes.append(replace(node, runtime_requirement=FrozenJson.from_value(runtime)))
    return replace(plan, nodes=tuple(nodes)).with_computed_hash()
