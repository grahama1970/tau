"""Focused local test-run evidence receipts for coding work."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
)

TEST_RUN_RECEIPT_SCHEMA = "tau.test_run_receipt.v1"


def write_test_run_receipt(
    *,
    repo: Path,
    output_path: Path,
    command: Sequence[str] | None = None,
    tested_paths: Sequence[str] | None = None,
    goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
    timeout_s: int = 120,
) -> dict[str, Any]:
    resolved_repo = repo.expanduser().resolve()
    resolved_out = output_path.expanduser().resolve()
    stdout_path = resolved_out.with_suffix(resolved_out.suffix + ".stdout.txt")
    stderr_path = resolved_out.with_suffix(resolved_out.suffix + ".stderr.txt")
    selected_command = list(command or [sys.executable, "-m", "pytest", "-q"])
    normalized_tested_paths, tested_path_errors = _normalize_tested_paths(tested_paths or [])
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        goal_hash=goal_hash,
    )
    if tested_path_errors:
        alerts.append(
            _alert(
                "invalid_tested_path",
                "tested_paths must be non-empty relative paths inside the repo",
                errors=tested_path_errors,
            )
        )
    if zero_trust and not _path_inside_root(resolved_out, resolved_repo):
        alerts.append(
            _alert(
                "test_run_receipt_outside_repo",
                "zero-trust test-run receipt must stay inside the repo",
            )
        )
    if timeout_s <= 0:
        alerts.append(_alert("invalid_timeout", "timeout_s must be greater than zero"))
    if not resolved_repo.is_dir():
        alerts.append(_alert("repo_missing", "test-run repo must be an existing directory"))
    if not _allowed_pytest_command(selected_command):
        alerts.append(
            _alert(
                "disallowed_test_command",
                "test-run only allows python -m pytest, pytest, or uv run pytest command forms",
            )
        )
    command_path_errors = _pytest_command_path_errors(selected_command, resolved_repo)
    if command_path_errors:
        alerts.append(
            _alert(
                "test_command_path_escape",
                "pytest path arguments must stay inside the repo",
                errors=command_path_errors,
            )
        )

    command_result: dict[str, Any] | None = None
    if not alerts:
        start = time.monotonic()
        try:
            completed = subprocess.run(
                selected_command,
                cwd=resolved_repo,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            duration_s = round(time.monotonic() - start, 3)
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(completed.stdout, encoding="utf-8")
            stderr_path.write_text(completed.stderr, encoding="utf-8")
            command_result = {
                "command": selected_command,
                "returncode": completed.returncode,
                "duration_s": duration_s,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "stdout_sha256": _optional_sha256(stdout_path),
                "stderr_sha256": _optional_sha256(stderr_path),
            }
            if completed.returncode != 0:
                alerts.append(
                    _alert(
                        "test_command_failed",
                        f"test command exited with {completed.returncode}",
                    )
                )
        except subprocess.TimeoutExpired as exc:
            duration_s = round(time.monotonic() - start, 3)
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
            stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
            command_result = {
                "command": selected_command,
                "returncode": None,
                "duration_s": duration_s,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "stdout_sha256": _optional_sha256(stdout_path),
                "stderr_sha256": _optional_sha256(stderr_path),
                "timed_out": True,
            }
            alerts.append(_alert("test_command_timeout", "test command timed out"))

    payload = {
        "schema": TEST_RUN_RECEIPT_SCHEMA,
        "ok": not alerts,
        "status": "PASS" if not alerts else "BLOCKED",
        "mocked": False,
        "live": command_result is not None,
        "provider_live": False,
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "repo": str(resolved_repo),
        "command": selected_command,
        "tested_paths": normalized_tested_paths,
        "timeout_s": timeout_s,
        "command_result": command_result,
        "tests_passed": bool(command_result and command_result.get("returncode") == 0),
        "stdout_artifact": _artifact_summary(stdout_path),
        "stderr_artifact": _artifact_summary(stderr_path),
        "test_log_artifacts": [_artifact_summary(stdout_path), _artifact_summary(stderr_path)],
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau executed a focused local pytest-shaped test command.",
                "Tau recorded the command result and stdout/stderr artifacts.",
            ],
            "does_not_prove": [
                "The full test suite passes unless the command was the full suite.",
                "Semantic code correctness.",
                "Agent truthfulness.",
                "Provider/model quality.",
            ],
        },
        "timestamp": _utc_stamp(),
        "receipt_path": str(resolved_out),
    }
    _write_json(resolved_out, payload)
    return payload


def _allowed_pytest_command(command: Sequence[str]) -> bool:
    if not command or not all(isinstance(item, str) and item for item in command):
        return False
    executable = Path(command[0]).name
    if executable in {"python", "python3"} or executable.startswith("python3."):
        return len(command) >= 3 and command[1:3] == ["-m", "pytest"]
    if executable == Path(sys.executable).name:
        return len(command) >= 3 and command[1:3] == ["-m", "pytest"]
    if executable == "pytest":
        return True
    if executable == "uv":
        return len(command) >= 3 and command[1:3] == ["run", "pytest"]
    return False


def _pytest_command_path_errors(command: Sequence[str], repo: Path) -> list[str]:
    args = _pytest_passthrough_args(command)
    errors: list[str] = []
    skip_next_path_value = False
    for arg in args:
        if skip_next_path_value:
            skip_next_path_value = False
            errors.extend(_path_arg_errors(arg, repo))
            continue
        if arg == "--rootdir":
            skip_next_path_value = True
            continue
        if arg.startswith("--rootdir="):
            _, _, value = arg.partition("=")
            errors.extend(_path_arg_errors(value, repo))
            continue
        if arg.startswith("-"):
            continue
        errors.extend(_path_arg_errors(arg, repo))
    return errors


def _pytest_passthrough_args(command: Sequence[str]) -> list[str]:
    if not _allowed_pytest_command(command):
        return []
    executable = Path(command[0]).name
    if executable in {"python", "python3"} or executable.startswith("python3."):
        return list(command[3:])
    if executable == Path(sys.executable).name and command[1:3] == ["-m", "pytest"]:
        return list(command[3:])
    if executable == "pytest":
        return list(command[1:])
    if executable == "uv":
        return list(command[3:])
    return []


def _path_arg_errors(raw_path: str, repo: Path) -> list[str]:
    if not raw_path or raw_path.startswith("::"):
        return []
    path_part = raw_path.split("::", 1)[0]
    if not path_part:
        return []
    candidate = Path(path_part).expanduser()
    if candidate.is_absolute():
        try:
            candidate.resolve().relative_to(repo)
        except ValueError:
            return [f"pytest path escapes repo: {raw_path}"]
    normalized = path_part.replace("\\", "/")
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return [f"pytest path escapes repo: {raw_path}"]
    return []


def _normalize_tested_paths(values: Sequence[str]) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    errors: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            errors.append("tested path must be a non-empty string")
            continue
        path = value.replace("\\", "/").removeprefix("./")
        if path.startswith("../") or path == ".." or Path(path).is_absolute():
            errors.append(f"tested path escapes repo: {value}")
            continue
        normalized.append(path)
    return sorted(set(normalized)), errors


def _path_inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
    goal_hash: str | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and not goal_hash:
        alerts.append(_alert("missing_goal_hash", "zero-trust test-run requires goal_hash"))
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust test-run requires policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(_alert("missing_data_boundary", "zero-trust test-run requires data_boundary"))
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    elif isinstance(policy_profile, Mapping):
        errors = validate_policy_profile(dict(policy_profile))
        if errors:
            alerts.append(
                _alert("invalid_policy_profile", "policy_profile is invalid", errors=errors)
            )
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    elif data_boundary is not None:
        errors = validate_data_boundary(dict(data_boundary))
        if errors:
            alerts.append(
                _alert("invalid_data_boundary", "data_boundary is invalid", errors=errors)
            )
        if data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "classified-not-allowed data may not be routed to test-run evidence",
                )
            )
    return alerts


def _artifact_summary(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        return {"path": str(resolved), "exists": False, "sha256": None, "bytes": None}
    return {
        "path": str(resolved),
        "exists": True,
        "sha256": _optional_sha256(resolved),
        "bytes": resolved.stat().st_size,
    }


def _optional_sha256(path: Path) -> str | None:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        return None
    return f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
