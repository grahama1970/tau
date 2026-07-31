from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, FrozenJson
from tau_coding.dag_runtime.resource_leases import ResourceLeaseManager
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.worker_assignment import (
    WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
    WorkerCapability,
    compile_worker_requirement,
    select_worker,
)


def test_worker_matcher_records_eligible_and_stable_rejection_codes(tmp_path: Path) -> None:
    plan = _worker_plan(tmp_path)
    node = plan.nodes[0]
    requirement = compile_worker_requirement(
        plan=plan,
        node=node,
        run_id="run-1",
        attempt_id="attempt-1",
        attempt=1,
    )
    candidates = (
        _capability("wrong-provider", provider="anthropic"),
        _capability("wrong-model", model="gpt-5.6-low"),
        _capability("missing-capability", supported_capabilities=("one_shot",)),
        _capability("wrong-trust", trust_zones=("internet",)),
        _capability("wrong-data", data_classes=("public",)),
        _capability("unhealthy", health="DEAD"),
        _capability("exhausted", active_slots=1, concurrency_slots=1),
        _capability("wrong-worktree", worktree_ids=("other-worktree",)),
        _capability("winner-b", priority=20),
        _capability("winner-a", priority=10),
    )

    assignment = select_worker(requirement=requirement, candidates=candidates)

    assert assignment.selected.worker_id == "winner-a"
    rejected = {item["worker_id"]: item["rejection_codes"] for item in assignment.rejected}
    assert rejected["wrong-provider"] == ["WORKER_PROVIDER_MISMATCH"]
    assert rejected["wrong-model"] == ["WORKER_MODEL_MISMATCH"]
    assert rejected["missing-capability"] == ["WORKER_CAPABILITY_MISSING"]
    assert rejected["wrong-trust"] == ["WORKER_TRUST_ZONE_MISMATCH"]
    assert rejected["wrong-data"] == ["WORKER_DATA_CLASS_DENIED"]
    assert rejected["unhealthy"] == ["WORKER_UNHEALTHY"]
    assert rejected["exhausted"] == ["WORKER_SLOTS_EXHAUSTED"]
    assert rejected["wrong-worktree"] == ["WORKER_WORKTREE_MISMATCH"]
    assert assignment.requirement_sha256.startswith("sha256:")


def test_scheduler_writes_worker_assignment_receipt_before_dispatch(tmp_path: Path) -> None:
    plan = _worker_plan(tmp_path)
    store_path = tmp_path / "run.sqlite3"
    lease_path = tmp_path / "resource-leases.sqlite3"
    observed_attempt: dict[str, str] = {}

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        assert attempt.worker_assignment_path is not None
        assert attempt.worker_assignment_sha256 is not None
        assert attempt.worker_assignment_admission_id is not None
        observed_attempt["path"] = attempt.worker_assignment_path
        observed_attempt["sha256"] = attempt.worker_assignment_sha256
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"worker_assignment": attempt.worker_assignment_path},
        }

    with (
        SqliteDagRunStore(store_path) as store,
        ResourceLeaseManager(
            lease_path,
            owner_id="test-worker-assignment",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="worker-e2e",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=(_capability("selected-worker"),),
        )
        admissions = store.list_admissions(
            "worker-e2e",
            receipt_kind=WORKER_ASSIGNMENT_RECEIPT_SCHEMA,
        )
        events = leases.events()

    receipt = json.loads(Path(observed_attempt["path"]).read_text(encoding="utf-8"))
    assert result.status == "PASS"
    assert len(admissions) == 1
    assert admissions[0]["sha256"] == observed_attempt["sha256"]
    assert receipt["schema"] == WORKER_ASSIGNMENT_RECEIPT_SCHEMA
    assert receipt["selected_worker"]["worker_id"] == "selected-worker"
    assert receipt["resource_lease_token"]["attempt_id"] == admissions[0]["attempt_id"]
    assert any(
        event["event_type"] == "resource_lease_acquired"
        and event["payload"]["payload"]["resource_kind"] == "worker"
        for event in events
    )
    assert any(
        event["event_type"] == "resource_lease_released"
        and event["payload"]["payload"]["resource_kind"] == "worker"
        for event in events
    )


def test_scheduler_blocks_before_dispatch_when_no_worker_matches(tmp_path: Path) -> None:
    plan = _worker_plan(tmp_path)
    store_path = tmp_path / "run.sqlite3"
    lease_path = tmp_path / "resource-leases.sqlite3"
    called = False

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del node, accepted_inputs, attempt
        nonlocal called
        called = True
        return {"node_id": "worker", "status": "PASS", "verdict": "PASS"}

    with (
        SqliteDagRunStore(store_path) as store,
        ResourceLeaseManager(
            lease_path,
            owner_id="test-worker-assignment",
        ) as leases,
    ):
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="worker-block",
            lease_owner="scheduler",
            resource_lease_manager=leases,
            worker_registry=(_capability("bad", provider="anthropic"),),
        )

    assert called is False
    assert result.status == "BLOCKED"
    assert result.verdict == "WORKER_NO_ELIGIBLE_CANDIDATE"


def test_explicit_worker_registry_requires_durable_lease_gate(tmp_path: Path) -> None:
    plan = _worker_plan(tmp_path)

    with pytest.raises(RuntimeError, match="worker assignment requires run_store"):
        run_dag_plan(
            plan,
            execute_node=lambda node, _inputs, _attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
            worker_registry=(_capability("selected-worker"),),
        )


def _worker_plan(tmp_path: Path) -> DagPlan:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "worker-assignment",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    runtime = plan.nodes[0].runtime_requirement.to_value()
    runtime["worker_requirement"] = {
        "provider": "openai",
        "model": "gpt-5.6-xhigh",
        "trust_zone": "repo",
        "data_classes": ["source"],
        "worktree_id": "tau-main",
        "required_capabilities": ["repo_edit"],
    }
    node = replace(
        plan.nodes[0],
        runtime_requirement=FrozenJson.from_value(runtime),
    )
    return replace(plan, nodes=(node,)).with_computed_hash()


def _capability(
    worker_id: str,
    *,
    runtime_kind: str = "local",
    provider: str = "openai",
    model: str = "gpt-5.6-xhigh",
    interaction_modes: tuple[str, ...] = ("one_shot",),
    supported_capabilities: tuple[str, ...] = (
        "one_shot",
        "repo_edit",
        "supports_working_directory",
    ),
    trust_zones: tuple[str, ...] = ("repo",),
    data_classes: tuple[str, ...] = ("public", "source"),
    worktree_ids: tuple[str, ...] = ("tau-main",),
    health: str = "HEALTHY",
    readiness: str = "READY",
    active_slots: int = 0,
    concurrency_slots: int = 1,
    priority: int = 100,
) -> WorkerCapability:
    return WorkerCapability(
        worker_id=worker_id,
        runtime_kind=runtime_kind,
        provider=provider,
        model=model,
        adapter_kind="generic_command",
        interaction_modes=interaction_modes,
        supported_capabilities=supported_capabilities,
        session_scopes=("node_attempt",),
        trust_zones=trust_zones,
        data_classes=data_classes,
        worktree_ids=worktree_ids,
        supports_worktree_binding=True,
        health=health,
        readiness=readiness,
        active_slots=active_slots,
        concurrency_slots=concurrency_slots,
        priority=priority,
    )
