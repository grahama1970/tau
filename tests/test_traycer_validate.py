from __future__ import annotations

import json
from pathlib import Path

from tau_coding.traycer.cli import parse_traycer_validate_cli_args, traycer_validate_command
from tau_coding.traycer.models import TraycerValidationOptions
from tau_coding.traycer.validate import validate_traycer_trace

GOAL_HASH = "sha256:active-goal"


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _trace_row(
    sequence: int = 1,
    *,
    goal_hash: str = GOAL_HASH,
    target: str = "issue:44",
    agent: str = "coder",
) -> dict[str, object]:
    return {
        "schema": "tau.subagent_trace.v1",
        "run_id": "traycer-test-run",
        "trace_id": f"trace-{sequence:04d}",
        "sequence": sequence,
        "ts": "2026-07-04T16:12:03Z",
        "agent": {"name": agent, "executor": "local"},
        "goal": {
            "goal_id": "tau-traycer",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "github": {"repo": "grahama1970/tau", "target": target},
        "phase": "plan",
        "event": {"kind": "intent_declared"},
    }


def _evidence_row(
    sequence: int = 2,
    *,
    supports: list[str] | None = None,
    confidence: str = "deterministic",
) -> dict[str, object]:
    return {
        "schema": "tau.evidence_claim.v1",
        "run_id": "traycer-test-run",
        "claim_id": f"ev-{sequence:04d}",
        "sequence": sequence,
        "ts": "2026-07-04T16:18:11Z",
        "agent": {"name": "coder"},
        "goal": {"goal_hash": GOAL_HASH},
        "claim": {
            "type": "test_result",
            "statement": "Focused tests passed",
            "artifact": "/tmp/focused-tests.txt",
            "verifier": {
                "kind": "command",
                "command": "uv run pytest tests/test_traycer_validate.py -q",
                "exit_code": 0,
            },
            "supports_required_evidence": supports or ["focused_tests_pass"],
            "confidence": confidence,
        },
    }


def _handoff(
    *,
    goal_hash: str = GOAL_HASH,
    target: str = "issue:44",
    previous_subagent: str = "coder",
    next_agent: str = "reviewer",
    required_evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": target},
        "goal": {
            "goal_id": "tau-traycer",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "previous_subagent": previous_subagent,
        "context": {
            "summary": "Coder completed an offline Traycer fixture.",
            "artifacts": [],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Fixture completed.",
            "evidence": [],
        },
        "rationale": "Route to reviewer for inspection.",
        "next_agent": {"name": next_agent, "executor": "local", "reason": "Review evidence."},
        "required_evidence": required_evidence or ["focused_tests_pass"],
        "stop_condition": "Stop if reviewer finds an unresolved monitor alert.",
    }


def _required_evidence(
    *ids: str,
    severity: str = "REVIEW",
    required_confidence: str | None = None,
) -> dict[str, object]:
    required = []
    for item_id in ids:
        item: dict[str, object] = {"id": item_id, "severity": severity}
        if required_confidence is not None:
            item["required_confidence"] = required_confidence
        required.append(item)
    return {
        "schema": "tau.required_evidence.v1",
        "goal_hash": GOAL_HASH,
        "required": required,
    }


def _validate(
    tmp_path: Path,
    *,
    trace_rows: list[dict[str, object]] | None = None,
    handoff: dict[str, object] | None = None,
    required_evidence: dict[str, object] | None = None,
    advisory: bool = False,
) -> dict[str, object]:
    trace_path = _write_jsonl(
        tmp_path / "trace.jsonl",
        trace_rows or [_trace_row(), _evidence_row()],
    )
    handoff_path = _write_json(tmp_path / "final-handoff.json", handoff or _handoff())
    receipt_path = tmp_path / "monitor-receipt.json"
    required_path = None
    if required_evidence is not None:
        required_path = _write_json(tmp_path / "required-evidence.json", required_evidence)
    return validate_traycer_trace(
        TraycerValidationOptions(
            trace_path=trace_path,
            handoff_path=handoff_path,
            active_goal_hash=GOAL_HASH,
            required_evidence_path=required_path,
            advisory_final_handoff_evidence=advisory,
            receipt_path=receipt_path,
        )
    )


def _codes(receipt: dict[str, object]) -> set[str]:
    alerts = receipt["alerts"]
    assert isinstance(alerts, list)
    return {str(alert["violation"]["code"]) for alert in alerts}


def test_valid_trace_and_handoff_passes(tmp_path: Path) -> None:
    receipt = _validate(tmp_path, required_evidence=_required_evidence("focused_tests_pass"))

    assert receipt["schema"] == "tau.monitor_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["verdict"]["next_allowed"] is True
    assert receipt["summary"]["blocking_alert_count"] == 0
    assert (tmp_path / "monitor-receipt.json").exists()


def test_warn_does_not_make_ok_false(tmp_path: Path) -> None:
    receipt = _validate(
        tmp_path,
        trace_rows=[_trace_row(), _evidence_row(confidence="claimed")],
        required_evidence=_required_evidence("focused_tests_pass"),
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["summary"]["max_severity"] == "WARN"
    assert "weak_evidence_confidence" in _codes(receipt)


def test_review_makes_ok_false_and_next_not_allowed(tmp_path: Path) -> None:
    receipt = _validate(
        tmp_path,
        trace_rows=[_trace_row(), _evidence_row(supports=["other"])],
        required_evidence=_required_evidence("focused_tests_pass"),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "REVIEW"
    assert receipt["verdict"]["next_allowed"] is False
    assert receipt["verdict"]["review_required"] is True
    assert "missing_required_evidence" in _codes(receipt)


def test_reroute_makes_ok_false(tmp_path: Path) -> None:
    scope_expansion = _trace_row()
    scope_expansion["event"] = {"kind": "scope_expansion_requested"}
    receipt = _validate(
        tmp_path,
        trace_rows=[scope_expansion, _evidence_row()],
        required_evidence=_required_evidence("focused_tests_pass"),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "REROUTE"
    assert receipt["verdict"]["next_allowed"] is False
    assert "scope_expansion_detected" in _codes(receipt)


def test_block_required_evidence_policy_makes_ok_false(tmp_path: Path) -> None:
    receipt = _validate(
        tmp_path,
        trace_rows=[_trace_row(), _evidence_row(supports=["other"])],
        required_evidence=_required_evidence("focused_tests_pass", severity="BLOCK"),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["summary"]["blocking_alert_count"] == 1


def test_goal_hash_mismatch_blocks(tmp_path: Path) -> None:
    receipt = _validate(
        tmp_path,
        trace_rows=[_trace_row(goal_hash="sha256:wrong"), _evidence_row()],
        required_evidence=_required_evidence("focused_tests_pass"),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "goal_hash_mismatch" in _codes(receipt)


def test_target_changed_blocks(tmp_path: Path) -> None:
    receipt = _validate(
        tmp_path,
        trace_rows=[_trace_row(), _evidence_row()],
        handoff=_handoff(target="issue:45"),
        required_evidence=_required_evidence("focused_tests_pass"),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "target_changed" in _codes(receipt)


def test_malformed_jsonl_row_blocks(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(json.dumps(_trace_row()) + "\n{not-json\n", encoding="utf-8")
    handoff_path = _write_json(tmp_path / "final-handoff.json", _handoff())
    required_path = _write_json(
        tmp_path / "required-evidence.json",
        _required_evidence("focused_tests_pass"),
    )

    receipt = validate_traycer_trace(
        TraycerValidationOptions(
            trace_path=trace_path,
            handoff_path=handoff_path,
            active_goal_hash=GOAL_HASH,
            required_evidence_path=required_path,
            receipt_path=tmp_path / "monitor-receipt.json",
        )
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "malformed_jsonl" in _codes(receipt)


def test_final_handoff_evidence_fallback_rejected_in_strict_mode(tmp_path: Path) -> None:
    receipt = _validate(tmp_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["evidence_authority"] is None
    assert "required_evidence_authority_missing" in _codes(receipt)


def test_final_handoff_evidence_fallback_allowed_in_advisory_mode(tmp_path: Path) -> None:
    receipt = _validate(tmp_path, advisory=True)

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["evidence_authority"] == "final_handoff_fallback"
    assert receipt["authority_warning"]


def test_receipt_hash_and_artifact_summary_written(tmp_path: Path) -> None:
    receipt = _validate(tmp_path, required_evidence=_required_evidence("focused_tests_pass"))

    persisted = json.loads((tmp_path / "monitor-receipt.json").read_text(encoding="utf-8"))
    assert persisted["trace"]["sha256"].startswith("sha256:")
    assert persisted["final_handoff"]["sha256"].startswith("sha256:")
    assert persisted["required_evidence"]["sha256"].startswith("sha256:")
    assert str(tmp_path / "monitor-receipt.json") in receipt["artifacts"]


def test_cli_parser_and_command_write_receipt(tmp_path: Path) -> None:
    trace_path = _write_jsonl(tmp_path / "trace.jsonl", [_trace_row(), _evidence_row()])
    handoff_path = _write_json(tmp_path / "final-handoff.json", _handoff())
    required_path = _write_json(
        tmp_path / "required-evidence.json",
        _required_evidence("focused_tests_pass"),
    )
    receipt_path = tmp_path / "monitor-receipt.json"

    options = parse_traycer_validate_cli_args(
        [
            "--trace",
            str(trace_path),
            "--handoff",
            str(handoff_path),
            "--active-goal-hash",
            GOAL_HASH,
            "--required-evidence",
            str(required_path),
            "--receipt",
            str(receipt_path),
        ]
    )
    receipt = traycer_validate_command(options)

    assert receipt["ok"] is True
    assert receipt_path.exists()
