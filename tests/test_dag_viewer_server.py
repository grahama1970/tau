"""Live loopback HTTP checks for the read-only DAG viewer server."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagNodeCompletion,
    DagTransitionBatch,
    DagTransitionView,
)
from tau_coding.dag_viewer.server import (
    RunningDagViewerServer,
    _host_header_matches_server,
    create_dag_viewer_server,
)


class _ReceiptTransitionPolicy(AllSuccessTransitionPolicy):
    def __init__(self, receipt_path: Path) -> None:
        self.receipt_path = receipt_path

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
            receipt_paths=(str(self.receipt_path),),
            events=base.events,
            block_run=base.block_run,
        )


def _durable_run(tmp_path: Path) -> None:
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "viewer-run",
        "run_dir": str(tmp_path),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": ["true"],
                "receipt_path": str(tmp_path / "worker-receipt.json"),
            }
        ],
    }
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    receipt_path = tmp_path / "worker-receipt.json"
    receipt_path.write_text(
        json.dumps({"schema": "tau.worker_receipt.v1", "status": "PASS"}),
        encoding="utf-8",
    )
    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        run_dag_plan(
            plan,
            run_store=store,
            run_id="viewer-run",
            transition_policy=_ReceiptTransitionPolicy(receipt_path),
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
        )


@pytest.fixture
def viewer_server(tmp_path: Path) -> tuple[RunningDagViewerServer, threading.Thread]:
    _durable_run(tmp_path)
    server = create_dag_viewer_server(run_dir=tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, thread
    if thread.is_alive():
        server.shutdown()
        thread.join(timeout=2)
    else:
        server.httpd.server_close()


def _request(
    server: RunningDagViewerServer,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, body


def _json(body: bytes) -> dict[str, Any]:
    payload = json.loads(body)
    assert isinstance(payload, dict)
    return payload


def test_server_is_loopback_read_only_and_serves_declared_contracts(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    database = server.application.run_dir / "dag-run.sqlite3"
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT (SELECT COUNT(*) FROM dag_run_events), lease_owner, lease_epoch "
            "FROM dag_runs WHERE run_id = 'viewer-run'"
        ).fetchone()
    status, headers, body = _request(server, "GET", "/")
    assert status == 200
    assert b"read-only API is operational" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    for path, schema in (
        ("/api/v1/capabilities", "tau.dag_viewer_capabilities.v1"),
        ("/api/v1/manifest", "tau.dag_view_manifest.v1"),
        ("/api/v1/state", "tau.dag_live_snapshot.v1"),
        ("/api/v1/events?after_sequence=0&limit=20", "tau.dag_live_event.v1"),
    ):
        status, headers, body = _request(server, "GET", path)
        assert status == 200
        assert _json(body)["schema"] == schema
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        status, headers, body = _request(server, method, "/api/v1/state")
        assert status == 405
        assert headers["Allow"] == "GET"
        assert _json(body)["code"] == "dag_viewer_method_not_allowed"
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT (SELECT COUNT(*) FROM dag_run_events), lease_owner, lease_epoch "
            "FROM dag_runs WHERE run_id = 'viewer-run'"
        ).fetchone()
    assert after == before


def test_state_etag_and_concurrent_reads_are_consistent(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    status, headers, first = _request(server, "GET", "/api/v1/state")
    assert status == 200
    etag = headers["ETag"]
    status, cached_headers, body = _request(
        server, "GET", "/api/v1/state", headers={"If-None-Match": etag}
    )
    assert status == 304
    assert body == b""
    assert cached_headers["Cache-Control"] == "no-store"
    assert "Content-Length" not in cached_headers
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: _request(server, "GET", "/api/v1/state"), range(16)))
    assert {headers["ETag"] for _, headers, _ in responses} == {etag}
    assert {body for _, _, body in responses} == {first}


def test_event_ranges_are_bounded_and_invalid_ranges_are_structured(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    status, _, body = _request(server, "GET", "/api/v1/events?before_sequence=5&limit=2")
    assert status == 200
    events = _json(body)["events"]
    assert len(events) <= 2
    assert all(int(event["seq"]) < 5 for event in events)
    status, _, body = _request(server, "GET", "/api/v1/events?limit=999999")
    assert status == 409
    assert _json(body)["code"] == "dag_viewer_event_range_invalid"


def test_wal_commit_becomes_visible_without_server_mutation(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    database = server.application.run_dir / "dag-run.sqlite3"
    before = _json(_request(server, "GET", "/api/v1/state")[2])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE dag_runs SET status = 'BLOCKED', verdict = 'TEST_BLOCK'")
    after = _json(_request(server, "GET", "/api/v1/state")[2])
    assert before["snapshot_sha256"] != after["snapshot_sha256"]
    assert after["run_status"] == "BLOCKED"


def test_receipts_are_allowlisted_and_tamper_evident(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    manifest = _json(_request(server, "GET", "/api/v1/manifest")[2])
    entry = manifest["receipt_index"][0]
    status, _, body = _request(server, "GET", f"/api/v1/receipts/{entry['receipt_id']}")
    assert status == 200
    assert _json(body)["source_sha256"] == entry["sha256"]
    status, _, body = _request(server, "GET", "/api/v1/receipts/%2e%2e%2fetc%2fpasswd")
    assert status == 404
    assert _json(body)["code"] == "dag_viewer_receipt_not_found"
    (server.application.run_dir / entry["path_display"]).write_text(
        json.dumps({"schema": entry["schema"], "status": "CHANGED"}), encoding="utf-8"
    )
    status, _, body = _request(server, "GET", f"/api/v1/receipts/{entry['receipt_id']}")
    assert status == 409
    assert _json(body)["code"] == "dag_viewer_receipt_hash_mismatch"


def test_host_header_must_match_bound_loopback_authority(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    status, _, body = _request(
        server,
        "GET",
        "/api/v1/state",
        headers={"Host": f"attacker.example:{server.port}"},
    )
    assert status == 421
    assert _json(body)["code"] == "dag_viewer_host_forbidden"


def test_localhost_authority_is_preserved(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    server = create_dag_viewer_server(run_dir=tmp_path, host="localhost", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = _request(
            server,
            "GET",
            "/api/v1/state",
            headers={"Host": f"localhost:{server.port}"},
        )
        assert status == 200
        assert _json(body)["schema"] == "tau.dag_live_snapshot.v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_default_http_port_may_be_omitted_from_authority() -> None:
    assert _host_header_matches_server("127.0.0.1", host="127.0.0.1", port=80)
    assert _host_header_matches_server("[::1]", host="::1", port=80)
    assert not _host_header_matches_server("127.0.0.1", host="127.0.0.1", port=8080)


def test_store_failure_is_structured_and_shutdown_releases_port(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, thread = viewer_server
    port = server.port
    (server.application.run_dir / "dag-run.sqlite3").unlink()
    status, _, body = _request(server, "GET", "/api/v1/state")
    assert status == 409
    error = _json(body)
    assert error["schema"] == "tau.dag_viewer_error.v1"
    assert error["code"] == "dag_run_store_missing"
    assert "Traceback" not in body.decode()
    server.shutdown()
    thread.join(timeout=2)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_corrupt_event_is_structured_without_sqlite_details(
    viewer_server: tuple[RunningDagViewerServer, threading.Thread],
) -> None:
    server, _ = viewer_server
    database = server.application.run_dir / "dag-run.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER dag_run_events_no_update")
        connection.execute(
            "UPDATE dag_run_events SET payload_sha256 = 'sha256:corrupt' WHERE seq = 1"
        )
    status, _, body = _request(server, "GET", "/api/v1/state")
    assert status == 409
    error = _json(body)
    assert error["code"] == "dag_run_event_hash_mismatch"
    assert "sqlite" not in error["message"].casefold()


def test_non_loopback_blocks_before_bind(tmp_path: Path) -> None:
    _durable_run(tmp_path)
    with pytest.raises(RuntimeError, match="dag_viewer_non_loopback_forbidden"):
        create_dag_viewer_server(run_dir=tmp_path, host="0.0.0.0", port=0)
