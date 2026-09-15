"""Loopback-only, read-only HTTP server for Tau DAG projections.

Diagram ID: tau.viewer-replay
Excalidraw source: docs/explain/boards/tau-viewer-replay.excalidraw
"""

from __future__ import annotations

import json
import secrets
import socket
import sqlite3
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_viewer.compare import (
    compare_attempts,
    compare_correction,
    compare_sequences,
)
from tau_coding.dag_viewer.contracts import viewer_capabilities
from tau_coding.dag_viewer.http import (
    ViewerHttpResponse,
    error_code,
    json_response,
    parse_at_sequence,
    parse_compare_query,
    parse_event_query,
    parse_node_inspector_query,
    parse_view_query,
    public_error_message,
    security_headers,
    viewer_error,
    with_headers,
)
from tau_coding.dag_viewer.project_receipt_projection import ProjectReceiptProjection
from tau_coding.dag_viewer.projection import (
    build_dag_live_events,
    build_dag_view_manifest,
    build_dag_view_state,
    build_selected_node_inspector,
    load_dag_replay_result,
)
from tau_coding.dag_viewer.query import query_dag_view
from tau_coding.dag_viewer.receipt_index import ReceiptIndex, build_receipt_index
from tau_coding.dag_viewer.static_files import read_static_viewer_file
from tau_coding.run_ledger import read_ledger, verify_ledger

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
GENERATION_MARKER = ":generation:"
MAX_OPERATOR_ACTION_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class LogicalRunIdentity:
    base_run_id: str
    generation: int
    physical_run_id: str


def _parse_logical_run_id(run_id: str) -> LogicalRunIdentity:
    if not run_id or GENERATION_MARKER not in run_id:
        if not run_id:
            raise RuntimeError("dag_viewer_run_id_invalid")
        return LogicalRunIdentity(run_id, 0, run_id)
    if run_id.count(GENERATION_MARKER) != 1:
        raise RuntimeError("dag_viewer_run_generation_invalid")
    base_run_id, suffix = run_id.split(GENERATION_MARKER, 1)
    if not base_run_id or not suffix.isascii() or not suffix.isdigit():
        raise RuntimeError("dag_viewer_run_generation_invalid")
    if suffix.startswith("0") or int(suffix) < 1:
        raise RuntimeError("dag_viewer_run_generation_invalid")
    return LogicalRunIdentity(base_run_id, int(suffix), run_id)


def _select_default_run(run_dir: Path) -> tuple[LogicalRunIdentity, str]:
    database = run_dir / "dag-run.sqlite3"
    lineages: dict[str, list[tuple[LogicalRunIdentity, str]]] = {}
    with SqliteDagRunReader(database) as reader:
        for run_id in reader.run_ids():
            identity = _parse_logical_run_id(run_id)
            plan_sha256 = reader.load_run_record(run_id).plan_sha256
            lineages.setdefault(identity.base_run_id, []).append((identity, plan_sha256))
    if len(lineages) != 1:
        raise RuntimeError("dag_viewer_run_id_ambiguous")
    entries = next(iter(lineages.values()), [])
    if not entries:
        raise RuntimeError("dag_viewer_run_id_not_found")
    generations = sorted(identity.generation for identity, _ in entries)
    if generations != list(range(generations[-1] + 1)):
        raise RuntimeError("dag_viewer_run_generation_non_contiguous")
    plan_hashes = {plan_sha256 for _, plan_sha256 in entries}
    if len(plan_hashes) != 1:
        raise RuntimeError("dag_viewer_plan_hash_mismatch")
    latest = max(entries, key=lambda entry: entry[0].generation)[0]
    return latest, plan_hashes.pop()


