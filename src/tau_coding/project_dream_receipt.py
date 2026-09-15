"""Project-memory dream terminal receipt validation.

The validator is intentionally local and replayable: it reads only the receipt
and hash-bound artifacts referenced by that receipt.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_DREAM_RECEIPT_SCHEMA = "tau.project_dream_receipt.v1"
PROJECT_DREAM_VALIDATION_RECEIPT_SCHEMA = "tau.project_dream_validation_receipt.v1"

TERMINAL_STATUSES = {
    "SHADOW_STAGED",
    "NO_CHANGE",
    "NEEDS_HUMAN_REVIEW",
    "BLOCKED_INPUT",
    "BLOCKED_POLICY",
    "BLOCKED_DATA_BOUNDARY",
    "BLOCKED_MODEL",
    "BLOCKED_VALIDATION",
    "BLOCKED_APPROVAL",
    "BLOCKED_CAS",
    "PROMOTED",
    "ROLLED_BACK",
    "ERROR",
}

_BLOCKED_STATUSES = {status for status in TERMINAL_STATUSES if status.startswith("BLOCKED_")} | {
    "ERROR"
}
_ZERO_EFFECT_STATUSES = {
    "SHADOW_STAGED",
    "NO_CHANGE",
    "NEEDS_HUMAN_REVIEW",
    "BLOCKED_INPUT",
    "BLOCKED_POLICY",
    "BLOCKED_DATA_BOUNDARY",
    "BLOCKED_MODEL",
    "BLOCKED_VALIDATION",
    "BLOCKED_APPROVAL",
    "BLOCKED_CAS",
    "ERROR",
}
_REQUIRED_REFS = (
    "policy_profile",
    "data_boundary",
    "transcript_corpus",
    "archival_compaction",
    "project_state",
    "code_ingest",
    "evidence_packet",
    "command_spec",
    "provider_readiness",
    "prompt_schema_policy",
    "raw_model_output",
    "candidate",
    "deterministic_validation",
    "stage_response",
)
_FORBIDDEN_MODEL_CAPABILITIES = {
    "memory.direct",
    "memory.write_direct",
    "arangodb.write",
    "qdrant.write",
    "database.write",
    "generic_upsert",
}
_REQUIRED_NON_CLAIMS = ("semantic truth", "model agreement", "receipt existence")


def validate_project_dream_receipt_path(
    receipt_path: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Validate one project-dream receipt and optionally write a validation receipt."""

    resolved_receipt = receipt_path.expanduser().resolve()
    payload = _read_json_object(resolved_receipt, "project dream receipt")
    errors = validate_project_dream_receipt(payload, base_dir=resolved_receipt.parent)
    receipt = {
        "schema": PROJECT_DREAM_VALIDATION_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "receipt_path": str(resolved_receipt),
        "receipt_sha256": f"sha256:{_sha256(resolved_receipt)}",
        "project_dream_schema": payload.get("schema"),
        "terminal_status": payload.get("terminal_status"),
        "project_id": payload.get("project_id"),
        "run_id": payload.get("run_id"),
        "attempt_id": payload.get("attempt_id"),
        "error_count": len(errors),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau replay-validated the project-dream receipt from hash-bound artifacts.",
                "Tau checked project-dream policy, approval, CAS/head readback, "
                "effect-count, and data-boundary gates deterministically.",
            ],
            "does_not_prove": [
                "The synthesized project knowledge is semantically true.",
                "Provider/model quality.",
                "Production Graph Memory persistence beyond referenced CAS receipts.",
                "Human identity verification beyond referenced approval receipts.",
            ],
        },
    }
    if output_path is not None:
        resolved_output = output_path.expanduser().resolve()
        receipt["validation_receipt_path"] = str(resolved_output)
        _write_json(resolved_output, receipt)
    return receipt


