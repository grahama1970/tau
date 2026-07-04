import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_signals import DAG_SIGNAL_RECEIPT_SCHEMA, write_dag_signal_receipt


def test_dag_signal_receipt_derives_pass_signals(tmp_path: Path) -> None:
    source = _write_dag_receipt(tmp_path, _pass_receipt())

    receipt = write_dag_signal_receipt(source)

    assert receipt["schema"] == DAG_SIGNAL_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["source_ok"] is True
    assert receipt["source_status"] == "PASS"
    assert receipt["memory_sync_candidate"] is True
    assert receipt["sync_status"] == "NOT_SYNCED"
    assert receipt["negative_signals"] == []
    assert len(receipt["route_reinforcement_candidates"]) == 2
    assert all(edge["status"] == "REINFORCE" for edge in receipt["edge_signals"])
    assert Path(str(receipt["receipt_path"])).exists()


def test_dag_signal_receipt_preserves_blocked_source_as_negative_signal(
    tmp_path: Path,
) -> None:
    payload = _pass_receipt()
    payload["ok"] = False
    payload["status"] = "BLOCKED"
    payload["verdict"] = "UNEXPECTED_EDGE"
    payload["alerts"] = [
        {
            "severity": "BLOCK",
            "code": "unexpected_edge",
            "message": "Observed handoff route is not allowed by DAG contract.",
            "evidence": {"from_node": "coder", "to_node": "human"},
        }
    ]
    source = _write_dag_receipt(tmp_path, payload)

    receipt = write_dag_signal_receipt(source)

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["source_ok"] is False
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["route_reinforcement_candidates"] == []
    assert receipt["negative_signals"] == [
        {
            "code": "unexpected_edge",
            "deterministic": True,
            "message": "Observed handoff route is not allowed by DAG contract.",
            "node_id": "coder",
            "severity": "BLOCK",
            "type": "alert",
        }
    ]
    assert all(edge["status"] == "DECAY" for edge in receipt["edge_signals"])


def test_dag_signal_receipt_marks_missing_evidence(tmp_path: Path) -> None:
    payload = _pass_receipt()
    payload["ok"] = False
    payload["status"] = "BLOCKED"
    payload["verdict"] = "MISSING_REQUIRED_EVIDENCE"
    payload["alerts"] = [
        {
            "severity": "BLOCK",
            "code": "missing_required_evidence",
            "message": "Node response did not include required evidence.",
            "evidence": {"node_id": "coder", "missing": ["creator_artifact"]},
        }
    ]
    source = _write_dag_receipt(tmp_path, payload)

    receipt = write_dag_signal_receipt(source)

    coder_signal = _node_signal(receipt, "coder")
    assert coder_signal["required_evidence_missing"] == ["creator_artifact"]
    assert coder_signal["negative_signal_reason"] == "missing_required_evidence"
    assert receipt["negative_signals"][0]["code"] == "missing_required_evidence"


def test_dag_signal_receipt_marks_reviewer_blocker(tmp_path: Path) -> None:
    payload = _pass_receipt()
    payload["ok"] = False
    payload["status"] = "BLOCKED"
    payload["verdict"] = "REVIEWER_GOAL_HASH_MISMATCH"
    payload["alerts"] = [
        {
            "severity": "BLOCK",
            "code": "reviewer_goal_hash_mismatch",
            "message": "Reviewer verdict does not cite the immutable goal hash.",
            "evidence": {
                "node_id": "reviewer",
                "expected_goal_hash": "sha256:active-goal",
                "observed_goal_hash": "sha256:stale-goal",
            },
        }
    ]
    payload["reviewer_verdicts"] = [
        {
            "kind": "reviewer_verdict",
            "reviewed_node_id": "coder",
            "goal_hash": "sha256:stale-goal",
            "verdict": "PASS",
        }
    ]
    source = _write_dag_receipt(tmp_path, payload)

    receipt = write_dag_signal_receipt(source)

    reviewer_signal = _node_signal(receipt, "reviewer")
    assert reviewer_signal["reviewer_blockers"] == ["reviewer_goal_hash_mismatch"]
    assert reviewer_signal["negative_signal_reason"] == "reviewer_goal_hash_mismatch"
    assert receipt["negative_signals"][0]["code"] == "reviewer_goal_hash_mismatch"


def test_dag_signal_receipt_marks_max_attempts(tmp_path: Path) -> None:
    payload = _pass_receipt()
    payload["ok"] = False
    payload["status"] = "BLOCKED"
    payload["verdict"] = "MAX_ATTEMPTS_EXCEEDED"
    payload["node_attempts"] = {"coder": 2, "reviewer": 1}
    payload["alerts"] = [
        {
            "severity": "BLOCK",
            "code": "max_attempts_exceeded",
            "message": "Node exceeded its DAG max_attempts.",
            "evidence": {"node_id": "coder", "attempts": 2, "max_attempts": 1},
        }
    ]
    source = _write_dag_receipt(tmp_path, payload)

    receipt = write_dag_signal_receipt(source)

    coder_signal = _node_signal(receipt, "coder")
    assert coder_signal["attempt_count"] == 2
    assert coder_signal["negative_signal_reason"] == "max_attempts_exceeded"
    assert receipt["negative_signals"][0]["code"] == "max_attempts_exceeded"


def test_cli_dag_signals_writes_receipt(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    source = _write_dag_receipt(run_dir, _pass_receipt())
    out = tmp_path / "signal.json"

    result = CliRunner().invoke(app, ["dag-signals", str(source.parent), "--receipt", str(out)])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert out.exists()
    assert payload["schema"] == DAG_SIGNAL_RECEIPT_SCHEMA
    assert payload["receipt_path"] == str(out)
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == DAG_SIGNAL_RECEIPT_SCHEMA


def _node_signal(receipt: dict[str, object], node_id: str) -> dict[str, object]:
    signals = receipt["node_signals"]
    assert isinstance(signals, list)
    for signal in signals:
        assert isinstance(signal, dict)
        if signal.get("node_id") == node_id:
            return signal
    raise AssertionError(f"missing node signal: {node_id}")


def _write_dag_receipt(tmp_path: Path, payload: dict[str, object]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "dag-receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pass_receipt() -> dict[str, object]:
    return {
        "schema": "tau.dag_receipt.v1",
        "ok": True,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": "handoff-loop",
        "dag_id": "creator-reviewer-test",
        "active_goal_hash": "sha256:active-goal",
        "selected_agents": ["coder", "reviewer"],
        "observed_edges": [
            {
                "from_node": "coder",
                "from_agent": "coder",
                "to_node": "reviewer",
                "to_agent": "reviewer",
            },
            {
                "from_node": "reviewer",
                "from_agent": "reviewer",
                "to_node": "human",
                "to_agent": "human",
            },
        ],
        "node_attempts": {"coder": 1, "reviewer": 1},
        "reviewer_verdicts": [
            {
                "kind": "reviewer_verdict",
                "reviewed_node_id": "coder",
                "goal_hash": "sha256:active-goal",
                "verdict": "PASS",
            }
        ],
        "alerts": [],
        "node_artifacts": {
            "coder": ["/tmp/coder-artifact.json"],
            "reviewer": ["/tmp/reviewer-artifact.json"],
        },
    }
