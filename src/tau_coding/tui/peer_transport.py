"""Local HTTP/SSE peer transport for Tau TUI instances."""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tau_coding.tui.peer_queue import DurablePeerQueue, sse_event


class PeerTransportServer:
    """Small local peer-message server for one Tau harness instance."""

    def __init__(self, *, harness_id: str, queue_path: Path, host: str = "127.0.0.1") -> None:
        self.harness_id = harness_id
        self.queue = DurablePeerQueue(queue_path, harness_id=harness_id)
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        handler = self._handler()
        self._server = ThreadingHTTPServer((host, 0), handler)
        self._server.transport = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, envelope: dict[str, Any]) -> dict[str, Any]:
        item = self.queue.enqueue(envelope)
        self._events.put({"type": "peer-message", "item": item})
        return item

    def next_event(self, *, timeout_seconds: float = 1.0) -> dict[str, Any] | None:
        try:
            return self._events.get(timeout=timeout_seconds)
        except queue.Empty:
            return None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                transport: PeerTransportServer = self.server.transport  # type: ignore[attr-defined]
                if urlparse(self.path).path != "/events":
                    self.send_error(404)
                    return
                event = transport.next_event(timeout_seconds=1.0)
                if event is None:
                    self.send_response(204)
                    self.end_headers()
                    return
                body = sse_event(
                    event=str(event["type"]),
                    event_id=str(event["item"]["id"]),
                    data=event["item"]["envelope"],
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                transport: PeerTransportServer = self.server.transport  # type: ignore[attr-defined]
                if urlparse(self.path).path != "/peer-message":
                    self.send_error(404)
                    return
                length = int(self.headers.get("content-length", "0") or "0")
                raw = self.rfile.read(length)
                try:
                    envelope = json.loads(raw.decode("utf-8")) if raw else {}
                    if not isinstance(envelope, dict):
                        raise RuntimeError("payload must be a JSON object")
                    item = transport.enqueue(envelope)
                except (json.JSONDecodeError, RuntimeError) as exc:
                    payload = {"ok": False, "error": str(exc)}
                    self._write_json(payload, status=400)
                    return
                self._write_json({"ok": True, "item_id": item["id"]}, status=202)

            def log_message(self, format: str, *args: object) -> None:
                return

            def _write_json(self, payload: dict[str, Any], *, status: int) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def post_peer_message(
    *,
    target_url: str,
    envelope: dict[str, Any],
    timeout_seconds: float = 1.0,
) -> None:
    """Post a peer envelope to another local Tau transport."""

    body = json.dumps(envelope, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        f"{target_url.rstrip('/')}/peer-message",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status >= 400:
                raise RuntimeError(f"peer post failed with status {response.status}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"peer post failed: {exc}") from exc


def read_sse_once(*, source_url: str, timeout_seconds: float = 1.0) -> str | None:
    """Read one SSE response from a local Tau transport."""

    request = urllib.request.Request(f"{source_url.rstrip('/')}/events", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status == 204:
                return None
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise RuntimeError(f"peer event read failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"peer event read failed: {exc}") from exc
