"""Outbox projection tests (#220)."""

from __future__ import annotations

from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.memory_projection import MemoryProjectionOutbox
from tau_coding.dag_runtime.run_store import SqliteDagRunStore


def _env(tmp_path: Path):
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1", "run_id": "run-mp",
            "run_dir": str(tmp_path / "run"),
            "nodes": [{
                "node_id": "n", "role": "n", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(tmp_path / "n.json"),
                "timeout_seconds": 1, "max_attempts": 1,
            }],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-mp", owner_id="t", ttl_seconds=60)
    return store, lease, MemoryProjectionOutbox(store)


def _enqueue(store, lease, outbox, *, attempt="attempt-1", kind="accepted_outcome"):
    with store._transaction():
        return outbox.enqueue_within_transaction(
            lease, node_id="n", attempt_id=attempt, fact_kind=kind,
            payload={"verdict": "PASS", "evidence": "sha256:abc"},
        )


def test_enqueue_is_idempotent_on_projection_key(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    k1 = _enqueue(store, lease, outbox)
    k2 = _enqueue(store, lease, outbox)
    assert k1 == k2
    assert len(outbox.all_rows()) == 1


def test_relay_projects_and_dedupes(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    _enqueue(store, lease, outbox)
    calls = []
    results = outbox.relay(lambda p: calls.append(p) or {"ok": True})
    assert [r.state for r in results] == ["projected"]
    # a second relay does not resend a projected row
    again = outbox.relay(lambda p: calls.append(p) or {"ok": True})
    assert again == []
    assert len(calls) == 1


def test_outage_degrades_after_max_attempts_not_raises(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    key = _enqueue(store, lease, outbox)

    def dead(_payload):
        raise ConnectionError("graph-memory unavailable")

    for _ in range(3):
        outbox.relay(dead, max_attempts=3)
    assert outbox.state_of(key) == "degraded"
    # execution state is untouched: the run store still holds the run row
    assert store.list_admissions("run-mp") == []


def test_permanent_rejection_is_terminal(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    key = _enqueue(store, lease, outbox)
    outbox.relay(lambda p: {"ok": False, "retryable": False, "error": "schema rejected"})
    assert outbox.state_of(key) == "permanently_rejected"
    # not retried
    assert outbox.pending() == []


def test_retryable_then_success(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    key = _enqueue(store, lease, outbox)
    outbox.relay(lambda p: {"ok": False, "retryable": True, "error": "timeout"}, max_attempts=5)
    assert outbox.state_of(key) == "retryable_failed"
    outbox.relay(lambda p: {"ok": True}, max_attempts=5)
    assert outbox.state_of(key) == "projected"
