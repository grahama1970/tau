"""Hash-bound code patch validation and application receipts."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import tokenize
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.course_correction import build_course_correction_receipt
from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
)

CODE_PATCH_SCHEMA = "tau.code_patch.v1"
CODE_PATCH_RECEIPT_SCHEMA = "tau.code_patch_receipt.v1"

GENERATED_PATH_PATTERNS = (
    "generated/**",
    "**/generated/**",
    "__generated__/**",
    "**/__generated__/**",
    "node_modules/**",
    "**/node_modules/**",
    ".venv/**",
    "**/.venv/**",
    "dist/**",
    "**/dist/**",
    "build/**",
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
    run_id: str | None = None,
    dag_id: str | None = None,
    node_id: str | None = None,
    agent: str | None = None,
    attempt: int | None = None,
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
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_patch.with_name("code-patch-receipt.json")
    )
    alerts: list[dict[str, Any]] = []
    patch_inside_repo = _path_inside_root(resolved_patch, resolved_repo)
    if zero_trust and not patch_inside_repo:
        alerts.append(
            _alert(
                "code_patch_outside_repo",
                "zero-trust code patch artifact must stay inside repo_root",
            )
        )
    if zero_trust and not _path_inside_root(resolved_receipt, resolved_repo):
        alerts.append(
            _alert(
                "code_patch_receipt_outside_repo",
                "zero-trust code patch receipt must stay inside repo_root",
            )
        )
    payload = _read_json_object(resolved_patch, alerts)
    before_sha: str | None = None
    after_sha: str | None = None
    staged_sha: str | None = None
    target_path: Path | None = None
    target_artifact_before: dict[str, Any] | None = None
    target_artifact_after: dict[str, Any] | None = None
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
    allowed_paths, allowed_paths_alerts = _optional_string_list(
        payload.get("allowed_paths"),
        field="allowed_paths",
    )
    forbidden_paths, forbidden_paths_alerts = _optional_string_list(
        payload.get("forbidden_paths"),
        field="forbidden_paths",
    )
    alerts.extend(allowed_paths_alerts)
    alerts.extend(forbidden_paths_alerts)
    if not allowed_paths:
        alerts.append(
            _alert(
                "missing_allowed_paths",
                "allowed_paths must include at least one path pattern",
            )
        )
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
            policy_write_allowlist = _policy_write_allowlist(policy_profile)
            if policy_write_allowlist is not None and not _path_allowed(
                relative_target,
                policy_write_allowlist,
            ):
                alerts.append(
                    _alert(
                        "policy_write_disallowed",
                        "target_file is outside policy_profile.filesystem.write_allowlist",
                    )
                )
            if _path_forbidden(relative_target, forbidden_paths) or _path_forbidden(
                relative_target,
                list(GENERATED_PATH_PATTERNS),
            ):
                alerts.append(_alert("forbidden_path", "target_file is forbidden or generated"))
            before_sha = f"sha256:{_sha256(target_path)}"
            target_artifact_before = _target_artifact_descriptor("target_before", target_path)
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
        target_artifact_after = _target_artifact_descriptor("target_after", target_path)
        applied = True
    elif target_path is not None and target_path.is_file():
        after_sha = f"sha256:{_sha256(target_path)}"
        target_artifact_after = _target_artifact_descriptor("target_after", target_path)

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
        "patch_sha256": _artifact_sha256_uri(resolved_patch),
        "patch_bytes": _artifact_size(resolved_patch),
        "patch_artifact": _artifact_descriptor("code_patch", resolved_patch),
        "repo_root": str(resolved_repo),
        "goal_hash": goal_hash,
        "target_file": relative_target,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "generated_path_patterns": list(GENERATED_PATH_PATTERNS),
        "apply_requested": apply,
        "dry_run": not apply,
        "applied": applied,
        "operation_count": len(operations),
        "before_sha256": before_sha,
        "staged_sha256": staged_sha,
        "after_sha256": after_sha,
        "target_artifact_before": target_artifact_before,
        "target_artifact_after": target_artifact_after,
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
    if not ok:
        receipt["course_correction"] = build_course_correction_receipt(
            trigger=_course_correction_trigger(receipt["alert_codes"]),
            run_id=run_id,
            dag_id=dag_id,
            goal_hash=goal_hash or expected_goal_hash,
            target={"target_file": relative_target} if relative_target else {},
            node_id=node_id,
            agent=agent,
            attempt=attempt,
            observed_state={
                "alert_codes": list(receipt["alert_codes"]),
                "target_file": relative_target,
                "apply_requested": apply,
            },
            observed_artifact_path=resolved_patch,
            live=True,
        )
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(resolved_receipt)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _course_correction_trigger(alert_codes: list[str]) -> str:
    if "stale_base_hash" in alert_codes:
        return "patch_stale"
    return "patch_failed"


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
    else:
        errors = validate_policy_profile(policy_profile)
        if errors:
            alerts.append(
                _alert("invalid_policy_profile", "policy_profile is invalid", errors=errors)
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
    else:
        errors = validate_data_boundary(data_boundary)
        if errors:
            alerts.append(
                _alert("invalid_data_boundary", "data_boundary is invalid", errors=errors)
            )
        if data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "classified-not-allowed data may not be routed to code patch execution",
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


def _path_inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return bool(patterns) and any(
        fnmatch.fnmatch(path, _normalize_policy_glob(pattern)) for pattern in patterns
    )


def _path_forbidden(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, _normalize_policy_glob(pattern)) for pattern in patterns)


def _policy_write_allowlist(policy_profile: Mapping[str, Any] | None) -> list[str] | None:
    if policy_profile is None:
        return None
    filesystem = policy_profile.get("filesystem")
    if not isinstance(filesystem, Mapping):
        return None
    write_allowlist = filesystem.get("write_allowlist")
    if not isinstance(write_allowlist, list) or not all(
        isinstance(item, str) for item in write_allowlist
    ):
        return None
    return [item for item in write_allowlist]


def _normalize_policy_glob(pattern: str) -> str:
    return pattern.removeprefix("./")


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
            if not _line_span_anchor_matches(value, text, lines):
                errors.append(f"anchors[{index}] line_span text was not found")
        elif kind == "symbol":
            if not _symbol_anchor_matches(value, text):
                errors.append(f"anchors[{index}] symbol was not found")
        else:
            errors.append(f"anchors[{index}] kind is unsupported: {kind}")
    return errors


def _symbol_anchor_matches(value: str, text: str) -> bool:
    if not value.isidentifier():
        return False
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return any(token.type == tokenize.NAME and token.string == value for token in tokens)
    except tokenize.TokenError:
        return False


def _line_span_anchor_matches(value: str, text: str, lines: list[str]) -> bool:
    if value in lines or value == text.strip():
        return True
    parsed = _parse_line_span_hash(value)
    if parsed is None:
        return False
    start, end, expected_hash = parsed
    if start < 1 or end < start or end > len(lines):
        return False
    span_text = "\n".join(lines[start - 1 : end])
    return _sha256_text(span_text) == expected_hash


def _parse_line_span_hash(value: str) -> tuple[int, int, str] | None:
    prefix = "line_span:"
    if not value.startswith(prefix):
        return None
    parts = value[len(prefix) :].split(":", 2)
    if len(parts) != 3:
        return None
    start_raw, end_raw, hash_raw = parts
    try:
        start = int(start_raw)
        end = int(end_raw)
    except ValueError:
        return None
    expected_hash = _sha256_string(hash_raw)
    if expected_hash is None:
        return None
    return start, end, expected_hash


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


def _optional_string_list(
    value: object,
    *,
    field: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    if value is None:
        return [], []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return [], [
            _alert(
                f"invalid_{field}",
                f"code patch {field} must be a list of non-empty strings",
            )
        ]
    return [item for item in value], []


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_sha256_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return f"sha256:{_sha256(path)}"
    except OSError:
        return None


def _artifact_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _artifact_descriptor(label: str, path: Path | None) -> dict[str, Any]:
    return {
        "label": label,
        "path": str(path) if path is not None else None,
        "exists": bool(path is not None and path.exists()),
        "sha256": _artifact_sha256_uri(path),
        "bytes": _artifact_size(path),
    }


def _target_artifact_descriptor(label: str, path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "label": label,
        "path": str(resolved),
        "exists": resolved.exists(),
        "sha256": _artifact_sha256_uri(resolved),
        "bytes": _artifact_size(resolved),
    }


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


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
