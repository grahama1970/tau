"""Read-only store and live projection contract checks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.correction import (
    CorrectionActionIntent,
    CorrectionIncident,
    run_correction_transaction,
)
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


def _transaction_run(tmp_path: Path) -> tuple[object, tuple[dict[str, object], ...]]:
    work_order = tmp_path / "work-order.json"
    work_order.write_text('{"task":"projection fixture"}\n', encoding="utf-8")
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "transaction-run",
            "run_dir": str(tmp_path),
            "nodes": [
                {
                    "node_id": "creator",
                    "role": "producer",
                    "command": ["true"],
                    "receipt_path": str(tmp_path / "creator.json"),
                    "work_order_path": str(work_order),
                    "max_attempts": 2,
                    "transaction": {
                        "schema": "tau.generic_artifact_transaction.v1",
                        "transaction_id": "tx-creator",
                        "artifact_root": str(tmp_path / "artifacts"),
                        "producer_id": "creator",
                        "reviewer": {"reviewer_id": "reviewer", "command": ["true"]},
                    },
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    database = tmp_path / "dag-run.sqlite3"

    def initialize(lease) -> None:  # type: ignore[no-untyped-def]
        with SqliteDagRunStore(database) as writer:
            for attempt, phase, evidence in (
                (1, "producer_completed", {"candidate_manifest_sha256": "sha256:first"}),
                (1, "validator_completed", {"status": "PASS"}),
                (1, "reviewer_completed", {"verdict": "REVISE"}),
                (1, "revision_committed", {"instruction": "address finding"}),
                (2, "producer_completed", {"candidate_manifest_sha256": "sha256:second"}),
                (2, "validator_completed", {"status": "PASS"}),
                (2, "reviewer_completed", {"verdict": "PASS"}),
                (
                    2,
                    "accepted_manifest_written",
                    {"accepted_manifest_sha256": "sha256:accepted"},
                ),
            ):
                writer.append_diagnostic_event(
                    lease,
                    event_key=f"transaction:creator:{attempt}:{phase}",
                    node_id="creator",
                    payload={
                        "schema": "tau.dag_diagnostic_event.v1",
                        "diagnostic_kind": "generic_artifact_transaction_progress",
                        "node_id": "creator",
                        "scheduler_attempt": 1,
                        "attempt": attempt,
                        "phase": phase,
                        "evidence": evidence,
                        "authority": "diagnostic_only",
                    },
                )

    with SqliteDagRunStore(database) as store:
        run_dag_plan(
            plan,
            run_store=store,
            run_id="transaction-run",
            on_lease_acquired=initialize,
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempts": [{"attempt": 1}, {"attempt": 2, "review_verdict": "PASS"}],
                "accepted_manifest_sha256": "sha256:accepted",
            },
        )
    return load_dag_replay(run_dir=tmp_path, run_id="transaction-run")


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


def test_reader_snapshot_is_consistent_across_concurrent_wal_commit(tmp_path: Path) -> None:
    database = _durable_run(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE dag_runs SET status = 'RUNNING', verdict = NULL")
    with SqliteDagRunReader(database) as reader, reader.snapshot():
        assert reader.load_run_record("run-1").status == "RUNNING"
        with sqlite3.connect(database) as writer:
            writer.execute("UPDATE dag_runs SET status = 'PASS', verdict = 'PASS'")
        assert reader.load_run_record("run-1").status == "RUNNING"
    with SqliteDagRunReader(database) as reader:
        assert reader.load_run_record("run-1").status == "PASS"


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


def test_snapshot_projects_verified_correction_lineage_without_accepting_node(
    tmp_path: Path,
) -> None:
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
                    "max_attempts": 2,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    incident = CorrectionIncident.create(
        run_id="run-1",
        node_id="node",
        attempt=1,
        trigger="provider_auth_required",
        classification="RETRYABLE",
        goal_hash=plan.runtime_goal_hash,
        observed_state={"auth": "EXPIRED"},
    )
    intent = CorrectionActionIntent.create(
        incident=incident,
        capability="provider.repair_auth",
        action="refresh_local_provider_auth",
        target={"provider": "local-fixture"},
        policy_sha256="sha256:policy",
        authorized=True,
    )
    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        run_correction_transaction(
            store=store,
            lease=lease,
            incident=incident,
            intent=intent,
            apply_action=lambda _intent: {"auth": "VALID"},
            verify_action=lambda _intent, _receipt: {"verified": True},
        )
        store.release_lease(lease)

    replay, events = load_dag_replay(run_dir=tmp_path, run_id="run-1")
    snapshot = build_dag_live_snapshot(replay=replay, recent_events=events)

    assert snapshot["corrections"][0]["state"] == "VERIFIED"
    assert snapshot["nodes"][0]["correction"]["state"] == "VERIFIED"
    assert snapshot["nodes"][0]["admission"]["accepted"] is False
    assert snapshot["attention_items"] == []


def test_transaction_diagnostics_show_revisions_but_cannot_accept_node(tmp_path: Path) -> None:
    replay, events = _transaction_run(tmp_path)
    pending = replace(
        replay,
        run_status="RUNNING",
        node_states=(("creator", "running"),),
        results=(),
    )
    node = build_dag_live_snapshot(replay=pending, recent_events=events)["nodes"][0]

    assert node["admission"]["accepted"] is False
    assert node["transaction"]["state"] == "AWAITING_RECEIPT"
    assert node["transaction"]["current_attempt"] == 2
    assert node["transaction"]["max_attempts"] == 2
    assert node["transaction"]["attempts"][0]["reviewer_verdict"] == "REVISE"
    assert node["transaction"]["attempts"][1]["reviewer_verdict"] == "PASS"
    assert node["transaction"]["accepted_manifest_sha256"] == "sha256:accepted"


def test_transaction_projection_excludes_prior_scheduler_attempt_diagnostics(
    tmp_path: Path,
) -> None:
    replay, events = _transaction_run(tmp_path)
    producer_event = next(
        event
        for event in events
        if event.get("event_key") == "transaction:creator:1:producer_completed"
    )
    current_event = {
        **producer_event,
        "seq": max(int(event["seq"]) for event in events) + 1,
        "event_key": "transaction:creator:2:1:producer_completed",
        "payload": {
            **producer_event["payload"],
            "scheduler_attempt": 2,
            "evidence": {"candidate_manifest_sha256": "sha256:current"},
        },
    }
    current = replace(
        replay,
        run_status="RUNNING",
        node_states=(("creator", "running"),),
        attempts=(DagReplayAttempt("creator", 2, "outer-attempt-2", "DISPATCHED", "STARTED"),),
        results=(),
    )

    transaction = build_dag_live_snapshot(
        replay=current,
        recent_events=events + (current_event,),
    )["nodes"][0]["transaction"]

    assert transaction["attempts"] == [
        {
            "attempt": 1,
            "producer_state": "PASS",
            "candidate_manifest_sha256": "sha256:current",
        }
    ]
    assert "reviewer_verdict" not in transaction["attempts"][0]


def test_transaction_projection_accepts_only_after_committed_scheduler_transition(
    tmp_path: Path,
) -> None:
    replay, events = _transaction_run(tmp_path)
    first = build_dag_live_snapshot(replay=replay, recent_events=events)
    second_replay, second_events = load_dag_replay(
        run_dir=tmp_path, run_id="transaction-run"
    )
    second = build_dag_live_snapshot(replay=second_replay, recent_events=second_events)

    assert first["nodes"][0]["admission"]["accepted"] is True
    assert first["nodes"][0]["transaction"]["state"] == "ACCEPTED"
    assert first == second


@pytest.mark.parametrize(
    ("committed_state", "transaction_state"),
    (("blocked", "BLOCKED"), ("failed", "REJECTED"), ("timed_out", "REJECTED")),
)
def test_transaction_projection_rejects_terminal_non_accepted_outcomes(
    tmp_path: Path,
    committed_state: str,
    transaction_state: str,
) -> None:
    replay, events = _transaction_run(tmp_path)
    terminal = replace(
        replay,
        run_status="BLOCKED",
        node_states=(("creator", committed_state),),
    )

    node = build_dag_live_snapshot(replay=terminal, recent_events=events)["nodes"][0]

    assert node["admission"]["accepted"] is False
    assert node["admission"]["state"] == "rejected"
    assert node["transaction"]["state"] == transaction_state


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
    assert payload["events"][0]["payload"]["stdout"] == "[REDACTED:RAW_OUTPUT]"
    assert "Bearer secret" not in json.dumps(payload)
    assert payload["redaction"] == {
        "redacted": True,
        "redacted_paths": [
            "$.events[0].payload.authorization",
            "$.events[0].payload.stdout",
        ],
        "truncated": False,
    }


def test_transaction_projection_uses_full_journal_but_bounds_visible_timeline(
    tmp_path: Path,
) -> None:
    replay, events = _transaction_run(tmp_path)
    last_sequence = max(int(event["seq"]) for event in events)
    filler = tuple(
        {
            "seq": last_sequence + offset,
            "event_key": f"diagnostic:filler:{offset}",
            "event_type": "scheduler_event_emitted",
            "entity_type": "scheduler",
            "entity_id": replay.run_id,
            "attempt_id": None,
            "lease_epoch": replay.lease_epoch,
            "payload": {"event": "filler", "offset": offset},
        }
        for offset in range(1, 251)
    )

    snapshot = build_dag_live_snapshot(replay=replay, recent_events=events + filler)
    transaction = snapshot["nodes"][0]["transaction"]

    assert [attempt["attempt"] for attempt in transaction["attempts"]] == [1, 2]
    assert transaction["attempts"][0]["reviewer_verdict"] == "REVISE"
    assert transaction["attempts"][0]["revision_instruction"] == "address finding"
    assert transaction["attempts"][1]["reviewer_verdict"] == "PASS"
    assert len(snapshot["recent_events"]) == 200
    assert snapshot["recent_events"][0]["payload"]["offset"] == 51
    assert snapshot["recent_events"][-1]["payload"]["offset"] == 250
