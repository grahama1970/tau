"""Adapter from code-runner skill results into Tau coding receipts."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from tau_coding.code_patch import CODE_PATCH_SCHEMA, apply_code_patch_receipt

CODE_RUNNER_RESULT_SCHEMA = "code_runner.result.v1"
CODE_RUNNER_WORKER_RECEIPT_SCHEMA = "tau.code_runner_worker_receipt.v1"


def write_code_runner_skill_adapter_receipt(
    *,
    result_path: Path,
    output_path: Path,
    repo_root: Path,
    expected_goal_hash: str | None = None,
) -> dict[str, Any]:
    resolved_result = result_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    errors: list[str] = []
    result = _read_json_object(resolved_result, errors=errors, label="code-runner result")
    if result:
        _validate_result(result, expected_goal_hash=expected_goal_hash, errors=errors)
    patch_path = _required_artifact_path(
        result.get("patch_artifact"),
        repo_root=resolved_repo,
        field="patch_artifact",
        errors=errors,
    )
    dod_path = _required_artifact_path(
        result.get("dod_artifact"),
        repo_root=resolved_repo,
        field="dod_artifact",
        errors=errors,
    )
    test_log_path = _required_artifact_path(
        result.get("test_log_artifact"),
        repo_root=resolved_repo,
        field="test_log_artifact",
        errors=errors,
    )
    code_patch_receipt: dict[str, Any] | None = None
    if patch_path is not None:
        patch_payload = _read_json_object(patch_path, errors=errors, label="code patch artifact")
        _validate_patch_allowlist(
            patch_payload,
            result=result,
            errors=errors,
        )
        if not errors:
            code_patch_receipt = apply_code_patch_receipt(
                patch_path=patch_path,
                repo_root=resolved_repo,
                receipt_path=resolved_output.parent / "code-patch-receipt.json",
                expected_goal_hash=expected_goal_hash,
                apply=False,
            )
            if code_patch_receipt.get("ok") is not True:
                errors.append("code patch receipt blocked")
    status = "PASS" if not errors else "BLOCKED"
    payload = {
        "schema": CODE_RUNNER_WORKER_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "source_result_path": str(resolved_result),
        "repo_root": str(resolved_repo),
        "goal_hash": result.get("goal_hash"),
        "expected_goal_hash": expected_goal_hash,
        "worker_status": result.get("status"),
        "patch_artifact": _artifact_ref(patch_path),
        "dod_artifact": _artifact_ref(dod_path),
        "test_log_artifact": _artifact_ref(test_log_path),
        "code_patch_receipt_path": str(resolved_output.parent / "code-patch-receipt.json")
        if code_patch_receipt is not None
        else None,
        "code_patch_receipt_status": code_patch_receipt.get("status")
        if code_patch_receipt is not None
        else None,
        "errors": errors,
        "course_correction": _course_correction(errors),
        "proof_scope": {
            "proves": [
                "Tau ingested a code-runner result artifact.",
                "Tau checked patch, DoD, and test/log artifact presence.",
                "Tau dry-run validated the patch through tau.code_patch_receipt.v1.",
            ],
            "does_not_prove": [
                "The patch is semantically correct.",
                "The task is complete.",
                "The worker model was truthful.",
                "The patch was applied.",
            ],
        },
    }
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _validate_result(
    result: dict[str, Any],
    *,
    expected_goal_hash: str | None,
    errors: list[str],
) -> None:
    if result.get("schema") != CODE_RUNNER_RESULT_SCHEMA:
        errors.append(f"schema must be {CODE_RUNNER_RESULT_SCHEMA}")
    if result.get("status") != "PASS":
        errors.append("code-runner result status is not PASS")
    goal_hash = result.get("goal_hash")
    if not isinstance(goal_hash, str) or not goal_hash:
        errors.append("goal_hash is required")
    elif expected_goal_hash and goal_hash != expected_goal_hash:
        errors.append("goal_hash mismatches expected_goal_hash")


def _required_artifact_path(
    value: Any,
    *,
    repo_root: Path,
    field: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{field} is required")
        return None
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    if not _is_relative_to(resolved, repo_root):
        errors.append(f"{field} escapes repo root: {resolved}")
        return None
    if not resolved.is_file():
        errors.append(f"{field} is missing or not a file: {resolved}")
        return None
    return resolved


def _validate_patch_allowlist(
    patch: dict[str, Any],
    *,
    result: dict[str, Any],
    errors: list[str],
) -> None:
    if patch.get("schema") != CODE_PATCH_SCHEMA:
        errors.append(f"patch_artifact schema must be {CODE_PATCH_SCHEMA}")
        return
    target_file = patch.get("target_file")
    if not isinstance(target_file, str) or not target_file:
        errors.append("patch_artifact target_file is required")
        return
    allowed_paths = result.get("allowed_paths", patch.get("allowed_paths", []))
    if not isinstance(allowed_paths, list) or not allowed_paths:
        errors.append("allowed_paths is required")
        return
    if not all(isinstance(item, str) and item for item in allowed_paths):
        errors.append("allowed_paths must be non-empty strings")
        return
    if not any(fnmatch.fnmatch(target_file, pattern) for pattern in allowed_paths):
        errors.append("patch target_file is outside allowed_paths")


def _artifact_ref(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
    }


def _course_correction(errors: list[str]) -> dict[str, Any] | None:
    if not errors:
        return None
    return {
        "schema": "tau.course_correction.v1",
        "trigger": "missing_evidence",
        "required_next_action": "retry_node",
        "allowed_next_routes": ["code-runner", "reviewer", "human"],
        "forbidden_next_routes": ["claim_patch_without_dod"],
        "required_evidence_before_retry": [
            "code_runner.result.v1",
            "tau.code_patch_receipt.v1",
            "dod_artifact",
            "test_log_artifact",
        ],
    }


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
