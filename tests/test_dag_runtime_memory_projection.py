"""Outbox projection tests (#220)."""

from __future__ import annotations

import json
from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.memory_projection import MemoryProjectionOutbox
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan


def _env(tmp_path: Path):
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-mp",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "n",
                    "role": "n",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "n.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-mp", owner_id="t", ttl_seconds=60)
    return store, lease, MemoryProjectionOutbox(store)


def _enqueue(store, lease, outbox, *, attempt="attempt-1", kind="accepted_outcome"):
    with store._transaction():
        return outbox.enqueue_within_transaction(
            lease,
            node_id="n",
            attempt_id=attempt,
            fact_kind=kind,
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


def test_outage_degrades_after_max_attempts_then_same_row_recovers_once(tmp_path: Path) -> None:
    store, lease, outbox = _env(tmp_path)
    key = _enqueue(store, lease, outbox)

    def dead(_payload):
        raise ConnectionError("graph-memory unavailable")

    outbox.relay(dead, max_attempts=1)
    assert outbox.state_of(key) == "degraded"
    # execution state is untouched: the run store still holds the run row
    assert store.list_admissions("run-mp") == []

    calls = []
    results = outbox.relay(lambda payload: calls.append(payload) or {"ok": True})
    replay = outbox.relay(lambda payload: calls.append(payload) or {"ok": True})
    assert [result.state for result in results] == ["projected"]
    assert replay == []
    assert len(calls) == 1


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


def _agent_event(
    node_id: str, attempt: int, seq: int, prev: str, event_type: str
) -> dict[str, object]:
    entry: dict[str, object] = {
        "schema": "tau.agent_event.v1",
        "node_id": node_id,
        "attempt": attempt,
        "seq": seq,
        "prev_sha256": prev,
        "event_type": event_type,
        "payload": {"summary": event_type},
    }
    entry["sha256"] = canonical_sha256(entry)
    return entry


def test_scheduler_enqueues_accepted_episode_rows_during_settlement(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-live-mp",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "n",
                    "role": "n",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "n.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, object]:
        event_store = SqliteDagRunStore(store.path)
        try:
            lease = event_store.acquire_run(
                plan=plan, run_id="run-live-mp", owner_id="tester", ttl_seconds=60
            )
            prev = ""
            for seq, event_type in enumerate(
                ("agent_turn_recorded", "tool_effect_recorded", "agent_turn_recorded"), start=1
            ):
                entry = _agent_event("n", attempt.attempt, seq, prev, event_type)
                event_store.append_agent_event(
                    lease,
                    entry=entry,
                    binding={
                        "plan_sha256": plan.plan_sha256,
                        "goal_hash": plan.runtime_goal_hash,
                        "work_order_sha256": attempt.idempotency_key,
                        "attempt_id": attempt.attempt_id,
                        "transport_correlation": {},
                    },
                )
                prev = str(entry["sha256"])
        finally:
            event_store.close()
        receipt = {
            "schema": "tau.test_receipt.v1",
            "status": "PASS",
            "attempt_id": attempt.attempt_id,
        }
        receipt_path = tmp_path / "n.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return {
            "node_id": "n",
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "source_node_id": "n",
                "plan_sha256": plan.plan_sha256,
                "attempt_id": attempt.attempt_id,
            },
            "live": True,
            "mocked": False,
            "provider_live": False,
        }

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id="run-live-mp",
        lease_owner="tester",
    )

    assert outcome.status == "PASS"
    rows = MemoryProjectionOutbox(store).all_rows()
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert [row["state"] for row in rows] == ["pending"] * 4
    assert [payload["fact_kind"] for payload in payloads] == [
        "agent_turn",
        "tool_effect",
        "agent_turn",
        "node_settlement",
    ]
    assert all(payload["schema"] == "memory.tau_orchestration_episode.v1" for payload in payloads)
    assert all(payload["source_receipt_hashes"] for payload in payloads)
    assert all(
        payload["source_event_refs"][-1].startswith("dag_run_events/") for payload in payloads
    )
