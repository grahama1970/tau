"""System-settlement and RUN_STORE_FAILURE tests (#201)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.system_settlement import (
    RUN_STORE_FAILURE_MARKER,
    SYSTEM_SETTLEMENT_KIND,
    RunStoreFailure,
    assert_dispatch_allowed,
    run_store_failed,
    settle_with_system_receipt,
)


def _env(tmp_path: Path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-sys",
            "run_dir": str(tmp_path / "run"),
            "nodes": [{
                "node_id": "node-a", "role": "node-a", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(receipts / "node-a" / "attempt-1.json"),
                "timeout_seconds": 1, "max_attempts": 1,
            }],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-sys", owner_id="t", ttl_seconds=60)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    return store, lease, identity, receipts


def test_missing_worker_receipt_settles_blocked_via_system_kind(tmp_path: Path) -> None:
    store, lease, identity, receipts = _env(tmp_path)

    record = settle_with_system_receipt(
        store, lease, identity.attempt_id,
        receipts_root=receipts, node_id="node-a",
        reason_code="expected_receipt_not_admitted",
        expected_receipt_kind="node_receipt",
        classification="attempted_and_swallowed",
        run_dir=tmp_path,
    )

    assert record["receipt_kind"] == SYSTEM_SETTLEMENT_KIND
    rows = store.list_admissions("run-sys")
    assert [r["receipt_kind"] for r in rows] == [SYSTEM_SETTLEMENT_KIND]
    payload = json.loads(Path(rows[0]["path"]).read_text())
    assert payload["verdict"] == "BLOCKED"
    assert payload["reason_code"] == "expected_receipt_not_admitted"
    assert not run_store_failed(tmp_path)


def test_trusted_path_failure_enters_run_store_failure(tmp_path: Path) -> None:
    store, lease, identity, receipts = _env(tmp_path)
    store.close()  # closed store makes admit fail -> trusted path failure

    with pytest.raises(RunStoreFailure):
        settle_with_system_receipt(
            store, lease, identity.attempt_id,
            receipts_root=receipts, node_id="node-a",
            reason_code="expected_receipt_not_admitted",
            expected_receipt_kind="node_receipt",
            classification="attempted_and_swallowed",
            run_dir=tmp_path,
        )

    assert run_store_failed(tmp_path)
    marker = json.loads((tmp_path / RUN_STORE_FAILURE_MARKER).read_text())
    assert marker["schema"] == "tau.run_store_failure.v1"
    with pytest.raises(RunStoreFailure, match="dispatch refused"):
        assert_dispatch_allowed(tmp_path)


def test_dispatch_allowed_when_no_marker(tmp_path: Path) -> None:
    assert_dispatch_allowed(tmp_path)
    assert run_store_failed(tmp_path) is False
