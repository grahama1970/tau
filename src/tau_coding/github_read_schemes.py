"""Read-only GitHub URI scheme receipts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

GITHUB_READ_RECEIPT_SCHEMA = "tau.github_read_receipt.v1"

_ISSUE_RE = re.compile(r"^issue://([^/]+)/([^/]+)/([0-9]+)$")
_PR_RE = re.compile(r"^pr://([^/]+)/([^/]+)/([0-9]+)$")
_DIFF_RE = re.compile(r"^diff://([^/]+)/([^/]+)/pull/([0-9]+)$")
_COMMIT_PREFIX_RE = re.compile(r"^commit://")
_COMMIT_RE = re.compile(r"^commit://([^/]+)/([^/]+)/([A-Fa-f0-9]{7,40})$")


def write_github_read_receipt(
    *,
    uri: str,
    output_path: Path,
    goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
    execute: bool = False,
    gh_bin: str = "gh",
    timeout_s: int = 30,
) -> dict[str, Any]:
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        goal_hash=goal_hash,
    )
    parsed = _parse_github_uri(uri)
    if parsed is None:
        if _COMMIT_PREFIX_RE.match(uri):
            alerts.append(
                _alert(
                    "invalid_commit_identifier",
                    "commit:// GitHub read URI requires a short or full hex SHA",
                )
            )
        else:
            alerts.append(_alert("unsupported_github_read_uri", "unsupported GitHub read URI"))
        parsed = {}
    ok = not alerts
    suggested_command = _suggested_gh_command(parsed)
    execution = _execute_read_command(
        output_path=output_path,
        command=suggested_command,
        gh_bin=gh_bin,
        timeout_s=timeout_s,
    ) if execute and ok else _empty_execution(execute_requested=execute)
    alerts.extend(execution.pop("alerts"))
    ok = not alerts
    payload = {
        "schema": GITHUB_READ_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": bool(execution["command_executed"]),
        "provider_live": False,
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "uri": uri,
        "parsed": parsed,
        "read_only": True,
        "dry_run_projection": True,
        "mutation_allowed": False,
        "blocked_mutations": ["comment", "label", "close", "merge", "push", "release"],
        "suggested_gh_command": suggested_command,
        "execution": execution,
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
                (
                    "The read projection content is current unless execute mode "
                    "captured fresh gh output."
                ),
                "Any GitHub mutation is allowed.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _execute_read_command(
    *,
    output_path: Path,
    command: list[str],
    gh_bin: str,
    timeout_s: int,
) -> dict[str, Any]:
    if not command:
        return _empty_execution(
            execute_requested=True,
            alerts=[
                _alert(
                    "github_read_command_missing",
                    "GitHub read execution requires a supported read command",
                )
            ],
        )
    resolved_gh = shutil.which(gh_bin) if "/" not in gh_bin else gh_bin
    if not resolved_gh:
        return _empty_execution(
            execute_requested=True,
            alerts=[_alert("github_read_gh_missing", "gh executable was not found")],
        )
    executed = [resolved_gh, *command[1:]]
    stdout_path = _sidecar_path(output_path, "stdout.txt")
    stderr_path = _sidecar_path(output_path, "stderr.txt")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            executed,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_s),
        )
        stdout_path.write_text(proc.stdout, encoding="utf-8")
        stderr_path.write_text(proc.stderr, encoding="utf-8")
        alerts = []
        if proc.returncode != 0:
            alerts.append(
                _alert(
                    "github_read_nonzero_exit",
                    f"GitHub read command exited {proc.returncode}",
                )
            )
        return {
            "execute_requested": True,
            "command_executed": True,
            "command": executed,
            "exit_code": proc.returncode,
            "timed_out": False,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_sha256": _sha256_uri(stdout_path),
            "stdout_bytes": len(proc.stdout.encode("utf-8")),
            "stderr_sha256": _sha256_uri(stderr_path),
            "stderr_bytes": len(proc.stderr.encode("utf-8")),
            "artifacts": _artifact_descriptors(
                ("stdout", stdout_path),
                ("stderr", stderr_path),
            ),
            "alerts": alerts,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return {
            "execute_requested": True,
            "command_executed": True,
            "command": executed,
            "exit_code": None,
            "timed_out": True,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_sha256": _sha256_uri(stdout_path),
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_sha256": _sha256_uri(stderr_path),
            "stderr_bytes": len(stderr.encode("utf-8")),
            "artifacts": _artifact_descriptors(
                ("stdout", stdout_path),
                ("stderr", stderr_path),
            ),
            "alerts": [_alert("github_read_timeout", "GitHub read command timed out")],
        }


def _empty_execution(
    *,
    execute_requested: bool,
    alerts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "execute_requested": execute_requested,
        "command_executed": False,
        "command": [],
        "exit_code": None,
        "timed_out": False,
        "stdout_path": None,
        "stderr_path": None,
        "stdout_sha256": None,
        "stdout_bytes": 0,
        "stderr_sha256": None,
        "stderr_bytes": 0,
        "artifacts": [],
        "alerts": alerts or [],
    }


def _sidecar_path(output_path: Path, suffix: str) -> Path:
    resolved = output_path.expanduser().resolve()
    return resolved.with_name(f"{resolved.name}.{suffix}")


def _artifact_descriptors(*items: tuple[str, Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for label, path in items:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            continue
        artifacts.append(
            {
                "label": label,
                "path": str(resolved),
                "sha256": _sha256_uri(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
    return artifacts


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


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
    goal_hash: str | None,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if zero_trust and not goal_hash:
        alerts.append(_alert("missing_goal_hash", "zero-trust GitHub read requires goal_hash"))
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
