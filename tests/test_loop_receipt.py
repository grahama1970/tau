import json
from pathlib import Path

import pytest

from tau_agent import (
    AgentEndEvent,
    AgentStartEvent,
    ErrorEvent,
    MessageDeltaEvent,
)
from tau_coding.loop_receipt import (
    LOOP2_EVENT_SCHEMA,
    LOOP_RECEIPT_CONTRACT_SCHEMA,
    LOOP_RECEIPT_CURRENT_STATE_SCHEMA,
    LOOP_RECEIPT_EVENTS_SCHEMA,
    LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA,
    LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA,
    LOOP_RECEIPT_NODE_RESULT_SCHEMA,
    LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
    LoopReceiptRecorder,
    backfill_loop_receipt_artifact_index,
    build_loop_harness_peer_message,
    build_loop_peer_switchboard_emit_request,
    loop_receipt_loop2_events,
    loop_receipt_summary,
)
from tau_coding.loop_validation import (
    validate_loop2_contract_file,
    validate_loop_receipt_with_loop2_contracts,
    validate_native_loop2_run_with_contracts,
)

LOOP2_SRC = (
    Path(__file__).resolve().parents[2] / "agent-skills" / "skills" / "loop2" / "src"
)


def test_loop_receipt_recorder_creates_run_dir_and_events_jsonl(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-1")

    recorder.record(AgentStartEvent())
    recorder.record(MessageDeltaEvent(delta="hello"))
    recorder.record(AgentEndEvent())

    assert recorder.run.run_id == "run-1"
    assert recorder.run.run_dir == tmp_path / "run-1"
    assert recorder.run.contract_path == tmp_path / "run-1" / "contract.json"
    assert recorder.run.events_path == tmp_path / "run-1" / "events.jsonl"
    assert recorder.run.current_state_path == tmp_path / "run-1" / "current-state.json"
    assert (
        recorder.run.transport_dag_evidence_path
        == tmp_path / "run-1" / "transport-dag-evidence.json"
    )
    assert recorder.run.final_receipt_path == tmp_path / "run-1" / "final-receipt.json"
    assert recorder.run.node_result_path == tmp_path / "run-1" / "node-result.json"
    assert recorder.event_count == 3

    rows = [json.loads(line) for line in recorder.run.events_path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert {row["schema"] for row in rows} == {LOOP_RECEIPT_EVENTS_SCHEMA}
    assert {row["run_id"] for row in rows} == {"run-1"}
    assert [row["event"]["type"] for row in rows] == [
        "agent_start",
        "message_delta",
        "agent_end",
    ]
    assert rows[1]["event"]["delta"] == "hello"

    current_state = json.loads(recorder.run.current_state_path.read_text())
    assert current_state["schema"] == LOOP_RECEIPT_CURRENT_STATE_SCHEMA
    assert current_state["run_id"] == "run-1"
    assert current_state["state"] == "ended"
    assert current_state["event_count"] == 3
    assert current_state["last_event_type"] == "agent_end"
    assert current_state["events_path"] == str(recorder.run.events_path)


def test_loop_receipt_recorder_initializes_current_state(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-2")

    current_state = json.loads(recorder.run.current_state_path.read_text())

    assert current_state["schema"] == LOOP_RECEIPT_CURRENT_STATE_SCHEMA
    assert current_state["run_id"] == "run-2"
    assert current_state["state"] == "running"
    assert current_state["event_count"] == 0
    assert current_state["last_event_type"] is None


def test_loop_receipt_recorder_writes_fixture_contract(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-contract-1")

    contract = recorder.write_contract(
        node_id="repair-demo",
        objective="Record a fixture Tau loop contract.",
        repo=tmp_path,
        allowed_globs=["src/**", "tests/**"],
        checks=["uv run pytest tests/test_loop_receipt.py -q"],
        max_attempts=1,
        backend="fixture",
    )

    written_contract = json.loads(recorder.run.contract_path.read_text())

    assert contract == written_contract
    assert written_contract["schema"] == LOOP_RECEIPT_CONTRACT_SCHEMA
    assert written_contract["node_id"] == "repair-demo"
    assert written_contract["objective"] == "Record a fixture Tau loop contract."
    assert written_contract["repo"] == str(tmp_path)
    assert written_contract["allowed_globs"] == ["src/**", "tests/**"]
    assert written_contract["checks"] == ["uv run pytest tests/test_loop_receipt.py -q"]
    assert written_contract["max_attempts"] == 1
    assert written_contract["backend"] == "fixture"
    assert written_contract["required_changed_globs"] == []
    assert written_contract["run_root"] == str(recorder.run.run_dir.parent)
    assert "run_id" not in written_contract
    assert "created_at" not in written_contract
    assert "fixture" not in written_contract


def test_loop_receipt_recorder_writes_scillm_contract_shape(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-contract-2")

    contract = recorder.write_contract(
        node_id="repair-live",
        objective="Run one live Tau repair transaction.",
        repo=tmp_path,
        allowed_globs=["src/tau_coding/**", "tests/test_loop_receipt.py"],
        checks=["uv run pytest tests/test_loop_receipt.py -q"],
        max_attempts=1,
        backend="scillm",
        backend_config={
            "base_url": "http://127.0.0.1:4001",
            "api_key": "dev-proxy-key-123",
            "agent_id": "",
            "agent": "build",
            "mode": "workspace_write",
            "model": "opencode-go/kimi-k2.6",
            "timeout_s": 900,
        },
    )

    written_contract = json.loads(recorder.run.contract_path.read_text())

    assert contract == written_contract
    assert written_contract["schema"] == LOOP_RECEIPT_CONTRACT_SCHEMA
    assert written_contract["backend"] == "scillm"
    assert written_contract["scillm"] == {
        "base_url": "http://127.0.0.1:4001",
        "api_key": "dev-proxy-key-123",
        "agent_id": "",
        "agent": "build",
        "mode": "workspace_write",
        "model": "opencode-go/kimi-k2.6",
        "timeout_s": 900,
    }


def test_loop_receipt_recorder_marks_nonrecoverable_error_failed(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-3")

    recorder.record(ErrorEvent(message="provider failed", recoverable=False))

    current_state = json.loads(recorder.run.current_state_path.read_text())
    assert current_state["state"] == "failed"
    assert current_state["event_count"] == 1
    assert current_state["last_event_type"] == "error"


def test_loop_receipt_recorder_writes_final_receipt_with_proof_scope(
    tmp_path: Path,
) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-4")

    recorder.record(AgentStartEvent())
    recorder.record(MessageDeltaEvent(delta="hello"))
    recorder.record(AgentEndEvent())

    receipt = recorder.write_final_receipt(
        node_id="repair-demo",
        mocked=True,
        live=False,
        provider="fake",
        model="fake-model",
        checks=[
            {
                "command": "uv run pytest tests/test_loop_receipt.py -q",
                "exit_code": 0,
                "stdout_path": "checks/fixture-loop.stdout.txt",
                "stderr_path": "checks/fixture-loop.stderr.txt",
                "elapsed_s": 0.1,
            }
        ],
        changed_files=["src/tau_coding/loop_receipt.py"],
        proof_scope="one bounded Tau loop recording",
        proves=["Tau can write a final receipt artifact for one recorded run."],
        does_not_prove=[
            "Loop2 node execution",
            "live provider behavior",
            "check-runner correctness",
        ],
    )

    written_receipt = json.loads(recorder.run.final_receipt_path.read_text())

    assert receipt == written_receipt
    assert written_receipt["schema"] == LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA
    assert written_receipt["run_id"] == "run-4"
    assert written_receipt["node_id"] == "repair-demo"
    assert written_receipt["status"] == "PASS"
    assert written_receipt["mocked"] is True
    assert written_receipt["live"] is False
    assert written_receipt["proof_scope"] == "one bounded Tau loop recording"
    assert written_receipt["changed_files"] == ["src/tau_coding/loop_receipt.py"]
    assert written_receipt["artifacts"]["contract"] == str(recorder.run.contract_path)
    assert written_receipt["artifacts"]["events"] == str(recorder.run.events_path)
    assert written_receipt["artifacts"]["current_state"] == str(recorder.run.current_state_path)
    assert written_receipt["artifacts"]["transport_dag_evidence"] == str(
        recorder.run.transport_dag_evidence_path
    )
    assert written_receipt["artifacts"]["final_receipt"] == str(
        recorder.run.final_receipt_path
    )
    assert written_receipt["artifacts"]["node_result"] == str(recorder.run.node_result_path)
    assert written_receipt["scillm"] == {"provider": "fake", "model": "fake-model"}
    assert written_receipt["error"] == ""
    assert written_receipt["checks"] == [
        {
            "command": "uv run pytest tests/test_loop_receipt.py -q",
            "exit_code": 0,
            "stdout_path": "checks/fixture-loop.stdout.txt",
            "stderr_path": "checks/fixture-loop.stderr.txt",
            "elapsed_s": 0.1,
        }
    ]
    assert written_receipt["claims"] == {
        "proves": ["Tau can write a final receipt artifact for one recorded run."],
        "does_not_prove": [
            "Loop2 node execution",
            "live provider behavior",
            "check-runner correctness",
        ],
    }


def test_loop_receipt_recorder_writes_loop2_node_result(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-5")

    recorder.record(AgentStartEvent())
    recorder.record(MessageDeltaEvent(delta="patched file"))
    recorder.record(AgentEndEvent())
    recorder.write_final_receipt(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=[
            {
                "command": "uv run pytest tests/test_loop_receipt.py -q",
                "exit_code": 0,
                "stdout_path": "checks/fixture-loop.stdout.txt",
                "stderr_path": "checks/fixture-loop.stderr.txt",
                "elapsed_s": 0.1,
            }
        ],
        changed_files=["src/tau_coding/loop_receipt.py"],
        proves=["Tau can write a final receipt artifact for one recorded run."],
        does_not_prove=["Loop2 node execution"],
    )

    result = recorder.write_node_result(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=[
            {
                "command": "uv run pytest tests/test_loop_receipt.py -q",
                "exit_code": 0,
                "stdout_path": "checks/fixture-loop.stdout.txt",
                "stderr_path": "checks/fixture-loop.stderr.txt",
                "elapsed_s": 0.1,
            }
        ],
        changed_files=["src/tau_coding/loop_receipt.py"],
    )

    written_result = json.loads(recorder.run.node_result_path.read_text())

    assert result == written_result
    assert written_result["schema"] == LOOP_RECEIPT_NODE_RESULT_SCHEMA
    assert written_result["node_id"] == "repair-demo"
    assert written_result["status"] == "PASS"
    assert written_result["run_id"] == "run-5"
    assert written_result["final_receipt"] == str(recorder.run.final_receipt_path)
    assert written_result["transport_dag_evidence"] == str(
        recorder.run.transport_dag_evidence_path
    )
    assert written_result["events"] == str(recorder.run.events_path)
    assert written_result["changed_files"] == ["src/tau_coding/loop_receipt.py"]
    assert written_result["mocked"] is True
    assert written_result["live"] is False
    assert written_result["checks"] == [
        {
            "command": "uv run pytest tests/test_loop_receipt.py -q",
            "exit_code": 0,
            "stdout_path": "checks/fixture-loop.stdout.txt",
            "stderr_path": "checks/fixture-loop.stderr.txt",
            "elapsed_s": 0.1,
        }
    ]


def test_loop_receipt_recorder_writes_failed_loop2_node_result(
    tmp_path: Path,
) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-6")

    recorder.record(ErrorEvent(message="provider failed", recoverable=False))

    result = recorder.write_node_result(
        node_id="repair-failed",
        mocked=True,
        live=False,
        checks=[],
    )

    written_result = json.loads(recorder.run.node_result_path.read_text())

    assert result == written_result
    assert written_result["schema"] == LOOP_RECEIPT_NODE_RESULT_SCHEMA
    assert written_result["status"] == "FAILED"
    assert written_result["checks"] == []


def test_loop_receipt_recorder_writes_transport_dag_evidence(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-7")

    recorder.write_contract(
        node_id="repair-demo",
        objective="Record one Tau loop as Loop2 evidence.",
        repo=tmp_path,
        allowed_globs=["src/**", "tests/**"],
        checks=["uv run pytest tests/test_loop_receipt.py -q"],
        max_attempts=1,
        backend="fixture",
    )
    recorder.record(AgentStartEvent())
    recorder.record(MessageDeltaEvent(delta="patched file"))
    recorder.record(AgentEndEvent())
    recorder.write_final_receipt(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=[
            {
                "command": "uv run pytest tests/test_loop_receipt.py -q",
                "exit_code": 0,
                "stdout_path": "checks/fixture-loop.stdout.txt",
                "stderr_path": "checks/fixture-loop.stderr.txt",
                "elapsed_s": 0.1,
            }
        ],
        proves=["Tau can write TransportRoom DAG evidence for one recorded run."],
        does_not_prove=["Live Loop2 execution"],
    )

    evidence = recorder.write_transport_dag_evidence()
    written_evidence = json.loads(recorder.run.transport_dag_evidence_path.read_text())

    assert evidence == written_evidence
    assert written_evidence["schema"] == LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA
    assert written_evidence["found"] is True
    assert written_evidence["transport_run_id"] == "run-7"
    assert written_evidence["graph_id"] == "loop2:repair-demo"
    assert written_evidence["proof_path"] == str(recorder.run.final_receipt_path)
    assert written_evidence["edges"] == [
        {"from": "contract", "to": "tau_loop"},
        {"from": "tau_loop", "to": "checks"},
        {"from": "checks", "to": "receipt"},
    ]
    assert written_evidence["layers"] == [
        ["contract"],
        ["tau_loop"],
        ["checks"],
        ["receipt"],
    ]
    assert [node["id"] for node in written_evidence["nodes"]] == [
        "contract",
        "tau_loop",
        "checks",
        "receipt",
    ]
    assert [node["status"] for node in written_evidence["nodes"]] == [
        "accepted",
        "accepted",
        "accepted",
        "accepted",
    ]
    assert written_evidence["not_proven"] == ["Live Loop2 execution"]
    assert written_evidence["progress_stream"]["event_count"] == 3
    assert written_evidence["progress_stream"]["event_types"] == [
        "agent_end",
        "agent_start",
        "message_delta",
    ]
    assert written_evidence["progress_stream"]["events_path"] == str(recorder.run.events_path)
    assert written_evidence["progress_stream"]["last_event_type"] == "agent_end"


def test_loop_receipt_summary_reads_complete_run(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-8")

    recorder.write_contract(
        node_id="repair-demo",
        objective="Summarize one Tau loop receipt.",
        repo=tmp_path,
        allowed_globs=["src/**"],
        checks=["python -m pytest tests/test_loop_receipt.py -q"],
        max_attempts=1,
        backend="fixture",
    )
    recorder.record(AgentStartEvent())
    recorder.record(AgentEndEvent())
    checks = [
        {
            "command": "python -m pytest tests/test_loop_receipt.py -q",
            "exit_code": 0,
            "stdout_path": "checks/stdout.txt",
            "stderr_path": "checks/stderr.txt",
            "elapsed_s": 0.1,
        }
    ]
    recorder.write_final_receipt(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=checks,
        proves=["Tau can summarize one complete receipt run."],
        does_not_prove=["Monitor server behavior"],
    )
    recorder.write_transport_dag_evidence()
    recorder.write_node_result(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=checks,
    )

    summary = loop_receipt_summary(recorder.run.run_dir)

    assert summary["schema"] == "tau.loop_receipt.summary.v1"
    assert summary["found"] is True
    assert summary["run_id"] == "run-8"
    assert summary["node_id"] == "repair-demo"
    assert summary["status"] == "PASS"
    assert summary["mocked"] is True
    assert summary["live"] is False
    assert summary["event_count"] == 2
    assert summary["last_event_type"] == "agent_end"
    assert summary["check_count"] == 1
    assert summary["artifacts"] == {
        "contract": str(recorder.run.contract_path),
        "events": str(recorder.run.events_path),
        "current_state": str(recorder.run.current_state_path),
        "transport_dag_evidence": str(recorder.run.transport_dag_evidence_path),
        "final_receipt": str(recorder.run.final_receipt_path),
        "node_result": str(recorder.run.node_result_path),
    }
    assert "tau_sanitization" not in summary
    assert summary["final_receipt"]["schema"] == LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA
    assert summary["node_result"]["schema"] == LOOP_RECEIPT_NODE_RESULT_SCHEMA


def test_loop_receipt_recorder_writes_harness_peer_message(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-peer")

    peer = recorder.write_harness_peer_message(
        target_harness="pi-mono",
        monitor_base_url="http://127.0.0.1:4321",
    )
    written = json.loads((recorder.run.run_dir / "harness-peer-message.json").read_text())

    assert peer == written
    assert peer["schema"] == LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA
    assert peer["message_type"] == "loop2_receipt_available"
    assert peer["ready"] is True
    assert peer["producer"] == {
        "harness": "tau",
        "run_id": "run-peer",
        "node_id": "repair-demo",
        "run_dir": str(recorder.run.run_dir),
    }
    assert peer["target"] == {"harness": "pi-mono"}
    assert peer["schemas"]["transport_dag_evidence"] == LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA
    assert peer["endpoints"]["transport_dag_evidence"] == (
        "http://127.0.0.1:4321/api/loop2/runs/run-peer/transport-dag-evidence"
    )
    assert peer["switchboard"]["from"] == "tau"
    assert peer["switchboard"]["to"] == "pi-mono"
    assert peer["switchboard"]["type"] == "info"
    assert peer["switchboard"]["priority"] == "normal"
    assert peer["switchboard"]["subject"] == "Tau Loop2 receipt available: run-peer"
    assert peer["switchboard"]["metadata"]["schema"] == LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA
    assert peer["switchboard"]["metadata"]["run_id"] == "run-peer"
    assert peer["switchboard"]["metadata"]["endpoints"]["transport_dag_evidence"] == (
        "http://127.0.0.1:4321/api/loop2/runs/run-peer/transport-dag-evidence"
    )
    assert peer["switchboard"]["metadata"]["claims"]["does_not_prove"] == peer["claims"][
        "does_not_prove"
    ]
    assert "claims.does_not_prove is preserved by the consuming harness" in peer[
        "consumer_checks"
    ]
    assert "switchboard.metadata.claims.does_not_prove is preserved when relayed" in peer[
        "consumer_checks"
    ]


def test_loop_peer_switchboard_emit_request_matches_pi_mono_contract(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-peer-emit")

    request = build_loop_peer_switchboard_emit_request(
        recorder.run.run_dir,
        target_harness="pi-mono",
        monitor_base_url="http://127.0.0.1:4321",
    )

    assert request["from"] == "tau"
    assert request["to"] == "pi-mono"
    assert request["type"] == "info"
    assert request["priority"] == "normal"
    assert request["subject"] == "Tau Loop2 receipt available: run-peer-emit"
    assert "preserve claims.does_not_prove" in request["message"]
    assert request["metadata"]["schema"] == LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA  # type: ignore[index]
    assert request["metadata"]["run_id"] == "run-peer-emit"  # type: ignore[index]
    assert request["metadata"]["endpoints"]["peer_message"] == (  # type: ignore[index]
        "http://127.0.0.1:4321/api/loop2/runs/run-peer-emit/peer-message"
    )
    assert request["metadata"]["claims"]["does_not_prove"]  # type: ignore[index]


def test_loop_harness_peer_message_fails_closed_for_incomplete_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "missing-peer"
    run_dir.mkdir()

    peer = build_loop_harness_peer_message(run_dir, target_harness="pi-mono")

    assert peer["schema"] == LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA
    assert peer["message_type"] == "loop2_receipt_unavailable"
    assert peer["ready"] is False
    assert peer["status"] == "MISSING_ARTIFACTS"
    assert "contract" in peer["missing_artifacts"]
    assert peer["switchboard"]["from"] == "tau"
    assert peer["switchboard"]["to"] == "pi-mono"
    assert peer["switchboard"]["type"] == "alert"
    assert peer["switchboard"]["priority"] == "high"
    assert peer["switchboard"]["metadata"]["ready"] is False
    assert "contract" in peer["switchboard"]["metadata"]["missing_artifacts"]


def test_loop_receipt_summary_includes_harness_peer_message_sidecar(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-peer-summary")
    peer = recorder.write_harness_peer_message(target_harness="pi-mono")

    summary = loop_receipt_summary(recorder.run.run_dir)

    assert summary["artifacts"]["harness_peer_message"] == str(
        recorder.run.run_dir / "harness-peer-message.json"
    )
    assert summary["harness_peer_message"] == peer


def test_loop_receipt_summary_includes_optional_tau_sanitization_sidecar(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-summary-sidecar")
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

    summary = loop_receipt_summary(recorder.run.run_dir)

    assert summary["artifacts"]["tau_sanitization"] == str(
        recorder.run.run_dir / "tau-sanitization.json"
    )
    assert summary["tau_sanitization"] == sidecar


def test_loop_receipt_summary_redacts_scillm_api_key_without_rewriting_file(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-summary-redaction")
    contract = json.loads(recorder.run.contract_path.read_text())
    contract["backend"] = "scillm"
    contract["scillm"] = {
        "base_url": "http://127.0.0.1:4001",
        "api_key": "summary-secret",
    }
    recorder.run.contract_path.write_text(json.dumps(contract), encoding="utf-8")

    summary = loop_receipt_summary(recorder.run.run_dir)
    persisted_contract = json.loads(recorder.run.contract_path.read_text())

    assert summary["contract"]["scillm"]["api_key"] == "<redacted-scillm-api-key>"
    assert persisted_contract["scillm"]["api_key"] == "summary-secret"


def test_loop_receipt_projects_tau_events_to_loop2_event_rows(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-events")

    events = loop_receipt_loop2_events(recorder.run.run_dir)

    assert [event["schema"] for event in events] == [LOOP2_EVENT_SCHEMA, LOOP2_EVENT_SCHEMA]
    assert [event["event_id"] for event in events] == [
        "run-loop2-events:0001:tau",
        "run-loop2-events:0002:tau",
    ]
    assert [event["event_type"] for event in events] == ["agent_start", "agent_end"]
    assert [event["status"] for event in events] == ["running", "completed"]
    assert {event["run_id"] for event in events} == {"run-loop2-events"}
    assert {event["node_id"] for event in events} == {"repair-demo"}
    assert all(isinstance(event["ts"], float) for event in events)
    assert all(str(event["iso_time"]).endswith("Z") for event in events)
    assert events[0]["data"]["tau_event"]["event"]["type"] == "agent_start"  # type: ignore[index]


def test_loop_receipt_emits_native_loop2_event_rows(tmp_path: Path) -> None:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id="run-loop2-native-events")

    recorder.emit_loop2_event(
        "checks_started",
        node_id="repair-demo",
        status="running",
        message="checks starting",
    )
    recorder.emit_loop2_event(
        "checks_finished",
        node_id="repair-demo",
        status="completed",
        data={"ok": True},
    )

    rows = [json.loads(line) for line in recorder.run.events_path.read_text().splitlines()]
    events = loop_receipt_loop2_events(recorder.run.run_dir)
    current_state = json.loads(recorder.run.current_state_path.read_text())

    assert [row["schema"] for row in rows] == [LOOP2_EVENT_SCHEMA, LOOP2_EVENT_SCHEMA]
    assert [event["event_type"] for event in events] == ["checks_started", "checks_finished"]
    assert events == rows
    assert current_state["state"] == "ended"
    assert current_state["event_count"] == 2
    assert current_state["last_event_type"] == "checks_finished"


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_projected_events_validate_against_loop2_event_contract(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-event-contract")
    import sys

    sys.path.insert(0, str(LOOP2_SRC))
    try:
        from loop2.contracts import Loop2Event

        for event in loop_receipt_loop2_events(recorder.run.run_dir):
            Loop2Event.model_validate(event)
    finally:
        sys.path.remove(str(LOOP2_SRC))


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_artifacts_validate_against_loop2_contracts(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-valid")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is True
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert result.errors == ()


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_contract_file_validates_against_loop2_contract(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-file-valid")

    result = validate_loop2_contract_file(recorder.run.contract_path, loop2_src=LOOP2_SRC)

    assert result.ok is True
    assert result.checked_artifacts == ("contract",)
    assert result.errors == ()


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_contract_file_validation_rejects_contract_mismatch(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-demo",
                "objective": "Invalid contract.",
                "allowed_globs": [],
                "checks": [],
            }
        ),
        encoding="utf-8",
    )

    result = validate_loop2_contract_file(contract_path, loop2_src=LOOP2_SRC)

    assert result.ok is False
    assert result.checked_artifacts == ()
    assert len(result.errors) == 1
    assert result.errors[0].startswith("contract:")


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validates_against_loop2_contracts(tmp_path: Path) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is True
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "secret_redaction",
        "state_status",
    )
    assert result.errors == ()


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validation_accepts_tau_sanitization_sidecar(
    tmp_path: Path,
) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")
    sidecar = {
        "schema": "tau.loop2_delegated_artifact_sanitization.v1",
        "ran": True,
        "artifact": str(run_dir / "tau-sanitization.json"),
        "run_dir": str(run_dir),
        "changed_artifacts": ["contract.json", "node-result.json"],
        "redacted_keys": ["contract.scillm.api_key"],
        "filtered_changed_files": 1,
    }
    (run_dir / "tau-sanitization.json").write_text(json.dumps(sidecar), encoding="utf-8")

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is True
    assert "tau_sanitization" in result.checked_artifacts
    assert result.errors == ()


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validation_rejects_bad_tau_sanitization_sidecar(
    tmp_path: Path,
) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")
    (run_dir / "tau-sanitization.json").write_text(
        json.dumps(
            {
                "schema": "tau.loop2_delegated_artifact_sanitization.v1",
                "ran": True,
                "artifact": str(run_dir / "other.json"),
                "run_dir": str(run_dir),
                "changed_artifacts": ["contract.json"],
                "redacted_keys": ["contract.scillm.api_key"],
                "filtered_changed_files": 0,
            }
        ),
        encoding="utf-8",
    )

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is False
    assert "tau_sanitization" not in result.checked_artifacts
    assert result.errors == ("tau_sanitization: artifact must point to tau-sanitization.json",)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validation_rejects_missing_indexed_tau_sanitization(
    tmp_path: Path,
) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")
    receipt = json.loads((run_dir / "final-receipt.json").read_text())
    receipt["artifacts"]["tau_sanitization"] = str(run_dir / "missing-sanitization.json")
    (run_dir / "final-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is False
    assert result.errors == (
        "artifact_paths: missing referenced paths: "
        f"final_receipt.artifacts.tau_sanitization={run_dir / 'missing-sanitization.json'}",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validation_rejects_unredacted_scillm_api_key(
    tmp_path: Path,
) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")
    contract = json.loads((run_dir / "contract.json").read_text())
    contract["scillm"]["api_key"] = "live-secret"
    (run_dir / "contract.json").write_text(json.dumps(contract), encoding="utf-8")

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is False
    assert "secret_redaction" not in result.checked_artifacts
    assert result.errors == (
        "secret_redaction: contract.scillm.api_key must be redacted in persisted run artifacts",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_native_loop2_run_validation_rejects_missing_referenced_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _write_native_loop2_run(tmp_path / "native-run")
    (run_dir / "checks" / "stdout.txt").unlink()

    result = validate_native_loop2_run_with_contracts(run_dir, loop2_src=LOOP2_SRC)

    assert result.ok is False
    assert result.checked_artifacts[:6] == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
    )
    assert result.errors == (
        "artifact_paths: missing referenced paths: "
        "final_receipt.checks[1].stdout_path="
        f"{run_dir / 'checks' / 'stdout.txt'}, "
        "node_result.checks[1].stdout_path="
        f"{run_dir / 'checks' / 'stdout.txt'}",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_loop2_contract_mismatch(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-invalid")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    receipt["status"] = "GREEN"
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert len(result.errors) == 2
    assert result.errors[0].startswith("final_receipt:")
    assert "GREEN" in result.errors[0]
    assert result.errors[1].startswith("check_status:")
    assert "GREEN" in result.errors[1]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_requires_events_artifact(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-events-missing")
    recorder.run.events_path.unlink()

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == ()
    assert result.errors == ("missing artifact: events",)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_requires_current_state_artifact(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-current-state-missing")
    recorder.run.current_state_path.unlink()

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == ()
    assert result.errors == ("missing artifact: current_state",)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_current_state_payload_mismatch(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-current-state-invalid")
    current_state = json.loads(recorder.run.current_state_path.read_text())
    current_state["event_count"] = 999
    recorder.run.current_state_path.write_text(json.dumps(current_state), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("current_state:")
    assert "does not match events 2" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_requires_transport_dag_evidence_artifact(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-dag-evidence-missing")
    recorder.run.transport_dag_evidence_path.unlink()

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == ()
    assert result.errors == ("missing artifact: transport_dag_evidence",)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_transport_dag_evidence_payload_mismatch(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-dag-evidence-invalid")
    evidence = json.loads(recorder.run.transport_dag_evidence_path.read_text())
    evidence["progress_stream"]["event_count"] = 999
    recorder.run.transport_dag_evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("transport_dag_evidence:")
    assert "does not match events 2" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_missing_referenced_check_artifact(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-check-artifact-missing")
    (recorder.run.run_dir / "checks" / "stdout.txt").unlink()

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("artifact_paths:")
    assert "final_receipt.checks[1].stdout_path=checks/stdout.txt" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_missing_receipt_artifact_contract_path(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-path-missing")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    del receipt["artifacts"]["contract"]
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert result.errors == (
        "artifact_paths: missing referenced paths: final_receipt.artifacts.contract",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_pass_status_with_failing_check(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-check-status-invalid")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    node_result = json.loads(recorder.run.node_result_path.read_text())
    receipt["checks"][0]["exit_code"] = 1
    node_result["checks"][0]["exit_code"] = 1
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    recorder.run.node_result_path.write_text(json.dumps(node_result), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert result.errors == (
        "check_status: final_receipt.status PASS has failing checks: [1]",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_mocked_live_mismatch(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-mocked-live-mismatch")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    receipt["live"] = True
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert result.errors == (
        "mocked_live: final_receipt.live True does not match node_result.live False",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_mocked_and_live_both_true(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-mocked-live-both-true")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    node_result = json.loads(recorder.run.node_result_path.read_text())
    receipt["live"] = True
    node_result["live"] = True
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    recorder.run.node_result_path.write_text(json.dumps(node_result), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "node_result_parity",
        "contract_parity",
        "state_status",
    )
    assert result.errors == ("mocked_live: mocked and live cannot both be true",)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_node_result_node_id_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-node-id-drift")
    node_result = json.loads(recorder.run.node_result_path.read_text())
    node_result["node_id"] = "different-node"
    recorder.run.node_result_path.write_text(json.dumps(node_result), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "contract_parity",
        "state_status",
    )
    assert result.errors == (
        "node_result_parity: final_receipt.node_id 'repair-demo' does not match "
        "node_result.node_id 'different-node'",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_node_result_check_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-check-drift")
    node_result = json.loads(recorder.run.node_result_path.read_text())
    node_result["checks"] = []
    recorder.run.node_result_path.write_text(json.dumps(node_result), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "contract_parity",
        "state_status",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("node_result_parity:")
    assert "final_receipt.checks" in result.errors[0]
    assert "node_result.checks []" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_contract_node_id_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-node-drift")
    contract = json.loads(recorder.run.contract_path.read_text())
    contract["node_id"] = "different-contract-node"
    recorder.run.contract_path.write_text(json.dumps(contract), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "state_status",
    )
    assert result.errors == (
        "contract_parity: contract.node_id 'different-contract-node' does not match "
        "final_receipt.node_id 'repair-demo'",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_contract_check_command_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-contract-check-drift")
    contract = json.loads(recorder.run.contract_path.read_text())
    contract["checks"] = ["python -m pytest other -q"]
    recorder.run.contract_path.write_text(json.dumps(contract), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "state_status",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("contract_parity:")
    assert "contract.checks ['python -m pytest other -q']" in result.errors[0]
    assert "final_receipt check commands" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_current_state_run_id_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-state-run-id-drift")
    current_state = json.loads(recorder.run.current_state_path.read_text())
    current_state["run_id"] = "different-run"
    recorder.run.current_state_path.write_text(json.dumps(current_state), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
    )
    assert len(result.errors) == 1
    assert result.errors[0].startswith("state_status:")
    assert "different-run" in result.errors[0]


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_loop_receipt_validation_rejects_current_state_last_event_drift(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-state-event-drift")
    current_state = json.loads(recorder.run.current_state_path.read_text())
    current_state["last_event_type"] = "message_delta"
    recorder.run.current_state_path.write_text(json.dumps(current_state), encoding="utf-8")

    result = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )

    assert result.ok is False
    assert result.checked_artifacts == (
        "contract",
        "final_receipt",
        "node_result",
        "events",
        "current_state",
        "transport_dag_evidence",
        "artifact_paths",
        "check_status",
        "mocked_live",
        "node_result_parity",
        "contract_parity",
    )
    assert result.errors == (
        "state_status: current_state.last_event_type 'message_delta' "
        "does not match last event 'agent_end'",
    )


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_backfill_loop_receipt_artifact_index_repairs_legacy_receipt(
    tmp_path: Path,
) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-backfill")
    receipt = json.loads(recorder.run.final_receipt_path.read_text())
    del receipt["artifacts"]["contract"]
    del receipt["artifacts"]["final_receipt"]
    recorder.run.final_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = backfill_loop_receipt_artifact_index(recorder.run.run_dir)
    validation = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=LOOP2_SRC,
    )
    repaired = json.loads(recorder.run.final_receipt_path.read_text())

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["added_keys"] == ["contract", "final_receipt"]
    assert Path(str(result["backup_path"])).exists()
    assert repaired["artifacts"]["contract"] == str(recorder.run.contract_path)
    assert repaired["artifacts"]["final_receipt"] == str(recorder.run.final_receipt_path)
    assert validation.ok is True


def test_backfill_loop_receipt_artifact_index_noops_when_complete(tmp_path: Path) -> None:
    recorder = _complete_loop_receipt_run(tmp_path, run_id="run-loop2-backfill-noop")

    result = backfill_loop_receipt_artifact_index(recorder.run.run_dir)

    assert result["ok"] is True
    assert result["changed"] is False
    assert result["added_keys"] == []
    assert result["backup_path"] == ""
    assert result["errors"] == []


def test_loop_receipt_summary_fails_closed_when_artifact_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "incomplete-run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")

    summary = loop_receipt_summary(run_dir)

    assert summary == {
        "schema": "tau.loop_receipt.summary.v1",
        "found": False,
        "run_id": "incomplete-run",
        "run_dir": str(run_dir),
        "missing_artifacts": [
            "contract",
            "current_state",
            "transport_dag_evidence",
            "final_receipt",
            "node_result",
        ],
    }


def _complete_loop_receipt_run(tmp_path: Path, *, run_id: str) -> LoopReceiptRecorder:
    recorder = LoopReceiptRecorder.create(root_dir=tmp_path, run_id=run_id)
    checks_dir = recorder.run.run_dir / "checks"
    checks_dir.mkdir()
    (checks_dir / "stdout.txt").write_text("ok\n", encoding="utf-8")
    (checks_dir / "stderr.txt").write_text("", encoding="utf-8")
    check = {
        "command": "python -m pytest tests/test_loop_receipt.py -q",
        "exit_code": 0,
        "stdout_path": "checks/stdout.txt",
        "stderr_path": "checks/stderr.txt",
        "elapsed_s": 0.1,
    }
    recorder.write_contract(
        node_id="repair-demo",
        objective="Validate Tau artifacts against Loop2 contracts.",
        repo=tmp_path,
        allowed_globs=["src/**"],
        checks=[str(check["command"])],
        max_attempts=1,
        backend="fixture",
    )
    recorder.record(AgentStartEvent())
    recorder.record(AgentEndEvent())
    recorder.write_final_receipt(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=[check],
        proves=["Tau artifacts validate against Loop2 Pydantic contracts."],
        does_not_prove=["live Loop2 runner execution"],
    )
    recorder.write_transport_dag_evidence()
    recorder.write_node_result(
        node_id="repair-demo",
        mocked=True,
        live=False,
        checks=[check],
    )
    return recorder


def _write_native_loop2_run(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True)
    checks_dir = run_dir / "checks"
    checks_dir.mkdir()
    stdout_path = checks_dir / "stdout.txt"
    stderr_path = checks_dir / "stderr.txt"
    stdout_path.write_text("ok\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    run_id = "loop2-repair-demo-1"
    node_id = "repair-demo"
    check = {
        "command": "python -m pytest",
        "exit_code": 0,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "elapsed_s": 0.1,
    }
    contract = {
        "schema": "loop2.repair_node_contract.v1",
        "run_id": run_id,
        "node_id": node_id,
        "objective": "Validate native Loop2 artifacts.",
        "repo": str(run_dir.parent),
        "allowed_globs": ["src/**"],
        "required_changed_globs": [],
        "checks": [check["command"]],
        "max_attempts": 1,
        "backend": "fixture",
        "run_root": str(run_dir.parent),
        "scillm": {
            "base_url": "http://127.0.0.1:4001",
            "api_key": "<redacted-scillm-api-key>",
            "agent_id": "",
            "agent": "build",
            "mode": "workspace_write",
            "model": "",
            "timeout_s": 900,
        },
    }
    events = [
        {
            "schema": LOOP2_EVENT_SCHEMA,
            "run_id": run_id,
            "node_id": node_id,
            "event_id": f"{run_id}:0001:contract",
            "event_type": "contract_loaded",
            "ts": 1.0,
            "iso_time": "2026-06-26T00:00:01Z",
            "status": "running",
            "message": "contract loaded",
            "data": {},
        },
        {
            "schema": LOOP2_EVENT_SCHEMA,
            "run_id": run_id,
            "node_id": node_id,
            "event_id": f"{run_id}:0002:receipt",
            "event_type": "receipt_written",
            "ts": 2.0,
            "iso_time": "2026-06-26T00:00:02Z",
            "status": "completed",
            "message": "",
            "data": {},
        },
    ]
    receipt = {
        "schema": "loop2.final_receipt.v1",
        "run_id": run_id,
        "node_id": node_id,
        "status": "PASS",
        "mocked": True,
        "live": False,
        "proof_scope": "one bounded loop2 repair node",
        "claims": {
            "proves": ["loop2 executed one bounded repair-node contract"],
            "does_not_prove": ["semantic repair quality; fixture backend is wiring-only"],
        },
        "changed_files": [],
        "checks": [check],
        "artifacts": {
            "run_dir": str(run_dir),
            "events": str(run_dir / "events.jsonl"),
            "current_state": str(run_dir / "current-state.json"),
            "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
            "node_result": str(run_dir / "node-result.json"),
        },
        "scillm": {},
        "error": "",
    }
    evidence = {
        "schema": LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
        "found": True,
        "transport_run_id": run_id,
        "graph_id": f"loop2:{node_id}",
        "proof_path": str(run_dir / "final-receipt.json"),
        "nodes": [{"id": "receipt", "label": "Receipt", "status": "accepted"}],
        "edges": [{"from": "contract", "to": "receipt"}],
        "layers": [["contract"], ["receipt"]],
        "not_proven": [],
        "progress_stream": {
            "state": "live_or_historical",
            "event_count": len(events),
            "event_types": ["contract_loaded", "receipt_written"],
            "events_path": str(run_dir / "events.jsonl"),
            "last_event_type": "receipt_written",
        },
    }
    node_result = {
        "schema": "loop2.node_result.v1",
        "node_id": node_id,
        "status": "PASS",
        "run_id": run_id,
        "final_receipt": str(run_dir / "final-receipt.json"),
        "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
        "events": str(run_dir / "events.jsonl"),
        "changed_files": [],
        "checks": [check],
        "mocked": True,
        "live": False,
    }
    current_state = {
        "schema": "loop2.current_state.v1",
        "run_id": run_id,
        "node_id": node_id,
        "status": "completed",
        "event_count": len(events),
        "last_event_type": "receipt_written",
        "updated_at": "2026-06-26T00:00:02Z",
    }

    (run_dir / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    (run_dir / "current-state.json").write_text(json.dumps(current_state), encoding="utf-8")
    (run_dir / "transport-dag-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (run_dir / "final-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (run_dir / "node-result.json").write_text(json.dumps(node_result), encoding="utf-8")
    return run_dir
