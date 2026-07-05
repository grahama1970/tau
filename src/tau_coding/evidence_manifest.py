"""Typed evidence manifest validation for Tau DAG receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVIDENCE_MANIFEST_SCHEMA = "tau.evidence_manifest.v1"
EVIDENCE_VALIDATION_RECEIPT_SCHEMA = "tau.evidence_validation_receipt.v1"


def write_evidence_validation_receipt(
    *,
    manifest_path: Path,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Validate a tau.evidence_manifest.v1 file and write a receipt."""

    resolved_manifest = manifest_path.expanduser().resolve()
    manifest = _read_json_object(resolved_manifest, label="evidence manifest")
    errors: list[str] = []
    checked_items: list[dict[str, Any]] = []

    if manifest.get("schema") != EVIDENCE_MANIFEST_SCHEMA:
        errors.append(f"manifest.schema must be {EVIDENCE_MANIFEST_SCHEMA}")
    goal_hash = _string(manifest.get("goal_hash"))
    dag_id = _string(manifest.get("dag_id"))
    if not goal_hash:
        errors.append("manifest.goal_hash must be a non-empty string")
    if not dag_id:
        errors.append("manifest.dag_id must be a non-empty string")

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append("manifest.items must be a non-empty list")
        items = []

    for index, item in enumerate(items):
        checked_items.append(
            _validate_item(
                index,
                item,
                manifest_path=resolved_manifest,
                manifest_goal_hash=goal_hash,
            )
        )

    for item in checked_items:
        errors.extend(item["errors"])

    ok = not errors
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_manifest.with_name("evidence-validation-receipt.json")
    )
    receipt = {
        "schema": EVIDENCE_VALIDATION_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "manifest_path": str(resolved_manifest),
        "manifest_sha256": f"sha256:{_sha256(resolved_manifest)}",
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "item_count": len(checked_items),
        "checked_items": checked_items,
        "errors": errors,
        "receipt_path": str(resolved_receipt),
        "proof_scope": {
            "proves": [
                "Tau inspected a typed evidence manifest.",
                "Every listed evidence item was checked for path existence and sha256 match.",
                "JSON evidence items with declared schemas were checked against their root schema "
                "field and goal hash.",
            ],
            "does_not_prove": [
                "Semantic correctness of the artifact contents beyond declared schema/hash checks.",
                "Provider/model quality.",
                "GitHub, Memory, or browser/UI side effects.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _validate_item(
    index: int,
    item: object,
    *,
    manifest_path: Path,
    manifest_goal_hash: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "index": index,
        "kind": None,
        "path": None,
        "sha256": None,
        "schema": None,
        "validator": None,
        "valid": False,
        "errors": [],
    }
    errors: list[str] = result["errors"]
    if not isinstance(item, dict):
        errors.append(f"items[{index}] must be an object")
        return result

    kind = _string(item.get("kind"))
    path_text = _string(item.get("path"))
    expected_sha = _normalize_sha256(item.get("sha256"))
    expected_schema = _string(item.get("schema"))
    validator = _string(item.get("validator"))
    declared_valid = item.get("valid")
    result.update(
        {
            "kind": kind,
            "path": path_text,
            "sha256": expected_sha,
            "schema": expected_schema,
            "validator": validator,
            "valid": declared_valid is True,
        }
    )
    if not kind:
        errors.append(f"items[{index}].kind must be a non-empty string")
    if not path_text:
        errors.append(f"items[{index}].path must be a non-empty string")
        return result
    path = Path(path_text)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.expanduser().resolve()
    result["path"] = str(path)
    if not path.is_file():
        errors.append(f"items[{index}].path does not exist or is not a file: {path}")
        return result
    actual_sha = f"sha256:{_sha256(path)}"
    result["actual_sha256"] = actual_sha
    if not expected_sha:
        errors.append(f"items[{index}].sha256 must be a sha256:<hex> string")
    elif expected_sha != actual_sha:
        errors.append(
            f"items[{index}].sha256 mismatch: expected {expected_sha}, observed {actual_sha}"
        )
    if expected_schema:
        try:
            payload = _read_json_object(path, label=f"items[{index}] evidence")
        except RuntimeError as exc:
            errors.append(str(exc))
        else:
            observed_schema = payload.get("schema")
            result["observed_schema"] = observed_schema
            if observed_schema != expected_schema:
                errors.append(
                    f"items[{index}].schema mismatch: expected {expected_schema}, "
                    f"observed {observed_schema}"
                )
            observed_goal_hash = payload.get("goal_hash")
            result["observed_goal_hash"] = observed_goal_hash
            if observed_goal_hash != manifest_goal_hash:
                errors.append(
                    f"items[{index}].goal_hash mismatch: expected {manifest_goal_hash}, "
                    f"observed {observed_goal_hash}"
                )
            observed_kind = payload.get("kind")
            result["observed_kind"] = observed_kind
            if observed_kind != kind:
                errors.append(
                    f"items[{index}].kind mismatch: expected {kind}, observed {observed_kind}"
                )
    if validator and declared_valid is not True:
        errors.append(f"items[{index}].valid must be true when validator is declared")
    if validator and not validator.startswith("tau evidence-validate "):
        errors.append(
            f"items[{index}].validator must use tau evidence-validate, observed {validator}"
        )
    result["valid"] = not errors
    return result


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _normalize_sha256(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.removeprefix("sha256:")
    if len(raw) != 64:
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    return f"sha256:{raw}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
