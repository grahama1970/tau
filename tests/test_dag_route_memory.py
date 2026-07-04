import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_route_memory import (
    DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA,
    write_dag_route_memory_candidate_receipt,
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


def _write_signal(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "dag-signal-receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
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
