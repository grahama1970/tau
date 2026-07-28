"""Strict-orphan-rule tests for startup reconciliation (#200)."""

from __future__ import annotations

from pathlib import Path

from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.reconciliation import reconcile_startup
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.write_intent import append_intent


def _env(tmp_path: Path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-rec",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "node-a",
                    "role": "node-a",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(receipts / "node-a" / "attempt-1.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 3,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-rec", owner_id="t", ttl_seconds=60)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    return store, lease, plan, identity, receipts, tmp_path / "intents.twi"


def _durable_receipt_with_intent(
    receipts: Path, sidecar: Path, attempt_id: str, payload: dict
) -> Path:
    target = receipts / "node-a" / "attempt-1.json"
    result = write_durable_json(target, payload)
    append_intent(
        sidecar,
        run_id="run-rec",
        node_id="node-a",
        attempt_id=attempt_id,
        receipt_kind="node_receipt",
        stage="S5",
        target_path=str(target),
        extra={"sha256": result.sha256},
    )
    return target


def test_orphan_with_matching_intent_is_readmitted(tmp_path: Path) -> None:
    store, lease, _plan, identity, receipts, sidecar = _env(tmp_path)
    _durable_receipt_with_intent(receipts, sidecar, identity.attempt_id, {"v": 1})

    outcome = reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    assert len(outcome.readmitted) == 1
    assert outcome.quarantined == []
    assert len(store.list_admissions("run-rec")) == 1


def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    store, lease, _plan, identity, receipts, sidecar = _env(tmp_path)
    _durable_receipt_with_intent(receipts, sidecar, identity.attempt_id, {"v": 1})
    reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    second = reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    assert second.readmitted == []
    assert second.quarantined == []
    assert len(store.list_admissions("run-rec")) == 1


def test_digest_mismatch_quarantines(tmp_path: Path) -> None:
    store, lease, _plan, identity, receipts, sidecar = _env(tmp_path)
    target = _durable_receipt_with_intent(receipts, sidecar, identity.attempt_id, {"v": 1})
    target.write_text('{"v": "tampered after intent"}')

    outcome = reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    assert outcome.readmitted == []
    assert [q["reason"] for q in outcome.quarantined] == ["intent_digest_mismatch"]
    assert store.list_admissions("run-rec") == []


def test_superseded_attempt_quarantines(tmp_path: Path) -> None:
    store, lease, plan, identity, receipts, sidecar = _env(tmp_path)
    _durable_receipt_with_intent(receipts, sidecar, identity.attempt_id, {"v": 1})
    store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=2)

    outcome = reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    assert outcome.readmitted == []
    assert [q["reason"] for q in outcome.quarantined] == ["attempt_superseded"]


def test_reconciliation_receipt_is_written_and_reports_clean(tmp_path: Path) -> None:
    store, lease, _plan, identity, receipts, sidecar = _env(tmp_path)
    _durable_receipt_with_intent(receipts, sidecar, identity.attempt_id, {"v": 1})

    outcome = reconcile_startup(store, lease, receipts_root=receipts, sidecar_path=sidecar)

    assert outcome.receipt_path is not None and outcome.receipt_path.is_file()
    assert outcome.clean is True
