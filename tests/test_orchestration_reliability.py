import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.orchestration_reliability import (
    ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA,
    write_orchestration_reliability_receipt,
)


def test_orchestration_reliability_passes_clean_run(tmp_path: Path) -> None:
    artifact = tmp_path / "command-loop-receipt.json"
    artifact.write_text("{}", encoding="utf-8")
    dag_receipt = _write_dag_receipt(tmp_path, artifacts=[str(artifact)])

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["schema"] == ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["goal_hash_preserved"] is True
    assert payload["dag_routes_respected"] is True
    assert payload["required_evidence_present"] is True
    assert payload["retry_budget_respected"] is True
    assert payload["terminal_condition_valid"] is True


def test_orchestration_reliability_blocks_missing_receipt(tmp_path: Path) -> None:
    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=tmp_path / "missing-dag-receipt.json",
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_dag_or_run_receipt" in payload["alert_codes"]


def test_orchestration_reliability_blocks_ignored_course_correction(tmp_path: Path) -> None:
    correction = tmp_path / "course-correction.json"
    correction.write_text("{}", encoding="utf-8")
    dag_receipt = _write_dag_receipt(
        tmp_path,
        course_correction_artifacts=[str(correction)],
    )

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["status"] == "BLOCKED"
    assert payload["course_corrections_emitted"] is True
    assert payload["course_corrections_followed"] is False
    assert "course_correction_ignored" in payload["alert_codes"]


def test_orchestration_reliability_never_claims_code_correctness(tmp_path: Path) -> None:
    dag_receipt = _write_dag_receipt(tmp_path)

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["agent_truthfulness"] == "NOT_CLAIMED"
    assert "Code correctness." in payload["proof_scope"]["does_not_prove"]
    assert "Agent truthfulness." in payload["proof_scope"]["does_not_prove"]


def test_cli_orchestration_reliability_writes_receipt(tmp_path: Path) -> None:
    dag_receipt = _write_dag_receipt(tmp_path)
    out = tmp_path / "orchestration-reliability.json"

    result = CliRunner().invoke(
        app,
        [
            "orchestration-reliability",
            "--dag-receipt",
            str(dag_receipt),
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA


def _write_dag_receipt(
    tmp_path: Path,
    *,
    artifacts: list[str] | None = None,
    course_correction_artifacts: list[str] | None = None,
) -> Path:
    payload = {
        "schema": "tau.dag_receipt.v1",
        "ok": True,
        "status": "PASS",
        "verdict": "PASS",
        "dag_id": "coding-dag",
        "active_goal_hash": "sha256:active-goal",
        "terminal_nodes": ["human"],
        "observed_edges": [
            {
                "from_agent": "coder",
                "from_node": "coder",
                "to_agent": "human",
                "to_node": "human",
            }
        ],
        "alerts": [],
        "artifacts": artifacts or [],
        "course_correction_artifacts": course_correction_artifacts or [],
    }
    path = tmp_path / "dag-receipt.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
