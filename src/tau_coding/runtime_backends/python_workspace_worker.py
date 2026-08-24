"""Stdlib JSONL worker for Tau's sandboxed Python workspace endpoint.

The worker runs inside a Tau-owned sandbox container. It accepts bounded JSON
requests on stdin, mutates only its in-process namespace, and returns typed JSON
responses on stdout. Host-side Tau code owns receipts, artifact admission, and
settlement; this worker is computation only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import socket
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any

_GLOBALS: dict[str, Any] = {"__builtins__": __builtins__}
_EXECUTIONS: dict[str, dict[str, Any]] = {}


class _ExecutionTimeout(TimeoutError):
    pass


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = _handle(request)
        except Exception as exc:
            response = {
                "schema": "tau.python_workspace_worker_response.v1",
                "status": "ERROR",
                "errors": [f"worker_error:{type(exc).__name__}:{exc}"],
            }
        print(json.dumps(response, sort_keys=True), flush=True)
    return 0


def _handle(request: Mapping[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    if command == "ping":
        return {
            "schema": "tau.python_workspace_worker_response.v1",
            "status": "OK",
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "env_keys": sorted(os.environ),
        }
    if command == "execute":
        return _execute(request)
    if command == "snapshot":
        return _snapshot(request)
    if command == "restore":
        return _restore(request)
    return {
        "schema": "tau.python_workspace_worker_response.v1",
        "status": "BLOCKED",
        "errors": [f"unknown_command:{command}"],
    }


def _execute(request: Mapping[str, Any]) -> dict[str, Any]:
    execution_id = _required_str(request, "execution_id")
    code = _required_str(request, "code")
    code_sha256 = _required_str(request, "code_sha256")
    limits = request.get("limits") if isinstance(request.get("limits"), dict) else {}
    max_stdout = _limit(limits, "max_stdout_bytes", 16_000)
    max_stderr = _limit(limits, "max_stderr_bytes", 16_000)
    timeout_seconds = max(1, _limit(limits, "timeout_seconds", 5))

    previous = _EXECUTIONS.get(execution_id)
    if previous is not None:
        if previous.get("code_sha256") == code_sha256:
            replay = dict(previous)
            replay["idempotent_replay"] = True
            return replay
        return {
            "schema": "tau.python_execution_worker_result.v1",
            "status": "BLOCKED",
            "execution_id": execution_id,
            "code_sha256": code_sha256,
            "idempotent_replay": False,
            "effects_applied": False,
            "errors": ["duplicate_execution_id_conflict"],
        }

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    status = "OK"
    errors: list[str] = []
    exports: Any = None

    def on_timeout(_signum: int, _frame: object) -> None:
        raise _ExecutionTimeout("execution_timeout")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, on_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compile(code, f"<tau-workspace:{execution_id}>", "exec"), _GLOBALS)
        exports = _GLOBALS.get("tau_exports")
    except _ExecutionTimeout:
        status = "BLOCKED"
        errors.append("execution_timeout")
    except Exception as exc:
        status = "ERROR"
        errors.append(type(exc).__name__)
        stderr_buffer.write(traceback.format_exc())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)

    stdout, stdout_truncated = _bounded(stdout_buffer.getvalue(), max_stdout)
    stderr, stderr_truncated = _bounded(stderr_buffer.getvalue(), max_stderr)
    result = {
        "schema": "tau.python_execution_worker_result.v1",
        "status": status,
        "execution_id": execution_id,
        "code_sha256": code_sha256,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "exports": _jsonable_or_marker(exports),
        "idempotent_replay": False,
        "effects_applied": status in {"OK", "ERROR", "BLOCKED"},
        "errors": errors,
    }
    _EXECUTIONS[execution_id] = result
    return result


def _snapshot(request: Mapping[str, Any]) -> dict[str, Any]:
    del request
    serializable: dict[str, Any] = {}
    unsupported: dict[str, str] = {}
    for name, value in sorted(_GLOBALS.items()):
        if name.startswith("__") or name == "tau_exports":
            continue
        marker = _jsonable_or_marker(value)
        if isinstance(marker, dict) and marker.get("unsupported") is True:
            unsupported[name] = str(marker["reason"])
        else:
            serializable[name] = marker
    return {
        "schema": "tau.python_workspace_worker_snapshot.v1",
        "status": "OK",
        "serializable_state": serializable,
        "unsupported_state": unsupported,
    }


def _restore(request: Mapping[str, Any]) -> dict[str, Any]:
    state = request.get("serializable_state")
    if not isinstance(state, dict):
        return {
            "schema": "tau.python_workspace_worker_restore.v1",
            "status": "BLOCKED",
            "errors": ["missing_serializable_state"],
        }
    for name, value in state.items():
        if isinstance(name, str) and name and not name.startswith("__"):
            _GLOBALS[name] = value
    return {
        "schema": "tau.python_workspace_worker_restore.v1",
        "status": "OK",
        "restored_names": sorted(str(name) for name in state),
        "errors": [],
    }


def _jsonable_or_marker(value: Any) -> Any:
    try:
        json.dumps(value, sort_keys=True)
    except TypeError:
        if isinstance(value, ModuleType):
            reason = f"module:{value.__name__}"
        elif isinstance(value, FunctionType):
            reason = f"function:{value.__name__}"
        else:
            reason = type(value).__name__
        return {"unsupported": True, "reason": reason}
    return value


def _bounded(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _limit(limits: Mapping[str, Any], name: str, default: int) -> int:
    value = limits.get(name, default)
    if type(value) is not int or value <= 0:
        return default
    return value


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


if __name__ == "__main__":
    # Keep a few modules imported so denial probes can run without host help.
    _GLOBALS.update({"socket": socket, "Path": Path, "os": os})
    raise SystemExit(main())
