"""Dry-run atomic commit planning receipts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

COMMIT_PLAN_SCHEMA = "tau.commit_plan.v1"
COMMIT_PLAN_RECEIPT_SCHEMA = "tau.commit_plan_receipt.v1"
SUPPORTED_EVIDENCE_RECEIPT_SCHEMAS = {
    "tau.code_patch_receipt.v1",
    "tau.lsp_diagnostics_receipt.v1",
    "tau.lsp_symbol_receipt.v1",
    "tau.lsp_rename_receipt.v1",
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
) -> dict[str, Any]:
    resolved_repo = repo.expanduser().resolve()
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        goal_hash=goal_hash,
    )
    changed = _changed_file_artifacts(resolved_repo, _git_changed_files(resolved_repo))
    groups = _commit_groups(changed)
    evidence_receipts = _evidence_receipts(
        evidence_receipt_paths or [],
        alerts,
        expected_goal_hash=goal_hash,
    )
    high_risk = [item for item in changed if _is_high_risk(item["path"])]
    warnings = _mixed_group_warnings(groups)
    if changed and not groups:
        alerts.append(_alert("empty_plan_with_dirty_tree", "dirty tree produced no commit groups"))
    for group in groups:
        if not group["rationale"]:
            alerts.append(_alert("commit_group_missing_rationale", "commit group has no rationale"))
    if high_risk:
        alerts.append(_alert("high_risk_paths_touched", "high-risk paths require approval"))
    if _has_group(groups, "source") and not _has_group(groups, "tests") and not evidence_receipts:
        alerts.append(
            _alert(
                "source_changes_lack_tests_or_evidence",
                "source changes require changed tests or explicit evidence receipts",
            )
        )
    if apply:
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
        "changed_file_count": len(changed),
        "changed_files": changed,
        "changed_file_artifacts": _reviewable_file_artifacts(changed),
        "evidence_receipts": evidence_receipts,
        "evidence_receipt_count": len(evidence_receipts),
        "proposed_commit_groups": groups,
        "group_count": len(groups),
        "dependency_order": [group["group_id"] for group in groups],
        "high_risk_paths": high_risk,
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


def _changed_file_artifacts(repo: Path, changed: list[dict[str, str]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in changed:
        artifact: dict[str, Any] = dict(item)
        path = repo / item["path"]
        if path.exists() and path.is_file():
            artifact["exists"] = True
            artifact["bytes"] = path.stat().st_size
            artifact["sha256"] = f"sha256:{_sha256(path)}"
        else:
            artifact["exists"] = False
            artifact["bytes"] = None
            artifact["sha256"] = None
        artifacts.append(artifact)
    return artifacts


def _reviewable_file_artifacts(changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("path", "status", "original_path", "exists", "bytes", "sha256")
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
    alerts: list[dict[str, str]],
    *,
    expected_goal_hash: str | None,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
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
                "sha256": f"sha256:{_sha256(resolved)}",
                "bytes": resolved.stat().st_size,
                "schema": schema,
                "schema_supported": schema_supported,
                "status": status,
                "ok": ok,
                "mocked": payload.get("mocked"),
                "live": payload.get("live"),
                "provider_live": payload.get("provider_live"),
                "goal_hash": receipt_goal_hash if isinstance(receipt_goal_hash, str) else None,
                "goal_hash_matches": (
                    None
                    if expected_goal_hash is None
                    else receipt_goal_hash == expected_goal_hash
                ),
            }
        )
    return receipts


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
    goal_hash: str | None,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
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
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    return alerts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _warning(code: str, message: str) -> dict[str, str]:
    return {"severity": "WARN", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
