import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from tau_agent import AgentEndEvent, AgentStartEvent
from tau_coding.loop_monitor import (
    check_loop_receipt_monitor_contract,
    create_loop_receipt_monitor_server,
    loop_receipt_monitor_response,
    loop_receipt_monitor_stream,
)
from tau_coding.loop_receipt import LoopReceiptRecorder


def test_loop_receipt_monitor_routes_summary_evidence_and_events(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    summary = loop_receipt_monitor_response(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/summary",
    )
    evidence = loop_receipt_monitor_response(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/transport-dag-evidence",
    )
    events = loop_receipt_monitor_response(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/events",
    )
    peer = loop_receipt_monitor_response(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/peer-message",
    )

    assert summary.status_code == 200
    assert summary.payload["schema"] == "loop2.summary.v1"
    assert summary.payload["receipt"]["status"] == "PASS"  # type: ignore[index]
    assert summary.payload["summary"]["found"] is True  # type: ignore[index]
    assert evidence.status_code == 200
    assert evidence.payload["schema"] == "ux_lab.transport_dag_run_evidence.v1"
    assert events.status_code == 200
    assert events.payload["schema"] == "loop2.events.v1"
    assert {row["schema"] for row in events.payload["events"]} == {  # type: ignore[index]
        "loop2.event.v1"
    }
    assert [row["event_type"] for row in events.payload["events"]] == [  # type: ignore[index]
        "agent_start",
        "agent_end",
    ]
    assert peer.status_code == 200
    assert peer.payload["schema"] == "tau.loop_harness_peer_message.v1"
    assert peer.payload["producer"]["run_id"] == recorder.run.run_id  # type: ignore[index]
    assert peer.payload["schemas"]["transport_dag_evidence"] == (  # type: ignore[index]
        "ux_lab.transport_dag_run_evidence.v1"
    )
    assert peer.payload["switchboard"]["from"] == "tau"  # type: ignore[index]
    assert peer.payload["switchboard"]["metadata"]["run_id"] == recorder.run.run_id  # type: ignore[index]
    assert "does_not_prove" in peer.payload["switchboard"]["metadata"]["claims"]  # type: ignore[index]


def test_loop_receipt_monitor_summary_includes_tau_sanitization_sidecar(
    tmp_path: Path,
) -> None:
    recorder = _complete_receipt_run(tmp_path)
    sidecar = {
        "schema": "tau.loop2_delegated_artifact_sanitization.v1",
        "ran": True,
        "artifact": str(recorder.run.run_dir / "tau-sanitization.json"),
        "changed_artifacts": ["contract.json"],
        "redacted_keys": ["contract.scillm.api_key"],
        "filtered_changed_files": 0,
    }
    (recorder.run.run_dir / "tau-sanitization.json").write_text(
        json.dumps(sidecar),
        encoding="utf-8",
    )

    summary = loop_receipt_monitor_response(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/summary",
    )

    assert summary.status_code == 200
    assert summary.payload["summary"]["artifacts"]["tau_sanitization"] == str(  # type: ignore[index]
        recorder.run.run_dir / "tau-sanitization.json"
    )
    assert summary.payload["summary"]["tau_sanitization"] == sidecar  # type: ignore[index]


def test_loop_receipt_monitor_contract_check_accepts_complete_run(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    result = check_loop_receipt_monitor_contract(recorder.run.run_dir)

    assert result.ok is True
    assert result.checked_endpoints == (
        "summary",
        "transport-dag-evidence",
        "events",
        "events/stream",
        "peer-message",
    )
    assert result.errors == ()


def test_loop_receipt_monitor_contract_check_fails_closed_for_missing_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "incomplete-run"
    run_dir.mkdir()

    result = check_loop_receipt_monitor_contract(run_dir)

    assert result.ok is False
    assert result.checked_endpoints == ()
    assert len(result.errors) == 5
    assert result.errors[0].startswith("summary: HTTP 404")
    assert result.errors[1].startswith("transport-dag-evidence: HTTP 404")
    assert result.errors[2].startswith("events: HTTP 404")
    assert result.errors[3].startswith("events/stream: HTTP 404")
    assert result.errors[4].startswith("peer-message: HTTP 404")


def test_loop_receipt_monitor_contract_check_rejects_bad_evidence_payload(
    tmp_path: Path,
) -> None:
    recorder = _complete_receipt_run(tmp_path)
    evidence = json.loads(recorder.run.transport_dag_evidence_path.read_text())
    evidence["schema"] = "bad.schema"
    recorder.run.transport_dag_evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = check_loop_receipt_monitor_contract(recorder.run.run_dir)

    assert result.ok is False
    assert result.checked_endpoints == (
        "summary",
        "events",
        "events/stream",
        "peer-message",
    )
    assert result.errors == (
        "transport-dag-evidence: schema mismatch",
    )


def test_loop_receipt_monitor_rejects_wrong_run_id(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    response = loop_receipt_monitor_response(
        recorder.run.run_dir,
        "/api/loop2/runs/other-run/summary",
    )

    assert response.status_code == 404
    assert response.payload == {"detail": "run id not served by this monitor"}


def test_loop_receipt_monitor_stream_replays_events(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    response = loop_receipt_monitor_stream(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/events/stream",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert body.count("event: loop2_event") == 2
    assert '"event_id": "run-monitor:0001:tau"' in body
    assert '"event_id": "run-monitor:0002:tau"' in body
    assert '"event_type": "agent_start"' in body
    assert '"event_type": "agent_end"' in body
    assert '"schema": "loop2.event.v1"' in body
    assert "event: end" in body
    assert '"reason": "events_available"' in body


def test_loop_receipt_monitor_stream_honors_after_sequence(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    response = loop_receipt_monitor_stream(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/events/stream?after_sequence=1",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert body.count("event: loop2_event") == 1
    assert '"event_id": "run-monitor:0001:tau"' not in body
    assert '"event_id": "run-monitor:0002:tau"' in body
    assert '"after_sequence": 1' in body
    assert '"event_count": 1' in body


def test_loop_receipt_monitor_stream_times_out_without_new_events(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)

    response = loop_receipt_monitor_stream(
        recorder.run.run_dir,
        f"/api/loop2/runs/{recorder.run.run_id}/events/stream"
        "?after_sequence=2&timeout_s=0.01&poll_interval_s=0.01",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "event: loop2_event" not in body
    assert "event: end" in body
    assert '"reason": "timeout"' in body
    assert '"event_count": 0' in body


def test_loop_receipt_monitor_stream_fails_closed_for_missing_events(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "missing-events-run"
    run_dir.mkdir()

    response = loop_receipt_monitor_stream(
        run_dir,
        "/api/loop2/runs/missing-events-run/events/stream",
    )

    assert response.status_code == 404
    assert response.body.decode() == (
        'event: error\ndata: {"detail": "events not found"}\n\n'
    )


def test_loop_receipt_monitor_summary_fails_closed_for_missing_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "incomplete-run"
    run_dir.mkdir()

    response = loop_receipt_monitor_response(
        run_dir,
        "/api/loop2/runs/incomplete-run/summary",
    )

    assert response.status_code == 404
    assert response.payload["found"] is False
    assert response.payload["missing_artifacts"] == [
        "contract",
        "events",
        "current_state",
        "transport_dag_evidence",
        "final_receipt",
        "node_result",
    ]


def test_loop_receipt_monitor_http_server_serves_json(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)
    server = create_loop_receipt_monitor_server(recorder.run.run_dir)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(  # noqa: S310 - local test server on ephemeral port
            f"http://{host}:{port}/api/loop2/runs/{recorder.run.run_id}/summary",
            timeout=5,
        ) as response:
            body = json.loads(response.read())

        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json"
        assert body["schema"] == "loop2.summary.v1"
        assert body["receipt"]["status"] == "PASS"

        try:
            urlopen(  # noqa: S310 - local test server on ephemeral port
                f"http://{host}:{port}/api/loop2/runs/other-run/summary",
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:  # pragma: no cover
            raise AssertionError("expected wrong run id to return HTTP 404")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loop_receipt_monitor_http_server_serves_sse_stream(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)
    server = create_loop_receipt_monitor_server(recorder.run.run_dir)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(  # noqa: S310 - local test server on ephemeral port
            f"http://{host}:{port}/api/loop2/runs/{recorder.run.run_id}/events/stream",
            timeout=5,
        ) as response:
            body = response.read().decode()

        assert response.status == 200
        assert response.headers["Content-Type"] == "text/event-stream"
        assert body.count("event: loop2_event") == 2
        assert "event: end" in body
        assert '"reason": "events_available"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loop_receipt_monitor_http_server_tails_new_sse_event(tmp_path: Path) -> None:
    recorder = _complete_receipt_run(tmp_path)
    server = create_loop_receipt_monitor_server(recorder.run.run_dir)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    body_holder: dict[str, str] = {}
    try:
        host, port = server.server_address

        def request_stream() -> None:
            with urlopen(  # noqa: S310 - local test server on ephemeral port
                f"http://{host}:{port}/api/loop2/runs/{recorder.run.run_id}/events/stream"
                "?after_sequence=2&timeout_s=2&poll_interval_s=0.01",
                timeout=5,
            ) as response:
                body_holder["body"] = response.read().decode()
                body_holder["content_type"] = response.headers["Content-Type"]

        request_thread = threading.Thread(target=request_stream)
        request_thread.start()
        time.sleep(0.05)
        recorder.record(AgentEndEvent())
        request_thread.join(timeout=5)

        assert request_thread.is_alive() is False
        assert body_holder["content_type"] == "text/event-stream"
        assert body_holder["body"].count("event: loop2_event") == 1
        assert '"event_id": "run-monitor:0003:tau"' in body_holder["body"]
        assert '"reason": "events_available"' in body_holder["body"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _complete_receipt_run(tmp_path: Path) -> LoopReceiptRecorder:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-monitor")
    checks = [
        {
            "command": "python -m pytest tests/test_loop_monitor.py -q",
            "exit_code": 0,
            "stdout_path": "checks/stdout.txt",
            "stderr_path": "checks/stderr.txt",
            "elapsed_s": 0.1,
        }
    ]
    recorder.write_contract(
        node_id="monitor-node",
        objective="Serve one receipt run.",
        repo=tmp_path,
        allowed_globs=["src/**"],
        checks=[str(checks[0]["command"])],
        max_attempts=1,
        backend="fixture",
    )
    recorder.record(AgentStartEvent())
    recorder.record(AgentEndEvent())
    recorder.write_final_receipt(
        node_id="monitor-node",
        mocked=True,
        live=False,
        checks=checks,
        proves=["Tau can serve read-only receipt summary and event replay endpoints."],
        does_not_prove=["live SSE tailing of events written after the request starts"],
    )
    recorder.write_transport_dag_evidence()
    recorder.write_node_result(
        node_id="monitor-node",
        mocked=True,
        live=False,
        checks=checks,
    )
    return recorder
