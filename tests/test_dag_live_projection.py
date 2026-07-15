"""Read-only store and live projection contract checks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.replay import DagReplayAttempt
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan
from tau_coding.dag_viewer.projection import (
    build_dag_live_events,
    build_dag_live_snapshot,
    build_dag_view_manifest,
    load_dag_replay,
)


def _durable_run(tmp_path: Path) -> Path:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-1",
            "run_dir": str(tmp_path),
            "nodes": [
                {
                    "node_id": "node",
                    "role": "worker",
                    "command": ["true"],
                    "receipt_path": str(tmp_path / "node.json"),
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    database = tmp_path / "dag-run.sqlite3"
    with SqliteDagRunStore(database) as store:
        run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
        )
    return database


def test_reader_is_query_only_and_projection_accepts_only_scheduler_success(tmp_path: Path) -> None:
    database = _durable_run(tmp_path)
    with (
        SqliteDagRunReader(database) as reader,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        reader._connection.execute("DELETE FROM dag_run_events")  # noqa: SLF001
    replay, events = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    snapshot = build_dag_live_snapshot(replay=replay, recent_events=events)
    assert snapshot["nodes"][0]["admission"]["accepted"] is True
    assert snapshot["nodes"][0]["scheduler"]["state"] == "settled"


def test_reader_rejects_missing_store_and_invalid_ranges(tmp_path: Path) -> None:
    with pytest.raises(DagRunStoreError, match="dag_run_store_missing"):
        SqliteDagRunReader(tmp_path / "missing.sqlite3")
    database = _durable_run(tmp_path)
    with (
        SqliteDagRunReader(database) as reader,
        pytest.raises(DagRunStoreError, match="dag_viewer_event_range_invalid"),
    ):
        reader.load_events("run-1", after_sequence=-1)


def test_reader_blocks_unknown_store_schema_and_corrupt_event(tmp_path: Path) -> None:
    database = _durable_run(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE dag_store_meta SET value = '999' WHERE key = 'schema_version'")
    with pytest.raises(DagRunStoreError, match="dag_run_store_schema_mismatch"):
        SqliteDagRunReader(database)

    database.unlink()
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    database = _durable_run(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER dag_run_events_no_update")
        connection.execute(
            "UPDATE dag_run_events SET payload_sha256 = 'sha256:corrupt' WHERE seq = 1"
        )
    with (
        SqliteDagRunReader(database) as reader,
        pytest.raises(DagRunStoreError, match="dag_run_event_hash_mismatch"),
    ):
        reader.load_events("run-1")


def test_runtime_pass_text_cannot_accept_node(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    replay, _ = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    running = replace(replay, run_status="RUNNING", node_states=(("node", "running"),))
    snapshot = build_dag_live_snapshot(
        replay=running,
        recent_events=({"event_type": "runtime_event_appended", "pane_text": "PASS done"},),
    )
    node = snapshot["nodes"][0]
    assert node["scheduler"]["state"] == "running"
    assert node["admission"]["accepted"] is False
    assert node["admission"]["state"] == "awaiting_receipt"


def test_active_attempt_state_is_visible_without_accepting_node(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    replay, _ = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    active = replace(
        replay,
        run_status="RUNNING",
        node_states=(("node", "pending"),),
        attempts=(DagReplayAttempt("node", 2, "attempt-2", "STAGED", "STARTED"),),
        results=(),
    )
    node = build_dag_live_snapshot(replay=active, recent_events=())["nodes"][0]
    assert node["scheduler"] == {"state": "validating", "attempt": 2, "max_attempts": 1}
    assert node["admission"]["state"] == "validating"
    assert node["admission"]["accepted"] is False


def test_manifest_blocks_modified_or_malformed_retained_source(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    replay, _ = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    source_path = tmp_path / "source-dag.json"
    source_path.write_text(json.dumps({"schema": "tau.generic_dag_spec.v1", "changed": True}))
    with pytest.raises(RuntimeError, match="dag_source_artifact_hash_mismatch"):
        build_dag_view_manifest(replay=replay, run_dir=tmp_path)
    source_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dag_source_artifact_invalid"):
        build_dag_view_manifest(replay=replay, run_dir=tmp_path)


def test_live_events_are_redacted_and_bounded(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    replay, _ = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    payload = build_dag_live_events(
        replay=replay,
        events=(
            {
                "seq": 1,
                "payload": {"authorization": "Bearer secret", "stdout": "x" * 9000},
            },
        ),
        after_sequence=0,
        limit=200,
    )
    assert payload["events"][0]["payload"]["authorization"] == "[REDACTED]"
    assert "Bearer secret" not in json.dumps(payload)
    assert payload["redaction"] == {
        "redacted": True,
        "redacted_paths": ["$.events[0].payload.authorization"],
        "truncated": True,
    }
