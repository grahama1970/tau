"""Hash-bound code patch validation and application receipts."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

CODE_PATCH_SCHEMA = "tau.code_patch.v1"
CODE_PATCH_RECEIPT_SCHEMA = "tau.code_patch_receipt.v1"

GENERATED_PATH_PATTERNS = (
    "**/generated/**",
    "**/__generated__/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/dist/**",
    "**/build/**",
)


def apply_code_patch_receipt(
    *,
    patch_path: Path,
    repo_root: Path,
    receipt_path: Path | None = None,
    expected_goal_hash: str | None = None,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
    zero_trust: bool = False,
    apply: bool = True,
) -> dict[str, Any]:
    """Validate a tau.code_patch.v1 artifact and write a receipt.

    The patch language is deliberately small for the first Tau-native coding
    rung: ``patch`` must be a JSON array string of exact replace operations:
    ``[{"op":"replace","old":"...","new":"..."}]``. Exact replacement plus
    base/post hashes keeps the first implementation deterministic and
    fail-closed.
    """

    resolved_patch = patch_path.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    alerts: list[dict[str, Any]] = []
    payload = _read_json_object(resolved_patch, alerts)
    before_sha: str | None = None
    after_sha: str | None = None
    staged_sha: str | None = None
    target_path: Path | None = None
    relative_target: str | None = None
    operations: list[dict[str, str]] = []
    staged_text: str | None = None

    if payload.get("schema") != CODE_PATCH_SCHEMA:
        alerts.append(_alert("invalid_schema", f"schema must be {CODE_PATCH_SCHEMA}"))

    goal_hash = _string(payload.get("goal_hash"))
    if not goal_hash:
        alerts.append(_alert("missing_goal_hash", "code patch goal_hash is required"))
    elif expected_goal_hash is not None and goal_hash != expected_goal_hash:
        alerts.append(
            _alert("goal_hash_mismatch", "code patch goal_hash did not match expected goal")
        )

    if zero_trust or policy_profile is not None or data_boundary is not None:
        alerts.extend(
            _policy_boundary_alerts(
                policy_profile=policy_profile,
                data_boundary=data_boundary,
            )
        )

    target_text = _string(payload.get("target_file"))
    allowed_paths = _string_list(payload.get("allowed_paths"))
    forbidden_paths = _string_list(payload.get("forbidden_paths"))
    if not target_text:
        alerts.append(_alert("missing_target_file", "target_file is required"))
    else:
        target_path, relative_target = _resolve_target(resolved_repo, target_text)
        if target_path is None or relative_target is None:
            alerts.append(_alert("target_path_escape", "target_file must stay inside repo_root"))
        elif not target_path.is_file():
            alerts.append(_alert("target_missing", "target_file does not exist"))
        else:
            if not _path_allowed(relative_target, allowed_paths):
                alerts.append(_alert("disallowed_path", "target_file is outside allowed_paths"))
            if _path_forbidden(relative_target, forbidden_paths) or _path_forbidden(
                relative_target,
                list(GENERATED_PATH_PATTERNS),
            ):
                alerts.append(_alert("forbidden_path", "target_file is forbidden or generated"))
            before_sha = f"sha256:{_sha256(target_path)}"
            expected_base = _sha256_string(payload.get("base_file_sha256"))
            if expected_base is None:
                alerts.append(
                    _alert("missing_base_file_sha256", "base_file_sha256 must be sha256:<hex>")
                )
            elif expected_base != before_sha:
                alerts.append(_alert("stale_base_hash", "target file hash changed before patch"))

    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        alerts.append(_alert("missing_anchor", "at least one anchor is required"))
    elif target_path is not None and target_path.is_file():
        text = target_path.read_text(encoding="utf-8")
        anchor_errors = _anchor_errors(anchors, text)
        alerts.extend(_alert("missing_anchor", error) for error in anchor_errors)

    patch_text = _string(payload.get("patch"))
    if not patch_text:
        alerts.append(_alert("malformed_patch", "patch must be a non-empty JSON string"))
    elif target_path is not None and target_path.is_file():
        try:
            operations = _parse_patch_operations(patch_text)
            staged_text = _apply_operations(target_path.read_text(encoding="utf-8"), operations)
        except RuntimeError as exc:
            alerts.append(_alert("malformed_patch", str(exc)))

    expected_post = _sha256_string(payload.get("expected_post_sha256"))
    if expected_post is None:
        alerts.append(
            _alert("missing_expected_post_sha256", "expected_post_sha256 must be sha256:<hex>")
        )
    elif staged_text is not None:
        staged_sha = f"sha256:{hashlib.sha256(staged_text.encode('utf-8')).hexdigest()}"
        if staged_sha != expected_post:
            alerts.append(
                _alert(
                    "expected_post_sha256_mismatch",
                    "staged patch content did not match expected_post_sha256",
                )
            )

    ok = not alerts
    applied = False
    if ok and apply and target_path is not None and staged_text is not None:
        target_path.write_text(staged_text, encoding="utf-8")
        after_sha = f"sha256:{_sha256(target_path)}"
        applied = True
    elif target_path is not None and target_path.is_file():
        after_sha = f"sha256:{_sha256(target_path)}"

    receipt = {
        "schema": CODE_PATCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": dict(policy_profile) if policy_profile is not None else None,
        "data_boundary": dict(data_boundary) if data_boundary is not None else None,
        "patch_path": str(resolved_patch),
        "repo_root": str(resolved_repo),
        "goal_hash": goal_hash,
        "target_file": relative_target,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "generated_path_patterns": list(GENERATED_PATH_PATTERNS),
        "applied": applied,
        "operation_count": len(operations),
        "before_sha256": before_sha,
        "staged_sha256": staged_sha,
        "after_sha256": after_sha,
        "expected_post_sha256": expected_post,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau checked a hash-bound code patch artifact before applying it.",
                "Tau blocked stale base hashes, missing anchors, disallowed paths, "
                "and post-hash mismatches.",
            ],
            "does_not_prove": [
                "The patch is semantically correct.",
                "The full test suite passes.",
                "The change is safe for production.",
                "The agent that proposed the patch is trustworthy.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_patch.with_name("code-patch-receipt.json")
    )
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(resolved_receipt)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _policy_boundary_alerts(
    *,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust code patch requires policy_profile")
        )
    elif policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(
            _alert(
                "invalid_policy_profile_schema",
                f"policy_profile.schema must be {POLICY_PROFILE_SCHEMA}",
            )
        )
    if data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust code patch requires data_boundary")
        )
    elif data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(
            _alert(
                "invalid_data_boundary_schema",
                f"data_boundary.schema must be {DATA_BOUNDARY_SCHEMA}",
            )
        )
    return alerts


def _resolve_target(repo_root: Path, target_text: str) -> tuple[Path | None, str | None]:
    target = (repo_root / target_text).resolve()
    try:
        relative = target.relative_to(repo_root)
    except ValueError:
        return None, None
    return target, relative.as_posix()


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return bool(patterns) and any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _path_forbidden(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _anchor_errors(anchors: list[object], text: str) -> list[str]:
    errors: list[str] = []
    lines = text.splitlines()
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            errors.append(f"anchors[{index}] must be an object")
            continue
        kind = anchor.get("kind")
        value = anchor.get("value")
        if not isinstance(kind, str) or not isinstance(value, str) or not value:
            errors.append(f"anchors[{index}] requires kind and value")
            continue
        if kind == "content_hash":
            if _sha256_text(text) != _sha256_string(value):
                errors.append(f"anchors[{index}] content_hash is stale or missing")
        elif kind == "line_span":
            if value not in lines and value not in text:
                errors.append(f"anchors[{index}] line_span text was not found")
        elif kind == "symbol":
            if value not in text:
                errors.append(f"anchors[{index}] symbol was not found")
        else:
            errors.append(f"anchors[{index}] kind is unsupported: {kind}")
    return errors


def _parse_patch_operations(patch_text: str) -> list[dict[str, str]]:
    try:
        raw = json.loads(patch_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"patch is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("patch must be a non-empty JSON array")
    operations: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise RuntimeError(f"patch[{index}] must be an object")
        if item.get("op") != "replace":
            raise RuntimeError(f"patch[{index}].op must be replace")
        old = item.get("old")
        new = item.get("new")
        if not isinstance(old, str) or not old:
            raise RuntimeError(f"patch[{index}].old must be a non-empty string")
        if not isinstance(new, str):
            raise RuntimeError(f"patch[{index}].new must be a string")
        operations.append({"op": "replace", "old": old, "new": new})
    return operations


def _apply_operations(text: str, operations: list[dict[str, str]]) -> str:
    current = text
    for index, operation in enumerate(operations):
        old = operation["old"]
        if current.count(old) != 1:
            raise RuntimeError(f"patch[{index}].old must match exactly once")
        current = current.replace(old, operation["new"], 1)
    return current


def _read_json_object(path: Path, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append(_alert("code_patch_missing", "code patch artifact is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(_alert("code_patch_unreadable", f"code patch is not readable JSON: {exc}"))
        return {}
    if not isinstance(payload, dict):
        alerts.append(_alert("code_patch_not_object", "code patch root must be an object"))
        return {}
    return payload


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in value]
    return []


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _sha256_string(value: object) -> str | None:
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


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
