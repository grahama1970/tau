"""Read-only HTTP monitor for Tau Loop2 receipt runs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tau_coding.loop_receipt import (
    build_loop_harness_peer_message,
    loop_receipt_loop2_events,
    loop_receipt_summary,
)


@dataclass(frozen=True, slots=True)
class LoopReceiptMonitorResponse:
    """One JSON response emitted by the receipt monitor."""

    status_code: int
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class LoopReceiptMonitorStream:
    """One SSE replay response emitted by the receipt monitor."""

    status_code: int
    body: bytes


@dataclass(frozen=True, slots=True)
class LoopReceiptMonitorCheckResult:
    """Validation result for the read-only Loop2 monitor surface."""

    ok: bool
    checked_endpoints: tuple[str, ...]
    errors: tuple[str, ...] = ()


def check_loop_receipt_monitor_contract(run_dir: Path) -> LoopReceiptMonitorCheckResult:
    """Exercise and validate the four Loop2 monitor endpoints for one run."""

    resolved = run_dir.resolve()
    run_id = resolved.name
    checked: list[str] = []
    errors: list[str] = []

    summary = loop_receipt_monitor_response(resolved, f"/api/loop2/runs/{run_id}/summary")
    try:
        _validate_summary_response(summary, run_id=run_id)
    except Exception as exc:
        errors.append(f"summary: {exc}")
    else:
        checked.append("summary")

    evidence = loop_receipt_monitor_response(
        resolved,
        f"/api/loop2/runs/{run_id}/transport-dag-evidence",
    )
    try:
        _validate_transport_dag_evidence_response(evidence)
    except Exception as exc:
        errors.append(f"transport-dag-evidence: {exc}")
    else:
        checked.append("transport-dag-evidence")

    events = loop_receipt_monitor_response(resolved, f"/api/loop2/runs/{run_id}/events")
    try:
        _validate_events_response(events, run_id=run_id)
    except Exception as exc:
        errors.append(f"events: {exc}")
    else:
        checked.append("events")

    stream = loop_receipt_monitor_stream(
        resolved,
        f"/api/loop2/runs/{run_id}/events/stream?timeout_s=0",
    )
    try:
        _validate_events_stream_response(stream, expected_event_count=_event_count(events))
    except Exception as exc:
        errors.append(f"events/stream: {exc}")
    else:
        checked.append("events/stream")

    peer = loop_receipt_monitor_response(resolved, f"/api/loop2/runs/{run_id}/peer-message")
    try:
        _validate_peer_message_response(peer, run_id=run_id)
    except Exception as exc:
        errors.append(f"peer-message: {exc}")
    else:
        checked.append("peer-message")

    return LoopReceiptMonitorCheckResult(
        ok=not errors,
        checked_endpoints=tuple(checked),
        errors=tuple(errors),
    )


def loop_receipt_monitor_response(run_dir: Path, path: str) -> LoopReceiptMonitorResponse:
    """Return the monitor response for one request path."""

    resolved = run_dir.resolve()
    parts = [part for part in urlparse(path).path.split("/") if part]
    if len(parts) != 5 or parts[:3] != ["api", "loop2", "runs"]:
        return _not_found("unknown endpoint")

    run_id = parts[3]
    endpoint = parts[4]
    if run_id != resolved.name:
        return _not_found("run id not served by this monitor")

    if endpoint == "summary":
        summary = loop_receipt_summary(resolved)
        if not summary.get("found"):
            return LoopReceiptMonitorResponse(404, summary)
        return LoopReceiptMonitorResponse(
            200,
            {
                "schema": "loop2.summary.v1",
                "run_id": run_id,
                "state": summary["current_state"],
                "receipt": summary["final_receipt"],
                "summary": summary,
            },
        )
    if endpoint == "transport-dag-evidence":
        evidence_path = resolved / "transport-dag-evidence.json"
        if not evidence_path.exists():
            return _not_found("transport DAG evidence not found")
        return LoopReceiptMonitorResponse(200, _read_json(evidence_path))
    if endpoint == "events":
        events_path = resolved / "events.jsonl"
        if not events_path.exists():
            return _not_found("events not found")
        return LoopReceiptMonitorResponse(
            200,
            {
                "schema": "loop2.events.v1",
                "run_id": run_id,
                "events": loop_receipt_loop2_events(resolved),
            },
        )
    if endpoint == "peer-message":
        peer = build_loop_harness_peer_message(resolved)
        if peer.get("ready") is not True:
            return LoopReceiptMonitorResponse(404, peer)
        return LoopReceiptMonitorResponse(200, peer)
    return _not_found("unknown endpoint")


def loop_receipt_monitor_stream(run_dir: Path, path: str) -> LoopReceiptMonitorStream:
    """Return an SSE replay or bounded-tail response for the Loop2 events stream."""

    resolved = run_dir.resolve()
    parsed = urlparse(path)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 6 or parts[:3] != ["api", "loop2", "runs"]:
        return LoopReceiptMonitorStream(404, _sse_event("error", {"detail": "unknown endpoint"}))

    run_id = parts[3]
    endpoint = parts[4:6]
    if run_id != resolved.name:
        return LoopReceiptMonitorStream(
            404,
            _sse_event("error", {"detail": "run id not served by this monitor"}),
        )
    if endpoint != ["events", "stream"]:
        return LoopReceiptMonitorStream(404, _sse_event("error", {"detail": "unknown endpoint"}))

    events_path = resolved / "events.jsonl"
    if not events_path.exists():
        return LoopReceiptMonitorStream(404, _sse_event("error", {"detail": "events not found"}))

    after_sequence = _after_sequence(parsed.query)
    timeout_s = _query_float(parsed.query, "timeout_s", default=0.0, minimum=0.0, maximum=30.0)
    poll_interval_s = _query_float(
        parsed.query,
        "poll_interval_s",
        default=0.1,
        minimum=0.01,
        maximum=5.0,
    )
    events, end_reason = _tail_events_after_sequence(
        resolved,
        after_sequence=after_sequence,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
    chunks = [_sse_event("loop2_event", row) for row in events]
    chunks.append(
        _sse_event(
            "end",
            {
                "reason": end_reason,
                "run_id": run_id,
                "after_sequence": after_sequence,
                "event_count": len(events),
            },
        )
    )
    return LoopReceiptMonitorStream(200, b"".join(chunks))


def create_loop_receipt_monitor_server(
    run_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create a stdlib HTTP server for one Tau Loop2 receipt run directory."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if _is_events_stream_path(self.path):
                stream = loop_receipt_monitor_stream(run_dir, self.path)
                self.send_response(stream.status_code)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(stream.body)
                return

            response = loop_receipt_monitor_response(run_dir, self.path)
            body = json.dumps(response.payload, sort_keys=True).encode()
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return ThreadingHTTPServer((host, port), Handler)


def _not_found(detail: str) -> LoopReceiptMonitorResponse:
    return LoopReceiptMonitorResponse(404, {"detail": detail})


def _validate_summary_response(response: LoopReceiptMonitorResponse, *, run_id: str) -> None:
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {response.payload}")
    payload = response.payload
    if payload.get("schema") != "loop2.summary.v1":
        raise ValueError("schema mismatch")
    if payload.get("run_id") != run_id:
        raise ValueError("run_id mismatch")
    for key in ("state", "receipt", "summary"):
        if not isinstance(payload.get(key), dict):
            raise ValueError(f"{key} must be a JSON object")
    summary = payload["summary"]
    if isinstance(summary, dict) and summary.get("found") is not True:
        raise ValueError("summary.found must be true")


def _validate_transport_dag_evidence_response(response: LoopReceiptMonitorResponse) -> None:
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {response.payload}")
    payload = response.payload
    if payload.get("schema") != "ux_lab.transport_dag_run_evidence.v1":
        raise ValueError("schema mismatch")
    if payload.get("found") is not True:
        raise ValueError("found must be true")
    for key in ("nodes", "edges", "layers"):
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(f"{key} must be a non-empty list")
    if not isinstance(payload.get("progress_stream"), dict):
        raise ValueError("progress_stream must be a JSON object")


def _validate_events_response(response: LoopReceiptMonitorResponse, *, run_id: str) -> None:
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {response.payload}")
    payload = response.payload
    if payload.get("schema") != "loop2.events.v1":
        raise ValueError("schema mismatch")
    if payload.get("run_id") != run_id:
        raise ValueError("run_id mismatch")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise ValueError(f"events[{index}] must be a JSON object")
        if event.get("schema") != "loop2.event.v1":
            raise ValueError(f"events[{index}].schema mismatch")
        if not isinstance(event.get("event_id"), str) or not event.get("event_id"):
            raise ValueError(f"events[{index}].event_id must be a non-empty string")
        if not isinstance(event.get("event_type"), str) or not event.get("event_type"):
            raise ValueError(f"events[{index}].event_type must be a non-empty string")


def _validate_events_stream_response(
    response: LoopReceiptMonitorStream,
    *,
    expected_event_count: int,
) -> None:
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {response.body.decode()}")
    events = _parse_sse_events(response.body)
    loop2_events = [payload for name, payload in events if name == "loop2_event"]
    end_events = [payload for name, payload in events if name == "end"]
    if len(loop2_events) != expected_event_count:
        raise ValueError(
            f"stream event count {len(loop2_events)} does not match events "
            f"{expected_event_count}"
        )
    if len(end_events) != 1:
        raise ValueError("stream must include exactly one end event")
    for index, event in enumerate(loop2_events, start=1):
        if event.get("schema") != "loop2.event.v1":
            raise ValueError(f"loop2_event[{index}].schema mismatch")
    end_event = end_events[0]
    if end_event.get("event_count") != expected_event_count:
        raise ValueError("end.event_count mismatch")


def _validate_peer_message_response(
    response: LoopReceiptMonitorResponse,
    *,
    run_id: str,
) -> None:
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {response.payload}")
    payload = response.payload
    if payload.get("schema") != "tau.loop_harness_peer_message.v1":
        raise ValueError("schema mismatch")
    if payload.get("message_type") != "loop2_receipt_available":
        raise ValueError("message_type mismatch")
    if payload.get("ready") is not True:
        raise ValueError("ready must be true")
    producer = payload.get("producer")
    if not isinstance(producer, dict) or producer.get("run_id") != run_id:
        raise ValueError("producer.run_id mismatch")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict) or "transport_dag_evidence" not in endpoints:
        raise ValueError("endpoints.transport_dag_evidence missing")
    schemas = payload.get("schemas")
    if (
        not isinstance(schemas, dict)
        or schemas.get("transport_dag_evidence") != "ux_lab.transport_dag_run_evidence.v1"
    ):
        raise ValueError("schemas.transport_dag_evidence mismatch")
    switchboard = payload.get("switchboard")
    if not isinstance(switchboard, dict):
        raise ValueError("switchboard envelope missing")
    if switchboard.get("from") != "tau":
        raise ValueError("switchboard.from mismatch")
    metadata = switchboard.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("run_id") != run_id:
        raise ValueError("switchboard.metadata.run_id mismatch")
    metadata_claims = metadata.get("claims")
    if not isinstance(metadata_claims, dict) or "does_not_prove" not in metadata_claims:
        raise ValueError("switchboard.metadata.claims.does_not_prove missing")


def _event_count(response: LoopReceiptMonitorResponse) -> int:
    events = response.payload.get("events")
    return len(events) if isinstance(events, list) else 0


def _parse_sse_events(body: bytes) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for block in body.decode().split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event_name and data:
            payload = json.loads(data)
            if isinstance(payload, dict):
                parsed.append((event_name, payload))
    return parsed


def _is_events_stream_path(path: str) -> bool:
    parts = [part for part in urlparse(path).path.split("/") if part]
    return len(parts) == 6 and parts[4:6] == ["events", "stream"]


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _read_events(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _after_sequence(query: str) -> int:
    return _query_int(query, "after_sequence", default=0, minimum=0)


def _query_int(query: str, name: str, *, default: int, minimum: int) -> int:
    values = parse_qs(query).get(name, [str(default)])
    try:
        return max(minimum, int(values[0]))
    except ValueError:
        return default


def _query_float(
    query: str,
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    values = parse_qs(query).get(name, [str(default)])
    try:
        return min(maximum, max(minimum, float(values[0])))
    except ValueError:
        return default


def _tail_events_after_sequence(
    run_dir: Path,
    *,
    after_sequence: int,
    timeout_s: float,
    poll_interval_s: float,
) -> tuple[list[dict[str, object]], str]:
    deadline = time.monotonic() + timeout_s
    while True:
        events = [
            row
            for row in loop_receipt_loop2_events(run_dir)
            if _event_index(row) > after_sequence
        ]
        if events:
            return events, "events_available"
        if timeout_s <= 0 or time.monotonic() >= deadline:
            return events, "timeout"
        time.sleep(min(poll_interval_s, max(0.0, deadline - time.monotonic())))


def _event_index(event: dict[str, object]) -> int:
    event_id = str(event.get("event_id") or "")
    parts = event_id.split(":")
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def _sse_event(event: str, payload: dict[str, object]) -> bytes:
    data = json.dumps(payload, sort_keys=True)
    return f"event: {event}\ndata: {data}\n\n".encode()
