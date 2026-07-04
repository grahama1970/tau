"""Branch-lock validation for mutating or provider DAG branches."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

try:
    import yaml
except ImportError:  # pragma: no cover - only used in stripped environments.
    yaml = None  # type: ignore[assignment]


DAG_BRANCH_LOCKS_SCHEMA = "tau.dag_branch_locks.v1"
DAG_BRANCH_LOCK_VALIDATION_RECEIPT_SCHEMA = "tau.dag_branch_lock_validation_receipt.v1"


def write_dag_branch_lock_validation_receipt(
    *,
    dag_contract_path: Path,
    locks_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Validate lock coverage before mutating/provider branches are schedulable."""

    resolved_contract_path = dag_contract_path.expanduser().resolve()
    resolved_locks_path = locks_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    contract_payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(contract_payload)
    locks_payload = _load_object(resolved_locks_path, label="DAG branch locks")
    required_locks = _required_locks(contract.payload)
    alerts = _lock_alerts(contract_payload=contract.payload, locks_payload=locks_payload, required_locks=required_locks)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": DAG_BRANCH_LOCK_VALIDATION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "dag_contract": str(resolved_contract_path),
        "dag_contract_sha256": f"sha256:{_sha256(resolved_contract_path)}",
        "locks": str(resolved_locks_path),
        "locks_sha256": f"sha256:{_sha256(resolved_locks_path)}",
        "receipt_path": str(resolved_receipt_path),
        "dag_id": contract.dag_id,
        "goal_hash": contract.goal["goal_hash"],
        "required_locks": required_locks,
        "required_lock_count": len(required_locks),
        "provided_lock_count": len(_dict_list(locks_payload.get("locks"))),
        "alerts": alerts,
        "route_mutation": False,
        "dag_mutation": False,
        "memory_sync": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "DAG contract was inspected for provider and mutating branches.",
                "Branch-lock metadata was checked against required provider/mutating nodes.",
                "No provider branch, mutating branch, route mutation, or DAG mutation was executed.",
            ],
            "does_not_prove": [
                "Provider branch safety after execution.",
                "GitHub mutation safety.",
                "Concurrent scheduling of provider or mutating branches.",
                "Human authorization for external side effects.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def _required_locks(contract_payload: dict[str, Any]) -> list[dict[str, Any]]:
    required: list[dict[str, Any]] = []
    contract_mutating = bool(contract_payload.get("mutating"))
    for node in _dict_list(contract_payload.get("nodes")):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if node.get("executor") == "provider" or isinstance(node.get("provider"), dict):
            required.append({"node_id": node_id, "branch_type": "provider"})
        if contract_mutating or bool(node.get("mutates")):
            required.append({"node_id": node_id, "branch_type": "mutating"})
    return required


def _lock_alerts(
    *,
    contract_payload: dict[str, Any],
    locks_payload: dict[str, Any],
    required_locks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if locks_payload.get("schema") != DAG_BRANCH_LOCKS_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_locks_schema",
                "Branch locks schema is not supported.",
                {"schema": locks_payload.get("schema")},
            )
        )
        return alerts
    if locks_payload.get("dag_id") != contract_payload.get("dag_id"):
        alerts.append(
            _alert(
                "BLOCK",
                "dag_id_mismatch",
                "Branch locks dag_id does not match the DAG contract.",
                {"expected": contract_payload.get("dag_id"), "observed": locks_payload.get("dag_id")},
            )
        )
    goal_hash = _mapping(contract_payload.get("goal")).get("goal_hash")
    if locks_payload.get("goal_hash") != goal_hash:
        alerts.append(
            _alert(
                "BLOCK",
                "goal_hash_mismatch",
                "Branch locks goal_hash does not match the immutable DAG goal hash.",
                {"expected": goal_hash, "observed": locks_payload.get("goal_hash")},
            )
        )
    locks = _dict_list(locks_payload.get("locks"))
    lock_index = {(lock.get("node_id"), lock.get("branch_type")): lock for lock in locks}
    for required in required_locks:
        key = (required["node_id"], required["branch_type"])
        lock = lock_index.get(key)
        if lock is None:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_branch_lock",
                    "Provider and mutating branches require an explicit branch lock.",
                    required,
                )
            )
        elif not lock.get("lock_id") or not lock.get("owner"):
            alerts.append(
                _alert(
                    "BLOCK",
                    "incomplete_branch_lock",
                    "Branch lock must include lock_id and owner.",
                    {"node_id": required["node_id"], "branch_type": required["branch_type"]},
                )
            )
    return alerts


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("YAML branch locks require PyYAML")
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _alert(severity: str, code: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
