"""Memory intent and evidence-case pre-dispatch gates for Tau DAGs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

MEMORY_INTENT_GATE_RECEIPT_SCHEMA = "tau.memory_intent_gate_receipt.v1"
EVIDENCE_CASE_GATE_RECEIPT_SCHEMA = "tau.evidence_case_gate_receipt.v1"


def write_memory_intent_gate_receipt(
    *,
    memory_intent: Mapping[str, Any] | None,
    memory_intent_path: Path | None = None,
    dag_contract: Mapping[str, Any] | None = None,
    receipt_path: Path,
) -> dict[str, Any]:
    """Validate that Memory intent can be used as a Tau dispatch input."""

    alerts: list[dict[str, Any]] = []
    goal_hash = _goal_hash(dag_contract)
    target = _target(dag_contract)

    if memory_intent is None:
        alerts.append(_alert("missing_memory_intent", "DAG requires memory_intent."))
    else:
        schema = memory_intent.get("schema")
        if not isinstance(schema, str) or not schema.startswith("memory."):
            alerts.append(
                _alert(
                    "invalid_memory_intent_schema",
                    "memory_intent.schema must be a memory.* schema.",
                    {"observed_schema": schema},
                )
            )
        if memory_intent.get("memory_first") is not True:
            alerts.append(
                _alert(
                    "memory_first_required",
                    "memory_intent.memory_first must be true before DAG dispatch.",
                )
            )
        route = _route(memory_intent)
        if not route:
            alerts.append(
                _alert(
                    "missing_memory_route",
                    "memory_intent must declare route, action, or intent.",
                )
            )
        elif route in {"CLARIFY", "DEFLECT", "NO_MATCH"}:
            alerts.append(
                _alert(
                    "memory_route_not_dispatchable",
                    "Memory route requires clarification or deflection before DAG dispatch.",
                    {"route": route},
                )
            )
        confidence = memory_intent.get("confidence")
        if confidence is not None and not _confidence_ok(confidence):
            alerts.append(
                _alert(
                    "memory_intent_low_confidence",
                    "memory_intent.confidence is below the dispatch threshold.",
                    {"confidence": confidence, "minimum": 0.5},
                )
            )
        observed_goal_hash = memory_intent.get("goal_hash")
        if goal_hash and observed_goal_hash is not None and observed_goal_hash != goal_hash:
            alerts.append(
                _alert(
                    "memory_intent_goal_hash_mismatch",
                    "memory_intent.goal_hash does not match DAG goal hash.",
                    {"expected_goal_hash": goal_hash, "observed_goal_hash": observed_goal_hash},
                )
            )
        observed_target = memory_intent.get("target")
        if target and isinstance(observed_target, Mapping) and dict(observed_target) != target:
            alerts.append(
                _alert(
                    "memory_intent_target_mismatch",
                    "memory_intent.target does not match DAG target.",
                    {"expected_target": target, "observed_target": dict(observed_target)},
                )
            )
        evidence = memory_intent.get("evidence")
        if isinstance(evidence, list) and evidence:
            alerts.append(
                _alert(
                    "inline_memory_evidence_rejected",
                    "Memory intent must route; evidence belongs in a separate evidence_case.",
                    {"inline_evidence_count": len(evidence)},
                )
            )

    return _write_receipt(
        schema=MEMORY_INTENT_GATE_RECEIPT_SCHEMA,
        receipt_path=receipt_path,
        source_path=memory_intent_path,
        payload=memory_intent,
        goal_hash=goal_hash,
        target=target,
        alerts=alerts,
        proves=[
            "Tau inspected a Memory intent product before DAG dispatch.",
            "Memory intent was treated as a routing/admissibility input, not inline evidence.",
        ],
        does_not_prove=[
            "Memory truth.",
            "Evidence-case artifact correctness.",
            "Provider/model semantic quality.",
        ],
    )


def write_evidence_case_gate_receipt(
    *,
    evidence_case: Mapping[str, Any] | None,
    evidence_case_path: Path | None = None,
    dag_contract: Mapping[str, Any] | None = None,
    memory_intent_receipt: Mapping[str, Any] | None = None,
    receipt_path: Path,
) -> dict[str, Any]:
    """Validate that a separate evidence case backs a dispatchable Memory route."""

    alerts: list[dict[str, Any]] = []
    goal_hash = _goal_hash(dag_contract)
    target = _target(dag_contract)

    if evidence_case is None:
        alerts.append(_alert("missing_evidence_case", "DAG requires evidence_case."))
    else:
        schema = evidence_case.get("schema")
        if not isinstance(schema, str) or "evidence" not in schema:
            alerts.append(
                _alert(
                    "invalid_evidence_case_schema",
                    "evidence_case.schema must be an evidence-case schema.",
                    {"observed_schema": schema},
                )
            )
        case_hash = _string(evidence_case.get("case_sha256")) or _string(
            evidence_case.get("sha256")
        )
        if not _valid_sha256(case_hash):
            alerts.append(
                _alert(
                    "missing_evidence_case_hash",
                    "evidence_case must include case_sha256 or sha256.",
                )
            )
        observed_goal_hash = evidence_case.get("goal_hash")
        if goal_hash and observed_goal_hash is not None and observed_goal_hash != goal_hash:
            alerts.append(
                _alert(
                    "evidence_case_goal_hash_mismatch",
                    "evidence_case.goal_hash does not match DAG goal hash.",
                    {"expected_goal_hash": goal_hash, "observed_goal_hash": observed_goal_hash},
                )
            )
        observed_target = evidence_case.get("target")
        if target and isinstance(observed_target, Mapping) and dict(observed_target) != target:
            alerts.append(
                _alert(
                    "evidence_case_target_mismatch",
                    "evidence_case.target does not match DAG target.",
                    {"expected_target": target, "observed_target": dict(observed_target)},
                )
            )
        boundary_hash = _string(evidence_case.get("data_boundary_sha256"))
        policy_hash = _string(evidence_case.get("policy_profile_sha256"))
        if (
            dag_contract
            and dag_contract.get("data_boundary") is not None
            and not boundary_hash
            and evidence_case.get("data_boundary") is None
        ):
            alerts.append(
                _alert(
                    "missing_evidence_case_data_boundary_hash",
                    "evidence_case must cite data_boundary_sha256 when DAG has a data_boundary.",
                )
            )
        if (
            dag_contract
            and dag_contract.get("policy_profile") is not None
            and not policy_hash
            and evidence_case.get("policy_profile") is None
        ):
            alerts.append(
                _alert(
                    "missing_evidence_case_policy_hash",
                    "evidence_case must cite policy_profile_sha256 when DAG has a policy_profile.",
                )
            )
        support = evidence_case.get("support_artifacts")
        if support is not None and not isinstance(support, list):
            alerts.append(
                _alert(
                    "invalid_evidence_case_support_artifacts",
                    "evidence_case.support_artifacts must be a list when present.",
                )
            )

    memory_ok = (
        isinstance(memory_intent_receipt, Mapping)
        and memory_intent_receipt.get("ok") is True
    )
    if memory_intent_receipt is not None and not memory_ok:
        alerts.append(
            _alert(
                "memory_intent_gate_not_passed",
                "Evidence case cannot pass until memory_intent gate passes.",
                {"memory_intent_status": memory_intent_receipt.get("status")},
            )
        )

    return _write_receipt(
        schema=EVIDENCE_CASE_GATE_RECEIPT_SCHEMA,
        receipt_path=receipt_path,
        source_path=evidence_case_path,
        payload=evidence_case,
        goal_hash=goal_hash,
        target=target,
        alerts=alerts,
        proves=[
            "Tau inspected a separate evidence case before DAG dispatch.",
            "Evidence case goal, target, and policy/boundary references were checked.",
        ],
        does_not_prove=[
            "Evidence-case semantic completeness.",
            "Memory truth.",
            "Provider/model semantic quality.",
        ],
    )


def read_gate_payload(
    value: str | Mapping[str, Any] | None,
    *,
    contract_path: Path,
    label: str,
) -> tuple[dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    if value is None:
        return None, None, []
    if isinstance(value, Mapping):
        return dict(value), None, []
    path = Path(value)
    if not path.is_absolute():
        path = contract_path.parent / path
    resolved = path.expanduser().resolve()
    try:
        return _read_json_object(resolved, label=label), resolved, []
    except RuntimeError as exc:
        return None, resolved, [_alert(f"{label}_unreadable", str(exc))]


def evaluate_memory_evidence_gate(
    *,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
    memory_intent: Mapping[str, Any] | None,
    evidence_case: Mapping[str, Any] | None,
    memory_intent_path: Path | None = None,
    evidence_case_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate Memory intent and evidence-case inputs without writing files.

    This compatibility API backs the local HTTP route and older tests. DAG dispatch uses
    the split file-backed receipt functions above.
    """

    memory_policy = policy_profile.get("memory") if isinstance(policy_profile, Mapping) else {}
    if not isinstance(memory_policy, Mapping):
        memory_policy = {}
    intent_required = memory_policy.get("intent_required") is True
    min_confidence = _float_value(memory_policy.get("min_intent_confidence"), default=0.0)
    clarify_blocks = memory_policy.get("clarify_blocks_dispatch", True) is not False
    deflect_blocks = memory_policy.get("deflect_blocks_dispatch", True) is not False
    required_routes = _string_set(memory_policy.get("evidence_case_required_for"))

    route = _route(memory_intent) if isinstance(memory_intent, Mapping) else None
    evidence_case_required = (
        isinstance(memory_intent, Mapping)
        and (
            memory_intent.get("evidence_case_required") is True
            or (route is not None and route in required_routes)
        )
    )

    intent_alerts: list[dict[str, Any]] = []
    if intent_required and memory_intent is None:
        intent_alerts.append(_alert("missing_memory_intent", "Memory intent is required."))
    elif isinstance(memory_intent, Mapping):
        intent_alerts.extend(
            _compat_intent_alerts(
                memory_intent,
                route=route,
                min_confidence=min_confidence,
                clarify_blocks=clarify_blocks,
                deflect_blocks=deflect_blocks,
            )
        )

    evidence_receipt = _compat_evidence_case_receipt(
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
                {"errors": evidence_receipt.get("alert_codes", [])},
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


def write_memory_evidence_gate_receipts(
    *,
    receipt_dir: Path,
    intent_receipt: dict[str, Any],
    evidence_receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write compatibility gate receipts and return copies with receipt paths."""

    resolved_dir = receipt_dir.expanduser().resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    intent_path = resolved_dir / "memory-intent-gate-receipt.json"
    evidence_path = resolved_dir / "evidence-case-gate-receipt.json"
    intent_payload = {**intent_receipt, "receipt_path": str(intent_path)}
    evidence_payload = {**evidence_receipt, "receipt_path": str(evidence_path)}
    intent_payload["evidence_case_receipt"] = str(evidence_path)
    intent_path.write_text(json.dumps(intent_payload, indent=2, sort_keys=True) + "\n")
    evidence_path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n")
    return intent_payload, evidence_payload


def _compat_intent_alerts(
    payload: Mapping[str, Any],
    *,
    route: str | None,
    min_confidence: float,
    clarify_blocks: bool,
    deflect_blocks: bool,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if payload.get("schema") != "memory.intent.v1":
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
                {"errors": [f"{confidence} < {min_confidence}"]},
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


def _compat_evidence_case_receipt(
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
    elif isinstance(evidence_case, Mapping):
        if evidence_case.get("schema") != "memory.evidence_case.v1":
            alerts.append(
                _alert("invalid_evidence_case_schema", "Evidence case schema is invalid.")
            )
        if not evidence_case.get("sha256"):
            alerts.append(_alert("evidence_case_hash_missing", "Evidence case hash is missing."))
        if _compat_boundary_key(evidence_case.get("data_boundary")) != _compat_boundary_key(
            data_boundary
        ):
            alerts.append(
                _alert(
                    "evidence_case_boundary_mismatch",
                    "Evidence case data boundary does not match DAG data boundary.",
                )
            )
        if _compat_policy_key(evidence_case.get("policy_profile")) != _compat_policy_key(
            policy_profile
        ):
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
        "data_boundary": _compat_boundary_key(data_boundary),
        "policy_profile": _compat_policy_key(policy_profile),
        "allowed_to_dispatch": ok,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
    }


def _float_value(value: object, *, default: float | None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _string_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {item.strip().upper() for item in value if isinstance(item, str) and item.strip()}
    return set()


def _list_value(payload: Mapping[str, Any] | None, key: str) -> list[Any]:
    if isinstance(payload, Mapping) and isinstance(payload.get(key), list):
        return list(payload[key])
    return []


def _source_payload(payload: Mapping[str, Any] | None, *, path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path else None,
        "sha256": f"sha256:{_sha256(path)}" if path else None,
        "inline": dict(payload) if path is None and isinstance(payload, Mapping) else None,
    }


def _compat_boundary_key(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "schema": value.get("schema"),
        "classification": value.get("classification"),
        "export_controlled": value.get("export_controlled"),
        "itar": value.get("itar"),
        "technical_data": value.get("technical_data"),
    }


def _compat_policy_key(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "schema": value.get("schema"),
        "profile_id": value.get("profile_id"),
        "default_decision": value.get("default_decision"),
    }


def _write_receipt(
    *,
    schema: str,
    receipt_path: Path,
    source_path: Path | None,
    payload: Mapping[str, Any] | None,
    goal_hash: str | None,
    target: dict[str, Any] | None,
    alerts: list[dict[str, Any]],
    proves: list[str],
    does_not_prove: list[str],
) -> dict[str, Any]:
    ok = not alerts
    resolved_receipt = receipt_path.expanduser().resolve()
    receipt = {
        "schema": schema,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_path": str(source_path) if source_path else None,
        "source_sha256": f"sha256:{_sha256(source_path)}" if source_path else None,
        "goal_hash": goal_hash,
        "target": target,
        "alert_count": len(alerts),
        "alert_codes": [alert["code"] for alert in alerts],
        "alerts": alerts,
        "receipt_path": str(resolved_receipt),
        "proof_scope": {
            "proves": proves,
            "does_not_prove": does_not_prove,
        },
        "timestamp": _utc_stamp(),
    }
    if payload is not None:
        receipt["payload_schema"] = payload.get("schema")
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _alert(code: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "evidence": evidence or {},
    }


def _goal_hash(dag_contract: Mapping[str, Any] | None) -> str | None:
    if not isinstance(dag_contract, Mapping):
        return None
    goal = dag_contract.get("goal")
    if isinstance(goal, Mapping):
        return _string(goal.get("goal_hash"))
    return None


def _target(dag_contract: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(dag_contract, Mapping):
        return None
    target = dag_contract.get("target")
    if isinstance(target, Mapping):
        return dict(target)
    return None


def _route(payload: Mapping[str, Any]) -> str | None:
    for key in ("route", "action", "intent"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _confidence_ok(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) >= 0.5
    return False


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _valid_sha256(value: str | None) -> bool:
    if not value:
        return False
    raw = value.removeprefix("sha256:")
    if len(raw) != 64:
        return False
    try:
        int(raw, 16)
    except ValueError:
        return False
    return True


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _sha256(path: Path | None) -> str:
    if path is None:
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
