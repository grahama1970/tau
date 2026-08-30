"""Durable cross-process operator action inbox tests for tau#314."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import (
    DagRunStoreError,
    SqliteDagRunStore,
    _operator_action_head,
)


def _plan(tmp_path: Path):
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "operator-action-inbox-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker-receipt.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 2,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _future_stamp() -> str:
    return (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def _action_request(store: SqliteDagRunStore, plan, *, action_id: str = "action-1") -> dict[str, object]:
    head_seq, head_sha256 = _operator_action_head(store._connection, "run-1")
    return {
        "schema": "tau.operator_action_request.v1",
        "action_request_id": action_id,
        "idempotency_key": f"idem-{action_id}",
        "run_id": "run-1",
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "goal_hash": plan.runtime_goal_hash,
        "node_id": "worker",
        "attempt": 1,
        "action": "add_next_turn_instruction",
        "actor": "graham",
        "principal": "graham",
        "authority_class": "human_operator",
        "observed_journal_seq": head_seq,
        "observed_journal_head_sha256": head_sha256,
        "requested_safe_point": "scheduler_boundary",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "expires_at": _future_stamp(),
        "arguments": {"instruction": "include proof receipt readback"},
        "client_correlation": {"source": "pytest"},
    }


def test_operator_action_request_survives_process_reopen_and_applies_once(tmp_path: Path) -> None:
    database = tmp_path / "dag-run.sqlite3"
    plan = _plan(tmp_path)

    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="worker", attempt=1)
        request = _action_request(store, plan)
        submitted = store.submit_operator_action_request(request)
        duplicate = store.submit_operator_action_request(request)

    assert submitted["status"] == "VALIDATED"
    assert duplicate["duplicate"] is True
    assert duplicate["status"] == "VALIDATED"

    with SqliteDagRunStore(database) as reopened:
        lease = reopened.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        listed = reopened.list_operator_actions("run-1", statuses=("VALIDATED",))
        assert [item["action_request_id"] for item in listed] == ["action-1"]
        claimed = reopened.claim_operator_action(lease)
        assert claimed is not None
        assert claimed["action_request_id"] == "action-1"
        completed = reopened.complete_operator_action(
            lease,
            action_request_id="action-1",
            status="APPLIED",
            outcome="instruction_queued",
            code="operator_action_instruction_queued",
            canonical_transition={"journal_append": "next_turn_instruction"},
        )
        repeated = reopened.complete_operator_action(
            lease,
            action_request_id="action-1",
            status="APPLIED",
            outcome="instruction_queued",
            code="operator_action_instruction_queued",
            canonical_transition={"journal_append": "next_turn_instruction"},
        )
        events = [event["event_type"] for event in reopened.load_events("run-1")]

    assert completed["status"] == "APPLIED"
    assert completed["receipt"]["status"] == "APPLIED"
    assert completed["receipt"]["code"] == "operator_action_instruction_queued"
    assert repeated["duplicate"] is True
    assert events.count("operator_action_add_next_turn_instruction") == 1
    assert "operator_action_received" in events
    assert "operator_action_validated" in events
    assert "operator_action_receipt_recorded" in events


def test_operator_action_inbox_rejects_idempotency_key_conflicts(tmp_path: Path) -> None:
    database = tmp_path / "dag-run.sqlite3"
    plan = _plan(tmp_path)

    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="worker", attempt=1)
        request = _action_request(store, plan)
        store.submit_operator_action_request(request)
        conflicting = copy.deepcopy(request)
        conflicting["action_request_id"] = "action-2"
        conflicting["arguments"] = {"instruction": "different action same idem"}
        with pytest.raises(DagRunStoreError, match="operator_action_idempotency_conflict"):
            store.submit_operator_action_request(conflicting)


def test_claimed_operator_action_reconciles_after_scheduler_restart(tmp_path: Path) -> None:
    database = tmp_path / "dag-run.sqlite3"
    plan = _plan(tmp_path)

    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="worker", attempt=1)
        request = _action_request(store, plan)
        store.submit_operator_action_request(request)
        claimed = store.claim_operator_action(lease)
        assert claimed is not None

    with SqliteDagRunStore(database) as reopened:
        lease = reopened.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        reconciled = reopened.reconcile_claimed_operator_actions(lease)
        actions = reopened.list_operator_actions("run-1")
        events = [event["event_type"] for event in reopened.load_events("run-1")]

    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "RECONCILED"
    assert reconciled[0]["code"] == "operator_action_reconciled_after_uncertain_claim"
    assert actions[0]["status"] == "RECONCILED"
    assert reconciled[0]["receipt"]["outcome"] == "uncertain_reconciled_no_confirmed_effect"
    assert "operator_action_uncertain" in events
    assert "operator_action_reconciled" in events


def test_operator_action_inbox_can_emit_machine_readable_proof(tmp_path: Path) -> None:
    """Live-path marker for agentic-evals: pytest exercises SQLite WAL bytes on disk."""

    database = tmp_path / "dag-run.sqlite3"
    plan = _plan(tmp_path)
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="scheduler")
        store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="worker", attempt=1)
        submitted = store.submit_operator_action_request(_action_request(store, plan))
        claimed = store.claim_operator_action(lease)
        assert claimed is not None
        completed = store.complete_operator_action(
            lease,
            action_request_id="action-1",
            status="APPLIED",
            outcome="instruction_queued",
            code="operator_action_instruction_queued",
            canonical_transition={"journal_append": "next_turn_instruction"},
        )
    proof = {
        "schema": "tau.operator_action_inbox_proof.v1",
        "ok": True,
        "live": True,
        "mocked": False,
        "database_exists": database.exists(),
        "submission_status": submitted["status"],
        "completion_status": completed["status"],
        "receipt_sha256": completed["receipt_sha256"],
    }
    assert proof["database_exists"] is True
    assert proof["submission_status"] == "VALIDATED"
    assert proof["completion_status"] == "APPLIED"
    assert isinstance(proof["receipt_sha256"], str) and proof["receipt_sha256"].startswith("sha256:")
    if output_path := os.environ.get("TAU_OPERATOR_ACTION_INBOX_PROOF"):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, sort_keys=True))
