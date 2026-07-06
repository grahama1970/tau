from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.memory_acquisition import (
    EVIDENCE_CASE_ACQUISITION_RECEIPT_SCHEMA,
    MEMORY_INTENT_ACQUISITION_RECEIPT_SCHEMA,
    write_evidence_case_acquisition_receipt,
    write_memory_intent_acquisition_receipt,
)


def test_memory_intent_acquisition_posts_to_memory_and_hashes_response(tmp_path: Path) -> None:
    server, requests = _start_memory_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        receipt = write_memory_intent_acquisition_receipt(
            query="Should Tau dispatch this DAG?",
            receipt_path=tmp_path / "memory-intent-acquisition.json",
            memory_url=f"http://127.0.0.1:{server.server_port}",
            goal_hash="sha256:g",
            target={"repo": "grahama1970/tau", "target": "issue:63"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert receipt["schema"] == MEMORY_INTENT_ACQUISITION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["response_schema"] == "memory.intent.v1"
    assert receipt["request_sha256"].startswith("sha256:")
    assert receipt["response_sha256"].startswith("sha256:")
    assert Path(str(receipt["response_path"])).exists()
    assert requests[0]["path"] == "/intent"
    assert requests[0]["payload"]["q"] == "Should Tau dispatch this DAG?"
    assert requests[0]["payload"]["goal_hash"] == "sha256:g"


def test_evidence_case_acquisition_posts_intent_to_memory(tmp_path: Path) -> None:
    intent_path = tmp_path / "memory-intent.json"
    intent_path.write_text(json.dumps(_intent_payload()), encoding="utf-8")
    server, requests = _start_memory_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        receipt = write_evidence_case_acquisition_receipt(
            intent_path=intent_path,
            receipt_path=tmp_path / "evidence-case-acquisition.json",
            memory_url=f"http://127.0.0.1:{server.server_port}",
            question="What evidence supports this route?",
            goal_hash="sha256:g",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert receipt["schema"] == EVIDENCE_CASE_ACQUISITION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["response_schema"] == "memory.evidence_case.v1"
    assert receipt["intent_sha256"].startswith("sha256:")
    assert Path(str(receipt["response_path"])).exists()
    assert requests[0]["path"] == "/create-evidence-case"
    assert requests[0]["payload"]["intent"]["schema"] == "memory.intent.v1"
    assert requests[0]["payload"]["question"] == "What evidence supports this route?"


def test_memory_intent_acquisition_blocks_non_json_response(tmp_path: Path) -> None:
    server, _ = _start_memory_server(non_json_intent=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        receipt = write_memory_intent_acquisition_receipt(
            query="non-json please",
            receipt_path=tmp_path / "memory-intent-acquisition.json",
            memory_url=f"http://127.0.0.1:{server.server_port}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "memory_non_json_response" in receipt["alert_codes"]


def test_cli_memory_intent_and_evidence_case_create(tmp_path: Path) -> None:
    server, requests = _start_memory_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        memory_url = f"http://127.0.0.1:{server.server_port}"
        intent_receipt_path = tmp_path / "memory-intent-acquisition.json"
        intent_result = CliRunner().invoke(
            app,
            [
                "memory-intent",
                "--query",
                "Find route evidence",
                "--memory-url",
                memory_url,
                "--out",
                str(intent_receipt_path),
            ],
        )
        intent_payload = json.loads(intent_result.output)
        evidence_receipt_path = tmp_path / "evidence-case-acquisition.json"
        evidence_result = CliRunner().invoke(
            app,
            [
                "evidence-case-create",
                "--intent",
                str(intent_payload["response_path"]),
                "--memory-url",
                memory_url,
                "--out",
                str(evidence_receipt_path),
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    evidence_payload = json.loads(evidence_result.output)
    assert intent_result.exit_code == 0
    assert evidence_result.exit_code == 0
    assert intent_payload["schema"] == MEMORY_INTENT_ACQUISITION_RECEIPT_SCHEMA
    assert evidence_payload["schema"] == EVIDENCE_CASE_ACQUISITION_RECEIPT_SCHEMA
    assert intent_receipt_path.exists()
    assert evidence_receipt_path.exists()
    assert [request["path"] for request in requests] == ["/intent", "/create-evidence-case"]


def _start_memory_server(
    *,
    non_json_intent: bool = False,
) -> tuple[HTTPServer, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            requests.append({"path": self.path, "payload": payload})
            if self.path == "/intent" and non_json_intent:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not json")
                return
            if self.path == "/intent":
                self._write_json(_intent_payload(goal_hash=payload.get("goal_hash")))
                return
            if self.path == "/create-evidence-case":
                self._write_json(_evidence_case_payload())
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    return server, requests


def _intent_payload(goal_hash: str | None = "sha256:g") -> dict[str, Any]:
    payload = {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "planner_only": True,
        "route": "COMPLIANCE",
        "confidence": 0.91,
        "tool_calls": [{"name": "create_evidence_case"}],
        "evidence_case_required": True,
    }
    if goal_hash:
        payload["goal_hash"] = goal_hash
    return payload


def _evidence_case_payload() -> dict[str, Any]:
    return {
        "schema": "memory.evidence_case.v1",
        "source": "graph-memory-operator:/create-evidence-case",
        "sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "support_artifacts": [],
    }
