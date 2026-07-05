"""Memory intent and evidence-case gate for zero-trust DAG dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MEMORY_INTENT_GATE_RECEIPT_SCHEMA = "tau.memory_intent_gate_receipt.v1"
EVIDENCE_CASE_GATE_RECEIPT_SCHEMA = "tau.evidence_case_gate_receipt.v1"

INTENT_SCHEMA = "memory.intent.v1"
EVIDENCE_CASE_SCHEMA = "memory.evidence_case.v1"
BLOCKING_ROUTES = {"CLARIFY", "DEFLECT"}


def evaluate_memory_evidence_gate(
    *,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
    memory_intent: Mapping[str, Any] | None,
    evidence_case: Mapping[str, Any] | None,
    memory_intent_path: Path | None = None,
    evidence_case_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return memory-intent and evidence-case gate receipts."""

    memory_policy = policy_profile.get("memory") if isinstance(policy_profile, Mapping) else {}
    if not isinstance(memory_policy, Mapping):
        memory_policy = {}

    intent_required = memory_policy.get("intent_required") is True
    min_confidence = _float_value(memory_policy.get("min_intent_confidence"), default=0.0)
    clarify_blocks = memory_policy.get("clarify_blocks_dispatch", True) is not False
    deflect_blocks = memory_policy.get("deflect_blocks_dispatch", True) is not False
    evidence_required_routes = _string_set(memory_policy.get("evidence_case_required_for"))

    intent_alerts: list[dict[str, Any]] = []
    route = _route(memory_intent)
    evidence_case_required = _evidence_case_required(
        memory_intent=memory_intent,
        route=route,
        required_routes=evidence_required_routes,
    )

    if intent_required and memory_intent is None:
        intent_alerts.append(_alert("missing_memory_intent", "Memory intent is required."))
    elif memory_intent is not None:
        intent_alerts.extend(
            _validate_intent(
                memory_intent,
                route=route,
                min_confidence=min_confidence,
                clarify_blocks=clarify_blocks,
                deflect_blocks=deflect_blocks,
            )
        )

    evidence_receipt = _evidence_case_receipt(
        evidence_case=evidence_case,
        evidence_case_path=evidence_case_path,
        data_boundary=data_boundary,
        policy_profile=policy_profile,
        evidence_case_required=evidence_case_required,
    )
    if evidence_case_required and evidence_receipt["ok"] is not True:
        intent_alerts.append(
            _alert(
                "missing_evidence_case",
                "Intent requires a separate evidence case before dispatch.",
                errors=evidence_receipt.get("alert_codes", []),
            )
        )

    intent_ok = not intent_alerts
    intent_receipt = {
        "schema": MEMORY_INTENT_GATE_RECEIPT_SCHEMA,
        "ok": intent_ok,
        "status": "PASS" if intent_ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "memory_first": (
            memory_intent.get("memory_first") if isinstance(memory_intent, Mapping) else None
        ),
        "intent_schema": (
            memory_intent.get("schema") if isinstance(memory_intent, Mapping) else None
        ),
        "planner_only": (
            memory_intent.get("planner_only") if isinstance(memory_intent, Mapping) else None
        ),
        "route": route,
        "confidence": (
            memory_intent.get("confidence") if isinstance(memory_intent, Mapping) else None
        ),
        "recall_profile": (
            memory_intent.get("recall_profile") if isinstance(memory_intent, Mapping) else None
        ),
        "required_artifacts": _list_value(memory_intent, "required_artifacts"),
        "tool_calls": _list_value(memory_intent, "tool_calls"),
        "evidence_case_required": evidence_case_required,
        "evidence_case_receipt": evidence_receipt.get("receipt_path"),
        "memory_intent": _source_payload(memory_intent, path=memory_intent_path),
        "alerts": intent_alerts,
        "alert_codes": [alert["code"] for alert in intent_alerts],
        "proof_scope": {
            "proves": [
                "Tau inspected Graph Memory intent before DAG dispatch.",
                "Tau did not let a subagent route start from ungrounded prompt text.",
            ],
            "does_not_prove": [
                "Memory facts are true.",
                "The evidence case is sufficient for closure.",
                "ITAR compliance.",
                "Semantic model quality.",
            ],
        },
    }
    return intent_receipt, evidence_receipt


