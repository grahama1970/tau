"""Local shared-key receipt signing for Tau evidence candidates."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SIGNED_RECEIPT_SCHEMA = "tau.signed_receipt.v1"
SIGNED_RECEIPT_VERIFICATION_SCHEMA = "tau.signed_receipt_verification.v1"
SIGNING_ALGORITHM = "HMAC-SHA256"

NON_CLAIMS = [
    "Public-key non-repudiation.",
    "Human legal identity.",
    "US-person or export-control eligibility.",
    "ITAR compliance.",
    "Runtime sandbox enforcement.",
    "Provider/model semantic safety.",
    "That the signed receipt claim is true.",
]


def sign_receipt(
    *,
    receipt_path: Path,
    key_path: Path,
    output_path: Path | None = None,
    actor_manifest_path: Path | None = None,
    environment_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Wrap a receipt hash in a local HMAC authenticity envelope."""

    key = _read_key(key_path)
    receipt_payload = _read_json_object(receipt_path)
    signed_payload = {
        "algorithm": SIGNING_ALGORITHM,
        "receipt": _file_reference(receipt_path, receipt_payload),
        "actor_manifest": _optional_file_reference(actor_manifest_path),
        "environment_manifest": _optional_file_reference(environment_manifest_path),
    }
    signature = _signature(key, signed_payload)
    envelope = {
        "schema": SIGNED_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "signed_at": _utc_stamp(),
        "algorithm": SIGNING_ALGORITHM,
        "key_id": _key_id(key),
        "signed_payload": signed_payload,
        "signature": signature,
        "proof_scope": {
            "proves": [
                "Tau computed a local shared-key signature over receipt input hashes.",
                "Tau can detect changes to the signed receipt inputs when verified "
                "with the same local key.",
            ],
            "does_not_prove": NON_CLAIMS,
        },
    }
    if output_path is not None:
        _write_json(output_path, envelope)
    return envelope


def verify_signed_receipt(
    *,
    signed_receipt_path: Path,
    key_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Verify a Tau signed receipt envelope with the same local shared key."""

    key = _read_key(key_path)
    envelope = _read_json_object(signed_receipt_path)
    errors: list[str] = []
    if envelope.get("schema") != SIGNED_RECEIPT_SCHEMA:
        errors.append(f"schema must be {SIGNED_RECEIPT_SCHEMA}")
    if envelope.get("algorithm") != SIGNING_ALGORITHM:
        errors.append(f"algorithm must be {SIGNING_ALGORITHM}")
    signed_payload = envelope.get("signed_payload")
    if not isinstance(signed_payload, Mapping):
        errors.append("signed_payload must be an object")
        signed_payload = {}
    expected_signature = _signature(key, signed_payload)
    if envelope.get("signature") != expected_signature:
        errors.append("signature mismatch")
    if envelope.get("key_id") != _key_id(key):
        errors.append("key_id mismatch")
    errors.extend(_current_file_mismatches(signed_payload))

    ok = not errors
    receipt = {
        "schema": SIGNED_RECEIPT_VERIFICATION_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "signed_receipt_path": str(signed_receipt_path.expanduser().resolve()),
        "algorithm": SIGNING_ALGORITHM,
        "key_id": _key_id(key),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau recomputed the local shared-key signature for this envelope.",
                "Tau compared current signed input files against recorded hashes "
                "when those paths were available.",
            ],
            "does_not_prove": NON_CLAIMS,
        },
    }
    if output_path is not None:
        _write_json(output_path, receipt)
    return receipt


def _file_reference(path: Path, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"path does not exist or is not a file: {resolved}")
    reference = {
        "path": str(resolved),
        "sha256": f"sha256:{_sha256(resolved)}",
    }
    if payload is not None and isinstance(payload.get("schema"), str):
        reference["schema"] = payload["schema"]
    return reference


def _optional_file_reference(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _read_json_object(path)
    return _file_reference(path, payload)


def _current_file_mismatches(signed_payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("receipt", "actor_manifest", "environment_manifest"):
        reference = signed_payload.get(field)
        if reference is None:
            continue
        if not isinstance(reference, Mapping):
            errors.append(f"{field} reference must be an object")
            continue
        path_value = reference.get("path")
        recorded_sha = reference.get("sha256")
        if not isinstance(path_value, str) or not path_value:
            errors.append(f"{field}.path must be a non-empty string")
            continue
        path = Path(path_value).expanduser().resolve()
        if not path.exists():
            errors.append(f"{field} path is missing: {path}")
            continue
        current_sha = f"sha256:{_sha256(path)}"
        if recorded_sha != current_sha:
            errors.append(f"{field} sha256 mismatch")
    return errors


def _signature(key: bytes, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"hmac-sha256:{hmac.new(key, canonical, hashlib.sha256).hexdigest()}"


def _read_key(path: Path) -> bytes:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"key path does not exist or is not a file: {resolved}")
    key = resolved.read_bytes().strip()
    if not key:
        raise RuntimeError(f"key path is empty: {resolved}")
    return key


def _key_id(key: bytes) -> str:
    return f"sha256:{hashlib.sha256(key).hexdigest()[:16]}"


def _read_json_object(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"file does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"file is not valid JSON: {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"file must contain a JSON object: {resolved}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
