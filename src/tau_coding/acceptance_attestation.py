"""Human acceptance attestation verification for Tau evidence baselines (#305)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACCEPTANCE_ATTESTATION_SCHEMA = "tau.human_acceptance_attestation.v1"
ACCEPTANCE_ATTESTATION_VERIFICATION_SCHEMA = "tau.human_acceptance_attestation_verification.v1"
DEFAULT_ACCEPTANCE_BASELINE = Path("docs/proofs/acceptance/rungs-evidence-receipt.json")
DEFAULT_ACCEPTANCE_ATTESTATION = Path("docs/proofs/acceptance/human-acceptance-attestation.json")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _parse_time(value: object, code: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(code)
        return None
    raw = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        errors.append(code)
        return None
    if parsed.tzinfo is None:
        errors.append(code)
        return None
    return parsed.astimezone(UTC)


def _relative_path(repo: Path, value: object, default: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        return repo / default
    path = Path(value)
    return path if path.is_absolute() else repo / path


def _first_digest(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value: Any = payload
        ok = True
        for part in key.split("/"):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                ok = False
                break
        if ok and isinstance(value, str) and value.startswith("sha256:"):
            return value
    return None


def verify_acceptance_attestation(
    repo: Path,
    *,
    attestation_path: Path | None = None,
    baseline_receipt_path: Path | None = None,
    proof_receipt_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a human acceptance attestation against exact tested evidence.

    The verifier is deliberately presence-insensitive: a missing attestation is
    reported as ``PENDING_HUMAN_SIGNATURE`` rather than accepted. Any malformed,
    expired, or mismatched attestation is ``BLOCKED`` with stable failure codes.
    """

    repo = repo.expanduser().resolve()
    attestation_file = _relative_path(
        repo,
        str(attestation_path) if attestation_path is not None else None,
        DEFAULT_ACCEPTANCE_ATTESTATION,
    )
    baseline_file = _relative_path(
        repo,
        str(baseline_receipt_path) if baseline_receipt_path is not None else None,
        DEFAULT_ACCEPTANCE_BASELINE,
    )
    checked_at = now or datetime.now(UTC)
    baseline_sha256 = sha256_file(baseline_file)
    head = _git_head(repo)
    errors: list[str] = []

    if baseline_sha256 is None:
        errors.append("baseline_receipt_missing")

    if not attestation_file.is_file():
        return {
            "schema": ACCEPTANCE_ATTESTATION_VERIFICATION_SCHEMA,
            "status": "PENDING_HUMAN_SIGNATURE",
            "ok": False,
            "state": "PENDING_HUMAN_SIGNATURE",
            "failure_codes": ["human_acceptance_attestation_missing"],
            "attestation_present": False,
            "attestation_path": str(attestation_file.relative_to(repo)) if attestation_file.is_relative_to(repo) else str(attestation_file),
            "attestation_sha256": None,
            "baseline_receipt_path": str(baseline_file.relative_to(repo)) if baseline_file.is_relative_to(repo) else str(baseline_file),
            "baseline_receipt_sha256": baseline_sha256,
            "source_commit": head,
            "checked_at": checked_at.isoformat(),
        }

    attestation_sha256 = sha256_file(attestation_file)
    try:
        payload = json.loads(attestation_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
        errors.append("attestation_json_invalid")
    if not isinstance(payload, dict):
        payload = {}
        errors.append("attestation_not_object")

    if payload.get("schema") != ACCEPTANCE_ATTESTATION_SCHEMA:
        errors.append("attestation_schema_invalid")

    signer = payload.get("signer")
    if not isinstance(signer, dict):
        errors.append("signer_missing")
        signer = {}
    if not isinstance(signer.get("id"), str) or not signer.get("id", "").strip():
        errors.append("signer_id_missing")
    if signer.get("authority_class") != "human_operator":
        errors.append("signer_authority_invalid")

    decision = payload.get("decision")
    if decision not in {"ACCEPTED", "accepted", True}:
        errors.append("decision_not_accepted")

    attested_at = _parse_time(payload.get("attested_at"), "attested_at_invalid", errors)
    if attested_at is not None and attested_at > checked_at:
        errors.append("attestation_from_future")
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        parsed_expiry = _parse_time(expires_at, "expires_at_invalid", errors)
        if parsed_expiry is not None and parsed_expiry <= checked_at:
            errors.append("attestation_expired")

    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    expected_baseline_path = baseline.get("path") if isinstance(baseline, dict) else None
    if isinstance(expected_baseline_path, str) and expected_baseline_path.strip():
        attested_baseline_file = _relative_path(repo, expected_baseline_path, DEFAULT_ACCEPTANCE_BASELINE)
        if attested_baseline_file.resolve() != baseline_file.resolve():
            errors.append("baseline_receipt_path_mismatch")
    expected_baseline_sha = _first_digest(payload, "baseline/sha256", "baseline_receipt_sha256")
    if expected_baseline_sha != baseline_sha256:
        errors.append("baseline_receipt_digest_mismatch")

    expected_source_commit = payload.get("source_commit") or payload.get("tested_source_commit")
    if not isinstance(expected_source_commit, str) or expected_source_commit != head:
        errors.append("source_commit_mismatch")

    proof = payload.get("proof_receipt") if isinstance(payload.get("proof_receipt"), dict) else {}
    proof_path_value = proof.get("path") if isinstance(proof, dict) else None
    proof_file = _relative_path(repo, str(proof_receipt_path), Path("")) if proof_receipt_path is not None else None
    if proof_file is None and isinstance(proof_path_value, str) and proof_path_value.strip():
        proof_file = _relative_path(repo, proof_path_value, Path(""))
    expected_proof_sha = _first_digest(payload, "proof_receipt/sha256", "proof_receipt_sha256")
    actual_proof_sha = sha256_file(proof_file) if proof_file is not None else None
    if expected_proof_sha is None:
        errors.append("proof_receipt_digest_missing")
    elif actual_proof_sha != expected_proof_sha:
        errors.append("proof_receipt_digest_mismatch")

    ok = not errors
    return {
        "schema": ACCEPTANCE_ATTESTATION_VERIFICATION_SCHEMA,
        "status": "ACCEPTED" if ok else "BLOCKED",
        "ok": ok,
        "state": "VERIFIED_ACCEPTANCE" if ok else "INVALID_HUMAN_SIGNATURE",
        "failure_codes": errors,
        "attestation_present": True,
        "attestation_path": str(attestation_file.relative_to(repo)) if attestation_file.is_relative_to(repo) else str(attestation_file),
        "attestation_sha256": attestation_sha256,
        "baseline_receipt_path": str(baseline_file.relative_to(repo)) if baseline_file.is_relative_to(repo) else str(baseline_file),
        "baseline_receipt_sha256": baseline_sha256,
        "proof_receipt_path": str(proof_file.relative_to(repo)) if proof_file is not None and proof_file.is_relative_to(repo) else (str(proof_file) if proof_file is not None else None),
        "proof_receipt_sha256": actual_proof_sha,
        "source_commit": head,
        "checked_at": checked_at.isoformat(),
    }


__all__ = [
    "ACCEPTANCE_ATTESTATION_SCHEMA",
    "ACCEPTANCE_ATTESTATION_VERIFICATION_SCHEMA",
    "DEFAULT_ACCEPTANCE_ATTESTATION",
    "DEFAULT_ACCEPTANCE_BASELINE",
    "sha256_file",
    "verify_acceptance_attestation",
]
