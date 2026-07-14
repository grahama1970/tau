"""Herdr implementation of Tau's interactive runtime backend contract."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import (
    RuntimeCapabilities,
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeLiveness,
    RuntimeState,
    RuntimeSubmitReceipt,
)
from tau_coding.runtime_backends.herdr_native_events import (
    HerdrNativeEventError,
    HerdrNativeEventTransport,
    discover_herdr_native_event_transport,
)

HERDR_CLEANUP_AUTHORIZATION_SCHEMA = "tau.runtime_cleanup_authorization.v1"

_CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class HerdrRuntimeScope:
    run_id: str
    owner: str
    session: str
    workspace_id: str
    tab_id: str
    cwd: Path
    label: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "backend": "herdr",
            "run_id": self.run_id,
            "owner": self.owner,
            "backend_session_id": self.session,
            "scope_id": self.workspace_id,
            "workspace_id": self.workspace_id,
            "tab_id": self.tab_id,
            "cwd": str(self.cwd),
            "label": self.label,
        }


@dataclass(slots=True)
class _HerdrEndpointState:
    lease: RuntimeEndpointLease
    scope: HerdrRuntimeScope
    submit_receipt: RuntimeSubmitReceipt | None = None
    last_event: RuntimeEvent | None = None
    terminated: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class HerdrRuntimeBackend:
    """Manage Tau-owned interactive endpoints through an explicit Herdr session."""

    def __init__(
        self,
        *,
        session: str,
        herdr_bin: str = "herdr",
        command_runner: _CommandRunner = subprocess.run,
        poll_interval_seconds: float = 0.1,
        command_timeout_seconds: float = 10.0,
        native_event_transport: HerdrNativeEventTransport | None = None,
    ) -> None:
        if not session.strip():
            raise ValueError("herdr runtime requires an explicit session")
        if not herdr_bin.strip():
            raise ValueError("herdr_bin must not be empty")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        self._session = session
        self._herdr_bin = herdr_bin
        self._command_runner = command_runner
        self._poll_interval_seconds = poll_interval_seconds
        self._command_timeout_seconds = command_timeout_seconds
        self._native_event_transport = native_event_transport
        self._native_event_discovery_error: str | None = None
        if native_event_transport is None and command_runner is subprocess.run:
            (
                self._native_event_transport,
                self._native_event_discovery_error,
            ) = discover_herdr_native_event_transport(
                session=session,
                herdr_bin=herdr_bin,
                command_runner=command_runner,
                timeout_seconds=command_timeout_seconds,
            )
        self._scopes: dict[str, HerdrRuntimeScope] = {}
        self._scope_ids: dict[str, str] = {}
        self._endpoints: dict[str, _HerdrEndpointState] = {}
        self._endpoint_ids: dict[str, str] = {}
        self._attempt_ids: set[str] = set()
        self._lock = threading.Lock()

    def capabilities(self) -> RuntimeCapabilities:
        native_events = self._native_event_transport is not None
        return RuntimeCapabilities(
            backend="herdr",
            version=(
                f"tau-herdr-runtime-v1+native-"
                f"{self._native_event_transport.server_version}-protocol-"
                f"{self._native_event_transport.protocol}-binding-"
                f"{self._native_event_transport.binding_sha256.removeprefix('sha256:')[:16]}"
                if self._native_event_transport is not None
                else "tau-herdr-runtime-v1"
            ),
            interactive=True,
            one_shot=False,
            native_events=native_events,
            native_agent_state=True,
            foreground_process_state=True,
            structured_composer_state=False,
            stable_endpoint_id=True,
            human_attach=True,
            supports_working_directory=True,
            supports_owned_inventory=True,
            supports_terminate=True,
            observation_confidence_levels=("NATIVE", "PROCESS", "HEURISTIC", "UNKNOWN"),
            supported_session_scopes=("node_attempt", "persistent_subagent"),
            unsupported_requirements=(
                ("structured_composer_state",)
                if native_events
                else ("native_events", "structured_composer_state")
            ),
        )

    def ensure_scope(self, request: FrozenJson) -> FrozenJson:
        payload = _object_payload(request, "herdr runtime scope request")
        run_id = _required_string(payload, "run_id")
        owner = _required_string(payload, "owner")
        cwd = _existing_directory(payload, "cwd")
        requested_label = _required_string(payload, "label")
        label = _owned_label(requested_label, run_id)
        with self._lock:
            existing = self._scopes.get(run_id)
            if existing is not None:
                if existing.owner != owner or existing.cwd != cwd:
                    raise RuntimeError("herdr_runtime_scope_binding_mismatch")
                return FrozenJson.from_value(existing.to_payload())

        workspace = self._run_json(
            "workspace",
            "create",
            "--cwd",
            str(cwd),
            "--label",
            label,
            "--no-focus",
        )
        workspace_id = _find_required_id(workspace, "workspace_id")
        try:
            tab = self._run_json(
                "tab",
                "create",
                "--workspace",
                workspace_id,
                "--cwd",
                str(cwd),
                "--label",
                "tau-agents",
                "--no-focus",
            )
            tab_id = _find_required_id(tab, "tab_id")
        except Exception:
            self._run_json("workspace", "close", workspace_id, check=False)
            raise
        scope = HerdrRuntimeScope(
            run_id=run_id,
            owner=owner,
            session=self._session,
            workspace_id=workspace_id,
            tab_id=tab_id,
            cwd=cwd,
            label=label,
        )
        with self._lock:
            if run_id in self._scopes or workspace_id in self._scope_ids:
                self._run_json("workspace", "close", workspace_id, check=False)
                raise RuntimeError("herdr_runtime_scope_already_exists")
            self._scopes[run_id] = scope
            self._scope_ids[workspace_id] = run_id
        return FrozenJson.from_value(scope.to_payload())

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease:
        payload = _object_payload(request, "herdr runtime spawn request")
        run_id = _required_string(payload, "run_id")
        scope_id = _required_string(payload, "scope_id")
        with self._lock:
            scope = self._scopes.get(run_id)
        if scope is None or scope.workspace_id != scope_id:
            raise RuntimeError("herdr_runtime_scope_unknown")
        command = _string_sequence(payload, "command")
        cwd = _existing_directory(payload, "cwd")
        if cwd != scope.cwd:
            raise RuntimeError("herdr_runtime_spawn_cwd_outside_scope")
        owner = _required_string(payload, "owner")
        if owner != scope.owner:
            raise RuntimeError("herdr_runtime_owner_mismatch")
        attempt_number = _required_int(payload, "attempt_number", minimum=1)
        attempt_id = _required_string(payload, "attempt_id")
        node_id = _required_string(payload, "node_id")
        plan_revision = _required_string(payload, "plan_revision")
        dag_id = _required_string(payload, "dag_id")
        execution_token = _required_string(payload, "execution_token")
        work_order_sha256 = _required_sha256(payload, "work_order_sha256")
        goal_hash = _required_sha256(payload, "goal_hash")
        lease_seconds = _optional_positive_float(
            payload.get("lease_seconds"), default=3600.0
        )
        environment = _environment_sequence(payload.get("environment"))
        endpoint_label = _owned_label(
            str(payload.get("label") or node_id),
            f"{run_id}-{attempt_id}",
        )
        attempt_identity = canonical_sha256(
            {
                "run_id": run_id,
                "plan_revision": plan_revision,
                "dag_id": dag_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "attempt_number": attempt_number,
                "execution_token": execution_token,
                "scope_id": scope_id,
            }
        )
        with self._lock:
            if attempt_identity in self._attempt_ids:
                raise RuntimeError("herdr_runtime_attempt_already_spawned")
            self._attempt_ids.add(attempt_identity)
        args = [
            "agent",
            "start",
            endpoint_label,
            "--cwd",
            str(cwd),
            "--workspace",
            scope.workspace_id,
            "--tab",
            scope.tab_id,
        ]
        for item in environment:
            args.extend(("--env", item))
        args.extend(("--no-focus", "--", *command))
        started = self._run_json(*args)
        try:
            agent = _find_agent(started, endpoint_label)
        except ValueError:
            recoverable = [
                candidate
                for candidate in _find_objects(started, "agent")
                if isinstance(candidate.get("pane_id"), str) and candidate.get("pane_id")
            ]
            if len(recoverable) == 1:
                self._reclaim_failed_spawn(
                    recoverable[0],
                    expected_workspace_id=scope.workspace_id,
                    expected_agent_name=endpoint_label,
                )
            raise RuntimeError("herdr_runtime_spawn_agent_mismatch") from None
        try:
            workspace_id = _required_string(agent, "workspace_id")
            pane_id = _required_string(agent, "pane_id")
            terminal_id = _required_string(agent, "terminal_id")
        except ValueError:
            self._reclaim_failed_spawn(
                agent,
                expected_workspace_id=scope.workspace_id,
                expected_agent_name=endpoint_label,
            )
            raise
        if workspace_id != scope.workspace_id:
            self._reclaim_failed_spawn(
                agent,
                expected_workspace_id=scope.workspace_id,
                expected_agent_name=endpoint_label,
            )
            raise RuntimeError("herdr_runtime_spawn_workspace_mismatch")
        now = datetime.now(UTC)
        lease = RuntimeEndpointLease(
            run_id=run_id,
            plan_revision=plan_revision,
            dag_id=dag_id,
            node_id=node_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            execution_token=execution_token,
            backend="herdr",
            backend_session_id=self._session,
            scope_id=scope.workspace_id,
            endpoint_id=pane_id,
            work_order_sha256=work_order_sha256,
            goal_hash=goal_hash,
            owner=owner,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
            heartbeat_policy=FrozenJson.from_value(
                {
                    "kind": (
                        "native_event_with_bounded_poll_fallback"
                        if self._native_event_transport is not None
                        else "bounded_poll"
                    ),
                    "interval_seconds": self._poll_interval_seconds,
                    "native_discovery_error": self._native_event_discovery_error,
                }
            ),
            cleanup_policy=FrozenJson.from_value(
                {
                    "kind": "exact_endpoint_authorization",
                    "workspace_cleanup": "delegated_to_tau_herdr_cleanup",
                }
            ),
            capabilities_sha256=self.capabilities().sha256,
            backend_ids=FrozenJson.from_value(
                {
                    "session": self._session,
                    "workspace_id": scope.workspace_id,
                    "tab_id": scope.tab_id,
                    "pane_id": pane_id,
                    "terminal_id": terminal_id,
                    "agent_name": endpoint_label,
                }
            ),
        )
        state = _HerdrEndpointState(lease=lease, scope=scope)
        with self._lock:
            if pane_id in self._endpoint_ids:
                raise RuntimeError("herdr_runtime_endpoint_already_exists")
            self._endpoints[lease.sha256] = state
            self._endpoint_ids[pane_id] = lease.sha256
        return lease

    def _reclaim_failed_spawn(
        self,
        agent: Mapping[str, Any],
        *,
        expected_workspace_id: str,
        expected_agent_name: str,
    ) -> None:
        pane_id = agent.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            return
        observed_before = self._run_json("pane", "get", pane_id, check=False)
        try:
            observed_pane = _find_object(observed_before, "pane")
        except ValueError as exc:
            raise RuntimeError("herdr_runtime_failed_spawn_ownership_not_verified") from exc
        observed_agent = observed_pane.get("agent") or observed_pane.get("name")
        if (
            observed_before.get("returncode") != 0
            or observed_pane.get("pane_id") != pane_id
            or observed_pane.get("workspace_id") != expected_workspace_id
            or observed_agent != expected_agent_name
        ):
            raise RuntimeError("herdr_runtime_failed_spawn_ownership_not_verified")
        self._run_json("pane", "close", pane_id, check=False)
        observed = self._run_json("pane", "get", pane_id, check=False)
        if observed.get("returncode") == 0 or _herdr_error_code(observed) != "pane_not_found":
            raise RuntimeError("herdr_runtime_failed_spawn_cleanup_not_verified")

    def submit(
        self, endpoint: RuntimeEndpointLease, work_order: FrozenJson
    ) -> RuntimeSubmitReceipt:
        state = self._state(endpoint)
        payload = _object_payload(work_order, "herdr runtime work order")
        if _required_sha256(payload, "work_order_sha256") != endpoint.work_order_sha256:
            raise ValueError("herdr runtime work order does not match endpoint lease")
        text = _required_string(payload, "text")
        if not text.endswith("\n"):
            text += "\n"
        with state.lock:
            if state.submit_receipt is not None:
                return state.submit_receipt
            result = self._run_json("pane", "send-text", endpoint.endpoint_id, text, check=False)
            ok = result["returncode"] == 0
            receipt = RuntimeSubmitReceipt(
                endpoint_lease_sha256=endpoint.sha256,
                work_order_sha256=endpoint.work_order_sha256,
                composer_state_before="UNKNOWN",
                text_delivery_count=1 if ok else 0,
                submit_attempt_count=1,
                composer_state_after="UNKNOWN",
                delivery_status="CONFIRMED" if ok else "INDETERMINATE",
                backend_acknowledgement=FrozenJson.from_value(
                    {
                        "session": self._session,
                        "pane_id": endpoint.endpoint_id,
                        "returncode": result["returncode"],
                        "response": result["payload"],
                    }
                ),
                provider_execution_status="NOT_OBSERVED",
                errors=() if ok else ("herdr_runtime_input_delivery_unverified",),
            )
            state.submit_receipt = receipt
            return receipt

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson:
        self._state(endpoint)
        if lines < 0:
            raise ValueError("capture lines must be non-negative")
        result = self._run_json(
            "pane",
            "read",
            endpoint.endpoint_id,
            "--source",
            "visible",
            "--lines",
            str(lines),
            check=False,
        )
        return FrozenJson.from_value(
            {
                "backend": "herdr",
                "backend_session_id": self._session,
                "endpoint_id": endpoint.endpoint_id,
                "returncode": result["returncode"],
                "text": result["stdout"],
                "diagnostic_only": True,
                "errors": [] if result["returncode"] == 0 else ["herdr_runtime_capture_failed"],
            }
        )

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent:
        return self._observe(endpoint)

    def _observe(
        self, endpoint: RuntimeEndpointLease, *, deadline: datetime | None = None
    ) -> RuntimeEvent:
        state = self._state(endpoint)
        pane_result = self._run_observation_json(
            deadline, "pane", "get", endpoint.endpoint_id
        )
        if pane_result["returncode"] != 0:
            error_code = _herdr_error_code(pane_result)
            pane_absent = error_code == "pane_not_found"
            event = _runtime_event(
                endpoint,
                state="CRASHED" if pane_absent else "UNKNOWN",
                liveness="DEAD" if pane_absent else "UNKNOWN",
                confidence="NATIVE" if pane_absent else "UNKNOWN",
                observation={
                    "session": self._session,
                    "pane_id": endpoint.endpoint_id,
                    "pane_present": False,
                    "error_code": error_code,
                    "pane_get": pane_result["payload"],
                },
            )
            state.last_event = event
            return event
        try:
            pane = _find_object(pane_result["payload"], "pane")
        except ValueError:
            event = _runtime_event(
                endpoint,
                state="UNKNOWN",
                liveness="UNKNOWN",
                confidence="UNKNOWN",
                observation={
                    "session": self._session,
                    "pane_id": endpoint.endpoint_id,
                    "pane_present": "UNKNOWN",
                    "error_code": "malformed_pane_response",
                },
            )
            state.last_event = event
            return event
        if _optional_string(pane.get("workspace_id")) != endpoint.scope_id:
            raise RuntimeError("herdr_runtime_endpoint_scope_mismatch")
        process_result = self._run_observation_json(
            deadline, "pane", "process-info", "--pane", endpoint.endpoint_id
        )
        visible_result = self._run_observation_json(
            deadline,
            "pane",
            "read",
            endpoint.endpoint_id,
            "--source",
            "visible",
            "--lines",
            "80",
        )
        visible_text = visible_result["stdout"] if visible_result["returncode"] == 0 else ""
        process_info_ok = process_result["returncode"] == 0
        process_error_code: str | None = None
        if process_info_ok:
            try:
                process = _find_object(process_result["payload"], "process_info")
            except ValueError:
                process = {}
                process_info_ok = False
                process_error_code = "malformed_process_info_response"
        else:
            process = {}
            process_error_code = _herdr_error_code(process_result)
        foreground = process.get("foreground_processes")
        processes = foreground if isinstance(foreground, list) else []
        observed_state, liveness, confidence = _classify_observation(
            pane=pane,
            processes=processes,
            process_info_ok=process_info_ok,
        )
        visible_diagnostics = _visible_text_diagnostics(visible_text)
        event = _runtime_event(
            endpoint,
            state=observed_state,
            liveness=liveness,
            confidence=confidence,
            observation={
                "session": self._session,
                "workspace_id": endpoint.scope_id,
                "pane_id": endpoint.endpoint_id,
                "terminal_id": endpoint.backend_ids.to_value().get("terminal_id"),
                "agent_status": pane.get("agent_status"),
                "processes": processes,
                "process_info_error_code": process_error_code,
                "visible_text_sha256": canonical_sha256(visible_text),
                "visible_text_diagnostic_only": True,
                **visible_diagnostics,
            },
        )
        state.last_event = event
        return event

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEvent | None:
        self._state(endpoint)
        if self._native_event_transport is not None:
            try:
                return self._native_event_transport.wait_event(endpoint, deadline)
            except HerdrNativeEventError as exc:
                return self._wait_event_poll(
                    endpoint,
                    cursor,
                    deadline,
                    native_fallback_code=exc.code,
                )
        return self._wait_event_poll(endpoint, cursor, deadline)

    def _wait_event_poll(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
        *,
        native_fallback_code: str | None = None,
    ) -> RuntimeEvent | None:
        while True:
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return None
            event = self._observe(endpoint, deadline=deadline)
            if datetime.now(UTC) >= deadline:
                return None
            if native_fallback_code is not None:
                observation = event.observation.to_value()
                observation["native_event_fallback"] = {
                    "code": native_fallback_code,
                    "polling_used": True,
                }
                fallback_digest = canonical_sha256(
                    {
                        "poll_event_id": event.event_id,
                        "native_event_fallback": native_fallback_code,
                    }
                ).removeprefix("sha256:")
                event = RuntimeEvent(
                    event_id=f"herdr:{fallback_digest}",
                    run_id=event.run_id,
                    endpoint_lease_sha256=event.endpoint_lease_sha256,
                    event_type=event.event_type,
                    observed_at=event.observed_at,
                    state=event.state,
                    liveness=event.liveness,
                    confidence=event.confidence,
                    source=event.source,
                    observation=FrozenJson.from_value(observation),
                )
            if event.event_id != cursor:
                return event
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return None
            time.sleep(min(self._poll_interval_seconds, remaining))

    def _run_observation_json(self, deadline: datetime | None, *args: str) -> dict[str, Any]:
        timeout_seconds = self._command_timeout_seconds
        if deadline is not None:
            timeout_seconds = min(
                timeout_seconds,
                max((deadline - datetime.now(UTC)).total_seconds(), 0.0),
            )
        return self._run_json(
            *args,
            check=False,
            timeout_seconds=timeout_seconds,
        )

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]:
        with self._lock:
            return [
                state.lease
                for state in self._endpoints.values()
                if state.lease.run_id == run_id and not state.terminated
            ]

    def terminate(
        self, endpoint: RuntimeEndpointLease, authorization: FrozenJson
    ) -> FrozenJson:
        state = self._state(endpoint)
        payload = _object_payload(authorization, "herdr runtime cleanup authorization")
        _validate_cleanup_authorization(payload, endpoint)
        with state.lock:
            if state.terminated:
                return FrozenJson.from_value(
                    {
                        "status": "PASS",
                        "action": "already_absent",
                        "endpoint_id": endpoint.endpoint_id,
                        "post_verified_absent": True,
                    }
                )
            close = self._run_json("pane", "close", endpoint.endpoint_id, check=False)
            verify = self._run_json("pane", "get", endpoint.endpoint_id, check=False)
            verify_error_code = _herdr_error_code(verify)
            absent = verify["returncode"] != 0 and verify_error_code == "pane_not_found"
            if not absent:
                return FrozenJson.from_value(
                    {
                        "status": "BLOCKED",
                        "action": "endpoint_close",
                        "endpoint_id": endpoint.endpoint_id,
                        "close_returncode": close["returncode"],
                        "post_verify_returncode": verify["returncode"],
                        "post_verify_error_code": verify_error_code,
                        "post_verified_absent": absent,
                        "errors": ["herdr_runtime_endpoint_termination_not_verified"],
                    }
                )
            state.terminated = True
            return FrozenJson.from_value(
                {
                    "status": "PASS",
                    "action": (
                        "endpoint_close" if close["returncode"] == 0 else "already_absent"
                    ),
                    "endpoint_id": endpoint.endpoint_id,
                    "close_returncode": close["returncode"],
                    "post_verify_returncode": verify["returncode"],
                    "post_verify_error_code": verify_error_code,
                    "post_verified_absent": True,
                }
            )

    def _state(self, endpoint: RuntimeEndpointLease) -> _HerdrEndpointState:
        if endpoint.backend != "herdr" or endpoint.backend_session_id != self._session:
            raise RuntimeError("herdr_runtime_endpoint_session_mismatch")
        with self._lock:
            state = self._endpoints.get(endpoint.sha256)
        if state is None or state.lease != endpoint:
            raise RuntimeError("herdr_runtime_endpoint_unknown")
        return state

    def _run_json(
        self,
        *args: str,
        check: bool = True,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        argv = [self._herdr_bin, "--session", self._session, *args]
        effective_timeout = (
            self._command_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if effective_timeout <= 0:
            if check:
                raise RuntimeError("herdr_runtime_command_timeout:" + " ".join(args[:2]))
            return {
                "argv": argv,
                "returncode": 124,
                "stdout": "",
                "stderr": "deadline expired before Herdr command",
                "payload": None,
                "timed_out": True,
            }
        try:
            completed = self._command_runner(
                argv,
                text=True,
                capture_output=True,
                check=False,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            if check:
                raise RuntimeError(
                    "herdr_runtime_command_timeout:" + " ".join(args[:2])
                ) from exc
            return {
                "argv": argv,
                "returncode": 124,
                "stdout": str(exc.stdout or ""),
                "stderr": str(exc.stderr or ""),
                "payload": None,
                "timed_out": True,
            }
        except OSError as exc:
            if check:
                raise RuntimeError(
                    "herdr_runtime_command_launch_failed:" + " ".join(args[:2])
                ) from exc
            return {
                "argv": argv,
                "returncode": 126,
                "stdout": "",
                "stderr": str(exc),
                "payload": None,
                "launch_error": type(exc).__name__,
            }
        payload: Any = None
        if completed.stdout.strip():
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                payload = completed.stdout.strip()
        result = {
            "argv": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "payload": payload,
        }
        if check and completed.returncode != 0:
            raise RuntimeError(
                "herdr_runtime_command_failed:"
                + " ".join(args[:2])
                + f":exit={completed.returncode}"
            )
        return result


def herdr_runtime_scope_request(
    *, run_id: str, owner: str, cwd: Path, label: str
) -> FrozenJson:
    return FrozenJson.from_value(
        {"run_id": run_id, "owner": owner, "cwd": str(cwd), "label": label}
    )


def herdr_runtime_spawn_request(
    *,
    run_id: str,
    plan_revision: str,
    dag_id: str,
    node_id: str,
    attempt_id: str,
    attempt_number: int,
    execution_token: str,
    scope_id: str,
    command: Sequence[str],
    cwd: Path,
    work_order_sha256: str,
    goal_hash: str,
    owner: str,
    label: str | None = None,
    environment: Mapping[str, str] | None = None,
    lease_seconds: float = 3600.0,
) -> FrozenJson:
    return FrozenJson.from_value(
        {
            "run_id": run_id,
            "plan_revision": plan_revision,
            "dag_id": dag_id,
            "node_id": node_id,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "execution_token": execution_token,
            "scope_id": scope_id,
            "command": list(command),
            "cwd": str(cwd),
            "work_order_sha256": work_order_sha256,
            "goal_hash": goal_hash,
            "owner": owner,
            "label": label,
            "environment": dict(environment or {}),
            "lease_seconds": lease_seconds,
        }
    )


def herdr_runtime_work_order(*, work_order_sha256: str, text: str) -> FrozenJson:
    return FrozenJson.from_value(
        {"work_order_sha256": work_order_sha256, "text": text}
    )


def herdr_cleanup_authorization(endpoint: RuntimeEndpointLease) -> FrozenJson:
    return FrozenJson.from_value(
        {
            "schema": HERDR_CLEANUP_AUTHORIZATION_SCHEMA,
            "approved": True,
            "action": "terminate_endpoint",
            "run_id": endpoint.run_id,
            "owner": endpoint.owner,
            "backend_session_id": endpoint.backend_session_id,
            "scope_id": endpoint.scope_id,
            "endpoint_id": endpoint.endpoint_id,
            "endpoint_lease_sha256": endpoint.sha256,
        }
    )


def _runtime_event(
    endpoint: RuntimeEndpointLease,
    *,
    state: RuntimeState,
    liveness: RuntimeLiveness,
    confidence: str,
    observation: dict[str, Any],
) -> RuntimeEvent:
    observation_payload = FrozenJson.from_value(observation)
    digest = canonical_sha256(
        {
            "endpoint_lease_sha256": endpoint.sha256,
            "state": state,
            "liveness": liveness,
            "confidence": confidence,
            "observation": observation,
        }
    ).removeprefix("sha256:")
    return RuntimeEvent(
        event_id=f"herdr:{digest}",
        run_id=endpoint.run_id,
        endpoint_lease_sha256=endpoint.sha256,
        event_type="RUNTIME_OBSERVATION_RECORDED",
        observed_at=datetime.now(UTC).isoformat(),
        state=state,
        liveness=liveness,
        confidence=cast(Any, confidence),
        source="herdr",
        observation=observation_payload,
    )


def _classify_observation(
    *,
    pane: dict[str, Any],
    processes: list[Any],
    process_info_ok: bool,
) -> tuple[RuntimeState, RuntimeLiveness, str]:
    if not process_info_ok:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN"
    if not processes:
        return "UNKNOWN", "UNKNOWN", "PROCESS"
    status = str(pane.get("agent_status") or "unknown").lower()
    state_by_status: dict[str, RuntimeState] = {
        "working": "RUNNING",
        "idle": "READY",
        "blocked": "BLOCKED",
        "done": "EXITED",
        "unknown": "UNKNOWN",
    }
    return state_by_status.get(status, "UNKNOWN"), "ALIVE", "NATIVE"


def _visible_text_diagnostics(visible_text: str) -> dict[str, bool]:
    lowered = visible_text.lower()
    return {
        "visible_auth_marker": any(
            marker in lowered for marker in ("login required", "not authenticated", "sign in")
        ),
        "visible_interstitial_marker": any(
            marker in lowered for marker in ("hooks need review", "update available")
        ),
    }


def _validate_cleanup_authorization(
    payload: dict[str, Any], endpoint: RuntimeEndpointLease
) -> None:
    expected = {
        "schema": HERDR_CLEANUP_AUTHORIZATION_SCHEMA,
        "approved": True,
        "action": "terminate_endpoint",
        "run_id": endpoint.run_id,
        "owner": endpoint.owner,
        "backend_session_id": endpoint.backend_session_id,
        "scope_id": endpoint.scope_id,
        "endpoint_id": endpoint.endpoint_id,
        "endpoint_lease_sha256": endpoint.sha256,
    }
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    if mismatches:
        raise RuntimeError("herdr_runtime_cleanup_unauthorized:" + ",".join(mismatches))


def _herdr_error_code(result: Mapping[str, Any]) -> str | None:
    for source in (result.get("payload"), result.get("stdout"), result.get("stderr")):
        payload = source
        if isinstance(source, str) and source.strip():
            try:
                payload = json.loads(source)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code
    return None


def _owned_label(label: str, identity: str) -> str:
    normalized = "".join(ch if ch.isalnum() else " " for ch in label.lower())
    slug = "-".join(filter(None, normalized.split()))
    suffix = canonical_sha256(identity).removeprefix("sha256:")[:12]
    prefix = "tau-"
    separator = "-"
    available_slug_length = 80 - len(prefix) - len(separator) - len(suffix)
    bounded_slug = (slug or "runtime")[:available_slug_length].rstrip("-") or "runtime"
    return f"{prefix}{bounded_slug}{separator}{suffix}"


def _object_payload(value: FrozenJson, label: str) -> dict[str, Any]:
    payload = value.to_value()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_int(payload: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = payload.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{key} must be an integer >= {minimum}")
    return value


def _required_sha256(payload: Mapping[str, Any], key: str) -> str:
    value = _required_string(payload, key)
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{key} must be a complete sha256 digest")
    try:
        int(value.removeprefix("sha256:"), 16)
    except ValueError as exc:
        raise ValueError(f"{key} must be a complete sha256 digest") from exc
    return value


def _string_sequence(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{key} must contain non-empty strings")
    return tuple(value)


def _environment_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError("environment must be an object")
    result = []
    for key, item in sorted(value.items()):
        if not isinstance(key, str) or not key or not isinstance(item, str):
            raise ValueError("environment entries must be string pairs")
        result.append(f"{key}={item}")
    return tuple(result)


def _existing_directory(payload: Mapping[str, Any], key: str) -> Path:
    path = Path(_required_string(payload, key)).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{key} must name an existing directory")
    return path


def _optional_positive_float(value: Any, *, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError("lease_seconds must be positive")
    return float(value)


def _find_required_id(payload: Any, key: str) -> str:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        for item in payload.values():
            try:
                return _find_required_id(item, key)
            except ValueError:
                pass
    elif isinstance(payload, list):
        for item in payload:
            try:
                return _find_required_id(item, key)
            except ValueError:
                pass
    raise ValueError(f"Herdr response is missing {key}")


def _find_object(payload: Any, key: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        for item in payload.values():
            try:
                return _find_object(item, key)
            except ValueError:
                pass
    raise ValueError(f"Herdr response is missing {key}")


def _find_objects(payload: Any, key: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, dict):
            matches.append(value)
        for item in payload.values():
            matches.extend(_find_objects(item, key))
    elif isinstance(payload, list):
        for item in payload:
            matches.extend(_find_objects(item, key))
    return matches


def _find_agent(payload: Any, expected_name: str) -> dict[str, Any]:
    for agent in _find_objects(payload, "agent"):
        if agent.get("agent") == expected_name or agent.get("name") == expected_name:
            return agent
    raise ValueError(f"Herdr response is missing expected agent {expected_name}")


__all__ = [
    "HERDR_CLEANUP_AUTHORIZATION_SCHEMA",
    "HerdrRuntimeBackend",
    "HerdrRuntimeScope",
    "herdr_cleanup_authorization",
    "herdr_runtime_scope_request",
    "herdr_runtime_spawn_request",
    "herdr_runtime_work_order",
]
