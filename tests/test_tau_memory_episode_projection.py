from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.memory_projection import (
    MemoryProjectionError,
    MemoryProjectionOutbox,
    build_tau_orchestration_episode,
    projection_key_for,
    validate_tau_orchestration_episode,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunStore


def _env(tmp_path: Path):
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-episode",
            "run_dir": str(tmp_path / "run"),
            "goal_hash": "sha256:goal",
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
    lease = store.acquire_run(plan=plan, run_id="run-episode", owner_id="mp", ttl_seconds=60)
    return plan, store, lease, MemoryProjectionOutbox(store)


def _episode(**overrides):
    payload = {
        "projection_key": projection_key_for("run-episode", "n", "attempt-1", "node_settlement"),
        "source_outbox_row": "mp-row-1",
        "run_id": "run-episode",
        "dag_id": "generic:run-episode",
        "dag_plan_hash": "sha256:plan",
        "node_id": "n",
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "goal_hash": "sha256:goal",
        "work_order_hash": "sha256:work-order",
        "journal_sequence": 7,
        "journal_head_hash": "sha256:journal-head",
        "source_event_refs": ["dag_run_events/7"],
        "source_receipt_refs": ["receipt_admissions/attempt-1"],
        "source_receipt_hashes": ["sha256:receipt"],
        "fact_kind": "node_settlement",
        "summary": "node n settled with accepted output",
        "outcome": "PASS",
        "project": "tau",
        "live": True,
        "mocked": False,
        "provider_live": False,
        "route_key": "bounded-ready-queue",
        "joined_from": ["prep"],
    }
    payload.update(overrides)
    return build_tau_orchestration_episode(**payload)


def test_tau_orchestration_episode_contains_required_lineage_and_no_raw_text() -> None:
    doc = _episode()
    assert doc["schema"] == "memory.tau_orchestration_episode.v1"
    assert doc["projection_idempotency_key"].startswith("mp-")
    assert doc["source_outbox_row"] == "mp-row-1"
    assert doc["journal_sequence"] == 7
    assert doc["source_receipt_hashes"] == ["sha256:receipt"]
    assert doc["validity_state"] == "accepted"
    assert "stdout" not in doc
    assert "hidden_reasoning" not in doc


def test_tau_orchestration_episode_rejects_mutated_lineage_and_forbidden_fields() -> None:
    doc = _episode()
    changed = dict(doc)
    changed["goal_hash"] = "sha256:different"
    with pytest.raises(MemoryProjectionError) as mutation:
        validate_tau_orchestration_episode(changed, existing=doc)
    assert "tau_episode_immutable_lineage_changed" in str(mutation.value)

    forbidden = dict(doc)
    forbidden["raw_prompt"] = "send the secret"
    with pytest.raises(MemoryProjectionError) as raw:
        validate_tau_orchestration_episode(forbidden)
    assert "tau_episode_forbidden_field" in str(raw.value)


def test_tau_episode_outbox_replay_is_exactly_once_and_terminal_rejection_does_not_touch_run(tmp_path: Path) -> None:
    _plan, store, lease, outbox = _env(tmp_path)
    doc = _episode()
    with store._transaction():
        key = outbox.enqueue_within_transaction(
            lease,
            node_id="n",
            attempt_id="attempt-1",
            fact_kind="tau_orchestration_episode",
            payload=doc,
        )
    calls = []
    outbox.relay(lambda payload: calls.append(payload) or {"ok": True})
    outbox.relay(lambda payload: calls.append(payload) or {"ok": True})
    assert outbox.state_of(key) == "projected"
    assert len(calls) == 1

    bad_doc = _episode(source_outbox_row="mp-row-bad", fact_kind="node_settlement_bad")
    with store._transaction():
        bad_key = outbox.enqueue_within_transaction(
            lease,
            node_id="n",
            attempt_id="attempt-2",
            fact_kind="tau_orchestration_episode_bad",
            payload=bad_doc,
        )
    outbox.relay(lambda payload: {"ok": False, "retryable": False, "error": "schema rejected"})
    assert outbox.state_of(bad_key) == "permanently_rejected"
    assert store.load_run_record("run-episode").status == "RUNNING"


def test_tau_episode_outage_degrades_then_recovery_projects_distinct_row(tmp_path: Path) -> None:
    _plan, store, lease, outbox = _env(tmp_path)
    with store._transaction():
        key = outbox.enqueue_within_transaction(
            lease,
            node_id="n",
            attempt_id="attempt-outage",
            fact_kind="tau_orchestration_episode",
            payload=_episode(source_outbox_row="mp-row-outage", attempt_id="attempt-outage"),
        )
    for _ in range(3):
        outbox.relay(lambda payload: {"ok": False, "retryable": True, "error": "memory down"})
    assert outbox.state_of(key) == "degraded"
    assert store.load_run_record("run-episode").status == "RUNNING"

    with store._transaction():
        recovery_key = outbox.enqueue_within_transaction(
            lease,
            node_id="n",
            attempt_id="attempt-recovery",
            fact_kind="tau_orchestration_episode_recovery",
            payload=_episode(source_outbox_row="mp-row-recovery", attempt_id="attempt-recovery"),
        )
    outbox.relay(lambda payload: {"ok": True})
    assert outbox.state_of(recovery_key) == "projected"
