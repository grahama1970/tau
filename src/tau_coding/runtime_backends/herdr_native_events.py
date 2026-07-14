"""Herdr AF_UNIX event subscription transport.

Inputs are an exact Herdr session socket and a Tau runtime endpoint lease. The
transport verifies the Herdr server status before use, subscribes only to the
leased pane's agent-status events, and returns backend-neutral RuntimeEvent
objects. Setup, protocol, binding, and stream failures raise a typed error so
the Herdr backend can record the failure and fall back to bounded polling.
"""

from __future__ import annotations

import json
import socket
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import (
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeLiveness,
    RuntimeState,
)

_SUPPORTED_PROTOCOL_VERSIONS = frozenset({("0.7.1", 14), ("0.7.1", 15)})
_MAX_LINE_BYTES = 64 * 1024
_AGENT_STATUSES = ("idle", "working", "blocked", "done", "unknown")

_CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class HerdrNativeEventError(RuntimeError):
    """A native event transport failure that permits bounded polling fallback."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class HerdrNativeEventTransport:
    """Verified binding to one running Herdr session's socket API."""

    session: str
    socket_path: Path
    server_version: str
    protocol: int

    @property
    def stream_id(self) -> str:
        return f"herdr:{self.session}:protocol-{self.protocol}"

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        deadline: datetime,
    ) -> RuntimeEvent | None:
        """Wait for one status event from the exact leased Herdr pane."""

        _validate_endpoint_binding(endpoint, self.session)
        request_id = f"tau:{endpoint.sha256.removeprefix('sha256:')[:24]}"
        request = {
            "id": request_id,
            "method": "events.subscribe",
            "params": {
                "subscriptions": [
                    {
                        "type": "pane.agent_status_changed",
                        "pane_id": endpoint.endpoint_id,
                        "agent_status": status,
                    }
                    for status in _AGENT_STATUSES
                ]
            },
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                _set_remaining_timeout(stream, deadline)
                stream.connect(str(self.socket_path))
                stream.sendall(_canonical_line(request))
                reader = _JsonLineStream(stream)
                ack = reader.read(deadline, timeout_is_error=True)
                _validate_ack(ack, request_id)
                while True:
                    payload = reader.read(deadline, timeout_is_error=False)
                    if payload is None:
                        return None
                    if payload.get("event") != "pane.agent_status_changed":
                        continue
                    return _event_from_payload(
                        endpoint=endpoint,
                        session=self.session,
                        stream_id=self.stream_id,
                        payload=payload,
                    )
        except HerdrNativeEventError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HerdrNativeEventError("herdr_native_stream_failed") from exc


def discover_herdr_native_event_transport(
    *,
    session: str,
    herdr_bin: str,
    command_runner: _CommandRunner,
    timeout_seconds: float,
) -> tuple[HerdrNativeEventTransport | None, str | None]:
    """Resolve and verify the exact session socket through Herdr's status API."""

    argv = [herdr_bin, "--session", session, "status", "server", "--json"]
    try:
        completed = command_runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):  # fmt: skip
        return None, "herdr_native_status_unavailable"
    if completed.returncode != 0:
        return None, "herdr_native_status_failed"
    try:
        status = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, "herdr_native_status_malformed"
    if not isinstance(status, dict):
        return None, "herdr_native_status_malformed"
    if status.get("running") is not True or status.get("compatible") is not True:
        return None, "herdr_native_server_incompatible"
    protocol = status.get("protocol")
    version = status.get("version")
    socket_value = status.get("socket")
    if not isinstance(version, str) or not version or not isinstance(socket_value, str):
        return None, "herdr_native_status_incomplete"
    if type(protocol) is not int or (version, protocol) not in _SUPPORTED_PROTOCOL_VERSIONS:
        return None, "herdr_native_protocol_unsupported"
    socket_path = Path(socket_value).expanduser().resolve()
    try:
        is_socket = socket_path.is_socket()
    except OSError:
        is_socket = False
    if not socket_path.is_absolute() or not is_socket:
        return None, "herdr_native_socket_unavailable"
    reported_session = status.get("session")
    if session == "default":
        if reported_session not in {None, "default"}:
            return None, "herdr_native_session_mismatch"
    elif reported_session != session:
        return None, "herdr_native_session_mismatch"
    return (
        HerdrNativeEventTransport(
            session=session,
            socket_path=socket_path,
            server_version=version,
            protocol=protocol,
        ),
        None,
    )


