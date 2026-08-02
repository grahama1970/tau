"""Admission-table and single-transaction settle tests (#199)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tau_coding.dag_runtime.attempt_result import DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import (
    DagRunStoreError,
    SqliteDagRunReader,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.transition import DagTransitionBatch, transition_batch_to_payload


def _plan(tmp_path: Path):
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-adm",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "node-a",
                    "role": "node-a",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "node-a.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _store_with_attempt(tmp_path: Path) -> tuple[SqliteDagRunStore, object, str]:
    plan = _plan(tmp_path)
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-adm", owner_id="tester", ttl_seconds=60)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    return store, lease, identity.attempt_id


def test_admit_inserts_row_and_event_in_one_transaction(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)

    record = store.admit_receipt(
        lease,
        attempt_id,
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/run/receipts/node-a/attempt-1.json",
        size_bytes=42,
    )

    assert record["duplicate"] is False
    rows = store.list_admissions("run-adm")
    assert len(rows) == 1
    assert rows[0]["sha256"] == "sha256:abc"
    assert rows[0]["admitted_event_seq"] is not None


def test_review_scope_snapshot_reads_plan_attempts_and_admissions(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.admit_receipt(
        lease,
        attempt_id,
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/run/receipts/node-a/attempt-1.json",
        size_bytes=42,
    )

    scope = store.review_scope_snapshot("run-adm", goal_hash="sha256:goal")

    assert scope["schema"] == "tau.review_scope.v1"
    assert scope["goal_hash"] == "sha256:goal"
    assert scope["plan_sha256"] == _plan(tmp_path).plan_sha256
    assert scope["reviewed_node_ids"] == ["node-a"]
    assert scope["reviewed_attempt_ids"] == [attempt_id]
    assert scope["admitted_artifacts"] == [
        {
            "schema": "node_receipt",
            "id": scope["admitted_artifacts"][0]["id"],
            "path": "/run/receipts/node-a/attempt-1.json",
            "sha256": "sha256:abc",
        }
    ]
    assert scope["journal_sequence_end"] >= 3
    store.close()

    with SqliteDagRunReader(tmp_path / "dag-run.sqlite3") as reader:
        readback = reader.review_scope_snapshot("run-adm", goal_hash="sha256:goal")
    assert readback == scope


def test_duplicate_same_digest_is_suppressed_not_error(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    kwargs = dict(
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/p.json",
        size_bytes=1,
    )
    store.admit_receipt(lease, attempt_id, **kwargs)

    second = store.admit_receipt(lease, attempt_id, **kwargs)

    assert second["duplicate"] is True
    assert len(store.list_admissions("run-adm")) == 1


def test_conflicting_digest_raises(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.admit_receipt(
        lease,
        attempt_id,
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/p.json",
        size_bytes=1,
    )

    with pytest.raises(DagRunStoreError, match="dag_admission_conflict"):
        store.admit_receipt(
            lease,
            attempt_id,
            receipt_kind="node_receipt",
            sha256="sha256:DIFFERENT",
            path="/p.json",
            size_bytes=1,
        )


def test_admission_rows_are_append_only(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.admit_receipt(
        lease,
        attempt_id,
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/p.json",
        size_bytes=1,
    )

    raw = sqlite3.connect(tmp_path / "dag-run.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("DELETE FROM receipt_admissions")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("UPDATE receipt_admissions SET sha256 = 'x'")


def test_v2_store_migrates_to_v3_with_admissions_table(tmp_path: Path) -> None:
    db = tmp_path / "dag-run.sqlite3"
    first = SqliteDagRunStore(db)
    first.close()
    raw = sqlite3.connect(db)
    raw.execute("DROP TABLE receipt_admissions")
    raw.execute("DROP TRIGGER IF EXISTS receipt_admissions_no_update")
    raw.execute("DROP TRIGGER IF EXISTS receipt_admissions_no_delete")
    raw.execute("UPDATE dag_store_meta SET value = '2' WHERE key = 'schema_version'")
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    reopened = SqliteDagRunStore(db)
    tables = {
        row[0]
        for row in reopened._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "receipt_admissions" in tables
    migrations = reopened._connection.execute(
        "SELECT from_version, to_version FROM dag_store_migrations"
    ).fetchall()
    assert (2, 3) in {(r[0], r[1]) for r in migrations}


def test_settlement_without_admission_lands_in_bypass_ledger(tmp_path: Path) -> None:
    """Shadow observer (#202): unadmitted settlement is recorded, not blocked."""
    import json as _json

    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.mark_dispatched(lease, attempt_id)
    _stage_and_validate_pass(store, lease, attempt_id)
    store.commit_output(lease, attempt_id)
    store.commit_transition(
        lease,
        attempt_id,
        completion={},
        result={},
        transition=transition_batch_to_payload(DagTransitionBatch()),
    )

    ledger = tmp_path / "admission-bypass-ledger.jsonl"
    entries = [_json.loads(line) for line in ledger.read_text().splitlines()]
    assert [e["node_id"] for e in entries] == ["node-a"]
    assert entries[0]["attempt_id"] == attempt_id


def test_settlement_with_admission_stays_off_the_ledger(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.admit_receipt(
        lease,
        attempt_id,
        receipt_kind="node_receipt",
        sha256="sha256:abc",
        path="/p.json",
        size_bytes=1,
    )
    store.mark_dispatched(lease, attempt_id)
    _stage_and_validate_pass(store, lease, attempt_id)
    store.commit_output(lease, attempt_id)
    store.commit_transition(
        lease,
        attempt_id,
        completion={},
        result={},
        transition=transition_batch_to_payload(DagTransitionBatch()),
    )

    assert not (tmp_path / "admission-bypass-ledger.jsonl").exists()


def _stage_and_validate_pass(store: SqliteDagRunStore, lease: object, attempt_id: str) -> None:
    staged = store.stage_result(
        lease,
        attempt_id,
        {
            "node_id": "node-a",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"ok": True},
        },
    )
    store.validate_result(
        lease,
        attempt_id,
        {
            "schema": DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA,
            "status": "PASS",
            "run_id": staged["run_id"],
            "plan_sha256": staged["plan_sha256"],
            "node_id": staged["node_id"],
            "attempt_id": staged["attempt_id"],
            "attempt": staged["attempt"],
            "result_sha256": canonical_sha256(staged),
        },
    )


def test_enforcement_blocks_pass_claim_with_torn_receipt(tmp_path: Path) -> None:
    """#207: a PASS claim whose receipt cannot be admitted settles BLOCKED
    via system_settlement instead of entering an accepted terminal state."""
    from tau_coding.dag_runtime.scheduler import run_dag_plan

    plan_receipts = tmp_path / "receipts"
    plan_receipts.mkdir()
    torn = plan_receipts / "liar" / "attempt.json"
    torn.parent.mkdir()
    torn.write_text('{"schema": "torn-mid-wri')  # unparseable on purpose

    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-enforce",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "liar",
                    "role": "liar",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(torn),
                    "timeout_seconds": 5,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")

    def lying_executor(node, accepted_inputs, attempt):  # noqa: ANN001, ARG001
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(torn),
            "command_results": [],
        }

    outcome = run_dag_plan(
        plan,
        execute_node=lying_executor,
        run_store=store,
        run_id="run-enforce",
        lease_owner="enforcer",
    )

    rows = store.list_admissions("run-enforce")
    assert [r["receipt_kind"] for r in rows] == [
        "system_settlement",
        "tau.node_input_manifest.v1",
    ]
    node_result = next(r for r in outcome.node_results if r["node_id"] == "liar")
    assert node_result["status"] == "BLOCKED"
    assert node_result["verdict"] == "RECEIPT_NOT_ADMITTED"
