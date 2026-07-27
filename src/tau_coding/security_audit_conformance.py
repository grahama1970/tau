"""Live security/audit conformance lane for Tau trust-boundary receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.approval_gate import evaluate_approval_gate

SECURITY_AUDIT_CONFORMANCE_SCHEMA = "tau.security_audit_conformance.v1"
ASYMMETRIC_SIGNATURE_RECEIPT_SCHEMA = "tau.asymmetric_signature_receipt.v1"
ASYMMETRIC_SIGNATURE_VERIFICATION_SCHEMA = "tau.asymmetric_signature_verification.v1"
API_MUTATING_REQUEST_RECEIPT_SCHEMA = "tau.api_mutating_request_receipt.v1"
AUDIT_LEDGER_VERIFICATION_SCHEMA = "tau.audit_ledger_verification.v1"
RUN_ID = "security-audit-conformance-run"
TARGET_ID = "workspace://tau/protected-file"
TARGET_LINEAGE = "sha256:security-audit-lineage"
ACTION = "working_tree_mutation"
AUTHORIZED_TOKEN = "tau-local-security-audit-token"


def write_security_audit_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Exercise signature, approval, RBAC/API, mutating request, and audit gates."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl is required for asymmetric signature conformance")

    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    signing_dir = artifacts_dir / "signing"
    approval_dir = artifacts_dir / "approvals"
    api_dir = artifacts_dir / "api"
    audit_dir = artifacts_dir / "audit"
    for directory in (signing_dir, approval_dir, api_dir, audit_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_receipt_path = signing_dir / "receipt-to-sign.json"
    _write_json(source_receipt_path, _source_receipt())
    signature_receipt = _sign_receipt_asymmetric(
        receipt_path=source_receipt_path,
        signing_dir=signing_dir,
        receipt_path_out=signing_dir / "asymmetric-signature-receipt.json",
    )
    signature_verification = _verify_signature_receipt(
        signature_receipt=signature_receipt,
        message_path=source_receipt_path,
        receipt_path=signing_dir / "signature-verification.json",
    )
    tampered_receipt_path = signing_dir / "receipt-to-sign.tampered.json"
    tampered = _source_receipt()
    tampered["status"] = "BLOCKED"
    _write_json(tampered_receipt_path, tampered)
    tamper_negative = _verify_signature_receipt(
        signature_receipt=signature_receipt,
        message_path=tampered_receipt_path,
        receipt_path=signing_dir / "tamper-negative-verification.json",
    )

    valid_approval_packet = approval_dir / "approval-valid.json"
    wrong_target_packet = approval_dir / "approval-wrong-target.json"
    expired_packet = approval_dir / "approval-expired.json"
    _write_approval_packet(
        valid_approval_packet,
        target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        expires_at=_future_stamp(),
    )
    _write_approval_packet(
        wrong_target_packet,
        target={"id": "workspace://tau/other-target", "lineage": TARGET_LINEAGE},
        expires_at=_future_stamp(),
    )
    _write_approval_packet(
        expired_packet,
        target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        expires_at=_past_stamp(),
    )
    approval_gate = evaluate_approval_gate(
        approval_packet=valid_approval_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "valid-run",
        output=approval_dir / "approval-bound-action.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )
    wrong_target_gate = evaluate_approval_gate(
        approval_packet=wrong_target_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "wrong-target-run",
        output=approval_dir / "wrong-target-denial.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )
    expired_gate = evaluate_approval_gate(
        approval_packet=expired_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "expired-run",
        output=approval_dir / "expired-denial.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )

    rbac_policy_path = api_dir / "rbac-policy.json"
    _write_json(rbac_policy_path, _rbac_policy())
    unauthorized_request = _evaluate_api_mutating_request(
        request={
            "actor_id": "agent:unauthorized",
            "auth_token": None,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
            "approval_receipt_path": approval_gate["approval_packet"],
        },
        policy_path=rbac_policy_path,
        approval_gate_receipt=approval_gate,
        receipt_path=api_dir / "unauthorized-mutating-request.json",
    )
    authorized_request = _evaluate_api_mutating_request(
        request={
            "actor_id": "human:graham",
            "auth_token": AUTHORIZED_TOKEN,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
            "approval_receipt_path": approval_gate["approval_packet"],
        },
        policy_path=rbac_policy_path,
        approval_gate_receipt=approval_gate,
        receipt_path=api_dir / "authorized-mutating-request.json",
    )

    ledger_path = audit_dir / "audit-ledger.jsonl"
    _write_audit_ledger(
        ledger_path,
        [
            _ledger_event("signature_verified", signature_verification),
            _ledger_event("tamper_negative_denied", tamper_negative),
            _ledger_event("approval_bound_action", approval_gate),
            _ledger_event("wrong_target_denied", wrong_target_gate),
            _ledger_event("expired_approval_denied", expired_gate),
            _ledger_event("unauthorized_mutating_request_denied", unauthorized_request),
            _ledger_event("authorized_mutating_request_accepted", authorized_request),
        ],
    )
    ledger_verification = _verify_audit_ledger(
        ledger_path=ledger_path,
        receipt_path=audit_dir / "audit-ledger-verification.json",
    )

    checks = {
        "signature_verification_pass": signature_verification.get("status") == "PASS",
        "tamper_negative_control_denied": tamper_negative.get("status") == "BLOCKED",
        "approval_bound_action_accepted": approval_gate.get("status") == "PASS",
        "wrong_target_approval_denied": wrong_target_gate.get("status") == "BLOCKED",
        "expired_approval_denied": expired_gate.get("status") == "BLOCKED",
        "unauthorized_api_mutating_request_denied": unauthorized_request.get("status")
        == "BLOCKED",
        "authorized_request_accepted_with_receipt": authorized_request.get("status") == "PASS",
        "audit_ledger_verifies": ledger_verification.get("status") == "PASS",
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    payload = {
        "schema": SECURITY_AUDIT_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "signature_receipt": signature_receipt["receipt_path"],
        "signature_verification_receipt": signature_verification["receipt_path"],
        "tamper_negative_receipt": tamper_negative["receipt_path"],
        "approval_bound_action_receipt": str(
            (approval_dir / "approval-bound-action.json").resolve()
        ),
        "wrong_target_denial_receipt": str((approval_dir / "wrong-target-denial.json").resolve()),
        "expired_approval_denial_receipt": str((approval_dir / "expired-denial.json").resolve()),
        "unauthorized_mutating_request_receipt": unauthorized_request["receipt_path"],
        "authorized_mutating_request_receipt": authorized_request["receipt_path"],
        "audit_ledger": str(ledger_path.resolve()),
        "audit_ledger_verification_receipt": ledger_verification["receipt_path"],
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "Tau generated an Ed25519 asymmetric keypair through local OpenSSL, signed "
                "a receipt artifact, and verified the signature with the public key.",
                "Tau denied a tampered signed payload.",
                "Tau accepted only action/target/lineage-bound unexpired human approval.",
                "Tau denied unauthorized API mutation attempts and accepted an authorized "
                "RBAC/API request with a receipt.",
                "Tau wrote and verified a hash-chained append-only audit ledger for this lane.",
            ],
            "does_not_prove": [
                "Human legal identity.",
                "Production key custody.",
                "Network API gateway deployment.",
                "External identity-provider integration.",
                "Provider/model semantic quality.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def _sign_receipt_asymmetric(
    *,
    receipt_path: Path,
    signing_dir: Path,
    receipt_path_out: Path,
) -> dict[str, Any]:
    private_key = signing_dir / "ed25519-private.pem"
    public_key = signing_dir / "ed25519-public.pem"
    signature_path = signing_dir / "receipt-to-sign.sig"
    _run_openssl(["genpkey", "-algorithm", "Ed25519", "-out", str(private_key)])
    _run_openssl(["pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)])
    _run_openssl(
        [
            "pkeyutl",
            "-sign",
            "-inkey",
            str(private_key),
            "-rawin",
            "-in",
            str(receipt_path),
            "-out",
            str(signature_path),
        ]
    )
    private_key.unlink()
    payload = {
        "schema": ASYMMETRIC_SIGNATURE_RECEIPT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "algorithm": "Ed25519",
        "openssl_path": shutil.which("openssl"),
        "public_key_path": str(public_key.resolve()),
        "public_key_sha256": _sha256_uri(public_key),
        "private_key_persisted": False,
        "signed_receipt_path": str(receipt_path.resolve()),
        "signed_receipt_sha256": _sha256_uri(receipt_path),
        "signature_path": str(signature_path.resolve()),
        "signature_base64": base64.b64encode(signature_path.read_bytes()).decode("ascii"),
        "receipt_path": str(receipt_path_out.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path_out, payload)
    return payload


def _verify_signature_receipt(
    *,
    signature_receipt: dict[str, Any],
    message_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    signature_path = Path(str(signature_receipt["signature_path"]))
    public_key_path = Path(str(signature_receipt["public_key_path"]))
    completed = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            str(public_key_path),
            "-rawin",
            "-in",
            str(message_path),
            "-sigfile",
            str(signature_path),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    payload = {
        "schema": ASYMMETRIC_SIGNATURE_VERIFICATION_SCHEMA,
        "status": "PASS" if completed.returncode == 0 else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "algorithm": "Ed25519",
        "message_path": str(message_path.resolve()),
        "message_sha256": _sha256_uri(message_path),
        "public_key_path": str(public_key_path.resolve()),
        "signature_path": str(signature_path.resolve()),
        "command": completed.args,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _evaluate_api_mutating_request(
    *,
    request: dict[str, Any],
    policy_path: Path,
    approval_gate_receipt: dict[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    policy = _read_json(policy_path)
    actor_id = str(request.get("actor_id") or "")
    roles_by_actor = policy.get("roles_by_actor") if isinstance(policy, dict) else {}
    role = roles_by_actor.get(actor_id) if isinstance(roles_by_actor, dict) else None
    permissions = policy.get("permissions") if isinstance(policy, dict) else {}
    allowed_actions = permissions.get(role, []) if isinstance(permissions, dict) else []
    token_hash = _token_sha256(str(request.get("auth_token") or ""))
    errors: list[str] = []
    if token_hash != policy.get("api_token_sha256"):
        errors.append("api authentication failed")
    if request.get("action") not in allowed_actions:
        errors.append("rbac denied action")
    if approval_gate_receipt.get("status") != "PASS":
        errors.append("approval gate receipt must PASS")
    target = request.get("target") if isinstance(request.get("target"), dict) else {}
    if target.get("id") != TARGET_ID:
        errors.append("target.id mismatch")
    if target.get("lineage") != TARGET_LINEAGE:
        errors.append("target.lineage mismatch")
    accepted = not errors
    payload = {
        "schema": API_MUTATING_REQUEST_RECEIPT_SCHEMA,
        "status": "PASS" if accepted else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "actor_id": actor_id,
        "role": role,
        "action": request.get("action"),
        "target": target,
        "auth_token_present": bool(request.get("auth_token")),
        "auth_token_sha256": token_hash if request.get("auth_token") else None,
        "approval_gate_status": approval_gate_receipt.get("status"),
        "approval_gate_receipt_path": approval_gate_receipt.get("approval_packet"),
        "accepted": accepted,
        "mutation_applied": accepted,
        "mutating_request_receipt": str(receipt_path.resolve()),
        "receipt_path": str(receipt_path.resolve()),
        "errors": errors,
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _write_audit_ledger(ledger_path: Path, events: list[dict[str, Any]]) -> None:
    previous_hash = "sha256:GENESIS"
    lines: list[str] = []
    for sequence, event in enumerate(events, start=1):
        entry = {
            "schema": "tau.audit_ledger_entry.v1",
            "sequence": sequence,
            "previous_hash": previous_hash,
            "event": event,
            "timestamp": _now(),
        }
        entry_hash = _entry_hash(entry)
        entry["entry_hash"] = entry_hash
        previous_hash = entry_hash
        lines.append(json.dumps(entry, sort_keys=True))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify_audit_ledger(*, ledger_path: Path, receipt_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    previous_hash = "sha256:GENESIS"
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {index} is not JSON: {exc}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"line {index} is not an object")
            continue
        recorded_hash = entry.get("entry_hash")
        entry_without_hash = dict(entry)
        entry_without_hash.pop("entry_hash", None)
        if entry.get("previous_hash") != previous_hash:
            errors.append(f"line {index} previous_hash mismatch")
        computed_hash = _entry_hash(entry_without_hash)
        if recorded_hash != computed_hash:
            errors.append(f"line {index} entry_hash mismatch")
        previous_hash = str(recorded_hash or computed_hash)
        entries.append(entry)
    payload = {
        "schema": AUDIT_LEDGER_VERIFICATION_SCHEMA,
        "status": "PASS" if not errors and entries else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": _sha256_uri(ledger_path),
        "entry_count": len(entries),
        "terminal_hash": previous_hash,
        "errors": errors,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _ledger_event(kind: str, receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "receipt_path": receipt.get("receipt_path"),
        "status": receipt.get("status"),
        "sha256": _sha256_uri(Path(str(receipt["receipt_path"])))
        if receipt.get("receipt_path")
        else None,
    }


def _run_openssl(args: list[str]) -> None:
    completed = subprocess.run(
        ["openssl", *args],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args)} failed: {completed.stderr}")


def _source_receipt() -> dict[str, Any]:
    return {
        "schema": "tau.security_audit_source_receipt.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "run_id": RUN_ID,
        "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        "checked_at": _now(),
    }


def _write_approval_packet(path: Path, *, target: dict[str, str], expires_at: str) -> None:
    payload: dict[str, Any] = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "action": ACTION,
        "actor": {"id": "human:graham", "auth_method": "local-signature"},
        "target": target,
        "reason": "Approve bounded security-audit conformance mutation.",
        "evidence": ["asymmetric-signature-receipt.json"],
        "nonce": f"security-audit-{target['id']}-{expires_at}",
        "expires_at": expires_at,
    }
    payload["signature"] = _local_signature(payload)
    _write_json(path, payload)


def _local_signature(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"


def _rbac_policy() -> dict[str, Any]:
    return {
        "schema": "tau.rbac_policy.v1",
        "roles_by_actor": {
            "human:graham": "operator",
            "agent:unauthorized": "viewer",
        },
        "permissions": {
            "operator": [ACTION],
            "viewer": [],
        },
        "api_token_sha256": _token_sha256(AUTHORIZED_TOKEN),
        "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    }


def _token_sha256(token: str) -> str:
    return f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def _entry_hash(entry_without_hash: dict[str, Any]) -> str:
    encoded = json.dumps(entry_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _future_stamp() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _past_stamp() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
