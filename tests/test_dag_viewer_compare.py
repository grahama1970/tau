"""Exactly-two authoritative DAG comparison tests."""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import (
    CORRECTION_JOURNAL_ENTRY_SCHEMA,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_viewer.compare import MAX_COMPARISON_BYTES, _comparison
from tau_coding.dag_viewer.server import RunningDagViewerServer, create_dag_viewer_server


def _server(
    tmp_path: Path, *, include_verified_correction: bool = False
) -> tuple[RunningDagViewerServer, threading.Thread]:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "compare-run",
            "run_dir": str(tmp_path),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "max_attempts": 2,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )

    def execute(
        _node: Any, _inputs: tuple[dict[str, Any], ...], execution: DagNodeAttempt
    ) -> dict[str, Any]:
        if execution.attempt == 1:
            return {
                "node_id": "worker",
                "status": "BLOCKED",
                "verdict": "TRANSIENT_FAILURE",
            }
        return {"node_id": "worker", "status": "PASS", "verdict": "PASS"}

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="compare-run",
        )
        lease = store.acquire_run(
            plan=plan, run_id="compare-run", owner_id="correction-writer"
        )
        incident = {
            "schema": "tau.correction_incident.v1",
            "incident_id": "incident-compare",
            "run_id": "compare-run",
            "dag_id": plan.plan_id,
            "node_id": "worker",
            "attempt": 1,
            "trigger": "provider_auth_required",
            "classification": "NON_RETRYABLE",
            "goal_hash": plan.runtime_goal_hash,
            "observed_state": {"auth": "EXPIRED"},
        }
        store.append_correction_event(
            lease,
            event_key="correction:incident-compare:requested",
            incident_id="incident-compare",
            payload={
                "schema": CORRECTION_JOURNAL_ENTRY_SCHEMA,
                "incident_id": "incident-compare",
                "state": "REQUESTED",
                "incident": incident,
            },
        )
        if include_verified_correction:
            intent = {"action": "repair_auth", "action_id": "repair-auth"}
            action_receipt = {"action_id": "repair-auth", "result": {"repaired": True}}
            for state, extra in (
                ("INTENT_COMMITTED", {"intent": intent}),
                ("STARTED", {"intent": intent}),
                ("APPLIED", {"intent": intent, "action_receipt": action_receipt}),
                (
                    "VERIFIED",
                    {
                        "verification": {
                            "schema": "tau.correction_verification.v1",
                            "incident_id": "incident-compare",
                            "action_id": "repair-auth",
                            "verified": True,
                            "result": {"verified": True},
                            "result_sha256": "sha256:" + "0" * 64,
                        }
                    },
                ),
            ):
                store.append_correction_event(
                    lease,
                    event_key=f"correction:incident-compare:{state.lower()}",
                    incident_id="incident-compare",
                    payload={
                        "schema": CORRECTION_JOURNAL_ENTRY_SCHEMA,
                        "incident_id": "incident-compare",
                        "state": state,
                        **extra,
                    },
                )
        else:
            store.append_correction_event(
                lease,
                event_key="correction:incident-compare:human-routed",
                incident_id="incident-compare",
                payload={
                    "schema": CORRECTION_JOURNAL_ENTRY_SCHEMA,
                    "incident_id": "incident-compare",
                    "state": "HUMAN_ROUTED",
                    "reason": "human_required",
                },
            )
        store.release_lease(lease)
    server = create_dag_viewer_server(run_dir=tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server: RunningDagViewerServer, path: str) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
    connection.request("GET", path)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def test_sequence_and_same_node_attempt_comparison_use_exactly_two_sides(
    tmp_path: Path,
) -> None:
    server, thread = _server(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "dag-run.sqlite3") as connection:
            sequences = [
                int(row[0])
                for row in connection.execute(
                    "SELECT seq FROM dag_run_events WHERE run_id = ? ORDER BY seq",
                    ("compare-run",),
                )
            ]
        status, sequence = _get(
            server,
            f"/api/v1/compare?kind=SEQUENCE_PAIR&at_sequence={sequences[-1]}"
            f"&left_sequence={sequences[0]}"
            f"&right_sequence={sequences[-1]}",
        )
        assert status == 200
        assert sequence["kind"] == "SEQUENCE_PAIR"
        assert sequence["left"]["sequence"] == sequences[0]
        assert sequence["right"]["sequence"] == sequences[-1]
        assert sequence["changes"]

        status, attempts = _get(
            server,
            "/api/v1/compare?kind=ATTEMPT_PAIR&node_id=worker"
            f"&at_sequence={sequences[-1]}&left_attempt=1&right_attempt=2",
        )
        assert status == 200
        assert attempts["left"]["projection"]["state"] == "RETRY_SCHEDULED"
        assert attempts["right"]["projection"]["state"] == "SETTLED"
        assert attempts["left"]["projection"]["metrics"]["cost"]["state"] == "NOT_RECORDED"

        status, correction = _get(
            server,
            f"/api/v1/compare?kind=CORRECTION_BEFORE_AFTER&at_sequence={sequences[-1]}"
            "&incident_id=incident-compare",
        )
        assert status == 200
        assert correction["left"]["projection"]["state"] == "REQUESTED"
        assert correction["right"]["projection"]["state"] == "HUMAN_ROUTED"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_comparison_rejects_third_side_identical_and_invalid_attempts(tmp_path: Path) -> None:
    server, thread = _server(tmp_path)
    try:
        paths = (
            "/api/v1/compare?kind=ATTEMPT_PAIR&at_sequence=1&node_id=worker&left_attempt=1"
            "&right_attempt=2&third_attempt=3",
            "/api/v1/compare?kind=ATTEMPT_PAIR&at_sequence=1&node_id=worker&left_attempt=1&right_attempt=1",
            "/api/v1/compare?kind=ATTEMPT_PAIR&at_sequence=1&node_id=other&left_attempt=1&right_attempt=2",
            "/api/v1/compare?kind=SEQUENCE_PAIR&at_sequence=1&left_sequence=1&right_sequence=999999",
            "/api/v1/compare?kind=SEQUENCE_PAIR&left_sequence=1&right_sequence=2",
        )
        for path in paths:
            status, payload = _get(server, path)
            assert status == 409
            assert payload["status"] == "BLOCKED"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_historical_comparison_cannot_disclose_future_attempt_or_correction(
    tmp_path: Path,
) -> None:
    server, thread = _server(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "dag-run.sqlite3") as connection:
            events = [
                (int(seq), str(event_type), json.loads(payload))
                for seq, event_type, payload in connection.execute(
                    "SELECT seq, event_type, payload_json FROM dag_run_events "
                    "WHERE run_id = ? ORDER BY seq",
                    ("compare-run",),
                )
            ]
        attempt_two_sequence = next(
                seq
                for seq, event_type, payload in events
                if event_type == "attempt_reserved"
                and payload.get("attempt") == 2
        )
        requested_sequence = next(
            seq
            for seq, event_type, payload in events
            if event_type == "correction_state_committed"
            and payload.get("state") == "REQUESTED"
        )
        status, _ = _get(
            server,
            f"/api/v1/compare?kind=ATTEMPT_PAIR&at_sequence={attempt_two_sequence - 1}"
            "&node_id=worker&left_attempt=1&right_attempt=2",
        )
        assert status == 409
        status, _ = _get(
            server,
            f"/api/v1/compare?kind=CORRECTION_BEFORE_AFTER&at_sequence={requested_sequence}"
            "&incident_id=incident-compare",
        )
        assert status == 409
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_sequence_comparison_requires_exact_run_local_authoritative_prefix(
    tmp_path: Path,
) -> None:
    server, thread = _server(tmp_path)
    try:
        database = tmp_path / "dag-run.sqlite3"
        with sqlite3.connect(database) as connection:
            sequences = [
                int(row[0])
                for row in connection.execute(
                    "SELECT seq FROM dag_run_events WHERE run_id = ? ORDER BY seq",
                    ("compare-run",),
                )
            ]
            connection.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = 'dag_run_events'",
                (sequences[-1] + 1,),
            )

        # A committed global sequence for another run is not a valid prefix for this run.
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        other_plan = compile_generic_dag_plan(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "other-run",
                "run_dir": str(other_dir),
                "nodes": [
                    {
                        "node_id": "other",
                        "role": "worker",
                        "command": ["true"],
                        "receipt_path": str(other_dir / "other.json"),
                    }
                ],
            },
            source_path=other_dir / "dag.json",
        )
        with SqliteDagRunStore(database) as store:
            run_dag_plan(
                other_plan,
                execute_node=lambda _node, _inputs, _attempt: {
                    "node_id": "other",
                    "status": "PASS",
                    "verdict": "PASS",
                },
                run_store=store,
                run_id="other-run",
            )
        with sqlite3.connect(database) as connection:
            other_sequence = int(
                connection.execute(
                    "SELECT MIN(seq) FROM dag_run_events WHERE run_id = ?", ("other-run",)
                ).fetchone()[0]
            )

        valid_left, valid_right = sequences[0], sequences[-1]
        invalid_prefixes = (
            sequences[-1] + 1,  # Deliberate global journal gap.
            other_sequence,  # Exact committed sequence owned by another run.
            other_sequence + 1000,  # Future sequence.
        )
        for at_sequence in invalid_prefixes:
            status, payload = _get(
                server,
                "/api/v1/compare?kind=SEQUENCE_PAIR"
                f"&at_sequence={at_sequence}&left_sequence={valid_left}"
                f"&right_sequence={valid_right}",
            )
            assert status == 409
            assert payload["status"] == "BLOCKED"
            assert payload["code"] == "dag_viewer_sequence_not_in_run"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_verified_correction_comparison_projects_canonical_verified_value(
    tmp_path: Path,
) -> None:
    server, thread = _server(tmp_path, include_verified_correction=True)
    try:
        status, state = _get(server, "/api/v1/state")
        assert status == 200
        status, comparison = _get(
            server,
            "/api/v1/compare?kind=CORRECTION_BEFORE_AFTER"
            f"&at_sequence={state['journal_sequence']}&incident_id=incident-compare",
        )
        assert status == 200
        assert comparison["right"]["projection"]["state"] == "VERIFIED"
        assert comparison["right"]["projection"]["verification_verified"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_comparison_is_size_bounded_and_reports_truncation() -> None:
    left = {
        "run_id": "run-1",
        "reference": {"kind": "SEQUENCE", "sequence": 1},
        "sequence": 1,
        "projection": {f"field-{index}": "left" * 400 for index in range(500)},
        "truncated": False,
    }
    right = {
        "run_id": "run-1",
        "reference": {"kind": "SEQUENCE", "sequence": 2},
        "sequence": 2,
        "projection": {f"field-{index}": "right" * 400 for index in range(500)},
        "truncated": False,
    }
    comparison = _comparison(
        kind="SEQUENCE_PAIR", left=left, right=right, as_of_sequence=2
    )
    assert comparison["truncated"] is True
    assert len(comparison["changes"]) <= 200
    assert len(json.dumps(comparison).encode()) <= MAX_COMPARISON_BYTES


def test_http_query_cursor_retrieves_second_page(tmp_path: Path) -> None:
    server, thread = _server(tmp_path)
    try:
        status, first = _get(server, "/api/v1/query?entity_kind=EVENT&limit=1")
        assert status == 200
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        assert len(cursor) <= 2048
        status, second = _get(
            server,
            "/api/v1/query?entity_kind=EVENT&limit=1&cursor=" + quote(cursor, safe=""),
        )
        assert status == 200
        assert second["items"]
        assert second["items"][0]["entity_id"] != first["items"][0]["entity_id"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
