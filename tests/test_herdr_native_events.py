"""Deterministic protocol tests for Herdr's native event transport."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease
from tau_coding.runtime_backends.herdr_native_events import (
    HerdrNativeEventError,
    HerdrNativeEventTransport,
    discover_herdr_native_event_transport,
)


def test_native_subscription_returns_exact_bound_runtime_event(tmp_path: Path) -> None:
    endpoint = _endpoint()
    event = _status_event(endpoint, status="blocked")
    with _subscription_server(tmp_path / "herdr.sock", event) as socket_path:
        transport = _transport(socket_path)

        observed = transport.wait_event(
            endpoint,
            datetime.now(UTC) + timedelta(seconds=2),
        )

    assert observed is not None
    assert observed.state == "BLOCKED"
    assert observed.liveness == "ALIVE"
    assert observed.confidence == "NATIVE"
    observation = observed.observation.to_value()
    assert observation["backend_session_id"] == "default"
    assert observation["workspace_id"] == endpoint.scope_id
    assert observation["pane_id"] == endpoint.endpoint_id
    assert observation["transport"]["mode"] == "native"
    assert observation["transport"]["raw_payload_sha256"] == canonical_sha256(event)
    assert "title" not in observation["transport"]["raw_payload_projection"]["data"]


def test_native_subscription_preserves_event_buffered_with_ack(tmp_path: Path) -> None:
    endpoint = _endpoint()
    with _subscription_server(
        tmp_path / "herdr.sock",
        _status_event(endpoint, status="working"),
        combine_ack_and_event=True,
    ) as socket_path:
        observed = _transport(socket_path).wait_event(
            endpoint,
            datetime.now(UTC) + timedelta(seconds=2),
        )

    assert observed is not None and observed.state == "RUNNING"


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"pane_id": "w1:p-other"}, "herdr_native_event_pane_mismatch"),
        ({"workspace_id": "w-other"}, "herdr_native_event_workspace_mismatch"),
        ({"agent": "other-agent"}, "herdr_native_event_agent_mismatch"),
        ({"session": "other"}, "herdr_native_event_session_mismatch"),
    ],
)
def test_native_subscription_rejects_wrong_endpoint_bindings(
    tmp_path: Path,
    override: dict[str, str],
    error: str,
) -> None:
    endpoint = _endpoint()
    event = _status_event(endpoint, status="idle")
    event["data"].update(override)
    with (
        _subscription_server(tmp_path / "herdr.sock", event) as socket_path,
        pytest.raises(HerdrNativeEventError, match=error),
    ):
        _transport(socket_path).wait_event(
            endpoint,
            datetime.now(UTC) + timedelta(seconds=2),
        )


def test_native_subscription_rejects_wrong_session_before_connect(tmp_path: Path) -> None:
    transport = HerdrNativeEventTransport(
        session="other",
        socket_path=tmp_path / "missing.sock",
        server_version="0.7.1",
        protocol=14,
    )

    with pytest.raises(HerdrNativeEventError, match="endpoint_session_mismatch"):
        transport.wait_event(_endpoint(), datetime.now(UTC) + timedelta(seconds=1))


def test_discovery_accepts_verified_protocol_and_exact_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    try:
        runner = _status_runner(socket_path=socket_path, protocol=14)

        transport, error = discover_herdr_native_event_transport(
            session="default",
            herdr_bin="herdr",
            command_runner=runner,
            timeout_seconds=1,
        )
    finally:
        listener.close()

    assert error is None
    assert transport is not None
    assert transport.socket_path == socket_path
    assert transport.protocol == 14


def test_discovery_rejects_unverified_protocol(tmp_path: Path) -> None:
    socket_path = tmp_path / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    try:
        transport, error = discover_herdr_native_event_transport(
            session="default",
            herdr_bin="herdr",
            command_runner=_status_runner(socket_path=socket_path, protocol=999),
            timeout_seconds=1,
        )
    finally:
        listener.close()

    assert transport is None
    assert error == "herdr_native_protocol_unsupported"


def test_discovery_rejects_unverified_version_for_known_protocol(tmp_path: Path) -> None:
    socket_path = tmp_path / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    try:
        runner = _status_runner(socket_path=socket_path, protocol=14, version="0.8.0")
        transport, error = discover_herdr_native_event_transport(
            session="default",
            herdr_bin="herdr",
            command_runner=runner,
            timeout_seconds=1,
        )
    finally:
        listener.close()

    assert transport is None
    assert error == "herdr_native_protocol_unsupported"


def test_discovery_rejects_relative_socket_path() -> None:
    transport, error = discover_herdr_native_event_transport(
        session="default",
        herdr_bin="herdr",
        command_runner=_status_runner(socket_path=Path("relative.sock"), protocol=14),
        timeout_seconds=1,
    )

    assert transport is None
    assert error == "herdr_native_socket_unavailable"


def test_discovery_rejects_missing_named_session_binding(tmp_path: Path) -> None:
    socket_path = tmp_path / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    try:
        transport, error = discover_herdr_native_event_transport(
            session="named",
            herdr_bin="herdr",
            command_runner=_status_runner(
                socket_path=socket_path,
                protocol=14,
                reported_session=None,
            ),
            timeout_seconds=1,
        )
    finally:
        listener.close()

    assert transport is None
    assert error == "herdr_native_session_mismatch"


def test_distinct_native_payloads_have_distinct_event_ids(tmp_path: Path) -> None:
    endpoint = _endpoint()
    first_payload = _status_event(endpoint, status="working")
    second_payload = _status_event(endpoint, status="working")
    second_payload["data"]["backend_sequence"] = 2
    observed = []

    for index, payload in enumerate((first_payload, second_payload)):
        with _subscription_server(tmp_path / f"herdr-{index}.sock", payload) as socket_path:
            event = _transport(socket_path).wait_event(
                endpoint,
                datetime.now(UTC) + timedelta(seconds=2),
            )
            assert event is not None
            observed.append(event)

    assert observed[0].event_id != observed[1].event_id


def _endpoint() -> RuntimeEndpointLease:
    now = datetime.now(UTC)
    return RuntimeEndpointLease(
        run_id="run-1",
        plan_revision=canonical_sha256({"plan": 1}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        backend="herdr",
        backend_session_id="default",
        scope_id="w1",
        endpoint_id="w1:p1",
        work_order_sha256=canonical_sha256({"work": 1}),
        goal_hash=canonical_sha256({"goal": 1}),
        owner="tau",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        heartbeat_policy=FrozenJson.from_value({"kind": "native"}),
        cleanup_policy=FrozenJson.from_value({"kind": "exact"}),
        capabilities_sha256=canonical_sha256({"capabilities": "native"}),
        backend_ids=FrozenJson.from_value(
            {
                "session": "default",
                "workspace_id": "w1",
                "pane_id": "w1:p1",
                "terminal_id": "term-1",
                "agent_name": "tau-worker",
            }
        ),
    )


def _transport(socket_path: Path) -> HerdrNativeEventTransport:
    return HerdrNativeEventTransport(
        session="default",
        socket_path=socket_path,
        server_version="0.7.1",
        protocol=14,
    )


def _status_event(endpoint: RuntimeEndpointLease, *, status: str) -> dict[str, Any]:
    return {
        "event": "pane.agent_status_changed",
        "data": {
            "pane_id": endpoint.endpoint_id,
            "workspace_id": endpoint.scope_id,
            "agent_status": status,
            "agent": endpoint.backend_ids.to_value()["agent_name"],
            "title": "must not enter projection",
        },
    }


@contextmanager
def _subscription_server(
    socket_path: Path,
    event: dict[str, Any],
    *,
    combine_ack_and_event: bool = False,
) -> Iterator[Path]:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = _recv_line(connection)
                ack = {"id": request["id"], "result": {"type": "subscription_started"}}
                ack_line = json.dumps(ack).encode() + b"\n"
                event_line = json.dumps(event).encode() + b"\n"
                if combine_ack_and_event:
                    connection.sendall(ack_line + event_line)
                else:
                    connection.sendall(ack_line)
                    connection.sendall(event_line)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield socket_path
    finally:
        thread.join(timeout=2)
        listener.close()
        assert not thread.is_alive()
        assert not errors


def _recv_line(connection: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("client closed before request")
        data.extend(chunk)
    value = json.loads(data.split(b"\n", 1)[0])
    assert isinstance(value, dict)
    return value


def _status_runner(
    *,
    socket_path: Path,
    protocol: int,
    version: str = "0.7.1",
    reported_session: str | None = None,
):
    def runner(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        payload = {
            "running": True,
            "compatible": True,
            "version": version,
            "protocol": protocol,
            "socket": str(socket_path),
            "session": reported_session,
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    return runner
