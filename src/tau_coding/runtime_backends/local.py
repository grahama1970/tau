"""One-shot local subprocess implementation of the runtime backend contract."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, cast

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.dag_runtime.subprocess_control import (
    CancellableSubprocessResult,
    run_cancellable_subprocess,
)
from tau_coding.runtime_backends.contracts import (
    RuntimeCapabilities,
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeState,
    RuntimeSubmitReceipt,
)


@dataclass(frozen=True, slots=True)
class LocalRuntimeExecutionRequest:
    run_id: str
    plan_revision: str
    dag_id: str
    node_id: str
    attempt_id: str
    attempt_number: int
    execution_token: str
    command: tuple[str, ...]
    cwd: Path | None
    env: Mapping[str, str] | None
    stdin_text: str | None
    timeout_seconds: float | None
    work_order_sha256: str
    goal_hash: str
    artifact_dir: Path | None = None
    cancel_event: Event | None = None


@dataclass(frozen=True, slots=True)
class LocalRuntimeExecutionResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    termination_cause: str
    endpoint_lease: RuntimeEndpointLease
    submit_receipt: RuntimeSubmitReceipt
    runtime_event: RuntimeEvent
    capture: FrozenJson
    artifact_paths: tuple[str, ...]


@dataclass(slots=True)
class _LocalExecutionState:
    request: LocalRuntimeExecutionRequest
    lease: RuntimeEndpointLease
    result: LocalRuntimeExecutionResult | None = None
    execution_started: bool = False
    completion_event: Event = field(default_factory=Event)
    execution_error: Exception | None = None
    termination_requested: bool = False
    failure_event: RuntimeEvent | None = None


class LocalRuntimeBackend:
    """Execute one-shot commands while preserving Tau's existing process semantics."""

    def __init__(self) -> None:
        self._states: dict[str, _LocalExecutionState] = {}
        self._endpoint_ids: dict[str, str] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend="local",
            version="tau-local-runtime-v1",
            interactive=False,
            one_shot=True,
            native_events=False,
            native_agent_state=False,
            foreground_process_state=True,
            structured_composer_state=False,
            stable_endpoint_id=True,
            human_attach=False,
            supports_working_directory=True,
            supports_owned_inventory=True,
            supports_terminate=True,
            observation_confidence_levels=("PROCESS",),
            supported_session_scopes=("node_attempt",),
        )

    def execute(self, request: LocalRuntimeExecutionRequest) -> LocalRuntimeExecutionResult:
        lease = self._spawn_request(request)
        return self._submit_request(lease)

    def ensure_scope(self, request: FrozenJson) -> FrozenJson:
        payload = request.to_value()
        if not isinstance(payload, dict):
            raise ValueError("local runtime scope request must be an object")
        return FrozenJson.from_value(
            {"backend": "local", "scope_id": str(payload.get("run_id") or "local")}
        )

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease:
        payload = request.to_value()
        if not isinstance(payload, dict):
            raise ValueError("local runtime spawn request must be an object")
        return self._spawn_request(_request_from_payload(payload))

    def submit(
        self, endpoint: RuntimeEndpointLease, work_order: FrozenJson
    ) -> RuntimeSubmitReceipt:
        payload = work_order.to_value()
        if not isinstance(payload, dict) or payload.get("work_order_sha256") != (
            endpoint.work_order_sha256
        ):
            raise ValueError("local runtime work order does not match endpoint lease")
        return self._submit_request(endpoint).submit_receipt

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson:
        result = self._completed_result(endpoint)
        if lines < 0:
            raise ValueError("capture lines must be non-negative")
        payload = result.capture.to_value()
        if not isinstance(payload, dict):
            raise RuntimeError("local runtime capture is invalid")
        bounded = dict(payload)
        for key in ("stdout", "stderr"):
            value = bounded.get(key)
            if isinstance(value, str):
                bounded[key] = "" if lines == 0 else "\n".join(value.splitlines()[-lines:])
        return FrozenJson.from_value(bounded)

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent:
        with self._lock:
            state = self._states.get(endpoint.sha256)
            if state is None:
                raise RuntimeError("local_runtime_endpoint_unknown")
            result = state.result
            failure_event = state.failure_event
            execution_started = state.execution_started
        if result is not None:
            return result.runtime_event
        if failure_event is not None:
            return failure_event
        if not execution_started:
            raise RuntimeError("local_runtime_endpoint_not_started")
        return RuntimeEvent(
            event_id="local-running:" + endpoint.endpoint_id.removeprefix("local:"),
            run_id=endpoint.run_id,
            endpoint_lease_sha256=endpoint.sha256,
            event_type="RUNTIME_ENDPOINT_RUNNING",
            observed_at=datetime.now(UTC).isoformat(),
            state="RUNNING",
            liveness="ALIVE",
            confidence="PROCESS",
            source="local",
            observation=FrozenJson.from_value({"process_started": True}),
        )

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEvent | None:
        with self._lock:
            state = self._states.get(endpoint.sha256)
        if state is None:
            raise RuntimeError("local_runtime_endpoint_unknown")
        remaining = max((deadline - datetime.now(UTC)).total_seconds(), 0.0)
        if not state.completion_event.wait(timeout=remaining):
            return None
        event = self.observe(endpoint)
        return None if cursor == event.event_id else event

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]:
        with self._lock:
            return [
                state.lease for state in self._states.values() if state.request.run_id == run_id
            ]

    def terminate(self, endpoint: RuntimeEndpointLease, authorization: FrozenJson) -> FrozenJson:
        pre_submit = False
        with self._lock:
            state = self._states.get(endpoint.sha256)
            if state is None:
                raise RuntimeError("local_runtime_endpoint_unknown")
            if state.result is not None:
                return FrozenJson.from_value(
                    {
                        "status": "PASS",
                        "action": "already_exited",
                        "endpoint_id": endpoint.endpoint_id,
                    }
                )
            if state.request.cancel_event is None:
                raise RuntimeError("local_runtime_endpoint_has_no_cancellation_handle")
            state.termination_requested = True
            state.request.cancel_event.set()
            if not state.execution_started:
                state.execution_started = True
                pre_submit = True
        if pre_submit:
            try:
                result = self._cancelled_before_submit_result(state)
            except Exception as exc:
                self._record_finalization_failure(state, exc)
                raise
            with self._lock:
                state.result = result
                state.completion_event.set()
        return FrozenJson.from_value(
            {
                "status": "PASS",
                "action": "cancellation_requested",
                "endpoint_id": endpoint.endpoint_id,
            }
        )

    def _spawn_request(self, request: LocalRuntimeExecutionRequest) -> RuntimeEndpointLease:
        if not request.command or any(not item for item in request.command):
            raise ValueError("local runtime command must contain non-empty arguments")
        if request.cancel_event is None:
            request = replace(request, cancel_event=Event())
        now = datetime.now(UTC)
        timeout = request.timeout_seconds or 0.0
        endpoint_id = (
            "local:"
            + canonical_sha256(
                {
                    "run_id": request.run_id,
                    "node_id": request.node_id,
                    "attempt_id": request.attempt_id,
                    "execution_token": request.execution_token,
                }
            ).removeprefix("sha256:")[:24]
        )
        lease = RuntimeEndpointLease(
            run_id=request.run_id,
            plan_revision=request.plan_revision,
            dag_id=request.dag_id,
            node_id=request.node_id,
            attempt_id=request.attempt_id,
            attempt_number=request.attempt_number,
            execution_token=request.execution_token,
            backend="local",
            backend_session_id=None,
            scope_id=request.run_id,
            endpoint_id=endpoint_id,
            work_order_sha256=request.work_order_sha256,
            goal_hash=request.goal_hash,
            owner="tau",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=max(timeout, 1.0) + 60.0)).isoformat(),
            heartbeat_policy=FrozenJson.from_value({"kind": "process_poll"}),
            cleanup_policy=FrozenJson.from_value({"kind": "process_group_termination"}),
            capabilities_sha256=self.capabilities().sha256,
            backend_ids=FrozenJson.from_value({"pid": None}),
        )
        with self._lock:
            if endpoint_id in self._endpoint_ids:
                raise RuntimeError("local_runtime_endpoint_already_exists")
            self._states[lease.sha256] = _LocalExecutionState(
                request=request,
                lease=lease,
            )
            self._endpoint_ids[endpoint_id] = lease.sha256
        return lease

    def _submit_request(self, endpoint: RuntimeEndpointLease) -> LocalRuntimeExecutionResult:
        with self._lock:
            state = self._states.get(endpoint.sha256)
            if state is None:
                raise RuntimeError("local_runtime_endpoint_unknown")
            if state.result is not None:
                return state.result
            if state.execution_error is not None:
                raise RuntimeError("local_runtime_execution_failed") from state.execution_error
            wait_for_existing_execution = state.execution_started
            state.execution_started = True
        if wait_for_existing_execution:
            state.completion_event.wait()
            with self._lock:
                if state.result is not None:
                    return state.result
                error = state.execution_error
            raise RuntimeError("local_runtime_execution_failed") from error
        request = state.request
        try:
            completed = run_cancellable_subprocess(
                request.command,
                cwd=request.cwd,
                env=request.env,
                input_text=request.stdin_text,
                timeout_seconds=request.timeout_seconds,
                cancel_event=request.cancel_event,
            )
        except Exception as exc:
            try:
                result = self._launch_failure_result(state, exc)
            except Exception as finalization_exc:
                self._record_finalization_failure(state, finalization_exc)
                raise
            with self._lock:
                state.execution_error = exc
                state.result = result
                state.completion_event.set()
            return result
        try:
            result = self._completed_process_result(state, completed)
        except Exception as exc:
            self._record_finalization_failure(state, exc)
            raise
        with self._lock:
            state.result = result
            state.completion_event.set()
        return result

    def _record_finalization_failure(self, state: _LocalExecutionState, error: Exception) -> None:
        with self._lock:
            state.execution_error = error
            state.failure_event = _runtime_failure_event(state.lease, error)
            state.completion_event.set()

    def _completed_process_result(
        self,
        state: _LocalExecutionState,
        completed: CancellableSubprocessResult,
    ) -> LocalRuntimeExecutionResult:
        endpoint = state.lease
        request = state.request
        cancelled = completed.termination_cause == "cancelled"
        timed_out = completed.termination_cause == "timed_out"
        stdin_requested = request.stdin_text is not None
        stdin_confirmed = completed.stdin_delivery == "confirmed"
        delivery_status = "CONFIRMED" if not stdin_requested or stdin_confirmed else "INDETERMINATE"
        delivery_errors = (
            () if delivery_status == "CONFIRMED" else ("local_runtime_stdin_delivery_unverified",)
        )
        submit = RuntimeSubmitReceipt(
            endpoint_lease_sha256=endpoint.sha256,
            work_order_sha256=endpoint.work_order_sha256,
            composer_state_before="NOT_APPLICABLE",
            text_delivery_count=1 if stdin_confirmed else 0,
            submit_attempt_count=1,
            composer_state_after="NOT_APPLICABLE",
            delivery_status=delivery_status,
            backend_acknowledgement=FrozenJson.from_value(
                {
                    "process_started": True,
                    "stdin_delivery": completed.stdin_delivery,
                }
            ),
            provider_execution_status="NOT_APPLICABLE",
            errors=delivery_errors,
        )
        state_name = (
            "EXITED"
            if completed.returncode == 0
            else "BLOCKED"
            if cancelled or timed_out
            else "CRASHED"
        )
        event = RuntimeEvent(
            event_id="local-event:" + endpoint.endpoint_id.removeprefix("local:"),
            run_id=endpoint.run_id,
            endpoint_lease_sha256=endpoint.sha256,
            event_type="RUNTIME_ENDPOINT_EXITED",
            observed_at=datetime.now(UTC).isoformat(),
            state=cast(RuntimeState, state_name),
            liveness="DEAD",
            confidence="PROCESS",
            source="local",
            observation=FrozenJson.from_value(
                {
                    "returncode": completed.returncode,
                    "timed_out": timed_out,
                    "cancelled": cancelled,
                }
            ),
        )
        capture = FrozenJson.from_value(
            {
                "schema": "tau.local_runtime_capture.v1",
                "command": list(request.command),
                "returncode": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "timed_out": timed_out,
                "cancelled": cancelled,
            }
        )
        paths = _write_runtime_artifacts(request.artifact_dir, endpoint, submit, event, capture)
        result = LocalRuntimeExecutionResult(
            args=list(request.command),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            termination_cause=completed.termination_cause,
            endpoint_lease=endpoint,
            submit_receipt=submit,
            runtime_event=event,
            capture=capture,
            artifact_paths=paths,
        )
        return result

    def _cancelled_before_submit_result(
        self, state: _LocalExecutionState
    ) -> LocalRuntimeExecutionResult:
        endpoint = state.lease
        request = state.request
        submit = RuntimeSubmitReceipt(
            endpoint_lease_sha256=endpoint.sha256,
            work_order_sha256=endpoint.work_order_sha256,
            composer_state_before="NOT_APPLICABLE",
            text_delivery_count=0,
            submit_attempt_count=0,
            composer_state_after="NOT_APPLICABLE",
            delivery_status="BLOCKED",
            backend_acknowledgement=FrozenJson.from_value(
                {"process_started": False, "reason": "termination_requested"}
            ),
            provider_execution_status="NOT_APPLICABLE",
            errors=("local_runtime_terminated_before_submit",),
        )
        event = RuntimeEvent(
            event_id="local-event:" + endpoint.endpoint_id.removeprefix("local:"),
            run_id=endpoint.run_id,
            endpoint_lease_sha256=endpoint.sha256,
            event_type="RUNTIME_ENDPOINT_TERMINATED",
            observed_at=datetime.now(UTC).isoformat(),
            state="BLOCKED",
            liveness="DEAD",
            confidence="PROCESS",
            source="local",
            observation=FrozenJson.from_value(
                {"returncode": 130, "timed_out": False, "cancelled": True, "process_started": False}
            ),
        )
        capture = FrozenJson.from_value(
            {
                "schema": "tau.local_runtime_capture.v1",
                "command": list(request.command),
                "returncode": 130,
                "stdout": "",
                "stderr": "local runtime terminated before submit",
                "timed_out": False,
                "cancelled": True,
            }
        )
        paths = _write_runtime_artifacts(request.artifact_dir, endpoint, submit, event, capture)
        return LocalRuntimeExecutionResult(
            args=list(request.command),
            returncode=130,
            stdout="",
            stderr="local runtime terminated before submit",
            termination_cause="cancelled",
            endpoint_lease=endpoint,
            submit_receipt=submit,
            runtime_event=event,
            capture=capture,
            artifact_paths=paths,
        )

    def _launch_failure_result(
        self, state: _LocalExecutionState, error: Exception
    ) -> LocalRuntimeExecutionResult:
        endpoint = state.lease
        request = state.request
        error_text = f"{type(error).__name__}: {error}"
        submit = RuntimeSubmitReceipt(
            endpoint_lease_sha256=endpoint.sha256,
            work_order_sha256=endpoint.work_order_sha256,
            composer_state_before="NOT_APPLICABLE",
            text_delivery_count=0,
            submit_attempt_count=1,
            composer_state_after="NOT_APPLICABLE",
            delivery_status="BLOCKED",
            backend_acknowledgement=FrozenJson.from_value(
                {"process_started": False, "reason": "process_launch_failed"}
            ),
            provider_execution_status="NOT_APPLICABLE",
            errors=("local_runtime_process_launch_failed",),
        )
        event = RuntimeEvent(
            event_id="local-event:" + endpoint.endpoint_id.removeprefix("local:"),
            run_id=endpoint.run_id,
            endpoint_lease_sha256=endpoint.sha256,
            event_type="RUNTIME_ENDPOINT_LAUNCH_FAILED",
            observed_at=datetime.now(UTC).isoformat(),
            state="CRASHED",
            liveness="DEAD",
            confidence="PROCESS",
            source="local",
            observation=FrozenJson.from_value({"process_started": False, "error": error_text}),
        )
        capture = FrozenJson.from_value(
            {
                "schema": "tau.local_runtime_capture.v1",
                "command": list(request.command),
                "returncode": 127,
                "stdout": "",
                "stderr": error_text,
                "timed_out": False,
                "cancelled": False,
                "launch_failed": True,
            }
        )
        paths = _write_runtime_artifacts(request.artifact_dir, endpoint, submit, event, capture)
        return LocalRuntimeExecutionResult(
            args=list(request.command),
            returncode=127,
            stdout="",
            stderr=error_text,
            termination_cause="launch_failed",
            endpoint_lease=endpoint,
            submit_receipt=submit,
            runtime_event=event,
            capture=capture,
            artifact_paths=paths,
        )

    def _completed_result(self, endpoint: RuntimeEndpointLease) -> LocalRuntimeExecutionResult:
        with self._lock:
            state = self._states.get(endpoint.sha256)
            result = state.result if state is not None else None
        if result is None:
            raise RuntimeError("local_runtime_endpoint_not_completed")
        return result


