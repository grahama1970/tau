"""Generic Tau wrapper receipts for agent-skills invocation artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_INVOCATION_REQUEST_SCHEMA = "tau.skill_invocation_request.v1"
SKILL_INVOCATION_RECEIPT_SCHEMA = "tau.skill_invocation_receipt.v1"
SKILL_ARTIFACT_BINDING_SCHEMA = "tau.skill_artifact_binding.v1"

ALLOWED_SKILL_INVOCATION_MODES = {"dry_run", "execute", "ingest_existing"}


def write_skill_invocation_receipt(
    *,
    request_path: Path,
    output_path: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write a Tau receipt for a bounded skill call or ingested skill artifact."""

    resolved_request = request_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = (repo_root or Path.cwd()).expanduser().resolve()
    errors: list[str] = []
    request = _read_json_object(resolved_request, errors=errors, label="skill invocation request")
    receipt = _build_receipt(
        request=request,
        request_path=resolved_request,
        repo_root=resolved_repo,
        errors=errors,
    )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _build_receipt(
    *,
    request: dict[str, Any],
    request_path: Path,
    repo_root: Path,
    errors: list[str],
) -> dict[str, Any]:
    if request:
        _validate_request_shape(request, errors=errors)
    mode = request.get("mode") if isinstance(request.get("mode"), str) else None
    command = _command_list(request.get("command"))
    artifacts = _artifact_bindings(request.get("artifacts"), repo_root=repo_root, errors=errors)
    execution: dict[str, Any] | None = None
    if not errors and mode == "execute":
        execution = _execute_command(command, repo_root=repo_root)
        if execution["exit_code"] != 0:
            errors.append(f"skill command exited non-zero: {execution['exit_code']}")
    status = "PASS" if not errors else "BLOCKED"
    return {
        "schema": SKILL_INVOCATION_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "skill": request.get("skill"),
        "capability": request.get("capability"),
        "mode": mode,
        "run_id": request.get("run_id"),
        "dag_id": request.get("dag_id"),
        "node_id": request.get("node_id"),
        "goal_hash": request.get("goal_hash"),
        "work_order_sha256": request.get("work_order_sha256"),
        "command": command,
        "request_path": str(request_path),
        "request_sha256": f"sha256:{_sha256(request_path)}" if request_path.exists() else None,
        "repo_root": str(repo_root),
        "artifacts": artifacts,
        "policy_profile_sha256": request.get("policy_profile_sha256"),
        "data_boundary_sha256": request.get("data_boundary_sha256"),
        "mocked": bool(request.get("mocked", False)),
        "live": bool(request.get("live", False)),
        "provider_live": bool(request.get("provider_live", False)),
        "execution": execution,
        "alert_count": len(errors),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau invoked or ingested a declared skill capability under a bounded request.",
                "Tau recorded request, command, artifact, work-order, and goal bindings.",
            ],
            "does_not_prove": [
                "The skill output is semantically correct.",
                "The agent is truthful.",
                "The task is complete.",
                "The native skill artifact is admissible without adapter validation.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def _validate_request_shape(request: dict[str, Any], *, errors: list[str]) -> None:
    if request.get("schema") != SKILL_INVOCATION_REQUEST_SCHEMA:
        errors.append(f"schema must be {SKILL_INVOCATION_REQUEST_SCHEMA}")
    for field in ("skill", "capability", "mode"):
        if not _non_empty_str(request.get(field)):
            errors.append(f"{field} must be a non-empty string")
    mode = request.get("mode")
    if isinstance(mode, str) and mode not in ALLOWED_SKILL_INVOCATION_MODES:
        errors.append(f"mode must be one of {sorted(ALLOWED_SKILL_INVOCATION_MODES)}")
    if request.get("zero_trust") is True and not _non_empty_str(request.get("goal_hash")):
        errors.append("goal_hash is required when zero_trust is true")
    if request.get("mode") in {"dry_run", "execute"} and not _command_list(request.get("command")):
        errors.append("command must be a non-empty string list for dry_run or execute mode")
    if request.get("mode") == "ingest_existing" and not isinstance(request.get("artifacts"), list):
        errors.append("artifacts must be a list for ingest_existing mode")
    artifacts = request.get("artifacts", [])
    if artifacts is not None and not isinstance(artifacts, list):
        errors.append("artifacts must be a list when provided")


def _artifact_bindings(
    value: Any,
    *,
    repo_root: Path,
    errors: list[str],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    bindings: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"artifacts[{index}] must be an object")
            continue
        path_value = item.get("path")
        if not _non_empty_str(path_value):
            errors.append(f"artifacts[{index}].path must be a non-empty string")
            continue
        path = Path(str(path_value)).expanduser()
        resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
        if not _is_relative_to(resolved, repo_root):
            errors.append(f"artifacts[{index}].path escapes repo root: {resolved}")
            continue
        if not resolved.exists() or not resolved.is_file():
            errors.append(f"artifacts[{index}].path is missing or not a file: {resolved}")
            continue
        bindings.append(
            {
                "schema": SKILL_ARTIFACT_BINDING_SCHEMA,
                "path": str(resolved),
                "declared_schema": item.get("schema"),
                "sha256": f"sha256:{_sha256(resolved)}",
                "bytes": resolved.stat().st_size,
            }
        )
    return bindings


def _execute_command(command: list[str], *, repo_root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _command_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    if not all(isinstance(item, str) and item for item in value):
        return []
    return [str(item) for item in value]


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
