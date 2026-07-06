"""Read-only orchestration reliability receipt builder."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ORCHESTRATION_RELIABILITY_SCHEMA = "tau.orchestration_reliability_receipt.v1"


def write_orchestration_reliability_receipt(
    *,
    run_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    resolved_run_dir = run_dir.expanduser().resolve()
    dag_receipt = _read_first_json(
        [
            resolved_run_dir / "dag-receipt.json",
            resolved_run_dir / "run" / "dag-receipt.json",
            resolved_run_dir / "run-receipt.json",
        ]
    )
    herdr_gates = _read_schema_glob(
        resolved_run_dir,
        "tau.herdr_observation_gate_receipt.v1",
    )
    course_corrections = _read_schema_glob(resolved_run_dir, "tau.course_correction.v1")
    embedded_course_corrections = [
        gate.get("course_correction")
        for gate in herdr_gates
        if isinstance(gate.get("course_correction"), dict)
    ]
    all_corrections = [*course_corrections, *embedded_course_corrections]
    dag_error = dag_receipt.get("dag_error") if isinstance(dag_receipt.get("dag_error"), dict) else {}
    failure_code = str(dag_error.get("failure_code") or "")
    goal_hash_preserved = failure_code not in {"goal_hash_mismatch", "goal_changed"}
    dag_routes_respected = failure_code not in {"unexpected_edge", "unexpected_node"}
    unhandled_herdr_blocks = [
        gate
        for gate in herdr_gates
        if gate.get("status") == "BLOCKED" and not isinstance(gate.get("course_correction"), dict)
    ]
    missing_receipt_count = _missing_receipt_count(dag_receipt, herdr_gates, all_corrections)
    retry_budget_respected = failure_code not in {"max_attempts_exceeded"}
    blocked_without_correction = bool(dag_receipt.get("status") == "BLOCKED") and not (
        dag_error or all_corrections
    )
    reliable = (
        bool(dag_receipt)
        and goal_hash_preserved
        and dag_routes_respected
        and retry_budget_respected
        and not unhandled_herdr_blocks
        and not blocked_without_correction
    )
    payload = {
        "schema": ORCHESTRATION_RELIABILITY_SCHEMA,
        "ok": reliable,
        "status": "PASS" if reliable else "BLOCKED",
        "mocked": False,
        "live": bool(dag_receipt or herdr_gates or course_corrections),
        "provider_live": _provider_live(dag_receipt, herdr_gates),
        "run_dir": str(resolved_run_dir),
        "dag_receipt_path": _path_for_payload(dag_receipt),
        "dag_status": dag_receipt.get("status"),
        "dag_verdict": dag_receipt.get("verdict"),
        "dag_id": dag_receipt.get("dag_id"),
        "goal_hash_preserved": goal_hash_preserved,
        "dag_routes_respected": dag_routes_respected,
        "missing_receipt_count": missing_receipt_count,
        "unhandled_herdr_block_count": len(unhandled_herdr_blocks),
        "course_correction_count": len(all_corrections),
        "course_corrections_followed": "NOT_EVALUATED",
        "retry_budget_respected": retry_budget_respected,
        "terminal_condition_valid": reliable,
        "reliable_orchestration": reliable,
        "agent_truthfulness": "NOT_CLAIMED",
        "alerts": _alerts(
            dag_receipt=dag_receipt,
            dag_error=dag_error,
            unhandled_herdr_blocks=unhandled_herdr_blocks,
            blocked_without_correction=blocked_without_correction,
        ),
        "artifact_counts": {
            "herdr_observation_gate": len(herdr_gates),
            "course_correction": len(course_corrections),
            "embedded_course_correction": len(embedded_course_corrections),
        },
        "proof_scope": {
            "proves": [
                "Tau inspected local orchestration receipts from one run directory.",
                "Tau distinguished controlled blocked states from unhandled blocked states.",
                "Tau did not claim agent truthfulness or task correctness.",
            ],
            "does_not_prove": [
                "The agent answer is semantically correct.",
                "A course-correction action was executed.",
                "Provider/model semantic quality.",
                "Future route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _alerts(
    *,
    dag_receipt: dict[str, Any],
    dag_error: dict[str, Any],
    unhandled_herdr_blocks: list[dict[str, Any]],
    blocked_without_correction: bool,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if not dag_receipt:
        alerts.append({"severity": "BLOCK", "code": "missing_dag_or_run_receipt"})
    failure_code = str(dag_error.get("failure_code") or "")
    if failure_code in {"goal_hash_mismatch", "goal_changed", "unexpected_edge", "unexpected_node"}:
        alerts.append({"severity": "BLOCK", "code": failure_code})
    if blocked_without_correction:
        alerts.append({"severity": "BLOCK", "code": "blocked_without_course_correction"})
    for gate in unhandled_herdr_blocks:
        alerts.append(
            {
                "severity": "BLOCK",
                "code": "unhandled_herdr_observation_block",
                "path": gate.get("_path"),
            }
        )
    return alerts


def _missing_receipt_count(
    dag_receipt: dict[str, Any],
    herdr_gates: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> int:
    count = 0
    if "receipt_timeout" in str(dag_receipt.get("verdict") or "").lower():
        count += 1
    count += sum(1 for gate in herdr_gates if gate.get("receipt_missing") is True)
    count += sum(1 for item in corrections if item.get("trigger") == "receipt_timeout")
    return count


def _read_first_json(paths: list[Path]) -> dict[str, Any]:
    for path in paths:
        payload = _read_json(path)
        if payload:
            payload["_path"] = str(path)
            return payload
    return {}


def _read_schema_glob(run_dir: Path, schema: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*.json")):
        payload = _read_json(path)
        if payload.get("schema") == schema:
            payload["_path"] = str(path)
            found.append(payload)
    return found


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _provider_live(dag_receipt: dict[str, Any], herdr_gates: list[dict[str, Any]]) -> bool:
    if dag_receipt.get("provider_live") is True:
        return True
    return any(gate.get("provider_live") is True for gate in herdr_gates)


def _path_for_payload(payload: dict[str, Any]) -> str | None:
    value = payload.get("_path")
    return str(value) if isinstance(value, str) and value else None


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
