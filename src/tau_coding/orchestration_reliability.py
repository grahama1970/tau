"""Read-only orchestration reliability receipt builder."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.course_correction import COURSE_CORRECTION_SCHEMA

ORCHESTRATION_RELIABILITY_SCHEMA = "tau.orchestration_reliability_receipt.v1"
ORCHESTRATION_RELIABILITY_RECEIPT_SCHEMA = ORCHESTRATION_RELIABILITY_SCHEMA
DAG_RECEIPT_SCHEMA = "tau.dag_receipt.v1"

GOAL_DRIFT_CODES = {
    "branch_goal_hash_divergence",
    "goal_hash_mismatch",
    "reviewer_goal_hash_mismatch",
}
ROUTE_DRIFT_CODES = {
    "missing_required_join",
    "missing_terminal_route",
    "unexpected_edge",
    "unexpected_node",
}
RETRY_BUDGET_CODES = {
    "brave_search_required_after_two_attempts",
    "max_attempts_exceeded",
}


def write_orchestration_reliability_receipt(
    *,
    output_path: Path,
    run_dir: Path | None = None,
    dag_receipt_path: Path | None = None,
    required_receipts: Iterable[Path] = (),
) -> dict[str, Any]:
    resolved_run_dir = run_dir.expanduser().resolve() if run_dir is not None else None
    resolved_dag_receipt_path = (
        dag_receipt_path.expanduser().resolve() if dag_receipt_path is not None else None
    )
    dag_receipt = (
        _read_json_with_path(resolved_dag_receipt_path)
        if resolved_dag_receipt_path is not None
        else _read_first_json(
            [
                resolved_run_dir / "dag-receipt.json",
                resolved_run_dir / "run" / "dag-receipt.json",
                resolved_run_dir / "run-receipt.json",
            ]
            if resolved_run_dir is not None
            else []
        )
    )
    search_root = resolved_run_dir or (
        resolved_dag_receipt_path.parent if resolved_dag_receipt_path is not None else None
    )
    herdr_gates = (
        _read_schema_glob(search_root, "tau.herdr_observation_gate_receipt.v1")
        if search_root is not None
        else []
    )
    course_corrections = (
        _read_schema_glob(search_root, "tau.course_correction.v1")
        if search_root is not None
        else []
    )
    embedded_course_corrections = [
        gate.get("course_correction")
        for gate in herdr_gates
        if isinstance(gate.get("course_correction"), dict)
    ]
    all_corrections = [*course_corrections, *embedded_course_corrections]
    dag_error = (
        dag_receipt.get("dag_error") if isinstance(dag_receipt.get("dag_error"), dict) else {}
    )
    dag_receipt_schema_valid = (
        not dag_receipt or dag_receipt.get("schema") == DAG_RECEIPT_SCHEMA
    )
    failure_code = str(dag_error.get("failure_code") or "")
    dag_alert_codes = _dag_alert_codes(dag_receipt)
    goal_hash_preserved = _goal_hash_preserved(dag_receipt, failure_code, dag_alert_codes)
    dag_routes_respected = _dag_routes_respected(failure_code, dag_alert_codes)
    unhandled_herdr_blocks = [
        gate
        for gate in herdr_gates
        if gate.get("status") == "BLOCKED" and not isinstance(gate.get("course_correction"), dict)
    ]
    missing_receipt_count = _missing_receipt_count(dag_receipt, herdr_gates, all_corrections)
    retry_budget_respected = failure_code not in RETRY_BUDGET_CODES and not (
        dag_alert_codes & RETRY_BUDGET_CODES
    )
    blocked_without_correction = bool(dag_receipt.get("status") == "BLOCKED") and not (
        dag_error or all_corrections
    )
    missing_artifacts = _missing_artifacts(dag_receipt)
    required_evidence_present = (
        not missing_artifacts and "missing_required_evidence" not in dag_alert_codes
    )
    course_correction_paths = _course_correction_paths(dag_receipt)
    course_correction_artifact_report = _course_correction_artifact_report(
        dag_receipt,
        course_correction_paths,
    )
    course_corrections_followed = _course_corrections_followed(
        dag_receipt,
        course_correction_artifact_report,
    )
    terminal_condition_valid = _terminal_condition_valid(
        dag_receipt,
        course_corrections_followed=course_corrections_followed,
        declared_course_corrections=bool(course_correction_artifact_report["declared"]),
    )
    required_receipt_report = _required_receipt_report(
        required_receipts,
        scope_root=search_root,
    )
    reliable = (
        bool(dag_receipt)
        and dag_receipt_schema_valid
        and goal_hash_preserved
        and dag_routes_respected
        and retry_budget_respected
        and required_evidence_present
        and terminal_condition_valid
        and course_corrections_followed
        and not required_receipt_report["missing"]
        and not required_receipt_report["invalid"]
        and not unhandled_herdr_blocks
        and not blocked_without_correction
    )
    alerts = _alerts(
        dag_receipt=dag_receipt,
        dag_receipt_schema_valid=dag_receipt_schema_valid,
        dag_error=dag_error,
        unhandled_herdr_blocks=unhandled_herdr_blocks,
        blocked_without_correction=blocked_without_correction,
        goal_hash_preserved=goal_hash_preserved,
        dag_routes_respected=dag_routes_respected,
        retry_budget_respected=retry_budget_respected,
        required_evidence_present=required_evidence_present,
        terminal_condition_valid=terminal_condition_valid,
        course_corrections_followed=course_corrections_followed,
        required_receipt_report=required_receipt_report,
    )
    payload = {
        "schema": ORCHESTRATION_RELIABILITY_SCHEMA,
        "ok": reliable,
        "status": "PASS" if reliable else "BLOCKED",
        "mocked": False,
        "live": bool(dag_receipt or herdr_gates or course_corrections),
        "provider_live": _provider_live(dag_receipt, herdr_gates),
        "run_dir": str(resolved_run_dir) if resolved_run_dir is not None else None,
        "dag_receipt_path": _path_for_payload(dag_receipt),
        "dag_receipt_schema": dag_receipt.get("schema"),
        "dag_receipt_schema_valid": dag_receipt_schema_valid,
        "dag_receipt_sha256": _payload_path_sha256_uri(dag_receipt),
        "dag_receipt_bytes": _payload_path_size(dag_receipt),
        "inspected_artifacts": _inspected_artifacts(
            ("dag_receipt", _path_for_payload(dag_receipt)),
        ),
        "dag_status": dag_receipt.get("status"),
        "dag_verdict": dag_receipt.get("verdict"),
        "dag_id": dag_receipt.get("dag_id"),
        "active_goal_hash": dag_receipt.get("active_goal_hash") or dag_receipt.get("goal_hash"),
        "goal_hash_preserved": goal_hash_preserved,
        "dag_routes_respected": dag_routes_respected,
        "unexpected_nodes": _alerts_by_code(dag_receipt, "unexpected_node"),
        "unexpected_edges": _alerts_by_code(dag_receipt, "unexpected_edge"),
        "missing_receipt_count": missing_receipt_count,
        "required_receipts_present": not required_receipt_report["missing"],
        "required_receipts": required_receipt_report,
        "required_evidence_present": required_evidence_present,
        "missing_artifacts": missing_artifacts,
        "unhandled_herdr_block_count": len(unhandled_herdr_blocks),
        "course_correction_count": len(all_corrections),
        "course_corrections_emitted": bool(course_correction_paths or all_corrections),
        "course_correction_artifacts": course_correction_paths,
        "course_correction_artifact_report": course_correction_artifact_report,
        "course_corrections_followed": course_corrections_followed,
        "retry_budget_respected": retry_budget_respected,
        "terminal_condition_valid": terminal_condition_valid,
        "reliable_orchestration": reliable,
        "agent_truthfulness": "NOT_CLAIMED",
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "artifact_counts": {
            "herdr_observation_gate": len(herdr_gates),
            "course_correction": len(course_corrections),
            "embedded_course_correction": len(embedded_course_corrections),
        },
        "proof_scope": {
            "proves": [
                "Tau inspected local orchestration receipts from one DAG run.",
                "Tau reported goal continuity, route discipline, artifacts, course corrections, "
                "retry budget, and terminal condition separately from code correctness.",
                "Tau did not claim agent truthfulness or task correctness.",
            ],
            "does_not_prove": [
                "Code correctness.",
                "Agent truthfulness.",
                "Provider/model semantic quality.",
                "Human acceptance.",
                "GitHub mutation or ticket closure.",
                "Future route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _alerts(
    *,
    dag_receipt: dict[str, Any],
    dag_receipt_schema_valid: bool,
    dag_error: dict[str, Any],
    unhandled_herdr_blocks: list[dict[str, Any]],
    blocked_without_correction: bool,
    goal_hash_preserved: bool,
    dag_routes_respected: bool,
    retry_budget_respected: bool,
    required_evidence_present: bool,
    terminal_condition_valid: bool,
    course_corrections_followed: bool,
    required_receipt_report: dict[str, list[str]],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if not dag_receipt:
        alerts.append({"severity": "BLOCK", "code": "missing_dag_or_run_receipt"})
    elif not dag_receipt_schema_valid:
        alerts.append({"severity": "BLOCK", "code": "invalid_dag_receipt_schema"})
    failure_code = str(dag_error.get("failure_code") or "")
    if failure_code in {"goal_hash_mismatch", "goal_changed", "unexpected_edge", "unexpected_node"}:
        alerts.append({"severity": "BLOCK", "code": failure_code})
    if blocked_without_correction:
        alerts.append({"severity": "BLOCK", "code": "blocked_without_course_correction"})
    if not goal_hash_preserved:
        alerts.append({"severity": "BLOCK", "code": "goal_hash_not_preserved"})
    if not dag_routes_respected:
        alerts.append({"severity": "BLOCK", "code": "dag_routes_not_respected"})
    if not retry_budget_respected:
        alerts.append({"severity": "BLOCK", "code": "retry_budget_not_respected"})
    if not required_evidence_present:
        alerts.append({"severity": "BLOCK", "code": "required_evidence_missing"})
    if not terminal_condition_valid:
        alerts.append({"severity": "BLOCK", "code": "terminal_condition_invalid"})
    if not course_corrections_followed:
        alerts.append({"severity": "BLOCK", "code": "course_correction_ignored"})
    if required_receipt_report["missing"]:
        alerts.append({"severity": "BLOCK", "code": "required_receipt_missing"})
    if required_receipt_report["invalid"]:
        alerts.append({"severity": "BLOCK", "code": "required_receipt_invalid"})
    for gate in unhandled_herdr_blocks:
        alerts.append(
            {
                "severity": "BLOCK",
                "code": "unhandled_herdr_observation_block",
                "path": gate.get("_path"),
            }
        )
    return alerts


def _goal_hash_preserved(
    dag_receipt: Mapping[str, Any],
    failure_code: str,
    dag_alert_codes: set[str],
) -> bool:
    active_goal_hash = dag_receipt.get("active_goal_hash") or dag_receipt.get("goal_hash")
    return bool(active_goal_hash) and failure_code not in {
        "goal_changed",
        "goal_hash_mismatch",
    } and not (dag_alert_codes & GOAL_DRIFT_CODES)


def _dag_routes_respected(failure_code: str, dag_alert_codes: set[str]) -> bool:
    return failure_code not in {"unexpected_edge", "unexpected_node"} and not (
        dag_alert_codes & ROUTE_DRIFT_CODES
    )


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


def _dag_alert_codes(dag_receipt: Mapping[str, Any]) -> set[str]:
    return {
        str(alert.get("code"))
        for alert in dag_receipt.get("alerts", [])
        if isinstance(alert, Mapping) and alert.get("code")
    }


def _alerts_by_code(dag_receipt: Mapping[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        dict(alert)
        for alert in dag_receipt.get("alerts", [])
        if isinstance(alert, Mapping) and alert.get("code") == code
    ]


def _missing_artifacts(dag_receipt: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for value in dag_receipt.get("artifacts", []):
        if isinstance(value, str) and value and not Path(value).expanduser().exists():
            missing.append(value)
    return missing


def _course_correction_paths(dag_receipt: Mapping[str, Any]) -> list[str]:
    return [
        value
        for value in dag_receipt.get("course_correction_artifacts", [])
        if isinstance(value, str) and value
    ]


def _course_corrections_followed(
    dag_receipt: Mapping[str, Any],
    course_correction_artifact_report: dict[str, Any],
) -> bool:
    if not course_correction_artifact_report["declared"]:
        return True
    if dag_receipt.get("status") == "PASS":
        return False
    return not (
        course_correction_artifact_report["missing"]
        or course_correction_artifact_report["invalid"]
    )


def _course_correction_artifact_report(
    dag_receipt: Mapping[str, Any],
    course_correction_paths: list[str],
) -> dict[str, Any]:
    active_goal_hash = dag_receipt.get("active_goal_hash") or dag_receipt.get("goal_hash")
    report: dict[str, Any] = {
        "declared": list(course_correction_paths),
        "valid": [],
        "missing": [],
        "invalid": [],
    }
    for value in course_correction_paths:
        path = Path(value).expanduser()
        resolved = str(path.resolve())
        if not path.exists():
            report["missing"].append(resolved)
            continue
        payload = _read_json(path)
        reason = _course_correction_invalid_reason(payload, active_goal_hash)
        if reason:
            report["invalid"].append({"path": resolved, "reason": reason})
            continue
        report["valid"].append(_artifact_descriptor(path))
    return report


def _course_correction_invalid_reason(
    payload: Mapping[str, Any],
    active_goal_hash: object,
) -> str | None:
    if payload.get("schema") != COURSE_CORRECTION_SCHEMA:
        return "schema_mismatch"
    if payload.get("status") != "REQUIRED":
        return "status_not_required"
    if payload.get("next_allowed") is not False:
        return "next_allowed_not_false"
    if payload.get("input_valid") is not True:
        return "input_not_valid"
    if active_goal_hash and not payload.get("goal_hash"):
        return "missing_goal_hash"
    if active_goal_hash and payload.get("goal_hash") != active_goal_hash:
        return "goal_hash_mismatch"
    if not payload.get("required_next_action"):
        return "missing_required_next_action"
    return None


def _terminal_condition_valid(
    dag_receipt: Mapping[str, Any],
    *,
    course_corrections_followed: bool,
    declared_course_corrections: bool,
) -> bool:
    if dag_receipt.get("status") == "BLOCKED":
        return declared_course_corrections and course_corrections_followed
    if dag_receipt.get("status") != "PASS":
        return False
    terminal_nodes = {
        str(value)
        for value in dag_receipt.get("terminal_nodes", [])
        if isinstance(value, str) and value
    }
    if not terminal_nodes:
        return True
    observed_edges = dag_receipt.get("observed_edges", [])
    if not isinstance(observed_edges, list):
        return False
    return any(
        isinstance(edge, Mapping) and str(edge.get("to_node")) in terminal_nodes
        for edge in observed_edges
    )


def _required_receipt_report(
    required_receipts: Iterable[Path],
    *,
    scope_root: Path | None,
) -> dict[str, Any]:
    present: list[str] = []
    missing: list[str] = []
    present_artifacts: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    resolved_scope = scope_root.expanduser().resolve() if scope_root is not None else None
    for receipt in required_receipts:
        resolved = receipt.expanduser().resolve()
        if resolved.exists():
            present.append(str(resolved))
            descriptor = _receipt_artifact_descriptor(resolved)
            present_artifacts.append(descriptor)
            reason = _required_receipt_invalid_reason(
                descriptor,
                path=resolved,
                scope_root=resolved_scope,
            )
            if reason:
                invalid.append({"path": str(resolved), "reason": reason})
        else:
            missing.append(str(resolved))
    return {
        "present": present,
        "missing": missing,
        "invalid": invalid,
        "present_artifacts": present_artifacts,
    }


def _required_receipt_invalid_reason(
    descriptor: Mapping[str, Any],
    *,
    path: Path,
    scope_root: Path | None,
) -> str | None:
    if scope_root is not None:
        try:
            path.relative_to(scope_root)
        except ValueError:
            return "outside_run_scope"
    if descriptor.get("schema") is None:
        return "unreadable_or_missing_schema"
    if descriptor.get("ok") is not True or descriptor.get("status") != "PASS":
        return "status_not_pass"
    if descriptor.get("mocked") is not False:
        return "mocked"
    if descriptor.get("live") is not True:
        return "not_live"
    return None


def _read_first_json(paths: list[Path]) -> dict[str, Any]:
    for path in paths:
        payload = _read_json(path)
        if payload:
            payload["_path"] = str(path)
            return payload
    return {}


def _read_json_with_path(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if payload:
        payload["_path"] = str(path)
    return payload


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


def _payload_path_sha256_uri(payload: dict[str, Any]) -> str | None:
    path = _path_for_payload(payload)
    if path is None:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return None
    return f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}"


def _payload_path_size(payload: dict[str, Any]) -> int | None:
    path = _path_for_payload(payload)
    if path is None:
        return None
    resolved = Path(path).expanduser().resolve()
    return resolved.stat().st_size if resolved.exists() else None


def _inspected_artifacts(*items: tuple[str, str | None]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for label, raw_path in items:
        if raw_path is None:
            continue
        resolved = Path(raw_path).expanduser().resolve()
        if not resolved.exists():
            continue
        descriptor = _artifact_descriptor(resolved)
        descriptor["label"] = label
        artifacts.append(descriptor)
    return artifacts


def _artifact_descriptor(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "exists": True,
        "sha256": f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}",
        "bytes": resolved.stat().st_size,
    }


def _receipt_artifact_descriptor(path: Path) -> dict[str, Any]:
    descriptor = _artifact_descriptor(path)
    payload = _read_json(path)
    if payload:
        descriptor.update(
            {
                "schema": payload.get("schema"),
                "status": payload.get("status"),
                "ok": payload.get("ok"),
                "mocked": payload.get("mocked"),
                "live": payload.get("live"),
                "provider_live": payload.get("provider_live"),
            }
        )
    return descriptor


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
