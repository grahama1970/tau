"""Dry-run atomic commit planning receipts."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
)

APPROVAL_GATE_RECEIPT_SCHEMA = "tau.approval_gate_receipt.v1"
COMMIT_PLAN_SCHEMA = "tau.commit_plan.v1"
COMMIT_PLAN_RECEIPT_SCHEMA = "tau.commit_plan_receipt.v1"
COMMIT_PLAN_APPLY_APPROVAL_ACTION = "working_tree_mutation"
SUPPORTED_EVIDENCE_RECEIPT_SCHEMAS = {
    "tau.code_patch_receipt.v1",
    "tau.lsp_diagnostics_receipt.v1",
    "tau.lsp_symbol_receipt.v1",
    "tau.lsp_rename_receipt.v1",
    "tau.test_run_receipt.v1",
    "tau.review_findings.v1",
    "tau.debug_session_receipt.v1",
    "tau.github_read_receipt.v1",
    "tau.omp_worker_receipt.v1",
    "tau.scillm_worker_receipt.v1",
    "tau.orchestration_reliability_receipt.v1",
    "tau.sandbox_run_receipt.v1",
}

HIGH_RISK_PATTERNS = (
    ".github/",
    "secrets/",
    ".env",
    "pyproject.toml",
    "uv.lock",
    "package-lock.json",
)
SENSITIVE_UNTRACKED_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "secrets/**",
)


def write_commit_plan_receipt(
    *,
    repo: Path,
    output_path: Path,
    goal_hash: str | None = None,
    apply: bool = False,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
    evidence_receipt_paths: list[Path] | None = None,
    approval_receipt_path: Path | None = None,
) -> dict[str, Any]:
    resolved_repo = repo.expanduser().resolve()
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        goal_hash=goal_hash,
    )
    excluded_paths = _excluded_commit_plan_paths(
        repo=resolved_repo,
        output_path=output_path,
        evidence_receipt_paths=evidence_receipt_paths or [],
        approval_receipt_path=approval_receipt_path,
    )
    changed = _changed_file_artifacts(
        resolved_repo,
        [
            item
            for item in _git_changed_files(resolved_repo)
            if item["path"] not in excluded_paths
        ],
        policy_profile=policy_profile,
        alerts=alerts,
    )
    groups = _commit_groups(changed)
    evidence_receipts = _evidence_receipts(
        evidence_receipt_paths or [],
        alerts,
        repo=resolved_repo,
        expected_goal_hash=goal_hash,
    )
    approval_receipt = _approval_receipt(
        approval_receipt_path,
        alerts,
        repo=resolved_repo,
        required=apply,
    )
    high_risk = [item for item in changed if _is_high_risk(item["path"])]
    sensitive_untracked = [
        item for item in changed if _is_untracked_sensitive_file(item)
    ]
    warnings = _mixed_group_warnings(groups)
    if changed and not groups:
        alerts.append(_alert("empty_plan_with_dirty_tree", "dirty tree produced no commit groups"))
    for group in groups:
        if not group["rationale"]:
            alerts.append(_alert("commit_group_missing_rationale", "commit group has no rationale"))
    if high_risk and approval_receipt is None:
        alerts.append(_alert("high_risk_paths_touched", "high-risk paths require approval"))
    if sensitive_untracked:
        alerts.append(
            _alert(
                "untracked_sensitive_files",
                "untracked sensitive files must be removed, ignored, or explicitly handled",
            )
        )
    if _has_group(groups, "source") and not _has_group(groups, "tests"):
        if not evidence_receipts:
            alerts.append(
                _alert(
                    "source_changes_lack_tests_or_evidence",
                    "source changes require changed tests or explicit evidence receipts",
                )
            )
        else:
            uncovered_source_paths = _uncovered_source_paths(groups, evidence_receipts)
            if uncovered_source_paths:
                alerts.append(
                    _alert(
                        "source_changes_lack_relevant_evidence",
                        "source changes require evidence receipts that cover every changed "
                        "source path",
                        errors=uncovered_source_paths,
                    )
                )
    if apply:
        if approval_receipt is None:
            alerts.append(
                _alert("approval_required_to_apply", "commit-plan apply requires approval receipt")
            )

    payload = {
        "schema": COMMIT_PLAN_RECEIPT_SCHEMA,
        "ok": not alerts,
        "status": "PASS" if not alerts else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(resolved_repo),
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "dry_run": not apply,
        "apply_requested": apply,
        "apply_eligible": apply and approval_receipt is not None and not alerts,
        "approval_receipt": approval_receipt,
        "changed_file_count": len(changed),
        "changed_files": changed,
        "changed_file_artifacts": _reviewable_file_artifacts(changed),
        "evidence_receipts": evidence_receipts,
        "evidence_receipt_count": len(evidence_receipts),
        "proposed_commit_groups": groups,
        "group_count": len(groups),
        "dependency_order": [group["group_id"] for group in groups],
        "high_risk_paths": high_risk,
        "sensitive_untracked_files": sensitive_untracked,
        "approval_required": bool(high_risk or apply),
        "lockfile_handling": "group_with_owner_changes_or_review_separately",
        "warnings": warnings,
        "warning_codes": [warning["code"] for warning in warnings],
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau inspected the Git working tree and proposed dry-run commit groups.",
                "Tau flagged high-risk paths and apply requests for approval.",
                "When apply is requested, Tau requires a valid working-tree mutation "
                "approval receipt before marking the plan apply-eligible.",
            ],
            "does_not_prove": [
                "The proposed grouping is semantically optimal.",
                "The code is correct.",
                "Commits were created.",
                "Tests passed.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _excluded_commit_plan_paths(
    *,
    repo: Path,
    output_path: Path,
    evidence_receipt_paths: list[Path],
    approval_receipt_path: Path | None,
) -> set[str]:
    candidates = [output_path, *evidence_receipt_paths]
    if approval_receipt_path is not None:
        candidates.append(approval_receipt_path)
    excluded: set[str] = set()
    for candidate in candidates:
        try:
            relative = candidate.expanduser().resolve().relative_to(repo)
        except ValueError:
            continue
        excluded.add(relative.as_posix())
    return excluded


def _git_changed_files(repo: Path) -> list[dict[str, str]]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    changed: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            old, _, new = path.partition(" -> ")
            path = new
            original_path = old
        else:
            original_path = ""
        changed.append(
            {
                "path": path,
                "status": status.strip() or "modified",
                "original_path": original_path,
            }
        )
    return changed


def _changed_file_artifacts(
    repo: Path,
    changed: list[dict[str, str]],
    *,
    policy_profile: dict[str, Any] | None,
    alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    read_denylist = _policy_read_denylist(policy_profile)
    write_allowlist = _policy_write_allowlist(policy_profile)
    for item in changed:
        artifact: dict[str, Any] = dict(item)
        artifact["policy_write_allowed"] = _path_allowed_by_policy(
            item["path"],
            write_allowlist,
        )
        if artifact["policy_write_allowed"] is False:
            alerts.append(
                _alert(
                    "policy_write_disallowed",
                    f"policy_profile.filesystem.write_allowlist denied {item['path']}",
                )
            )
        if _path_denied_by_policy(item["path"], read_denylist):
            artifact["exists"] = None
            artifact["bytes"] = None
            artifact["sha256"] = None
            artifact["policy_read_denied"] = True
            alerts.append(
                _alert(
                    "policy_read_denied",
                    f"policy_profile.filesystem.read_denylist denied {item['path']}",
                )
            )
            artifacts.append(artifact)
            continue
        path = repo / item["path"]
        if path.exists() and path.is_file():
            artifact["exists"] = True
            artifact["bytes"] = path.stat().st_size
            artifact["sha256"] = f"sha256:{_sha256(path)}"
        else:
            artifact["exists"] = False
            artifact["bytes"] = None
            artifact["sha256"] = None
        artifact["policy_read_denied"] = False
        artifacts.append(artifact)
    return artifacts


def _reviewable_file_artifacts(changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "path",
        "status",
        "original_path",
        "exists",
        "bytes",
        "sha256",
        "policy_read_denied",
        "policy_write_allowed",
    )
    return [{field: item.get(field) for field in fields} for item in changed]


def _commit_groups(changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in changed:
        path = item["path"]
        if path.startswith("tests/"):
            key = "tests"
        elif path.startswith("docs/") or path.endswith(".md"):
            key = "docs"
        elif path.endswith((".lock", "uv.lock", "package-lock.json")):
            key = "lockfiles"
        else:
            key = "source"
        buckets[key].append(item)
    order = ["source", "tests", "docs", "lockfiles"]
    groups: list[dict[str, Any]] = []
    for key in order:
        files = buckets.get(key, [])
        if not files:
            continue
        groups.append(
            {
                "group_id": key,
                "rationale": _rationale(key),
                "files": files,
                "risk_level": (
                    "high" if any(_is_high_risk(item["path"]) for item in files) else "normal"
                ),
                "required_evidence": _required_evidence(key),
            }
        )
    return groups


def _evidence_receipts(
    paths: list[Path],
    alerts: list[dict[str, Any]],
    *,
    repo: Path,
    expected_goal_hash: str | None,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            resolved.relative_to(repo)
        except ValueError:
            alerts.append(
                _alert(
                    "evidence_receipt_outside_repo",
                    f"evidence receipt must be inside the repo: {resolved}",
                )
            )
            continue
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            alerts.append(
                _alert("evidence_receipt_unreadable", f"evidence receipt unreadable: {exc}")
            )
            continue
        if not isinstance(payload, dict):
            alerts.append(
                _alert("evidence_receipt_not_object", "evidence receipt must be JSON object")
            )
            continue
        schema = payload.get("schema")
        status = payload.get("status")
        ok = payload.get("ok")
        mocked = payload.get("mocked")
        live = payload.get("live")
        receipt_goal_hash = payload.get("goal_hash")
        schema_supported = schema in SUPPORTED_EVIDENCE_RECEIPT_SCHEMAS
        if not schema_supported:
            alerts.append(
                _alert(
                    "unsupported_evidence_receipt_schema",
                    f"unsupported evidence receipt schema for commit-plan: {schema!r}",
                )
            )
        if ok is not True or status != "PASS":
            alerts.append(
                _alert(
                    "evidence_receipt_not_pass",
                    f"evidence receipt must be PASS with ok:true: {resolved}",
                )
            )
        if mocked is not False:
            alerts.append(
                _alert(
                    "evidence_receipt_mocked",
                    f"evidence receipt cannot justify a commit plan when mocked: {resolved}",
                )
            )
        if live is not True:
            alerts.append(
                _alert(
                    "evidence_receipt_not_live",
                    f"evidence receipt must record live:true for commit planning: {resolved}",
                )
            )
        if expected_goal_hash is not None:
            if not isinstance(receipt_goal_hash, str) or not receipt_goal_hash:
                alerts.append(
                    _alert(
                        "evidence_receipt_missing_goal_hash",
                        f"evidence receipt must include goal_hash: {resolved}",
                    )
                )
            elif receipt_goal_hash != expected_goal_hash:
                alerts.append(
                    _alert(
                        "evidence_receipt_goal_hash_mismatch",
                        f"evidence receipt goal_hash does not match commit plan: {resolved}",
                    )
                )
        receipts.append(
            {
                "path": str(resolved),
                "exists": True,
                "sha256": f"sha256:{_sha256(resolved)}",
                "bytes": resolved.stat().st_size,
                "schema": schema,
                "schema_supported": schema_supported,
                "status": status,
                "ok": ok,
                "mocked": mocked,
                "live": live,
                "provider_live": payload.get("provider_live"),
                "goal_hash": receipt_goal_hash if isinstance(receipt_goal_hash, str) else None,
                "goal_hash_matches": (
                    None
                    if expected_goal_hash is None
                    else receipt_goal_hash == expected_goal_hash
                ),
                "covered_paths": _covered_paths_from_receipt(payload, repo),
            }
        )
    return receipts


def _approval_receipt(
    path: Path | None,
    alerts: list[dict[str, Any]],
    *,
    repo: Path,
    required: bool,
) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        alerts.append(
            _alert(
                "approval_receipt_outside_repo",
                f"approval receipt must be inside the planned repo: {resolved}",
            )
        )
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(
            _alert("approval_receipt_unreadable", f"approval receipt unreadable: {exc}")
        )
        return None
    if not isinstance(payload, dict):
        alerts.append(_alert("approval_receipt_not_object", "approval receipt must be an object"))
        return None

    packet_summary = payload.get("packet_summary")
    item = {
        "path": str(resolved),
        "exists": True,
        "sha256": f"sha256:{_sha256(resolved)}",
        "bytes": resolved.stat().st_size,
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "approved": payload.get("approved"),
        "requested_action": payload.get("requested_action"),
        "target_id": (
            packet_summary.get("target_id") if isinstance(packet_summary, dict) else None
        ),
    }
    valid = True
    if payload.get("schema") != APPROVAL_GATE_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "invalid_approval_receipt_schema",
                f"approval receipt schema must be {APPROVAL_GATE_RECEIPT_SCHEMA}",
            )
        )
        valid = False
    if payload.get("ok") is not True or payload.get("status") != "PASS":
        alerts.append(
            _alert("approval_receipt_not_pass", "approval receipt must be PASS with ok:true")
        )
        valid = False
    if payload.get("approved") is not True:
        alerts.append(
            _alert("approval_receipt_not_approved", "approval receipt approved must be true")
        )
        valid = False
    if payload.get("mocked") is not False:
        alerts.append(
            _alert(
                "approval_receipt_mocked",
                "approval receipt cannot be mocked for commit-plan apply eligibility",
            )
        )
        valid = False
    if payload.get("requested_action") != COMMIT_PLAN_APPLY_APPROVAL_ACTION:
        alerts.append(
            _alert(
                "approval_receipt_wrong_action",
                "commit-plan apply requires working_tree_mutation approval",
            )
        )
        valid = False
    expected_target_id = f"repo:{repo}"
    if item["target_id"] != expected_target_id:
        alerts.append(
            _alert(
                "approval_receipt_target_mismatch",
                "commit-plan approval receipt target_id must match the planned repo",
            )
        )
        valid = False
    if required and not valid:
        return None
    return item if valid else None


def _uncovered_source_paths(
    groups: list[dict[str, Any]],
    evidence_receipts: list[dict[str, Any]],
) -> list[str]:
    source_paths = {
        str(item.get("path"))
        for group in groups
        if group.get("group_id") == "source"
        for item in group.get("files", [])
        if item.get("path")
    }
    covered_paths = {
        path
        for receipt in evidence_receipts
        for path in receipt.get("covered_paths", [])
        if isinstance(path, str)
    }
    return sorted(source_paths - covered_paths)


def _covered_paths_from_receipt(payload: dict[str, Any], repo: Path) -> list[str]:
    paths: set[str] = set()
    _append_covered_path(paths, payload.get("target_file"), repo)
    for field in ("changed_files", "result_artifacts", "required_artifacts", "tested_paths"):
        value = payload.get(field)
        if isinstance(value, list):
            for item in value:
                _append_covered_path(paths, item, repo)
    for field in (
        "inspected_artifacts",
        "changed_file_artifacts",
        "required_artifact_descriptors",
        "test_log_artifacts",
        "validated_artifacts",
    ):
        value = payload.get(field)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                _append_covered_path(paths, item.get("artifact"), repo)
                _append_covered_path(paths, item.get("path"), repo)
    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                _append_covered_path(paths, item.get("file"), repo)
    return sorted(paths)


def _append_covered_path(paths: set[str], raw_path: object, repo: Path) -> None:
    if not isinstance(raw_path, str) or not raw_path:
        return
    normalized = _relative_path_for_receipt(raw_path, repo)
    if normalized:
        paths.add(normalized)


def _relative_path_for_receipt(raw_path: str, repo: Path) -> str | None:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repo).as_posix()
        except ValueError:
            return None
    if raw_path.startswith("../"):
        return None
    return candidate.as_posix()


def _has_group(groups: list[dict[str, Any]], group_id: str) -> bool:
    return any(group.get("group_id") == group_id for group in groups)


def _mixed_group_warnings(groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    group_ids = {str(group.get("group_id")) for group in groups}
    warnings: list[dict[str, str]] = []
    if "docs" in group_ids and group_ids & {"source", "tests", "lockfiles"}:
        warnings.append(
            _warning(
                "mixed_docs_with_runtime_changes",
                "documentation changes are mixed with runtime/test changes; review whether "
                "they should be separate commits",
            )
        )
    if "lockfiles" in group_ids and group_ids - {"lockfiles"}:
        warnings.append(
            _warning(
                "mixed_lockfiles_with_other_changes",
                "lockfile changes are mixed with other changes; review dependency rationale",
            )
        )
    return warnings


def _rationale(group_id: str) -> str:
    return {
        "source": "Source and runtime behavior changes should be reviewed together.",
        "tests": "Tests prove or constrain the source changes.",
        "docs": "Documentation updates are reviewable separately from runtime behavior.",
        "lockfiles": "Lockfiles should be tied to dependency changes or reviewed separately.",
    }[group_id]


def _required_evidence(group_id: str) -> list[str]:
    if group_id == "source":
        return ["focused_tests_or_receipts"]
    if group_id == "tests":
        return ["test_command_output"]
    if group_id == "docs":
        return ["diff_review"]
    return ["dependency_change_rationale"]


def _is_high_risk(path: str) -> bool:
    return any(path == pattern or path.startswith(pattern) for pattern in HIGH_RISK_PATTERNS)


def _is_untracked_sensitive_file(item: dict[str, Any]) -> bool:
    if item.get("status") != "??":
        return False
    path = str(item.get("path") or "")
    return any(
        fnmatch.fnmatch(path, pattern.removeprefix("./"))
        for pattern in SENSITIVE_UNTRACKED_PATTERNS
    )


def _policy_read_denylist(policy_profile: dict[str, Any] | None) -> list[str] | None:
    if policy_profile is None:
        return None
    filesystem = policy_profile.get("filesystem")
    if not isinstance(filesystem, dict):
        return None
    read_denylist = filesystem.get("read_denylist")
    if not _is_string_list(read_denylist):
        return None
    return [item for item in read_denylist]


def _policy_write_allowlist(policy_profile: dict[str, Any] | None) -> list[str] | None:
    if policy_profile is None:
        return None
    filesystem = policy_profile.get("filesystem")
    if not isinstance(filesystem, dict):
        return None
    write_allowlist = filesystem.get("write_allowlist")
    if not _is_string_list(write_allowlist):
        return None
    return [item for item in write_allowlist]


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _path_denied_by_policy(path: str, read_denylist: list[str] | None) -> bool:
    if read_denylist is None:
        return False
    return any(
        fnmatch.fnmatch(path, pattern.removeprefix("./")) for pattern in read_denylist
    )


def _path_allowed_by_policy(path: str, write_allowlist: list[str] | None) -> bool | None:
    if write_allowlist is None:
        return None
    return any(
        fnmatch.fnmatch(path, pattern.removeprefix("./")) for pattern in write_allowlist
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
    goal_hash: str | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and not goal_hash:
        alerts.append(_alert("missing_goal_hash", "zero-trust commit plan requires goal_hash"))
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust commit plan requires policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust commit plan requires data_boundary")
        )
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    elif policy_profile is not None:
        errors = validate_policy_profile(policy_profile)
        if errors:
            alerts.append(
                _alert("invalid_policy_profile", "policy_profile is invalid", errors=errors)
            )
        filesystem = policy_profile.get("filesystem")
        if isinstance(filesystem, dict):
            read_denylist = filesystem.get("read_denylist")
            write_allowlist = filesystem.get("write_allowlist")
            if read_denylist is not None and not _is_string_list(read_denylist):
                alerts.append(
                    _alert(
                        "invalid_policy_read_denylist",
                        "policy_profile filesystem.read_denylist must be a list of strings",
                    )
                )
            if write_allowlist is not None and not _is_string_list(write_allowlist):
                alerts.append(
                    _alert(
                        "invalid_policy_write_allowlist",
                        "policy_profile filesystem.write_allowlist must be a list of strings",
                    )
                )
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    elif data_boundary is not None:
        errors = validate_data_boundary(data_boundary)
        if errors:
            alerts.append(
                _alert("invalid_data_boundary", "data_boundary is invalid", errors=errors)
            )
        if data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "classified-not-allowed data may not be routed to commit planning",
                )
            )
    return alerts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _warning(code: str, message: str) -> dict[str, str]:
    return {"severity": "WARN", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
