import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.herdr_observation_gate import (
    HERDR_OBSERVATION_GATE_SCHEMA,
    write_herdr_observation_gate_receipt,
)


def test_herdr_observation_gate_passes_when_expected_receipt_exists(
    tmp_path: Path,
) -> None:
    snapshot = _write_snapshot(tmp_path, state="running")
    expected_receipt = tmp_path / "node-receipt.json"
    expected_receipt.write_text('{"schema":"tau.provider_dag_node_receipt.v1"}\n')

    payload = write_herdr_observation_gate_receipt(
        tmp_path / "gate.json",
        snapshot_path=snapshot,
        expected_receipt_path=expected_receipt,
        expected_workspace_id="w1",
        expected_pane_id="w1:p1",
        expected_terminal_id="term-1",
        dag_id="dag-1",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["schema"] == HERDR_OBSERVATION_GATE_SCHEMA
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["recommended_action"] == "continue"
    assert payload["course_correction"] is None
    assert payload["binding_errors"] == []


def test_herdr_observation_gate_blocks_overdue_missing_receipt(
    tmp_path: Path,
) -> None:
    snapshot = _write_snapshot(tmp_path, state="running")

    payload = write_herdr_observation_gate_receipt(
        tmp_path / "gate.json",
        snapshot_path=snapshot,
        expected_receipt_path=tmp_path / "missing-receipt.json",
        expected_workspace_id="w1",
        expected_pane_id="w1:p1",
        expected_terminal_id="term-1",
        run_id="run-1",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
        receipt_overdue=True,
        receipt_timeout_seconds=0,
    )

    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["recommended_action"] == "retry_node_or_route_goal_guardian"
    assert payload["course_correction"]["schema"] == "tau.course_correction.v1"
    assert payload["course_correction"]["trigger"] == "receipt_timeout"
    assert payload["course_correction"]["observed_state"]["receipt_missing"] is True
    assert payload["course_correction"]["observed_state"]["receipt_overdue"] is True


def test_cli_herdr_observation_gate_blocks_binding_mismatch(tmp_path: Path) -> None:
    snapshot = _write_snapshot(tmp_path, state="ready", workspace_id="w2")
    out = tmp_path / "gate.json"

    result = CliRunner().invoke(
        app,
        [
            "herdr-observation-gate",
            "--snapshot",
            str(snapshot),
            "--out",
            str(out),
            "--expected-workspace-id",
            "w1",
            "--expected-pane-id",
            "w1:p1",
            "--expected-terminal-id",
            "term-1",
            "--dag-id",
            "dag-1",
            "--node-id",
            "coder",
            "--agent",
            "coder",
            "--attempt",
            "1",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["course_correction"]["trigger"] == "herdr_binding_mismatch"
    assert payload["course_correction"]["required_next_action"] == "block_run"
    assert "workspace_id mismatch" in payload["binding_errors"][0]


def _write_snapshot(
    tmp_path: Path,
    *,
    state: str,
    workspace_id: str = "w1",
) -> Path:
    path = tmp_path / "herdr-snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema": "herdr.monitor_snapshot.v1",
                "state": state,
                "workspace_id": workspace_id,
                "pane_id": "w1:p1",
                "terminal_id": "term-1",
                "agent_name": "coder",
                "process_alive": True,
            }
        ),
        encoding="utf-8",
    )
    return path
