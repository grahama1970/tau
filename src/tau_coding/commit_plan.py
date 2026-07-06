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
    )
    changed = _git_changed_files(resolved_repo)
    groups = _commit_groups(changed)
    evidence_receipts = _evidence_receipts(evidence_receipt_paths or [], alerts)
    high_risk = [item for item in changed if _is_high_risk(item["path"])]
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
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "dry_run": not apply,
        "apply_requested": apply,
        "changed_file_count": len(changed),
        "changed_files": changed,
        "evidence_receipts": evidence_receipts,
        "evidence_receipt_count": len(evidence_receipts),
        "proposed_commit_groups": groups,
        "group_count": len(groups),
        "dependency_order": [group["group_id"] for group in groups],
        "high_risk_paths": high_risk,
        "approval_required": bool(high_risk or apply),
        "lockfile_handling": "group_with_owner_changes_or_review_separately",
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


def _commit_groups(changed: list[dict[str, str]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
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
        receipts.append(
            {
                "path": str(resolved),
                "sha256": f"sha256:{_sha256(resolved)}",
                "schema": schema,
                "status": status,
                "ok": payload.get("ok"),
            }
        )
    return receipts


def _has_group(groups: list[dict[str, Any]], group_id: str) -> bool:
    return any(group.get("group_id") == group_id for group in groups)


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
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
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


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
