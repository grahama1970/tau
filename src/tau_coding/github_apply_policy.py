"""Validation-only GitHub apply policy receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

GITHUB_APPLY_POLICY_SCHEMA = "tau.github_apply_policy.v1"
GITHUB_APPLY_POLICY_RECEIPT_SCHEMA = "tau.github_apply_policy_receipt.v1"
GITHUB_REDACTION_RECEIPT_SCHEMA = "tau.github_projection_redaction_receipt.v1"
APPROVAL_GATE_RECEIPT_SCHEMA = "tau.approval_gate_receipt.v1"


def write_github_apply_policy_receipt(
    *,
    projection_path: Path,
    policy_path: Path,
    receipt_path: Path,
    approval_receipt_path: Path | None = None,
    redaction_receipt_path: Path | None = None,
    preflight_ready: bool = False,
) -> dict[str, Any]:
    """Validate whether a GitHub projection is eligible for apply transport."""

    resolved_projection = projection_path.expanduser().resolve()
    resolved_policy = policy_path.expanduser().resolve()
    resolved_receipt = receipt_path.expanduser().resolve()
    errors: list[str] = []
    checks: list[dict[str, Any]] = []

    projection = _read_json_object(resolved_projection, label="GitHub projection", errors=errors)
    policy = _read_json_object(resolved_policy, label="GitHub apply policy", errors=errors)
    target = _target_from_projection(projection, errors=errors)
    actions = _actions_from_projection(projection, errors=errors)

    if policy and policy.get("schema") != GITHUB_APPLY_POLICY_SCHEMA:
        errors.append(f"policy.schema must be {GITHUB_APPLY_POLICY_SCHEMA}")
    _check_allowed_repo(policy, target, errors=errors, checks=checks)
    _check_allowed_actions(policy, actions, errors=errors, checks=checks)
    _check_required_preflight(policy, preflight_ready, errors=errors, checks=checks)

    redaction_receipt: dict[str, Any] = {}
    if _requires(policy, "requires_redaction"):
        if redaction_receipt_path is None:
            errors.append("redaction receipt is required by policy")
            checks.append({"code": "redaction_required", "ok": False})
        else:
            resolved_redaction = redaction_receipt_path.expanduser().resolve()
            redaction_receipt = _read_json_object(
                resolved_redaction,
                label="GitHub redaction receipt",
                errors=errors,
            )
            _check_redaction_receipt(
                redaction_receipt,
                projection_path=resolved_projection,
                errors=errors,
                checks=checks,
            )

    approval_receipt: dict[str, Any] = {}
    if _requires(policy, "requires_approval_packet"):
        if approval_receipt_path is None:
            errors.append("approval receipt is required by policy")
            checks.append({"code": "approval_required", "ok": False})
        else:
            resolved_approval = approval_receipt_path.expanduser().resolve()
            approval_receipt = _read_json_object(
                resolved_approval,
                label="approval receipt",
                errors=errors,
            )
            _check_approval_receipt(approval_receipt, errors=errors, checks=checks)

    ok = not errors
    receipt = {
        "schema": GITHUB_APPLY_POLICY_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "projection": str(resolved_projection),
        "projection_sha256": _safe_file_sha256(resolved_projection),
        "policy": str(resolved_policy),
        "policy_sha256": _safe_file_sha256(resolved_policy),
        "target": target,
        "actions": actions,
        "requirements": {
            "approval_packet": _requires(policy, "requires_approval_packet"),
            "preflight": _requires(policy, "requires_preflight"),
            "redaction": _requires(policy, "requires_redaction"),
        },
        "preflight_ready": preflight_ready,
        "approval_receipt": _resolved_optional_path(approval_receipt_path),
        "approval_receipt_sha256": _safe_optional_sha256(approval_receipt_path),
        "redaction_receipt": _resolved_optional_path(redaction_receipt_path),
        "redaction_receipt_sha256": _safe_optional_sha256(redaction_receipt_path),
        "checks": checks,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "A GitHub projection and apply policy were inspected deterministically.",
                "Repo/action allowlists and denial checks were evaluated before apply.",
                "Required approval, redaction, and preflight gates fail closed when missing.",
            ],
            "does_not_prove": [
                "Live GitHub mutation.",
                "GitHub auth actually succeeds.",
                "Semantic safety of the projected public comment.",
                "Human acceptance of the projection content.",
            ],
        },
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(resolved_receipt)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _read_json_object(path: Path, *, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} root must be a JSON object: {path}")
        return {}
    return payload


def _target_from_projection(projection: Mapping[str, Any], *, errors: list[str]) -> dict[str, Any]:
    target = projection.get("target")
    if not isinstance(target, Mapping):
        errors.append("projection.target must be an object")
        return {}
    repo = target.get("repo")
    target_ref = target.get("target")
    if not isinstance(repo, str) or not repo.strip():
        errors.append("projection.target.repo must be a non-empty string")
    if not isinstance(target_ref, str) or not target_ref.strip():
        errors.append("projection.target.target must be a non-empty string")
    return {
        "repo": repo.strip() if isinstance(repo, str) else repo,
        "target": target_ref.strip() if isinstance(target_ref, str) else target_ref,
    }


def _actions_from_projection(projection: Mapping[str, Any], *, errors: list[str]) -> list[str]:
    if projection.get("ok") is not True:
        errors.append("projection.ok must be true before GitHub apply policy can pass")
    actions: list[str] = []
    target = projection.get("target")
    target_ref = target.get("target") if isinstance(target, Mapping) else None
    if target_ref == "new":
        actions.append("create_issue")
    else:
        comment = projection.get("comment")
        body = comment.get("body") if isinstance(comment, Mapping) else None
        if isinstance(body, str) and body.strip():
            actions.append("comment")
        labels = projection.get("labels")
        if isinstance(labels, Mapping):
            add_labels = labels.get("add")
            remove_labels = labels.get("remove")
            if _non_empty_string_list(add_labels) or _non_empty_string_list(remove_labels):
                actions.append("label")
    if not actions:
        errors.append("projection does not contain an applyable GitHub action")
    return actions


def _check_allowed_repo(
    policy: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    errors: list[str],
    checks: list[dict[str, Any]],
) -> None:
    allowed = policy.get("allowed_repos")
    repo = target.get("repo")
    if allowed is None:
        checks.append({"code": "repo_allowlist", "ok": True, "mode": "not_configured"})
        return
    if not _is_string_list(allowed):
        errors.append("policy.allowed_repos must be a list of strings")
        checks.append({"code": "repo_allowlist", "ok": False})
        return
    ok = isinstance(repo, str) and repo in allowed
    checks.append({"code": "repo_allowlist", "ok": ok, "repo": repo})
    if not ok:
        errors.append(f"projection repo is not allowed by policy: {repo}")


def _check_allowed_actions(
    policy: Mapping[str, Any],
    actions: list[str],
    *,
    errors: list[str],
    checks: list[dict[str, Any]],
) -> None:
    allowed = policy.get("allowed_actions")
    denied = policy.get("denied_actions", [])
    if allowed is not None and not _is_string_list(allowed):
        errors.append("policy.allowed_actions must be a list of strings")
        checks.append({"code": "action_allowlist", "ok": False})
        return
    if not _is_string_list(denied):
        errors.append("policy.denied_actions must be a list of strings")
        checks.append({"code": "action_denylist", "ok": False})
        return
    denied_hits = [action for action in actions if action in denied]
    allowed_misses = [action for action in actions if allowed is not None and action not in allowed]
    checks.append({"code": "action_denylist", "ok": not denied_hits, "denied_hits": denied_hits})
    checks.append(
        {
            "code": "action_allowlist",
            "ok": not allowed_misses,
            "allowed_misses": allowed_misses,
        }
    )
    if denied_hits:
        errors.append(f"projection contains denied GitHub actions: {denied_hits}")
    if allowed_misses:
        errors.append(f"projection contains actions not allowed by policy: {allowed_misses}")


def _check_required_preflight(
    policy: Mapping[str, Any],
    preflight_ready: bool,
    *,
    errors: list[str],
    checks: list[dict[str, Any]],
) -> None:
    if not _requires(policy, "requires_preflight"):
        checks.append({"code": "preflight_required", "ok": True, "required": False})
        return
    checks.append({"code": "preflight_required", "ok": preflight_ready, "required": True})
    if not preflight_ready:
        errors.append("preflight is required by policy but --preflight-ready was not supplied")


def _check_redaction_receipt(
    receipt: Mapping[str, Any],
    *,
    projection_path: Path,
    errors: list[str],
    checks: list[dict[str, Any]],
) -> None:
    ok = (
        receipt.get("schema") == GITHUB_REDACTION_RECEIPT_SCHEMA
        and receipt.get("ok") is True
        and receipt.get("status") == "PASS"
    )
    redacted_projection = receipt.get("redacted_projection")
    expected_redacted_sha = receipt.get("redacted_projection_sha256")
    if not isinstance(redacted_projection, str) or not Path(redacted_projection).exists():
        ok = False
        errors.append("redaction receipt redacted_projection must exist")
    elif not isinstance(expected_redacted_sha, str) or not expected_redacted_sha.strip():
        ok = False
        errors.append("redaction receipt redacted_projection_sha256 is required")
    else:
        actual_redacted_sha = _safe_file_sha256(Path(redacted_projection).expanduser().resolve())
        if actual_redacted_sha != expected_redacted_sha:
            ok = False
            errors.append("redaction receipt redacted_projection_sha256 does not match artifact")
    if receipt.get("projection") != str(projection_path):
        ok = False
        errors.append("redaction receipt projection does not match checked projection")
    checks.append({"code": "redaction_receipt", "ok": ok})
    if not ok:
        errors.append("redaction receipt is not a passing Tau redaction receipt")


def _check_approval_receipt(
    receipt: Mapping[str, Any],
    *,
    errors: list[str],
    checks: list[dict[str, Any]],
) -> None:
    ok = (
        receipt.get("schema") == APPROVAL_GATE_RECEIPT_SCHEMA
        and receipt.get("ok") is True
        and receipt.get("status") == "PASS"
        and receipt.get("approved") is True
        and receipt.get("requested_action") == "github_apply"
    )
    checks.append({"code": "approval_receipt", "ok": ok})
    if not ok:
        errors.append("approval receipt is not a passing github_apply approval receipt")


def _requires(policy: Mapping[str, Any], key: str) -> bool:
    return policy.get(key) is True


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _non_empty_string_list(value: Any) -> bool:
    return _is_string_list(value) and bool(value)


def _safe_optional_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    return _safe_file_sha256(path.expanduser().resolve())


def _safe_file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _resolved_optional_path(path: Path | None) -> str | None:
    return str(path.expanduser().resolve()) if path is not None else None
