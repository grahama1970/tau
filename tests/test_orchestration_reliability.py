import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.course_correction import write_course_correction_receipt
from tau_coding.orchestration_reliability import (
    ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA,
    write_orchestration_reliability_receipt,
)


def test_orchestration_reliability_passes_clean_run(tmp_path: Path) -> None:
    artifact = tmp_path / "command-loop-receipt.json"
    artifact.write_text(
        json.dumps(
            {
                "schema": "tau.command_loop_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": True,
                "provider_live": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    dag_receipt = _write_dag_receipt(tmp_path, artifacts=[str(artifact)])

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
        required_receipts=[artifact],
    )

    assert payload["schema"] == ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["goal_hash_preserved"] is True
    assert payload["dag_routes_respected"] is True
    assert payload["required_evidence_present"] is True
    assert payload["retry_budget_respected"] is True
    assert payload["terminal_condition_valid"] is True
    assert payload["dag_receipt_sha256"] == f"sha256:{_sha256(dag_receipt)}"
    assert payload["dag_receipt_bytes"] == dag_receipt.stat().st_size
    assert payload["required_receipts"]["present_artifacts"] == [
        {
            "path": str(artifact.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(artifact)}",
            "bytes": artifact.stat().st_size,
            "schema": "tau.command_loop_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
    ]
    assert payload["inspected_artifacts"][0] == {
        "label": "dag_receipt",
        "path": str(dag_receipt.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(dag_receipt)}",
        "bytes": dag_receipt.stat().st_size,
    }


def test_orchestration_reliability_blocks_missing_receipt(tmp_path: Path) -> None:
    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=tmp_path / "missing-dag-receipt.json",
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_dag_or_run_receipt" in payload["alert_codes"]


def test_orchestration_reliability_blocks_invalid_dag_receipt_schema(
    tmp_path: Path,
) -> None:
    dag_receipt = _write_dag_receipt(tmp_path)
    payload = json.loads(dag_receipt.read_text(encoding="utf-8"))
    payload["schema"] = "not.tau.dag_receipt.v1"
    dag_receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["dag_receipt_schema"] == "not.tau.dag_receipt.v1"
    assert receipt["dag_receipt_schema_valid"] is False
    assert "invalid_dag_receipt_schema" in receipt["alert_codes"]


def test_orchestration_reliability_blocks_required_receipt_not_pass(tmp_path: Path) -> None:
    artifact = _write_required_receipt(tmp_path / "worker-receipt.json", status="BLOCKED")
    dag_receipt = _write_dag_receipt(tmp_path, artifacts=[str(artifact)])

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
        required_receipts=[artifact],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["required_receipts_present"] is True
    assert "required_receipt_invalid" in payload["alert_codes"]
    assert payload["required_receipts"]["invalid"] == [
        {"path": str(artifact.resolve()), "reason": "status_not_pass"}
    ]


def test_orchestration_reliability_blocks_mocked_required_receipt(tmp_path: Path) -> None:
    artifact = _write_required_receipt(tmp_path / "worker-receipt.json", mocked=True)
    dag_receipt = _write_dag_receipt(tmp_path, artifacts=[str(artifact)])

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
        required_receipts=[artifact],
    )

    assert payload["status"] == "BLOCKED"
    assert "required_receipt_invalid" in payload["alert_codes"]
    assert payload["required_receipts"]["invalid"] == [
        {"path": str(artifact.resolve()), "reason": "mocked"}
    ]


def test_orchestration_reliability_blocks_non_live_required_receipt(tmp_path: Path) -> None:
    artifact = _write_required_receipt(tmp_path / "worker-receipt.json", live=False)
    dag_receipt = _write_dag_receipt(tmp_path, artifacts=[str(artifact)])

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
        required_receipts=[artifact],
    )

    assert payload["status"] == "BLOCKED"
    assert "required_receipt_invalid" in payload["alert_codes"]
    assert payload["required_receipts"]["invalid"] == [
        {"path": str(artifact.resolve()), "reason": "not_live"}
    ]


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


def test_orchestration_reliability_blocks_invalid_course_correction_artifact(
    tmp_path: Path,
) -> None:
    correction = tmp_path / "course-correction.json"
    correction.write_text(json.dumps({"schema": "not.tau.course_correction.v1"}), encoding="utf-8")
    dag_receipt = _write_dag_receipt(
        tmp_path,
        status="BLOCKED",
        course_correction_artifacts=[str(correction)],
    )

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["status"] == "BLOCKED"
    assert payload["course_corrections_followed"] is False
    assert payload["course_correction_artifact_report"]["invalid"] == [
        {"path": str(correction.resolve()), "reason": "schema_mismatch"}
    ]
    assert "course_correction_ignored" in payload["alert_codes"]


def test_orchestration_reliability_accepts_valid_course_correction_artifact(
    tmp_path: Path,
) -> None:
    correction = tmp_path / "course-correction.json"
    write_course_correction_receipt(
        correction,
        trigger="receipt_timeout",
        dag_id="coding-dag",
        goal_hash="sha256:active-goal",
        node_id="coder",
        agent="coder",
        attempt=1,
    )
    dag_receipt = _write_dag_receipt(
        tmp_path,
        status="BLOCKED",
        course_correction_artifacts=[str(correction)],
    )

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["course_corrections_followed"] is True
    assert payload["course_correction_artifact_report"]["missing"] == []
    assert payload["course_correction_artifact_report"]["invalid"] == []
    assert payload["course_correction_artifact_report"]["valid"][0]["path"] == str(
        correction.resolve()
    )
    assert payload["course_correction_artifact_report"]["valid"][0]["exists"] is True


def test_orchestration_reliability_blocks_unbound_course_correction_artifact(
    tmp_path: Path,
) -> None:
    correction = tmp_path / "course-correction.json"
    correction.write_text(
        json.dumps(
            {
                "schema": "tau.course_correction.v1",
                "status": "REQUIRED",
                "next_allowed": False,
                "input_valid": True,
                "required_next_action": "retry_node",
            }
        ),
        encoding="utf-8",
    )
    dag_receipt = _write_dag_receipt(
        tmp_path,
        status="BLOCKED",
        course_correction_artifacts=[str(correction)],
    )

    payload = write_orchestration_reliability_receipt(
        dag_receipt_path=dag_receipt,
        output_path=tmp_path / "orchestration-reliability.json",
    )

    assert payload["status"] == "BLOCKED"
    assert payload["course_corrections_followed"] is False
    assert payload["course_correction_artifact_report"]["invalid"] == [
        {"path": str(correction.resolve()), "reason": "missing_goal_hash"}
    ]
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
    status: str = "PASS",
    artifacts: list[str] | None = None,
    course_correction_artifacts: list[str] | None = None,
) -> Path:
    payload = {
        "schema": "tau.dag_receipt.v1",
        "ok": status == "PASS",
        "status": status,
        "verdict": status,
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


def _write_required_receipt(
    path: Path,
    *,
    status: str = "PASS",
    mocked: bool = False,
    live: bool = True,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "tau.command_loop_receipt.v1",
                "status": status,
                "ok": status == "PASS",
                "mocked": mocked,
                "live": live,
                "provider_live": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
