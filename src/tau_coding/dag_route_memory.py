"""Local route-memory reinforcement candidates derived from DAG signal receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA = "tau.dag_route_memory_candidate_receipt.v1"
DAG_ROUTE_MEMORY_SYNC_RECEIPT_SCHEMA = "tau.dag_route_memory_sync_receipt.v1"
SOURCE_DAG_SIGNAL_RECEIPT_SCHEMA = "tau.dag_signal_receipt.v1"
APPROVAL_GATE_RECEIPT_SCHEMA = "tau.approval_gate_receipt.v1"


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


def write_dag_route_memory_sync_receipt(
    *,
    candidate_receipt_path: Path,
    receipt_path: Path,
    collection: str = "tau_route_memory",
    memory_url: str = "http://127.0.0.1:8601",
    apply: bool = False,
    approval_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Project candidate routes into Memory documents, optionally syncing through /upsert."""

    resolved_candidate_path = candidate_receipt_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    resolved_approval_path = approval_receipt_path.expanduser().resolve() if approval_receipt_path else None
    candidate_receipt = _read_json_object(resolved_candidate_path, label="DAG route-memory candidate receipt")
    approval_receipt = (
        _read_json_object(resolved_approval_path, label="approval gate receipt")
        if resolved_approval_path
        else None
    )
    alerts = _sync_gate_alerts(
        candidate_receipt,
        collection=collection,
        apply=apply,
        approval_receipt=approval_receipt,
        approval_receipt_path=resolved_approval_path,
    )
    documents = _memory_documents(candidate_receipt, collection=collection) if not alerts else []
    sync_response: dict[str, Any] | None = None
    if apply and not alerts:
        try:
            with httpx.Client(base_url=memory_url.rstrip("/"), timeout=httpx.Timeout(10.0, connect=2.0)) as client:
                response = client.post("/upsert", json={"collection": collection, "documents": documents})
                response.raise_for_status()
                sync_response = response.json() if response.content else {"status_code": response.status_code}
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            alerts.append(
                _alert(
                    "BLOCK",
                    "memory_upsert_failed",
                    "Memory /upsert failed while syncing route-memory candidates.",
                    {"memory_url": memory_url, "error": str(exc)},
                )
            )
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": DAG_ROUTE_MEMORY_SYNC_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "candidate_receipt": str(resolved_candidate_path),
        "candidate_receipt_sha256": f"sha256:{_sha256(resolved_candidate_path)}",
        "approval_receipt": str(resolved_approval_path) if resolved_approval_path else None,
        "approval_receipt_sha256": f"sha256:{_sha256(resolved_approval_path)}"
        if resolved_approval_path
        else None,
        "receipt_path": str(resolved_receipt_path),
        "dag_id": candidate_receipt.get("dag_id"),
        "goal_hash": candidate_receipt.get("goal_hash"),
        "collection": collection,
        "memory_url": memory_url,
        "apply": apply,
        "memory_sync": bool(apply and status == "PASS"),
        "sync_status": "SYNCED" if apply and status == "PASS" else ("BLOCKED" if alerts else "DRY_RUN"),
        "projected_document_count": len(documents),
        "documents": documents,
        "memory_response": sync_response,
        "alerts": alerts,
        "route_mutation": False,
        "dag_mutation": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "Route-memory candidate receipt was inspected deterministically.",
                "Accepted candidates were projected into Memory /upsert document shape.",
                (
                    "Projected documents were written through Memory /upsert."
                    if apply and status == "PASS"
                    else "No Memory write was attempted in dry-run mode."
                ),
            ],
            "does_not_prove": [
                "Future route correctness.",
                "Runtime route mutation.",
                "Adaptive DAG expansion application.",
                "Provider/model semantic quality.",
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
        missing_route_fields = [
            key
            for key in ("from_node", "to_node", "from_agent", "to_agent")
            if not isinstance(candidate.get(key), str) or not candidate.get(key)
        ]
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
        if (
            not missing_route_fields
            and confidence >= min_confidence
            and candidate.get("sync_status") == "NOT_SYNCED"
        ):
            accepted.append(route)
        else:
            if missing_route_fields:
                rejection_reason = "missing_route_fields"
            elif confidence < min_confidence:
                rejection_reason = "confidence_below_threshold"
            else:
                rejection_reason = "candidate_already_synced_or_unknown_sync_state"
            rejected.append(
                {
                    **route,
                    "missing_route_fields": missing_route_fields,
                    "rejection_reason": rejection_reason,
                }
            )
    return accepted, rejected


def _sync_gate_alerts(
    candidate_receipt: dict[str, Any],
    *,
    collection: str,
    apply: bool,
    approval_receipt: dict[str, Any] | None,
    approval_receipt_path: Path | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if candidate_receipt.get("schema") != DAG_ROUTE_MEMORY_CANDIDATE_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_candidate_schema",
                "Route-memory sync requires a tau.dag_route_memory_candidate_receipt.v1 input.",
                {"schema": candidate_receipt.get("schema")},
            )
        )
    if candidate_receipt.get("ok") is not True or candidate_receipt.get("status") != "PASS":
        alerts.append(
            _alert(
                "BLOCK",
                "candidate_receipt_not_pass",
                "Route-memory sync requires a passing candidate receipt.",
                {"ok": candidate_receipt.get("ok"), "status": candidate_receipt.get("status")},
            )
        )
    if not collection:
        alerts.append(_alert("BLOCK", "missing_collection", "Memory sync collection is required.", {}))
    if int(candidate_receipt.get("accepted_candidate_count") or 0) <= 0:
        alerts.append(
            _alert(
                "BLOCK",
                "no_accepted_candidates",
                "Route-memory sync requires at least one accepted candidate.",
                {"accepted_candidate_count": candidate_receipt.get("accepted_candidate_count")},
            )
        )
    if apply:
        alerts.extend(
            _approval_alerts(
                approval_receipt=approval_receipt,
                approval_receipt_path=approval_receipt_path,
            )
        )
    return alerts


