"""Admission-table and single-transaction settle tests (#199)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import (
    DagRunStoreError,
    SqliteDagRunStore,
)


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
        lease, attempt_id, receipt_kind="node_receipt",
        sha256="sha256:abc", path="/p.json", size_bytes=1,
    )

    with pytest.raises(DagRunStoreError, match="dag_admission_conflict"):
        store.admit_receipt(
            lease, attempt_id, receipt_kind="node_receipt",
            sha256="sha256:DIFFERENT", path="/p.json", size_bytes=1,
        )


def test_admission_rows_are_append_only(tmp_path: Path) -> None:
    store, lease, attempt_id = _store_with_attempt(tmp_path)
    store.admit_receipt(
        lease, attempt_id, receipt_kind="node_receipt",
        sha256="sha256:abc", path="/p.json", size_bytes=1,
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
        for row in reopened._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "receipt_admissions" in tables
    migrations = reopened._connection.execute(
        "SELECT from_version, to_version FROM dag_store_migrations"
    ).fetchall()
    assert (2, 3) in {(r[0], r[1]) for r in migrations}
