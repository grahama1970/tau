import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.orchestration_reliability import (
    ORCHESTRATION_RELIABILITY_SCHEMA,
    write_orchestration_reliability_receipt,
)


def test_orchestration_reliability_passes_clean_dag_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "ok": True,
            "dag_id": "dag-1",
            "provider_live": False,
        },
    )

    payload = write_orchestration_reliability_receipt(
        run_dir=tmp_path,
        output_path=tmp_path / "reliability.json",
    )

    assert payload["schema"] == ORCHESTRATION_RELIABILITY_SCHEMA
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["reliable_orchestration"] is True
    assert payload["agent_truthfulness"] == "NOT_CLAIMED"
    assert payload["course_correction_count"] == 0
    assert payload["alerts"] == []


def test_orchestration_reliability_accepts_controlled_block_with_course_correction(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "status": "BLOCKED",
            "verdict": "POINTLESS_UNIT_TEST_DRIFT",
            "ok": False,
            "dag_id": "dag-1",
            "dag_error": {
                "schema": "tau.dag_error.v1",
                "failure_code": "pointless_unit_test_drift",
            },
        },
    )
    _write_json(
        tmp_path / "course-corrections" / "coder.json",
        {
            "schema": "tau.course_correction.v1",
            "trigger": "pointless_unit_test_drift",
            "required_next_action": "stop_test_churn_report_blocker_and_replan",
        },
    )

    payload = write_orchestration_reliability_receipt(
        run_dir=tmp_path,
        output_path=tmp_path / "reliability.json",
    )

    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["reliable_orchestration"] is True
    assert payload["dag_status"] == "BLOCKED"
    assert payload["course_correction_count"] == 1


def test_cli_orchestration_reliability_blocks_unhandled_herdr_gate(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "ok": True,
            "dag_id": "dag-1",
        },
    )
    _write_json(
        tmp_path / "herdr-observation-gate.json",
        {
            "schema": "tau.herdr_observation_gate_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "course_correction": None,
        },
    )
    out = tmp_path / "reliability.json"

    result = CliRunner().invoke(
        app,
        [
            "orchestration-reliability",
            "--run-dir",
            str(tmp_path),
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["unhandled_herdr_block_count"] == 1
    assert payload["alerts"] == [
        {
            "severity": "BLOCK",
            "code": "unhandled_herdr_observation_block",
            "path": str(tmp_path / "herdr-observation-gate.json"),
        }
    ]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
