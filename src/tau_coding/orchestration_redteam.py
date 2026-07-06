"""Deterministic red-team checks for Tau orchestration reliability."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.herdr_observation_gate import write_herdr_observation_gate_receipt
from tau_coding.orchestration_reliability import write_orchestration_reliability_receipt

ORCHESTRATION_REDTEAM_SCHEMA = "tau.orchestration_redteam_receipt.v1"


def run_orchestration_redteam(*, run_dir: Path) -> dict[str, Any]:
    """Run deterministic fixtures against Tau's control-loop reliability gates."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    attempts = [
        _attempt_herdr_state_gate(resolved_run_dir, "herdr_stale", state="stale"),
        _attempt_herdr_state_gate(
            resolved_run_dir,
            "provider_auth_required",
            state="auth_required",
        ),
        _attempt_herdr_state_gate(
            resolved_run_dir,
            "provider_interstitial",
            state="interstitial",
        ),
        _attempt_herdr_state_gate(resolved_run_dir, "provider_crashed", state="crashed"),
        _attempt_receipt_timeout(resolved_run_dir),
        _attempt_wrong_pane_binding(resolved_run_dir),
        _attempt_unhandled_blocked_run(resolved_run_dir),
        _attempt_unhandled_herdr_block(resolved_run_dir),
    ]
    ok = all(attempt["status"] == "PASS" for attempt in attempts)
    receipt = {
        "schema": ORCHESTRATION_REDTEAM_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "attempt_count": len(attempts),
        "passed_attempt_count": sum(1 for attempt in attempts if attempt["status"] == "PASS"),
        "attempts": attempts,
        "receipt_path": str(resolved_run_dir / "orchestration-redteam-receipt.json"),
        "proof_scope": {
            "proves": [
                "Tau ran deterministic adversarial fixtures against orchestration gates.",
                "Each listed attempt passed only if Tau emitted the expected fail-closed "
                "course-correction or reliability alert.",
                "No external provider, Herdr workspace mutation, GitHub mutation, Memory write, "
                "browser, or Docker command was executed.",
            ],
            "does_not_prove": [
                "Exhaustive orchestration failure coverage.",
                "Provider/model semantic quality.",
                "A course-correction action was executed.",
                "Future route correctness.",
                "Live Herdr monitor snapshot availability.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_run_dir / "orchestration-redteam-receipt.json", receipt)
    return receipt


def _attempt_herdr_state_gate(root: Path, attempt_id: str, *, state: str) -> dict[str, Any]:
    attempt_dir = root / attempt_id
    snapshot_path = _write_snapshot(attempt_dir, state=state)
    receipt_path = attempt_dir / "herdr-observation-gate.json"
    receipt = write_herdr_observation_gate_receipt(
        receipt_path,
        snapshot_path=snapshot_path,
        expected_workspace_id="w-redteam",
        expected_pane_id="w-redteam:p1",
        expected_terminal_id="term-redteam",
        run_id=f"run-{attempt_id}",
        dag_id="dag-orchestration-redteam",
        goal_hash="sha256:redteam",
        node_id="coder",
        agent="coder",
        attempt=1,
        mocked=False,
        live=False,
        provider_live=False,
    )
    return _course_correction_attempt_summary(
        attempt_id,
        receipt,
        expected_trigger=attempt_id,
        receipt_path=receipt_path,
    )


def _attempt_receipt_timeout(root: Path) -> dict[str, Any]:
    attempt_id = "receipt_timeout"
    attempt_dir = root / attempt_id
    snapshot_path = _write_snapshot(attempt_dir, state="running")
    receipt_path = attempt_dir / "herdr-observation-gate.json"
    expected_receipt_path = attempt_dir / "missing-node-receipt.json"
    receipt = write_herdr_observation_gate_receipt(
        receipt_path,
        snapshot_path=snapshot_path,
        expected_receipt_path=expected_receipt_path,
        expected_workspace_id="w-redteam",
        expected_pane_id="w-redteam:p1",
        expected_terminal_id="term-redteam",
        run_id="run-receipt-timeout",
        dag_id="dag-orchestration-redteam",
        goal_hash="sha256:redteam",
        node_id="coder",
        agent="coder",
        attempt=2,
        receipt_overdue=True,
        receipt_timeout_seconds=1,
        mocked=False,
        live=False,
        provider_live=False,
    )
    return _course_correction_attempt_summary(
        attempt_id,
        receipt,
        expected_trigger="receipt_timeout",
        receipt_path=receipt_path,
    )


def _attempt_wrong_pane_binding(root: Path) -> dict[str, Any]:
    attempt_id = "provider_receipt_wrong_pane"
    attempt_dir = root / attempt_id
    snapshot_path = _write_snapshot(attempt_dir, state="ready", pane_id="w-redteam:p999")
    receipt_path = attempt_dir / "herdr-observation-gate.json"
    receipt = write_herdr_observation_gate_receipt(
        receipt_path,
        snapshot_path=snapshot_path,
        expected_workspace_id="w-redteam",
        expected_pane_id="w-redteam:p1",
        expected_terminal_id="term-redteam",
        run_id="run-wrong-pane",
        dag_id="dag-orchestration-redteam",
        goal_hash="sha256:redteam",
        node_id="coder",
        agent="coder",
        attempt=1,
        mocked=False,
        live=False,
        provider_live=False,
    )
    return _course_correction_attempt_summary(
        attempt_id,
        receipt,
        expected_trigger="herdr_binding_mismatch",
        receipt_path=receipt_path,
    )


def _attempt_unhandled_blocked_run(root: Path) -> dict[str, Any]:
    attempt_id = "blocked_run_without_course_correction"
    attempt_dir = root / attempt_id
    _write_json(
        attempt_dir / "dag-receipt.json",
        {
            "schema": "tau.dag_run_receipt.v1",
            "status": "BLOCKED",
            "verdict": "BLOCKED",
            "dag_id": "dag-orchestration-redteam",
            "goal_hash": "sha256:redteam",
        },
    )
    receipt_path = attempt_dir / "orchestration-reliability.json"
    receipt = write_orchestration_reliability_receipt(
        run_dir=attempt_dir,
        output_path=receipt_path,
    )
    return _reliability_attempt_summary(
        attempt_id,
        receipt,
        expected_alert_code="blocked_without_course_correction",
        receipt_path=receipt_path,
    )


def _attempt_unhandled_herdr_block(root: Path) -> dict[str, Any]:
    attempt_id = "unhandled_herdr_observation_block"
    attempt_dir = root / attempt_id
    _write_json(
        attempt_dir / "dag-receipt.json",
        {
            "schema": "tau.dag_run_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "dag_id": "dag-orchestration-redteam",
            "goal_hash": "sha256:redteam",
        },
    )
    _write_json(
        attempt_dir / "bad-herdr-gate.json",
        {
            "schema": "tau.herdr_observation_gate_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "dag_id": "dag-orchestration-redteam",
            "goal_hash": "sha256:redteam",
            "node_id": "coder",
        },
    )
    receipt_path = attempt_dir / "orchestration-reliability.json"
    receipt = write_orchestration_reliability_receipt(
        run_dir=attempt_dir,
        output_path=receipt_path,
    )
    return _reliability_attempt_summary(
        attempt_id,
        receipt,
        expected_alert_code="unhandled_herdr_observation_block",
        receipt_path=receipt_path,
    )


def _course_correction_attempt_summary(
    attempt_id: str,
    receipt: dict[str, Any],
    *,
    expected_trigger: str,
    receipt_path: Path,
) -> dict[str, Any]:
    course_correction = receipt.get("course_correction")
    trigger = (
        course_correction.get("trigger")
        if isinstance(course_correction, dict)
        else None
    )
    blocked = receipt.get("ok") is False and trigger == expected_trigger
    return {
        "attempt_id": attempt_id,
        "status": "PASS" if blocked else "FAIL",
        "expected_trigger": expected_trigger,
        "observed_trigger": trigger,
        "receipt_schema": receipt.get("schema"),
        "receipt_status": receipt.get("status"),
        "receipt_path": str(receipt_path),
    }


def _reliability_attempt_summary(
    attempt_id: str,
    receipt: dict[str, Any],
    *,
    expected_alert_code: str,
    receipt_path: Path,
) -> dict[str, Any]:
    alert_codes = [
        alert.get("code")
        for alert in receipt.get("alerts", [])
        if isinstance(alert, dict) and isinstance(alert.get("code"), str)
    ]
    blocked = receipt.get("ok") is False and expected_alert_code in alert_codes
    return {
        "attempt_id": attempt_id,
        "status": "PASS" if blocked else "FAIL",
        "expected_alert_code": expected_alert_code,
        "observed_alert_codes": alert_codes,
        "receipt_schema": receipt.get("schema"),
        "receipt_status": receipt.get("status"),
        "receipt_path": str(receipt_path),
    }


def _write_snapshot(
    root: Path,
    *,
    state: str,
    workspace_id: str = "w-redteam",
    pane_id: str = "w-redteam:p1",
    terminal_id: str = "term-redteam",
) -> Path:
    path = root / "herdr-monitor-snapshot.json"
    _write_json(
        path,
        {
            "schema": "herdr.monitor_snapshot.v1",
            "state": state,
            "workspace_id": workspace_id,
            "pane_id": pane_id,
            "terminal_id": terminal_id,
            "agent_name": "redteam-coder",
            "process_alive": state not in {"crashed", "exited"},
            "visible_log_path": str(root / "visible.log"),
            "last_visible_output_at": _utc_stamp(),
        },
    )
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