def _operator_action_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def _operator_action_receipt(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["receipt_json"] is None:
        return None
    receipt = json.loads(str(row["receipt_json"]))
    if not isinstance(receipt, dict):
        raise DagRunStoreError(
            "operator_action_receipt_hash_mismatch", str(row["action_request_id"])
        )
    body = {key: value for key, value in receipt.items() if key != "sha256"}
    if canonical_sha256(body) != str(row["receipt_sha256"]):
        raise DagRunStoreError(
            "operator_action_receipt_hash_mismatch", str(row["action_request_id"])
        )
    return receipt


def _operator_action_projection(
    row: sqlite3.Row, *, lifecycle: tuple[dict[str, Any], ...] = ()
) -> dict[str, Any]:
    request = json.loads(str(row["request_json"]))
    if not isinstance(request, dict) or canonical_sha256(request) != str(row["request_sha256"]):
        raise DagRunStoreError(
            "operator_action_request_hash_mismatch", str(row["action_request_id"])
        )
    return {
        "schema": "tau.operator_action_projection.v1",
        "action_request_id": str(row["action_request_id"]),
        "idempotency_key": str(row["idempotency_key"]),
        "run_id": str(row["run_id"]),
        "plan_id": str(row["plan_id"]),
        "plan_sha256": str(row["plan_sha256"]),
        "node_id": str(row["node_id"]),
        "attempt": int(row["attempt_no"]),
        "goal_hash": str(row["goal_hash"]),
        "observed_journal_seq": int(row["observed_journal_seq"]),
        "observed_journal_head_sha256": str(row["observed_journal_head_sha256"]),
        "action": str(row["action"]),
        "actor": str(row["actor"]),
        "principal": str(row["principal"]),
        "authority_class": str(row["authority_class"]),
        "requested_safe_point": str(row["requested_safe_point"]),
        "created_at": str(row["created_at"]),
        "expires_at": str(row["expires_at"]),
        "status": str(row["status"]),
        "outcome": str(row["outcome"]) if row["outcome"] is not None else None,
        "code": str(row["code"]) if row["code"] is not None else None,
        "receipt": _operator_action_receipt(row),
        "receipt_sha256": str(row["receipt_sha256"]) if row["receipt_sha256"] is not None else None,
        "lifecycle": list(lifecycle),
    }


def _list_operator_actions(database: Path, run_id: str) -> tuple[dict[str, Any], ...]:
    with _operator_action_connection(database) as connection:
        rows = connection.execute(
            """SELECT * FROM operator_action_requests
               WHERE run_id = ? ORDER BY created_at, action_request_id""",
            (run_id,),
        ).fetchall()
    return tuple(_operator_action_projection(row) for row in rows)


def _load_operator_action(database: Path, run_id: str, action_request_id: str) -> dict[str, Any]:
    with _operator_action_connection(database) as connection:
        row = connection.execute(
            """SELECT * FROM operator_action_requests
               WHERE run_id = ? AND action_request_id = ?""",
            (run_id, action_request_id),
        ).fetchone()
        if row is None:
            raise DagRunStoreError("operator_action_request_missing", action_request_id)
        ledger_rows = connection.execute(
            """SELECT status, event_seq, receipt_sha256, ledger_json, ledger_sha256, created_at
               FROM operator_action_ledger
               WHERE run_id = ? AND action_request_id = ?
               ORDER BY event_seq, created_at""",
            (run_id, action_request_id),
        ).fetchall()
    lifecycle = []
    for ledger_row in ledger_rows:
        ledger = json.loads(str(ledger_row["ledger_json"]))
        if not isinstance(ledger, dict) or canonical_sha256(ledger) != str(
            ledger_row["ledger_sha256"]
        ):
            raise DagRunStoreError("operator_action_ledger_hash_mismatch", action_request_id)
        lifecycle.append(
            {
                "status": str(ledger_row["status"]),
                "event_seq": int(ledger_row["event_seq"]),
                "receipt_sha256": (
                    str(ledger_row["receipt_sha256"])
                    if ledger_row["receipt_sha256"] is not None
                    else None
                ),
                "created_at": str(ledger_row["created_at"]),
            }
        )
    return _operator_action_projection(row, lifecycle=tuple(lifecycle))


class DagViewerApplication:
    def __init__(self, *, run_dir: Path, run_id: str | None = None) -> None:
        self.run_dir = run_dir.expanduser().resolve()
        self._project_receipt: ProjectReceiptProjection | None = None
        project_receipt: ProjectReceiptProjection | None = None
        if run_id is None:
            try:
                project_receipt = ProjectReceiptProjection.load(self.run_dir)
            except RuntimeError:
                if not (self.run_dir / "dag-run.sqlite3").is_file():
                    raise
        if project_receipt is not None:
            self._project_receipt = project_receipt
            self.run_id = project_receipt.run_id
            self._follow_generations = False
            self._base_run_id = project_receipt.run_id
            self.plan_sha256 = project_receipt.plan_sha256
            self._cursor_key = secrets.token_bytes(32)
            self._request_lock = threading.RLock()
            self.receipts = project_receipt.receipt_index
            return
        if run_id is None:
            identity, expected_plan_sha256 = _select_default_run(self.run_dir)
            selected_run_id = identity.physical_run_id
        else:
            identity = _parse_logical_run_id(run_id)
            selected_run_id = run_id
            expected_plan_sha256 = ""
        result = load_dag_replay_result(run_dir=self.run_dir, run_id=selected_run_id)
        self.run_id = result.replay.run_id
        self._follow_generations = run_id is None
        self._base_run_id = identity.base_run_id
        self.plan_sha256 = result.replay.plan.plan_sha256
        if expected_plan_sha256 and self.plan_sha256 != expected_plan_sha256:
            raise RuntimeError("dag_viewer_plan_hash_mismatch")
        self._cursor_key = secrets.token_bytes(32)
        self._request_lock = threading.RLock()
        self.receipts: ReceiptIndex = build_receipt_index(
            self.run_dir, result.replay.transition_receipts
        )

    def handle_get(self, target: str, *, if_none_match: str | None) -> ViewerHttpResponse:
        with self._request_lock:
            return self._handle_get(target, if_none_match=if_none_match)

    def handle_post(self, target: str, body: bytes) -> ViewerHttpResponse:
        with self._request_lock:
            return self._handle_post(target, body)

    def _handle_get(self, target: str, *, if_none_match: str | None) -> ViewerHttpResponse:
        parsed = urlsplit(target)
        path = unquote(parsed.path)
        if path == "/" or path.startswith("/assets/"):
            asset = read_static_viewer_file(path)
            return ViewerHttpResponse(200, asset.body, asset.content_type, {})
        if path == "/healthz":
            return json_response({"status": "ok", "read_only": True})
        if path == "/api/v1/capabilities":
            return json_response(viewer_capabilities())
        if path == "/api/v1/catalog":
            # tau#350: the canonical five-workflow catalog served from the
            # same authoritative definitions registry the CLI uses, so the
            # viewer landing cannot drift from `tau workflows list`.
            from tau_coding.workflows.catalog import workflow_catalog_payload

            return json_response(workflow_catalog_payload())
        if path == "/api/v1/manifest":
            at_sequence = parse_at_sequence(parsed.query)
            if self._project_receipt is not None:
                manifest = self._project_receipt.manifest()
                manifest["receipt_index"] = self.receipts.public_entries()
                return json_response(manifest)
            result = self._replay(at_sequence=at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            manifest = build_dag_view_manifest(replay=result.replay, run_dir=self.run_dir)
            manifest["receipt_index"] = self.receipts.public_entries()
            return json_response(manifest)
        if path == "/api/v1/state":
            at_sequence = parse_at_sequence(parsed.query)
            if self._project_receipt is not None:
                snapshot = self._project_receipt.snapshot(at_sequence=at_sequence)
                etag = f'"{snapshot["snapshot_sha256"]}"'
                response_headers = {
                    "ETag": etag,
                    "X-Tau-Journal-Head-Sequence": str(snapshot["journal_sequence"]),
                }
                if if_none_match == etag:
                    return ViewerHttpResponse(
                        304,
                        b"",
                        "application/json",
                        {**response_headers, "Cache-Control": "no-store"},
                    )
                return with_headers(json_response(snapshot), response_headers)
            result = self._replay(at_sequence=at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            snapshot, _ = build_dag_view_state(
                replay=result.replay,
                recent_events=result.events,
                view_mode=result.view_mode,
                selected_event_created_at=result.selected_event_created_at,
                receipt_index=self.receipts,
            )
            etag = f'"{snapshot["snapshot_sha256"]}"'
            response_headers = {
                "ETag": etag,
                "X-Tau-Journal-Head-Sequence": str(result.head_sequence),
            }
            if if_none_match == etag:
                return ViewerHttpResponse(
                    304,
                    b"",
                    "application/json",
                    {**response_headers, "Cache-Control": "no-store"},
                )
            return with_headers(json_response(snapshot), response_headers)
        if path == "/api/v1/ledger":
            ledger_path = self.run_dir / "run-ledger.json"
            ledger = read_ledger(ledger_path)
            return json_response(
                {
                    "schema": "tau.dag_viewer_ledger_projection.v1",
                    "run_id": self.run_id,
                    "read_only": True,
                    "ledger_path": str(ledger_path),
                    "verification": verify_ledger(ledger),
                    "ledger": ledger,
                }
            )
        operator_action_prefix = "/api/v1/operator-actions"
        if path == operator_action_prefix:
            return json_response(
                {
                    "schema": "tau.operator_action_list.v1",
                    "run_id": self.run_id,
                    "actions": list(
                        _list_operator_actions(self.run_dir / "dag-run.sqlite3", self.run_id)
                    ),
                }
            )
        if path.startswith(f"{operator_action_prefix}/"):
            action_path = path.removeprefix(f"{operator_action_prefix}/")
            receipt_requested = action_path.endswith("/receipt")
            action_request_id = (
                action_path.removesuffix("/receipt") if receipt_requested else action_path
            )
            if (
                not action_request_id
                or "/" in action_request_id
                or action_request_id in {".", ".."}
            ):
                raise RuntimeError("operator_action_request_missing")
            projection = _load_operator_action(
                self.run_dir / "dag-run.sqlite3", self.run_id, action_request_id
            )
            if receipt_requested:
                receipt = projection.get("receipt")
                if not isinstance(receipt, dict):
                    raise DagRunStoreError("operator_action_receipt_not_ready", action_request_id)
                return json_response(receipt)
            return json_response(projection)
        if path == "/api/v1/events":
            after, before, limit = parse_event_query(parsed.query)
            if self._project_receipt is not None:
                events = self._project_receipt.events()
                selected = tuple(
                    event
                    for event in events
                    if int(event["seq"]) > after and (before is None or int(event["seq"]) < before)
                )
                selected = selected[-limit:] if before is not None else selected[:limit]
                return json_response(
                    {
                        "schema": "tau.dag_live_event.v1",
                        "run_id": self._project_receipt.run_id,
                        "after_sequence": after,
                        "limit": limit,
                        "events": list(selected),
                    }
                )
            result = self._replay()
            replay, events = result.replay, result.events
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
        if path == "/api/v1/query":
            query = parse_view_query(parsed.query)
            result = self._replay(at_sequence=query.at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            snapshot, _ = build_dag_view_state(
                replay=result.replay,
                recent_events=result.events,
                view_mode=result.view_mode,
                selected_event_created_at=result.selected_event_created_at,
                receipt_index=self.receipts,
            )
            return json_response(
                query_dag_view(
                    run_id=result.replay.run_id,
                    view_sequence=result.selected_sequence,
                    snapshot=snapshot,
                    events=result.events,
                    receipts=self.receipts,
                    query=query,
                    cursor_key=self._cursor_key,
                )
            )
        if path == "/api/v1/compare":
            comparison = parse_compare_query(parsed.query)
            at_sequence = comparison["at_sequence"]

            def load(sequence: int) -> Any:
                return self._replay(at_sequence=sequence)

            if comparison["kind"] == "SEQUENCE_PAIR":
                payload = compare_sequences(
                    left_sequence=comparison["left_sequence"],
                    right_sequence=comparison["right_sequence"],
                    at_sequence=at_sequence,
                    load=load,
                    run_dir=self.run_dir,
                )
            elif comparison["kind"] == "ATTEMPT_PAIR":
                payload = compare_attempts(
                    node_id=comparison["node_id"],
                    left_attempt=comparison["left_attempt"],
                    right_attempt=comparison["right_attempt"],
                    load=load,
                    at_sequence=at_sequence,
                )
            else:
                payload = compare_correction(
                    incident_id=comparison["incident_id"],
                    load=load,
                    at_sequence=at_sequence,
                    run_dir=self.run_dir,
                )
            return json_response(payload)
        node_inspector_prefix = "/api/v1/nodes/"
        if path.startswith(node_inspector_prefix) and path.endswith("/inspector"):
            node_id = path.removeprefix(node_inspector_prefix).removesuffix("/inspector")
            if not node_id or "/" in node_id or node_id in {".", ".."}:
                raise RuntimeError("dag_viewer_node_inspector_not_found")
            at_sequence, attempt = parse_node_inspector_query(parsed.query)
            if self._project_receipt is not None:
                raise RuntimeError("dag_viewer_node_inspector_not_found")
            result = self._replay(at_sequence=at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            return json_response(
                build_selected_node_inspector(
                    replay=result.replay,
                    recent_events=result.events,
                    node_id=node_id,
                    attempt=attempt,
                    view_mode=result.view_mode,
                    selected_event_created_at=result.selected_event_created_at,
                    receipt_index=self.receipts,
                )
            )
        explanation_prefix = "/api/v1/explanations/"
        if path.startswith(explanation_prefix):
            remainder = path.removeprefix(explanation_prefix)
            parts = remainder.split("/")
            if len(parts) != 2 or not all(parts):
                raise RuntimeError("dag_viewer_explanation_not_found")
            kind, subject_id = parts[0].upper(), parts[1]
            at_sequence = parse_at_sequence(parsed.query)
            if self._project_receipt is not None:
                return json_response(
                    self._project_receipt.explanation(kind, subject_id, at_sequence=at_sequence)
                )
            result = self._replay(at_sequence=at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            _, causal = build_dag_view_state(
                replay=result.replay,
                recent_events=result.events,
                view_mode=result.view_mode,
                selected_event_created_at=result.selected_event_created_at,
                receipt_index=self.receipts,
            )
            return json_response(causal.explanation(kind, subject_id))
        receipt_prefix = "/api/v1/receipts/"
        if path.startswith(receipt_prefix):
            at_sequence = parse_at_sequence(parsed.query)
            receipt_id = path.removeprefix(receipt_prefix)
            if not receipt_id or "/" in receipt_id or receipt_id in {".", ".."}:
                raise RuntimeError("dag_viewer_receipt_not_found")
            result = self._replay(at_sequence=at_sequence)
            self._refresh_receipts(result.replay.transition_receipts)
            return json_response(self.receipts.read_projection(receipt_id))
        return viewer_error(
            "dag_viewer_endpoint_not_found", "The endpoint does not exist.", status=404
        )

    def _handle_post(self, target: str, body: bytes) -> ViewerHttpResponse:
        parsed = urlsplit(target)
        path = unquote(parsed.path)
        if path != "/api/v1/operator-actions":
            return viewer_error(
                "dag_viewer_endpoint_not_found", "The endpoint does not exist.", status=404
            )
        if len(body) > MAX_OPERATOR_ACTION_REQUEST_BYTES:
            raise DagRunStoreError("operator_action_request_too_large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DagRunStoreError("operator_action_request_json_invalid") from exc
        if not isinstance(payload, dict):
            raise DagRunStoreError("operator_action_request_json_invalid")
        if payload.get("schema") != "tau.operator_action_request.v1":
            raise DagRunStoreError("operator_action_schema_invalid")
        with SqliteDagRunStore(self.run_dir / "dag-run.sqlite3") as store:
            submitted = store.submit_operator_action_request(payload)
        return json_response(submitted, status=HTTPStatus.ACCEPTED)

    def _replay(self, *, at_sequence: int | None = None) -> Any:
        selected_run_id = self.run_id
        if self._follow_generations and at_sequence is None:
            identity, plan_sha256 = _select_default_run(self.run_dir)
            if identity.base_run_id != self._base_run_id:
                raise RuntimeError("dag_viewer_run_lineage_mismatch")
            if plan_sha256 != self.plan_sha256:
                raise RuntimeError("dag_viewer_plan_hash_mismatch")
            selected_run_id = identity.physical_run_id
        result = load_dag_replay_result(
            run_dir=self.run_dir, run_id=selected_run_id, at_sequence=at_sequence
        )
        if result.replay.plan.plan_sha256 != self.plan_sha256:
            raise RuntimeError("dag_viewer_plan_hash_mismatch")
        if self._follow_generations and at_sequence is None:
            self.run_id = result.replay.run_id
        return result

    def _refresh_receipts(self, receipt_refs: Any) -> None:
        expected = {
            (str(Path(item.path).expanduser().resolve()), item.file_sha256) for item in receipt_refs
        }
        current = {(str(item.path), item.sha256) for item in self.receipts.entries}
        if current != expected:
            self.receipts = build_receipt_index(self.run_dir, receipt_refs)


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
    handler = _handler_for(application, authority_host=host)
    server_type = DagViewerHttpServerV6 if host == "::1" else DagViewerHttpServer
    httpd = server_type((host, port), handler)
    return RunningDagViewerServer(httpd=httpd, application=application, host=host)


def _handler_for(
    application: DagViewerApplication, *, authority_host: str
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            server_address = self.server.server_address
            bound_port = int(server_address[1]) if isinstance(server_address, tuple) else -1
            if not _host_header_matches_server(
                self.headers.get("Host"),
                host=authority_host,
                port=bound_port,
            ):
                self._send(
                    viewer_error(
                        "dag_viewer_host_forbidden",
                        "The request authority does not match the loopback viewer.",
                        status=HTTPStatus.MISDIRECTED_REQUEST,
                    )
                )
                return
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
            server_address = self.server.server_address
            bound_port = int(server_address[1]) if isinstance(server_address, tuple) else -1
            if not _host_header_matches_server(
                self.headers.get("Host"),
                host=authority_host,
                port=bound_port,
            ):
                self._send(
                    viewer_error(
                        "dag_viewer_host_forbidden",
                        "The request authority does not match the loopback viewer.",
                        status=HTTPStatus.MISDIRECTED_REQUEST,
                    )
                )
                return
            if unquote(urlsplit(self.path).path) != "/api/v1/operator-actions":
                self._method_not_allowed()
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
                if content_length < 1 or content_length > MAX_OPERATOR_ACTION_REQUEST_BYTES:
                    raise DagRunStoreError("operator_action_request_too_large")
                response = application.handle_post(self.path, self.rfile.read(content_length))
            except Exception as exc:  # HTTP boundary converts internals to a closed error contract.
                code = error_code(exc)
                response = viewer_error(code, public_error_message(code), status=409)
            self._send(response)

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        do_PATCH = do_PUT
        do_DELETE = do_PUT

        def _method_not_allowed(self) -> None:
            self._send(
                viewer_error(
                    "dag_viewer_method_not_allowed",
                    "The DAG viewer only accepts GET and operator-action POST requests.",
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                ),
                extra={"Allow": "GET, POST"},
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
            if response.status != HTTPStatus.NOT_MODIFIED:
                headers["Content-Length"] = str(len(response.body))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _host_header_matches_server(value: str | None, *, host: str, port: int) -> bool:
    if value is None or "," in value or "@" in value:
        return False
    expected_host = f"[{host}]" if ":" in host else host
    allowed = {f"{expected_host}:{port}".casefold()}
    if port == 80:
        allowed.add(expected_host.casefold())
    return value.casefold() in allowed
