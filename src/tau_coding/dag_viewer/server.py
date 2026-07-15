"""Loopback-only, read-only HTTP server for Tau DAG projections."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from tau_coding.dag_viewer.contracts import viewer_capabilities
from tau_coding.dag_viewer.http import (
    ViewerHttpResponse,
    error_code,
    html_response,
    json_response,
    parse_event_query,
    public_error_message,
    security_headers,
    viewer_error,
    with_headers,
)
from tau_coding.dag_viewer.projection import (
    build_dag_live_events,
    build_dag_live_snapshot,
    build_dag_view_manifest,
    load_dag_replay,
)
from tau_coding.dag_viewer.receipt_index import ReceiptIndex, build_receipt_index

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
INFO_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Tau DAG Viewer API</title></head>
<body><main><h1>Tau DAG Viewer API</h1>
<p>The read-only API is operational. Packaged React assets arrive in Child C.</p>
</main></body></html>"""


class DagViewerApplication:
    def __init__(self, *, run_dir: Path, run_id: str | None = None) -> None:
        self.run_dir = run_dir.expanduser().resolve()
        replay, _ = load_dag_replay(run_dir=self.run_dir, run_id=run_id)
        self.run_id = replay.run_id
        self.plan_sha256 = replay.plan.plan_sha256
        self.receipts: ReceiptIndex = build_receipt_index(self.run_dir)

    def handle_get(self, target: str, *, if_none_match: str | None) -> ViewerHttpResponse:
        parsed = urlsplit(target)
        path = unquote(parsed.path)
        if path == "/":
            return html_response(INFO_PAGE)
        if path == "/healthz":
            return json_response({"status": "ok", "read_only": True})
        if path == "/api/v1/capabilities":
            return json_response(viewer_capabilities())
        if path == "/api/v1/manifest":
            replay, _ = self._replay()
            manifest = build_dag_view_manifest(replay=replay, run_dir=self.run_dir)
            manifest["receipt_index"] = self.receipts.public_entries()
            return json_response(manifest)
        if path == "/api/v1/state":
            replay, events = self._replay()
            snapshot = build_dag_live_snapshot(replay=replay, recent_events=events[-200:])
            etag = f'"{snapshot["snapshot_sha256"]}"'
            if if_none_match == etag:
                return ViewerHttpResponse(
                    304,
                    b"",
                    "application/json",
                    {"ETag": etag, "Cache-Control": "no-store"},
                )
            return with_headers(json_response(snapshot), {"ETag": etag})
        if path == "/api/v1/events":
            after, before, limit = parse_event_query(parsed.query)
            replay, events = self._replay()
            selected = tuple(
                event
                for event in events
                if int(event["seq"]) > after and (before is None or int(event["seq"]) < before)
            )
            selected = selected[-limit:] if before is not None else selected[:limit]
            return json_response(
                build_dag_live_events(
                    replay=replay,
                    events=selected,
                    after_sequence=after,
                    limit=limit,
                )
            )
        receipt_prefix = "/api/v1/receipts/"
        if path.startswith(receipt_prefix):
            receipt_id = path.removeprefix(receipt_prefix)
            if not receipt_id or "/" in receipt_id or receipt_id in {".", ".."}:
                raise RuntimeError("dag_viewer_receipt_not_found")
            return json_response(self.receipts.read_projection(receipt_id))
        return viewer_error(
            "dag_viewer_endpoint_not_found", "The endpoint does not exist.", status=404
        )

    def _replay(self) -> tuple[Any, tuple[dict[str, Any], ...]]:
        replay, events = load_dag_replay(run_dir=self.run_dir, run_id=self.run_id)
        if replay.plan.plan_sha256 != self.plan_sha256:
            raise RuntimeError("dag_viewer_plan_hash_mismatch")
        return replay, events


class DagViewerHttpServer(ThreadingHTTPServer):
    daemon_threads = True


class DagViewerHttpServerV6(DagViewerHttpServer):
    address_family = socket.AF_INET6


@dataclass(slots=True)
class RunningDagViewerServer:
    httpd: ThreadingHTTPServer
    application: DagViewerApplication
    host: str

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        displayed_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{displayed_host}:{self.port}/"

    def receipt(self) -> dict[str, Any]:
        return {
            "schema": "tau.dag_viewer_server_receipt.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "read_only": True,
            "host": self.host,
            "port": self.port,
            "url": self.url,
            "run_id": self.application.run_id,
            "plan_sha256": self.application.plan_sha256,
            "proof_scope": {
                "proves": [
                    "Tau bound a loopback-only read-only HTTP server to a validated DAG run."
                ],
                "does_not_prove": [
                    "The React viewer is packaged.",
                    "The server authorizes DAG mutation.",
                    "Agent or provider output is semantically correct.",
                ],
            },
        }

    def serve_forever(self) -> None:
        try:
            self.httpd.serve_forever()
        finally:
            self.httpd.server_close()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def create_dag_viewer_server(
    *, run_dir: Path, run_id: str | None = None, host: str = "127.0.0.1", port: int = 0
) -> RunningDagViewerServer:
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError("dag_viewer_non_loopback_forbidden")
    if not 0 <= port <= 65535:
        raise RuntimeError("dag_viewer_port_invalid")
    application = DagViewerApplication(run_dir=run_dir, run_id=run_id)
    handler = _handler_for(application)
    server_type = DagViewerHttpServerV6 if host == "::1" else DagViewerHttpServer
    httpd = server_type((host, port), handler)
    return RunningDagViewerServer(httpd=httpd, application=application, host=host)


def _handler_for(application: DagViewerApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            try:
                response = application.handle_get(
                    self.path,
                    if_none_match=self.headers.get("If-None-Match"),
                )
            except Exception as exc:  # HTTP boundary converts internals to a closed error contract.
                code = error_code(exc)
                status = 404 if code == "dag_viewer_receipt_not_found" else 409
                response = viewer_error(code, public_error_message(code), status=status)
            self._send(response)

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def _method_not_allowed(self) -> None:
            self._send(
                viewer_error(
                    "dag_viewer_method_not_allowed",
                    "The DAG viewer is read-only.",
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                ),
                extra={"Allow": "GET"},
            )

        def _send(
            self, response: ViewerHttpResponse, *, extra: dict[str, str] | None = None
        ) -> None:
            self.send_response(response.status)
            headers = {
                **security_headers(html=response.content_type.startswith("text/html")),
                **response.headers,
                **(extra or {}),
            }
            headers["Content-Type"] = response.content_type
            headers["Content-Length"] = str(len(response.body))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler
