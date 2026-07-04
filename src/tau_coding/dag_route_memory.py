"""Local route-memory reinforcement candidates derived from DAG signal receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA = "tau.dag_route_memory_candidate_receipt.v1"
SOURCE_DAG_SIGNAL_RECEIPT_SCHEMA = "tau.dag_signal_receipt.v1"


def write_dag_route_memory_candidate_receipt(
    *,
    signal_receipt_path: Path,
    receipt_path: Path,
    min_confidence: float = 1.0,
) -> dict[str, Any]:
    """Write local route-memory candidates without syncing them to Memory."""

    resolved_signal_path = signal_receipt_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    signal = _read_json_object(resolved_signal_path, label="DAG signal receipt")
    if signal.get("schema") != SOURCE_DAG_SIGNAL_RECEIPT_SCHEMA:
        raise RuntimeError(
            f"DAG signal receipt schema must be {SOURCE_DAG_SIGNAL_RECEIPT_SCHEMA}: "
            f"{resolved_signal_path}"
        )
    if min_confidence < 0 or min_confidence > 1:
        raise RuntimeError("--min-confidence must be between 0 and 1")

    alerts = _gate_alerts(signal)
    accepted, rejected = _gate_candidates(signal, min_confidence=min_confidence)
    if not accepted and not alerts:
        alerts.append(
            _alert(
                "BLOCK",
                "no_accepted_candidates",
                "No route-memory candidates met the quality gate.",
                {"min_confidence": min_confidence},
            )
        )
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_signal_receipt": str(resolved_signal_path),
        "source_signal_receipt_sha256": f"sha256:{_sha256(resolved_signal_path)}",
        "receipt_path": str(resolved_receipt_path),
        "dag_id": signal.get("dag_id"),
        "goal_hash": signal.get("goal_hash"),
        "scheduler": signal.get("scheduler"),
        "min_confidence": min_confidence,
        "accepted_candidates": accepted,
        "rejected_candidates": rejected,
        "accepted_candidate_count": len(accepted),
        "rejected_candidate_count": len(rejected),
        "alerts": alerts,
        "memory_sync": False,
        "sync_status": "NOT_SYNCED",
        "sync_reason": "local_candidate_receipt_only",
        "route_mutation": False,
        "dag_mutation": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "DAG signal receipt was inspected deterministically.",
                "Route reinforcement candidates were quality-gated locally.",
                "Accepted candidates were not synced to Memory or applied to routing.",
            ],
            "does_not_prove": [
                "Memory route learning.",
                "Runtime route mutation.",
                "Adaptive DAG expansion application.",
                "Provider/model semantic quality.",
                "Future route correctness.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def _gate_alerts(signal: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if signal.get("ok") is not True or signal.get("status") != "PASS":
        alerts.append(
            _alert(
                "BLOCK",
                "source_signal_not_pass",
                "Route-memory candidates require a passing signal receipt.",
                {"source_ok": signal.get("ok"), "source_status": signal.get("status")},
            )
        )
    if signal.get("source_ok") is not True:
        alerts.append(
            _alert(
                "BLOCK",
                "source_dag_not_pass",
                "Route-memory candidates require a passing source DAG receipt.",
                {
                    "source_ok": signal.get("source_ok"),
                    "source_status": signal.get("source_status"),
                    "source_verdict": signal.get("source_verdict"),
                },
            )
        )
    negative_signals = _dict_list(signal.get("negative_signals"))
    if negative_signals:
        alerts.append(
            _alert(
                "BLOCK",
                "negative_signals_present",
                "Route-memory candidates require zero negative DAG signals.",
                {"negative_signal_count": len(negative_signals)},
            )
        )
    return alerts


def _gate_candidates(
    signal: dict[str, Any],
    *,
    min_confidence: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in _dict_list(signal.get("route_reinforcement_candidates")):
        confidence = _float_or_zero(candidate.get("confidence"))
        route = {
            "route_key": _route_key(candidate),
            "from_node": candidate.get("from_node"),
            "to_node": candidate.get("to_node"),
            "from_agent": candidate.get("from_agent"),
            "to_agent": candidate.get("to_agent"),
            "confidence": confidence,
            "source": candidate.get("source"),
            "source_dag_receipt": signal.get("source_dag_receipt"),
            "source_signal_receipt": signal.get("receipt_path"),
            "memory_sync_candidate": True,
            "sync_status": "NOT_SYNCED",
            "sync_reason": "local_candidate_receipt_only",
        }
        if confidence >= min_confidence and candidate.get("sync_status") == "NOT_SYNCED":
            accepted.append(route)
        else:
            rejected.append(
                {
                    **route,
                    "rejection_reason": (
                        "confidence_below_threshold"
                        if confidence < min_confidence
                        else "candidate_already_synced_or_unknown_sync_state"
                    ),
                }
            )
    return accepted, rejected


def _route_key(candidate: dict[str, Any]) -> str:
    return (
        f"{candidate.get('from_node')}:{candidate.get('from_agent')}"
        f"->{candidate.get('to_node')}:{candidate.get('to_agent')}"
    )


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _float_or_zero(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _alert(
    severity: str,
    code: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
