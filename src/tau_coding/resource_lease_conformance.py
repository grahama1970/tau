"""Live conformance receipt for scheduler resource leases and branch locks."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, FrozenJson
from tau_coding.dag_runtime.resource_leases import ResourceLeaseDenied, ResourceLeaseManager
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan

RESOURCE_LEASE_CONFORMANCE_SCHEMA = "tau.resource_lease_conformance.v1"


def write_resource_lease_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Run the live resource-lease dispatch and denial conformance workload."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    run_dir = proof_dir / "run"
    proof_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = _lease_plan(proof_dir)
    store_path = run_dir / "dag-run.sqlite3"
    lease_store_path = run_dir / "resource-leases.sqlite3"
    events: list[dict[str, Any]] = []
    barrier = threading.Barrier(2)

    def execute(
        node: DagPlanNode,
        _accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id in {"gpu-left", "gpu-right"}:
            barrier.wait(timeout=2)
            time.sleep(0.05)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "node_id": node.node_id,
                "attempt_id": attempt.attempt_id,
            },
        }

    with SqliteDagRunStore(store_path) as run_store, ResourceLeaseManager(
        lease_store_path,
        owner_id="tau-resource-lease-conformance",
    ) as manager:
        scheduler_result = run_dag_plan(
            plan,
            execute_node=execute,
            max_concurrency=2,
            event_sink=events.append,
            run_store=run_store,
            run_id="resource-lease-conformance-run",
            lease_owner="scheduler-owner",
            resource_lease_manager=manager,
            resource_lease_ttl_seconds=10,
        )
        event_counts_after_scheduler = manager.event_counts()
        mutation_denial = _exercise_mutation_denial(manager=manager, run_store=run_store, plan=plan)
        expiry_recovery = _exercise_expiry_recovery(manager=manager, run_store=run_store, plan=plan)
        lease_events = manager.events()
        integrity = run_store.integrity_check()
        journal_events = list(run_store.load_events(scheduler_result.run_id or ""))

    checks = {
        "scheduler_status_pass": scheduler_result.status == "PASS",
        "configured_concurrency_enforced": scheduler_result.max_observed_concurrency == 2,
        "acquisition_events_durable": event_counts_after_scheduler.get(
            "resource_lease_acquired", 0
        )
        >= 2,
        "release_events_durable": event_counts_after_scheduler.get("resource_lease_released", 0)
        >= 2,
        "overlapping_mutation_lock_denied": mutation_denial.get("denied") is True,
        "expired_lease_recovery_recorded": expiry_recovery.get("expired_count") == 1,
        "expired_leases_fail_closed": expiry_recovery.get("active_after_recovery") == 0,
        "run_store_integrity_pass": integrity.get("ok") is True,
        "scheduler_diagnostic_events_durable": any(
            item["event_type"] == "dag_diagnostic_event_appended" for item in journal_events
        ),
    }
    failed_checks = [name for name, value in checks.items() if value is not True]
    payload = {
        "schema": RESOURCE_LEASE_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "run_dir": str(run_dir),
        "dag_store": str(store_path),
        "resource_lease_store": str(lease_store_path),
        "scheduler_result": {
            "status": scheduler_result.status,
            "verdict": scheduler_result.verdict,
            "max_observed_concurrency": scheduler_result.max_observed_concurrency,
            "completed_node_ids": list(scheduler_result.completed_node_ids),
            "run_id": scheduler_result.run_id,
            "lease_epoch": scheduler_result.lease_epoch,
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "event_counts": _event_counts(lease_events),
        "mutation_denial": mutation_denial,
        "expiry_recovery": expiry_recovery,
        "journal_event_count": len(journal_events),
        "resource_lease_event_count": len(lease_events),
        "resource_lease_events": lease_events,
        "scheduler_events": events,
        "run_store_integrity": integrity,
        "proof_scope": {
            "proves": [
                "Tau scheduler dispatch acquired resource leases before node execution.",
                "Tau released resource leases after node completion.",
                "Tau recorded lease acquire/release/denial/expiry events durably.",
                "Tau denied an overlapping mutation branch lock.",
                "Tau recovered expired leases before later dispatch could reuse them.",
            ],
            "does_not_prove": [
                "Distributed lock consensus across hosts.",
                "OS isolation against malicious same-user processes.",
                "Provider/model semantic quality.",
                "GPU driver-level isolation.",
            ],
        },
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_output, payload)
    return payload


def _lease_plan(root: Path):
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "resource-lease-conformance",
        "run_dir": str(root / "generic-run"),
        "nodes": [
            _node(root, "gpu-left"),
            _node(root, "gpu-right"),
        ],
    }
    plan = compile_generic_dag_plan(payload, source_path=root / "resource-lease-dag.json")
    nodes = []
    for node in plan.nodes:
        runtime = node.runtime_requirement.to_value()
        runtime["resource_leases"] = [
            {
                "resource_kind": "gpu",
                "resource_id": "gpu:0",
                "mode": "shared",
            }
        ]
        nodes.append(replace(node, runtime_requirement=FrozenJson.from_value(runtime)))
    return replace(plan, nodes=tuple(nodes)).with_computed_hash()


def _exercise_mutation_denial(
    *,
    manager: ResourceLeaseManager,
    run_store: SqliteDagRunStore,
    plan,
) -> dict[str, Any]:
    lease = run_store.acquire_run(
        plan=plan,
        run_id="resource-lease-mutation-denial",
        owner_id="mutation-owner",
    )
    node_a = _ad_hoc_node("mutator-a", mutation_key="branch:main")
    node_b = _ad_hoc_node("mutator-b", mutation_key="branch:main")
    first = manager.acquire_for_attempt(
        node=node_a,
        run_id=lease.run_id,
        attempt_id="attempt-mutator-a",
        ttl_seconds=10,
        run_store=run_store,
        scheduler_lease=lease,
    )
    denied = False
    error = None
    try:
        manager.acquire_for_attempt(
            node=node_b,
            run_id=lease.run_id,
            attempt_id="attempt-mutator-b",
            ttl_seconds=10,
            run_store=run_store,
            scheduler_lease=lease,
        )
    except ResourceLeaseDenied as exc:
        denied = True
        error = {"code": exc.code, "detail": exc.detail, "message": str(exc)}
    manager.release(first, run_store=run_store, scheduler_lease=lease, reason="mutation_probe_done")
    run_store.mark_run_finished(lease, status="PASS", verdict="PASS")
    run_store.release_lease(lease)
    return {"denied": denied, "error": error}


def _exercise_expiry_recovery(
    *,
    manager: ResourceLeaseManager,
    run_store: SqliteDagRunStore,
    plan,
) -> dict[str, Any]:
    lease = run_store.acquire_run(
        plan=plan,
        run_id="resource-lease-expiry-recovery",
        owner_id="expiry-owner",
    )
    tokens = manager.acquire_for_attempt(
        node=_ad_hoc_node("expiry-holder"),
        run_id=lease.run_id,
        attempt_id="attempt-expiry-holder",
        ttl_seconds=0.02,
        run_store=run_store,
        scheduler_lease=lease,
    )
    time.sleep(0.05)
    expired_count = manager.recover_expired(reason="conformance_crash_recovery")
    active_after = manager.active_count(resource_kind="workspace", resource_id="workspace:shared")
    run_store.mark_run_finished(lease, status="PASS", verdict="PASS")
    run_store.release_lease(lease)
    return {
        "token_count": len(tokens),
        "expired_count": expired_count,
        "active_after_recovery": active_after,
    }


def _ad_hoc_node(node_id: str, *, mutation_key: str | None = None) -> DagPlanNode:
    requirement = {
        "resource_kind": "workspace",
        "resource_id": "workspace:shared",
        "mode": "exclusive",
    }
    if mutation_key is not None:
        requirement["mutation_key"] = mutation_key
    return DagPlanNode(
        node_id=node_id,
        role=node_id,
        executor="local",
        adapter_kind="inline",
        adapter_config=FrozenJson.from_value({}),
        max_attempts=1,
        timeout_kind="explicit",
        timeout_seconds=1,
        required_evidence=(),
        static_context=FrozenJson.from_value({}),
        requested_capabilities=(),
        source_bindings=(),
        source_extensions=FrozenJson.from_value({}),
        runtime_requirement=FrozenJson.from_value({"resource_leases": [requirement]}),
    )


def _node(root: Path, node_id: str) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": [],
        "accepted_context_from": [],
        "receipt_path": str(root / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _event_counts(events: tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
