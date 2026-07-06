"""Build a machine-readable index over Tau proof receipts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROOF_INDEX_ENTRY_SCHEMA = "tau.proof_index_entry.v1"
PROOF_INDEX_BUILD_RECEIPT_SCHEMA = "tau.proof_index_build_receipt.v1"


def build_proof_index(
    proofs_dir: Path,
    *,
    output_path: Path,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Write a JSONL index for Tau receipt-like JSON artifacts under ``proofs_dir``."""

    resolved_proofs_dir = proofs_dir.expanduser().resolve()
    resolved_output_path = output_path.expanduser().resolve()
    resolved_receipt_path = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_output_path.with_suffix(".receipt.json")
    )

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    entries: list[dict[str, Any]] = []

    if not resolved_proofs_dir.exists():
        raise RuntimeError(f"proofs directory does not exist: {resolved_proofs_dir}")
    if not resolved_proofs_dir.is_dir():
        raise RuntimeError(f"proofs path is not a directory: {resolved_proofs_dir}")

    for path in sorted(resolved_proofs_dir.rglob("*.json")):
        resolved_path = path.resolve()
        if resolved_path in {resolved_output_path, resolved_receipt_path}:
            continue
        try:
            payload = _read_json_object(resolved_path)
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "path": str(resolved_path),
                    "code": "malformed_json",
                    "message": str(exc),
                }
            )
            continue
        except OSError as exc:
            errors.append(
                {
                    "path": str(resolved_path),
                    "code": "unreadable_json",
                    "message": str(exc),
                }
            )
            continue
        except RuntimeError as exc:
            warnings.append(
                {
                    "path": str(resolved_path),
                    "code": "non_object_json",
                    "message": str(exc),
                }
            )
            continue

        if not _looks_like_tau_receipt(resolved_path, payload):
            continue
        entries.append(_entry_for_receipt(resolved_proofs_dir, resolved_path, payload))

    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_output_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    schema_counts = Counter(str(entry.get("receipt_schema")) for entry in entries)
    status_counts = Counter(str(entry.get("status")) for entry in entries)
    build_receipt = {
        "schema": PROOF_INDEX_BUILD_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "proofs_dir": str(resolved_proofs_dir),
        "output_path": str(resolved_output_path),
        "output_sha256": f"sha256:{_sha256(resolved_output_path)}",
        "receipt_path": str(resolved_receipt_path),
        "indexed_receipt_count": len(entries),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "schema_counts": dict(sorted(schema_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "proof_scope": {
            "proves": [
                "Tau receipt-like JSON artifacts were scanned deterministically.",
                "The proof index JSONL was written with stable path, hash, status, and boundary fields.",
            ],
            "does_not_prove": [
                "Receipt semantic truth.",
                "Provider/model semantic quality.",
                "Live GitHub mutation.",
                "Production Memory persistence.",
            ],
        },
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_receipt_path, build_receipt)
    return build_receipt


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("JSON value is not an object")
    return payload


def _looks_like_tau_receipt(path: Path, payload: dict[str, Any]) -> bool:
    schema = payload.get("schema")
    if not isinstance(schema, str) or not schema.startswith("tau."):
        return False
    lowered_path = path.name.lower()
    lowered_schema = schema.lower()
    return "receipt" in lowered_schema or lowered_path in {
        "run-receipt.json",
        "campaign-receipt.json",
    }


def _entry_for_receipt(root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    proof_scope = payload.get("proof_scope")
    claims = payload.get("claims")
    proves = _string_list_from_scope(proof_scope, "proves") or _string_list_from_scope(
        claims, "proves"
    )
    does_not_prove = _string_list_from_scope(
        proof_scope, "does_not_prove"
    ) or _string_list_from_scope(claims, "does_not_prove")
    status = (
        payload.get("status") or payload.get("verdict") or ("PASS" if payload.get("ok") else None)
    )
    return {
        "schema": PROOF_INDEX_ENTRY_SCHEMA,
        "receipt_path": str(path),
        "receipt_relative_path": str(path.relative_to(root)),
        "receipt_sha256": f"sha256:{_sha256(path)}",
        "receipt_schema": payload.get("schema"),
        "status": status,
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "run_id": payload.get("run_id"),
        "dag_id": payload.get("dag_id"),
        "goal_hash": _extract_goal_hash(payload),
        "proves": proves,
        "does_not_prove": does_not_prove,
    }


def _string_list_from_scope(value: object, key: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    items = value.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str)]


def _extract_goal_hash(payload: dict[str, Any]) -> str | None:
    value = payload.get("goal_hash")
    if isinstance(value, str):
        return value
    goal = payload.get("goal")
    if isinstance(goal, dict) and isinstance(goal.get("goal_hash"), str):
        return str(goal["goal_hash"])
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