def _event_from_payload(
    *,
    endpoint: RuntimeEndpointLease,
    session: str,
    stream_id: str,
    payload: Mapping[str, Any],
) -> RuntimeEvent:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HerdrNativeEventError("herdr_native_event_malformed")
    pane_id = data.get("pane_id")
    workspace_id = data.get("workspace_id")
    if pane_id != endpoint.endpoint_id:
        raise HerdrNativeEventError("herdr_native_event_pane_mismatch")
    if workspace_id != endpoint.scope_id:
        raise HerdrNativeEventError("herdr_native_event_workspace_mismatch")
    expected_agent = endpoint.backend_ids.to_value().get("agent_name")
    observed_agent = data.get("agent")
    if observed_agent is not None and observed_agent != expected_agent:
        raise HerdrNativeEventError("herdr_native_event_agent_mismatch")
    status = data.get("agent_status")
    if status not in _AGENT_STATUSES:
        raise HerdrNativeEventError("herdr_native_event_status_unknown")
    state, liveness = _state_from_agent_status(cast(str, status))
    raw_payload_sha256 = canonical_sha256(payload)
    projection = {
        "event": "pane.agent_status_changed",
        "data": {
            "pane_id": pane_id,
            "workspace_id": workspace_id,
            "agent_status": status,
            "agent": observed_agent,
        },
    }
    observation = {
        "session": session,
        "workspace_id": workspace_id,
        "pane_id": pane_id,
        "agent_status": status,
        "agent": observed_agent,
        "visible_text_diagnostic_only": True,
        "transport": {
            "mode": "native",
            "stream_id": stream_id,
            "backend_cursor": None,
            "backend_sequence": None,
            "raw_payload_sha256": raw_payload_sha256,
            "raw_payload_projection": projection,
            "raw_payload_omitted_reason": "redacted_to_runtime_fields",
            "raw_payload_truncated": False,
        },
    }
    digest = canonical_sha256(
        {
            "endpoint_lease_sha256": endpoint.sha256,
            "state": state,
            "liveness": liveness,
            "agent_status": status,
            "agent": observed_agent,
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
        confidence="NATIVE",
        source="herdr",
        observation=FrozenJson.from_value(observation),
    )


def _validate_endpoint_binding(endpoint: RuntimeEndpointLease, session: str) -> None:
    if endpoint.backend != "herdr":
        raise HerdrNativeEventError("herdr_native_endpoint_backend_mismatch")
    if endpoint.backend_session_id != session:
        raise HerdrNativeEventError("herdr_native_endpoint_session_mismatch")
    backend_ids = endpoint.backend_ids.to_value()
    if backend_ids.get("session") != session:
        raise HerdrNativeEventError("herdr_native_endpoint_session_mismatch")
    if backend_ids.get("workspace_id") != endpoint.scope_id:
        raise HerdrNativeEventError("herdr_native_endpoint_workspace_mismatch")
    if backend_ids.get("pane_id") != endpoint.endpoint_id:
        raise HerdrNativeEventError("herdr_native_endpoint_pane_mismatch")


def _validate_ack(payload: Mapping[str, Any] | None, request_id: str) -> None:
    if payload is None or payload.get("id") != request_id:
        raise HerdrNativeEventError("herdr_native_subscription_ack_mismatch")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("type") != "subscription_started":
        raise HerdrNativeEventError("herdr_native_subscription_rejected")


def _canonical_line(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


class _JsonLineStream:
    def __init__(self, stream: socket.socket) -> None:
        self._stream = stream
        self._buffer = bytearray()

    def read(
        self,
        deadline: datetime,
        *,
        timeout_is_error: bool,
    ) -> dict[str, Any] | None:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise HerdrNativeEventError("herdr_native_line_malformed")
                return value
            try:
                _set_remaining_timeout(self._stream, deadline)
                chunk = self._stream.recv(4096)
            except TimeoutError as exc:
                if timeout_is_error:
                    raise HerdrNativeEventError("herdr_native_subscription_timeout") from exc
                return None
            if not chunk:
                raise HerdrNativeEventError("herdr_native_stream_closed")
            self._buffer.extend(chunk)
            if len(self._buffer) > _MAX_LINE_BYTES:
                raise HerdrNativeEventError("herdr_native_line_too_large")


def _set_remaining_timeout(stream: socket.socket, deadline: datetime) -> None:
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise HerdrNativeEventError("herdr_native_deadline_expired")
    stream.settimeout(remaining)


def _state_from_agent_status(status: str) -> tuple[RuntimeState, RuntimeLiveness]:
    states: dict[str, RuntimeState] = {
        "idle": "READY",
        "working": "RUNNING",
        "blocked": "BLOCKED",
        "done": "EXITED",
        "unknown": "UNKNOWN",
    }
    liveness: RuntimeLiveness = "UNKNOWN" if status == "unknown" else "ALIVE"
    return states[status], liveness


__all__ = [
    "HerdrNativeEventError",
    "HerdrNativeEventTransport",
    "discover_herdr_native_event_transport",
]