def _request_from_payload(payload: dict[str, Any]) -> LocalRuntimeExecutionRequest:
    command = payload.get("command")
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
        raise ValueError("local runtime command must be a string array")
    cwd = payload.get("cwd")
    env = payload.get("env")
    return LocalRuntimeExecutionRequest(
        run_id=str(payload["run_id"]),
        plan_revision=str(payload["plan_revision"]),
        dag_id=str(payload["dag_id"]),
        node_id=str(payload["node_id"]),
        attempt_id=str(payload["attempt_id"]),
        attempt_number=int(payload["attempt_number"]),
        execution_token=str(payload["execution_token"]),
        command=tuple(command),
        cwd=Path(cwd) if isinstance(cwd, str) else None,
        env={str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else None,
        stdin_text=str(payload["stdin_text"]) if payload.get("stdin_text") is not None else None,
        timeout_seconds=(
            float(payload["timeout_seconds"])
            if payload.get("timeout_seconds") is not None
            else None
        ),
        work_order_sha256=str(payload["work_order_sha256"]),
        goal_hash=str(payload["goal_hash"]),
        artifact_dir=(
            Path(payload["artifact_dir"]) if isinstance(payload.get("artifact_dir"), str) else None
        ),
    )


def _runtime_failure_event(endpoint: RuntimeEndpointLease, error: Exception) -> RuntimeEvent:
    return RuntimeEvent(
        event_id="local-failure:" + endpoint.endpoint_id.removeprefix("local:"),
        run_id=endpoint.run_id,
        endpoint_lease_sha256=endpoint.sha256,
        event_type="RUNTIME_EVIDENCE_FINALIZATION_FAILED",
        observed_at=datetime.now(UTC).isoformat(),
        state="CRASHED",
        liveness="DEAD",
        confidence="PROCESS",
        source="local",
        observation=FrozenJson.from_value({"error": f"{type(error).__name__}: {error}"}),
    )


def _write_runtime_artifacts(
    root: Path | None,
    lease: RuntimeEndpointLease,
    submit: RuntimeSubmitReceipt,
    event: RuntimeEvent,
    capture: FrozenJson,
) -> tuple[str, ...]:
    if root is None:
        return ()
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "runtime-endpoint-lease.json": lease.to_payload(),
        "runtime-submit-receipt.json": submit.to_payload(),
        "runtime-event.json": event.to_payload(),
        "runtime-capture.json": capture.to_value(),
    }
    paths = []
    for name, payload in payloads.items():
        path = root / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(str(path))
    return tuple(paths)


