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
    alerts = _lock_alerts(
        contract_payload=contract.payload,
        locks_payload=locks_payload,
        locks_dir=resolved_locks_path.parent,
        required_locks=required_locks,
    )
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
                "Side-effect authorization metadata was checked before scheduling.",
                "No provider branch, mutating branch, route mutation, or DAG mutation was executed.",
            ],
            "does_not_prove": [
                "Provider branch safety after execution.",
                "GitHub mutation safety.",
                "Concurrent scheduling of provider or mutating branches.",
                "That the approval packet hash corresponds to a valid human approval packet.",
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
    locks_dir: Path,
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
    approval_packet_hashes = _approval_packet_hashes(locks_payload, locks_dir=locks_dir)
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
            continue
        alerts.extend(
            _branch_lock_field_alerts(
                lock=lock,
                required=required,
                approval_packet_hashes=approval_packet_hashes,
            )
        )
    return alerts


def _branch_lock_field_alerts(
    *,
    lock: dict[str, Any],
    required: dict[str, Any],
    approval_packet_hashes: set[str] | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    node_id = str(required["node_id"])
    branch_type = str(required["branch_type"])
    evidence = {"node_id": node_id, "branch_type": branch_type}

    missing = [
        key
        for key in (
            "lock_id",
            "owner",
            "actor_identity",
            "approval_packet_sha256",
            "allowed_paths",
            "side_effect_class",
            "expires_at",
            "rollback_policy",
        )
        if not lock.get(key)
    ]
    if missing:
        alerts.append(
            _alert(
                "BLOCK",
                "incomplete_branch_lock",
                "Branch lock is missing required authorization fields.",
                {**evidence, "missing_fields": missing},
            )
        )
    approval_packet_sha256 = lock.get("approval_packet_sha256")
    if not (
        isinstance(approval_packet_sha256, str)
        and approval_packet_sha256.startswith("sha256:")
        and len(approval_packet_sha256) > len("sha256:")
    ):
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_approval_packet_sha256",
                "Branch lock approval_packet_sha256 must be a sha256-prefixed digest.",
                evidence,
            )
        )
    elif (
        approval_packet_hashes is not None
        and approval_packet_sha256 not in approval_packet_hashes
    ):
        alerts.append(
            _alert(
                "BLOCK",
                "approval_packet_hash_not_bound",
                "Branch lock approval_packet_sha256 does not match any supplied approval packet.",
                {**evidence, "approval_packet_sha256": approval_packet_sha256},
            )
        )
    allowed_paths = lock.get("allowed_paths")
    if not _string_list(allowed_paths):
        alerts.append(
            _alert(
                "BLOCK",
                "missing_allowed_paths",
                "Branch lock must name the paths allowed for the side-effecting branch.",
                evidence,
            )
        )
    side_effect_class = lock.get("side_effect_class")
    allowed_side_effect_classes = {"filesystem", "github", "memory", "provider"}
    if side_effect_class not in allowed_side_effect_classes:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_side_effect_class",
                "Branch lock side_effect_class is not supported.",
                {**evidence, "side_effect_class": side_effect_class},
            )
        )
    if branch_type == "provider" and not lock.get("workspace_lease"):
        alerts.append(
            _alert(
                "BLOCK",
                "missing_workspace_lease",
                "Provider branch locks require a Herdr workspace lease reference.",
                evidence,
            )
        )
    if lock.get("rollback_policy") != "required":
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_rollback_policy",
                "Branch lock rollback_policy must be 'required'.",
                {**evidence, "rollback_policy": lock.get("rollback_policy")},
            )
        )
    expires_at = lock.get("expires_at")
    parsed_expiry = _parse_timestamp(expires_at)
    if parsed_expiry is None:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_lock_expiry",
                "Branch lock expires_at must be an ISO-8601 timestamp.",
                {**evidence, "expires_at": expires_at},
            )
        )
    elif parsed_expiry <= datetime.now(UTC):
        alerts.append(
            _alert(
                "BLOCK",
                "branch_lock_expired",
                "Branch lock has expired.",
                {**evidence, "expires_at": expires_at},
            )
        )
    return alerts


def _approval_packet_hashes(
    locks_payload: dict[str, Any],
    *,
    locks_dir: Path,
) -> set[str] | None:
    approval_packets = locks_payload.get("approval_packets")
    if approval_packets is None:
        return None
    hashes: set[str] = set()
    if not isinstance(approval_packets, list):
        return hashes
    for item in approval_packets:
        if not isinstance(item, str) or not item:
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = locks_dir / path
        try:
            hashes.add(f"sha256:{_sha256(path.resolve())}")
        except OSError:
            continue
    return hashes


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _alert(severity: str, code: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
