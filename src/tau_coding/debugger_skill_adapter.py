"""Adapter from debugger skill proof artifacts into Tau debug receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tau_coding.debug_session_receipt import (
    DEBUG_SESSION_PACKET_SCHEMA,
    write_debug_session_receipt,
)

DEBUGGER_PROOF_SCHEMA = "debugger.proof.v1"
DEBUGGER_SKILL_ADAPTER_RECEIPT_SCHEMA = "tau.debugger_skill_adapter_receipt.v1"


def write_debugger_skill_adapter_receipt(
    *,
    proof_path: Path,
    output_path: Path,
    debug_session_output_path: Path,
    repo_root: Path | None = None,
    expected_goal_hash: str | None = None,
    zero_trust: bool = False,
) -> dict[str, Any]:
    resolved_proof = proof_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = (repo_root or Path.cwd()).expanduser().resolve()
    errors: list[str] = []
    proof = _read_json_object(resolved_proof, errors=errors, label="debugger proof")
    if proof:
        _validate_debugger_proof(
            proof,
            repo_root=resolved_repo,
            expected_goal_hash=expected_goal_hash,
            zero_trust=zero_trust,
            errors=errors,
        )
    packet_path = resolved_output.parent / "debug-session-packet.json"
    packet = _debugger_proof_to_debug_session_packet(proof)
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    debug_receipt = write_debug_session_receipt(
        session_path=packet_path,
        output_path=debug_session_output_path,
        required=True,
        expected_goal_hash=expected_goal_hash,
        zero_trust=zero_trust,
    )
    if debug_receipt.get("ok") is not True:
        errors.append("debug session receipt blocked")
    status = "PASS" if not errors else "BLOCKED"
    payload = {
        "schema": DEBUGGER_SKILL_ADAPTER_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "proof_path": str(resolved_proof),
        "debug_session_packet_path": str(packet_path),
        "debug_session_receipt_path": str(debug_session_output_path.expanduser().resolve()),
        "debug_session_status": debug_receipt.get("status"),
        "goal_hash": proof.get("goal_hash"),
        "expected_goal_hash": expected_goal_hash,
        "target": _target_from_proof(proof),
        "adapter": _adapter_from_proof(proof),
        "errors": errors,
        "course_correction": _course_correction(errors),
        "proof_scope": {
            "proves": [
                "Tau translated a debugger skill proof into a Tau debug session packet.",
                "Tau validated the translated packet with tau.debug_session_receipt.v1.",
            ],
            "does_not_prove": [
                "The bug is fixed.",
                "The debugger conclusion is semantically complete.",
                "The code is correct.",
                "Redaction found every sensitive value.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _validate_debugger_proof(
    proof: dict[str, Any],
    *,
    repo_root: Path,
    expected_goal_hash: str | None,
    zero_trust: bool,
    errors: list[str],
) -> None:
    if proof.get("schema") != DEBUGGER_PROOF_SCHEMA:
        errors.append(f"schema must be {DEBUGGER_PROOF_SCHEMA}")
    goal_hash = proof.get("goal_hash")
    if zero_trust and not _non_empty_str(goal_hash):
        errors.append("goal_hash is required in zero-trust mode")
    if expected_goal_hash and goal_hash != expected_goal_hash:
        errors.append("goal_hash mismatches expected_goal_hash")
    if not _non_empty_str(_target_from_proof(proof)):
        errors.append("target command is required")
    if not _non_empty_str(_adapter_from_proof(proof)):
        errors.append("adapter label is required")
    if not isinstance(proof.get("breakpoints"), list):
        errors.append("breakpoints must be a list")
    if not isinstance(proof.get("stopped_frame"), dict):
        errors.append("stopped_frame must be an object")
    if not isinstance(proof.get("variables"), list):
        errors.append("variables must be a list")
    for field in ("stdout_path", "stderr_path"):
        value = proof.get(field)
        if value is None:
            continue
        if not _non_empty_str(value):
            errors.append(f"{field} must be a string when provided")
            continue
        path = Path(str(value)).expanduser()
        resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
        if not _is_relative_to(resolved, repo_root):
            errors.append(f"{field} escapes repo root: {resolved}")


def _debugger_proof_to_debug_session_packet(proof: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": DEBUG_SESSION_PACKET_SCHEMA,
        "goal_hash": proof.get("goal_hash"),
        "target": _target_from_proof(proof),
        "adapter": _adapter_from_proof(proof),
        "adapter_available": proof.get("adapter_available", True),
        "allowed_paths": proof.get("allowed_paths", []),
        "forbidden_paths": proof.get("forbidden_paths", []),
        "breakpoints": proof.get("breakpoints", []),
        "stopped_frame": proof.get("stopped_frame", {}),
        "variables": proof.get("variables", []),
        "commands": proof.get("commands", []),
        "stdout_path": proof.get("stdout_path"),
        "stderr_path": proof.get("stderr_path"),
        "conclusion": proof.get("conclusion"),
    }


def _course_correction(errors: list[str]) -> dict[str, Any] | None:
    if not errors:
        return None
    return {
        "schema": "tau.course_correction.v1",
        "trigger": "debugger_evidence_required",
        "required_next_action": "debug_or_route_reviewer",
        "allowed_next_routes": ["debugger", "reviewer", "human"],
        "forbidden_next_routes": ["claim_pass_without_debugger_proof"],
        "required_evidence_before_retry": ["debugger.proof.v1", "tau.debug_session_receipt.v1"],
    }


def _target_from_proof(proof: dict[str, Any]) -> Any:
    return proof.get("target_command", proof.get("target"))


def _adapter_from_proof(proof: dict[str, Any]) -> Any:
    return proof.get("adapter_label", proof.get("adapter"))


def _read_json_object(path: Path, *, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object: {path}")
        return {}
    return payload


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
