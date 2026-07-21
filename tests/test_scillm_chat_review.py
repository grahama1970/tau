from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.scillm_chat_review import write_scillm_chat_review_receipt


def test_scillm_chat_review_dry_run_validates_without_http(tmp_path: Path) -> None:
    request = _write_review_request(tmp_path)

    payload = write_scillm_chat_review_receipt(
        request_path=request,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["dry_run"] is True
    assert payload["http_executed"] is False
    assert payload["provider_live"] is False
    assert payload["request_payload"]["messages"] == "<redacted-review-request-messages>"


def test_scillm_chat_review_apply_posts_payload_and_writes_response(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_chat_server()
    request = _write_review_request(tmp_path)
    out = tmp_path / "receipt.json"
    response_out = tmp_path / "review_response.json"
    try:
        payload = write_scillm_chat_review_receipt(
            request_path=request,
            output_path=out,
            response_output_path=response_out,
            scillm_base_url=base_url,
            caller_skill="pdf-lab",
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "PASS"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["provider_live"] is True
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert payload["parsed_page_status"] == "clean"
    assert payload["parsed_candidate_finding_count"] == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(response_out.read_text(encoding="utf-8"))["schema"] == (
        "pdf_lab.second_pass.review_response.v1"
    )
    assert requests[0]["path"] == "/v1/chat/completions"
    assert requests[0]["authorization"] == "Bearer test-token"
    assert requests[0]["caller_skill"] == "pdf-lab"
    assert requests[0]["payload"]["messages"][0]["content"][1]["type"] == "image_url"
    assert "test-token" not in json.dumps(payload)
    assert "data:image/png" not in json.dumps(payload)


def test_scillm_chat_review_apply_blocks_without_auth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SCILLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("SCILLM_API_KEY", raising=False)
    monkeypatch.delenv("SCILLM_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SCILLM_ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("SCILLM_DOCKER_AUTH_DISCOVERY", "0")
    server, base_url, requests = _start_fake_chat_server()
    request = _write_review_request(tmp_path)
    try:
        payload = write_scillm_chat_review_receipt(
            request_path=request,
            output_path=tmp_path / "receipt.json",
            scillm_base_url=base_url,
            apply=True,
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert payload["headers"]["authorization"] == "REDACTED_REQUIRED"
    assert "missing_scillm_auth_token" in payload["alert_codes"]
    assert requests == []


def test_scillm_chat_review_cli_apply_writes_receipt(tmp_path: Path) -> None:
    server, base_url, requests = _start_fake_chat_server()
    request = _write_review_request(tmp_path)
    out = tmp_path / "receipt.json"
    response_out = tmp_path / "review_response.json"
    try:
        result = CliRunner().invoke(
            app,
            [
                "scillm-chat-review",
                "--request",
                str(request),
                "--out",
                str(out),
                "--response-out",
                str(response_out),
                "--scillm-base-url",
                base_url,
                "--caller-skill",
                "pdf-lab",
                "--apply",
                "--auth-token",
                "test-token",
                "--request-timeout-s",
                "5",
            ],
        )
    finally:
        server.shutdown()

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert requests[0]["payload"]["model"] == "vlm-free2"


def test_scillm_chat_review_blocks_invalid_response_schema(tmp_path: Path) -> None:
    server, base_url, _requests = _start_fake_chat_server(
        review_response={"schema": "wrong.schema", "page_status": "clean"}
    )
    request = _write_review_request(tmp_path)
    try:
        payload = write_scillm_chat_review_receipt(
            request_path=request,
            output_path=tmp_path / "receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["provider_live"] is False
    assert "invalid_review_response" in payload["alert_codes"]


def test_scillm_chat_review_timeout_does_not_claim_parse_failure(tmp_path: Path) -> None:
    server, base_url, _requests = _start_fake_chat_server(response_delay_s=2.0)
    request = _write_review_request(tmp_path)
    try:
        payload = write_scillm_chat_review_receipt(
            request_path=request,
            output_path=tmp_path / "receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=1,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["timed_out"] is True
    assert payload["root_cause_code"] == "scillm_chat_review_request_timeout"
    assert payload["recommended_next_action"].startswith("rerun with --timeout-diagnosis-mode")
    assert "scillm_chat_review_timeout" in payload["alert_codes"]
    assert "review_response_not_parseable" not in payload["alert_codes"]
    assert payload["raw_response_path"] is None


def test_scillm_chat_review_timeout_canary_classifies_service_unresponsive(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_chat_server(response_delay_s=2.0)
    request = _write_review_request(tmp_path)
    try:
        payload = write_scillm_chat_review_receipt(
            request_path=request,
            output_path=tmp_path / "receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=1,
            timeout_diagnosis_mode="live_canary",
            timeout_diagnosis_timeout_s=1,
        )
    finally:
        server.shutdown()

    assert len(requests) == 2
    assert payload["status"] == "BLOCKED"
    assert payload["timed_out"] is True
    assert payload["root_cause_code"] == "scillm_chat_review_service_unresponsive"
    assert payload["recommended_next_action"].startswith("do not retry PDF Lab page payloads")
    assert payload["timeout_diagnosis"]["status"] == "TIMEOUT"
    assert "scillm_chat_review_service_unresponsive" in payload["alert_codes"]
    assert "review_response_not_parseable" not in payload["alert_codes"]


def _write_review_request(tmp_path: Path) -> Path:
    request = {
        "schema": "pdf_lab.second_pass.review_request.v1",
        "model": "vlm-free2",
        "page_case": {
            "case_id": "page_case_0001_p0041",
            "page_number": 41,
            "candidate_ids": ["cand:p0041:0000:side_chrome"],
        },
        "response_format": {"type": "json_object"},
        "scillm_metadata": {
            "case_id": "page_case_0001_p0041",
            "request_sha256": "sha256-fixture",
        },
        "scillm_payload": {
            "model": "vlm-free2",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Return review JSON."},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,ZmFrZQ=="},
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "scillm_metadata": {
                "case_id": "page_case_0001_p0041",
                "request_sha256": "sha256-fixture",
            },
        },
    }
    path = tmp_path / "review_request.json"
    path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _start_fake_chat_server(
    *,
    review_response: dict | None = None,
    response_delay_s: float = 0.0,
) -> tuple[ThreadingHTTPServer, str, list[dict]]:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "caller_skill": self.headers.get("X-Caller-Skill"),
                    "payload": json.loads(body),
                }
            )
            if response_delay_s > 0:
                time.sleep(response_delay_s)
            content = review_response or {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0041:0000:side_chrome",
                        "status": "clean",
                        "evidence": "Fixture image and JSON agree.",
                        "rationale": "The candidate is page chrome.",
                        "suggested_fix_surface": "none",
                    }
                ],
                "page_rationale": "Fixture page is clean.",
            }
            response_payload = {
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(content, sort_keys=True),
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            encoded = json.dumps(response_payload, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}", requests