def _approval_alerts(
    *,
    approval_receipt: dict[str, Any] | None,
    approval_receipt_path: Path | None,
) -> list[dict[str, Any]]:
    if approval_receipt_path is None:
        return [
            _alert(
                "BLOCK",
                "missing_approval_receipt",
                "Route-memory apply requires a PASS approval receipt for memory_upsert.",
                {},
            )
        ]
    if approval_receipt is None:
        return [
            _alert(
                "BLOCK",
                "approval_receipt_unreadable",
                "Route-memory apply approval receipt could not be loaded.",
                {"approval_receipt": str(approval_receipt_path)},
            )
        ]
    alerts: list[dict[str, Any]] = []
    if approval_receipt.get("schema") != APPROVAL_GATE_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_approval_receipt_schema",
                "Route-memory apply requires tau.approval_gate_receipt.v1.",
                {"schema": approval_receipt.get("schema")},
            )
        )
    if approval_receipt.get("ok") is not True or approval_receipt.get("approved") is not True:
        alerts.append(
            _alert(
                "BLOCK",
                "approval_receipt_not_pass",
                "Route-memory apply requires approved=true and ok=true.",
                {
                    "ok": approval_receipt.get("ok"),
                    "status": approval_receipt.get("status"),
                    "approved": approval_receipt.get("approved"),
                },
            )
        )
    if approval_receipt.get("requested_action") != "memory_upsert":
        alerts.append(
            _alert(
                "BLOCK",
                "approval_action_mismatch",
                "Route-memory apply approval must be for requested_action=memory_upsert.",
                {"requested_action": approval_receipt.get("requested_action")},
            )
        )
    return alerts


def _memory_documents(candidate_receipt: dict[str, Any], *, collection: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for candidate in _dict_list(candidate_receipt.get("accepted_candidates")):
        route_key = str(candidate.get("route_key") or "")
        digest = hashlib.sha256(
            f"{collection}|{candidate_receipt.get('goal_hash')}|{candidate_receipt.get('dag_id')}|{route_key}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        documents.append(
            {
                "_key": f"tau-route-{digest}",
                "schema": "tau.route_memory_signal.v1",
                "kind": "tau_route_memory_signal",
                "dag_id": candidate_receipt.get("dag_id"),
                "goal_hash": candidate_receipt.get("goal_hash"),
                "route_key": route_key,
                "from_node": candidate.get("from_node"),
                "to_node": candidate.get("to_node"),
                "from_agent": candidate.get("from_agent"),
                "to_agent": candidate.get("to_agent"),
                "confidence": candidate.get("confidence"),
                "source": candidate.get("source"),
                "source_signal_receipt": candidate_receipt.get("source_signal_receipt"),
                "source_dag_receipt": candidate.get("source_dag_receipt"),
                "source_candidate_receipt": candidate_receipt.get("receipt_path"),
                "sync_source": "tau.dag_route_memory_sync_receipt.v1",
                "retrieval_text": (
                    f"Tau DAG route memory signal {route_key} for {candidate_receipt.get('dag_id')} "
                    f"goal {candidate_receipt.get('goal_hash')}"
                ),
                "observed_at": _utc_stamp(),
                "memory_sync_candidate": True,
            }
        )
    return documents


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
