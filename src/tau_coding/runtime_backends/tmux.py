"""Tmux implementation of Tau's interactive runtime backend contract."""

from __future__ import annotations

import fcntl
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
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

TMUX_CLEANUP_AUTHORIZATION_SCHEMA = "tau.runtime_cleanup_authorization.v1"
_PANE_FORMAT = (
    "#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_dead}\t"
    "#{pane_pid}\t#{pane_current_command}\t#{pane_dead_status}\t#{pane_dead_signal}"
)
_SCOPE_FORMAT = "#{session_id}\t#{session_name}\t#{window_id}\t#{pane_id}"
_SPAWN_RECONCILE_FORMAT = (
    "#{session_id}\t#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}\t"
    "#{pane_start_command}"
)
_SERVER_FORMAT = "#{socket_path}\t#{pid}\t#{start_time}\t#{version}"
_SERVER_SESSION_FORMAT = "#{session_id}\t#{session_name}"
_OWNER_TOKEN_VARIABLE = "TAU_TMUX_OWNER_TOKEN"
_ATTEMPT_TOKEN_VARIABLE = "TAU_TMUX_ATTEMPT_TOKEN"

_CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class TmuxRuntimeScope:
    run_id: str
    owner: str
    server_name: str
    socket_root: Path
    socket_path: str
    server_pid: int
    server_start_time: int
    server_version: str
    server_incarnation_sha256: str
    session_id: str
    session_name: str
    control_window_id: str
    control_pane_id: str
    cwd: Path
    label: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "backend": "tmux",
            "run_id": self.run_id,
            "owner": self.owner,
            "backend_session_id": self.server_incarnation_sha256,
            "socket_root": str(self.socket_root),
            "socket_path": self.socket_path,
            "server_pid": self.server_pid,
            "server_start_time": self.server_start_time,
            "server_version": self.server_version,
            "server_incarnation_sha256": self.server_incarnation_sha256,
            "scope_id": self.session_id,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "control_window_id": self.control_window_id,
            "control_pane_id": self.control_pane_id,
            "cwd": str(self.cwd),
            "label": self.label,
        }