def load_memory_gate_object(
    value: str | dict[str, Any] | None,
    *,
    contract_path: Path,
    field_name: str,
) -> tuple[dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    """Resolve an inline memory-gate object or a contract-relative JSON path."""

    if value is None:
        return None, None, []
    if isinstance(value, dict):
        return value, None, []
    path = Path(value)
    if not path.is_absolute():
        path = contract_path.parent / path
    try:
        payload = _read_json_object(path.expanduser().resolve())
    except RuntimeError as exc:
        return (
            None,
            path,
            [
                _alert(
                    f"invalid_{field_name}",
                    f"{field_name} could not be read.",
                    errors=[str(exc)],
                )
            ],
        )
    return payload, path.expanduser().resolve(), []


def write_memory_evidence_gate_receipts(
    *,
    receipt_dir: Path,
    intent_receipt: dict[str, Any],
    evidence_receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write gate receipts and return copies with receipt paths."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    intent_path = receipt_dir / "memory-intent-gate-receipt.json"
    evidence_path = receipt_dir / "evidence-case-gate-receipt.json"
    intent_payload = {**intent_receipt, "receipt_path": str(intent_path)}
    evidence_payload = {**evidence_receipt, "receipt_path": str(evidence_path)}
    intent_payload["evidence_case_receipt"] = str(evidence_path)
    _write_json(intent_path, intent_payload)
    _write_json(evidence_path, evidence_payload)
    return intent_payload, evidence_payload


def _validate_intent(
    payload: Mapping[str, Any],
    *,
    route: str | None,
    min_confidence: float,
    clarify_blocks: bool,
    deflect_blocks: bool,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if payload.get("schema") != INTENT_SCHEMA:
        alerts.append(_alert("invalid_memory_intent_schema", "Memory intent schema is invalid."))
    if payload.get("memory_first") is not True:
        alerts.append(_alert("memory_first_not_true", "Memory intent must set memory_first true."))
    if payload.get("planner_only") is not True:
        alerts.append(_alert("intent_not_planner_only", "Memory intent must be planner-only."))
    if route == "CLARIFY" and clarify_blocks:
        alerts.append(_alert("intent_clarify_required", "CLARIFY intent blocks dispatch."))
    if route == "DEFLECT" and deflect_blocks:
        alerts.append(_alert("intent_deflected", "DEFLECT intent blocks dispatch."))
    confidence = _float_value(payload.get("confidence"), default=None)
    if confidence is None:
        alerts.append(_alert("intent_confidence_missing", "Memory intent confidence is missing."))
    elif confidence < min_confidence:
        alerts.append(
            _alert(
                "intent_confidence_too_low",
                "Memory intent confidence is below policy threshold.",
                errors=[f"{confidence} < {min_confidence}"],
            )
        )
    if "evidence" in payload or "evidence_case" in payload:
        alerts.append(
            _alert(
                "intent_contains_inline_evidence",
                "Intent must not inline evidence; use create-evidence-case.",
            )
        )
    return alerts


def _evidence_case_receipt(
    *,
    evidence_case: Mapping[str, Any] | None,
    evidence_case_path: Path | None,
    data_boundary: Mapping[str, Any] | None,
    policy_profile: Mapping[str, Any] | None,
    evidence_case_required: bool,
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    if evidence_case_required and evidence_case is None:
        alerts.append(_alert("missing_evidence_case", "Evidence case is required."))
    elif evidence_case is not None:
        if evidence_case.get("schema") != EVIDENCE_CASE_SCHEMA:
            alerts.append(
                _alert("invalid_evidence_case_schema", "Evidence case schema is invalid.")
            )
        if not evidence_case.get("sha256"):
            alerts.append(_alert("evidence_case_hash_missing", "Evidence case hash is missing."))
        if _boundary_key(evidence_case.get("data_boundary")) != _boundary_key(data_boundary):
            alerts.append(
                _alert(
                    "evidence_case_boundary_mismatch",
                    "Evidence case data boundary does not match DAG data boundary.",
                )
            )
        if _policy_key(evidence_case.get("policy_profile")) != _policy_key(policy_profile):
            alerts.append(
                _alert(
                    "evidence_case_policy_mismatch",
                    "Evidence case policy profile does not match DAG policy profile.",
                )
            )

    ok = not alerts
    return {
        "schema": EVIDENCE_CASE_GATE_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "source": "graph-memory-operator:/create-evidence-case",
        "evidence_case_path": str(evidence_case_path) if evidence_case_path else None,
        "evidence_case_sha256": (
            evidence_case.get("sha256") if isinstance(evidence_case, Mapping) else None
        ),
        "question": (
            evidence_case.get("question") if isinstance(evidence_case, Mapping) else None
        ),
        "data_boundary": _boundary_key(data_boundary),
        "policy_profile": _policy_key(policy_profile),
        "allowed_to_dispatch": ok,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
    }


def _evidence_case_required(
    *,
    memory_intent: Mapping[str, Any] | None,
    route: str | None,
    required_routes: set[str],
) -> bool:
    if isinstance(memory_intent, Mapping) and memory_intent.get("evidence_case_required") is True:
        return True
    return route in required_routes


def _boundary_key(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {
        "schema": value.get("schema"),
        "classification": value.get("classification"),
        "export_controlled": value.get("export_controlled"),
        "itar": value.get("itar"),
        "technical_data": value.get("technical_data"),
    }


def _policy_key(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {
        "schema": value.get("schema"),
        "profile_id": value.get("profile_id"),
        "default_decision": value.get("default_decision"),
    }


def _source_payload(payload: Mapping[str, Any] | None, *, path: Path | None) -> dict[str, Any]:
    return {
        "present": payload is not None,
        "path": str(path) if path else None,
        "sha256": f"sha256:{_sha256(path)}" if path else None,
    }


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "severity": "BLOCK",
        "code": code,
        "message": message,
    }
    if errors:
        payload["errors"] = errors
    return payload


def _route(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("route"), str):
        return None
    return str(payload["route"]).upper()


def _float_value(value: object, *, default: float | None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).upper() for item in value if isinstance(item, str) and item}


def _list_value(payload: Mapping[str, Any] | None, key: str) -> list[Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get(key), list):
        return []
    value = payload[key]
    assert isinstance(value, list)
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"could not read JSON file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON file must contain an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
