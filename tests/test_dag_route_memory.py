import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_route_memory import (
    DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA,
    DAG_ROUTE_MEMORY_SYNC_RECEIPT_SCHEMA,
    write_dag_route_memory_candidate_receipt,
    write_dag_route_memory_sync_receipt,
)


def test_route_memory_candidates_accept_clean_signal_receipt(tmp_path: Path) -> None:
    signal_path = _write_signal(tmp_path, _signal_receipt())
    receipt_path = tmp_path / "route-memory.json"

    receipt = write_dag_route_memory_candidate_receipt(
        signal_receipt_path=signal_path,
        receipt_path=receipt_path,
    )

    assert receipt["schema"] == DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["memory_sync"] is False
    assert receipt["sync_status"] == "NOT_SYNCED"
    assert receipt["route_mutation"] is False
    assert receipt["dag_mutation"] is False
    assert receipt["provider_calls"] is False
    assert receipt["accepted_candidate_count"] == 2
    assert receipt["rejected_candidate_count"] == 0
    assert receipt["accepted_candidates"][0]["route_key"] == "start:goal-guardian->coder:coder"
    assert receipt_path.exists()


def test_route_memory_candidates_block_negative_signal_receipt(tmp_path: Path) -> None:
    payload = _signal_receipt()
    payload["source_ok"] = False
    payload["source_status"] = "BLOCKED"
    payload["negative_signals"] = [
        {
            "type": "alert",
            "severity": "BLOCK",
            "code": "missing_required_evidence",
            "message": "Missing evidence.",
            "deterministic": True,
        }
    ]
    signal_path = _write_signal(tmp_path, payload)

    receipt = write_dag_route_memory_candidate_receipt(
        signal_receipt_path=signal_path,
        receipt_path=tmp_path / "route-memory.json",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert {alert["code"] for alert in receipt["alerts"]} == {
        "source_dag_not_pass",
        "negative_signals_present",
    }
    assert receipt["memory_sync"] is False
    assert receipt["accepted_candidate_count"] == 2


def test_route_memory_candidates_reject_low_confidence(tmp_path: Path) -> None:
    payload = _signal_receipt()
    payload["route_reinforcement_candidates"][1]["confidence"] = 0.5
    signal_path = _write_signal(tmp_path, payload)

    receipt = write_dag_route_memory_candidate_receipt(
        signal_receipt_path=signal_path,
        receipt_path=tmp_path / "route-memory.json",
        min_confidence=0.75,
    )

    assert receipt["ok"] is True
    assert receipt["accepted_candidate_count"] == 1
    assert receipt["rejected_candidate_count"] == 1
    assert receipt["rejected_candidates"][0]["rejection_reason"] == "confidence_below_threshold"


def test_route_memory_candidates_block_when_no_candidate_meets_gate(tmp_path: Path) -> None:
    payload = _signal_receipt()
    for candidate in payload["route_reinforcement_candidates"]:
        candidate["confidence"] = 0.5
    signal_path = _write_signal(tmp_path, payload)

    receipt = write_dag_route_memory_candidate_receipt(
        signal_receipt_path=signal_path,
        receipt_path=tmp_path / "route-memory.json",
        min_confidence=0.9,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["alerts"][0]["code"] == "no_accepted_candidates"
    assert receipt["accepted_candidate_count"] == 0
    assert receipt["rejected_candidate_count"] == 2


def test_cli_route_memory_candidates_writes_receipt(tmp_path: Path) -> None:
    signal_path = _write_signal(tmp_path, _signal_receipt())
    receipt_path = tmp_path / "route-memory.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-route-memory-candidates",
            "--signal-receipt",
            str(signal_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA
    assert payload["accepted_candidate_count"] == 2
    assert receipt_path.exists()


def test_route_memory_sync_projects_documents_without_memory_write(tmp_path: Path) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)
    receipt_path = tmp_path / "sync.json"

    receipt = write_dag_route_memory_sync_receipt(
        candidate_receipt_path=candidate_path,
        receipt_path=receipt_path,
    )

    assert receipt["schema"] == DAG_ROUTE_MEMORY_SYNC_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["apply"] is False
    assert receipt["memory_sync"] is False
    assert receipt["sync_status"] == "DRY_RUN"
    assert receipt["projected_document_count"] == 2
    assert receipt["documents"][0]["schema"] == "tau.route_memory_signal.v1"
    assert receipt["documents"][0]["_key"].startswith("tau-route-")
    assert receipt_path.exists()


def test_route_memory_sync_blocks_failed_candidate_receipt(tmp_path: Path) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["ok"] = False
    candidate["status"] = "BLOCKED"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    receipt = write_dag_route_memory_sync_receipt(
        candidate_receipt_path=candidate_path,
        receipt_path=tmp_path / "sync.json",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["memory_sync"] is False
    assert any(alert["code"] == "candidate_receipt_not_pass" for alert in receipt["alerts"])


def test_route_memory_sync_apply_requires_approval_receipt(tmp_path: Path) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)

    receipt = write_dag_route_memory_sync_receipt(
        candidate_receipt_path=candidate_path,
        receipt_path=tmp_path / "sync.json",
        apply=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["memory_sync"] is False
    assert receipt["sync_status"] == "BLOCKED"
    assert receipt["approval_receipt"] is None
    assert any(alert["code"] == "missing_approval_receipt" for alert in receipt["alerts"])


def test_route_memory_sync_apply_blocks_wrong_approval_action(tmp_path: Path) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)
    approval_path = _write_approval_receipt(tmp_path, requested_action="github_apply")

    receipt = write_dag_route_memory_sync_receipt(
        candidate_receipt_path=candidate_path,
        receipt_path=tmp_path / "sync.json",
        approval_receipt_path=approval_path,
        apply=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["memory_sync"] is False
    assert receipt["approval_receipt"] == str(approval_path.resolve())
    assert receipt["approval_receipt_sha256"].startswith("sha256:")
    assert any(alert["code"] == "approval_action_mismatch" for alert in receipt["alerts"])


def test_cli_route_memory_sync_writes_dry_run_receipt(tmp_path: Path) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)
    receipt_path = tmp_path / "sync.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-route-memory-sync",
            "--candidate-receipt",
            str(candidate_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_ROUTE_MEMORY_SYNC_RECEIPT_SCHEMA
    assert payload["memory_sync"] is False
    assert payload["projected_document_count"] == 2
    assert receipt_path.exists()


def test_cli_route_memory_sync_apply_without_approval_exits_nonzero(
    tmp_path: Path,
) -> None:
    candidate_path = _write_candidate_receipt(tmp_path)
    receipt_path = tmp_path / "sync.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-route-memory-sync",
            "--candidate-receipt",
            str(candidate_path),
            "--receipt",
            str(receipt_path),
            "--apply",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["status"] == "BLOCKED"
    assert payload["alerts"][0]["code"] == "missing_approval_receipt"
    assert receipt_path.exists()


def _write_signal(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "dag-signal-receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_candidate_receipt(tmp_path: Path) -> Path:
    signal_path = _write_signal(tmp_path, _signal_receipt())
    candidate_path = tmp_path / "candidate.json"
    write_dag_route_memory_candidate_receipt(
        signal_receipt_path=signal_path,
        receipt_path=candidate_path,
    )
    return candidate_path


def _write_approval_receipt(tmp_path: Path, *, requested_action: str) -> Path:
    path = tmp_path / "approval-receipt.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "approved": True,
                "requested_action": requested_action,
            }
        ),
        encoding="utf-8",
    )
    return path


def _signal_receipt() -> dict[str, object]:
    return {
        "schema": "tau.dag_signal_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "receipt_path": "/tmp/dag-signal-receipt.json",
        "source_dag_receipt": "/tmp/dag-receipt.json",
        "dag_id": "route-memory-test",
        "goal_hash": "sha256:route-memory-test",
        "source_ok": True,
        "source_status": "PASS",
        "source_verdict": "PASS",
        "scheduler": "bounded-ready-queue",
        "negative_signals": [],
        "route_reinforcement_candidates": [
            {
                "from_node": "start",
                "from_agent": "goal-guardian",
                "to_node": "coder",
                "to_agent": "coder",
                "confidence": 1.0,
                "source": "deterministic_dag_receipt_pass",
                "memory_sync_candidate": True,
                "sync_status": "NOT_SYNCED",
                "sync_reason": "first_slice_local_only",
            },
            {
                "from_node": "coder",
                "from_agent": "coder",
                "to_node": "reviewer",
                "to_agent": "reviewer",
                "confidence": 1.0,
                "source": "deterministic_dag_receipt_pass",
                "memory_sync_candidate": True,
                "sync_status": "NOT_SYNCED",
                "sync_reason": "first_slice_local_only",
            },
        ],
    }