def validate_project_dream_receipt(
    payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> list[str]:
    """Return deterministic validation errors for ``tau.project_dream_receipt.v1``."""

    base = (base_dir or Path.cwd()).expanduser().resolve()
    errors: list[str] = []
    if payload.get("schema") != PROJECT_DREAM_RECEIPT_SCHEMA:
        errors.append(f"schema must be {PROJECT_DREAM_RECEIPT_SCHEMA}")

    for key in ("run_id", "attempt_id", "project_id", "immutable_goal_hash", "proof_scope"):
        if not _non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")

    terminal_status = payload.get("terminal_status")
    if terminal_status not in TERMINAL_STATUSES:
        errors.append(f"terminal_status must be one of {sorted(TERMINAL_STATUSES)}")

    refs: dict[str, dict[str, Any]] = {}
    for key in _REQUIRED_REFS:
        refs[key] = _validate_ref(payload, key, base_dir=base, errors=errors, required=True)

    _validate_code_ingest(payload, refs.get("code_ingest") or {}, errors)
    _validate_provider(payload, errors)
    _validate_command_spec(refs.get("command_spec") or {}, errors)
    _validate_candidate_chain(payload, refs, errors)
    _validate_aggregate(payload, refs, errors)
    _validate_effects(payload, refs.get("data_boundary") or {}, errors)
    _validate_policy_and_terminal(payload, refs, terminal_status, base, errors)
    _validate_telemetry(payload, errors)
    _validate_non_claims(payload, errors)
    return errors


def _validate_ref(
    payload: dict[str, Any],
    key: str,
    *,
    base_dir: Path,
    errors: list[str],
    required: bool = False,
) -> dict[str, Any]:
    ref = payload.get(key)
    if ref is None:
        if required:
            errors.append(f"{key} ref is required")
        return {}
    if not isinstance(ref, dict):
        errors.append(f"{key} must be an object ref")
        return {}

    path_text = ref.get("path")
    expected_sha = _normalize_sha256(ref.get("sha256"))
    if not _non_empty_string(path_text):
        errors.append(f"{key}.path must be a non-empty string")
        return {}
    if expected_sha is None:
        errors.append(f"{key}.sha256 must be a sha256:<hex> string")

    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        errors.append(f"{key}.path does not exist or is not a file: {path}")
        return {}

    actual_sha = f"sha256:{_sha256(path)}"
    if expected_sha is not None and expected_sha != actual_sha:
        errors.append(f"{key}.sha256 mismatch: expected {expected_sha}, observed {actual_sha}")

    schema = ref.get("schema")
    if schema is None:
        return {}
    if not _non_empty_string(schema):
        errors.append(f"{key}.schema must be a non-empty string when present")
        return {}
    try:
        artifact = _read_json_object(path, key)
    except RuntimeError as exc:
        errors.append(str(exc))
        return {}
    observed_schema = artifact.get("schema")
    if observed_schema != schema:
        errors.append(f"{key}.schema mismatch: expected {schema}, observed {observed_schema}")
    return artifact


def _validate_code_ingest(
    payload: dict[str, Any], artifact: dict[str, Any], errors: list[str]
) -> None:
    code_ingest = payload.get("code_ingest")
    if not isinstance(code_ingest, dict):
        errors.append("code_ingest must be an object ref")
        return
    for key in ("git_commit", "coverage_scope"):
        if not _non_empty_string(code_ingest.get(key)) and not _non_empty_string(artifact.get(key)):
            errors.append(f"code_ingest.{key} must be a non-empty string")
    eligible = code_ingest.get("reconciliation_eligible", artifact.get("reconciliation_eligible"))
    if not isinstance(eligible, bool):
        errors.append("code_ingest.reconciliation_eligible must be a boolean")

    candidate = artifact.get("candidate") if isinstance(artifact.get("candidate"), dict) else {}
    claim_type = str(candidate.get("claim_type") or "")
    coverage = str(code_ingest.get("coverage_scope") or artifact.get("coverage_scope") or "")
    if coverage == "incremental" and claim_type in {"absence", "deprecation"}:
        errors.append("incremental ingest cannot support absence or deprecation claims")


def _validate_provider(payload: dict[str, Any], errors: list[str]) -> None:
    provider = payload.get("provider")
    if not isinstance(provider, dict):
        errors.append("provider must be an object")
        return
    for key in ("provider", "model"):
        if not _non_empty_string(provider.get(key)):
            errors.append(f"provider.{key} must be a non-empty string")


def _validate_command_spec(command_spec: dict[str, Any], errors: list[str]) -> None:
    declared = _string_set(command_spec.get("declared_capabilities")) | _string_set(
        command_spec.get("capabilities")
    )
    used = _string_set(command_spec.get("uses_capabilities")) | _string_set(
        command_spec.get("uses")
    )
    undeclared = sorted(used - declared)
    if undeclared:
        errors.append(f"command_spec uses undeclared capabilities: {undeclared}")
    forbidden = sorted((declared | used) & _FORBIDDEN_MODEL_CAPABILITIES)
    if forbidden:
        errors.append(f"command_spec contains permanently blocked model capabilities: {forbidden}")
    if command_spec.get("generic_upsert_as_promotion") is True:
        errors.append("generic upsert cannot be used as project-dream promotion")


def _validate_candidate_chain(
    payload: dict[str, Any], refs: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    candidate_digest = payload.get("candidate_digest")
    if not _non_empty_string(candidate_digest):
        errors.append("candidate_digest must be a non-empty string")
        return
    for key in ("candidate", "deterministic_validation", "stage_response"):
        observed = refs.get(key, {}).get("candidate_digest")
        if observed is not None and observed != candidate_digest:
            errors.append(
                f"{key}.candidate_digest mismatch: expected {candidate_digest}, observed {observed}"
            )

    corpus = refs.get("transcript_corpus") or {}
    if corpus.get("includes_dream_worker_transcript") is True:
        errors.append("dream worker transcript cannot be independent source evidence")


def _validate_aggregate(
    payload: dict[str, Any], refs: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    aggregate = payload.get("aggregate")
    if not isinstance(aggregate, dict):
        errors.append("aggregate must be an object")
        return
    for name in ("candidate_count", "terminal_row_count"):
        expected = aggregate.get(f"expected_{name}")
        observed = aggregate.get(f"observed_{name}")
        if not isinstance(expected, int) or expected < 0:
            errors.append(f"aggregate.expected_{name} must be a non-negative integer")
        if not isinstance(observed, int) or observed < 0:
            errors.append(f"aggregate.observed_{name} must be a non-negative integer")
        if isinstance(expected, int) and isinstance(observed, int) and expected != observed:
            errors.append(f"aggregate {name} mismatch: expected {expected}, observed {observed}")
    validation = refs.get("deterministic_validation") or {}
    for name in ("candidate_count", "terminal_row_count"):
        observed = validation.get(name)
        expected = aggregate.get(f"observed_{name}")
        if observed is not None and observed != expected:
            errors.append(
                f"deterministic_validation.{name} mismatch: "
                f"expected {expected}, observed {observed}"
            )


def _validate_effects(
    payload: dict[str, Any], data_boundary: dict[str, Any], errors: list[str]
) -> None:
    effects = payload.get("effects")
    if effects is None:
        effects = []
    if not isinstance(effects, list):
        errors.append("effects must be a list when present")
        return
    project_id = payload.get("project_id")
    allowed_projects = set(data_boundary.get("allowed_projects") or [])
    if project_id and not allowed_projects:
        allowed_projects = {str(project_id)}
    allowed_paths = [
        Path(str(item)).expanduser().resolve() for item in data_boundary.get("allowed_paths") or []
    ]
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict):
            errors.append(f"effects[{index}] must be an object")
            continue
        effect_project = effect.get("project_id")
        if effect_project is not None and str(effect_project) not in allowed_projects:
            errors.append(f"effects[{index}].project_id escapes data boundary: {effect_project}")
        path_text = effect.get("path")
        if path_text is not None and allowed_paths:
            path = Path(str(path_text)).expanduser().resolve()
            if not any(_is_relative_to(path, allowed) for allowed in allowed_paths):
                errors.append(f"effects[{index}].path escapes data boundary: {path}")


def _validate_policy_and_terminal(
    payload: dict[str, Any],
    refs: dict[str, dict[str, Any]],
    terminal_status: object,
    base_dir: Path,
    errors: list[str],
) -> None:
    policy = payload.get("promotion_policy")
    if not isinstance(policy, dict):
        errors.append("promotion_policy must be an object")
        return
    policy_class = policy.get("policy_class")
    decision = policy.get("decision")
    if policy_class not in {"shadow_only", "low_risk_auto", "human_approval_required", "blocked"}:
        errors.append("promotion_policy.policy_class is invalid")
    if not _non_empty_string(decision):
        errors.append("promotion_policy.decision must be a non-empty string")

    if terminal_status in _BLOCKED_STATUSES and not _non_empty_string(
        payload.get("first_failed_gate")
    ):
        errors.append("first_failed_gate is required for blocked/error terminal statuses")
    if terminal_status not in _BLOCKED_STATUSES and payload.get("first_failed_gate") not in {
        None,
        "",
    }:
        errors.append("first_failed_gate must be empty for successful terminal statuses")

    accepted_effect_count = payload.get("accepted_effect_count")
    accepted_effects = payload.get("accepted_effects", [])
    if not isinstance(accepted_effect_count, int) or accepted_effect_count < 0:
        errors.append("accepted_effect_count must be a non-negative integer")
    if not isinstance(accepted_effects, list):
        errors.append("accepted_effects must be a list when present")
        accepted_effects = []
    if isinstance(accepted_effect_count, int) and accepted_effect_count != len(accepted_effects):
        errors.append(
            "accepted_effect_count mismatch: "
            f"expected {accepted_effect_count}, observed {len(accepted_effects)}"
        )
    if terminal_status in _ZERO_EFFECT_STATUSES and accepted_effect_count not in {0, None}:
        errors.append(f"{terminal_status} must not claim accepted effects")
    if terminal_status == "PROMOTED" and accepted_effect_count != 1:
        errors.append("PROMOTED requires exactly one accepted effect")
    if terminal_status == "ROLLED_BACK" and accepted_effect_count != 1:
        errors.append("ROLLED_BACK requires exactly one rollback effect")

    if terminal_status == "SHADOW_STAGED" and decision != "shadow_only":
        errors.append("SHADOW_STAGED requires promotion_policy.decision=shadow_only")
    if terminal_status == "NEEDS_HUMAN_REVIEW" and not _approval_required(policy):
        errors.append("NEEDS_HUMAN_REVIEW requires a human-approval-required policy")
    if terminal_status == "PROMOTED":
        _validate_promotion(payload, refs, policy, base_dir, errors)
    if terminal_status == "ROLLED_BACK":
        rollback = _validate_ref(
            payload, "rollback_receipt", base_dir=base_dir, errors=errors, required=True
        )
        if rollback.get("deletes_intervening_history") is True:
            errors.append("rollback must not delete intervening history")


def _validate_promotion(
    payload: dict[str, Any],
    refs: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    base_dir: Path,
    errors: list[str],
) -> None:
    if policy.get("policy_class") == "blocked" or policy.get("permanently_blocked") is True:
        errors.append("permanently blocked project-dream changes cannot be promoted")
    decision = policy.get("decision")
    if decision not in {"auto_promote", "approved_by_human"}:
        errors.append(
            "PROMOTED requires promotion_policy.decision auto_promote or approved_by_human"
        )
    if _approval_required(policy):
        approval = _validate_ref(
            payload, "approval_receipt", base_dir=base_dir, errors=errors, required=True
        )
        _validate_approval(payload, approval, errors)

    promotion = _validate_ref(
        payload, "promotion_receipt", base_dir=base_dir, errors=errors, required=True
    )
    head_readback = _validate_ref(
        payload, "head_readback", base_dir=base_dir, errors=errors, required=True
    )
    if promotion.get("cas_outcome") not in {"APPLIED", "PROMOTED"} or promotion.get(
        "status"
    ) not in {
        "PASS",
        "APPLIED",
        "PROMOTED",
    }:
        errors.append("promotion_receipt must show successful Graph Memory CAS application")
    candidate_digest = payload.get("candidate_digest")
    if promotion.get("candidate_digest") not in {None, candidate_digest}:
        errors.append("promotion_receipt.candidate_digest does not match receipt candidate_digest")

    expected_head = payload.get("expected_head")
    if not isinstance(expected_head, dict):
        errors.append("expected_head must be an object for PROMOTED")
        return
    _check_equal(expected_head, "before_generation", promotion, "head_before_generation", errors)
    _check_equal(expected_head, "before_digest", promotion, "head_before_digest", errors)
    _check_equal(expected_head, "after_generation", promotion, "head_after_generation", errors)
    _check_equal(expected_head, "after_digest", promotion, "head_after_digest", errors)
    _check_equal(expected_head, "after_generation", head_readback, "generation", errors)
    _check_equal(expected_head, "after_digest", head_readback, "digest", errors)


def _validate_approval(
    payload: dict[str, Any], approval: dict[str, Any], errors: list[str]
) -> None:
    if approval.get("decision") != "approved":
        errors.append("approval_receipt.decision must be approved")
    if approval.get("project_id") != payload.get("project_id"):
        errors.append("approval_receipt.project_id does not match receipt project_id")
    if approval.get("candidate_digest") != payload.get("candidate_digest"):
        errors.append("approval_receipt.candidate_digest does not match receipt candidate_digest")
    expires_at = approval.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("approval_receipt.expires_at must be an ISO timestamp")
        else:
            if expires <= datetime.now(UTC):
                errors.append("approval_receipt is expired")
    else:
        errors.append("approval_receipt.expires_at must be an ISO timestamp")


def _validate_telemetry(payload: dict[str, Any], errors: list[str]) -> None:
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict):
        errors.append("telemetry must be an object")
        return
    if telemetry.get("status") == "unknown":
        for key in ("cost_usd", "input_tokens", "output_tokens", "duration_ms"):
            if telemetry.get(key) == 0:
                errors.append(f"telemetry.{key} must be unknown/null, not zero, when unavailable")


def _validate_non_claims(payload: dict[str, Any], errors: list[str]) -> None:
    claims = payload.get("explicit_non_claims")
    if not isinstance(claims, list) or not all(isinstance(item, str) and item for item in claims):
        errors.append("explicit_non_claims must be a non-empty list of strings")
        return
    text = "\n".join(claims).lower()
    for required in _REQUIRED_NON_CLAIMS:
        if required not in text:
            errors.append(f"explicit_non_claims must mention {required}")


def _approval_required(policy: dict[str, Any]) -> bool:
    return (
        bool(policy.get("human_approval_required"))
        or policy.get("policy_class") == "human_approval_required"
    )


def _check_equal(
    left: dict[str, Any],
    left_key: str,
    right: dict[str, Any],
    right_key: str,
    errors: list[str],
) -> None:
    if left.get(left_key) != right.get(right_key):
        errors.append(
            f"head mismatch: expected_head.{left_key}={left.get(left_key)!r} "
            f"but observed {right_key}={right.get(right_key)!r}"
        )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
