"""Docker-backed persistent Python workspace endpoint for Tau agent nodes.

This module is the sandboxed v1 workspace slice. It starts a Tau-owned worker
process outside the scheduler, binds every request and snapshot to the node
attempt identity, returns receipts with bounded output/artifact references, and
leaves Tau admission/settlement as an explicit validator step.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tau_coding.dag_runtime.model import canonical_sha256

PYTHON_WORKSPACE_REQUEST_SCHEMA = "tau.python_workspace_request.v1"
PYTHON_WORKSPACE_RECEIPT_SCHEMA = "tau.python_workspace_receipt.v1"
PYTHON_EXECUTION_REQUEST_SCHEMA = "tau.python_execution_request.v1"
PYTHON_EXECUTION_RECEIPT_SCHEMA = "tau.python_execution_receipt.v1"
PYTHON_WORKSPACE_SNAPSHOT_SCHEMA = "tau.python_workspace_snapshot.v1"
PYTHON_PACKAGE_MANIFEST_SCHEMA = "tau.python_package_manifest.v1"
PYTHON_ARTIFACT_ADMISSION_RECEIPT_SCHEMA = "tau.python_workspace_artifact_admission_receipt.v1"

DEFAULT_WORKSPACE_IMAGE = (
    "python:3.12-slim@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e"
)

WorkspaceStatus = Literal["READY", "BLOCKED", "STOPPED"]


class PythonWorkspaceError(RuntimeError):
    """Raised when Tau cannot satisfy the Python workspace contract."""


@dataclass(frozen=True, slots=True)
class SandboxedPythonWorkspaceRequest:
    run_id: str
    node_id: str
    attempt_id: str
    attempt_number: int
    goal_hash: str
    plan_hash: str
    work_order_sha256: str
    policy_sha256: str
    data_boundary_sha256: str
    worktree_sha256: str
    sandbox_attestation_sha256: str
    backend: str = "docker"
    image: str = DEFAULT_WORKSPACE_IMAGE
    max_stdout_bytes: int = 16_000
    max_stderr_bytes: int = 16_000
    max_artifact_bytes: int = 1_000_000
    timeout_seconds: int = 5
    memory_limit: str = "256m"
    process_limit: int = 64

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "node_id",
            "attempt_id",
            "goal_hash",
            "plan_hash",
            "work_order_sha256",
            "policy_sha256",
            "data_boundary_sha256",
            "worktree_sha256",
            "sandbox_attestation_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise PythonWorkspaceError(f"{name} is required")
        if self.backend != "docker":
            raise PythonWorkspaceError("v1 sandboxed python workspace supports docker only")
        if "@sha256:" not in self.image:
            raise PythonWorkspaceError("workspace image must be pinned by sha256 digest")
        if self.attempt_number < 1:
            raise PythonWorkspaceError("attempt_number must be at least 1")
        for name in (
            "max_stdout_bytes",
            "max_stderr_bytes",
            "max_artifact_bytes",
            "timeout_seconds",
            "process_limit",
        ):
            if getattr(self, name) <= 0:
                raise PythonWorkspaceError(f"{name} must be positive")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_WORKSPACE_REQUEST_SCHEMA,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "goal_hash": self.goal_hash,
            "plan_hash": self.plan_hash,
            "work_order_sha256": self.work_order_sha256,
            "policy_sha256": self.policy_sha256,
            "data_boundary_sha256": self.data_boundary_sha256,
            "worktree_sha256": self.worktree_sha256,
            "sandbox_attestation_sha256": self.sandbox_attestation_sha256,
            "backend": self.backend,
            "image": self.image,
            "limits": _limits(self),
        }


class SandboxedPythonWorkspace:
    """Owns one Docker-sandboxed mutable namespace for a single node attempt."""

    def __init__(self, request: SandboxedPythonWorkspaceRequest, *, state_dir: Path) -> None:
        self.request = request
        self.state_dir = state_dir.expanduser().resolve()
        self.artifact_dir = self.state_dir / "artifacts"
        self.worker_path = self.state_dir / "python_workspace_worker.py"
        self._process: subprocess.Popen[str] | None = None
        self._generation_id = f"generation-{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
        self._workspace_receipt: dict[str, Any] | None = None

    @property
    def generation_id(self) -> str:
        return self._generation_id

    @property
    def handle(self) -> dict[str, Any]:
        return {
            "schema": "tau.python_workspace_handle.v1",
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "state_dir": str(self.state_dir),
            "endpoint_id": f"docker-python-workspace:{self.generation_id}",
        }

    def start(self) -> dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o777)
        os.chmod(self.artifact_dir, 0o777)
        shutil.copyfile(Path(__file__).with_name("python_workspace_worker.py"), self.worker_path)
        os.chmod(self.worker_path, 0o644)
        package_manifest = self._package_manifest()
        errors = self._startup_errors()
        if errors:
            receipt = self._workspace_receipt_payload(
                status="BLOCKED",
                package_manifest=package_manifest,
                process_identity={},
                errors=errors,
            )
            self._write_artifact("workspace-receipt.json", receipt)
            self._workspace_receipt = receipt
            return receipt
        command = self._docker_command()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._process = process
        ping = self._rpc({"command": "ping"}, timeout_seconds=10)
        if ping.get("status") != "OK":
            self.stop()
            receipt = self._workspace_receipt_payload(
                status="BLOCKED",
                package_manifest=package_manifest,
                process_identity={"docker_command": command},
                errors=["worker_ping_failed", *[str(item) for item in ping.get("errors", [])]],
            )
            self._write_artifact("workspace-receipt.json", receipt)
            self._workspace_receipt = receipt
            return receipt
        receipt = self._workspace_receipt_payload(
            status="READY",
            package_manifest=package_manifest,
            process_identity={
                "host_pid": process.pid,
                "worker_pid": ping.get("pid"),
                "container_cwd": ping.get("cwd"),
                "worker_env_keys": ping.get("env_keys"),
                "docker_command_sha256": canonical_sha256({"command": command}),
            },
            errors=[],
        )
        self._write_artifact("workspace-receipt.json", receipt)
        self._workspace_receipt = receipt
        return receipt

    def execute(self, execution_id: str, code: str) -> dict[str, Any]:
        if self._process is None or self._process.poll() is not None:
            raise PythonWorkspaceError("python workspace endpoint is not running")
        request = {
            "schema": PYTHON_EXECUTION_REQUEST_SCHEMA,
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "execution_id": execution_id,
            "goal_hash": self.request.goal_hash,
            "plan_hash": self.request.plan_hash,
            "work_order_sha256": self.request.work_order_sha256,
            "policy_sha256": self.request.policy_sha256,
            "data_boundary_sha256": self.request.data_boundary_sha256,
            "code_sha256": canonical_sha256({"code": code}),
            "limits": _limits(self.request),
        }
        started_at = _utc_now()
        worker_result = self._rpc(
            {
                "command": "execute",
                "execution_id": execution_id,
                "code": code,
                "code_sha256": request["code_sha256"],
                "limits": request["limits"],
            },
            timeout_seconds=self.request.timeout_seconds + 5,
        )
        finished_at = _utc_now()
        output_artifact = self._write_artifact(
            f"execution-{execution_id}.json",
            {
                "schema": "tau.python_execution_output_artifact.v1",
                "execution_request_sha256": canonical_sha256(request),
                "worker_result": worker_result,
            },
        )
        receipt = {
            "schema": PYTHON_EXECUTION_RECEIPT_SCHEMA,
            "execution_request_sha256": canonical_sha256(request),
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "execution_id": execution_id,
            "status": worker_result.get("status", "ERROR"),
            "started_at": started_at,
            "finished_at": finished_at,
            "code_sha256": request["code_sha256"],
            "stdout": worker_result.get("stdout", ""),
            "stderr": worker_result.get("stderr", ""),
            "stdout_truncated": bool(worker_result.get("stdout_truncated")),
            "stderr_truncated": bool(worker_result.get("stderr_truncated")),
            "exports": worker_result.get("exports"),
            "output_artifact": output_artifact,
            "idempotent_replay": bool(worker_result.get("idempotent_replay")),
            "tau_admission_status": "not_admitted",
            "effects_admitted": False,
            "errors": worker_result.get("errors", []),
            "limits": request["limits"],
        }
        self._write_artifact(f"execution-{execution_id}-receipt.json", receipt)
        return receipt

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        worker_snapshot = self._rpc({"command": "snapshot"}, timeout_seconds=5)
        payload = {
            "schema": PYTHON_WORKSPACE_SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "goal_hash": self.request.goal_hash,
            "plan_hash": self.request.plan_hash,
            "attempt_id": self.request.attempt_id,
            "policy_sha256": self.request.policy_sha256,
            "data_boundary_sha256": self.request.data_boundary_sha256,
            "package_manifest_sha256": self._package_manifest()["sha256"],
            "serializable_state": worker_snapshot.get("serializable_state", {}),
            "unsupported_state": worker_snapshot.get("unsupported_state", {}),
            "restore_policy": "exact_identity_and_manifest_required",
            "created_at": _utc_now(),
        }
        payload["sha256"] = canonical_sha256(payload)
        self._write_artifact(f"snapshot-{snapshot_id}.json", payload)
        return payload

    def restore_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        errors = stale_snapshot_errors(snapshot, self.request, self._package_manifest())
        if errors:
            return {
                "schema": "tau.python_workspace_snapshot_restore_receipt.v1",
                "status": "BLOCKED",
                "workspace_request_sha256": self.request.sha256,
                "generation_id": self.generation_id,
                "snapshot_sha256": snapshot.get("sha256"),
                "restored_names": [],
                "non_restorable_values": snapshot.get("unsupported_state", {}),
                "errors": errors,
            }
        response = self._rpc(
            {
                "command": "restore",
                "serializable_state": snapshot.get("serializable_state", {}),
            },
            timeout_seconds=5,
        )
        return {
            "schema": "tau.python_workspace_snapshot_restore_receipt.v1",
            "status": response.get("status", "ERROR"),
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "snapshot_sha256": snapshot.get("sha256"),
            "restored_names": response.get("restored_names", []),
            "non_restorable_values": snapshot.get("unsupported_state", {}),
            "errors": response.get("errors", []),
        }

    def stop(self) -> dict[str, Any]:
        process = self._process
        if process is None:
            return self._control_receipt("shutdown", "STOPPED", ["not_started"])
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        return self._control_receipt("shutdown", "STOPPED", [])

    def _rpc(self, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise PythonWorkspaceError("workspace process is not connected")
        process.stdin.write(json.dumps(payload, sort_keys=True) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            if process.poll() is not None:
                break
        stderr = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read(4000)
            except OSError:
                stderr = ""
        return {"status": "BLOCKED", "errors": ["worker_response_timeout", stderr]}

    def _docker_command(self) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.request.process_limit),
            "--memory",
            self.request.memory_limit,
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,source={self.state_dir},target=/workspace",
            self.request.image,
            "/usr/bin/env",
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED=1",
            "python",
            "/workspace/python_workspace_worker.py",
        ]

    def _startup_errors(self) -> list[str]:
        if shutil.which("docker") is None:
            return ["docker_unavailable"]
        return []

    def _package_manifest(self) -> dict[str, Any]:
        payload = {
            "schema": PYTHON_PACKAGE_MANIFEST_SCHEMA,
            "backend": "docker",
            "image": self.request.image,
            "packages": [],
            "environment_allowlist": [],
            "network": "none",
            "home_access": "denied",
            "provider_credentials": "denied",
            "sandbox": {
                "read_only_rootfs": True,
                "cap_drop": ["ALL"],
                "no_new_privileges": True,
                "user": "65532:65532",
                "host_home_mounted": False,
            },
        }
        payload["sha256"] = canonical_sha256(payload)
        return payload

    def _workspace_receipt_payload(
        self,
        *,
        status: WorkspaceStatus,
        package_manifest: dict[str, Any],
        process_identity: dict[str, Any],
        errors: list[str],
    ) -> dict[str, Any]:
        return {
            "schema": PYTHON_WORKSPACE_RECEIPT_SCHEMA,
            "workspace_request_sha256": self.request.sha256,
            "status": status,
            "backend": "docker",
            "endpoint": self.handle if status == "READY" else None,
            "generation_id": self.generation_id,
            "package_manifest": package_manifest,
            "process_identity": process_identity,
            "sandbox_attestation": {
                "network": "none",
                "environment_allowlist": [],
                "home_access": "denied",
                "provider_credentials": "denied",
                "host_process_namespace": "denied",
                "sha256": self.request.sandbox_attestation_sha256,
            },
            "ordinary_tau_startup_cost": "none_until_workspace_requested",
            "errors": errors,
        }

    def _control_receipt(self, action: str, status: str, errors: list[str]) -> dict[str, Any]:
        return {
            "schema": "tau.python_workspace_control_receipt.v1",
            "workspace_request_sha256": self.request.sha256,
            "generation_id": self.generation_id,
            "action": action,
            "status": status,
            "errors": errors,
        }

    def _write_artifact(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        if len(data) > self.request.max_artifact_bytes:
            raise PythonWorkspaceError("artifact_limit_exceeded")
        digest = hashlib.sha256(data).hexdigest()
        path = self.artifact_dir / f"{digest}-{name}"
        if not path.exists():
            path.write_bytes(data)
        return {
            "schema": "tau.host_artifact_ref.v1",
            "path": str(path),
            "sha256": f"sha256:{digest}",
            "bytes": len(data),
            "media_type": "application/json",
        }


def stale_snapshot_errors(
    snapshot: Mapping[str, Any],
    request: SandboxedPythonWorkspaceRequest,
    package_manifest: Mapping[str, Any],
) -> list[str]:
    checks = {
        "workspace_request_sha256": request.sha256,
        "goal_hash": request.goal_hash,
        "plan_hash": request.plan_hash,
        "attempt_id": request.attempt_id,
        "policy_sha256": request.policy_sha256,
        "data_boundary_sha256": request.data_boundary_sha256,
        "package_manifest_sha256": package_manifest.get("sha256"),
    }
    errors = [
        f"stale_snapshot:{name}"
        for name, expected in checks.items()
        if snapshot.get(name) != expected
    ]
    if snapshot.get("schema") != PYTHON_WORKSPACE_SNAPSHOT_SCHEMA:
        errors.append("stale_snapshot:schema")
    return errors


def admit_python_execution_artifact(receipt: Mapping[str, Any]) -> dict[str, Any]:
    artifact = receipt.get("output_artifact")
    errors: list[str] = []
    if receipt.get("schema") != PYTHON_EXECUTION_RECEIPT_SCHEMA:
        errors.append("invalid_execution_receipt_schema")
    if receipt.get("status") != "OK":
        errors.append("execution_not_successful")
    if not isinstance(artifact, dict):
        errors.append("missing_output_artifact")
    else:
        path_value = artifact.get("path")
        digest = artifact.get("sha256")
        if not isinstance(path_value, str) or not Path(path_value).is_file():
            errors.append("output_artifact_missing")
        elif _file_sha256(Path(path_value)) != digest:
            errors.append("output_artifact_sha256_mismatch")
    return {
        "schema": PYTHON_ARTIFACT_ADMISSION_RECEIPT_SCHEMA,
        "status": "BLOCKED" if errors else "PASS",
        "execution_receipt_sha256": canonical_sha256(dict(receipt)),
        "artifact": artifact if isinstance(artifact, dict) else None,
        "accepted_output": None
        if errors
        else {
            "artifact": artifact,
            "exports": receipt.get("exports"),
            "source_execution_id": receipt.get("execution_id"),
        },
        "errors": errors,
    }


def _limits(request: SandboxedPythonWorkspaceRequest) -> dict[str, Any]:
    return {
        "max_stdout_bytes": request.max_stdout_bytes,
        "max_stderr_bytes": request.max_stderr_bytes,
        "max_artifact_bytes": request.max_artifact_bytes,
        "timeout_seconds": request.timeout_seconds,
        "process_limit": request.process_limit,
        "memory_limit": request.memory_limit,
    }


def _file_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
