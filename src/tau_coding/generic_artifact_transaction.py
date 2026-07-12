"""Hash-bound artifact transaction contracts for the generic Tau DAG runner.

The helpers in this module parse transaction declarations, write run-owned
contexts, validate producer and reviewer claims, and revalidate accepted state.
They do not schedule commands or decide DAG dependencies.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRANSACTION_SCHEMA = "tau.generic_artifact_transaction.v1"
CANDIDATE_MANIFEST_SCHEMA = "tau.media_artifact_manifest.v1"
REVIEW_SCHEMA = "tau.generic_artifact_review.v1"
VALIDATION_SCHEMA = "tau.generic_artifact_validation.v1"
ATTEMPT_CONTEXT_SCHEMA = "tau.generic_artifact_attempt_context.v1"
REVIEW_CONTEXT_SCHEMA = "tau.generic_artifact_review_context.v1"
ACCEPTED_MANIFEST_SCHEMA = "tau.accepted_artifact_manifest.v1"
TRANSACTION_RECEIPT_SCHEMA = "tau.generic_artifact_transaction_receipt.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


@dataclass(frozen=True)
class ReviewerSpec:
    reviewer_id: str
    command: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class ValidatorSpec:
    validator_id: str
    command: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class ApprovalSpec:
    action: str
    packet_path: Path


@dataclass(frozen=True)
class ContinuationSpec:
    command: tuple[str, ...]
    timeout_seconds: float
    approval: ApprovalSpec | None


@dataclass(frozen=True)
class AcceptanceSpec:
    require_provider_live_producer: bool
    require_provider_live_reviewer: bool
    require_output_change_after_revise: bool
    require_distinct_from_accepted_inputs: bool


@dataclass(frozen=True)
class ArtifactTransactionSpec:
    transaction_id: str
    artifact_root: Path
    producer_id: str
    validator: ValidatorSpec | None
    reviewer: ReviewerSpec
    acceptance: AcceptanceSpec
    continuation: ContinuationSpec | None


def parse_transaction_spec(raw: Any, *, base_dir: Path, node_id: str) -> ArtifactTransactionSpec:
    """Parse and resolve an optional-node transaction declaration."""

    if not isinstance(raw, dict):
        raise RuntimeError(f"node {node_id} transaction must be an object")
    if raw.get("schema") != TRANSACTION_SCHEMA:
        raise RuntimeError(f"node {node_id} transaction schema must be {TRANSACTION_SCHEMA}")
    transaction_id = _required_string(raw, "transaction_id", node_id=node_id)
    producer_id = _required_string(raw, "producer_id", node_id=node_id)
    artifact_root = _resolve_path(
        _required_string(raw, "artifact_root", node_id=node_id), base_dir=base_dir
    )
    reviewer_raw = raw.get("reviewer")
    if not isinstance(reviewer_raw, dict):
        raise RuntimeError(f"node {node_id} transaction reviewer must be an object")
    reviewer_id = _required_string(reviewer_raw, "reviewer_id", node_id=node_id)
    if reviewer_id == producer_id:
        raise RuntimeError(f"node {node_id} transaction reviewer must differ from producer")
    reviewer = ReviewerSpec(
        reviewer_id=reviewer_id,
        command=_command(reviewer_raw.get("command"), label=f"node {node_id} reviewer command"),
        timeout_seconds=_positive_float(
            reviewer_raw.get("timeout_seconds", 60), label=f"node {node_id} reviewer timeout"
        ),
    )
    validator_raw = raw.get("validator")
    validator = None
    if validator_raw is not None:
        if not isinstance(validator_raw, dict):
            raise RuntimeError(f"node {node_id} transaction validator must be an object")
        validator = ValidatorSpec(
            validator_id=_required_string(validator_raw, "validator_id", node_id=node_id),
            command=_command(
                validator_raw.get("command"), label=f"node {node_id} validator command"
            ),
            timeout_seconds=_positive_float(
                validator_raw.get("timeout_seconds", 60),
                label=f"node {node_id} validator timeout",
            ),
        )
    acceptance_raw = raw.get("acceptance", {})
    if not isinstance(acceptance_raw, dict):
        raise RuntimeError(f"node {node_id} transaction acceptance must be an object")
    acceptance = AcceptanceSpec(
        require_provider_live_producer=_optional_bool(
            acceptance_raw,
            "require_provider_live_producer",
            node_id=node_id,
        ),
        require_provider_live_reviewer=_optional_bool(
            acceptance_raw,
            "require_provider_live_reviewer",
            node_id=node_id,
        ),
        require_output_change_after_revise=_optional_bool(
            acceptance_raw,
            "require_output_change_after_revise",
            node_id=node_id,
        ),
        require_distinct_from_accepted_inputs=_optional_bool(
            acceptance_raw,
            "require_distinct_from_accepted_inputs",
            node_id=node_id,
        ),
    )
    continuation_raw = raw.get("continuation")
    continuation = None
    if continuation_raw is not None:
        if not isinstance(continuation_raw, dict):
            raise RuntimeError(f"node {node_id} transaction continuation must be an object")
        approval_raw = continuation_raw.get("approval")
        approval = None
        if approval_raw is not None:
            if not isinstance(approval_raw, dict):
                raise RuntimeError(f"node {node_id} continuation approval must be an object")
            approval = ApprovalSpec(
                action=_required_string(approval_raw, "action", node_id=node_id),
                packet_path=_resolve_path(
                    _required_string(approval_raw, "packet_path", node_id=node_id),
                    base_dir=base_dir,
                ),
            )
        continuation = ContinuationSpec(
            command=_command(
                continuation_raw.get("command"), label=f"node {node_id} continuation command"
            ),
            timeout_seconds=_positive_float(
                continuation_raw.get("timeout_seconds", 60),
                label=f"node {node_id} continuation timeout",
            ),
            approval=approval,
        )
    return ArtifactTransactionSpec(
        transaction_id=transaction_id,
        artifact_root=artifact_root,
        producer_id=producer_id,
        validator=validator,
        reviewer=reviewer,
        acceptance=acceptance,
        continuation=continuation,
    )


def validate_acceptance_policy(
    *,
    spec: ArtifactTransactionSpec,
    producer_receipt: dict[str, Any],
    review_feedback: dict[str, Any],
    artifacts: list[dict[str, Any]],
    previous_artifact_sha256s: set[str],
    accepted_inputs: list[dict[str, Any]],
) -> list[str]:
    """Validate role-specific liveness and output-change acceptance invariants."""

    errors: list[str] = []
    policy = spec.acceptance
    producer_execution = producer_receipt.get("provider_execution")
    producer_provider_live = producer_receipt.get("provider_live") is True or (
        isinstance(producer_execution, dict) and producer_execution.get("provider_live") is True
    )
    if policy.require_provider_live_producer and not producer_provider_live:
        errors.append("producer_provider_live_required")
    if policy.require_provider_live_reviewer and review_feedback.get("provider_live") is not True:
        errors.append("reviewer_provider_live_required")
    artifact_hashes = {
        str(item.get("sha256")) for item in artifacts if isinstance(item.get("sha256"), str)
    }
    if (
        policy.require_output_change_after_revise
        and previous_artifact_sha256s
        and artifact_hashes == previous_artifact_sha256s
    ):
        errors.append("unchanged_output_after_revise")
    if policy.require_distinct_from_accepted_inputs:
        input_hashes = {
            str(artifact.get("sha256"))
            for projection in accepted_inputs
            for artifact in projection.get("artifacts", [])
            if isinstance(projection, dict)
            and isinstance(projection.get("artifacts"), list)
            and isinstance(artifact, dict)
            and isinstance(artifact.get("sha256"), str)
        }
        if artifact_hashes & input_hashes:
            errors.append("output_duplicates_accepted_input")
    return errors


def write_attempt_context(
    *,
    path: Path,
    run_id: str,
    node_id: str,
    spec: ArtifactTransactionSpec,
    attempt: int,
    max_attempts: int,
    work_order_path: Path,
    work_order_sha256: str,
    accepted_inputs: list[dict[str, Any]],
    revision: dict[str, Any] | None,
    candidate_manifest_path: Path,
    producer_receipt_path: Path,
) -> tuple[dict[str, Any], str]:
    payload = {
        "schema": ATTEMPT_CONTEXT_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "transaction_id": spec.transaction_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "producer_id": spec.producer_id,
        "reviewer_id": spec.reviewer.reviewer_id,
        "work_order": {"path": str(work_order_path), "sha256": work_order_sha256},
        "accepted_inputs": accepted_inputs,
        "revision": revision,
        "output_contract": {
            "candidate_manifest_path": str(candidate_manifest_path),
            "producer_receipt_path": str(producer_receipt_path),
        },
    }
    write_json(path, payload)
    return payload, file_sha256(path)


def write_review_context(
    *,
    path: Path,
    run_id: str,
    node_id: str,
    spec: ArtifactTransactionSpec,
    attempt: int,
    attempt_context_path: Path,
    attempt_context_sha256: str,
    candidate_manifest_path: Path,
    candidate_manifest_sha256: str,
    artifacts: list[dict[str, Any]],
    review_feedback_path: Path,
) -> tuple[dict[str, Any], str]:
    payload = {
        "schema": REVIEW_CONTEXT_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "transaction_id": spec.transaction_id,
        "attempt": attempt,
        "producer_id": spec.producer_id,
        "reviewer_id": spec.reviewer.reviewer_id,
        "attempt_context_path": str(attempt_context_path),
        "attempt_context_sha256": attempt_context_sha256,
        "candidate_manifest_path": str(candidate_manifest_path),
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "validated_artifacts": artifacts,
        "output_contract": {"review_feedback_path": str(review_feedback_path)},
    }
    write_json(path, payload)
    return payload, file_sha256(path)


def validate_candidate_manifest(
    *,
    path: Path,
    spec: ArtifactTransactionSpec,
    node_id: str,
    attempt: int,
    work_order_sha256: str,
    attempt_context_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    payload, errors = load_json(path, label="candidate manifest")
    if errors:
        return {}, errors
    expected = {
        "schema": CANDIDATE_MANIFEST_SCHEMA,
        "transaction_id": spec.transaction_id,
        "node_id": node_id,
        "attempt": attempt,
        "producer_id": spec.producer_id,
        "work_order_sha256": work_order_sha256,
        "attempt_context_sha256": attempt_context_sha256,
    }
    errors.extend(_binding_errors(payload, expected))
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("candidate_manifest_artifacts_required")
        return payload, errors
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append("artifact_entry_not_object")
            continue
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append("artifact_id_required")
        elif artifact_id in seen:
            errors.append(f"artifact_id_duplicate:{artifact_id}")
        else:
            seen.add(artifact_id)
        if not isinstance(item.get("kind"), str) or not str(item.get("kind")).strip():
            errors.append(f"artifact_kind_required:{artifact_id}")
        media_type = item.get("media_type")
        if not isinstance(media_type, str) or not _MEDIA_TYPE_RE.fullmatch(media_type):
            errors.append(f"artifact_media_type_invalid:{artifact_id}")
        errors.extend(_validate_artifact(item, root=spec.artifact_root, artifact_id=artifact_id))
    return payload, errors


def validate_review_feedback(
    *,
    path: Path,
    spec: ArtifactTransactionSpec,
    node_id: str,
    attempt: int,
    review_context_sha256: str,
    candidate_manifest_sha256: str,
    artifact_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    payload, errors = load_json(path, label="review feedback")
    if errors:
        return {}, errors
    expected = {
        "schema": REVIEW_SCHEMA,
        "transaction_id": spec.transaction_id,
        "node_id": node_id,
        "attempt": attempt,
        "producer_id": spec.producer_id,
        "reviewer_id": spec.reviewer.reviewer_id,
        "review_context_sha256": review_context_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
    }
    errors.extend(_binding_errors(payload, expected))
    verdict = str(payload.get("verdict") or "").upper()
    if verdict not in {"PASS", "REVISE", "BLOCKED"}:
        errors.append("review_verdict_invalid")
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        errors.append("review_summary_required")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        errors.append("review_findings_must_be_list")
        findings = []
    if verdict == "REVISE" and not findings:
        errors.append("revise_requires_findings")
    revision_instruction_found = False
    for finding in findings:
        if not isinstance(finding, dict):
            errors.append("review_finding_not_object")
            continue
        if (
            isinstance(finding.get("revision_instruction"), str)
            and finding["revision_instruction"].strip()
        ):
            revision_instruction_found = True
        refs = finding.get("artifact_ids")
        if not isinstance(refs, list):
            errors.append("review_artifact_ids_must_be_list")
        else:
            for ref in refs:
                if ref not in artifact_ids:
                    errors.append(f"review_unknown_artifact_id:{ref}")
        if verdict == "PASS" and str(finding.get("severity") or "").upper() == "BLOCK":
            errors.append("review_pass_contains_block_finding")
    if verdict == "REVISE" and not revision_instruction_found:
        errors.append("revise_requires_revision_instruction")
    return payload, errors


def write_accepted_manifest(
    *,
    path: Path,
    run_id: str,
    node_id: str,
    spec: ArtifactTransactionSpec,
    attempt: int,
    work_order_sha256: str,
    candidate_manifest_path: Path,
    review_feedback_path: Path,
    artifacts: list[dict[str, Any]],
    accepted_inputs: list[dict[str, Any]],
    validation_receipt_path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    payload = {
        "schema": ACCEPTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "transaction_id": spec.transaction_id,
        "accepted_attempt": attempt,
        "work_order_sha256": work_order_sha256,
        "source_manifest": {
            "path": str(candidate_manifest_path),
            "sha256": file_sha256(candidate_manifest_path),
        },
        "review_feedback": {
            "path": str(review_feedback_path),
            "sha256": file_sha256(review_feedback_path),
            "reviewer_id": spec.reviewer.reviewer_id,
        },
        "accepted_inputs": _accepted_input_bindings(accepted_inputs),
        "validation_receipt": (
            {
                "path": str(validation_receipt_path),
                "sha256": file_sha256(validation_receipt_path),
            }
            if validation_receipt_path is not None
            else None
        ),
        "artifacts": artifacts,
    }
    write_json(path, payload)
    return payload, file_sha256(path)


def revalidate_accepted_manifest(
    *,
    path: Path,
    expected_sha256: str,
    spec: ArtifactTransactionSpec,
    node_id: str,
    work_order_sha256: str,
    accepted_inputs: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    payload, errors = load_json(path, label="accepted manifest")
    if errors:
        return {}, errors
    if file_sha256(path) != expected_sha256:
        errors.append("stale_accepted_manifest")
    errors.extend(
        _binding_errors(
            payload,
            {
                "schema": ACCEPTED_MANIFEST_SCHEMA,
                "node_id": node_id,
                "transaction_id": spec.transaction_id,
                "work_order_sha256": work_order_sha256,
            },
        )
    )
    if payload.get("accepted_inputs") != _accepted_input_bindings(accepted_inputs):
        errors.append("stale_accepted_context")
    validation_receipt = payload.get("validation_receipt")
    if validation_receipt is not None:
        if not isinstance(validation_receipt, dict):
            errors.append("validation_receipt_binding_invalid")
        else:
            validation_path = Path(str(validation_receipt.get("path") or ""))
            expected_validation_sha = validation_receipt.get("sha256")
            if not validation_path.is_file():
                errors.append("validation_receipt_missing")
            elif file_sha256(validation_path) != expected_validation_sha:
                errors.append("validation_receipt_hash_mismatch")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("accepted_manifest_artifacts_required")
        return payload, errors
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append("accepted_artifact_not_object")
            continue
        errors.extend(
            _validate_artifact(item, root=spec.artifact_root, artifact_id=item.get("artifact_id"))
        )
    return payload, errors


def _accepted_input_bindings(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_node_id": item.get("source_node_id"),
            "accepted_manifest_sha256": item.get("accepted_manifest_sha256"),
        }
        for item in inputs
    ]


def accepted_projection(*, path: Path, sha256: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_node_id": payload["node_id"],
        "accepted_manifest_path": str(path),
        "accepted_manifest_sha256": sha256,
        "artifacts": payload["artifacts"],
    }


def canonical_command_sha256(command: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(command), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, *, label: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"{label.replace(' ', '_')}_missing:{path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"{label.replace(' ', '_')}_invalid_json:{exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{label.replace(' ', '_')}_not_object"]
    return payload, []


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_artifact(item: dict[str, Any], *, root: Path, artifact_id: Any) -> list[str]:
    errors: list[str] = []
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return [f"artifact_path_required:{artifact_id}"]
    path = Path(raw_path).expanduser().resolve()
    root_resolved = root.expanduser().resolve()
    if not path.is_relative_to(root_resolved):
        errors.append(f"artifact_path_outside_root:{artifact_id}")
        return errors
    if not path.is_file():
        errors.append(f"artifact_missing:{artifact_id}")
        return errors
    declared_hash = item.get("sha256")
    if not isinstance(declared_hash, str) or not _SHA256_RE.fullmatch(declared_hash):
        errors.append(f"artifact_sha256_invalid:{artifact_id}")
    elif file_sha256(path) != declared_hash:
        errors.append(f"artifact_hash_mismatch:{artifact_id}")
    size = item.get("bytes")
    if not isinstance(size, int) or size < 0:
        errors.append(f"artifact_bytes_invalid:{artifact_id}")
    elif path.stat().st_size != size:
        errors.append(f"artifact_size_mismatch:{artifact_id}")
    return errors


def _binding_errors(payload: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return [
        f"binding_mismatch:{key}" for key, value in expected.items() if payload.get(key) != value
    ]


def _required_string(payload: dict[str, Any], key: str, *, node_id: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"node {node_id} transaction {key} must be a non-empty string")
    return value


def _optional_bool(payload: dict[str, Any], key: str, *, node_id: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise RuntimeError(f"node {node_id} transaction acceptance {key} must be a boolean")
    return value


def _command(value: Any, *, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise RuntimeError(f"{label} must be a non-empty string list")
    return tuple(value)


def _positive_float(value: Any, *, label: str) -> float:
    result = float(value)
    if result <= 0:
        raise RuntimeError(f"{label} must be positive")
    return result


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
