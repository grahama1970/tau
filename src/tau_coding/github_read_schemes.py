"""Read-only GitHub URI scheme receipts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

GITHUB_READ_RECEIPT_SCHEMA = "tau.github_read_receipt.v1"

_ISSUE_RE = re.compile(r"^issue://([^/]+)/([^/]+)/([0-9]+)$")
_PR_RE = re.compile(r"^pr://([^/]+)/([^/]+)/([0-9]+)$")
_DIFF_RE = re.compile(r"^diff://([^/]+)/([^/]+)/pull/([0-9]+)$")
_COMMIT_RE = re.compile(r"^commit://([^/]+)/([^/]+)/([A-Za-z0-9._-]+)$")


def write_github_read_receipt(
    *,
    uri: str,
    output_path: Path,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    parsed = _parse_github_uri(uri)
    if parsed is None:
        alerts.append(_alert("unsupported_github_read_uri", "unsupported GitHub read URI"))
        parsed = {}
    ok = not alerts
    payload = {
        "schema": GITHUB_READ_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "uri": uri,
        "parsed": parsed,
        "read_only": True,
        "dry_run_projection": True,
        "mutation_allowed": False,
        "blocked_mutations": ["comment", "label", "close", "merge", "push", "release"],
        "suggested_gh_command": _suggested_gh_command(parsed),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau parsed a supported GitHub read URI into a read-only projection.",
                "Tau did not authorize GitHub mutation for this read receipt.",
            ],
            "does_not_prove": [
                "Live GitHub auth succeeds.",
                "The GitHub object exists.",
                "The read projection content is current.",
                "Any GitHub mutation is allowed.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _parse_github_uri(uri: str) -> dict[str, Any] | None:
    for kind, pattern in (
        ("issue", _ISSUE_RE),
        ("pr", _PR_RE),
        ("diff", _DIFF_RE),
        ("commit", _COMMIT_RE),
    ):
        match = pattern.match(uri)
        if not match:
            continue
        owner, repo, identifier = match.groups()
        return {
            "kind": kind,
            "owner": owner,
            "repo": f"{owner}/{repo}",
            "name": repo,
            "identifier": identifier,
        }
    return None


def _suggested_gh_command(parsed: dict[str, Any]) -> list[str]:
    kind = parsed.get("kind")
    repo = parsed.get("repo")
    identifier = parsed.get("identifier")
    if not isinstance(repo, str) or not isinstance(identifier, str):
        return []
    if kind == "issue":
        return [
            "gh",
            "issue",
            "view",
            identifier,
            "--repo",
            repo,
            "--json",
            "number,title,state,body",
        ]
    if kind == "pr":
        return [
            "gh",
            "pr",
            "view",
            identifier,
            "--repo",
            repo,
            "--json",
            "number,title,state,body",
        ]
    if kind == "diff":
        return ["gh", "pr", "diff", identifier, "--repo", repo]
    if kind == "commit":
        return ["gh", "api", f"repos/{repo}/commits/{identifier}"]
    return []


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust GitHub read requires policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust GitHub read requires data_boundary")
        )
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    return alerts


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
