"""Parity checks for the shared durable DAG replay reducer."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.replay import replay_dag_run
from tau_coding.dag_runtime.run_store import SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan


def _plan(tmp_path: Path):  # type: ignore[no-untyped-def]
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "run-1",
        "run_dir": str(tmp_path),
        "nodes": [
            {
                "node_id": "creator",
                "role": "creator",
                "command": ["true"],
                "receipt_path": str(tmp_path / "creator.json"),
            },
            {
                "node_id": "reviewer",
                "role": "reviewer",
                "command": ["true"],
                "depends_on": ["creator"],
                "receipt_path": str(tmp_path / "reviewer.json"),
            },
        ],
    }
    return compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_replay_matches_scheduler_state(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    database = tmp_path / "dag-run.sqlite3"
    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"source_node_id": node.node_id},
            },
        )
    with SqliteDagRunReader(database) as reader:
        replay = replay_dag_run(
            plan=reader.load_plan("run-1"),
            run_record=reader.load_run_record("run-1"),
            events=tuple(item.to_mapping() for item in reader.load_events("run-1", limit=5000)),
            attempts=reader.load_attempts("run-1"),
            runtime_projections=reader.runtime_projections("run-1"),
        )
    assert dict(replay.node_states) == dict(result.node_states)
    assert dict(replay.edge_states) == dict(result.edge_states)
    assert dict(replay.terminal_states) == dict(result.terminal_states)


def test_blocked_replay_preserves_earlier_completed_nodes(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    database = tmp_path / "dag-run.sqlite3"
    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS" if node.node_id == "creator" else "BLOCKED",
                "verdict": "PASS" if node.node_id == "creator" else "REVIEW_BLOCKED",
            },
        )
    assert first.status == "BLOCKED"
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            execute_node=lambda node, inputs, attempt: pytest.fail("durable run re-executed"),
        )
    assert "creator" in resumed.completed_node_ids
