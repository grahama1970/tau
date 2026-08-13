"""Jupyter-backed Python workspace runtime for attempt-scoped Tau executions."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.kernel_contracts import (
    PYTHON_KERNEL_FEATURES,
    PythonExecutionReceipt,
    PythonExecutionRequest,
    PythonKernelControlReceipt,
    PythonPackageManifest,
    PythonWorkspaceReceipt,
    PythonWorkspaceRequest,
    build_python_package_manifest,
    workspace_endpoint_lease,
)


class PythonKernelUnavailableError(RuntimeError):
    """Raised when the optional Python workspace dependencies are unavailable."""


class PythonKernelWorkspace:
    """Owns one persistent kernel namespace for one Tau node attempt."""

    def __init__(self, request: PythonWorkspaceRequest, *, state_dir: Path) -> None:
        self.request = request
        self.state_dir = state_dir.expanduser().resolve()
        self.artifact_dir = self.state_dir / "artifacts"
        self.state_path = self.state_dir / "python-kernel-state.json"
        self._lock = threading.Lock()
        self._generation_id = f"generation-{secrets.token_hex(12)}"
        self._kernel_manager: Any | None = None
        self._client: Any | None = None
        self._endpoint_lease_sha256: str | None = None
        self._workspace_request_sha256 = request.sha256
        self._process_identity: dict[str, Any] = {}
        self._ready = False
        self._quarantined = False

    @property
    def generation_id(self) -> str:
        return self._generation_id

    @property
    def endpoint_lease_sha256(self) -> str:
        if self._endpoint_lease_sha256 is None:
            raise PythonKernelUnavailableError("python workspace has no endpoint lease")
        return self._endpoint_lease_sha256

    def start(self) -> PythonWorkspaceReceipt:
        manifest = build_python_package_manifest()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        if not manifest.available:
            receipt = PythonWorkspaceReceipt(
                workspace_request_sha256=self._workspace_request_sha256,
                status="UNAVAILABLE",
                endpoint_lease=None,
                package_manifest=FrozenJson.from_value(manifest.to_payload()),
                generation_id=self._generation_id,
                process_identity=FrozenJson.from_value({}),
                state_path=str(self.state_path),
                errors=manifest.errors,
            )
            _write_json(self.state_path, receipt.to_payload())
            return receipt

        try:
            from jupyter_client.manager import KernelManager
        except ImportError as exc:
            raise PythonKernelUnavailableError("jupyter_client is not installed") from exc

        kernel_manager = KernelManager()
        worktree = Path(self.request.worktree).expanduser().resolve()
        kernel_manager.start_kernel(cwd=str(worktree))
        client = kernel_manager.client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=self.request.startup_timeout_seconds)
        except Exception:
            self._shutdown_kernel(kernel_manager, client)
            raise

        self._kernel_manager = kernel_manager
        self._client = client
        self._process_identity = _kernel_process_identity(kernel_manager)
        now = _utc_now()
        endpoint = workspace_endpoint_lease(
            self.request,
            endpoint_id=f"python-kernel:{self._generation_id}",
            backend_session_id=_kernel_id(kernel_manager),
            generation_id=self._generation_id,
            process_identity=self._process_identity,
            created_at=now,
            expires_at=(datetime.now(UTC) + timedelta(hours=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        )
        self._endpoint_lease_sha256 = endpoint.sha256
        self._ready = True
        receipt = PythonWorkspaceReceipt(
            workspace_request_sha256=self._workspace_request_sha256,
            status="READY",
            endpoint_lease=FrozenJson.from_value(endpoint.to_payload()),
            package_manifest=FrozenJson.from_value(manifest.to_payload()),
            generation_id=self._generation_id,
            process_identity=FrozenJson.from_value(self._process_identity),
            state_path=str(self.state_path),
            errors=(),
        )
        _write_json(self.state_path, receipt.to_payload())
        return receipt

    def execute(
        self,
        code: str,
        *,
        execution_id: str | None = None,
        timeout_seconds: float = 10.0,
        late_output_grace_seconds: float = 0.0,
    ) -> PythonExecutionReceipt:
        if not self._ready or self._client is None:
            raise PythonKernelUnavailableError("python workspace is not ready")
        if self._quarantined:
            raise PythonKernelUnavailableError("python workspace is quarantined")
        with self._lock:
            request = PythonExecutionRequest(
                workspace_request_sha256=self._workspace_request_sha256,
                endpoint_lease_sha256=self.endpoint_lease_sha256,
                generation_id=self._generation_id,
                execution_id=execution_id or f"exec-{secrets.token_hex(8)}",
                goal_hash=self.request.goal_hash,
                policy_sha256=self.request.policy_sha256,
                data_boundary_sha256=self.request.data_boundary_sha256,
                code=code,
            )
            return self._execute_locked(
                request,
                timeout_seconds=timeout_seconds,
                late_output_grace_seconds=late_output_grace_seconds,
            )

    def interrupt(self, *, timeout_seconds: float = 5.0) -> PythonKernelControlReceipt:
        if self._kernel_manager is None or self._client is None:
            raise PythonKernelUnavailableError("python workspace is not started")
        before = dict(self._process_identity)
        errors: list[str] = []
        status = "READY"
        try:
            self._kernel_manager.interrupt_kernel()
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                with suppress(Exception):
                    self._client.get_shell_msg(timeout=0.05)
                if _pid_is_same_process(before):
                    break
            if not _pid_is_same_process(before):
                status = "QUARANTINED"
                errors.append("kernel_process_identity_changed")
                self._quarantined = True
        except Exception as exc:
            status = "QUARANTINED"
            errors.append(f"interrupt_failed:{type(exc).__name__}")
            self._quarantined = True
        after = _kernel_process_identity(self._kernel_manager)
        return PythonKernelControlReceipt(
            endpoint_lease_sha256=self.endpoint_lease_sha256,
            generation_id=self._generation_id,
            action="interrupt",
            status=status,
            process_identity_before=FrozenJson.from_value(before),
            process_identity_after=FrozenJson.from_value(after),
            errors=tuple(errors),
        )

    def stop(self) -> PythonKernelControlReceipt:
        if self._kernel_manager is None or self._client is None:
            raise PythonKernelUnavailableError("python workspace is not started")
        before = dict(self._process_identity)
        errors: list[str] = []
        try:
            self._shutdown_kernel(self._kernel_manager, self._client)
        except Exception as exc:
            errors.append(f"shutdown_failed:{type(exc).__name__}")
        finally:
            self._ready = False
        after = _kernel_process_identity(self._kernel_manager)
        return PythonKernelControlReceipt(
            endpoint_lease_sha256=self.endpoint_lease_sha256,
            generation_id=self._generation_id,
            action="shutdown",
            status="STOPPED" if not errors else "BLOCKED",
            process_identity_before=FrozenJson.from_value(before),
            process_identity_after=FrozenJson.from_value(after),
            errors=tuple(errors),
        )

    def _execute_locked(
        self,
        request: PythonExecutionRequest,
        *,
        timeout_seconds: float,
        late_output_grace_seconds: float,
    ) -> PythonExecutionReceipt:
        client = self._client
        if client is None:
            raise PythonKernelUnavailableError("python workspace is not ready")
        started = time.monotonic()
        started_at = _utc_now()
        msg_id = client.execute(request.code, store_history=True)
        messages: list[dict[str, Any]] = []
        errors: list[str] = []
        status = "OK"
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                message = client.get_iopub_msg(timeout=remaining)
            except Exception:
                status = "BLOCKED"
                errors.append("execution_timeout")
                break
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            messages.append(_event_from_message(message, classification="during_execution"))
            message_type = message.get("msg_type")
            if message_type == "error":
                status = "ERROR"
                content = message.get("content", {})
                ename = content.get("ename")
                if isinstance(ename, str) and ename:
                    errors.append(ename)
            if (
                message_type == "status"
                and message.get("content", {}).get("execution_state") == "idle"
            ):
                break
        late_messages = self._collect_late_messages(msg_id, grace_seconds=late_output_grace_seconds)
        finished_at = _utc_now()
        duration_ms = int((time.monotonic() - started) * 1000)
        output_artifact = self._write_output_artifact(
            request=request,
            messages=messages,
            late_messages=late_messages,
            status=status,
            errors=errors,
        )
        projection = _output_projection(output_artifact)
        return PythonExecutionReceipt(
            execution_request_sha256=request.sha256,
            workspace_request_sha256=request.workspace_request_sha256,
            endpoint_lease_sha256=request.endpoint_lease_sha256,
            generation_id=request.generation_id,
            execution_id=request.execution_id,
            jupyter_msg_id=msg_id,
            status=status,  # type: ignore[arg-type]
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            code_sha256=canonical_sha256({"code": request.code}),
            output_artifact=FrozenJson.from_value(output_artifact),
            output_projection=FrozenJson.from_value(projection),
            late_async_outputs=tuple(FrozenJson.from_value(item) for item in late_messages),
            tau_admission_status="not_admitted",
            errors=tuple(errors),
        )

    def _collect_late_messages(self, msg_id: str, *, grace_seconds: float) -> list[dict[str, Any]]:
        if grace_seconds <= 0 or self._client is None:
            return []
        deadline = time.monotonic() + grace_seconds
        late_messages: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            try:
                message = self._client.get_iopub_msg(timeout=0.05)
            except Exception:
                continue
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            late_messages.append(_event_from_message(message, classification="late_async_output"))
        return late_messages

    def _write_output_artifact(
        self,
        *,
        request: PythonExecutionRequest,
        messages: list[dict[str, Any]],
        late_messages: list[dict[str, Any]],
        status: str,
        errors: list[str],
    ) -> dict[str, Any]:
        payload = {
            "schema": "tau.python_execution_output_artifact.v1",
            "execution_request_sha256": request.sha256,
            "workspace_request_sha256": request.workspace_request_sha256,
            "endpoint_lease_sha256": request.endpoint_lease_sha256,
            "generation_id": request.generation_id,
            "execution_id": request.execution_id,
            "status": status,
            "messages": messages,
            "late_async_outputs": late_messages,
            "errors": errors,
        }
        digest = canonical_sha256(payload)
        path = self.artifact_dir / f"{request.execution_id}-{digest.removeprefix('sha256:')}.json"
        _write_json(path, payload)
        return {
            "path": str(path),
            "sha256": digest,
            "message_count": len(messages),
            "late_async_output_count": len(late_messages),
            "full_output_schema": payload["schema"],
        }

    @staticmethod
    def _shutdown_kernel(kernel_manager: Any, client: Any) -> None:
        client.stop_channels()
        kernel_manager.shutdown_kernel(now=True)


def create_python_workspace(
    request: PythonWorkspaceRequest, *, state_dir: Path
) -> tuple[PythonKernelWorkspace, PythonWorkspaceReceipt]:
    workspace = PythonKernelWorkspace(request, state_dir=state_dir)
    return workspace, workspace.start()


def reconcile_kernel_process_state(state_path: Path) -> dict[str, Any]:
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    process_identity = payload.get("process_identity")
    endpoint = payload.get("endpoint_lease") or {}
    lease_sha256 = canonical_sha256(endpoint) if endpoint else "sha256:" + ("0" * 64)
    if not isinstance(process_identity, dict):
        return _reconciliation_payload(
            lease_sha256=lease_sha256,
            action="block_unknown_process_identity",
            status="BLOCKED",
            evidence={},
            errors=("missing_process_identity",),
        )
    if _pid_is_same_process(process_identity):
        return _reconciliation_payload(
            lease_sha256=lease_sha256,
            action="reconcile_existing_owned_kernel",
            status="PASS",
            evidence={"process_identity": process_identity},
            errors=(),
        )
    if _pid_exists(process_identity.get("pid")):
        return _reconciliation_payload(
            lease_sha256=lease_sha256,
            action="skip_unrelated_pid_reuse",
            status="PASS",
            evidence={"process_identity": process_identity},
            errors=(),
        )
    return _reconciliation_payload(
        lease_sha256=lease_sha256,
        action="mark_dead_kernel",
        status="PASS",
        evidence={"process_identity": process_identity},
        errors=(),
    )


def write_python_workspace_canary(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    request = PythonWorkspaceRequest(
        run_id="python-workspace-canary",
        plan_revision=canonical_sha256({"plan": "python-workspace-canary"}),
        dag_id="python-workspace-canary",
        node_id="kernel",
        attempt_id="attempt-1",
        attempt_number=1,
        worktree=str(Path.cwd()),
        goal_hash=canonical_sha256({"goal": "python workspace canary"}),
        policy_sha256=canonical_sha256({"policy": "canary"}),
        data_boundary_sha256=canonical_sha256({"data_boundary": "local"}),
        required_features=tuple(sorted(PYTHON_KERNEL_FEATURES)),
        startup_timeout_seconds=20,
    )
    workspace, workspace_receipt = create_python_workspace(request, state_dir=output_dir)
    receipts: list[dict[str, Any]] = []
    interrupt_receipt: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    try:
        receipts.append(workspace.execute("x = 41\nprint('stored', x)").to_payload())
        receipts.append(workspace.execute("print('persisted', x + 1)").to_payload())
        receipts.append(workspace.execute("print('PASS')").to_payload())
        worker = threading.Thread(
            target=lambda: workspace.execute("while True:\n    pass", timeout_seconds=30),
            daemon=True,
        )
        worker.start()
        time.sleep(0.5)
        interrupt_receipt = workspace.interrupt().to_payload()
        worker.join(timeout=5)
        reconciliation = reconcile_kernel_process_state(Path(workspace_receipt.state_path))
    finally:
        with suppress(Exception):
            workspace.stop()
    checks = {
        "endpoint_lease_present": workspace_receipt.endpoint_lease is not None,
        "package_manifest_available": PythonPackageManifest.from_payload(
            workspace_receipt.package_manifest.to_value()
        ).available,
        "namespace_persisted": "persisted" in _artifact_text(receipts[1]),
        "kernel_success_not_tau_acceptance": (
            receipts[2]["status"] == "OK" and receipts[2]["tau_admission_status"] == "not_admitted"
        ),
        "interrupt_returned_terminal_control_receipt": interrupt_receipt is not None
        and interrupt_receipt["status"] in {"READY", "QUARANTINED"},
        "restart_reconciliation_positive": reconciliation is not None
        and reconciliation["status"] == "PASS",
    }
    receipt: dict[str, Any] = {
        "schema": "tau.python_workspace_canary_receipt.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "workspace_receipt": workspace_receipt.to_payload(),
        "execution_receipts": receipts,
        "interrupt_receipt": interrupt_receipt,
        "reconciliation_receipt": reconciliation,
        "checks": checks,
    }
    receipt["status"] = "PASS" if all(checks.values()) else "BLOCKED"
    _write_json(output_dir / "python-workspace-canary.json", receipt)
    return receipt


def _event_from_message(message: dict[str, Any], *, classification: str) -> dict[str, Any]:
    content = message.get("content", {})
    payload: dict[str, Any] = {
        "msg_type": str(message.get("msg_type", "")),
        "classification": classification,
        "content": content,
    }
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        payload["text"] = content["text"]
    return payload


def _output_projection(output_artifact: dict[str, Any], *, max_chars: int = 500) -> dict[str, Any]:
    path = Path(str(output_artifact["path"]))
    text = path.read_text(encoding="utf-8")
    truncated = len(text) > max_chars
    return {
        "schema": "tau.python_execution_output_projection.v1",
        "artifact": output_artifact,
        "preview": text[:max_chars],
        "truncated": truncated,
        "full_output_sha256": output_artifact["sha256"],
    }


def _artifact_text(receipt_payload: dict[str, Any]) -> str:
    output_artifact = receipt_payload.get("output_artifact")
    if not isinstance(output_artifact, dict):
        return ""
    path = output_artifact.get("path")
    if not isinstance(path, str):
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _kernel_id(kernel_manager: Any) -> str:
    kernel_id = getattr(kernel_manager, "kernel_id", None)
    if isinstance(kernel_id, str) and kernel_id:
        return kernel_id
    connection_file = getattr(kernel_manager, "connection_file", None)
    if isinstance(connection_file, str) and connection_file:
        return Path(connection_file).stem
    return f"kernel-{secrets.token_hex(8)}"


def _kernel_process_identity(kernel_manager: Any) -> dict[str, Any]:
    provisioner = getattr(kernel_manager, "provisioner", None)
    pid = getattr(provisioner, "pid", None)
    if not isinstance(pid, int):
        kernel = getattr(kernel_manager, "kernel", None)
        pid = getattr(kernel, "pid", None)
    if not isinstance(pid, int):
        return {"pid": None, "start_time_ticks": None, "alive": False}
    return _process_identity(pid)


def _process_identity(pid: int) -> dict[str, Any]:
    return {
        "pid": pid,
        "start_time_ticks": _linux_process_start_time_ticks(pid),
        "alive": _pid_exists(pid),
    }


def _pid_is_same_process(identity: dict[str, Any]) -> bool:
    pid = identity.get("pid")
    if not isinstance(pid, int) or not _pid_exists(pid):
        return False
    expected_start = identity.get("start_time_ticks")
    if expected_start is None:
        return True
    if not isinstance(expected_start, str):
        return False
    return _linux_process_start_time_ticks(pid) == expected_start


def _pid_exists(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _linux_process_start_time_ticks(pid: int) -> str | None:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = stat.rsplit(") ", maxsplit=1)
    if len(parts) != 2:
        return None
    fields = parts[1].split()
    if len(fields) < 20:
        return None
    return fields[19]


def _reconciliation_payload(
    *,
    lease_sha256: str,
    action: str,
    status: str,
    evidence: dict[str, Any],
    errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": "tau.runtime_reconciliation_receipt.v1",
        "run_id": "python-workspace",
        "endpoint_lease_sha256": lease_sha256,
        "status": status,
        "action": action,
        "evidence": evidence,
        "errors": list(errors),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
