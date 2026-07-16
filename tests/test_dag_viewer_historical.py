"""Exact-sequence historical replay and receipt-boundary tests."""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagNodeCompletion,
    DagTransitionBatch,
    DagTransitionView,
)
from tau_coding.dag_viewer.server import RunningDagViewerServer, create_dag_viewer_server


class _ReceiptPolicy(AllSuccessTransitionPolicy):
    def __init__(self, receipt: Path) -> None:
        self.receipt = receipt

    def after_node_terminal(
        self, view: DagTransitionView, completion: DagNodeCompletion
    ) -> DagTransitionBatch:
        base = super().after_node_terminal(view, completion)
        return DagTransitionBatch(
            edge_settlements=base.edge_settlements,
            node_settlements=base.node_settlements,
            node_cancellations=base.node_cancellations,
            deadline_arms=base.deadline_arms,
            deadline_cancellations=base.deadline_cancellations,
            receipt_paths=(str(self.receipt),),
            events=base.events,
            block_run=base.block_run,
        )


def _run(tmp_path: Path) -> tuple[RunningDagViewerServer, threading.Thread]:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "historical-run",
            "run_dir": str(tmp_path),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "receipt_path": str(tmp_path / "worker.json"),
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    receipt = tmp_path / "worker.json"
    receipt.write_text(
        json.dumps({"schema": "tau.worker_receipt.v1", "status": "PASS"}),
        encoding="utf-8",
    )
    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        run_dag_plan(
            plan,
            run_store=store,
            run_id="historical-run",
            transition_policy=_ReceiptPolicy(receipt),
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
        )
    server = create_dag_viewer_server(run_dir=tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server: RunningDagViewerServer, path: str) -> tuple[int, dict[str, str], dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
    connection.request("GET", path)
    response = connection.getresponse()
    headers = dict(response.getheaders())
    payload = json.loads(response.read())
    connection.close()
    assert isinstance(payload, dict)
    return response.status, headers, payload


def test_exact_historical_prefix_is_stable_and_excludes_future_state(tmp_path: Path) -> None:
    server, thread = _run(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "dag-run.sqlite3") as connection:
            rows = connection.execute(
                "SELECT seq, event_type FROM dag_run_events "
                "WHERE run_id = 'historical-run' ORDER BY seq"
            ).fetchall()
        sequence_by_type = {event_type: int(sequence) for sequence, event_type in rows}
        created_sequence = sequence_by_type["run_created"]
        dispatched_sequence = sequence_by_type["attempt_dispatched"]
        committed_sequence = sequence_by_type["scheduler_transition_committed"]

        _, head_headers, live = _get(server, "/api/v1/state")
        _, first_headers, created = _get(
            server, f"/api/v1/state?at_sequence={created_sequence}"
        )
        _, second_headers, repeated = _get(
            server, f"/api/v1/state?at_sequence={created_sequence}"
        )
        _, _, dispatched = _get(
            server, f"/api/v1/state?at_sequence={dispatched_sequence}"
        )
        _, _, committed = _get(
            server, f"/api/v1/state?at_sequence={committed_sequence}"
        )

        assert live["view"]["mode"] == "LIVE"
        assert created["view"] == repeated["view"]
        assert created["snapshot_sha256"] == repeated["snapshot_sha256"]
        assert first_headers["ETag"] == second_headers["ETag"]
        assert created["nodes"][0]["scheduler"]["state"] == "pending"
        assert created["nodes"][0]["admission"]["accepted"] is False
        assert dispatched["nodes"][0]["scheduler"]["state"] == "running"
        assert dispatched["nodes"][0]["admission"]["accepted"] is False
        assert committed["nodes"][0]["admission"]["accepted"] is True
        assert int(head_headers["X-Tau-Journal-Head-Sequence"]) == int(
            first_headers["X-Tau-Journal-Head-Sequence"]
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_historical_manifest_and_receipt_are_sequence_bounded(tmp_path: Path) -> None:
    server, thread = _run(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "dag-run.sqlite3") as connection:
            rows = connection.execute(
                "SELECT seq, event_type FROM dag_run_events "
                "WHERE run_id = 'historical-run' ORDER BY seq"
            ).fetchall()
        created_sequence = next(int(seq) for seq, kind in rows if kind == "run_created")
        committed_sequence = next(
            int(seq) for seq, kind in rows if kind == "scheduler_transition_committed"
        )
        _, _, early = _get(
            server, f"/api/v1/manifest?at_sequence={created_sequence}"
        )
        _, _, later = _get(
            server, f"/api/v1/manifest?at_sequence={committed_sequence}"
        )
        assert early["receipt_index"] == []
        receipt_id = later["receipt_index"][0]["receipt_id"]
        status, _, blocked = _get(
            server,
            f"/api/v1/receipts/{receipt_id}?at_sequence={created_sequence}",
        )
        assert status == 404
        assert blocked["code"] == "dag_viewer_receipt_not_found"
        status, _, receipt = _get(
            server,
            f"/api/v1/receipts/{receipt_id}?at_sequence={committed_sequence}",
        )
        assert status == 200
        assert receipt["receipt_id"] == receipt_id
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_invalid_sequence_queries_block_without_mutation(tmp_path: Path) -> None:
    server, thread = _run(tmp_path)
    database = tmp_path / "dag-run.sqlite3"
    try:
        with sqlite3.connect(database) as connection:
            before = connection.execute(
                "SELECT COUNT(*), MAX(seq) FROM dag_run_events"
            ).fetchone()
            future = int(before[1]) + 100
        for path in (
            "/api/v1/state?at_sequence=0",
            f"/api/v1/state?at_sequence={future}",
            "/api/v1/state?at_sequence=1&at_sequence=2",
            "/api/v1/state?unknown=1",
        ):
            status, _, payload = _get(server, path)
            assert status == 409
            assert payload["status"] == "BLOCKED"
        with sqlite3.connect(database) as connection:
            after = connection.execute(
                "SELECT COUNT(*), MAX(seq) FROM dag_run_events"
            ).fetchone()
        assert after == before
    finally:
        server.shutdown()
        thread.join(timeout=2)