@dataclass(slots=True)
class _TmuxEndpointState:
    lease: RuntimeEndpointLease
    scope: TmuxRuntimeScope
    submit_receipt: RuntimeSubmitReceipt | None = None
    last_event: RuntimeEvent | None = None
    terminated: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class TmuxRuntimeBackend:
    """Manage Tau-owned interactive panes through one explicit tmux server."""

    def __init__(
        self,
        *,
        server_name: str,
        tmux_bin: str = "tmux",
        command_runner: _CommandRunner = subprocess.run,
        poll_interval_seconds: float = 0.1,
        command_timeout_seconds: float = 10.0,
        max_capture_bytes: int = 65_536,
        socket_root: Path | None = None,
    ) -> None:
        if not server_name.strip():
            raise ValueError("tmux runtime requires an explicit server name")
        if re.fullmatch(r"[A-Za-z0-9_.-]+", server_name) is None:
            raise ValueError("tmux server name contains unsupported characters")
        if not tmux_bin.strip():
            raise ValueError("tmux_bin must not be empty")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        if max_capture_bytes < 1:
            raise ValueError("max_capture_bytes must be positive")
        self._server_name = server_name
        self._owner_token = secrets.token_hex(32)
        self._tmux_bin = tmux_bin
        self._command_runner = command_runner
        self._poll_interval_seconds = poll_interval_seconds
        self._command_timeout_seconds = command_timeout_seconds
        self._max_capture_bytes = max_capture_bytes
        self._socket_root = (
            socket_root.expanduser().resolve()
            if socket_root is not None
            else Path(os.environ.get("TMUX_TMPDIR", "/tmp")).expanduser().resolve()
        )
        if not self._socket_root.is_dir():
            raise ValueError("tmux socket_root must name an existing directory")
        self._command_environment = dict(os.environ)
        self._command_environment["TMUX_TMPDIR"] = str(self._socket_root)
        self._server_incarnation: dict[str, Any] | None = None
        self._scopes: dict[str, TmuxRuntimeScope] = {}
        self._scope_ids: dict[str, str] = {}
        self._endpoints: dict[str, _TmuxEndpointState] = {}
        self._endpoint_ids: dict[str, str] = {}
        self._attempt_ids: set[str] = set()
        self._lock = threading.Lock()
        self._scope_creation_lock = threading.Lock()

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend="tmux",
            version="tau-tmux-runtime-v1",
            interactive=True,
            one_shot=False,
            native_events=False,
            native_agent_state=False,
            foreground_process_state=True,
            structured_composer_state=False,
            stable_endpoint_id=True,
            human_attach=True,
            supports_working_directory=True,
            supports_owned_inventory=True,
            supports_terminate=True,
            observation_confidence_levels=("NATIVE", "PROCESS", "UNKNOWN"),
            supported_session_scopes=("node_attempt", "persistent_subagent"),
            unsupported_requirements=(
                "native_agent_state",
                "native_events",
                "structured_composer_state",
            ),
        )

    def ensure_scope(self, request: FrozenJson) -> FrozenJson:
        with self._scope_creation_lock:
            return self._ensure_scope(request)

    def _ensure_scope(self, request: FrozenJson) -> FrozenJson:
        payload = _object_payload(request, "tmux runtime scope request")
        run_id = _required_string(payload, "run_id")
        owner = _required_string(payload, "owner")
        cwd = _existing_directory(payload, "cwd")
        requested_label = _required_string(payload, "label")
        session_name = _owned_name(requested_label, run_id)
        with self._lock:
            existing = self._scopes.get(run_id)
            if existing is not None:
                if existing.owner != owner or existing.cwd != cwd:
                    raise RuntimeError("tmux_runtime_scope_binding_mismatch")
                self._require_server_incarnation(existing)
                return FrozenJson.from_value(existing.to_payload())

        first_scope = self._server_incarnation is None
        guard = self._server_creation_guard() if first_scope else nullcontext()
        with guard:
            if first_scope:
                probe = self._run("list-sessions", check=False)
                if probe["returncode"] == 0:
                    raise RuntimeError("tmux_runtime_server_exists_unowned")
                if not _server_conclusively_absent(probe):
                    raise RuntimeError("tmux_runtime_server_probe_uncertain")
            else:
                self._require_server_incarnation()
            created = self._run(
                "new-session",
                "-d",
                "-P",
                "-F",
                _SCOPE_FORMAT,
                "-s",
                session_name,
                "-n",
                "tau-control",
                "-c",
                str(cwd),
                "-e",
                f"{_OWNER_TOKEN_VARIABLE}={self._owner_token}",
                may_start_server=first_scope,
                check=False,
            )
            try:
                values = _parse_single_record(
                    created, 4, "tmux_runtime_scope_creation_uncertain"
                )
            except RuntimeError:
                values = self._reconcile_scope_creation(session_name)
            session_id, observed_name, control_window_id, control_pane_id = values
            if observed_name != session_name:
                raise RuntimeError("tmux_runtime_scope_identity_uncertain")
            self._verify_scope_owner_token(session_id)
            if first_scope:
                self._verify_exclusive_initial_server(session_id, session_name)
            server_incarnation = self._read_server_incarnation()
            if server_incarnation is None:
                raise RuntimeError("tmux_runtime_server_incarnation_unavailable")
            if first_scope:
                self._server_incarnation = server_incarnation
            elif server_incarnation != self._server_incarnation:
                raise RuntimeError("tmux_runtime_server_incarnation_mismatch")
            configured = self._run(
                "set-window-option",
                "-g",
                "remain-on-exit",
                "on",
                check=False,
            )
            if configured["returncode"] != 0:
                self._reclaim_owned_scope(session_id)
                if first_scope:
                    self._server_incarnation = None
                raise RuntimeError("tmux_runtime_scope_configuration_uncertain")
        incarnation_sha256 = canonical_sha256(server_incarnation)
        scope = TmuxRuntimeScope(
            run_id=run_id,
            owner=owner,
            server_name=self._server_name,
            socket_root=self._socket_root,
            socket_path=str(server_incarnation["socket_path"]),
            server_pid=int(server_incarnation["server_pid"]),
            server_start_time=int(server_incarnation["server_start_time"]),
            server_version=str(server_incarnation["server_version"]),
            server_incarnation_sha256=incarnation_sha256,
            session_id=session_id,
            session_name=session_name,
            control_window_id=control_window_id,
            control_pane_id=control_pane_id,
            cwd=cwd,
            label=requested_label,
        )
        with self._lock:
            if run_id in self._scopes or session_id in self._scope_ids:
                raise RuntimeError("tmux_runtime_scope_already_exists")
            self._scopes[run_id] = scope
            self._scope_ids[session_id] = run_id
        return FrozenJson.from_value(scope.to_payload())

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease:
        payload = _object_payload(request, "tmux runtime spawn request")
        run_id = _required_string(payload, "run_id")
        scope_id = _required_string(payload, "scope_id")
        with self._lock:
            scope = self._scopes.get(run_id)
        if scope is None or scope.session_id != scope_id:
            raise RuntimeError("tmux_runtime_scope_unknown")
        self._require_server_incarnation(scope)
        owner = _required_string(payload, "owner")
        if owner != scope.owner:
            raise RuntimeError("tmux_runtime_owner_mismatch")
        cwd = _existing_directory(payload, "cwd")
        if cwd != scope.cwd:
            raise RuntimeError("tmux_runtime_spawn_cwd_outside_scope")
        command = _string_sequence(payload, "command")
        environment = _environment(payload.get("environment"))
        attempt_number = _required_int(payload, "attempt_number", minimum=1)
        attempt_id = _required_string(payload, "attempt_id")
        node_id = _required_string(payload, "node_id")
        plan_revision = _required_string(payload, "plan_revision")
        dag_id = _required_string(payload, "dag_id")
        execution_token = _required_string(payload, "execution_token")
        work_order_sha256 = _required_sha256(payload, "work_order_sha256")
        goal_hash = _required_sha256(payload, "goal_hash")
        lease_seconds = _optional_positive_float(payload.get("lease_seconds"), default=3600.0)
        window_name = _owned_name(str(payload.get("label") or node_id), f"{run_id}-{attempt_id}")
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
                raise RuntimeError("tmux_runtime_attempt_already_spawned")
            self._attempt_ids.add(attempt_identity)

        attempt_token = secrets.token_hex(32)
        shell_command = _shell_command(
            command, {**environment, _ATTEMPT_TOKEN_VARIABLE: attempt_token}
        )
        started = self._run(
            "new-window",
            "-d",
            "-P",
            "-F",
            _SCOPE_FORMAT,
            "-t",
            scope.session_id,
            "-n",
            window_name,
            "-c",
            str(cwd),
            shell_command,
            check=False,
        )
        try:
            session_id, _session_name, window_id, pane_id = _parse_single_record(
                started, 4, "tmux_runtime_spawn_response_malformed"
            )
            self._verify_pane_attempt_token(pane_id, attempt_token)
        except RuntimeError:
            session_id, _session_name, window_id, pane_id = (
                self._reconcile_spawn(scope, window_name, attempt_token)
            )
        if session_id != scope.session_id:
            raise RuntimeError("tmux_runtime_spawn_identity_uncertain")
        now = datetime.now(UTC)
        lease = RuntimeEndpointLease(
            run_id=run_id,
            plan_revision=plan_revision,
            dag_id=dag_id,
            node_id=node_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            execution_token=execution_token,
            backend="tmux",
            backend_session_id=scope.server_incarnation_sha256,
            scope_id=scope.session_id,
            endpoint_id=pane_id,
            work_order_sha256=work_order_sha256,
            goal_hash=goal_hash,
            owner=owner,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
            heartbeat_policy=FrozenJson.from_value(
                {"kind": "bounded_poll", "interval_seconds": self._poll_interval_seconds}
            ),
            cleanup_policy=FrozenJson.from_value(
                {"kind": "exact_endpoint_authorization", "scope_cleanup": "explicit"}
            ),
            capabilities_sha256=self.capabilities().sha256,
            backend_ids=FrozenJson.from_value(
                {
                    "server_name": self._server_name,
                    "socket_root": str(scope.socket_root),
                    "socket_path": scope.socket_path,
                    "server_pid": scope.server_pid,
                    "server_start_time": scope.server_start_time,
                    "server_version": scope.server_version,
                    "server_incarnation_sha256": scope.server_incarnation_sha256,
                    "session_id": scope.session_id,
                    "session_name": scope.session_name,
                    "window_id": window_id,
                    "window_name": window_name,
                    "pane_id": pane_id,
                }
            ),
        )
        state = _TmuxEndpointState(lease=lease, scope=scope)
        with self._lock:
            if pane_id in self._endpoint_ids:
                self._reclaim_unleased_spawn(scope, window_name, attempt_token)
                raise RuntimeError("tmux_runtime_endpoint_already_exists")
            self._endpoints[lease.sha256] = state
            self._endpoint_ids[pane_id] = lease.sha256
        return lease

    def submit(
        self, endpoint: RuntimeEndpointLease, work_order: FrozenJson
    ) -> RuntimeSubmitReceipt:
        state = self._state(endpoint)
        payload = _object_payload(work_order, "tmux runtime work order")
        if _required_sha256(payload, "work_order_sha256") != endpoint.work_order_sha256:
            raise ValueError("tmux runtime work order does not match endpoint lease")
        text = _required_string(payload, "text")
        _validate_single_line_submit(text)
        buffer_name = "tau-" + canonical_sha256(
            {
                "run_id": endpoint.run_id,
                "attempt_id": endpoint.attempt_id,
                "endpoint_lease_sha256": endpoint.sha256,
                "work_order_sha256": endpoint.work_order_sha256,
            }
        ).removeprefix("sha256:")[:32]
        with state.lock:
            if state.submit_receipt is not None:
                return state.submit_receipt
            inventory_result, records = self._pane_inventory(state.scope, None)
            if records is None:
                raise RuntimeError("tmux_runtime_submit_preflight_uncertain")
            pane = _exact_pane(records, endpoint)
            if pane is None:
                receipt = _submit_receipt(
                    endpoint,
                    delivery_status="REJECTED",
                    text_delivery_count=0,
                    acknowledgement={
                        "server_name": self._server_name,
                        "pane_id": endpoint.endpoint_id,
                        "buffer_name": buffer_name,
                        "preflight_returncode": inventory_result["returncode"],
                        "mutation_attempted": False,
                    },
                    errors=("tmux_runtime_submit_target_unavailable",),
                )
                state.submit_receipt = receipt
                return receipt
            mutated = self._run(
                "load-buffer",
                "-b",
                buffer_name,
                "-",
                ";",
                "if-shell",
                "-F",
                "-t",
                endpoint.endpoint_id,
                _guarded_pane_condition(endpoint, self._owner_token),
                (
                    f"paste-buffer -p -d -b {shlex.quote(buffer_name)} "
                    f"-t {shlex.quote(endpoint.endpoint_id)} ; "
                    f"send-keys -t {shlex.quote(endpoint.endpoint_id)} Enter"
                ),
                "run-shell 'exit 86'",
                input_text=text,
                check=False,
            )
            if mutated["returncode"] != 0:
                self._run("delete-buffer", "-b", buffer_name, check=False)
            confirmed = (
                mutated["returncode"] == 0 and not str(mutated["stderr"]).strip()
            )
            post_verify_error: str | None = None
            if confirmed:
                try:
                    verify_result, verify_records = self._pane_inventory(
                        state.scope, None
                    )
                    confirmed = _exact_pane(verify_records, endpoint) is not None
                except Exception as exc:
                    confirmed = False
                    verify_result = {"returncode": None}
                    post_verify_error = type(exc).__name__
            else:
                verify_result = {"returncode": None}
            receipt = _submit_receipt(
                endpoint,
                delivery_status="CONFIRMED" if confirmed else "INDETERMINATE",
                text_delivery_count=1 if confirmed else 0,
                acknowledgement={
                    "server_name": self._server_name,
                    "pane_id": endpoint.endpoint_id,
                    "buffer_name": buffer_name,
                    "mutation_returncode": mutated["returncode"],
                    "post_verify_returncode": verify_result["returncode"],
                    "mutation_attempted": True,
                    "automatic_retry_allowed": False,
                    "post_verify_error": post_verify_error,
                },
                errors=() if confirmed else ("tmux_runtime_submit_outcome_uncertain",),
            )
            state.submit_receipt = receipt
            return receipt

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson:
        state = self._state(endpoint)
        if lines < 0:
            raise ValueError("capture lines must be non-negative")
        inventory_result, records = self._pane_inventory(state.scope, None)
        if records is None:
            return FrozenJson.from_value(
                {
                    "backend": "tmux",
                    "backend_session_id": endpoint.backend_session_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "returncode": inventory_result["returncode"],
                    "text": "",
                    "requested_lines": lines,
                    "returned_lines": 0,
                    "returned_bytes": 0,
                    "max_capture_bytes": self._max_capture_bytes,
                    "truncated": False,
                    "diagnostic_only": True,
                    "pane_present": "UNKNOWN",
                    "errors": ["tmux_runtime_capture_inventory_uncertain"],
                }
            )
        if _exact_pane(records, endpoint) is None:
            return FrozenJson.from_value(
                {
                    "backend": "tmux",
                    "backend_session_id": endpoint.backend_session_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "returncode": inventory_result["returncode"],
                    "text": "",
                    "requested_lines": lines,
                    "returned_lines": 0,
                    "returned_bytes": 0,
                    "max_capture_bytes": self._max_capture_bytes,
                    "truncated": False,
                    "diagnostic_only": True,
                    "errors": ["tmux_runtime_capture_target_unavailable"],
                }
            )
        result = self._run(
            "capture-pane",
            "-p",
            "-S",
            f"-{lines}",
            "-t",
            endpoint.endpoint_id,
            check=False,
        )
        text, line_truncated, byte_truncated = _bound_capture_text(
            str(result["stdout"]), lines, self._max_capture_bytes
        )
        return FrozenJson.from_value(
            {
                "backend": "tmux",
                "backend_session_id": endpoint.backend_session_id,
                "endpoint_id": endpoint.endpoint_id,
                "returncode": result["returncode"],
                "text": text,
                "requested_lines": lines,
                "returned_lines": len(text.splitlines()),
                "returned_bytes": len(text.encode("utf-8")),
                "max_capture_bytes": self._max_capture_bytes,
                "truncated": line_truncated or byte_truncated,
                "line_truncated": line_truncated,
                "byte_truncated": byte_truncated,
                "diagnostic_only": True,
                "errors": [] if result["returncode"] == 0 else ["tmux_runtime_capture_failed"],
            }
        )

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent:
        return self._observe(endpoint)

    def _observe(
        self, endpoint: RuntimeEndpointLease, *, deadline: datetime | None = None
    ) -> RuntimeEvent:
        state = self._state(endpoint)
        result, records = self._pane_inventory(state.scope, deadline)
        if records is None:
            event = _runtime_event(
                endpoint,
                state="UNKNOWN",
                liveness="UNKNOWN",
                confidence="UNKNOWN",
                observation={
                    "server_name": self._server_name,
                    "pane_id": endpoint.endpoint_id,
                    "pane_present": "UNKNOWN",
                    "returncode": result["returncode"],
                    "error_code": "tmux_inventory_unavailable_or_malformed",
                },
            )
        else:
            pane = _exact_pane(records, endpoint)
            if pane is None:
                event = _runtime_event(
                    endpoint,
                    state="EXITED",
                    liveness="DEAD",
                    confidence="NATIVE",
                    observation={
                        "server_name": self._server_name,
                        "pane_id": endpoint.endpoint_id,
                        "pane_present": False,
                        "inventory_complete": True,
                    },
                )
            else:
                pane_dead = pane["pane_dead"] == "1"
                current_command = pane["pane_current_command"]
                dead_status = pane["pane_dead_status"]
                dead_signal = pane["pane_dead_signal"]
                dead_crashed = pane_dead and (
                    bool(dead_signal) or dead_status not in {"", "0"}
                )
                event = _runtime_event(
                    endpoint,
                    state=(
                        "CRASHED"
                        if dead_crashed
                        else "EXITED"
                        if pane_dead
                        else "UNKNOWN"
                        if current_command in {"", "bash", "dash", "fish", "sh", "zsh"}
                        else "RUNNING"
                    ),
                    liveness="DEAD" if pane_dead else "ALIVE",
                    confidence="NATIVE" if pane_dead else "PROCESS",
                    observation={
                        "server_name": self._server_name,
                        **pane,
                        "pane_present": True,
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
        while True:
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return None
            event = self._observe(endpoint, deadline=deadline)
            if event.event_id != cursor:
                return event
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return None
            time.sleep(min(self._poll_interval_seconds, remaining))

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]:
        with self._lock:
            candidates = [
                state.lease
                for state in self._endpoints.values()
                if state.lease.run_id == run_id and not state.terminated
            ]
        with self._lock:
            scope = next(
                (scope for scope in self._scopes.values() if scope.run_id == run_id),
                None,
            )
        if scope is None:
            return []
        _result, records = self._pane_inventory(scope, None)
        if records is None:
            raise RuntimeError("tmux_runtime_inventory_uncertain")
        return [lease for lease in candidates if _exact_pane(records, lease) is not None]

    @contextmanager
    def _server_creation_guard(self) -> Iterator[None]:
        lock_path = self._socket_root / f".tau-tmux-{self._server_name}.creation.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("tmux_runtime_server_creation_in_progress") from exc
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def terminate(
        self, endpoint: RuntimeEndpointLease, authorization: FrozenJson
    ) -> FrozenJson:
        state = self._state(endpoint)
        payload = _object_payload(authorization, "tmux runtime cleanup authorization")
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
            self._require_server_incarnation(state.scope)
            preflight_result, preflight_records = self._pane_inventory(state.scope, None)
            if _exact_pane(preflight_records, endpoint) is None:
                if preflight_records is not None:
                    state.terminated = True
                    return FrozenJson.from_value(
                        {
                            "status": "PASS",
                            "action": "already_absent",
                            "endpoint_id": endpoint.endpoint_id,
                            "post_verify_returncode": preflight_result["returncode"],
                            "post_verified_absent": True,
                        }
                    )
                return FrozenJson.from_value(
                    {
                        "status": "BLOCKED",
                        "action": "endpoint_close",
                        "endpoint_id": endpoint.endpoint_id,
                        "post_verify_returncode": preflight_result["returncode"],
                        "post_verified_absent": False,
                        "errors": ["tmux_runtime_endpoint_termination_not_verified"],
                    }
                )
            closed = self._run(
                "if-shell",
                "-F",
                "-t",
                endpoint.endpoint_id,
                _guarded_pane_condition(endpoint, self._owner_token),
                f"kill-pane -t {endpoint.endpoint_id}",
                "run-shell 'exit 86'",
                check=False,
            )
            verify_result, records = self._pane_inventory(state.scope, None)
            absent = records is not None and all(
                item["pane_id"] != endpoint.endpoint_id for item in records
            )
            if not absent:
                return FrozenJson.from_value(
                    {
                        "status": "BLOCKED",
                        "action": "endpoint_close",
                        "endpoint_id": endpoint.endpoint_id,
                        "close_returncode": closed["returncode"],
                        "post_verify_returncode": verify_result["returncode"],
                        "post_verified_absent": False,
                        "errors": ["tmux_runtime_endpoint_termination_not_verified"],
                    }
                )
            state.terminated = True
            return FrozenJson.from_value(
                {
                    "status": "PASS",
                    "action": "endpoint_close" if closed["returncode"] == 0 else "already_absent",
                    "endpoint_id": endpoint.endpoint_id,
                    "close_returncode": closed["returncode"],
                    "post_verify_returncode": verify_result["returncode"],
                    "post_verified_absent": True,
                }
            )

    def _pane_inventory(
        self, scope: TmuxRuntimeScope, deadline: datetime | None
    ) -> tuple[dict[str, Any], list[dict[str, str]] | None]:
        incarnation = self._read_server_incarnation(check=False, deadline=deadline)
        if incarnation is None:
            return _failed_command(
                [self._tmux_bin, "-L", self._server_name, "-N", "display-message"],
                1,
                "tmux server incarnation unavailable",
            ), None
        if canonical_sha256(incarnation) != scope.server_incarnation_sha256:
            raise RuntimeError("tmux_runtime_server_incarnation_mismatch")
        timeout_seconds = self._command_timeout_seconds
        if deadline is not None:
            timeout_seconds = min(
                timeout_seconds,
                max((deadline - datetime.now(UTC)).total_seconds(), 0.0),
            )
        result = self._run(
            "list-panes",
            "-a",
            "-F",
            _PANE_FORMAT,
            check=False,
            timeout_seconds=timeout_seconds,
        )
        if result["returncode"] != 0:
            return result, None
        records: list[dict[str, str]] = []
        for line in result["stdout"].splitlines():
            fields = line.split("\t")
            if len(fields) != 8 or not all(fields[:5]):
                return result, None
            records.append(
                dict(
                    zip(
                        (
                            "session_id",
                            "window_id",
                            "pane_id",
                            "pane_dead",
                            "pane_pid",
                            "pane_current_command",
                            "pane_dead_status",
                            "pane_dead_signal",
                        ),
                        fields,
                        strict=True,
                    )
                )
            )
        return result, records

    def _reconcile_scope_creation(self, session_name: str) -> tuple[str, ...]:
        result = self._run("list-panes", "-a", "-F", _SCOPE_FORMAT, check=False)
        if result["returncode"] != 0:
            raise RuntimeError("tmux_runtime_scope_creation_uncertain")
        matches = [
            tuple(line.split("\t"))
            for line in str(result["stdout"]).splitlines()
            if line and len(line.split("\t")) == 4 and line.split("\t")[1] == session_name
        ]
        if len(matches) != 1 or any(not value for value in matches[0]):
            raise RuntimeError("tmux_runtime_scope_creation_uncertain")
        return matches[0]

    def _verify_exclusive_initial_server(
        self, session_id: str, session_name: str
    ) -> None:
        result = self._run(
            "list-sessions", "-F", _SERVER_SESSION_FORMAT, check=False
        )
        records = [
            tuple(line.split("\t"))
            for line in str(result["stdout"]).splitlines()
            if line
        ]
        expected = [(session_id, session_name)]
        if result["returncode"] == 0 and records == expected:
            return
        self._reclaim_owned_scope(session_id)
        raise RuntimeError("tmux_runtime_server_ownership_uncertain")

    def _verify_scope_owner_token(self, session_id: str) -> None:
        result = self._run(
            "show-environment",
            "-t",
            session_id,
            _OWNER_TOKEN_VARIABLE,
            check=False,
        )
        expected = f"{_OWNER_TOKEN_VARIABLE}={self._owner_token}"
        if result["returncode"] != 0 or str(result["stdout"]).strip() != expected:
            raise RuntimeError("tmux_runtime_scope_owner_token_mismatch")

    def _reclaim_owned_scope(self, session_id: str) -> None:
        condition = f"#{{==:#{{E:{_OWNER_TOKEN_VARIABLE}}},{self._owner_token}}}"
        result = self._run(
            "if-shell",
            "-F",
            "-t",
            session_id,
            condition,
            f"kill-session -t {session_id}",
            "run-shell 'exit 86'",
            check=False,
        )
        if result["returncode"] != 0:
            raise RuntimeError("tmux_runtime_scope_reclaim_uncertain")

    def _reconcile_spawn(
        self, scope: TmuxRuntimeScope, window_name: str, attempt_token: str
    ) -> tuple[str, ...]:
        result = self._run(
            "list-panes", "-a", "-F", _SPAWN_RECONCILE_FORMAT, check=False
        )
        if result["returncode"] != 0:
            raise RuntimeError("tmux_runtime_spawn_orphan_uncertain")
        matches: list[tuple[str, ...]] = []
        for line in str(result["stdout"]).splitlines():
            values = tuple(line.split("\t"))
            if (
                len(values) == 6
                and values[0] == scope.session_id
                and values[3] == window_name
                and all(values)
                and _pane_start_has_attempt_token(values[5], attempt_token)
            ):
                matches.append((values[0], values[1], values[2], values[4]))
        if len(matches) != 1:
            raise RuntimeError("tmux_runtime_spawn_orphan_uncertain")
        return matches[0]

    def _verify_pane_attempt_token(
        self, pane_id: str, attempt_token: str
    ) -> None:
        result = self._run(
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{pane_start_command}",
            check=False,
        )
        if result["returncode"] != 0 or not _pane_start_has_attempt_token(
            str(result["stdout"]).strip(), attempt_token
        ):
            raise RuntimeError("tmux_runtime_spawn_attempt_token_mismatch")

    def _reclaim_unleased_spawn(
        self, scope: TmuxRuntimeScope, window_name: str, attempt_token: str
    ) -> None:
        session_id, _session_name, window_id, pane_id = self._reconcile_spawn(
            scope, window_name, attempt_token
        )
        closed = self._run("kill-window", "-t", window_id, check=False)
        if closed["returncode"] != 0:
            raise RuntimeError("tmux_runtime_spawn_orphan_uncertain")
        _result, records = self._pane_inventory(scope, None)
        if records is None or any(
            record["session_id"] == session_id
            and record["window_id"] == window_id
            and record["pane_id"] == pane_id
            for record in records
        ):
            raise RuntimeError("tmux_runtime_spawn_orphan_uncertain")

    def _state(self, endpoint: RuntimeEndpointLease) -> _TmuxEndpointState:
        if endpoint.backend != "tmux":
            raise RuntimeError("tmux_runtime_endpoint_session_mismatch")
        with self._lock:
            state = self._endpoints.get(endpoint.sha256)
        if state is None or state.lease != endpoint:
            raise RuntimeError("tmux_runtime_endpoint_unknown")
        if endpoint.backend_session_id != state.scope.server_incarnation_sha256:
            raise RuntimeError("tmux_runtime_endpoint_session_mismatch")
        return state

    def _read_server_incarnation(
        self,
        *,
        check: bool = True,
        deadline: datetime | None = None,
    ) -> dict[str, Any] | None:
        timeout_seconds = self._command_timeout_seconds
        if deadline is not None:
            timeout_seconds = min(
                timeout_seconds,
                max((deadline - datetime.now(UTC)).total_seconds(), 0.0),
            )
        result = self._run(
            "list-sessions",
            "-F",
            _SERVER_FORMAT,
            check=False,
            timeout_seconds=timeout_seconds,
        )
        if result["returncode"] != 0:
            if check:
                raise RuntimeError("tmux_runtime_server_incarnation_unavailable")
            return None
        try:
            socket_path, pid, start_time, version = _parse_server_incarnation(result)
            return {
                "server_name": self._server_name,
                "socket_root": str(self._socket_root),
                "socket_path": socket_path,
                "server_pid": int(pid),
                "server_start_time": int(start_time),
                "server_version": version,
            }
        except (RuntimeError, ValueError) as exc:
            if check:
                raise RuntimeError("tmux_runtime_server_incarnation_malformed") from exc
            return None

    def _require_server_incarnation(
        self, scope: TmuxRuntimeScope | None = None
    ) -> dict[str, Any]:
        expected = self._server_incarnation
        if expected is None:
            raise RuntimeError("tmux_runtime_server_incarnation_unbound")
        observed = self._read_server_incarnation()
        if observed != expected:
            raise RuntimeError("tmux_runtime_server_incarnation_mismatch")
        if scope is not None and canonical_sha256(observed) != scope.server_incarnation_sha256:
            raise RuntimeError("tmux_runtime_server_incarnation_mismatch")
        return observed

    def _run(
        self,
        *args: str,
        check: bool = True,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
        may_start_server: bool = False,
    ) -> dict[str, Any]:
        global_args = (
            ["-f", "/dev/null"] if may_start_server else ["-N"]
        )
        argv = [self._tmux_bin, "-L", self._server_name, *global_args, *args]
        effective_timeout = (
            self._command_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if effective_timeout <= 0:
            if check:
                raise RuntimeError("tmux_runtime_command_timeout:" + " ".join(args[:2]))
            return _failed_command(
                argv, 124, "deadline expired before tmux command", timed_out=True
            )
        try:
            completed = self._command_runner(
                argv,
                text=True,
                input=input_text,
                capture_output=True,
                check=False,
                timeout=effective_timeout,
                env=self._command_environment,
            )
        except subprocess.TimeoutExpired as exc:
            if check:
                raise RuntimeError("tmux_runtime_command_timeout:" + " ".join(args[:2])) from exc
            return _failed_command(
                argv,
                124,
                str(exc.stderr or "tmux command timed out"),
                stdout=str(exc.stdout or ""),
                timed_out=True,
            )
        except OSError as exc:
            if check:
                raise RuntimeError(
                    "tmux_runtime_command_launch_failed:" + " ".join(args[:2])
                ) from exc
            return _failed_command(argv, 126, str(exc), launch_error=type(exc).__name__)
        result = {
            "argv": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if check and completed.returncode != 0:
            raise RuntimeError(
                "tmux_runtime_command_failed:"
                + " ".join(args[:2])
                + f":exit={completed.returncode}"
            )
        return result


def tmux_runtime_scope_request(
    *, run_id: str, owner: str, cwd: Path, label: str
) -> FrozenJson:
    return FrozenJson.from_value(
        {"run_id": run_id, "owner": owner, "cwd": str(cwd), "label": label}
    )


def tmux_runtime_spawn_request(
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


def tmux_runtime_work_order(*, work_order_sha256: str, text: str) -> FrozenJson:
    return FrozenJson.from_value({"work_order_sha256": work_order_sha256, "text": text})


def tmux_cleanup_authorization(endpoint: RuntimeEndpointLease) -> FrozenJson:
    return FrozenJson.from_value(
        {
            "schema": TMUX_CLEANUP_AUTHORIZATION_SCHEMA,
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


def _submit_receipt(
    endpoint: RuntimeEndpointLease,
    *,
    delivery_status: str,
    text_delivery_count: int,
    acknowledgement: dict[str, Any],
    errors: tuple[str, ...],
) -> RuntimeSubmitReceipt:
    return RuntimeSubmitReceipt(
        endpoint_lease_sha256=endpoint.sha256,
        work_order_sha256=endpoint.work_order_sha256,
        composer_state_before="UNKNOWN",
        text_delivery_count=text_delivery_count,
        submit_attempt_count=1,
        composer_state_after="UNKNOWN",
        delivery_status=delivery_status,
        backend_acknowledgement=FrozenJson.from_value(acknowledgement),
        provider_execution_status="NOT_OBSERVED",
        errors=errors,
    )


def _runtime_event(
    endpoint: RuntimeEndpointLease,
    *,
    state: RuntimeState,
    liveness: RuntimeLiveness,
    confidence: str,
    observation: dict[str, Any],
) -> RuntimeEvent:
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
        event_id=f"tmux:{digest}",
        run_id=endpoint.run_id,
        endpoint_lease_sha256=endpoint.sha256,
        event_type="RUNTIME_OBSERVATION_RECORDED",
        observed_at=datetime.now(UTC).isoformat(),
        state=state,
        liveness=liveness,
        confidence=cast(Any, confidence),
        source="tmux",
        observation=FrozenJson.from_value(observation),
    )


def _validate_cleanup_authorization(
    payload: dict[str, Any], endpoint: RuntimeEndpointLease
) -> None:
    expected = {
        "schema": TMUX_CLEANUP_AUTHORIZATION_SCHEMA,
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
        raise RuntimeError("tmux_runtime_cleanup_unauthorized:" + ",".join(mismatches))


def _exact_pane(
    records: list[dict[str, str]] | None, endpoint: RuntimeEndpointLease
) -> dict[str, str] | None:
    if records is None:
        return None
    expected_window = endpoint.backend_ids.to_value().get("window_id")
    for record in records:
        if record["pane_id"] != endpoint.endpoint_id:
            continue
        if (
            record["session_id"] != endpoint.scope_id
            or record["window_id"] != expected_window
        ):
            raise RuntimeError("tmux_runtime_endpoint_binding_mismatch")
        return record
    return None


def _guarded_pane_condition(
    endpoint: RuntimeEndpointLease, owner_token: str
) -> str:
    backend_ids = endpoint.backend_ids.to_value()
    checks = (
        f"#{{==:#{{pid}},{backend_ids.get('server_pid')}}}",
        f"#{{==:#{{start_time}},{backend_ids.get('server_start_time')}}}",
        f"#{{==:#{{session_id}},{endpoint.scope_id}}}",
        f"#{{==:#{{window_id}},{backend_ids.get('window_id')}}}",
        f"#{{==:#{{pane_id}},{endpoint.endpoint_id}}}",
        f"#{{==:#{{E:{_OWNER_TOKEN_VARIABLE}}},{owner_token}}}",
    )
    condition = checks[-1]
    for check in reversed(checks[:-1]):
        condition = f"#{{&&:{check},{condition}}}"
    return condition


def _pane_start_has_attempt_token(command: str, attempt_token: str) -> bool:
    expected = f"{_ATTEMPT_TOKEN_VARIABLE}={attempt_token}"
    try:
        parts = shlex.split(command)
        if len(parts) == 1 and parts[0] != command:
            parts = shlex.split(parts[0])
        return expected in parts
    except ValueError:
        return False


def _validate_single_line_submit(text: str) -> None:
    invalid = [character for character in text if ord(character) < 32]
    if invalid or "\x7f" in text:
        raise ValueError("tmux runtime work order must be one printable line")


def _bound_capture_text(
    text: str, max_lines: int, max_bytes: int
) -> tuple[str, bool, bool]:
    lines = text.splitlines(keepends=True)
    while lines and not lines[-1].rstrip("\r\n"):
        lines.pop()
    line_truncated = len(lines) > max_lines
    bounded = "".join(lines[-max_lines:]) if max_lines else ""
    encoded = bounded.encode("utf-8")
    if len(encoded) <= max_bytes:
        return bounded, line_truncated, False
    return (
        encoded[:max_bytes].decode("utf-8", errors="ignore"),
        line_truncated,
        True,
    )


def _server_conclusively_absent(result: Mapping[str, Any]) -> bool:
    if result.get("returncode") == 0:
        return False
    diagnostic = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    return "no server running" in diagnostic or (
        "error connecting" in diagnostic and "no such file or directory" in diagnostic
    )


def _parse_single_record(result: Mapping[str, Any], count: int, error: str) -> tuple[str, ...]:
    if result.get("returncode") != 0:
        raise RuntimeError(error)
    lines = str(result.get("stdout") or "").splitlines()
    if len(lines) != 1:
        raise RuntimeError(error)
    values = tuple(lines[0].split("\t"))
    if len(values) != count or any(not value for value in values):
        raise RuntimeError(error)
    return values


def _parse_server_incarnation(result: Mapping[str, Any]) -> tuple[str, ...]:
    lines = str(result.get("stdout") or "").splitlines()
    values = {tuple(line.split("\t")) for line in lines if line}
    if len(values) != 1:
        raise RuntimeError("tmux_runtime_server_incarnation_malformed")
    record = next(iter(values))
    if len(record) != 4 or any(not value for value in record):
        raise RuntimeError("tmux_runtime_server_incarnation_malformed")
    return record


def _shell_command(command: tuple[str, ...], environment: dict[str, str]) -> str:
    prefix = ["exec"]
    if environment:
        prefix.extend(("env", *(f"{key}={value}" for key, value in sorted(environment.items()))))
    return shlex.join((*prefix, *command))


def _failed_command(
    argv: list[str],
    returncode: int,
    stderr: str,
    *,
    stdout: str = "",
    timed_out: bool = False,
    launch_error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "argv": argv,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if timed_out:
        result["timed_out"] = True
    if launch_error is not None:
        result["launch_error"] = launch_error
    return result


def _owned_name(label: str, identity: str) -> str:
    normalized = "".join(ch if ch.isalnum() else " " for ch in label.lower())
    slug = "-".join(filter(None, normalized.split()))
    suffix = canonical_sha256(identity).removeprefix("sha256:")[:12]
    bounded = (slug or "runtime")[:48].rstrip("-") or "runtime"
    return f"tau-{bounded}-{suffix}"


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


def _environment(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("environment must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str):
            raise ValueError("environment entries must be string pairs")
        result[key] = item
    return result


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


__all__ = [
    "TMUX_CLEANUP_AUTHORIZATION_SCHEMA",
    "TmuxRuntimeBackend",
    "TmuxRuntimeScope",
    "tmux_cleanup_authorization",
    "tmux_runtime_scope_request",
    "tmux_runtime_spawn_request",
    "tmux_runtime_work_order",
]