def local_runtime_request(
    *,
    command: Sequence[str],
    run_id: str,
    plan_revision: str,
    dag_id: str,
    node_id: str,
    attempt_id: str,
    attempt_number: int,
    execution_token: str,
    work_order: object,
    goal: object,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    timeout_seconds: float | None = None,
    artifact_dir: Path | None = None,
    cancel_event: Event | None = None,
) -> LocalRuntimeExecutionRequest:
    return LocalRuntimeExecutionRequest(
        run_id=run_id,
        plan_revision=_complete_hash(plan_revision),
        dag_id=dag_id,
        node_id=node_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        execution_token=execution_token,
        command=tuple(command),
        cwd=cwd,
        env=env,
        stdin_text=stdin_text,
        timeout_seconds=timeout_seconds,
        work_order_sha256=_complete_hash(work_order),
        goal_hash=_complete_hash(goal),
        artifact_dir=artifact_dir,
        cancel_event=cancel_event,
    )


def _complete_hash(value: object) -> str:
    if isinstance(value, str):
        candidate = value.removeprefix("sha256:")
        if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
            return "sha256:" + candidate
    return canonical_sha256(value)


__all__ = [
    "LocalRuntimeBackend",
    "LocalRuntimeExecutionRequest",
    "LocalRuntimeExecutionResult",
    "local_runtime_request",
]
