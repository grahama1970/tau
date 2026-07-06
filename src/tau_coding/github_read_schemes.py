"""Read-only GitHub URI scheme receipts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GITHUB_READ_RECEIPT_SCHEMA = "tau.github_read_receipt.v1"

_ISSUE_RE = re.compile(r"^issue://([^/]+)/([^/]+)/([0-9]+)$")
_PR_RE = re.compile(r"^pr://([^/]+)/([^/]+)/([0-9]+)$")
_DIFF_RE = re.compile(r"^diff://([^/]+)/([^/]+)/pull/([0-9]+)$")
_COMMIT_RE = re.compile(r"^commit://([^/]+)/([^/]+)/([A-Za-z0-9._-]+)$")


def write_github_read_receipt(*, uri: str, output_path: Path) -> dict[str, Any]:
    alerts: list[dict[str, str]] = []
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


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
